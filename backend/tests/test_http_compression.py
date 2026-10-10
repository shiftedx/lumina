"""LAN response compression and byte-exact media transfers."""
from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.http_compression import ResponseCompressionMiddleware
from app.http_boundaries import SECURITY_HEADERS
from app.main import app
from app.services.media_titles import jellyfin_id
from title_support import ALICE_TOKEN, MOVIE, MOVIE_1080, jellyfin_household, mediabrowser


@pytest.fixture
def client(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def test_large_json_compresses_only_when_negotiated(client: TestClient) -> None:
    identity = client.get("/Items", headers={**mediabrowser(ALICE_TOKEN), "Accept-Encoding": "identity"})
    compressed = client.get("/Items", headers={**mediabrowser(ALICE_TOKEN), "Accept-Encoding": "gzip"})
    assert identity.status_code == compressed.status_code == 200
    assert len(identity.content) > 1000
    assert compressed.json() == identity.json()
    assert compressed.headers.get("content-encoding") == "gzip"
    assert "content-encoding" not in identity.headers
    assert "accept-encoding" in compressed.headers["vary"].lower()
    assert "accept-encoding" in identity.headers["vary"].lower()
    assert int(compressed.headers["content-length"]) < len(identity.content) // 2


def test_full_and_ranged_media_remain_byte_exact_with_gzip_accepted(client: TestClient) -> None:
    query = {"ApiKey": ALICE_TOKEN, "MediaSourceId": jellyfin_id(MOVIE_1080), "Static": "true"}
    path = f"/Videos/{jellyfin_id(MOVIE)}/stream.mkv"
    full = client.get(path, params=query, headers={"Accept-Encoding": "gzip"})
    ranged = client.get(path, params=query, headers={"Accept-Encoding": "gzip", "Range": "bytes=0-2047"})
    assert full.status_code == 200
    assert ranged.status_code == 206
    assert full.content == b"\0" * 4096
    assert ranged.content == full.content[:2048]
    assert "content-encoding" not in full.headers
    assert "content-encoding" not in ranged.headers
    assert ranged.headers["content-range"] == "bytes 0-2047/4096"
    assert int(ranged.headers["content-length"]) == 2048


@pytest.mark.parametrize(("path", "method", "starts_before_body"), [
    ("/jellyfin/videos/item/stream", "GET", True),
    ("/jellyfin/videos/item/stream.mkv", "HEAD", True),
    ("/jellyfin/videos/item/streaming", "GET", False),
    ("/jellyfin/videos/item/stream.mkv/extra", "GET", False),
    ("/jellyfin/videos/item/stream.mkv", "POST", False),
])
def test_direct_range_stream_forwards_headers_before_the_first_body(
    path: str, method: str, starts_before_body: bool,
) -> None:
    """Only normalized direct Range streams bypass compression's delayed response start."""
    async def scenario() -> None:
        first_body = anyio.Event()
        response_started = anyio.Event()
        sent: list[dict] = []

        async def downstream(scope, receive, send):  # noqa: ANN001, ANN202
            await send({
                "type": "http.response.start", "status": 206,
                "headers": [(b"content-type", b"video/mp4"), (b"content-range", b"bytes 0-1/2")],
            })
            response_started.set()
            await first_body.wait()
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        async def receive():  # noqa: ANN202
            return {"type": "http.disconnect"}

        async def capture(message):  # noqa: ANN001, ANN202
            sent.append(message)

        middleware = ResponseCompressionMiddleware(downstream)
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"range", b"bytes=0-1"), (b"accept-encoding", b"gzip")],
            "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        }
        async with anyio.create_task_group() as group:
            group.start_soon(middleware, scope, receive, capture)
            await response_started.wait()
            before_body = any(message["type"] == "http.response.start" for message in sent)
            first_body.set()
        assert before_body is starts_before_body

    anyio.run(scenario)


@pytest.mark.parametrize(("item_id", "media_source_id", "token", "range_header", "status"), [
    (MOVIE, MOVIE_1080, "missing", "bytes=0-1", 401),
    ("f" * 32, "f" * 32, ALICE_TOKEN, "bytes=0-1", 404),
    (MOVIE, MOVIE_1080, ALICE_TOKEN, "bytes=99999-100000", 416),
])
def test_direct_range_stream_errors_keep_identity_and_security_headers(
    client: TestClient, item_id: str, media_source_id: str, token: str, range_header: str, status: int,
) -> None:
    response = client.get(
        f"/Videos/{jellyfin_id(item_id)}/stream.mkv",
        params={"ApiKey": token, "MediaSourceId": jellyfin_id(media_source_id)},
        headers={"Accept-Encoding": "gzip", "Range": range_header},
    )
    assert response.status_code == status
    assert "content-encoding" not in response.headers
    assert "accept-encoding" not in response.headers.get("vary", "").lower()
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_octet_stream_probe_is_not_compressed(client: TestClient) -> None:
    response = client.get(
        "/Playback/BitrateTest", params={"size": 4096},
        headers={**mediabrowser(ALICE_TOKEN), "Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.content == bytes(4096)
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == 4096


def test_excluded_stream_keeps_background_cleanup() -> None:
    closed: list[bool] = []
    streaming = FastAPI()

    @streaming.get("/media")
    def media():  # noqa: ANN202
        return StreamingResponse(
            iter([b"x" * 2048]), media_type="video/mp4", background=BackgroundTask(closed.append, True),
        )

    streaming.add_middleware(ResponseCompressionMiddleware, minimum_size=1000, compresslevel=3)
    with TestClient(streaming) as test_client:
        response = test_client.get("/media", headers={"Accept-Encoding": "gzip"})

    assert response.content == b"x" * 2048
    assert "content-encoding" not in response.headers
    assert closed == [True]


@pytest.mark.parametrize("encoding", ["gzip;q=0, identity", "GZIP; q=0.000", "br, gzip;q=0, *;q=1"])
def test_explicitly_declined_gzip_is_not_sent(client: TestClient, encoding: str) -> None:
    response = client.get("/Items", headers={**mediabrowser(ALICE_TOKEN), "Accept-Encoding": encoding})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(response.content)
    assert "accept-encoding" in response.headers["vary"].lower()
