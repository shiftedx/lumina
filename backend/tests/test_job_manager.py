import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from types import SimpleNamespace

import pytest

from app.events import EventBus
from app.models import DownloadJob, LiveRecording
from app.schemas import FormatResolutionState, JobCreateRequest
from app.services import job_manager as job_manager_module
from app.services.format_resolution import AcquisitionResult
from app.services.job_manager import JobAdmissionError, JobManager
from app.services.storage_routing import RouteDecision, StorageRoutingService
from app.services.yt_dlp_service import DownloadCancelled, YtDlpService
from support import file_backed_session_factory, make_user, memory_session_factory, seed_app_settings


class CapturingEvents:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


make_session_factory = memory_session_factory


def test_job_manager_start_stop() -> None:
    async def runner() -> None:
        job_manager_module.SessionLocal = make_session_factory()
        manager = JobManager(EventBus())
        await manager.start()
        assert manager._worker_task is not None
        await manager.stop()
        assert manager._worker_task is None

    asyncio.run(runner())


def test_job_manager_starts_the_configured_number_of_workers() -> None:
    async def runner() -> None:
        job_manager_module.SessionLocal = make_session_factory()
        manager = JobManager(EventBus())
        await manager.start(concurrency=3)
        assert len(manager._worker_tasks) == 3
        await manager.stop()
        assert manager._worker_tasks == []

    asyncio.run(runner())


def test_job_manager_worker_pool_enforces_measured_peak_concurrency() -> None:
    async def measure(concurrency: int) -> int:
        job_manager_module.SessionLocal = make_session_factory()
        manager = JobManager(EventBus())
        state = {"active": 0, "peak": 0}
        lock = Lock()

        def run_job(job_id: str) -> None:
            del job_id
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.08)
            with lock:
                state["active"] -= 1

        manager._run_job = run_job  # type: ignore[method-assign]
        await manager.start(concurrency=concurrency)
        manager._enqueue_job_id("fixture-one")
        manager._enqueue_job_id("fixture-two")
        await asyncio.wait_for(manager._queue.join(), timeout=2)
        await manager.stop()
        return state["peak"]

    assert asyncio.run(measure(1)) == 1
    assert asyncio.run(measure(2)) == 2


def test_clear_completed_only_removes_completed_jobs() -> None:
    session_factory = make_session_factory()
    job_manager_module.SessionLocal = session_factory
    manager = JobManager(EventBus())

    with session_factory() as session:
        user = make_user(str(uuid.uuid4()), role="admin", username="admin", display_name="Admin", password_hash="hash")
        session.add(user)
        session.add_all(
            [
                DownloadJob(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    source_url="https://example.com/a",
                    status="completed",
                    created_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                ),
                DownloadJob(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    source_url="https://example.com/b",
                    status="completed",
                    created_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                ),
                DownloadJob(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    source_url="https://example.com/c",
                    status="running",
                    created_at=datetime.utcnow(),
                ),
            ]
        )
        session.commit()

        deleted = manager.clear_completed(session, user)
        remaining_statuses = [job.status for job in session.query(DownloadJob).order_by(DownloadJob.created_at.asc()).all()]

        assert deleted == 2
        assert remaining_statuses == ["running"]


def test_job_event_payload_exposes_user_id_for_sse_filtering() -> None:
    job = DownloadJob(
        id=str(uuid.uuid4()),
        user_id="user-1",
        source_url="https://example.com/video",
        status="running",
        format_selection={},
        output_profile={},
        created_at=datetime.utcnow(),
    )

    payload = JobManager._job_event_payload(job, progress=42.5)

    assert payload["user_id"] == "user-1"
    assert payload["job"]["user_id"] == "user-1"
    assert payload["job"]["progress"] == 42.5


def test_enqueue_and_cancel_publish_user_scoped_job_events() -> None:
    session_factory = make_session_factory()
    job_manager_module.SessionLocal = session_factory
    events = CapturingEvents()
    manager = JobManager(events)  # type: ignore[arg-type]

    with session_factory() as session:
        user = make_user("user-1", role="admin", username="admin", display_name="Admin", password_hash="hash")
        session.add(user)
        session.commit()

        job = manager.enqueue(session, JobCreateRequest(source_url="https://example.com/video"), user)
        manager.cancel(session, job.id, user)

    assert [event_type for event_type, _payload in events.published] == ["job_queued", "job_failed"]
    assert all(payload["user_id"] == "user-1" for _event_type, payload in events.published)
    assert all(payload["job"]["user_id"] == "user-1" for _event_type, payload in events.published)


