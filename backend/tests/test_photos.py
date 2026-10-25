from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from pillow_heif import register_heif_opener
from starlette.requests import ClientDisconnect

from app import photo_face_models, photo_models, photo_root_models  # noqa: F401 - register metadata before db_factory creates the schema
from app.db import session_scope
from app.models import MemberAccess, StorageRoot, User
from app.photo_face_models import PhotoFace, PhotoPerson
from app.photo_models import PhotoItem
from app.routers import photos as photo_routes
from app.services import photo_derivatives, photo_imports, upload_admission as upload_admission_module
from app.services.background_work import background_work
from app.services.photos import PhotoService
from app.services.storage_roots import StorageRootService
from app.services.upload_admission import MIN_FREE_SPACE_BYTES, UploadAdmission, UploadBusy


@pytest.fixture
def db_factory():  # noqa: ANN201
    """Importer lookahead requires independent SQLite connections per thread."""
    from app.db import init_db
    from support import file_backed_session_factory

    init_db()
    return file_backed_session_factory()


def _user(db_factory, user_id: str) -> User:  # noqa: ANN001
    with db_factory() as db:
        user = User(id=user_id, username=user_id, display_name=user_id, role="viewer", is_active=True)
        db.add(user)
        db.commit()
        return user


def _jpeg(*, orientation: int = 1) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (12, 20), "#d06040")
    exif = Image.Exif()
    exif[274] = orientation
    exif[36867] = "2024:02:03 04:05:06"
    exif[271] = "Camera Co"
    exif[272] = "Small One"
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def _stub_pipeline(monkeypatch) -> None:  # noqa: ANN001
    def inspect(_path, _type):  # noqa: ANN001,ANN202
        return {
            "content_type": "image/jpeg", "width": 12, "height": 20, "orientation": 1,
            "captured_at": "2024-02-03T04:05:06", "captured_at_raw": "2024:02:03 04:05:06",
            "camera_make": "Camera Co", "camera_model": "Small One", "metadata": {},
        }

    def render(_path, content_hash):  # noqa: ANN001,ANN202
        products = {}
        for width in photo_derivatives.WIDTHS:
            key = photo_derivatives.source_key(content_hash, width)
            target = photo_derivatives.derivative_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"webp-{width}".encode())
            products[width] = (key, target.stat().st_size)
        return products

    monkeypatch.setattr(photo_derivatives, "inspect", inspect)
    monkeypatch.setattr(photo_derivatives, "render", render)
    monkeypatch.setattr(
        photo_derivatives, "prepare",
        lambda path, content_type, content_hash: (inspect(path, content_type), render(path, content_hash)),
    )


def _prepared_test_file(relative, entry):  # noqa: ANN001,ANN202
    path = Path(entry.path)
    info = entry.stat(follow_symlinks=False)
    source = path.read_bytes()
    content_hash = hashlib.sha256(source).hexdigest()
    content_type = photo_derivatives.sniff(source[:128])
    assert content_type is not None
    facts, products = photo_derivatives.prepare(path, content_type, content_hash)
    return photo_imports.PreparedPhoto(
        relative=relative, root_relative=relative, filename=path.name,
        size=info.st_size, mtime_ns=info.st_mtime_ns, unchanged=False,
        content_type=content_type, content_hash=content_hash, facts=facts, products=products,
    )


def test_import_preserves_original_dedupes_per_owner_and_builds_stable_derivatives(
    api_client, db_factory, monkeypatch,
) -> None:
    _stub_pipeline(monkeypatch)
    owner = _user(db_factory, "photo-owner")
    payload = _jpeg()
    with api_client(user=owner, base_url="http://localhost") as client:
        first = client.put("/api/photos/import?filename=day.jpg", content=payload)
        assert first.status_code == 201, first.text
        photo = first.json()
        assert photo["filename"] == "day.jpg"
        assert photo["camera"] == "Camera Co Small One"
        assert photo["can_edit"] is True and photo["visibility"] == "private"
        assert client.put("/api/photos/import?filename=renamed.jpg", content=payload).json()["id"] == photo["id"]
        original = client.get(photo["original_url"])
        assert original.status_code == 200 and original.content == payload
        image = client.get(photo["thumbnail_url"])
        assert image.content == b"webp-400"
        assert image.headers["cache-control"] == "private, max-age=0, must-revalidate"
        assert image.headers["etag"] == f'"{photo_derivatives.source_key(hashlib.sha256(payload).hexdigest(), 400)}"'
        assert client.get(photo["thumbnail_url"], headers={"If-None-Match": image.headers["etag"]}).status_code == 304
        photo_derivatives.derivative_path(image.headers["etag"].strip('"')).unlink()
        assert client.get(photo["thumbnail_url"]).content == b"webp-400"
    with db_factory() as db:
        assert db.query(PhotoItem).count() == 1


