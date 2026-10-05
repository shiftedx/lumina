"""Jellyfin-compatible sign-in and the jellyfin_user dependency."""
from __future__ import annotations

import json
import logging
import re
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, text
from starlette.requests import Request

from app.models import AppSettings, DeviceToken, User
from app.routers.jellyfin_auth import JELLYFIN_VERSION
from app.security import hash_password, session_digest
from app.services.connected_apps import create_agent_token, jellyfin_user, parse_client_auth, revoke_connected_app
from app.services.users import UserService
from support import make_user, seed_app_settings

PASSWORD = "Test-only-passphrase-1"
ALICE = "0a8b9c2e-1111-4d2a-9c3b-5e6f7a8b9c01"  # a Lumina admin: never IsAdministrator over this API
BOB = "7d1e2f30-2222-4b5c-8d9e-0f1a2b3c4d02"


@pytest.fixture
def jf(db_factory, api_client):
    with db_factory.begin() as db:
        db.add_all([
            make_user(ALICE, role="admin", username="alice", display_name="Alice", password_hash=hash_password(PASSWORD)),
            make_user(BOB, username="bob", display_name="Bob", password_hash=hash_password(PASSWORD)),
        ])
    with db_factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    return api_client(base_url="http://localhost"), db_factory


def mb_header(token: str | None = None, *, device_id: str = "infuse-appletv", scheme: str = "MediaBrowser", header: str = "Authorization") -> dict[str, str]:
    parts = ['Client="Infuse-Direct"', 'Device="Apple%20TV"', f'DeviceId="{device_id}"', 'Version="8.1.4"']
    if token:
        parts.append(f'Token="{token}"')
    return {header: f"{scheme} " + ", ".join(parts)}


def login(client, username: str = "alice", **header_kwargs) -> str:
    response = client.post("/users/authenticatebyname", json={"Username": username, "Pw": PASSWORD}, headers=mb_header(**header_kwargs))
    assert response.status_code == 200, response.text
    return response.json()["AccessToken"]


def make_request(headers: dict[str, str] | None = None, query: str = "", path_params: dict[str, str] | None = None) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/users/me", "raw_path": b"/users/me",
        "query_string": query.encode(), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("192.0.2.5", 5000), "server": ("testserver", 80), "scheme": "http", "path_params": path_params or {},
    })


def test_parse_client_auth_reads_both_schemes_and_decodes_values() -> None:
    auth = parse_client_auth(make_request(mb_header("tok-1")))
    assert (auth.token, auth.client, auth.device, auth.device_id, auth.version) == ("tok-1", "Infuse-Direct", "Apple TV", "infuse-appletv", "8.1.4")
    emby = parse_client_auth(make_request({"X-Emby-Authorization": 'Emby UserId="x", Client=Swiftfin, DeviceId="d2", Token="tok-2"'}))
    assert (emby.token, emby.client, emby.device_id) == ("tok-2", "Swiftfin", "d2")
    for bad in ("MediaBrowser", 'MediaBrowser Token="unterminated', "Basic YWxpY2U6cGFzcw==", "Bearer abc", 'Emby Token=""'):
        assert parse_client_auth(make_request({"Authorization": bad})).token in (None, '"unterminated')


def test_parse_client_auth_precedence() -> None:
    everything = {**mb_header("from-auth"), "X-Emby-Token": "from-emby", "X-MediaBrowser-Token": "from-mb"}
    assert parse_client_auth(make_request(everything, "api_key=from-query")).token == "from-auth"
    assert parse_client_auth(make_request({**mb_header("from-x", header="X-Emby-Authorization", scheme="Emby"), "X-Emby-Token": "from-emby"})).token == "from-x"
    assert parse_client_auth(make_request({"X-Emby-Token": "from-emby", "X-MediaBrowser-Token": "from-mb"}, "api_key=q")).token == "from-emby"
    assert parse_client_auth(make_request({"X-MediaBrowser-Token": "from-mb"}, "api_key=q")).token == "from-mb"
    assert parse_client_auth(make_request(query="ApiKey=from-camel")).token == "from-camel"
    assert parse_client_auth(make_request(query="apikey=from-lower")).token == "from-lower"


