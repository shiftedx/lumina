"""Connected-app Settings are member-scoped and require fresh proofs for sensitive writes."""
from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import AppSettings, DeviceToken, MemberAccess, User
from app.security import hash_password, session_digest
from app.services import two_factor
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, jellyfin_household, mediabrowser


BASE = "/Lumina/Settings"
PASSWORD = "Correct horse battery staple 7!"


def call(client: TestClient, method: str, path: str = "", *, token: str = ALICE_TOKEN, **kwargs):  # noqa: ANN201
    return client.request(method, BASE + path, headers=mediabrowser(token), **kwargs)


def reauth(client: TestClient, *, token: str = ALICE_TOKEN, **body: str):  # noqa: ANN201
    response = call(client, "POST", "/reauth", token=token, json={"password": PASSWORD, **body})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["proof"]


def sensitive_headers(token: str, proof: str) -> dict[str, str]:
    return {**mediabrowser(token), "X-Lumina-Reauth": proof}


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    from app.services.rate_limit import rate_limiter

    rate_limiter.clear()
    jellyfin_household(tmp_path / "media")
    from app import db as db_module

    with db_module.SessionLocal.begin() as db:
        for user_id in (ALICE, BOB):
            db.get(User, user_id).password_hash = hash_password(PASSWORD)
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()
    rate_limiter.clear()


def test_settings_share_member_preferences_but_never_browser_api_or_other_members(native: TestClient) -> None:
    current = call(native, "GET")
    assert current.status_code == 200
    assert current.json()["user_id"] == ALICE
    assert current.headers["cache-control"] == "no-store"

    first = call(native, "PUT", json={"ui_prefs": {"existing_key": "kept"}})
    assert first.status_code == 200, first.text
    saved = call(native, "PUT", json={"ui_prefs": {"theme": "dark", "autoplay_up_next": False}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["ui_prefs"] == {"existing_key": "kept", "theme": "dark", "autoplay_up_next": False}
    assert call(native, "GET", token=BOB_TOKEN).json()["ui_prefs"] == {}
    assert call(native, "GET", "/profile").json()["id"] == ALICE
    assert native.get("/api/settings/me", headers=mediabrowser(ALICE_TOKEN)).status_code == 401


def set_native_streaming_enabled(enabled: bool) -> None:
    with db_module.SessionLocal.begin() as db:
        record = db.get(AppSettings, 1)
        assert record is not None
        record.native_streaming_enabled = enabled


def test_native_streaming_capability_requires_admin_opt_in_and_member_provider_access(native: TestClient) -> None:
    assert call(native, "GET", "/capabilities").json() == {"streaming_enabled": False, "youtube_session_enabled": False}
    assert call(native, "PUT", json={"native_streaming_enabled": True}).status_code == 400
    assert call(native, "PUT", json={"ui_prefs": {"native_streaming_enabled": True}}).status_code == 200
    assert call(native, "GET", "/capabilities").json()["streaming_enabled"] is False
    for method, kwargs in (("GET", {}), ("PUT", {"json": {"cookies": "x", "consent": True}}), ("DELETE", {})):
        denied = call(native, method, "/youtube-session", **kwargs)
        assert denied.status_code == 403

    set_native_streaming_enabled(True)
    enabled = call(native, "GET", "/capabilities")
    assert enabled.headers["cache-control"] == "no-store"
    assert enabled.json() == {"streaming_enabled": True, "youtube_session_enabled": True}

    with db_module.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=ALICE, streaming={"youtube": False, "twitch": False, "kick": False}))
    assert call(native, "GET", "/capabilities").json() == {"streaming_enabled": False, "youtube_session_enabled": False}
    blocked = call(native, "GET", "/youtube-session")
    assert blocked.status_code == 403

    with db_module.SessionLocal.begin() as db:
        user = db.get(User, ALICE)
        assert user is not None
        user.role = "admin"
    assert call(native, "GET", "/capabilities").json() == {"streaming_enabled": True, "youtube_session_enabled": True}
    set_native_streaming_enabled(False)
    assert call(native, "GET", "/capabilities").json() == {"streaming_enabled": False, "youtube_session_enabled": False}


