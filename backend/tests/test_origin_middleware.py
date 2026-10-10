"""The origin check is pure ASGI in the same place with the same rule, and every launcher keeps connections alive 30 s."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.http_boundaries import SECURITY_HEADERS, OriginCheckMiddleware
from app.main import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(api_client):  # noqa: ANN001, ANN201
    return api_client(base_url="http://localhost")


def test_no_middleware_is_base_http_and_the_order_is_unchanged() -> None:
    assert BaseHTTPMiddleware not in {middleware.cls for middleware in app.user_middleware}
    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "SecurityHeadersMiddleware", "FailedRequestLogMiddleware", "OriginCheckMiddleware", "JellyfinPathMiddleware",
        "ResponseCompressionMiddleware", "RequestBodyLimitMiddleware", "CORSMiddleware", "SecureCookieMiddleware",
        "DynamicTrustedHostMiddleware",
    ]


def test_an_untrusted_origin_is_refused_with_the_security_headers(client) -> None:  # noqa: ANN001
    response = client.post("/api/session/login", json={"username": "x", "password": "y"}, headers={"Origin": "https://attacker.invalid"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Untrusted request origin: https://attacker.invalid"}
    assert response.headers["X-Frame-Options"] == SECURITY_HEADERS["X-Frame-Options"]


def test_a_cookie_mutation_without_origin_is_refused_and_safe_methods_pass(client) -> None:  # noqa: ANN001
    client.cookies.set(settings.session_cookie_name, "anything")
    refused = client.post("/api/session/logout")
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Missing Origin or Referer header for a state-changing session request."}
    assert client.get("/api/health").status_code == 200
    preflight = client.options("/api/session/login", headers={"Origin": "https://attacker.invalid", "Access-Control-Request-Method": "POST"})
    assert preflight.status_code != 403  # OPTIONS is not state-changing: CORS answers it


def test_a_trusted_origin_reaches_the_route(client) -> None:  # noqa: ANN001
    response = client.post("/api/session/login", json={"username": "nobody", "password": "wrong-password-1"}, headers={"Origin": settings.allowed_origins_list[0]})
    assert response.status_code == 401


def test_non_http_scopes_pass_through_untouched() -> None:
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:  # noqa: ANN001
        seen.append(scope["type"])

    asyncio.run(OriginCheckMiddleware(inner)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


def test_every_launcher_keeps_connections_alive_30_seconds() -> None:
    for relative in ("docker/entrypoint.sh", "scripts/run-backend.sh"):
        assert "--timeout-keep-alive 30" in (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
    for relative in ("scripts/run_realstack_fixtures.py", "scripts/perf/serve.py"):
        assert "timeout_keep_alive=30" in (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