def test_login_mints_hashed_per_device_token_and_relogin_replaces_it(jf) -> None:
    client, factory = jf
    first = client.post("/users/authenticatebyname", json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header())

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert set(body) == {"User", "SessionInfo", "AccessToken", "ServerId"}
    assert body["User"]["Id"] == uuid.UUID(ALICE).hex and body["User"]["Name"] == "alice"
    assert body["User"]["Policy"]["IsAdministrator"] is False
    assert body["User"]["ServerId"] == body["ServerId"] == client.get("/system/info/public").json()["Id"]
    assert (body["SessionInfo"]["DeviceId"], body["SessionInfo"]["DeviceName"]) == ("infuse-appletv", "Apple TV")

    old, new = body["AccessToken"], login(client)
    with factory() as db:
        rows = db.execute(text("SELECT * FROM device_tokens")).all()
    assert len(rows) == 1 and old not in repr(rows) and new not in repr(rows)
    assert rows[0].token_digest == session_digest(new)
    assert (rows[0].kind, rows[0].scope, rows[0].client, rows[0].client_version) == ("jellyfin", "write", "Infuse-Direct", "8.1.4")
    assert client.get("/users/me", headers={"X-Emby-Token": old}).status_code == 401
    assert client.get("/users/me", headers={"X-Emby-Token": new}).status_code == 200

    login(client, device_id="infuse-iphone")
    with factory() as db:
        assert db.query(DeviceToken).count() == 2


def test_login_failures_and_no_registration(jf) -> None:
    client, factory = jf
    url = "/users/authenticatebyname"
    assert client.post(url, json={"Username": "alice", "Pw": "wrong"}, headers=mb_header()).status_code == 401
    assert client.post(url, json={"Username": "alice", "Pw": PASSWORD}, headers={"Authorization": 'MediaBrowser Client="Infuse"'}).status_code == 400
    assert client.post(url, json={"Username": "alice"}, headers=mb_header()).status_code == 400
    assert client.post(url, json={"Username": "alice", "Pw": 12345}, headers=mb_header()).status_code == 400
    assert client.post(url, json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header(device_id="d" * 300)).status_code == 400
    # An agent row's DeviceId is never reachable from a Jellyfin sign-in.
    assert client.post(url, json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header(device_id=f"agent:{uuid.uuid4()}")).status_code == 400
    assert client.post("/users/new", json={"Name": "mallory", "Password": PASSWORD}).status_code in (404, 405)
    assert client.get("/users/public").json() == []
    assert client.get("/quickconnect/enabled").json() is False
    with factory() as db:
        assert db.query(DeviceToken).count() == 0
        assert db.query(User).count() == 2


def test_login_is_rate_limited_like_the_web_sign_in(jf) -> None:
    client, _ = jf
    for _ in range(10):
        assert client.post("/users/authenticatebyname", json={"Username": "alice", "Pw": "wrong"}, headers=mb_header()).status_code == 401
    blocked = client.post("/users/authenticatebyname", json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header())
    assert blocked.status_code == 429


