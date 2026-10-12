"""#145: chat stays in sync with media recorded across a restart gap.

Real ffmpeg: a synthetic lavfi live source is cut into 2 s MPEG-TS segments
(media sequence k = source time 2k s = wall clock offset 2k s). A crashed run
captured segments 0-3; the resumed run follows a later playlist window. Chat is
timed by wall clock from the recording start, so the published media must keep
source time == media position on both sides of the gap, with no duplicates.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.services.live_chat_seam import LiveChatBootstrap, LiveChatPage
from app.services.live_recording_adapters import FfmpegLiveMediaSink, GuardedLiveChatCapturer, GuardedLiveHlsRunner
from app.services.live_recording_manager import RecordingContext
from tests.test_live_recording_chat_capture import _asset, _factory, _FakeFetcher, _message

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")

T0 = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture(scope="module")
def segments(tmp_path_factory):
    out = tmp_path_factory.mktemp("hls")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=25", "-f", "lavfi",
         "-i", "sine=sample_rate=48000", "-t", "48", "-c:v", "libx264", "-g", "50", "-sc_threshold", "0",
         "-c:a", "aac", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", str(out / "p.m3u8")],
        check=True,
    )
    return out


def _ctx(factory):
    return RecordingContext(
        recording_id="rec-1", user_id="user-a", source_url="https://youtube.com/watch?v=LIVE1",
        source_identity="youtube:LIVE1", format_selection={}, output_profile={}, offset_base=T0, resume=True,
        session_factory=factory, _stop=threading.Event(), _cancel=threading.Event(), _either=threading.Event(),
    )


class _Resolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        return {"manifest_url": "https://cdn.example/media.m3u8"}


class _Fetch:
    def __init__(self, segments, window):
        self.segments, self.window = segments, window

    def fetch_text(self, url, *, headers):  # noqa: ANN001
        lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{self.window[0]}"]
        for k in self.window:
            lines += ["#EXTINF:2.0,", f"p{k}.ts"]
        return "\n".join([*lines, "#EXT-X-ENDLIST", ""])

    def fetch_bytes(self, url, *, headers):  # noqa: ANN001
        return (self.segments / url.rsplit("/", 1)[-1]).read_bytes()


def _video_pts(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted(float(x) for x in out.split())


@pytest.mark.parametrize(("window", "expected_parts"), [
    (range(20, 24), [(0, 8), (40, 48)]),  # a 32 s restart gap
    (range(2, 6), [(0, 12)]),  # a quick restart relists already-captured segments
])
def test_resumed_media_keeps_chat_in_sync(segments, tmp_path, monkeypatch, window, expected_parts) -> None:
    factory = _factory()
    published = tmp_path / "published.mp4"
    monkeypatch.setattr(FfmpegLiveMediaSink, "_publish", lambda self, staged: shutil.copy(staged, published) and "item-1")
    ctx = _ctx(factory)

    def sink_factory(c):  # noqa: ANN001
        return FfmpegLiveMediaSink(c, session_factory=factory, events=None, temp_root=tmp_path, ffmpeg_path=None)

    crashed = sink_factory(ctx)  # the pre-crash run: segments 0-3, then the process dies
    for k in range(4):
        crashed.append_segment((segments / f"p{k}.ts").read_bytes(), k)
    crashed._handle.close()

    runner = GuardedLiveHlsRunner(
        resolver=_Resolver(), fetch=_Fetch(segments, window), sink_factory=sink_factory,
        max_runtime_seconds=3600, max_disk_bytes=10**9, poll_interval_seconds=0,
    )
    assert runner.run(ctx).library_item_id == "item-1"

    # Chat posted 1 s into each captured part, timed by wall clock like the capturer does.
    walls = iter([(T0 + timedelta(seconds=start + 1)).replace(tzinfo=UTC).timestamp() for start, _ in expected_parts] + [0.0])  # + the "ended" page
    pages = [LiveChatPage(actions=[_message(f"m{i}", "hi")], continuation="c", status="active") for i in range(len(expected_parts))]
    fetcher = _FakeFetcher(LiveChatBootstrap(continuation="c", headers={}, status="available"), pages)
    GuardedLiveChatCapturer(fetcher=fetcher, clock=lambda: next(walls), poll_interval_seconds=0).capture(ctx)
    chat_seconds = [event["offset_ms"] / 1000 for event in _asset(factory).events_json]

    pts = _video_pts(published)
    assert len(pts) == 25 * sum(end - start for start, end in expected_parts)  # no duplicated media
    for chat_at, (start, end) in zip(chat_seconds, expected_parts):
        assert chat_at == start + 1
        # The media plays source time `chat_at` exactly at position `chat_at`.
        part = [p for p in pts if start - 0.5 < p < end + 0.5]
        assert len(part) == 25 * (end - start)
        assert abs(part[0] - start) < 0.1 and abs(part[-1] - (end - 0.04)) < 0.1
