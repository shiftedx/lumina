"""SQLite FTS5 full-text index for the household library and its member curation.

Two contentless-by-hand FTS5 tables carry the search corpus and encode the
household visibility model directly in *where the text lives*:

* ``library_item_fts`` — one row per Library item, holding text every viewer of
  the item may see: title, uploader, playlist, channel, description, and the
  item's *household* notes. Matches here are gated only by item visibility.
* ``library_member_fts`` — one row per (item, member) that carries text only
  that member may see: the member's own tags and the member's *private*
  notes. A member's private curation can never surface in another member's
  search because it physically lives in a row keyed by ``member_id``.

A member's search unions matches from ``library_item_fts`` (any item the member
can view) with matches from ``library_member_fts`` scoped to ``member_id``, then
joins ``library_items`` under the standard household visibility predicate. That
join is also what makes a removed Library item disappear from results even if a
stale index row lingers.

The tables are regular (content-carrying) FTS5 tables maintained in the same
session transaction as the source mutation, so they commit atomically with it.
SQLite before 3.43 has no ``contentless_delete`` and the base tables use UUID
string primary keys rather than integer rowids, so a literal ``content=`` /
``content_rowid=`` external-content table is not usable here; the tables shadow
the base rows and are rebuildable from them via :func:`ensure_search_index`.
"""

from __future__ import annotations

import json

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy import delete, event, literal_column, select, text
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, aliased

from app.db import Base
from app.models import LibraryNote, LibraryItem, LibraryTag, MediaTitle, SearchEmbedding, Summary, Transcript, TranscriptCue, User
from app.services import member_access


# A search never materialises more than this many ranked matches — the accepted
# total-recall cap. Escalating fetch stops here when a query is dominated by
# another member's invisible items.
SEARCH_MAX_RESULTS = 500
# Fetch starts at x4 the window and widens by this factor per refill pass: x4 -> x16 -> the cap.
FETCH_ESCALATION_FACTOR = 4

ITEM_FTS_TABLE = "library_item_fts"
MEMBER_FTS_TABLE = "library_member_fts"

# Each FTS row is addressed by an integer rowid kept in a small ordinary map
# table keyed by the row's identity. Deletes and re-indexes seek the rowid
# (a primary-key lookup + O(1) FTS rowid delete) instead of scanning the FTS
# content on an UNINDEXED key — the difference between an O(1) and an O(corpus)
# write, which matters when a batched playlist upsert re-indexes many existing
# items inside one writer-slot transaction.
ITEM_FTS_MAP_TABLE = "library_item_fts_map"
MEMBER_FTS_MAP_TABLE = "library_member_fts_map"

# Media titles: one row per title; episodes and seasons also carry their series name.
TITLE_FTS_TABLE = "media_title_fts"
TITLE_FTS_MAP_TABLE = "media_title_fts_map"
# Music titles are never in the title index: search finds music through its track items.
UNINDEXED_TITLE_TYPES = ("album", "artist")
# Moments: one row per ~60 s window of an item's one timing transcript (see
# TranscriptService.timing_transcript: ASR, then synced, then captions; never a translation or a
# second copy), plus one row (start_ms NULL) for its latest summary. A timed hit is "the one where…".
TRANSCRIPT_FTS_TABLE = "transcript_fts"
TRANSCRIPT_FTS_MAP_TABLE = "transcript_fts_map"
TRANSCRIPT_WINDOW_MS = 60_000

# unicode61 folds case and (with remove_diacritics 2) strips accents, so the
# index matches naive queries against accented titles. Both tables share it so
# item text and member curation tokenize identically.
_TOKENIZE = "unicode61 remove_diacritics 2"

_CREATE_ITEM_FTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {ITEM_FTS_TABLE} USING fts5("
    "item_id UNINDEXED, title, uploader, playlist, channel, description, shared_comments, "
    f"tokenize='{_TOKENIZE}')"
)
_CREATE_MEMBER_FTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {MEMBER_FTS_TABLE} USING fts5("
    "item_id UNINDEXED, member_id UNINDEXED, tags, private_comments, "
    f"tokenize='{_TOKENIZE}')"
)
# The map tables' implicit integer rowid is the addressed FTS rowid; their PKs
# make the identity->rowid lookup an indexed seek.
_CREATE_ITEM_FTS_MAP = f"CREATE TABLE IF NOT EXISTS {ITEM_FTS_MAP_TABLE} (item_id TEXT PRIMARY KEY)"
_CREATE_MEMBER_FTS_MAP = (
    f"CREATE TABLE IF NOT EXISTS {MEMBER_FTS_MAP_TABLE} "
    "(item_id TEXT NOT NULL, member_id TEXT NOT NULL, PRIMARY KEY (item_id, member_id))"
)

