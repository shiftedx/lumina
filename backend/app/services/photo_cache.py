"""Durable, quota-admitted storage for content-addressed photo previews."""
from __future__ import annotations

import os
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from sqlalchemy import func, select

from app.config import settings
from app.db import session_scope
from app.models import utcnow
from app.persistence import write_transaction
from app.photo_models import PhotoDerivative
from app.photo_preview_models import PhotoPreviewAsset, PhotoPreviewStorage
from app.services import photo_derivatives

RECLAIM_BATCH = 256
_lock = threading.RLock()


class PhotoStorageFull(photo_derivatives.PhotoDecodeError):
    code = "preview_storage_full"
    retry_after = 60

    def __init__(self, reason: str) -> None:
        super().__init__(self.code)
        self.reason = reason


class PhotoStorageBusy(photo_derivatives.PhotoDecodeError):
    code = "preview_storage_busy"
    retry_after = 2


@dataclass
class PreviewReservation:
    id: str
    content_hash: str
    keys: dict[int, str]
    prior_bytes: dict[int, int | None]
    existing: dict[int, int]
    reserved_bytes: int
    claimed_existing: set[int] = field(default_factory=set)
    installed: list[Path] = field(default_factory=list)
    complete: bool = False


def _storage(db) -> PhotoPreviewStorage:  # noqa: ANN001
    row = db.get(PhotoPreviewStorage, 1)
    if row is None:
        used = int(db.scalar(select(func.coalesce(func.sum(PhotoPreviewAsset.bytes), 0)).where(
            PhotoPreviewAsset.state == "ready",
        )) or 0)
        row = PhotoPreviewStorage(id=1, used_bytes=used, reserved_bytes=0)
        db.add(row)
        db.flush()
    return row


def _preview_size(path: Path) -> int | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            header = source.read(16)
    except OSError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
        or info.st_size > photo_derivatives.MAX_OUTPUT_BYTES
        or photo_derivatives.sniff(header) != "image/webp"
    ):
        return None
    return info.st_size


def _capacity(storage: PhotoPreviewStorage, *, new_bytes: int, staging_bytes: int) -> None:
    quota = max(0, settings.photo_derivative_cache_max_bytes)
    if quota and new_bytes > 0 and storage.used_bytes + storage.reserved_bytes + new_bytes > quota:
        raise PhotoStorageFull("quota")
    root = photo_derivatives.photos_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise PhotoStorageFull("free_space") from exc
    if free < max(0, settings.photo_preview_min_free_bytes) + storage.reserved_bytes + staging_bytes:
        raise PhotoStorageFull("free_space")


def _reserve(content_hash: str) -> PreviewReservation:
    reservation_id = str(uuid.uuid4())
    keys = {width: photo_derivatives.source_key(content_hash, width) for width in photo_derivatives.WIDTHS}
    prior: dict[int, int | None] = {}
    existing: dict[int, int] = {}
    claimed_existing: set[int] = set()
    maximum = len(keys) * photo_derivatives.MAX_OUTPUT_BYTES
    with _lock, session_scope() as db, write_transaction(db, name="photo_preview_reserve"):
        storage = _storage(db)
        rows = {
            row.source_key: row for row in db.scalars(select(PhotoPreviewAsset).where(
                PhotoPreviewAsset.source_key.in_(keys.values()),
            ))
        }
        missing: list[tuple[int, str, PhotoPreviewAsset | None]] = []
        new_charge = 0
        for width, key in keys.items():
            row = rows.get(key)
            if row is not None and (row.state == "reserved" or row.reservation_id is not None):
                raise PhotoStorageBusy("preview_storage_busy")
            size = _preview_size(photo_derivatives.derivative_path(key))
            if size is not None:
                existing[width] = size
                if row is None:
                    row = PhotoPreviewAsset(
                        source_key=key, content_hash=content_hash, width=width, state="ready",
                        bytes=size, reserved_bytes=0,
                    )
                    db.add(row)
                    storage.used_bytes += size
                elif row.state == "ready" and row.bytes != size:
                    storage.used_bytes = max(0, storage.used_bytes + size - max(0, row.bytes or 0))
                    row.bytes = size
                # Pin even an already-cataloged asset: its last reference can
                # disappear while this publication is still preparing.
                row.reservation_id = reservation_id
                row.updated_at = utcnow()
                claimed_existing.add(width)
                continue
            missing.append((width, key, row))
            if row is None or row.state != "ready":
                new_charge += photo_derivatives.MAX_OUTPUT_BYTES
        _capacity(storage, new_bytes=new_charge, staging_bytes=maximum)
        storage.reserved_bytes += maximum
        for width, key, row in missing:
            if row is None:
                prior[width] = None
                db.add(PhotoPreviewAsset(
                    source_key=key, content_hash=content_hash, width=width, state="reserved",
                    bytes=None, reserved_bytes=photo_derivatives.MAX_OUTPUT_BYTES,
                    reservation_id=reservation_id,
                ))
            else:
                prior[width] = row.bytes
                storage.used_bytes = max(0, storage.used_bytes - max(0, row.bytes or 0))
                row.state, row.reserved_bytes, row.reservation_id = (
                    "reserved", photo_derivatives.MAX_OUTPUT_BYTES, reservation_id,
                )
                row.updated_at = utcnow()
    return PreviewReservation(
        reservation_id, content_hash, keys, prior, existing, maximum,
        claimed_existing=claimed_existing,
    )


