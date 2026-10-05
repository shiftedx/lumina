from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
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


ADDED_AT_SQL = _dating(_FILE_TIME)  # the upgrade: every title from its files
# A scan: a file replaced in place (a re-encode, an upgrade) never makes its movie or episode later.
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
# Schema step 8 derives with the default anime folders, ["Anime"], as a literal pattern so the step stays static SQL.
ANIME_DEFAULT_MATCH = "('/' || r.path || '/' || a.relative_path LIKE '%/Anime/%' ESCAPE '\\')"
CATEGORY_BACKFILL_SQL = tuple(statement.format(match=ANIME_DEFAULT_MATCH, scope="") for statement in CATEGORY_SQL)
# Step 9: a saved YouTube item's channel id, tried in order: metadata channel_id,
# uploader_id, then the /channel/UC… segment of channel_url or uploader_url. Anything that is not a 24-character UC id is NULL.
_UC_ID_GLOB = "UC" + "[0-9A-Za-z_-]" * 22


def _uc_id(expr: str) -> str:
    return f"CASE WHEN {expr} GLOB '{_UC_ID_GLOB}' THEN {expr} END"


def _uc_id_in_url(expr: str) -> str:
    segment = f"substr({expr}, instr({expr}, '/channel/') + 9, 24)"
    return f"CASE WHEN instr({expr}, '/channel/UC') > 0 THEN {_uc_id(segment)} END"


ITEM_CHANNEL_ID_SQL = "coalesce(" + ", ".join((
    _uc_id("json_extract(metadata_json, '$.channel_id')"),
    _uc_id("json_extract(metadata_json, '$.uploader_id')"),
    _uc_id_in_url("json_extract(metadata_json, '$.channel_url')"),
    _uc_id_in_url("json_extract(metadata_json, '$.uploader_url')"),
)) + ")"


