"""The admin overview reports exact aggregates, never private content or invented zeros."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.main import app
from app.models import DownloadJob, LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot, User
from app.security import hash_password, utcnow
from app.routers import admin_overview
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

SECRET_TITLE = "Private birthday video"


def seed(factory, *, items: int = 3) -> None:
    now = utcnow()
    with factory.begin() as db:
        for status in ("completed", "completed", "queued", "running"):
            db.add(DownloadJob(id=str(uuid.uuid4()), user_id="u1", source_url="https://example.com/v", status=status))
        for error, age in (("HTTP Error 403", 1), ("HTTP Error 403", 2), ("Video unavailable", 3), ("Ancient failure", 30)):
            db.add(DownloadJob(id=str(uuid.uuid4()), user_id="u1", source_url="https://example.com/v", status="failed", error=error, finished_at=now - timedelta(days=age)))
        db.add(StorageRoot(id="root-a", label="Media", path="/media/a", mode="managed", minimum_free_bytes=5, observation={"state": "available", "checked_at": "2026-01-01T00:00:00+00:00", "free_bytes": 900, "total_bytes": 1000}))
        db.add(StorageRoot(id="root-b", label="NAS", path="/media/b", mode="external", observation={}))
        db.add(MediaArtifact(id="art-1", root_id="root-a", relative_path="a.mp4", ownership="managed", size=100))
        db.add(MediaArtifact(id="art-2", root_id="root-a", relative_path="b.mp4", ownership="managed", size=50))
        for index in range(items):
            item_id = str(uuid.uuid4())
            db.add(LibraryItem(id=item_id, user_id="u1", title=SECRET_TITLE, visibility="private", metadata_json={"description": SECRET_TITLE}, status="missing" if index == 0 else "available"))
            # Every item links to the same physical file: its bytes count once.
            db.add(LibraryItemArtifact(library_item_id=item_id, artifact_id="art-1"))


def test_metrics_counts_real_scope(client: TestClient, factory) -> None:
    seed(factory)
    login(client)
    body = client.get("/api/admin/overview").json()
    assert body["jobs_by_status"] == {"completed": 2, "queued": 1, "running": 1, "failed": 4}
    # Recent (7-day) failures grouped by reason, newest first.
    assert [(f["reason"], f["count"]) for f in body["recent_failures"]] == [("HTTP Error 403", 2), ("Video unavailable", 1)]
    assert body["library_items_by_status"] == {"available": 2, "missing": 1}
    roots = {root["id"]: root for root in body["roots"]}
    assert (roots["root-a"]["artifact_count"], roots["root-a"]["artifact_bytes"]) == (2, 150)
    assert (roots["root-a"]["state"], roots["root-a"]["free_bytes"], roots["root-a"]["minimum_free_bytes"]) == ("available", 900, 5)
    assert (roots["root-b"]["artifact_count"], roots["root-b"]["artifact_bytes"]) == (0, 0)
    assert body["event_streams"] == 0
    assert isinstance(body["persistence"], dict)
    assert (body["concurrency"], body["max_active_jobs_per_user"]) == (1, 25)


def test_metrics_no_private_content(client: TestClient, factory) -> None:
    seed(factory)
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    login(client)
    assert SECRET_TITLE not in client.get("/api/admin/overview").text
    member = TestClient(app, base_url="http://localhost")
    login(member, "member")
    denied = member.get("/api/admin/overview")
    assert denied.status_code == 403 and "jobs_by_status" not in denied.text


def test_metrics_bounded_query(client: TestClient, factory) -> None:
    seed(factory, items=400)
    login(client)
    statements: list[str] = []
    engine = factory.kw["bind"]
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert client.get("/api/admin/overview").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    # A fixed number of aggregate statements, whatever the row counts (session auth adds a few).
    assert len(statements) <= 12, statements
    assert not any("metadata_json" in sql for sql in statements)


def test_metrics_unavailable_not_zero(client: TestClient, factory, monkeypatch) -> None:
    seed(factory)
    monkeypatch.setattr(admin_overview, "_free_bytes", lambda root: None)
    login(client)
    body = client.get("/api/admin/overview").json()
    assert body["library_free_bytes"] is None
    unprobed = next(root for root in body["roots"] if root["id"] == "root-b")
    assert (unprobed["state"], unprobed["free_bytes"], unprobed["total_bytes"]) == (None, None, None)
