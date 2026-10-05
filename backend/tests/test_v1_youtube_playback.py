"""S27 — public YouTube playback: real split A/V, bounded refresh, quality switch, honest restrictions.

Hermetic: a recorded YouTube-shaped info dict points at real ffmpeg-generated
DASH (SegmentBase) media served by an in-memory upstream; no provider network.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yt_dlp

from app import main as main_module
from app.schemas import FormatSelection
from app.services.media_capabilities import derive_media_capabilities
from app.services.remote_streaming import (
    BrowserCapabilities,
    ByteRange,
    RemoteStreamingService,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
)
from app.services.yt_dlp_service import YtDlpService
from support import memory_session_factory

WATCH_URL = "https://www.youtube.com/watch?v=lumina00001"
DASH_BROWSER = BrowserCapabilities(frozenset({"dash-segment-base", "mp4-av1-aac", "m4a-aac", "webm-opus"}))


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def dash_media(tmp_path_factory) -> dict[str, bytes]:  # noqa: ANN001
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("BLOCKED: ffmpeg/ffprobe are required for the real split A/V fixture")
    work = tmp_path_factory.mktemp("s27")
    dash = ["-f", "mp4", "-movflags", "+dash+global_sidx+frag_keyframe"]
    try:
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=2", "-c:v", "libsvtav1", "-preset", "12", "-g", "24", "-an", *dash, str(work / "v1080.mp4"))
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2", "-c:v", "libsvtav1", "-preset", "12", "-g", "24", "-an", *dash, str(work / "v360.mp4"))
    except subprocess.CalledProcessError:
        pytest.skip("BLOCKED: this ffmpeg build has no libsvtav1 encoder")
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", *dash, str(work / "a.m4a"))
    return {name: (work / name).read_bytes() for name in ("v1080.mp4", "v360.mp4", "a.m4a")}


def _youtube_info(media: dict[str, bytes], signature: str = "fresh") -> dict:
    """Recorded shape of a public YouTube watch page (yt-dlp sanitized info)."""

    def fmt(format_id: str, name: str, **facts) -> dict:
        return {
            "format_id": format_id, "url": f"https://rr1.googlevideo.example/videoplayback?itag={format_id}&sig={signature}",
            "protocol": "https", "filesize": len(media[name]), "http_headers": {"User-Agent": "yt-dlp"}, **facts,
        }

    return {
        "id": "lumina00001", "title": "Public split A/V", "extractor": "youtube", "extractor_key": "Youtube",
        "webpage_url": WATCH_URL, "availability": "public", "live_status": "not_live", "duration": 2,
        "formats": [
            fmt("134", "v360.mp4", ext="mp4", container="mp4_dash", vcodec="av01.0.04M.08", acodec="none", width=640, height=360, fps=24, tbr=300),
            fmt("399", "v1080.mp4", ext="mp4", container="mp4_dash", vcodec="av01.0.08M.08", acodec="none", width=1920, height=1080, fps=24, tbr=2_500),
            fmt("140", "a.m4a", ext="m4a", container="m4a_dash", vcodec="none", acodec="mp4a.40.2", tbr=129),
        ],
    }


class Upstream:
    """In-memory googlevideo stand-in honoring byte ranges; can expire signatures."""

    def __init__(self, media: dict[str, bytes], expired: set[str] | None = None) -> None:
        self.by_itag = {"134": media["v360.mp4"], "399": media["v1080.mp4"], "140": media["a.m4a"]}
        self.expired = expired or set()
        self.opens: list[tuple[str, ByteRange | None]] = []

    def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
        del timeout_seconds
        self.opens.append((track.url, byte_range))
        if any(f"sig={sig}" in track.url for sig in self.expired):
            return UpstreamMediaResponse(403, {}, [b"expired"])
        payload = self.by_itag[re.search(r"itag=(\d+)", track.url).group(1)]
        start = byte_range.start if byte_range else 0
        end = min(byte_range.end if byte_range and byte_range.end is not None else len(payload) - 1, len(payload) - 1)
        body = payload[start : end + 1]
        return UpstreamMediaResponse(
            206 if byte_range else 200,
            {"Content-Length": str(len(body)), "Content-Range": f"bytes {start}-{end}/{len(payload)}"},
            [body],
        )


class Resolver:
    def __init__(self, info: dict) -> None:
        self.info = info
        self.calls = 0

    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        assert (source_url, owner_user_id) == (WATCH_URL, "member")
        self.calls += 1
        return self.info


def _service(media: dict[str, bytes], resolver: Resolver, upstream: Upstream) -> RemoteStreamingService:
    return RemoteStreamingService(
        resolver=resolver, reader=upstream, token_factory=lambda: "yt-stream",
        rendition_token_factory=iter(f"r{n}" for n in range(100)).__next__, clock=lambda: 1_000,
    )


def _read(spec) -> bytes:  # noqa: ANN001
    try:
        return b"".join(spec.body)
    finally:
        spec.close()


def _download_track(service: RemoteStreamingService, generation: int, resource: str, length: int) -> bytes:
    """Fetch a whole DASH track the way dash.js does: bounded explicit byte ranges."""

    chunk, out = 512 * 1024, b""
    for start in range(0, length, chunk):
        spec = service.serve_dash("member", "yt-stream", generation, resource, f"bytes={start}-{min(start + chunk, length) - 1}")
        assert spec.status_code == 206
        out += _read(spec)
    return out


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_youtube_separate_av_real_fixture(dash_media, tmp_path) -> None:  # noqa: ANN001
    info = _youtube_info(dash_media)
    caps = derive_media_capabilities(info)
    assert (caps.provider, caps.lifecycle, caps.can_play, caps.can_acquire) == ("youtube", "vod", True, True)

    upstream = Upstream(dash_media)
    service = _service(dash_media, Resolver(info), upstream)
    playback = service.register(owner_user_id="member", source_url=WATCH_URL, info=info, browser_capabilities=DASH_BROWSER)

    # Auto adapts across the whole AV1 ladder, up to the best quality; the ladder's actual facts are exposed.
    assert (playback.status, playback.transport, playback.seekable, playback.has_audio) == ("ready", "dash", True, True)
    assert (playback.selected_rendition_id, playback.auto_available) == ("auto", True)
    best = playback.renditions[-1]
    assert (best.width, best.height, best.video_codec, best.audio_codec) == (1920, 1080, "av1", "aac")
    assert [item.display_label for item in playback.renditions] == ["360p", "1080p"]

    manifest = _read(service.serve_dash("member", "yt-stream", 1, "manifest.mpd")).decode()
    assert "googlevideo" not in manifest and "sig=" not in manifest
    assert 'height="360"' in manifest and 'height="1080"' in manifest and 'mediaPresentationDuration="PT2.0' in manifest

    video = _download_track(service, 1, "video-1", len(dash_media["v1080.mp4"]))
    audio = _download_track(service, 1, "audio", len(dash_media["a.m4a"]))
    assert video == dash_media["v1080.mp4"] and audio == dash_media["a.m4a"]
    (tmp_path / "v.mp4").write_bytes(video)
    (tmp_path / "a.m4a").write_bytes(audio)
    muxed = tmp_path / "muxed.mp4"
    _ffmpeg("-i", str(tmp_path / "v.mp4"), "-i", str(tmp_path / "a.m4a"), "-c", "copy", str(muxed))
    probed = _probe(muxed)
    streams = {stream["codec_type"]: stream for stream in probed["streams"]}
    assert (streams["video"]["codec_name"], streams["video"]["width"], streams["video"]["height"]) == ("av1", 1920, 1080)
    assert streams["audio"]["codec_name"] == "aac"
    assert float(probed["format"]["duration"]) == pytest.approx(2.0, abs=0.1)


def test_expired_transport_refresh_once(dash_media) -> None:  # noqa: ANN001
    def audio_only(signature: str) -> dict:
        info = _youtube_info(dash_media, signature=signature)
        return {**info, "formats": info["formats"][-1:]}

    resolver = Resolver(audio_only("fresh"))
    upstream = Upstream(dash_media, expired={"stale"})
    service = _service(dash_media, resolver, upstream)
    service.register(owner_user_id="member", source_url=WATCH_URL, info=audio_only("stale"))

    # An expired signature refreshes once and serves the SAME requested position.
    spec = service.serve_content("member", "yt-stream", "bytes=4096-8191")
    assert spec.status_code == 206 and len(_read(spec)) == 4096
    assert resolver.calls == 1
    assert [byte_range for _, byte_range in upstream.opens] == [ByteRange(4096, 8191), ByteRange(4096, 8191)]

    # If the refreshed transport is also rejected, playback fails once — no loop.
    resolver.info = audio_only("stale")
    service.refresh("member", "yt-stream")
    calls = resolver.calls
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_content("member", "yt-stream", "bytes=0-1023")
    assert resolver.calls == calls + 1


def test_quality_switch_preserves_checkpoint(dash_media) -> None:  # noqa: ANN001
    info = _youtube_info(dash_media)
    service = _service(dash_media, Resolver(info), Upstream(dash_media))
    playback = service.register(owner_user_id="member", source_url=WATCH_URL, info=info, browser_capabilities=DASH_BROWSER)
    low = next(item for item in playback.renditions if item.height == 360)

    switched = service.select_rendition("member", "yt-stream", low.rendition_id)

    # Same opaque session (the player keeps its checkpoint and owns one element),
    # a fresh generation, and the descriptor reports the quality actually chosen.
    assert switched.stream_id == playback.stream_id
    assert switched.selected_rendition_id == low.rendition_id
    assert switched.playback_url == "/api/remote-streams/yt-stream/dash/2/manifest.mpd"
    manifest = _read(service.serve_dash("member", "yt-stream", 2, "manifest.mpd")).decode()
    assert 'height="360"' in manifest and 'height="1080"' not in manifest
    assert manifest.count('contentType="audio"') == 1


RESTRICTED = {
    "sign_in_required": [
        "ERROR: [youtube] lumina00001: Sign in to confirm your age. This video may be inappropriate for some users. Use --cookies-from-browser or --cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies",
        "ERROR: [youtube] lumina00001: Join this channel to get access to members-only content like this video, and other exclusive perks.",
        "ERROR: [youtube] lumina00001: Private video. Sign in if you've been granted access to this video. Use --cookies-from-browser or --cookies for the authentication.",
    ],
    "region_blocked": ["ERROR: [youtube] lumina00001: The uploader has not made this video available in your country. This video is available in CA. You might want to use a VPN or a proxy server (with --proxy) to workaround."],
    "removed": ["ERROR: [youtube] lumina00001: Video unavailable. This video has been removed by the uploader"],
    "rate_limited": ["ERROR: [youtube] lumina00001: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."],
}


def _session():
    return memory_session_factory()()


@pytest.mark.parametrize(("category", "message"), [(c, m) for c, ms in RESTRICTED.items() for m in ms])
def test_restricted_source_honest(category: str, message: str) -> None:
    class RejectingYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            raise yt_dlp.utils.DownloadError(message)

    with pytest.raises(yt_dlp.utils.DownloadError) as error:
        YtDlpService(_session(), ydl_factory=RejectingYDL).preview(WATCH_URL, lazy_playlist=False, format_selection=FormatSelection(preset="best"))
    detail = main_module._download_error_http_exception(error.value).detail
    assert detail["category"] == category
    lowered = detail["message"].lower()
    assert "cookie" not in lowered and "--" not in lowered and "proxy" not in lowered


def test_restricted_availability_disables_every_action_without_sign_in() -> None:
    info = {"extractor_key": "Youtube", "availability": "needs_auth", "formats": [{"protocol": "https", "url": "https://rr1.googlevideo.example/x", "acodec": "mp4a"}]}
    caps = derive_media_capabilities(info)
    assert (caps.can_play, caps.play_reason, caps.can_acquire, caps.acquire_reason) == (False, "sign_in_required", False, "sign_in_required")
    members = derive_media_capabilities({**info, "availability": "subscriber_only"})
    assert (members.can_play, members.play_reason) == (False, "subscriber_only")


def test_unclassified_error_never_relays_cookie_advice() -> None:
    explained = YtDlpService.explain_download_error("ERROR: [example] 1: This content is odd. Use --cookies-from-browser or --cookies for the authentication.")
    assert explained == "ERROR: [example] 1: This content is odd"
