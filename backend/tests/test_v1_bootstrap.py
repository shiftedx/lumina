"""S05 — serialized first-owner bootstrap and last-admin invariants.

Hermetic under the autouse guards (per-test temporary app root, provider
network denied). The concurrency tests exercise the REAL SQLite writer seam
(``app.persistence.write_transaction`` — process-wide RLock + BEGIN IMMEDIATE)
with real threads sharing a temporary on-disk database, never a mocked lock.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, SessionLocal
from app.main import app, jobs
from app.models import AppSettings, User, UserSettings
from app.schemas import UserUpdateRequest
from app.services import job_manager as job_manager_module
from app.services.rate_limit import rate_limiter
from app.services.users import UserService

CONSUMED_SETUP_DETAIL = "Initial admin has already been created"


@pytest.fixture(autouse=True)
def _reset_job_manager_singleton():
    """Restore the module-level JobManager to fresh-process state before each test.

    The real app lifespan (``with TestClient(app)``) starts ``jobs`` workers on
    the TestClient's portal event loop. Python 3.11's ``asyncio.Queue`` binds
    itself to the first running loop that touches an empty queue and raises
    ``RuntimeError`` on any later empty-queue operation on a different loop, so
    a second full lifespan in the same process (another test, or another
    TestClient context) crashes at shutdown. No existing test exercises the
    real lifespan, so the suite never hit this. Resetting the loop-bound queue
    and stale worker tasks gives every test the same guarantee a fresh process
    has: its lifespan binds the queue to its own loop.

    The fixture also repairs a pre-existing cross-test leak:
    ``tests/test_job_manager.py`` and ``tests/test_acquisition_job_manager.py``
    assign ``job_manager_module.SessionLocal`` to a per-test in-memory
    ``sessionmaker`` with plain assignment (no restore). The last such
    assignment persists for the rest of the pytest process, so the module
    global the real app's ``JobManager`` resolves at call time no longer
    points at ``app.db.SessionLocal``. When the real lifespan then starts
    ``jobs`` on the TestClient portal thread, the leaked ``sqlite:///:memory:``
    engine (``SingletonThreadPool``, ``check_same_thread`` on) hands out a
    fresh connection for a brand-new empty in-memory database and startup
    reconciles against it (``no such table: acquisition_job_outputs``).
    Restoring the real ``app.db.SessionLocal`` here (conftest rebinds that
    shared sessionmaker to the per-test database) gives the lifespan the
    binding a fresh server process has.
    """
    jobs._worker_tasks = []
    jobs._worker_task = None
    jobs._loop = None
    jobs._queue = asyncio.Queue()
    job_manager_module.SessionLocal = SessionLocal
    yield


def _post_bootstrap(username: str) -> tuple[int, dict]:
    """One bootstrap POST through the real app, on its own TestClient (no lifespan re-run)."""
    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.post(
            "/api/bootstrap/admin",
            json={"username": username, "display_name": username.replace("-", " ").title(), "password": "First-run passphrase 42"},
        )
    finally:
        client.close()
    return response.status_code, response.json()


def test_concurrent_bootstrap_one_owner() -> None:
    """Two synchronized bootstrap requests: exactly one 201 and one 409, one owner row, no partials."""
    rate_limiter.clear()

    # Run the app lifespan once so the startup path (schema gate + the
    # ensure_app_settings singleton row) lands on this test's database before
    # the race; the race itself runs request handlers only.
    with TestClient(app, base_url="http://localhost") as started:
        assert started.get("/api/bootstrap/status").json() == {"needs_setup": True}

    results: list[tuple[int, dict]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(username: str) -> None:
        try:
            barrier.wait(timeout=30)
            results.append(_post_bootstrap(username))
        except BaseException as exc:  # noqa: BLE001 - surface as a test failure
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("first-owner",)),
        threading.Thread(target=worker, args=("second-owner",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert errors == []
    results.sort(key=lambda item: item[0])
    (created_code, created_body), (conflict_code, conflict_body) = results
    assert created_code == 201
    assert conflict_code == 409
    winner_username = created_body["username"]
    assert created_body["role"] == "admin"
    assert conflict_body["detail"] == CONSUMED_SETUP_DETAIL

    # Committed state (a fresh session, not the request sessions): exactly one
    # admin row and exactly one app_settings singleton row (id=1), no orphans.
    with SessionLocal() as db:
        owners = db.query(User).all()
        settings_rows = db.query(AppSettings).all()
        orphan_settings = (
            db.query(UserSettings).filter(~UserSettings.user_id.in_(select(User.id))).all()
        )

    assert len(owners) == 1
    assert owners[0].username == winner_username
    assert owners[0].role == "admin"
    assert owners[0].is_active is True
    assert len(settings_rows) == 1
    assert settings_rows[0].id == 1
    assert orphan_settings == []

    # A plain client (no second lifespan run): the committed state reports the
    # setup as consumed.
    consumed = TestClient(app, base_url="http://localhost")
    try:
        assert consumed.get("/api/bootstrap/status").json() == {"needs_setup": False}
    finally:
        consumed.close()


def test_concurrent_last_admin_changes(tmp_path: Path) -> None:
    """Two synchronized demotion/disable mutations at the writer seam cannot leave zero active admins.

    With exactly one active admin and one inactive admin, the mutation that
    would remove the last active admin conflicts (409-class) while the other
    legitimate mutation commits; the committed household keeps an active admin.
    """
    database_path = tmp_path / "household.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
        future=True,
    )
    try:
        Base.metadata.create_all(bind=engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

        with factory() as db:
            db.add_all([
                User(id="owner-1", username="owner-1", display_name="Owner", password_hash="test-only-hash", role="admin", is_active=True),
                User(id="owner-2", username="owner-2", display_name="Second", password_hash="test-only-hash", role="admin", is_active=False),
            ])
            db.commit()

        outcomes: dict[str, str] = {}
        outcomes_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt(key: str, user_id: str, payload: UserUpdateRequest) -> None:
            try:
                with factory() as db:
                    barrier.wait(timeout=30)
                    try:
                        UserService(db).update_user(user_id, payload)
                        result = "landed"
                    except ValueError as exc:
                        result = str(exc)
            except BaseException as exc:  # noqa: BLE001 - surface as a test failure
                result = f"unexpected: {exc!r}"
            with outcomes_lock:
                outcomes[key] = result

        threads = [
            # Removing the last ACTIVE admin must conflict, even under a
            # concurrent writer at the seam.
            threading.Thread(target=attempt, args=("deactivate-last-active", "owner-1", UserUpdateRequest(is_active=False))),
            # A legitimate concurrent mutation still commits through the seam.
            threading.Thread(target=attempt, args=("demote-inactive", "owner-2", UserUpdateRequest(role="viewer"))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()

        assert outcomes["demote-inactive"] == "landed"
        assert outcomes["deactivate-last-active"] == "The household must keep at least one active vault owner"

        # Committed state (a fresh session, not the request sessions).
        with factory() as db:
            last_active = db.get(User, "owner-1")
            second = db.get(User, "owner-2")
            active_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).count()

        assert last_active.role == "admin"
        assert last_active.is_active is True
        assert second.role == "viewer"
        assert second.is_active is False
        assert active_admins >= 1
    finally:
        engine.dispose()


def test_remote_setup_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote (HTTPS) profile refuses an unbootstrapped service; loopback completes first-run setup."""
    remote_values = {
        "app_public_url": "https://vault.example.test",
        "frontend_public_url": "https://vault.example.test",
        "allowed_origins": "https://vault.example.test",
        "trusted_proxy_ips": "127.0.0.1/32",
        "session_cookie_secure": True,
        "host": "0.0.0.0",
    }
    original_values = {name: getattr(settings, name) for name in remote_values}
    for name, value in remote_values.items():
        monkeypatch.setattr(settings, name, value)
    rate_limiter.clear()

    # Remote profile + no owner yet: the lifespan gate refuses to start, so the
    # service never serves traffic (startup failure propagates from __enter__).
    try:
        with pytest.raises(RuntimeError, match="setup over loopback"):
            with TestClient(app, base_url="http://localhost") as refused:
                del refused
    finally:
        for name, value in original_values.items():
            setattr(settings, name, value)

    # Loopback profile in the same test process completes first-run setup
    # end-to-end on the same database (schema already ready from the refused
    # startup; the app_settings row is created by the lifespan that now runs).
    with TestClient(app, base_url="http://localhost") as loopback:
        assert loopback.get("/api/bootstrap/status").json() == {"needs_setup": True}
        created = loopback.post(
            "/api/bootstrap/admin",
            json={"username": "owner", "display_name": "Owner", "password": "Loopback first-run passphrase 9"},
        )
        assert created.status_code == 201
        assert loopback.get("/api/bootstrap/status").json() == {"needs_setup": False}
