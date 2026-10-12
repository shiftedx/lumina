"""The public and local addresses: extra browser origins, plus HTTPS awareness behind a trusted proxy.

``X-Forwarded-Proto`` is believed only from a peer inside ``LUMINA_TRUSTED_PROXY_IPS``. LAN requests (plain http, no proxy)
behave exactly as before. The local address is a home-network DNS name (ADR 0001, local address amendment), LAN HTTP mode only.
"""
from __future__ import annotations

import logging
from ipaddress import ip_address
from urllib.parse import urlparse

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import DNS_HOST_PATTERN, settings

logger = logging.getLogger(__name__)
# Names that only resolve inside a home network: mDNS, the common router suffix, RFC 8375 and ICANN's private-use TLD.
LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal")
LOCAL_ADDRESS_HINT = "The local address must look like http://lumina.home.arpa: a home-network name ending in .local, .lan, .home.arpa or .internal, with no path."
_origin: str | None = None  # Per-process cache; set at startup and on save (single-process server)
_local: str | None = None


def normalize(value: str | None) -> str | None:
    """``https://host[:port]`` or None for blank. Raises ValueError for anything that is not a bare origin."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("The public address must look like https://lumina.example.com (no path, query or credentials).")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def set_origin(value: str | None) -> None:
    global _origin
    _origin = normalize(value)


def origin() -> str | None:
    return _origin


def normalize_local(value: str | None) -> str | None:
    """``http(s)://name.home.arpa[:port]`` or None for blank. Raises ValueError unless LAN HTTP mode is on and the name is a
    home-network DNS name (never an IP or wildcard) other than the public address's host."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return None
    if not settings.lan_http:
        raise ValueError("A local address needs LAN HTTP mode (LUMINA_LAN_HTTP=true).")
    parsed = urlparse(value)
    try:
        parsed.port  # noqa: B018 - raises ValueError for a port out of range
    except ValueError as exc:
        raise ValueError(LOCAL_ADDRESS_HINT) from exc
    name = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
        or not DNS_HOST_PATTERN.fullmatch(name) or not name.endswith(LAN_SUFFIXES)
    ):
        raise ValueError(LOCAL_ADDRESS_HINT)
    if name == host():
        raise ValueError("The local address must differ from the public address.")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def set_local_origin(value: str | None) -> None:
    """Applies a saved local address; one that no longer validates (LAN HTTP mode turned off) is ignored with a warning."""
    global _local
    try:
        _local = normalize_local(value)
    except ValueError as exc:
        logger.warning("Ignoring the saved local address: %s", exc)
        _local = None


def local_origin() -> str | None:
    return _local


def link_base() -> str:
    return _origin or _local or settings.resolved_frontend_public_url


def host() -> str | None:
    return urlparse(_origin).hostname if _origin else None


def local_host() -> str | None:
    return urlparse(_local).hostname if _local else None


def origin_for(hostname: str | None) -> str | None:
    """The configured address a request arrived on, by its Host: so a reply never tells a caller about the other one."""
    if hostname and hostname == host():
        return _origin
    if hostname and hostname == local_host():
        return _local
    return None


def _peer_trusted(peer: str | None) -> bool:
    try:
        address = ip_address((peer or "").strip())
    except ValueError:
        return False
    return any(address in net for net in settings.trusted_proxy_networks if address.version == net.version)


def arrived_over_https(scope: Scope) -> bool:
    peer = (scope.get("client") or (None,))[0]
    if not _peer_trusted(peer):
        return False
    return Headers(scope=scope).get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


class DynamicTrustedHostMiddleware(TrustedHostMiddleware):
    """TrustedHostMiddleware whose allow-list also holds the configured public and local addresses' hosts."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.allowed_hosts = sorted({*settings.trusted_hosts_list, *filter(None, (host(), local_host()))})
        await super().__call__(scope, receive, send)


class SecureCookieMiddleware:
    """Adds ``Secure`` to every cookie set for a request that arrived over HTTPS through a trusted proxy."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not arrived_over_https(scope):
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                cookies = headers.getlist("set-cookie")
                if cookies:
                    del headers["set-cookie"]
                    for cookie in cookies:
                        headers.append("set-cookie", cookie if "; secure" in cookie.lower() else cookie + "; Secure")
            await send(message)

        await self.app(scope, receive, secured)
