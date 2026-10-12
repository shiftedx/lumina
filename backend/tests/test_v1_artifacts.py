"""Downloads register root-relative artifacts; media is served only through them."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot, User
from app.security import hash_password
from app.services.library import LibraryService
from app.services.rate_limit import rate_limiter
from app.services.storage_roots import SENTINEL_NAME

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def household() -> None:
    settings.library_root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        db.add_all([
            User(id=user_id, username=user_id, display_name=user_id, password_hash=hash_password(PASSWORD), role=role, is_active=True)
            for user_id, role in (("alice", "viewer"), ("bob", "viewer"), ("admin", "admin"))
        ])
        db.commit()
    rate_limiter.clear()
    yield
    rate_limiter.clear()


@contextmanager
def _client(username: str) -> Iterator[TestClient]:
    client = TestClient(app, base_url="http://localhost")  # no lifespan: no background workers
    client.auth = (username, PASSWORD)
    try:
        yield client
    finally:
        client.close()


def _download(owner: str, path: Path, remote_id: str = "vid-1") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"media-bytes")
    info = {"id": remote_id, "extractor_key": "Youtube", "title": "A video", "filepath": str(path)}
    with db_module.SessionLocal() as db:
        item = LibraryService(db).upsert_from_info(info, owner_user_id=owner)
        db.commit()
        return item.id


def _reconcile() -> None:
    with db_module.SessionLocal() as db:
        service = LibraryService(db)
        while not service.reconcile_files_step()[1]:
            pass


def _status(item_id: str) -> str:
    with db_module.SessionLocal() as db:
        return db.get(LibraryItem, item_id).status


def test_download_registers_root_relative_artifact(household: None) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)

    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        root = db.get(StorageRoot, artifact.root_id)
        assert (root.label, root.mode, root.observation["state"]) == ("Library", "managed", "available")
        assert (artifact.relative_path, artifact.ownership, artifact.owner_user_id) == ("alice/video.mp4", "managed", "alice")
        assert db.get(LibraryItemArtifact, item_id).artifact_id == artifact.id

    with _client("alice") as client:
        response = client.get(f"/api/library/{item_id}/media")
    assert response.status_code == 200
    assert response.content == b"media-bytes"


def test_out_of_root_path_not_served(household: None, tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "secret.mp4"
    item_id = _download("alice", outside)
    with db_module.SessionLocal() as db:
        assert db.query(MediaArtifact).count() == 0

    with _client("alice") as client:
        assert client.get(f"/api/library/{item_id}/media").status_code == 404
        assert client.post(f"/api/library/{item_id}/delete-file").status_code == 409
    assert outside.read_bytes() == b"media-bytes"


def test_symlink_swapped_into_root_not_served(household: None, tmp_path: Path) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)
    secret = tmp_path / "secret.txt"
    secret.write_text("not for members")
    media.unlink()
    media.symlink_to(secret)

    with _client("alice") as client:
        assert client.get(f"/api/library/{item_id}/media").status_code == 404


def test_shared_artifact_reference_count(household: None) -> None:
    media = settings.library_root / "shared" / "film.mp4"
    alice_item = _download("alice", media)
    bob_item = _download("bob", media)
    assert alice_item != bob_item

    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        links = {link.library_item_id for link in db.query(LibraryItemArtifact).filter_by(artifact_id=artifact.id)}
    assert links == {alice_item, bob_item}

    with _client("bob") as client:
        assert client.get(f"/api/library/{bob_item}/media").status_code == 200
        assert client.get(f"/api/library/{alice_item}/media").status_code == 404  # alice's entry stays private


def test_offline_root_is_unknown_not_missing(household: None) -> None:
    media = settings.library_root / "alice" / "video.mp4"
    item_id = _download("alice", media)
    sentinel = settings.library_root / SENTINEL_NAME

    # Mount gone (sentinel missing): nothing is concluded about its files.
    sentinel.rename(sentinel.with_suffix(".away"))
    media.unlink()
    _reconcile()
    assert _status(item_id) == "available"

    # Mount back and the file really is gone: now it is missing.
    sentinel.with_suffix(".away").rename(sentinel)
    _reconcile()
    assert _status(item_id) == "missing"


def test_root_with_artifacts_cannot_be_deregistered(household: None) -> None:
    _download("alice", settings.library_root / "alice" / "video.mp4")
    with db_module.SessionLocal() as db:
        root_id = db.query(StorageRoot.id).scalar()
    with _client("admin") as client:
        assert client.get("/api/admin/storage/roots").json()[0]["artifact_count"] == 1
        assert client.delete(f"/api/admin/storage/roots/{root_id}").status_code == 409
    assert (settings.library_root / "alice" / "video.mp4").exists()