@pytest.mark.parametrize("form", ["authorization", "x-emby-authorization", "X-Emby-Token", "X-MediaBrowser-Token", "api_key", "ApiKey"])
def test_every_client_token_form_authenticates(jf, form) -> None:
    client, _ = jf
    token = login(client)
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if form == "authorization":
        headers = mb_header(token)
    elif form == "x-emby-authorization":
        headers = mb_header(token, header="X-Emby-Authorization", scheme="Emby")
    elif form.startswith("X-"):
        headers = {form: token}
    else:
        params = {form: token}
    response = client.get("/users/me", headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["Id"] == uuid.UUID(ALICE).hex


@pytest.mark.parametrize("headers", [
    {"Authorization": "MediaBrowser"},
    {"Authorization": 'MediaBrowser Token="unterminated'},
    {"Authorization": "Basic YWxpY2U6cGFzcw=="},
    {"Authorization": "Bearer something"},
    {"X-Emby-Authorization": 'Emby Token=""'},
    {"X-Emby-Token": "x" * 600},
    {},
])
def test_malformed_credentials_are_401_not_500(jf, headers) -> None:
    client, _ = jf
    assert client.get("/users/me", headers=headers).status_code == 401


def test_lumina_cookie_is_ignored_on_jellyfin(jf) -> None:
    client, _ = jf
    assert client.post("/api/session/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    assert client.get("/users/me").status_code == 401


def test_tokens_only_work_on_their_own_surface(jf) -> None:
    client, factory = jf
    jellyfin_token = login(client)
    with factory() as db:
        _, agent_token = create_agent_token(db, db.get(User, ALICE), "Script", "write")
    assert client.get("/api/session/me", headers={"Authorization": f"Bearer {jellyfin_token}"}).status_code == 401
    assert client.get("/users/me", headers={"X-Emby-Token": agent_token}).status_code == 401


def test_revoked_device_and_password_change_force_sign_in(jf) -> None:
    client, factory = jf
    token = login(client)
    with factory() as db:
        app_id = db.query(DeviceToken.id).scalar()
        revoke_connected_app(db, db.get(User, ALICE), app_id)
    assert client.get("/users/me", headers={"X-Emby-Token": token}).status_code == 401

    token = login(client)
    with factory() as db:
        UserService(db).change_password(db.get(User, ALICE), PASSWORD, "New secure passphrase 42!")
    assert client.get("/users/me", headers={"X-Emby-Token": token}).status_code == 401


def test_legacy_uid_must_be_the_caller(jf) -> None:
    client, _ = jf
    auth = {"X-Emby-Token": login(client)}
    assert client.get(f"/users/{uuid.UUID(ALICE).hex}", headers=auth).status_code == 200
    assert client.get(f"/users/{ALICE.upper()}", headers=auth).status_code == 200
    assert client.get(f"/users/{uuid.UUID(BOB).hex}", headers=auth).status_code == 404
    assert client.get("/users/not-a-uuid", headers=auth).status_code == 404
    assert client.get("/users/me", headers=auth, params={"userId": uuid.UUID(BOB).hex}).status_code == 404
    assert client.get("/users/me", headers=auth, params={"UserId": uuid.UUID(ALICE).hex}).status_code == 200
    # Every userId value must be the caller, whichever one a route happens to read.
    mixed = f"/users/me?userId={uuid.UUID(ALICE).hex}&UserId={uuid.UUID(BOB).hex}"
    assert client.get(mixed, headers=auth).status_code == 404


def test_disabled_surface_is_404_everywhere(jf) -> None:
    client, factory = jf
    token = login(client)
    with factory.begin() as db:
        db.get(AppSettings, 1).jellyfin_enabled = False
    assert client.get("/system/info/public").status_code == 404
    assert client.get("/users/public").status_code == 404
    assert client.post("/users/authenticatebyname", json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header()).status_code == 404
    assert client.get("/users/me", headers={"X-Emby-Token": token}).status_code == 404
    for path in ("/System/Info/Public", "/users/public"):  # the root form adds no surface while off
        response = client.get(path)
        assert response.status_code == 404 and response.headers["content-type"] == "application/json", path


def test_root_form_discovers_and_signs_in(jf) -> None:
    client, _ = jf
    public = client.get("/system/info/public").json()
    assert client.get("/System/Info/Public").json() == public
    assert client.get("http://localhost//system/info/public/").json() == public
    response = client.post("/Users/AuthenticateByName", json={"Username": "alice", "Pw": PASSWORD}, headers=mb_header())
    assert response.status_code == 200, response.text
    token = response.json()["AccessToken"]
    assert client.get("/Users/Me", headers={"X-Emby-Token": token}).json()["Name"] == "alice"
    assert client.get("/jellyfin/users/me", headers={"X-Emby-Token": token}).status_code == 404  # no prefixed aliases
    assert client.get("/Users/Me").status_code == 401


def test_fresh_database_without_settings_row_keeps_jellyfin_off(db_factory, api_client) -> None:
    client = api_client(base_url="http://localhost")
    assert client.get("/system/info/public").status_code == 404


def test_server_identity_is_stable_jellyfin_branded_and_pathless(jf) -> None:
    client, factory = jf
    with factory() as db:
        assert db.get(AppSettings, 1).jellyfin_server_key is None
    public = client.get("/system/info/public").json()
    assert (public["ProductName"], public["Version"], public["ServerName"]) == ("Jellyfin Server", JELLYFIN_VERSION, "Lumina")
    assert re.fullmatch(r"[0-9a-f]{32}", public["Id"]) and not public["LocalAddress"].endswith("/jellyfin")
    assert client.get("/system/info").status_code == 401

    full = client.get("/system/info", headers={"X-Emby-Token": login(client)}).json()
    assert full["Id"] == public["Id"] == client.get("/system/info/public").json()["Id"]
    assert not [key for key in full if key.endswith("Path")]
    assert "emby" not in json.dumps(full).lower()
    with factory() as db:
        assert re.fullmatch(r"[0-9a-f]{64}", db.get(AppSettings, 1).jellyfin_server_key)
    assert client.get("/system/ping").json() == "Jellyfin Server"


def test_jellyfin_user_returns_a_detached_snapshot(jf) -> None:
    client, factory = jf
    token = login(client)
    with factory() as db:
        user = jellyfin_user(make_request({"X-Emby-Token": token}), db)
        assert inspect(user).transient and user.id == ALICE and user.role == "admin"
        with pytest.raises(HTTPException) as denied:
            jellyfin_user(make_request({"X-Emby-Token": token}, path_params={"uid": uuid.UUID(BOB).hex}), db)
        assert denied.value.status_code == 404


def test_tokens_never_reach_logs(jf, caplog: pytest.LogCaptureFixture) -> None:
    client, _ = jf
    with caplog.at_level(logging.DEBUG):
        token = login(client)
        client.get("/users/me", headers={"X-Emby-Token": token})
        client.get("/users/me", headers={"X-MediaBrowser-Token": token})
        client.get("/users/me", headers=mb_header(token, header="X-Emby-Authorization", scheme="Emby"))
        client.get(f"/users/me?api_key={token}")
        client.get(f"/users/me?ApiKey={token}")
        client.get(f"/users/me?api_key={token}-stale")  # 401 path
        logging.getLogger("lumina.test").warning("client sent X-Emby-Token: %s / X-MediaBrowser-Token: %s", token, token)
    assert caplog.records
    assert token not in caplog.text


def test_sessions_logout_revokes_only_the_calling_device(jf) -> None:
    """Jellyfin clients POST /Sessions/Logout on sign-out; the token must die then, not after 90 idle days."""
    client, _ = jf
    tv, phone = login(client, device_id="tv"), login(client, device_id="phone")
    assert client.post("/Sessions/Logout").status_code == 401
    assert client.post("/Sessions/Logout", headers={"X-Emby-Token": tv}).status_code == 204
    assert client.get("/users/me", headers={"X-Emby-Token": tv}).status_code == 401
    assert client.get("/users/me", headers={"X-Emby-Token": phone}).status_code == 200


def test_public_info_over_the_public_address_never_names_the_lan_address(jf) -> None:
    from app.config import settings
    from app.services import public_address

    client, _ = jf
    lan = client.get("/System/Info/Public").json()["LocalAddress"]
    public_address.set_origin("https://lumina.example.com")
    try:
        assert client.get("/System/Info/Public").json()["LocalAddress"] == lan == settings.resolved_app_public_url  # LAN callers
        remote = client.get("https://lumina.example.com/System/Info/Public").json()
        assert remote["LocalAddress"] == "https://lumina.example.com"
        token = login(client)
        full = client.get("https://lumina.example.com/System/Info", headers={"X-Emby-Token": token}).json()
        assert full["LocalAddress"] == "https://lumina.example.com"
    finally:
        public_address.set_origin(None)


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/Users"), ("POST", "/Users/New"), ("GET", "/System/Configuration"), ("GET", "/System/Logs"),
    ("GET", "/ScheduledTasks"), ("GET", "/Sessions"), ("GET", "/Devices"), ("GET", "/Auth/Keys"), ("POST", "/Auth/Keys"),
    ("GET", "/Startup/Configuration"), ("POST", "/Startup/User"), ("POST", "/Library/Refresh"), ("POST", "/System/Restart"),
    ("POST", "/QuickConnect/Initiate"), ("POST", "/Users/AuthenticateWithQuickConnect"), ("POST", f"/Users/{uuid.UUID(BOB).hex}/Password"),
])
def test_no_admin_or_wizard_surface_even_for_a_vault_owner(jf, method: str, path: str) -> None:
    """Real Jellyfin serves these to administrators (and the wizard to anyone before setup); Lumina serves none of them."""
    client, _ = jf
    token = login(client)  # alice is a Lumina vault owner
    for headers in ({}, {"X-Emby-Token": token}):
        response = client.request(method, path, headers=headers)
        # With a built frontend, an unknown GET path is the SPA shell (HTML) and other methods 405; never Jellyfin data.
        spa_shell = response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html")
        assert response.status_code in (401, 404, 405) or spa_shell, (path, response.status_code)
        assert "alice" not in response.text and "bob" not in response.text


def test_public_info_over_the_local_address_names_the_local_address(jf, monkeypatch) -> None:
    from app.config import settings
    from app.services import public_address

    client, _ = jf
    monkeypatch.setattr(settings, "lan_http", True)
    public_address.set_origin("https://lumina.example.com")
    public_address.set_local_origin("http://lumina.home.arpa")
    try:
        assert client.get("http://lumina.home.arpa/System/Info/Public").json()["LocalAddress"] == "http://lumina.home.arpa"
        assert client.get("https://lumina.example.com/System/Info/Public").json()["LocalAddress"] == "https://lumina.example.com"
        assert client.get("/System/Info/Public").json()["LocalAddress"] == settings.resolved_app_public_url
    finally:
        public_address.set_local_origin(None)
        public_address.set_origin(None)
