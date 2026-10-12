"""S31 — scoped guarded Kick playback and acquisition.

Kick media is IVS-style HLS. It plays only through the guarded relay and saves
only through GuardedHlsFD, admitted by provider + lifecycle (never a generic
HLS widening). Extraction is anonymous: no curl_cffi, cookies or tokens.
Record-from-now (#142) captures the live edge through the same guarded fetch;
scheduling stays off because yt-dlp's Kick extractor exposes no upcoming broadcasts.

Fixtures are recorded shapes from a 2026-09 manual smoke against public Kick
(VOD: EVENT playlist with EXT-X-TWITCH-* tags, TS segments; live: IVS window
with EXT-X-PREFETCH hints and DATERANGE metadata). The live master/window shapes
were re-recorded on 2026-09-25 from a public Kick broadcast (#142), with signed
segment tokens, session data and hosts replaced by placeholders.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from app.services.hls_relay import HlsRelayService
from app.services.hls_relay_support import supports_hls_relay
from app.services.live_hls_relay import LiveHlsRelayService
from app.services.media_capabilities import derive_media_capabilities
from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicyError
from app.services.storage_routing import StorageRule, decide
from app.services.job_manager import _actual_height
from app.services.hls_relay_support import derive_provider
from tests.test_hls_relay import FakeRelayFetcher, drain, iter_factory, public_policy
from tests.test_twitch_vod_acquisition_guarded import LoopbackHlsPolicy, _acquire, hls_fixture

VOD_PAGE = "https://kick.com/luminafixture/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb"
STREAM = "https://stream.kick.example/ivs/v1/1/abc/2026/9/23/21/41/xyz"
LIVE = "https://fa723fc1b171.cloudfront.hls.live-video.example"


def _vod_info(master: str = f"{STREAM}/media/hls/master.m3u8", **extra) -> dict:
    return {
        "id": "5c697a87-afce-4256-b01f-3c8fe71ef5cb", "title": "Kick VOD", "extractor": "kick:vod", "extractor_key": "KickVOD",
        "channel": "luminafixture", "webpage_url": VOD_PAGE, "duration": 8.0,
        "formats": [{"format_id": "720p60", "protocol": "m3u8_native", "url": master.replace("master.m3u8", "720p60/playlist.m3u8"),
                     "manifest_url": master, "ext": "mp4", "vcodec": "avc1.4D401F", "acodec": "mp4a.40.2", "height": 720}],
        **extra,
    }


def _live_info(master: str = f"{LIVE}/api/video/v1/channel.m3u8") -> dict:
    return {
        "id": "92722911-live", "extractor": "kick:live", "extractor_key": "Kick", "channel": "luminafixture", "is_live": True,
        "webpage_url": "https://kick.com/luminafixture",
        "formats": [{"format_id": "720p60", "protocol": "m3u8_native", "url": f"{LIVE}/v1/playlist/720.m3u8", "manifest_url": master,
                     "ext": "mp4", "vcodec": "avc1.4D401F", "acodec": "mp4a.40.2", "height": 720}],
    }


def _ids(playlist: str, stream_id: str) -> list[str]:
    return re.findall(rf"/api/remote-streams/{stream_id}/relay/1/r/([\w-]+)", playlist)


@pytest.fixture(scope="module")
def kick_vod(tmp_path_factory) -> dict[str, bytes]:  # noqa: ANN001
    if not shutil.which("ffmpeg"):
        pytest.skip("BLOCKED: ffmpeg is required for the real Kick VOD fixture")
    out = tmp_path_factory.mktemp("s31")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=8", "-f", "lavfi",
        "-i", "sine=frequency=440:duration=8", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac",
        "-shortest", "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "event", "-hls_segment_filename", str(out / "%d.ts"),
        str(out / "playlist.m3u8"),
    ], check=True, capture_output=True)
    files = {path.name: path.read_bytes() for path in out.iterdir()}
    # Kick's VOD playlist decorations, as recorded.
    files["playlist.m3u8"] = files["playlist.m3u8"].replace(
        b"#EXT-X-PLAYLIST-TYPE:EVENT", b"#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-TWITCH-ELAPSED-SECS:0.000\n#EXT-X-TWITCH-TOTAL-SECS:8.000",
    )
    return files


def test_kick_vod_real_hls_fixture(kick_vod, tmp_path) -> None:  # noqa: ANN001
    caps = derive_media_capabilities(_vod_info())
    assert (caps.provider, caps.lifecycle, caps.can_play, caps.can_acquire, caps.can_record) == ("kick", "vod", True, True, False)

    master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS="avc1.4D401F,mp4a.40.2",RESOLUTION=320x180\n720p60/playlist.m3u8\n'
    responses = {f"{STREAM}/media/hls/master.m3u8": (200, {}, master)}
    responses |= {f"{STREAM}/media/hls/720p60/{name}": (200, {}, body) for name, body in kick_vod.items()}
    service = HlsRelayService(
        resolver=None, fetcher=FakeRelayFetcher(responses), policy=public_policy({"stream.kick.example": ["151.101.1.1"]}),
        token_factory=lambda: "kick-vod", resource_token_factory=iter_factory([f"r{i}" for i in range(50)]), clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="member", source_url=VOD_PAGE, info=_vod_info())
    assert (playback.status, playback.seekable, playback.live) == ("ready", True, False)
    media = drain(service.serve_resource("member", "kick-vod", 1, _ids(drain(service.serve_master("member", "kick-vod", 1)).decode(), "kick-vod")[0])).decode()
    assert "kick.example" not in media and sum(float(v) for v in re.findall(r"#EXTINF:([\d.]+)", media)) == pytest.approx(8.0, abs=0.1)
    segments = _ids(media, "kick-vod")
    assert drain(service.serve_resource("member", "kick-vod", 1, segments[-1])) == kick_vod[f"{len(segments) - 1}.ts"]

    # Finite save: yt-dlp's real native HLS downloader through PolicyYoutubeDL/GuardedHlsFD.
    policy = LoopbackHlsPolicy()
    with hls_fixture({f"/{name}": body for name, body in kick_vod.items()}) as (port, handler):
        info = _vod_info()
        info["formats"][0].update(url=f"http://127.0.0.1:{port}/playlist.m3u8", manifest_url=f"http://127.0.0.1:{port}/playlist.m3u8")
        info["webpage_url"] = VOD_PAGE
        _acquire(tmp_path, policy, info)
    saved = tmp_path / f"{info['id']}.mp4"
    assert saved.read_bytes() == b"".join(kick_vod[f"{i}.ts"] for i in range(len(segments)))
    assert set(policy.connected) == {"127.0.0.1"}
    assert {path for path, _ in handler.seen} == {"/playlist.m3u8", *(f"/{i}.ts" for i in range(len(segments)))}


def test_kick_live_guarded_fixture() -> None:
    caps = derive_media_capabilities(_live_info())
    assert (caps.lifecycle, caps.can_play, caps.can_acquire) == ("live", True, False)
    # Record-from-now (#142) is offered from the live edge only; the download gate agrees.
    assert (caps.can_record, caps.record_reason, caps.from_start_available, caps.can_schedule) == (True, None, False, False)
    with PolicyYoutubeDL({"quiet": True}, policy=public_policy({"fa723fc1b171.cloudfront.hls.live-video.example": ["151.101.1.1"]})) as ydl:
        ydl.validate_download_transport({**_live_info(), **_live_info()["formats"][0]})

    def window(first: int) -> bytes:
        lines = [
            "#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:6", f"#EXT-X-MEDIA-SEQUENCE:{first}",
            f"#EXT-X-NET-LIVE-VIDEO-LIVE-SEQUENCE:{first}",
            f'#EXT-X-DATERANGE:ID="source-{first}",CLASS="live-video-net-stream-source",START-DATE="2026-09-25T04:52:43.758Z",END-ON-NEXT=YES',
        ]
        for n in range(first, first + 3):
            lines += ["#EXT-X-PROGRAM-DATE-TIME:2026-09-25T04:52:43.758Z", "#EXTINF:1.967,live", f"{LIVE}/v1/segment/{n}.ts"]
        lines += [f"#EXT-X-PREFETCH:{LIVE}/v1/segment/{first + 3}.ts", f"#EXT-X-PREFETCH:{LIVE}/v1/segment/{first + 4}.ts"]
        return "\n".join(lines).encode()

    windows = iter([window(894), window(895)])

    class LiveFetcher(FakeRelayFetcher):
        def fetch(self, url, **kwargs):  # noqa: ANN001, ANN003
            if url.endswith("/v1/playlist/720.m3u8"):
                self.responses[url] = (200, {}, next(windows))
            return super().fetch(url, **kwargs)

    master = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n{LIVE}/v1/playlist/720.m3u8\n".encode()
    responses = {f"{LIVE}/api/video/v1/channel.m3u8": (200, {}, master), f"{LIVE}/v1/segment/897.ts": (200, {}, b"edge-ts")}
    service = LiveHlsRelayService(
        resolver=None, fetcher=LiveFetcher(responses), policy=public_policy({"fa723fc1b171.cloudfront.hls.live-video.example": ["151.101.1.1"]}),
        token_factory=lambda: "kick-live", resource_token_factory=iter_factory([f"r{i}" for i in range(50)]), clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="member", source_url="https://kick.com/luminafixture", info=_live_info())
    assert (playback.live, playback.seekable) == (True, False)
    media_id = _ids(drain(service.serve_master("member", "kick-live", 1)).decode(), "kick-live")[0]
    first = drain(service.serve_resource("member", "kick-live", 1, media_id)).decode()
    second = drain(service.serve_resource("member", "kick-live", 1, media_id)).decode()
    assert "MEDIA-SEQUENCE:894" in first and "MEDIA-SEQUENCE:895" in second
    # Prefetch hints name raw future addresses: dropped, never relayed; no address leaks.
    assert "PREFETCH" not in first + second and "live-video.example" not in first + second
    assert drain(service.serve_resource("member", "kick-live", 1, _ids(second, "kick-live")[-1])) == b"edge-ts"


def test_kick_malicious_manifest_failclosed() -> None:
    hostile = {
        "loopback segment": b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nhttp://127.0.0.1/steal.ts\n#EXT-X-ENDLIST\n",
        "metadata key": b'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-KEY:METHOD=AES-128,URI="http://169.254.169.254/latest"\n#EXTINF:2.0,\n0.ts\n#EXT-X-ENDLIST\n',
    }
    master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n720p60/playlist.m3u8\n"
    for playlist in hostile.values():
        responses = {f"{STREAM}/media/hls/master.m3u8": (200, {}, master), f"{STREAM}/media/hls/720p60/playlist.m3u8": (200, {}, playlist)}
        fetcher = FakeRelayFetcher(responses)
        service = HlsRelayService(
            resolver=None, fetcher=fetcher, policy=public_policy({"stream.kick.example": ["151.101.1.1"]}),
            token_factory=lambda: "kick-vod", resource_token_factory=iter_factory([f"r{i}" for i in range(9)]), clock=lambda: 1_000.0,
        )
        service.register(owner_user_id="member", source_url=VOD_PAGE, info=_vod_info())
        with pytest.raises(PublicSourcePolicyError):
            service.serve_resource("member", "kick-vod", 1, _ids(drain(service.serve_master("member", "kick-vod", 1)).decode(), "kick-vod")[0])
        assert not any("127.0.0.1" in url or "169.254" in url for url in fetcher.calls)

    # Scoped admission, not a generic widening: the same HLS from a generic site or
    # a lookalike extractor is neither relayed nor downloadable over native HLS.
    for identity in ({"extractor": "generic", "extractor_key": "Generic"}, {"extractor": "KickStarter", "extractor_key": "KickStarter"}):
        info = {**_vod_info(), **identity}
        assert supports_hls_relay(info, derive_provider(info), "vod") is False
        with PolicyYoutubeDL({"quiet": True}, policy=public_policy({"stream.kick.example": ["151.101.1.1"]})) as ydl:
            with pytest.raises(PublicSourcePolicyError):
                ydl.validate_download_transport({**info, **info["formats"][0]})


def test_kick_record_routing_actual_quality() -> None:
    rules = [
        StorageRule(id="kick-hd", priority=1, sources=["kick"], min_height=1080, target_root_id="uhd", relative_template="{source}/{height}"),
        StorageRule(id="kick-sd", priority=2, sources=["kick"], max_height=1079, target_root_id="main", relative_template="{source}/{height}"),
    ]
    # "Best" was requested; the finished entry actually resolved to 720p60.
    entry = {**_vod_info(), "requested_downloads": [{"height": 720, "format_id": "720p60"}], "height": None}
    route = decide(rules, "default", 1, derive_provider(entry), "video", _actual_height(entry))
    assert (route.rule_id, route.root_id, route.folder) == ("kick-sd", "main", "kick/720p")


def _ivs_window(first: int, *, count: int = 3, ended: bool = False) -> str:
    """A Kick (IVS) live media playlist, tag-for-tag like the 2026-09-25 recording."""
    lines = [
        "#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:6", f"#EXT-X-MEDIA-SEQUENCE:{first}",
        f"#EXT-X-NET-LIVE-VIDEO-LIVE-SEQUENCE:{first}", f"#EXT-X-NET-LIVE-VIDEO-ELAPSED-SECS:{first}.000",
        f"#EXT-X-NET-LIVE-VIDEO-TOTAL-SECS:{first + 31}.000",
        f'#EXT-X-DATERANGE:ID="playlist-creation-{first}",CLASS="timestamp",START-DATE="2026-09-25T14:06:22.471Z",'
        'END-ON-NEXT=YES,X-SERVER-TIME="1790345182.47"',
        '#EXT-X-DATERANGE:ID="source-1790345151",CLASS="live-video-net-stream-source",START-DATE="2026-09-25T14:05:51.556Z",'
        'END-ON-NEXT=YES,X-NET-LIVE-VIDEO-STREAM-SOURCE="live"',
    ]
    for n in range(first, first + count):
        lines += [f"#EXT-X-PROGRAM-DATE-TIME:2026-09-25T14:05:{n % 60:02d}.556Z", "#EXTINF:1.000,live", f"{LIVE}/v1/segment/{n}.ts?dna=tok"]
    if ended:
        lines.append("#EXT-X-ENDLIST")
    else:
        lines += [f"#EXT-X-PREFETCH:{LIVE}/v1/segment/{n}.ts?dna=tok" for n in (first + count, first + count + 1)]
    return "\n".join(lines) + "\n"


# Kick's IVS master lists its lowest rendition first; the recorder must still take the best.
IVS_MASTER = "\n".join([
    "#EXTM3U",
    '#EXT-X-SESSION-DATA:DATA-ID="NODE",VALUE="node.example"',
    '#EXT-X-SESSION-DATA:DATA-ID="FUTURE",VALUE="true"',
    '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="160p30",NAME="160p",AUTOSELECT=YES,DEFAULT=YES',
    '#EXT-X-STREAM-INF:BANDWIDTH=230000,RESOLUTION=284x160,CODECS="avc1.4D401F,mp4a.40.2",VIDEO="160p30",FRAME-RATE=30.000',
    f"{LIVE}/v1/playlist/160.m3u8",
    '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="chunked",NAME="1080p60",AUTOSELECT=YES,DEFAULT=YES',
    '#EXT-X-STREAM-INF:BANDWIDTH=7006652,RESOLUTION=1920x1080,CODECS="avc1.4D402A,mp4a.40.2",VIDEO="chunked",FRAME-RATE=60.000',
    f"{LIVE}/v1/playlist/1080.m3u8",
    '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="720p60",NAME="720p60",AUTOSELECT=YES,DEFAULT=YES',
    '#EXT-X-STREAM-INF:BANDWIDTH=3422999,RESOLUTION=1280x720,CODECS="avc1.4D401F,mp4a.40.2",VIDEO="720p60",FRAME-RATE=60.000',
    f"{LIVE}/v1/playlist/720.m3u8",
]) + "\n"


def test_kick_live_record_capture() -> None:
    import threading

    from app.services.live_recording_adapters import GuardedLiveHlsRunner
    from app.services.live_recording_manager import RecordingContext

    master_url = f"{LIVE}/api/video/v1/channel.m3u8"
    # Connect-time window, a one-segment advance (overlap deduped), then the broadcast ends.
    pages = {master_url: [IVS_MASTER], f"{LIVE}/v1/playlist/1080.m3u8": [_ivs_window(29322), _ivs_window(29323), _ivs_window(29325, ended=True)]}
    fetched: list[str] = []

    class Fetch:
        current = ""

        def fetch_text(self, url, *, headers):  # noqa: ANN001
            self.current = pages[url].pop(0)
            return self.current

        def fetch_bytes(self, url, *, headers):  # noqa: ANN001
            # Only listed segments are fetched, never a prefetch hint for a future one.
            assert f"\n{url}\n" in self.current and f"PREFETCH:{url}" not in self.current, url
            fetched.append(url)
            return url.encode()

    class Sink:
        def append_init(self, data):  # noqa: ANN001
            raise AssertionError("IVS TS has no init segment")

        def append_segment(self, data, sequence=None):  # noqa: ANN001
            pass

        def finalize(self):
            return "kick-item"

        def discard(self):
            raise AssertionError("a captured Kick recording is never discarded")

    class Resolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            return _live_info(master_url)

    stop, cancel, either = threading.Event(), threading.Event(), threading.Event()
    ctx = RecordingContext(
        recording_id="rec-kick", user_id="member", source_url="https://kick.com/luminafixture", source_identity="kick:92722911-live",
        format_selection={}, output_profile={}, offset_base=None, resume=False, session_factory=lambda: None,
        _stop=stop, _cancel=cancel, _either=either,
    )
    outcome = GuardedLiveHlsRunner(
        resolver=Resolver(), fetch=Fetch(), sink_factory=lambda _ctx: Sink(), poll_interval_seconds=0,
        max_runtime_seconds=3600, max_disk_bytes=10_000_000,
    ).run(ctx)

    # The 1080p variant, every segment once and in order, through to source-end.
    assert fetched == [f"{LIVE}/v1/segment/{n}.ts?dna=tok" for n in range(29322, 29328)]
    assert (outcome.library_item_id, outcome.reached_source_end, outcome.end_reason) == ("kick-item", True, "source_ended")
    assert (outcome.from_start_supported, outcome.began_at_source_start) == (False, False)
