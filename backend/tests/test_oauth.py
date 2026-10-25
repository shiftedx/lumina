"""OAuth browser journeys through real signed tokens and mocked provider HTTP."""
from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey, ECKey

from app.config import EnvSettings, settings
from app.models import User
from app.services import oauth, two_factor
from test_v1_sessions import client, factory, login  # noqa: F401


@pytest.fixture
def provider(monkeypatch):
    config = {"id": "home", "name": "Household SSO", "issuer": "https://id.example.com",
              "client_id": "lumina-test", "client_secret": "test-only-secret", "subjects": {"subject-1": "u1"}}
    monkeypatch.setattr(settings, "oauth_providers", [config])
    oauth._attempts.clear()
    yield config
    oauth._attempts.clear()


@pytest.fixture
def idp(monkeypatch, provider):
    key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
    state = {"claims": {}, "requests": [], "key": key, "nonce": "", "discovery": {}, "error": False, "token": None}

    def handle(request):
        state["requests"].append(request)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": provider["issuer"], "authorization_endpoint": provider["issuer"].rstrip("/") + "/authorize",
                "token_endpoint": provider["issuer"].rstrip("/") + "/token", "jwks_uri": provider["issuer"].rstrip("/") + "/jwks", **state["discovery"]})
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [key.as_dict(private=False)]})
        if request.url.path.endswith("/token") or request.url.path == "/login/oauth/access_token":
            if state["error"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret-must-not-leak"})
            claims = {"iss": provider["issuer"], "aud": provider["client_id"], "sub": "subject-1",
                      "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": state["nonce"], **state["claims"]}
            return httpx.Response(200, json={"access_token": "test-access", "token_type": "Bearer",
                "id_token": state["token"] or jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, state["key"])})
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"id": 12345, "login": "not-used"})
        raise AssertionError(str(request.url))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: handle(request))
    return state


def begin(client, idp, remember=False):
    response = client.post("/api/session/oauth/home/start", json={"remember_on_device": remember})
    assert response.status_code == 200, response.text
    params = parse_qs(urlparse(response.json()["authorization_url"]).query)
    idp["nonce"] = params.get("nonce", [""])[0]
    return params


def finish(client, params):
    return client.get("/api/session/oauth/home/callback", params={"state": params["state"][0], "code": "test-code"}, follow_redirects=False)


def test_disabled_is_local_only(client, monkeypatch):
    monkeypatch.setattr(settings, "oauth_providers", [])
    assert client.get("/api/session/oauth/providers").json() == []
    assert client.post("/api/session/oauth/home/start", json={}).status_code == 404
    login(client)
    assert client.get("/api/session/me").status_code == 200


@pytest.mark.parametrize("issuer", ["https://id.example.com", "https://id.example.com/application/o/lumina/",
    "https://id.example.com/realms/household", "https://gitlab.com", "https://accounts.google.com"])
