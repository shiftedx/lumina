from __future__ import annotations

import math
import re
import threading
from collections import OrderedDict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import LibraryNote, LibraryItem, LibraryTag, MediaTitle, SourceAutomation, User
from app.services import embeddings
from app.services.library_search import LibrarySearchService, MomentHit, title_search_fields


SearchMode = Literal["hybrid", "lexical"]
MatchKind = Literal["library", "channel", "automation", "title", "moment"]
_KIND_ORDER = {"channel": 0, "title": 1, "library": 1, "moment": 2, "automation": 3}

# Per query the FTS index proposes at most this many library items to rank, so a
# search never materialises the whole corpus. The member's own automations are
# always ranked in addition to these candidates.
CANDIDATE_LIMIT = 200

# Document embeddings are shared across members and bounded by this LRU so a
# large household library cannot grow the resident embedding set without bound.
EMBEDDING_CACHE_CAPACITY = 4096

# Each member's resident index (which pins detached ORM records + vectors) is
# LRU-capped too, so browsing a large library never grows it without bound.
MEMBER_INDEX_CAPACITY = 2048

# Real embedding models put unrelated text well above zero cosine; below this a dense-only match is noise.
# One floor for every model; make it an admin setting if a model needs another.
DENSE_SEMANTIC_FLOOR = 0.3


class SemanticEncoder(Protocol):
    def encode(self, text: str) -> dict[str, float]: ...


@dataclass(frozen=True)
class SearchDocument:
    kind: MatchKind
    record_id: str
    title: str
    subtitle: str
    fields: tuple[tuple[str, str], ...]
    signature: str
    library_item: LibraryItem | None = None
    automation: SourceAutomation | None = None
    media_title: MediaTitle | None = None
    start_ms: int | None = None  # moment documents only

    @property
    def text(self) -> str:
        return " ".join(value for _, value in self.fields if value)


@dataclass(frozen=True)
class DiscoveryMatch:
    kind: MatchKind
    record_id: str
    title: str
    subtitle: str
    score: float
    lexical_score: float
    semantic_score: float
    match_mode: Literal["hybrid", "lexical", "semantic"]
    library_item: LibraryItem | None = None
    automation: SourceAutomation | None = None
    media_title: MediaTitle | None = None
    start_ms: int | None = None  # moment documents only


@dataclass(frozen=True)
class DiscoveryResult:
    query: str
    mode: SearchMode
    matches: tuple[DiscoveryMatch, ...]
    index_generation: int


@dataclass
class _IndexedDocument:
    document: SearchDocument
    embedding: dict[str, float]


@dataclass
class _MemberIndex:
    documents: "OrderedDict[str, _IndexedDocument]" = field(default_factory=OrderedDict)
    generation: int = 0


class OfflineIntentLexicon:
    """Deterministic token-to-intent expansion that can be extended without changing ranking."""

    def __init__(self, concepts: dict[str, set[str]]):
        by_token: dict[str, set[str]] = {}
        by_concept: dict[str, set[str]] = {}
        for concept, vocabulary in concepts.items():
            for token in vocabulary:
                normalized = token.casefold().strip()
                if normalized:
                    by_token.setdefault(normalized, set()).add(concept)
                    by_concept.setdefault(concept, set()).add(normalized)
        self._by_token = {token: tuple(sorted(names)) for token, names in by_token.items()}
        self._by_concept = {concept: tuple(sorted(vocab)) for concept, vocab in by_concept.items()}

    def intents_for(self, token: str) -> tuple[str, ...]:
        return self._by_token.get(token, ())

    def expand(self, tokens: list[str]) -> set[str]:
        """Related vocabulary for a query's tokens, so semantic candidates with no literal overlap still surface.

        A search for "space exploration" expands through the astronomy concept
        into "astronaut", "mars", "orbit"…, letting FTS propose an item whose
        text shares none of the query's own words.
        """
        related: set[str] = set()
        for token in tokens:
            for concept in self.intents_for(token):
                related.update(self._by_concept.get(concept, ()))
        return related


