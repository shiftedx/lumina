"""The direct-play route stays ahead of unrelated application routes."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.routers.admin_storage import URL as STORAGE_ROOTS_URL
from app.services.media_titles import jellyfin_id
from title_support import ALICE_TOKEN, MOVIE, jellyfin_household


@pytest.fixture
def jf(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("HEAD", ".mkv")])
def test_direct_stream_bypasses_earlier_unrelated_route_matching(
    jf: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, suffix: str,
) -> None:
    """The /Videos hot path must not probe an earlier admin route before dispatch."""
    original_matches = APIRoute.matches
    stream_path = f"/jellyfin/videos/{jellyfin_id(MOVIE)}/stream{suffix}"

    def matches(route, scope):  # noqa: ANN001, ANN202
        if scope["path"] == stream_path and route.path == STORAGE_ROOTS_URL:
            raise AssertionError("direct stream reached unrelated earlier admin route matching")
        return original_matches(route, scope)

    monkeypatch.setattr(APIRoute, "matches", matches)
    response = jf.request(
        method,
        f"/Videos/{jellyfin_id(MOVIE)}/stream{suffix}",
        params={"api_key": ALICE_TOKEN},
        headers={"Range": "bytes=0-9"},
    )
    assert response.status_code == 206
    assert response.headers["content-length"] == "10"
    assert response.content == (b"\0" * 10 if method == "GET" else b"")
    # The unrelated route remains registered and handles its usual unauthenticated response.
    assert jf.get(STORAGE_ROOTS_URL).status_code == 401
