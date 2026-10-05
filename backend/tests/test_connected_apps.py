"""Connected apps: hashed per-device bearer tokens for non-browser clients."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.models import DeviceToken, LibraryItem, User
from app.schemas import UserUpdateRequest
from app.security import CSRF_HEADER, SESSION_TOUCH_INTERVAL_SECONDS, hash_password, session_digest, utcnow
from app.services.redaction import redact
from app.services.users import UserService
from support import make_user

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def factory(db_factory):
    with db_factory.begin() as db:
        db.add_all([
            make_user("owner", role="admin", username="owner", password_hash=hash_password(PASSWORD)),
            make_user("member", username="member", password_hash=hash_password(PASSWORD)),
        ])
    return db_factory


@pytest.fixture
def client(factory, api_client) -> TestClient:
    return api_client(base_url="http://localhost")


def add_token(factory, user_id: str, *, kind: str = "agent", scope: str = "write", last_seen_at: datetime | None = None) -> tuple[str, str]:
    """Insert a Connected app row directly; returns (app id, raw token)."""
    token, app_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    with factory.begin() as db:
        db.add(DeviceToken(
            id=app_id, user_id=user_id, kind=kind, scope=scope, token_digest=session_digest(token),
            device_id=f"agent:{uuid.uuid4()}", device_name="Script", last_seen_at=last_seen_at or utcnow(),
        ))
    return app_id, token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def browser_login(client: TestClient, username: str) -> dict[str, str]:
    """Cookie sign-in; returns the headers a browser mutation needs (trusted Origin + CSRF)."""
    response = client.post("/api/session/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: response.json()["csrf_token"]}


def test_write_agent_token_mutates_without_cookie_origin_or_csrf(client, factory) -> None:
    _, token = add_token(factory, "member")

    changed = client.put("/api/session/me", json={"display_name": "Scripted"}, headers=bearer(token))

    assert changed.status_code == 200, changed.text
    me = client.get("/api/session/me", headers=bearer(token)).json()
    assert me["user"]["display_name"] == "Scripted"
    assert me["csrf_token"] is None  # no cookie, no CSRF token


def test_read_agent_token_lists_the_library_but_every_mutation_is_403(client, factory) -> None:
    with factory.begin() as db:
        db.add(LibraryItem(id="shared-1", user_id="owner", visibility="shared", title="Household film", metadata_json={}, status="available"))
    _, token = add_token(factory, "member", scope="read")

    listed = client.get("/api/library", headers=bearer(token))
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == ["shared-1"]

    assert client.post("/api/jobs", json={"source_url": "https://example.com/v"}, headers=bearer(token)).status_code == 403
    assert client.put("/api/session/me", json={"display_name": "Nope"}, headers=bearer(token)).status_code == 403
    assert client.get("/api/session/me", headers=bearer(token)).json()["user"]["display_name"] == "Member"


def test_cookie_session_still_needs_csrf_even_with_a_valid_bearer(client, factory) -> None:
    _, token = add_token(factory, "owner")
    headers = browser_login(client, "owner")

    no_csrf = client.put("/api/session/me", json={"display_name": "X"}, headers={"Origin": headers["Origin"], **bearer(token)})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["detail"] == "CSRF token missing or invalid."
    assert client.put("/api/session/me", json={"display_name": "Owner 2"}, headers=headers).status_code == 200


def test_unknown_wrong_kind_oversized_and_deleted_tokens_are_401(client, factory) -> None:
    app_id, token = add_token(factory, "member")
    _, jellyfin_token = add_token(factory, "member", kind="jellyfin")

    assert client.get("/api/session/me", headers=bearer("not-a-token")).status_code == 401
    assert client.get("/api/session/me", headers=bearer(jellyfin_token)).status_code == 401
    assert client.get("/api/session/me", headers=bearer("x" * 600)).status_code == 401
    assert client.get("/api/session/me", headers=bearer(token)).status_code == 200
    with factory.begin() as db:
        db.delete(db.get(DeviceToken, app_id))
    assert client.get("/api/session/me", headers=bearer(token)).status_code == 401


def test_password_change_reset_and_deactivation_revoke_every_connected_app(client, factory) -> None:
    _, token = add_token(factory, "member")
    add_token(factory, "member", kind="jellyfin")
    _, owner_token = add_token(factory, "owner")

    with factory() as db:
        UserService(db).change_password(db.get(User, "member"), PASSWORD, "New secure passphrase 42!")
    assert client.get("/api/session/me", headers=bearer(token)).status_code == 401
    assert client.get("/api/session/me", headers=bearer(owner_token)).status_code == 200

    add_token(factory, "member")
    with factory() as db:
        service = UserService(db)
        _, reset = service.issue_account_token(db.get(User, "owner"), "reset", expires_in_hours=1, user_id="member")
        service.redeem_password_reset(reset, "Another secure phrase 43!")
        assert db.query(DeviceToken).filter(DeviceToken.user_id == "member").count() == 0

    add_token(factory, "member")
    with factory() as db:
        UserService(db).manage_user(db.get(User, "owner"), "member", UserUpdateRequest(is_active=False))
        assert db.query(DeviceToken).filter(DeviceToken.user_id == "member").count() == 0
        assert db.query(DeviceToken).filter(DeviceToken.user_id == "owner").count() == 1


def test_idle_tokens_are_deleted_and_last_seen_touch_is_throttled(client, factory) -> None:
    idle_id, idle = add_token(factory, "member", last_seen_at=utcnow() - timedelta(days=91))
    assert client.get("/api/session/me", headers=bearer(idle)).status_code == 401
    with factory() as db:
        assert db.get(DeviceToken, idle_id) is None

    recent = utcnow() - timedelta(seconds=60)
    recent_id, fresh = add_token(factory, "member", last_seen_at=recent)
    assert client.get("/api/session/me", headers=bearer(fresh)).status_code == 200
    with factory() as db:
        assert db.get(DeviceToken, recent_id).last_seen_at == recent

    stale = utcnow() - timedelta(seconds=SESSION_TOUCH_INTERVAL_SECONDS + 5)
    stale_id, touched = add_token(factory, "member", last_seen_at=stale)
    assert client.get("/api/session/me", headers=bearer(touched)).status_code == 200
    with factory() as db:
        assert db.get(DeviceToken, stale_id).last_seen_at > stale


def test_agent_token_is_shown_once_hashed_at_rest_and_usable(client, factory) -> None:
    headers = browser_login(client, "member")

    created = client.post("/api/connected-apps", json={"name": "  Home Assistant ", "scope": "read"}, headers=headers)

    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    token = body["token"]
    assert body["app"]["kind"] == "agent" and body["app"]["scope"] == "read" and body["app"]["device_name"] == "Home Assistant"
    with factory() as db:
        rows = db.execute(text("SELECT * FROM device_tokens")).all()
    assert len(rows) == 1 and token not in repr(rows) and rows[0].token_digest == session_digest(token)
    assert rows[0].device_id.startswith("agent:")
    listed = client.get("/api/connected-apps")
    assert token not in listed.text and [row["id"] for row in listed.json()] == [body["app"]["id"]]
    client.cookies.clear()
    assert client.get("/api/session/me", headers=bearer(token)).json()["user"]["id"] == "member"


def test_bearer_requests_cannot_mint_agent_tokens(client, factory) -> None:
    _, token = add_token(factory, "member")
    read_id, read_token = add_token(factory, "member", scope="read")

    response = client.post("/api/connected-apps", json={"name": "Child", "scope": "write"}, headers=bearer(token))

    assert response.status_code == 403
    assert client.delete(f"/api/connected-apps/{read_id}", headers=bearer(read_token)).status_code == 403  # read scope
    with factory() as db:
        assert db.query(DeviceToken).count() == 2


def test_members_see_their_own_apps_and_the_admin_sees_everyone(factory, api_client) -> None:
    owner_app, _ = add_token(factory, "owner")
    member_app, _ = add_token(factory, "member", kind="jellyfin")
    add_token(factory, "member", last_seen_at=utcnow() - timedelta(days=91))  # idle: hidden
    member_client, owner_client = api_client(base_url="http://localhost"), api_client(base_url="http://localhost")
    member_headers, owner_headers = browser_login(member_client, "member"), browser_login(owner_client, "owner")

    mine = member_client.get("/api/connected-apps").json()
    assert [(row["id"], row["owner_display_name"]) for row in mine] == [(member_app, None)]
    everyone = {row["id"]: row["owner_display_name"] for row in owner_client.get("/api/connected-apps").json()}
    assert everyone == {owner_app: "Owner", member_app: "Member"}

    assert member_client.delete(f"/api/connected-apps/{owner_app}", headers=member_headers).status_code == 404
    assert owner_client.delete(f"/api/connected-apps/{member_app}", headers=owner_headers).status_code == 204
    assert member_client.get("/api/connected-apps").json() == []
    assert owner_client.delete(f"/api/connected-apps/{member_app}", headers=owner_headers).status_code == 404


@pytest.mark.parametrize("payload", [{"name": "   ", "scope": "read"}, {"name": "x", "scope": "admin"}, {"name": "x", "scope": "read", "kind": "jellyfin"}, {"scope": "read"}])
def test_create_rejects_invalid_requests(client, factory, payload) -> None:
    headers = browser_login(client, "member")
    assert client.post("/api/connected-apps", json=payload, headers=headers).status_code == 422
    with factory() as db:
        assert db.query(DeviceToken).count() == 0


def test_redaction_covers_jellyfin_token_headers_and_query_keys() -> None:
    text_in = (
        "X-Emby-Token: s3cret-a\n"
        "x-mediabrowser-token: s3cret-b\n"
        'X-Emby-Authorization: MediaBrowser Client="Infuse", Token="s3cret-c"\n'
        "GET /jellyfin/videos/1/stream?ApiKey=s3cret-d&api_key=s3cret-e&static=true"
    )
    out = redact(text_in)
    for secret in ("s3cret-a", "s3cret-b", "s3cret-c", "s3cret-d", "s3cret-e"):
        assert secret not in out, (secret, out)
    assert "static=true" in out
