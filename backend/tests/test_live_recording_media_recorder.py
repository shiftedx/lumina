"""Media recorder outcome mapping for live recordings (issue #97).

The recorder maps a guarded media run to the sibling media output's terminal
state: a completed run publishes a Library item, a deliberate stop finalizes the
usable partial, an abrupt cancel discards it, and any failure is a redacted media
failure. The yt-dlp-facing run is behind an injected runner so this maps outcomes
without live YouTube; the real runner flows through the guarded transport.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from app.services.live_recording_adapters import (
    GuardedLiveMediaRecorder,
    LiveMediaCancelled,
    MediaRunOutcome,
    recording_library_info,
)
from app.services.live_recording_manager import RecordingContext


def _ctx():
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
    )


class _Runner:
    def __init__(self, behavior):
        self._behavior = behavior

    def run(self, ctx):
        return self._behavior(ctx)


def test_source_end_run_completes() -> None:
    # Only a run that reached true source-end reports completed.
    recorder = GuardedLiveMediaRecorder(
        runner=_Runner(lambda ctx: MediaRunOutcome(library_item_id="item-9", reached_source_end=True))
    )
    result = recorder.record(_ctx())
    assert result.status == "completed"
    assert result.library_item_id == "item-9"


def test_usable_capture_without_source_end_is_partial_not_completed() -> None:
    # A deliberate stop / bounded window / interruption: the real runner returns
    # a usable Library item but reached_source_end=False. Reporting completed here
    # would be a false complete (review fix 1b), so the honest mapping is partial.
    recorder = GuardedLiveMediaRecorder(
        runner=_Runner(lambda ctx: MediaRunOutcome(library_item_id="item-partial", reached_source_end=False))
    )
    result = recorder.record(_ctx())
    assert result.status == "partial"
    assert result.library_item_id == "item-partial"


def test_cancel_discards_without_publishing() -> None:
    def behavior(ctx):
        raise LiveMediaCancelled()

    recorder = GuardedLiveMediaRecorder(runner=_Runner(behavior))
    result = recorder.record(_ctx())
    assert result.status == "failed"
    assert result.library_item_id is None
    assert result.failure.category == "media_cancelled"


def test_no_media_produced_is_a_failure() -> None:
    recorder = GuardedLiveMediaRecorder(runner=_Runner(lambda ctx: MediaRunOutcome(library_item_id=None)))
    result = recorder.record(_ctx())
    assert result.status == "failed"
    assert result.failure.category == "media_failed"


def test_unexpected_error_is_a_redacted_media_failure() -> None:
    def behavior(ctx):
        raise RuntimeError("upstream 403 with secret token in message")

    recorder = GuardedLiveMediaRecorder(runner=_Runner(behavior))
    result = recorder.record(_ctx())
    assert result.status == "failed"
    assert result.failure.category == "media_failed"
    # The upstream exception text never crosses the boundary.
    assert "secret token" not in result.failure.message


# --- publish-time pointer (issue #112) --------------------------------------

def test_publish_records_the_recording_pointer_in_the_same_commit(tmp_path) -> None:
    # Closing the mid-capture crash orphan window: the Library item insert and
    # the recording row's `library_item_id` pointer must be ONE durable commit.
    # Were they separate commits, a crash between them would leave a published
    # item no `live` row points at, and restart recovery would RESUME and
    # re-capture — orphaning the first item.
    from sqlalchemy import create_engine, event as sa_event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import LibraryItem, LiveRecording, User

    # Importing the library service registers the search-table metadata listener,
    # so create_all below also creates the FTS side tables the publish touches.
    from app.services.library import LibraryService  # noqa: F401
    from app.services.live_recording import LiveRecordingService
    from app.services.live_recording_adapters import FfmpegLiveMediaSink

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
    )
    with factory.begin() as session:
        session.add(User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True))
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

    commits: list[bool] = []

    def spy_factory():
        session = factory()

        @sa_event.listens_for(session, "after_commit")
        def _count(sess):  # noqa: ANN001
            commits.append(True)

        return session

    ctx = _ctx()
    ctx.recording_id = recording_id
    sink = FfmpegLiveMediaSink(
        ctx,
        session_factory=spy_factory,
        events=None,
        temp_root=tmp_path / "tmp",
        ffmpeg_path=None,
    )
    staged = tmp_path / "tmp" / f"live-{recording_id}.mp4"
    staged.write_bytes(b"remuxed")
    try:
        item_id = sink._publish(staged)
    finally:
        sink.discard()
    assert item_id is not None
    # One durable commit covers both the item and the pointer: no crash point
    # exists where the item is published but the recording does not point at it.
    assert len(commits) == 1
    with factory() as session:
        row = session.get(LiveRecording, recording_id)
        item = session.get(LibraryItem, item_id)
    assert item is not None
    assert row.library_item_id == item_id
    # Floored to a usable partial until the finalize-outcome commit resolves it.
    assert row.media_status == "partial"


# --- provider-aware publish (issue #100) ------------------------------------

def test_recording_library_info_uses_the_source_provider() -> None:
    # A recorded Twitch broadcast publishes its Library item under the Twitch
    # provider identity (never a hardcoded YouTube one), so the completed item and
    # its captured chat asset share one source identity.
    twitch = recording_library_info("twitch:12345", "https://twitch.tv/coolstreamer", "/vault/live-rec-1.mp4")
    assert twitch["extractor"] == "twitch"
    assert twitch["extractor_key"] == "Twitch"
    assert twitch["id"] == "12345"
    assert twitch["webpage_url"] == "https://twitch.tv/coolstreamer"
    assert twitch["filepath"] == "/vault/live-rec-1.mp4"

    youtube = recording_library_info("youtube:LIVE1", "https://youtube.com/watch?v=LIVE1", "/vault/live-rec-2.mp4")
    assert youtube["extractor"] == "youtube"
    assert youtube["extractor_key"] == "Youtube"
    assert youtube["id"] == "LIVE1"
