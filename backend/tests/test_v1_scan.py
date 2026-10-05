"""Bounded, resumable, read-only scan of an external root."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import ImportRun, LibraryItem, MediaArtifact, User
from app.security import hash_password
from app.services import library_import
from app.services.library_import import LibraryImportService
from app.services.rate_limit import rate_limiter

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "mnt"
    root = parent / "media"
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add_all([
            User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(id="member", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True),
        ])
        db.commit()
    rate_limiter.clear()
    yield root
    rate_limiter.clear()


def client_for(username: str) -> TestClient:
    client = TestClient(app, base_url="http://localhost")
    client.auth = (username, PASSWORD)
    return client


def write(path: Path, content: bytes = b"\x00fake-media") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def manifest(root: Path) -> list[tuple[str, str, int, int]]:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            status = os.lstat(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else ""
            rows.append((str(path.relative_to(root)), digest, status.st_mtime_ns, status.st_size))
    return sorted(rows)


def register_root(admin: TestClient, root: Path) -> str:
    response = admin.post("/api/admin/storage/roots", json={"label": "Media", "container_path": str(root), "mode": "external"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_external_scan_zero_mutation(library: Path) -> None:
    write(library / "Movies" / "Heat (1995).mkv", b"heat-bytes")
    write(library / "Music" / "Artist" / "Album" / "01 - Song.flac", b"song-bytes")
    write(library / "notes.txt", b"not media")
    write(library / ".hidden.mkv")
    (library / "Movies").chmod(0o755)
    before = manifest(library)
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        started = admin.post("/api/admin/imports", json={"root_id": root_id, "visibility": "private"})
        assert started.status_code == 202, started.text
        run = admin.get(started.json()["status_url"]).json()
        assert run["state"] == "succeeded"
        assert run["counters"]["indexed"] == 2
        assert run["visibility"] == "private"

        page = admin.get("/api/library").json()
        titles = sorted(item["title"] for item in page["items"])
        assert titles == ["Heat", "Song"]  # S21 sidecar/filename grouping
        heat = next(item for item in page["items"] if item["title"] == "Heat")
        media = admin.get(f"/api/library/{heat['id']}/media")
        assert media.status_code == 200 and media.content == b"heat-bytes"
        found = admin.get("/api/library", params={"search": "heat"}).json()["items"]
        assert [item["id"] for item in found] == [heat["id"]]
        # "Delete file" is unavailable for external artifacts.
        assert admin.post(f"/api/library/{heat['id']}/delete-file").status_code == 409

        member = client_for("member")
        assert member.get("/api/library").json()["items"] == []  # a private import stays admin-only
        assert member.post("/api/admin/imports", json={"root_id": root_id}).status_code == 403
    assert manifest(library) == before


def test_scan_cancel_resume_deduplicates(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(7):
        write(library / f"dir{index % 3}" / f"clip{index}.mp4", f"clip{index}".encode())
    monkeypatch.setattr(library_import, "BATCH_SIZE", 2)
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    with db_module.SessionLocal() as db:
        service = LibraryImportService(db)
        run = service.start(root_id, "admin")
        db.commit()
        assert service.step(run.id) is False  # first batch committed with its cursor
        service.cancel(db.get(ImportRun, run.id))
        db.commit()
        assert service.step(run.id) is True
        assert db.get(ImportRun, run.id).state == "cancelled"
        assert db.query(LibraryItem).count() == 2

        for _ in range(2):  # resuming twice (second is a fresh rescan) never duplicates
            current = db.get(ImportRun, run.id)
            if current.state == "cancelled":
                service.resume(current)
            else:
                run = service.start(root_id, "admin")
            db.commit()
            while not service.step(run.id):
                pass
        assert db.get(ImportRun, run.id).state == "succeeded"
        assert db.query(LibraryItem).count() == 7
        assert db.query(MediaArtifact).count() == 7


def test_symlink_and_permission_errors(library: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside / "secret.mp4", b"secret")
    (library / "escape.mp4").symlink_to(outside / "secret.mp4")
    (library / "escape-dir").symlink_to(outside)
    write(library / "ok" / "good.mp4", b"good")
    locked = library / "locked"
    write(locked / "hidden.mp4", b"x")
    locked.chmod(0o000)
    try:
        with client_for("admin") as admin:
            root_id = register_root(admin, library)
            run = admin.post("/api/admin/imports", json={"root_id": root_id}).json()
            run = admin.get(run["status_url"]).json()
            entries = admin.get(f"{run['status_url']}/entries").json()
            titles = [item["title"] for item in admin.get("/api/library").json()["items"]]
    finally:
        locked.chmod(0o755)
    assert titles == ["good"]
    assert run["state"] == "partial" and run["coverage"] == "incomplete"
    assert run["counters"]["failed"] == 1 and run["counters"]["skipped"] == 2
    assert {(entry["relative_path"], entry["error"]) for entry in entries} == {
        ("escape-dir", "symlink"),
        ("escape.mp4", "symlink"),
        ("locked", "permission_denied"),
    }


def test_managed_root_and_active_run_are_rejected(library: Path, tmp_path: Path) -> None:
    write(library / "a.mp4")
    managed = library.parent / "managed"
    managed.mkdir()
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        managed_id = admin.post("/api/admin/storage/roots", json={"label": "M", "container_path": str(managed), "mode": "managed"}).json()["id"]
        assert admin.post("/api/admin/imports", json={"root_id": managed_id}).status_code == 409
        assert admin.post("/api/admin/imports", json={"root_id": "nope"}).status_code == 404
    with db_module.SessionLocal() as db:
        LibraryImportService(db).start(root_id, "admin")
        db.commit()
    with client_for("admin") as admin:
        assert admin.post("/api/admin/imports", json={"root_id": root_id}).status_code == 409


def test_rescan_tombstones_vanished_file_and_keeps_search_index_in_sync(library: Path) -> None:
    from app.services.library_search import _index_in_sync

    write(library / "Heat (1995).mkv", b"heat")
    write(library / "Ronin (1998).mkv", b"ronin")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        admin.post("/api/admin/imports", json={"root_id": root_id})
        (library / "Ronin (1998).mkv").unlink()
        rerun = admin.get(admin.post("/api/admin/imports", json={"root_id": root_id}).json()["status_url"]).json()
        assert rerun["counters"]["missing"] == 1
        # The vanished file tombstones its item (status "missing"), never deletes the
        # row, so its FTS row stays valid and the index stays in sync (#104.3).
        found = admin.get("/api/library", params={"search": "ronin"}).json()["items"]
        assert [(item["title"], item["status"]) for item in found] == [("Ronin", "missing")]
    with db_module.engine.connect() as connection:
        assert _index_in_sync(connection)


def test_noop_rescan_keeps_title_embeddings(library: Path) -> None:
    from app.models import MediaTitle, SearchEmbedding

    write(library / "Heat (1995).mkv", b"heat")
    write(library / "Show" / "Season 1" / "Show - S01E01.mkv", b"ep1")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        admin.post("/api/admin/imports", json={"root_id": root_id})
        with db_module.session_scope() as db:
            ids = set(db.scalars(select(MediaTitle.id)))
            assert len(ids) >= 3
            db.add_all(SearchEmbedding(target_id=i, model_id="m", kind="title", signature="s", vector=b"\x00\x00\x80?") for i in ids)
        rerun = admin.get(admin.post("/api/admin/imports", json={"root_id": root_id}).json()["status_url"]).json()
        assert rerun["counters"]["unchanged"] == 2
    with db_module.session_scope() as db:
        assert set(db.scalars(select(SearchEmbedding.target_id))) == ids


def test_import_defaults_to_shared(library: Path) -> None:
    """An import started without a visibility is shared with the household."""
    write(library / "Movies" / "Heat (1995).mkv", b"heat-bytes")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        started = admin.post("/api/admin/imports", json={"root_id": root_id})
        assert started.status_code == 202, started.text
        run = admin.get(started.json()["status_url"]).json()
        assert run["state"] == "succeeded" and run["visibility"] == "shared"
    with db_module.SessionLocal() as db:
        assert {item.visibility for item in db.scalars(select(LibraryItem))} == {"shared"}
