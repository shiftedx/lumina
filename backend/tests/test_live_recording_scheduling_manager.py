"""Manager coordination of scheduling + from-start (issue #98).

Extends the #97 manager coordination with the waiting phase and the from-start
provenance flow. A fake waiter (scripted decisions) and fake recorder/capturer
(scripted provenance) keep the tests deterministic with no real time and no live
YouTube — the durable transitions, bounded terminal outcomes, restart recovery of
a waiting recording, and the from-start honesty are what these cover.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from app.models import LiveRecording, User
from app.services.acquisition_batch import RedactedFailure
from app.services.live_recording import LiveRecordingService, RecoveryDecision
from app.services.live_recording_manager import (
    LiveChatResult,
    LiveMediaResult,
    LiveRecordingManager,
    RecordingContext,
)
from app.services.live_recording_waiter import WaitDecision, WaitOutcome
from support import file_backed_session_factory


def _factory():
    return file_backed_session_factory("user-a")


class _FakeWaiter:
    def __init__(self, decision: WaitDecision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def wait(self, ctx: RecordingContext) -> WaitDecision:
        self.calls.append({"recording_id": ctx.recording_id, "start_intent": ctx.start_intent})
        return self.decision


class _FromStartRecorder:
    """A recorder that reports scripted from-start provenance."""

    def __init__(self, result: LiveMediaResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def record(self, ctx: RecordingContext) -> LiveMediaResult:
        self.calls.append(
            {"start_intent": ctx.start_intent, "from_start_supported": ctx.from_start_supported}
        )
        return self.result


class _FakeCapturer:
    def __init__(self, result: LiveChatResult) -> None:
        self.result = result

    def capture(self, ctx: RecordingContext) -> LiveChatResult:
        return self.result


def _manager(factory, *, waiter=None, recorder=None, capturer=None, **kwargs):
    return LiveRecordingManager(
        session_factory=factory,
        recorder=recorder or _FromStartRecorder(LiveMediaResult(status="completed", library_item_id="item")),
        chat_capturer=capturer or _FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat")),
        waiter=waiter,
        **kwargs,
    )


def _schedule(manager, factory, *, intent="from_start", fallback="allow_live_edge", identity="youtube:UP1"):
    return manager.submit(
        user_id="user-a",
        source_url="https://youtube.com/watch?v=UP1",
        source_identity=identity,
        extractor="youtube",
        remote_id="UP1",
        title="Upcoming stream",
        scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        start_intent=intent,
        fallback_policy=fallback,
    )


def _row(factory, recording_id) -> LiveRecording:
    with factory() as session:
        return session.get(LiveRecording, recording_id)


# -- schedule launches a durable waiter -------------------------------------


def test_scheduling_starts_in_waiting_and_runs_the_waiter() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=True))
    recorder = _FromStartRecorder(
        LiveMediaResult(
            status="completed",
            library_item_id="item",
            capture_origin="source_beginning",
            media_history_complete=True,
            from_start_supported=True,
        )
    )
    # The media captured the whole broadcast from the beginning, but the forward-only
    # chat connects at the live-chat edge and cannot be evidenced to reach back to the
    # beginning (covered_from_start=False): an honest partial-history, never a false
    # complete — even though the waiter connected and both outputs are terminal.
    manager = _manager(factory, waiter=waiter, recorder=recorder,
                       capturer=_FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False)))
    claim = _schedule(manager, factory)
    # Immediately after submit the recording is durably waiting.
    assert claim.status == "waiting"
    manager.wait_all(timeout=5)
    assert len(waiter.calls) == 1
    row = _row(factory, claim.recording_id)
    assert row.status == "partial"
    assert row.capture_origin == "source_beginning"
    assert row.history == "partial"
    # The waiter's from-start support flowed into the capture.
    assert recorder.calls[0]["from_start_supported"] is True


def test_no_duplicate_waiter_for_the_same_member_and_source() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=False))
    # Hold the recorder until we release it, so the first waiter is still active.
    gate = threading.Event()

    class _BlockingRecorder:
        def __init__(self) -> None:
            self.calls = 0

        def record(self, ctx: RecordingContext) -> LiveMediaResult:
            self.calls += 1
            gate.wait(timeout=5)
            return LiveMediaResult(status="completed", library_item_id="item", capture_origin="live_edge")

    recorder = _BlockingRecorder()
    manager = _manager(factory, waiter=waiter, recorder=recorder)
    first = _schedule(manager, factory)
    second = _schedule(manager, factory)
    assert first.recording_id == second.recording_id
    assert second.created is False
    gate.set()
    manager.wait_all(timeout=5)
    assert recorder.calls == 1


# -- bounded terminal wait outcomes -----------------------------------------


def test_source_cancelled_upstream_is_failed_with_reason() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.SOURCE_CANCELLED))
    recorder = _FromStartRecorder(LiveMediaResult(status="completed", library_item_id="item"))
    manager = _manager(factory, waiter=waiter, recorder=recorder)
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "failed"
    assert row.waiting_reason == "source_cancelled"
    # The capture never ran: no false recording of a cancelled broadcast.
    assert recorder.calls == []


def test_excessive_delay_is_failed_with_reason() -> None:
    factory = _factory()
    manager = _manager(factory, waiter=_FakeWaiter(WaitDecision(WaitOutcome.EXPIRED)))
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "failed"
    assert row.waiting_reason == "excessive_delay"


def test_auth_expiry_during_wait_is_failed_with_reason() -> None:
    factory = _factory()
    manager = _manager(factory, waiter=_FakeWaiter(WaitDecision(WaitOutcome.AUTH_EXPIRED)))
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "failed"
    assert row.waiting_reason == "auth_expired"


def test_member_cancel_during_wait_is_cancelled_not_failed() -> None:
    factory = _factory()
    manager = _manager(factory, waiter=_FakeWaiter(WaitDecision(WaitOutcome.MEMBER_CANCELLED)))
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "cancelled"


def test_missing_waiter_fails_closed_rather_than_hanging() -> None:
    factory = _factory()
    manager = _manager(factory, waiter=None)
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "failed"


# -- from-start honesty through the manager ---------------------------------


def test_from_start_incomplete_history_is_partial_never_completed() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=True))
    recorder = _FromStartRecorder(
        LiveMediaResult(
            status="partial",
            library_item_id="item",
            capture_origin="source_beginning",
            media_history_complete=False,
            from_start_supported=True,
        )
    )
    manager = _manager(factory, waiter=waiter, recorder=recorder,
                       capturer=_FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False)))
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status == "partial"
    assert row.capture_origin == "source_beginning"
    assert row.history == "partial"


def test_from_start_complete_video_but_chat_gap_is_partial_history() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=True))
    # Media reached source-end from the beginning with complete history, but chat
    # only covers from connect forward: missing earlier chat -> partial-history.
    recorder = _FromStartRecorder(
        LiveMediaResult(
            status="completed",
            library_item_id="item",
            capture_origin="source_beginning",
            media_history_complete=True,
            from_start_supported=True,
        )
    )
    capturer = _FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False))
    manager = _manager(factory, waiter=waiter, recorder=recorder, capturer=capturer)
    claim = _schedule(manager, factory)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.history == "partial"
    assert row.status == "partial"  # never a false 'completed'


def test_from_start_unavailable_require_choice_fails_without_edge_fallback() -> None:
    factory = _factory()
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=False))
    # The recorder refused to silently fall back and produced no capture.
    recorder = _FromStartRecorder(
        LiveMediaResult(
            status="failed",
            failure=RedactedFailure("from_start_unavailable", "From-start was not available."),
            from_start_supported=False,
            awaiting_fallback_choice=True,
        )
    )
    manager = _manager(factory, waiter=waiter, recorder=recorder,
                       capturer=_FakeCapturer(LiveChatResult(status="unavailable")))
    claim = _schedule(manager, factory, fallback="require_choice")
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert row.status in {"failed", "partial"}
    assert row.awaiting_fallback_choice is True
    assert row.capture_origin == "pending"  # nothing captured from the edge


# -- restart recovery of a waiting recording --------------------------------


def test_recover_resumes_a_waiting_recording() -> None:
    factory = _factory()
    # A durable schedule left mid-wait by a crash.
    with factory() as session:
        claim = LiveRecordingService(session).begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
            start_intent="from_start",
        )
        session.commit()
        recording_id = claim.recording.id
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY, from_start_supported=True))
    recorder = _FromStartRecorder(
        LiveMediaResult(status="completed", library_item_id="item", capture_origin="source_beginning", media_history_complete=True)
    )
    # Resumed forward-only chat still cannot be evidenced to reach the beginning, so
    # the recovered schedule finishes as an honest partial-history, not a false
    # complete — the recovery path reaches a terminal state exactly as before.
    # Pin the recovery clock within the waiting deadline so this stays a
    # within-window resume regardless of the wall clock.
    manager = _manager(factory, waiter=waiter, recorder=recorder,
                       capturer=_FakeCapturer(LiveChatResult(status="completed", chat_asset_id="chat", covered_from_start=False)),
                       clock=lambda: datetime(2026, 7, 20, 12, 0, 0))
    manager.recover()
    manager.wait_all(timeout=5)
    assert len(waiter.calls) == 1
    assert _row(factory, recording_id).status == "partial"


def test_recover_finalizes_a_waiting_recording_the_member_cancelled() -> None:
    factory = _factory()
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        )
        service.request_cancel(session.get(User, "user-a"), claim.recording.id)
        session.commit()
        recording_id = claim.recording.id
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY))
    recorder = _FromStartRecorder(LiveMediaResult(status="completed", library_item_id="item", capture_origin="live_edge"))
    manager = _manager(factory, waiter=waiter, recorder=recorder)
    manager.recover()
    manager.wait_all(timeout=5)
    # The member's durable cancel intent is honored: finalized, never resumed into
    # a capture, and never a false recording.
    assert _row(factory, recording_id).status == "cancelled"
    assert recorder.calls == []


def test_benign_restarts_during_wait_do_not_exhaust_the_capture_budget() -> None:
    # Issue I1: a long durable `waiting` (a broadcast scheduled hours out — the
    # whole point of #98) can span many ordinary process restarts. Re-entering the
    # waiting phase must NOT consume the bounded capture-recovery budget, or the
    # schedule would fail before it ever goes live. Only a capture (re)entry counts
    # an attempt. The recovery clock is pinned within the waiting deadline so this
    # exercises the benign-restart path, not the durable timeout.
    within = datetime(2026, 7, 20, 12, 0, 0)  # before the 18:00 scheduled start
    factory = _factory()
    with factory() as session:
        service = LiveRecordingService(session, max_recovery_attempts=2, clock=lambda: within)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        )
        recording_id = claim.recording.id
        # Simulate five benign restarts while the broadcast is still upcoming: each
        # re-claims a still-`waiting` recording.
        for _ in range(5):
            service.claim_for_run(recording_id)
        session.commit()
        row = session.get(LiveRecording, recording_id)
        assert row.attempts == 0  # waiting re-entries never counted
        # The recovery decision still RESUMEs (re-waits), never FINALIZE.
        assert service.plan_recovery(row) is RecoveryDecision.RESUME

    # And a full recover() re-runs the waiter rather than failing the schedule.
    waiter = _FakeWaiter(WaitDecision(WaitOutcome.SOURCE_CANCELLED))
    manager = _manager(factory, waiter=waiter, max_recovery_attempts=2, clock=lambda: within)
    manager.recover()
    manager.wait_all(timeout=5)
    row = _row(factory, recording_id)
    assert waiter.calls, "a benign restart during the wait must re-run the waiter"
    # This particular re-run observed the upstream cancel — an honest reason, NOT a
    # timeout (the budget was never consumed by waiting).
    assert row.status == "failed"
    assert row.waiting_reason == "source_cancelled"


def test_poisoned_capture_still_terminates_after_bounded_attempts() -> None:
    # The poison-capture crash-loop protection is preserved for the CAPTURE phase:
    # a recording that has left waiting and exhausted its capture attempts finalizes
    # rather than resuming forever. (Attempts are set directly to model the post-
    # crash CAPTURE-phase state — waiting no longer accrues them; see I1.)
    factory = _factory()
    with factory() as session:
        service = LiveRecordingService(session, max_recovery_attempts=2)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        )
        recording_id = claim.recording.id
        # The recording reached capture (queued) and then crash-looped to exhaustion.
        service.mark_connecting(recording_id)
        row = session.get(LiveRecording, recording_id)
        row.attempts = 2
        session.commit()
        assert service.plan_recovery(row) is RecoveryDecision.FINALIZE


def test_no_start_waiting_is_bounded_by_the_created_at_cap() -> None:
    # I1 residual: a waiting recording with NO announced start time (reachable when
    # an upcoming source has no capabilities.scheduled_start) has no scheduled deadline
    # and the waiter's probe bound resets each restart. plan_recovery bounds it durably
    # from created_at + the no-start cap, so it cannot hold an active slot forever.
    factory = _factory()
    base = datetime(2026, 7, 20, 0, 0, 0)
    with factory() as session:
        claim = LiveRecordingService(session).begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            await_schedule=True,  # upcoming, but no scheduled_start_at
        )
        recording_id = claim.recording.id
        row = session.get(LiveRecording, recording_id)
        assert row.status == "waiting"
        assert row.scheduled_start_at is None
        row.created_at = base  # pin the durable base for a deterministic deadline
        session.commit()

    with factory() as session:
        row = session.get(LiveRecording, recording_id)
        # 47h after creation: within the 48h no-start cap -> keep waiting.
        within = LiveRecordingService(session, clock=lambda: base + timedelta(hours=47))
        assert within.plan_recovery(row) is RecoveryDecision.RESUME
        # 49h after creation: past the cap -> finalize (bounded), never resume forever.
        past = LiveRecordingService(session, clock=lambda: base + timedelta(hours=49))
        assert past.plan_recovery(row) is RecoveryDecision.FINALIZE


def test_scheduled_waiting_is_bounded_by_grace_across_restart() -> None:
    # A scheduled source past scheduled_start + grace is bounded durably (the waiter's
    # wall-clock grace, made restart-durable in plan_recovery), independent of the
    # waiter's per-process probe counter.
    factory = _factory()
    scheduled = datetime(2026, 7, 20, 18, 0, 0)
    with factory() as session:
        claim = LiveRecordingService(session).begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            scheduled_start_at=scheduled,
        )
        recording_id = claim.recording.id
        session.commit()

    with factory() as session:
        row = session.get(LiveRecording, recording_id)
        # 5h past the scheduled start: within the 6h grace -> keep waiting.
        within = LiveRecordingService(session, clock=lambda: scheduled + timedelta(hours=5))
        assert within.plan_recovery(row) is RecoveryDecision.RESUME
        # 7h past the scheduled start: beyond grace -> finalize.
        past = LiveRecordingService(session, clock=lambda: scheduled + timedelta(hours=7))
        assert past.plan_recovery(row) is RecoveryDecision.FINALIZE


def test_recover_finalizes_a_perpetual_no_start_wait_as_excessive_delay() -> None:
    # End-to-end: a no-announced-start waiting row older than the cap is FINALIZEd on
    # restart as an honest excessive_delay, WITHOUT re-running the waiter (so a
    # perpetual upcoming never keeps holding one of the bounded active slots).
    factory = _factory()
    base = datetime(2026, 7, 20, 0, 0, 0)
    with factory() as session:
        claim = LiveRecordingService(session).begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=UP1",
            source_identity="youtube:UP1",
            await_schedule=True,
        )
        recording_id = claim.recording.id
        row = session.get(LiveRecording, recording_id)
        row.created_at = base
        session.commit()

    waiter = _FakeWaiter(WaitDecision(WaitOutcome.READY))
    manager = _manager(factory, waiter=waiter, clock=lambda: base + timedelta(hours=49))
    manager.recover()
    manager.wait_all(timeout=5)
    row = _row(factory, recording_id)
    assert row.status == "failed"
    assert row.waiting_reason == "excessive_delay"
    assert waiter.calls == []  # never re-ran the waiter; the slot is released


def _wait_until(factory, recording_id, predicate, timeout=5.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate(_row(factory, recording_id)):
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")
