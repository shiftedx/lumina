"""Playback segment responses pin regenerable files until streaming finishes."""
from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from pathlib import Path

import pytest

from app.services.jellyfin_hls import HlsRun, Plan
from app.services.media_response import pinned_media_file_response
from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE
from tests.test_transcode_sessions import REMUX, registry, start  # noqa: F401 - shared fixture


async def request(response, range_header: bytes | None = None) -> tuple[int, bytes]:  # noqa: ANN001
    messages = []

    async def receive() -> dict:
        return {"type": "http.request"}

    async def send(message: dict) -> None:
        messages.append(message)

    headers = [] if range_header is None else [(b"range", range_header)]
    await response({"type": "http", "method": "GET", "headers": headers}, receive, send)
    return messages[0]["status"], b"".join(message["body"] for message in messages[1:])


def test_raw_segment_hardlink_survives_history_prune_and_preserves_ranges(registry) -> None:
    session = start(registry, REMUX)
    session.plan = Plan(tuple(float(i * 10) for i in range(100)), (10.0,) * 100)
    session.run = HlsRun(0, 0)
    source = session.directory / "seg42.m4s"
    source.write_bytes(b"raw-segment")
    session.max_requested = 50

    response = pinned_media_file_response(lambda: registry.segment(session, source.name), session.seek_lock)
    assert response is not None
    pinned = Path(response.path)
    assert os.stat(source).st_ino == os.stat(pinned).st_ino

    registry.maintain_buffers()
    assert not source.exists() and pinned.exists()
    assert asyncio.run(request(response, b"bytes=1-4")) == (206, b"aw-s")
    assert not pinned.parent.exists()


def test_roku_split_segment_hardlink_survives_seek_restart(registry) -> None:
    session = start(registry, REMUX)
    session.plan = Plan((0.0, 4.0, 8.0), (4.0,) * 3)
    session.run = HlsRun(0, 0)
    session.respawn = lambda run: subprocess.Popen(["sleep", "60"])
    source = session.directory / "audio-seg1.m4s"
    source.write_bytes(b"roku-segment")

    response = pinned_media_file_response(lambda: source if source.is_file() else None, session.seek_lock)
    assert response is not None
    pinned = Path(response.path)
    assert os.stat(source).st_ino == os.stat(pinned).st_ino

    with session.seek_lock:
        registry._restart(session, HlsRun(1, 4))
    assert not source.exists() and pinned.exists()
    assert asyncio.run(request(response))[1] == b"roku-segment"
    assert not pinned.parent.exists()


def test_pin_retries_a_source_removed_while_waiting_for_the_seek_lock(tmp_path: Path) -> None:
    source = tmp_path / "seg7.m4s"
    calls = 0

    class InvalidatesOnce:
        def __enter__(self):  # noqa: ANN204
            if source.exists() and calls == 1:
                source.unlink()

        def __exit__(self, *args) -> None:  # noqa: ANN002
            pass

    def resolve() -> Path:
        nonlocal calls
        calls += 1
        if not source.exists():
            source.write_bytes(b"replacement")
        return source

    response = pinned_media_file_response(resolve, InvalidatesOnce())
    assert response is not None and calls == 2
    assert asyncio.run(request(response))[1] == b"replacement"


def test_pin_streams_before_cleanup_when_the_server_offers_deferred_path_send(tmp_path: Path) -> None:
    source = tmp_path / "seg0.m4s"
    source.write_bytes(b"segment")
    response = pinned_media_file_response(lambda: source, threading.Lock())
    assert response is not None
    messages = []

    async def stream() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            messages.append(message)

        await response(
            {"type": "http", "method": "GET", "headers": [], "extensions": {"http.response.pathsend": {}}},
            receive,
            send,
        )

    asyncio.run(stream())
    assert not any(message["type"] == "http.response.pathsend" for message in messages)
    assert b"".join(message["body"] for message in messages[1:]) == b"segment"


@pytest.mark.parametrize("failure", ["send", "receive"])
def test_pin_is_cleaned_after_stream_error_or_disconnect(tmp_path: Path, failure: str) -> None:
    source = tmp_path / "seg0.m4s"
    source.write_bytes(b"x" * (2 * MEDIA_FILE_CHUNK_SIZE))
    response = pinned_media_file_response(lambda: source, threading.Lock())
    assert response is not None
    pinned = Path(response.path)

    async def stream() -> None:
        disconnected = asyncio.Event()

        async def receive() -> dict:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] != "http.response.body":
                return
            if failure == "send":
                raise ConnectionError("client disconnected")
            disconnected.set()

        await response(
            {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-")]}, receive, send,
        )

    if failure == "send":
        with pytest.raises(ConnectionError, match="client disconnected"):
            asyncio.run(stream())
    else:
        asyncio.run(stream())
    assert not pinned.parent.exists()
