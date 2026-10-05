"""Delete = DB change first, then a recoverable same-root quarantine move."""
from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot, User
from app.security import hash_password
from app.services import artifact_quarantine
from app.services.artifact_quarantine import QUARANTINE_DIRNAME, ArtifactQuarantineService
from app.services.library import LibraryService
from app.services.library_search import _index_in_sync
from app.services.rate_limit import rate_limiter
from app.services.storage_roots import StorageRootService

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def household() -> None:
    settings.library_root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        db.add_all([
            User(id=user_id, username=user_id, display_name=user_id, password_hash=hash_password(PASSWORD), role="viewer", is_active=True)
            for user_id in ("alice", "bob")
        ])
        db.commit()
    rate_limiter.clear()
    yield
    rate_limiter.clear()


@contextmanager
def _client(username: str) -> Iterator[TestClient]:
    client = TestClient(app, base_url="http://localhost")
    client.auth = (username, PASSWORD)
    try:
        yield client
    finally:
        client.close()


def _download(owner: str, path: Path, remote_id: str = "vid-1") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"precious-bytes")
    info = {"id": remote_id, "extractor_key": "Youtube", "title": "A video", "filepath": str(path)}
    with db_module.SessionLocal() as db:
        item = LibraryService(db).upsert_from_info(info, owner_user_id=owner)
        db.commit()
        return item.id


def _artifact() -> MediaArtifact:
    with db_module.SessionLocal() as db:
        return db.query(MediaArtifact).one()


def _quarantine_file(artifact_id: str) -> Path:
    return settings.library_root / QUARANTINE_DIRNAME / artifact_id


def _sweep(now: datetime | None = None) -> int:
    with db_module.SessionLocal() as db:
        return ArtifactQuarantineService(db).sweep(now)


def test_flush_failure_preserves_bytes(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)

    # Failure before commit: nothing moved, nothing changed.
    with db_module.SessionLocal() as db:
        LibraryService(db).delete_file(db.get(LibraryItem, item_id))
        db.rollback()
    assert media.read_bytes() == b"precious-bytes"
    assert _artifact().lifecycle == "available"

    # Commit durable, then the move fails (crash): DB truthfully says quarantined,
    # the bytes are still recoverable, and the sweep finishes the move.
    def crash(*_args: object) -> None:
        raise OSError("simulated crash during rename")

    real_rename = artifact_quarantine.os.rename
    monkeypatch.setattr(artifact_quarantine.os, "rename", crash)
    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/delete-file").json()["status"] == "missing"
        assert client.get(f"/api/library/{item_id}/media").status_code == 404
    assert media.read_bytes() == b"precious-bytes"
    assert _artifact().lifecycle == "quarantined"

    monkeypatch.setattr(artifact_quarantine.os, "rename", real_rename)
    assert _sweep() == 0
    assert not media.exists()
    assert _quarantine_file(_artifact().id).read_bytes() == b"precious-bytes"


def test_external_delete_rejected(household: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mounts = tmp_path / "mnt"
    source = mounts / "plex" / "Movies" / "film.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"external-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(settings, "storage_mount_parents", str(mounts))
    with db_module.SessionLocal() as db:
        StorageRootService(db).create(label="Plex", container_path=str(mounts / "plex"), mode="external")
        db.commit()
    item_id = _download("alice", source)
    assert _artifact().ownership == "external"

    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/delete-file").status_code == 409
        assert client.get(f"/api/library/{item_id}/media").status_code == 200
    _sweep()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in (mounts / "plex").iterdir()) == ["Movies"]


def test_shared_copy_not_removed(household: None) -> None:
    media = settings.library_root / "shared" / "film.mp4"
    alice_item = _download("alice", media)
    bob_item = _download("bob", media)

    with _client("alice") as client:
        assert client.post(f"/api/library/{alice_item}/delete-file").status_code == 200
    _sweep(datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30))

    assert media.read_bytes() == b"precious-bytes"
    assert _artifact().lifecycle == "available"
    with _client("bob") as client:
        assert client.get(f"/api/library/{bob_item}/media").content == b"precious-bytes"


def test_quarantine_restore_roundtrip(household: None) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)
    artifact_id = _artifact().id

    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/delete-file").status_code == 200
        assert not media.exists()
        assert _quarantine_file(artifact_id).read_bytes() == b"precious-bytes"
        assert client.get(f"/api/library/{item_id}/media").status_code == 404
        assert client.post(f"/api/library/{item_id}/delete-file").status_code == 409

        restored = client.post(f"/api/library/{item_id}/restore-file")
        assert restored.status_code == 200
        assert restored.json()["status"] == "available"
        assert client.get(f"/api/library/{item_id}/media").content == b"precious-bytes"
    assert not _quarantine_file(artifact_id).exists()


def test_retention_purge_after_window(household: None) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)
    artifact_id = _artifact().id
    with _client("alice") as client:
        client.post(f"/api/library/{item_id}/delete-file")

    assert _sweep() == 0  # inside the recovery window
    assert _quarantine_file(artifact_id).exists()

    assert _sweep(datetime.now(UTC).replace(tzinfo=None) + timedelta(days=8)) == 1
    assert not _quarantine_file(artifact_id).exists()
    with db_module.SessionLocal() as db:
        assert db.query(MediaArtifact).count() == 0
        assert db.query(LibraryItemArtifact).count() == 0
        assert db.get(LibraryItem, item_id).status == "missing"
        assert db.query(StorageRoot).count() == 1
    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/restore-file").status_code == 409
        # Quarantine and purge tombstone the Library item (status "missing"), never
        # delete its row, so its FTS row stays and the index stays in sync (#104.3).
        assert [item["id"] for item in client.get("/api/library", params={"search": "video"}).json()["items"]] == [item_id]
    with db_module.engine.connect() as connection:
        assert _index_in_sync(connection)
