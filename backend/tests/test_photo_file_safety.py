from __future__ import annotations

import hashlib
import io
import os
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app.config import EnvSettings
from app.db import session_scope
from app.models import StorageRoot
from app.models import utcnow
from app.photo_models import PhotoDerivative, PhotoItem
from app.photo_preview_models import PhotoPreviewAsset, PhotoPreviewStorage
from app.services import photo_cache, photo_derivatives, photo_files
from app.services.storage_roots import StorageRootService
from support import make_user


OWNER = make_user("safe-photo-owner")


@pytest.fixture(autouse=True)
def preview_registry() -> None:
    from app import db as db_module
    PhotoPreviewAsset.__table__.create(db_module.engine, checkfirst=True)
    PhotoPreviewStorage.__table__.create(db_module.engine, checkfirst=True)
    PhotoItem.__table__.create(db_module.engine, checkfirst=True)
    PhotoDerivative.__table__.create(db_module.engine, checkfirst=True)


def jpeg(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format="JPEG")
    return output.getvalue()


def _prepared_payload(target, **updates) -> dict:  # noqa: ANN001
    products = {}
    for width in photo_derivatives.WIDTHS:
        output = target / f"{width}.webp"
        Image.new("RGB", (2, 1), "red").save(output, format="WEBP")
        products[str(width)] = output.name
    payload = {
        "ok": True, "products": products, "content_type": "image/jpeg",
        "width": 16, "height": 12, "orientation": 1,
        "captured_at": None, "captured_at_raw": None,
        "camera_make": None, "camera_model": None,
        "metadata": {
            "lens": None, "iso": None, "focal_length": None,
            "aperture": None, "exposure_time": None, "has_embedded_icc": False,
        },
    }
    payload.update(updates)
    return payload


