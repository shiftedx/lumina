"""Media title helpers shared by the scanner, TMDB, the Jellyfin API and the web UI (ADR 0009).

Ids and ticks live here rather than in services/jellyfin.py because people,
segments, synthetic Channels ids and the Watchlist playlist need them too.
"""
from __future__ import annotations

import functools
import uuid
from datetime import datetime
from typing import Any, Iterable, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.db import RESCAN_ADDED_AT_SQL
from app.models import MediaTitle

# Fixed forever: every synthetic id is uuid5 of this namespace. Changing it re-ids
# views, Channels, people, studios, segments and playlists in every client.
LUMINA_NAMESPACE = uuid.UUID("b313b443-44d5-44a1-95e2-052a2d34c25b")
TICKS_PER_SECOND = 10_000_000
# user > nfo > tmdb > path: a scan or refresh writes a field only at or above its recorded source.
FIELD_SOURCE_RANK = {"path": 0, "tmdb": 1, "nfo": 2, "user": 3}
# Columns apply_field may write; key/type/root_id are structural and belong to the scanner. parent_id is structural
# too, except that a user may move an episode to another season (#164): only the editor writes it through apply_field.
TITLE_COLUMNS = frozenset({"name", "sort_name", "year", "index_number", "index_number_end", "provider_ids", "boxset_id", "parent_id"})


# ---- 2.1.0 edit model (ADR 0016). ----
MATCHABLE_TYPES = ("series", "movie")  # title_metadata.MATCHABLE re-exports this
CLEARED = None  # a cleared field: JSON null with source "user"; an image removal is {"removed": True, "tag": ...}
Outcome = Literal["write", "same", "empty", "kept_edit", "kept_lock", "kept_higher"]


def is_tombstone(value: Any) -> bool:
    """A cleared value or a removed image entry: readers treat it as absent (ADR 0016)."""
    return value is None or (isinstance(value, dict) and value.get("removed") is True)


def no_match_locked(title: MediaTitle) -> bool:
    """Unmatch stores ``provider_ids = {}`` as ``user``: a deliberate no-match, never fetched."""
    return (title.field_sources or {}).get("provider_ids") == "user" and "Tmdb" not in (title.provider_ids or {})


def _get(title: MediaTitle, field: str) -> Any:
    if field == "added_at":  # a datetime column; kept values are JSON, so an ISO string
        return title.added_at.isoformat() if title.added_at else None
    if field in TITLE_COLUMNS:
        return getattr(title, field)
    if field.startswith("images."):
        return (title.images or {}).get(field.removeprefix("images."))
    return (title.metadata_json or {}).get(field)


def _set(title: MediaTitle, field: str, value: Any) -> None:
    """Write a value; JSON columns are reassigned, never mutated, so SQLAlchemy persists them."""
    if field == "added_at":
        title.added_at = datetime.fromisoformat(value) if value else None
    elif field in TITLE_COLUMNS:
        setattr(title, field, value)
    elif field.startswith("images."):
        title.images = {**(title.images or {}), field.removeprefix("images."): value}
    else:
        title.metadata_json = {**(title.metadata_json or {}), field: value}


def _drop(title: MediaTitle, field: str) -> None:
    if field == "added_at":
        title.added_at = None
    elif field in TITLE_COLUMNS:
        if field != "name":  # a title always has a name
            setattr(title, field, {} if field == "provider_ids" else None)
    elif field.startswith("images."):
        title.images = {k: v for k, v in (title.images or {}).items() if k != field.removeprefix("images.")}
    else:
        title.metadata_json = {k: v for k, v in (title.metadata_json or {}).items() if k != field}


def write_outcome(title: MediaTitle, field: str, value: Any, source: str) -> Outcome:
    """What ``apply_field`` would do, without doing it; the single decision (the refresh preview reads it).

    None with a non-user source is ``empty``; None with ``user`` is a clear (``write``). A non-user write loses to a
    ``user`` field (``kept_edit``) and to a locked item (``kept_lock``); a ``user`` write passes the lock. Then the rank rule.
    """
    rank = FIELD_SOURCE_RANK[source]  # an unknown source is a programming error
    if field not in TITLE_COLUMNS and field in MediaTitle.__table__.columns:
        raise ValueError(f"{field} is structural; set it directly, not through apply_field")
    if value is None and (source != "user" or field == "name"):  # a title always has a name
        return "empty"
    current = (title.field_sources or {}).get(field)
    if source != "user":
        if current == "user":
            return "kept_edit"
        if title.locked:
            return "kept_lock"
    if rank < FIELD_SOURCE_RANK.get(current, -1):
        return "kept_higher"
    return "write" if current != source or _get(title, field) != value else "same"


def would_apply(title: MediaTitle, field: str, value: Any, source: str) -> bool:
    """``write_outcome(...) == "write"``; pure (the refresh preview reads it)."""
    return write_outcome(title, field, value, source) == "write"


def _keep_incoming(title: MediaTitle, field: str, value: Any, source: str) -> None:
    """A write lost to a ``user`` field or a locked item: remember it when it is at least as good as what is kept."""
    kept = dict(title.source_values or {})  # deferred: loaded only here, on a blocked write
    entry, current = kept.get(field), (title.field_sources or {}).get(field)
    if entry and entry.get("source"):
        floor = FIELD_SOURCE_RANK[entry["source"]]
    else:
        floor = FIELD_SOURCE_RANK[current] if current and current != "user" else -1
    new = {"source": source, "value": value}
    if current == source and _get(title, field) == value:  # the source is back at the current value: an older kept entry is stale
        if entry:
            title.source_values = {k: v for k, v in kept.items() if k != field}
        return
    if FIELD_SOURCE_RANK[source] < floor or entry == new:
        return
    title.source_values = {**kept, field: new}


