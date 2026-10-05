from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as db_module
from app.config import settings
from app.db import Base
from app.models import AppSettings, StorageRoot


def _init(tmp_path, monkeypatch):  # noqa: ANN001, ANN202
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_module.init_db()
    return engine


def test_fresh_database_is_created_and_stamped(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
    db_module.init_db()  # restart on the same version is a no-op


@pytest.mark.parametrize(
    "script",
    [
        "PRAGMA user_version = 99; CREATE TABLE users (id TEXT PRIMARY KEY);",
        "CREATE TABLE users (id TEXT PRIMARY KEY);",  # tables but no stamp
    ],
)
def test_foreign_schema_version_refuses_to_start_and_changes_nothing(tmp_path, monkeypatch, script) -> None:  # noqa: ANN001
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.executescript(script)
    with pytest.raises(db_module.SchemaVersionError, match="refusing to start"):
        _init(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "app.db") as connection:
        assert [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["users"]


def _columns(engine) -> dict[str, list[str]]:  # noqa: ANN001
    inspector = inspect(engine)
    return {table: sorted(column["name"] for column in inspector.get_columns(table)) for table in sorted(Base.metadata.tables)}


# Tables a step's create_all adds; the step's ALTER/CREATE INDEX statements cover existing tables.
NEW_TABLES = {
    3: ("media_titles", "device_tokens", "member_favorites", "people", "search_embeddings", "series_recaps"),
    6: ("title_artwork", "client_metric_days"),
    9: ("remote_media", "reco_pool", "reco_events"),
    10: ("title_edits", "title_uploads", "library_watch_dirs"),
    11: ("playback_history",),
    12: ("media_requests", "media_request_followers", "arr_servers", "request_policies"),
    13: ("member_access", "screen_time"),
    16: ("person_overrides",),
}



def _indexes(engine) -> dict[str, list[str]]:  # noqa: ANN001
    inspector = inspect(engine)
    return {table: sorted(index["name"] for index in inspector.get_indexes(table)) for table in sorted(Base.metadata.tables)}


def _unwind_to_v1(connection) -> None:  # noqa: ANN001
    """Shape a current database like a v1 one: undo every step >= 2, newest first, indexes before their columns."""
    for step in sorted(db_module.MIGRATIONS, reverse=True):
        for statement in reversed(db_module.MIGRATIONS[step]):
            if match := re.match(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+) ON", statement):
                connection.execute(text(f"DROP INDEX {match[1]}"))
            elif match := re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", statement):
                connection.execute(text(f"ALTER TABLE {match[1]} DROP COLUMN {match[2]}"))
            elif statement.startswith("UPDATE "):
                continue  # a backfill leaves nothing to undo once its column is gone
            else:
                raise AssertionError(f"teach _unwind_to_v1 to undo: {statement}")
        for table in NEW_TABLES.get(step, ()):
            connection.execute(text(f"DROP TABLE {table}"))


def test_older_database_is_backed_up_then_upgraded_in_place(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "fresh").mkdir()
    fresh = _init(tmp_path / "fresh", monkeypatch)
    expected_columns, expected_indexes = _columns(fresh), _indexes(fresh)
    old = tmp_path / "old"
    old.mkdir()
    engine = create_engine(f"sqlite:///{old / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _unwind_to_v1(connection)
        connection.execute(text(
            "INSERT INTO download_jobs (id, source_url, status, format_selection, output_profile, attempts, created_at)"
            " VALUES ('job-1', 'https://example.com/v', 'completed', '{}', '{}', '[]', '2026-09-25 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO asr_jobs (id, library_item_id, model_id, state, created_at)"
            " VALUES ('asr-1', 'item-1', 'whisper', 'succeeded', '2026-09-25 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO app_settings (id, temp_root, archive_path, concurrency, max_active_jobs_per_user, min_free_disk_mb,"
            " max_playback_sessions, yt_dlp_defaults, ui_prefs, backup_daily, backup_keep, recording_keep_days,"
            " recording_max_gb, updated_at) VALUES (1, '/t', '/a', 1, 25, 2048, 2, '{}', '{}', 0, 7, 0, 0, '2026-09-25 00:00:00')"
        ))
        connection.execute(text("PRAGMA user_version = 1"))
    upgraded = _init(old, monkeypatch)
    assert _columns(upgraded) == expected_columns
    assert _indexes(upgraded) == expected_indexes  # a missing CREATE INDEX in a step fails here
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.scalar(text("SELECT status FROM download_jobs WHERE id = 'job-1'")) == "completed"
        assert connection.scalar(text("SELECT kind FROM asr_jobs WHERE id = 'asr-1'")) == "asr"
        assert connection.scalar(text("SELECT ai_features_disabled FROM app_settings WHERE id = 1")) == "[]"
    from app.services import backups

    [manifest] = [item for item in backups.list_backups() if item["kind"] == "pre-upgrade"]
    assert manifest["schema_version"] == 1
    assert backups.verify_file(backups.backup_path(manifest["name"])) == []


def test_v3_adds_the_media_vault_schema(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    columns = _columns(_init(tmp_path, monkeypatch))
    assert db_module.SCHEMA_VERSION >= 3
    assert set(NEW_TABLES[3]) <= set(columns)
    assert {"field_sources", "boxset_id", "metadata_due_at"} <= set(columns["media_titles"])
    assert {"title_id", "extra_type"} <= set(columns["library_items"])
    assert {"kind", "scope", "token_digest", "device_id"} <= set(columns["device_tokens"])
    assert {"kind", "params"} <= set(columns["asr_jobs"])
    assert "analysis" in columns["media_artifacts"]
    assert "derived_from" in columns["transcripts"]
    assert "words" in columns["transcript_cues"]
    assert "dismissed_at" in columns["playback_progress"]
    assert "rules" in columns["household_collections"]
    assert {
        "jellyfin_enabled", "jellyfin_server_key", "tmdb_api_key", "metadata_language",
        "ai_embedding_model", "ai_features_disabled", "introdb_enabled", "hwaccel", "transcode_cache_gb",
    } <= set(columns["app_settings"])


def test_newer_database_is_refused(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute(f"PRAGMA user_version = {db_module.SCHEMA_VERSION + 1}")
        connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    with pytest.raises(db_module.SchemaVersionError, match="newer than this release"):
        _init(tmp_path, monkeypatch)


def test_model_changes_ship_with_a_migration(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """If this fails: add a MIGRATIONS step in app/db.py, then refresh tests/schema_snapshot.json."""
    import json
    from pathlib import Path

    snapshot = json.loads((Path(__file__).parent / "schema_snapshot.json").read_text())
    assert snapshot["version"] == db_module.SCHEMA_VERSION
    assert _columns(_init(tmp_path, monkeypatch)) == snapshot["columns"]


def test_v4_adds_on_device_model_settings(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    columns = _columns(_init(tmp_path, monkeypatch))
    assert db_module.SCHEMA_VERSION >= 4
    assert {"local_search_model", "local_speech_model", "model_threads"} <= set(columns["app_settings"])


def test_v5_adds_the_jellyfin_import_address(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    columns = _columns(_init(tmp_path, monkeypatch))
    assert db_module.SCHEMA_VERSION >= 5
    assert "jellyfin_import_url" in columns["app_settings"]


def _sqlite_indexes(engine, table: str) -> set[str]:  # noqa: ANN001
    """Index names from sqlite_master: the inspector skips expression indexes such as ix_media_titles_type_sort."""
    with engine.connect() as connection:
        return set(connection.scalars(text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t"), {"t": table}))


def _assert_partial_wall_indexes(engine) -> None:  # noqa: ANN001
    """Partial so no episode or season lookup can drive from them (the 1.3.1 Shows-page failure class)."""
    with engine.connect() as connection:
        sql = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE name LIKE 'ix_media_titles_type_%'")).all())
    assert set(sql) == {"ix_media_titles_type_sort", "ix_media_titles_type_created", "ix_media_titles_type_added"}
    assert all(statement.endswith("WHERE type IN ('movie', 'series')") for statement in sql.values()), sql


def test_v6_adds_the_gallery_schema(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    columns = _columns(engine)
    assert db_module.SCHEMA_VERSION >= 6
    assert set(NEW_TABLES[6]) <= set(columns)
    assert columns["title_artwork"] == sorted([
        "title_id", "image_type", "source_key", "state", "width", "height", "preview", "preview_type", "dominant", "accent", "error", "updated_at",
    ])
    assert columns["client_metric_days"] == sorted(["day", "metric", "label", "histogram", "count"])
    assert {"ix_media_titles_type_sort", "ix_media_titles_type_created"} <= _sqlite_indexes(engine, "media_titles")
    _assert_partial_wall_indexes(engine)


def test_v5_database_gains_the_wall_indexes_on_upgrade(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The expression index is invisible to _indexes(), so the generic upgrade test cannot catch a missing step-6 statement."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in db_module.MIGRATIONS[6]:
            connection.execute(text(f"DROP INDEX {re.match(r'CREATE INDEX (?:IF NOT EXISTS )?(\w+) ON', statement)[1]}"))
        for table in NEW_TABLES[6]:
            connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text("PRAGMA user_version = 5"))
    upgraded = _init(tmp_path, monkeypatch)
    assert {"ix_media_titles_type_sort", "ix_media_titles_type_created"} <= _sqlite_indexes(upgraded, "media_titles")
    _assert_partial_wall_indexes(upgraded)
    assert set(NEW_TABLES[6]) <= set(inspect(upgraded).get_table_names())


def _v6_database(tmp_path, *, keep_column: bool = False):  # noqa: ANN001, ANN202
    """A v6 database; ``keep_column``: one a 1.5 image upgraded and the documented rollback re-stamped to 6."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for name in ("ix_media_titles_type_added", *STEP8_INDEXES):  # step 8's indexes read added_at too
            connection.execute(text(f"DROP INDEX {name}"))
        if not keep_column:
            connection.execute(text("ALTER TABLE media_titles DROP COLUMN added_at"))
        connection.execute(text("PRAGMA user_version = 6"))
    return engine


SCANNED = "2026-09-20 10:00:00.000000"


def _add_title(connection, title_id: str, kind: str, parent: str | None = None, *files: tuple[int | None, str | None]) -> None:  # noqa: ANN001
    """A title created by the first scan, with one library item per (file mtime_ns, extra type)."""
    connection.execute(text(
        "INSERT INTO media_titles (id, type, parent_id, key, name, provider_ids, field_sources, images, metadata_json, created_at, updated_at)"
        " VALUES (:id, :type, :parent, :id, :id, '{}', '{}', '{}', '{}', '2026-09-20 10:00:00.000000', '2026-09-20 10:00:00.000000')"
    ), {"id": title_id, "type": kind, "parent": parent})
    for n, (mtime_ns, extra) in enumerate(files):
        item, artifact = f"{title_id}-i{n}", f"{title_id}-a{n}"
        connection.execute(text(
            "INSERT INTO library_items (id, user_id, visibility, title, title_id, extra_type, status, kind, metadata_json, created_at, updated_at)"
            " VALUES (:item, 'u', 'shared', :item, :title, :extra, 'available', 'movie', '{}', :at, :at)"
        ), {"item": item, "title": title_id, "extra": extra, "at": SCANNED})
        connection.execute(text(
            "INSERT INTO media_artifacts (id, root_id, relative_path, ownership, lifecycle, mtime_ns, created_at, updated_at)"
            " VALUES (:a, 'r', :a, 'external', 'available', :m, :at, :at)"
        ), {"a": artifact, "m": mtime_ns, "at": SCANNED})
        connection.execute(
            text("INSERT INTO library_item_artifacts (library_item_id, artifact_id, created_at) VALUES (:item, :a, :at)"), {"item": item, "a": artifact, "at": SCANNED}
        )


def test_v7_dates_existing_titles_by_their_recorded_file_times(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The scan already stored each file's mtime (MediaArtifact.mtime_ns): the upgrade reads it, it never stats a root."""
    from sqlalchemy.orm import Session

    from app.models import MediaTitle

    day = 86_400 * 10**9
    t2020 = 1_577_836_800 * 10**9  # 2020-01-01T00:00:00Z
    engine = _v6_database(tmp_path)
    with engine.begin() as connection:
        _add_title(connection, "movie", "movie", None, (t2020 + 5 * day + 123_456_789, None), (t2020 + 400 * day, None), (t2020 + 900 * day, "trailer"))
        _add_title(connection, "no-file-time", "movie", None, (None, None))
        _add_title(connection, "pre-1970", "movie", None, (-500_000_000, None))  # a bogus clock: no usable file time
        _add_title(connection, "2098", "movie", None, (4_039_372_800 * 10**9, None))  # 2098-01-01: would top the list forever
        _add_title(connection, "2098-and-2020", "movie", None, (4_039_372_800 * 10**9, None), (t2020, None))
        _add_title(connection, "show", "series")
        _add_title(connection, "show-s1", "season", "show")
        _add_title(connection, "show-s2", "season", "show")
        _add_title(connection, "show-s1e1", "episode", "show-s1", (t2020 + 10 * day, None))
        _add_title(connection, "show-s2e1", "episode", "show-s2", (t2020 + 50 * day, None))
        _add_title(connection, "show-s2e2", "episode", "show-s2", (None, None))
    upgraded = _init(tmp_path, monkeypatch)
    with Session(upgraded) as session:
        added = {title.id: title.added_at for title in session.query(MediaTitle)}
    assert added == {
        "movie": datetime(2020, 1, 6, 0, 0, 0, 123_456),  # the earliest version; the trailer never counts
        "no-file-time": None,  # sorts by created_at
        "pre-1970": None,  # never a malformed '1969-12-31 23:59:59.-500000' that fails every read
        "2098": None, "2098-and-2020": datetime(2020, 1, 1),  # more than a day ahead is no file time either
        "show": datetime(2020, 2, 20), "show-s2": datetime(2020, 2, 20), "show-s1": datetime(2020, 1, 11),  # the newest episode
        "show-s1e1": datetime(2020, 1, 11), "show-s2e1": datetime(2020, 2, 20), "show-s2e2": None,
    }
    assert _sqlite_indexes(upgraded, "media_titles") >= {"ix_media_titles_type_added"}
    _assert_partial_wall_indexes(upgraded)


def test_v7_upgrades_a_rolled_back_database_that_already_has_the_column(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """docker/README's rollback re-stamps user_version 6 and keeps added_at: upgrading again must not fail on the column."""
    engine = _v6_database(tmp_path, keep_column=True)
    with engine.begin() as connection:
        _add_title(connection, "movie", "movie", None, (1_577_836_800 * 10**9, None))
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.scalar(text("SELECT added_at FROM media_titles")) == "2020-01-01 00:00:00.000000"


def test_v9_covering_index_survives_a_rollback_restamp(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The rollback re-stamps user_version 8: 1.8.0 never reads reco_events, so the extra index is inert and re-upgrading keeps it."""
    engine = _init(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA user_version = 8"))
    upgraded = _init(tmp_path, monkeypatch)
    assert "ix_reco_events_kind_surface" in _sqlite_indexes(upgraded, "reco_events")
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION


STEP8_INDEXES = ("ix_media_titles_category_sort", "ix_media_titles_category_added", "ix_media_titles_music_sort", "ix_media_titles_music_added")


def _v7_database(tmp_path, *, keep_columns: bool = False):  # noqa: ANN001, ANN202
    """A v7 database with one settings row and two roots (TV at /media/tv, Anime at /media/anime). ``keep_columns``: one a
    1.6 image upgraded and the documented rollback re-stamped to 7, which keeps both step-8 columns."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        # Before the columns go: the model's Python defaults fill the settings row's NOT NULL columns.
        connection.execute(AppSettings.__table__.insert().values(id=1, temp_root="/t", archive_path="/a"))
        for name in STEP8_INDEXES:
            connection.execute(text(f"DROP INDEX {name}"))
        if not keep_columns:
            connection.execute(text("ALTER TABLE media_titles DROP COLUMN category"))
            connection.execute(text("ALTER TABLE app_settings DROP COLUMN anime_folders"))
        connection.execute(StorageRoot.__table__.insert(), [
            {"id": "tv", "label": "TV", "path": "/media/tv", "mode": "external"}, {"id": "an", "label": "Anime", "path": "/media/anime", "mode": "external"},
        ])
        connection.execute(text("PRAGMA user_version = 7"))
    return engine


def _add_filed_title(connection, title_id: str, kind: str, parent: str | None = None, relative: str | None = None, root: str = "tv") -> None:  # noqa: ANN001
    """A title and, with ``relative``, one item whose file sits there under storage root ``root`` (step 8 reads paths only)."""
    connection.execute(text(
        "INSERT INTO media_titles (id, type, parent_id, key, name, provider_ids, field_sources, images, metadata_json, created_at, updated_at)"
        " VALUES (:id, :type, :parent, :id, :id, '{}', '{}', '{}', '{}', :at, :at)"
    ), {"id": title_id, "type": kind, "parent": parent, "at": SCANNED})
    if relative is None:
        return
    connection.execute(text(
        "INSERT INTO library_items (id, user_id, visibility, title, title_id, status, kind, metadata_json, created_at, updated_at)"
        " VALUES (:id, 'u', 'shared', :id, :id, 'available', 'movie', '{}', :at, :at)"
    ), {"id": title_id, "at": SCANNED})
    connection.execute(text(
        "INSERT INTO media_artifacts (id, root_id, relative_path, ownership, lifecycle, created_at, updated_at)"
        " VALUES (:a, :root, :rel, 'external', 'available', :at, :at)"
    ), {"a": f"{title_id}-a", "root": root, "rel": relative, "at": SCANNED})
    connection.execute(
        text("INSERT INTO library_item_artifacts (library_item_id, artifact_id, created_at) VALUES (:id, :a, :at)"),
        {"id": title_id, "a": f"{title_id}-a", "at": SCANNED},
    )


def test_v8_sorts_every_title_into_a_category_by_its_folders(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """On the upgrade: a folder named Anime in any case, the root's own path included, makes a title anime;
    a file name never does. Seasons and episodes follow their series; boxsets stay NULL; nothing else is NULL."""
    engine = _v7_database(tmp_path)
    with engine.begin() as connection:
        _add_filed_title(connection, "film", "movie", None, "Movies/Film (2020)/Film (2020).mkv")
        _add_filed_title(connection, "anime-film", "movie", None, "TV/ANIME/Film (2020)/Film (2020).mkv")
        _add_filed_title(connection, "file-named-anime", "movie", None, "Movies/Anime.mkv")
        _add_filed_title(connection, "rooted", "movie", None, "Film/Film.mkv", root="an")
        _add_filed_title(connection, "no-file", "movie")
        _add_filed_title(connection, "show", "series")
        _add_filed_title(connection, "show-s1", "season", "show")
        _add_filed_title(connection, "show-s1e1", "episode", "show-s1", "TV/Shows/Show/Season 1/Show S01E01.mkv")
        _add_filed_title(connection, "anime", "series")
        _add_filed_title(connection, "anime-s1", "season", "anime")
        _add_filed_title(connection, "anime-s1e1", "episode", "anime-s1", "TV/Anime/Show A/Season 1/Show A S01E01.mkv")
        _add_filed_title(connection, "saga", "boxset")
    upgraded = _init(tmp_path, monkeypatch)
    assert db_module.SCHEMA_VERSION >= 8
    with upgraded.connect() as connection:
        category = dict(connection.execute(text("SELECT id, category FROM media_titles")).all())
        folders = connection.scalar(text("SELECT anime_folders FROM app_settings WHERE id = 1"))
        sql = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'media_titles'")).all())
    assert category == {
        "film": "movies", "anime-film": "anime", "file-named-anime": "movies", "rooted": "anime", "no-file": "movies",
        "show": "shows", "show-s1": "shows", "show-s1e1": "shows",
        "anime": "anime", "anime-s1": "anime", "anime-s1e1": "anime", "saga": None,
    }
    assert json.loads(folders) == ["Anime"]
    for name in STEP8_INDEXES[:2]:
        assert sql[name].endswith("WHERE type IN ('movie', 'series')"), sql[name]
    for name in STEP8_INDEXES[2:]:
        assert sql[name].endswith("WHERE type IN ('album', 'artist')"), sql[name]
    _assert_partial_wall_indexes(upgraded)


def test_v8_upgrades_a_rolled_back_database_that_already_has_the_columns(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The rollback keeps category and anime_folders. Upgrading again skips both ADD COLUMNs and derives the
    titles 1.5.1 added meanwhile."""
    engine = _v7_database(tmp_path, keep_columns=True)
    with engine.begin() as connection:
        _add_filed_title(connection, "added-by-1.5.1", "movie", None, "TV/Anime/Film (2021)/Film (2021).mkv")
    upgraded = _init(tmp_path, monkeypatch)
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.scalar(text("SELECT category FROM media_titles WHERE id = 'added-by-1.5.1'")) == "anime"
    assert set(STEP8_INDEXES) <= _sqlite_indexes(upgraded, "media_titles")


def test_an_sqlite_older_than_3_33_refuses_to_start(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Step 8 and every category refresh use UPDATE … FROM, which SQLite added in 3.33."""
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 32, 3))
    with pytest.raises(RuntimeError, match="SQLite 3.33 or later is required"):
        _init(tmp_path, monkeypatch)


def test_v13_adds_member_access(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    columns = _columns(_init(tmp_path, monkeypatch))
    assert db_module.SCHEMA_VERSION >= 13
    assert set(NEW_TABLES[13]) <= set(columns)
    assert {"rating", "rating_rank"} <= set(columns["media_titles"])
    assert {"email", "access", "sent_at", "revoked_at"} <= set(columns["account_tokens"])
    assert {"public_address", "invite_permissions"} <= set(columns["app_settings"])
