"""Schema step 10 (2.1.0): library automation and metadata editor columns, tables, defaults and the documented
rollback to 2.0.1. One test file for both specs."""
from __future__ import annotations

import re

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app import db as db_module
from app.db import Base
from app.models import ImportRun, MediaTitle, TitleUpload
from test_schema_version import NEW_TABLES, _init

AT = "2026-10-01 10:00:00.000000"
# docker/README.md, row "2.0.1 (schema 9)" (S6 copies these three statements verbatim).
ROLLBACK_SQL = (
    "DELETE FROM import_entries WHERE run_id IN (SELECT id FROM import_runs WHERE scope IS NOT NULL)",
    "DELETE FROM import_runs WHERE scope IS NOT NULL",
    "PRAGMA user_version = 9",
)


def _v9_database(tmp_path):  # noqa: ANN001, ANN202
    """A 2.0.1 database: today's tables with step 10 undone, stamped 9."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in reversed(db_module.MIGRATIONS[10]):
            if match := re.match(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+) ON", statement):
                connection.execute(text(f"DROP INDEX {match[1]}"))
            elif match := re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", statement):
                connection.execute(text(f"ALTER TABLE {match[1]} DROP COLUMN {match[2]}"))
        for table in NEW_TABLES[10]:
            connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text("PRAGMA user_version = 9"))
    return engine


def _insert_as_2_0_1(connection, suffix: str) -> None:  # noqa: ANN001
    """Rows written naming only 2.0.1 columns: every new NOT NULL column must have a SQL default."""
    connection.execute(text(
        "INSERT INTO storage_roots (id, label, path, mode, enabled, minimum_free_bytes, observation, created_at, updated_at)"
        " VALUES (:id, 'TV', :path, 'external', 1, 0, '{}', :at, :at)"
    ), {"id": f"root-{suffix}", "path": f"/tv-{suffix}", "at": AT})
    connection.execute(text(
        "INSERT INTO import_runs (id, root_id, user_id, visibility, state, cursor, counters, coverage, root_observation, created_at, updated_at)"
        " VALUES (:id, :root, 'u', 'shared', 'succeeded', '[]', '{}', 'complete', '{}', :at, :at)"
    ), {"id": f"run-{suffix}", "root": f"root-{suffix}", "at": AT})
    connection.execute(text(
        "INSERT INTO media_titles (id, type, key, name, provider_ids, field_sources, images, metadata_json, created_at, updated_at)"
        " VALUES (:id, 'movie', :key, 'Dune', '{}', '{}', '{}', '{}', :at, :at)"
    ), {"id": f"title-{suffix}", "key": f"movie:dune-{suffix}", "at": AT})


def _insert_settings(connection) -> None:  # noqa: ANN001
    connection.execute(text(
        "INSERT INTO app_settings (id, temp_root, archive_path, concurrency, max_active_jobs_per_user, min_free_disk_mb,"
        " max_playback_sessions, yt_dlp_defaults, ui_prefs, backup_daily, backup_keep, recording_keep_days,"
        " recording_max_gb, updated_at) VALUES (1, '/t', '/a', 1, 25, 2048, 2, '{}', '{}', 0, 7, 0, 0, :at)"
    ), {"at": AT})


def test_step_10_upgrades_a_v9_database_with_defaults(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _v9_database(tmp_path)
    with engine.begin() as connection:
        _insert_as_2_0_1(connection, "a")
        _insert_settings(connection)
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.execute(text(
            "SELECT scan_schedule, watch_enabled, watch_interval_s FROM storage_roots WHERE id = 'root-a'"
        )).one() == ("off", 0, 300)
        assert connection.execute(text(
            "SELECT library_scan_night_hour, members_edit_metadata FROM app_settings WHERE id = 1"
        )).one() == (3, 0)
        assert connection.execute(text("SELECT trigger, scope FROM import_runs WHERE id = 'run-a'")).one() == (None, None)
        assert connection.execute(text("SELECT locked, source_values FROM media_titles WHERE id = 'title-a'")).one() == (0, "{}")
        indexes = set(connection.scalars(text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'import_runs'")))
    assert "ix_import_runs_root_created" in indexes
    assert set(NEW_TABLES[10]) <= set(inspect(upgraded).get_table_names())
    from app.services import backups

    assert [item["schema_version"] for item in backups.list_backups() if item["kind"] == "pre-upgrade"] == [9]


def test_rollback_to_2_0_1_removes_scoped_runs_only_and_upgrading_again_keeps_everything(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    with engine.begin() as connection:
        _insert_as_2_0_1(connection, "a")
        _insert_settings(connection)
        connection.execute(text("UPDATE storage_roots SET scan_schedule = 'nightly', watch_enabled = 1 WHERE id = 'root-a'"))
        connection.execute(text("UPDATE app_settings SET library_scan_night_hour = 4, members_edit_metadata = 1"))
        connection.execute(text("UPDATE media_titles SET locked = 1, source_values = '{\"name\": {\"source\": \"nfo\", \"value\": \"Dune\"}}'"))
        connection.execute(text("UPDATE import_runs SET trigger = 'scheduled' WHERE id = 'run-a'"))
        connection.execute(text(
            "INSERT INTO import_runs (id, root_id, user_id, visibility, state, cursor, counters, coverage, root_observation,"
            " created_at, updated_at, trigger, scope) VALUES ('run-scoped', 'root-a', 'u', 'shared', 'needs_confirmation',"
            " '[]', '{}', 'complete', '{}', :at, :at, 'watch', '[{\"dir\": \"Show/Season 1\", \"deep\": false}]')"
        ), {"at": AT})
        for run_id in ("run-a", "run-scoped"):
            connection.execute(text(
                "INSERT INTO import_entries (run_id, relative_path, outcome, created_at) VALUES (:run, 'x.mkv', 'failed', :at)"
            ), {"run": run_id, "at": AT})
        for statement in ROLLBACK_SQL:
            connection.execute(text(statement))
    with engine.begin() as connection:  # 2.0.1 runs on the rolled-back database
        assert connection.scalar(text("PRAGMA user_version")) == 9
        assert list(connection.scalars(text("SELECT id FROM import_runs ORDER BY id"))) == ["run-a"]
        assert list(connection.scalars(text("SELECT run_id FROM import_entries"))) == ["run-a"]
        _insert_as_2_0_1(connection, "b")
    again = _init(tmp_path, monkeypatch)
    with again.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.execute(text(
            "SELECT scan_schedule, watch_enabled FROM storage_roots WHERE id = 'root-a'"
        )).one() == ("nightly", 1)
        assert connection.execute(text("SELECT library_scan_night_hour, members_edit_metadata FROM app_settings")).one() == (4, 1)
        assert connection.scalar(text("SELECT locked FROM media_titles WHERE id = 'title-a'")) == 1
        assert connection.scalar(text("SELECT trigger FROM import_runs WHERE id = 'run-a'")) == "scheduled"
        assert connection.execute(text(
            "SELECT scan_schedule, locked FROM storage_roots, media_titles WHERE storage_roots.id = 'root-b' AND media_titles.id = 'title-b'"
        )).one() == ("off", 0)


def test_full_run_scope_is_sql_null_and_a_scope_round_trips(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    with Session(engine) as session:
        session.add(ImportRun(id="full", root_id="r", user_id="u", state="succeeded", scope=None, trigger="manual"))
        session.add(ImportRun(id="scoped", root_id="r", user_id="u", state="succeeded", scope=[{"dir": "Show", "deep": True}], trigger="watch"))
        session.commit()
    with engine.connect() as connection:
        assert list(connection.scalars(text("SELECT id FROM import_runs WHERE scope IS NULL"))) == ["full"]
    with Session(engine) as session:
        assert session.get(ImportRun, "scoped").scope == [{"dir": "Show", "deep": True}]
        assert session.scalar(select(ImportRun.scope).where(ImportRun.id == "full")) is None


def test_large_columns_are_deferred() -> None:
    assert MediaTitle.__mapper__.column_attrs["source_values"].deferred
    assert TitleUpload.__mapper__.column_attrs["data"].deferred