def test_external_snapshot_is_immutable_and_refuses_symlink_swaps(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "nas"
    nested = root / "family"
    nested.mkdir(parents=True)
    source = nested / "photo.jpg"
    original = jpeg("red")
    source.write_bytes(original)
    work = tmp_path / "work"

    with photo_files.snapshot_external(
        root, "family/photo.jpg", work, expected_sha256=hashlib.sha256(original).hexdigest(),
    ) as snapshot:
        source.unlink()
        source.symlink_to(tmp_path / "secret.jpg")
        assert snapshot.path.read_bytes() == original
        assert snapshot.fingerprint.size == len(original)

    (tmp_path / "secret.jpg").write_bytes(jpeg("blue"))
    with pytest.raises(photo_files.PhotoFileUnavailable):
        with photo_files.snapshot_external(root, "family/photo.jpg", work):
            pass

    source.unlink()
    (root / "family").rmdir()
    (root / "family").symlink_to(tmp_path)
    with pytest.raises(photo_files.PhotoFileUnavailable):
        with photo_files.snapshot_external(root, "family/secret.jpg", work):
            pass


def test_snapshot_admission_is_nonblocking_and_released_after_errors(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    root = tmp_path / "nas"
    root.mkdir()
    source = root / "photo.jpg"
    source.write_bytes(jpeg())
    monkeypatch.setattr(photo_files, "_SNAPSHOT_ADMISSION", threading.BoundedSemaphore(1))
    held = photo_files.snapshot_external(root, "photo.jpg", tmp_path / "work")
    held.__enter__()
    with pytest.raises(photo_files.PhotoSnapshotBusy):
        with photo_files.snapshot_external(root, "photo.jpg", tmp_path / "work"):
            pass
    held.__exit__(None, None, None)
    with pytest.raises(photo_files.PhotoFileChanged):
        with photo_files.snapshot_external(
            root, "photo.jpg", tmp_path / "work", expected_sha256="0" * 64,
        ):
            pass
    with photo_files.snapshot_external(root, "photo.jpg", tmp_path / "work") as snapshot:
        assert snapshot.fingerprint.size == source.stat().st_size


def test_render_refuses_to_install_bytes_under_the_wrong_content_hash(tmp_path) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    wrong = "0" * 64
    with pytest.raises(photo_derivatives.PhotoDecodeError, match="content changed"):
        photo_derivatives.render(source, wrong)
    assert not photo_derivatives.derivative_path(photo_derivatives.source_key(wrong, 400)).exists()


@pytest.mark.parametrize(
    ("invalid_facts", "message"),
    [({"content_type": "image/png"}, "container"), ({"orientation": 9}, "metadata")],
)
def test_prepare_rejects_untrusted_facts_before_publishing(
    tmp_path, monkeypatch, invalid_facts, message,
) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def invalid_child(_mode, _source, target):  # noqa: ANN001,ANN202
        return _prepared_payload(target, **invalid_facts)

    monkeypatch.setattr(photo_derivatives, "_run", invalid_child)
    with pytest.raises(photo_derivatives.PhotoDecodeError, match=message):
        photo_derivatives.prepare(source, "image/jpeg", content_hash)
    for width in photo_derivatives.WIDTHS:
        assert not photo_derivatives.derivative_path(photo_derivatives.source_key(content_hash, width)).exists()


def test_prepare_rehashes_source_after_decode_before_publishing(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg("red"))
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def mutating_child(_mode, _source, target):  # noqa: ANN001,ANN202
        payload = _prepared_payload(target)
        source.write_bytes(jpeg("blue"))
        return payload

    monkeypatch.setattr(photo_derivatives, "_run", mutating_child)
    with pytest.raises(photo_derivatives.PhotoDecodeError, match="content changed"):
        photo_derivatives.prepare(source, "image/jpeg", content_hash)
    for width in photo_derivatives.WIDTHS:
        assert not photo_derivatives.derivative_path(photo_derivatives.source_key(content_hash, width)).exists()


def test_prepare_validates_the_complete_product_set_before_publishing(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def incomplete_child(_mode, _source, target):  # noqa: ANN001,ANN202
        payload = _prepared_payload(target)
        del payload["products"]["2400"]
        return payload

    monkeypatch.setattr(photo_derivatives, "_run", incomplete_child)
    with pytest.raises(photo_derivatives.PhotoDecodeError, match="incomplete"):
        photo_derivatives.prepare(source, "image/jpeg", content_hash)
    for width in photo_derivatives.WIDTHS:
        assert not photo_derivatives.derivative_path(photo_derivatives.source_key(content_hash, width)).exists()


def test_original_endpoint_never_follows_replaced_external_path(api_client, db_factory, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(StorageRootService, "is_online", lambda _service, _root: True)
    monkeypatch.setattr(photo_files, "_SNAPSHOT_ADMISSION", threading.BoundedSemaphore(1))
    root = tmp_path / "nas"
    root.mkdir()
    source = root / "portrait.jpg"
    original = jpeg("red")
    source.write_bytes(original)
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(jpeg("blue"))
    with db_factory() as db:
        db.add(make_user(OWNER.id))
        db.add(StorageRoot(
            id="safe-root", label="NAS", path=str(root), mode="external", enabled=True,
            identity="test", observation={},
        ))
        db.add(PhotoItem(
            id="safe-photo", owner_user_id=OWNER.id, visibility="private", storage_kind="external",
            storage_key="external:safe-root:portrait.jpg", root_id="safe-root", relative_path="portrait.jpg",
            original_filename="portrait.jpg", content_type="image/jpeg",
            sha256=hashlib.sha256(original).hexdigest(), bytes=len(original), width=16, height=12,
            orientation=1, metadata_json={},
        ))
        db.commit()

    assert photo_files._SNAPSHOT_ADMISSION.acquire(blocking=False)  # noqa: SLF001
    busy = api_client(user=OWNER, base_url="http://localhost").get("/api/photos/safe-photo/original")
    photo_files._SNAPSHOT_ADMISSION.release()  # noqa: SLF001
    assert busy.status_code == 503 and busy.headers["retry-after"] == "2"

    valid = api_client(user=OWNER, base_url="http://localhost").get("/api/photos/safe-photo/original")
    assert valid.status_code == 200 and valid.content == original
    assert photo_files._SNAPSHOT_ADMISSION.acquire(blocking=False)  # noqa: SLF001
    photo_files._SNAPSHOT_ADMISSION.release()  # noqa: SLF001

    source.unlink()
    os.symlink(secret, source)
    response = api_client(user=OWNER, base_url="http://localhost").get("/api/photos/safe-photo/original")
    assert response.status_code == 404
    assert secret.read_bytes() not in response.content


def test_preview_reclaim_never_evicts_a_referenced_ready_asset(monkeypatch) -> None:  # noqa: ANN001
    root = photo_derivatives.photos_root()
    original = root / "originals" / "aa" / "keep.jpg"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"original")
    referenced, orphan = "1" * 64, "2" * 64
    for key in (referenced, orphan):
        path = photo_derivatives.derivative_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = io.BytesIO()
        Image.new("RGB", (2, 1), "red").save(output, format="WEBP")
        path.write_bytes(output.getvalue())
    old = utcnow() - timedelta(hours=1)
    with session_scope() as db:
        db.add_all([
            PhotoPreviewStorage(id=1, used_bytes=100, reserved_bytes=0),
            PhotoPreviewAsset(
                source_key=referenced, content_hash="a" * 64, width=400, state="ready",
                bytes=50, reserved_bytes=0, updated_at=old,
            ),
            PhotoPreviewAsset(
                source_key=orphan, content_hash="b" * 64, width=400, state="ready",
                bytes=50, reserved_bytes=0, updated_at=old,
            ),
            PhotoDerivative(
                id="kept-preview", photo_id="photo", width=400, source_key=referenced,
                state="ready", bytes=50,
            ),
        ])
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 1)
    photo_cache.trim(force=True)
    assert photo_derivatives.derivative_path(referenced).is_file()
    assert not photo_derivatives.derivative_path(orphan).exists()
    assert original.read_bytes() == b"original"


def test_preview_reclaim_advances_one_bounded_registry_page_per_call() -> None:
    output = io.BytesIO()
    Image.new("RGB", (2, 1), "red").save(output, format="WEBP")
    payload = output.getvalue()
    assets = []
    for index in range(photo_cache.RECLAIM_BATCH + 44):
        key = f"{index:064x}"
        path = photo_derivatives.derivative_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        assets.append(PhotoPreviewAsset(
            source_key=key, content_hash=hashlib.sha256(key.encode()).hexdigest(),
            width=400, state="ready", bytes=len(payload), reserved_bytes=0,
        ))
    with session_scope() as db:
        db.add(PhotoPreviewStorage(
            id=1, used_bytes=len(payload) * len(assets), reserved_bytes=0,
        ))
        db.add_all(assets)

    photo_cache.trim(force=True)
    with session_scope() as db:
        assert db.query(PhotoPreviewAsset).count() == 44
        assert db.get(PhotoPreviewStorage, 1).sweep_after == f"{photo_cache.RECLAIM_BATCH - 1:064x}"
    photo_cache.trim(force=True)
    with session_scope() as db:
        assert db.query(PhotoPreviewAsset).count() == 0


def test_preview_capacity_refuses_before_starting_the_decoder(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 1)
    monkeypatch.setattr(photo_cache.settings, "photo_preview_min_free_bytes", 0)
    monkeypatch.setattr(
        photo_derivatives, "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decoder must not start")),
    )
    with pytest.raises(photo_cache.PhotoStorageFull) as error:
        photo_derivatives.prepare(source, "image/jpeg", content_hash)
    assert error.value.code == "preview_storage_full" and error.value.reason == "quota"