def test_staged_acquisition_rows_consume_the_member_waiting_allowance() -> None:
    sessions = make_session_factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    with sessions() as db:
        user = make_user("member-a")
        db.add(user)
        seed_app_settings(db, resource_policy={"defaults": {"download_queued": 1}})
        db.add(DownloadJob(
            id="already-staged", user_id=user.id, source_url="https://example.com/already",
            status="staged", format_selection={}, output_profile={},
        ))
        db.commit()

        with pytest.raises(JobAdmissionError, match="downloads waiting"):
            with job_manager_module.write_transaction(db, name="test_stage_limit"):
                manager.admit_staged(
                    db, user_id=user.id, preview_snapshot={}, format_selection={},
                )


def test_dispatch_claims_members_round_robin_and_reserves_the_running_slot() -> None:
    sessions = make_session_factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    with sessions() as db:
        db.add_all([make_user("member-a"), make_user("member-b")])
        seed_app_settings(db)
        db.add_all([
            DownloadJob(id="a-one", user_id="member-a", source_url="https://example.com/a-one", status="queued", format_selection={}, output_profile={}),
            DownloadJob(id="a-two", user_id="member-a", source_url="https://example.com/a-two", status="queued", format_selection={}, output_profile={}),
            DownloadJob(id="b-one", user_id="member-b", source_url="https://example.com/b-one", status="queued", format_selection={}, output_profile={}),
            DownloadJob(id="b-two", user_id="member-b", source_url="https://example.com/b-two", status="queued", format_selection={}, output_profile={}),
        ])
        db.commit()

    claimed: list[str] = []
    for _ in range(4):
        job_id = manager._claim_next_queued_job()
        assert job_id is not None
        claimed.append(job_id)
        with sessions() as db, job_manager_module.write_transaction(db, name="test_finish_claim"):
            db.get(DownloadJob, job_id).status = "completed"

    assert claimed == ["a-one", "b-one", "a-two", "b-two"]
    with sessions() as db:
        assert all(db.get(DownloadJob, job_id).started_at is not None for job_id in claimed)


