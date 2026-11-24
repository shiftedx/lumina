from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings
from app.persistence import discard_after_commit_actions, drain_after_commit_actions


Base = declarative_base()
engine = create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False, "timeout": 10})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
# MediaTitle.added_at from the file times the scan already records (MediaArtifact.mtime_ns): a movie or episode's earliest
# version (extras never count; an mtime before 1970 or more than a day ahead is no file time), then a season's and a series' newest child.
# Written in SQLAlchemy's DateTime text so it compares with created_at and bound datetimes. Only changed rows are written.
# {ids} narrows a statement to some titles (media_titles.refresh_added_at).
_FILE_TIME = (
    "(SELECT strftime('%Y-%m-%d %H:%M:%S', min(a.mtime_ns) / 1000000000, 'unixepoch') || printf('.%06d', min(a.mtime_ns) / 1000 % 1000000)"
    " FROM library_items i JOIN library_item_artifacts l ON l.library_item_id = i.id JOIN media_artifacts a ON a.id = l.artifact_id"
    " WHERE i.title_id = media_titles.id AND i.extra_type IS NULL AND a.mtime_ns > 0"
    " AND a.mtime_ns <= CAST(strftime('%s', 'now') AS INTEGER) * 1000000000 + 86400000000000)"
)
_NEWEST_CHILD = "(SELECT max(c.added_at) FROM media_titles c WHERE c.parent_id = media_titles.id)"


# A scan never re-dates a title whose date is the user's or that is locked.
_UNPINNED = " AND NOT locked AND coalesce(json_extract(field_sources, '$.added_at'), '') != 'user'"


def _dating(leaf: str, guard: str = "") -> tuple[str, ...]:
    return tuple(
        f"UPDATE media_titles SET added_at = {value} WHERE type {kinds}{{ids}} AND added_at IS NOT {value}{guard}"
        for kinds, value in (("IN ('movie', 'episode')", leaf), ("= 'season'", _NEWEST_CHILD), ("= 'series'", _NEWEST_CHILD))
    )


# A scan: a file replaced in place (a re-encode) never makes its movie or episode later.
RESCAN_ADDED_AT_SQL = _dating(f"coalesce(min(added_at, {_FILE_TIME}), {_FILE_TIME}, added_at)", _UNPINNED)
# MediaTitle.category, one statement per level, run in this order: movies, series, then
# seasons and episodes inherit their series'. A title's path set is every artifact linked to its items (extras too) and,
# for a series, its episodes'. {match} tests one artifact's '/' || root path || '/' || relative path against the anime
# folders, so a root's own folders count; {scope} narrows a statement (CATEGORY_SCOPES), "" = every title.
# Only changed rows are written. UPDATE … FROM needs SQLite 3.33 (init_db checks).
_CATEGORY_FILES = (
    "library_items i JOIN library_item_artifacts l ON l.library_item_id = i.id"
    " JOIN media_artifacts a ON a.id = l.artifact_id JOIN storage_roots r ON r.id = a.root_id"
)
CATEGORY_SQL = (
    f"UPDATE media_titles SET category = d.v FROM (SELECT m.id, CASE WHEN EXISTS (SELECT 1 FROM {_CATEGORY_FILES}"
    " WHERE i.title_id = m.id AND {match}) THEN 'anime' ELSE 'movies' END AS v"
    " FROM media_titles m WHERE m.type = 'movie'{scope}) d WHERE media_titles.id = d.id AND media_titles.category IS NOT d.v",
    f"UPDATE media_titles SET category = d.v FROM (SELECT s.id, CASE WHEN EXISTS (SELECT 1 FROM {_CATEGORY_FILES}"
    " WHERE i.title_id = s.id AND {match})"
    f" OR EXISTS (SELECT 1 FROM media_titles se JOIN media_titles e ON e.parent_id = se.id JOIN {_CATEGORY_FILES}"
    " WHERE se.parent_id = s.id AND i.title_id = e.id AND {match}) THEN 'anime' ELSE 'shows' END AS v"
    " FROM media_titles s WHERE s.type = 'series'{scope}) d WHERE media_titles.id = d.id AND media_titles.category IS NOT d.v",
    "UPDATE media_titles SET category = (SELECT p.category FROM media_titles p WHERE p.id = media_titles.parent_id)"
    " WHERE type = 'season'{scope} AND category IS NOT (SELECT p.category FROM media_titles p WHERE p.id = media_titles.parent_id)",
    "UPDATE media_titles SET category = (SELECT p.category FROM media_titles p WHERE p.id = media_titles.parent_id)"
    " WHERE type = 'episode'{scope} AND category IS NOT (SELECT p.category FROM media_titles p WHERE p.id = media_titles.parent_id)",
)
# refresh_category's {scope} per statement (expanding bind parameters): the movies among the touched titles, then the
# touched series (with the series of touched seasons and episodes) and their seasons and episodes.
CATEGORY_SCOPES = (
    " AND m.id IN :movie_ids",
    " AND s.id IN :series_ids",
    " AND parent_id IN :series_ids",
    " AND parent_id IN (SELECT id FROM media_titles WHERE parent_id IN :series_ids)",
)

