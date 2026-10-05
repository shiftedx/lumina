from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import main as main_module
from app.db import Base
from app.models import SourceAutomation, User
from app.persistence import write_transaction as _real_write_transaction
from app.schemas import AutomationRuleSet, PreviewResponse, SourceAutomationCreateRequest
from app.security import hash_password
from app.services import job_manager as job_manager_module
from app.services import source_automation as source_automation_module
from app.services.job_manager import JobManager
from app.services.source_automation import AutomationAlreadyRunningError, SourceAutomationService
from app.services.yt_dlp_service import YtDlpService
from support import FakeJobs, memory_session_factory


def make_user(user_id: str = "user-1", username: str = "tester") -> User:
    return User(
        id=user_id,
        username=username,
        display_name=username.title(),
        password_hash=hash_password("secret"),
        role="viewer",
        is_active=True,
    )


def fake_playlist_preview(source_url: str) -> PreviewResponse:
    return PreviewResponse(
        kind="playlist",
        title="Test playlist",
        extractor="youtube",
        extractor_key="Youtube",
        webpage_url=source_url,
        availability="public",
        entries=[
            {
                "id": f"vid-{index}",
                "title": f"Keep tutorial {index}",
                "duration": 240,
                "thumbnail": f"https://example.com/{index}.jpg",
                "webpage_url": f"https://www.youtube.com/watch?v=vid-{index}",
                "uploader": "Creator",
                "availability": "public",
            }
            for index in range(1, 4)
        ],
        raw={},
    )


def _make_automation(service: SourceAutomationService, user: User, **overrides) -> SourceAutomation:
    payload = {
        "label": "Shared automation",
        "source_url": "https://www.youtube.com/playlist?list=PL123",
        "source_type": "playlist",
        "max_items_per_run": 5,
        "backfill_limit": 3,
        "auto_download": True,
    }
    payload.update(overrides)
    return service.create_automation(SourceAutomationCreateRequest(**payload), user)


def make_memory_session():
    return memory_session_factory()()


def make_file_sessions(path):  # noqa: ANN001
    engine = create_engine(
        f"sqlite:///{path}", future=True, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_lease_is_released_after_a_successful_run(monkeypatch) -> None:  # noqa: ANN001
    session = make_memory_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = _make_automation(service, user)
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda self, url, *a, **k: fake_playlist_preview(url))
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    run = service.run_automation(automation.id, user)
    session.commit()

    assert run.status == "completed"
    assert run.queued_count == 3
    refreshed = service.get_automation(automation.id, user)
    assert refreshed.run_lease_id is None
    assert refreshed.run_lease_expires_at is None


def test_lease_is_released_after_a_failed_run(monkeypatch) -> None:  # noqa: ANN001
    session = make_memory_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = _make_automation(service, user)
    session.commit()
    monkeypatch.setattr(
        YtDlpService, "preview", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("source down"))
    )

    run = service.run_automation(automation.id, user)
    session.commit()

    assert run.status == "failed"
    refreshed = service.get_automation(automation.id, user)
    assert refreshed.run_lease_id is None
    assert refreshed.run_lease_expires_at is None


def test_a_held_unexpired_lease_blocks_a_manual_run_before_inspecting_the_source(monkeypatch) -> None:  # noqa: ANN001
    session = make_memory_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = _make_automation(service, user)
    automation.run_lease_id = "another-runner"
    automation.run_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    session.commit()
    monkeypatch.setattr(
        YtDlpService, "preview", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not inspect"))
    )

    with pytest.raises(AutomationAlreadyRunningError, match="already running"):
        service.run_automation(automation.id, user)

    refreshed = service.get_automation(automation.id, user)
    assert refreshed.run_lease_id == "another-runner"


def test_an_expired_lease_is_reclaimed_by_a_new_run(monkeypatch) -> None:  # noqa: ANN001
    session = make_memory_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = _make_automation(service, user)
    automation.run_lease_id = "stale-runner"
    automation.run_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda self, url, *a, **k: fake_playlist_preview(url))
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    run = service.run_automation(automation.id, user)
    session.commit()

    assert run.status == "completed"
    refreshed = service.get_automation(automation.id, user)
    assert refreshed.run_lease_id is None