def keep_source_value(title: MediaTitle, field: str) -> None:
    """Before the first user write to ``field``: remember what its source gave. A field already ``user``, or one a
    locked item already holds an entry for, keeps its entry (it is already the best non-user value)."""
    current = (title.field_sources or {}).get(field)
    kept = dict(title.source_values or {})
    if current == "user" or field in kept:
        return
    title.source_values = {**kept, field: {"source": current, "value": _get(title, field)}}


def wake_refresh(title: MediaTitle, now: datetime) -> None:
    """A refresh is the only way an emptied or restored-from-TMDB field catches up."""
    if title.type in MATCHABLE_TYPES and not title.locked and not no_match_locked(title):
        title.metadata_due_at = now


def revert_field(title: MediaTitle, field: str, now: datetime) -> bool:
    """Unlock = revert (ADR 0016): the field takes back its kept source value, no network. False when not ``user``."""
    sources = dict(title.field_sources or {})
    if sources.get(field) != "user":
        return False
    kept = dict(title.source_values or {})
    entry = kept.pop(field, None)
    sources.pop(field)
    if entry and (entry.get("source") or field in ("added_at", "parent_id")):  # file-dated / structural: no source, a value
        _set(title, field, entry["value"])
        if entry.get("source"):
            sources[field] = entry["source"]
    else:
        _drop(title, field)
    title.field_sources, title.source_values = sources, kept
    if not entry or entry.get("source") in (None, "tmdb"):
        wake_refresh(title, now)
    return True


def set_item_locked(title: MediaTitle, locked: bool, now: datetime) -> bool:
    """"Lock this item": every non-user field write stops, and so does the refresh queue. Unlocking applies what
    the sources offered meanwhile (kept entries of non-``user`` fields) and wakes the title."""
    if bool(title.locked) == locked:
        return False
    title.locked = locked
    if locked:
        title.metadata_due_at = None
        return True
    kept = dict(title.source_values or {})
    for field, entry in list(kept.items()):
        if (title.field_sources or {}).get(field) != "user":
            if entry.get("source"):
                apply_field(title, field, entry["value"], entry["source"])
            del kept[field]
    title.source_values = kept
    wake_refresh(title, now)
    return True


def scanned_parent(title: MediaTitle, parent_id: str) -> None:
    """The scanner's structural parent write. A user-moved episode keeps its season; the folder's season is kept for revert."""
    if (title.field_sources or {}).get("parent_id") == "user":
        if (title.source_values or {}).get("parent_id", {}).get("value") != parent_id:
            title.source_values = {**(title.source_values or {}), "parent_id": {"source": None, "value": parent_id}}
    elif title.parent_id != parent_id:
        title.parent_id = parent_id


def synthetic_id(key: str) -> str:
    """Stable dashed uuid5 for an entity with no row of its own, e.g. ``synthetic_id("view:movies")``."""
    return str(uuid.uuid5(LUMINA_NAMESPACE, key))


def person_key(name: str) -> str:
    """One person across titles: casefolded, whitespace collapsed."""
    return " ".join(name.split()).casefold()


@functools.lru_cache(maxsize=65_536)  # uuid5 per credit dominated title tokenising (reco I2); a pure function of the name
def person_name_id(name: str) -> str:
    """The id of a person credited by name (NFO): the one Jellyfin clients already key such people by."""
    return synthetic_id(f"person-name:{person_key(name)}")


def jellyfin_id(uuid_str: str) -> str:
    """Lumina's dashed uuid -> the 32-hex form Jellyfin clients use."""
    return uuid.UUID(uuid_str).hex


def parse_item_id(value: object) -> str | None:
    """A client-supplied id (dashed or not, any case) -> Lumina's dashed lowercase uuid; None when invalid (callers 404)."""
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def to_ticks(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * TICKS_PER_SECOND)


def from_ticks(ticks: int | None) -> float | None:
    return None if ticks is None else ticks / TICKS_PER_SECOND


def apply_field(title: MediaTitle, field: str, value: Any, source: str) -> bool:
    """Write one title field when ``write_outcome`` says so; return whether anything changed.

    ``field`` is a column in TITLE_COLUMNS, ``images.<ImageType>``, or else a ``metadata_json`` key.
    None never overwrites from a scan or refresh, so an empty result never erases data; None from ``user`` is a
    clear (CLEARED). Dict values replace: callers merge provider ids before calling. A non-user write blocked by a
    ``user`` field or a locked item returns False and keeps its value in ``source_values`` for revert (ADR 0016).
    User edits go through ``keep_source_value`` first (the editor); Identify and Unmatch call this with ``user`` directly.
    """
    outcome = write_outcome(title, field, value, source)
    if outcome in ("kept_edit", "kept_lock"):
        _keep_incoming(title, field, value, source)
        return False
    if outcome != "write":
        return False
    _set(title, field, value)
    sources = title.field_sources or {}
    if sources.get(field) != source:
        title.field_sources = {**sources, field: source}
    return True


def refresh_added_at(db: Session, title_ids: Iterable[str]) -> None:
    """Re-date these titles (leaves before their season and series) from their files' recorded mtimes, never later."""
    ids = list(title_ids)
    if ids:
        for statement in RESCAN_ADDED_AT_SQL:
            db.execute(text(statement.format(ids=" AND id IN :ids")).bindparams(bindparam("ids", expanding=True)), {"ids": ids})
