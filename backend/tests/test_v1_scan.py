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
from app.models import ImportRun, LibraryItem, MediaArtifact, MediaTitle, User, utcnow
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


@pytest.mark.parametrize("protection", [None, "locked", "unmatched"])
def test_later_episode_import_wakes_series_metadata_without_overriding_owner_choices(library: Path, protection: str | None) -> None:
    from datetime import timedelta

    folder = library / "Series"
    write(folder / "tvshow.nfo", b'<tvshow><title>Series</title><uniqueid type="tmdb">1396</uniqueid></tvshow>')
    write(folder / "Season 01" / "Series - S01E01 - Pilot.mkv")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)

        def rescan():
            result = admin.post("/api/admin/imports", json={"root_id": root_id})
            assert result.status_code == 202, result.text
            assert admin.get(result.json()["status_url"]).json()["state"] == "succeeded"

        rescan()
        with db_module.session_scope() as db:
            series = db.scalar(select(MediaTitle).where(MediaTitle.type == "series"))
            series_id = series.id
            future = utcnow() + timedelta(days=90)
            series.metadata_due_at = future
            if protection == "locked":
                series.locked = True
            elif protection == "unmatched":
                series.provider_ids = {}
                series.field_sources = {**series.field_sources, "provider_ids": "user"}
                series.metadata_due_at = None
        before = utcnow()
        write(folder / "Season 01" / "Series - S01E02 - Return.mkv")
        write(folder / "Season 02" / "Series - S02E01 - New Season.mkv")
        rescan()
        with db_module.session_scope() as db:
            series = db.get(MediaTitle, series_id)
            due = series.metadata_due_at
            if protection == "locked":
                assert due == future
            elif protection == "unmatched":
                assert due is None and series.provider_ids == {}
            else:
                assert due is not None and before <= due <= utcnow(), "new episodes must not wait for the weekly/90-day series refresh"
        rescan()
        with db_module.session_scope() as db:
            assert db.get(MediaTitle, series_id).metadata_due_at == due, "an unchanged scan must not keep requeueing metadata"


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
        assert service.step(run.id) is False
        run = db.get(ImportRun, run.id)
        assert run.cursor != "[]" and run.counters["candidate_discovered"] == 2
        assert "candidate_total" not in run.counters
        service.cancel(db.get(ImportRun, run.id))
        db.commit()
        assert service.step(run.id) is True
        assert db.get(ImportRun, run.id).state == "cancelled"
        assert db.query(LibraryItem).count() == 2  # cancellation keeps batches that already committed

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


def test_legacy_run_resumes_its_processing_cursor_without_restarting_discovery(library: Path) -> None:
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        write(library / name, name.encode())
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    with db_module.SessionLocal() as db:
        service = LibraryImportService(db)
        run = service.start(root_id, "admin")
        run.counters = {}  # persisted by a release before discovery counters existed
        run.cursor = '["a.mp4"]'
        db.commit()
        while not service.step(run.id):
            pass
        run = db.get(ImportRun, run.id)
        assert run.state == "succeeded" and run.counters["inspected"] == 2
        assert {artifact.relative_path for artifact in db.query(MediaArtifact)} == {"b.mp4", "c.mp4"}


