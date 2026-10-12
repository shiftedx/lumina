from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time
from typing import Any

from app.persistence import atomic_write, read_regular_file


GIB = 1024 * 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
# Starlette advances synchronous iterators and async file reads in worker threads.
# Media-sized chunks reduce those handoffs while bounding one read per stream.
MEDIA_FILE_CHUNK_SIZE = 1024 * 1024
CACHE_HEADER_NAMES = frozenset({
    "accept-ranges",
    "cache-control",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
    "x-content-type-options",
})


@dataclass(frozen=True)
class StreamCachePolicy:
    """The bounded, owner-specific retention policy for replayable media bytes."""

    recent_video_limit: int = 5
    max_bytes: int = 2 * GIB

    def __post_init__(self) -> None:
        if self.recent_video_limit < 0 or self.max_bytes < 0:
            raise ValueError("Stream cache limits cannot be negative.")


@dataclass(frozen=True)
class StreamCacheKey:
    """A private cache identity that deliberately excludes expiring upstream URLs."""

    owner_user_id: str
    source_identity: str
    track_fingerprint: str
    range_start: int
    range_end: int

    def __post_init__(self) -> None:
        if self.range_start < 0 or self.range_end < self.range_start:
            raise ValueError("A stream cache key requires one exact non-negative byte range.")


@dataclass(frozen=True)
class _EntryMetadata:
    version: int
    entry_digest: str
    owner_digest: str
    video_digest: str
    byte_length: int
    status_code: int
    headers: dict[str, str]
    created_at: float
    recent_video_limit: int
    owner_max_bytes: int


class CachedStreamRange:
    def __init__(
        self,
        *,
        status_code: int,
        headers: Mapping[str, str],
        body_path: Path,
        byte_length: int,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers)
        self._body_path = body_path
        self._byte_length = byte_length

    def open(self) -> tuple[Iterable[bytes], Callable[[], None]]:
        body = _FileBody(self._body_path, self._byte_length)
        return body, body.close


class _FileBody:
    def __init__(self, path: Path, expected_length: int, chunk_size: int = MEDIA_FILE_CHUNK_SIZE) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != expected_length:
                raise OSError("Cached stream body is not the validated regular file.")
            self._file = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        self._chunk_size = chunk_size
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        try:
            while chunk := self._file.read(self._chunk_size):
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._file.close()


class _WriteThroughBody:
    """Tee a response to a private temp file without making cache health user-visible."""

    def __init__(
        self,
        *,
        source: Iterable[bytes],
        close_source: Callable[[], None],
        writer: _RangeWriter | None,
    ) -> None:
        self._source = source
        self._close_source = close_source
        self._writer = writer
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        completed = False
        try:
            for chunk in self._source:
                if self._writer is not None:
                    try:
                        self._writer.write(chunk)
                    except Exception:
                        self._writer.abort()
                        self._writer = None
                yield chunk
            completed = True
        finally:
            if completed and self._writer is not None:
                try:
                    self._writer.commit()
                except Exception:
                    self._writer.abort()
                self._writer = None
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._writer is not None:
            self._writer.abort()
            self._writer = None
        self._close_source()


