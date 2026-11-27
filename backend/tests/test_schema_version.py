from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as db_module
from app.config import settings
from app.db import Base
from app.services.photo_face_index_schema import TRIGGER_NAMES


def _init(tmp_path, monkeypatch):  # noqa: ANN001, ANN202
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_module.init_db()
    return engine


def _columns(engine) -> dict[str, list[str]]:  # noqa: ANN001
    inspector = inspect(engine)
    return {table: sorted(column["name"] for column in inspector.get_columns(table)) for table in sorted(Base.metadata.tables)}


def test_fresh_database_matches_the_current_schema_snapshot_and_restarts(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    snapshot = json.loads((Path(__file__).parent / "schema_snapshot.json").read_text())
    assert snapshot["version"] == db_module.SCHEMA_VERSION == 44
    assert _columns(engine) == snapshot["columns"]
    with engine.begin() as connection:
        assert set(connection.scalars(text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_photo_face_index_%'"
        ))) == set(TRIGGER_NAMES)
        connection.execute(text(
            "INSERT INTO app_maintenance_state (key, value, updated_at) VALUES ('restart-proof', 'kept', CURRENT_TIMESTAMP)"
        ))
    db_module.init_db()
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == 44
        assert connection.scalar(text("SELECT value FROM app_maintenance_state WHERE key = 'restart-proof'")) == "kept"


@pytest.mark.parametrize("header_only", [False, True])
def test_startup_initializes_an_empty_database_file(header_only: bool) -> None:
    database = settings.data_dir / settings.database_filename
    database.touch()
    if header_only:
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        assert database.stat().st_size > 0
    with pytest.raises(db_module.SchemaVersionError, match="schema version 0"):
        db_module.validate_database_file(database)

    db_module.init_db()
    db_module.validate_database_file(database)


@pytest.mark.parametrize("interrupt_at", [1, 40])
def test_interrupted_first_start_can_restart(interrupt_at: int) -> None:
    interrupted = subprocess.run(
        [sys.executable, "-c", """
import os
import sys
from sqlalchemy import event
from app.db import engine, init_db

remaining = int(sys.argv[1])

@event.listens_for(engine, "before_cursor_execute")
def interrupt_schema_creation(_conn, _cursor, statement, *_args):
    global remaining
    if statement.lstrip().startswith("CREATE TABLE"):
        remaining -= 1
        if remaining == 0:
            os._exit(23)

init_db()
""", str(interrupt_at)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "LUMINA_DATA_DIR": str(settings.data_dir)},
        timeout=30,
    )
    assert interrupted.returncode == 23
    database = settings.data_dir / settings.database_filename
    assert database.is_file()
    db_module.init_db()
    db_module.validate_database_file(database)


@pytest.mark.parametrize(
    "script",
    [
        "PRAGMA user_version = 43; CREATE TABLE users (id TEXT PRIMARY KEY);",
        "PRAGMA user_version = 45; CREATE TABLE users (id TEXT PRIMARY KEY);",
        "CREATE TABLE users (id TEXT PRIMARY KEY);",
        "CREATE VIEW legacy_view AS SELECT 1;",
        "PRAGMA user_version = 43;",
    ],
)
def test_unsupported_or_unstamped_database_is_refused_before_mutation(tmp_path, monkeypatch, script) -> None:  # noqa: ANN001
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(script)
    before = database.read_bytes()
    with pytest.raises(db_module.SchemaVersionError, match="refusing to start"):
        _init(tmp_path, monkeypatch)
    assert database.read_bytes() == before


def test_stamped_but_corrupt_database_is_refused_before_mutation(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE users"))
    engine.dispose()
    database = tmp_path / "app.db"
    before = database.read_bytes()
    with pytest.raises(db_module.SchemaVersionError, match="incomplete or corrupt"):
        _init(tmp_path, monkeypatch)
    assert database.read_bytes() == before


def test_stamped_database_missing_a_live_trigger_is_refused_before_mutation(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_photo_face_index_insert"))
    engine.dispose()
    database = tmp_path / "app.db"
    before = database.read_bytes()
    with pytest.raises(db_module.SchemaVersionError, match="trg_photo_face_index_insert"):
        _init(tmp_path, monkeypatch)
    assert database.read_bytes() == before