def test_native_streaming_can_remain_available_when_youtube_is_member_blocked(native: TestClient) -> None:
    set_native_streaming_enabled(True)
    with db_module.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=ALICE, streaming={"youtube": False, "twitch": True, "kick": False}))
    assert call(native, "GET", "/capabilities").json() == {"streaming_enabled": True, "youtube_session_enabled": False}


def test_sensitive_writes_reject_bearer_only_expired_wrong_token_and_replayed_proofs(native: TestClient) -> None:
    bearer_only = call(native, "POST", "/connected-apps", json={"name": "Denied", "scope": "read"})
    assert bearer_only.status_code == 401

    alice_proof = reauth(native)
    wrong_token = native.post(
        BASE + "/connected-apps", headers=sensitive_headers(BOB_TOKEN, alice_proof), json={"name": "Bob", "scope": "read"},
    )
    assert wrong_token.status_code == 401
    assert native.post(
        BASE + "/connected-apps", headers=sensitive_headers(ALICE_TOKEN, alice_proof), json={"name": "Replay", "scope": "read"},
    ).status_code == 401

    expired = reauth(native)
    from app.routers import jellyfin_settings

    with jellyfin_settings._reauth_proofs._lock:
        digest = session_digest(expired)
        user_id, token_id, _expires = jellyfin_settings._reauth_proofs._items[digest]
        jellyfin_settings._reauth_proofs._items[digest] = (user_id, token_id, time.monotonic() - 1)
    assert native.post(
        BASE + "/connected-apps", headers=sensitive_headers(ALICE_TOKEN, expired), json={"name": "Expired", "scope": "read"},
    ).status_code == 401

    proof = reauth(native)
    created = native.post(
        BASE + "/connected-apps", headers=sensitive_headers(ALICE_TOKEN, proof), json={"name": "Native test", "scope": "read"},
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    assert created.json()["app"]["user_id"] == ALICE


def test_connected_app_list_and_revoke_stay_member_scoped_even_for_owner(native: TestClient) -> None:
    from app import db as db_module

    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"
        bob_token_id = db.query(DeviceToken.id).filter(DeviceToken.user_id == BOB).one()[0]

    listed = call(native, "GET", "/connected-apps")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert {entry["user_id"] for entry in listed.json()} == {ALICE}

    proof = reauth(native)
    rejected = native.delete(BASE + f"/connected-apps/{bob_token_id}", headers=sensitive_headers(ALICE_TOKEN, proof))
    assert rejected.status_code == 404
    assert call(native, "GET", "/connected-apps", token=BOB_TOKEN).status_code == 200


def test_two_factor_requires_fresh_second_factor_and_spends_recovery_code_once(native: TestClient) -> None:
    setup = native.post(BASE + "/two-factor/setup", headers=sensitive_headers(ALICE_TOKEN, reauth(native)))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    raw_secret = base64.b32decode(secret + "=" * (-len(secret) % 8))
    code = two_factor.totp(raw_secret, int(time.time()) // two_factor.STEP_SECONDS)

    enabled = native.post(
        BASE + "/two-factor/enable", headers=sensitive_headers(ALICE_TOKEN, reauth(native)), json={"code": code},
    )
    assert enabled.status_code == 200, enabled.text
    recovery = enabled.json()["recovery_codes"][0]

    assert call(native, "POST", "/reauth", json={"password": PASSWORD}).status_code == 403
    proof = reauth(native, recovery_code=recovery)
    assert native.post(
        BASE + "/connected-apps", headers=sensitive_headers(ALICE_TOKEN, proof), json={"name": "With 2FA", "scope": "read"},
    ).status_code == 201
    assert call(native, "POST", "/reauth", json={"password": PASSWORD, "recovery_code": recovery}).status_code == 403


def test_password_change_requires_proof_and_revokes_the_calling_connected_app(native: TestClient) -> None:
    assert call(native, "POST", "/password", json={"new_password": "Another secure passphrase 8!"}).status_code == 401
    changed = native.post(
        BASE + "/password", headers=sensitive_headers(ALICE_TOKEN, reauth(native)), json={"new_password": "Another secure passphrase 8!"},
    )
    assert changed.status_code == 204, changed.text
    assert call(native, "GET").status_code == 401