class _RangeWriter:
    def __init__(
        self,
        *,
        cache: PersistentStreamRangeCache,
        key: StreamCacheKey,
        status_code: int,
        headers: Mapping[str, str],
        expected_length: int,
        temp_path: Path,
        temp_file: Any,
    ) -> None:
        self._cache = cache
        self._key = key
        self._status_code = status_code
        self._headers = dict(headers)
        self._expected_length = expected_length
        self._temp_path = temp_path
        self._file = temp_file
        self._length = 0
        self._finished = False

    def write(self, chunk: bytes) -> None:
        if self._finished:
            return
        if self._length + len(chunk) > self._expected_length:
            raise ValueError("The upstream response exceeded its validated cache length.")
        self._file.write(chunk)
        self._length += len(chunk)

    def commit(self) -> None:
        if self._finished:
            return
        if self._length != self._expected_length:
            raise ValueError("The upstream response ended before its validated cache length.")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._cache._commit(
            self._key,
            self._status_code,
            self._headers,
            self._expected_length,
            self._temp_path,
        )
        self._finished = True

    def abort(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._file.close()
        except OSError:
            pass
        try:
            self._temp_path.unlink(missing_ok=True)
        except OSError:
            pass


class PersistentStreamRangeCache:
    """Crash-safe, owner-scoped LRU storage for validated exact media ranges.

    Cache data is an optimization only: every public operation is safe to treat as a
    miss, and the write-through body never turns a disk failure into playback failure.
    """

    def __init__(
        self,
        root: Path,
        *,
        policy_provider: Callable[[str], StreamCachePolicy] | None = None,
        max_bytes_global: int = 20 * GIB,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        clock: Callable[[], float] = time,
    ) -> None:
        if max_bytes_global < 0 or max_age_seconds <= 0:
            raise ValueError("The global stream cache limit cannot be negative.")
        self._root = root
        self._entries_root = root / "entries"
        self._temp_root = root / ".tmp"
        self._policy_provider = policy_provider or (lambda _owner_id: StreamCachePolicy())
        self._max_bytes_global = max_bytes_global
        self._max_age_seconds = max_age_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._available = True
        try:
            self._prepare_private_directories()
        except OSError:
            self._available = False

    def lookup(self, key: StreamCacheKey) -> CachedStreamRange | None:
        if not self._available:
            return None
        try:
            policy = self._policy(key.owner_user_id)
            if policy.recent_video_limit <= 0 or policy.max_bytes <= 0 or self._max_bytes_global <= 0:
                return None
            digest = _entry_digest(key)
            metadata_path, body_path = self._paths(digest)
            with self._lock:
                metadata = self._read_metadata(metadata_path)
                if (
                    metadata is None
                    or metadata.entry_digest != digest
                    or metadata.owner_digest != _owner_digest(key.owner_user_id)
                    or metadata.video_digest != _video_digest(key)
                    or metadata.byte_length != key.range_end - key.range_start + 1
                    or metadata.byte_length > policy.max_bytes
                    or self._clock() - metadata.created_at > self._max_age_seconds
                    or not self._valid_cached_response(metadata, key)
                    or not self._is_regular_file(body_path, metadata.byte_length)
                ):
                    self._remove_paths(metadata_path, body_path)
                    return None
                now = self._clock()
                os.utime(metadata_path, (now, now))
                os.utime(body_path, (now, now))
                return CachedStreamRange(
                    status_code=metadata.status_code,
                    headers=metadata.headers,
                    body_path=body_path,
                    byte_length=metadata.byte_length,
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def capture(
        self,
        key: StreamCacheKey,
        *,
        status_code: int,
        headers: Mapping[str, str],
        expected_length: int,
        body: Iterable[bytes],
        close: Callable[[], None],
    ) -> tuple[Iterable[bytes], Callable[[], None]]:
        writer: _RangeWriter | None = None
        raw_path: str | None = None
        fd: int | None = None
        try:
            policy = self._policy(key.owner_user_id)
            if (
                self._available
                and policy.recent_video_limit > 0
                and policy.max_bytes > 0
                and self._max_bytes_global > 0
                and 0 < expected_length <= policy.max_bytes
                and expected_length <= self._max_bytes_global
            ):
                fd, raw_path = tempfile.mkstemp(prefix="range-", suffix=".part", dir=self._temp_root)
                os.chmod(raw_path, 0o600)
                temp_file = os.fdopen(fd, "wb")
                fd = None
                writer = _RangeWriter(
                    cache=self,
                    key=key,
                    status_code=status_code,
                    headers=headers,
                    expected_length=expected_length,
                    temp_path=Path(raw_path),
                    temp_file=temp_file,
                )
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if raw_path is not None:
                try:
                    Path(raw_path).unlink(missing_ok=True)
                except OSError:
                    pass
            writer = None
        wrapped = _WriteThroughBody(source=body, close_source=close, writer=writer)
        return wrapped, wrapped.close

    def prune(self) -> None:
        if not self._available:
            return
        try:
            with self._lock:
                self._prune_locked()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def prune_owner(self, owner_user_id: str) -> None:
        """Apply the owner's current policy immediately after a settings change."""

        if not self._available:
            return
        try:
            owner_digest = _owner_digest(owner_user_id)
            with self._lock:
                self._prune_locked({owner_digest: self._policy(owner_user_id)})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _commit(
        self,
        key: StreamCacheKey,
        status_code: int,
        headers: Mapping[str, str],
        byte_length: int,
        temp_path: Path,
    ) -> None:
        digest = _entry_digest(key)
        metadata_path, body_path = self._paths(digest)
        with self._lock:
            # The policy decision and final replacement share the same lock as
            # prune_owner().  Either an earlier opt-out is observed here, or a
            # later opt-out necessarily prunes after this write completes.
            policy = self._policy(key.owner_user_id)
            if (
                policy.recent_video_limit <= 0
                or policy.max_bytes <= 0
                or byte_length > policy.max_bytes
                or byte_length > self._max_bytes_global
            ):
                temp_path.unlink(missing_ok=True)
                return
            self._prepare_shard_directory(digest)
            metadata = _EntryMetadata(
                version=1,
                entry_digest=digest,
                owner_digest=_owner_digest(key.owner_user_id),
                video_digest=_video_digest(key),
                byte_length=byte_length,
                status_code=status_code,
                headers={
                    str(name): str(value)
                    for name, value in headers.items()
                    if str(name).casefold() in CACHE_HEADER_NAMES
                },
                created_at=self._clock(),
                recent_video_limit=policy.recent_video_limit,
                owner_max_bytes=policy.max_bytes,
            )
            # Never leave old metadata pointing at replacement bytes if the new
            # metadata write fails after the atomic body replacement.
            metadata_path.unlink(missing_ok=True)
            try:
                os.replace(temp_path, body_path)
                os.chmod(body_path, 0o600)
                self._write_metadata(metadata_path, metadata)
            except BaseException:
                body_path.unlink(missing_ok=True)
                raise
            self._prune_locked({metadata.owner_digest: policy})

    def _prune_locked(self, policy_overrides: Mapping[str, StreamCachePolicy] | None = None) -> None:
        policy_overrides = policy_overrides or {}
        entries = self._entries()
        owners = sorted({entry[1].owner_digest for entry in entries})
        for owner_digest in owners:
            owner_entries = [entry for entry in entries if entry[1].owner_digest == owner_digest]
            newest_metadata = max(owner_entries, key=lambda entry: entry[3])[1]
            policy = policy_overrides.get(owner_digest) or StreamCachePolicy(
                recent_video_limit=newest_metadata.recent_video_limit,
                max_bytes=newest_metadata.owner_max_bytes,
            )
            video_access: dict[str, float] = {}
            for metadata_path, metadata, _body_path, accessed_at in owner_entries:
                video_access[metadata.video_digest] = max(
                    accessed_at,
                    video_access.get(metadata.video_digest, 0),
                )
            retained_videos = {
                video
                for video, _access in sorted(video_access.items(), key=lambda item: item[1], reverse=True)[
                    : policy.recent_video_limit
                ]
            }
            for metadata_path, metadata, body_path, _accessed_at in owner_entries:
                if metadata.video_digest not in retained_videos:
                    self._remove_paths(metadata_path, body_path)

            remaining = [entry for entry in self._entries() if entry[1].owner_digest == owner_digest]
            total = sum(entry[1].byte_length for entry in remaining)
            for metadata_path, metadata, body_path, _accessed_at in sorted(
                remaining,
                key=lambda entry: entry[3],
            ):
                if total <= policy.max_bytes:
                    break
                self._remove_paths(metadata_path, body_path)
                total -= metadata.byte_length

        remaining = self._entries()
        total = sum(entry[1].byte_length for entry in remaining)
        for metadata_path, metadata, body_path, _accessed_at in sorted(
            remaining,
            key=lambda entry: entry[3],
        ):
            if total <= self._max_bytes_global:
                break
            self._remove_paths(metadata_path, body_path)
            total -= metadata.byte_length

    def _entries(self) -> list[tuple[Path, _EntryMetadata, Path, float]]:
        entries: list[tuple[Path, _EntryMetadata, Path, float]] = []
        for directory in self._shard_directories():
            for metadata_path in directory.glob("*.json"):
                body_path = metadata_path.with_suffix(".bin")
                metadata = self._read_metadata(metadata_path)
                if (
                    metadata is None
                    or self._clock() - metadata.created_at > self._max_age_seconds
                    or not self._is_regular_file(body_path, metadata.byte_length)
                ):
                    self._remove_paths(metadata_path, body_path)
                    continue
                entries.append((metadata_path, metadata, body_path, metadata_path.stat().st_mtime))
            for body_path in directory.glob("*.bin"):
                if not self._is_regular_file(body_path, None) or not body_path.with_suffix(".json").exists():
                    body_path.unlink(missing_ok=True)
        return entries

    def _policy(self, owner_user_id: str | None) -> StreamCachePolicy:
        if owner_user_id is None:
            return StreamCachePolicy(0, 0)
        try:
            policy = self._policy_provider(owner_user_id)
            return policy if isinstance(policy, StreamCachePolicy) else StreamCachePolicy(0, 0)
        except Exception:
            return StreamCachePolicy(0, 0)

    def _paths(self, digest: str) -> tuple[Path, Path]:
        directory = self._entries_root / digest[:2]
        return directory / f"{digest}.json", directory / f"{digest}.bin"

    def _prepare_shard_directory(self, digest: str) -> Path:
        directory = self._entries_root / digest[:2]
        try:
            file_stat = os.lstat(directory)
        except FileNotFoundError:
            directory.mkdir(mode=0o700)
        else:
            if stat.S_ISLNK(file_stat.st_mode):
                directory.unlink()
                directory.mkdir(mode=0o700)
            elif not stat.S_ISDIR(file_stat.st_mode):
                raise OSError("Stream cache shard is not a directory.")
        file_stat = os.lstat(directory)
        if not stat.S_ISDIR(file_stat.st_mode):
            raise OSError("Stream cache shard is not a private directory.")
        os.chmod(directory, 0o700)
        return directory

    def _shard_directories(self) -> Iterator[Path]:
        try:
            candidates = tuple(self._entries_root.iterdir())
        except OSError:
            return
        for directory in candidates:
            try:
                file_stat = os.lstat(directory)
                if stat.S_ISLNK(file_stat.st_mode):
                    directory.unlink(missing_ok=True)
                    continue
                if not stat.S_ISDIR(file_stat.st_mode):
                    continue
            except OSError:
                continue
            yield directory

    @staticmethod
    def _read_metadata(path: Path) -> _EntryMetadata | None:
        try:
            payload = json.loads(read_regular_file(path, 32 * 1024).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        try:
            metadata = _EntryMetadata(
                version=1,
                entry_digest=str(payload["entry_digest"]),
                owner_digest=str(payload["owner_digest"]),
                video_digest=str(payload["video_digest"]),
                byte_length=int(payload["byte_length"]),
                status_code=int(payload["status_code"]),
                headers={str(key): str(value) for key, value in dict(payload["headers"]).items()},
                created_at=float(payload["created_at"]),
                recent_video_limit=int(payload.get("recent_video_limit", 5)),
                owner_max_bytes=int(payload.get("owner_max_bytes", 2 * GIB)),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if (
            len(metadata.entry_digest) != 64
            or len(metadata.owner_digest) != 64
            or len(metadata.video_digest) != 64
            or any(character not in "0123456789abcdef" for character in metadata.entry_digest + metadata.owner_digest + metadata.video_digest)
            or metadata.byte_length <= 0
            or metadata.status_code not in {200, 206}
            or metadata.created_at < 0
            or metadata.recent_video_limit < 0
            or metadata.owner_max_bytes < 0
            or any(name.casefold() not in CACHE_HEADER_NAMES for name in metadata.headers)
        ):
            return None
        return metadata

    @staticmethod
    def _is_regular_file(path: Path, expected_length: int | None) -> bool:
        try:
            file_stat = os.lstat(path)
            return stat.S_ISREG(file_stat.st_mode) and (
                expected_length is None or file_stat.st_size == expected_length
            )
        except OSError:
            return False

    @staticmethod
    def _valid_cached_response(metadata: _EntryMetadata, key: StreamCacheKey) -> bool:
        headers = {name.casefold(): value for name, value in metadata.headers.items()}
        try:
            if int(headers.get("content-length", "-1")) != metadata.byte_length:
                return False
        except ValueError:
            return False
        if metadata.status_code == 206:
            expected_prefix = f"bytes {key.range_start}-{key.range_end}/"
            return headers.get("content-range", "").casefold().startswith(expected_prefix)
        return key.range_start == 0 and metadata.byte_length == key.range_end + 1

    def _write_metadata(self, path: Path, metadata: _EntryMetadata) -> None:
        atomic_write(path, json.dumps(asdict(metadata), sort_keys=True, separators=(",", ":")))

    @staticmethod
    def _remove_paths(metadata_path: Path, body_path: Path) -> None:
        metadata_path.unlink(missing_ok=True)
        body_path.unlink(missing_ok=True)

    def _prepare_private_directories(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._entries_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        os.chmod(self._entries_root, 0o700)
        os.chmod(self._temp_root, 0o700)
        for orphan in self._temp_root.glob("*.part"):
            orphan.unlink(missing_ok=True)
        for directory in self._shard_directories():
            for body_path in directory.glob("*.bin"):
                if not body_path.with_suffix(".json").exists():
                    body_path.unlink(missing_ok=True)
            for metadata_path in directory.glob("*.json"):
                if not metadata_path.with_suffix(".bin").exists():
                    metadata_path.unlink(missing_ok=True)


def _digest(*parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _owner_digest(owner_user_id: str) -> str:
    return _digest("owner-v1", owner_user_id)


def _video_digest(key: StreamCacheKey) -> str:
    return _digest(
        "video-v1",
        key.owner_user_id,
        key.source_identity,
    )


def _entry_digest(key: StreamCacheKey) -> str:
    return _digest(
        "range-v1",
        key.owner_user_id,
        key.source_identity,
        key.track_fingerprint,
        str(key.range_start),
        str(key.range_end),
    )
