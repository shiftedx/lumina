""""Who's watching?" never signs anyone in without their own live session on this
browser (created by their password, with their opt-in) or their password, and every session rule still applies."""
from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, security
from app.config import settings
from app.models import AppSession, User
from app.schemas import SessionLoginRequest, UserUpdateRequest
from app.security import hash_password
from app.services.users import UserService
from support import make_user

PASSWORD = "Test-only-passphrase-1"
ORIGIN = {"Origin": settings.allowed_origins_list[0]}
SESSION = settings.session_cookie_name
RING = f"{settings.session_cookie_name}_ring"


@pytest.fixture
def factory(db_factory, api_client):  # noqa: ANN001, ANN201
    hashed = hash_password(PASSWORD)  # one PBKDF2 run for every member
    with db_factory.begin() as session:
        session.add(make_user("owner", role="admin", username="owner", display_name="Owner", password_hash=hashed))
        for index in range(1, 8):
            session.add(make_user(f"m{index}", username=f"member{index}", display_name=f"Member {index}", password_hash=hashed))
    return db_factory


@pytest.fixture
def client(factory, api_client):  # noqa: ANN001, ANN201
    return api_client(base_url="http://localhost")


def login(client: TestClient, username: str, remember: bool = False):  # noqa: ANN201
    response = client.post("/api/session/login", json={"username": username, "password": PASSWORD, "remember_on_device": remember}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response


def ring(client: TestClient) -> list[str]:
    value = client.cookies.get(RING)
    return value.split(".") if value else []


def members(client: TestClient) -> list[dict]:
    response = client.get("/api/session/device-members")
    assert response.status_code == 200, response.text
    return response.json()


def usernames(client: TestClient) -> list[str]:
    return [member["username"] for member in members(client)]


def switch(client: TestClient, user_id: str, headers: dict | None = None):  # noqa: ANN201
    return client.post("/api/session/switch", json={"user_id": user_id}, headers=ORIGIN if headers is None else headers)


def alive(api_client, token: str) -> bool:  # noqa: ANN001
    probe = api_client(base_url="http://localhost")
    probe.cookies.set(SESSION, token)
    return probe.get("/api/session/me").status_code == 200


# ---- login ----

def test_remember_on_device_defaults_to_off() -> None:
    assert SessionLoginRequest(username="a", password="b").remember_on_device is False


def test_login_without_remember_leaves_the_ring_unchanged(client: TestClient) -> None:
    response = login(client, "member1")
    assert not any(header.startswith(f"{RING}=") for header in response.headers.get_list("set-cookie"))
    assert ring(client) == []
    assert members(client) == []


def test_remember_appends_this_sessions_token(client: TestClient) -> None:
    login(client, "member1", remember=True)
    assert ring(client) == [client.cookies.get(SESSION)]
    assert usernames(client) == ["member1"]


def plant_ring(client: TestClient, value: str) -> None:
    """Overwrites this browser's ring cookie in place (same domain and path), as a hand-edited or older ring."""
    existing = next(cookie for cookie in client.cookies.jar if cookie.name == RING)
    client.cookies.set(RING, value, domain=existing.domain, path=existing.path)


def owner_tokens_in(client: TestClient, factory) -> list[str]:  # noqa: ANN001
    """Every value this browser holds (ring segments and the session cookie) that authenticates as the vault owner."""
    candidates = [*ring(client), client.cookies.get(SESSION)]
    with factory() as db:
        return [value for value in candidates if value and (user := security.resolve_session_user(db, value)) is not None and user.role == "admin"]


def test_a_remembered_owner_is_a_marker_that_authenticates_nothing(client: TestClient, factory, api_client) -> None:  # noqa: ANN001
    # The ring never holds a usable owner bearer.
    login(client, "member1", remember=True)
    login(client, "owner", remember=True)
    owner_session = client.cookies.get(SESSION)
    assert owner_session not in ring(client)
    assert [member["switch"] for member in members(client)] == ["instant", "password"]
    assert switch(client, "m1").status_code == 200  # owner -> member: the owner's session is signed out
    assert not alive(api_client, owner_session)
    assert owner_tokens_in(client, factory) == []
    for segment in ring(client):
        assert not alive(api_client, segment) or segment == client.cookies.get(SESSION)
    assert switch(client, "owner").status_code == 401
    assert [member["username"] for member in members(client)] == ["member1", "owner"]


def test_a_forged_owner_marker_is_ignored(client: TestClient) -> None:
    login(client, "owner", remember=True)
    marker = ring(client)[0]
    client.cookies.set(RING, marker.replace("owner", "m1", 1), path="/api/session")
    assert members(client) == []


def test_an_owner_token_left_in_an_older_ring_is_revoked_and_becomes_a_marker(client: TestClient, factory, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    with factory() as db:
        _, legacy = security.create_app_session(db, db.get(User, "owner"), None)
        db.commit()
    plant_ring(client, ".".join([legacy, client.cookies.get(SESSION)]))
    assert [(member["username"], member["switch"]) for member in members(client)] == [("owner", "password"), ("member1", "instant")]
    assert not alive(api_client, legacy)
    assert legacy not in ring(client)
    assert owner_tokens_in(client, factory) == []


def test_an_active_owner_session_in_an_older_ring_is_signed_out_when_left(client: TestClient, factory, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    member_token = client.cookies.get(SESSION)
    login(client, "owner")
    owner_session = client.cookies.get(SESSION)
    plant_ring(client, ".".join([member_token, owner_session]))
    assert members(client)[-1]["switch"] == "password"
    assert alive(api_client, owner_session)  # still the browser's active session until it leaves
    assert switch(client, "m1").status_code == 200
    assert not alive(api_client, owner_session)
    assert owner_tokens_in(client, factory) == []


@pytest.mark.parametrize("via", ["update_user", "manage_user"])
def test_a_role_change_signs_the_member_out_everywhere(client: TestClient, factory, api_client, via: str) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    promoted = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    with factory() as db:
        service = UserService(db)
        if via == "update_user":
            service.update_user("m1", UserUpdateRequest(role="admin"))
        else:
            service.manage_user(db.get(User, "owner"), "m1", UserUpdateRequest(role="admin"))
        db.commit()
    assert not alive(api_client, promoted)
    assert switch(client, "m1").status_code == 404
    assert owner_tokens_in(client, factory) == []


def test_an_unchanged_role_keeps_the_members_sessions(client: TestClient, factory, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    token = client.cookies.get(SESSION)
    with factory() as db:
        UserService(db).update_user("m1", UserUpdateRequest(role="viewer", display_name="Renamed"))
        db.commit()
    assert alive(api_client, token)


def test_ring_cookie_attributes_mirror_the_session_cookie(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for secure, samesite in ((False, "lax"), (True, "strict")):
        monkeypatch.setattr(settings, "session_cookie_secure", secure)
        monkeypatch.setattr(settings, "session_cookie_samesite", samesite)
        response = login(client, "member1", remember=True)
        header = next(value for value in response.headers.get_list("set-cookie") if value.startswith(f"{RING}="))
        parts = [part.strip().lower() for part in header.split(";")]
        assert "httponly" in parts
        assert "path=/api/session" in parts
        assert f"max-age={settings.session_duration_hours * 3600}" in parts
        assert f"samesite={samesite}" in parts
        assert ("secure" in parts) is secure


def test_the_seventh_member_evicts_the_oldest(client: TestClient) -> None:
    for index in range(1, 8):
        login(client, f"member{index}", remember=True)
    assert len(ring(client)) == 6
    assert usernames(client) == [f"member{index}" for index in range(2, 8)]


def test_the_evicted_oldest_session_is_revoked(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    oldest = client.cookies.get(SESSION)
    for index in range(2, 8):
        login(client, f"member{index}", remember=True)
    assert oldest not in ring(client)
    assert not alive(api_client, oldest)


def test_one_entry_per_member_and_the_older_session_is_revoked(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    login(client, "member1", remember=True)
    assert usernames(client) == ["member2", "member1"]
    assert first not in ring(client)
    assert not alive(api_client, first)


def test_signing_in_over_an_unremembered_session_signs_it_out(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1")
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    assert not alive(api_client, first)


def test_signing_in_over_a_remembered_session_keeps_it(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2")
    assert alive(api_client, first)
    assert usernames(client) == ["member1"]


# ---- device-members ----

def test_device_members_marks_vault_owners_and_the_active_member(client: TestClient) -> None:
    login(client, "owner", remember=True)
    login(client, "member1", remember=True)
    assert members(client) == [
        {"user_id": "owner", "display_name": "Owner", "username": "owner", "role": "admin", "switch": "password", "active": False},
        {"user_id": "m1", "display_name": "Member 1", "username": "member1", "role": "viewer", "switch": "instant", "active": True},
    ]


def test_device_members_drops_dead_entries_and_rewrites_the_cookie(client: TestClient, factory) -> None:  # noqa: ANN001
    for index in range(1, 6):
        login(client, f"member{index}", remember=True)
    tokens = ring(client)
    now = security.utcnow()
    with factory.begin() as db:
        db.get(AppSession, security.session_digest(tokens[0])).expires_at = now - timedelta(seconds=1)       # absolute expiry
        db.get(AppSession, security.session_digest(tokens[1])).last_seen_at = now - timedelta(hours=settings.session_idle_hours, seconds=1)  # idle
        db.delete(db.get(AppSession, security.session_digest(tokens[2])))                                   # revoked
        db.get(User, "m4").is_active = False                                                                  # deactivated
    assert usernames(client) == ["member5"]
    assert ring(client) == [tokens[4]]


def test_listing_the_ring_never_extends_a_remembered_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    start = security.utcnow()
    idle = timedelta(hours=settings.session_idle_hours)
    monkeypatch.setattr(security, "utcnow", lambda: start + idle - timedelta(minutes=1))
    assert "member1" in usernames(client)
    monkeypatch.setattr(security, "utcnow", lambda: start + idle + timedelta(minutes=1))
    assert "member1" not in usernames(client)


def test_nothing_lists_members_without_a_ring(client: TestClient, api_client) -> None:  # noqa: ANN001
    anonymous = api_client(base_url="http://localhost")
    response = anonymous.get("/api/session/device-members")
    assert response.json() == []
    assert not any(header.startswith(f"{RING}=") for header in response.headers.get_list("set-cookie"))
    assert anonymous.get("/api/admin/users").status_code == 401
    login(client, "member1")
    assert client.get("/api/admin/users").status_code == 403


# ---- switch ----

def test_switch_signs_in_a_remembered_member_with_a_fresh_csrf_token(client: TestClient) -> None:
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    response = switch(client, "m1")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "member1"
    rotated = client.cookies.get(SESSION)
    assert response.json()["csrf_token"] == security.csrf_token_for(rotated)
    assert client.get("/api/session/me").json()["user"]["username"] == "member1"


def test_every_switch_rotates_the_targets_bearer_and_keeps_its_absolute_expiry(client: TestClient, factory, api_client) -> None:  # noqa: ANN001
    # Security review I1: a copied ring segment dies on the next switch to that member.
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    with factory() as db:
        deadline = db.get(AppSession, security.session_digest(first)).expires_at
    login(client, "member2", remember=True)
    assert switch(client, "m1").status_code == 200
    rotated = client.cookies.get(SESSION)
    assert rotated != first
    assert not alive(api_client, first)
    assert rotated in ring(client) and first not in ring(client)
    with factory() as db:
        assert db.get(AppSession, security.session_digest(rotated)).expires_at == deadline
    assert usernames(client) == ["member1", "member2"]


def test_a_losing_concurrent_switch_with_the_same_old_token_mints_nothing(client: TestClient, factory, monkeypatch) -> None:  # noqa: ANN001
    # The old session is claimed atomically; the loser gets 404 and no new session.
    import sys

    from sqlalchemy.orm import Session as OrmSession

    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    original_get = OrmSession.get

    def get_then_lose_the_race(self, entity, ident, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        row = original_get(self, entity, ident, *a, **kw)
        if entity is AppSession and ident == security.session_digest(first) and sys._getframe(1).f_code.co_name == "switch_session":
            with factory() as other:  # the winning switch commits between our read and our claim
                other.query(AppSession).filter(AppSession.id == ident).delete()
                other.commit()
        return row

    monkeypatch.setattr(OrmSession, "get", get_then_lose_the_race)
    with factory() as db:
        before = db.query(AppSession).count()
    response = switch(client, "m1")
    assert (response.status_code, response.json()) == (404, {"detail": "not_in_ring"})
    with factory() as db:
        assert db.query(AppSession).count() == before - 1  # only the winner's delete; nothing minted


def test_switch_refuses_a_vault_owner_without_a_password(client: TestClient) -> None:
    login(client, "owner", remember=True)
    login(client, "member1", remember=True)
    before = client.cookies.get(SESSION)
    response = switch(client, "owner")
    assert response.status_code == 401
    assert response.json() == {"detail": "password_required"}
    assert client.cookies.get(SESSION) == before


def test_switch_refuses_a_member_not_in_the_ring(client: TestClient) -> None:
    login(client, "member1", remember=True)
    login(client, "member2")
    assert switch(client, "m2").status_code == 404  # signed in here, but never remembered: not a ring member
    assert switch(client, "m3").json() == {"detail": "not_in_ring"}
    assert switch(client, "m3").status_code == 404
    assert switch(client, "nobody").status_code == 404


def test_switch_refuses_a_forged_ring(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    real = client.cookies.get(SESSION)
    for forged in (secrets.token_urlsafe(48), security.session_digest(real), "<script>..;", "a" * 5000):
        other = api_client(base_url="http://localhost")
        other.cookies.set(RING, forged, path="/api/session")
        response = switch(other, "m1")
        assert response.status_code == 404, forged[:12]
        assert other.cookies.get(SESSION) is None


def test_switch_is_rate_limited(client: TestClient) -> None:
    # The session_login bucket is checked on every switch but charged only on a failure.
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    ok = [switch(client, "m1" if index % 2 == 0 else "m2").status_code for index in range(11)]
    assert ok == [200] * 11  # a successful switch reuses a session already on this browser: not a guess
    failed = [switch(client, "not-in-ring").status_code for _ in range(11)]
    assert failed[:10] == [404] * 10
    assert failed[10] == 429


def test_switch_signs_out_an_unremembered_member_and_keeps_a_remembered_one(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2")
    unremembered = client.cookies.get(SESSION)
    assert switch(client, "m1").status_code == 200
    assert not alive(api_client, unremembered)
    login(client, "member3", remember=True)
    remembered = client.cookies.get(SESSION)
    assert switch(client, "m1").status_code == 200
    assert alive(api_client, remembered)


def test_switch_to_the_active_member_changes_nothing(client: TestClient) -> None:
    login(client, "member1", remember=True)
    before = client.cookies.get(SESSION)
    assert switch(client, "m1").status_code == 200
    assert client.cookies.get(SESSION) == before


def test_an_active_owner_switching_to_themselves_is_a_free_no_op(client: TestClient) -> None:
    login(client, "owner", remember=True)
    before = client.cookies.get(SESSION)
    assert [switch(client, "owner").status_code for _ in range(11)] == [200] * 11
    assert client.cookies.get(SESSION) == before
    assert switch(client, "not-in-ring").status_code == 404  # none of the 11 was charged to session_login


def test_switch_keeps_the_current_member_when_the_target_vanishes(client: TestClient, api_client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    target = client.cookies.get(SESSION)
    login(client, "member2")
    unremembered = client.cookies.get(SESSION)
    real = main.peek_session_user

    def vanishing(db, token):  # noqa: ANN001, ANN202
        user = real(db, token)
        if token == target and user is not None:
            db.delete(db.get(AppSession, security.session_digest(token)))
            db.flush()
        return user

    monkeypatch.setattr(main, "peek_session_user", vanishing)
    assert switch(client, "m1").status_code == 404
    assert alive(api_client, unremembered)


def test_a_switch_never_extends_the_absolute_expiry(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    start = security.utcnow()
    # Idle must not be the cause: every session was last seen just before the clock jump.
    with factory.begin() as db:
        for record in db.query(AppSession).all():
            record.last_seen_at = start + timedelta(hours=settings.session_duration_hours, seconds=1) - timedelta(minutes=1)
    monkeypatch.setattr(security, "utcnow", lambda: start + timedelta(hours=settings.session_duration_hours, seconds=-120))
    assert switch(client, "m1").status_code == 200  # control: still inside the absolute window
    monkeypatch.setattr(security, "utcnow", lambda: start + timedelta(hours=settings.session_duration_hours, seconds=1))
    assert switch(client, "m2").status_code == 404  # only the absolute expiry can refuse it


def test_a_password_change_revokes_the_ring_entry(client: TestClient, factory) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    with factory() as db:
        UserService(db).change_password(db.get(User, "m1"), PASSWORD, "Another-test-passphrase-2")
        db.commit()
    assert switch(client, "m1").status_code == 404
    assert usernames(client) == ["member2"]


def test_a_deactivated_member_cannot_be_switched_to(client: TestClient, factory) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    with factory.begin() as db:
        db.get(User, "m1").is_active = False
    assert switch(client, "m1").status_code == 404


def test_a_cross_origin_switch_is_refused(client: TestClient) -> None:
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    assert switch(client, "m1", headers={"Origin": "https://attacker.invalid"}).status_code == 403


def test_the_ring_cookie_alone_needs_an_origin(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    other = api_client(base_url="http://localhost")
    other.cookies.set(RING, client.cookies.get(RING), path="/api/session")
    response = switch(other, "m1", headers={})
    assert response.status_code == 403
    assert other.cookies.get(SESSION) is None


# ---- logout and forget ----

def test_logout_removes_the_active_member_from_the_ring(client: TestClient) -> None:
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    assert client.post("/api/session/logout", headers=ORIGIN).status_code == 204
    assert usernames(client) == ["member1"]


def test_an_owner_signing_out_leaves_the_picker(client: TestClient) -> None:
    login(client, "member1", remember=True)
    login(client, "owner", remember=True)
    assert client.post("/api/session/logout", headers=ORIGIN).status_code == 204
    assert usernames(client) == ["member1"]


def test_forget_revokes_removes_and_is_idempotent(client: TestClient, api_client) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    assert client.post("/api/session/forget", json={"user_id": "m1"}, headers=ORIGIN).status_code == 204
    assert usernames(client) == ["member2"]
    assert not alive(api_client, first)
    assert client.post("/api/session/forget", json={"user_id": "m1"}, headers=ORIGIN).status_code == 204
    assert client.post("/api/session/forget", json={"user_id": "m2"}, headers=ORIGIN).status_code == 204
    assert client.cookies.get(SESSION) is None
    assert client.cookies.get(RING) is None
    assert client.get("/api/session/me").status_code == 401


def test_a_duplicate_member_ring_entry_is_revoked_when_dropped(client: TestClient, api_client, factory) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    first = client.cookies.get(SESSION)
    login(client, "member2", remember=True)
    with factory() as db:  # a second live session of member1, as a hand-built ring could carry
        _, extra = security.create_app_session(db, db.get(User, "m1"), None)
    client.cookies.set(RING, ".".join([first, extra, client.cookies.get(SESSION)]), path="/api/session")
    assert usernames(client) == ["member1", "member2"]
    assert alive(api_client, first) and not alive(api_client, extra)


def test_switch_answers_404_when_the_session_vanishes_between_peek_and_apply(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    real = main.peek_session_user
    calls = {"n": 0}

    def vanishing(db, token):  # noqa: ANN001, ANN202
        user = real(db, token)
        calls["n"] += 1
        if calls["n"] == 1 and user is not None:  # m1 (the target) validated: a concurrent sign-out lands right after it
            record = db.get(AppSession, security.session_digest(token))
            db.delete(record)
            db.flush()
        return user

    monkeypatch.setattr(main, "peek_session_user", vanishing)
    assert switch(client, "m1").status_code == 404


def test_forget_drops_ring_entries_whose_session_is_gone(client: TestClient, factory) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    with factory.begin() as db:
        db.delete(db.get(AppSession, security.session_digest(ring(client)[0])))
    assert client.post("/api/session/forget", json={"user_id": "m2"}, headers=ORIGIN).status_code == 204
    assert client.cookies.get(RING) is None


def test_forget_needs_a_trusted_origin(client: TestClient) -> None:
    login(client, "member1", remember=True)
    assert client.post("/api/session/forget", json={"user_id": "m1"}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert usernames(client) == ["member1"]


def test_ring_routes_never_echo_a_token_and_a_switch_keeps_one_session_per_member(client: TestClient, factory) -> None:  # noqa: ANN001
    login(client, "member1", remember=True)
    login(client, "member2", remember=True)
    tokens = ring(client)
    with factory() as db:
        before = db.query(AppSession).count()
    listed = client.get("/api/session/device-members")
    switched = switch(client, "m1")
    for body in (listed.text, switched.text):
        assert not any(token in body or security.session_digest(token) in body for token in tokens)
    with factory() as db:
        assert db.query(AppSession).count() == before  # a switch rotates the member's own session: one new bearer, the old one revoked


def test_listing_the_ring_is_rate_limited(client: TestClient) -> None:
    login(client, "member1", remember=True)
    statuses = [client.get("/api/session/device-members").status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


def test_request_bodies_are_bounded() -> None:
    from app.schemas import DeviceForgetRequest, SessionSwitchRequest
    for model in (SessionSwitchRequest, DeviceForgetRequest):
        with pytest.raises(ValueError):
            model(user_id="")
        with pytest.raises(ValueError):
            model(user_id="x" * 37)
