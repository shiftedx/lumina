"""Coordinated execution + restart recovery for live recordings (issue #97).

The manager runs the media recording and the forward-only chat capture as
concurrent sibling outputs, honoring deliberate stop vs abrupt cancel, keeping
media and chat failures independent, and deterministically recovering
non-terminal recordings after a process restart. Fakes stand in for the guarded
media recorder and the chat capturer so no live YouTube is touched.
"""

from __future__ import annotations

import threading

from app.models import LiveRecording, User
from app.services.acquisition_batch import RedactedFailure
from app.services.live_recording import LiveRecordingService
from app.services.live_recording_manager import (
    LiveChatResult,
    LiveMediaResult,
    LiveRecordingManager,
    RecordingContext,
)
from support import file_backed_session_factory


def _factory():
    return file_backed_session_factory("user-a", "user-b")


class _FakeRecorder:
    """A scriptable media recorder that records what it was asked to do."""

    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.calls: list[dict] = []

    def record(self, ctx: RecordingContext) -> LiveMediaResult:
        self.calls.append({"recording_id": ctx.recording_id, "resume": ctx.resume})
        if self.mode == "fail":
            return LiveMediaResult(status="failed", failure=RedactedFailure("media_failed", "Media capture failed."))
        if self.mode == "await_stop":
            ctx.wait_for_end(timeout=5)
            if ctx.cancel_requested():
                return LiveMediaResult(status="failed", failure=RedactedFailure("media_cancelled", "Recording cancelled."))
            return LiveMediaResult(status="completed", library_item_id=f"item-{ctx.recording_id}")
        return LiveMediaResult(status="completed", library_item_id=f"item-{ctx.recording_id}")


class _FakeCapturer:
    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.calls: list[dict] = []

    def capture(self, ctx: RecordingContext) -> LiveChatResult:
        self.calls.append({"recording_id": ctx.recording_id, "offset_base": ctx.offset_base})
        if self.mode == "fail":
            return LiveChatResult(status="failed", failure=RedactedFailure("chat_failed", "Chat capture failed."))
        if self.mode == "unavailable":
            return LiveChatResult(status="unavailable")
        if self.mode == "await_stop":
            ctx.wait_for_end(timeout=5)
            if ctx.cancel_requested():
                return LiveChatResult(status="failed", failure=RedactedFailure("chat_cancelled", "Chat cancelled."))
            return LiveChatResult(status="completed", chat_asset_id=f"chat-{ctx.recording_id}")
        return LiveChatResult(status="completed", chat_asset_id=f"chat-{ctx.recording_id}")


def _manager(factory, recorder, capturer, **kwargs):
    return LiveRecordingManager(
        session_factory=factory,
        recorder=recorder,
        chat_capturer=capturer,
        **kwargs,
    )


def _submit(manager, factory, user_id="user-a", identity="youtube:LIVE1", url="https://youtube.com/watch?v=LIVE1"):
    return manager.submit(
        user_id=user_id,
        source_url=url,
        source_identity=identity,
        extractor="youtube",
        remote_id=identity.split(":", 1)[1],
        title="Live stream",
        format_selection={"quality": "best"},
        output_profile={"container": "mp4"},
    )


def _status(factory, recording_id):
    with factory() as session:
        recording = session.get(LiveRecording, recording_id)
        return (recording.status, recording.media_status, recording.chat_status)


# -- happy path -------------------------------------------------------------


def test_happy_path_completes_both_outputs() -> None:
    factory = _factory()
    recorder, capturer = _FakeRecorder("complete"), _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    assert _status(factory, claim.recording_id) == ("completed", "completed", "completed")
    with factory() as session:
        recording = session.get(LiveRecording, claim.recording_id)
        assert recording.library_item_id == f"item-{claim.recording_id}"
        assert recording.chat_asset_id == f"chat-{claim.recording_id}"
        assert recording.recording_started_at is not None


