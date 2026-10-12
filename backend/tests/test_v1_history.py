"""Server-owned search history replaces browser-global localStorage.

Exercises the route wiring end to end (real cookie login + CSRF, like
test_v1_csrf.py): history never leaks between Household accounts sharing a
browser, mutations require the session-bound CSRF token, list/add/delete-one/
clear all round-trip, and a bounded/deduped write never surfaces a raw 500.
"""
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
        session.add_all([make_user(name, password_hash=hash_password(PASSWORD)) for name in ("alice", "bob")])
    return api_client(base_url="http://localhost")


def _login(client: TestClient, username: str) -> str:
    response = client.post("/api/session/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _mutation_headers(token: str) -> dict[str, str]:
    return {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: token}


def test_alice_bob_search_isolation(client: TestClient) -> None:
    token = _login(client, "alice")
    assert client.post("/api/search/history", json={"query": "alice's secret playlist"}, headers=_mutation_headers(token)).status_code == 200
    assert [e["query"] for e in client.get("/api/search/history").json()] == ["alice's secret playlist"]

    # Logout, then Bob signs in on the same browser/client (shared cookie jar).
    client.post("/api/session/logout", headers=_mutation_headers(token))
    bob_token = _login(client, "bob")

    assert client.get("/api/search/history").json() == []
    assert client.post("/api/search/history", json={"query": "bob's search"}, headers=_mutation_headers(bob_token)).status_code == 200
    assert [e["query"] for e in client.get("/api/search/history").json()] == ["bob's search"]

    # Alice signs back in (simulated browser reload / re-login): still only her own history.
    client.post("/api/session/logout", headers=_mutation_headers(bob_token))
    _login(client, "alice")
    assert [e["query"] for e in client.get("/api/search/history").json()] == ["alice's secret playlist"]


def test_search_history_mutations_require_csrf(client: TestClient) -> None:
    _login(client, "alice")
    origin = settings.allowed_origins_list[0]
    assert client.post("/api/search/history", json={"query": "x"}).status_code == 403
    assert client.post("/api/search/history", json={"query": "x"}, headers={"Origin": origin}).status_code == 403
    assert client.delete("/api/search/history").status_code == 403


def test_list_add_delete_one_clear_round_trip(client: TestClient) -> None:
    token = _login(client, "alice")
    headers = _mutation_headers(token)
    first = client.post("/api/search/history", json={"query": "space documentaries"}, headers=headers).json()
    client.post("/api/search/history", json={"query": "sea shanties"}, headers=headers)

    listing = client.get("/api/search/history").json()
    assert [e["query"] for e in listing] == ["sea shanties", "space documentaries"]

    assert client.delete(f"/api/search/history/{first['id']}", headers=headers).status_code == 204
    assert [e["query"] for e in client.get("/api/search/history").json()] == ["sea shanties"]

    cleared = client.delete("/api/search/history", headers=headers)
    assert cleared.status_code == 200 and cleared.json() == {"deleted": 1}
    assert client.get("/api/search/history").json() == []


def test_record_search_history_never_500s_on_blank_or_oversized_query(client: TestClient) -> None:
    token = _login(client, "alice")
    headers = _mutation_headers(token)
    # Whitespace-only passes the request-boundary min_length check but is a
    # no-op write: the service skips it rather than storing an empty entry.
    blank = client.post("/api/search/history", json={"query": "   "}, headers=headers)
    assert blank.status_code == 200 and blank.json() is None
    assert client.post("/api/search/history", json={"query": "x" * 501}, headers=headers).status_code == 422
    assert client.get("/api/search/history").json() == []
