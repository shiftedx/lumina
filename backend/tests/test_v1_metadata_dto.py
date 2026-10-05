"""Member library/search/event DTOs expose only allowlisted public
metadata — never nested paths, headers, cookies or signed URLs — while the
server still serves media through its registered artifact."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import LibraryItem, User
from app.security import hash_password
from app.services.library import LibraryService
from app.services.library_search import LibrarySearchService
from app.services.media_artifacts import MediaArtifactService
from app.services.rate_limit import rate_limiter

PASSWORD = "Test-only-passphrase-1"
SECRETS = ("lumina-secret-dir", "SIGNED-TOKEN", "Cookie-Value", "Bearer-Leak")


@pytest.fixture
def shared_item(tmp_path: Path):
    media = settings.library_root / "lumina-secret-dir" / "film.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"not-really-mp4")
    signed = "https://cdn.example.test/v.mp4?sig=SIGNED-TOKEN"
    headers = {"Cookie": "Cookie-Value", "Authorization": "Bearer-Leak"}
    metadata = {
        "id": "abc123",
        "title": "Aurora walk",
        "channel": "Northern",
        "description": "Northern lights over the fjord",
        "webpage_url": "https://video.example.test/watch?v=abc123",
        "height": 1080,
        "vcodec": "avc1",
        "filepath": str(media),
        "filename": str(media),
        "thumbnail": str(media.with_suffix(".jpg")),
        "url": signed,
        "http_headers": headers,
        "cookies": "Cookie-Value",
        "formats": [{"url": signed, "http_headers": headers}],
        "requested_downloads": [{"filepath": str(media), "_filename": str(media), "http_headers": headers}],
        "tags": ["aurora", {"path": str(media)}],
        "original_url": "file:///lumina-secret-dir/film.mp4",
    }
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        owner = User(id="owner", username="owner", display_name="Owner", password_hash=hash_password(PASSWORD), role="viewer", is_active=True)
        member = User(id="member", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True)
        item = LibraryItem(id="shared-1", user_id=owner.id, visibility="shared", title="Aurora walk", file_path=None, status="available")
        db.add_all([owner, member, item])
        LibraryService._set_metadata(item, metadata)
        db.flush()
        LibrarySearchService(db).sync_item(item)
        MediaArtifactService(db).register_file(item, str(media))
        db.commit()
    rate_limiter.clear()
    client = TestClient(app, base_url="http://localhost")
    client.auth = ("member", PASSWORD)
    try:
        yield client, media
    finally:
        client.close()
        rate_limiter.clear()


def _assert_public(metadata: dict) -> None:
    dumped = json.dumps(metadata)
    for secret in SECRETS:
        assert secret not in dumped
    assert not {"filepath", "filename", "thumbnail", "url", "http_headers", "cookies", "formats", "requested_downloads"} & metadata.keys()


def test_shared_metadata_no_nested_path(shared_item) -> None:
    client, _ = shared_item
    detail = client.get("/api/library/shared-1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "file_path" not in body
    assert all(secret not in json.dumps(body) for secret in SECRETS)
    _assert_public(body["metadata_json"])
    # Useful display metadata survives with its types.
    assert body["metadata_json"]["channel"] == "Northern"
    assert body["metadata_json"]["height"] == 1080
    assert body["metadata_json"]["webpage_url"] == "https://video.example.test/watch?v=abc123"
    assert body["metadata_json"]["tags"] == ["aurora"]


def test_summary_and_event_same_redaction(shared_item) -> None:
    client, _ = shared_item
    listed = client.get("/api/library").json()["items"]
    searched = client.get("/api/search", params={"q": "aurora"}).json()
    assert [item["id"] for item in listed] == ["shared-1"]
    assert [item["id"] for item in searched["items"]] == ["shared-1"]
    with db_module.SessionLocal() as db:
        event = LibraryService(db)._event_payload(db.get(LibraryItem, "shared-1"))
    assert event["broadcast"] is True
    summaries = [listed[0], searched["items"][0], event["item"]]
    for summary in summaries:
        assert "file_path" not in summary
        assert all(secret not in json.dumps(summary) for secret in SECRETS)
        _assert_public(summary["metadata_json"])
    assert summaries[0]["metadata_json"] == summaries[1]["metadata_json"] == summaries[2]["metadata_json"]


def test_metadata_redaction_preserves_playback(shared_item) -> None:
    client, media = shared_item
    response = client.get("/api/library/shared-1/media")
    assert response.status_code == 200
    assert response.content == media.read_bytes()
    # The raw extraction evidence is kept server-side, not wiped.
    with db_module.SessionLocal() as db:
        stored = db.get(LibraryItem, "shared-1").metadata_json
    assert stored["filepath"] == str(media)
