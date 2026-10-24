"""Descriptor-safe reads and immutable snapshots for managed and NAS photos."""
from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from sqlalchemy.orm import Session

from app.models import StorageRoot
from app.photo_models import PhotoItem
from app.services import photo_derivatives
from app.services.storage_roots import StorageRootService

MAX_ORIGINAL_BYTES = 128 * 1024 * 1024
MAX_ACTIVE_SNAPSHOTS = 4
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)
_SNAPSHOT_ADMISSION = threading.BoundedSemaphore(MAX_ACTIVE_SNAPSHOTS)


class PhotoFileUnavailable(OSError):
    pass


class PhotoFileChanged(PhotoFileUnavailable):
    pass


class PhotoSnapshotBusy(PhotoFileUnavailable):
    pass


@dataclass(frozen=True)
class PhotoFingerprint:
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class PhotoSnapshot:
    path: Path
    fingerprint: PhotoFingerprint


def _components(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise PhotoFileUnavailable("Photo is unavailable.")
    return parts


def _open_root(root_path: str | Path) -> int:
    root = Path(root_path)
    if not root.is_absolute() or root != Path(os.path.normpath(str(root))):
        raise PhotoFileUnavailable("Photo storage root is unavailable.")
    # Reject configured symlinks as well as symlinks below the root. Walking
    # from an already-open `/` descriptor prevents parent replacement races.
    if Path(os.path.realpath(root)) != root:
        raise PhotoFileUnavailable("Photo storage root is unavailable.")
    try:
        descriptor = os.open("/", _DIRECTORY_FLAGS)
    except OSError as exc:
        raise PhotoFileUnavailable("Photo storage root is unavailable.") from exc
    try:
        for component in root.parts[1:]:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise PhotoFileUnavailable("Photo storage root is unavailable.")
        return descriptor
    except (OSError, ValueError) as exc:
        os.close(descriptor)
        raise PhotoFileUnavailable("Photo storage root is unavailable.") from exc
    except BaseException:
        os.close(descriptor)
        raise


def open_external(root_path: str | Path, relative_path: str, *, max_bytes: int = MAX_ORIGINAL_BYTES) -> int:
    """Open one regular file beneath an absolute root without following any symlink."""
    parts = _components(relative_path)
    descriptor = _open_root(root_path)
    try:
        for component in parts[:-1]:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], _READ_FLAGS, dir_fd=descriptor)
    except (OSError, ValueError) as exc:
        raise PhotoFileUnavailable("Photo is unavailable.") from exc
    finally:
        os.close(descriptor)
    try:
        info = os.fstat(file_descriptor)
    except OSError as exc:
        os.close(file_descriptor)
        raise PhotoFileUnavailable("Photo is unavailable.") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > max_bytes:
        os.close(file_descriptor)
        raise PhotoFileUnavailable("Photo is unavailable.")
    return file_descriptor


@contextmanager
def snapshot_external(
    root_path: str | Path,
    relative_path: str,
    temp_root: str | Path,
    *,
    expected_sha256: str | None = None,
    max_bytes: int = MAX_ORIGINAL_BYTES,
) -> Iterator[PhotoSnapshot]:
    """Copy exact bytes from a securely opened source into a private immutable work file."""
    if expected_sha256 is not None and (
        len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise PhotoFileChanged("Photo content changed.")
    if not _SNAPSHOT_ADMISSION.acquire(blocking=False):
        raise PhotoSnapshotBusy("Photo snapshot capacity is busy.")
    target: Path | None = None
    try:
        source = open_external(root_path, relative_path, max_bytes=max_bytes)
        try:
            temporary_root = Path(temp_root)
            temporary_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            target_fd, target_name = tempfile.mkstemp(prefix=".photo-snapshot-", suffix=".bin", dir=temporary_root)
        except BaseException:
            os.close(source)
            raise
        target = Path(target_name)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(source, "rb", closefd=True) as reader, os.fdopen(target_fd, "wb", closefd=True) as writer:
                before = os.fstat(reader.fileno())
                while block := reader.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise PhotoFileUnavailable("Photo is unavailable.")
                    digest.update(block)
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
                observed = os.fstat(reader.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns,
            ):
                raise PhotoFileChanged("Photo content changed.")
            actual = digest.hexdigest()
            if size <= 0 or expected_sha256 is not None and not hmac.compare_digest(actual, expected_sha256):
                raise PhotoFileChanged("Photo content changed.")
            yield PhotoSnapshot(target, PhotoFingerprint(actual, size, observed.st_mtime_ns))
        finally:
            target.unlink(missing_ok=True)
    finally:
        _SNAPSHOT_ADMISSION.release()


def photo_location(db: Session, photo: PhotoItem) -> tuple[Path, str]:
    if photo.storage_kind == "managed":
        key = PurePosixPath(photo.storage_key)
        if len(key.parts) != 3 or key.parts[0] != "originals":
            raise PhotoFileUnavailable("Photo is unavailable.")
        return photo_derivatives.photos_root(), photo.storage_key
    if photo.storage_kind == "external" and photo.root_id and photo.relative_path:
        root = db.get(StorageRoot, photo.root_id)
        if root is None or root.mode != "external" or not StorageRootService(db).is_online(root):
            raise PhotoFileUnavailable("Photo is unavailable.")
        return Path(root.path), photo.relative_path
    raise PhotoFileUnavailable("Photo is unavailable.")


def build_derivatives(
    root: str | Path, relative_path: str, expected_sha256: str,
) -> tuple[dict[int, tuple[str, int]], PhotoFingerprint]:
    """Render all cache products from one hash-bound immutable snapshot."""
    with snapshot_external(
        root, relative_path, photo_derivatives.photos_root() / ".snapshots",
        expected_sha256=expected_sha256, max_bytes=MAX_ORIGINAL_BYTES,
    ) as snapshot:
        return photo_derivatives.render(snapshot.path, snapshot.fingerprint.sha256), snapshot.fingerprint


@contextmanager
def snapshot_photo(
    db: Session,
    photo: PhotoItem,
    *,
    expected_sha256: bool = True,
    temp_root: str | Path | None = None,
) -> Iterator[PhotoSnapshot]:
    root, relative = photo_location(db, photo)
    work = Path(temp_root) if temp_root is not None else photo_derivatives.photos_root() / ".snapshots"
    with snapshot_external(
        root, relative, work,
        expected_sha256=photo.sha256 if expected_sha256 else None,
        max_bytes=MAX_ORIGINAL_BYTES,
    ) as snapshot:
        yield snapshot
