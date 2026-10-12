"""Guarded live edge-following media recorder (issue #97, review fixes 1a/1b/2/5).

The recorder must FOLLOW the live edge — re-fetch the media playlist through the
guarded transport and append newly-listed segments until true source-end
(#EXT-X-ENDLIST) — and report source-end HONESTLY: it reaches ``reached_source_end``
only on ENDLIST, so a deliberate stop, a runtime/disk ceiling, or an interruption
yields a usable-but-partial capture that is never a false complete. A fake guarded
fetcher/resolver/sink stands in so no live YouTube is touched.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from app.services.live_recording_adapters import (
    GuardedLiveHlsRunner,
    LiveMediaCancelled,
)
from app.services.live_recording_manager import RecordingContext


def _ctx(*, stop=None, cancel=None, either=None):
    stop = stop or threading.Event()
    cancel = cancel or threading.Event()
    either = either or threading.Event()
    return RecordingContext(
        recording_id="rec-1",
        user_id="user-a",
        source_url="https://youtube.com/watch?v=LIVE1",
        source_identity="youtube:LIVE1",
        format_selection={},
        output_profile={},
        offset_base=datetime.now(UTC).replace(tzinfo=None),
        resume=False,
        session_factory=lambda: None,
        _stop=stop,
        _cancel=cancel,
        _either=either,
    )


MEDIA = "https://cdn.example/media.m3u8"


def _abs(name):
    # The recorder resolves relative segment URIs against the playlist base.
    return f"https://cdn.example/{name}"


def _seg_bytes(name):
    return f"BYTES:{_abs(name)}".encode()


def _media_playlist(segments, *, ended=False, media_seq=0):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4", f"#EXT-X-MEDIA-SEQUENCE:{media_seq}"]
    for seg in segments:
        lines.append("#EXTINF:4.0,")
        lines.append(seg)
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class _FakeResolver:
    def __init__(self, info):
        self._info = info

    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        return self._info


class _FakeFetch:
    """Guarded-fetch stand-in: per-url playlist queues + per-url segment bytes.

    Each ``fetch_text`` consumes the next scripted refresh; once a url's queue is
    exhausted the fetch raises, standing in for a live edge that stopped resolving.
    """

    def __init__(self, text_by_url):
        self._text_by_url = {url: list(pages) for url, pages in text_by_url.items()}
        self.segment_reads: list[str] = []

    def fetch_text(self, url, *, headers):  # noqa: ANN001
        pages = self._text_by_url.get(url, [])
        if not pages:
            raise RuntimeError(f"no more refreshes for {url}")
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def fetch_bytes(self, url, *, headers):  # noqa: ANN001
        self.segment_reads.append(url)
        return f"BYTES:{url}".encode()


class _FakeSink:
    def __init__(self):
        self.init: list[bytes] = []
        self.segments: list[bytes] = []
        self.finalized = False
        self.discarded = False

    def append_init(self, data):  # noqa: ANN001
        self.init.append(data)

    def append_segment(self, data, sequence=None):  # noqa: ANN001
        self.segments.append(data)

    def finalize(self):
        self.finalized = True
        return "item-1" if self.segments or self.init else None

    def discard(self):
        self.discarded = True


def _runner(fetch, sink, **kwargs):
    resolver = _FakeResolver({"manifest_url": MEDIA})
    sinks = []

    def sink_factory(ctx):
        sinks.append(sink)
        return sink

    runner = GuardedLiveHlsRunner(
        resolver=resolver,
        fetch=fetch,
        sink_factory=sink_factory,
        poll_interval_seconds=0,
        max_runtime_seconds=kwargs.pop("max_runtime_seconds", 3600),
        max_disk_bytes=kwargs.pop("max_disk_bytes", 10_000_000),
        max_consecutive_failures=kwargs.pop("max_consecutive_failures", 4),
        clock=kwargs.pop("clock", None),
    )
    return runner


def test_follows_the_edge_across_refreshes_until_endlist() -> None:
    fetch = _FakeFetch({
        MEDIA: [
            _media_playlist(["s1.ts", "s2.ts"], media_seq=0),
            _media_playlist(["s2.ts", "s3.ts"], media_seq=1),
            _media_playlist(["s3.ts", "s4.ts"], ended=True, media_seq=2),
        ],
    })
    sink = _FakeSink()
    outcome = _runner(fetch, sink).run(_ctx())
    # Every distinct segment across the advancing edge is captured exactly once.
    assert fetch.segment_reads == [_abs("s1.ts"), _abs("s2.ts"), _abs("s3.ts"), _abs("s4.ts")]
    assert len(sink.segments) == 4
    assert outcome.reached_source_end is True
    assert outcome.library_item_id == "item-1"
    assert sink.finalized is True


def test_deliberate_stop_yields_a_usable_partial_not_a_false_complete() -> None:
    stop = threading.Event()
    stop.set()  # ask to stop before the run; the first refresh is still captured
    fetch = _FakeFetch({
        MEDIA: [
            _media_playlist(["s1.ts", "s2.ts"], media_seq=0),
            _media_playlist(["s3.ts"], ended=True, media_seq=1),
        ],
    })
    sink = _FakeSink()
    outcome = _runner(fetch, sink).run(_ctx(stop=stop))
    # Captured the first window then stopped BEFORE ENDLIST: usable, but honestly
    # not a complete capture.
    assert sink.segments == [_seg_bytes("s1.ts"), _seg_bytes("s2.ts")]
    assert outcome.reached_source_end is False
    assert outcome.library_item_id == "item-1"
    assert sink.discarded is False


def test_abrupt_cancel_discards_without_finalizing() -> None:
    cancel = threading.Event()
    cancel.set()
    fetch = _FakeFetch({MEDIA: [_media_playlist(["s1.ts"], media_seq=0)]})
    sink = _FakeSink()
    with pytest.raises(LiveMediaCancelled):
        _runner(fetch, sink).run(_ctx(cancel=cancel))
    assert sink.finalized is False
    assert sink.discarded is True


def test_runtime_ceiling_finalizes_a_partial() -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    fetch = _FakeFetch({
        MEDIA: [
            _media_playlist(["s1.ts"], media_seq=0),
            _media_playlist(["s2.ts"], media_seq=1),
            _media_playlist(["s3.ts"], ended=True, media_seq=2),
        ],
    })
    sink = _FakeSink()
    outcome = _runner(fetch, sink, max_runtime_seconds=10, clock=lambda: next(ticks)).run(_ctx())
    assert outcome.reached_source_end is False
    assert outcome.library_item_id == "item-1"
    assert len(sink.segments) >= 1


def test_disk_ceiling_finalizes_a_partial() -> None:
    fetch = _FakeFetch({
        MEDIA: [
            _media_playlist(["s1.ts", "s2.ts", "s3.ts"], media_seq=0),
            _media_playlist(["s4.ts"], ended=True, media_seq=1),
        ],
    })
    sink = _FakeSink()
    # Each fake segment is ~len("BYTES:sN.ts") bytes (~11); cap after the first.
    outcome = _runner(fetch, sink, max_disk_bytes=10).run(_ctx())
    assert outcome.reached_source_end is False
    assert outcome.library_item_id == "item-1"


def test_encrypted_playlist_fails_closed() -> None:
    encrypted = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.example/key.bin"\n'
        "#EXTINF:4.0,\ns1.ts\n#EXT-X-ENDLIST\n"
    )
    fetch = _FakeFetch({MEDIA: [encrypted]})
    sink = _FakeSink()
    outcome = _runner(fetch, sink).run(_ctx())
    # An encrypted playlist would need an out-of-guard key/decrypt path; refuse
    # rather than record garbage, mirroring GuardedHlsFD.
    assert outcome.library_item_id is None
    assert sink.discarded is True


def test_source_end_with_no_media_is_a_failure() -> None:
    fetch = _FakeFetch({MEDIA: [_media_playlist([], ended=True, media_seq=0)]})
    sink = _FakeSink()
    outcome = _runner(fetch, sink).run(_ctx())
    assert outcome.library_item_id is None
    assert outcome.reached_source_end is False


def test_persistent_fetch_failure_after_capture_keeps_the_partial() -> None:
    # First refresh succeeds, then the media playlist stops resolving.
    fetch = _FakeFetch({MEDIA: [_media_playlist(["s1.ts"], media_seq=0)]})
    # After the single page is consumed, fetch_text raises (no more pages).
    sink = _FakeSink()
    outcome = _runner(fetch, sink, max_consecutive_failures=2).run(_ctx())
    assert outcome.reached_source_end is False
    assert outcome.library_item_id == "item-1"
    assert sink.segments == [_seg_bytes("s1.ts")]


def test_master_playlist_selects_a_variant_media_playlist() -> None:
    master = "https://cdn.example/master.m3u8"
    master_text = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n'
        "media.m3u8\n"
    )
    fetch = _FakeFetch({
        master: [master_text],
        MEDIA: [_media_playlist(["s1.ts"], ended=True, media_seq=0)],
    })
    sink = _FakeSink()
    resolver = _FakeResolver({"manifest_url": master})
    runner = GuardedLiveHlsRunner(
        resolver=resolver, fetch=fetch, sink_factory=lambda ctx: sink,
        poll_interval_seconds=0, max_runtime_seconds=3600, max_disk_bytes=10_000_000,
    )
    outcome = runner.run(_ctx())
    assert outcome.reached_source_end is True
    assert sink.segments == [_seg_bytes("s1.ts")]
