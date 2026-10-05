"""S17/F09: retry admits only terminal failures, atomically, and keeps attempt history."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.events import EventBus
from app.main import app
from app.models import DownloadJob, User
from app.security import get_current_user
from app.services.job_manager import JobConflictError, JobManager
from app.services.yt_dlp_service import YtDlpService

STARTED = datetime(2026, 1, 1, 10, 0, 0)
FINISHED = datetime(2026, 1, 1, 10, 5, 0)


@pytest.fixture
def fixture(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retry.sqlite3'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    user = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    with factory.begin() as db:
        db.add(user)
    yield factory, user, JobManager(EventBus())
    engine.dispose()


def _add_job(factory, status: str, *, job_id: str = "job-1", error: str | None = None) -> None:  # noqa: ANN001
    with factory.begin() as db:
        db.add(
            DownloadJob(
                id=job_id, user_id="member-1", source_url="https://example.com/v", status=status,
                format_selection={}, output_profile={}, error=error, started_at=STARTED, finished_at=FINISHED,
            )
        )


@pytest.mark.parametrize("status", ["queued", "running", "postprocessing", "completed"])
def test_retry_running_rejected(fixture, status: str) -> None:  # noqa: ANN001
    factory, user, manager = fixture
    _add_job(factory, status)
    flag = manager._cancel_flags.setdefault("job-1", __import__("threading").Event())
    with factory() as db, pytest.raises(JobConflictError):
        manager.retry(db, "job-1", user)
    with factory() as db:
        job = db.get(DownloadJob, "job-1")
        assert (job.status, job.started_at, job.finished_at, job.attempts) == (status, STARTED, FINISHED, [])
    assert manager._cancel_flags["job-1"] is flag
    assert manager._queue.qsize() == 0


def test_retry_running_rejected_over_http_with_409(fixture) -> None:  # noqa: ANN001
    factory, user, manager = fixture
    _add_job(factory, "running")

    def override_db():
        with factory() as db:
            yield db
            db.commit()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app, base_url="http://localhost").post("/api/jobs/job-1/retry")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_double_retry_one_attempt(fixture) -> None:  # noqa: ANN001
    factory, user, manager = fixture
    _add_job(factory, "failed", error="HTTP 403")
    barrier = Barrier(4)

    def retry() -> str:
        with factory() as db:
            barrier.wait()
            try:
                return manager.retry(db, "job-1", user).status
            except JobConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = sorted(pool.map(lambda _: retry(), range(4)))
    assert outcomes == ["conflict", "conflict", "conflict", "queued"]
    assert manager._queue.qsize() == 1
    with factory() as db:
        assert len(db.get(DownloadJob, "job-1").attempts) == 1


def test_failed_retry_keeps_history(fixture) -> None:  # noqa: ANN001
    factory, user, manager = fixture
    _add_job(factory, "failed", error="HTTP 403: Forbidden")
    with factory() as db:
        job = manager.retry(db, "job-1", user)
        payload = JobManager.serialize(job)
    assert payload.status == "queued" and payload.error is None and payload.started_at is None
    assert [attempt.model_dump() for attempt in payload.attempts] == [
        {"status": "failed", "error": "HTTP 403: Forbidden", "started_at": STARTED, "finished_at": FINISHED}
    ]
    # A second failure appends; earlier evidence is never rewritten.
    with factory.begin() as db:
        db.get(DownloadJob, "job-1").status = "cancelled"
    with factory() as db:
        attempts = manager.retry(db, "job-1", user).attempts
    assert [attempt["status"] for attempt in attempts] == ["failed", "cancelled"]
    assert attempts[0]["error"] == "HTTP 403: Forbidden"


# test_batch_retry_selected_failures_only: covered by
# test_acquisition_collections_api (retry creates a new batch holding only the
# failed entry) and test_acquisition_job_manager (linked jobs refuse ordinary retry).
