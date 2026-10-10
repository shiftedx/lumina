"""Settings reads on the real Jellyfin request boundary."""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from app import db as database
from app.main import app
from app.models import AppSettings
from app.persistence import write_transaction
from title_support import jellyfin_household


def test_public_info_loads_settings_once_and_observes_the_next_request_disable(tmp_path: Path) -> None:
    jellyfin_household(tmp_path / "media")
    statements: list[str] = []

    def count_settings(_conn, _cursor, statement, _parameters, _context, _many):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT") and "app_settings" in statement.lower():
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", count_settings)
    client = TestClient(app, base_url="http://localhost")
    try:
        # Initialize the persistent ServerId before measuring steady requests.
        assert client.get("/System/Info/Public").status_code == 200
        statements.clear()
        response = client.get("/System/Info/Public")
        assert response.status_code == 200
        assert len(statements) == 1, "enabled check and ServerId must share the request's settings row"

        with database.SessionLocal() as db:
            with write_transaction(db, name="test_disable_jellyfin"):
                db.get(AppSettings, 1).jellyfin_enabled = False
        assert client.get("/System/Info/Public").status_code == 404
    finally:
        client.close()
        event.remove(database.engine, "before_cursor_execute", count_settings)
