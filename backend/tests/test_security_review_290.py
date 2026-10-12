"""2.9.0 security review: app passwords and agent tokens never reach account takeover, persistent TOTP lockout, owner
capability needs two-step when required, and Infuse-style apps keep working."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import security
from app.models import User
from app.models import utcnow
from app.services import two_factor
from app.services.rate_limit import rate_limiter
from support import seed_app_settings
from test_two_factor import MB, _app_password, _challenge, _enroll, _fresh, _headers, _now_code, _origin  # noqa: F401
from test_jellyfin_auth import PASSWORD as JF_PASSWORD, jf, mb_header  # noqa: F401  (fixture)
from test_v1_account_lifecycle import ORIGIN, add_member
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)


@pytest.fixture
def owner_app(client: TestClient, factory):  # noqa: ANN201, F811
    """The owner ("local", u1) with two-step on, signed in in ``client``, plus an app password and a write agent token."""
    with factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    member_id = add_member(factory, "kid")
    secret, codes = _enroll(client, login(client))
    client.cookies.clear()
    csrf = client.post("/api/session/two-factor", json={"challenge": _challenge(client), "code": _now_code(secret, 1)}).json()["csrf_token"]
    password = _app_password(client, csrf)["password"]
    agent = client.post("/api/connected-apps", json={"name": "bot", "scope": "write"}, headers=_headers(csrf))
    assert agent.status_code == 201, agent.text
    return {"csrf": csrf, "basic": ("local", password), "bearer": {"Authorization": f"Bearer {agent.json()['token']}"},
            "member": member_id, "secret": secret, "codes": codes}


def test_an_app_password_or_agent_token_cannot_take_over_the_owner(client: TestClient, owner_app) -> None:  # noqa: F811
    basic, bearer, member = owner_app["basic"], owner_app["bearer"], owner_app["member"]
    for kwargs in ({"auth": basic}, {"headers": bearer}):
        fresh = _fresh()
        assert fresh.post("/api/admin/users/u1/reset-link", **kwargs).status_code == 403
        assert fresh.post(f"/api/admin/users/{member}/reset-link", **kwargs).status_code == 403
        assert fresh.delete("/api/admin/users/u1/two-factor", **kwargs).status_code == 403
        assert fresh.delete(f"/api/admin/users/{member}/two-factor", **kwargs).status_code == 403
        assert fresh.put(f"/api/admin/users/{member}", json={"role": "admin"}, **kwargs).status_code == 403
        assert fresh.put("/api/admin/public-address", json={"public_address": "https://evil.example"}, **kwargs).status_code == 403
        assert fresh.post("/api/admin/invitations", json={"role": "admin"}, **kwargs).status_code == 403
        assert fresh.post("/api/connected-apps", json={"name": "x", "scope": "write"}, **kwargs).status_code == 403
    # An app password is no owner at all: no admin reads, no account settings.
    assert _fresh().get("/api/admin/users", auth=basic).status_code == 403
    assert _fresh().get("/api/connected-apps", auth=basic).status_code == 403
    assert _fresh().get("/api/me/two-factor", auth=basic).status_code == 403
    assert _fresh().post("/api/me/password", json={"current_password": PASSWORD, "new_password": "A-new-Pa55word-xyz!"}, auth=basic).status_code == 403
    # It still reads what a media app needs.
    assert _fresh().get("/api/session/me", auth=basic).status_code == 200
    assert _fresh().get("/api/library", auth=basic).status_code == 200
    # The two-step setting stays on, and the web sign-in still asks for the code.
    assert "two_factor_required" in _fresh().post("/api/session/login", json={"username": "local", "password": PASSWORD}).json()


def test_the_owner_turns_their_own_two_step_off_with_a_code_not_the_member_reset(client: TestClient, owner_app) -> None:  # noqa: F811
    csrf = owner_app["csrf"]
    assert client.delete("/api/admin/users/u1/two-factor", headers=_headers(csrf)).status_code == 409
    assert client.delete(f"/api/admin/users/{owner_app['member']}/two-factor", headers=_headers(csrf)).status_code == 204
    assert client.post("/api/admin/users/u1/reset-link", headers=_headers(csrf)).status_code == 201


def test_agent_tokens_cannot_be_minted_or_revoked_by_an_app_password(client: TestClient, owner_app) -> None:  # noqa: F811
    apps = client.get("/api/connected-apps").json()
    agent_id = next(row["id"] for row in apps if row["kind"] == "agent")
    assert _fresh().delete(f"/api/connected-apps/{agent_id}", auth=owner_app["basic"]).status_code == 403
    assert _fresh().delete(f"/api/connected-apps/{agent_id}", headers=owner_app["bearer"]).status_code == 403
    assert client.delete(f"/api/connected-apps/{agent_id}", headers=_headers(owner_app["csrf"])).status_code == 204


def _infuse_browses(token: str) -> None:
    headers = mb_header(token)
    for path in ("/users/me", "/userviews", "/items"):
        assert _fresh().get(path, headers=headers).status_code == 200, path
    assert _fresh().post("/sessions/playing/ping", headers=headers).status_code == 204


def test_infuse_with_an_app_password_keeps_working(jf) -> None:  # noqa: ANN001
    client, _ = jf
    client.headers["Origin"] = ORIGIN
    csrf = client.post("/api/session/login", json={"username": "alice", "password": JF_PASSWORD}).json()["csrf_token"]
    _enroll(client, csrf)
    password = client.post("/api/connected-apps/app-passwords", json={"name": "Infuse"}, headers=_headers(csrf)).json()["password"]
    signed_in = _fresh().post("/users/authenticatebyname", json={"Username": "alice", "Pw": password}, headers=mb_header())
    assert signed_in.status_code == 200, signed_in.text
    _infuse_browses(signed_in.json()["AccessToken"])
    assert _fresh().get("/api/library", auth=("alice", password)).status_code == 200


def test_infuse_and_basic_with_the_account_password_without_two_step(jf) -> None:  # noqa: ANN001
    signed_in = _fresh().post("/users/authenticatebyname", json={"Username": "bob", "Pw": JF_PASSWORD}, headers=mb_header())
    assert signed_in.status_code == 200
    _infuse_browses(signed_in.json()["AccessToken"])
    assert _fresh().get("/api/library", auth=("bob", JF_PASSWORD)).status_code == 200
    assert _fresh().post("/api/connected-apps", json={"name": "x", "scope": "read"}, auth=("bob", JF_PASSWORD)).status_code == 403


def test_app_password_scope() -> None:
    allows = security.app_password_allows
    assert allows("GET", "/api/library") and allows("GET", "/api/session/me") and allows("HEAD", "/api/art/x")
    assert allows("PUT", "/api/library/abc/playback") and allows("POST", "/api/library/abc/playback-sessions")
    assert allows("DELETE", "/api/playback-sessions/abc") and allows("PUT", "/api/titles/abc/watched")
    for method, path in (("GET", "/api/admin/users"), ("GET", "/api/connected-apps"), ("GET", "/api/me/two-factor"),
                         ("POST", "/api/session/logout"), ("POST", "/api/connected-apps"), ("POST", "/api/me/password"),
                         ("PUT", "/api/settings/me"), ("POST", "/api/requests"), ("POST", "/api/library/abc/playback/x")):
        assert not allows(method, path), (method, path)


def test_ten_wrong_codes_lock_totp_persistently_until_the_cool_down(client: TestClient, factory, caplog) -> None:  # noqa: ANN001, F811
    with factory() as db:
        seed_app_settings(db)
    secret, codes = _enroll(client, login(client))
    with factory() as db:
        user = db.get(User, "u1")
        with caplog.at_level(logging.INFO, logger="lumina.audit"):
            for _ in range(two_factor.TOTP_LOCK_AFTER):
                assert two_factor.verify(db, user, code="000000") is False
        assert "two_factor.locked user=u1" in caplog.text
        assert two_factor.verify(db, user, code=_now_code(secret, 1)) is False  # the right code, refused while locked
        record = db.get(User, "u1", populate_existing=True)
        assert record.totp_failures == two_factor.TOTP_LOCK_AFTER
        assert timedelta(minutes=14) < record.totp_locked_until - utcnow() <= timedelta(minutes=15)
        # After the cool-down one more miss locks again for twice as long.
        record.totp_locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
        assert two_factor.verify(db, user, code="000000") is False
        record = db.get(User, "u1", populate_existing=True)
        assert timedelta(minutes=29) < record.totp_locked_until - utcnow() <= timedelta(minutes=30)
        # A recovery code still works and clears the count.
        assert two_factor.verify(db, user, recovery_code=codes[0]) is True
        record = db.get(User, "u1", populate_existing=True)
        assert (record.totp_failures, record.totp_locked_until) == (0, None)
        assert two_factor.verify(db, user, code=_now_code(secret, 1)) is True
    assert two_factor.lock_seconds(10) == 900 and two_factor.lock_seconds(30) == two_factor.lock_seconds(10**6) == 24 * 3600


def test_a_challenge_minted_after_the_right_password_counts_toward_the_ip_block(client: TestClient, factory) -> None:  # noqa: F811
    with factory() as db:
        seed_app_settings(db)
    _enroll(client, login(client))
    client.cookies.clear()
    rate_limiter.clear()
    for _ in range(10):
        _challenge(client)
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 429


def test_owner_powers_need_two_step_when_the_household_requires_it(client: TestClient, factory) -> None:  # noqa: F811
    from app.models import AppSettings

    with factory() as db:
        seed_app_settings(db)
        db.add(User(id="u2", username="second", display_name="Second", password_hash=security.hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        second = db.get(User, "u2")
        assert two_factor.owner_capable(db, second) is True
        db.get(AppSettings, 1).require_owner_two_factor = True
        db.commit()
        assert two_factor.owner_capable(db, second) is False
        assert two_factor.owner_capable(db, db.get(User, "u1")) is False  # also not enrolled
    kid = add_member(factory, "kid")
    csrf = login(client, "kid")
    apps = client.post("/api/connected-apps", json={"name": "bot", "scope": "read"}, headers=_headers(csrf)).json()
    other = _fresh()
    other_csrf = login(other, "second")
    assert [row["id"] for row in other.get("/api/connected-apps").json()] == []  # not the household's list
    assert other.delete(f"/api/connected-apps/{apps['app']['id']}", headers=_headers(other_csrf)).status_code == 404
    del kid
