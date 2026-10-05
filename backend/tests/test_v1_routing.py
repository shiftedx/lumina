"""Deterministic source/actual-quality storage rules and the no-write dry run."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import DownloadJob, StorageRoot, User
from app.security import hash_password
from app.services.rate_limit import rate_limiter
from app.services.storage_routing import StorageRule, decide, render_folder

PASSWORD = "Test-only-passphrase-1"

EXAMPLE = [
    StorageRule(id="uhd", priority=10, sources=["youtube"], media_kinds=["video"], min_height=2160, target_root_id="uhd-root", relative_template="Video UHD"),
    StorageRule(id="yt", priority=20, sources=["youtube"], media_kinds=["video"], target_root_id="media-root", relative_template="YouTube/{height}"),
    StorageRule(id="live", priority=30, sources=["twitch", "kick"], media_kinds=["recording"], target_root_id="media-root", relative_template="Live archive/{source}"),
    StorageRule(id="audio", priority=90, media_kinds=["audio"], target_root_id="media-root", relative_template="Audio"),
    StorageRule(id="off", enabled=False, priority=0, target_root_id="media-root"),
]


@pytest.mark.parametrize(
    ("source", "kind", "height", "rule_id", "root", "folder"),
    [
        ("youtube", "video", 2160, "uhd", "uhd-root", "Video UHD"),
        ("youtube", "video", 1080, "yt", "media-root", "YouTube/1080p"),
        ("twitch", "recording", None, "live", "media-root", "Live archive/twitch"),
        ("kick", "recording", 720, "live", "media-root", "Live archive/kick"),
        ("youtube", "audio", None, "audio", "media-root", "Audio"),  # not shadowed by YouTube video rules
        ("soundcloud", "audio", None, "audio", "media-root", "Audio"),
        ("generic", "video", 480, None, "general", ""),
        ("mystery", "video", 1080, None, "general", ""),  # unknown providers normalize to generic
    ],
)
def test_routing_priority_and_audio(source, kind, height, rule_id, root, folder) -> None:  # noqa: ANN001
    decision = decide(EXAMPLE, "general", 7, source, kind, height)
    assert (decision.rule_id, decision.root_id, decision.folder, decision.revision) == (rule_id, root, folder, 7)


def test_routing_unknown_height_default() -> None:
    # A best/4K request with no inspected output never matches the UHD bound.
    bounded_only = [EXAMPLE[0]]
    decision = decide(bounded_only, "general", 1, "youtube", "video", None)
    assert (decision.rule_id, decision.root_id) == (None, "general")
    assert "unknown" in decision.reason
    assert decide(EXAMPLE, "general", 1, "youtube", "video", None).folder == "YouTube/unknown"


@pytest.mark.parametrize("template", ["../escape", "/abs", "a/../b", "{owner}", "{source.__class__}", ".hidden", "a\\b", "C:x"])
def test_folder_template_rejects_injection(template: str) -> None:
    with pytest.raises(ValueError):
        render_folder(template, "youtube", "video", 1080)


@pytest.fixture
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    parent = tmp_path / "mnt"
    parent.mkdir()
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        db.add(User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    rate_limiter.clear()
    client = TestClient(app, base_url="http://localhost")
    client.auth = ("admin", PASSWORD)
    for name, mode in (("uhd", "managed"), ("media", "managed"), ("movies", "external")):
        (parent / name).mkdir()
        response = client.post("/api/admin/storage/roots", json={"label": name, "container_path": str(parent / name), "mode": mode})
        assert response.status_code == 201
    roots = {root["label"]: root["id"] for root in client.get("/api/admin/storage/roots").json()}
    yield client, roots, parent
    client.close()
    rate_limiter.clear()


def _tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def test_routing_dryrun_no_writes(admin) -> None:  # noqa: ANN001
    client, roots, parent = admin
    rules = [
        {"id": "uhd", "priority": 10, "sources": ["youtube"], "media_kinds": ["video"], "min_height": 2160, "target_root_id": roots["uhd"], "relative_template": "Video UHD"},
        {"id": "yt", "priority": 20, "sources": ["youtube"], "target_root_id": roots["media"], "relative_template": "YouTube"},
    ]
    saved = client.put("/api/admin/storage/rules", json={"expected_revision": 0, "default_root_id": roots["media"], "rules": rules})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1

    before = _tree(parent)
    resolved = client.post("/api/admin/storage/resolve", json={"source": "youtube", "media_kind": "video", "height": 1080})
    assert resolved.status_code == 200
    body = resolved.json()
    assert (body["rule_id"], body["root_label"], body["relative_path"], body["rule_revision"], body["can_admit"]) == ("yt", "media", "YouTube", 1, True)
    unknown = client.post("/api/admin/storage/resolve", json={"source": "youtube", "media_kind": "video", "height": None}).json()
    assert unknown["rule_id"] == "yt"  # the unbounded rule, not the UHD guess
    assert _tree(parent) == before
    with db_module.SessionLocal() as db:
        assert db.query(DownloadJob).count() == 0

    # External targets, traversal, stale revisions and ambiguous priorities are refused.
    external = [{**rules[0], "target_root_id": roots["movies"]}]
    assert client.put("/api/admin/storage/rules", json={"expected_revision": 1, "default_root_id": None, "rules": external}).status_code == 422
    traversal = [{**rules[0], "relative_template": "../../etc"}]
    assert client.put("/api/admin/storage/rules", json={"expected_revision": 1, "default_root_id": None, "rules": traversal}).status_code == 422
    assert client.put("/api/admin/storage/rules", json={"expected_revision": 1, "default_root_id": roots["movies"], "rules": []}).status_code == 422
    assert client.put("/api/admin/storage/rules", json={"expected_revision": 0, "default_root_id": None, "rules": []}).status_code == 409
    clash = [rules[0], {**rules[1], "priority": 10}]
    assert client.put("/api/admin/storage/rules", json={"expected_revision": 1, "default_root_id": None, "rules": clash}).status_code == 422
    assert client.get("/api/admin/storage/rules").json()["revision"] == 1

    # A root that rules still target cannot be deregistered.
    assert client.delete(f"/api/admin/storage/roots/{roots['uhd']}").status_code == 409
    with db_module.SessionLocal() as db:
        assert db.get(StorageRoot, roots["uhd"]) is not None