def test_preview_run_never_claims_or_clears_the_lease(monkeypatch) -> None:  # noqa: ANN001
    session = make_memory_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = _make_automation(service, user)
    automation.run_lease_id = "active-runner"
    automation.run_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda self, url, *a, **k: fake_playlist_preview(url))
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    result = service.preview_automation(automation.id, user)

    assert result.run.status == "preview"
    refreshed = service.get_automation(automation.id, user)
    assert refreshed.run_lease_id == "active-runner"


def test_two_concurrent_runs_let_exactly_one_execute_and_the_other_conflicts(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    sessions = make_file_sessions(tmp_path / "lease.db")
    with sessions() as db:
        db.add(make_user())
        db.commit()
        automation = _make_automation(SourceAutomationService(db, FakeJobs()), make_user())
        db.commit()
        automation_id = automation.id

    preview_reached = Event()
    release_preview = Event()

    def blocking_preview(self, source_url, lazy_playlist=True, format_selection=None):  # noqa: ANN001
        preview_reached.set()
        assert release_preview.wait(timeout=5)
        return fake_playlist_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", blocking_preview)
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    results: dict[str, tuple[str, int]] = {}

    def attempt(tag: str) -> None:
        with sessions() as db:
            service = SourceAutomationService(db, FakeJobs())
            try:
                run = service.run_automation(automation_id, make_user())
                results[tag] = ("ran", run.queued_count)
            except AutomationAlreadyRunningError:
                release_preview.set()  # let the lease holder finish
                results[tag] = ("conflict", 0)

    threads = [Thread(target=attempt, args=(tag,)) for tag in ("manual", "scheduled")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    release_preview.set()  # safety: never leave a thread blocked

    outcomes = sorted(outcome for outcome, _count in results.values())
    assert outcomes == ["conflict", "ran"]
    winner = next(count for outcome, count in results.values() if outcome == "ran")
    assert winner == 3


def test_concurrent_runs_never_queue_more_than_the_daily_limit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    sessions = make_file_sessions(tmp_path / "daily.db")
    with sessions() as db:
        db.add(make_user())
        db.commit()
        automation = _make_automation(
            SourceAutomationService(db, FakeJobs()),
            make_user(),
            max_items_per_day=1,
            rules=AutomationRuleSet(),
        )
        db.commit()
        automation_id = automation.id

    preview_reached = Event()
    release_preview = Event()

    def blocking_preview(self, source_url, lazy_playlist=True, format_selection=None):  # noqa: ANN001
        preview_reached.set()
        assert release_preview.wait(timeout=5)
        return fake_playlist_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", blocking_preview)
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    results: dict[str, tuple[str, int]] = {}

    def attempt(tag: str) -> None:
        with sessions() as db:
            service = SourceAutomationService(db, FakeJobs())
            try:
                run = service.run_automation(automation_id, make_user())
                results[tag] = ("ran", run.queued_count)
            except AutomationAlreadyRunningError:
                release_preview.set()
                results[tag] = ("conflict", 0)

    threads = [Thread(target=attempt, args=(tag,)) for tag in ("manual", "scheduled")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    release_preview.set()

    total_queued = sum(count for _outcome, count in results.values())
    assert total_queued <= 1


def test_a_finalizing_run_never_clears_a_lease_reclaimed_by_another_runner(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    sessions = make_file_sessions(tmp_path / "identity.db")
    with sessions() as db:
        db.add(make_user())
        db.commit()
        automation = _make_automation(SourceAutomationService(db, FakeJobs()), make_user())
        db.commit()
        automation_id = automation.id

    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)

    def preview_then_foreign_reclaim(self, url, *a, **k):  # noqa: ANN001
        # The first run's lease is live here. Simulate its TTL elapsing and a
        # second runner reclaiming the automation with a fresh lease id, then
        # let the first run proceed to finalize.
        with sessions() as other:
            row = other.get(SourceAutomation, automation_id)
            row.run_lease_id = "lease-from-runner-b"
            row.run_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
            other.commit()
        return fake_playlist_preview(url)

    monkeypatch.setattr(YtDlpService, "preview", preview_then_foreign_reclaim)

    with sessions() as db:
        run = SourceAutomationService(db, FakeJobs()).run_automation(automation_id, make_user())

    assert run.status == "completed"
    with sessions() as check:
        row = check.get(SourceAutomation, automation_id)
        # The first run must not clear the live lease it no longer owns.
        assert row.run_lease_id == "lease-from-runner-b"


def test_writer_contention_at_staging_releases_the_lease_instead_of_holding_it_to_ttl(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    sessions = make_file_sessions(tmp_path / "contention.db")
    with sessions() as db:
        db.add(make_user())
        db.commit()
        automation = _make_automation(SourceAutomationService(db, FakeJobs()), make_user())
        db.commit()
        automation_id = automation.id

    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    monkeypatch.setattr(YtDlpService, "preview", lambda self, url, *a, **k: fake_playlist_preview(url))

    tripped = {"done": False}

    @contextmanager
    def flaky_write_transaction(db, *, name):  # noqa: ANN001
        # Ordinary writer contention surfaces as OperationalError (a SQLAlchemyError)
        # from the admission timeout. Force it once at the staging transaction.
        if name == "automation_run" and not tripped["done"]:
            tripped["done"] = True
            raise OperationalError(
                "acquire SQLite writer slot", {}, TimeoutError("SQLite writer slot is busy")
            )
        with _real_write_transaction(db, name=name):
            yield

    monkeypatch.setattr(source_automation_module, "write_transaction", flaky_write_transaction)

    with sessions() as db:
        with pytest.raises(OperationalError):
            SourceAutomationService(db, FakeJobs()).run_automation(automation_id, make_user())

    with sessions() as check:
        row = check.get(SourceAutomation, automation_id)
        # The lease must be released out of band, not held for the full 15-min TTL.
        assert row.run_lease_id is None
        assert row.run_lease_expires_at is None


def test_writer_contention_at_the_failure_record_releases_the_lease_instead_of_holding_it_to_ttl(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    sessions = make_file_sessions(tmp_path / "failure_record_contention.db")
    with sessions() as db:
        db.add(make_user())
        db.commit()
        automation = _make_automation(SourceAutomationService(db, FakeJobs()), make_user())
        db.commit()
        automation_id = automation.id

    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    # The source inspection itself fails, sending the run down the
    # failure-record path rather than the staging path.
    monkeypatch.setattr(
        YtDlpService, "preview", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("source down"))
    )

    tripped = {"done": False}

    @contextmanager
    def flaky_write_transaction(db, *, name):  # noqa: ANN001
        # Writer contention surfaces as OperationalError (a SQLAlchemyError)
        # from the admission timeout. Force it once at the failure-record
        # write — the double fault: inspection already failed AND the writer
        # is starved when the failed run is being recorded.
        if name == "automation_run" and not tripped["done"]:
            tripped["done"] = True
            raise OperationalError(
                "acquire SQLite writer slot", {}, TimeoutError("SQLite writer slot is busy")
            )
        with _real_write_transaction(db, name=name):
            yield

    monkeypatch.setattr(source_automation_module, "write_transaction", flaky_write_transaction)

    with sessions() as db:
        with pytest.raises(OperationalError):
            SourceAutomationService(db, FakeJobs()).run_automation(automation_id, make_user())

    with sessions() as check:
        row = check.get(SourceAutomation, automation_id)
        # The lease must be released out of band, not held for the full 15-min TTL.
        assert row.run_lease_id is None
        assert row.run_lease_expires_at is None


@pytest.fixture
def run_api(monkeypatch, db_factory, api_client):  # noqa: ANN001
    sessions = db_factory
    user = make_user()
    with sessions.begin() as db:
        db.add(user)

    manager = JobManager(Mock())
    manager.acquisition.session_factory = sessions
    monkeypatch.setattr(main_module, "jobs", manager)
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    client = api_client(user=user, base_url="http://localhost", raise_server_exceptions=False)
    return client, sessions, user


def test_run_route_maps_a_lost_lease_claim_to_http_409(run_api, monkeypatch) -> None:  # noqa: ANN001
    client, sessions, user = run_api
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    with sessions() as db:
        automation = _make_automation(SourceAutomationService(db, FakeJobs()), user)
        automation.run_lease_id = "another-runner"
        automation.run_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        db.commit()
        automation_id = automation.id

    response = client.post(f"/api/automations/{automation_id}/run")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"].lower()
