from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette import responses

from app.services import media_response
from app.services.media_response import MediaFileResponse
from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE

try:
    import uvloop
except ImportError:  # pragma: no cover - optional on unsupported platforms
    UVLOOP_LOOP = pytest.param(
        asyncio.new_event_loop, id="uvloop", marks=pytest.mark.skip(reason="uvloop is not installed"),
    )
else:
    UVLOOP_LOOP = pytest.param(uvloop.new_event_loop, id="uvloop")



def test_media_file_response_preserves_range_bytes_with_fewer_bounded_reads(tmp_path: Path) -> None:
    media = tmp_path / "media.bin"
    payload = bytes(range(251)) * (MEDIA_FILE_CHUNK_SIZE // 251 + 2)
    media.write_bytes(payload)
    start, end = 7, MEDIA_FILE_CHUNK_SIZE + 11
    messages: list[dict] = []

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            messages.append(message)

        await MediaFileResponse(media)(
            {"type": "http", "method": "GET", "headers": [(b"range", f"bytes={start}-{end}".encode())]},
            receive,
            send,
        )

    asyncio.run(request())

    response_start = messages[0]
    body = [message["body"] for message in messages[1:]]
    headers = dict(response_start["headers"])
    assert response_start["status"] == 206
    assert headers[b"content-range"] == f"bytes {start}-{end}/{len(payload)}".encode()
    assert [len(chunk) for chunk in body] == [MediaFileResponse.first_chunk_size, end - start + 1 - MediaFileResponse.first_chunk_size]
    assert b"".join(body) == payload[start : end + 1]


def test_media_file_response_keeps_head_and_multipart_range_semantics(tmp_path: Path) -> None:
    media = tmp_path / "media.bin"
    media.write_bytes(bytes(range(32)))

    async def request(method: str, range_header: bytes | None = None) -> list[dict]:
        messages = []

        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            messages.append(message)

        headers = [] if range_header is None else [(b"range", range_header)]
        await MediaFileResponse(media)({"type": "http", "method": method, "headers": headers}, receive, send)
        return messages

    head = asyncio.run(request("HEAD"))
    assert head[0]["status"] == 200 and head[1] == {"type": "http.response.body", "body": b"", "more_body": False}
    assert dict(head[0]["headers"])[b"content-length"] == b"32"

    multipart = asyncio.run(request("GET", b"bytes=0-2,10-12"))
    headers = dict(multipart[0]["headers"])
    body = b"".join(message["body"] for message in multipart[1:])
    assert multipart[0]["status"] == 206 and headers[b"content-type"].startswith(b"multipart/byteranges; boundary=")
    assert b"Content-Range: bytes 0-2/32" in body and b"\x00\x01\x02" in body
    assert b"Content-Range: bytes 10-12/32" in body and b"\x0a\x0b\x0c" in body


@pytest.mark.parametrize("range_header", [None, b"bytes=0-"])
def test_media_file_response_closes_the_file_when_the_client_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, range_header: bytes | None,
) -> None:
    media = tmp_path / "media.bin"
    media.write_bytes(b"a" * (MEDIA_FILE_CHUNK_SIZE + 1))
    opened = []
    if range_header is None:
        real_open = responses.anyio.open_file

        async def tracked_open(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            handle = await real_open(*args, **kwargs)
            opened.append(handle)
            return handle

        monkeypatch.setattr(responses.anyio, "open_file", tracked_open)
    else:
        real_open_seek_read = media_response._open_seek_read

        def tracked_open_seek_read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            handle, chunk = real_open_seek_read(*args, **kwargs)
            opened.append(handle)
            return handle, chunk

        monkeypatch.setattr(media_response, "_open_seek_read", tracked_open_seek_read)

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def disconnect(message: dict) -> None:
            if message["type"] == "http.response.body":
                raise ConnectionError("client disconnected")

        headers = [] if range_header is None else [(b"range", range_header)]
        await MediaFileResponse(media)({"type": "http", "method": "GET", "headers": headers}, receive, disconnect)

    with pytest.raises(ConnectionError, match="client disconnected"):
        asyncio.run(request())
    assert len(opened) == 1
    assert (opened[0]._fp.closed if range_header is None else opened[0].closed) is True


def test_range_sends_a_small_first_chunk_before_bulk_reads(tmp_path: Path) -> None:
    media = tmp_path / "startup.bin"
    payload = bytes(range(251)) * (3 * MEDIA_FILE_CHUNK_SIZE // 251 + 1)
    media.write_bytes(payload)
    messages = []

    async def request():  # noqa: ANN202
        async def receive():  # noqa: ANN202
            return {"type": "http.request"}

        async def send(message):  # noqa: ANN001
            messages.append(message)

        await MediaFileResponse(media)({"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-")]}, receive, send)

    asyncio.run(request())
    body = [message["body"] for message in messages[1:]]
    assert len(body[0]) == 64 * 1024
    assert len(body[1]) == MEDIA_FILE_CHUNK_SIZE
    assert b"".join(body) == payload
    assert messages[-1]["more_body"] is False


