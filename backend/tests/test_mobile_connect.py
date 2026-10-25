"""One-use QR handoffs become revocable app passwords only when claimed."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import AppSettings, DeviceToken, MobilePairing, User, utcnow
from app.schemas import UserUpdateRequest
from app.security import CSRF_HEADER, authenticate_user, hash_password, session_digest
from app.services import qr
from app.services.requests import notify
from app.services.users import UserService
from support import file_backed_session_factory, make_user, seed_app_settings

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def factory(db_factory):
    with db_factory.begin() as db:
        db.add_all([
            make_user("owner", role="admin", username="owner", password_hash=hash_password(PASSWORD)),
            make_user("backup", role="admin", username="backup", password_hash=hash_password(PASSWORD)),
            make_user("member", username="member", password_hash=hash_password(PASSWORD)),
            make_user("managed", username="managed", password_hash=None),
        ])
        seed_app_settings(db, jellyfin_enabled=True)
    return db_factory


@pytest.fixture
def client(factory, api_client) -> TestClient:
    return api_client(base_url="http://localhost")


def browser_headers(client: TestClient, username: str) -> dict[str, str]:
    result = client.post("/api/session/login", json={"username": username, "password": PASSWORD}, headers={"Origin": settings.allowed_origins_list[0]})
    assert result.status_code == 200, result.text
    return {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: result.json()["csrf_token"]}


def test_pair_claim_is_single_use_and_never_persists_the_qr_secret(client, api_client, factory) -> None:
    created = client.post("/api/mobile-connect", json={"server_url": "http://192.0.2.8:8765"}, headers=browser_headers(client, "member"))

    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    pair = created.json()
    payload = json.loads(pair["qr_payload"])
    assert payload | {"token": None} == {"type": "lumina-pair", "version": 1, "server_url": "http://192.0.2.8:8765", "token": None}
    assert pair["qr_size"] > 21 and pair["qr_path"].startswith("M")
    assert qr.png(pair["qr_payload"]).startswith(b"\x89PNG\r\n\x1a\n")
    with factory() as db:
        rows = db.query(MobilePairing).all()
        assert len(rows) == 1 and rows[0].token_digest == session_digest(payload["token"])
        assert payload["token"] not in {value for value in rows[0].__dict__.values() if isinstance(value, str)}

    claimed = api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": payload["token"], "name": "Dana's iPhone"})
    assert claimed.status_code == 200, claimed.text
    assert claimed.headers["cache-control"] == "no-store"
    assert claimed.json()["username"] == "member" and claimed.json()["server_url"] == payload["server_url"]
    assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": payload["token"], "name": "Other"}).status_code == 401
    with factory() as db:
        assert db.query(DeviceToken).filter(DeviceToken.kind == "app_password").count() == 1
        assert db.query(MobilePairing).one().used_at is not None


def test_disabled_invalid_and_other_member_boundaries(client, api_client, factory) -> None:
    member_headers = browser_headers(client, "member")
    with factory.begin() as db:
        db.get(AppSettings, 1).jellyfin_enabled = False
    assert client.post("/api/mobile-connect", json={"server_url": "https://vault.example"}, headers=member_headers).status_code == 409
    owner = api_client(base_url="http://localhost")
    assert owner.post("/api/mobile-connect/enable", headers=browser_headers(owner, "owner")).json()["enabled"] is True
    assert client.post("/api/mobile-connect", json={"server_url": "https://vault.example/path"}, headers=member_headers).status_code == 422
    assert client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "owner"}, headers=member_headers).status_code == 403
    assert client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=member_headers).status_code == 403


def test_owner_share_replaces_prior_code_and_emails_an_inline_png(client, factory, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(notify, "send", lambda *args, **kwargs: sent.append((args, kwargs)))
    with factory.begin() as db:
        settings = db.get(AppSettings, 1)
        settings.smtp_host, settings.smtp_from = "smtp.test", "lumina@test"
    csrf = browser_headers(client, "owner")
    first = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=csrf).json()
    replacement = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True, "email": "member@example.com", "replace_id": first["id"]}, headers=csrf)

    assert replacement.status_code == 201 and replacement.json()["email_sent"] is True
    assert sent[0][1]["attachment"][0] == "lumina-mobile-connect.png"
    assert sent[0][1]["attachment"][1].startswith(b"\x89PNG")
    assert "lumina://pair#" in sent[0][0][4]
    assert client.get(f"/api/mobile-connect/{first['id']}", headers=csrf).json() == {"status": "revoked"}


def test_managed_member_can_claim_an_owner_shared_code(client, api_client, factory) -> None:
    owner_headers = browser_headers(client, "owner")
    created = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "managed", "share": True}, headers=owner_headers)
    payload = json.loads(created.json()["qr_payload"])

    claimed = api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": payload["token"], "name": "Managed phone"})

    assert claimed.status_code == 200
    with factory() as db:
        assert authenticate_user(db, "managed", claimed.json()["password"], app_passwords=True).id == "managed"


def test_owner_sharing_requires_two_factor_capability_and_revokes_stale_code(client, api_client, factory) -> None:
    owner_headers = browser_headers(client, "owner")
    created = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=owner_headers)
    assert created.status_code == 201
    token = json.loads(created.json()["qr_payload"])["token"]
    with factory.begin() as db:
        db.get(AppSettings, 1).require_owner_two_factor = True
    refused = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=owner_headers)
    assert refused.status_code == 403
    assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": token, "name": "Phone"}).status_code == 401
    with factory() as db:
        assert db.query(MobilePairing).one().revoked_at is not None


def test_deactivation_reactivation_does_not_restore_pairing_code(client, api_client, factory) -> None:
    member_headers = browser_headers(client, "member")
    own = client.post("/api/mobile-connect", json={"server_url": "https://vault.example"}, headers=member_headers).json()
    owner = api_client(base_url="http://localhost")
    owner_headers = browser_headers(owner, "owner")
    shared = owner.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=owner_headers).json()
    with factory.begin() as db:
        member = db.get(User, "member")
        member.is_active = False
        UserService(db).revoke_sessions(member.id)
        member.is_active = True
    for pair in (own, shared):
        token = json.loads(pair["qr_payload"])["token"]
        assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": token, "name": "Phone"}).status_code == 401
    with factory() as db:
        assert all(pair.revoked_at is not None for pair in db.query(MobilePairing).all())


def test_issuer_role_change_does_not_restore_shared_code(client, api_client, factory) -> None:
    owner_headers = browser_headers(client, "owner")
    shared = client.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True}, headers=owner_headers).json()
    with factory() as db:
        service = UserService(db)
        backup = db.get(User, "backup")
        service.manage_user(backup, "owner", UserUpdateRequest(role="viewer"))
        service.manage_user(backup, "owner", UserUpdateRequest(role="admin"))
    token = json.loads(shared["qr_payload"])["token"]
    assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": token, "name": "Phone"}).status_code == 401
    with factory() as db:
        assert db.query(MobilePairing).one().revoked_at is not None


def test_expired_code_and_email_failure_leave_no_credential(client, api_client, factory, monkeypatch) -> None:
    member_headers = browser_headers(client, "member")
    own = client.post("/api/mobile-connect", json={"server_url": "https://vault.example"}, headers=member_headers).json()
    assert own["expires_at"].endswith("Z")
    with factory.begin() as db:
        db.query(MobilePairing).filter(MobilePairing.id == own["id"]).update({MobilePairing.expires_at: utcnow() - timedelta(seconds=1)})
    assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": json.loads(own["qr_payload"])["token"], "name": "Phone"}).status_code == 401

    monkeypatch.setattr(notify, "send", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("smtp unavailable")))
    with factory.begin() as db:
        settings = db.get(AppSettings, 1)
        settings.smtp_host, settings.smtp_from = "smtp.test", "lumina@test"
    owner = api_client(base_url="http://localhost")
    shared = owner.post("/api/mobile-connect", json={"server_url": "https://vault.example", "user_id": "member", "share": True, "email": "member@example.com"}, headers=browser_headers(owner, "owner"))
    assert shared.status_code == 201 and shared.json()["email_sent"] is False
    token = json.loads(shared.json()["qr_payload"])["token"]
    assert api_client(base_url="http://localhost").post("/api/mobile-connect/claim", json={"token": token, "name": "Phone"}).status_code == 200


def test_concurrent_claims_mint_one_app_password() -> None:
    factory = file_backed_session_factory()
    raw = "a" * 32
    with factory() as db:
        user = make_user("member", username="member", password_hash=hash_password(PASSWORD))
        db.add(user)
        seed_app_settings(db, jellyfin_enabled=True)
    with factory.begin() as db:
        user = db.get(User, "member")
        db.add(MobilePairing(
            id="pair", token_digest=session_digest(raw), user_id=user.id, issued_by=user.id, server_url="https://vault.example",
            user_credential_digest=session_digest(user.password_hash), issuer_credential_digest=session_digest(user.password_hash),
            shared=False, expires_at=utcnow() + timedelta(minutes=5),
        ))

    def override_db():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    prior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    try:
        def claim_once() -> int:
            claimant = TestClient(app, base_url="http://localhost")
            try:
                return claimant.post("/api/mobile-connect/claim", json={"token": raw, "name": "Phone"}).status_code
            finally:
                claimant.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: claim_once(), range(2)))
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = prior
    with factory() as db:
        assert sorted(statuses) == [200, 401]
        assert db.query(DeviceToken).filter(DeviceToken.kind == "app_password").count() == 1
