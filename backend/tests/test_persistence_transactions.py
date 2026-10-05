import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.db as app_db
import app.persistence as persistence
from app.db import Base, get_db
from app.main import app
from app.models import User
from app.security import get_current_user
from app.persistence import (
    persistence_metrics_snapshot,
    reset_persistence_metrics,
    write_transaction,
)
from app.events import EventBus
from app.schemas import (
    JobCreateRequest,
    PreviewResponse,
    RemotePlaybackProgressUpdateRequest,
    SourceAutomationCreateRequest,
    UserSettingsUpdateRequest,
)
from app.security import hash_password
from app.services.job_manager import JobManager
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.source_automation import SourceAutomationService
from app.services.user_settings import UserSettingsService
from app.services.yt_dlp_service import YtDlpService


METRIC_KEYS = {"count", "errors", "busy_timeouts", "total_ms", "max_ms", "total_wait_ms", "max_wait_ms"}


def make_session_factory(tmp_path, *, busy_timeout_ms: int = 100):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'persistence.sqlite3'}",
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


def make_user(user_id: str = "member-1", username: str = "member") -> User:
    return User(
        id=user_id,
        username=username,
        display_name=username.title(),
        password_hash=hash_password("secret-password"),
        role="viewer",
        is_active=True,
    )


@pytest.fixture(autouse=True)
def clean_metrics():
    reset_persistence_metrics()
    yield
    reset_persistence_metrics()


