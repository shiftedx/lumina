"""Streaming limits: YouTube, Twitch, Kick, live and open search per member.

A blocked kind answers 403 ``streaming_blocked:<kind>``. ``followed_only`` keeps a member to the channels they follow:
surfaces that reach beyond the follows (search, Popular, Live walls, new follows) are refused for that kind, and a
source plays only when it or its channel is followed. Admins and unrestricted members have no access row: never gated.
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services import member_access
from app.services.channel_discovery import normalize_channel_source_url
from app.services.member_follows import MemberFollowService

_HOSTS = {"youtube.com": "youtube", "youtu.be": "youtube", "youtube-nocookie.com": "youtube", "twitch.tv": "twitch", "kick.com": "kick"}


_NOT_PROVIDERS = {"Kicker", "KickStarter"}  # yt-dlp extractors whose names only look like Kick's


@cache
def _provider_extractors() -> tuple[tuple[type, str], ...]:
    """yt-dlp's YouTube / Twitch / Kick extractors: they also accept mirrors (Invidious, Piped, yewtu.be, hooktube,
    youtubekids, youtube.googleapis.com, bare video ids, ``ytsearch:``)."""
    from yt_dlp.extractor import gen_extractor_classes

    kinds = {"youtube": "youtube", "twitch": "twitch", "kick": "kick"}
    return tuple(
        (ie, kind) for ie in gen_extractor_classes() if ie.ie_key() not in _NOT_PROVIDERS
        for prefix, kind in kinds.items() if ie.ie_key().lower().startswith(prefix)
    )


def extractor_kind(extractor: str | None) -> str | None:
    """youtube / twitch / kick for a yt-dlp extractor key or name (``Youtube``, ``TwitchStream``, ``twitch:stream`` …)."""
    name = (extractor or "").casefold()
    return next((kind for ie, kind in _provider_extractors() if name in (ie.ie_key().casefold(), ie.IE_NAME.casefold())), None)


def url_kind(url: str, extractor: str | None = None) -> str:
    """youtube / twitch / kick by host (subdomains too), by the yt-dlp extractor that accepts the address, or by the
    ``extractor`` an extraction resolved to; any other site is open search's."""
    if kind := extractor_kind(extractor):
        return kind
    host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").casefold()
    for domain, kind in _HOSTS.items():
        if host == domain or host.endswith(f".{domain}"):
            return kind
    return next((kind for ie, kind in _provider_extractors() if ie.suitable(url)), "open_search")


def _streaming(db: Session, user: User) -> dict | None:
    access = member_access.for_user(db, user)
    return None if access is None else access.streaming


def blocked_kinds(db: Session, user: User) -> list[str]:
    access = member_access.for_user(db, user)
    return [] if access is None else access.blocked_streaming


def _deny(kind: str) -> HTTPException:
    return HTTPException(status_code=403, detail=f"streaming_blocked:{kind}")


def check(db: Session, user: User, kind: str, *, beyond_follows: bool = False) -> None:
    """``beyond_follows``: the surface shows sources the member does not follow (search, walls, a new follow)."""
    streaming = _streaming(db, user)
    if streaming is None:
        return
    if not streaming.get(kind, True) or (beyond_follows and streaming.get("followed_only", False)):
        raise _deny(kind)


def check_url(
    db: Session, user: User, url: str, *, channels: Iterable[str | None] | None = None, live: bool = False, beyond_follows: bool = False,
    extractor: str | None = None,
) -> None:
    """Gate one source by its kind (and ``live``). Under followed_only the source address or one of ``channels`` (its
    channel addresses, once known) must be followed; ``channels=None`` defers that to a later call that knows them.
    ``extractor``: the yt-dlp extractor an extraction resolved to (a shortener or redirect that lands on a provider)."""
    streaming = _streaming(db, user)
    if streaming is None:
        return
    kind = url_kind(url, extractor)
    check(db, user, kind, beyond_follows=beyond_follows)
    if live:
        check(db, user, "live")
    if channels is not None and streaming.get("followed_only", False):
        followed = MemberFollowService(db, validate_url=str).existing_follow_identities(user)
        if not any(normalize_channel_source_url(address) in followed for address in (url, *channels) if address):
            raise _deny(kind)


def allows_url(db: Session, user: User, url: str, **kwargs) -> bool:  # noqa: ANN003
    try:
        check_url(db, user, url, **kwargs)
    except HTTPException:
        return False
    return True


def require_streaming(kind: str, *, beyond_follows: bool = True):  # noqa: ANN201
    """Route dependency: 403 ``streaming_blocked:<kind>`` for a member whose access blocks ``kind``."""
    def dependency(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> None:
        check(db, current_user, kind, beyond_follows=beyond_follows)
    return dependency


def can_download(db: Session, user: User | None) -> bool:
    """Member access ``can_download``: may this member save media to the vault (jobs, batches, recordings, auto-download)?"""
    access = member_access.for_user(db, user) if user is not None else None
    return access is None or access.can_download


def require_download(db: Session, user: User) -> None:
    if not can_download(db, user):
        raise HTTPException(status_code=403, detail="downloads_not_allowed")
