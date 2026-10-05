"""Admin registers and probes managed and read-only external roots."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import StorageRoot, User
from app.security import hash_password
from app.services.rate_limit import rate_limiter
from app.services.storage_roots import SENTINEL_NAME, StorageRootService

PASSWORD = "Test-only-passphrase-1"
URL = "/api/admin/storage/roots"


@pytest.fixture
def mounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "mnt"
    parent.mkdir()
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        db.add_all([
            User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(id="member", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True),
        ])
        db.commit()
    rate_limiter.clear()
    yield parent
    rate_limiter.clear()


@pytest.fixture
def admin(mounts: Path):
    client = TestClient(app, base_url="http://localhost")
    client.auth = ("admin", PASSWORD)
    yield client
    client.close()


def _tree(path: Path) -> list[tuple[str, int, int]]:
    return sorted((str(p.relative_to(path)), p.stat().st_mtime_ns, p.stat().st_size) for p in [path, *path.rglob("*")])


def test_root_scope_and_symlink_rejected(admin: TestClient, mounts: Path, tmp_path: Path) -> None:
    real = mounts / "movies"
    real.mkdir()
    (mounts / "alias").symlink_to(real)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert admin.post(URL, json={"label": "Movies", "container_path": str(real), "mode": "external"}).status_code == 201
    before = _tree(mounts)

    rejected = [
        "movies/sub",  # relative
        f"{mounts}/other/../movies",  # traversal
        str(mounts / "alias"),  # symlink alias of an existing root
        str(mounts / "alias" / "inner"),  # through a symlink
        str(outside),  # outside every mount parent
        str(real / "season-1"),  # overlaps (inside) an existing root
        str(mounts),  # overlaps (contains) an existing root
    ]
    for path in rejected:
        response = admin.post(URL, json={"label": "Bad", "container_path": path, "mode": "managed"})
        assert response.status_code == 422, path

    assert _tree(mounts) == before
    assert not (outside / SENTINEL_NAME).exists()
    assert [root["path"] for root in admin.get(URL).json()] == [str(real)]


def test_app_data_is_forbidden(admin: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_mount_parents", str(settings.data_dir.parent))
    for path in (settings.data_dir, settings.data_dir / "library", settings.data_dir.parent):
        response = admin.post(URL, json={"label": "Data", "container_path": str(path), "mode": "managed"})
        assert response.status_code == 422, path
    assert admin.get(URL).json() == []


def test_members_cannot_see_or_register_roots(mounts: Path) -> None:
    client = TestClient(app, base_url="http://localhost")
    client.auth = ("member", PASSWORD)
    try:
        assert client.get(URL).status_code == 403
        assert client.post(URL, json={"label": "X", "container_path": str(mounts), "mode": "managed"}).status_code == 403
    finally:
        client.close()


def test_external_probe_zero_writes(admin: TestClient, mounts: Path) -> None:
    library = mounts / "jellyfin"
    (library / "Show").mkdir(parents=True)
    (library / "Show" / "episode.mkv").write_bytes(b"media")
    before = _tree(library)

    created = admin.post(URL, json={"label": "Jellyfin", "container_path": str(library), "mode": "external"})
    assert created.status_code == 201
    probed = admin.post(f"{URL}/{created.json()['id']}/probe").json()

    assert probed["observation"]["state"] == "available"
    assert probed["observation"]["identity_verified"] is True
    assert _tree(library) == before


def test_managed_probe_writes_only_sentinel_and_probe_dir(admin: TestClient, mounts: Path) -> None:
    managed = mounts / "downloads"
    managed.mkdir()
    body = admin.post(URL, json={"label": "Downloads", "container_path": str(managed), "mode": "managed"}).json()

    assert body["observation"]["state"] == "available"
    assert isinstance(body["observation"]["free_bytes"], int)
    assert (managed / SENTINEL_NAME).read_text() == body["id"]
    assert sorted(p.name for p in managed.iterdir()) == [".lumina-probe", SENTINEL_NAME]
    assert list((managed / ".lumina-probe").iterdir()) == []

    low = admin.patch(f"{URL}/{body['id']}", json={"minimum_free_bytes": 2**62}).json()
    assert admin.post(f"{URL}/{low['id']}/probe").json()["observation"]["state"] == "low_space"


def test_offline_mount_not_empty(admin: TestClient, mounts: Path) -> None:
    missing = admin.post(URL, json={"label": "NAS", "container_path": str(mounts / "nas"), "mode": "external"}).json()
    assert missing["observation"]["state"] == "offline"
    assert not (mounts / "nas").exists()  # no fallback write into the container layer

    managed = mounts / "archive"
    managed.mkdir()
    body = admin.post(URL, json={"label": "Archive", "container_path": str(managed), "mode": "managed"}).json()
    # The disk is unmounted and an empty mount point remains: not an empty library.
    (managed / SENTINEL_NAME).unlink()
    probed = admin.post(f"{URL}/{body['id']}/probe").json()
    assert probed["observation"]["state"] == "identity_mismatch"
    assert not (managed / SENTINEL_NAME).exists()

    with db_module.SessionLocal() as db:
        service = StorageRootService(db)
        assert service.is_online(db.get(StorageRoot, missing["id"])) is False
        assert service.is_online(db.get(StorageRoot, body["id"])) is False
        external = db.get(StorageRoot, missing["id"])
        (mounts / "nas").mkdir()
        external.identity = "some-other-device"  # a different disk now sits at the path
        assert service.is_online(external) is False


def test_deregister_never_touches_directory(admin: TestClient, mounts: Path) -> None:
    managed = mounts / "keep"
    managed.mkdir()
    (managed / "video.mp4").write_bytes(b"x")
    body = admin.post(URL, json={"label": "Keep", "container_path": str(managed), "mode": "managed"}).json()
    assert admin.delete(f"{URL}/{body['id']}").status_code == 204
    assert (managed / "video.mp4").read_bytes() == b"x"
    assert admin.get(URL).json() == []
    assert os.path.isdir(managed)
