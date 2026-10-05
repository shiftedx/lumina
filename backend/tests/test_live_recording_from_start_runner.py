"""Guarded from-start decision in the edge-following HLS runner (issue #98).

The runner honors the from-start member intent through the SAME guarded transport
as #97: it refuses to silently record the edge when the saved intent forbids it,
and it answers "did capture begin at the source start?" honestly from the first
media playlist's MEDIA-SEQUENCE — never claiming from-start when the beginning was
not actually available.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from app.services.live_recording_adapters import GuardedLiveHlsRunner
from app.services.live_recording_manager import RecordingContext


MEDIA = "https://cdn.example/media.m3u8"


def _ctx(*, start_intent, fallback_policy="allow_live_edge", from_start_supported=None):
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
        _stop=threading.Event(),
        _cancel=threading.Event(),
        _either=threading.Event(),
        start_intent=start_intent,
        fallback_policy=fallback_policy,
        from_start_supported=from_start_supported,
    )


def _media_playlist(segments, *, ended=False, media_seq=0):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4"]
    if media_seq is not None:
        # ``media_seq=None`` omits the tag entirely so a test can exercise an
        # absent MEDIA-SEQUENCE (which must NOT be assumed to mean "began at 0").
        lines.append(f"#EXT-X-MEDIA-SEQUENCE:{media_seq}")
    for seg in segments:
        lines.append("#EXTINF:4.0,")
        lines.append(seg)
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class _FakeResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        return {"manifest_url": MEDIA}


class _FakeFetch:
    def __init__(self, pages):
        self._pages = list(pages)

    def fetch_text(self, url, *, headers):  # noqa: ANN001
        if not self._pages:
            raise RuntimeError("no more refreshes")
        return self._pages.pop(0)

    def fetch_bytes(self, url, *, headers):  # noqa: ANN001
        return f"BYTES:{url}".encode()


class _FakeSink:
    def __init__(self):
        self.segments = []
        self.finalized = False
        self.discarded = False

    def append_init(self, data):  # noqa: ANN001
        pass

    def append_segment(self, data, sequence=None):  # noqa: ANN001
        self.segments.append(data)

    def finalize(self):
        self.finalized = True
        return "item-1" if self.segments else None

    def discard(self):
        self.discarded = True


def _runner(pages, sink):
    return GuardedLiveHlsRunner(
        resolver=_FakeResolver(),
        fetch=_FakeFetch(pages),
        sink_factory=lambda ctx: sink,
        poll_interval_seconds=0,
        max_runtime_seconds=3600,
        max_disk_bytes=10_000_000,
        max_consecutive_failures=4,
    )


def test_from_start_from_sequence_zero_to_end_is_began_and_complete() -> None:
    sink = _FakeSink()
    pages = [_media_playlist(["s1.ts", "s2.ts"], ended=True, media_seq=0)]
    outcome = _runner(pages, sink).run(
        _ctx(start_intent="from_start", from_start_supported=True)
    )
    assert outcome.began_at_source_start is True
    assert outcome.reached_source_end is True
    assert outcome.history_complete is True


def test_from_start_supported_but_edge_past_zero_is_not_a_beginning() -> None:
    # Supported, but the earliest available segment is past the broadcast start:
    # the beginning is genuinely missing, so it is NOT reported as from-start.
    sink = _FakeSink()
    pages = [_media_playlist(["s9.ts"], ended=True, media_seq=42)]
    outcome = _runner(pages, sink).run(
        _ctx(start_intent="from_start", from_start_supported=True)
    )
    assert outcome.began_at_source_start is False
    assert outcome.history_complete is False


def test_require_choice_refuses_the_edge_when_supported_but_past_zero() -> None:
    # I1: from-start IS supported, but capture actually begins past the broadcast
    # start (first playlist MEDIA-SEQUENCE=42 — already-live or late-detected). A
    # member who chose require_choice must NOT silently get an edge partial: the
    # runner surfaces needs_fallback_choice and publishes nothing, checked against
    # the ACTUAL capture origin rather than only the inspected from-start support.
    sink = _FakeSink()
    outcome = _runner([_media_playlist(["s9.ts"], ended=True, media_seq=42)], sink).run(
        _ctx(start_intent="from_start", fallback_policy="require_choice", from_start_supported=True)
    )
    assert outcome.needs_fallback_choice is True
    assert outcome.began_at_source_start is False
    assert outcome.library_item_id is None
    # No edge partial was published — the captured window was discarded.
    assert sink.finalized is False
    assert sink.discarded is True
    assert sink.segments == []


def test_absent_media_sequence_is_not_assumed_to_be_the_beginning() -> None:
    # I2: a playlist that omits #EXT-X-MEDIA-SEQUENCE must NOT be treated as
    # sequence 0 (assume-beginning). Without positive evidence of seq 0, the
    # beginning is unconfirmed, so began_at_source_start stays False and the run
    # is an honest edge/partial capture rather than a false from-start.
    sink = _FakeSink()
    outcome = _runner([_media_playlist(["s1.ts"], ended=True, media_seq=None)], sink).run(
        _ctx(start_intent="from_start", fallback_policy="allow_live_edge", from_start_supported=True)
    )
    assert outcome.began_at_source_start is False
    assert outcome.history_complete is False


def test_require_choice_refuses_to_record_the_edge_when_unsupported() -> None:
    sink = _FakeSink()
    outcome = _runner([_media_playlist(["s1.ts"], media_seq=0)], sink).run(
        _ctx(start_intent="from_start", fallback_policy="require_choice", from_start_supported=False)
    )
    assert outcome.needs_fallback_choice is True
    assert outcome.library_item_id is None
    # Nothing was captured — the sink was never even created for a capture.
    assert sink.segments == []


def test_allowed_fallback_records_the_edge_when_unsupported() -> None:
    sink = _FakeSink()
    outcome = _runner([_media_playlist(["s1.ts"], ended=True, media_seq=7)], sink).run(
        _ctx(start_intent="from_start", fallback_policy="allow_live_edge", from_start_supported=False)
    )
    # It captured from the edge (honest fallback), never claiming the beginning.
    assert outcome.began_at_source_start is False
    assert outcome.library_item_id == "item-1"


def test_live_edge_intent_never_claims_the_beginning_even_from_sequence_zero() -> None:
    sink = _FakeSink()
    outcome = _runner([_media_playlist(["s1.ts"], ended=True, media_seq=0)], sink).run(
        _ctx(start_intent="live_edge", from_start_supported=True)
    )
    assert outcome.began_at_source_start is False
    assert outcome.reached_source_end is True