def test_write_transaction_commits_durably_on_success(tmp_path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with write_transaction(session, name="test_write"):
                session.add(make_user())
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is not None
    finally:
        engine.dispose()


def test_write_transaction_rolls_back_and_reraises_on_failure(tmp_path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with pytest.raises(RuntimeError, match="boom"):
                with write_transaction(session, name="test_write"):
                    session.add(make_user())
                    session.flush()
                    raise RuntimeError("boom")
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is None
    finally:
        engine.dispose()


def test_write_transaction_claims_the_writer_eagerly(tmp_path) -> None:
    """BEGIN IMMEDIATE must claim the writer up front instead of upgrading mid-transaction."""
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with write_transaction(session, name="test_write"):
                driver = session.connection().connection.driver_connection
                assert driver.in_transaction, "the seam must open the SQLite transaction before any flush"
                session.add(make_user())
    finally:
        engine.dispose()


def test_write_transaction_times_out_when_writer_slot_is_busy(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(persistence, "WRITER_ADMISSION_TIMEOUT_SECONDS", 0.05)
    holder_ready = Event()
    release_holder = Event()

    def hold_writer_slot() -> None:
        acquired = persistence.writer_admission_lock.acquire()
        holder_ready.set()
        try:
            assert release_holder.wait(timeout=2)
        finally:
            persistence.writer_admission_lock.release()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            holder = executor.submit(hold_writer_slot)
            assert holder_ready.wait(timeout=1)
            try:
                with session_factory() as session:
                    with pytest.raises(OperationalError, match="SQLite writer slot is busy"):
                        with write_transaction(session, name="busy_write"):
                            session.add(make_user())
            finally:
                release_holder.set()
            holder.result(timeout=1)

        snapshot = persistence_metrics_snapshot()
        assert snapshot["busy_write"]["count"] == 1
        assert snapshot["busy_write"]["errors"] == 1
        assert snapshot["busy_write"]["busy_timeouts"] == 1
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is None
    finally:
        engine.dispose()


def test_sqlite_busy_errors_count_as_busy_timeouts(tmp_path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with pytest.raises(OperationalError):
                with write_transaction(session, name="locked_write"):
                    raise OperationalError("INSERT", {}, Exception("database is locked"))
        snapshot = persistence_metrics_snapshot()
        assert snapshot["locked_write"]["count"] == 1
        assert snapshot["locked_write"]["errors"] == 1
        assert snapshot["locked_write"]["busy_timeouts"] == 1
    finally:
        engine.dispose()


def test_non_busy_operational_error_invalidates_the_pooled_connection(tmp_path) -> None:
    """A poisoned descriptor (e.g. disk I/O error) must not return to the pool.

    SQLite's is_disconnect never fires for these errors and there is no
    pool pre-ping, so without explicit invalidation every later admission
    reuses the same broken descriptor and fails identically for hours.
    """
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with write_transaction(session, name="warm_write"):
                poisoned_driver = session.connection().connection.driver_connection
                session.add(make_user())

            with pytest.raises(OperationalError, match="disk I/O error"):
                with write_transaction(session, name="poisoned_write"):
                    assert session.connection().connection.driver_connection is poisoned_driver
                    raise OperationalError("INSERT", {}, Exception("disk I/O error"))

            # The next admission must run on a fresh descriptor, and the same
            # session must stay usable for a durable write.
            with write_transaction(session, name="recovered_write"):
                fresh_driver = session.connection().connection.driver_connection
                session.add(make_user("member-2", "member2"))
            assert fresh_driver is not poisoned_driver, "the poisoned pooled connection was reused"

        snapshot = persistence_metrics_snapshot()
        assert snapshot["poisoned_write"]["errors"] == 1
        assert snapshot["poisoned_write"]["busy_timeouts"] == 0
        with session_factory() as verifier:
            assert verifier.get(User, "member-2") is not None
    finally:
        engine.dispose()


def test_busy_errors_keep_the_pooled_connection(tmp_path) -> None:
    """Ordinary busy/locked contention must not churn pooled connections."""
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with write_transaction(session, name="warm_write"):
                original_driver = session.connection().connection.driver_connection
                session.add(make_user())

            with pytest.raises(OperationalError, match="database is locked"):
                with write_transaction(session, name="locked_write"):
                    raise OperationalError("INSERT", {}, Exception("database is locked"))

            with write_transaction(session, name="retry_write"):
                retry_driver = session.connection().connection.driver_connection
                session.add(make_user("member-2", "member2"))
            assert retry_driver is original_driver, "a busy-classified error must not invalidate the connection"
    finally:
        engine.dispose()


def test_nested_write_transactions_commit_once_at_the_outermost_exit(tmp_path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            with write_transaction(session, name="outer_write"):
                with write_transaction(session, name="inner_write"):
                    session.add(make_user())
                    session.flush()
                with session_factory() as verifier:
                    assert verifier.get(User, "member-1") is None, "inner exit must not commit"
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is not None
        snapshot = persistence_metrics_snapshot()
        assert snapshot["outer_write"]["count"] == 1
        assert "inner_write" not in snapshot, "nested passthroughs are part of the outer write"
    finally:
        engine.dispose()


def test_metrics_record_counts_durations_and_waits(tmp_path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    try:
        with session_factory() as session:
            for index in range(2):
                with write_transaction(session, name="metered_write"):
                    session.add(make_user(f"member-{index}", f"member{index}"))
        snapshot = persistence_metrics_snapshot()
        assert set(snapshot["metered_write"]) == METRIC_KEYS
        assert snapshot["metered_write"]["count"] == 2
        assert snapshot["metered_write"]["errors"] == 0
        assert snapshot["metered_write"]["busy_timeouts"] == 0
        assert snapshot["metered_write"]["total_ms"] >= snapshot["metered_write"]["max_ms"] > 0
        assert snapshot["metered_write"]["total_wait_ms"] >= snapshot["metered_write"]["max_wait_ms"] >= 0

        reset_persistence_metrics()
        assert persistence_metrics_snapshot() == {}
    finally:
        engine.dispose()


class _StagingJobs:
    def __init__(self) -> None:
        self.staged = []
        self.dispatched = []

    def stage_enqueue(self, db, payload, user, **kwargs):  # noqa: ANN001
        self.staged.append(payload)
        return type("StagedJob", (), {"id": f"job-{len(self.staged)}"})()

    def dispatch_staged(self, job) -> None:  # noqa: ANN001
        self.dispatched.append(job.id)


def _single_entry_preview(source_url: str) -> PreviewResponse:
    return PreviewResponse(
        kind="playlist",
        title="Automated source",
        extractor="youtube",
        extractor_key="Youtube",
        webpage_url=source_url,
        availability="public",
        entries=[
            {
                "id": "vid-1",
                "title": "Fresh upload",
                "duration": 240,
                "thumbnail": "https://example.com/1.jpg",
                "webpage_url": "https://www.youtube.com/watch?v=vid-1",
                "uploader": "Creator",
                "availability": "public",
            }
        ],
        raw={},
    )


def test_paused_automation_preview_does_not_block_interactive_writes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A stalled source inspection must never hold the writer against interactive requests."""
    engine, session_factory = make_session_factory(tmp_path, busy_timeout_ms=200)
    owner = make_user("automation-owner", "automator")
    viewer = make_user("watching-member", "watcher")
    service_session = session_factory()
    service_session.add_all([owner, viewer])
    service_session.commit()
    jobs = _StagingJobs()
    service = SourceAutomationService(service_session, jobs)  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Stalled source",
            source_url="https://example.com/feed",
            source_type="channel",
            auto_download=True,
        ),
        owner,
    )
    service_session.commit()

    preview_started = Event()
    release_preview = Event()

    def blocking_preview(self, source_url, lazy_playlist=True, format_selection=None, **_kwargs):  # noqa: ANN001
        preview_started.set()
        assert release_preview.wait(timeout=5)
        return _single_entry_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", blocking_preview)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            run_future = executor.submit(service.run_automation, automation.id, owner)
            assert preview_started.wait(timeout=2)

            interactive_started = time.perf_counter()
            with session_factory() as playback_session:
                with write_transaction(playback_session, name="remote_playback_update"):
                    RemotePlaybackProgressService(playback_session).update(
                        "youtube:blocked-watch",
                        RemotePlaybackProgressUpdateRequest(
                            source_identity="youtube:blocked-watch",
                            source_url="https://www.youtube.com/watch?v=blocked-watch",
                            title="Concurrent watch",
                            position_seconds=15,
                            duration_seconds=120,
                            completed=False,
                            checkpoint_client_id="watch-player",
                            checkpoint_sequence=1,
                            expected_revision=0,
                        ),
                        viewer,
                    )
            with session_factory() as settings_session:
                with write_transaction(settings_session, name="user_settings_update"):
                    UserSettingsService(settings_session).update_for_user(
                        viewer,
                        UserSettingsUpdateRequest(ui_prefs={"autoplay_up_next": False}),
                    )
            interactive_elapsed = time.perf_counter() - interactive_started
            assert interactive_elapsed < 2.0, f"interactive writes took {interactive_elapsed:.2f}s behind a stalled preview"

            release_preview.set()
            run = run_future.result(timeout=5)

        assert run.status == "completed"
        assert run.discovered_count == 1
        assert run.queued_count == 1
        assert jobs.dispatched == ["job-1"]
        with session_factory() as verifier:
            persisted = verifier.execute(text("SELECT status FROM automation_runs")).all()
            assert persisted == [("completed",)]
    finally:
        release_preview.set()
        service_session.close()
        engine.dispose()


def _record_source_validation_transaction_state(monkeypatch) -> list[bool]:  # noqa: ANN001
    """Record whether a SQLite write transaction is open at each URL validation (DNS) call."""
    observed: list[bool] = []
    original = YtDlpService.validate_source_url

    def recording_validate(self, source_url):  # noqa: ANN001
        driver = self.db.connection().connection.driver_connection
        observed.append(bool(driver.in_transaction))
        return original(self, source_url)

    monkeypatch.setattr(YtDlpService, "validate_source_url", recording_validate)
    return observed


def test_enqueue_validates_source_urls_outside_the_write_transaction(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine, session_factory = make_session_factory(tmp_path)
    user = make_user("member-1", "member")
    with session_factory() as setup:
        setup.add(user)
        setup.commit()
    observed = _record_source_validation_transaction_state(monkeypatch)
    manager = JobManager(EventBus())
    try:
        with session_factory() as session:
            job = manager.enqueue(session, JobCreateRequest(source_url="https://example.com/video"), user)
            assert job.status == "queued"
        assert observed == [False], "URL validation resolved DNS while the writer was held"
    finally:
        engine.dispose()


def test_automation_staging_validates_urls_outside_the_write_transaction(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine, session_factory = make_session_factory(tmp_path)
    session = session_factory()
    user = make_user("automation-owner", "automator")
    session.add(user)
    session.commit()
    # The real JobManager stages the queued entry, so its URL validation path runs.
    service = SourceAutomationService(session, JobManager(EventBus()))
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Guarded staging",
            auto_download=True,
            source_url="https://example.com/feed",
            source_type="channel",
        ),
        user,
    )
    session.commit()

    observed = _record_source_validation_transaction_state(monkeypatch)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, **_kwargs):  # noqa: ANN001
        return _single_entry_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    try:
        run = service.run_automation(automation.id, user)
    finally:
        session.close()
        engine.dispose()

    assert run.status == "completed"
    assert run.queued_count == 1
    assert observed == [False, False], "per-entry URL validation resolved DNS while the writer was held"


def test_create_automation_validates_source_urls_before_any_write(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A first-time member's settings insert must not leave a write transaction open across DNS."""
    engine, session_factory = make_session_factory(tmp_path)
    session = session_factory()
    user = make_user("fresh-member", "fresh")
    session.add(user)
    session.commit()

    observed = _record_source_validation_transaction_state(monkeypatch)
    service = SourceAutomationService(session, _StagingJobs())  # type: ignore[arg-type]
    try:
        automation = service.create_automation(
            SourceAutomationCreateRequest(
                label="First automation",
                source_url="https://example.com/feed",
                source_type="channel",
            ),
            user,
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    assert automation.id
    assert observed, "URL validation must still run for new automations"
    assert all(open_txn is False for open_txn in observed), "URL validation resolved DNS with a write transaction open"


def test_after_commit_actions_run_after_the_writer_is_released(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Slow post-commit notification delivery must not extend writer admission."""
    engine, session_factory = make_session_factory(tmp_path, busy_timeout_ms=200)
    service_session = session_factory()
    owner = make_user("automation-owner", "automator")
    viewer = make_user("watching-member", "watcher")
    service_session.add_all([owner, viewer])
    service_session.commit()

    action_started = Event()
    release_action = Event()
    published: list[str] = []

    class SlowEvents:
        def publish(self, event_type, payload) -> None:  # noqa: ANN001
            published.append(event_type)
            action_started.set()
            assert release_action.wait(timeout=5)

    service = SourceAutomationService(service_session, _StagingJobs(), SlowEvents())  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Slow notification",
            source_url="https://example.com/feed",
            source_type="channel",
            auto_download=False,
        ),
        owner,
    )
    service_session.commit()

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, **_kwargs):  # noqa: ANN001
        return _single_entry_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    monkeypatch.setattr(persistence, "WRITER_ADMISSION_TIMEOUT_SECONDS", 0.2)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            run_future = executor.submit(service.run_automation, automation.id, owner)
            assert action_started.wait(timeout=5)
            try:
                # The writer slot must already be free while delivery blocks.
                with session_factory() as playback_session:
                    with write_transaction(playback_session, name="remote_playback_update"):
                        RemotePlaybackProgressService(playback_session).update(
                            "youtube:notify-watch",
                            RemotePlaybackProgressUpdateRequest(
                                source_identity="youtube:notify-watch",
                                source_url="https://www.youtube.com/watch?v=notify-watch",
                                title="Concurrent watch",
                                position_seconds=15,
                                duration_seconds=120,
                                completed=False,
                                checkpoint_client_id="watch-player",
                                checkpoint_sequence=1,
                                expected_revision=0,
                            ),
                            viewer,
                        )
            finally:
                release_action.set()
            run = run_future.result(timeout=10)

        assert run.status == "completed"
        assert run.manual_count == 1
        assert published, "notifications must still deliver after the durable commit"
    finally:
        release_action.set()
        service_session.close()
        engine.dispose()


def test_automation_execute_holds_no_write_transaction_during_preview(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine, session_factory = make_session_factory(tmp_path)
    session = session_factory()
    user = make_user("automation-owner", "automator")
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, _StagingJobs())  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Guarded source",
            source_url="https://example.com/feed",
            source_type="channel",
        ),
        user,
    )
    session.commit()

    preview_window = {"active": False}
    statements_during_preview: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statements(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        if preview_window["active"]:
            statements_during_preview.append(statement)

    observed = {}

    def instrumented_preview(self, source_url, lazy_playlist=True, format_selection=None, **_kwargs):  # noqa: ANN001
        driver = self.db.connection().connection.driver_connection
        observed["write_transaction_open"] = bool(driver.in_transaction)
        preview_window["active"] = True
        try:
            return _single_entry_preview(source_url)
        finally:
            preview_window["active"] = False

    monkeypatch.setattr(YtDlpService, "preview", instrumented_preview)
    try:
        run = service.run_automation(automation.id, user)
    finally:
        event.remove(engine, "before_cursor_execute", record_statements)
        session.close()
        engine.dispose()

    assert observed["write_transaction_open"] is False, "the execute path flushed durable state before the external preview"
    assert statements_during_preview == []
    assert run.status == "completed"


def test_admin_diagnostics_reports_persistence_write_metrics(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The soak driver reads per-name write metrics from the admin diagnostics endpoint."""
    engine, session_factory = make_session_factory(tmp_path)
    user = make_user("health-reader", "reader")
    user.role = "admin"
    monkeypatch.setattr("app.routers.admin_diagnostics.check_connection", lambda config: {"ok": False, "model_available": False, "error": "offline"})
    with session_factory() as setup:
        setup.add(user)
        setup.commit()

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
    client = TestClient(app, base_url="http://localhost")
    try:
        write_response = client.put("/api/settings/me", json={"ui_prefs": {"autoplay_up_next": True}})
        assert write_response.status_code == 200, write_response.text

        response = client.get("/api/admin/diagnostics")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "data_dir" not in payload
        assert "ffmpeg_available" in payload["runtime"]
        metrics = payload["persistence"]["user_settings_update"]
        assert set(metrics) == METRIC_KEYS
        assert metrics["count"] >= 1
        assert metrics["errors"] == 0
        assert metrics["busy_timeouts"] == 0
        assert metrics["max_ms"] > 0
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()


def test_reconcile_loop_counts_consecutive_sweep_failures_and_resets_on_success(monkeypatch) -> None:  # noqa: ANN001
    """Repeated maintenance-cycle failures must be visible, and one success must clear them."""
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "reconcile_interval_seconds", 0)
    main_module.maintenance_sweep_health.reset()
    snapshots_at_entry: list[dict] = []

    def flaky_cycle() -> None:
        snapshots_at_entry.append(main_module.maintenance_sweep_health.snapshot())
        if len(snapshots_at_entry) <= 3:
            raise OperationalError("SELECT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(main_module, "run_library_maintenance_cycle", flaky_cycle)

    async def drive() -> None:
        task = asyncio.create_task(main_module.reconcile_loop())
        try:
            deadline = time.perf_counter() + 5
            while time.perf_counter() < deadline:
                snapshot = main_module.maintenance_sweep_health.snapshot()
                if len(snapshots_at_entry) >= 4 and snapshot["consecutive_failures"] == 0:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("the reconcile loop never recovered from the flaky sweep")
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    try:
        asyncio.run(drive())
        before_recovery = snapshots_at_entry[3]
        assert before_recovery["consecutive_failures"] == 3
        assert "disk I/O error" in before_recovery["last_error"]
        assert before_recovery["last_failure_at"] is not None
        recovered = main_module.maintenance_sweep_health.snapshot()
        assert recovered["consecutive_failures"] == 0
        assert recovered["last_error"] is None
        assert recovered["last_success_at"] is not None
    finally:
        main_module.maintenance_sweep_health.reset()


def test_repeated_sweep_failures_surface_as_degraded_health_and_admin_diagnostics(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A poisoned sweep state must show instead of hiding behind green liveness: members see "degraded", admins the detail."""
    import app.main as main_module

    engine, session_factory = make_session_factory(tmp_path)
    user = make_user("health-reader", "reader")
    with session_factory() as setup:
        setup.add(user)
        setup.commit()

    def override_db():
        with session_factory() as session:
            yield session

    monkeypatch.setattr("app.routers.admin_diagnostics.check_connection", lambda config: {"ok": False, "model_available": False, "error": "offline"})
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app, base_url="http://localhost")
    main_module.maintenance_sweep_health.reset()
    try:
        for _ in range(3):
            main_module.maintenance_sweep_health.record_failure(
                OperationalError("SELECT", {}, Exception("disk I/O error"))
            )
        member = client.get("/api/runtime-health")
        assert member.status_code == 200, member.text
        assert member.json()["status"] == "degraded" and "maintenance_sweeps" not in member.json()
        user.role = "admin"
        response = client.get("/api/admin/diagnostics")
        assert response.status_code == 200, response.text
        sweeps = response.json()["maintenance_sweeps"]
        assert set(sweeps) == {"consecutive_failures", "last_error", "last_success_at", "last_failure_at"}
        assert sweeps["consecutive_failures"] == 3
        assert "disk I/O error" in sweeps["last_error"]
        assert sweeps["last_failure_at"] is not None
    finally:
        main_module.maintenance_sweep_health.reset()
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()


def test_session_scope_runs_after_commit_actions_only_after_the_teardown_commit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Routes without a seam call commit at the get_db teardown; queued actions must drain there."""
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(app_db, "SessionLocal", session_factory)
    delivered: list[str] = []
    try:
        with app_db.session_scope() as session:
            session.add(make_user())
            session.flush()
            persistence.queue_after_commit(session, lambda: delivered.append("delivered"))
            assert delivered == [], "the action ran before the teardown commit"
        assert delivered == ["delivered"]
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is not None
    finally:
        engine.dispose()


def test_session_scope_discards_after_commit_actions_on_rollback(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(app_db, "SessionLocal", session_factory)
    delivered: list[str] = []
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with app_db.session_scope() as session:
                session.add(make_user())
                session.flush()
                persistence.queue_after_commit(session, lambda: delivered.append("delivered"))
                raise RuntimeError("boom")
        assert delivered == [], "the action ran for a rolled-back request"
        with session_factory() as verifier:
            assert verifier.get(User, "member-1") is None
    finally:
        engine.dispose()


def test_serialized_threads_write_without_lock_errors(tmp_path) -> None:
    """Writers queue on admission instead of surfacing SQLITE_BUSY to callers."""
    engine, session_factory = make_session_factory(tmp_path)
    try:
        def write_member(index: int) -> None:
            with session_factory() as session:
                with write_transaction(session, name="threaded_write"):
                    session.add(make_user(f"member-{index}", f"member{index}"))

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(write_member, index) for index in range(12)]
            for future in futures:
                future.result(timeout=5)

        with session_factory() as verifier:
            assert verifier.scalar(text("SELECT COUNT(*) FROM users")) == 12
        snapshot = persistence_metrics_snapshot()
        assert snapshot["threaded_write"]["count"] == 12
        assert snapshot["threaded_write"]["errors"] == 0
    finally:
        engine.dispose()