def test_range_combines_open_seek_and_first_read_in_one_worker_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "startup-handoff.bin"
    media.write_bytes(b"x" * (MediaFileResponse.first_chunk_size + 1))
    original_run_sync = media_response.anyio.to_thread.run_sync
    header_sent = False
    first_body_sent = False
    handoffs: list[str] = []

    async def tracked_run_sync(func, *args, **kwargs):  # noqa: ANN001, ANN202
        if header_sent and not first_body_sent:
            handoffs.append(getattr(func, "__name__", type(func).__name__))
        return await original_run_sync(func, *args, **kwargs)

    monkeypatch.setattr(media_response.anyio.to_thread, "run_sync", tracked_run_sync)

    async def request() -> None:
        nonlocal header_sent, first_body_sent

        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            nonlocal header_sent, first_body_sent
            if message["type"] == "http.response.start":
                header_sent = True
            elif message["type"] == "http.response.body":
                first_body_sent = True

        await MediaFileResponse(media)(
            {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-")]},
            receive,
            send,
        )

    asyncio.run(request())
    assert handoffs == ["_open_seek_read"]


@pytest.mark.parametrize("method,header,status,expected", [
    ("GET", b"bytes=10-19", 206, bytes(range(10, 20))),
    ("GET", b"bytes=-5", 206, bytes(range(27, 32))),
    ("HEAD", b"bytes=10-19", 206, b""),
    ("GET", b"bytes=32-", 416, b""),
])
def test_startup_chunk_keeps_range_boundaries(tmp_path: Path, method: str, header: bytes, status: int, expected: bytes) -> None:
    media = tmp_path / "bounds.bin"
    media.write_bytes(bytes(range(32)))
    messages = []

    async def request():  # noqa: ANN202
        async def receive():  # noqa: ANN202
            return {"type": "http.request"}

        async def send(message):  # noqa: ANN001
            messages.append(message)

        await MediaFileResponse(media)({"type": "http", "method": method, "headers": [(b"range", header)]}, receive, send)

    asyncio.run(request())
    assert messages[0]["status"] == status
    assert b"".join(message["body"] for message in messages[1:]) == expected


def test_range_stops_reading_when_receive_disconnects_and_send_ignores_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uvicorn's send may return silently after its receive reports disconnect."""
    media = tmp_path / "abandoned.bin"
    media.write_bytes(b"x" * (16 * MEDIA_FILE_CHUNK_SIZE))
    opened, read_bytes = [], []
    real_open_seek_read = media_response._open_seek_read

    class TrackedFile:
        def __init__(self, handle):  # noqa: ANN001
            self._handle = handle

        def read(self, size: int) -> bytes:
            body = self._handle.read(size)
            read_bytes.append(len(body))
            return body

        def close(self) -> None:
            self._handle.close()

        @property
        def closed(self) -> bool:
            return self._handle.closed

    def tracked_open_seek_read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        handle, chunk = real_open_seek_read(*args, **kwargs)
        read_bytes.append(len(chunk))
        tracked = TrackedFile(handle)
        opened.append(tracked)
        return tracked, chunk

    monkeypatch.setattr(media_response, "_open_seek_read", tracked_open_seek_read)

    async def request() -> None:
        disconnected = asyncio.Event()

        async def receive() -> dict:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body":
                disconnected.set()  # later sends intentionally return without an error

        await MediaFileResponse(media)({"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-")]}, receive, send)

    asyncio.run(request())
    assert sum(read_bytes) <= MediaFileResponse.first_chunk_size + MEDIA_FILE_CHUNK_SIZE
    assert len(opened) == 1 and opened[0].closed


