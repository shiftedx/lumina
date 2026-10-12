import asyncio
import time
import uuid
from datetime import datetime
from threading import Lock

from app.events import EventBus
from app.models import DownloadJob
from app.schemas import JobCreateRequest
from app.services import job_manager as job_manager_module
from app.services.job_manager import JobManager
from support import make_user, memory_session_factory


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
