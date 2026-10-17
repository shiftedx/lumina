from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.services.rate_limit import enforce_rate_limit, rate_limit_dependency, rate_limiter, resolve_client_key


def test_rate_limiter_blocks_after_limit_and_recovers() -> None:
    rate_limiter.clear()
    bucket = "preview"
    client_key = "127.0.0.1"

    for current in range(20):
        rate_limiter.check(bucket, client_key, now=float(current))

    try:
        rate_limiter.check(bucket, client_key, now=20.0)
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "status_code", None) == 429
        assert exc.headers["Retry-After"] == "40"
    else:  # pragma: no cover
        raise AssertionError("Expected the request to be rate limited")

    rate_limiter.check(bucket, client_key, now=61.0)


def test_resolve_client_key_ignores_forwarded_for_without_trusted_proxy() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 127.0.0.1")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8765),
            "scheme": "http",
        }
    )
    assert resolve_client_key(request) == "127.0.0.1"


def test_rate_limit_dependency_returns_429_with_retry_after() -> None:
    rate_limiter.clear()
    app = FastAPI()

    @app.post("/limited", dependencies=[Depends(rate_limit_dependency("webhook_test"))])
    def limited():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(5):
        response = client.post("/limited")
        assert response.status_code == 200

    blocked = client.post("/limited")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert blocked.json()["detail"].startswith("Too many requests")


def test_enforce_rate_limit_tracks_recent_events_and_user_bucket() -> None:
    rate_limiter.clear()
    app = FastAPI()

    @app.post("/limited")
    def limited(request: Request):
        enforce_rate_limit("webhook_test", request, user_id="user-1")
        return {"ok": True}

    client = TestClient(app)
    for _ in range(5):
        response = client.post("/limited")
        assert response.status_code == 200

    blocked = client.post("/limited")
    assert blocked.status_code == 429
    events = rate_limiter.recent_events()
    assert events
    assert events[0].bucket == "webhook_test"
    assert events[0].user_id == "user-1"


def test_authenticated_members_on_one_address_have_independent_budgets() -> None:
    rate_limiter.clear()
    request = Request({"type": "http", "client": ("203.0.113.7", 1234), "headers": []})
    for _ in range(5):
        enforce_rate_limit("webhook_test", request, user_id="member-a")
    enforce_rate_limit("webhook_test", request, user_id="member-b")
    import pytest
    from fastapi import HTTPException
    # The same member on another device/address still uses the original budget.
    other_device = Request({"type": "http", "client": ("198.51.100.8", 5678), "headers": []})
    with pytest.raises(HTTPException) as blocked:
        enforce_rate_limit("webhook_test", other_device, user_id="member-a")
    assert blocked.value.status_code == 429