def test_chat_start_offset_base_is_the_media_start() -> None:
    factory = _factory()
    capturer = _FakeCapturer("complete")
    manager = _manager(factory, _FakeRecorder("complete"), capturer)
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    # Chat capture is handed the media start as its offset base so the completed
    # item and its timed chat asset stay synchronized.
    assert capturer.calls[0]["offset_base"] is not None


# -- deliberate stop vs abrupt cancel ---------------------------------------


def test_deliberate_stop_finalizes_usable_outputs() -> None:
    factory = _factory()
    manager = _manager(factory, _FakeRecorder("await_stop"), _FakeCapturer("await_stop"))
    claim = _submit(manager, factory)
    _wait_until_live(factory, claim.recording_id)
    manager.stop("user-a", claim.recording_id)
    manager.wait_all(timeout=5)
    assert _status(factory, claim.recording_id) == ("completed", "completed", "completed")


def test_abrupt_cancel_is_distinct_from_stop_and_failure() -> None:
    factory = _factory()
    manager = _manager(factory, _FakeRecorder("await_stop"), _FakeCapturer("await_stop"))
    claim = _submit(manager, factory)
    _wait_until_live(factory, claim.recording_id)
    manager.cancel("user-a", claim.recording_id)
    manager.wait_all(timeout=5)
    status, _, _ = _status(factory, claim.recording_id)
    assert status == "cancelled"


# -- independent media/chat failures ----------------------------------------


def test_media_failure_with_good_chat_is_partial() -> None:
    factory = _factory()
    manager = _manager(factory, _FakeRecorder("fail"), _FakeCapturer("complete"))
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    status, media, chat = _status(factory, claim.recording_id)
    assert (status, media, chat) == ("partial", "failed", "completed")
    with factory() as session:
        assert session.get(LiveRecording, claim.recording_id).chat_asset_id is not None


def test_chat_failure_with_good_media_is_partial() -> None:
    factory = _factory()
    manager = _manager(factory, _FakeRecorder("complete"), _FakeCapturer("fail"))
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    status, media, chat = _status(factory, claim.recording_id)
    assert (status, media, chat) == ("partial", "completed", "failed")
    with factory() as session:
        assert session.get(LiveRecording, claim.recording_id).library_item_id is not None


def test_chat_unavailable_still_completes() -> None:
    factory = _factory()
    manager = _manager(factory, _FakeRecorder("complete"), _FakeCapturer("unavailable"))
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    assert _status(factory, claim.recording_id) == ("completed", "completed", "unavailable")


def test_recorder_and_capturer_each_run_exactly_once() -> None:
    factory = _factory()
    recorder, capturer = _FakeRecorder("complete"), _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    assert len(recorder.calls) == 1
    assert len(capturer.calls) == 1


# -- runtime ceiling --------------------------------------------------------


def test_runtime_ceiling_stops_a_recording() -> None:
    factory = _factory()
    # Endless-awaiting siblings: only the runtime watchdog can end this recording.
    manager = _manager(
        factory, _FakeRecorder("await_stop"), _FakeCapturer("await_stop"), max_runtime_seconds=0.05
    )
    claim = _submit(manager, factory)
    manager.wait_all(timeout=5)
    # The watchdog requested a deliberate stop, so both siblings wound down and
    # the recording finalized instead of running unbounded.
    status, _, _ = _status(factory, claim.recording_id)
    assert status in {"completed", "partial"}


# -- idempotency ------------------------------------------------------------


def test_duplicate_submission_launches_one_recording() -> None:
    factory = _factory()
    recorder = _FakeRecorder("await_stop")
    manager = _manager(factory, recorder, _FakeCapturer("await_stop"))
    first = _submit(manager, factory)
    second = _submit(manager, factory)
    assert first.recording_id == second.recording_id
    assert second.created is False
    manager.stop("user-a", first.recording_id)
    manager.wait_all(timeout=5)
    # Only one worker ever ran the recorder for this member + source.
    assert len(recorder.calls) == 1