# Registers the current face-index schema's metadata hook before fresh schemas are created.
from app.services import photo_face_index_schema  # noqa: E402, F401

SCHEMA_VERSION = 44


class SchemaVersionError(RuntimeError):
    """The database was created by a different schema version."""


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Browser requests abort after 20 seconds. A remaining uncoordinated writer
    # must fail fast enough for the API to return a useful 503 instead of
    # surfacing as a misleading client-side request timeout.
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def init_storage() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.library_root.mkdir(parents=True, exist_ok=True)
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    settings.archive_path.touch(exist_ok=True)


def _validate_current_schema(connection) -> None:  # noqa: ANN001
    """Reject a stamped database whose mapped tables no longer match schema 44."""
    inspector = inspect(connection)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    missing_tables = expected_tables - actual_tables
    changed_tables = {
        table
        for table in expected_tables - missing_tables
        if {column["name"] for column in inspector.get_columns(table)}
        != {column.name for column in Base.metadata.tables[table].columns}
    }
    actual_indexes = set(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'index'").scalars())
    missing_indexes = {
        index.name
        for table in expected_tables - missing_tables
        for index in Base.metadata.tables[table].indexes
        if index.name is not None and index.name not in actual_indexes
    }
    if missing_tables or changed_tables or missing_indexes:
        detail = ", ".join(sorted(missing_tables | changed_tables | missing_indexes))
        raise SchemaVersionError(f"Database schema {SCHEMA_VERSION} is incomplete or corrupt ({detail}); refusing to start.")


def init_db() -> None:
    """Create the current schema on an empty database and validate a stamped current database."""
    from app import (  # noqa: F401  (registers model metadata)
        book_models, models, photo_face_index_models, photo_face_models, photo_models, photo_preview_models, photo_root_models,
    )
    from app.services.library_search import ensure_search_index

    if sqlite3.sqlite_version_info < (3, 33):  # UPDATE … FROM (CATEGORY_SQL)
        raise RuntimeError(f"SQLite 3.33 or later is required; this Python links SQLite {sqlite3.sqlite_version}.")

    init_storage()
    with engine.connect() as connection:
        version = int(connection.scalar(text("PRAGMA user_version")) or 0)
        has_tables = bool(inspect(connection).get_table_names())
    if version == 0 and has_tables:
        raise SchemaVersionError(
            "Database has tables but no Lumina schema version stamp; refusing to start. "
            "Point Lumina at an empty data directory."
        )
    if version != 0 and version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version {version} is unsupported by this release (requires {SCHEMA_VERSION}); "
            "refusing to start. Restore a current-schema backup."
        )
    if version == SCHEMA_VERSION:
        with engine.connect() as connection:
            _validate_current_schema(connection)
    else:
        with engine.begin() as connection:
            Base.metadata.create_all(bind=connection)
            connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
    ensure_search_index(engine)


@contextmanager
def session_scope(session_factory: Callable[[], Session] | None = None) -> Iterator[Session]:
    session = (SessionLocal if session_factory is None else session_factory)()
    try:
        yield session
        session.commit()
    except Exception:
        discard_after_commit_actions(session)
        session.rollback()
        raise
    else:
        # The teardown commit is the durable commit for writes made without a
        # seam call. It holds no writer admission slot, so the queued actions'
        # after-lock-release property holds trivially here.
        drain_after_commit_actions(session)
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


async def stream_session_scope() -> Callable[[], AbstractContextManager[Session]]:
    """Defer a direct stream's normal session lifecycle to its sync endpoint worker."""
    return session_scope
