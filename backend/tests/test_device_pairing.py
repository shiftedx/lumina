"""TV enrollment is an explicit, short-lived approval from an enrolled phone."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import AppSettings, DevicePairing, DeviceToken, User, utcnow
from app.security import authenticate_user, hash_password, session_digest
from app.services.connected_apps import revoke_connected_app
from support import make_user, seed_app_settings

PASSWORD = "Test-only-passphrase-1"
MEMBER = "7d1e2f30-2222-4b5c-8d9e-0f1a2b3c4d02"
OTHER = "0a8b9c2e-1111-4d2a-9c3b-5e6f7a8b9c01"
START = "/Lumina/DevicePairing/Start"
STATUS = "/Lumina/DevicePairing/Status"
INSPECT = "/Lumina/DevicePairing/Inspect"
APPROVE = "/Lumina/DevicePairing/Approve"
DENY = "/Lumina/DevicePairing/Deny"
CLAIM = "/Lumina/DevicePairing/Claim"


@pytest.fixture
def pairing(db_factory, api_client):
    with db_factory.begin() as db:
        db.add_all([
            make_user(MEMBER, username="member", password_hash=hash_password(PASSWORD)),
            make_user(OTHER, username="other", password_hash=hash_password(PASSWORD)),
        ])
        seed_app_settings(db, jellyfin_enabled=True)
    return api_client(base_url="http://localhost"), db_factory


def media_browser_header(token: str | None = None, *, device_id: str, device: str) -> dict[str, str]:
    fields = ['Client="Lumina"', f'Device="{device}"', f'DeviceId="{device_id}"', 'Version="1.0"']
    if token:
        fields.append(f'Token="{token}"')
    return {"Authorization": "MediaBrowser " + ", ".join(fields)}


def phone_token(client: TestClient, *, username: str = "member", device_id: str = "phone-1") -> str:
    response = client.post(
        "/Users/AuthenticateByName",
        json={"Username": username, "Pw": PASSWORD},
        headers=media_browser_header(device_id=device_id, device="Kyle's iPhone"),
    )
    assert response.status_code == 200, response.text
    return response.json()["AccessToken"]


def start(client: TestClient) -> tuple[dict, dict[str, str]]:
    headers = media_browser_header(device_id="apple-tv-1", device="Living room Apple TV")
    response = client.post(START, json={"device_name": "Living room Apple TV"}, headers=headers)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json(), headers


def test_tv_pairing_issues_approves_claims_and_authenticates_an_independent_tv_app(pairing) -> None:
    client, factory = pairing
    created, tv_headers = start(client)

    assert set(created) == {"id", "device_secret", "code", "expires_at", "poll_interval_seconds", "server_id"}
    assert len(created["code"]) == 8 and created["code"].isalnum() and created["code"] == created["code"].upper()
    assert len(created["device_secret"]) >= 32 and created["poll_interval_seconds"] == 3
    with factory() as db:
        row = db.get(DevicePairing, created["id"])
        assert row is not None
        assert row.code_digest == session_digest(created["code"])
        assert row.device_secret_digest == session_digest(created["device_secret"])
        assert created["code"] not in {value for value in row.__dict__.values() if isinstance(value, str)}
        assert created["device_secret"] not in {value for value in row.__dict__.values() if isinstance(value, str)}

    payload = {"id": created["id"], "device_secret": created["device_secret"]}
    assert client.post(STATUS, json=payload, headers=tv_headers).json()["status"] == "pending"
    phone_headers = media_browser_header(phone_token(client), device_id="phone-1", device="Kyle's iPhone")
    inspected = client.post(INSPECT, json={"code": created["code"]}, headers=phone_headers)
    assert inspected.status_code == 200 and inspected.json() == {
        "code": created["code"], "device_name": "Living room Apple TV", "expires_at": created["expires_at"],
        "server_id": created["server_id"], "status": "pending",
    }
    assert client.post(APPROVE, json={"code": created["code"]}, headers=phone_headers).json() == {"status": "approved"}
    assert client.post(STATUS, json=payload, headers=tv_headers).json()["status"] == "approved"

    claimed = client.post(CLAIM, json=payload, headers=tv_headers)
    assert claimed.status_code == 200, claimed.text
    credential = claimed.json()
    assert credential["username"] == "member" and credential["password"]
    assert client.post(CLAIM, json=payload, headers=tv_headers).status_code == 409
    assert client.post(STATUS, json=payload, headers=tv_headers).json()["status"] == "consumed"

    signed_in = client.post(
        "/Users/AuthenticateByName",
        json={"Username": credential["username"], "Pw": credential["password"]},
        headers=tv_headers,
    )
    assert signed_in.status_code == 200, signed_in.text
    token = signed_in.json()["AccessToken"]
    with factory() as db:
        app = db.query(DeviceToken).filter(DeviceToken.kind == "app_password").one()
        member = db.get(User, MEMBER)
        revoke_connected_app(db, member, app.id)
    assert client.get("/Users/Me", headers=media_browser_header(token, device_id="apple-tv-1", device="Living room Apple TV")).status_code == 401
    assert client.post("/Users/AuthenticateByName", json={"Username": "member", "Pw": credential["password"]}, headers=tv_headers).status_code == 401
    assert client.get("/Users/Me", headers=phone_headers).status_code == 200


def test_tv_pairing_rejects_wrong_tv_pending_denied_and_expired_requests(pairing) -> None:
    client, factory = pairing
    created, tv_headers = start(client)
    payload = {"id": created["id"], "device_secret": created["device_secret"]}
    assert client.post(STATUS, json={**payload, "device_secret": "x" * 32}, headers=tv_headers).status_code == 401
    assert client.post(STATUS, json=payload, headers=media_browser_header(device_id="other-tv", device="Other TV")).status_code == 401
    assert client.post(CLAIM, json=payload, headers=tv_headers).status_code == 409
    assert client.post(INSPECT, json={"code": created["code"]}).status_code == 401

    phone_headers = media_browser_header(phone_token(client), device_id="phone-1", device="Kyle's iPhone")
    assert client.post(DENY, json={"code": created["code"]}, headers=phone_headers).json() == {"status": "denied"}
    assert client.post(STATUS, json=payload, headers=tv_headers).json()["status"] == "denied"
    assert client.post(CLAIM, json=payload, headers=tv_headers).status_code == 409

    later, later_headers = start(client)
    with factory.begin() as db:
        db.get(DevicePairing, later["id"]).expires_at = utcnow() - timedelta(seconds=1)
    later_payload = {"id": later["id"], "device_secret": later["device_secret"]}
    assert client.post(STATUS, json=later_payload, headers=later_headers).json()["status"] == "expired"
    assert client.post(CLAIM, json=later_payload, headers=later_headers).status_code == 410


def test_unclaimed_approval_dies_when_the_approving_phone_or_member_credential_is_revoked(pairing) -> None:
    client, factory = pairing
    phone_headers = media_browser_header(phone_token(client), device_id="phone-1", device="Kyle's iPhone")
    created, tv_headers = start(client)
    assert client.post(APPROVE, json={"code": created["code"]}, headers=phone_headers).status_code == 200
    with factory() as db:
        phone = db.query(DeviceToken).filter(DeviceToken.device_id == "phone-1").one()
        member = db.get(User, MEMBER)
        revoke_connected_app(db, member, phone.id)
    assert client.post(CLAIM, json={"id": created["id"], "device_secret": created["device_secret"]}, headers=tv_headers).status_code == 409
    with factory() as db:
        assert db.query(DeviceToken).filter(DeviceToken.kind == "app_password").count() == 0

    token = phone_token(client)
    phone_headers = media_browser_header(token, device_id="phone-1", device="Kyle's iPhone")
    replacement, replacement_headers = start(client)
    assert client.post(APPROVE, json={"code": replacement["code"]}, headers=phone_headers).status_code == 200
    with factory.begin() as db:
        db.get(User, MEMBER).password_hash = hash_password("Changed-test-passphrase-2")
    assert client.post(CLAIM, json={"id": replacement["id"], "device_secret": replacement["device_secret"]}, headers=replacement_headers).status_code == 409
    with factory() as db:
        assert authenticate_user(db, "member", "Changed-test-passphrase-2", app_passwords=True).id == MEMBER


def test_only_the_first_write_scoped_phone_can_change_an_approved_tv_pairing(pairing) -> None:
    client, factory = pairing
    created, _ = start(client)
    member_headers = media_browser_header(phone_token(client), device_id="phone-1", device="Kyle's iPhone")
    with factory.begin() as db:
        db.query(DeviceToken).filter(DeviceToken.device_id == "phone-1").update({DeviceToken.scope: "read"})
    assert client.post(APPROVE, json={"code": created["code"]}, headers=member_headers).status_code == 403
    assert client.post(DENY, json={"code": created["code"]}, headers=member_headers).status_code == 403

    member_headers = media_browser_header(phone_token(client), device_id="phone-1", device="Kyle's iPhone")
    assert client.post(APPROVE, json={"code": created["code"]}, headers=member_headers).status_code == 200
    other_headers = media_browser_header(phone_token(client, username="other", device_id="phone-2"), device_id="phone-2", device="Other iPhone")
    assert client.post(DENY, json={"code": created["code"]}, headers=other_headers).status_code == 409
    assert client.post(APPROVE, json={"code": created["code"]}, headers=other_headers).status_code == 409


def test_device_pairing_is_gated_and_the_private_mount_is_not_directly_public(pairing) -> None:
    client, factory = pairing
    with factory.begin() as db:
        db.get(AppSettings, 1).jellyfin_enabled = False
    headers = media_browser_header(device_id="apple-tv-1", device="Living room Apple TV")
    assert client.post(START, json={"device_name": "Living room Apple TV"}, headers=headers).status_code == 404
    assert client.post("/jellyfin/Lumina/DevicePairing/Start", json={"device_name": "Living room Apple TV"}, headers=headers).status_code == 404
