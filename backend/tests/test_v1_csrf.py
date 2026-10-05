"""Cookie-session mutations need trusted Origin + session-bound CSRF
token; auth-header presence never exempts them; valid Basic keeps the
originless script exception but never an untrusted Origin."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.security import CSRF_HEADER, hash_password
from support import make_user

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def client(db_factory, api_client):
    with db_factory.begin() as session:
        session.add(make_user("u1", username="local", display_name="Local", password_hash=hash_password(PASSWORD), role="admin"))
    return api_client(base_url="http://localhost")


def _login(client: TestClient) -> str:
    response = client.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    assert token and client.get("/api/session/me").json()["csrf_token"] == token
    return token


def test_bogus_bearer_cookie_requires_csrf(client: TestClient) -> None:
    token = _login(client)
    origin = settings.allowed_origins_list[0]
    body = {"display_name": "Changed"}

    # Valid session + bogus bearer + no Origin: header presence exempts nothing.
    assert client.put("/api/session/me", json=body, headers={"Authorization": "Bearer bogus"}).status_code == 403
    # Trusted Origin but no / wrong CSRF token, even with a bogus bearer.
    assert client.put("/api/session/me", json=body, headers={"Origin": origin, "Authorization": "Bearer bogus"}).status_code == 403
    assert client.put("/api/session/me", json=body, headers={"Origin": origin, CSRF_HEADER: "0" * 64}).status_code == 403
    assert client.get("/api/session/me").json()["user"]["display_name"] == "Local"

    ok = client.put("/api/session/me", json=body, headers={"Origin": origin, CSRF_HEADER: token, "Authorization": "Bearer bogus"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["display_name"] == "Changed"


def test_untrusted_origin_valid_basic_rejected(client: TestClient) -> None:
    auth = ("local", PASSWORD)
    body = {"display_name": "Script"}
    # Validated non-cookie credential: the originless local-script exception holds.
    assert client.put("/api/session/me", json=body, auth=auth).status_code == 200
    # ...but never with an untrusted Origin.
    assert client.put("/api/session/me", json=body, auth=auth, headers={"Origin": "https://attacker.invalid"}).status_code == 403


def test_csrf_token_is_session_bound(client: TestClient) -> None:
    first = _login(client)
    client.cookies.clear()
    second = _login(client)
    assert first != second
    origin = settings.allowed_origins_list[0]
    stale = client.put("/api/session/me", json={"bio": "x"}, headers={"Origin": origin, CSRF_HEADER: first})
    assert stale.status_code == 403
    assert stale.json()["detail"] == "CSRF token missing or invalid."
