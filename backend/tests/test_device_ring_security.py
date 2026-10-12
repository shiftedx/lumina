"""App polish 6.2: ring validation applies every session rule but never refreshes a session; the ring is a credential."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import settings
from app.models import AppSession, User
from app.security import hash_password
from support import make_user

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def factory(db_factory, api_client):  # noqa: ANN001, ANN201
    with db_factory.begin() as session:
        session.add(make_user("m1", username="member1", password_hash=hash_password(PASSWORD)))
    return db_factory


def new_session(factory) -> str:  # noqa: ANN001
    with factory() as db:
        _, token = security.create_app_session(db, db.get(User, "m1"))
        db.commit()
    return token


def test_peek_applies_expiry_without_touching_the_session(factory, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    token = new_session(factory)
    with factory() as db:
        seen = db.get(AppSession, security.session_digest(token)).last_seen_at
    start = security.utcnow()
    monkeypatch.setattr(security, "utcnow", lambda: start + timedelta(hours=settings.session_idle_hours) - timedelta(minutes=1))
    with factory() as db:
        assert security.peek_session_user(db, token).id == "m1"
        db.commit()
    with factory() as db:
        assert db.get(AppSession, security.session_digest(token)).last_seen_at == seen  # never touched
    monkeypatch.setattr(security, "utcnow", lambda: start + timedelta(hours=settings.session_idle_hours) + timedelta(minutes=1))
    with factory() as db:
        assert security.peek_session_user(db, token) is None
        db.commit()
    with factory() as db:
        assert db.get(AppSession, security.session_digest(token)) is None  # an idle session is deleted, as resolve does


def test_peek_refuses_inactive_members_and_unknown_tokens(factory) -> None:  # noqa: ANN001
    token = new_session(factory)
    with factory.begin() as db:
        db.get(User, "m1").is_active = False
    with factory() as db:
        assert security.peek_session_user(db, token) is None
        assert security.peek_session_user(db, security.session_digest(token)) is None
        assert security.peek_session_user(db, None) is None


def test_resolve_still_touches_after_the_interval(factory, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    token = new_session(factory)
    start = security.utcnow()
    monkeypatch.setattr(security, "utcnow", lambda: start + timedelta(seconds=security.SESSION_TOUCH_INTERVAL_SECONDS + 1))
    with factory() as db:
        assert security.resolve_session_user(db, token).id == "m1"
        db.commit()
    with factory() as db:
        assert db.get(AppSession, security.session_digest(token)).last_seen_at > start


def test_revoke_token_deletes_only_that_session(factory) -> None:  # noqa: ANN001
    first, second = new_session(factory), new_session(factory)
    with factory() as db:
        security.revoke_token(db, first)
        db.commit()
    with factory() as db:
        assert db.get(AppSession, security.session_digest(first)) is None
        assert db.get(AppSession, security.session_digest(second)) is not None


def test_the_ring_cookie_alone_needs_an_origin(api_client) -> None:  # noqa: ANN001
    client: TestClient = api_client(base_url="http://localhost")
    client.cookies.set(f"{settings.session_cookie_name}_ring", "x" * 64, path="/api/session")
    refused = client.post("/api/session/logout")
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Missing Origin or Referer header for a state-changing session request."}
