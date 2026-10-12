"""Every admin API refuses members, whatever URL they craft."""
from __future__ import annotations

import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import User
from app.security import CSRF_HEADER, hash_password
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)


def admin_routes() -> list[tuple[str, str]]:
    routes = [
        (method, re.sub(r"\{[^}]+\}", "x", route.path))
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/admin")
        for method in route.methods
    ]
    assert len(routes) > 5  # the walk really found the admin surface
    return routes


def test_admin_direct_route_guard(client: TestClient, factory) -> None:
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    csrf = login(client, "member")
    headers = {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf}
    for method, path in admin_routes():
        response = client.request(method, path, headers=headers, json={})
        assert response.status_code == 403, (method, path, response.status_code)
        assert set(response.json()) == {"detail"}, (method, path)


def test_admin_local_session_only(client: TestClient, factory) -> None:
    # A plain local sign-in is all an owner needs; no provider connection is involved.
    login(client)
    assert client.get("/api/admin/users").status_code == 200
    anonymous = TestClient(app, base_url="http://localhost")
    assert anonymous.get("/api/admin/users").status_code == 401
