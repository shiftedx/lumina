"""Public-exposure auth hardening: per-visitor keys behind Cloudflare, a temporary per-account lockout on the public
address that LAN sign-in and an owner can lift, PBKDF2 upgrade on sign-in, and audit lines without secrets."""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import security
from app.config import settings
from app.models import User
from app.services import public_address
from app.services.rate_limit import RATE_LIMIT_RULES, resolve_client_key
from test_v1_account_lifecycle import ORIGIN, add_member
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

LOCK_AFTER = RATE_LIMIT_RULES["session_login_username"].max_requests


def _request(peer: str, host: str, headers: dict[str, str]) -> Request:
    raw = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/api/session/login", "query_string": b"", "headers": raw,
                    "client": (peer, 1234), "server": ("127.0.0.1", 8765), "scheme": "http"})


@pytest.fixture
def public_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "172.18.0.0/16")
    public_address.set_origin("https://lumina.example.test")
    yield
    public_address.set_origin(None)


def test_cloudflare_visitor_address_keys_public_requests(public_host) -> None:
    cf = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1"}
    assert resolve_client_key(_request("172.18.0.5", "lumina.example.test", cf)) == "203.0.113.9"
    # The LAN address through the same proxy, an untrusted peer, or a malformed header never use it.
    assert resolve_client_key(_request("172.18.0.5", "192.168.1.10:8765", cf)) == "198.51.100.1"
    assert resolve_client_key(_request("192.168.1.50", "lumina.example.test", cf)) == "192.168.1.50"
    with pytest.raises(HTTPException):
        resolve_client_key(_request("172.18.0.5", "lumina.example.test", {"cf-connecting-ip": "1.2.3.4, 5.6.7.8"}))


def _attack(client: TestClient, monkeypatch: pytest.MonkeyPatch, username: str = "local") -> dict:
    """LOCK_AFTER failures from distinct public visitors (each under the per-IP limit)."""
    state = {"ip": "", "public": True}
    monkeypatch.setattr(security, "resolve_client_key", lambda _r: state["ip"])
    monkeypatch.setattr(security, "arrived_over_https", lambda _scope: state["public"])
    for i in range(LOCK_AFTER):
        state["ip"] = f"203.0.113.{i}"
        assert client.post("/api/session/login", json={"username": username, "password": f"wrong-{i}"}).status_code == 401
    state["ip"] = "198.51.100.7"
    return state


def test_public_account_lockout_then_lan_sign_in_lifts_it(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _attack(client, monkeypatch)
    # Locked on the public address: even the right password from a fresh visitor is refused.
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 429
    assert client.get("/api/session/me", auth=("local", PASSWORD)).status_code == 429
    # Unknown usernames lock the same way (no enumeration through the lockout).
    state = _attack(client, monkeypatch, "ghost")
    assert client.post("/api/session/login", json={"username": "ghost", "password": PASSWORD}).status_code == 429
    # The household LAN is never locked out, and a successful sign-in clears the failures.
    state["public"] = False
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 200
    state["public"], state["ip"] = True, "198.51.100.8"
    client.cookies.clear()
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 200


def test_owner_sees_and_unlocks_a_locked_member(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    member_id = add_member(factory, "kid")
    state = _attack(client, monkeypatch, "kid")
    assert client.post("/api/session/login", json={"username": "kid", "password": PASSWORD}).status_code == 429
    state["public"] = False
    csrf = login(client)
    headers = {"Origin": ORIGIN, security.CSRF_HEADER: csrf}
    rows = {row["id"]: row for row in client.get("/api/admin/users").json()}
    assert rows[member_id]["sign_in_locked"] is True
    assert client.post(f"/api/admin/users/{member_id}/unlock", headers=headers).status_code == 204
    assert {row["id"]: row for row in client.get("/api/admin/users").json()}[member_id]["sign_in_locked"] is False
    state["public"] = True
    other = TestClient(client.app, base_url="http://localhost")
    assert other.post("/api/session/login", json={"username": "kid", "password": PASSWORD}).status_code == 200


def test_sign_in_upgrades_an_older_password_hash(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "PBKDF2_ITERATIONS", 1000)
    old = security.hash_password(PASSWORD)
    monkeypatch.setattr(security, "PBKDF2_ITERATIONS", 2000)
    with factory.begin() as db:
        db.get(User, "u1").password_hash = old
    login(client)
    with factory() as db:
        upgraded = db.get(User, "u1").password_hash
    assert upgraded.split("$")[1] == "2000" and security.verify_password(PASSWORD, upgraded)


def test_production_hash_cost_meets_owasp() -> None:
    assert security.PBKDF2_ITERATIONS >= 600_000


def test_auth_events_are_audited_without_secrets(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="lumina.audit"):
        assert client.post("/api/session/login", json={"username": "local", "password": "Wrong-guess-123"}).status_code == 401
        assert client.post("/api/session/login", json={"username": "Typed-Secret-99", "password": "x"}).status_code == 401
        login(client)
    text = caplog.text
    assert "login.fail user=u1" in text and "login.fail user=unknown" in text and "login.success user=u1" in text
    assert "Wrong-guess-123" not in text and "Typed-Secret-99".lower() not in text.lower() and PASSWORD not in text


def test_login_rejects_oversized_credentials(client: TestClient) -> None:
    assert client.post("/api/session/login", json={"username": "u" * 81, "password": "x"}).status_code == 422
    assert client.post("/api/session/login", json={"username": "local", "password": "p" * 257}).status_code == 422


def test_one_tunnel_visitor_cannot_block_sign_in_for_everyone(public_host, factory, api_client) -> None:
    """Every Cloudflare visitor shares the tunnel host's X-Forwarded-For; the per-IP throttle must still be per visitor."""
    browser = api_client(base_url="https://lumina.example.test", client=("172.18.0.5", 4242))
    tunnel = {"x-forwarded-for": "192.168.1.20", "x-forwarded-proto": "https"}
    attacker = {**tunnel, "cf-connecting-ip": "203.0.113.66"}
    limit = RATE_LIMIT_RULES["session_login"].max_requests
    codes = [browser.post("/api/session/login", headers=attacker, json={"username": f"guess-{i}", "password": "x"}).status_code for i in range(limit + 1)]
    assert codes == [401] * limit + [429]
    member = {**tunnel, "cf-connecting-ip": "198.51.100.23"}
    response = browser.post("/api/session/login", headers=member, json={"username": "local", "password": PASSWORD})
    assert response.status_code == 200 and "secure" in response.headers["set-cookie"].lower()