_CREATE_TITLE_FTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {TITLE_FTS_TABLE} USING fts5("
    "title_id UNINDEXED, name, series_name, overview, genres, people, "
    f"tokenize='{_TOKENIZE}')"
)
_CREATE_TITLE_FTS_MAP = f"CREATE TABLE IF NOT EXISTS {TITLE_FTS_MAP_TABLE} (title_id TEXT PRIMARY KEY)"
_CREATE_TRANSCRIPT_FTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {TRANSCRIPT_FTS_TABLE} USING fts5("
    "item_id UNINDEXED, start_ms UNINDEXED, text, "
    f"tokenize='{_TOKENIZE}')"
)
# One map row per FTS row; the (item_id, seq) key makes "every row of this item" an indexed range.
_CREATE_TRANSCRIPT_FTS_MAP = (
    f"CREATE TABLE IF NOT EXISTS {TRANSCRIPT_FTS_MAP_TABLE} "
    "(item_id TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY (item_id, seq))"
)

# Query tokens are letters/digits only. Everything else — the FTS5 syntax
# metacharacters ``" * ( ) : ^ - + NEAR`` included — is dropped before a MATCH
# string is built, so no user input can ever reach the FTS5 query parser as
# syntax. Tokens are lower-cased, so an all-caps ``AND``/``OR``/``NOT``/``NEAR``
# can never be read as a boolean operator either.
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SearchHit:
    item_id: str
    rank: float


def create_search_tables(connection: Connection) -> None:
    for statement in (
        _CREATE_ITEM_FTS, _CREATE_MEMBER_FTS, _CREATE_ITEM_FTS_MAP, _CREATE_MEMBER_FTS_MAP,
        _CREATE_TITLE_FTS, _CREATE_TITLE_FTS_MAP, _CREATE_TRANSCRIPT_FTS, _CREATE_TRANSCRIPT_FTS_MAP,
    ):
        connection.exec_driver_sql(statement)


@event.listens_for(Base.metadata, "after_create")
def _create_search_tables_after_metadata(target, connection, **kw):  # type: ignore[no-untyped-def]
    # Every ``Base.metadata.create_all`` (production init and every test that
    # builds a schema) gets the FTS tables, so write hooks and search never
    # find them missing.
    if connection.dialect.name == "sqlite":
        create_search_tables(connection)


def _delete_item_index_rows(connection: Connection, item_id: str) -> None:
    """Sweep every FTS row (item + all member curation) for one Library item, by rowid seek."""
    row = connection.exec_driver_sql(
        f"SELECT rowid FROM {ITEM_FTS_MAP_TABLE} WHERE item_id = ?", (item_id,)
    ).first()
    if row is not None:
        connection.exec_driver_sql(f"DELETE FROM {ITEM_FTS_TABLE} WHERE rowid = ?", (int(row[0]),))
        connection.exec_driver_sql(f"DELETE FROM {ITEM_FTS_MAP_TABLE} WHERE item_id = ?", (item_id,))
    for (member_rowid,) in connection.exec_driver_sql(
        f"SELECT rowid FROM {MEMBER_FTS_MAP_TABLE} WHERE item_id = ?", (item_id,)
    ).all():
        connection.exec_driver_sql(f"DELETE FROM {MEMBER_FTS_TABLE} WHERE rowid = ?", (int(member_rowid),))
    connection.exec_driver_sql(f"DELETE FROM {MEMBER_FTS_MAP_TABLE} WHERE item_id = ?", (item_id,))
    _delete_transcript_rows(connection, item_id)


@event.listens_for(LibraryItem, "after_delete")
def _unindex_deleted_library_item(mapper, connection, target):  # type: ignore[no-untyped-def]
    # HARD PRECONDITION of the FTS corpus: any Library-item delete path must
    # sweep the item's index rows, or FTS garbage accumulates until the next
    # count-mismatch full rebuild at boot. Wiring it to the ORM delete event
    # makes the sweep structural — inside the same flush/transaction — instead
    # of a convention every future delete path must remember. (A bulk
    # ``query(LibraryItem).delete()`` bypasses ORM events and must call
    # :meth:`LibrarySearchService.remove_item` explicitly.)
    if connection.dialect.name == "sqlite":
        _delete_item_index_rows(connection, target.id)


def query_tokens(query: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_PATTERN.findall(query or "")]


MAX_MATCH_TOKENS = 32


def build_match(terms: Iterable[str]) -> str | None:
    """A prefix-OR MATCH string for typeahead, or ``None`` when nothing is searchable."""
    seen = list(dict.fromkeys(token for term in terms for token in query_tokens(term)))[:MAX_MATCH_TOKENS]
    return " OR ".join(f"{token}*" for token in seen) or None


@dataclass(frozen=True)
class MomentHit:
    item_id: str
    start_ms: int | None  # None = the item's summary row
    text: str
    rank: float


