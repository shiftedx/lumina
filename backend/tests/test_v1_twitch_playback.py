"""S29 — Twitch live + VOD lifecycle without auth, over the guarded HLS relay.

Hermetic: a real ffmpeg fMP4 HLS VOD served by an in-memory upstream, a moving
live window, and a loopback HTTP server standing in for the CDN (the policy still
resolves every host through fixture DNS and rejects private answers).
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yt_dlp

from app import main as main_module
from app.services import network_policy
from app.services.hls_relay import HlsRelayService
from app.services.live_hls_relay import LiveHlsRelayService
from app.services.media_capabilities import derive_media_capabilities
from app.services.network_policy import PublicSourcePolicyError
from app.services.remote_streaming_adapters import PublicRelayFetcher
from app.services.yt_dlp_service import YtDlpService
from tests.test_hls_relay import FakeRelayFetcher, drain, iter_factory, public_policy, resolver_for

VOD_URL = "https://www.twitch.tv/videos/2000000001"
CHANNEL_URL = "https://www.twitch.tv/luminafixture"
CDN = "https://vod.cdn.example/abc/chunked"
LIVE = "https://live.cdn.example/hls"


def _vod_info(master: str = f"{CDN}/master.m3u8", **extra) -> dict:
    """Recorded shape of a public completed Twitch VOD (yt-dlp sanitized info)."""

    return {
        "id": "v2000000001", "extractor": "twitch:vod", "extractor_key": "TwitchVod", "webpage_url": VOD_URL,
        "was_live": True, "live_status": "was_live", "duration": 8,
        "formats": [{"format_id": "360p30", "protocol": "m3u8_native", "url": master.replace("master.m3u8", "360p/index.m3u8"),
                     "manifest_url": master, "vcodec": "vp09.00.21.08", "acodec": "opus", "height": 360}],
        **extra,
    }


def _live_info(master: str = f"{LIVE}/master.m3u8") -> dict:
    return {
        "id": "luminafixture", "extractor": "twitch:stream", "extractor_key": "TwitchStream", "webpage_url": CHANNEL_URL,
        "is_live": True, "live_status": "is_live",
        "formats": [{"format_id": "360p", "protocol": "m3u8_native", "url": master.replace("master.m3u8", "360p/index.m3u8"),
                     "manifest_url": master, "vcodec": "avc1.4d401e", "acodec": "mp4a.40.2"}],
    }


def _ids(playlist: str, stream_id: str, generation: int) -> list[str]:
    return re.findall(rf"/api/remote-streams/{stream_id}/relay/{generation}/r/([\w-]+)", playlist)


@pytest.fixture(scope="module")
def vod_hls(tmp_path_factory) -> dict[str, bytes]:  # noqa: ANN001
    if not shutil.which("ffmpeg"):
        pytest.skip("BLOCKED: ffmpeg is required for the real HLS VOD fixture")
    out = tmp_path_factory.mktemp("s29")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libvpx-vp9", "-deadline", "realtime",
        "-cpu-used", "8", "-b:v", "300k", "-g", "48", "-c:a", "libopus", "-shortest", "-f", "hls",
        "-hls_segment_type", "fmp4", "-hls_time", "2", "-hls_playlist_type", "vod", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(out / "seg%d.m4s"), str(out / "index.m3u8"),
    ], check=True, capture_output=True)
    return {path.name: path.read_bytes() for path in out.iterdir()}


def test_twitch_vod_seek_fixture(vod_hls) -> None:  # noqa: ANN001
    caps = derive_media_capabilities(_vod_info())
    assert (caps.provider, caps.lifecycle, caps.can_play, caps.can_acquire) == ("twitch", "completed_live", True, True)
    assert caps.from_start_available is False and caps.can_record is False

    master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360,CODECS="vp09.00.21.08,opus"\n360p/index.m3u8\n'
    responses = {f"{CDN}/master.m3u8": (200, {}, master.encode())}
    responses |= {f"{CDN}/360p/{name}": (200, {}, body) for name, body in vod_hls.items()}
    service = HlsRelayService(
        resolver=None, fetcher=FakeRelayFetcher(responses), policy=public_policy({"vod.cdn.example": ["93.184.216.34"]}),
        token_factory=lambda: "vod", resource_token_factory=iter_factory([f"r{i}" for i in range(50)]), clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="member", source_url=VOD_URL, info=_vod_info())
    assert (playback.status, playback.seekable, playback.live) == ("ready", True, False)

    relayed_master = drain(service.serve_master("member", "vod", 1)).decode()
    media = drain(service.serve_resource("member", "vod", 1, _ids(relayed_master, "vod", 1)[0])).decode()
    # A finite VOD: ENDLIST present, total duration intact, no upstream address leaks.
    assert "#EXT-X-ENDLIST" in media and "cdn.example" not in media + relayed_master
    assert sum(float(value) for value in re.findall(r"#EXTINF:([\d.]+)", media)) == pytest.approx(8.0)
    init_id, *segment_ids = _ids(media, "vod", 1)
    assert drain(service.serve_resource("member", "vod", 1, init_id)) == vod_hls["init.mp4"]
    # Seeking jumps straight to a later segment (and a byte range inside it).
    assert drain(service.serve_resource("member", "vod", 1, segment_ids[3])) == vod_hls["seg3.m4s"]
    part = service.serve_resource("member", "vod", 1, segment_ids[2], "bytes=100-199")
    assert part.status_code == 206 and drain(part) == vod_hls["seg2.m4s"][100:200]


def test_twitch_live_edge_fixture() -> None:
    caps = derive_media_capabilities(_live_info())
    assert (caps.lifecycle, caps.can_play, caps.can_acquire, caps.can_record) == ("live", True, False, True)
    # No DVR/from-start/scheduling affordance is offered for Twitch.
    assert caps.from_start_available is False and caps.can_schedule is False

    def window(first: int) -> bytes:
        body = f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:{first}\n"
        return (body + "".join(f"#EXTINF:2.0,\nseg{n}.ts\n" for n in range(first, first + 3))).encode()

    windows = iter([window(40), window(41)])

    class LiveFetcher(FakeRelayFetcher):
        def fetch(self, url, **kwargs):  # noqa: ANN001, ANN003
            if url.endswith("360p/index.m3u8"):
                self.responses[url] = (200, {}, next(windows))
            return super().fetch(url, **kwargs)

    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360p/index.m3u8\n"
    state = {"info": _live_info()}

    class Resolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            if isinstance(state["info"], Exception):
                raise state["info"]
            return state["info"]

    service = LiveHlsRelayService(
        resolver=Resolver(), fetcher=LiveFetcher({f"{LIVE}/master.m3u8": (200, {}, master.encode())}),
        policy=public_policy({"live.cdn.example": ["93.184.216.34"]}), token_factory=iter_factory(["live-a", "live-b"]),
        resource_token_factory=iter_factory([f"r{i}" for i in range(50)]), clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="member", source_url=CHANNEL_URL, info=_live_info())
    assert (playback.live, playback.seekable) == (True, False)
    media_id = _ids(drain(service.serve_master("member", "live-a", 1)).decode(), "live-a", 1)[0]
    first = drain(service.serve_resource("member", "live-a", 1, media_id)).decode()
    second = drain(service.serve_resource("member", "live-a", 1, media_id)).decode()
    assert "MEDIA-SEQUENCE:40" in first and "MEDIA-SEQUENCE:41" in second
    assert "#EXT-X-ENDLIST" not in first + second

    # live -> ended: yt-dlp reports an offline Twitch channel as UserNotLive.
    state["info"] = yt_dlp.utils.DownloadError("ERROR: [twitch:stream] luminafixture: The channel is not currently live")
    ended = service.refresh("member", "live-a")
    assert (ended.status, ended.fallback_code, ended.fallback_message) == ("unsupported", "live_stream_ended", "This live stream has ended.")

    # A broadcast that finished into a VOD (was_live) is never relayed as live.
    service.register(owner_user_id="member", source_url=CHANNEL_URL, info=_live_info())
    state["info"] = _vod_info(master=f"{LIVE}/master.m3u8")
    assert service.refresh("member", "live-b").fallback_code == "live_stream_ended"

    # Any other resolve failure is not mistaken for the end of the broadcast.
    state["info"] = yt_dlp.utils.DownloadError("ERROR: [twitch:stream] luminafixture: Unable to download JSON metadata")
    with pytest.raises(yt_dlp.utils.DownloadError):
        service.refresh("member", "live-a")


def test_twitch_gated_no_auth_prompt() -> None:
    # Resolved but subscriber-only: every action is an explicit, distinct not-available state.
    caps = derive_media_capabilities(_vod_info(availability="subscriber_only"))
    assert (caps.can_play, caps.play_reason, caps.can_acquire, caps.acquire_reason) == (False, "subscriber_only", False, "subscriber_only")
    # Refused by the extractor: classified, and yt-dlp's login/cookie advice is never relayed.
    error = yt_dlp.utils.DownloadError(
        "ERROR: [twitch:vod] 2000000001: You must be logged into an account that has access to this subscriber-only content. "
        "Use --cookies-from-browser or --cookies for the authentication.",
    )
    detail = main_module._download_error_http_exception(error).detail
    assert detail["category"] == "sign_in_required"
    assert "cookie" not in detail["message"].lower() and "logged into an account" not in detail["message"]
    assert YtDlpService.explain_download_error(str(error)) == detail["message"]


class _Cdn(BaseHTTPRequestHandler):
    """Loopback stand-in for the public CDN: the key request redirects into private space."""

    requests: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(self.path)
        bodies = {
            "/vod/master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n360p/index.m3u8\n",
            "/vod/360p/index.m3u8": b'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-KEY:METHOD=AES-128,URI="k.bin"\n#EXTINF:2.0,\ns0.ts\n#EXT-X-ENDLIST\n',
            "/vod/360p/s0.ts": b"segment",
        }
        if self.path == "/vod/360p/k.bin":
            self.send_response(302)
            self.send_header("Location", "http://metadata.internal.example/latest/key")
            self.end_headers()
            return
        body = bodies.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *args) -> None:  # noqa: ANN002
        del args


def test_hls_redirect_policy_preserved(monkeypatch) -> None:  # noqa: ANN001
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Cdn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def to_loopback(answer, timeout, source_address):  # noqa: ANN001
        # Policy already approved this public answer; route the socket to the fixture CDN.
        del answer, source_address
        return socket.create_connection(("127.0.0.1", port), timeout=None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout)  # noqa: SLF001

    monkeypatch.setitem(network_policy.create_public_connection.__kwdefaults__, "_create_socket_func", to_loopback)
    policy = public_policy({"cdn.example": ["93.184.216.34"], "metadata.internal.example": ["169.254.169.254"]})
    master = "http://cdn.example/vod/master.m3u8"  # web port; to_loopback routes the socket to the fixture
    info = {**_vod_info(master=master), "http_headers": {}}
    service = HlsRelayService(
        resolver=None, fetcher=PublicRelayFetcher(policy=policy), policy=policy, token_factory=lambda: "vod",
        resource_token_factory=iter_factory([f"r{i}" for i in range(20)]), clock=lambda: 1_000.0,
    )
    try:
        service.register(owner_user_id="member", source_url=VOD_URL, info=info)
        media = drain(service.serve_resource("member", "vod", 1, _ids(drain(service.serve_master("member", "vod", 1)).decode(), "vod", 1)[0])).decode()
        key_id, segment_id = _ids(media, "vod", 1)
        assert drain(service.serve_resource("member", "vod", 1, segment_id)) == b"segment"
        with pytest.raises(PublicSourcePolicyError):
            service.serve_resource("member", "vod", 1, key_id)
    finally:
        server.shutdown()
    # The redirect target was denied before any request was made to it.
    assert _Cdn.requests == ["/vod/master.m3u8", "/vod/360p/index.m3u8", "/vod/360p/s0.ts", "/vod/360p/k.bin"]
    assert "/latest/key" not in _Cdn.requests
