"""Bounded, disposable static cover renditions for managed books."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path

from app.config import settings
from app.persistence import atomic_write, read_regular_file
from app.services import raster_images, renditions, title_images
from app.services.book_archives import read_resource_uncached


COVER_SIDE = 600
MAX_COVER_BYTES = 1024 * 1024
CACHE_CAP_BYTES = 256 * 1024 * 1024
CACHE_CAP_FILES = 2048
MAX_PENDING = 32
REQUEST_WAIT_SECONDS = 0.05
ENCODE_TIMEOUT_SECONDS = 10.0
FAILED_RETRY_SECONDS = 30.0
_CACHE_VERSION = "v1-jpeg-600-q4"
_DEMUXERS = {**renditions.DEMUXERS, "image/gif": "gif"}


class BookCoverPreparing(Exception):
    """The bounded background cover job is pending or temporarily unavailable."""


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="book-cover")
_inflight: dict[str, Future[bytes]] = {}
_failed: OrderedDict[str, float] = OrderedDict()
_lock = threading.Lock()


def _identity(path: Path, format_: str, member: str) -> tuple[str, tuple[int, int, int, int, int]]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise FileNotFoundError("Book cover is unavailable") from exc
    raw = "\0".join((
        _CACHE_VERSION, format_, str(stat.st_dev), str(stat.st_ino), str(stat.st_size),
        str(stat.st_mtime_ns), str(stat.st_ctime_ns), member,
    )).encode()
    return hashlib.sha256(raw).hexdigest(), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _cache_root() -> Path:
    return settings.data_dir / "book-cache" / "covers"


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError("Book cover storage is not a private directory")
    return path


def _cache_path(key: str) -> Path:
    return _cache_root() / key[:2] / f"{key}.jpg"


def _read_cached(key: str) -> bytes | None:
    path = _cache_path(key)
    try:
        data = read_regular_file(path, MAX_COVER_BYTES)
        if time.time() - path.stat().st_mtime > 24 * 60 * 60:
            os.utime(path)
        return data
    except OSError:
        return None


def _encode_cover(data: bytes, mime: str, ffmpeg: str | None) -> bytes:
    if ffmpeg is None or mime not in _DEMUXERS:
        raise BookCoverPreparing
    if mime == "image/png":
        try:
            data = raster_images.strip_png(data)
        except ValueError as exc:
            raise BookCoverPreparing from exc
    elif mime == "image/webp":
        data = _first_webp_frame(data)
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-filter_threads", "1",
        "-protocol_whitelist", "pipe", "-f", _DEMUXERS[mime], "-max_pixels", "40000000",
        "-threads", "1", "-i", "pipe:0", "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", f"scale='min({COVER_SIDE},iw)':'min({COVER_SIDE},ih)':force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-an", "-sn", "-dn", "-threads", "1", "-c:v", "mjpeg", "-q:v", "4",
        "-f", "image2pipe", "pipe:1",
    ]
    try:
        output = title_images._run(argv, data, ENCODE_TIMEOUT_SECONDS)  # noqa: SLF001 - shared bounded process seam
    except title_images.UploadError as exc:
        raise BookCoverPreparing from exc
    size = renditions.image_size(output)
    if title_images.sniff(output) != "image/jpeg" or size is None or not all(size) or max(size) > COVER_SIDE or len(output) > MAX_COVER_BYTES:
        raise BookCoverPreparing
    return output


def _first_webp_frame(data: bytes) -> bytes:
    """Turn an animated WebP's first ANMF payload into a static WebP."""
    if len(data) < 30 or data[12:16] != b"VP8X" or not data[20] & 0x02:
        return data
    end = int.from_bytes(data[4:8], "little") + 8
    at = 12
    while at + 8 <= end:
        kind = data[at:at + 4]
        length = int.from_bytes(data[at + 4:at + 8], "little")
        chunk_end = at + 8 + length
        if chunk_end > end:
            break
        if kind == b"ANMF" and length >= 16:
            payload = data[at + 24:chunk_end]
            return b"RIFF" + (len(payload) + 4).to_bytes(4, "little") + b"WEBP" + payload
        at = chunk_end + (length & 1)
    raise BookCoverPreparing


