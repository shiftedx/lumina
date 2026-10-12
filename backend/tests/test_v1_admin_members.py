"""Admins list and revoke invitations; the secret is never listed; no temporary-password creation path."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.security import CSRF_HEADER
from test_v1_invites import redeem, token_of
from test_v1_sessions import client, factory, login  # noqa: F401  (fixtures)


def test_admin_invite_list_revoke(client: TestClient, factory) -> None:
    headers = {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: login(client)}
    first = client.post("/api/admin/invitations", json={"role": "admin"}, headers=headers).json()
    second = client.post("/api/admin/invitations", json={}, headers=headers).json()

    listed = client.get("/api/admin/invitations")
    assert listed.status_code == 200
    assert {(row["id"], row["role"], row["status"]) for row in listed.json()} == {(first["id"], "admin", "pending"), (second["id"], "viewer", "pending")}
    assert token_of(first) not in listed.text and "invitation_url" not in listed.text

    revoked = client.post(f"/api/admin/invitations/{first['id']}/revoke", headers=headers)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert redeem(token_of(first)) == 410
    # Revoking twice is harmless; a used invitation cannot be revoked; unknown ids are 404.
    assert client.post(f"/api/admin/invitations/{first['id']}/revoke", headers=headers).json()["status"] == "revoked"
    assert redeem(token_of(second)) == 201
    assert client.post(f"/api/admin/invitations/{second['id']}/revoke", headers=headers).status_code == 409
    assert client.post("/api/admin/invitations/nope/revoke", headers=headers).status_code == 404
    assert {row["status"] for row in client.get("/api/admin/invitations").json()} == {"revoked", "used"}


def test_no_temporary_password_member_creation(client: TestClient, factory) -> None:
    headers = {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: login(client)}
    body = {"username": "alex", "password": "Strong pass 42!", "role": "viewer"}
    assert client.post("/api/admin/users", json=body, headers=headers).status_code == 405
