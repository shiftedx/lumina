"""Schema step 8 on real upgrade paths: from v7 with production's
layout, from v1 through every step, and again after a rollback re-stamp."""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing

from sqlalchemy import create_engine, text

from app import db as db_module
from app.db import CATEGORY_BACKFILL_SQL, Base
from test_schema_version import STEP8_INDEXES, _add_filed_title, _init, _unwind_to_v1, _v7_database

CATEGORISED = "('movie', 'series', 'season', 'episode')"
EXPECTED = {
    "movie": "movies", "anime-movie": "anime", "show": "shows", "show-s1": "shows", "show-s1e1": "shows",
    "anime": "anime", "anime-s1": "anime", "anime-s1e1": "anime", "extra-anime": "anime", "saga": None,
}


def _fill_production_layout(connection) -> None:  # noqa: ANN001
    """Production's shape (spec Intent): Movies/, TV/Shows and TV/Anime under one TV root, an anime film under Movies,
    a show whose only anime file is its own extra, and a boxset."""
    _add_filed_title(connection, "movie", "movie", None, "Movies/Heat (1995)/Heat (1995).mkv")
    _add_filed_title(connection, "anime-movie", "movie", None, "Movies/Anime/Film (2020)/Film (2020).mkv")
    _add_filed_title(connection, "show", "series")
    _add_filed_title(connection, "show-s1", "season", "show")
    _add_filed_title(connection, "show-s1e1", "episode", "show-s1", "TV/Shows/Show B/Season 01/Show B S01E01.mkv")
    _add_filed_title(connection, "anime", "series")
    _add_filed_title(connection, "anime-s1", "season", "anime")
    _add_filed_title(connection, "anime-s1e1", "episode", "anime-s1", "TV/Anime/Show A/Season 01/Show A S01E01.mkv")
    _add_filed_title(connection, "extra-anime", "series", None, "TV/anime/Show C/extras/Interview.mkv")
    _add_filed_title(connection, "saga", "boxset")


def _assert_step8(engine) -> None:  # noqa: ANN001
    """Criterion 4 and the step's shape: version 8, the four partial indexes' exact WHERE, the default folders, no NULL."""
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        sql = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'media_titles'")).all())
        assert json.loads(connection.scalar(text("SELECT anime_folders FROM app_settings WHERE id = 1"))) == ["Anime"]
        assert connection.scalar(text(f"SELECT count(*) FROM media_titles WHERE type IN {CATEGORISED} AND category IS NULL")) == 0
    for name in STEP8_INDEXES[:2]:
        assert sql[name].endswith("WHERE type IN ('movie', 'series')"), sql[name]
    for name in STEP8_INDEXES[2:]:
        assert sql[name].endswith("WHERE type IN ('album', 'artist')"), sql[name]


def _snapshot(engine) -> tuple:  # noqa: ANN001
    with engine.connect() as connection:
        return (
            sorted(connection.execute(text("SELECT id, category FROM media_titles")).all()),
            connection.scalar(text("SELECT anime_folders FROM app_settings WHERE id = 1")),
            sorted(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'index'")).all()),
        )


def test_a_v7_database_sorts_productions_layout(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _v7_database(tmp_path)
    with engine.begin() as connection:
        _fill_production_layout(connection)
    upgraded = _init(tmp_path, monkeypatch)
    _assert_step8(upgraded)
    with upgraded.connect() as connection:
        assert dict(connection.execute(text("SELECT id, category FROM media_titles")).all()) == EXPECTED


def test_a_v1_database_upgrades_through_every_step_to_8(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Pinned interpretation 4: v1 predates media_titles (step 3), so only the step's shape and settings can be checked."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _unwind_to_v1(connection)
        connection.execute(text(
            "INSERT INTO app_settings (id, temp_root, archive_path, concurrency, max_active_jobs_per_user, min_free_disk_mb,"
            " max_playback_sessions, yt_dlp_defaults, ui_prefs, backup_daily, backup_keep, recording_keep_days,"
            " recording_max_gb, updated_at) VALUES (1, '/t', '/a', 1, 25, 2048, 2, '{}', '{}', 0, 7, 0, 0, '2026-09-25 00:00:00')"
        ))
        connection.execute(text("PRAGMA user_version = 1"))
    _assert_step8(_init(tmp_path, monkeypatch))


def test_step_8_runs_again_after_a_re_stamp_and_changes_nothing(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Re-stamping 7 and starting again skips the guarded ADD COLUMNs and writes no row."""
    engine = _v7_database(tmp_path)
    with engine.begin() as connection:
        _fill_production_layout(connection)
    upgraded = _init(tmp_path, monkeypatch)
    before = _snapshot(upgraded)
    with upgraded.begin() as connection:
        connection.execute(text("PRAGMA user_version = 7"))
    shutil.rmtree(tmp_path / "backups")  # the pre-upgrade backup name has 1 s resolution: a fast re-run would collide
    again = _init(tmp_path, monkeypatch)
    _assert_step8(again)
    assert _snapshot(again) == before
    with closing(sqlite3.connect(tmp_path / "app.db")) as raw:
        changes = raw.total_changes
        for statement in CATEGORY_BACKFILL_SQL:
            raw.execute(statement)
        raw.commit()
        assert raw.total_changes == changes  # only changed rows are written: none
