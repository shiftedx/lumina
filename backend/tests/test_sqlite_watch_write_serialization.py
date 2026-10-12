import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, RLock
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.persistence as persistence
from app.db import Base, get_db
from app.events import EventBus
from app.main import app
from app.models import AppSession, DownloadJob, RemotePlaybackProgress, User, UserSettings
from app.persistence import write_transaction
from app.schemas import RemotePlaybackProgressUpdateRequest, UserSettingsUpdateRequest
from app.security import create_app_session, get_current_user, hash_password
from app.services.job_manager import JobManager
from app.services.rate_limit import rate_limiter
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.user_settings import UserSettingsService


class ObservedWriterLock:
    """Expose when a second request has reached the serialized writer queue."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counter_lock = Lock()
        self._acquire_attempts = 0
        self.second_acquire_attempted = Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        with self._counter_lock:
            self._acquire_attempts += 1
            if self._acquire_attempts == 2:
                self.second_acquire_attempted.set()
        return self._lock.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        self._lock.release()


def test_watch_checkpoint_and_autoplay_preference_writes_do_not_contend(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The watch page saves these independently, so one request must not lock out the other."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'watch-contention.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=50")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    user = User(id="watch-user", username="watcher", display_name="Watcher", role="viewer", is_active=True)
    with session_factory.begin() as session:
        session.add(user)

    def override_db():
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    rate_limiter.clear()
    observed_lock = ObservedWriterLock()
    monkeypatch.setattr(persistence, "writer_admission_lock", observed_lock)
    checkpoint_flushed = Event()
    release_checkpoint = Event()
    original_update = RemotePlaybackProgressService.update

    def hold_checkpoint_transaction(self, source_identity, payload, current_user):  # noqa: ANN001
        progress = original_update(self, source_identity, payload, current_user)
        checkpoint_flushed.set()
        assert release_checkpoint.wait(timeout=2)
        return progress

    monkeypatch.setattr(RemotePlaybackProgressService, "update", hold_checkpoint_transaction)
    checkpoint_client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    settings_client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    identity = "youtube:concurrent-watch"
    endpoint = f"/api/playback/remote/{quote(identity, safe='')}"
    checkpoint_payload = {
        "source_identity": identity,
        "source_url": "https://www.youtube.com/watch?v=concurrent-watch",
        "title": "Concurrent watch",
        "position_seconds": 15,
        "duration_seconds": 120,
        "completed": False,
        "checkpoint_client_id": "watch-player",
        "checkpoint_sequence": 1,
        "expected_revision": 0,
    }
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            checkpoint_future = executor.submit(checkpoint_client.put, endpoint, json=checkpoint_payload)
            assert checkpoint_flushed.wait(timeout=1)
            settings_future = executor.submit(
                settings_client.put,
                "/api/settings/me",
                json={"ui_prefs": {"autoplay_up_next": False}},
            )
            try:
                assert observed_lock.second_acquire_attempted.wait(timeout=1)
            finally:
                release_checkpoint.set()
            checkpoint_response = checkpoint_future.result(timeout=2)
            settings_response = settings_future.result(timeout=2)

        assert checkpoint_response.status_code == 200, checkpoint_response.text
        assert settings_response.status_code == 200, settings_response.text
        assert settings_response.json()["ui_prefs"]["autoplay_up_next"] is False
    finally:
        release_checkpoint.set()
        checkpoint_client.close()
        settings_client.close()
        rate_limiter.clear()
        app.dependency_overrides.clear()
        engine.dispose()


def test_write_transaction_bounds_writer_queue_wait(monkeypatch) -> None:  # noqa: ANN001
    writer_lock = RLock()
    holder_ready = Event()
    release_holder = Event()
    session = Mock()
    session.info = {}
    monkeypatch.setattr(persistence, "writer_admission_lock", writer_lock)
    monkeypatch.setattr(persistence, "WRITER_ADMISSION_TIMEOUT_SECONDS", 0.05)

    def hold_writer_slot() -> None:
        with writer_lock:
            holder_ready.set()
            assert release_holder.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(hold_writer_slot)
        assert holder_ready.wait(timeout=1)
        try:
            with pytest.raises(OperationalError, match="SQLite writer slot is busy"):
                with write_transaction(session, name="bounded_wait"):
                    pass
        finally:
            release_holder.set()
        holder.result(timeout=1)

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def _make_engine(tmp_path, *, busy_timeout_ms: int = 100):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'serialized-writers.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": busy_timeout_ms / 1000},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def _make_member(user_id: str, username: str) -> User:
    return User(
        id=user_id,
        username=username,
        display_name=username.title(),
        password_hash=hash_password("secret-password"),
        role="viewer",
        is_active=True,
    )


def test_job_status_writes_share_the_writer_admission_slot(tmp_path) -> None:  # noqa: ANN001
    """Every durable writer must queue on one admission slot instead of racing SQLite's file lock."""
    engine, session_factory = _make_engine(tmp_path)
    manager = JobManager(EventBus())
    user = _make_member("member-1", "member")
    job_id = str(uuid.uuid4())
    with session_factory() as setup:
        setup.add(user)
        setup.add(
            DownloadJob(
                id=job_id,
                user_id=user.id,
                source_url="https://example.com/video",
                status="failed",
                format_selection={},
                output_profile={},
            )
        )
        setup.commit()

    holder_entered = Event()
    release_holder = Event()

    def hold_seam_write() -> None:
        with session_factory() as session:
            with write_transaction(session, name="held_write"):
                holder_entered.set()
                assert release_holder.wait(timeout=5)

    def retry_job() -> str:
        with session_factory() as session:
            job = manager.retry(session, job_id, user)
            return job.status

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            holder = executor.submit(hold_seam_write)
            assert holder_entered.wait(timeout=2)
            retrier = executor.submit(retry_job)
            # Hold the writer well past the SQLite busy timeout: the retry must
            # queue on writer admission rather than race the file lock and fail.
            time.sleep(0.5)
            release_holder.set()
            assert retrier.result(timeout=5) == "queued"
            holder.result(timeout=2)
        with session_factory() as verifier:
            assert verifier.get(DownloadJob, job_id).status == "queued"
    finally:
        release_holder.set()
        engine.dispose()