def test_concurrent_dispatch_claims_respect_one_member_running_allowance() -> None:
    sessions = file_backed_session_factory("member-a")
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    with sessions() as db:
        seed_app_settings(db, resource_policy={"defaults": {"download_running": 1}})
        db.add_all([
            DownloadJob(id="first", user_id="member-a", source_url="https://example.com/first", status="queued", format_selection={}, output_profile={}),
            DownloadJob(id="second", user_id="member-a", source_url="https://example.com/second", status="queued", format_selection={}, output_profile={}),
        ])
        db.commit()

    claimed: list[str | None] = []
    workers = [Thread(target=lambda: claimed.append(manager._claim_next_queued_job())) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert len([job_id for job_id in claimed if job_id is not None]) == 1
    with sessions() as db:
        assert db.query(DownloadJob).filter(DownloadJob.status == "running").count() == 1
        assert db.query(DownloadJob).filter(DownloadJob.status == "queued").count() == 1


def test_download_dispatch_stays_queued_while_member_recording_is_capturing() -> None:
    sessions = make_session_factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    with sessions() as db:
        db.add(make_user("member-a"))
        seed_app_settings(db, resource_policy={"defaults": {"download_running": 1}})
        db.add_all([
            DownloadJob(id="queued-download", user_id="member-a", source_url="https://example.com/download", status="queued", format_selection={}, output_profile={}),
            LiveRecording(
                id="live-recording", user_id="member-a", source_url="https://example.com/live",
                source_identity="youtube:live", source_identity_key="live-key", status="live",
            ),
        ])
        db.commit()

    assert manager._claim_next_queued_job() is None
    with sessions() as db:
        assert db.get(DownloadJob, "queued-download").status == "queued"


@pytest.fixture
def attempt_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sessions = make_session_factory()
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    manager = JobManager(EventBus())
    root = SimpleNamespace(id="root", path=str(tmp_path))
    clock = [10.0]
    with sessions() as db:
        db.add(make_user("private-member"))
        seed_app_settings(db, min_free_disk_mb=0)
        db.add(DownloadJob(
            id="timed-attempt", user_id="private-member",
            source_url="https://private.example/video?token=secret-signed-value", status="queued",
            format_selection={}, output_profile={}, preview_snapshot={"title": "Private title"},
        ))
        db.commit()
    monkeypatch.setattr(job_manager_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        job_manager_module, "_requested_route",
        lambda db, preview, selection: RouteDecision(None, 0, root.id, "", "test"),
    )
    monkeypatch.setattr(StorageRoutingService, "writable_root", lambda self, decision: root)
    monkeypatch.setattr(job_manager_module, "_free_bytes", lambda path: 10**12)
    monkeypatch.setattr(job_manager_module.TranscriptService, "ingest_download_captions", lambda self, sources: None)
    monkeypatch.setattr(job_manager_module.WebhookService, "notify_job_failed", lambda self, payload: None)
    return manager, sessions, clock


def test_attempt_timing_reports_acquisition_hooks_and_publication_without_private_input(
    attempt_env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:  # noqa: ANN001
    manager, sessions, clock = attempt_env

    def download(_self, private_url, *, output_root, progress_hooks, postprocessor_hooks, **kwargs):  # noqa: ANN001, ANN202
        assert "secret-signed-value" in private_url and kwargs["owner_user_id"] == "private-member"
        clock[0] = 10.2
        progress_hooks[0]({"downloaded_bytes": 1, "total_bytes": 2})
        clock[0] = 10.5
        postprocessor_hooks[0]({"status": "started", "postprocessor": "Private merger"})
        clock[0] = 12.0
        media = output_root / "Private title.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"media")
        return AcquisitionResult(
            info={"id": "private-id", "title": "Private title", "ext": "mp4", "filepath": str(media)},
            format_resolution=FormatResolutionState(requested_selector="bestvideo+bestaudio/best"),
        )

    def publish(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        clock[0] = 13.5
        return SimpleNamespace(id="private-library-id")

    monkeypatch.setattr(YtDlpService, "download", download)
    monkeypatch.setattr(job_manager_module, "publish_file", publish)
    caplog.set_level(logging.INFO, logger="lumina.download")

    manager._run_attempt("timed-attempt")

    records = [record for record in caplog.records if record.name == "lumina.download"]
    assert [record.getMessage() for record in records] == [
        "attempt_timing status=completed acquisition_ms=2000 first_progress_ms=200 "
        "first_postprocess_ms=500 publication_ms=1500"
    ]
    assert not any(private in records[0].getMessage() for private in (
        "private.example", "secret-signed-value", "Private title", "private-member", "Private merger",
    ))
    with sessions() as db:
        assert db.get(DownloadJob, "timed-attempt").status == "completed"


@pytest.mark.parametrize(
    ("failure", "stopping", "status"),
    [
        (RuntimeError("private title /private/path token=secret-value"), False, "failed"),
        (DownloadCancelled("private cancellation detail"), False, "cancelled"),
        (RuntimeError("private shutdown detail"), True, "interrupted"),
    ],
)
def test_attempt_timing_has_numeric_defaults_without_hooks_for_every_terminal_failure(
    attempt_env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    failure: Exception, stopping: bool, status: str,
) -> None:  # noqa: ANN001
    manager, sessions, clock = attempt_env
    manager._stopping = stopping

    def download(_self, *_args, **_kwargs):  # noqa: ANN001, ANN202
        clock[0] = 10.75
        raise failure

    monkeypatch.setattr(YtDlpService, "download", download)
    caplog.set_level(logging.INFO, logger="lumina.download")

    manager._run_attempt("timed-attempt")

    records = [record for record in caplog.records if record.name == "lumina.download"]
    assert [record.getMessage() for record in records] == [
        f"attempt_timing status={status} acquisition_ms=750 first_progress_ms=-1 "
        "first_postprocess_ms=-1 publication_ms=-1"
    ]
    assert "private" not in records[0].getMessage()
    with sessions() as db:
        assert db.get(DownloadJob, "timed-attempt").status == status
