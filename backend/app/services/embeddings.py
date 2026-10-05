"""Optional dense embeddings for search and title similarity.

Only text every viewer of a target may see is embedded: a Media title's name, series name, overview,
genres and people; a summarized item's title, description and latest summary. Never tags or notes.
Vectors are unit-length float32, so cosine is ``math.sumprod``. One model's vectors never mix with
another's: rows are keyed by model id ("local:<catalog id>" for an on-device model, else the external
model name), and readers ask for one model only.

Two sources matter at any time. The *target* is the best available source (model_endpoints); the
backfill fills it. The *serving* source is the one search reads: the target once its backfill has found
nothing left, until then the best other source that still has vectors, so a model switch never empties
search. When the target's index is complete, every other model's rows are deleted.
``index_title``/``index_item_transcript`` delete a target's row when its text changes, and the next
backfill re-embeds it.
Remote videos recommendations nominated are a third kind: their vectors live on remote_media, one batch per drain.
"""
from __future__ import annotations

import hashlib
import heapq
import logging
import math
import threading
from array import array
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import AppSettings, LibraryItem, MediaTitle, RemoteMedia, SearchEmbedding, Summary, utcnow
from app.persistence import write_transaction
from app.services import model_catalog, model_endpoints
from app.services.library_search import title_search_fields
from app.services.local_ai import AiConfig, LocalAiError, embed
from app.services.model_endpoints import Choice
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

BACKFILL_BATCH = 32
REMOTE_RECENT = timedelta(days=14)  # The window pool.py keeps remote_media rows alive in
QUERY_TIMEOUT_SECONDS = 1.5
TOP_K = 200
FEATURE_KEY = "semantic_search"  # AppSettings.ai_features_disabled kill switch (ADR 0013)

_lock = threading.Lock()
_vectors: dict[str, dict[str, tuple[str, array]]] = {}  # model id -> target id -> (kind, unit vector)
_present: set[str] | None = None  # distinct SearchEmbedding.model_id values; None = read them again
_complete: set[str] = set()  # model ids whose backfill found nothing left to embed


def _enabled_record(db: Session) -> AppSettings | None:
    record = YtDlpService(db).get_app_settings()
    return None if FEATURE_KEY in (record.ai_features_disabled or []) else record


def _choices(db: Session) -> list[Choice]:
    record = _enabled_record(db)
    return model_endpoints.search_choices(record) if record is not None else []


def target(db: Session) -> Choice | None:
    """The source the backfill fills; None when semantic search is switched off or nothing is available."""
    choices = _choices(db)
    return choices[0] if choices else None


def serving(db: Session) -> Choice | None:
    """The source whose vectors search reads: the target once complete, else the best other source with vectors."""
    choices = _choices(db)
    if not choices:
        return None
    if choices[0].model_id in _complete:
        return choices[0]
    present = _present_models(db)
    return next((choice for choice in choices[1:] if choice.model_id in present), choices[0])


def _present_models(db: Session) -> set[str]:
    global _present
    with _lock:
        if _present is not None:
            return _present
    present = set(db.scalars(select(SearchEmbedding.model_id).distinct()))
    with _lock:
        _present = present
    return present


def prefixes(model_id: str) -> tuple[str, str]:
    """(query prefix, document prefix) a catalog model expects, e.g. nomic's "search_query: "; ("", "") otherwise."""
    model = model_catalog.from_choice_id(model_id)
    engine = model.engine if model is not None else {}
    return str(engine.get("query_prefix") or ""), str(engine.get("document_prefix") or "")


def unit(values) -> array:  # noqa: ANN001 - any float iterable
    vector = array("f", values)
    norm = math.sqrt(math.sumprod(vector, vector))
    return array("f", (value / norm for value in vector)) if norm else vector


def cosine(left: array, right: array) -> float:
    return math.sumprod(left, right) if len(left) == len(right) else 0.0


def vector_map(db: Session, model_id: str) -> dict[str, tuple[str, array]]:
    """Every stored vector of ``model_id``; loaded once per process, dropped when a backfill writes."""
    with _lock:
        cached = _vectors.get(model_id)
    if cached is not None:
        return cached
    loaded: dict[str, tuple[str, array]] = {}
    rows = db.execute(
        select(SearchEmbedding.target_id, SearchEmbedding.kind, SearchEmbedding.vector).where(
            SearchEmbedding.model_id == model_id,
            # 1.9.0 embedded album/artist titles; they never feed search or recommendations (no migration needed)
            SearchEmbedding.target_id.not_in(select(MediaTitle.id).where(MediaTitle.type.in_(("album", "artist")))),
        )
    )
    for target_id, kind, blob in rows:
        vector = array("f")
        vector.frombytes(blob)
        loaded[target_id] = (kind, vector)
    with _lock:
        _vectors[model_id] = loaded
    return loaded