DEFAULT_INTENT_LEXICON = OfflineIntentLexicon(
    {
        "astronomy": {"astronaut", "cosmos", "cosmic", "lunar", "mars", "moon", "orbit", "planet", "rocket", "space"},
        "baking": {"bake", "baking", "bread", "dough", "knead", "loaf", "pastry", "proof", "sourdough", "yeast"},
        "cooking": {"chef", "cook", "cooking", "cuisine", "food", "kitchen", "meal", "recipe"},
        "music": {"album", "audio", "band", "concert", "melody", "music", "song", "track"},
        "wildlife": {"animal", "forest", "habitat", "nature", "ocean", "species", "wildlife"},
        "software": {"code", "computer", "developer", "programming", "software", "technology"},
        "fitness": {"cardio", "exercise", "fitness", "strength", "training", "workout", "yoga"},
        "travel": {"city", "destination", "journey", "tour", "travel", "trip", "vacation"},
        "education": {"course", "education", "explain", "guide", "learn", "lesson", "masterclass", "tutorial"},
        "personal_finance": {"budget", "debt", "expense", "expenses", "finance", "money", "saving", "savings", "spending"},
        "plumbing": {"faucet", "leak", "leaking", "pipe", "plumbing", "tap", "washer"},
        "photography": {"camera", "composition", "exposure", "nighttime", "photo", "photograph", "picture", "portrait", "sunset"},
        "gardening": {"balcony", "garden", "grow", "harvest", "seedling", "soil", "vegetable", "yard"},
        "woodworking": {"carpentry", "joinery", "lumber", "saw", "timber", "wood", "woodworking"},
        "automotive": {"automotive", "brake", "car", "engine", "mechanic", "tire", "vehicle"},
        "sewing": {"fabric", "needle", "pattern", "sew", "sewing", "stitch", "thread"},
        "meditation": {"breathing", "calm", "meditate", "meditation", "mindful", "relaxation"},
        "history": {"ancient", "archive", "historical", "history", "past", "war"},
        "language_learning": {"conversation", "fluency", "grammar", "language", "pronunciation", "speak", "vocabulary"},
        "home_repair": {"diy", "fix", "home", "repair", "replace", "restore", "workshop"},
    }
)


class DeterministicLocalEncoder:
    """Small offline encoder combining token features with curated intent expansion."""

    def __init__(self, lexicon: OfflineIntentLexicon = DEFAULT_INTENT_LEXICON):
        self._lexicon = lexicon

    def encode(self, text: str) -> dict[str, float]:
        features: dict[str, float] = {}
        for token in _content_tokens(text):
            stem = _stem(token)
            features[f"term:{stem}"] = features.get(f"term:{stem}", 0.0) + 1.0
            intents = set(self._lexicon.intents_for(token)) | set(self._lexicon.intents_for(stem))
            for intent in intents:
                features[f"intent:{intent}"] = 2.5
        return _normalize_vector(features)


class _EmbeddingCache:
    """Thread-safe bounded LRU from a document signature to its embedding vector."""

    def __init__(self, capacity: int = EMBEDDING_CACHE_CAPACITY):
        self._capacity = max(1, capacity)
        self._store: OrderedDict[str, dict[str, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, signature: str) -> dict[str, float] | None:
        with self._lock:
            embedding = self._store.get(signature)
            if embedding is not None:
                self._store.move_to_end(signature)
            return embedding

    def put(self, signature: str, embedding: dict[str, float]) -> None:
        with self._lock:
            self._store[signature] = embedding
            self._store.move_to_end(signature)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)


