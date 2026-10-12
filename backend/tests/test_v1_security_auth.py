"""S72 pre-gate: Basic auth shares the login throttle and hides unknown users,
auth never blocks the event loop, the SSE cap is atomic, limiter memory is
bounded, and a new reset link revokes older ones."""
from __future__ import annotations

import asyncio
import itertools
import time

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import main as main_module
from app import security
from app.events import MAX_STREAMS_PER_USER
from app.main import events, events_endpoint
from app.models import User
from app.security import CSRF_HEADER
from app.services.rate_limit import RATE_LIMIT_RULES, InMemoryRateLimiter
from test_v1_account_lifecycle import NEW_PASSWORD, ORIGIN, add_member, fresh_client
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

LOGIN_LIMIT = RATE_LIMIT_RULES["session_login"].max_requests


def test_basic_auth_failures_are_rate_limited(client: TestClient) -> None:
    codes = [client.get("/api/session/me", auth=("local", f"wrong-{i}")).status_code for i in range(LOGIN_LIMIT)]
    assert set(codes) == {401}
    # The correct password is no longer even checked once the bucket is spent.
    assert client.get("/api/session/me", auth=("local", PASSWORD)).status_code == 429
    # Basic failures and form logins share one bucket.
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 429


def test_lan_failures_never_lock_out_a_member(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ceiling = RATE_LIMIT_RULES["session_login_username"].max_requests
    ip = {"value": ""}
    monkeypatch.setattr(security, "resolve_client_key", lambda _request: ip["value"])
    for i in range(ceiling + 2):  # distributed guessing: one failure per IP, off the public address
        ip["value"] = f"203.0.113.{i}"
        assert client.get("/api/session/me", auth=("local", f"wrong-{i}")).status_code == 401
    # The owner's own browser on the LAN still signs in (the public-address lockout lives in test_auth_lockout).
    ip["value"] = "198.51.100.7"
    assert client.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 200
    # Brute force from a single IP is still a hard 429.
    client.cookies.clear()
    ip["value"] = "192.0.2.9"
    codes = [client.get("/api/session/me", auth=("local", f"x-{i}")).status_code for i in range(LOGIN_LIMIT + 1)]
    assert codes == [401] * LOGIN_LIMIT + [429]


def test_unknown_username_costs_a_password_hash(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    verified: list[str | None] = []
    real_verify = security.verify_password
    monkeypatch.setattr(security, "verify_password", lambda pw, h: verified.append(h) or real_verify(pw, h))
    assert client.get("/api/session/me", auth=("ghost", "whatever")).status_code == 401
    assert client.post("/api/session/login", json={"username": "ghost", "password": "whatever"}).status_code == 401
    assert len(verified) == 2  # a full PBKDF2 run even though no such user exists


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/events", "query_string": b"", "headers": []})


def test_event_stream_auth_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    on_loop: list[bool] = []

    def resolve(_request, credentials=None):  # noqa: ANN001
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return User(id="loop-probe", username="p", display_name="P", role="viewer", is_active=True)

    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", resolve)

    async def scenario() -> None:
        response = await events_endpoint(_request(), None)
        await response.background()

    asyncio.run(scenario())
    assert on_loop == [False]


def test_concurrent_event_stream_opens_respect_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(_request, credentials=None):  # noqa: ANN001
        time.sleep(0.01)  # PBKDF2 stand-in: lets the opens interleave
        return User(id="racer", username="r", display_name="R", role="viewer", is_active=True)

    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", resolve)

    async def open_one():  # noqa: ANN202
        try:
            return await events_endpoint(_request(), None)
        except HTTPException as exc:
            return exc.status_code

    async def scenario() -> list:
        return await asyncio.gather(*(open_one() for _ in range(30)))

    results = asyncio.run(scenario())
    try:
        assert sum(1 for r in results if r != 429) == MAX_STREAMS_PER_USER
        assert events.open_streams("racer") == MAX_STREAMS_PER_USER
    finally:
        for response in results:
            if response != 429:
                asyncio.run(response.background())
    assert events.open_streams("racer") == 0


def test_rate_limiter_evicts_stale_buckets() -> None:
    limiter = InMemoryRateLimiter()
    for n in range(500):
        limiter.check("preview", f"2001:db8::{n}", now=0.0)
    horizon = max(rule.window_seconds for rule in RATE_LIMIT_RULES.values())
    limiter.check("preview", "fresh", now=horizon + 1)
    assert list(limiter._events) == [("preview", "fresh")]


def test_new_reset_link_revokes_older_links(client: TestClient, factory) -> None:
    member_id = add_member(factory)
    csrf = login(client)
    headers = {"Origin": ORIGIN, CSRF_HEADER: csrf}
    tokens = [
        client.post(f"/api/admin/users/{member_id}/reset-link", headers=headers).json()["reset_url"].split("#reset=", 1)[1]
        for _ in range(2)
    ]
    anonymous = fresh_client()
    assert anonymous.post("/api/password-reset/redeem", json={"token": tokens[0], "new_password": NEW_PASSWORD}).status_code == 410
    assert anonymous.post("/api/password-reset/redeem", json={"token": tokens[1], "new_password": NEW_PASSWORD}).status_code == 204
