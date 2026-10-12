from __future__ import annotations

import asyncio
import socket
from contextlib import nullcontext
from dataclasses import dataclass, field

from app import main as main_module
from app.models import User
from app.schemas import MediaSourceCapabilities, PreviewRequest, PreviewResponse
from app.services.hls_relay import HlsRelayService
from app.services.network_policy import PublicSourcePolicy
from app.services.remote_streaming import ByteRange, UpstreamMediaResponse
from app.services.yt_dlp_service import YtDlpService


MASTER_URL = "https://cdn.example/vod/master.m3u8"
MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\"\n"
    "360p/index.m3u8\n"
)
MEDIA = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
)


def resolver_for(mapping):
    def resolve(host, port, family=0, socktype=socket.SOCK_STREAM):  # noqa: ANN001
        del family
        return [(socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (mapping[host][0], port))]

    return resolve


@dataclass
class FakeRelayFetcher:
    calls: list[str] = field(default_factory=list)

    def fetch(self, url, *, headers=None, byte_range: ByteRange | None = None, timeout_seconds=None):  # noqa: ANN001
        self.calls.append(url)
        bodies = {
            MASTER_URL: MASTER.encode(),
            "https://cdn.example/vod/360p/index.m3u8": MEDIA.encode(),
            "https://cdn.example/vod/360p/seg0.ts": b"transport-stream-bytes",
        }
        body = bodies.get(url)
        if body is None:
            return UpstreamMediaResponse(404, {}, [b""])
        headers_out = {"content-length": str(len(body)), "set-cookie": "upstream=secret"}
        return UpstreamMediaResponse(200, headers_out, [body])


class FakeResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        del owner_user_id
        return {
            "extractor_key": "TwitchVod",
            "webpage_url": source_url,
            "formats": [{"format_id": "360p", "protocol": "m3u8_native", "url": "https://cdn.example/vod/360p/index.m3u8", "manifest_url": MASTER_URL}],
        }


def _relay_service() -> HlsRelayService:
    return HlsRelayService(
        resolver=FakeResolver(),
        fetcher=FakeRelayFetcher(),
        policy=PublicSourcePolicy(resolver=resolver_for({"cdn.example": ["93.184.216.34"]})),
        token_factory=lambda: "relay-stream",
        resource_token_factory=iter(["res-0", "res-1", "res-2", "res-3"]).__next__,
        clock=lambda: 1_000.0,
    )


def _raw_info(source_url: str) -> dict:
    return {
        "title": "Twitch VOD",
        "webpage_url": source_url,
        "extractor_key": "TwitchVod",
        "formats": [{"format_id": "360p", "protocol": "m3u8_native", "url": "https://cdn.example/vod/360p/index.m3u8", "manifest_url": MASTER_URL, "vcodec": "avc1", "acodec": "mp4a"}],
    }


def _twitch_capabilities() -> MediaSourceCapabilities:
    return MediaSourceCapabilities(provider="twitch", lifecycle="completed_live", can_play=True, can_acquire=False, acquire_reason="segmented_transport_not_supported")


def _setup(monkeypatch, service: HlsRelayService, user: User) -> None:
    monkeypatch.setattr(main_module, "hls_relay", service)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *a, **k: user)
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        return PreviewResponse(kind="video", title="Twitch VOD", webpage_url=source_url, capabilities=_twitch_capabilities(), raw=_raw_info(source_url))

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)


def _read(response) -> bytes:
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_twitch_vod_preview_registers_relay_playback(monkeypatch) -> None:
    user = User(id="owner-1", username="owner", role="viewer", is_active=True)
    _setup(monkeypatch, _relay_service(), user)
    response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.twitch.tv/videos/1"), object()))
    assert response.playback is not None
    assert response.playback.transport == "hls"
    assert response.playback.status == "ready"
    assert response.playback.playback_url == "/api/remote-streams/relay-stream/relay/1/master.m3u8"
    assert "cdn.example" not in repr(response)


def test_relay_master_and_resources_serve_only_lumina_addresses(monkeypatch) -> None:
    user = User(id="owner-1", username="owner", role="viewer", is_active=True)
    service = _relay_service()
    _setup(monkeypatch, service, user)
    asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.twitch.tv/videos/1"), object()))

    master_response = main_module.remote_stream_relay_master("relay-stream", 1, object(), user)
    master = _read(master_response).decode()
    assert "cdn.example" not in master
    assert "/api/remote-streams/relay-stream/relay/1/r/" in master
    assert master_response.headers["content-type"].startswith("application/vnd.apple.mpegurl")

    variant_id = master.split("/relay/1/r/")[1].split()[0].strip()
    media_response = main_module.remote_stream_relay_resource("relay-stream", 1, variant_id, object(), user, None)
    media = _read(media_response).decode()
    assert "cdn.example" not in media

    segment_id = media.split("/relay/1/r/")[1].split()[0].strip()
    segment_response = main_module.remote_stream_relay_resource("relay-stream", 1, segment_id, object(), user, None)
    assert "set-cookie" not in {k.lower() for k in segment_response.headers}
    assert _read(segment_response) == b"transport-stream-bytes"


def test_relay_refresh_and_release_dispatch_through_shared_routes(monkeypatch) -> None:
    user = User(id="owner-1", username="owner", role="viewer", is_active=True)
    service = _relay_service()
    _setup(monkeypatch, service, user)
    asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.twitch.tv/videos/1"), object()))

    refreshed = main_module.refresh_remote_stream("relay-stream", object(), user)
    assert refreshed.stream_id == "relay-stream"
    assert refreshed.playback_url == "/api/remote-streams/relay-stream/relay/2/master.m3u8"

    released = main_module.release_remote_stream("relay-stream", user)
    assert released.status_code == 204
    assert not service.has_owned_stream(user.id, "relay-stream")
