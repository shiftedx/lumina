"""Bounded book-cover generation, reuse and scheduling."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import pytest

from app.config import settings
from app.services import book_covers, renditions


JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03" + b"\x00" * 24


@pytest.fixture(autouse=True)
def fresh_cover_jobs(tmp_path: Path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path / "app-data")
    settings.data_dir.mkdir()
    with book_covers._lock:
        previous = list(book_covers._inflight.values())
    wait(previous)
    with book_covers._lock:
        book_covers._inflight.clear()
        book_covers._failed.clear()
    yield
    with book_covers._lock:
        remaining = list(book_covers._inflight.values())
    wait(remaining)


def source(tmp_path: Path, name: str = "book.cbz") -> Path:
    path = tmp_path / name
    path.write_bytes(b"managed-book")
    return path


def test_exact_identity_disk_cache_avoids_repeat_work_and_ctime_replacement_invalidates(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    book = source(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(book_covers, "REQUEST_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(book_covers, "read_resource_uncached", lambda _path, member: (member.encode(), "image/jpeg"))
    monkeypatch.setattr(book_covers, "_encode_cover", lambda data, _mime, _ffmpeg: calls.append(data.decode()) or JPEG + bytes([len(calls)]))

    first = book_covers.read_cover(book, "cbz", "cover.jpg", "ffmpeg")
    assert book_covers.read_cover(book, "cbz", "cover.jpg", "ffmpeg") == first
    assert calls == ["cover.jpg"]

    old_mtime = book.stat().st_mtime_ns
    time.sleep(0.01)
    book.write_bytes(b"changed-book")  # same byte length, then restore mtime; ctime still invalidates
    os.utime(book, ns=(old_mtime, old_mtime))
    second = book_covers.read_cover(book, "cbz", "cover.jpg", "ffmpeg")
    assert second != first and calls == ["cover.jpg", "cover.jpg"]
    assert len(book_covers._cover_files()) == 2


def test_identical_cold_requests_share_one_job_and_return_pending_quickly(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    book = source(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked(_path, _member):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(2)
        return b"source", "image/jpeg"

    monkeypatch.setattr(book_covers, "REQUEST_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(book_covers, "read_resource_uncached", blocked)
    monkeypatch.setattr(book_covers, "_encode_cover", lambda *_args: JPEG)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _pending(book), range(8)))
    assert entered.is_set() and results == [True] * 8
    assert calls == 1 and time.monotonic() - started < 0.5
    with book_covers._lock:
        assert len(book_covers._inflight) == 1
    release.set()
    _wait_ready(book)
    assert book_covers.read_cover(book, "cbz", "cover.jpg", "ffmpeg") == (JPEG, "image/jpeg")


def test_distinct_cold_covers_have_a_bounded_queue_and_one_source_worker(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    books = [source(tmp_path, f"{index}.cbz") for index in range(36)]
    release = threading.Event()
    state_lock = threading.Lock()
    active = peak = 0

    def blocked(_path, _member):  # noqa: ANN001, ANN202
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
        release.wait(2)
        with state_lock:
            active -= 1
        return b"source", "image/jpeg"

    monkeypatch.setattr(book_covers, "REQUEST_WAIT_SECONDS", 0.001)
    monkeypatch.setattr(book_covers, "read_resource_uncached", blocked)
    monkeypatch.setattr(book_covers, "_encode_cover", lambda *_args: JPEG)
    assert all(_pending(book) for book in books)
    with book_covers._lock:
        assert len(book_covers._inflight) == book_covers.MAX_PENDING
    assert peak == 1
    release.set()
    with book_covers._lock:
        queued = list(book_covers._inflight.values())
    wait(queued, timeout=5)
    assert peak == 1


def test_disk_cache_evicts_oldest_by_file_and_byte_bounds(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    root = settings.data_dir / "book-cache" / "covers" / "aa"
    root.mkdir(parents=True)
    paths = []
    for index in range(3):
        path = root / (f"{index:064x}.jpg")
        path.write_bytes(bytes([index]) * 10)
        os.utime(path, (index + 1, index + 1))
        paths.append(path)
    monkeypatch.setattr(book_covers, "CACHE_CAP_FILES", 2)
    monkeypatch.setattr(book_covers, "CACHE_CAP_BYTES", 20)
    book_covers._sweep()
    assert [path.exists() for path in paths] == [False, True, True]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_animated_cover_becomes_one_small_static_jpeg(tmp_path: Path) -> None:
    gif = tmp_path / "animated.gif"
    subprocess.run([
        shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=900x1300:rate=4:duration=0.5", "-frames:v", "2", str(gif),
    ], check=True)
    result = book_covers._encode_cover(gif.read_bytes(), "image/gif", shutil.which("ffmpeg"))
    assert result.startswith(b"\xff\xd8\xff")
    assert renditions.image_size(result) == (415, 600)
    assert len(result) <= book_covers.MAX_COVER_BYTES

    apng = tmp_path / "animated.png"
    subprocess.run([
        shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=900x1300:rate=4:duration=0.5", "-frames:v", "2", "-plays", "0", "-f", "apng", str(apng),
    ], check=True)
    result = book_covers._encode_cover(apng.read_bytes(), "image/png", shutil.which("ffmpeg"))
    assert result.startswith(b"\xff\xd8\xff") and renditions.image_size(result) == (415, 600)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_animated_webp_first_frame_becomes_static_jpeg() -> None:
    animated = base64.b64decode(
        "UklGRoQAAABXRUJQVlA4WAoAAAACAAAACAAADAAAQU5JTQYAAAD/////AABBTk1GKAAAAAAAAAAAAAgAAAwAAGQAAAJWUDhMDwAAAC8IAAMABxD1j/4HIqL/AQBBTk1GKAAAAAAAAAAAAAgAAAwAAGQAAABWUDhMDwAAAC8IAAMABxDR//4HIqL/AQA="
    )
    result = book_covers._encode_cover(animated, "image/webp", shutil.which("ffmpeg"))
    assert result.startswith(b"\xff\xd8\xff")
    assert renditions.image_size(result) == (9, 13)


def _pending(book: Path) -> bool:
    try:
        book_covers.read_cover(book, "cbz", "cover.jpg", "ffmpeg")
    except book_covers.BookCoverPreparing:
        return True
    return False


def _wait_ready(book: Path) -> None:
    with book_covers._lock:
        futures = list(book_covers._inflight.values())
    wait(futures, timeout=2)
