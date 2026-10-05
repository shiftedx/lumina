"""Durable scheduling + from-start transitions on LiveRecordingService (issue #98).

These extend #97's durable state machine with the waiting/scheduled phase and the
from-start intent columns, keeping member-scoped idempotency (no duplicate waiter)
and honest bounded outcomes for a scheduled acquisition.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import LiveRecording, User
from app.services.live_recording import LiveRecordingService


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    session = factory()
    session.add(User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True))
    session.commit()
    return session


def _user(session) -> User:
    return session.get(User, "user-a")


def test_begin_upcoming_starts_in_waiting_with_schedule_and_intent() -> None:
    session = _session()
    service = LiveRecordingService(session)
    when = datetime(2026, 7, 20, 18, 0, 0)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=when,
        start_intent="from_start",
        fallback_policy="require_choice",
    )
    session.commit()
    rec = claim.recording
    assert claim.created is True
    assert rec.status == "waiting"
    assert rec.scheduled_start_at == when
    assert rec.start_intent == "from_start"
    assert rec.fallback_policy == "require_choice"
    assert rec.capture_origin == "pending"
    assert rec.history == "pending"


def test_begin_live_source_without_schedule_starts_queued_like_97() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=LIVE1",
        source_identity="youtube:LIVE1",
    )
    session.commit()
    assert claim.recording.status == "queued"
    assert claim.recording.start_intent == "live_edge"


def test_duplicate_schedule_returns_the_same_waiter() -> None:
    session = _session()
    service = LiveRecordingService(session)
    first = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        start_intent="from_start",
    )
    session.commit()
    second = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=datetime(2026, 7, 20, 19, 0, 0),
        start_intent="live_edge",
    )
    session.commit()
    assert second.created is False
    assert second.recording.id == first.recording.id
    # The original intent is preserved; a duplicate submission never mutates it.
    assert second.recording.start_intent == "from_start"


def test_invalid_intent_or_fallback_is_rejected() -> None:
    session = _session()
    service = LiveRecordingService(session)
    for kwargs in ({"start_intent": "raw_flag"}, {"fallback_policy": "whatever"}):
        try:
            service.begin(
                user=_user(session),
                source_url="https://youtube.com/watch?v=UP1",
                source_identity="youtube:UP1",
                **kwargs,
            )
        except ValueError:
            session.rollback()
            continue
        raise AssertionError(f"expected ValueError for {kwargs!r}")


def test_mark_connecting_moves_waiting_to_queued_and_records_from_start_support() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
        start_intent="from_start",
    )
    session.commit()
    rec = service.mark_connecting(claim.recording.id, from_start_supported=True)
    session.commit()
    assert rec.status == "queued"
    assert rec.from_start_supported is True


def test_record_schedule_change_updates_the_provider_start_time() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
    )
    session.commit()
    moved = datetime(2026, 7, 20, 20, 30, 0)
    rec = service.record_schedule_change(claim.recording.id, moved)
    session.commit()
    assert rec.scheduled_start_at == moved
    assert rec.status == "waiting"


def test_fail_scheduled_records_reason_and_terminal_failed() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0),
    )
    session.commit()
    rec = service.fail_scheduled(claim.recording.id, reason="source_cancelled")
    session.commit()
    assert rec.status == "failed"
    assert rec.waiting_reason == "source_cancelled"
    assert rec.finished_at is not None


def test_fail_scheduled_from_start_unavailable_flags_awaiting_choice() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        start_intent="from_start",
        fallback_policy="require_choice",
    )
    session.commit()
    rec = service.fail_scheduled(
        claim.recording.id, reason="from_start_unavailable", awaiting_fallback_choice=True
    )
    session.commit()
    assert rec.status == "failed"
    assert rec.awaiting_fallback_choice is True
    assert rec.waiting_reason == "from_start_unavailable"


def test_set_media_status_persists_capture_origin_and_history() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        start_intent="from_start",
    )
    session.commit()
    rec = service.set_media_status(
        claim.recording.id,
        "partial",
        library_item_id="item-1",
        capture_origin="source_beginning",
        history="partial",
    )
    session.commit()
    assert rec.capture_origin == "source_beginning"
    assert rec.history == "partial"
    assert rec.library_item_id == "item-1"


def test_finalize_honors_history_partial_never_false_complete() -> None:
    session = _session()
    service = LiveRecordingService(session)
    claim = service.begin(
        user=_user(session),
        source_url="https://youtube.com/watch?v=UP1",
        source_identity="youtube:UP1",
        start_intent="from_start",
    )
    session.commit()
    rid = claim.recording.id
    service.mark_recording_started(rid)
    # Both outputs individually complete, but the from-start history is partial
    # (chat never reached the beginning): overall must be partial, never complete.
    service.set_media_status(rid, "completed", library_item_id="item-1", capture_origin="source_beginning", history="partial")
    service.set_chat_status(rid, "completed", chat_asset_id="chat-1")
    session.commit()
    rec = service.finalize(rid)
    session.commit()
    assert rec.status == "partial"
