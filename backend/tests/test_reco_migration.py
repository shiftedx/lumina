"""Schema step 9: watch-depth and channel-key backfills, a re-upgrade after
the documented rollback, and the rollback's premise that step 9 only adds."""
from __future__ import annotations

import json
import re

from sqlalchemy import create_engine, inspect, text

from app import db as db_module
from app.db import Base
from test_schema_version import NEW_TABLES, _init

UC = "UC" + "abcdefghijklmnopqrst" + "_-"  # 24 characters
CHANNEL = f"https://www.youtube.com/channel/{UC}"
AT = "2026-09-20 10:00:00.000000"


def _v8_database(tmp_path, *, keep_step9: bool = False):  # noqa: ANN001, ANN202
    """A v8 database. ``keep_step9``: one 1.9.0 upgraded and the documented rollback re-stamped to 8 (tables and columns stay)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        if not keep_step9:
            for statement in reversed(db_module.MIGRATIONS[9]):
                if match := re.match(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+) ON", statement):
                    connection.execute(text(f"DROP INDEX {match[1]}"))
                elif match := re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", statement):
                    connection.execute(text(f"ALTER TABLE {match[1]} DROP COLUMN {match[2]}"))
            for table in NEW_TABLES[9]:
                connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text("PRAGMA user_version = 8"))
    return engine


def _progress(connection, row_id: str, position: int, duration: int | None, completed: bool) -> None:  # noqa: ANN001
    connection.execute(text(
        "INSERT INTO playback_progress (id, user_id, item_id, position_seconds, duration_seconds, completed, last_watched_at, created_at, updated_at)"
        " VALUES (:id, 'u', :id, :p, :d, :c, :at, :at, :at)"
    ), {"id": row_id, "p": position, "d": duration, "c": completed, "at": AT})


def _remote(connection, row_id: str, position: float, duration: float | None, completed: bool, cleared: bool = False) -> None:  # noqa: ANN001
    connection.execute(text(
        "INSERT INTO remote_playback_progress (id, user_id, source_identity, source_identity_key, source_url, position_seconds,"
        " duration_seconds, completed, checkpoint_client_id, checkpoint_sequence, checkpoint_revision, cleared, last_watched_at, created_at, updated_at)"
        " VALUES (:id, 'u', :id, :id, 'https://example.com/v', :p, :d, :c, 'c', 1, 1, :cl, :at, :at, :at)"
    ), {"id": row_id, "p": position, "d": duration, "c": completed, "cl": cleared, "at": AT})


def _item(connection, item_id: str, extractor: str, metadata: dict) -> None:  # noqa: ANN001
    connection.execute(text(
        "INSERT INTO library_items (id, user_id, visibility, extractor, title, status, kind, metadata_json, created_at, updated_at)"
        " VALUES (:id, 'u', 'shared', :ex, :id, 'available', 'video', :m, :at, :at)"
    ), {"id": item_id, "ex": extractor, "m": json.dumps(metadata), "at": AT})


def test_step_9_backfills_watch_depth_from_the_last_checkpoint(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _v8_database(tmp_path)
    with engine.begin() as connection:
        _progress(connection, "done", 100, 1000, True)
        _progress(connection, "half", 600, 1200, False)
        _progress(connection, "past-end", 1300, 1200, False)
        _progress(connection, "no-duration", 30, None, False)
        _remote(connection, "r-done", 10.0, 600.0, True)
        _remote(connection, "r-quarter", 150.0, 600.0, False)
        _remote(connection, "r-cleared", 0.0, None, False, cleared=True)
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        local = {row[0]: tuple(row[1:]) for row in connection.execute(text("SELECT id, max_fraction, plays, completions FROM playback_progress"))}
        remote = {row[0]: tuple(row[1:]) for row in connection.execute(text("SELECT id, max_fraction, plays, completions FROM remote_playback_progress"))}
    assert local == {"done": (1.0, 1, 1), "half": (0.5, 1, 0), "past-end": (1.0, 1, 0), "no-duration": (0.0, 1, 0)}
    # A cleared remote row already lost its position (remote_playback.clear): nothing to recover, so it stays at zero.
    assert remote == {"r-done": (1.0, 1, 1), "r-quarter": (0.25, 1, 0), "r-cleared": (0.0, 0, 0)}


def test_step_9_keys_saved_youtube_items_by_channel_id_from_any_of_four_fields(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """channel_id, then uploader_id, then the /channel/UC… segment of channel_url or uploader_url; else NULL."""
    engine = _v8_database(tmp_path)
    with engine.begin() as connection:
        _item(connection, "by-channel-id", "youtube", {"channel_id": UC, "uploader_id": "@handle"})
        _item(connection, "by-uploader-id", "youtube", {"uploader_id": UC})
        _item(connection, "by-channel-url", "Youtube", {"uploader_id": "@handle", "channel_url": f"https://www.youtube.com/channel/{UC}/videos"})
        _item(connection, "by-uploader-url", "youtube:tab", {"uploader_url": f"https://youtube.com/channel/{UC}"})
        _item(connection, "handle-only", "youtube", {"uploader_id": "@handle", "uploader_url": "https://www.youtube.com/@handle"})
        _item(connection, "too-long", "youtube", {"channel_id": UC + "x"})
        _item(connection, "too-short", "youtube", {"channel_id": UC[:-1]})
        _item(connection, "bad-character", "youtube", {"channel_id": UC[:-1] + "!"})
        _item(connection, "not-youtube", "twitch", {"channel_id": UC})
        _item(connection, "no-metadata", "youtube", {})
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        keys = dict(connection.execute(text("SELECT id, channel_key FROM library_items")).all())
    assert keys == {
        "by-channel-id": CHANNEL, "by-uploader-id": CHANNEL, "by-channel-url": CHANNEL, "by-uploader-url": CHANNEL,
        "handle-only": None, "too-long": None, "too-short": None, "bad-character": None, "not-youtube": None, "no-metadata": None,
    }
    assert "ix_library_items_user_channel" in {index["name"] for index in inspect(upgraded).get_indexes("library_items")}


def test_upgrading_again_after_the_rollback_keeps_what_1_9_0_counted(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """docker/README's rollback stamps 8 and keeps step 9's tables and columns; rows 1.8.0 wrote meanwhile have plays = 0."""
    engine = _v8_database(tmp_path, keep_step9=True)
    with engine.begin() as connection:
        _progress(connection, "counted-by-1.9.0", 900, 1000, True)
        connection.execute(text("UPDATE playback_progress SET plays = 3, completions = 2, max_fraction = 1.0 WHERE id = 'counted-by-1.9.0'"))
        _progress(connection, "written-by-1.8.0", 250, 1000, False)
        _item(connection, "keyed-by-1.9.0", "youtube", {"channel_id": UC})
        connection.execute(text("UPDATE library_items SET channel_key = 'https://www.youtube.com/channel/UCkeptkeptkeptkeptkept' WHERE id = 'keyed-by-1.9.0'"))
        connection.execute(text(
            "INSERT INTO reco_events (user_id, at, kind, target_kind, item_key) VALUES ('u', :at, 'impression', 'title', 't')"
        ), {"at": AT})
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        rows = {row[0]: tuple(row[1:]) for row in connection.execute(text("SELECT id, max_fraction, plays, completions FROM playback_progress"))}
        assert connection.scalar(text("SELECT channel_key FROM library_items")) == "https://www.youtube.com/channel/UCkeptkeptkeptkeptkept"
        assert connection.scalar(text("SELECT count(*) FROM reco_events")) == 1
    assert rows == {"counted-by-1.9.0": (1.0, 3, 2), "written-by-1.8.0": (0.25, 1, 0)}


def test_step_9_only_adds_so_1_8_0_reads_a_rolled_back_database(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The rollback is a re-stamp: every step-9 statement adds a column or index, or backfills a new column."""
    for statement in db_module.MIGRATIONS[9]:
        assert re.match(r"ALTER TABLE \w+ ADD COLUMN \w+ |CREATE INDEX IF NOT EXISTS |UPDATE ", statement), statement
    updated = {re.match(r"UPDATE (\w+) SET (\w+)", s).groups() for s in db_module.MIGRATIONS[9] if s.startswith("UPDATE ")}
    added = {re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", s).groups() for s in db_module.MIGRATIONS[9] if s.startswith("ALTER ")}
    assert updated <= added  # a backfill only ever writes a column step 9 added
    (tmp_path / "v8").mkdir()
    (tmp_path / "v9").mkdir()
    v8 = inspect(_v8_database(tmp_path / "v8"))
    v9 = inspect(_init(tmp_path / "v9", monkeypatch))
    for table in v8.get_table_names():
        assert {c["name"] for c in v8.get_columns(table)} <= {c["name"] for c in v9.get_columns(table)}, table
