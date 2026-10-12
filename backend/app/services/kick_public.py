"""Canonical public Kick identity over yt-dlp's pinned Kick extractors.

Resolution is anonymous. yt-dlp's Kick extractors only add an ``Authorization``
header when the cookie jar holds a Kick ``session_token``; Lumina never loads
cookies (see ``PUBLIC_OPTION_ALLOWLIST``), so no account or client-credential
route is ever used. yt-dlp asks for curl_cffi impersonation on the Kick API; with
none installed it falls back to plain requests through Lumina's guarded network
handler (never bypassed). Media then flows only through the guarded HLS relay
(``hls_relay_support``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import parse_qs, urlsplit

KICK_HOSTS = frozenset({"kick.com", "www.kick.com"})
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}")
_VOD_ID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
_CLIP_ID = re.compile(r"clip_[A-Za-z0-9_-]{1,64}")
# First path segments yt-dlp's Kick live extractor refuses: they are site pages, not channels.
_RESERVED = frozenset({"video", "categories", "search", "auth"})
_KIND_BY_EXTRACTOR = {"kick": "channel", "kickvod": "vod", "kickclip": "clip"}

KICK_UNSUPPORTED_LINK = "Lumina resolves public Kick channel, video and clip links only."


@dataclass(frozen=True)
class KickRef:
    kind: Literal["channel", "vod", "clip"]
    channel: str
    id: str

    @property
    def url(self) -> str:
        if self.kind == "vod":
            return f"https://kick.com/{self.channel}/videos/{self.id}"
        if self.kind == "clip":
            return f"https://kick.com/{self.channel}/clips/{self.id}"
        return f"https://kick.com/{self.channel}"


def is_kick_host(url: str) -> bool:
    try:
        return (urlsplit(url.strip()).hostname or "").lower() in KICK_HOSTS
    except ValueError:
        return False


def parse_kick_url(url: str) -> KickRef | None:
    """Recognize the exact public Kick link forms; lookalikes and odd forms are None."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme not in {"http", "https"}
        or (parts.hostname or "").lower() not in KICK_HOSTS
        or port is not None
        or parts.username is not None
    ):
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or not _SLUG.fullmatch(segments[0]) or segments[0].lower() in _RESERVED:
        return None
    channel = segments[0].lower()
    if len(segments) == 1:
        clip = parse_qs(parts.query).get("clip", [None])[0]
        if clip is None:
            return KickRef("channel", channel, channel)
        return KickRef("clip", channel, clip) if _CLIP_ID.fullmatch(clip) else None
    if len(segments) == 3 and segments[1] == "videos" and _VOD_ID.fullmatch(segments[2]):
        return KickRef("vod", channel, segments[2])
    if len(segments) == 3 and segments[1] == "clips" and _CLIP_ID.fullmatch(segments[2]):
        return KickRef("clip", channel, segments[2])
    return None


def kick_ref_from_info(info: Mapping[str, Any]) -> KickRef | None:
    """Canonical identity from a resolved Kick info dict (stable ids, never titles)."""
    extractor = info.get("extractor_key")
    kind = _KIND_BY_EXTRACTOR.get(extractor.lower()) if isinstance(extractor, str) else None
    channel = info.get("channel")
    if kind is None or not isinstance(channel, str) or not _SLUG.fullmatch(channel):
        return None
    channel = channel.lower()
    if kind == "channel":
        return KickRef("channel", channel, channel)
    media_id = info.get("id")
    pattern = _VOD_ID if kind == "vod" else _CLIP_ID
    if not isinstance(media_id, str) or not pattern.fullmatch(media_id):
        return None
    return KickRef(kind, channel, media_id)
