from __future__ import annotations

import hashlib
import io
import os
import threading

import pytest
from PIL import Image
from pydantic import ValidationError

from app.config import EnvSettings
from app.models import StorageRoot
from app.photo_models import PhotoItem
from app.services import photo_cache, photo_derivatives, photo_files
from app.services.storage_roots import StorageRootService
from support import make_user


OWNER = make_user("safe-photo-owner")


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


def test_photo_cache_evicts_lru_derivatives_but_never_originals(monkeypatch) -> None:  # noqa: ANN001
    root = photo_derivatives.photos_root()
    original = root / "originals" / "aa" / "keep.jpg"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"original")
    paths = []
    for index in range(3):
        key = f"{index + 1:064x}"
        path = photo_derivatives.derivative_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"12345")
        os.utime(path, ns=(index + 1, index + 1))
        paths.append(path)
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 10)
    photo_cache.trim(force=True)
    assert not paths[0].exists()
    assert paths[2].exists()
    assert original.read_bytes() == b"original"


def test_photo_cache_headroom_avoids_a_full_walk_after_every_publication(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 100)
    for index in range(11):
        path = photo_derivatives.derivative_path(f"{index + 100:064x}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        os.utime(path, ns=(index + 1, index + 1))
    photo_cache.trim(force=True)
    original = photo_cache._regular_files  # noqa: SLF001 - observe sweep frequency, not implementation output
    calls = 0
    def counted(root):  # noqa: ANN001,ANN202
        nonlocal calls
        calls += 1
        yield from original(root)
    monkeypatch.setattr(photo_cache, "_regular_files", counted)
    for _ in range(4):
        photo_cache.published(5)
    assert calls == 1


def test_zero_photo_cache_limit_means_unlimited_not_unservable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(photo_cache.settings, "photo_derivative_cache_max_bytes", 0)
    path = photo_derivatives.derivative_path("f" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"servable derivative")
    photo_cache.trim(force=True)
    photo_cache.published(path.stat().st_size)
    assert path.read_bytes() == b"servable derivative"


def test_photo_cache_configuration_rejects_an_unservable_nonzero_cap() -> None:
    assert EnvSettings(_env_file=None, photo_derivative_cache_max_bytes=0).photo_derivative_cache_max_bytes == 0
    with pytest.raises(ValidationError, match="at least 32 MiB"):
        EnvSettings(_env_file=None, photo_derivative_cache_max_bytes=1024)