def test_private_shared_personal_favorites_sections_and_download_permission(
    api_client, db_factory, monkeypatch,
) -> None:
    _stub_pipeline(monkeypatch)
    owner, other = _user(db_factory, "sharing-owner"), _user(db_factory, "sharing-member")
    with api_client(user=owner, base_url="http://localhost") as client:
        photo = client.put("/api/photos/import?filename=shared.jpg", content=_jpeg()).json()
        assert client.patch(f"/api/photos/{photo['id']}", json={"favorite": True, "visibility": "shared"}).status_code == 200
    with db_factory() as db:
        db.add(PhotoPerson(
            id="owner-person", owner_user_id=owner.id, pipeline_signature="test", name="Private identity",
        ))
        db.add(PhotoFace(
            id="owner-face", photo_id=photo["id"], owner_user_id=owner.id, person_id="owner-person",
            pipeline_signature="test", detector_score=0.9, x1=0.1, y1=0.1, x2=0.4, y2=0.4,
            embedding=b"private-vector", assignment_source="manual",
        ))
        db.commit()

    with api_client(user=other, base_url="http://localhost") as client:
        shared = client.get("/api/photos").json()["items"]
        assert [item["id"] for item in shared] == [photo["id"]]
        assert shared[0]["favorite"] is False and shared[0]["can_edit"] is False
        assert shared[0]["person_ids"] == []
        assert client.get("/api/photos?person_id=owner-person").status_code == 404
        assert client.patch(f"/api/photos/{photo['id']}", json={"favorite": True}).status_code == 200
        assert client.patch(f"/api/photos/{photo['id']}", json={"title": "Not mine"}).status_code == 403
    with api_client(user=owner, base_url="http://localhost") as client:
        assert client.get("/api/photos?favorite=true").json()["total"] == 1

    with db_factory() as db:
        db.add(MemberAccess(user_id=other.id, sections=["photos"], streaming={}, can_download=False))
        db.commit()
    with api_client(user=other, base_url="http://localhost") as client:
        visible = client.get("/api/photos").json()["items"][0]
        assert visible["original_url"] is None
        assert client.get(f"/api/photos/{photo['id']}/original").status_code == 403
    with db_factory() as db:
        access = db.get(MemberAccess, other.id)
        access.sections = ["movies"]
        db.commit()
    with api_client(user=other, base_url="http://localhost") as client:
        assert client.get("/api/photos").json()["items"] == []
        assert client.get(f"/api/photos/{photo['id']}/image?width=400").status_code == 404


def test_keyset_trash_restore_and_album_visibility(api_client, db_factory, monkeypatch) -> None:
    _stub_pipeline(monkeypatch)
    owner, other = _user(db_factory, "catalog-owner"), _user(db_factory, "catalog-member")
    photos = []
    with api_client(user=owner, base_url="http://localhost") as client:
        for index in range(4):
            body = _jpeg() + bytes([index])
            imported = client.put(f"/api/photos/import?filename={index}.jpg", content=body).json()
            client.patch(f"/api/photos/{imported['id']}", json={"visibility": "shared"})
            photos.append(imported)
        first = client.get("/api/photos?sort=added&limit=2").json()
        second = client.get(f"/api/photos?sort=added&limit=2&cursor={first['next_cursor']}").json()
        assert first["total"] == second["total"] == 4
        assert set(item["id"] for item in first["items"]).isdisjoint(item["id"] for item in second["items"])
        trashed = client.patch(f"/api/photos/{photos[0]['id']}", json={"deleted": True}).json()
        assert trashed["deleted_at"] is not None
        assert client.get("/api/photos?deleted=true").json()["total"] == 1
        assert client.get(f"/api/photos/{photos[0]['id']}/image?width=400").status_code == 200
    with api_client(user=other, base_url="http://localhost") as client:
        assert client.get(f"/api/photos/{photos[0]['id']}/image?width=400").status_code == 404
    with api_client(user=owner, base_url="http://localhost") as client:
        assert client.post(f"/api/photos/{photos[0]['id']}/restore").json()["deleted_at"] is None
        album = client.post("/api/photo-albums", json={"name": "Family", "visibility": "shared"}).json()
        assert client.put(f"/api/photo-albums/{album['id']}/photos/{photos[0]['id']}").json()["photo_count"] == 1
        private_album = client.post("/api/photo-albums", json={"name": "Private"}).json()
        assert client.put(f"/api/photo-albums/{private_album['id']}/photos/{photos[0]['id']}").status_code == 200
    with api_client(user=other, base_url="http://localhost") as client:
        album = client.get("/api/photo-albums").json()["items"][0]
        assert album["photo_count"] == 1 and album["can_edit"] is False
        shared_photo = next(item for item in client.get("/api/photos").json()["items"] if item["id"] == photos[0]["id"])
        assert shared_photo["album_ids"] == [album["id"]]
        assert client.get(f"/api/photos?album_id={private_album['id']}").status_code == 404
        assert client.delete(f"/api/photo-albums/{album['id']}").status_code == 404


def test_malformed_image_releases_admission_without_catalog_or_staging_residue(api_client, db_factory, monkeypatch) -> None:
    owner = _user(db_factory, "malformed-owner")
    with api_client(user=owner, base_url="http://localhost") as client:
        response = client.put("/api/photos/import?filename=broken.jpg", content=b"\xff\xd8\xffnot-a-jpeg")
        assert response.status_code == 422
        with db_factory() as db:
            assert db.query(PhotoItem).count() == 0
        assert list(photo_derivatives.photos_root().glob(".upload-*")) == []
        _stub_pipeline(monkeypatch)
        assert client.put("/api/photos/import?filename=after.jpg", content=_jpeg()).status_code == 201
    with db_factory() as db:
        assert db.query(PhotoItem).count() == 1
    assert list(photo_derivatives.photos_root().glob(".upload-*")) == []


def test_upload_admission_keeps_a_lane_available_to_another_member(tmp_path) -> None:
    admission = UploadAdmission(max_active=2, max_per_member=1, min_free_bytes=0)
    with admission.acquire("member-a", tmp_path, content_length="10", maximum_bytes=100):
        with pytest.raises(UploadBusy):
            admission.acquire("member-a", tmp_path, content_length="10", maximum_bytes=100)
        with admission.acquire("member-b", tmp_path, content_length="10", maximum_bytes=100):
            with pytest.raises(UploadBusy):
                admission.acquire("member-c", tmp_path, content_length="10", maximum_bytes=100)

    with admission.acquire("member-a", tmp_path, content_length="10", maximum_bytes=100):
        pass


