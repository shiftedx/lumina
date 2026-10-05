from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from starlette.requests import Request

from app.config import EnvSettings, settings
from app.schemas import UserCreateRequest
from app.security import hash_password
from app.services.rate_limit import resolve_client_key
from app.services.users import UserService
from support import make_user, memory_session_factory


def make_session():
    return memory_session_factory()()


def test_bootstrap_rejects_weak_password_without_reflecting_it() -> None:
    session = make_session()
    submitted = "short"

    with pytest.raises(ValueError) as exc_info:
        UserService(session).create_initial_admin("owner", submitted, "Owner")

    assert "at least 12 characters" in str(exc_info.value)
    assert submitted not in str(exc_info.value)
    assert UserService(session).needs_bootstrap() is True


def test_password_cannot_contain_short_username() -> None:
    session = make_session()

    with pytest.raises(ValueError, match="must not contain"):
        UserService(session).create_initial_admin("ab", "ab Strong Password 42!", "Owner")


def test_account_creation_and_password_changes_share_password_policy() -> None:
    session = make_session()
    existing = make_user("owner-1", role="admin", display_name="Owner", username="owner", password_hash=hash_password("Strong test pass 42!"))
    session.add(existing)
    session.commit()
    service = UserService(session)
    original_hash = existing.password_hash

    with pytest.raises(ValueError, match="at least 12 characters"):
        service.create_user(UserCreateRequest(username="viewer", password="short"))
    with pytest.raises(ValueError, match="at least 12 characters"):
        service.change_password(existing, "Strong test pass 42!", "short")

    assert existing.password_hash == original_hash


def make_request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = [] if forwarded_for is None else [(b"x-forwarded-for", forwarded_for.encode("ascii"))]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/session/login",
            "raw_path": b"/api/session/login",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("127.0.0.1", 8765),
            "scheme": "http",
        }
    )