def title_search_fields(title: MediaTitle, lookup: Callable[[str | None], MediaTitle | None]) -> dict[str, str]:
    """Text of one Media title that every viewer of it may see; ``lookup`` resolves parent ids."""
    metadata = title.metadata_json if isinstance(title.metadata_json, dict) else {}
    series = None
    if title.type == "season":
        series = lookup(title.parent_id)
    elif title.type == "episode":
        season = lookup(title.parent_id)
        series = lookup(season.parent_id) if season is not None else None
    genres = metadata.get("genres") if isinstance(metadata.get("genres"), list) else []
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    people = metadata.get("people") if isinstance(metadata.get("people"), list) else []
    overview = metadata.get("overview")
    return {
        "title_id": title.id,
        "name": title.name or "",
        "series_name": series.name if series is not None else "",
        "overview": overview if isinstance(overview, str) else "",
        "genres": " ".join(item for item in [*genres, *tags] if isinstance(item, str)),
        "people": " ".join(p["name"] for p in people if isinstance(p, dict) and isinstance(p.get("name"), str)),
    }


def _delete_title_row(db: Session, title_id: str) -> None:
    found = db.execute(text(f"SELECT rowid FROM {TITLE_FTS_MAP_TABLE} WHERE title_id = :id"), {"id": title_id}).first()
    if found is not None:
        db.execute(text(f"DELETE FROM {TITLE_FTS_TABLE} WHERE rowid = :rowid"), {"rowid": int(found[0])})
        db.execute(text(f"DELETE FROM {TITLE_FTS_MAP_TABLE} WHERE title_id = :id"), {"id": title_id})


def _insert_title_row(db: Session, fields: dict[str, str]) -> None:
    rowid = db.execute(text(f"INSERT INTO {TITLE_FTS_MAP_TABLE} (title_id) VALUES (:title_id)"), fields).lastrowid
    db.execute(
        text(
            f"INSERT INTO {TITLE_FTS_TABLE} (rowid, title_id, name, series_name, overview, genres, people) "
            "VALUES (:rowid, :title_id, :name, :series_name, :overview, :genres, :people)"
        ),
        {"rowid": int(rowid), **fields},
    )


def _refs(title: MediaTitle) -> list[dict]:
    people = (title.metadata_json or {}).get("people") if isinstance(title.metadata_json, dict) else None
    return [ref for ref in people or [] if isinstance(ref, dict)]


def _renamed_people(db: Session, titles: list[MediaTitle]) -> dict[str, str]:
    ids = sorted({ref["person_id"] for title in titles for ref in _refs(title) if isinstance(ref.get("person_id"), str)})
    if not ids:
        return {}
    return dict(db.execute(text("SELECT id, name FROM person_overrides WHERE id IN (SELECT value FROM json_each(:ids))"
                                " AND name IS NOT NULL"), {"ids": json.dumps(ids)}).all())


def index_title(db: Session, title: MediaTitle) -> None:
    """(Re)write a Media title's FTS row after its name, overview, genres or people change.

    Called by the scanner's ``_link_titles()`` and by TMDB refresh/identify. A series also
    re-indexes its seasons and episodes (they carry its name). The title's stored embedding is
    dropped so the backfill re-embeds the new text under the current model.
    """
    if title.type in UNINDEXED_TITLE_TYPES:
        return
    lookup = lambda title_id: db.get(MediaTitle, title_id) if title_id else None  # noqa: E731
    targets = [title]
    if title.type == "series":
        seasons = list(db.scalars(select(MediaTitle).where(MediaTitle.parent_id == title.id)))
        episodes = list(db.scalars(select(MediaTitle).where(MediaTitle.parent_id.in_([s.id for s in seasons])))) if seasons else []
        targets += seasons + episodes
    renamed = _renamed_people(db, targets)
    for target in targets:
        fields = title_search_fields(target, lookup)
        if extra := [renamed[ref["person_id"]] for ref in _refs(target) if ref.get("person_id") in renamed]:
            fields["people"] = " ".join([fields["people"], *extra]).strip()  # #164: the household's names find them too
        _delete_title_row(db, target.id)
        _insert_title_row(db, fields)
    db.execute(delete(SearchEmbedding).where(SearchEmbedding.target_id == title.id))