# -- restart recovery -------------------------------------------------------


def test_recover_resumes_interrupted_recording() -> None:
    factory = _factory()
    # Simulate a crash: a durable recording left mid-flight in 'live'.
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=LIVE1",
            source_identity="youtube:LIVE1",
        )
        service.mark_recording_started(claim.recording.id)
        session.commit()
        recording_id = claim.recording.id
    recorder = _FakeRecorder("complete")
    manager = _manager(factory, recorder, _FakeCapturer("complete"))
    manager.recover()
    manager.wait_all(timeout=5)
    assert _status(factory, recording_id)[0] == "completed"
    assert recorder.calls[0]["resume"] is True


def test_recover_finalizes_a_recording_the_member_stopped() -> None:
    factory = _factory()
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=LIVE1",
            source_identity="youtube:LIVE1",
        )
        service.mark_recording_started(claim.recording.id)
        # Media was durably published before the crash; chat never finalized.
        service.set_media_status(claim.recording.id, "completed", library_item_id="item-durable")
        service.request_stop(session.get(User, "user-a"), claim.recording.id)
        session.commit()
        recording_id = claim.recording.id
    recorder = _FakeRecorder("complete")
    manager = _manager(factory, recorder, _FakeCapturer("complete"))
    manager.recover()
    manager.wait_all(timeout=5)
    status, media, chat = _status(factory, recording_id)
    # Not resumed (the member already asked to stop); finalized honestly from the
    # durable outputs: media survived, chat did not -> partial.
    assert status == "partial"
    assert media == "completed"
    assert recorder.calls == []


def test_recover_after_publish_time_pointer_never_recaptures_or_orphans() -> None:
    factory = _factory()
    # The earlier orphan window (issue #112): the process crashed AFTER the media
    # sink published the Library item (the publish-time pointer committed with it)
    # but BEFORE the atomic finalize-outcome commit — the row is still `live`,
    # with no member stop/cancel and the attempt budget unused.
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=LIVE1",
            source_identity="youtube:LIVE1",
        )
        service.mark_recording_started(claim.recording.id)
        service.note_media_published(claim.recording.id, "item-first")
        session.commit()
        recording_id = claim.recording.id
    recorder = _FakeRecorder("complete")
    capturer = _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    manager.recover()
    manager.wait_all(timeout=5)
    with factory() as session:
        row = session.get(LiveRecording, recording_id)
    # Recovery must NOT resume: a re-capture would publish a second Library item
    # and orphan "item-first". It finalizes from the durable outputs instead: the
    # published capture is a usable partial, chat never finalized -> honest
    # overall partial (never a false failure), still pointing at the first item.
    assert recorder.calls == []
    assert capturer.calls == []
    assert row.status == "partial"
    assert row.media_status == "partial"
    assert row.library_item_id == "item-first"


def _factory_with_commit_spy():
    """A session factory that snapshots (status, media, chat) after every commit.

    Lets a test observe the DURABLE ordering of the finalize path: any commit that
    shows the recording in `finalizing` is a point a crash could freeze it at, so
    its sibling sub-statuses must already be terminal there (part 2).
    """
    from sqlalchemy import event as sa_event

    factory = _factory()
    commits: list[dict] = []

    def _wrapped():
        session = factory()

        @sa_event.listens_for(session, "after_commit")
        def _snapshot(sess):  # noqa: ANN001
            for obj in list(sess.identity_map.values()):
                if isinstance(obj, LiveRecording):
                    commits.append(
                        {
                            "status": obj.status,
                            "media_status": obj.media_status,
                            "chat_status": obj.chat_status,
                            "library_item_id": obj.library_item_id,
                        }
                    )

        return session

    return _wrapped, commits