def test_direct_client_cannot_spoof_forwarded_address(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_ips", "127.0.0.1")

    assert resolve_client_key(make_request("198.51.100.20", "203.0.113.7")) == "198.51.100.20"


def test_single_trusted_proxy_supplies_canonical_client_address(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_ips", "127.0.0.1")

    assert resolve_client_key(make_request("127.0.0.1", "2001:0db8:0:0::5")) == "2001:db8::5"


def test_trusted_proxy_rejects_malformed_forwarding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_ips", "127.0.0.1")

    for value in ("not-an-ip", "", "203.0.113.7, ", "203.0.113.7,,127.0.0.1"):
        with pytest.raises(HTTPException) as exc_info:
            resolve_client_key(make_request("127.0.0.1", value))
        assert exc_info.value.status_code == 400


def test_proxy_chain_uses_rightmost_untrusted_hop(monkeypatch) -> None:
    """Cloudflare edge -> cloudflared -> Traefik: each hop appends; the client can only prepend."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", "172.18.0.0/16")

    # Traefik (peer) appended cloudflared (trusted); the hop before it is the real client.
    assert resolve_client_key(make_request("172.18.0.2", "203.0.113.7, 172.18.0.5")) == "203.0.113.7"
    # A client-forged prefix (even garbage) left of the real client is never used.
    assert resolve_client_key(make_request("172.18.0.2", "198.51.100.1, 203.0.113.7, 172.18.0.5")) == "203.0.113.7"
    assert resolve_client_key(make_request("172.18.0.2", "not-an-ip, 203.0.113.7")) == "203.0.113.7"
    # Two clients behind the same tunnel get separate buckets, not the tunnel's.
    assert resolve_client_key(make_request("172.18.0.2", "198.51.100.9, 172.18.0.5")) == "198.51.100.9"
    # A chain made only of trusted proxies falls back to the leftmost (the origin hop).
    assert resolve_client_key(make_request("172.18.0.2", "172.18.0.9, 172.18.0.5")) == "172.18.0.9"


def test_ipv4_mapped_peer_uses_stable_ipv4_bucket(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_ips", "")

    assert resolve_client_key(make_request("::ffff:192.0.2.9")) == "192.0.2.9"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_public_url": "https://vault.example.test"}, "Secure session cookies"),
        (
            {"app_public_url": "https://vault.example.test", "session_cookie_secure": True},
            "trusted proxy",
        ),
        ({"session_cookie_secure": True}, "HTTPS public URL"),
        ({"trusted_proxy_ips": "127.0.0.1"}, "HTTPS public URL"),
        ({"app_public_url": "http://vault.example.test"}, "must use HTTPS"),
        (
            {
                "app_public_url": "https://vault.example.test",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "172.30.0.0/24",
            },
            "exactly one proxy IP",
        ),
        (
            {
                "app_public_url": "https://vault.example.test",
                "frontend_public_url": "https://other.example.test",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "127.0.0.1",
            },
            "same public URL",
        ),
        (
            {
                "app_public_url": "https://vault.example.test",
                "allowed_origins": "https://vault.example.test,https://evil.example.test",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "127.0.0.1",
            },
            "only the public URL",
        ),
        (
            {
                "app_public_url": "https://vault.example.test/path",
                "frontend_public_url": "https://vault.example.test/path",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "127.0.0.1",
            },
            "canonical origin",
        ),
        (
            {
                "app_public_url": "https://192.0.2.1",
                "frontend_public_url": "https://192.0.2.1",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "127.0.0.1",
            },
            "bare DNS hostname",
        ),
        (
            {
                "app_public_url": "https://Vault.Example.test",
                "frontend_public_url": "https://Vault.Example.test",
                "session_cookie_secure": True,
                "trusted_proxy_ips": "127.0.0.1",
            },
            "lowercase canonical origin",
        ),
    ],
)
def test_unsafe_remote_authentication_configuration_fails_closed(overrides, message) -> None:  # noqa: ANN001
    config = EnvSettings(_env_file=None, **overrides)

    with pytest.raises(ValueError, match=message):
        config.validate_runtime_security()


def test_https_proxy_configuration_is_coherent() -> None:
    config = EnvSettings(
        _env_file=None,
        app_public_url="https://vault.example.test",
        frontend_public_url="https://vault.example.test",
        allowed_origins="https://vault.example.test",
        host="0.0.0.0",
        session_cookie_secure=True,
        trusted_proxy_ips="127.0.0.1/32",
    )

    config.validate_runtime_security()
    assert config.allowed_origins_list == ["https://vault.example.test"]
    assert config.trusted_hosts_list == ["127.0.0.1", "localhost", "vault.example.test"]


def test_remote_https_refuses_unclaimed_first_run() -> None:
    session = make_session()

    with pytest.raises(RuntimeError, match="setup over loopback"):
        UserService(session).require_remote_bootstrap_ready(True)

    UserService(session).require_remote_bootstrap_ready(False)


def test_bootstrap_http_rejection_is_clear_and_non_sensitive(api_client) -> None:  # noqa: ANN001
    client = api_client(base_url="http://localhost")
    submitted = "short"
    response = client.post(
        "/api/bootstrap/admin",
        json={"username": "owner", "display_name": "Owner", "password": submitted},
    )
    assert response.status_code == 422
    assert "at least 12 characters" in response.json()["detail"]
    assert submitted not in response.text
    assert client.get("/api/bootstrap/status").json() == {"needs_setup": True}


def test_https_deployment_sets_secure_session_cookie(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    user = make_user("owner-1", role="admin", display_name="Owner", username="owner", password_hash=hash_password("Strong test pass 42!"))
    with db_factory.begin() as db:
        db.add(user)
    client = api_client(base_url="http://localhost")
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    response = client.post(
        "/api/session/login",
        json={"username": "owner", "password": "Strong test pass 42!"},
    )
    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_entrypoints_leave_forwarding_trust_to_the_application() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    for relative_path in ("docker/entrypoint.sh", "scripts/run-backend.sh"):
        contents = (repository_root / relative_path).read_text(encoding="utf-8")
        assert "--no-proxy-headers" in contents
        assert "--no-access-log" in contents


def test_base_compose_restarts_and_health_checks_lumina() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repository_root / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["yt-dlp-ui"]

    assert service["restart"] == "unless-stopped"
    # The healthcheck lives in the image (Dockerfile HEALTHCHECK, asserted by test_v1_packaging).
    assert "healthcheck" not in service