def test_photo_upload_refuses_low_space_before_staging_or_decode(api_client, db_factory, monkeypatch) -> None:
    owner = _user(db_factory, "low-space-photo-owner")
    payload = _jpeg()
    root = photo_derivatives.photos_root()
    decoded = False

    def unexpected_decode(*_args):  # noqa: ANN202
        nonlocal decoded
        decoded = True
        raise AssertionError("low-space upload reached the decoder")

    monkeypatch.setattr(
        upload_admission_module.shutil, "disk_usage",
        lambda _root: SimpleNamespace(free=MIN_FREE_SPACE_BYTES + len(payload) - 1),
    )
    monkeypatch.setattr(photo_derivatives, "prepare", unexpected_decode)
    with api_client(user=owner, base_url="http://localhost") as client:
        response = client.put("/api/photos/import?filename=full.jpg", content=payload)

    assert (response.status_code, response.json()) == (507, {"detail": "storage_full"})
    assert response.headers["Cache-Control"] == "no-store"
    assert decoded is False
    assert list(root.glob(".upload-*")) == []


@pytest.mark.parametrize("failure", [asyncio.CancelledError, ClientDisconnect], ids=["cancelled", "disconnected"])
def test_interrupted_photo_upload_releases_its_lane_and_removes_staging(tmp_path, monkeypatch, failure) -> None:  # noqa: ANN001
    class InterruptedRequest:
        headers = {"content-length": "2"}
        scope: dict = {}

        async def stream(self):  # noqa: ANN202
            yield b"x"
            raise failure()

    monkeypatch.setattr(photo_routes, "photos_section_allowed", lambda *_args: True)
    monkeypatch.setattr(photo_routes.photo_derivatives, "photos_root", lambda: tmp_path)
    user = SimpleNamespace(id="cancelled-photo-owner")

    expected = asyncio.CancelledError if failure is asyncio.CancelledError else HTTPException
    with pytest.raises(expected) as raised:
        asyncio.run(photo_routes.import_photo("interrupted.jpg", InterruptedRequest(), current_user=user, db=object()))
    if failure is ClientDisconnect:
        assert raised.value.status_code == 400

    assert list(tmp_path.glob(".upload-*")) == []
    with upload_admission_module.upload_admission.acquire(
        user.id, tmp_path, content_length="2", maximum_bytes=2,
    ):
        pass


def test_catalog_failure_after_original_publication_removes_only_losing_target(db_factory, monkeypatch) -> None:
    owner = _user(db_factory, "failed-publication-owner")
    staged = photo_derivatives.photos_root() / ".upload-race.tmp"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"source bytes")
    with db_factory() as db:
        service = PhotoService(db)
        monkeypatch.setattr(db, "flush", lambda: (_ for _ in ()).throw(RuntimeError("database failed")))
        with pytest.raises(RuntimeError, match="database failed"):
            service.import_staged(staged, "race.jpg", owner, "a" * 64, 12, {
                "content_type": "image/jpeg", "width": 1, "height": 1,
            })
    assert not staged.exists()
    assert list((photo_derivatives.photos_root() / "originals").rglob("*.jpg")) == []