def transcript_windows(cues: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Group ordered (start_ms, text) cues into windows starting at most every TRANSCRIPT_WINDOW_MS."""
    windows: list[tuple[int, list[str]]] = []
    for start_ms, body in cues:
        if not windows or start_ms - windows[-1][0] >= TRANSCRIPT_WINDOW_MS:
            windows.append((start_ms, []))
        windows[-1][1].append(body)
    return [(start, " ".join(parts)) for start, parts in windows]


def _transcript_rows(db: Session, item_id: str) -> list[tuple[int | None, str]]:
    from app.services.transcripts import TranscriptService  # local: transcripts.store imports this module

    transcript = TranscriptService(db).timing_transcript(item_id)
    rows: list[tuple[int | None, str]] = []
    if transcript is not None:
        cues = (
            db.query(TranscriptCue.start_ms, TranscriptCue.text)
            .filter(TranscriptCue.transcript_id == transcript.id)
            .order_by(TranscriptCue.ordinal)
        )
        rows.extend(transcript_windows(cues))
    summary = (
        db.query(Summary.overview, Summary.key_points)
        .filter(Summary.library_item_id == item_id, Summary.state == "succeeded")
        .order_by(Summary.completed_at.desc())
        .first()
    )
    if summary is not None:
        points = [point.get("text", "") for point in summary.key_points or [] if isinstance(point, dict)]
        rows.append((None, "\n".join([summary.overview or "", *points])))
    return rows


def _delete_transcript_rows(connection, item_id: str) -> None:  # noqa: ANN001 - Connection or Session
    params = {"id": item_id}
    connection.execute(
        text(f"DELETE FROM {TRANSCRIPT_FTS_TABLE} WHERE rowid IN (SELECT rowid FROM {TRANSCRIPT_FTS_MAP_TABLE} WHERE item_id = :id)"),
        params,
    )
    connection.execute(text(f"DELETE FROM {TRANSCRIPT_FTS_MAP_TABLE} WHERE item_id = :id"), params)


def _insert_transcript_rows(db: Session, item_id: str, rows: list[tuple[int | None, str]]) -> None:
    for seq, (start_ms, body) in enumerate(rows):
        rowid = db.execute(
            text(f"INSERT INTO {TRANSCRIPT_FTS_MAP_TABLE} (item_id, seq) VALUES (:item_id, :seq)"),
            {"item_id": item_id, "seq": seq},
        ).lastrowid
        db.execute(
            text(f"INSERT INTO {TRANSCRIPT_FTS_TABLE} (rowid, item_id, start_ms, text) VALUES (:rowid, :item_id, :start_ms, :text)"),
            {"rowid": int(rowid), "item_id": item_id, "start_ms": start_ms, "text": body},
        )


def index_item_transcript(db: Session, item_id: str) -> None:
    """Rewrite an item's moment rows from its newest searchable transcript and latest summary.

    Called on transcript store and summary success. Drops the item's stored embedding so the
    backfill re-embeds the new summary text.
    """
    _delete_transcript_rows(db, item_id)
    _insert_transcript_rows(db, item_id, _transcript_rows(db, item_id))
    db.execute(delete(SearchEmbedding).where(SearchEmbedding.target_id == item_id))


class LibrarySearchService:
    """Maintain and query the FTS5 corpus for one database session."""

    def __init__(self, db: Session):
        self.db = db

    # -- write path -----------------------------------------------------------

    def sync_item(self, item: LibraryItem) -> None:
        """Rebuild the item-level FTS row from the item and its household notes."""
        self._write_item_row(item, self._shared_comment_text(item.id))

    def index_new_item(self, item: LibraryItem) -> None:
        """Insert the FTS row for a just-created item.

        A brand-new item has no prior row and no comments yet, so this skips the
        rowid lookup :meth:`sync_item` does — keeping bulk acquisition purely
        insert-only per download.
        """
        rowid = self._allocate_item_rowid(item.id)
        self._insert_item_row(rowid, item, "")

    def _write_item_row(self, item: LibraryItem, shared_comments: str) -> None:
        rowid = self._item_rowid(item.id)
        if rowid is None:
            rowid = self._allocate_item_rowid(item.id)
        else:
            self.db.execute(text(f"DELETE FROM {ITEM_FTS_TABLE} WHERE rowid = :rowid"), {"rowid": rowid})
        self._insert_item_row(rowid, item, shared_comments)

    def _insert_item_row(self, rowid: int, item: LibraryItem, shared_comments: str) -> None:
        self.db.execute(
            text(
                f"INSERT INTO {ITEM_FTS_TABLE} "
                "(rowid, item_id, title, uploader, playlist, channel, description, shared_comments) "
                "VALUES (:rowid, :item_id, :title, :uploader, :playlist, :channel, :description, :shared_comments)"
            ),
            {
                "rowid": rowid,
                "item_id": item.id,
                "title": item.title or "",
                "uploader": item.uploader or "",
                "playlist": item.playlist_name or "",
                "channel": _metadata_channel(item),
                "description": _metadata_description(item),
                "shared_comments": shared_comments,
            },
        )

    def remove_item(self, item_id: str) -> None:
        _delete_item_index_rows(self.db.connection(), item_id)

    def sync_member_curation(self, item_id: str, member_id: str) -> None:
        """Rebuild the (item, member) FTS row from that member's tags and private notes."""
        tags = self._member_tag_text(item_id, member_id)
        private_comments = self._member_private_comment_text(item_id, member_id)
        rowid = self._member_rowid(item_id, member_id)
        if not tags and not private_comments:
            if rowid is not None:
                self.db.execute(text(f"DELETE FROM {MEMBER_FTS_TABLE} WHERE rowid = :rowid"), {"rowid": rowid})
                self.db.execute(
                    text(f"DELETE FROM {MEMBER_FTS_MAP_TABLE} WHERE item_id = :item_id AND member_id = :member_id"),
                    {"item_id": item_id, "member_id": member_id},
                )
            return
        if rowid is None:
            rowid = self._allocate_member_rowid(item_id, member_id)
        else:
            self.db.execute(text(f"DELETE FROM {MEMBER_FTS_TABLE} WHERE rowid = :rowid"), {"rowid": rowid})
        self._insert_member_row(rowid, item_id, member_id, tags, private_comments)

    def _insert_member_row(self, rowid: int, item_id: str, member_id: str, tags: str, private_comments: str) -> None:
        self.db.execute(
            text(
                f"INSERT INTO {MEMBER_FTS_TABLE} (rowid, item_id, member_id, tags, private_comments) "
                "VALUES (:rowid, :item_id, :member_id, :tags, :private_comments)"
            ),
            {"rowid": rowid, "item_id": item_id, "member_id": member_id, "tags": tags, "private_comments": private_comments},
        )

    def _item_rowid(self, item_id: str) -> int | None:
        row = self.db.execute(
            text(f"SELECT rowid FROM {ITEM_FTS_MAP_TABLE} WHERE item_id = :item_id"), {"item_id": item_id}
        ).first()
        return int(row[0]) if row is not None else None

    def _allocate_item_rowid(self, item_id: str) -> int:
        result = self.db.execute(text(f"INSERT INTO {ITEM_FTS_MAP_TABLE} (item_id) VALUES (:item_id)"), {"item_id": item_id})
        return int(result.lastrowid)

    def _member_rowid(self, item_id: str, member_id: str) -> int | None:
        row = self.db.execute(
            text(f"SELECT rowid FROM {MEMBER_FTS_MAP_TABLE} WHERE item_id = :item_id AND member_id = :member_id"),
            {"item_id": item_id, "member_id": member_id},
        ).first()
        return int(row[0]) if row is not None else None

    def _allocate_member_rowid(self, item_id: str, member_id: str) -> int:
        result = self.db.execute(
            text(f"INSERT INTO {MEMBER_FTS_MAP_TABLE} (item_id, member_id) VALUES (:item_id, :member_id)"),
            {"item_id": item_id, "member_id": member_id},
        )
        return int(result.lastrowid)

    def _shared_comment_text(self, item_id: str) -> str:
        rows = (
            self.db.query(LibraryNote.body)
            .filter(LibraryNote.item_id == item_id, LibraryNote.visibility == "household")
            .all()
        )
        return "\n".join(row[0] for row in rows if row[0])

    def _member_tag_text(self, item_id: str, member_id: str) -> str:
        rows = (
            self.db.query(LibraryTag.tag)
            .filter(LibraryTag.item_id == item_id, LibraryTag.user_id == member_id)
            .all()
        )
        return " ".join(row[0] for row in rows if row[0])

    def _member_private_comment_text(self, item_id: str, member_id: str) -> str:
        rows = (
            self.db.query(LibraryNote.body)
            .filter(
                LibraryNote.item_id == item_id,
                LibraryNote.user_id == member_id,
                LibraryNote.visibility != "household",
            )
            .all()
        )
        return "\n".join(row[0] for row in rows if row[0])

    # -- read path ------------------------------------------------------------

    def search(self, user: User, query: str, *, limit: int, extra_terms: Iterable[str] = ()) -> list[SearchHit]:
        """Top-``limit`` VISIBLE matches in bm25 order (deterministic tiebreak: FTS rowid for the pool, item id for the page).

        Each FTS table is ranked and truncated to a fetch bound FIRST, so SQLite
        applies FTS5 top-K and only that bounded candidate set is joined to
        ``library_items`` for the visibility check — the cheap shape.

        But truncating before the visibility check can starve a member whose own
        matches rank below another member's dominant private items: the top fetch
        could be entirely invisible. So the fetch escalates (x4 → x16 →
        the 500 cap) and re-queries until the requested window fills with VISIBLE
        rows or the candidate pool is exhausted. The common case is one query at
        the fetch=4x-limit cost; only an invisible-dominated query pays for the
        deeper passes, up to the (previously accepted) 500 total-recall cap.

        A sparse query (fewer raw matches than the window) must not walk the
        whole escalating ladder: after a short first pass, one cheap unranked
        count of the raw candidate pool tells us whether deeper fetches can add
        anything at all, and the loop exits as soon as the fetch bound covers
        the pool.
        """
        match = build_match([query, *extra_terms])
        if match is None:
            return []
        desired = min(max(1, limit), SEARCH_MAX_RESULTS)
        # Over-fetch the first pass: top-K cost is dominated by ranking every match,
        # not by K, so one wider pass usually absorbs other members' private hits
        # without the count + second pass.
        fetch = min(desired * FETCH_ESCALATION_FACTOR, SEARCH_MAX_RESULTS)
        hits = self._ranked_hits(user, match, fetch=fetch, limit=desired)
        if len(hits) >= desired:
            return hits
        pool = self._candidate_count(user, match)
        while len(hits) < desired and fetch < SEARCH_MAX_RESULTS and pool > fetch:
            fetch = min(fetch * FETCH_ESCALATION_FACTOR, SEARCH_MAX_RESULTS)
            hits = self._ranked_hits(user, match, fetch=fetch, limit=desired)
        return hits

    def _candidate_count(self, user: User, match: str) -> int:
        """Distinct raw candidates for ``match`` across both tables (no ranking, no visibility join)."""
        sql = text(
            f"""
            SELECT count(*) FROM (
                SELECT item_id FROM {ITEM_FTS_TABLE} WHERE {ITEM_FTS_TABLE} MATCH :match
                UNION
                SELECT item_id FROM {MEMBER_FTS_TABLE}
                WHERE {MEMBER_FTS_TABLE} MATCH :match AND member_id = :member_id
            )
            """
        )
        value = self.db.execute(sql, {"match": match, "member_id": user.id}).scalar()
        return int(value or 0)

    def _ranked_hits(self, user: User, match: str, *, fetch: int, limit: int) -> list[SearchHit]:
        visibility_sql, params = _visibility_clause(self.db, user)
        params.update({"match": match, "member_id": user.id, "fetch": fetch, "limit": limit})
        sql = text(
            f"""
            WITH item_hits AS (
                SELECT item_id, bm25({ITEM_FTS_TABLE}) AS rank
                FROM {ITEM_FTS_TABLE}
                WHERE {ITEM_FTS_TABLE} MATCH :match
                ORDER BY rank, rowid
                LIMIT :fetch
            ),
            member_hits AS (
                SELECT item_id, bm25({MEMBER_FTS_TABLE}) AS rank
                FROM {MEMBER_FTS_TABLE}
                WHERE {MEMBER_FTS_TABLE} MATCH :match AND member_id = :member_id
                ORDER BY rank, rowid
                LIMIT :fetch
            ),
            ranked AS (
                SELECT item_id, MIN(rank) AS rank
                FROM (SELECT * FROM item_hits UNION ALL SELECT * FROM member_hits)
                GROUP BY item_id
            )
            SELECT ranked.item_id AS item_id, ranked.rank AS rank
            FROM ranked
            JOIN library_items li ON li.id = ranked.item_id
            WHERE {visibility_sql}
            ORDER BY ranked.rank ASC, li.id ASC
            LIMIT :limit
            """
        )
        rows = self.db.execute(sql, params).all()
        return [SearchHit(item_id=row.item_id, rank=row.rank) for row in rows]

    def item_ids_for_query(self, user: User, query: str, *, limit: int, extra_terms: Iterable[str] = ()) -> list[str]:
        return [hit.item_id for hit in self.search(user, query, limit=limit, extra_terms=extra_terms)]

    def _fetch(self, query: str, limit: int, extra_terms: Iterable[str]) -> tuple[str | None, int]:
        return build_match([query, *extra_terms]), min(max(1, limit) * FETCH_ESCALATION_FACTOR, SEARCH_MAX_RESULTS)

    def visible_title_ids(self, user: User, ids: Iterable[str]) -> set[str]:
        from app.services.library import LibraryService  # library imports this module

        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return set()
        return set(self.db.scalars(select(MediaTitle.id).where(MediaTitle.id.in_(wanted), LibraryService.visible_title_predicate(user))))

    def visible_item_ids(self, user: User, ids: Iterable[str]) -> list[str]:
        from app.services.library import LibraryService

        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        visible = set(self.db.scalars(
            select(LibraryItem.id).where(LibraryItem.id.in_(wanted), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
        ))
        return [item_id for item_id in wanted if item_id in visible]

    def title_ids_for_query(self, user: User, query: str, *, limit: int, extra_terms: Iterable[str] = ()) -> list[str]:
        """Top visible Media titles in bm25 order; ranked and truncated in FTS first, then visibility-filtered.

        One pass at 4x the window (no escalation like ``search``); a query dominated by another
        member's private titles can return a short list. Escalate like ``search`` if that is ever reported.
        """
        match, fetch = self._fetch(query, limit, extra_terms)
        if match is None:
            return []
        ranked = [row[0] for row in self.db.execute(
            text(f"SELECT title_id FROM {TITLE_FTS_TABLE} WHERE {TITLE_FTS_TABLE} MATCH :match "
                 f"ORDER BY bm25({TITLE_FTS_TABLE}), rowid LIMIT :fetch"),
            {"match": match, "fetch": fetch},
        )]
        visible = self.visible_title_ids(user, ranked)
        return [title_id for title_id in ranked if title_id in visible][: max(1, limit)]

    def title_records_for_query(
        self, user: User, query: str, *, limit: int, linked_ids: Iterable[str] = (), extra_terms: Iterable[str] = (),
    ) -> list[Row]:
        """The same bounded title FTS pool plus linked titles, visibility checked and projected together."""
        from app.services.library import LibraryService

        match, fetch = self._fetch(query, limit, extra_terms)
        ranked = [row[0] for row in self.db.execute(
            text(f"SELECT title_id FROM {TITLE_FTS_TABLE} WHERE {TITLE_FTS_TABLE} MATCH :match "
                 f"ORDER BY bm25({TITLE_FTS_TABLE}), rowid LIMIT :fetch"),
            {"match": match, "fetch": fetch},
        )] if match is not None else []
        linked = sorted(set(linked_ids))
        wanted = list(dict.fromkeys([*ranked, *linked]))
        if not wanted:
            return []
        visible = {row.id: row for row in self.db.execute(select(
            MediaTitle.id, MediaTitle.type, MediaTitle.parent_id, MediaTitle.name, MediaTitle.year, MediaTitle.metadata_json,
        ).where(MediaTitle.id.in_(wanted), LibraryService.visible_title_predicate(user)))}
        # Preserve the separate FTS result ceiling before adding linked titles.
        # These can match a version or summary even when their title text does not.
        ids = list(dict.fromkeys([
            *[title_id for title_id in ranked if title_id in visible][:max(1, limit)],
            *[title_id for title_id in linked if title_id in visible],
        ]))
        return [visible[title_id] for title_id in ids]

    def moment_hits(self, user: User, query: str, *, limit: int, extra_terms: Iterable[str] = ()) -> list[MomentHit]:
        """Top visible transcript windows and summary rows; present items only, visibility joined in SQL."""
        match, fetch = self._fetch(query, limit, extra_terms)
        if match is None:
            return []
        visibility_sql, params = _visibility_clause(self.db, user)
        params.update({"match": match, "fetch": fetch, "limit": max(1, limit)})
        rows = self.db.execute(
            text(
                f"""
                WITH hits AS (
                    SELECT item_id, start_ms, text, bm25({TRANSCRIPT_FTS_TABLE}) AS rank
                    FROM {TRANSCRIPT_FTS_TABLE}
                    WHERE {TRANSCRIPT_FTS_TABLE} MATCH :match
                    ORDER BY rank, rowid
                    LIMIT :fetch
                )
                SELECT hits.item_id, hits.start_ms, hits.text, hits.rank
                FROM hits JOIN library_items li ON li.id = hits.item_id
                WHERE {visibility_sql} AND li.status != 'missing'
                ORDER BY hits.rank, li.id, hits.start_ms
                LIMIT :limit
                """
            ),
            params,
        ).all()
        return [MomentHit(item_id=row[0], start_ms=row[1], text=row[2], rank=row[3]) for row in rows]


def _visibility_clause(db: Session, user: User) -> tuple[str, dict]:
    """LibraryService.visible_predicate for raw FTS SQL over ``library_items li``, member access included (ADR 0019)."""
    shared, owner = "li.visibility = 'shared'", "li.user_id = :owner_id"
    row = aliased(LibraryItem, name="access_li")
    if (limits := member_access.item_clause(user, row)) is not None:
        allowed = select(row.id).where(row.id == literal_column("li.id"), limits).exists()
        # Wrapped columns: the hits drive, never a MULTI-INDEX OR over every visible item paying the access check each.
        shared = f"(coalesce(li.visibility, '') = 'shared' AND {allowed.compile(dialect=db.get_bind().dialect, compile_kwargs={'literal_binds': True})})"
        owner = "coalesce(li.user_id, '') = :owner_id"
    clause = f"({shared} OR {owner}"
    params: dict = {"owner_id": user.id}
    if user.role == "admin":
        clause += " OR li.user_id IS NULL"
    clause += ")"
    return clause, params


def _metadata_channel(item: LibraryItem) -> str:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    channel = metadata.get("channel")
    return channel if isinstance(channel, str) else ""


def _metadata_description(item: LibraryItem) -> str:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    description = metadata.get("description")
    return description if isinstance(description, str) else ""


def ensure_search_index(engine: Engine) -> None:
    """Create the FTS tables if absent and rebuild them when out of sync with the base tables.

    Called at startup and after the ``sqlite3.backup()`` recovery path in
    :mod:`app.db`: a restored database may carry a stale or absent index, so the
    corpus is rebuilt from ``library_items`` / ``library_notes`` /
    ``library_tags`` whenever the item counts disagree.

    A malformed FTS shadow table must not fail startup (and would otherwise
    poison every later search): the probe below raises :class:`DatabaseError`
    for it, and the virtual tables are then dropped, recreated, and rebuilt
    from the base tables. (Text drift with equal counts remains undetected —
    the sync check is a count check, not a content diff.)
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        create_search_tables(connection)
        try:
            _probe_fts_index(connection)
            if _index_in_sync(connection):
                return
        except DatabaseError:
            _drop_search_tables(connection)
            create_search_tables(connection)
        _rebuild(connection)


_FTS_TABLES = (ITEM_FTS_TABLE, MEMBER_FTS_TABLE, TITLE_FTS_TABLE, TRANSCRIPT_FTS_TABLE)
_FTS_MAP_TABLES = (ITEM_FTS_MAP_TABLE, MEMBER_FTS_MAP_TABLE, TITLE_FTS_MAP_TABLE, TRANSCRIPT_FTS_MAP_TABLE)


def _probe_fts_index(connection: Connection) -> None:
    """Raise :class:`DatabaseError` when an FTS index structure is malformed.

    ``count(*)`` scans the stored content and does NOT touch the term index,
    so a missing or truncated ``…_data`` shadow table sails through the count
    sync check and only explodes on the first real search. One bounded MATCH
    per table reads the structure blob and term b-tree instead.
    """
    for table in _FTS_TABLES:
        connection.exec_driver_sql(f"SELECT rowid FROM {table} WHERE {table} MATCH 'lumina' LIMIT 1").first()


def _drop_search_tables(connection: Connection) -> None:
    for table in _FTS_TABLES:
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")


def _index_in_sync(connection: Connection) -> bool:
    """Both FTS tables must match their source: a dropped item OR member table forces a rebuild."""
    item_count = connection.exec_driver_sql("SELECT count(*) FROM library_items").scalar()
    indexed_items = connection.exec_driver_sql(f"SELECT count(*) FROM {ITEM_FTS_TABLE}").scalar()
    if item_count != indexed_items:
        return False
    # Member rows exist for each (item, member) pair with a tag or private comment.
    expected_members = connection.exec_driver_sql(
        """
        SELECT count(*) FROM (
            SELECT item_id, user_id FROM library_tags WHERE user_id IS NOT NULL
            UNION
            SELECT item_id, user_id FROM library_notes WHERE user_id IS NOT NULL AND visibility <> 'household'
        )
        """
    ).scalar()
    indexed_members = connection.exec_driver_sql(f"SELECT count(*) FROM {MEMBER_FTS_TABLE}").scalar()
    if expected_members != indexed_members:
        return False
    titles = connection.exec_driver_sql("SELECT count(*) FROM media_titles WHERE type NOT IN ('album', 'artist')").scalar()
    if titles != connection.exec_driver_sql(f"SELECT count(*) FROM {TITLE_FTS_TABLE}").scalar():
        return False
    # Count check (like the item tables): items with a searchable transcript or a succeeded summary.
    # Joined against live library_items, so a hard-deleted item's leftover transcript/summary
    # rows (the ORM-delete sweep clears FTS, not the Transcript/Summary rows themselves) never force
    # a rebuild on every boot.
    transcribed = connection.exec_driver_sql(
        """
        SELECT count(*) FROM (
            SELECT library_item_id FROM transcripts WHERE source_kind IN ('asr', 'synced', 'source_caption')
            UNION
            SELECT library_item_id FROM summaries WHERE state = 'succeeded'
        ) t
        WHERE t.library_item_id IN (SELECT id FROM library_items)
        """
    ).scalar()
    indexed = connection.exec_driver_sql(f"SELECT count(DISTINCT item_id) FROM {TRANSCRIPT_FTS_MAP_TABLE}").scalar()
    return transcribed == indexed


def _rebuild(connection: Connection) -> None:
    """Repopulate both FTS tables and their rowid maps from the base tables (cold path, batched reads)."""
    for table in (*_FTS_TABLES, *_FTS_MAP_TABLES):
        connection.exec_driver_sql(f"DELETE FROM {table}")
    session = Session(bind=connection)
    try:
        service = LibrarySearchService(session)

        shared_by_item: dict[str, list[str]] = {}
        for item_id, body in (
            session.query(LibraryNote.item_id, LibraryNote.body)
            .filter(LibraryNote.visibility == "household")
            .all()
        ):
            if body:
                shared_by_item.setdefault(item_id, []).append(body)
        for item in session.query(LibraryItem).yield_per(500):
            rowid = service._allocate_item_rowid(item.id)
            service._insert_item_row(rowid, item, "\n".join(shared_by_item.get(item.id, [])))

        member_tags: dict[tuple[str, str], list[str]] = {}
        for item_id, member_id, tag in session.query(LibraryTag.item_id, LibraryTag.user_id, LibraryTag.tag).all():
            if member_id and tag:
                member_tags.setdefault((item_id, member_id), []).append(tag)
        member_private: dict[tuple[str, str], list[str]] = {}
        for item_id, member_id, body in (
            session.query(LibraryNote.item_id, LibraryNote.user_id, LibraryNote.body)
            .filter(LibraryNote.visibility != "household")
            .all()
        ):
            if member_id and body:
                member_private.setdefault((item_id, member_id), []).append(body)
        for pair in set(member_tags) | set(member_private):
            item_id, member_id = pair
            rowid = service._allocate_member_rowid(item_id, member_id)
            service._insert_member_row(
                rowid,
                item_id,
                member_id,
                " ".join(member_tags.get(pair, [])),
                "\n".join(member_private.get(pair, [])),
            )

        from app.services.transcripts import TIMING_KINDS  # local: transcripts.store imports this module

        titles = {title.id: title for title in session.query(MediaTitle).all()}
        for title in titles.values():
            if title.type not in UNINDEXED_TITLE_TYPES:
                _insert_title_row(session, title_search_fields(title, lambda title_id: titles.get(title_id or "")))
        # Three small reads per transcribed item on this cold path; batch by item if a
        # rebuild of a large transcribed library is ever slow at boot.
        transcribed = {
            item_id for (item_id,) in session.query(Transcript.library_item_id)
            .filter(Transcript.source_kind.in_(TIMING_KINDS)).distinct()
        } | {item_id for (item_id,) in session.query(Summary.library_item_id).filter(Summary.state == "succeeded").distinct()}
        for item_id in sorted(transcribed):
            _insert_transcript_rows(session, item_id, _transcript_rows(session, item_id))
        session.flush()
    finally:
        session.close()
