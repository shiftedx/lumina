from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette import responses

from app.services.media_response import MediaFileResponse
from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE


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
    assert [len(chunk) for chunk in body] == [MEDIA_FILE_CHUNK_SIZE, end - start + 1 - MEDIA_FILE_CHUNK_SIZE]
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


def test_media_file_response_closes_the_file_when_the_client_disconnects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "media.bin"
    media.write_bytes(b"a" * (MEDIA_FILE_CHUNK_SIZE + 1))
    opened = []
    real_open = responses.anyio.open_file

    async def tracked_open(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        handle = await real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(responses.anyio, "open_file", tracked_open)

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def disconnect(message: dict) -> None:
            if message["type"] == "http.response.body":
                raise ConnectionError("client disconnected")

        await MediaFileResponse(media)({"type": "http", "method": "GET", "headers": []}, receive, disconnect)

    with pytest.raises(ConnectionError, match="client disconnected"):
        asyncio.run(request())
    assert len(opened) == 1 and opened[0]._fp.closed is True
