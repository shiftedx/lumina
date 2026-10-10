"""LAN response compression and byte-exact media transfers."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.http_compression import ResponseCompressionMiddleware
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
