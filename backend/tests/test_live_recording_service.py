"""Durable multi-output live-recording state machine (issue #97).

These exercise the honesty rules that reviewers weight: media and chat are
sibling outputs with EXPLICIT partial outcomes, the overall acquisition is only
``completed`` when both requested outputs reached a documented terminal state,
deliberate stop is distinct from abrupt cancel and from failure, member-scoped
idempotency cannot create a second active recording, active-recording limits are
bounded, restart recovery is deterministic, and everything is member-isolated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import LiveRecording, User
from app.services.acquisition_batch import RedactedFailure
from app.services.live_recording import (
    LiveRecordingLimitError,
    LiveRecordingService,
    RecoveryDecision,
    compute_overall_status,
)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    with factory.begin() as session:
        session.add_all(
            [
                User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True),
                User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True),
            ]
        )
    return factory


def _user(session, user_id):
    return session.get(User, user_id)


def _begin(service, session, user_id, *, identity="youtube:LIVE1", url="https://youtube.com/watch?v=LIVE1", title="Live stream"):
    claim = service.begin(
        user=_user(session, user_id),
        source_url=url,
        source_identity=identity,
        extractor="youtube",
        remote_id=identity.split(":", 1)[1],
        title=title,
        format_selection={"quality": "best"},
        output_profile={"container": "mp4"},
    )
    session.commit()
    return claim


# -- creation + idempotency -------------------------------------------------


def test_begin_creates_one_active_member_owned_recording() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        claim = _begin(service, session, "user-a")
        assert claim.created is True
        recording = claim.recording
        assert recording.user_id == "user-a"
        assert recording.status == "queued"
        assert recording.media_status == "pending"
        assert recording.chat_status == "pending"
        assert recording.stop_requested is False
        assert recording.cancel_requested is False
        assert session.scalar(select(func.count()).select_from(LiveRecording)) == 1


def test_begin_is_idempotent_per_member_and_source() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        first = _begin(service, session, "user-a")
        second = _begin(service, session, "user-a")
        assert second.created is False
        assert second.recording.id == first.recording.id
        # A duplicate submission must never create a second active recording.
        assert session.scalar(select(func.count()).select_from(LiveRecording)) == 1


def test_begin_isolates_members_for_the_same_source() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        a = _begin(service, session, "user-a")
        b = _begin(service, session, "user-b")
        assert a.recording.id != b.recording.id
        assert session.scalar(select(func.count()).select_from(LiveRecording)) == 2


def test_terminal_recording_frees_the_member_source_slot() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        first = _begin(service, session, "user-a").recording
        service.set_media_status(first.id, "completed", library_item_id=None)
        service.set_chat_status(first.id, "completed", chat_asset_id=None)
        service.finalize(first.id)
        session.commit()
        # A finished recording is exempt from the active-uniqueness index, so a
        # fresh recording of the same source is allowed.
        again = _begin(service, session, "user-a")
        assert again.created is True
        assert again.recording.id != first.id


# -- limits -----------------------------------------------------------------


def test_begin_enforces_per_member_active_limit() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session, max_active_per_user=1, max_active_global=10)
        _begin(service, session, "user-a", identity="youtube:LIVE1", url="https://youtube.com/watch?v=LIVE1")
        with pytest.raises(LiveRecordingLimitError):
            _begin(service, session, "user-a", identity="youtube:LIVE2", url="https://youtube.com/watch?v=LIVE2")


def test_begin_enforces_global_active_limit() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session, max_active_per_user=10, max_active_global=1)
        _begin(service, session, "user-a", identity="youtube:LIVE1", url="https://youtube.com/watch?v=LIVE1")
        with pytest.raises(LiveRecordingLimitError):
            _begin(service, session, "user-b", identity="youtube:LIVE2", url="https://youtube.com/watch?v=LIVE2")


def test_duplicate_submission_at_limit_returns_existing_not_error() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session, max_active_per_user=1, max_active_global=1)
        first = _begin(service, session, "user-a")
        # The member re-submits the SAME source while at the limit: idempotency
        # wins over the limit, so it returns the existing recording, not a 429.
        again = _begin(service, session, "user-a")
        assert again.created is False
        assert again.recording.id == first.recording.id


# -- lifecycle transitions --------------------------------------------------


def test_mark_recording_started_moves_to_live_and_sets_offset_base() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        session.commit()
        session.refresh(recording)
        assert recording.status == "live"
        assert recording.media_status == "recording"
        assert recording.recording_started_at is not None
        assert recording.started_at is not None


def test_request_stop_is_distinct_from_cancel() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.request_stop(_user(session, "user-a"), recording.id)
        session.commit()
        session.refresh(recording)
        assert recording.stop_requested is True
        assert recording.cancel_requested is False
        assert recording.status == "stopping"


def test_request_cancel_sets_cancel_intent() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.request_cancel(_user(session, "user-a"), recording.id)
        session.commit()
        session.refresh(recording)
        assert recording.cancel_requested is True
        assert recording.stop_requested is False


def test_stop_and_cancel_reject_other_members() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        with pytest.raises(LookupError):
            service.request_stop(_user(session, "user-b"), recording.id)
        with pytest.raises(LookupError):
            service.request_cancel(_user(session, "user-b"), recording.id)


# -- finalize: the multi-output honesty rules -------------------------------


def _finalize_with(session, service, *, media, chat, cancel=False, stop=False):
    recording = _begin(service, session, "user-a").recording
    service.mark_recording_started(recording.id)
    if stop:
        service.request_stop(_user(session, "user-a"), recording.id)
    if cancel:
        service.request_cancel(_user(session, "user-a"), recording.id)
    service.enter_finalizing(recording.id)
    service.set_media_status(
        recording.id, media,
        failure=None if media != "failed" else RedactedFailure("media_failed", "Media capture failed."),
        library_item_id="item-1" if media in {"completed", "partial"} else None,
    )
    service.set_chat_status(
        recording.id, chat,
        failure=None if chat != "failed" else RedactedFailure("chat_failed", "Chat capture failed."),
        chat_asset_id="chat-1" if chat == "completed" else None,
    )
    result = service.finalize(recording.id)
    session.commit()
    return result


def test_finalize_completed_when_both_outputs_succeed() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        result = _finalize_with(session, service, media="completed", chat="completed")
        assert result.status == "completed"
        assert result.library_item_id == "item-1"
        assert result.chat_asset_id == "chat-1"
        assert result.finished_at is not None


def test_finalize_persists_both_failure_categories() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        result = _finalize_with(session, service, media="failed", chat="failed")
        assert result.status == "failed"
        assert result.media_failure_category == "media_failed"
        assert result.chat_failure_category == "chat_failed"


def test_finalize_is_idempotent_once_terminal() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        result = _finalize_with(session, service, media="completed", chat="completed")
        first_finished = result.finished_at
        again = service.finalize(result.id)
        assert again.status == "completed"
        assert again.finished_at == first_finished


# -- pure honesty function --------------------------------------------------


@pytest.mark.parametrize(
    "media,chat,cancel,expected",
    [
        ("completed", "completed", False, "completed"),
        ("completed", "unavailable", False, "completed"),
        ("completed", "failed", False, "partial"),
        ("failed", "completed", False, "partial"),
        ("failed", "unavailable", False, "failed"),
        ("failed", "failed", False, "failed"),
        ("failed", "failed", True, "cancelled"),
        ("completed", "failed", True, "partial"),
        ("recording", "capturing", False, "failed"),  # non-terminal outputs never count as success
        # A usable-but-not-source-end media capture (deliberate stop, bounded
        # window, or interruption) is NEVER a false completed: it is an honest
        # partial even when chat also succeeded.
        ("partial", "completed", False, "partial"),
        ("partial", "unavailable", False, "partial"),
        ("partial", "failed", False, "partial"),
        ("partial", "failed", True, "partial"),  # usable media survives a late cancel
    ],
)
def test_compute_overall_status(media, chat, cancel, expected) -> None:
    assert compute_overall_status(media, chat, cancel_requested=cancel) == expected


def test_get_and_list_are_member_scoped() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        a = _begin(service, session, "user-a").recording
        _begin(service, session, "user-b")
        assert service.get(_user(session, "user-a"), a.id) is not None
        assert service.get(_user(session, "user-b"), a.id) is None
        recordings, next_cursor = service.list_for_user(_user(session, "user-a"))
        assert [r.user_id for r in recordings] == ["user-a"]
        assert next_cursor is None


# -- bounded pagination (issue #111, ADR 0006 bounded-read pattern) ----------


def _terminal_recordings(service, session, user_id, count):
    """Create ``count`` terminal recordings for one member, oldest first."""
    from datetime import datetime, timedelta

    base = datetime(2026, 7, 1, 12, 0, 0)
    ids = []
    for n in range(count):
        claim = _begin(
            service,
            session,
            user_id,
            identity=f"youtube:VOD{n:04d}",
            url=f"https://youtube.com/watch?v=VOD{n:04d}",
        )
        recording = claim.recording
        recording.status = "completed"
        recording.created_at = base + timedelta(minutes=n)
        session.flush()
        ids.append(recording.id)
    return ids


def test_list_for_user_is_bounded_with_a_keyset_cursor() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        ids = _terminal_recordings(service, session, "user-a", 7)
        first, cursor = service.list_for_user(_user(session, "user-a"), limit=3)
        assert [r.id for r in first] == [ids[6], ids[5], ids[4]]
        assert cursor is not None
        second, cursor = service.list_for_user(_user(session, "user-a"), cursor=cursor, limit=3)
        assert [r.id for r in second] == [ids[3], ids[2], ids[1]]
        assert cursor is not None
        third, cursor = service.list_for_user(_user(session, "user-a"), cursor=cursor, limit=3)
        assert [r.id for r in third] == [ids[0]]
        assert cursor is None


def test_list_for_user_clamps_limit_to_the_page_cap() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        _terminal_recordings(service, session, "user-a", 3)
        page, cursor = service.list_for_user(_user(session, "user-a"), limit=0)
        assert len(page) == 1
        assert cursor is not None
        assert LiveRecordingService.PAGE_MAX_LIMIT == 100
        assert LiveRecordingService.PAGE_DEFAULT_LIMIT == 50


def test_list_for_user_rejects_a_malformed_cursor() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        with pytest.raises(ValueError):
            service.list_for_user(_user(session, "user-a"), cursor="not-a-cursor")


# -- restart recovery decision ----------------------------------------------


def test_recovery_resumes_an_interrupted_live_recording() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        session.commit()
        session.refresh(recording)
        assert service.plan_recovery(recording) is RecoveryDecision.RESUME


def test_recovery_finalizes_when_member_already_asked_to_stop() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.request_stop(_user(session, "user-a"), recording.id)
        session.commit()
        session.refresh(recording)
        assert service.plan_recovery(recording) is RecoveryDecision.FINALIZE


def test_recovery_finalizes_when_cancelled() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.request_cancel(_user(session, "user-a"), recording.id)
        session.commit()
        session.refresh(recording)
        assert service.plan_recovery(recording) is RecoveryDecision.FINALIZE


def test_recovery_finalizes_a_finalizing_recording_never_resumes() -> None:
    # A crash inside the finalizing phase must FINALIZE from the durable
    # outputs, never RESUME (which would re-capture over an already-published media
    # recording). This holds even with no member stop/cancel and attempts unused.
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.set_media_status(recording.id, "completed", library_item_id="item-published")
        service.set_chat_status(recording.id, "completed", chat_asset_id="chat-published")
        service.enter_finalizing(recording.id)
        session.commit()
        session.refresh(recording)
        assert recording.status == "finalizing"
        assert recording.stop_requested is False
        assert recording.cancel_requested is False
        assert service.plan_recovery(recording) is RecoveryDecision.FINALIZE


def test_recovery_finalizes_a_live_recording_with_a_published_item() -> None:
    # The earlier orphan window (issue #112): a crash AFTER the media sink
    # published the Library item but BEFORE the atomic finalize-outcome commit
    # leaves the row `live` with no stop/cancel and attempts unused. The
    # publish-time pointer is durable, so recovery must FINALIZE — a RESUME
    # would re-capture and publish a second item, orphaning the first.
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.note_media_published(recording.id, "item-first")
        session.commit()
        session.refresh(recording)
        assert recording.status == "live"
        assert recording.stop_requested is False
        assert recording.cancel_requested is False
        assert service.plan_recovery(recording) is RecoveryDecision.FINALIZE


def test_note_media_published_records_pointer_and_floors_usable_partial() -> None:
    # At publish time the capture is usable but not yet proven source-end
    # complete: the pointer is durable and the media sub-status floors to
    # `partial` — never a false `completed` — so a crash-recovery finalize
    # resolves an honest partial instead of a false total failure.
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.note_media_published(recording.id, "item-first")
        session.commit()
        session.refresh(recording)
        assert recording.library_item_id == "item-first"
        assert recording.media_status == "partial"


def test_note_media_published_never_demotes_a_resolved_media_status() -> None:
    # The ordinary finalize-outcome path resolves the real status (e.g. a
    # source-end `completed`); a publish-time note must never floor it back.
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        recording = _begin(service, session, "user-a").recording
        service.mark_recording_started(recording.id)
        service.set_media_status(recording.id, "completed", library_item_id="item-first")
        service.note_media_published(recording.id, "item-first")
        session.commit()
        session.refresh(recording)
        assert recording.media_status == "completed"
        assert recording.library_item_id == "item-first"


def test_recovery_gives_up_after_bounded_attempts() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session, max_recovery_attempts=2)
        recording = _begin(service, session, "user-a").recording
        recording.attempts = 2
        session.commit()
        session.refresh(recording)
        assert service.plan_recovery(recording) is RecoveryDecision.FINALIZE


def test_recovery_none_for_terminal() -> None:
    factory = _session_factory()
    with factory() as session:
        service = LiveRecordingService(session)
        result = _finalize_with(session, service, media="completed", chat="completed")
        assert service.plan_recovery(result) is RecoveryDecision.NONE
