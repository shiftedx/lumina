"""Opt-in LAN HTTP mode (ADR 0001 amendment): plain http on a private LAN IP, no proxy."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

from app.config import EnvSettings, settings
from app.security import hash_password, validate_state_changing_request
from support import make_user, seed_app_settings

LAN = "http://192.168.1.96:8765"
PASSWORD = "Strong test pass 42!"


def lan_config(**overrides) -> EnvSettings:  # noqa: ANN003
    values = {"lan_http": True, "host": "0.0.0.0", "app_public_url": LAN} | overrides
    return EnvSettings(_env_file=None, **values)


@pytest.mark.parametrize(
    "url",
    [LAN, "http://10.0.0.5:8765", "http://172.16.0.1:8765", "http://172.31.255.254"],
)
def test_lan_mode_accepts_private_ip_http_origins(url: str) -> None:
    lan_config(app_public_url=url).validate_runtime_security()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_public_url": "http://8.8.8.8:8765"}, "private LAN IP"),
        ({"app_public_url": "http://172.32.0.1:8765"}, "private LAN IP"),
        ({"app_public_url": "http://169.254.1.1:8765"}, "private LAN IP"),
        ({"app_public_url": "http://[2001:db8::1]:8765"}, "private LAN IP"),
        ({"app_public_url": "http://[fd12:3456::1]:8765"}, "private LAN IP"),  # Starlette's host check cannot match IPv6
        ({"app_public_url": "http://nas.local:8765"}, "private LAN IP"),
        ({"app_public_url": None}, "private LAN IP"),  # defaults to loopback
        ({"app_public_url": "https://192.168.1.96"}, "plain http"),
        ({"app_public_url": "http://user:pw@192.168.1.96:8765"}, "canonical origin"),
        ({"app_public_url": LAN + "/lumina"}, "canonical origin"),
        ({"session_cookie_secure": True}, "Secure cookies"),
        ({"frontend_public_url": "http://192.168.1.97:8765"}, "same public URL"),
        ({"allowed_origins": LAN + ",http://192.168.1.97:8765"}, "only the public URL"),
    ],
)
def test_lan_mode_rejects_anything_but_a_private_ip_http_origin(overrides, message) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match=message):
        lan_config(**overrides).validate_runtime_security()


def test_without_the_flag_a_lan_http_url_keeps_the_old_error() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        EnvSettings(_env_file=None, app_public_url=LAN).validate_runtime_security()


def test_lan_mode_trusts_the_lan_url_and_keeps_loopback_on_the_host() -> None:
    config = lan_config()
    assert config.trusted_hosts_list == ["127.0.0.1", "192.168.1.96", "localhost"]
    assert set(config.allowed_origins_list) == {LAN, "http://127.0.0.1:8765", "http://localhost:8765"}
    assert config.remote_https_enabled is False


def lan_mode(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "lan_http", True)
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "app_public_url", LAN)


def post_from(origin: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"origin", origin.encode())]})


def test_csrf_origin_check_accepts_only_the_lan_origin(monkeypatch) -> None:  # noqa: ANN001
    assert validate_state_changing_request(post_from(LAN)) is not None
    lan_mode(monkeypatch)
    assert validate_state_changing_request(post_from(LAN)) is None
    assert validate_state_changing_request(post_from("http://127.0.0.1:8765")) is None
    assert validate_state_changing_request(post_from("http://192.168.1.97:8765")) is not None


def create_admin(client):  # noqa: ANN001, ANN201
    return client.post(
        "/api/bootstrap/admin",
        json={"username": "owner", "display_name": "Owner", "password": PASSWORD},
        headers={"Origin": LAN},
    )


def test_first_run_setup_from_the_lan_is_refused_without_lan_mode(api_client) -> None:  # noqa: ANN001
    assert create_admin(api_client(base_url="http://localhost")).status_code == 403


def test_first_run_setup_from_the_lan_works_in_lan_mode(api_client, monkeypatch) -> None:  # noqa: ANN001
    lan_mode(monkeypatch)
    response = create_admin(api_client(base_url="http://localhost"))
    assert response.status_code == 201


def test_lan_mode_session_cookie_is_not_secure(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    with db_factory.begin() as db:
        db.add(make_user("owner-1", role="admin", username="owner", display_name="Owner", password_hash=hash_password(PASSWORD)))
    lan_mode(monkeypatch)
    response = api_client(base_url="http://localhost").post(
        "/api/session/login", json={"username": "owner", "password": PASSWORD}, headers={"Origin": LAN}
    )
    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert "Secure" not in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_jellyfin_local_address_is_the_lan_url(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    with db_factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    lan_mode(monkeypatch)
    client = api_client(base_url="http://localhost")
    assert client.get("/system/info/public").json()["LocalAddress"] == LAN
    assert client.get("/System/Info/Public").json()["LocalAddress"] == LAN  # Infuse adds the server by host:port


HOST_PROBE = """
import sys
from starlette.testclient import TestClient
from app.main import app
client = TestClient(app)  # no lifespan: only the middleware stack is under test
print(" ".join(str(client.get("/api/health", headers={"host": host}).status_code) for host in sys.argv[1:]))
"""


def test_lan_mode_app_accepts_only_the_lan_host_header(tmp_path) -> None:  # noqa: ANN001
    # TrustedHostMiddleware reads the settings at import time, so build the app in a fresh interpreter.
    env = os.environ | {"LUMINA_LAN_HTTP": "true", "LUMINA_HOST": "0.0.0.0", "LUMINA_APP_PUBLIC_URL": LAN, "LUMINA_DATA_DIR": str(tmp_path)}
    hosts = ["192.168.1.96:8765", "127.0.0.1:8765", "evil.example", "192.168.1.97:8765"]
    result = subprocess.run(
        [sys.executable, "-c", HOST_PROBE, *hosts], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True
    )
    assert result.stdout.split() == ["200", "200", "400", "400"]


@pytest.mark.parametrize(("ranges", "warned"), [
    ("172.18.0.0/16", False),  # production: Traefik's docker bridge
    ("172.18.0.5", False),
    ("172.16.0.0/12", True),  # wider than a bridge
    ("192.168.1.0/24", True),  # the LAN itself
    ("192.168.0.0/16", True),  # contains the LAN
    ("0.0.0.0/0", True),
    ("172.18.0.0/16,fd00::/8", True),
])
def test_lan_mode_warns_when_a_trusted_proxy_range_is_too_broad(ranges: str, warned: bool, caplog) -> None:  # noqa: ANN001
    with caplog.at_level("WARNING", logger="app.config"):
        lan_config(trusted_proxy_ips=ranges).validate_runtime_security()
    assert ("TRUSTED_PROXY_IPS" in caplog.text) == warned
