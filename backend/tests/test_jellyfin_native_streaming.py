"""Native streaming stays inside the Connected-app boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.models import AppSettings, MemberAccess, User
from title_support import ALICE, ALICE_TOKEN, jellyfin_household, mediabrowser
from app.schemas import PreviewResponse
from app.services.remote_streaming import PlaybackDescriptor, StreamResponseSpec
from app.services import screen_time
from app.services.yt_dlp_service import YtDlpService


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def _enabled() -> None:
    with main.SessionLocal.begin() as db:
        db.get(AppSettings, 1).native_streaming_enabled = True


def _preview(url: str) -> PreviewResponse:
    return PreviewResponse(kind="video", webpage_url=url, raw={
        "id": "native-proof", "extractor_key": "Youtube", "webpage_url": url,
        "formats": [{"format_id": "muxed", "url": "https://cdn.example/video.mp4", "protocol": "https",
                     "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "content_type": "video/mp4"}],
    })


@dataclass
class Streams:
    released: list[str]
    registered_profiles: list[frozenset[str]] = field(default_factory=list)
    body: bytes = b"native-video"

    def register(self, **kwargs):  # noqa: ANN003, ANN201
        self.registered_profiles.append(kwargs["browser_capabilities"].supported_profiles)
        return PlaybackDescriptor("ready", "Native_Mixed", "progressive", "video", "/api/remote-streams/Native_Mixed/content", "video/mp4", True, True, True)

    def serve_content(self, owner, stream_id, range_header=None, rendition_id=None):  # noqa: ANN001, ANN201
        assert owner and stream_id == "Native_Mixed" and rendition_id is None
        return StreamResponseSpec(200, {"Content-Type": "video/mp4", "Content-Length": str(len(self.body))}, [self.body])

    def release(self, owner, stream_id):  # noqa: ANN001
        assert owner and stream_id == "Native_Mixed"
        self.released.append(stream_id)


def test_native_streaming_requires_configuration_then_serves_and_releases_an_owner_session(native: TestClient, monkeypatch) -> None:
    source = "https://www.youtube.com/watch?v=nativeproof"
    called = []
    monkeypatch.setattr(YtDlpService, "preview_for_playback", lambda *_args, **_kwargs: called.append(1))
    assert native.post("/Lumina/Streaming/playback", headers=mediabrowser(ALICE_TOKEN), json={"source_url": source}).status_code == 403
    assert not called

    _enabled()
    streams = Streams([])
    monkeypatch.setattr(main, "remote_streams", streams)
    monkeypatch.setattr(YtDlpService, "preview_for_playback", lambda _self, url, *_args, **_kwargs: _preview(url))
    started = native.post("/Lumina/Streaming/playback", headers=mediabrowser(ALICE_TOKEN), json={"source_url": source})
    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["playback_url"].startswith("/Lumina/Streaming/playback/Native_Mixed/content?api_key=")
    assert streams.registered_profiles == [frozenset({"mp4-avc-aac", "m4a-aac"})]
    content = native.get(payload["playback_url"])
    assert (content.status_code, content.content) == (200, b"native-video")

    with main.SessionLocal.begin() as db:
        db.get(AppSettings, 1).native_streaming_enabled = False
    assert native.delete("/Lumina/Streaming/playback/Native_Mixed", headers=mediabrowser(ALICE_TOKEN)).status_code == 204
    assert streams.released == ["Native_Mixed"]


def test_native_relay_manifest_never_leaks_browser_or_upstream_paths(native: TestClient, monkeypatch) -> None:
    _enabled()

    class Relay:
        def serve_master(self, *_args):  # noqa: ANN001, ANN201
            return StreamResponseSpec(200, {"Content-Type": "application/vnd.apple.mpegurl"}, [b"#EXTM3U\n/api/remote-streams/Relay_Mixed/relay/1/r/resource\n"])

    monkeypatch.setattr(main, "_relay_service_for", lambda *_args: Relay())
    response = native.get("/Lumina/Streaming/playback/Relay_Mixed/relay/1/master.m3u8", params={"api_key": ALICE_TOKEN})
    assert response.status_code == 200
    assert b"/api/remote-streams/" not in response.content
    assert b"/Lumina/Streaming/playback/Relay_Mixed/relay/1/r/resource?api_key=" in response.content


def test_native_hls_generation_redirect_stays_on_the_native_route(native: TestClient, monkeypatch) -> None:
    _enabled()

    class HLS:
        def has_presentation(self, *_args):  # noqa: ANN001, ANN201
            return False

        def serve_hls(self, *_args):  # noqa: ANN001, ANN201
            return StreamResponseSpec(
                307,
                {"Location": "/api/remote-streams/Hls_Mixed/hls/2/manifest.m3u8"},
                [],
            )

    monkeypatch.setattr(main, "remote_streams", HLS())
    response = native.get(
        "/Lumina/Streaming/playback/Hls_Mixed/hls/1/manifest.m3u8",
        params={"api_key": ALICE_TOKEN}, follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["Location"].startswith("/Lumina/Streaming/playback/Hls_Mixed/hls/2/manifest.m3u8?api_key=")
    assert "/api/remote-streams/" not in response.headers["Location"]


def test_native_streaming_rechecks_member_provider_restrictions_before_extraction(native: TestClient, monkeypatch) -> None:
    _enabled()
    with main.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=ALICE, streaming={"youtube": False}))
    called = []
    monkeypatch.setattr(YtDlpService, "preview_for_playback", lambda *_args, **_kwargs: called.append(1))
    response = native.post("/Lumina/Streaming/playback", headers=mediabrowser(ALICE_TOKEN), json={"source_url": "https://www.youtube.com/watch?v=blocked"})
    assert response.status_code == 403
    assert not called


def test_native_streaming_start_rechecks_screen_time_before_extraction(native: TestClient, monkeypatch) -> None:
    """A cached segment allowance must not authorize a new remote stream."""
    _enabled()
    with main.SessionLocal.begin() as db:
        # Prime the running-stream cache while Alice is unrestricted, then add
        # an exhausted member policy without going through the setter that
        # deliberately clears that cache.
        screen_time.enforce(db, db.get(User, ALICE))
        db.add(MemberAccess(user_id=ALICE, daily_limit_minutes=0))
    called = []
    monkeypatch.setattr(YtDlpService, "preview_for_playback", lambda *_args, **_kwargs: called.append(1))
    try:
        response = native.post(
            "/Lumina/Streaming/playback", headers=mediabrowser(ALICE_TOKEN),
            json={"source_url": "https://www.youtube.com/watch?v=screen-time"},
        )
        assert response.status_code == 403
        assert not called
    finally:
        screen_time.forget(ALICE)
