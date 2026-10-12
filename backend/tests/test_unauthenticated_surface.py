"""Every API route a visitor without a session can reach is listed here on purpose (public exposure audit).

A new route that answers anything but 401/403 without credentials fails this test until it is reviewed and added.
Jellyfin's API is pinned by its own tests.
"""
from __future__ import annotations

import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

ANONYMOUS = {
    ("GET", "/api/health"),  # {"status": "ok"} only
    ("GET", "/api/bootstrap/status"),  # needs_setup only
    ("POST", "/api/bootstrap/admin"),  # first-run only, rate limited
    ("POST", "/api/session/login"),
    ("POST", "/api/session/logout"),
    ("GET", "/api/session/device-members"),  # this browser's opted-in ring only
    ("POST", "/api/session/two-factor"),  # a password-verified, client-bound challenge plus its code
    ("POST", "/api/session/switch"),
    ("POST", "/api/session/forget"),
    ("POST", "/api/password-reset/redeem"),
    ("POST", "/api/invitations/redeem"),
    ("GET", "/api/art/{sig}/{title_id}/{image_type}/{file}"),  # HMAC-signed image URLs
    # Sign-in showcase: public catalog releases only (never library, requests or members), rate limited; the image
    # route serves only URLs the showcase itself signed (test_showcase pins both).
    ("GET", "/api/public/showcase"),
    ("GET", "/api/public/showcase/art/{token}"),
    # Authenticate inside the handler (after body validation), so an empty body answers 422 first.
    ("POST", "/api/preview"),
    ("POST", "/api/youtube-search"),
    ("POST", "/api/source-search"),
}


def test_only_reviewed_routes_answer_without_a_session() -> None:
    reachable = set()
    with TestClient(app, base_url="http://localhost") as client:
        for route in app.routes:
            if not isinstance(route, APIRoute) or route.path.startswith("/jellyfin"):
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            for method in route.methods - {"HEAD"}:
                body = {} if method in {"POST", "PUT", "PATCH"} else None
                if client.request(method, path, json=body).status_code not in {401, 403}:
                    reachable.add((method, route.path))
    assert reachable == ANONYMOUS


def test_in_handler_auth_routes_refuse_a_valid_body_without_a_session() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/preview", json={"source_url": "https://example.com/v"}).status_code == 401
        assert client.post("/api/youtube-search", json={"query": "x"}).status_code == 401
        assert client.post("/api/source-search", json={"query": "x"}).status_code == 401


def test_api_docs_are_off_by_default() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/openapi.json").status_code == 404
        assert "swagger" not in client.get("/docs").text.lower()