# Additive upgrade steps, applied in order to a database stamped with an older
# version (after an automatic pre-upgrade backup). A model change ships with a
# new step here; tests/test_schema_version.py fails if the schema snapshot drifts.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE download_jobs ADD COLUMN acquisition_batch_id VARCHAR(36)",
        "ALTER TABLE download_jobs ADD COLUMN acquisition_entry_id VARCHAR(36)",
    ),
    3: (  # media-vault pass: the only step for the whole pass (new tables come from create_all)
        "ALTER TABLE library_items ADD COLUMN title_id VARCHAR(36)",
        "ALTER TABLE library_items ADD COLUMN extra_type VARCHAR(24)",
        "CREATE INDEX ix_library_items_title_id ON library_items (title_id)",
        "ALTER TABLE media_artifacts ADD COLUMN analysis JSON",
        "ALTER TABLE transcripts ADD COLUMN derived_from VARCHAR(80)",
        "ALTER TABLE transcript_cues ADD COLUMN words JSON",
        "ALTER TABLE asr_jobs ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'asr'",
        "ALTER TABLE asr_jobs ADD COLUMN params JSON",
        "CREATE INDEX ix_asr_jobs_state_created ON asr_jobs (state, created_at)",
        "ALTER TABLE playback_progress ADD COLUMN dismissed_at DATETIME",
        "ALTER TABLE household_collections ADD COLUMN rules JSON",
        "ALTER TABLE app_settings ADD COLUMN jellyfin_enabled BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN jellyfin_server_key VARCHAR(64)",
        "ALTER TABLE app_settings ADD COLUMN tmdb_api_key TEXT",
        "ALTER TABLE app_settings ADD COLUMN metadata_language VARCHAR(16) NOT NULL DEFAULT 'en-US'",
        "ALTER TABLE app_settings ADD COLUMN ai_embedding_model VARCHAR(200)",
        "ALTER TABLE app_settings ADD COLUMN ai_features_disabled JSON NOT NULL DEFAULT '[]'",
        "ALTER TABLE app_settings ADD COLUMN introdb_enabled BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN hwaccel VARCHAR(8) NOT NULL DEFAULT 'auto'",
        "ALTER TABLE app_settings ADD COLUMN transcode_cache_gb INTEGER NOT NULL DEFAULT 10",
    ),
    4: (  # on-device models: the admin's active model per role and the thread cap
        "ALTER TABLE app_settings ADD COLUMN local_search_model VARCHAR(64)",
        "ALTER TABLE app_settings ADD COLUMN local_speech_model VARCHAR(64)",
        "ALTER TABLE app_settings ADD COLUMN model_threads INTEGER",
    ),
    5: (  # Jellyfin history import: the admin's Jellyfin server address
        "ALTER TABLE app_settings ADD COLUMN jellyfin_import_url TEXT",
    ),
    6: (  # gallery foundation: wall keyset indexes (title_artwork and client_metric_days come from create_all)
        "CREATE INDEX IF NOT EXISTS ix_media_titles_type_sort ON media_titles (type, coalesce(sort_name, name) COLLATE NOCASE, id) "
        "WHERE type IN ('movie', 'series')",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_type_created ON media_titles (type, created_at DESC, id) WHERE type IN ('movie', 'series')",
    ),
    7: (  # Recently added by when the files arrived, dated from the scan's recorded mtimes (no filesystem access)
        "ALTER TABLE media_titles ADD COLUMN added_at DATETIME",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_type_added ON media_titles (type, coalesce(added_at, created_at) DESC, id) "
        "WHERE type IN ('movie', 'series')",
        *(statement.format(ids="") for statement in ADDED_AT_SQL),
    ),
    8: (  # Library gallery: categories, anime folders, music indexes (album and artist are new type values, no DDL)
        "ALTER TABLE media_titles ADD COLUMN category VARCHAR(8)",
        "ALTER TABLE app_settings ADD COLUMN anime_folders JSON NOT NULL DEFAULT '[\"Anime\"]'",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_category_sort ON media_titles (category, coalesce(sort_name, name) COLLATE NOCASE, id) "
        "WHERE type IN ('movie', 'series')",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_category_added ON media_titles (category, coalesce(added_at, created_at) DESC, id) "
        "WHERE type IN ('movie', 'series')",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_music_sort ON media_titles (type, coalesce(sort_name, name) COLLATE NOCASE, id) "
        "WHERE type IN ('album', 'artist')",
        "CREATE INDEX IF NOT EXISTS ix_media_titles_music_added ON media_titles (type, coalesce(added_at, created_at) DESC, id) "
        "WHERE type IN ('album', 'artist')",
        *CATEGORY_BACKFILL_SQL,
    ),
    9: (  # recommendations: watch depth and stable channel keys (reco_events, reco_pool, remote_media come from create_all)
        "ALTER TABLE playback_progress ADD COLUMN max_fraction FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE playback_progress ADD COLUMN plays INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE playback_progress ADD COLUMN completions INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE remote_playback_progress ADD COLUMN max_fraction FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE remote_playback_progress ADD COLUMN plays INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE remote_playback_progress ADD COLUMN completions INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE remote_playback_progress ADD COLUMN channel_key VARCHAR(255)",
        "ALTER TABLE library_items ADD COLUMN channel_key VARCHAR(255)",
        "ALTER TABLE member_recommendation_suppressions ADD COLUMN channel_key VARCHAR(255)",
        # Guarded by plays = 0, so upgrading again after a rollback never overwrites counts 1.9.0 wrote.
        "UPDATE playback_progress SET plays = 1, completions = CASE WHEN completed THEN 1 ELSE 0 END, "
        "max_fraction = CASE WHEN completed THEN 1.0 WHEN duration_seconds > 0 THEN min(1.0, position_seconds * 1.0 / duration_seconds) ELSE 0 END "
        "WHERE plays = 0",
        "UPDATE remote_playback_progress SET plays = 1, completions = CASE WHEN completed THEN 1 ELSE 0 END, "
        "max_fraction = CASE WHEN completed THEN 1.0 WHEN duration_seconds > 0 THEN min(1.0, position_seconds / duration_seconds) ELSE 0 END "
        "WHERE plays = 0 AND cleared = 0",
        f"UPDATE library_items SET channel_key = 'https://www.youtube.com/channel/' || {ITEM_CHANNEL_ID_SQL} "
        f"WHERE channel_key IS NULL AND extractor LIKE 'youtube%' AND {ITEM_CHANNEL_ID_SQL} IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_library_items_user_channel ON library_items (user_id, channel_key)",
    ),
    10: (  # 2.1.0: library automation + metadata editor. title_edits, title_uploads and
           # library_watch_dirs come from create_all. Additive only; rollback = docker/README.md row "2.0.1 (schema 9)".
        "ALTER TABLE storage_roots ADD COLUMN scan_schedule VARCHAR(8) NOT NULL DEFAULT 'off'",
        "ALTER TABLE storage_roots ADD COLUMN watch_enabled BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE storage_roots ADD COLUMN watch_interval_s INTEGER NOT NULL DEFAULT 300",
        "ALTER TABLE app_settings ADD COLUMN library_scan_night_hour INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE import_runs ADD COLUMN trigger VARCHAR(16)",
        "ALTER TABLE import_runs ADD COLUMN scope JSON",
        "CREATE INDEX IF NOT EXISTS ix_import_runs_root_created ON import_runs (root_id, created_at DESC)",
        "ALTER TABLE media_titles ADD COLUMN locked BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE media_titles ADD COLUMN source_values JSON NOT NULL DEFAULT '{}'",
        "ALTER TABLE app_settings ADD COLUMN members_edit_metadata BOOLEAN NOT NULL DEFAULT 0",
    ),
    11: (),  # 2.2.0: admin Activity. playback_history comes from create_all; rollback = drop it, user_version = 10 (docker/README.md).
    12: (  # 2.6.0 Requests (ADR 0018): media_requests, media_request_followers, arr_servers, request_policies come from create_all
        "ALTER TABLE app_settings ADD COLUMN requests_enabled BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN smtp_host TEXT",
        "ALTER TABLE app_settings ADD COLUMN smtp_port INTEGER",
        "ALTER TABLE app_settings ADD COLUMN smtp_security VARCHAR(8) NOT NULL DEFAULT 'starttls'",
        "ALTER TABLE app_settings ADD COLUMN smtp_username TEXT",
        "ALTER TABLE app_settings ADD COLUMN smtp_password TEXT",
        "ALTER TABLE app_settings ADD COLUMN smtp_from TEXT",
        "ALTER TABLE user_settings ADD COLUMN notify_email TEXT",
        "ALTER TABLE user_settings ADD COLUMN notify_requests BOOLEAN NOT NULL DEFAULT 1",
    ),
    13: (  # 2.8.0 member access (ADR 0019): member_access and screen_time come from create_all
        "ALTER TABLE media_titles ADD COLUMN rating VARCHAR(8)",
        "ALTER TABLE media_titles ADD COLUMN rating_rank INTEGER",
        "ALTER TABLE account_tokens ADD COLUMN email TEXT",
        "ALTER TABLE account_tokens ADD COLUMN access JSON",
        "ALTER TABLE account_tokens ADD COLUMN sent_at DATETIME",
        "ALTER TABLE app_settings ADD COLUMN public_address TEXT",
        "ALTER TABLE app_settings ADD COLUMN invite_permissions JSON NOT NULL DEFAULT '{}'",
    ),
    14: (  # 2.9.0 two-step verification and app passwords (app passwords are device_tokens rows of kind app_password).
           # Additive only; rollback = docker/README.md row "2.8.1 (schema 13)".
        "ALTER TABLE users ADD COLUMN totp_secret TEXT",
        "ALTER TABLE users ADD COLUMN totp_enabled_at DATETIME",
        "ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN totp_recovery JSON NOT NULL DEFAULT '[]'",
        "ALTER TABLE users ADD COLUMN totp_failures INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN totp_locked_until DATETIME",
        "ALTER TABLE app_settings ADD COLUMN require_owner_two_factor BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE device_tokens ADD COLUMN app_password_id VARCHAR(36)",
    ),
    # After 2.9.0: no DDL. Startup reseals 2.9.0 authenticator secrets under app-data/totp-key (two_factor.reseal_legacy); the
    # bump takes a pre-upgrade backup first and stops 2.9.0 opening secrets it cannot read (docker/README.md rollback).
    15: (),
    16: (),  # #164 household people editor: person_overrides comes from create_all; rollback = drop it, user_version = 15.
    17: ("ALTER TABLE app_settings ADD COLUMN local_address TEXT",),  # the local address beside the public one; additive
}
ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)")
SCHEMA_VERSION = max(MIGRATIONS, default=1)


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


def init_db() -> None:
    """Create the schema on a fresh database, upgrade an older one, refuse a newer one."""
    from app import models  # noqa: F401  (registers model metadata)
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
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version {version} is newer than this release's version {SCHEMA_VERSION}; "
            "refusing to start. Run the release that created it, or restore an older backup."
        )
    if 0 < version < SCHEMA_VERSION:
        from app.services.backups import create_backup

        create_backup("pre-upgrade")
    with engine.begin() as connection:
        # New tables first, so a step may index a table an older database lacks (step 6 on media_titles).
        # create_all never alters an existing table; the steps below do.
        Base.metadata.create_all(bind=connection)
        for step in range(version + 1 if version else SCHEMA_VERSION + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[step]:
                # create_all made the table with it (an older table's step), or a rollback re-stamped a newer database
                if (match := ADD_COLUMN.match(statement)) and match[2] in {c["name"] for c in inspect(connection).get_columns(match[1])}:
                    continue
                connection.execute(text(statement))
        connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
    ensure_search_index(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
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