@contextmanager
def reserve_set(content_hash: str) -> Iterator[PreviewReservation]:
    """Reserve quota and free-space headroom before starting a decoder child."""
    reservation = _reserve(content_hash)
    try:
        yield reservation
    finally:
        if not reservation.complete:
            abandon(reservation)


def publish_set(
    reservation: PreviewReservation,
    products: dict[int, tuple[Path, str, int]],
) -> dict[int, tuple[str, int]]:
    """Install a fully validated set, then make its registry rows ready together."""
    installed: dict[int, tuple[str, int]] = {}
    with _lock:
        for width in photo_derivatives.WIDTHS:
            staged, key, size = products[width]
            if key != reservation.keys[width]:
                raise photo_derivatives.PhotoDecodeError("Photo derivatives were incomplete.")
            if width in reservation.existing:
                staged.unlink(missing_ok=True)
                installed[width] = (key, reservation.existing[width])
                continue
            target = photo_derivatives.derivative_path(key)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(staged, target)
            reservation.installed.append(target)
            directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            installed[width] = (key, size)
        with session_scope() as db, write_transaction(db, name="photo_preview_publish"):
            storage = _storage(db)
            for width in reservation.prior_bytes:
                key, size = installed[width]
                row = db.get(PhotoPreviewAsset, key)
                if row is None or row.state != "reserved" or row.reservation_id != reservation.id:
                    raise PhotoStorageBusy("preview_storage_busy")
                row.state, row.bytes = "ready", size
                # Keep the token after publication. It is a durable pin until
                # the caller's catalog transaction commits or is abandoned.
                row.reserved_bytes, row.updated_at = 0, utcnow()
                storage.used_bytes += size
            storage.reserved_bytes = max(0, storage.reserved_bytes - reservation.reserved_bytes)
        reservation.complete = True
        return installed


def abandon(reservation: PreviewReservation) -> None:
    with _lock:
        for path in reservation.installed:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        with session_scope() as db, write_transaction(db, name="photo_preview_abandon"):
            storage = _storage(db)
            for width, prior in reservation.prior_bytes.items():
                key = reservation.keys[width]
                row = db.get(PhotoPreviewAsset, key)
                if row is None or row.reservation_id != reservation.id:
                    continue
                if prior is None:
                    db.delete(row)
                else:
                    row.state, row.bytes = "ready", prior
                    row.reserved_bytes, row.reservation_id, row.updated_at = 0, None, utcnow()
                    storage.used_bytes += prior
            for width in reservation.claimed_existing:
                row = db.get(PhotoPreviewAsset, reservation.keys[width])
                if row is not None and row.reservation_id == reservation.id:
                    row.reservation_id = None
            storage.reserved_bytes = max(0, storage.reserved_bytes - reservation.reserved_bytes)


