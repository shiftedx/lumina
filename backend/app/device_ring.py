"""The "Who's watching?" device ring.

The ring is the app sessions of members who chose to be remembered on one browser, held as bearer tokens in a single
HttpOnly cookie scoped to /api/session. It never creates a session: every entry was made by that member's own password
sign-in, and every session rule (absolute and idle expiry, revocation, deactivation) still decides whether it is valid.

A vault owner is never held as a bearer: their entry is a signed marker, "<user id>~<HMAC>", that
names them for the picker and authenticates nothing; only their password signs them in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from fastapi import Request, Response

from app.config import settings
from app.services import art_urls

RING_COOKIE_SUFFIX = "_ring"
RING_MAX = 6
RING_PATH = "/api/session"
_MAX_COOKIE_CHARS = 4096
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")  # secrets.token_urlsafe(48) is 64 characters and never contains "."
_MARKER = re.compile(r"^([A-Za-z0-9_-]{1,36})~[A-Za-z0-9_-]{22}$")  # "~" never occurs in a token


def owner_marker(user_id: str) -> str:
    """A vault owner's ring entry: the member id signed with the app secret (art_urls.secret), never a credential."""
    digest = hmac.new(art_urls.secret(), f"ring-owner:{user_id}".encode(), hashlib.sha256).digest()[:16]
    return f"{user_id}~{base64.urlsafe_b64encode(digest).rstrip(b'=').decode()}"


def marker_user_id(entry: str) -> str | None:
    """The member id of a genuine owner marker; None for a token or a forged marker."""
    match = _MARKER.match(entry)
    if match is None or not hmac.compare_digest(entry, owner_marker(match.group(1))):
        return None
    return match.group(1)


def ring_cookie_name() -> str:
    return f"{settings.session_cookie_name}{RING_COOKIE_SUFFIX}"


def parse_ring(value: str | None) -> list[str]:
    """Well-formed tokens and owner markers in cookie order, de-duplicated, at most the last RING_MAX. Anything else is
    ignored (a marker's signature is checked where it is used)."""
    if not value:
        return []
    tokens: list[str] = []
    parts = value.split(".")
    if len(value) > _MAX_COOKIE_CHARS:  # a segment cut by the cap is a fragment, never a token
        parts = value[:_MAX_COOKIE_CHARS].split(".")[:-1]
    for part in parts:
        if (_TOKEN.match(part) or _MARKER.match(part)) and part not in tokens:
            tokens.append(part)
    return tokens[-RING_MAX:]


def serialize_ring(tokens: list[str]) -> str:
    return ".".join(tokens[-RING_MAX:])


def append_token(tokens: list[str], token: str) -> list[str]:
    """Adds (or moves) a token to the newest end; the oldest drops out beyond RING_MAX."""
    return [*[existing for existing in tokens if existing != token], token][-RING_MAX:]


def drop_token(tokens: list[str], token: str) -> list[str]:
    return [existing for existing in tokens if existing != token]


def read_ring(request: Request) -> list[str]:
    return parse_ring(request.cookies.get(ring_cookie_name()))


def apply_ring_cookie(response: Response, tokens: list[str]) -> None:
    """Writes the ring with the session cookie's Secure/SameSite, or deletes it when empty."""
    if not tokens:
        response.delete_cookie(
            key=ring_cookie_name(), path=RING_PATH, secure=settings.session_cookie_secure,
            httponly=True, samesite=settings.session_cookie_samesite,
        )
        return
    response.set_cookie(
        key=ring_cookie_name(),
        value=serialize_ring(tokens),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_duration_hours * 3600,
        path=RING_PATH,
    )