def test_finalizing_is_crash_consistent_with_its_sub_statuses() -> None:
    factory, commits = _factory_with_commit_spy()
    recorder = _FakeRecorder("complete")
    capturer = _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    submission = _submit(manager, factory)
    manager.wait_all(timeout=5)
    # A durable `finalizing` row is only ever observable AFTER both sibling outputs
    # are committed at their terminal sub-statuses. Enter-finalizing that committed
    # BEFORE the sub-statuses (an earlier bug) would surface a (finalizing, recording,
    # pending) commit here, which a crash-recovery FINALIZE would mislabel.
    finalizing_commits = [c for c in commits if c["status"] == "finalizing"]
    assert finalizing_commits, "expected the recording to pass through finalizing"
    for snapshot in finalizing_commits:
        assert snapshot["media_status"] == "completed"
        assert snapshot["chat_status"] == "completed"
        assert snapshot["library_item_id"] == f"item-{submission.recording_id}"


def test_recover_finalizing_never_recaptures_and_stays_completed() -> None:
    factory = _factory()
    # A crash INSIDE the finalizing phase: both sibling outputs already
    # reached their durable terminal sub-statuses and the media Library item was
    # published, but the overall never resolved before the process died. There is
    # no member stop/cancel and the attempt budget is not exhausted.
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=LIVE1",
            source_identity="youtube:LIVE1",
        )
        service.mark_recording_started(claim.recording.id)
        service.set_media_status(claim.recording.id, "completed", library_item_id="item-published")
        service.set_chat_status(claim.recording.id, "completed", chat_asset_id="chat-published")
        service.enter_finalizing(claim.recording.id)
        session.commit()
        recording_id = claim.recording.id
    recorder = _FakeRecorder("complete")
    capturer = _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    manager.recover()
    manager.wait_all(timeout=5)
    with factory() as session:
        row = session.get(LiveRecording, recording_id)
    # Finalizing recovers by FINALIZE, never RESUME: neither collaborator runs, so a
    # completed+published recording is never re-captured over. The overall resolves
    # honestly to completed from the durable outputs and the published Library item
    # id is untouched (not overwritten, not orphaned).
    assert recorder.calls == []
    assert capturer.calls == []
    assert row.status == "completed"
    assert row.media_status == "completed"
    assert row.library_item_id == "item-published"


def test_recover_finalizing_media_ok_chat_failed_is_honest_partial() -> None:
    factory = _factory()
    # A finalizing crash where the media completed and published but the chat
    # output failed: recovery must resolve to the honest partial from the durable
    # sub-statuses, still without any re-capture.
    with factory() as session:
        service = LiveRecordingService(session)
        claim = service.begin(
            user=session.get(User, "user-a"),
            source_url="https://youtube.com/watch?v=LIVE1",
            source_identity="youtube:LIVE1",
        )
        service.mark_recording_started(claim.recording.id)
        service.set_media_status(claim.recording.id, "completed", library_item_id="item-published")
        service.set_chat_status(
            claim.recording.id, "failed", failure=RedactedFailure("chat_failed", "Chat capture failed.")
        )
        service.enter_finalizing(claim.recording.id)
        session.commit()
        recording_id = claim.recording.id
    recorder = _FakeRecorder("complete")
    capturer = _FakeCapturer("complete")
    manager = _manager(factory, recorder, capturer)
    manager.recover()
    manager.wait_all(timeout=5)
    with factory() as session:
        row = session.get(LiveRecording, recording_id)
    assert recorder.calls == []
    assert capturer.calls == []
    assert row.status == "partial"
    assert row.media_status == "completed"
    assert row.library_item_id == "item-published"


# -- helpers ----------------------------------------------------------------


def _wait_until_live(factory, recording_id, timeout=5.0):
    deadline = threading.Event()
    import time

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with factory() as session:
            recording = session.get(LiveRecording, recording_id)
            if recording is not None and recording.recording_started_at is not None:
                return
        time.sleep(0.01)
    raise AssertionError("recording never reached the live edge")
