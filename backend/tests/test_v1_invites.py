"""Admin-issued local invitations are digest-stored, role-bound, single-use and atomic."""
from __future__ import annotations

import threading
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.main import app
from app.models import AccountToken, User
from app.security import CSRF_HEADER, utcnow
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

INVITEE_PASSWORD = "Invitee-passphrase-9"


def issue(client: TestClient, **body) -> dict:
    csrf = login(client)
    origin = settings.allowed_origins_list[0]
    response = client.post("/api/admin/invitations", json=body, headers={"Origin": origin, CSRF_HEADER: csrf})
    assert response.status_code == 201, response.text
    return response.json()


def token_of(invitation: dict) -> str:
    url = invitation["invitation_url"]
    assert url.startswith(settings.resolved_frontend_public_url + "/#invite=")
    return url.split("#invite=", 1)[1]


def redeem(token: str, username: str = "guest", **extra) -> int:
    body = {"token": token, "username": username, "display_name": "Guest", "password": INVITEE_PASSWORD, **extra}
    response = TestClient(app, base_url="http://localhost").post("/api/invitations/redeem", json=body)
    return response.status_code


def test_invite_single_use_race(client: TestClient, factory) -> None:
    token = token_of(issue(client))
    with factory() as db:
        assert token not in repr(db.execute(text("SELECT * FROM account_tokens")).all())

    statuses: list[int] = []
    barrier = threading.Barrier(4)

    def attempt(index: int) -> None:
        barrier.wait()
        statuses.append(redeem(token, f"guest{index}"))

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [201, 410, 410, 410]
    with factory() as db:
        guests = db.query(User).filter(User.username.like("guest%")).all()
        assert len(guests) == 1 and guests[0].role == "viewer"
    # The invitee signs in locally with their own password.
    assert TestClient(app, base_url="http://localhost").post(
        "/api/session/login", json={"username": guests[0].username, "password": INVITEE_PASSWORD}
    ).status_code == 200


def test_invite_role_tamper_rejected(client: TestClient, factory) -> None:
    token = token_of(issue(client, role="viewer"))
    assert redeem(token, role="admin") == 422
    assert redeem(token) == 201
    with factory() as db:
        assert db.query(User).filter(User.username == "guest").one().role == "viewer"


def test_invite_failures_leave_no_orphans(client: TestClient, factory) -> None:
    token = token_of(issue(client))
    assert redeem(token, "local") == 409  # username taken: the invite stays usable
    assert redeem("not-a-real-token") == 410
    with factory() as db:
        db.query(AccountToken).update({"expires_at": utcnow() - timedelta(seconds=1)})
        db.commit()
    assert redeem(token) == 410
    with factory() as db:
        assert db.query(User).count() == 1
        assert db.query(AccountToken).one().used_at is None


def test_invite_issue_requires_admin(client: TestClient, factory) -> None:
    with factory.begin() as db:
        from app.security import hash_password

        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    csrf = login(client, "member")
    response = client.post(
        "/api/admin/invitations", json={}, headers={"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf}
    )
    assert response.status_code == 403
