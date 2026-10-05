"""Incremental rescans, missing mounts and renamed files."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, PlaybackProgress, StorageRoot, User
from app.security import hash_password
from app.services.rate_limit import rate_limiter

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "mnt"
    (parent / "media").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add(User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    rate_limiter.clear()
    yield parent / "media"
    rate_limiter.clear()


@pytest.fixture
def admin(root: Path):
    with TestClient(app, base_url="http://localhost") as client:
        client.auth = ("admin", PASSWORD)
        client.root_id = client.post(
            "/api/admin/storage/roots", json={"label": "Media", "container_path": str(root), "mode": "external"}
        ).json()["id"]
        yield client


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def scan(admin: TestClient) -> dict:
    started = admin.post("/api/admin/imports", json={"root_id": admin.root_id})
    assert started.status_code == 202, started.text
    return admin.get(started.json()["status_url"]).json()


def statuses() -> dict[str, str]:
    with db_module.SessionLocal() as db:
        return {item.title: item.status for item in db.query(LibraryItem)}


def watch(title: str) -> str:
    with db_module.SessionLocal() as db:
        item = db.query(LibraryItem).filter_by(title=title).one()
        db.add(PlaybackProgress(id=f"p-{item.id}", user_id="admin", item_id=item.id, position_seconds=42))
        db.commit()
        return item.id


def test_mount_disconnect_keeps_catalog(admin: TestClient, root: Path, tmp_path: Path) -> None:
    write(root / "a.mkv", b"a")
    write(root / "b.mkv", b"b")
    assert scan(admin)["counters"]["indexed"] == 2
    watch("a")

    away = tmp_path / "unmounted"
    root.rename(away)
    offline = scan(admin)
    assert (offline["state"], offline["error"], offline["coverage"]) == ("failed", "offline", "incomplete")
    away.rename(root)

    with db_module.SessionLocal() as db:
        db.get(StorageRoot, admin.root_id).identity = "another-device"
        db.commit()
    swapped = scan(admin)
    assert (swapped["state"], swapped["error"]) == ("failed", "identity_mismatch")

    # Same device but suddenly empty (e.g. a bind mount lost its source): refuse to conclude absence.
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, admin.root_id).identity = None
        db.commit()
    stash = tmp_path / "stash"
    stash.mkdir()
    for path in root.iterdir():
        path.rename(stash / path.name)
    empty = scan(admin)
    assert (empty["state"], empty["error"], empty["counters"].get("missing", 0)) == ("partial", "root_empty", 0)

    assert statuses() == {"a": "available", "b": "available"}
    with db_module.SessionLocal() as db:
        assert db.query(PlaybackProgress).one().position_seconds == 42


def test_incomplete_scan_no_missing_mark(admin: TestClient, root: Path) -> None:
    write(root / "a" / "one.mkv", b"1")
    write(root / "b" / "two.mkv", b"2")
    write(root / "c" / "three.mkv", b"3")
    assert scan(admin)["counters"]["indexed"] == 3
    (root / "a" / "one.mkv").unlink()
    (root / "b").chmod(0o000)
    try:
        partial = scan(admin)
    finally:
        (root / "b").chmod(0o755)
    assert (partial["state"], partial["coverage"]) == ("partial", "incomplete")
    assert statuses() == {"one": "available", "two": "available", "three": "available"}

    complete = scan(admin)
    assert complete["state"] == "succeeded"
    assert complete["counters"]["missing"] == 1 and complete["counters"]["unchanged"] == 2
    assert statuses() == {"one": "missing", "two": "available", "three": "available"}  # tombstoned, not deleted

    write(root / "a" / "one.mkv", b"1")  # the file comes back
    assert scan(admin)["counters"]["updated"] == 1
    assert statuses()["one"] == "available"


def test_confirmed_move_preserves_identity(admin: TestClient, root: Path) -> None:
    write(root / "A" / "Film (2000).mkv", b"original-film-bytes")
    write(root / "B" / "Film (2000).mkv", b"a-different-cut")  # equal title, different bytes
    write(root / "C" / "clip.mp4", b"clip-bytes")
    write(root / "D" / "dup1.mp4", b"same")
    write(root / "D" / "dup2.mp4", b"same")
    assert scan(admin)["counters"]["indexed"] == 5
    with db_module.SessionLocal() as db:
        film_id = (
            db.query(LibraryItemArtifact.library_item_id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .filter(MediaArtifact.relative_path == "A/Film (2000).mkv")
            .scalar()
        )
        db.add(PlaybackProgress(id="p1", user_id="admin", item_id=film_id, position_seconds=42))
        db.commit()
    clip_id = admin.get("/api/library", params={"search": "clip"}).json()["items"][0]["id"]

    (root / "A" / "Film (2000).mkv").rename(root / "A" / "Film Renamed (2000).mkv")  # same inode
    shutil.copyfile(root / "C" / "clip.mp4", root / "C" / "clip-moved.mp4")  # new inode, same bytes
    (root / "C" / "clip.mp4").unlink()
    for name in ("dup1.mp4", "dup2.mp4"):
        (root / "D" / name).unlink()
    write(root / "D" / "dup3.mp4", b"same")  # matches two vanished files: ambiguous

    run = scan(admin)
    assert run["state"] == "succeeded"
    assert run["counters"]["relinked"] == 2 and run["counters"]["indexed"] == 1
    assert run["counters"]["missing"] == 2 and run["counters"]["unchanged"] == 1
    review = admin.get(f"{run['status_url']}/entries", params={"outcome": "review"}).json()
    assert [entry["relative_path"] for entry in review] == ["D/dup3.mp4"]

    renamed = admin.get(f"/api/library/{film_id}").json()
    assert renamed["title"] == "Film Renamed" and renamed["status"] == "available"
    assert admin.get(f"/api/library/{film_id}/media").content == b"original-film-bytes"
    copied = admin.get(f"/api/library/{clip_id}").json()
    assert copied["title"] == "clip-moved" and copied["status"] == "available"
    with db_module.SessionLocal() as db:
        assert db.get(PlaybackProgress, "p1").item_id == film_id
        titles = sorted(item.title for item in db.query(LibraryItem))
    assert titles == ["Film", "Film Renamed", "clip-moved", "dup1", "dup2", "dup3"]