def test_mixed_interactive_writers_are_all_durable_without_lock_errors(tmp_path) -> None:  # noqa: ANN001
    """Small deterministic soak: playback, settings, session, and job writers interleave cleanly."""
    engine, session_factory = _make_engine(tmp_path)
    manager = JobManager(EventBus())
    viewer = _make_member("member-1", "member")
    job_ids = [str(uuid.uuid4()) for _ in range(6)]
    with session_factory() as setup:
        setup.add(viewer)
        setup.add_all(
            DownloadJob(
                id=job_id,
                user_id=viewer.id,
                source_url=f"https://example.com/video-{index}",
                status="failed",
                format_selection={},
                output_profile={},
            )
            for index, job_id in enumerate(job_ids)
        )
        setup.commit()

    def write_playback(index: int) -> None:
        with session_factory() as session:
            with write_transaction(session, name="remote_playback_update"):
                RemotePlaybackProgressService(session).update(
                    f"youtube:soak-{index}",
                    RemotePlaybackProgressUpdateRequest(
                        source_identity=f"youtube:soak-{index}",
                        source_url=f"https://www.youtube.com/watch?v=soak-{index}",
                        title=f"Soak {index}",
                        position_seconds=index + 1,
                        duration_seconds=120,
                        completed=False,
                        checkpoint_client_id="soak-player",
                        checkpoint_sequence=1,
                        expected_revision=0,
                    ),
                    viewer,
                )

    def write_settings(index: int) -> None:
        with session_factory() as session:
            with write_transaction(session, name="user_settings_update"):
                UserSettingsService(session).update_for_user(
                    viewer,
                    UserSettingsUpdateRequest(ui_prefs={f"soak_pref_{index}": True}),
                )

    def write_session(_index: int) -> None:
        with session_factory() as session:
            create_app_session(session, viewer)

    def write_job_status(index: int) -> None:
        with session_factory() as session:
            manager.retry(session, job_ids[index], viewer)

    workloads = [write_playback, write_settings, write_session, write_job_status]
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(workload, index) for index in range(6) for workload in workloads]
        for future in futures:
            future.result(timeout=10)

    with session_factory() as verifier:
        assert verifier.query(RemotePlaybackProgress).count() == 6
        assert verifier.query(AppSession).count() == 6
        assert [status for (status,) in verifier.query(DownloadJob.status).all()] == ["queued"] * 6
        settings_record = verifier.query(UserSettings).filter(UserSettings.user_id == viewer.id).one()
        assert all(settings_record.ui_prefs.get(f"soak_pref_{index}") is True for index in range(6))
    engine.dispose()
