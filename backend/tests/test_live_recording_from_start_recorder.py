"""From-start media recorder mapping (issue #98).

The recorder maps a guarded from-start run + the member INTENT to the sibling
media output's honest state and provenance. This is where the settled invariant
lives: from-start is best-effort and NEVER reports a false ``completed`` when the
beginning history is incomplete, and it NEVER silently falls back to the edge
contrary to the member's saved intent. The yt-dlp-facing run is behind a fake
runner so the honesty is covered offline.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from app.services.live_recording_adapters import (
    GuardedLiveMediaRecorder,
    MediaRunOutcome,
)
from app.services.live_recording_manager import RecordingContext


def _ctx(*, start_intent="from_start", fallback_policy="allow_live_edge", scheduled=None):
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
        scheduled_start_at=scheduled,
    )


class _Runner:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    def run(self, ctx):
        self.calls.append(ctx.start_intent)
        return self._outcome


def test_from_start_full_history_to_end_completes_from_the_beginning() -> None:
    recorder = GuardedLiveMediaRecorder(
        runner=_Runner(
            MediaRunOutcome(
                library_item_id="item-9",
                reached_source_end=True,
                began_at_source_start=True,
                history_complete=True,
                from_start_supported=True,
            )
        )
    )
    result = recorder.record(_ctx(start_intent="from_start"))
    assert result.status == "completed"
    assert result.capture_origin == "source_beginning"
    assert result.media_history_complete is True
    assert result.from_start_supported is True


def test_from_start_unavailable_require_choice_never_captures_the_edge() -> None:
    # The runner signals it refused to silently fall back; the recorder reports a
    # from-start-unavailable failure that a member can resolve deliberately.
    recorder = GuardedLiveMediaRecorder(
        runner=_Runner(
            MediaRunOutcome(
                library_item_id=None,
                needs_fallback_choice=True,
                from_start_supported=False,
            )
        )
    )
    result = recorder.record(_ctx(start_intent="from_start", fallback_policy="require_choice"))
    assert result.status == "failed"
    assert result.awaiting_fallback_choice is True
    assert result.failure.category == "from_start_unavailable"
    assert result.capture_origin is None  # nothing captured from the edge
