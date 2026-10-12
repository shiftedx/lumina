"""Public API contract for scheduling + from-start (issue #98).

Exercises the create route against a real shared SQLite engine, a fake waiter +
recorder/capturer (no live YouTube), and a stubbed preview that reports an
upcoming (schedulable) or a from-start-capable live source.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app import main as main_module
from app.models import LiveRecording, User
from app.schemas import (
    LiveRecordingCreateRequest,
    MediaSourceCapabilities,
    MediaSourceChatCapabilities,
    PreviewResponse,
)
from app.services.live_recording_manager import LiveChatResult, LiveMediaResult, LiveRecordingManager
from app.services.live_recording_waiter import WaitDecision, WaitOutcome
from app.services.yt_dlp_service import YtDlpService
from support import file_backed_session_factory


def _engine_factory():
    return file_backed_session_factory("owner-1")


class _FakeWaiter:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def wait(self, ctx):
        self.calls += 1
        return self.decision


class _ProvenanceRecorder:
    def __init__(self, result):
        self.result = result
        self.seen = []

    def record(self, ctx):
        self.seen.append({"intent": ctx.start_intent, "from_start_supported": ctx.from_start_supported})
        return self.result


class _FakeCapturer:
    def __init__(self, result):
        self.result = result

    def capture(self, ctx):
        return self.result


def _install(monkeypatch, factory, *, lifecycle, waiter=None, recorder=None, capturer=None,
             can_record=False, can_schedule=False, from_start_available=False, scheduled_start=None):
    manager = LiveRecordingManager(
        session_factory=factory,
        recorder=recorder or _ProvenanceRecorder(LiveMediaResult(status="completed", library_item_id="item", capture_origin="live_edge")),
        chat_capturer=capturer or _FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=True)),
        waiter=waiter or _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=from_start_available)),
    )
    monkeypatch.setattr(main_module, "live_recording_manager", manager)
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *a, **k: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        video_id = parse_qs(urlsplit(source_url).query).get("v", ["UP1"])[0]
        caps = MediaSourceCapabilities(
            provider="youtube", lifecycle=lifecycle,
            can_play=False, can_acquire=False,
            can_record=can_record, can_schedule=can_schedule,
            schedule_reason=None if can_schedule else "upcoming_schedule_not_supported",
            scheduled_start=scheduled_start, from_start_available=from_start_available,
            chat=MediaSourceChatCapabilities(live="available"),
        )
        return PreviewResponse(
            kind="video", title="Broadcast", extractor="youtube", webpage_url=source_url,
            capabilities=caps, raw={"id": video_id, "extractor": "youtube"},
        )

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    return manager


def _create(factory, **payload_kwargs):
    payload = LiveRecordingCreateRequest(source_url="https://www.youtube.com/watch?v=UP1", **payload_kwargs)
    with factory() as db:
        result = main_module.create_live_recording(payload, object(), db.get(User, "owner-1"), db)
        # Mirror the real request lifecycle (app.db.session_scope commits at teardown).
        db.commit()
        return result


def _row(factory, recording_id):
    with factory() as db:
        return db.get(LiveRecording, recording_id)


def test_schedule_upcoming_source_starts_waiting_then_records(monkeypatch) -> None:
    factory = _engine_factory()
    recorder = _ProvenanceRecorder(
        LiveMediaResult(status="completed", library_item_id="item", capture_origin="source_beginning", media_history_complete=True, from_start_supported=True)
    )
    manager = _install(
        monkeypatch, factory, lifecycle="upcoming", can_schedule=True, from_start_available=True,
        scheduled_start=datetime(2026, 7, 20, 18, 0, 0),
        waiter=_FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=True)),
        recorder=recorder,
        # The media captured the beginning, but the forward-only chat cannot be
        # evidenced back to it (covered_from_start=False), so the honest terminal is
        # partial-history, never a false complete.
        capturer=_FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False)),
    )
    created = _create(factory, start_intent="from_start")
    # The response exposes the waiting state + scheduled time + intent.
    assert created.status == "waiting"
    assert created.scheduled_start_at == datetime(2026, 7, 20, 18, 0, 0)
    assert created.start_intent == "from_start"
    manager.wait_all(timeout=5)
    row = _row(factory, created.id)
    assert row.status == "partial"
    assert row.history == "partial"
    assert row.capture_origin == "source_beginning"
    assert recorder.seen[0]["from_start_supported"] is True


def test_upcoming_without_a_scheduled_time_still_waits(monkeypatch) -> None:
    factory = _engine_factory()
    # An upcoming source with no precise provider start time must still wait for the
    # broadcast rather than trying to connect immediately.
    manager = _install(
        monkeypatch, factory, lifecycle="upcoming", can_schedule=True, from_start_available=True,
        scheduled_start=None,
        waiter=_FakeWaiter(WaitDecision(WaitOutcome.SOURCE_CANCELLED)),
    )
    created = _create(factory, start_intent="from_start")
    assert created.status == "waiting"
    assert created.scheduled_start_at is None
    manager.wait_all(timeout=5)
    row = _row(factory, created.id)
    assert row.status == "failed"
    assert row.waiting_reason == "source_cancelled"


def test_from_start_intent_on_live_source_persists_support(monkeypatch) -> None:
    factory = _engine_factory()
    recorder = _ProvenanceRecorder(
        LiveMediaResult(status="partial", library_item_id="item", capture_origin="source_beginning", media_history_complete=False, from_start_supported=True)
    )
    manager = _install(
        monkeypatch, factory, lifecycle="live", can_record=True, from_start_available=True, recorder=recorder,
        capturer=_FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False)),
    )
    created = _create(factory, start_intent="from_start")
    # A live source records from now (no waiting phase), but with from-start intent.
    assert created.status in {"queued", "live", "finalizing", "partial"}
    assert created.start_intent == "from_start"
    manager.wait_all(timeout=5)
    row = _row(factory, created.id)
    # From-start with an incomplete beginning is an honest partial, never completed.
    assert row.status == "partial"
    assert row.history == "partial"
    assert recorder.seen[0]["from_start_supported"] is True


def test_gate_blocks_a_source_that_is_neither_recordable_nor_schedulable(monkeypatch) -> None:
    factory = _engine_factory()
    _install(monkeypatch, factory, lifecycle="upcoming", can_schedule=False)
    with pytest.raises(HTTPException) as excinfo:
        _create(factory)
    assert excinfo.value.status_code == 409


def test_response_surfaces_from_start_and_history_fields(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory, lifecycle="live", can_record=True, from_start_available=True)
    created = _create(factory, start_intent="live_edge")
    manager.wait_all(timeout=5)
    with factory() as db:
        final = main_module.get_live_recording(created.id, db.get(User, "owner-1"), db)
    assert final.start_intent == "live_edge"
    assert final.capture_origin in {"live_edge", "pending"}
    assert final.history in {"from_edge", "pending"}
    assert hasattr(final, "awaiting_fallback_choice")