@pytest.mark.parametrize("loop_factory", [pytest.param(asyncio.new_event_loop, id="asyncio"), UVLOOP_LOOP])
def test_initial_range_gives_a_queued_disconnect_time_before_bulk_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loop_factory,
) -> None:  # noqa: ANN001
    """A transport disconnect queued by the first body prevents speculative bulk reading."""
    media = tmp_path / "queued-disconnect.bin"
    media.write_bytes(b"x" * (MediaFileResponse.first_chunk_size + MEDIA_FILE_CHUNK_SIZE + 1))
    bulk_reads, opened = [], []
    real_open_seek_read = media_response._open_seek_read

    class TrackedFile:
        def __init__(self, handle):  # noqa: ANN001
            self._handle = handle

        def read(self, size: int) -> bytes:
            bulk_reads.append(size)
            return self._handle.read(size)

        def close(self) -> None:
            self._handle.close()

        @property
        def closed(self) -> bool:
            return self._handle.closed

    def tracked_open_seek_read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        handle, chunk = real_open_seek_read(*args, **kwargs)
        tracked = TrackedFile(handle)
        opened.append(tracked)
        return tracked, chunk

    monkeypatch.setattr(media_response, "_open_seek_read", tracked_open_seek_read)

    async def request() -> list[int]:
        disconnected = asyncio.Event()
        body_sizes = []

        async def receive() -> dict:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body":
                body_sizes.append(len(message["body"]))
                if len(body_sizes) == 1:
                    # The protocol schedules the disconnect after this send has returned.
                    asyncio.get_running_loop().call_soon(disconnected.set)

        await MediaFileResponse(media)(
            {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-")]}, receive, send,
        )
        return body_sizes

    loop = loop_factory()
    try:
        assert loop.run_until_complete(request()) == [MediaFileResponse.first_chunk_size]
    finally:
        loop.close()
    assert bulk_reads == []
    assert len(opened) == 1 and opened[0].closed


def test_shared_response_keeps_disconnect_receivers_request_scoped(tmp_path: Path) -> None:
    media = tmp_path / "shared.bin"
    payload = bytes(range(251)) * (4 * MEDIA_FILE_CHUNK_SIZE // 251 + 1)
    media.write_bytes(payload)
    response = MediaFileResponse(media)

    async def request(header: bytes, abandon: bool) -> bytes:
        disconnected = asyncio.Event()
        bodies = []

        async def receive() -> dict:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body":
                bodies.append(message["body"])
                if abandon or not message["more_body"]:
                    disconnected.set()

        await response({"type": "http", "method": "GET", "headers": [(b"range", header)]}, receive, send)
        return b"".join(bodies)

    async def both() -> list[bytes]:
        return await asyncio.gather(request(b"bytes=0-", True), request(b"bytes=13-2097152", False))

    abandoned, complete = asyncio.run(both())
    assert len(abandoned) <= response.first_chunk_size + MEDIA_FILE_CHUNK_SIZE
    assert complete == payload[13:2097153]