def test_preview_free_space_floor_refuses_before_starting_the_decoder(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 0)
    monkeypatch.setattr(photo_cache.settings, "photo_preview_min_free_bytes", 1)
    monkeypatch.setattr(photo_cache.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    monkeypatch.setattr(
        photo_derivatives, "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decoder must not start")),
    )
    with pytest.raises(photo_cache.PhotoStorageFull) as error:
        photo_derivatives.prepare(source, "image/jpeg", content_hash)
    assert error.value.code == "preview_storage_full" and error.value.reason == "free_space"


def test_lowered_quota_allows_promised_preview_repair_but_blocks_a_new_set(
    tmp_path, monkeypatch,
) -> None:  # noqa: ANN001
    source = tmp_path / "repair.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 32 * 1024 * 1024)
    monkeypatch.setattr(photo_cache.settings, "photo_preview_min_free_bytes", 0)
    monkeypatch.setattr(
        photo_derivatives, "_run", lambda _mode, _source, target: _prepared_payload(target),
    )
    with session_scope() as db:
        db.add(PhotoPreviewStorage(id=1, used_bytes=64 * 1024 * 1024, reserved_bytes=0))
        for width in photo_derivatives.WIDTHS:
            key = photo_derivatives.source_key(content_hash, width)
            db.add(PhotoPreviewAsset(
                source_key=key, content_hash=content_hash, width=width, state="ready",
                bytes=100, reserved_bytes=0,
            ))
            db.add(PhotoDerivative(
                id=f"repair-{width}", photo_id="repair-photo", width=width,
                source_key=key, state="ready", bytes=100,
            ))

    _facts, repaired = photo_derivatives.prepare(source, "image/jpeg", content_hash)
    photo_cache.release_publication({key for key, _size in repaired.values()})
    assert all(photo_derivatives.derivative_path(key).is_file() for key, _size in repaired.values())

    new_source = tmp_path / "new.jpg"
    new_source.write_bytes(jpeg("blue"))
    new_hash = hashlib.sha256(new_source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        photo_derivatives, "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decoder must not start")),
    )
    with pytest.raises(photo_cache.PhotoStorageFull) as error:
        photo_derivatives.prepare(new_source, "image/jpeg", new_hash)
    assert error.value.reason == "quota"


