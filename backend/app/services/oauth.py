"""Optional OIDC login. Provider credentials and subject bindings are operator-owned.

Only state, nonce and PKCE verifiers live briefly in process memory. Tokens are
validated and discarded; the browser receives Lumina's existing session cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Literal
from urllib.parse import urlparse

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import ECKey, KeySet
from joserfc.jws import JWSRegistry
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.config import settings


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    issuer: str
    discovery_url: str | None = None
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"
    # Stable provider sub -> immutable Lumina user ID. Never link by email or username.
    subjects: dict[str, str] = Field(default_factory=dict)
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_provider(self) -> Provider:
        https_url(self.issuer)
        if urlparse(self.issuer).query or urlparse(self.issuer).params:
            raise ValueError("OIDC issuers cannot contain query parameters")
        if not self.client_secret.get_secret_value() and not self.apple_private_key:
            raise ValueError("An OIDC client secret or Apple signing key is required")
        if self.discovery_url:
            https_url(self.discovery_url)
        if any(not sub or len(sub) > 255 or not user for sub, user in self.subjects.items()):
            raise ValueError("OIDC subjects and member IDs must be nonempty; subjects must be at most 255 characters")
        apple_fields = (self.apple_team_id, self.apple_key_id, self.apple_private_key)
        if any(apple_fields) and (not self.apple or not all(apple_fields)):
            raise ValueError("Apple signing requires the Apple issuer, team ID, key ID and private key together")
        return self

    @property
    def github(self) -> bool:
        return self.issuer == "https://github.com"

    @property
    def apple(self) -> bool:
        return self.issuer == "https://appleid.apple.com"

    @property
    def fingerprint(self) -> str:
        # Secrets cannot change between start and callback either.
        data = self.model_dump_json() + self.client_secret.get_secret_value()
        if self.apple_private_key:
            data += self.apple_private_key.get_secret_value()
        return hashlib.sha256(data.encode()).hexdigest()


def https_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("OIDC URLs must use HTTPS without credentials or fragments")


def providers() -> list[Provider]:
    return [Provider.model_validate(value) for value in settings.oauth_providers]


def provider_for(provider_id: str) -> Provider | None:
    return next((provider for provider in providers() if provider.id == provider_id), None)


def callback_url(provider: Provider) -> str:
    return f"{settings.resolved_app_public_url}/api/session/oauth/{provider.id}/callback"


def browser_cookie(provider: Provider) -> str:
    prefix = "__Host-" if settings.remote_https_enabled else ""
    return f"{prefix}{settings.session_cookie_name}_oauth_{provider.id}"


@dataclass(frozen=True)
class Attempt:
    provider: str
    fingerprint: str
    binding: str
    nonce: str
    verifier: str
    remember: bool
    expires: float


ATTEMPT_SECONDS = 300
MAX_ATTEMPTS = 1024
_attempts: dict[str, Attempt] = {}
_lock = Lock()


def new_attempt(provider: Provider, remember: bool) -> tuple[str, str, Attempt]:
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    attempt = Attempt(provider.id, provider.fingerprint, hashlib.sha256(binding.encode()).hexdigest(),
                      secrets.token_urlsafe(32), secrets.token_urlsafe(48), remember, time.monotonic() + ATTEMPT_SECONDS)
    with _lock:
        for key, value in list(_attempts.items()):
            if value.expires <= time.monotonic():
                del _attempts[key]
        if len(_attempts) >= MAX_ATTEMPTS:
            raise ValueError("Too many pending OIDC sign-ins")
        _attempts[state] = attempt
    return state, binding, attempt


def consume(provider: Provider, state: str, binding: str) -> Attempt:
    with _lock:
        attempt = _attempts.get(state)
        if (attempt is None or attempt.expires <= time.monotonic() or attempt.provider != provider.id
                or attempt.fingerprint != provider.fingerprint
                or not hmac.compare_digest(attempt.binding, hashlib.sha256(binding.encode()).hexdigest())):
            raise ValueError("Invalid OIDC state")
        del _attempts[state]
    return attempt


def metadata(provider: Provider) -> dict:
    # Endpoints are explicitly trusted by the operator, including private IdPs.
    # Never follow redirects or permit plaintext endpoints discovered remotely.
    if provider.github:
        return {"authorization_endpoint": "https://github.com/login/oauth/authorize",
                "token_endpoint": "https://github.com/login/oauth/access_token"}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(provider.discovery_url or provider.issuer.rstrip("/") + "/.well-known/openid-configuration")
        response.raise_for_status()
        data = response.json()
    if data["issuer"] != provider.issuer:
        raise ValueError("OIDC discovery issuer mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        https_url(data[key])
    return data


def secret(provider: Provider) -> str:
    if not provider.apple_private_key:
        return provider.client_secret.get_secret_value()
    now = int(time.time())
    return jwt.encode({"alg": "ES256", "kid": provider.apple_key_id}, {
        "iss": provider.apple_team_id, "iat": now, "exp": now + 300,
        "aud": provider.issuer, "sub": provider.client_id,
    }, ECKey.import_key(provider.apple_private_key.get_secret_value()))


def client_for(provider: Provider) -> OAuth2Client:
    return OAuth2Client(provider.client_id, secret(provider), redirect_uri=callback_url(provider),
                        scope="read:user" if provider.github else "openid email" if provider.apple else "openid profile email",
                        token_endpoint_auth_method="client_secret_post" if provider.apple or provider.github else provider.token_endpoint_auth_method,
                        code_challenge_method=None if provider.apple else "S256",
                        timeout=10, follow_redirects=False, trust_env=False)


def authorization_url(provider: Provider, state: str, attempt: Attempt) -> str:
    data = metadata(provider)
    with client_for(provider) as client:
        url, _ = client.create_authorization_url(data["authorization_endpoint"], state=state,
            code_verifier=attempt.verifier,
            **({} if provider.github else {"nonce": attempt.nonce}),
            **({"response_mode": "form_post"} if provider.apple else {}))
    return url


def exchange(provider: Provider, attempt: Attempt, code: str) -> str:
    data = metadata(provider)
    with client_for(provider) as client:
        token = client.fetch_token(data["token_endpoint"], code=code,
                                   **({} if provider.apple else {"code_verifier": attempt.verifier}))
    if provider.github:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.get("https://api.github.com/user", headers={
                "Authorization": "Bearer " + token["access_token"], "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
            subject = response.json()["id"]
        if not isinstance(subject, int) or isinstance(subject, bool) or subject <= 0:
            raise ValueError("Invalid GitHub user ID")
        return str(subject)
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(data["jwks_uri"])
        response.raise_for_status()
        keys = KeySet.import_key_set(response.json())
    # Explicit asymmetric allowlist; provider metadata cannot enable none/HMAC.
    decoded = jwt.decode(token["id_token"], keys, registry=JWSRegistry(algorithms=["RS256", "ES256"]))
    claims = CodeIDToken(decoded.claims, decoded.header,
                        {"iss": {"essential": True, "value": provider.issuer},
                         "aud": {"essential": True, "values": [provider.client_id]}},
                        {"client_id": provider.client_id, "nonce": attempt.nonce, "access_token": token.get("access_token")})
    claims.validate(leeway=60)
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise ValueError("Invalid OIDC subject")
    return subject
