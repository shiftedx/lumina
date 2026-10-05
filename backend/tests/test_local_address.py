"""The local address (ADR 0001, local address amendment): a home-network name beside the public address, behind the proxy."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import settings
from app.security import CSRF_HEADER
from app.services import public_address
from app.services.rate_limit import resolve_client_key
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

LOCAL = "http://lumina.home.arpa"
PROXY = ("172.18.0.5", 4242)  # Traefik on its docker network


@pytest.fixture(autouse=True)
def lan_mode(monkeypatch):
    monkeypatch.setattr(settings, "lan_http", True)
    monkeypatch.setattr(settings, "trusted_proxy_ips", "172.18.0.0/16")
    public_address.set_origin("https://lumina.example.com")
    yield
    public_address.set_local_origin(None)
    public_address.set_origin(None)


@pytest.mark.parametrize(("value", "expected"), [
    ("http://lumina.home.arpa", "http://lumina.home.arpa"),
    ("HTTP://Lumina.home.arpa/", "http://lumina.home.arpa"),
    ("https://lumina.home.arpa:8443", "https://lumina.home.arpa:8443"),
    ("http://media.lan:8765", "http://media.lan:8765"),
    ("http://lumina.internal", "http://lumina.internal"),
    ("", None),
])
def test_accepts_home_network_names(value, expected):
    assert public_address.normalize_local(value) == expected


@pytest.mark.parametrize("value", [
    "lumina.home.arpa", "ftp://lumina.local", "http://192.168.1.96", "http://[fd00::1]",
    "http://*.home.arpa", "http://lumina.home.arpa/path", "http://lumina.home.arpa?x=1", "http://u:p@lumina.local",
    "http://lumina.example.com", "http://local", "http://lumina.local:99999", "http://lumina_x.local",
])
def test_rejects_anything_else(value):
    with pytest.raises(ValueError):
        public_address.normalize_local(value)


def test_rejects_the_public_host_and_needs_lan_mode(monkeypatch):
    public_address.set_origin("https://lumina.home.arpa")
    with pytest.raises(ValueError, match="differ from the public"):
        public_address.normalize_local(LOCAL)
    public_address.set_origin(None)
    monkeypatch.setattr(settings, "lan_http", False)
    with pytest.raises(ValueError, match="LAN HTTP mode"):
        public_address.normalize_local(LOCAL)
    public_address.set_local_origin(LOCAL)  # a saved one is ignored, not fatal, once LAN mode is off
    assert public_address.local_origin() is None


def test_sign_in_and_post_over_the_local_address(client, api_client):
    csrf = login(client)
    put = client.put("/api/admin/local-address", json={"local_address": LOCAL}, headers={"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf})
    assert put.json() == {"local_address": LOCAL, "lan_http": True}
    assert client.put("/api/admin/public-address", json={"public_address": "https://lumina.home.arpa"},
                      headers={"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf}).status_code == 422

    home = api_client(base_url=LOCAL, client=PROXY, headers={"X-Forwarded-For": "192.168.1.50", "X-Forwarded-Proto": "http", "Origin": LOCAL})
    response = home.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert "secure" not in response.headers["set-cookie"].lower() and "strict-transport-security" not in response.headers
    home_csrf = response.json()["csrf_token"]
    assert home.put("/api/admin/local-address", json={"local_address": LOCAL}, headers={CSRF_HEADER: home_csrf}).status_code == 200
    created = home.post("/api/connected-apps/app-passwords", json={"name": "Infuse"}, headers={CSRF_HEADER: home_csrf}).json()
    assert (created["local_server_address"], created["server_address"]) == (LOCAL, "https://lumina.example.com")  # home and away

    # the client key is the LAN device behind Traefik, not the proxy
    scope = {"type": "http", "client": PROXY, "headers": [(b"host", b"lumina.home.arpa"), (b"x-forwarded-for", b"192.168.1.50")],
             "method": "GET", "path": "/", "query_string": b"", "scheme": "http", "server": ("lumina.home.arpa", 80)}
    assert resolve_client_key(Request(scope)) == "192.168.1.50"

    # an unlisted name is refused as a Host and as an Origin
    assert api_client(base_url="http://other.home.arpa").get("/api/session/me").status_code == 400
    assert client.post("/api/session/logout", headers={"Origin": "http://other.home.arpa", CSRF_HEADER: csrf}).status_code == 403


def test_https_relayed_for_the_local_name(client, api_client):
    login(client)
    public_address.set_local_origin(LOCAL)
    tls = api_client(base_url=LOCAL, client=PROXY, headers={"X-Forwarded-Proto": "https", "Origin": "https://lumina.home.arpa"})
    assert tls.get("/api/session/me").status_code in {200, 401}  # the Host is trusted
    assert "strict-transport-security" not in tls.get("/api/session/me").headers  # an http address is never pinned to HTTPS
    assert tls.post("/api/session/login", json={"username": "local", "password": PASSWORD}).status_code == 403  # not the saved origin
    plain = api_client(base_url=LOCAL, client=PROXY, headers={"X-Forwarded-Proto": "https", "Origin": LOCAL})
    response = plain.post("/api/session/login", json={"username": "local", "password": PASSWORD})
    assert response.status_code == 200 and all("; Secure" in c for c in response.headers.get_list("set-cookie"))
