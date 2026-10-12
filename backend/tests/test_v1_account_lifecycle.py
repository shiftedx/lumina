"""Password changes need the current password, admin resets are
single-use links, and deactivation revokes sessions and stops owned work."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import AppSession, DownloadJob, User
from app.security import CSRF_HEADER, hash_password
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

NEW_PASSWORD = "Brand-new-passphrase-7"
ORIGIN = settings.allowed_origins_list[0]


def fresh_client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def add_member(factory, username: str = "member") -> str:
    user_id = str(uuid.uuid4())
    with factory.begin() as db:
        db.add(User(id=user_id, username=username, display_name=username.title(), password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    return user_id


def test_password_change_requires_current(client: TestClient, factory) -> None:
    csrf = login(client)
    other = fresh_client()
    login(other)
    headers = {"Origin": ORIGIN, CSRF_HEADER: csrf}

    # A valid session alone cannot replace the password.
    assert client.put("/api/session/me", json={"password": NEW_PASSWORD}, headers=headers).status_code == 422
    wrong = client.post("/api/me/password", json={"current_password": "nope", "new_password": NEW_PASSWORD}, headers=headers)
    assert wrong.status_code == 403

    changed = client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers)
    assert changed.status_code == 200, changed.text
    rotated_csrf = changed.json()["csrf_token"]
    assert rotated_csrf and rotated_csrf != csrf
    # This browser keeps working on the rotated session; every other session is gone.
    assert client.get("/api/session/me").json()["csrf_token"] == rotated_csrf
    assert other.get("/api/session/me").status_code == 401
    with factory() as db:
        assert db.query(AppSession).count() == 1
    assert fresh_client().post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 401
    login(fresh_client(), password=NEW_PASSWORD)


def test_admin_reset_link_is_single_use(client: TestClient, factory) -> None:
    member_id = add_member(factory)
    member = fresh_client()
    login(member, "member")
    csrf = login(client)
    # Admins can no longer set a reusable temporary password directly.
    assert client.put(f"/api/admin/users/{member_id}", json={"password": NEW_PASSWORD}, headers={"Origin": ORIGIN, CSRF_HEADER: csrf}).status_code == 422
    issued = client.post(f"/api/admin/users/{member_id}/reset-link", headers={"Origin": ORIGIN, CSRF_HEADER: csrf})
    assert issued.status_code == 201, issued.text
    token = issued.json()["reset_url"].split("#reset=", 1)[1]

    anonymous = fresh_client()
    assert anonymous.post("/api/password-reset/redeem", json={"token": token, "new_password": "short"}).status_code == 422
    assert anonymous.post("/api/password-reset/redeem", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 204
    assert anonymous.post("/api/password-reset/redeem", json={"token": token, "new_password": NEW_PASSWORD + "x"}).status_code == 410
    assert member.get("/api/session/me").status_code == 401
    login(fresh_client(), "member", NEW_PASSWORD)


def test_disabled_user_no_new_or_existing_work(client: TestClient, factory) -> None:
    member_id = add_member(factory)
    with factory.begin() as db:
        db.add(DownloadJob(id="job-1", user_id=member_id, source_url="https://example.com/v", status="queued"))
    member = fresh_client()
    login(member, "member")
    csrf = login(client)

    response = client.put(f"/api/admin/users/{member_id}", json={"is_active": False}, headers={"Origin": ORIGIN, CSRF_HEADER: csrf})
    assert response.status_code == 200, response.text
    assert member.get("/api/session/me").status_code == 401
    assert fresh_client().post("/api/session/login", json={"username": "member", "password": PASSWORD}).status_code == 401
    with factory() as db:
        assert db.query(AppSession).filter(AppSession.user_id == member_id).count() == 0
        job = db.get(DownloadJob, "job-1")
        assert job is not None and job.status == "cancelled"  # history retained, work stopped
