"""Library categories: Movies, Shows or Anime, decided by folder names.

db.CATEGORY_SQL is the single source of truth. Schema step 8 runs it with the default folders; every scan batch runs it
for the titles it touched (``refresh_category``). An admin's new anime folders re-run it over the whole library on one
background thread (``recategorise``). Folder names are always bound parameters, never SQL text.
"""
from __future__ import annotations

import logging
import string
import threading
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.db import CATEGORY_SCOPES, CATEGORY_SQL, session_scope
from app.models import AppSettings, MediaTitle
from app.persistence import write_transaction

logger = logging.getLogger(__name__)

DEFAULT_FOLDERS = ["Anime"]  # AppSettings.anime_folders' default, for a database whose settings row does not exist yet
# SQLite's LIKE folds ASCII letters only: two lists equal under this folding sort every title the same way.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _pattern(folder: str) -> str:
    """The LIKE pattern for one whole folder segment. \\, % and _ in a name are literal (ESCAPE '\\')."""
    escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%/{escaped}/%"


_LINKED = "library_items ki JOIN library_item_artifacts kl ON kl.library_item_id = ki.id"
# Outside the step-8 backfill, a movie or series is re-derived only when it has no category yet or
# still has a linked file (for a series: its own or an episode's). A title whose files are gone (an unplugged NAS, a
# tombstoned folder) keeps its category instead of falling back to movies/shows. Seasons and episodes follow their series.
KEEP_FILELESS = (
    f" AND (m.category IS NULL OR EXISTS (SELECT 1 FROM {_LINKED} WHERE ki.title_id = m.id))",
    f" AND (s.category IS NULL OR EXISTS (SELECT 1 FROM {_LINKED} WHERE ki.title_id = s.id)"
    f" OR EXISTS (SELECT 1 FROM media_titles kse JOIN media_titles ke ON ke.parent_id = kse.id JOIN {_LINKED}"
    " WHERE kse.parent_id = s.id AND ki.title_id = ke.id))",
    "",
    "",
)


def category_sql(folders: Sequence[str], ids: Mapping[str, Sequence[str]] | None = None) -> list[TextClause]:
    """db.CATEGORY_SQL with ``folders`` bound as :p0…:pN, in run order (movies, series, seasons, episodes).

    ``ids`` ({"movie_ids": …, "series_ids": …}) narrows each statement to its db.CATEGORY_SCOPES entry; None derives
    every title. An empty ``folders`` makes nothing anime. Every statement carries KEEP_FILELESS (the step-8 backfill
    uses db.CATEGORY_BACKFILL_SQL, which has no such guard, so that no title is left NULL).
    """
    tests = " OR ".join(f"'/' || r.path || '/' || a.relative_path LIKE :p{n} ESCAPE '\\'" for n in range(len(folders)))
    match = f"({tests})" if folders else "0"
    patterns = {f"p{n}": _pattern(folder) for n, folder in enumerate(folders)}
    clauses = []
    for statement, scope, keep in zip(CATEGORY_SQL, CATEGORY_SCOPES, KEEP_FILELESS, strict=True):
        clause = text(statement.format(match=match, scope=(scope if ids is not None else "") + keep))
        if "{match}" in statement and patterns:
            clause = clause.bindparams(**patterns)
        if ids is not None:
            clause = clause.bindparams(*(bindparam(name, list(ids[name]), expanding=True) for name in ids if f":{name}" in scope))
        clauses.append(clause)
    return clauses


def anime_folders(db: Session) -> list[str]:
    """The saved anime folder names (Settings → Library & storage)."""
    folders = db.scalar(select(AppSettings.anime_folders).where(AppSettings.id == 1))
    return list(DEFAULT_FOLDERS if folders is None else folders)


def same_folders(first: Iterable[str], second: Iterable[str]) -> bool:
    """True when two folder lists sort every title the same way: equal sets under LIKE's case folding."""
    return {name.translate(_ASCII_LOWER) for name in first} == {name.translate(_ASCII_LOWER) for name in second}


def refresh_category(db: Session, title_ids: Iterable[str]) -> None:
    """Re-derive a scan batch's categories: its movies, and the series among it or above its seasons and
    episodes, with every season and episode of those series. Runs inside the caller's write transaction."""
    ids = list(title_ids)
    if not ids:
        return
    rows = db.execute(select(MediaTitle.id, MediaTitle.type, MediaTitle.parent_id).where(MediaTitle.id.in_(ids))).all()
    movie_ids = [title_id for title_id, kind, _parent in rows if kind == "movie"]
    series_ids = {title_id for title_id, kind, _parent in rows if kind == "series"}
    series_ids |= {parent for _id, kind, parent in rows if kind == "season" and parent}
    seasons = {parent for _id, kind, parent in rows if kind == "episode" and parent}
    if seasons:
        series_ids |= set(db.scalars(select(MediaTitle.parent_id).where(MediaTitle.id.in_(seasons), MediaTitle.parent_id.is_not(None))))
    scoped = category_sql(anime_folders(db), {"movie_ids": movie_ids, "series_ids": sorted(series_ids)})
    for clause, scope_ids in zip(scoped, (movie_ids, series_ids, series_ids, series_ids), strict=True):
        if scope_ids:
            db.execute(clause)


# ---- Re-sort when the anime folders change ----------------------------------------------------------------

_lock = threading.Lock()
_running = False  # a thread is re-sorting, or will run once more
_dirty = False  # a save arrived during the current run
_thread: threading.Thread | None = None


def recategorise() -> None:
    """Re-derive every title's category on a daemon thread. At most one run is active; saves during a run coalesce
    into one more run after it."""
    global _running, _dirty, _thread
    with _lock:
        if _running:
            _dirty = True
            return
        _running = True
        _thread = threading.Thread(target=_loop, name="recategorise", daemon=True)
        _thread.start()


def recategorising() -> bool:
    """MediaServerSettings.recategorising: a re-sort is running or queued."""
    with _lock:
        return _running


def wait(timeout: float = 10.0) -> None:
    """Block until the current run, and any run coalesced after it, ends."""
    thread = _thread
    if thread is not None:
        thread.join(timeout)


def _loop() -> None:
    global _running, _dirty
    while True:
        try:
            _run_once()
        except Exception as exc:  # noqa: BLE001 - a background thread: log the class only, never names or paths
            # A failed run is logged, not retried; the next scan batch re-derives what it touches and any
            # changed save re-runs the whole library. Retry here if failures show up in the logs.
            logger.error("Re-sorting titles into categories failed (%s)", type(exc).__name__)
        _clear_caches()
        with _lock:
            if not _dirty:
                _running = False
                return
            _dirty = False


def _run_once() -> None:
    """The four unscoped statements with the saved folders, in one write transaction (≤ 5 s on the reference host)."""
    with session_scope() as db, write_transaction(db, name="recategorise"):
        for clause in category_sql(anime_folders(db)):
            db.execute(clause)


def _clear_caches() -> None:
    """Facets and sections count by category; drop both per-process caches (routers import services, hence here)."""
    from app.routers import library_sections, titles

    titles._facets.clear()  # noqa: SLF001
    library_sections.clear()