def test_published_previews_stay_pinned_until_the_catalog_transaction_resolves(
    tmp_path, monkeypatch,
) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 0)
    monkeypatch.setattr(photo_cache.settings, "photo_preview_min_free_bytes", 0)
    monkeypatch.setattr(
        photo_derivatives, "_run", lambda _mode, _source, target: _prepared_payload(target),
    )

    _facts, products = photo_derivatives.prepare(source, "image/jpeg", content_hash)
    keys = {key for key, _size in products.values()}
    with session_scope() as db:
        assert all(db.get(PhotoPreviewAsset, key).reservation_id is not None for key in keys)

    # Neither ordinary maintenance nor an obsolete-source purge may cross the
    # durable publication-to-catalog pin, regardless of elapsed batch time.
    photo_cache.trim(force=True)
    photo_cache.purge(keys)
    assert all(photo_derivatives.derivative_path(key).is_file() for key in keys)

    with session_scope() as db:
        for width, (key, size) in products.items():
            db.add(PhotoDerivative(
                id=f"cataloged-{width}", photo_id="cataloged", width=width,
                source_key=key, state="ready", bytes=size,
            ))
    photo_cache.release_publication(keys)
    with session_scope() as db:
        assert all(db.get(PhotoPreviewAsset, key).reservation_id is None for key in keys)
    assert all(photo_derivatives.derivative_path(key).is_file() for key in keys)

    # Reusing existing cataloged files also pins them: their last old catalog
    # reference may be removed before the replacement transaction commits.
    repaired = photo_derivatives.render(source, content_hash)
    with session_scope() as db:
        db.query(PhotoDerivative).filter(PhotoDerivative.source_key.in_(keys)).delete(synchronize_session=False)
    photo_cache.purge(keys)
    assert all(photo_derivatives.derivative_path(key).is_file() for key in keys)
    photo_cache.release_publication({key for key, _size in repaired.values()})
    assert all(not photo_derivatives.derivative_path(key).exists() for key in keys)