def _same_identity(path: Path, expected: tuple[int, int, int, int, int]) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) == expected


def _generate(path: Path, format_: str, member: str, ffmpeg: str | None, key: str,
              identity: tuple[int, int, int, int, int]) -> bytes:
    # This executor has one worker. It enters the shared native parser pool only
    # after claiming that lane, so a cold cover grid leaves one parser for pages.
    data, mime = read_resource_uncached(path, member)
    output = _encode_cover(data, mime, ffmpeg)
    if not _same_identity(path, identity):
        raise BookCoverPreparing
    target = _cache_path(key)
    _private_dir(settings.data_dir / "book-cache")
    _private_dir(_cache_root())
    _private_dir(target.parent)
    atomic_write(target, output)
    _sweep()
    return output


def _finished(key: str, future: Future[bytes]) -> None:
    with _lock:
        _inflight.pop(key, None)
        if future.exception() is not None:
            _failed[key] = time.monotonic() + FAILED_RETRY_SECONDS
            _failed.move_to_end(key)
            while len(_failed) > MAX_PENDING:
                _failed.popitem(last=False)


def _schedule(path: Path, format_: str, member: str, ffmpeg: str | None, key: str,
              identity: tuple[int, int, int, int, int]) -> Future[bytes] | None:
    now = time.monotonic()
    with _lock:
        if future := _inflight.get(key):
            return future
        retry_at = _failed.get(key)
        if retry_at is not None:
            if retry_at > now:
                return None
            _failed.pop(key, None)
        if len(_inflight) >= MAX_PENDING:
            return None
        future = _executor.submit(_generate, path, format_, member, ffmpeg, key, identity)
        _inflight[key] = future
    # A very fast job may already be done; Future then invokes the callback
    # synchronously, so never register it while holding the non-reentrant lock.
    future.add_done_callback(lambda done: _finished(key, done))
    return future


def read_cover(path: Path, format_: str, member: str, ffmpeg: str | None) -> tuple[bytes, str]:
    """Return a static JPEG rendition, or raise while its bounded background job runs.

    The cache key contains only an exact file identity, format and archive member,
    then is hashed before it becomes a filename. No original path or member name is
    written below app data.
    """
    if format_ not in {"epub", "pdf", "cbz", "cbr"} or not isinstance(member, str) or not member or len(member) > 1024:
        raise FileNotFoundError("Book cover is unavailable")
    key, identity = _identity(path, format_, member)
    if cached := _read_cached(key):
        return cached, "image/jpeg"
    future = _schedule(path, format_, member, ffmpeg, key, identity)
    if future is None:
        raise BookCoverPreparing
    done, _ = wait([future], timeout=REQUEST_WAIT_SECONDS)
    if not done:
        raise BookCoverPreparing
    try:
        return future.result(), "image/jpeg"
    except Exception:
        raise BookCoverPreparing from None


def _cover_files() -> list[tuple[Path, int, float]]:
    root = _cache_root()
    found: list[tuple[Path, int, float]] = []
    try:
        shards = list(os.scandir(root))
    except OSError:
        return found
    for shard in shards:
        if shard.is_symlink() or not shard.is_dir(follow_symlinks=False):
            continue
        try:
            entries = os.scandir(shard.path)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if len(entry.name) != 68 or not entry.name.endswith(".jpg") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                found.append((Path(entry.path), stat.st_size, stat.st_mtime))
    return found


def _sweep() -> None:
    files = sorted(_cover_files(), key=lambda item: (item[2], str(item[0])))
    total = sum(item[1] for item in files)
    while files and (len(files) > CACHE_CAP_FILES or total > CACHE_CAP_BYTES):
        path, size, _mtime = files.pop(0)
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
