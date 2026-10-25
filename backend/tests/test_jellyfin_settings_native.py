"""Connected-app Settings are member-scoped and require fresh proofs for sensitive writes."""
from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.main import app
from app.models import AccountToken, AppSettings, DeviceToken, MemberAccess, User
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


def test_navigation_tracks_available_libraries_and_member_visibility(native: TestClient, tmp_path: Path) -> None:
    from app.book_models import BookItem
    from app.models import LibraryItem, MediaTitle
    from title_support import add_file, seed_gallery, uid

    initial = call(native, "GET", "/navigation")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json() == {"categories": ["movies", "tv"]}
    with db_module.SessionLocal.begin() as db:
        seed_gallery(db, tmp_path / "media")
        db.add(BookItem(id="native-nav-book", storage_key="native-nav-book.epub", format="epub", kind="ebook",
                        title="Synthetic book", owner_user_id=ALICE, visibility="private"))
    assert call(native, "GET", "/navigation").json()["categories"] == ["movies", "tv", "anime", "music", "books"]
    assert call(native, "GET", "/navigation", token=BOB_TOKEN).json()["categories"] == ["movies", "tv", "anime", "music"]
    with db_module.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=BOB, sections=["shows"]))
        db.get(BookItem, "native-nav-book").available = False
        # A removed library's remaining metadata must not keep its menu alive.
        album_ids = select(MediaTitle.id).where(MediaTitle.type == "album")
        for track in db.scalars(select(LibraryItem).where(LibraryItem.title_id.in_(album_ids))):
            track.status = "missing"
    assert call(native, "GET", "/navigation", token=BOB_TOKEN).json()["categories"] == ["tv"]
    assert call(native, "GET", "/navigation").json()["categories"] == ["movies", "tv", "anime"]
    with db_module.SessionLocal.begin() as db:
        add_file(db, tmp_path / "media", "Music/Standalone.flac", item_id=uid(990), title_id=None,
                 owner=ALICE, title="Standalone track", kind="audio")
    assert "music" in call(native, "GET", "/navigation").json()["categories"]


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


def test_native_admin_quick_actions_use_live_admin_and_safe_library_contract(native: TestClient) -> None:
    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"

    browser_surface = native.get("/api/admin/invites", headers=mediabrowser(ALICE_TOKEN))
    assert browser_surface.status_code == 401

    snapshot = call(native, "GET", "/admin/library")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.headers["cache-control"] == "no-store"
    assert set(snapshot.json()) == {"server_timezone", "night_hour", "poller", "roots"}
    assert '"path"' not in snapshot.text

    scan = call(native, "POST", "/admin/library/scan", json={})
    assert scan.status_code == 202, scan.text
    assert scan.headers["cache-control"] == "no-store"
    assert set(scan.json()) == {"started", "queued", "skipped"}
    assert '"path"' not in scan.text

    unknown = call(native, "POST", "/admin/library/scan", json={"root_id": "not-a-storage-root"})
    assert unknown.status_code == 404

    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "viewer"
    assert call(native, "GET", "/admin/library").status_code == 403
    assert call(native, "POST", "/admin/library/scan", json={}).status_code == 403

    with db_module.SessionLocal.begin() as db:
        user = db.get(User, ALICE)
        assert user is not None
        user.role, user.is_active = "admin", False
    assert call(native, "GET", "/admin/library").status_code == 401


def test_native_admin_invites_reuse_email_contract_and_require_one_use_proofs(native: TestClient) -> None:
    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"

    body = {"email": "Native-Pal@Example.test", "display_name": "Native Pal", "send": False}
    missing_proof = call(native, "POST", "/admin/invites", json=body)
    assert missing_proof.status_code == 401

    proof = reauth(native)
    created = native.post(BASE + "/admin/invites", headers=sensitive_headers(ALICE_TOKEN, proof), json=body)
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    created_body = created.json()
    assert created_body["email"] == "native-pal@example.test"
    assert created_body["email_sent"] is False
    assert "#invite=" in created_body["invitation_url"]
    with db_module.SessionLocal.begin() as db:
        record = db.get(AccountToken, created_body["id"])
        assert record is not None and record.role == "viewer"

    listed = call(native, "GET", "/admin/invites")
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "no-store"
    listed_body = listed.json()
    assert listed_body[0]["id"] == created_body["id"]
    assert listed_body[0]["email"] == "native-pal@example.test"
    assert "invitation_url" not in listed_body[0]

    assert native.post(BASE + "/admin/invites", headers=sensitive_headers(ALICE_TOKEN, proof), json=body).status_code == 401
    assert native.delete(BASE + f"/admin/invites/{created_body['id']}", headers=mediabrowser(ALICE_TOKEN)).status_code == 401

    revoked = native.delete(
        BASE + f"/admin/invites/{created_body['id']}",
        headers=sensitive_headers(ALICE_TOKEN, reauth(native)),
    )
    assert revoked.status_code == 204
    assert revoked.headers["cache-control"] == "no-store"
    assert call(native, "GET", "/admin/invites").json()[0]["status"] == "revoked"


def test_native_admin_invite_proof_is_token_bound_and_mutations_need_write_scope(native: TestClient) -> None:
    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"
        db.get(User, BOB).role = "admin"

    body = {"email": "bound@example.test", "send": False}
    proof = reauth(native)
    wrong_token = native.post(BASE + "/admin/invites", headers=sensitive_headers(BOB_TOKEN, proof), json=body)
    assert wrong_token.status_code == 401

    with db_module.SessionLocal.begin() as db:
        token = db.query(DeviceToken).filter(DeviceToken.user_id == ALICE).one()
        token.scope = "read"
    assert call(native, "POST", "/admin/library/scan", json={}).status_code == 403
    assert call(native, "POST", "/admin/invites", json=body).status_code == 403

    with db_module.SessionLocal.begin() as db:
        token = db.query(DeviceToken).filter(DeviceToken.user_id == ALICE).one()
        token.scope = "write"


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