class SemanticDiscovery:
    """Rank a bounded, FTS-proposed candidate set of member-scoped discovery documents."""

    def __init__(
        self,
        encoder: SemanticEncoder | None = None,
        lexicon: OfflineIntentLexicon = DEFAULT_INTENT_LEXICON,
        *,
        member_index_capacity: int = MEMBER_INDEX_CAPACITY,
    ):
        self._encoder = encoder or DeterministicLocalEncoder()
        self._lexicon = lexicon
        self._member_index_capacity = max(1, member_index_capacity)
        self._member_indexes: dict[str, _MemberIndex] = {}
        self._embeddings = _EmbeddingCache()
        self._lock = threading.RLock()

    def search(
        self, db: Session, member: User, query: str, *, limit: int = 12, types: Collection[str] | None = None,
    ) -> DiscoveryResult:
        normalized_query = " ".join(query.strip().split())
        if not normalized_query or limit < 1:
            return DiscoveryResult(query=normalized_query, mode="hybrid", matches=(), index_generation=0)
        choice = embeddings.serving(db)
        dense = embeddings.query_vector(choice, normalized_query) if choice is not None else None
        if dense is not None:
            return self._dense_search(db, member, normalized_query, dense, choice.model_id, limit=limit, types=types)

        documents = _filter_types(self._candidate_documents(db, member, normalized_query), types)
        with self._lock:
            index = self._member_indexes.setdefault(member.id, _MemberIndex())
            try:
                self._synchronize(index, documents)
                query_embedding = self._encoder.encode(normalized_query)
                mode: SearchMode = "hybrid"
                indexed = tuple(index.documents[self._document_key(document)] for document in documents)
            except Exception:
                # Typeahead must stay useful if an optional semantic adapter fails.
                mode = "lexical"
                query_embedding = {}
                indexed = tuple(_IndexedDocument(document=document, embedding={}) for document in documents)

            matches = self._rank(indexed, normalized_query, mode, query_embedding)[:limit]
            return DiscoveryResult(
                query=normalized_query,
                mode=mode,
                matches=tuple(matches),
                index_generation=index.generation,
            )

    def _dense_search(self, db, member, query, dense, model_id, *, limit, types) -> DiscoveryResult:  # noqa: ANN001
        """Stored vectors score the semantic side; an unembedded document scores 0 (vector spaces never mix)."""
        vectors = embeddings.vector_map(db, model_id)
        documents = _filter_types(self._candidate_documents(db, member, query, embeddings.nearest(vectors, dense)), types)
        scores = {
            self._document_key(document): embeddings.cosine(dense, vectors[document.record_id][1])
            for document in documents
            if document.kind in ("title", "library") and document.record_id in vectors
        }
        indexed = tuple(_IndexedDocument(document=document, embedding={}) for document in documents)
        matches = self._rank(indexed, query, "hybrid", {}, semantic_scores=scores, semantic_floor=DENSE_SEMANTIC_FLOOR)
        return DiscoveryResult(query=query, mode="hybrid", matches=tuple(matches[:limit]), index_generation=0)

    def _synchronize(self, index: _MemberIndex, documents: list[SearchDocument]) -> None:
        # Accumulate: only the current query's candidates are (re)indexed, and a
        # candidate that no longer matches is simply not revisited. The member
        # generation advances when a candidate is newly indexed or its text
        # changed, so an added/edited tag or comment is reflected on the next
        # matching search without scanning the whole corpus each request.
        changed = False
        for document in documents:
            key = self._document_key(document)
            current = index.documents.get(key)
            if current is not None and current.document.signature == document.signature:
                # Keep the live ORM records while reusing the unchanged vector.
                current.document = document
                index.documents.move_to_end(key)
                continue
            index.documents[key] = _IndexedDocument(document=document, embedding=self._embedding_for(document))
            index.documents.move_to_end(key)
            changed = True
        # Drop the least-recently-touched documents beyond the cap. This only
        # evicts entries from earlier queries (this query's candidates were just
        # moved to the end), so it never changes the current result set. The
        # max() floor keeps that true even when the configured capacity is
        # smaller than one query's candidate set — without it, eviction would
        # eat the current candidates and the later index lookup would KeyError
        # into the lexical fallback.
        while len(index.documents) > max(self._member_index_capacity, len(documents)):
            index.documents.popitem(last=False)
        if changed:
            index.generation += 1

    def _embedding_for(self, document: SearchDocument) -> dict[str, float]:
        cached = self._embeddings.get(document.signature)
        if cached is not None:
            return cached
        embedding = self._encoder.encode(document.text)
        self._embeddings.put(document.signature, embedding)
        return embedding

    def _rank(
        self,
        indexed: tuple[_IndexedDocument, ...],
        query: str,
        mode: SearchMode,
        query_vector: dict[str, float],
        *,
        semantic_scores: dict[str, float] | None = None,
        semantic_floor: float = 0.08,
    ) -> list[DiscoveryMatch]:
        ranked: list[DiscoveryMatch] = []
        for entry in indexed:
            lexical = _lexical_score(entry.document, query)
            if semantic_scores is not None:
                semantic = semantic_scores.get(self._document_key(entry.document), 0.0)
            else:
                semantic = _cosine(query_vector, entry.embedding) if mode == "hybrid" else 0.0
            if lexical <= 0 and semantic < semantic_floor:
                continue
            total = lexical + (semantic * 4.0)
            match_mode: Literal["hybrid", "lexical", "semantic"]
            if lexical > 0 and semantic >= semantic_floor:
                match_mode = "hybrid"
            elif lexical > 0:
                match_mode = "lexical"
            else:
                match_mode = "semantic"
            ranked.append(
                DiscoveryMatch(
                    kind=entry.document.kind,
                    record_id=entry.document.record_id,
                    title=entry.document.title,
                    subtitle=entry.document.subtitle,
                    score=round(total, 6),
                    lexical_score=round(lexical, 6),
                    semantic_score=round(semantic, 6),
                    match_mode=match_mode,
                    library_item=entry.document.library_item,
                    automation=entry.document.automation,
                    media_title=entry.document.media_title,
                    start_ms=entry.document.start_ms,
                )
            )
        ranked.sort(key=lambda match: (-match.score, _KIND_ORDER[match.kind], match.title.casefold(), match.record_id))
        return ranked

    def _candidate_documents(
        self, db: Session, member: User, query: str, vector_hits: Sequence[tuple[str, str]] = (),
    ) -> list[SearchDocument]:
        """Visible candidates: item FTS ∪ title FTS ∪ transcript FTS ∪ vector top-K, plus the member's automations.

        Every list is visibility-filtered before ranking. A hit on a version of a Media title proposes
        the title (one card per title, not per file); a transcript window proposes a moment (the best
        window per item); a summary row proposes its item, carrying the summary text so it can score.
        """
        extra_terms = self._lexicon.expand(_content_tokens(query))
        search = LibrarySearchService(db)
        moments = search.moment_hits(member, query, limit=CANDIDATE_LIMIT, extra_terms=extra_terms)
        summaries = {hit.item_id: hit.text for hit in moments if hit.start_ms is None}
        item_ids = list(dict.fromkeys([
            *search.item_ids_for_query(member, query, limit=CANDIDATE_LIMIT, extra_terms=extra_terms),
            *summaries,
            *search.visible_item_ids(member, [target for target, kind in vector_hits if kind == "item"]),
        ]))
        loaded = list(dict.fromkeys([*item_ids, *(hit.item_id for hit in moments)]))
        items = {item.id: item for item in db.query(LibraryItem).filter(LibraryItem.id.in_(loaded)).all()} if loaded else {}
        versions: dict[str, list[str]] = {}
        summaries_by_title: dict[str, list[str]] = {}
        for item_id in item_ids:
            item = items.get(item_id)
            if item is not None and item.title_id:
                versions.setdefault(item.title_id, []).append(item.title)
                if item_id in summaries:
                    summaries_by_title.setdefault(item.title_id, []).append(summaries[item_id])
        title_ids = list(dict.fromkeys([
            *search.title_ids_for_query(member, query, limit=CANDIDATE_LIMIT, extra_terms=extra_terms),
            *sorted(search.visible_title_ids(member, [*versions, *(t for t, kind in vector_hits if kind == "title")])),
        ]))
        documents = self._title_documents(db, title_ids, versions, summaries_by_title)
        plain = [item_id for item_id in item_ids if item_id in items and not items[item_id].title_id]
        if plain:
            tags_by_item, comments_by_item = self._member_curation(db, member, plain)
            documents += [
                self._library_document(
                    items[item_id], tags_by_item.get(item_id, []), comments_by_item.get(item_id, []), summaries.get(item_id, ""),
                )
                for item_id in plain
            ]
        moment_items: set[str] = set()
        for hit in moments:
            if hit.start_ms is None or hit.item_id in moment_items or hit.item_id not in items:
                continue
            moment_items.add(hit.item_id)
            documents.append(self._moment_document(items[hit.item_id], hit))
        automations = db.query(SourceAutomation).filter(SourceAutomation.user_id == member.id).all()
        documents.extend(self._automation_document(automation) for automation in automations)
        return documents

    @staticmethod
    def _member_curation(
        db: Session, member: User, candidate_ids: list[str]
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        tags_by_item: dict[str, list[str]] = {}
        for tag in (
            db.query(LibraryTag)
            .filter(LibraryTag.user_id == member.id, LibraryTag.item_id.in_(candidate_ids))
            .all()
        ):
            tags_by_item.setdefault(tag.item_id, []).append(tag.tag)
        comments_by_item: dict[str, list[str]] = {}
        for comment in (
            db.query(LibraryNote)
            .filter(LibraryNote.item_id.in_(candidate_ids))
            .filter(or_(LibraryNote.user_id == member.id, LibraryNote.visibility == "household"))
            .all()
        ):
            comments_by_item.setdefault(comment.item_id, []).append(comment.body)
        return tags_by_item, comments_by_item

    @staticmethod
    def _library_document(item: LibraryItem, tags: list[str], comments: list[str], summary: str = "") -> SearchDocument:
        metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
        fields = (
            ("title", item.title),
            ("description", _text(metadata.get("description"))),
            ("channel", _text(metadata.get("channel"))),
            ("uploader", item.uploader or _text(metadata.get("uploader"))),
            ("tags", " ".join(sorted(tags))),
            ("comments", " ".join(sorted(comments))),
            ("summary", summary),
        )
        return SearchDocument(
            kind="library",
            record_id=item.id,
            title=item.title,
            subtitle=item.uploader or _text(metadata.get("channel")),
            fields=fields,
            signature=_signature(fields),
            library_item=item,
        )

    @staticmethod
    def _automation_document(automation: SourceAutomation) -> SearchDocument:
        fields = (
            ("title", automation.label),
            ("source", automation.source_url),
            ("type", automation.source_type),
            ("rules", _text(automation.rules)),
        )
        return SearchDocument(
            kind="channel" if automation.source_type == "channel" else "automation",
            record_id=automation.id,
            title=automation.label,
            subtitle="Followed channel" if automation.source_type == "channel" else "Source automation",
            fields=fields,
            signature=_signature(fields),
            automation=automation,
        )

    @staticmethod
    def _title_documents(
        db: Session, title_ids: list[str], versions: dict[str, list[str]], summaries: dict[str, list[str]],
    ) -> list[SearchDocument]:
        if not title_ids:
            return []
        known = {t.id: t for t in db.scalars(select(MediaTitle).where(MediaTitle.id.in_(title_ids)))}
        titles = dict(known)
        for _generation in range(2):  # parents, then grandparents: episodes need their series name
            missing = {t.parent_id for t in known.values() if t.parent_id and t.parent_id not in known}
            if missing:
                known.update({t.id: t for t in db.scalars(select(MediaTitle).where(MediaTitle.id.in_(missing)))})
        lookup = lambda title_id: known.get(title_id or "")  # noqa: E731
        return [
            SemanticDiscovery._title_document(
                titles[title_id], title_search_fields(titles[title_id], lookup),
                versions.get(title_id, []), summaries.get(title_id, []),
            )
            for title_id in title_ids
            if title_id in titles
        ]

    @staticmethod
    def _title_document(
        title: MediaTitle, text_fields: dict[str, str], version_titles: list[str], summaries: list[str],
    ) -> SearchDocument:
        fields = (
            ("title", title.name),
            ("series", text_fields["series_name"]),
            ("description", text_fields["overview"]),
            ("genres", text_fields["genres"]),
            ("people", text_fields["people"]),
            ("versions", " ".join(sorted(version_titles))),
            ("summary", " ".join(sorted(summaries))),
        )
        subtitle = text_fields["series_name"] or (str(title.year) if title.year else title.type.title())
        return SearchDocument(
            kind="title", record_id=title.id, title=title.name, subtitle=subtitle,
            fields=fields, signature=_signature(fields), media_title=title,
        )

    @staticmethod
    def _moment_document(item: LibraryItem, hit: MomentHit) -> SearchDocument:
        fields = (("transcript", hit.text),)
        return SearchDocument(
            kind="moment", record_id=f"{item.id}@{hit.start_ms}", title=item.title, subtitle=f"At {_clock(hit.start_ms or 0)}",
            fields=fields, signature=_signature(fields), library_item=item, start_ms=hit.start_ms,
        )

    @staticmethod
    def _document_key(document: SearchDocument) -> str:
        return f"{document.kind}:{document.record_id}"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


_STOP_WORDS = {
    "a",
    "an",
    "and",
    "after",
    "better",
    "for",
    "how",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def _content_tokens(text: str) -> list[str]:
    return [token for token in _tokens(text) if token not in _STOP_WORDS]


def _stem(token: str) -> str:
    for suffix in ("ation", "ing", "ies", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            return token[: -len(suffix)]
    return token


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(entry) for entry in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(entry) for entry in value)
    return str(value) if isinstance(value, (int, float)) else ""


def _signature(fields: tuple[tuple[str, str], ...]) -> str:
    return sha256(repr(fields).encode("utf-8")).hexdigest()


def _normalize_vector(vector: dict[str, float]) -> dict[str, float]:
    magnitude = math.sqrt(sum(value * value for value in vector.values()))
    return {key: value / magnitude for key, value in vector.items()} if magnitude else {}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def _lexical_score(document: SearchDocument, query: str) -> float:
    phrase = query.casefold()
    query_terms = set(_content_tokens(query))
    weights = {
        "title": 5.0, "tags": 4.0, "channel": 3.0, "uploader": 3.0, "series": 3.0, "people": 3.0, "genres": 2.0,
        "description": 1.5, "versions": 1.0, "transcript": 1.0, "summary": 1.0, "source": 1.0, "type": 1.0, "rules": 1.0,
    }
    score = 0.0
    for field_name, value in document.fields:
        normalized = value.casefold()
        if not normalized:
            continue
        weight = weights.get(field_name, 1.0)
        if phrase in normalized:
            score += weight * (2.0 if normalized.startswith(phrase) else 1.0)
        field_terms = set(_content_tokens(normalized))
        score += weight * 0.35 * len(query_terms & field_terms)
    return score


def _clock(ms: int) -> str:
    hours, rest = divmod(ms // 1000, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _document_type(document: SearchDocument) -> str:
    return document.media_title.type if document.media_title is not None and document.kind == "title" else document.kind


def _filter_types(documents: list[SearchDocument], types: Collection[str] | None) -> list[SearchDocument]:
    return documents if types is None else [document for document in documents if _document_type(document) in types]


@dataclass(frozen=True)
class SearchRef:
    """One ranked hit as ids, for routes that render their own shapes (Jellyfin /Search/Hints, /Items?searchTerm)."""

    kind: MatchKind
    id: str  # title id | library item id (library and moment hits) | automation id
    title_id: str | None  # the Media title the hit belongs to; Jellyfin shows a moment as its Episode
    start_ms: int | None  # moment hits only


def match_title_id(match: DiscoveryMatch) -> str | None:
    if match.media_title is not None:
        return match.media_title.id
    return match.library_item.title_id if match.library_item is not None else None


# The one process-wide instance: its member indexes are caches shared by every search surface.
discovery = SemanticDiscovery()


def search_ids(
    db: Session, member: User, q: str, *, types: Collection[str] | None = None, limit: int = 20,
) -> list[SearchRef]:
    """Ranked, visibility-filtered hits shared by /api/search, Jellyfin /Search/Hints and /Items?searchTerm.

    ``types`` keeps only these document types: a Media title type (movie, series, season, episode,
    boxset), "library" (untitled items), "moment", "channel" or "automation". None keeps all.
    """
    result = discovery.search(db, member, q, limit=limit, types=types)
    return [
        SearchRef(
            kind=match.kind,
            id=match.library_item.id if match.kind == "moment" and match.library_item is not None else match.record_id,
            title_id=match_title_id(match),
            start_ms=match.start_ms,
        )
        for match in result.matches
    ]
