"""Member access invites: email, copy-link fallback, resend, revoke, redeem preset, public address and proxy trust."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import AccountToken, MemberAccess, RequestPolicy, User
from app.security import CSRF_HEADER, utcnow
from app.services import invite_email, public_address
from app.services.requests import notify
from support import make_user, seed_app_settings
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

PW = "Invitee-passphrase-9"
ORIGIN = settings.allowed_origins_list[0]


@pytest.fixture(autouse=True)
def _reset_address():
    yield
    public_address.set_origin(None)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda smtp, to, subject, body, html=None: sent.append((to, subject, body, html)))
    return sent


def smtp_on(factory) -> None:
    with factory.begin() as db:
        seed_app_settings(db, smtp_host="smtp.test", smtp_from="lumina@test")


def call(client: TestClient, csrf: str, method: str, url: str, **kw):
    return client.request(method, url, headers={"Origin": ORIGIN, CSRF_HEADER: csrf}, **kw)


def test_create_sends_email_with_link(client, factory, outbox):
    smtp_on(factory)
    csrf = login(client)
    public_address.set_origin("https://lumina.example.com")
    r = call(client, csrf, "POST", "/api/admin/invites", json={"email": "Pal@Example.com", "access": {"sections": ["movies", "anime"]}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email_sent"] is True and body["libraries"] == ["Anime", "Movies"]  # sorted like a member access save
    assert body["invitation_url"].startswith("https://lumina.example.com/#invite=")
    to, subject, text, html = outbox[0]
    assert to == "pal@example.com" and body["invitation_url"] in text and body["invitation_url"] in html and "Movies" in html
    assert (body["expires_at"] and (utcnow() + timedelta(days=6, hours=23)) < __import__("datetime").datetime.fromisoformat(body["expires_at"]))


def test_no_smtp_returns_link_to_copy(client, factory, outbox):
    csrf = login(client)
    body = call(client, csrf, "POST", "/api/admin/invites", json={"email": "a@b.co"}).json()
    assert body["email_sent"] is False and "#invite=" in body["invitation_url"] and not outbox
    assert call(client, csrf, "GET", "/api/admin/invites").json()[0]["status"] == "pending"


def test_resend_rotates_link_and_revoke_blocks_redeem(client, factory, outbox):
    smtp_on(factory)
    csrf = login(client)
    first = call(client, csrf, "POST", "/api/admin/invites", json={"email": "a@b.co"}).json()
    second = call(client, csrf, "POST", f"/api/admin/invites/{first['id']}/resend").json()
    assert second["id"] == first["id"] and second["invitation_url"] != first["invitation_url"] and len(outbox) == 2
    old, new = (b["invitation_url"].split("#invite=")[1] for b in (first, second))
    anon = TestClient(app, base_url="http://localhost")
    redeem = lambda t, u: anon.post("/api/invitations/redeem", json={"token": t, "username": u, "password": PW}).status_code  # noqa: E731
    assert redeem(old, "x1") == 410
    assert call(client, csrf, "DELETE", f"/api/admin/invites/{first['id']}").status_code == 204
    assert redeem(new, "x2") == 410


def test_expired_invite_refused(client, factory):
    csrf = login(client)
    body = call(client, csrf, "POST", "/api/admin/invites", json={"email": "a@b.co"}).json()
    with factory.begin() as db:
        db.query(AccountToken).update({"expires_at": utcnow() - timedelta(seconds=1)})
    assert TestClient(app, base_url="http://localhost").post(
        "/api/invitations/redeem", json={"token": body["invitation_url"].split("#invite=")[1], "username": "x", "password": PW}).status_code == 410


def test_redeem_applies_preset_and_request_policy(client, factory):
    csrf = login(client)
    body = call(client, csrf, "POST", "/api/admin/invites", json={"email": "a@b.co", "display_name": "Pal", "access": {"sections": ["movies"], "movie_rating_max": "PG", "can_request": False}}).json()
    r = TestClient(app, base_url="http://localhost").post(
        "/api/invitations/redeem", json={"token": body["invitation_url"].split("#invite=")[1], "username": "pal", "password": PW})
    assert r.status_code == 201 and r.json()["display_name"] == "Pal" and r.json()["role"] == "viewer"
    with factory() as db:
        uid = db.query(User).filter(User.username == "pal").one().id
        row = db.get(MemberAccess, uid)
        assert row.sections == ["movies"] and row.movie_rating_max == "PG" and row.can_download is False
        policies = db.query(RequestPolicy).filter(RequestPolicy.user_id == uid).all()
        assert len(policies) == 3 and not any(p.can_request for p in policies)


def test_invites_admin_only(factory, api_client):
    with factory.begin() as db:
        db.add(make_user("kid"))
    kid = api_client(user=make_user("kid"), base_url="http://localhost")
    assert kid.post("/api/admin/invites", json={"email": "a@b.co"}, headers={"Origin": ORIGIN}).status_code == 403
    assert kid.get("/api/admin/invites").status_code == 403


def test_template_escapes_names():
    _, text, html = invite_email.render(inviter='<b>Eve</b>&"', server="Lumina", libraries=["<i>x</i>"], link="https://h/#invite=t", expires=utcnow())
    assert "<b>Eve" not in html and "&lt;b&gt;Eve" in html and "<i>x</i>" not in html


def test_public_address_roundtrip_and_origin(client, factory):
    csrf = login(client)
    assert call(client, csrf, "PUT", "/api/admin/public-address", json={"public_address": "https://lumina.example.com/path"}).status_code == 422
    assert call(client, csrf, "PUT", "/api/admin/public-address", json={"public_address": "https://Lumina.Example.com/"}).json() == {"public_address": "https://lumina.example.com"}
    # the public origin and the LAN origin both pass the CSRF origin check; a stranger does not
    public = {"Origin": "https://lumina.example.com", CSRF_HEADER: csrf}
    assert client.post("/api/session/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/admin/public-address", headers=public).status_code == 200
    assert client.put("/api/admin/public-address", json={"public_address": None}, headers=public).status_code == 200
    assert call(client, csrf, "GET", "/api/admin/public-address").json() == {"public_address": None}


def _https_scope(peer: str, proto: str = "https") -> dict:
    return {"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-proto", proto.encode())]}


def test_forwarded_proto_trusted_only_from_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "172.30.0.2/32")
    assert public_address.arrived_over_https(_https_scope("172.30.0.2"))
    assert not public_address.arrived_over_https(_https_scope("172.30.0.9"))
    assert not public_address.arrived_over_https(_https_scope("172.30.0.2", "http"))


def test_secure_cookie_only_over_trusted_https(client, factory, monkeypatch):
    response = client.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert "secure" not in response.headers["set-cookie"].lower()  # LAN http, as before
    monkeypatch.setattr(public_address, "arrived_over_https", lambda scope: True)
    response = client.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert all("; Secure" in c for c in response.headers.get_list("set-cookie"))
