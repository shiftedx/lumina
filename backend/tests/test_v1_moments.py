"""Bookmarks are timestamped notes; they round-trip, stay private and respect the item's duration."""
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import LibraryItem, User
from app.security import get_current_user


def _user(identifier: str) -> User:
    return User(id=identifier, username=identifier, display_name=identifier.title(), role="viewer", is_active=True)


def test_moment_create_seek_roundtrip_private_and_range() -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        db.add_all([
            _user("owner"),
            _user("other"),
            LibraryItem(id="clip", user_id="owner", visibility="shared", title="Clip", duration=60, metadata_json={}, status="available"),
        ])
    current = {"user": _user("owner")}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        http = TestClient(app, base_url="http://localhost")
        created = http.post("/api/library/clip/notes", json={"body": "Great line", "timestamp_ms": 12_300})
        assert created.status_code == 201
        assert [(n["body"], n["timestamp_ms"]) for n in http.get("/api/library/clip/notes").json()] == [("Great line", 12_300)]

        assert http.post("/api/library/clip/notes", json={"body": "Past the end", "timestamp_ms": 61_001}).status_code == 400
        assert http.post("/api/library/clip/notes", json={"body": "Negative", "timestamp_ms": -1}).status_code == 422

        current["user"] = _user("other")  # a private bookmark is invisible and uneditable for another member
        assert http.get("/api/library/clip/notes").json() == []
        assert http.put(f"/api/library/notes/{created.json()['id']}", json={"body": "x", "visibility": "private"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