def test_oidc_journey_uses_pkce_and_normal_revocable_session(client, provider, idp, issuer):
    provider["issuer"] = issuer
    assert client.get("/api/session/oauth/providers").json() == [{"id": "home", "name": "Household SSO"}]
    params = begin(client, idp, remember=True)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["http://127.0.0.1:8765/api/session/oauth/home/callback"]
    binding = client.cookies.get(oauth.browser_cookie(oauth.provider_for("home")))
    response = finish(client, params)
    assert response.status_code == 303 and response.headers["location"].endswith("/")
    assert "no-store" in response.headers["cache-control"]
    me = client.get("/api/session/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == "u1"
    posted = parse_qs(next(r.content.decode() for r in idp["requests"] if r.method == "POST"))
    digest = base64.urlsafe_b64encode(hashlib.sha256(posted["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == [digest]
    assert binding not in repr(oauth._attempts)
    assert client.get("/api/session/device-members").json()[0]["user_id"] == "u1"
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert client.post("/api/session/logout", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 204
    assert client.get("/api/session/me").status_code == 401


@pytest.mark.parametrize("claims", [
    {"iss": "https://other.example.com"}, {"aud": "other-client", "azp": "lumina-test"},
    {"nonce": "wrong"}, {"nonce": None}, {"exp": 1}, {"iat": int(time.time()) + 3600},
    {"sub": "unmapped", "email": "local@example.com", "preferred_username": "local"},
])
def test_untrusted_identity_never_opens_session(client, provider, idp, claims):
    idp["claims"] = claims
    params = begin(client, idp)
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401


def test_wrong_signature_refused(client, provider, idp):
    idp["key"] = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
    assert finish(client, begin(client, idp)).headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401


def test_state_requires_same_browser_and_expires(client, provider, idp, monkeypatch):
    params = begin(client, idp)
    cookies = dict(client.cookies)
    client.cookies.clear()
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert not any(r.method == "POST" for r in idp["requests"])
    client.cookies.update(cookies)
    real_time = oauth.time.monotonic
    monkeypatch.setattr(oauth.time, "monotonic", lambda: real_time() + 301)
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401


def test_deactivated_and_reconfigured_identity_refused(client, factory, provider, idp):
    params = begin(client, idp)
    with factory.begin() as db:
        db.get(User, "u1").is_active = False
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401
    params = begin(client, idp)
    provider["client_id"] = "different"
    assert finish(client, params).headers["location"].endswith("#oauth-error")


def test_two_factor_remains_required(client, factory, provider, idp, monkeypatch):
    monkeypatch.setattr(two_factor, "enabled", lambda user: True)
    response = finish(client, begin(client, idp, remember=True))
    assert "#oauth-two-factor=" in response.headers["location"]
    assert client.get("/api/session/me").status_code == 401
    challenge = response.headers["location"].split("=", 1)[1]
    monkeypatch.setattr(two_factor, "verify", lambda *args, **kwargs: True)
    response = client.post("/api/session/two-factor", json={"challenge": challenge, "code": "123456"})
    assert response.status_code == 200, response.text
    assert client.get("/api/session/me").status_code == 200


def test_discovery_and_token_errors_are_safe(client, provider, idp):
    idp["discovery"] = {"issuer": "https://wrong.example.com"}
    response = client.post("/api/session/oauth/home/start", json={})
    assert response.status_code == 503 and "wrong.example" not in response.text
    idp["discovery"] = {}
    params = begin(client, idp)
    idp["error"] = True
    response = finish(client, params)
    assert response.headers["location"].endswith("#oauth-error")
    assert "secret-must-not-leak" not in response.text


def test_start_origin_and_throttling(client, provider, idp):
    assert client.post("/api/session/oauth/home/start", json={}, headers={"Origin": "https://other.example.com"}).status_code == 403
    for _ in range(10):
        assert client.post("/api/session/oauth/home/start", json={}).status_code == 200
    assert client.post("/api/session/oauth/home/start", json={}).status_code == 429


def test_apple_form_post_browser_binding_and_rotating_secret(client, provider, idp, monkeypatch):
    provider.update(issuer="https://appleid.apple.com", apple_team_id="TESTTEAM", apple_key_id="TESTKEY",
                    apple_private_key=ECKey.generate_key("P-256").as_pem(private=True).decode())
    monkeypatch.setattr(settings, "app_public_url", "https://localhost")
    monkeypatch.setattr(settings, "frontend_public_url", "https://localhost")
    # TestClient must model the HTTPS browser for its Secure binding cookie.
    client.base_url = httpx.URL("https://localhost")
    params = begin(client, idp)
    assert oauth.browser_cookie(oauth.provider_for("home")).startswith("__Host-")
    assert params["response_mode"] == ["form_post"]
    assert "code_challenge" not in params
    response = client.post("/api/session/oauth/home/callback", data={"state": params["state"][0], "code": "test-code"},
                           headers={"Origin": "https://appleid.apple.com"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "https://localhost/"
    posted = parse_qs(next(r.content.decode() for r in idp["requests"] if r.method == "POST"))
    apple_key = ECKey.import_key(provider["apple_private_key"])
    claims = jwt.decode(posted["client_secret"][0], apple_key).claims
    assert claims["iss"] == "TESTTEAM" and claims["sub"] == provider["client_id"]
    assert claims["exp"] - claims["iat"] == 300
    client.cookies.clear()
    response = client.post("/api/session/oauth/home/callback", data={"state": params["state"][0], "code": "test-code"},
                           headers={"Origin": "https://appleid.apple.com"}, follow_redirects=False)
    assert response.headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401


def test_github_uses_stable_numeric_id(client, provider, idp):
    provider.update(issuer="https://github.com", subjects={"12345": "u1"})
    params = begin(client, idp)
    assert finish(client, params).headers["location"].endswith("/")
    assert client.get("/api/session/me").json()["user"]["id"] == "u1"
    assert not any("well-known" in str(r.url) for r in idp["requests"])


def test_invalid_provider_configuration_fails_closed():
    with pytest.raises(ValueError):
        EnvSettings(oauth_providers=[{"id": "bad"}]).validate_runtime_security()


def test_unsigned_id_token_and_provider_cancel_are_refused(client, provider, idp):
    import json
    claims = {"iss": provider["issuer"], "aud": provider["client_id"], "sub": "subject-1",
              "exp": int(time.time()) + 300, "iat": int(time.time())}
    params = begin(client, idp)
    claims["nonce"] = idp["nonce"]
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    idp["token"] = encode({"alg": "none"}) + "." + encode(claims) + "."
    assert finish(client, params).headers["location"].endswith("#oauth-error")
    assert client.get("/api/session/me").status_code == 401
    params = begin(client, idp)
    response = client.get("/api/session/oauth/home/callback", params={"state": params["state"][0], "error": "access_denied"}, follow_redirects=False)
    assert response.headers["location"].endswith("#oauth-error")
    assert params["state"][0] not in oauth._attempts


def test_non_apple_callback_cannot_bypass_origin_checks(client, provider, idp):
    params = begin(client, idp)
    response = client.post("/api/session/oauth/home/callback", data={"state": params["state"][0], "code": "test"},
                           headers={"Origin": "https://appleid.apple.com"})
    assert response.status_code == 403
    assert not any(request.method == "POST" for request in idp["requests"])


def test_provider_validation_rejects_duplicates_and_insecure_endpoints(provider, idp, client):
    with pytest.raises(ValueError, match="unique"):
        EnvSettings(oauth_providers=[provider, provider]).validate_runtime_security()
    with pytest.raises(ValueError, match="HTTPS"):
        oauth.Provider.model_validate({**provider, "issuer": "http://id.example.com"})
    with pytest.raises(ValueError, match="together"):
        oauth.Provider.model_validate({**provider, "apple_team_id": "TESTTEAM"})
    idp["discovery"] = {"token_endpoint": "http://id.example.com/token"}
    assert client.post("/api/session/oauth/home/start", json={}).status_code == 503
