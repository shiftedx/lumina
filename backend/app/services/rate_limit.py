from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address, ip_address
from threading import RLock
from time import monotonic

from fastapi import HTTPException, Request

from app.config import settings
from app.services import public_address


@dataclass(frozen=True)
class RateLimitRule:
    max_requests: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitEvent:
    bucket: str
    client_key: str
    user_id: str | None
    occurred_at: datetime
    retry_after_seconds: int


RATE_LIMIT_RULES: dict[str, RateLimitRule] = {
    "bootstrap_admin": RateLimitRule(max_requests=3, window_seconds=600),
    "session_login": RateLimitRule(max_requests=10, window_seconds=300),
    # Per-username failures across all IPs. Full = that account is locked on the public address (never on the LAN or
    # loopback) until the window drains or an owner unlocks it; a successful sign-in clears it.
    "session_login_username": RateLimitRule(max_requests=10, window_seconds=900),
    # "Who's watching?" listing (app polish 6.2): reads only this browser's ring, so a generous per-client cap.
    "device_members": RateLimitRule(max_requests=60, window_seconds=60),
    # Public sign-in showcase (no session): the slide list is cached for 6 h; images are ~24 per visit, browser-cached a day.
    "showcase": RateLimitRule(max_requests=60, window_seconds=60),
    "showcase_art": RateLimitRule(max_requests=120, window_seconds=60),
    "account_token_redeem": RateLimitRule(max_requests=10, window_seconds=300),
    "preview": RateLimitRule(max_requests=20, window_seconds=60),
    # Intent prefetch: a card hovered 300 ms; the prefetcher also dedupes and caps running extractions.
    "remote_prefetch": RateLimitRule(max_requests=30, window_seconds=60),
    # Channel pages and address resolution: mostly cache hits, so they get their own bucket and never starve /api/preview.
    "channel_page": RateLimitRule(max_requests=60, window_seconds=60),
    "remote_stream_refresh": RateLimitRule(max_requests=20, window_seconds=60),
    "remote_stream_register": RateLimitRule(max_requests=20, window_seconds=60),
    "remote_stream_package": RateLimitRule(max_requests=6, window_seconds=60),
    "remote_stream_content": RateLimitRule(max_requests=240, window_seconds=60),
    "remote_stream_select": RateLimitRule(max_requests=30, window_seconds=60),
    "remote_playback_mutation": RateLimitRule(max_requests=60, window_seconds=60),
    # A replay-chat load triggers a coordinated yt-dlp subtitle download, so it
    # is bounded like preview rather than like a cheap read.
    "chat_replay_load": RateLimitRule(max_requests=12, window_seconds=60),
    "youtube_search": RateLimitRule(max_requests=30, window_seconds=60),
    "source_search": RateLimitRule(max_requests=30, window_seconds=60),
    # Up Next fires a real provider search on every video open. It is metered at
    # the same ceiling as the explicit search buckets to bound provider
    # amplification, but on its own bucket so it neither drains nor is drained by
    # the search box or Home's looser popular budget.
    "up_next": RateLimitRule(max_requests=30, window_seconds=60),
    # "artwork" bounds upstream fetch amplification and is charged only when a
    # request would trigger a cold provider fetch; "artwork_serve" is the cheap
    # ceiling for serving warm cache hits and 304 revalidations.
    "artwork": RateLimitRule(max_requests=120, window_seconds=60),
    "artwork_serve": RateLimitRule(max_requests=600, window_seconds=60),
    # Editor artwork uploads: each one runs an ffmpeg re-encode; per editor.
    "image_upload": RateLimitRule(max_requests=30, window_seconds=3600),
    # A TMDB round trip per preview; people typeahead is cheap but per keystroke burst.
    "metadata_preview": RateLimitRule(max_requests=6, window_seconds=60),
    "metadata_people": RateLimitRule(max_requests=120, window_seconds=60),
    "popular": RateLimitRule(max_requests=60, window_seconds=60),
    # The client flushes every 30 s and on pagehide, so 12 a minute is generous; per member, not per address.
    "reco_events": RateLimitRule(max_requests=12, window_seconds=60),
    "create_job": RateLimitRule(max_requests=20, window_seconds=60),
    "source_automation_run": RateLimitRule(max_requests=6, window_seconds=60),
    "webhook_test": RateLimitRule(max_requests=5, window_seconds=300),
    # Each call signs in to the admin's Jellyfin with a member-typed password: bound guessing through Lumina.
    "jellyfin_import": RateLimitRule(max_requests=10, window_seconds=300),
    # Requests: each create may call TMDB and Sonarr/Radarr; generous for a member clicking through a catalog.
    "media_request": RateLimitRule(max_requests=30, window_seconds=60),
    # Member-started AI/ASR/subtitle jobs share household workers; per member only (security.MEMBER_JOB), so one
    # invitee cannot keep them busy all day. A repeat of a running or finished job is cheap but still charged.
    "member_job": RateLimitRule(max_requests=30, window_seconds=3600),
}
_MAX_WINDOW_SECONDS = max(rule.window_seconds for rule in RATE_LIMIT_RULES.values())
_SWEEP_INTERVAL_SECONDS = 60.0