def test_isolated_pipeline_orients_and_strips_source_metadata(tmp_path, monkeypatch) -> None:
    from app.db import init_db
    init_db()
    source = tmp_path / "oriented.jpg"
    source.write_bytes(_jpeg(orientation=6))
    monkeypatch.chdir(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    facts, products = photo_derivatives.prepare(source, "image/jpeg", source_hash)
    assert (facts["width"], facts["height"], facts["orientation"]) == (20, 12, 6)
    assert facts["captured_at"] == "2024-02-03T04:05:06"
    assert facts["camera_make"] == "Camera Co" and "gps" not in facts["metadata"]
    derivative = photo_derivatives.derivative_path(products[400][0])
    with Image.open(derivative) as image:
        assert image.size == (20, 12)
        assert not image.getexif() and "exif" not in image.info

    register_heif_opener()
    heic = tmp_path / "phone.heic"
    Image.new("RGB", (32, 24), "green").save(heic, format="HEIF")
    content_type = photo_derivatives.sniff(heic.read_bytes()[:128])
    assert content_type == "image/heic"
    assert photo_derivatives.inspect(heic, content_type)["width"] == 32


def test_registered_external_source_indexes_in_place_resumes_and_never_deletes_source_bytes(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(background_work, "busy", lambda: False)
    monkeypatch.setattr(background_work, "close", lambda: None)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    photo_imports.worker._stop.clear()  # noqa: SLF001 - deterministic direct worker exercise
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "nas"
    folder.mkdir()
    source_file = folder / "family.jpg"
    source_file.write_bytes(_jpeg())
    admin = _user(db_factory, "photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        stored_admin = db.get(User, admin.id)
        stored_admin.role = "admin"
        db.add(StorageRoot(
            id="external-photo-root", label="Family NAS", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()

    with api_client(user=admin, base_url="http://localhost") as client:
        created = client.post("/api/admin/photo-sources", json={
            "root_id": "external-photo-root", "relative_folder": "", "visibility": "private",
        })
        assert created.status_code == 201, created.text
        source = created.json()
        assert source["root_label"] == "Family NAS" and "path" not in source
        duplicate = client.post("/api/admin/photo-sources", json={
            "root_id": "external-photo-root", "relative_folder": "", "visibility": "private",
        })
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "That photo source is already registered."
        assert str(folder) not in duplicate.text
        assert client.post("/api/admin/photo-sources", json={
            "root_id": "external-photo-root", "relative_folder": "../outside",
        }).status_code == 422

    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.
    photo_imports.worker.run_once()
    with db_factory() as db:
        assert db.query(PhotoItem).count() == 1, [
            (run.state, run.error_count) for run in db.query(photo_root_models.PhotoImportRun).all()
        ]
    with monkeypatch.context() as scoped:
        scoped.setattr(
            StorageRootService, "is_online",
            lambda _service, _root: (_ for _ in ()).throw(AssertionError("GET must not touch the NAS")),
        )
        with api_client(user=admin, base_url="http://localhost") as client:
            assert client.get("/api/admin/photo-sources").status_code == 200
    # A traversal failure is not evidence that previously indexed files went
    # missing. Only a fully completed online walk may reconcile absence.
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.post(f"/api/admin/photo-sources/{source['id']}/scan").status_code == 202
    with monkeypatch.context() as scoped:
        def failed_walk(*_args, **_kwargs):  # noqa: ANN202
            raise photo_imports.ScanIncomplete("walk_failed")
            yield  # pragma: no cover - make this a generator
        scoped.setattr(photo_imports, "walk", failed_walk)
        photo_imports.worker.run_once()
    with db_factory() as db:
        assert db.query(PhotoItem).one().missing_at is None

    # Slow decoding happens without a SQLite write transaction: an unrelated
    # foreground household write can still commit while the decoder is blocked.
    entered, release = threading.Event(), threading.Event()
    original_prepare = photo_derivatives.prepare
    source_file.write_bytes(_jpeg(orientation=6))
    os.utime(source_file, ns=(source_file.stat().st_atime_ns, source_file.stat().st_mtime_ns + 1_000_000))
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.post(f"/api/admin/photo-sources/{source['id']}/scan").status_code == 202
    def blocked_prepare(path, content_type, content_hash):  # noqa: ANN001,ANN202
        entered.set()
        assert release.wait(2)
        return original_prepare(path, content_type, content_hash)
    with monkeypatch.context() as scoped:
        scoped.setattr(photo_derivatives, "prepare", blocked_prepare)
        thread = threading.Thread(target=photo_imports.worker.run_once)
        thread.start()
        assert entered.wait(2)
        with db_factory() as db:
            stored_admin = db.get(User, admin.id)
            stored_admin.display_name = "Foreground write"
            db.commit()
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()

    with api_client(user=admin, base_url="http://localhost") as client:
        photo = client.get("/api/photos").json()["items"][0]
        assert client.get(photo["original_url"]).content == source_file.read_bytes()
        client.patch(f"/api/photos/{photo['id']}", json={"title": "Keep me", "favorite": True})
        source_file.write_bytes(_jpeg(orientation=6))
        os.utime(source_file, ns=(source_file.stat().st_atime_ns, source_file.stat().st_mtime_ns + 1_000_000))
        assert client.post(f"/api/admin/photo-sources/{source['id']}/scan").status_code == 202
    photo_imports.worker.run_once()
    with api_client(user=admin, base_url="http://localhost") as client:
        updated = client.get("/api/photos").json()["items"][0]
        assert updated["id"] == photo["id"] and updated["title"] == "Keep me" and updated["favorite"] is True
        assert (updated["width"], updated["height"]) == (20, 12)
        source_file.unlink()
        monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: False)
        client.post(f"/api/admin/photo-sources/{source['id']}/scan")
    photo_imports.worker.run_once()
    with db_factory() as db:
        assert db.get(PhotoItem, photo["id"]).missing_at is None, "an offline scan cannot mark files missing"
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    source_file.write_bytes(_jpeg(orientation=6))
    with api_client(user=admin, base_url="http://localhost") as client:
        client.patch(f"/api/photos/{photo['id']}", json={"deleted": True})
        source_file.unlink()
        client.post(f"/api/admin/photo-sources/{source['id']}/scan")
    photo_imports.worker.run_once()
    source_file.write_bytes(_jpeg())
    with api_client(user=admin, base_url="http://localhost") as client:
        client.post(f"/api/admin/photo-sources/{source['id']}/scan")
    photo_imports.worker.run_once()
    with db_factory() as db:
        stored = db.get(PhotoItem, photo["id"])
        assert stored.deleted_at is not None, "a rescan must not resurrect an item the owner trashed"
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.delete(f"/api/admin/photo-sources/{source['id']}").status_code == 204
    assert source_file.is_file(), "source deletion removes only Lumina's index"


def test_external_publication_rehashes_same_size_same_mtime_replacement(tmp_path) -> None:
    root = tmp_path / "nas"
    root.mkdir()
    source = root / "photo.jpg"
    original = b"first-bytes"
    replacement = b"other-bytes"
    assert len(original) == len(replacement)
    source.write_bytes(original)
    modified = source.stat().st_mtime_ns
    prepared = photo_imports.PreparedPhoto(
        relative="photo.jpg", root_relative="photo.jpg", filename="photo.jpg",
        size=len(original), mtime_ns=modified, unchanged=False,
        content_type="image/jpeg", content_hash=hashlib.sha256(original).hexdigest(), facts={}, products={},
    )
    source.write_bytes(replacement)
    os.utime(source, ns=(source.stat().st_atime_ns, modified))
    assert photo_imports._still_current(str(root), prepared) is False  # noqa: SLF001 - publication trust boundary


def test_one_external_source_prepares_two_files_with_bounded_ordered_lookahead(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(background_work, "closed", False)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "parallel-nas"
    folder.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
        (folder / name).write_bytes(_jpeg() + name.encode())
    admin = _user(db_factory, "parallel-photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(
            id="parallel-photo-root", label="Parallel", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        source = client.post("/api/admin/photo-sources", json={
            "root_id": "parallel-photo-root", "relative_folder": "", "visibility": "private",
        }).json()
    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.

    original_walk = photo_imports.walk
    walked: list[str] = []
    entered: list[str] = []
    release = threading.Event()
    two_entered = threading.Event()
    lock = threading.Lock()

    def tracked_walk(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        for relative, entry in original_walk(*args, **kwargs):
            walked.append(relative)
            yield relative, entry

    def blocked_prepare(source_id, relative, entry):  # noqa: ANN001,ANN202
        with lock:
            entered.append(relative)
            if len(entered) == 2:
                two_entered.set()
        assert release.wait(5)
        return _prepared_test_file(relative, entry)

    monkeypatch.setattr(photo_imports, "walk", tracked_walk)
    monkeypatch.setattr(photo_imports, "_prepare_one", blocked_prepare)
    worker = photo_imports.PhotoImportWorker()
    lane = threading.Thread(target=worker.run_once)
    lane.start()
    try:
        assert two_entered.wait(5), "one source never used its second preparation slot"
        assert set(entered) == {"a.jpg", "b.jpg"}
        assert walked == ["a.jpg", "b.jpg"], "the source prefetched past its bounded lookahead"
        release.set()
        lane.join(timeout=10)
        assert not lane.is_alive()
        with db_factory() as db:
            stored = db.get(photo_root_models.PhotoImportSource, source["id"])
            run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=stored.id).one()
            assert run.state == "succeeded" and run.cursor == "d.jpg"
            assert run.seen_count == 4 and db.query(PhotoItem).filter_by(source_id=stored.id).count() == 4
    finally:
        release.set()
        lane.join(timeout=5)
        worker.close()


def test_cancel_does_not_advance_cursor_past_a_slower_earlier_file(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(background_work, "closed", False)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "cancel-nas"
    folder.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (folder / name).write_bytes(_jpeg() + name.encode())
    admin = _user(db_factory, "cancel-photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(
            id="cancel-photo-root", label="Cancel", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        source = client.post("/api/admin/photo-sources", json={
            "root_id": "cancel-photo-root", "relative_folder": "", "visibility": "private",
        }).json()
    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.

    first_entered, release_first, second_finished = threading.Event(), threading.Event(), threading.Event()

    def out_of_order_prepare(source_id, relative, entry):  # noqa: ANN001,ANN202
        if relative == "a.jpg":
            first_entered.set()
            assert release_first.wait(5)
        result = _prepared_test_file(relative, entry)
        if relative == "b.jpg":
            second_finished.set()
        return result

    monkeypatch.setattr(photo_imports, "_prepare_one", out_of_order_prepare)
    worker = photo_imports.PhotoImportWorker()
    lane = threading.Thread(target=worker.run_once)
    lane.start()
    try:
        assert first_entered.wait(5) and second_finished.wait(5)
        with api_client(user=admin, base_url="http://localhost") as client:
            assert client.post(f"/api/admin/photo-sources/{source['id']}/cancel").status_code == 200
        lane.join(timeout=5)
        assert not lane.is_alive(), "cancel waited for the blocked preparation"
        with db_factory() as db:
            run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=source["id"]).one()
            assert run.state == "canceled" and run.cursor == "" and run.seen_count == 0
            assert db.query(PhotoItem).filter_by(source_id=source["id"]).count() == 0
    finally:
        release_first.set()
        lane.join(timeout=5)
        worker.close()

    monkeypatch.setattr(
        photo_imports, "_prepare_one",
        lambda _source_id, relative, entry: _prepared_test_file(relative, entry),
    )
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.post(f"/api/admin/photo-sources/{source['id']}/scan").status_code == 202
    background_work.closed = False
    restarted = photo_imports.PhotoImportWorker()
    try:
        assert restarted.run_once() is True
    finally:
        restarted.close()
    with db_factory() as db:
        runs = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=source["id"]).all()
        assert sorted(run.state for run in runs) == ["canceled", "succeeded"]
        assert db.query(PhotoItem).filter_by(source_id=source["id"]).count() == 3


def test_shutdown_retirement_discards_a_late_preparation(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "shutdown-nas"
    folder.mkdir()
    (folder / "late.jpg").write_bytes(_jpeg())
    admin = _user(db_factory, "shutdown-photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(
            id="shutdown-photo-root", label="Shutdown", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        source = client.post("/api/admin/photo-sources", json={
            "root_id": "shutdown-photo-root", "relative_folder": "", "visibility": "private",
        }).json()
    background_work.closed = False

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_prepare(_source_id, relative, entry):  # noqa: ANN001,ANN202
        entered.set()
        assert release.wait(5)
        try:
            return _prepared_test_file(relative, entry)
        finally:
            finished.set()

    monkeypatch.setattr(photo_imports, "_prepare_one", blocked_prepare)
    worker = photo_imports.PhotoImportWorker()
    generation = worker._generation  # noqa: SLF001 - exercise retirement of this exact generation
    lane = threading.Thread(target=worker._run_guarded_once, args=(generation,))  # noqa: SLF001
    lane.start()
    try:
        assert entered.wait(5)
        worker.close()
        with db_factory() as db:
            run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=source["id"]).one()
            assert run.state == "running" and run.cursor == ""
            assert db.query(PhotoItem).filter_by(source_id=source["id"]).count() == 0
        release.set()
        assert finished.wait(5)
        lane.join(timeout=5)
        with db_factory() as db:
            run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=source["id"]).one()
            assert run.state == "running" and run.cursor == ""
            assert db.query(PhotoItem).filter_by(source_id=source["id"]).count() == 0
    finally:
        release.set()
        lane.join(timeout=5)
        worker.close()


def test_preparation_pool_close_retires_global_admission_waiters(monkeypatch) -> None:
    class HeldAdmission:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self._lock = threading.Lock()
            self._waiters: set[int] = set()

        def acquire(self, *, timeout):  # noqa: ANN001,ANN202
            with self._lock:
                self._waiters.add(threading.get_ident())
                if len(self._waiters) == photo_imports.MAX_PREPARATION_WORKERS:
                    self.entered.set()
            threading.Event().wait(timeout)
            return False

        def release(self) -> None:
            raise AssertionError("a waiter without admission released the global slot")

    admission = HeldAdmission()
    prepared = threading.Event()
    monkeypatch.setattr(photo_imports, "_PREPARATION_SLOTS", admission)
    monkeypatch.setattr(photo_imports, "_prepare_result", lambda *_args: prepared.set())
    pool = photo_imports._PreparationPool()  # noqa: SLF001 - bounded shutdown contract
    futures = [
        pool.submit("source", f"{index}.jpg", None, lambda: (True, True))
        for index in range(4)
    ]
    assert admission.entered.wait(5)
    pool.close()
    assert not prepared.is_set()
    assert all(future.done() and isinstance(future.exception(), RuntimeError) for future in futures)
    assert not any(thread.is_alive() for thread in pool._threads)  # noqa: SLF001


def test_automatic_preparation_rechecks_gate_after_global_admission(monkeypatch) -> None:
    class ControlledAdmission:
        def __init__(self) -> None:
            self.waiting = threading.Event()
            self.capacity = threading.Event()
            self.released = threading.Event()

        def acquire(self, *, timeout):  # noqa: ANN001,ANN202
            self.waiting.set()
            return self.capacity.wait(timeout)

        def release(self) -> None:
            self.released.set()

    admission = ControlledAdmission()
    allowed, paused, prepared = threading.Event(), threading.Event(), threading.Event()
    allowed.set()
    monkeypatch.setattr(background_work, "backfill_allowed", lambda: allowed.is_set())

    def ready() -> tuple[bool, bool]:
        backfill_allowed = background_work.backfill_allowed()
        if not backfill_allowed:
            paused.set()
        return True, backfill_allowed

    monkeypatch.setattr(photo_imports, "_PREPARATION_SLOTS", admission)
    monkeypatch.setattr(photo_imports, "_prepare_result", lambda *_args: prepared.set())
    pool = photo_imports._PreparationPool()  # noqa: SLF001 - automatic admission contract
    first = pool.submit("source", "first.jpg", None, ready)
    try:
        assert admission.waiting.wait(5)
        allowed.clear()
        admission.capacity.set()
        assert admission.released.wait(5)
        assert paused.wait(5)
        assert not prepared.is_set(), "automatic work ran after its window closed"
        allowed.set()
        assert prepared.wait(5)
        first.result(timeout=5)

        admission.capacity.clear()
        admission.waiting.clear()
        prepared.clear()
        stopped = threading.Event()
        canceled = pool.submit(
            "source", "canceled.jpg", None,
            lambda: (not stopped.is_set(), True),
        )
        assert admission.waiting.wait(5)
        stopped.set()
        assert isinstance(canceled.exception(timeout=5), RuntimeError)
        assert not prepared.is_set(), "canceled queued work reached preparation"

        allowed.clear()
        paused.clear()
        second = pool.submit("source", "second.jpg", None, ready)
        assert paused.wait(5)
        pool.close()
        assert isinstance(second.exception(timeout=5), RuntimeError)
        assert not any(thread.is_alive() for thread in pool._threads)  # noqa: SLF001
    finally:
        pool.close()


def test_external_scan_skips_one_bad_file_without_starving_later_photos(
    api_client, db_factory, monkeypatch, tmp_path, request,
) -> None:
    from app.db import init_db
    from support import file_backed_session_factory

    init_db()  # TestClient startup also uses this file; retain its schema stamp.
    # Preparation threads need independent connections; StaticPool shares one
    # connection whose reader rollback can discard another session's writes.
    original_bind = db_factory.kw["bind"]
    db_factory.configure(bind=file_backed_session_factory().kw["bind"])
    request.addfinalizer(lambda: db_factory.configure(bind=original_bind))
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(background_work, "busy", lambda: False)
    monkeypatch.setattr(background_work, "close", lambda: None)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    photo_imports.worker._stop.clear()  # noqa: SLF001
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "partial-nas"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(_jpeg() + b"a")
    (folder / "b.jpg").write_bytes(b"not an image")
    (folder / "c.jpg").write_bytes(_jpeg() + b"c")
    admin = _user(db_factory, "partial-photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(
            id="partial-photo-root", label="Partial NAS", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        source = client.post("/api/admin/photo-sources", json={
            "root_id": "partial-photo-root", "relative_folder": "", "visibility": "private",
        }).json()
    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.
    assert photo_imports.worker.run_once() is True
    with api_client(user=admin, base_url="http://localhost") as client:
        page = client.get("/api/photos").json()
        status = client.get("/api/admin/photo-sources").json()["items"][0]
        assert sorted(item["filename"] for item in page["items"]) == ["a.jpg", "c.jpg"], status
        assert status["state"] == "failed" and status["error_code"] == "partial_scan"
        assert status["error_count"] == 1 and status["indexed_count"] == 2
        (folder / "b.jpg").write_bytes(_jpeg() + b"b")
        assert client.post(f"/api/admin/photo-sources/{source['id']}/scan").status_code == 202
    assert photo_imports.worker.run_once() is True
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.get("/api/photos").json()["total"] == 3
        status = client.get("/api/admin/photo-sources").json()["items"][0]
        assert status["state"] == "idle" and status["error_count"] == 0


def test_photo_sources_reject_component_overlaps_but_allow_sibling_names(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    folder = tmp_path / "overlap-nas"
    (folder / "Family" / "Trips").mkdir(parents=True)
    (folder / "Family2").mkdir()
    admin = _user(db_factory, "overlap-photo-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(
            id="overlap-photo-root", label="Overlap NAS", path=str(folder), mode="external",
            enabled=True, identity="test", observation={},
        ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        def create(relative_folder: str):  # noqa: ANN202
            return client.post("/api/admin/photo-sources", json={
                "root_id": "overlap-photo-root", "relative_folder": relative_folder,
                "visibility": "private",
            })
        assert create("Family").status_code == 201
        assert create("Family/Trips").status_code == 422
        assert create("").status_code == 422
        assert create("Family2").status_code == 201


def test_stalled_nas_source_does_not_block_another_source(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(background_work, "busy", lambda: False)
    monkeypatch.setattr(background_work, "close", lambda: None)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    admin = _user(db_factory, "lanes-photo-admin")
    admin.role = "admin"
    roots = []
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        for name in ("stalled", "healthy"):
            folder = tmp_path / name
            folder.mkdir()
            filenames = ("stalled-a.jpg", "stalled-b.jpg") if name == "stalled" else ("healthy.jpg",)
            for filename in filenames:
                (folder / filename).write_bytes(_jpeg() + filename.encode())
            root_id = f"{name}-photo-root"
            roots.append((root_id, folder))
            db.add(StorageRoot(
                id=root_id, label=name.title(), path=str(folder), mode="external",
                enabled=True, identity="test", observation={},
            ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        for root_id, _folder in roots:
            assert client.post("/api/admin/photo-sources", json={
                "root_id": root_id, "relative_folder": "", "visibility": "private",
            }).status_code == 201

    entered, release = threading.Event(), threading.Event()
    stalled_count = 0
    stalled_lock = threading.Lock()

    def prepare(_source_id, relative, entry):  # noqa: ANN001,ANN202
        nonlocal stalled_count
        if relative.startswith("stalled-"):
            with stalled_lock:
                stalled_count += 1
                if stalled_count == 2:
                    entered.set()
            assert release.wait(5)
        return _prepared_test_file(relative, entry)

    monkeypatch.setattr(photo_imports, "_prepare_one", prepare)
    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.
    worker = photo_imports.PhotoImportWorker()
    stalled_lane = threading.Thread(target=worker._run_guarded_once, args=(None,))  # noqa: SLF001
    healthy_lane = threading.Thread(target=worker._run_guarded_once, args=(None,))  # noqa: SLF001
    stalled_lane.start()
    try:
        assert entered.wait(5), "one stalled source did not fill its two-slot lookahead"
        healthy_lane.start()
        healthy_lane.join(timeout=5)
        assert not healthy_lane.is_alive(), "a stalled NAS consumed every photo source lane"
        with db_factory() as db:
            assert db.query(PhotoItem).filter(PhotoItem.root_id == "healthy-photo-root").count() == 1
    finally:
        release.set()
        stalled_lane.join(timeout=5)
        assert not stalled_lane.is_alive()
        worker.close()


def test_worker_lane_failure_does_not_fail_another_active_scan(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(background_work, "busy", lambda: False)
    monkeypatch.setattr(background_work, "close", lambda: None)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    admin = _user(db_factory, "isolated-lanes-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        for name in ("bad-lane", "healthy-lane"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / f"{name}.jpg").write_bytes(_jpeg() + name.encode())
            db.add(StorageRoot(
                id=name, label=name, path=str(folder), mode="external", enabled=True,
                identity="test", observation={},
            ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        for root_id in ("healthy-lane", "bad-lane"):
            assert client.post("/api/admin/photo-sources", json={
                "root_id": root_id, "relative_folder": "", "visibility": "private",
            }).status_code == 201

    healthy_entered, release_healthy = threading.Event(), threading.Event()
    original_prepare = photo_imports._prepare_one  # noqa: SLF001 - inject one unexpected lane failure
    def prepare(source_id, relative, entry):  # noqa: ANN001,ANN202
        if entry.name == "bad-lane.jpg":
            raise SystemExit("synthetic decoder crash")
        if entry.name == "healthy-lane.jpg":
            healthy_entered.set()
            assert release_healthy.wait(5)
        return original_prepare(source_id, relative, entry)
    monkeypatch.setattr(photo_imports, "_prepare_one", prepare)

    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.
    worker = photo_imports.PhotoImportWorker()
    healthy_lane = threading.Thread(target=worker._run_guarded_once, args=(None,))  # noqa: SLF001
    bad_lane = threading.Thread(target=worker._run_guarded_once, args=(None,))  # noqa: SLF001
    healthy_lane.start()
    try:
        assert healthy_entered.wait(5)
        bad_lane.start()
        bad_lane.join(timeout=5)
        assert not bad_lane.is_alive(), "the failing source lane did not become terminal"
        with db_factory() as db:
            bad_source = db.query(photo_root_models.PhotoImportSource).filter_by(root_id="bad-lane").one()
            healthy_source = db.query(photo_root_models.PhotoImportSource).filter_by(root_id="healthy-lane").one()
            bad_run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=bad_source.id).one()
            healthy_run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=healthy_source.id).one()
            assert bad_run.state == "failed"
            assert bad_run.error_count == 1 and bad_source.error_code == "scan_failed"
            assert healthy_run.state == "running" and healthy_source.state == "scanning"
        release_healthy.set()
        healthy_lane.join(timeout=5)
        assert not healthy_lane.is_alive(), "the healthy source did not finish after the other lane failed"
        with db_factory() as db:
            healthy_source = db.query(photo_root_models.PhotoImportSource).filter_by(root_id="healthy-lane").one()
            healthy_run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=healthy_source.id).one()
            assert healthy_run.state == "succeeded"
            assert db.query(PhotoItem).filter_by(source_id=healthy_source.id).count() == 1
    finally:
        release_healthy.set()
        healthy_lane.join(timeout=5)
        if bad_lane.ident is not None:
            bad_lane.join(timeout=5)
        worker.close()


def test_manual_scan_progresses_during_http_activity_but_watch_scan_waits(
    api_client, db_factory, monkeypatch, tmp_path,
) -> None:
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(background_work, "busy", lambda: True)
    monkeypatch.setattr(background_work, "backfill_allowed", lambda: False)
    monkeypatch.setattr(background_work, "closed", False)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    admin = _user(db_factory, "requested-scan-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        for name in ("manual-source", "watch-source"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / f"{name}.jpg").write_bytes(_jpeg() + name.encode())
            db.add(StorageRoot(
                id=name, label=name, path=str(folder), mode="external", enabled=True,
                identity="test", observation={},
            ))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        manual = client.post("/api/admin/photo-sources", json={
            "root_id": "manual-source", "relative_folder": "", "visibility": "private",
        })
        assert manual.status_code == 201
    background_work.closed = False  # TestClient lifespan shutdown closes the process-wide gate.
    with db_factory() as db:
        watch = photo_root_models.PhotoImportSource(
            id="watch-source-id", root_id="watch-source", relative_folder="",
            owner_user_id=admin.id, visibility="private", state="queued", scan_requested=True,
        )
        db.add(watch)
        db.add(photo_root_models.PhotoImportRun(
            id="watch-run-id", source_id=watch.id, state="queued", trigger="watch",
        ))
        db.commit()

    worker = photo_imports.PhotoImportWorker()
    assert worker.run_once() is True
    with db_factory() as db:
        manual_source = db.query(photo_root_models.PhotoImportSource).filter_by(root_id="manual-source").one()
        manual_run = db.query(photo_root_models.PhotoImportRun).filter_by(source_id=manual_source.id).one()
        watch_run = db.get(photo_root_models.PhotoImportRun, "watch-run-id")
        assert manual_run.state == "succeeded"
        assert db.query(PhotoItem).filter_by(source_id=manual_source.id).count() == 1
        assert watch_run.state == "queued"
    assert worker.run_once() is False
    worker.close()


@pytest.mark.parametrize("reason", ["full", "busy"])
def test_upload_storage_pressure_is_retryable_without_a_catalog_entry(api_client, db_factory, monkeypatch, reason) -> None:  # noqa: ANN001
    from app.services import photo_cache

    user = _user(db_factory, "storage-pressure")
    error = photo_cache.PhotoStorageFull("quota") if reason == "full" else photo_cache.PhotoStorageBusy()

    def blocked(*_args):  # noqa: ANN002,ANN202
        raise error

    monkeypatch.setattr(photo_derivatives, "prepare", blocked)
    response = api_client(user=user, base_url="http://localhost").put(
        "/api/photos/import?filename=photo.jpg", content=_jpeg(), headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == error.code
    assert response.headers["retry-after"] == str(error.retry_after)
    with db_factory() as db:
        assert db.query(PhotoItem).count() == 0


@pytest.mark.parametrize("prefix_busy", [False, True])
def test_external_storage_pressure_preserves_cursor_and_resumes(api_client, db_factory, monkeypatch, tmp_path, prefix_busy) -> None:  # noqa: ANN001
    from app.photo_root_models import PhotoImportRun
    from app.services import photo_cache

    _stub_pipeline(monkeypatch)
    prepare = photo_imports._prepare_one  # noqa: SLF001
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_imports, "session_scope", lambda: session_scope(db_factory))
    monkeypatch.setattr(background_work, "busy", lambda: False)
    monkeypatch.setattr(background_work, "close", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "start", lambda: None)
    monkeypatch.setattr(photo_imports.worker, "close", lambda: None)
    photo_imports.worker._stop.clear()  # noqa: SLF001
    folder = tmp_path / "storage-pressure-source"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(_jpeg())
    (folder / "b.jpg").write_bytes(_jpeg() + b"second")
    admin = _user(db_factory, "storage-pressure-admin")
    admin.role = "admin"
    with db_factory() as db:
        db.get(User, admin.id).role = "admin"
        db.add(StorageRoot(id="pressure-root", label="Photos", path=str(folder), mode="external", enabled=True, identity="test", observation={}))
        db.commit()
    with api_client(user=admin, base_url="http://localhost") as client:
        assert client.post("/api/admin/photo-sources", json={"root_id": "pressure-root", "relative_folder": "", "visibility": "private"}).status_code == 201

    def blocked(source_id, relative, entry):  # noqa: ANN001,ANN202
        if relative == "b.jpg":
            raise photo_cache.PhotoStorageFull("quota")
        return prepare(source_id, relative, entry)

    monkeypatch.setattr(photo_imports, "_prepare_one", blocked)
    original_check = photo_imports._still_current  # noqa: SLF001
    contended = prefix_busy

    def revalidate(*args):  # noqa: ANN002,ANN202
        nonlocal contended
        if contended:
            contended = False
            raise photo_imports.PhotoSnapshotBusy()
        return original_check(*args)

    monkeypatch.setattr(photo_imports, "_still_current", revalidate)
    background_work.closed = False
    assert photo_imports.worker.run_once() is False
    if prefix_busy:
        with db_factory() as db:
            run = db.query(PhotoImportRun).one()
            assert run.state == "queued" and run.error_count == 0 and not run.cursor
            assert db.query(PhotoItem).count() == 0
        assert photo_imports.worker.run_once() is False
    with db_factory() as db:
        run = db.query(PhotoImportRun).one()
        assert run.state == "queued" and run.error_count == 0 and run.cursor == "a.jpg"
        assert db.query(PhotoItem).count() == 1
    monkeypatch.setattr(photo_imports, "_prepare_one", prepare)
    assert photo_imports.worker.run_once() is True
    with db_factory() as db:
        run = db.query(PhotoImportRun).one()
        assert run.state == "succeeded" and run.error_count == 0
        assert db.query(PhotoItem).count() == 2
