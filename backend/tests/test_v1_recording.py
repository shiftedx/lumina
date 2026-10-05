"""Recording lifecycle end reasons, limits, crash recovery and retention."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app import main as main_module
from app.db import Base
from app.models import AppSettings, LibraryItem, LiveRecording, User
from app.schemas import LiveRecordingKeepRequest
from app.services.acquisition_batch import RedactedFailure
from app.services.library import LibraryService
from app.services.live_recording import LiveRecordingService, _now, sweep_recording_retention
from app.services.live_recording_adapters import FfmpegLiveMediaSink, GuardedLiveHlsRunner, GuardedLiveMediaRecorder
from app.services.live_recording_manager import LiveChatResult, LiveMediaResult, LiveRecordingManager
from app.services.live_recording_waiter import WaitDecision, WaitOutcome

MEDIA = "https://cdn.example/media.m3u8"


def _factory():
    Base.metadata.create_all(bind=db_module.engine)
    factory = sessionmaker(bind=db_module.engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    with factory.begin() as db:
        db.add(User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True))
    return factory


def _playlist(segments, *, ended=False):
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", "#EXT-X-MEDIA-SEQUENCE:0"]
    for seg in segments:
        lines += ["#EXTINF:4.0,", seg]
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class _Resolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        return {"manifest_url": MEDIA}


class _Fetch:
    def __init__(self, pages):
        self.pages = list(pages)

    def fetch_text(self, url, *, headers):  # noqa: ANN001
        if not self.pages:
            raise RuntimeError("edge gone")
        return self.pages.pop(0)

    def fetch_bytes(self, url, *, headers):  # noqa: ANN001
        return f"<{url.rsplit('/', 1)[-1]}>".encode()


class _Sink:
    def __init__(self):
        self.finalized = 0
        self.discarded = 0
        self.bytes = b""

    def append_init(self, data):  # noqa: ANN001
        self.bytes += data

    def append_segment(self, data, sequence=None):  # noqa: ANN001
        self.bytes += data

    def finalize(self):
        self.finalized += 1
        return "item-1"

    def discard(self):
        self.discarded += 1


class _NoChat:
    def capture(self, ctx):  # noqa: ANN001
        ctx.wait_for_end(timeout=5)
        return LiveChatResult(status="unavailable", covered_from_start=True)


def _guarded(fetch, sink_factory, **kwargs):
    runner = GuardedLiveHlsRunner(
        resolver=_Resolver(), fetch=fetch, sink_factory=sink_factory, poll_interval_seconds=0,
        max_runtime_seconds=kwargs.pop("max_runtime_seconds", 3600), max_disk_bytes=kwargs.pop("max_disk_bytes", 10_000_000),
    )
    return GuardedLiveMediaRecorder(runner=runner)


def _submit(manager):
    return manager.submit(user_id="user-a", source_url="https://youtube.com/watch?v=LIVE1", source_identity="youtube:LIVE1")


def _row(factory, recording_id) -> LiveRecording:
    with factory() as db:
        return db.get(LiveRecording, recording_id)


def test_recording_end_finalizes_once() -> None:
    factory = _factory()
    sink = _Sink()
    fetch = _Fetch([_playlist(["a.ts"]), _playlist(["a.ts", "b.ts"], ended=True)])
    manager = LiveRecordingManager(session_factory=factory, recorder=_guarded(fetch, lambda ctx: sink), chat_capturer=_NoChat())
    claim = _submit(manager)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert (row.status, row.media_status, row.library_item_id) == ("completed", "completed", "item-1")
    assert row.media_end_reason == "source_ended"
    assert sink.finalized == 1 and sink.discarded == 0
    assert sink.bytes == b"<a.ts><b.ts>"  # each segment appended exactly once


def test_recording_limit_and_owner_disable() -> None:
    factory = _factory()
    # Size limit: the capture stops at the ceiling and keeps what it recorded.
    sink = _Sink()
    fetch = _Fetch([_playlist(["a.ts", "b.ts", "c.ts"])])
    manager = LiveRecordingManager(
        session_factory=factory, recorder=_guarded(fetch, lambda ctx: sink, max_disk_bytes=10), chat_capturer=_NoChat()
    )
    claim = _submit(manager)
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert (row.status, row.media_status, row.media_end_reason) == ("partial", "partial", "size_limit")
    assert sink.finalized == 1

    # Owner disabled mid-capture: the monitor stops it and the partial is kept.
    class _UntilStopped:
        def record(self, ctx):  # noqa: ANN001
            ctx.wait_for_end(timeout=5)
            return LiveMediaResult(status="partial", library_item_id="item-2", capture_origin="live_edge", end_reason="stopped")

    manager = LiveRecordingManager(
        session_factory=factory, recorder=_UntilStopped(), chat_capturer=_NoChat(), resource_check_seconds=0.01
    )
    with factory.begin() as db:
        db.add(User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True))
    claim = manager.submit(user_id="user-b", source_url="https://youtube.com/watch?v=LIVE2", source_identity="youtube:LIVE2")
    with factory.begin() as db:
        db.get(User, "user-b").is_active = False
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording_id)
    assert (row.status, row.library_item_id, row.media_end_reason) == ("partial", "item-2", "owner_disabled")


def test_scheduled_not_started() -> None:
    factory = _factory()

    class _Recorder:
        calls = 0

        def record(self, ctx):  # noqa: ANN001
            self.calls += 1
            ctx.wait_for_end(timeout=5)
            return LiveMediaResult(status="partial", library_item_id="item-1", capture_origin="live_edge", end_reason="stopped")

    class _Waiter:
        def __init__(self, outcome, delay=0.0):
            self.outcome, self.delay = outcome, delay

        def wait(self, ctx):  # noqa: ANN001
            if ctx.wait_for_end(self.delay):
                return WaitDecision(WaitOutcome.MEMBER_CANCELLED)
            return WaitDecision(self.outcome)

    def schedule(manager, video):
        return manager.submit(
            user_id="user-a", source_url=f"https://youtube.com/watch?v={video}", source_identity=f"youtube:{video}",
            await_schedule=True,
        )

    # Timeout: the broadcast never went live -> honest failure, nothing recorded.
    recorder = _Recorder()
    manager = LiveRecordingManager(session_factory=factory, recorder=recorder, chat_capturer=_NoChat(), waiter=_Waiter(WaitOutcome.EXPIRED))
    expired = schedule(manager, "UP1")
    manager.wait_all(timeout=5)
    row = _row(factory, expired.recording_id)
    assert (row.status, row.waiting_reason, row.library_item_id, recorder.calls) == ("failed", "excessive_delay", None, 0)

    # Cancel while waiting -> cancelled, never a fabricated recording.
    manager = LiveRecordingManager(session_factory=factory, recorder=recorder, chat_capturer=_NoChat(), waiter=_Waiter(WaitOutcome.READY, 5))
    waiting = schedule(manager, "UP2")
    manager.cancel("user-a", waiting.recording_id)
    manager.wait_all(timeout=5)
    row = _row(factory, waiting.recording_id)
    assert (row.status, row.library_item_id, recorder.calls) == ("cancelled", None, 0)

    # A wait longer than the runtime ceiling does not eat the capture budget:
    # the recording starts, then stops at its own time limit.
    manager = LiveRecordingManager(
        session_factory=factory, recorder=recorder, chat_capturer=_NoChat(),
        waiter=_Waiter(WaitOutcome.READY, 0.2), max_runtime_seconds=0.1,
    )
    late = schedule(manager, "UP3")
    manager.wait_all(timeout=5)
    row = _row(factory, late.recording_id)
    assert (row.status, row.media_end_reason, recorder.calls) == ("partial", "time_limit", 1)


def test_restart_recording_ledger(tmp_path, monkeypatch) -> None:
    factory = _factory()
    remuxed: list[bytes] = []

    def fake_remux(self, out_path):  # noqa: ANN001
        remuxed.append(self._raw_path.read_bytes())
        out_path.write_bytes(b"mp4")
        return True

    monkeypatch.setattr(FfmpegLiveMediaSink, "_remux", fake_remux)
    monkeypatch.setattr(FfmpegLiveMediaSink, "_publish", lambda self, staged: f"item-{len(remuxed)}")

    def sink_factory(ctx):  # noqa: ANN001
        return FfmpegLiveMediaSink(ctx, session_factory=factory, events=None, temp_root=tmp_path, ffmpeg_path=None)

    def crashed_recording(video):
        with factory() as db:
            service = LiveRecordingService(db)
            claim = service.begin(user=db.get(User, "user-a"), source_url=f"https://youtube.com/watch?v={video}", source_identity=f"youtube:{video}")
            service.claim_for_run(claim.recording.id)
            service.mark_recording_started(claim.recording.id)
            db.commit()
        (tmp_path / "live-recordings").mkdir(exist_ok=True)
        (tmp_path / "live-recordings" / f"{claim.recording.id}.raw").write_bytes(b"<init><pre-crash>")
        return claim.recording.id

    # Resume: the new run appends after the pre-crash bytes (no second init),
    # publishes ONE item, and is honestly partial with a restart gap.
    resumed_id = crashed_recording("LIVE1")
    fetch = _Fetch([_playlist(["x.ts"], ended=True)])
    manager = LiveRecordingManager(session_factory=factory, recorder=_guarded(fetch, sink_factory), chat_capturer=_NoChat())
    manager.recover()
    manager.wait_all(timeout=5)
    row = _row(factory, resumed_id)
    assert remuxed == [b"<init><pre-crash><x.ts>"]
    assert (row.status, row.media_status, row.library_item_id) == ("partial", "partial", "item-1")
    assert main_module.serialize_live_recording(row).resumed_after_restart is True

    # Stopped before the crash: recovery salvages the leftover bytes into one
    # interrupted partial instead of reporting a false total failure.
    stopped_id = crashed_recording("LIVE2")
    with factory.begin() as db:
        LiveRecordingService(db).request_stop(db.get(User, "user-a"), stopped_id)
    manager = LiveRecordingManager(session_factory=factory, recorder=_guarded(_Fetch([]), sink_factory), chat_capturer=_NoChat())
    manager.recover()
    manager.wait_all(timeout=5)
    manager.recover()  # a second restart finds it terminal: no duplicate output
    manager.wait_all(timeout=5)
    row = _row(factory, stopped_id)
    assert (row.status, row.library_item_id, row.media_end_reason) == ("partial", "item-2", "interrupted")
    assert len(remuxed) == 2
    assert not list((tmp_path / "live-recordings").glob("*.raw"))


def test_resume_with_a_changed_init_segment_publishes_only_the_pre_restart_part(tmp_path, monkeypatch) -> None:
    factory = _factory()
    remuxed: list[bytes] = []

    def fake_remux(self, out_path):  # noqa: ANN001
        remuxed.append(self._raw_path.read_bytes())
        out_path.write_bytes(b"mp4")
        return True

    monkeypatch.setattr(FfmpegLiveMediaSink, "_remux", fake_remux)
    monkeypatch.setattr(FfmpegLiveMediaSink, "_publish", lambda self, staged: "item-1")
    with factory() as db:
        service = LiveRecordingService(db)
        claim = service.begin(user=db.get(User, "user-a"), source_url="https://youtube.com/watch?v=LIVE1", source_identity="youtube:LIVE1")
        service.claim_for_run(claim.recording.id)
        service.mark_recording_started(claim.recording.id)
        db.commit()
    (tmp_path / "live-recordings").mkdir()
    (tmp_path / "live-recordings" / f"{claim.recording.id}.raw").write_bytes(b"<init.mp4><s0.m4s>")

    # After the restart the source serves a different variant's init segment.
    page = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:9\n#EXT-X-MAP:URI=\"other.mp4\"\n#EXTINF:4.0,\ns9.m4s\n"
    recorder = _guarded(_Fetch([page]), lambda ctx: FfmpegLiveMediaSink(ctx, session_factory=factory, events=None, temp_root=tmp_path, ffmpeg_path=None))
    manager = LiveRecordingManager(session_factory=factory, recorder=recorder, chat_capturer=_NoChat())
    manager.recover()
    manager.wait_all(timeout=5)
    row = _row(factory, claim.recording.id)
    assert remuxed == [b"<init.mp4><s0.m4s>"]
    assert (row.media_status, row.library_item_id, row.media_end_reason) == ("partial", "item-1", "interrupted")


def test_retention_sweep_spares_kept_recordings(monkeypatch) -> None:
    factory = _factory()
    deleted: list[str] = []

    def fake_delete(self, item):  # noqa: ANN001
        item.status = "missing"
        deleted.append(item.id)
        return item

    monkeypatch.setattr(LibraryService, "delete_file", fake_delete)
    old = _now() - timedelta(days=10)
    with factory.begin() as db:
        db.add(AppSettings(id=1, temp_root="/tmp/x", archive_path="/tmp/x/a", recording_keep_days=7, recording_max_gb=1))
        for name, kept, finished, size in [
            ("old", False, old, 1), ("old-kept", True, old, 1),
            ("new-kept", True, _now(), 2**30), ("new", False, _now() - timedelta(hours=1), 10),
        ]:
            db.add(LibraryItem(id=f"item-{name}", title=name, status="available", file_size=size, user_id="user-a"))
            db.add(LiveRecording(
                id=f"rec-{name}", user_id="user-a", source_url="u", source_identity=f"youtube:{name}",
                source_identity_key=name, status="partial", library_item_id=f"item-{name}", kept=kept, finished_at=finished,
            ))
    with factory() as db:
        assert sweep_recording_retention(db) == 2
        db.commit()
        assert sweep_recording_retention(db) == 0
    # "old" expired; "new" pushed the total past 1 GB behind a kept recording.
    assert sorted(deleted) == ["item-new", "item-old"]

    with factory() as db:
        response = main_module.keep_live_recording("rec-old", LiveRecordingKeepRequest(kept=True), db.get(User, "user-a"), db)
    assert response.kept is True


def test_media_failure_does_not_leave_chat_running() -> None:
    factory = _factory()

    class _Fails:
        def record(self, ctx):  # noqa: ANN001
            return LiveMediaResult(status="failed", failure=RedactedFailure("media_failed", "x"))

    started = time.monotonic()
    manager = LiveRecordingManager(session_factory=factory, recorder=_Fails(), chat_capturer=_NoChat())
    claim = _submit(manager)
    manager.wait_all(timeout=5)
    assert time.monotonic() - started < 2  # chat wound down with media, not after its 5s wait
    assert _row(factory, claim.recording_id).status == "failed"