def nearest(vectors: dict[str, tuple[str, array]], query: array, *, limit: int = TOP_K) -> list[tuple[str, str]]:
    """(target id, kind) of the ``limit`` most similar vectors, best first. NOT visibility-filtered: callers filter.

    Brute-force cosine over every stored vector (~50k × 768 dims ≈ 150 MB resident, ~50 ms a
    query on Python 3.12). Upgrade path: sqlite-vec.
    """
    best = heapq.nlargest(limit, ((cosine(query, vector), target_id, kind) for target_id, (kind, vector) in vectors.items()))
    return [(target_id, kind) for _score, target_id, kind in best]


@lru_cache(maxsize=256)
def _query_vector(config: AiConfig, text: str) -> array:
    (values,) = embed(config, [text], timeout=QUERY_TIMEOUT_SECONDS, slot_wait=QUERY_TIMEOUT_SECONDS)
    return unit(values)


def query_vector(choice: Choice, text: str) -> array | None:
    """The query's unit vector within ~1.5 s (LRU-cached; failures are not cached); None means go lexical.

    A local server that is not running yet is started in the background, and this one query goes lexical.
    """
    try:
        with model_endpoints.connect(choice, wait=False, heavy=False) as config:
            return None if config is None else _query_vector(config, prefixes(choice.model_id)[0] + text)
    except LocalAiError:
        return None


def reset_caches() -> None:
    global _present
    with _lock:
        _vectors.clear()
        _present = None
        _complete.clear()
    _query_vector.cache_clear()


def _title_text(db: Session, title: MediaTitle) -> str:
    fields = title_search_fields(title, lambda title_id: db.get(MediaTitle, title_id) if title_id else None)
    return "\n".join(value for key, value in fields.items() if key != "title_id" and value)


def _item_text(db: Session, item: LibraryItem) -> str:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    description = metadata.get("description")
    overview = db.scalar(
        select(Summary.overview)
        .where(Summary.library_item_id == item.id, Summary.state == "succeeded")
        .order_by(Summary.completed_at.desc())
        .limit(1)
    )
    return "\n".join(part for part in (item.title, description if isinstance(description, str) else "", overview or "") if part)


def _documents(db: Session, model_id: str, limit: int) -> list[tuple[str, str, str]]:
    """Up to ``limit`` (target id, kind, text) with no vector under ``model_id``: titles first, then summarized items."""
    embedded = select(SearchEmbedding.target_id).where(SearchEmbedding.model_id == model_id)
    titles = db.scalars(
        select(MediaTitle).where(MediaTitle.type.not_in(("season", "album", "artist")), MediaTitle.id.not_in(embedded))
        .order_by(MediaTitle.created_at, MediaTitle.id).limit(limit)
    ).all()
    documents = [(title.id, "title", _title_text(db, title)) for title in titles]
    if len(documents) < limit:
        summarized = select(Summary.library_item_id).where(Summary.state == "succeeded")
        items = db.scalars(
            select(LibraryItem).where(LibraryItem.id.in_(summarized), LibraryItem.id.not_in(embedded))
            .order_by(LibraryItem.id).limit(limit - len(documents))
        ).all()
        documents += [(item.id, "item", _item_text(db, item)) for item in items]
    return documents


def _remote_documents(db: Session, model_id: str, limit: int, *, now=None) -> list[tuple[str, str, str]]:  # noqa: ANN001
    """Up to ``limit`` (remote_media key, "remote", text) with no vector under ``model_id``, newest nominated first.

    Only rows a source nominated within ``REMOTE_RECENT`` count, and a row another model
    embedded is taken again lazily: its vector is ignored meanwhile.
    """
    cutoff = (now or utcnow()) - REMOTE_RECENT
    rows = db.scalars(
        select(RemoteMedia)
        .where(
            RemoteMedia.last_nominated_at >= cutoff, RemoteMedia.title.is_not(None),
            or_(RemoteMedia.vector_model.is_(None), RemoteMedia.vector_model != model_id),
        )
        .order_by(RemoteMedia.last_nominated_at.desc(), RemoteMedia.key).limit(limit)
    ).all()
    return [(row.key, "remote", _remote_text(row)) for row in rows]


def _remote_text(row: RemoteMedia) -> str:
    return f"{row.title} — {row.uploader}" if row.uploader else (row.title or "")