def _canonical_address(value: str) -> str:
    try:
        address = ip_address(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid client forwarding address.") from exc
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


def _peer_is_trusted_proxy(peer: IPv4Address | IPv6Address) -> bool:
    return any(peer in network for network in settings.trusted_proxy_networks if peer.version == network.version)


def _forwarded_hop(forwarded_for: str) -> str:
    """A chain (Cloudflare edge -> cloudflared -> Traefik) appends one hop per proxy. Walk from the right and
    take the first address that is not itself a trusted proxy: entries left of it are client-supplied."""
    for raw in reversed(forwarded_for.split(",")):
        hop = _canonical_address(raw)
        if not _peer_is_trusted_proxy(ip_address(hop)):
            return hop
    return hop


def _tunnel_visitor(request: Request, peer: IPv4Address | IPv6Address) -> str | None:
    """Cloudflare's visitor header, when this request is one the tunnel carries to the public address's host."""
    visitor = request.headers.get("cf-connecting-ip")
    if visitor is None or not _peer_is_trusted_proxy(peer) or not public_address.host() or request.url.hostname != public_address.host():
        return None
    if not visitor.strip() or "," in visitor:
        raise HTTPException(status_code=400, detail="Invalid client forwarding address.")
    return _canonical_address(visitor)


def grant_address(request: Request) -> str | None:
    """The address a grant (stream, image) is keyed on: one the requester cannot choose. Behind the tunnel the visitor
    header is client-supplied, so it only counts together with the hop that actually reached the proxy."""
    try:
        peer = ip_address((request.client.host if request.client else "").strip())
    except ValueError:
        return resolve_client_key(request)
    try:
        visitor = _tunnel_visitor(request, peer)
        if visitor is None:
            return resolve_client_key(request)
        forwarded_for = request.headers.get("x-forwarded-for")
        return f"{visitor}|{_forwarded_hop(forwarded_for) if forwarded_for is not None else peer}"
    except HTTPException:
        return None


def resolve_client_key(request: Request | None) -> str:
    if request is None:
        return "test-client"
    if request.client and request.client.host:
        peer_value = request.client.host.strip()
        try:
            peer = ip_address(peer_value)
        except ValueError:
            return "unknown"
        forwarded_for = request.headers.get("x-forwarded-for")
        # Cloudflare Tunnel: the proxy's X-Forwarded-For is the tunnel host for every visitor, so the public address is
        # keyed by Cloudflare's visitor header. A LAN host could forge it, which only moves IP buckets; account lockout
        # and grants (grant_address) do not depend on it alone.
        if (visitor := _tunnel_visitor(request, peer)) is not None:
            return visitor
        if _peer_is_trusted_proxy(peer) and forwarded_for is not None:
            return _forwarded_hop(forwarded_for)
        if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
            return str(peer.ipv4_mapped)
        return str(peer)
    return "unknown"


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = RLock()
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._audit_log: deque[RateLimitEvent] = deque(maxlen=100)
        self._next_sweep = 0.0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._next_sweep = 0.0
            self._audit_log.clear()

    def recent_events(self, limit: int = 20) -> list[RateLimitEvent]:
        with self._lock:
            return list(self._audit_log)[-limit:][::-1]

    def check(
        self, bucket: str, client_key: str, *, now: float | None = None, user_id: str | None = None, record: bool = True
    ) -> None:
        """Raise 429 when the bucket is full; otherwise charge one event (``record=False`` only peeks)."""
        rule = RATE_LIMIT_RULES[bucket]
        current = monotonic() if now is None else now
        window_start = current - rule.window_seconds
        key = (bucket, client_key)
        with self._lock:
            if current >= self._next_sweep:
                self._evict_stale(current)
            history = self._events.setdefault(key, deque()) if record else self._events.get(key, deque())
            while history and history[0] <= window_start:
                history.popleft()
            if len(history) >= rule.max_requests:
                retry_after = max(1, int(history[0] + rule.window_seconds - current))
                self._audit_log.append(
                    RateLimitEvent(
                        bucket=bucket,
                        client_key=client_key,
                        user_id=user_id,
                        occurred_at=datetime.now(UTC),
                        retry_after_seconds=retry_after,
                    )
                )
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please slow down and try again shortly.",
                    headers={"Retry-After": str(retry_after)},
                )
            if record:
                history.append(current)

    def is_full(self, bucket: str, client_key: str) -> bool:
        """Peek without charging or auditing (an owner's member list)."""
        rule = RATE_LIMIT_RULES[bucket]
        horizon = monotonic() - rule.window_seconds
        with self._lock:
            return sum(1 for at in self._events.get((bucket, client_key), ()) if at > horizon) >= rule.max_requests

    def reset(self, bucket: str, client_key: str) -> None:
        with self._lock:
            self._events.pop((bucket, client_key), None)

    def _evict_stale(self, current: float) -> None:
        # Drop keys with no event inside the longest window so rotating client
        # keys (e.g. IPv6 privacy addresses) cannot grow memory without bound.
        horizon = current - _MAX_WINDOW_SECONDS
        for key in [key for key, history in self._events.items() if not history or history[-1] <= horizon]:
            del self._events[key]
        self._next_sweep = current + _SWEEP_INTERVAL_SECONDS


rate_limiter = InMemoryRateLimiter()


def rate_limit_dependency(bucket: str):
    def dependency(request: Request) -> None:
        rate_limiter.check(bucket, resolve_client_key(request))

    return dependency


def enforce_rate_limit(bucket: str, request: Request, *, user_id: str | None = None) -> None:
    client_key = resolve_client_key(request)
    rate_limiter.check(bucket, client_key, user_id=user_id)
    if user_id:
        rate_limiter.check(bucket, f"user:{user_id}", user_id=user_id)