def release_publication(keys: set[str]) -> None:
    """Release ready-file pins after the catalog transaction resolves.

    Cataloged files become ordinary durable assets. Files from a failed or
    canceled publication are reclaimed while the reference check and registry
    update hold the serialized writer lock.
    """
    if not keys:
        return
    with _lock, session_scope() as db, write_transaction(db, name="photo_preview_release"):
        for key in keys:
            row = db.get(PhotoPreviewAsset, key)
            if row is None or row.state != "ready" or row.reservation_id is None:
                continue
            referenced = db.scalar(select(PhotoDerivative.id).where(
                PhotoDerivative.source_key == key,
            ).limit(1)) is not None
            row.reservation_id = None
            if not referenced:
                _reclaim(db, {key})


def recover() -> None:
    """Resolve crash reservations and transient files without walking preview storage."""
    root = photo_derivatives.photos_root()
    for name in (".staging", ".snapshots"):
        shutil.rmtree(root / name, ignore_errors=True)
    if root.exists():
        for candidate in root.glob(".upload-*.tmp"):
            try:
                if stat.S_ISREG(candidate.lstat().st_mode):
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue
    with _lock, session_scope() as db, write_transaction(db, name="photo_preview_recover"):
        _storage(db)
        rows = list(db.scalars(select(PhotoPreviewAsset).where(
            (PhotoPreviewAsset.state == "reserved") | (PhotoPreviewAsset.reservation_id.is_not(None)),
        )))
        for row in rows:
            referenced = db.scalar(select(PhotoDerivative.id).where(
                PhotoDerivative.source_key == row.source_key,
            ).limit(1)) is not None
            path = photo_derivatives.derivative_path(row.source_key)
            size = _preview_size(path)
            if row.state == "ready":
                row.reservation_id = None
                if not referenced:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
                    storage = _storage(db)
                    storage.used_bytes = max(0, storage.used_bytes - max(0, row.bytes or 0))
                    db.delete(row)
            elif referenced:
                row.state, row.bytes = "ready", size if size is not None else row.bytes
                row.reserved_bytes, row.reservation_id, row.updated_at = 0, None, utcnow()
                storage = _storage(db)
                storage.used_bytes += max(0, row.bytes or 0)
            else:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                db.delete(row)
        db.flush()
        storage = _storage(db)
        storage.reserved_bytes = 0


def touch(_key: str) -> None:
    """Compatibility no-op: durable previews are not ordered by recent use."""


def _reclaim(db, keys: set[str]) -> int:  # noqa: ANN001
    removed = 0
    for key in keys:
        row = db.get(PhotoPreviewAsset, key)
        if row is not None and (row.state != "ready" or row.reservation_id is not None):
            continue
        if db.scalar(select(PhotoDerivative.id).where(PhotoDerivative.source_key == key).limit(1)) is not None:
            continue
        try:
            photo_derivatives.derivative_path(key).unlink(missing_ok=True)
        except OSError:
            continue
        if row is not None:
            storage = _storage(db)
            storage.used_bytes = max(0, storage.used_bytes - max(0, row.bytes or 0))
            db.delete(row)
        removed += 1
    return removed


def purge(keys: set[str]) -> None:
    """Reclaim only keys still unreferenced under the serialized writer lock."""
    if not keys:
        return
    with _lock, session_scope() as db, write_transaction(db, name="photo_preview_purge"):
        _reclaim(db, keys)


def trim(*, force: bool = False) -> None:  # noqa: ARG001
    """Check one indexed keyset page for orphans; never scan all references."""
    with _lock, session_scope() as db, write_transaction(db, name="photo_preview_reclaim"):
        storage = _storage(db)
        page = select(PhotoPreviewAsset.source_key).where(
            PhotoPreviewAsset.state == "ready", PhotoPreviewAsset.reservation_id.is_(None),
        )
        if storage.sweep_after is not None:
            page = page.where(PhotoPreviewAsset.source_key > storage.sweep_after)
        candidates = list(db.scalars(page.order_by(PhotoPreviewAsset.source_key).limit(RECLAIM_BATCH)))
        if not candidates:
            storage.sweep_after = None
            return
        storage.sweep_after = candidates[-1]
        _reclaim(db, set(candidates))
