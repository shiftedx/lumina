"""Only a digest of the session bearer is persisted, and activity
refreshes an idle deadline but can never push past the absolute expiry."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import security
from app.config import settings
from app.main import app
from app.models import AppSession
from app.security import hash_password
from support import make_user

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def factory(db_factory, api_client):
    """The seeded database behind every API request (get_db is bound by api_client)."""
    with db_factory.begin() as session:
        session.add(make_user("u1", role="admin", username="local", display_name="Local", password_hash=hash_password(PASSWORD)))
    return db_factory


@pytest.fixture
def client(factory, api_client):
    return api_client(base_url="http://localhost")


def login(client: TestClient, username: str = "local", password: str = PASSWORD) -> str:
    response = client.post("/api/session/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_raw_bearer_absent_database(client: TestClient, factory) -> None:
    login(client)
    bearer = client.cookies.get(settings.session_cookie_name)
    assert bearer and client.get("/api/session/me").status_code == 200
    with factory() as db:
        rows = db.execute(text("SELECT * FROM app_sessions")).all()
    assert len(rows) == 1
    assert bearer not in repr(rows)
    assert rows[0].id == security.session_digest(bearer)


def test_absolute_expiry_cannot_slide(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    login(client)
    real_now = security.utcnow()
    clock = {"now": real_now}
    monkeypatch.setattr(security, "utcnow", lambda: clock["now"])

    # Frequent activity (well inside the idle window) keeps the session alive...
    step = timedelta(hours=settings.session_idle_hours / 2)
    while clock["now"] + step < real_now + timedelta(hours=settings.session_duration_hours):
        clock["now"] += step
        assert client.get("/api/session/me").status_code == 200
    # ...but never past the absolute deadline fixed at login.
    clock["now"] = real_now + timedelta(hours=settings.session_duration_hours, seconds=1)
    assert client.get("/api/session/me").status_code == 401
    with factory() as db:
        assert db.query(AppSession).count() == 0


def test_idle_expiry(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    login(client)
    idle = timedelta(hours=settings.session_idle_hours)
    start = security.utcnow()
    monkeypatch.setattr(security, "utcnow", lambda: start + idle - timedelta(seconds=1))
    assert client.get("/api/session/me").status_code == 200  # activity refreshes the idle deadline
    monkeypatch.setattr(security, "utcnow", lambda: start + 2 * idle - timedelta(seconds=2))
    assert client.get("/api/session/me").status_code == 200
    monkeypatch.setattr(security, "utcnow", lambda: start + 3 * idle)
    assert client.get("/api/session/me").status_code == 401


def test_session_upgrade_restart(client: TestClient) -> None:
    # A restarted app resolves the same cookie through the persisted digest.
    login(client)
    cookie = client.cookies.get(settings.session_cookie_name)
    restarted = TestClient(app, base_url="http://localhost")
    restarted.cookies.set(settings.session_cookie_name, cookie)
    assert restarted.get("/api/session/me").json()["user"]["username"] == "local"
    restarted.cookies.set(settings.session_cookie_name, security.session_digest(cookie))
    assert restarted.get("/api/session/me").status_code == 401  # a stolen row value is not a bearer
