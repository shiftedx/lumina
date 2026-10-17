from __future__ import annotations

from threading import Barrier, Event, Lock, Thread

import pytest
from sqlalchemy import select

from app.events import EventBus
from app.models import DownloadJob, LiveRecording, User
from app.services import job_manager as job_manager_module
from app.services.job_manager import JobAdmissionError, JobManager
from app.services.live_recording_manager import LiveChatResult, LiveMediaResult, LiveRecordingManager, RecordingContext
from support import file_backed_session_factory, seed_app_settings


def _job(job_id: str, user_id: str, *, status: str = "queued") -> DownloadJob:
    return DownloadJob(
        id=job_id,
        user_id=user_id,
        source_url=f"https://example.test/{job_id}",
        status=status,
        format_selection={},
        output_profile={},
    )


def test_concurrent_outbox_staging_admits_only_one_member_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = file_backed_session_factory("member-a")
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    with sessions() as db:
        seed_app_settings(db, resource_policy={"defaults": {"download_queued": 1}})

    barrier = Barrier(2)
    lock = Lock()
    receipts: list[str] = []
    failures: list[Exception] = []

    def stage(key: str) -> None:
        barrier.wait()
        try:
            receipt = manager.acquisition.ensure_dispatched(
                idempotency_key=key,
                batch_id="batch",
                batch_entry_id=key,
                user_id="member-a",
                source_url=f"https://example.test/{key}",
                format_selection={},
                output_profile={},
                preview_snapshot={},
            )
            with lock:
                receipts.append(receipt.download_job_id)
        except Exception as exc:  # assertion below verifies the only rejection kind
            with lock:
                failures.append(exc)

    workers = [Thread(target=stage, args=(key,)) for key in ("first", "second")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert len(receipts) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], JobAdmissionError)
    with sessions() as db:
        assert db.scalars(select(DownloadJob).where(DownloadJob.status == "staged")).all()[0].id == receipts[0]


def test_concurrent_workers_claim_members_round_robin_with_member_fifo(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = file_backed_session_factory("member-a", "member-b")
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    manager = JobManager(EventBus())
    with sessions() as db:
        seed_app_settings(db, resource_policy={"defaults": {"download_running": 1}})
        db.add_all([_job("a-one", "member-a"), _job("a-two", "member-a"), _job("b-one", "member-b"), _job("b-two", "member-b")])
        db.commit()

    def claim_pair() -> set[str]:
        claimed: list[str | None] = []
        threads = [Thread(target=lambda: claimed.append(manager._claim_next_queued_job())) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return {job_id for job_id in claimed if job_id is not None}

    assert claim_pair() == {"a-one", "b-one"}
    with sessions() as db:
        for job_id in ("a-one", "b-one"):
            db.get(DownloadJob, job_id).status = "completed"
        db.commit()
    assert claim_pair() == {"a-two", "b-two"}


class _HoldingRecorder:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def record(self, ctx: RecordingContext) -> LiveMediaResult:
        self.started.set()
        self.release.wait(timeout=5)
        return LiveMediaResult(status="completed", library_item_id="recorded-item")


class _CompletedChat:
    def capture(self, _ctx: RecordingContext) -> LiveChatResult:
        return LiveChatResult(status="completed", chat_asset_id="recorded-chat")


def test_live_completion_wakes_blocked_download_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = file_backed_session_factory("member-a")
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    jobs = JobManager(EventBus())
    recorder = _HoldingRecorder()
    recordings = LiveRecordingManager(session_factory=sessions, recorder=recorder, chat_capturer=_CompletedChat())
    recordings.set_capacity_waker(jobs.wake_queued)
    with sessions() as db:
        seed_app_settings(db, resource_policy={"defaults": {"download_running": 1}})

    submission = recordings.submit(
        user_id="member-a", source_url="https://example.test/live", source_identity="example:live",
    )
    assert recorder.started.wait(timeout=5)
    with sessions() as db:
        assert db.get(LiveRecording, submission.recording_id).status == "live"
        db.add(_job("blocked-download", "member-a"))
        db.commit()
    assert jobs._queue.empty()

    recorder.release.set()
    recordings.wait_all(timeout=5)

    assert jobs._queue.get_nowait() == "blocked-download"


def test_legacy_active_ceiling_still_rejects_staged_outbox_work(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = file_backed_session_factory("member-a")
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    manager = JobManager(EventBus())
    with sessions() as db:
        seed_app_settings(
            db,
            max_active_jobs_per_user=1,
            resource_policy={"defaults": {"download_queued": 0}},
        )
        db.add(_job("existing", "member-a", status="staged"))
        db.commit()
        with pytest.raises(JobAdmissionError, match="queued or running"):
            manager.admit_staged(db, user_id="member-a", preview_snapshot={}, format_selection={})


def test_cancelled_queued_download_releases_member_waiting_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = file_backed_session_factory("member-a")
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    manager = JobManager(EventBus())
    with sessions() as db:
        seed_app_settings(db, resource_policy={"defaults": {"download_queued": 1}})
        queued = _job("queued", "member-a")
        db.add(queued)
        db.commit()
        manager.cancel(db, queued.id, db.get(User, "member-a"))
        manager.admit_staged(db, user_id="member-a", preview_snapshot={}, format_selection={})