def test_failed_catalog_publication_releases_and_reclaims_all_renditions(
    tmp_path, monkeypatch,
) -> None:  # noqa: ANN001
    source = tmp_path / "photo.jpg"
    source.write_bytes(jpeg())
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 0)
    monkeypatch.setattr(photo_cache.settings, "photo_preview_min_free_bytes", 0)
    monkeypatch.setattr(
        photo_derivatives, "_run", lambda _mode, _source, target: _prepared_payload(target),
    )
    _facts, products = photo_derivatives.prepare(source, "image/jpeg", content_hash)
    keys = {key for key, _size in products.values()}

    photo_cache.release_publication(keys)

    assert all(not photo_derivatives.derivative_path(key).exists() for key in keys)
    with session_scope() as db:
        assert all(db.get(PhotoPreviewAsset, key) is None for key in keys)
        storage = db.get(PhotoPreviewStorage, 1)
        assert storage.used_bytes == 0 and storage.reserved_bytes == 0


def test_preview_purge_rechecks_shared_catalog_references() -> None:
    key = "e" * 64
    path = photo_derivatives.derivative_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    output = io.BytesIO()
    Image.new("RGB", (2, 1), "red").save(output, format="WEBP")
    path.write_bytes(output.getvalue())
    with session_scope() as db:
        db.add_all([
            PhotoPreviewStorage(id=1, used_bytes=len(output.getvalue()), reserved_bytes=0),
            PhotoPreviewAsset(
                source_key=key, content_hash="c" * 64, width=400, state="ready",
                bytes=len(output.getvalue()), reserved_bytes=0, updated_at=utcnow() - timedelta(hours=1),
            ),
            PhotoDerivative(id="shared-a", photo_id="a", width=400, source_key=key, state="ready"),
            PhotoDerivative(id="shared-b", photo_id="b", width=400, source_key=key, state="ready"),
        ])
    photo_cache.purge({key})
    assert path.is_file()
    with session_scope() as db:
        db.query(PhotoDerivative).filter(PhotoDerivative.source_key == key).delete(synchronize_session=False)
    photo_cache.purge({key})
    assert not path.exists()


def test_preview_recovery_cleans_unreferenced_crash_publication_without_tree_scan() -> None:
    key = "d" * 64
    path = photo_derivatives.derivative_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    output = io.BytesIO()
    Image.new("RGB", (2, 1), "red").save(output, format="WEBP")
    path.write_bytes(output.getvalue())
    with session_scope() as db:
        db.add_all([
            PhotoPreviewStorage(
                id=1, used_bytes=0, reserved_bytes=photo_derivatives.MAX_OUTPUT_BYTES,
            ),
            PhotoPreviewAsset(
                source_key=key, content_hash="d" * 64, width=400, state="reserved", bytes=None,
                reserved_bytes=photo_derivatives.MAX_OUTPUT_BYTES, reservation_id="crashed",
            ),
        ])
    photo_cache.recover()
    assert not path.exists()
    with session_scope() as db:
        assert db.get(PhotoPreviewAsset, key) is None
        assert db.get(PhotoPreviewStorage, 1).reserved_bytes == 0


def test_zero_photo_cache_limit_means_unlimited_not_unservable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 0)
    path = photo_derivatives.derivative_path("f" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"servable derivative")
    photo_cache.trim(force=True)
    assert path.read_bytes() == b"servable derivative"


def test_photo_cache_configuration_rejects_an_unservable_nonzero_cap() -> None:
    assert EnvSettings(_env_file=None, photo_derivative_cache_max_bytes=0).photo_derivative_cache_max_bytes == 0
    with pytest.raises(ValidationError, match="at least 32 MiB"):
        EnvSettings(_env_file=None, photo_derivative_cache_max_bytes=1024)
