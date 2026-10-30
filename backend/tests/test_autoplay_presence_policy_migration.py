"""The global autoplay-presence policy is an additive settings migration."""
from sqlalchemy import inspect, text

from app import db as database


def test_schema_42_adds_the_autoplay_presence_json_column_after_a_schema_41_restamp() -> None:
    database.init_db()
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE app_settings DROP COLUMN autoplay_presence"))
        connection.execute(text("PRAGMA user_version = 41"))

    database.init_db()
    assert "autoplay_presence" in {column["name"] for column in inspect(database.engine).get_columns("app_settings")}
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == 42
