"""2.9.0 two-step verification: RFC 6238 TOTP, enrollment, the two-step sign-in, recovery codes, throttling, challenges,
trusted devices, owner policy and reset, and app passwords for Jellyfin apps and HTTP Basic."""
from __future__ import annotations

import base64
import hashlib
import logging
import time

import hmac
import os
import secrets
import threading

import pytest
from Cryptodome.Cipher import AES
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app import main, security
from app.models import DeviceToken, User, UserSettings
from app.security import CSRF_HEADER
from app.services import art_urls, qr, two_factor
from app.services.requests import notify
from app.services.rate_limit import RATE_LIMIT_RULES
from support import seed_app_settings
from test_v1_account_lifecycle import ORIGIN, add_member, fresh_client
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

MB = {"Authorization": 'MediaBrowser Client="Infuse-Direct", Device="Apple%20TV", DeviceId="infuse-1", Version="8.1.4"'}


def _fresh() -> TestClient:
    browser = fresh_client()
    browser.headers["Origin"] = ORIGIN  # a browser sends it; cookie-bearing posts are refused without one
    return browser


@pytest.fixture(autouse=True)
def _origin(client: TestClient) -> None:  # noqa: F811
    client.headers["Origin"] = ORIGIN


def _now_code(secret: bytes, offset: int = 0) -> str:
    return two_factor.totp(secret, int(time.time()) // 30 + offset)


def _headers(csrf: str) -> dict[str, str]:
    return {"Origin": ORIGIN, CSRF_HEADER: csrf}


def _enroll(client: TestClient, csrf: str) -> tuple[bytes, list[str]]:
    setup = client.post("/api/me/two-factor/setup", json={"password": PASSWORD}, headers=_headers(csrf))
    assert setup.status_code == 200, setup.text
    secret_b32 = setup.json()["secret"]
    secret = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    enabled = client.post("/api/me/two-factor/enable", json={"code": _now_code(secret, -1)}, headers=_headers(csrf))
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["recovery_codes"]


def _challenge(client: TestClient, username: str = "local", password: str = PASSWORD) -> str:
    response = client.post("/api/session/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["two_factor_required"] is True and "user" not in body
    return body["challenge"]


@pytest.fixture
def enrolled(client: TestClient, factory):  # noqa: F811
    with factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    secret, codes = _enroll(client, login(client))
    client.cookies.clear()
    return secret, codes


def test_rfc6238_sha1_vectors() -> None:
    secret = b"12345678901234567890"
    for timestamp, expected in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                                (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")):
        assert two_factor.totp(secret, timestamp // 30, digits=8) == expected
    step = 1111111109 // 30
    code = two_factor.totp(secret, step)
    assert two_factor.matching_step(secret, code, last_step=0, now=1111111109 + 30) == step  # one step of drift
    assert two_factor.matching_step(secret, code, last_step=0, now=1111111109 + 90) is None
    assert two_factor.matching_step(secret, code, last_step=step, now=1111111109) is None  # already used


def test_secret_is_sealed_and_qr_is_stable() -> None:
    sealed = two_factor.seal(b"x" * 20, "u1")
    assert sealed.startswith("v2:") and two_factor.unseal(sealed, "u1") == b"x" * 20
    assert two_factor.unseal(sealed[:-4] + "AAAA", "u1") is None  # tampered
    assert two_factor.unseal(sealed, "u2") is None  # bound to its account: copied onto another row it does not open
    assert two_factor.key_path().stat().st_mode & 0o777 == 0o600
    # Decoded by macOS CoreImage when written (versions 1-19); pinned so the encoder cannot drift silently.
    size, path = qr.svg_path("otpauth://totp/Lumina:dana?secret=JBSWY3DPEHPK3PXP&issuer=Lumina")
    assert size == 37 + 8
    assert hashlib.sha256(path.encode()).hexdigest() == PINNED_QR


PINNED_QR = "d41e45ecff8b0c4b18534c92fcb8be6a521ce7e37ad0c005438e980e0c9efe4f"


def test_enrollment_stores_only_ciphertext_and_digests(client: TestClient, factory) -> None:  # noqa: F811
    csrf = login(client)
    assert client.post("/api/me/two-factor/setup", json={"password": "wrong"}, headers=_headers(csrf)).status_code == 403
    setup = client.post("/api/me/two-factor/setup", json={"password": PASSWORD}, headers=_headers(csrf)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Lumina:local?secret=" + setup["secret"])
    assert setup["qr_size"] > 21 and setup["qr_path"].startswith("M")
    assert client.post("/api/me/two-factor/enable", json={"code": "000000"}, headers=_headers(csrf)).status_code == 400
    assert client.get("/api/me/two-factor").json()["enabled"] is False
    secret = base64.b32decode(setup["secret"] + "=" * (-len(setup["secret"]) % 8))
    codes = client.post("/api/me/two-factor/enable", json={"code": _now_code(secret)}, headers=_headers(csrf)).json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert client.get("/api/me/two-factor").json() == {"enabled": True, "recovery_codes_left": 10, "required": False}
    assert client.get("/api/session/me").json()["user"]["two_factor_enabled"] is True
    with factory() as db:
        user = db.get(User, "u1")
        assert user.totp_secret.startswith("v2:") and setup["secret"] not in user.totp_secret
        assert not any(code in repr(user.totp_recovery) for code in codes)
    # Already on: a second setup is refused (it would replace the live secret).
    assert client.post("/api/me/two-factor/setup", json={"password": PASSWORD}, headers=_headers(csrf)).status_code == 409


def test_sign_in_needs_the_code_and_refuses_a_replay(client: TestClient, enrolled) -> None:
    secret, _ = enrolled
    challenge = _challenge(client)
    assert client.get("/api/session/me").status_code == 401  # the password alone opened no session
    assert client.post("/api/session/two-factor", json={"challenge": challenge, "code": "123456"}).status_code in (401,)
    code = _now_code(secret, 1)
    ok = client.post("/api/session/two-factor", json={"challenge": challenge, "code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["username"] == "local" and ok.json()["csrf_token"]
    assert client.get("/api/session/me").status_code == 200
    # Single use, and the same code on a new sign-in is a replay.
    assert client.post("/api/session/two-factor", json={"challenge": challenge, "code": code}).status_code == 410
    other = _fresh()
    assert other.post("/api/session/two-factor", json={"challenge": _challenge(other), "code": code}).status_code == 401


def test_recovery_codes_are_single_use(client: TestClient, enrolled) -> None:
    _, codes = enrolled
    ok = client.post("/api/session/two-factor", json={"challenge": _challenge(client), "recovery_code": codes[0].upper()})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/me/two-factor").json()["recovery_codes_left"] == 9
    again = _fresh()
    assert again.post("/api/session/two-factor", json={"challenge": _challenge(again), "recovery_code": codes[0]}).status_code == 401


def test_challenge_is_bound_to_its_client_and_expires(client: TestClient, enrolled, monkeypatch: pytest.MonkeyPatch) -> None:
    secret, _ = enrolled
    challenge = _challenge(client)
    monkeypatch.setattr(main, "resolve_client_key", lambda _request: "198.51.100.99")
    assert client.post("/api/session/two-factor", json={"challenge": challenge, "code": _now_code(secret)}).status_code == 410
    monkeypatch.undo()
    monkeypatch.setattr(two_factor, "CHALLENGE_SECONDS", 0)
    expired = _challenge(client)
    assert client.post("/api/session/two-factor", json={"challenge": expired, "code": _now_code(secret)}).status_code == 410
    monkeypatch.undo()
    # A few wrong codes and the challenge is spent: back to the password.
    from app.services.rate_limit import rate_limiter

    rate_limiter.clear()  # each challenge and miss above cost a sign-in attempt from this address
    tired = _challenge(client)
    for _ in range(two_factor.CHALLENGE_ATTEMPTS):
        assert client.post("/api/session/two-factor", json={"challenge": tired, "code": "000000"}).status_code == 401
    assert client.post("/api/session/two-factor", json={"challenge": tired, "code": _now_code(secret)}).status_code == 410


def test_wrong_codes_count_toward_the_ip_block_and_the_account_lock(client: TestClient, enrolled, monkeypatch: pytest.MonkeyPatch) -> None:
    secret, _ = enrolled
    for _ in range(2):  # 10 failures over two challenges hit the per-IP limit
        challenge = _challenge(client)
        for _ in range(two_factor.CHALLENGE_ATTEMPTS):
            client.post("/api/session/two-factor", json={"challenge": challenge, "code": "000000"})
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 429

    # On the public address the account itself locks, whatever addresses the guesses come from; the password alone
    # never clears it.
    from app.services.rate_limit import rate_limiter

    rate_limiter.clear()
    state = {"ip": "203.0.113.1"}
    monkeypatch.setattr(security, "resolve_client_key", lambda _r: state["ip"])
    monkeypatch.setattr(main, "resolve_client_key", lambda _r: state["ip"])
    monkeypatch.setattr(security, "arrived_over_https", lambda _scope: True)
    for i in range(RATE_LIMIT_RULES["session_login_username"].max_requests):
        state["ip"] = f"203.0.113.{i}"
        challenge = _challenge(_fresh())
        assert _fresh().post("/api/session/two-factor", json={"challenge": challenge, "code": "000000"}).status_code == 401
    state["ip"] = "198.51.100.7"
    assert _fresh().post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 429
    del secret


def test_trusted_device_skips_the_code_for_that_member_only(client: TestClient, enrolled) -> None:
    secret, _ = enrolled
    ok = client.post("/api/session/two-factor", json={"challenge": _challenge(client), "code": _now_code(secret), "trust_device": True})
    assert ok.status_code == 200
    assert two_factor.trust_cookie_name() in ok.headers["set-cookie"]
    again = client.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert again.status_code == 200 and again.json()["user"]["username"] == "local"
    stolen = _fresh()
    stolen.cookies.set(two_factor.trust_cookie_name(), client.cookies.get(two_factor.trust_cookie_name()) + "x")
    assert "two_factor_required" in stolen.post("/api/session/login", json={"username": "local", "password": PASSWORD}).json()


def test_disable_needs_password_and_code(client: TestClient, enrolled) -> None:
    secret, codes = enrolled
    csrf = client.post("/api/session/two-factor", json={"challenge": _challenge(client), "recovery_code": codes[1]}).json()["csrf_token"]
    url = "/api/me/two-factor/disable"
    assert client.post(url, json={"password": "wrong", "code": _now_code(secret)}, headers=_headers(csrf)).status_code == 403
    assert client.post(url, json={"password": PASSWORD, "code": "000000"}, headers=_headers(csrf)).status_code == 403
    assert client.post(url, json={"password": PASSWORD, "code": codes[2]}, headers=_headers(csrf)).status_code == 204
    assert _fresh().post("/api/session/login", json={"username": "local", "password": PASSWORD}).json()["user"]
    # Basic credentials never manage two-step settings.
    assert _fresh().post("/api/me/two-factor/setup", json={"password": PASSWORD}, auth=("local", PASSWORD)).status_code == 403


def test_owner_resets_a_member_and_it_is_audited(client: TestClient, factory, caplog: pytest.LogCaptureFixture) -> None:  # noqa: F811
    member_id = add_member(factory, "kid")
    member = _fresh()
    _enroll(member, login(member, "kid"))
    assert "two_factor_required" in _fresh().post("/api/session/login", json={"username": "kid", "password": PASSWORD}).json()
    csrf = login(client)
    rows = {row["username"]: row for row in client.get("/api/admin/users").json()}
    assert rows["kid"]["two_factor_enabled"] is True
    with caplog.at_level(logging.INFO, logger="lumina.audit"):
        assert client.delete(f"/api/admin/users/{member_id}/two-factor", headers=_headers(csrf)).status_code == 204
    assert f"two_factor.reset actor=u1 user={member_id}" in caplog.text
    assert _fresh().post("/api/session/login", json={"username": "kid", "password": PASSWORD}).json()["user"]["username"] == "kid"
    # Members cannot reset anyone.
    member_csrf = login(member, "kid")
    assert member.delete("/api/admin/users/u1/two-factor", headers=_headers(member_csrf)).status_code == 403


def test_require_for_owners(client: TestClient, factory) -> None:  # noqa: F811
    with factory() as db:
        seed_app_settings(db)
        db.add(User(id="u2", username="second", display_name="Second", password_hash=security.hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    csrf = login(client)
    # Not until the acting owner has it on.
    assert client.put("/api/admin/settings", json={"require_owner_two_factor": True}, headers=_headers(csrf)).status_code == 409
    secret, _ = _enroll(client, csrf)
    assert client.put("/api/admin/settings", json={"require_owner_two_factor": True}, headers=_headers(csrf)).json()["require_owner_two_factor"] is True
    assert client.post("/api/me/two-factor/disable", json={"password": PASSWORD, "code": _now_code(secret, 1)}, headers=_headers(csrf)).status_code == 409
    # Another owner without it: owner settings refuse until they turn it on; their own settings still work.
    other = _fresh()
    other_csrf = login(other, "second")
    assert other.get("/api/session/me").json()["user"]["two_factor_setup_required"] is True
    refused = other.get("/api/admin/users")
    assert refused.status_code == 403 and refused.json()["detail"] == "two_factor_setup_required"
    _enroll(other, other_csrf)
    assert other.get("/api/admin/users").status_code == 200
    # Members are never forced.
    add_member(factory, "kid")
    kid = _fresh()
    login(kid, "kid")
    assert kid.get("/api/session/me").json()["user"]["two_factor_setup_required"] is False


def _app_password(client: TestClient, csrf: str, name: str = "Infuse on the Apple TV") -> dict:
    created = client.post("/api/connected-apps/app-passwords", json={"name": name}, headers=_headers(csrf))
    assert created.status_code == 201, created.text
    return created.json()


def test_jellyfin_apps_and_basic_use_an_app_password_on_two_step_accounts(client: TestClient, factory) -> None:  # noqa: F811
    with factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    add_member(factory, "kid")
    secret, _ = _enroll(client, login(client, "kid"))
    client.cookies.clear()
    refused = client.post("/users/authenticatebyname", json={"Username": "kid", "Pw": PASSWORD}, headers=MB)
    assert (refused.status_code, refused.content) == (401, b"")  # Jellyfin apps read an error body as data
    basic = _fresh().get("/api/session/me", auth=("kid", PASSWORD))
    assert basic.status_code == 401 and "app password" in basic.json()["detail"]
    csrf = client.post("/api/session/two-factor", json={"challenge": _challenge(client, "kid"), "code": _now_code(secret, 1)}).json()["csrf_token"]
    created = _app_password(client, csrf)
    assert created["username"] == "kid" and created["app"]["kind"] == "app_password"
    password = created["password"]
    # Not a way past the code on the web form.
    assert "two_factor_required" in _fresh().post("/api/session/login", json={"username": "kid", "password": PASSWORD}).json()
    assert _fresh().post("/api/session/login", json={"username": "kid", "password": password}).status_code == 401
    signed_in = _fresh().post("/users/authenticatebyname", json={"Username": "kid", "Pw": password}, headers=MB)
    assert signed_in.status_code == 200, signed_in.text
    token = signed_in.json()["AccessToken"]
    assert _fresh().get("/users/me", headers={"X-Emby-Token": token}).status_code == 200
    assert _fresh().get("/api/session/me", auth=("kid", password.upper().replace("-", ""))).status_code == 200
    # An app password cannot mint more of itself, and revoking it signs out the app that used it.
    assert fresh_client().post("/api/connected-apps/app-passwords", json={"name": "x"}, auth=("kid", password)).status_code == 403
    assert client.delete(f"/api/connected-apps/{created['app']['id']}", headers=_headers(csrf)).status_code == 204
    assert _fresh().get("/users/me", headers={"X-Emby-Token": token}).status_code == 401
    assert _fresh().get("/api/session/me", auth=("kid", password)).status_code == 401
    with factory() as db:
        assert db.query(DeviceToken).count() == 0


def test_without_two_step_apps_keep_the_account_password(client: TestClient, factory) -> None:  # noqa: F811
    with factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    add_member(factory, "kid")
    assert client.post("/users/authenticatebyname", json={"Username": "kid", "Pw": PASSWORD}, headers=MB).status_code == 200
    assert fresh_client().get("/api/session/me", auth=("kid", PASSWORD)).status_code == 200


def test_turning_it_on_keeps_signed_in_apps_until_sign_out_all(client: TestClient, factory) -> None:  # noqa: F811
    with factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    add_member(factory, "kid")
    token = client.post("/users/authenticatebyname", json={"Username": "kid", "Pw": PASSWORD}, headers=MB).json()["AccessToken"]
    csrf = login(client, "kid")
    _enroll(client, csrf)
    assert _fresh().get("/users/me", headers={"X-Emby-Token": token}).status_code == 200
    created = _app_password(client, csrf)
    assert client.post("/api/connected-apps/sign-out-all", headers=_headers(csrf)).json() == {"revoked": 1}
    assert _fresh().get("/users/me", headers={"X-Emby-Token": token}).status_code == 401
    kinds = [row["kind"] for row in client.get("/api/connected-apps").json()]
    assert kinds == ["app_password"]
    assert _fresh().post("/users/authenticatebyname", json={"Username": "kid", "Pw": created["password"]}, headers=MB).status_code == 200


def test_migration_14_adds_the_two_step_columns(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from test_schema_version import _init

    engine = _init(tmp_path, monkeypatch)
    columns = {table: {c["name"] for c in inspect(engine).get_columns(table)} for table in ("users", "app_settings", "device_tokens")}
    assert {"totp_secret", "totp_enabled_at", "totp_last_step", "totp_recovery"} <= columns["users"]
    assert "require_owner_two_factor" in columns["app_settings"]
    assert "app_password_id" in columns["device_tokens"]


def _seal_v1(secret: bytes) -> str:
    """How 2.9.0 sealed: a key derived from app-data/art-secret, no user binding."""
    key = hmac.new(art_urls.secret(), b"lumina-totp-secret-v1", hashlib.sha256).digest()
    nonce = secrets.token_bytes(12)
    ciphertext, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(secret)
    return "v1:" + base64.urlsafe_b64encode(nonce + ciphertext + tag).decode()


def test_startup_reseals_2_9_0_secrets_under_the_totp_key(factory, monkeypatch) -> None:  # noqa: ANN001, F811
    kid = add_member(factory, "kid")
    with factory.begin() as db:
        for user_id, fill in (("u1", b"a"), (kid, b"b")):
            db.get(User, user_id).totp_secret = _seal_v1(fill * 20)
    assert not two_factor.key_path().exists()

    # A failure part-way (here, sealing the second row) rolls every row back; the next start retries.
    real_seal, calls = two_factor.seal, []

    def flaky(secret: bytes, user_id: str) -> str:
        calls.append(user_id)
        if len(calls) == 2:
            raise RuntimeError("crash")
        return real_seal(secret, user_id)

    monkeypatch.setattr(two_factor, "seal", flaky)
    with factory() as db:
        two_factor.reseal_legacy(db)
    with factory() as db:
        assert all(u.totp_secret.startswith("v1:") for u in db.query(User).filter(User.totp_secret.is_not(None)))
    monkeypatch.setattr(two_factor, "seal", real_seal)

    with factory() as db:
        two_factor.reseal_legacy(db)
    with factory() as db:
        sealed = {u.id: u.totp_secret for u in db.query(User).filter(User.totp_secret.is_not(None))}
    assert all(value.startswith("v2:") for value in sealed.values())
    assert two_factor.unseal(sealed["u1"], "u1") == b"a" * 20 and two_factor.unseal(sealed[kid], kid) == b"b" * 20
    # Now independent of art-secret, and a second start changes nothing.
    art_urls.secret_path().unlink()
    with factory() as db:
        two_factor.reseal_legacy(db)
        assert {u.id: u.totp_secret for u in db.query(User).filter(User.totp_secret.is_not(None))} == sealed
    assert two_factor.unseal(sealed["u1"], "u1") == b"a" * 20


def test_a_2_9_0_secret_without_its_art_secret_is_left_for_a_later_start(factory, monkeypatch, caplog) -> None:  # noqa: ANN001, F811
    with factory.begin() as db:
        db.get(User, "u1").totp_secret = legacy = _seal_v1(b"a" * 20)
    secret_file = art_urls.secret_path().read_bytes()
    art_urls.secret_path().unlink()
    monkeypatch.setattr(art_urls, "_secret", None)
    with factory() as db, caplog.at_level(logging.ERROR):
        two_factor.reseal_legacy(db)
    assert "could not be opened with app-data/art-secret" in caplog.text
    assert not art_urls.secret_path().exists()  # not minted: a restored art-secret still opens the row
    with factory() as db:
        assert db.get(User, "u1").totp_secret == legacy
    art_urls.secret_path().write_bytes(secret_file)
    with factory() as db:
        two_factor.reseal_legacy(db)
        assert two_factor.unseal(db.get(User, "u1").totp_secret, "u1") == b"a" * 20


def test_the_totp_key_is_never_replaced(factory) -> None:  # noqa: F811
    two_factor.seal(b"a" * 20, "u1")
    key = two_factor.key_path().read_bytes()
    two_factor.seal(b"b" * 20, "u1")
    assert two_factor.key_path().read_bytes() == key
    two_factor.key_path().write_bytes(b"short")
    with pytest.raises(RuntimeError):
        two_factor.seal(b"c" * 20, "u1")
    assert two_factor.key_path().read_bytes() == b"short"
    os.unlink(two_factor.key_path())
    assert two_factor.unseal(two_factor.seal(b"d" * 20, "u1"), "u1") == b"d" * 20


def test_owners_get_an_email_when_a_members_codes_pause(client: TestClient, factory, monkeypatch) -> None:  # noqa: ANN001, F811
    with factory() as db:
        seed_app_settings(db, smtp_host="smtp.test", smtp_from="lumina@test")
    with factory.begin() as db:
        db.add(UserSettings(id="s1", user_id="u1", notify_email="owner@example.com"))
    sent, done = [], threading.Event()
    monkeypatch.setattr(notify, "send", lambda smtp, to, subject, body, html=None: (sent.append((to, subject, body)), done.set()))
    _enroll(client, login(client))
    with factory() as db:
        user = db.get(User, "u1")
        for _ in range(two_factor.TOTP_LOCK_AFTER - 1):
            two_factor.verify(db, user, code="000000")
        assert not sent
        two_factor.verify(db, user, code="000000")
        assert done.wait(5)
        assert sent == [("owner@example.com", "Two-step verification paused for Local", sent[0][2])]
        assert "(local) entered 10 wrong" in sent[0][2] and "000000" not in sent[0][2]
        # Further misses after the pause do not mail again.
        db.get(User, "u1", populate_existing=True).totp_locked_until = None
        db.commit()
        two_factor.verify(db, user, code="000000")
    assert len(sent) == 1