def backfill(db: Session, config: AiConfig, *, limit: int = BACKFILL_BATCH) -> int:
    """Embed up to ``limit`` documents with no vector under ``config``'s embedding model; returns how many."""
    model_id = config.ai_embedding_model or ""
    documents = _documents(db, model_id, limit)
    if not documents:
        return 0
    document_prefix = prefixes(model_id)[1]
    vectors = embed(config, [document_prefix + text for _target, _kind, text in documents])
    with write_transaction(db, name="embedding_backfill"):
        for (target_id, kind, text), values in zip(documents, vectors, strict=True):
            db.merge(SearchEmbedding(
                target_id=target_id, model_id=model_id, kind=kind,
                signature=hashlib.sha256(text.encode()).hexdigest(), vector=unit(values).tobytes(),
            ))
    with _lock:
        _vectors.pop(model_id, None)
        if _present is not None:
            _present.add(model_id)
    return len(documents)


def backfill_remote(db: Session, config: AiConfig, *, limit: int = BACKFILL_BATCH) -> int:
    """Embed up to ``limit`` recent remote videos with no vector under ``config``'s model; the vector lands on the row.

    ``search_embeddings.target_id`` is 36 characters and remote keys are 64, so ``remote_media`` carries its own vector.
    """
    model_id = config.ai_embedding_model or ""
    documents = _remote_documents(db, model_id, limit)
    if not documents:
        return 0
    document_prefix = prefixes(model_id)[1]
    vectors = embed(config, [document_prefix + text for _key, _kind, text in documents])
    with write_transaction(db, name="embedding_backfill_remote"):
        for (key, _kind, text), values in zip(documents, vectors, strict=True):
            row = db.get(RemoteMedia, key)
            if row is not None and _remote_text(row) == text:  # the title changed meanwhile: next batch takes it
                row.vector_model, row.vector_signature, row.vector = model_id, hashlib.sha256(text.encode()).hexdigest(), unit(values).tobytes()
    return len(documents)


def _finish(db: Session, model_id: str) -> None:
    """``model_id``'s index is complete: it becomes the served index, and every other model's vectors go.

    The admin's installed local search model keeps its vectors: it only stepped aside (crash-failed until
    Retry, which is in memory only), so it serves again at once without re-embedding the library on CPU.
    """
    global _present
    kept = {model_id}
    active = model_endpoints.active_model(YtDlpService(db).get_app_settings(), "search")
    if model_catalog.is_installed(active):
        kept.add(active.choice_id)
    with write_transaction(db, name="embedding_index_switch"):
        db.execute(delete(SearchEmbedding).where(SearchEmbedding.model_id.not_in(kept)))
    with _lock:
        _complete.intersection_update(kept)  # a model whose rows just went is never served from memory
        _complete.add(model_id)
        for other in [key for key in _vectors if key not in kept]:
            del _vectors[other]
        _present = None


_draining = threading.Lock()


def start_backfill(_run_id: str | None = None) -> None:
    """Embed everything missing under the target model on one background thread; returns at once.

    Triggers: ``library_import.after_import_hooks`` (the argument is the import run id), summary
    success, an admin model change, a search model becoming ready, and every maintenance tick (restart-safe
    catch-up). A second trigger while one drain runs is a no-op, and so is one with no target.
    """
    with session_scope() as db:
        if target(db) is None:
            return
    if _draining.acquire(blocking=False):
        threading.Thread(target=_drain, daemon=True, name="embedding-backfill").start()


def _drain() -> None:
    try:
        while True:
            remote_only = False
            with session_scope() as db:
                choice = target(db)
                if choice is None:
                    return
                if not _documents(db, choice.model_id, 1):
                    # Nothing to embed: finish the switch if one is pending, and never start a server to find this out.
                    if choice.model_id not in _complete or _present_models(db) - {choice.model_id}:
                        _finish(db, choice.model_id)
                    if not _remote_documents(db, choice.model_id, 1):
                        return
                    remote_only = True
            # One batch per lease: local speech transcription can take its turn between batches (heavy lock).
            with model_endpoints.connect(choice, wait=True, heavy=True) as config, session_scope() as db:
                if remote_only:
                    backfill_remote(db, config)  # one remote batch per drain; the next maintenance tick takes the next
                    return
                backfill(db, config)
    except LocalAiError as exc:
        logger.info("Embedding backfill paused: %s", exc)
    except Exception as exc:  # noqa: BLE001 - a background drain never takes the app down; log the type only
        logger.warning("Embedding backfill failed: %s", type(exc).__name__)
    finally:
        _draining.release()


from app.services import library_import  # noqa: E402  (hook list; imported last to keep the module order readable)

if start_backfill not in library_import.after_import_hooks:
    library_import.after_import_hooks.append(start_backfill)