def test_indexing_total_is_unknown_until_the_bounded_walk_finishes(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(library / "a.mp4", b"a")
    write(library / "b.mp4", b"b")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    with db_module.SessionLocal() as db:
        monkeypatch.setattr(library_import, "BATCH_SIZE", 1)
        service = LibraryImportService(db)
        run = service.start(root_id, "admin")
        db.commit()
        assert service.step(run.id) is False
        run = db.get(ImportRun, run.id)
        assert run.counters["scan_phase"] == "indexing" and "candidate_total" not in run.counters
        while not service.step(run.id):
            pass
        run = db.get(ImportRun, run.id)
        assert (run.counters["candidate_completed"], run.counters["candidate_total"]) == (2, 2)


def test_files_changed_during_a_live_walk_are_reconciled_by_the_next_scan(
    library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(library / "a.mp4", b"a")
    write(library / "b.mp4", b"b")
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    with db_module.SessionLocal() as db:
        service = LibraryImportService(db)
        baseline = service.start(root_id, "admin")
        db.commit()
        while not service.step(baseline.id):
            pass

        monkeypatch.setattr(library_import, "BATCH_SIZE", 1)
        run = service.start(root_id, "admin")
        db.commit()
        assert service.step(run.id) is False  # the retained listing now holds a then b
        (library / "b.mp4").unlink()
        write(library / "c.mp4", b"c")
        write(library / "d.mp4", b"d")
        while not service.step(run.id):
            pass
        run = db.get(ImportRun, run.id)
        assert run.state == "partial" and run.coverage == "incomplete"
        assert (run.counters["candidate_completed"], run.counters["candidate_total"]) == (2, 2)
        assert db.scalar(select(MediaArtifact.lifecycle).where(MediaArtifact.relative_path == "b.mp4")) == "available"

        followup = service.start(root_id, "admin")
        db.commit()
        while not service.step(followup.id):
            pass
        followup = db.get(ImportRun, followup.id)
        assert followup.state == "succeeded"
        assert (followup.counters["candidate_completed"], followup.counters["candidate_total"]) == (3, 3)
        assert {
            artifact.relative_path: artifact.lifecycle for artifact in db.query(MediaArtifact)
        } == {"a.mp4": "available", "b.mp4": "missing", "c.mp4": "available", "d.mp4": "available"}


def test_batches_share_one_live_walk(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(7):
        write(library / f"clip{index}.mp4", str(index).encode())
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    real_walk = library_import.walk
    walks = 0

    def counted_walk(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal walks
        walks += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(library_import, "BATCH_SIZE", 2)
    monkeypatch.setattr(library_import, "walk", counted_walk)
    with db_module.SessionLocal() as db:
        service = LibraryImportService(db)
        run = service.start(root_id, "admin")
        db.commit()
        while not service.step(run.id):
            pass
        assert walks == 1
        assert db.query(MediaArtifact).count() == 7


def test_failed_batch_rebuilds_the_walk_from_its_durable_cursor(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(5):
        write(library / f"clip{index}.mp4", str(index).encode())
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
    real_describe = library_import.describe
    attempts = 0

    def fail_once(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic description failure")
        return real_describe(*args, **kwargs)

    monkeypatch.setattr(library_import, "BATCH_SIZE", 2)
    monkeypatch.setattr(library_import, "describe", fail_once)
    with db_module.SessionLocal() as db:
        service = LibraryImportService(db)
        run = service.start(root_id, "admin")
        db.commit()
        with pytest.raises(RuntimeError, match="synthetic description failure"):
            service.step(run.id)
        db.rollback()
        assert db.get(ImportRun, run.id).cursor == "[]"
        while not service.step(run.id):
            pass
        assert {artifact.relative_path for artifact in db.query(MediaArtifact)} == {
            f"clip{index}.mp4" for index in range(5)
        }


def test_rescan_requeues_a_probe_after_a_missing_file_returns(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = library / "Movies" / "Return.mp4"
    write(media)
    changed_artifacts: dict[str, set[str]] = {}

    def capture(run_id: str) -> None:
        changes = library_import.import_changes(run_id)
        assert changes is not None
        changed_artifacts[run_id] = set(changes.artifact_ids)

    monkeypatch.setattr(library_import, "after_import_hooks", [capture])
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        run = admin.post("/api/admin/imports", json={"root_id": root_id}).json()
        assert admin.get(run["status_url"]).json()["state"] == "succeeded"
        with db_module.SessionLocal() as db:
            artifact = db.query(MediaArtifact).one()
            artifact_id = artifact.id
            artifact.probe = {"fingerprint": None, "error": "missing", "loudness": {"error": "missing"}}
            db.commit()

        rerun = admin.post("/api/admin/imports", json={"root_id": root_id}).json()
        assert admin.get(rerun["status_url"]).json()["state"] == "succeeded"
        assert changed_artifacts[rerun["id"]] == {artifact_id}
    with db_module.SessionLocal() as db:
        probe = db.query(MediaArtifact).one().probe
        assert probe is None or probe.get("error") != "missing"  # the background worker may already have retried


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
    assert run["counters"]["candidate_completed"] == run["counters"]["candidate_total"] == 4
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
