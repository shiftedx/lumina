"""Owner settings, inheritance, classification and additive resource-policy upgrade contracts."""
import sqlite3

import pytest
from fastapi import Request

from app.config import settings
from app.db import MIGRATIONS
from app.services.resource_policy import HouseholdResources, remote
from support import make_user


def test_owner_defaults_overrides_reset_and_authorization(api_client, db_factory):
    owner, member = make_user("resource-owner", role="admin"), make_user("resource-member")
    with db_factory() as db:
        db.add_all([owner, member])
        db.commit()
    client = api_client(user=owner, base_url="http://localhost")
    policy = client.get("/api/admin/resources").json()
    assert policy["upload_bps"] is None
    assert policy["defaults"]["download_running"] == 1
    assert client.put("/api/admin/resources", json={**policy, "upload_bps": 20_000_000}).status_code == 200
    path = f"/api/admin/members/{member.id}/resources"
    changed = client.put(path, json={"download_running": 3, "ai_queued": 0}).json()
    assert changed["overrides"] == {"download_running": 3, "ai_queued": 0}
    assert changed["effective"]["download_running"] == 3
    assert changed["effective"]["playback_sessions"] == 2
    assert client.put(path, json={"remote_member_bps": 1_000_000}).status_code == 422
    assert client.put(path, json={"download_running": -1}).status_code == 422
    assert client.put(path, json={"download_running": True}).status_code == 422
    assert client.put(path, json={"unknown": 1}).status_code == 422
    assert client.put(path, json={"download_running": None}).json()["overrides"] == {}
    assert client.get(path).json()["effective"]["download_running"] == 1
    assert client.put("/api/admin/resources", json={**policy, "upload_bps": 0}).status_code == 422
    assert client.put("/api/admin/resources", json={**policy, "lan_networks": ["0.0.0.0/0"]}).status_code == 422
    viewer = api_client(user=member, base_url="http://localhost")
    assert viewer.get("/api/admin/resources").status_code == 403
    assert viewer.put(path, json={"ai_running": 0}).status_code == 403
    assert viewer.get("/api/me/resources").json()["effective"]["ai_running"] == 1


@pytest.mark.parametrize("peer,headers,local", [
    ("192.168.1.4", {}, True),
    ("192.168.1.4", {"x-forwarded-for": "192.168.1.5"}, False),
    ("127.0.0.1", {}, False),  # private proxy peer never proves a local viewer
    ("127.0.0.1", {"x-forwarded-for": "192.168.1.4"}, True),
    ("127.0.0.1", {"x-forwarded-for": "192.168.1.4, 203.0.113.2"}, False),
    ("127.0.0.1", {"x-forwarded-for": "192.168.1.4", "cf-connecting-ip": "192.168.1.4"}, False),
    ("203.0.113.4", {"host": "lumina.home.arpa"}, False),
    ("fd12::1", {}, True),
    ("::ffff:192.168.1.4", {}, True),
    ("10.10.0.4", {}, False),  # a VPN range is remote until explicitly exempted
])
def test_only_verified_explicit_lan_networks_are_exempt(monkeypatch, peer, headers, local):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "127.0.0.1/32")
    request = Request({"type": "http", "client": (peer, 1234), "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})
    policy = HouseholdResources(lan_networks=["192.168.1.0/24", "fd12::/64"])
    assert remote(request, policy) is not local
    assert remote(request, HouseholdResources()) is True


def test_resource_upgrade_is_additive_and_preserves_worker_settings(db_factory):
    # Execute the actual step against a previous-version shape; useful populated values survive.
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript("CREATE TABLE app_settings (concurrency INTEGER); INSERT INTO app_settings VALUES (4); CREATE TABLE users (id TEXT); INSERT INTO users VALUES ('member');")
        for statement in MIGRATIONS[22]:
            connection.execute(statement)
        assert connection.execute("SELECT concurrency, resource_policy FROM app_settings").fetchone() == (4, "{}")
        assert connection.execute("SELECT id, resource_overrides FROM users").fetchone() == ("member", "{}")
    finally:
        connection.close()


def test_capped_provider_routes_refuse_unbounded_playback_and_allow_cleanup(api_client, db_factory, monkeypatch):
    from app import main
    owner = make_user("resource-owner", role="admin")
    with db_factory() as db:
        db.add(owner)
        db.commit()
    monkeypatch.setattr(main, "SessionLocal", db_factory)
    client = api_client(user=owner, base_url="http://localhost")
    assert client.put("/api/admin/resources", json={"upload_bps": 20_000_000}).status_code == 200
    for path in ("content", "renditions/original/content", "hls/1/manifest.m3u8", "dash/1/manifest.mpd",
                 "relay/1/master.m3u8", "relay/1/r/segment"):
        response = client.get(f"/api/remote-streams/opaque/{path}")
        assert response.status_code == 409, (path, response.text)
        assert response.json()["detail"]["code"] == "unsupported_resource_policy"
    assert client.post("/api/remote-streams/opaque/renditions/original/select").status_code == 409
    assert client.post("/api/remote-streams/opaque/refresh").status_code == 409
    # Cancellation still reaches the owner-bound service rather than being rejected at capacity.
    calls = []
    monkeypatch.setattr(main.remote_streams, "release", lambda user, stream: calls.append((user, stream)))
    assert client.delete("/api/remote-streams/opaque").status_code == 204
    assert calls == [(owner.id, "opaque")]
