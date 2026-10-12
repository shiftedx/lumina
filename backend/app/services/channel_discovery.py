from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from app.services.hls_relay_support import PUBLIC_PROVIDERS
from app.services.kick_public import is_kick_host, parse_kick_url
from app.services.popular_discovery import POPULAR_CATEGORIES, PopularItem, PopularSnapshot


# Availability values that mean a source cannot be played or acquired without
# extra rights; a channel is only suggested when it is followable "when known".
_UNFOLLOWABLE_AVAILABILITY = {"private", "unavailable", "needs_auth", "premium", "subscriber_only", "login_required"}

# YouTube host variants collapse to one canonical host so a channel reached by a
# pasted bare host, an m./music. host, and a category suggestion share identity.
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_CANONICAL_YOUTUBE_HOST = "www.youtube.com"

# A supported YouTube channel address is a handle or a legacy channel/c/user
# path — never a watch/playlist/search URL, which name media, not a creator.
_YOUTUBE_CHANNEL_PREFIXES = ("channel", "c", "user")
_YOUTUBE_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")

_CATEGORY_LABELS = {category.key: category.label for category in POPULAR_CATEGORIES}
_CATEGORY_ORDER = {category.key: index for index, category in enumerate(POPULAR_CATEGORIES)}


@dataclass(frozen=True)
class ChannelCandidate:
    """A followable channel presented identically across every discovery surface.

    ``channel_key`` is the durable, cross-surface identity (a normalized channel
    address). It is what a follow automation dedupes on and what "already
    following" recognition compares. ``display_name`` is the human channel name,
    which doubles as the follow automation's label and the basis for the
    casefolded key handed to #88's Home ranking seam.
    """

    channel_key: str
    source_url: str
    display_name: str
    source: str = "youtube"
    source_label: str = "YouTube"
    artwork_url: str | None = None
    category_keys: tuple[str, ...] = ()
    following: bool = False


@dataclass(frozen=True)
class CategoryChannelSuggestions:
    """A bounded "Popular in <label>" list, never presented as authoritative trending."""

    key: str
    label: str
    state: str  # "ranked" (from fresh discovery) | "curated" (fallback) | "empty"
    channels: tuple[ChannelCandidate, ...]


def channel_display_key(display_name: str | None) -> str:
    """Casefolded channel name — the exact key #88's Home seam matches uploaders on."""
    return (display_name or "").strip().casefold()


def normalize_channel_source_url(source_url: str | None) -> str | None:
    """Return one canonical channel address, or ``None`` if it is not usable.

    Canonicalizing the scheme, host, and trailing slash gives a single durable
    identity for a channel however it was reached — a manual search hit, a
    pasted address, a category suggestion, or an existing follow.
    """
    value = (source_url or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    if is_kick_host(value):
        # Any public Kick channel/VOD/clip link names one channel slug; odd forms are unusable.
        kick_ref = parse_kick_url(value)
        return f"https://kick.com/{kick_ref.channel}" if kick_ref else None
    try:
        parsed = urlsplit(value)
        # .port lazily parses and raises ValueError on a non-numeric port, so it
        # must stay inside the guard: a crafted address is unusable, not a crash.
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if not host or "." not in host:
        return None
    path = parsed.path.rstrip("/")
    query = parsed.query
    if host in _YOUTUBE_HOSTS:
        host = _CANONICAL_YOUTUBE_HOST
        segments = [segment for segment in path.split("/") if segment]
        # Handles are case-insensitive and channel tabs (/videos, /streams, ...)
        # name the same creator, so both collapse to one identity.
        if segments and segments[0].startswith("@") and len(segments[0]) > 1:
            path, query = "/" + segments[0].casefold(), ""
        elif len(segments) >= 2 and segments[0].casefold() in _YOUTUBE_CHANNEL_PREFIXES:
            path, query = f"/{segments[0].casefold()}/{segments[1]}", ""
    netloc = f"{host}:{port}" if port else host
    return urlunsplit(("https", netloc, path, query, ""))


def channel_feed_url(source_url: str) -> str:
    """The address whose entries are a channel's newest uploads.

    A bare YouTube channel address lists its tabs, not videos, so it is read
    through ``/videos``; every other address (a tab, Twitch, Kick) is used as is.
    """
    parsed = urlsplit(source_url)
    if (parsed.hostname or "").casefold() not in _YOUTUBE_HOSTS:
        return source_url
    segments = [segment for segment in parsed.path.split("/") if segment]
    is_handle = len(segments) == 1 and segments[0].startswith("@") and len(segments[0]) > 1
    is_legacy = len(segments) == 2 and segments[0].casefold() in _YOUTUBE_CHANNEL_PREFIXES
    return f"{source_url.rstrip('/')}/videos" if is_handle or is_legacy else source_url


def canonical_channel_identity(source_url: str | None, channel_id: str | None = None) -> str | None:
    """The durable follow identity: a YouTube channel id when known, else the normalized address.

    A channel id (``UC…``) never changes, while handles and vanity URLs can, so a
    search hit, a pasted handle resolved by the provider, and a category
    suggestion all converge on ``/channel/<id>`` whenever the id is available.
    """
    if isinstance(channel_id, str) and _YOUTUBE_CHANNEL_ID.fullmatch(channel_id):
        return f"https://{_CANONICAL_YOUTUBE_HOST}/channel/{channel_id}"
    return normalize_channel_source_url(source_url)


def _source_label(source: str) -> str:
    known = PUBLIC_PROVIDERS.get(source)
    return known.label if known else source.replace("_", " ").title() or "Source"


def _followable(availability: str | None) -> bool:
    return availability is None or availability.strip().casefold() not in _UNFOLLOWABLE_AVAILABILITY


class ChannelDiscoveryService:
    """Derive followable channel candidates with one stable identity everywhere.

    The service never persists a raw search query (ADR 0007): it turns provider
    results, pasted addresses, and the fresh category-discovery snapshot into
    channel candidates, and following one of them is the durable signal.
    """

    def __init__(
        self,
        *,
        per_category_limit: int = 6,
        curated: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    ) -> None:
        self._per_category_limit = per_category_limit
        self._curated = curated if curated is not None else CURATED_CHANNELS

    def suggestions_for_categories(
        self,
        snapshot: PopularSnapshot,
        category_keys: Iterable[str],
        *,
        followed_identities: frozenset[str] = frozenset(),
    ) -> list[CategoryChannelSuggestions]:
        requested = {key for key in category_keys if key in _CATEGORY_ORDER}
        ordered = sorted(requested, key=_CATEGORY_ORDER.__getitem__)
        states = {category.key: category.state for category in snapshot.categories}

        suggestions: list[CategoryChannelSuggestions] = []
        for key in ordered:
            channels: tuple[ChannelCandidate, ...] = ()
            state = "empty"
            if states.get(key) == "ready":
                channels = tuple(self._channels_from_snapshot(snapshot, key))
                if channels:
                    state = "ranked"
            if not channels:
                channels = tuple(self._curated_channels(key))
                state = "curated" if channels else "empty"
            channels = tuple(self._annotate(channel, followed_identities) for channel in channels)
            suggestions.append(
                CategoryChannelSuggestions(key=key, label=_CATEGORY_LABELS.get(key, key), state=state, channels=channels)
            )
        return suggestions

    def channels_from_search(
        self,
        results: Iterable[Mapping[str, object]],
        *,
        followed_identities: frozenset[str] = frozenset(),
    ) -> list[ChannelCandidate]:
        candidates = (self._candidate_from_raw(result) for result in results)
        deduped = _dedupe(candidate for candidate in candidates if candidate is not None)
        return [self._annotate(candidate, followed_identities) for candidate in deduped[: self._per_category_limit * 2]]

    def _channels_from_snapshot(self, snapshot: PopularSnapshot, category_key: str) -> list[ChannelCandidate]:
        candidates: list[ChannelCandidate] = []
        for item in snapshot.items:
            if category_key not in item.category_keys or not _followable(item.availability):
                continue
            candidate = self._candidate_from_item(item, category_key)
            if candidate is not None:
                candidates.append(candidate)
        return _dedupe(candidates)[: self._per_category_limit]

    def _curated_channels(self, category_key: str) -> list[ChannelCandidate]:
        entries = self._curated.get(category_key, ())
        candidates: list[ChannelCandidate] = []
        for display_name, source_url in entries:
            identity = normalize_channel_source_url(source_url)
            if identity is None or not display_name.strip():
                continue
            candidates.append(
                ChannelCandidate(
                    channel_key=identity, source_url=identity, display_name=display_name.strip(),
                    source="youtube", source_label="YouTube", artwork_url=None, category_keys=(category_key,),
                )
            )
        return _dedupe(candidates)[: self._per_category_limit]

    @staticmethod
    def _candidate_from_item(item: PopularItem, category_key: str) -> ChannelCandidate | None:
        identity = canonical_channel_identity(item.uploader_url, item.uploader_id)
        display_name = (item.uploader or "").strip()
        if identity is None or not display_name:
            return None
        return ChannelCandidate(
            channel_key=identity, source_url=identity, display_name=display_name,
            source=item.source or "youtube", source_label=item.source_label or "YouTube",
            # A category candidate is derived from video records, which carry no
            # channel avatar; the accessible initials fallback stands in.
            artwork_url=None, category_keys=(category_key,),
        )

    @staticmethod
    def _candidate_from_raw(raw: Mapping[str, object]) -> ChannelCandidate | None:
        availability = _first_str(raw, "availability")
        if availability and availability.strip().casefold() in _UNFOLLOWABLE_AVAILABILITY:
            return None
        source_url = _first_str(raw, "channel_url", "uploader_url", "source_url", "url", "webpage_url")
        identity = canonical_channel_identity(source_url, _first_str(raw, "channel_id"))
        display_name = (_first_str(raw, "uploader", "channel", "display_name", "title") or "").strip()
        if identity is None or not display_name:
            return None
        source = _first_str(raw, "source", "extractor_key", "extractor") or "youtube"
        return ChannelCandidate(
            channel_key=identity, source_url=identity, display_name=display_name, source=source,
            source_label=_source_label(source),
            artwork_url=_first_str(raw, "avatar_url", "channel_artwork_url", "artwork_url", "thumbnail"),
        )

    @staticmethod
    def _annotate(candidate: ChannelCandidate, followed_identities: frozenset[str]) -> ChannelCandidate:
        following = candidate.channel_key in followed_identities
        return candidate if following == candidate.following else replace(candidate, following=following)


def _dedupe(candidates: Iterable[ChannelCandidate]) -> list[ChannelCandidate]:
    """Collapse to one candidate per identity, keeping the first and merging detail."""
    merged: "OrderedDict[str, ChannelCandidate]" = OrderedDict()
    for candidate in candidates:
        existing = merged.get(candidate.channel_key)
        if existing is None:
            merged[candidate.channel_key] = candidate
            continue
        merged[candidate.channel_key] = replace(
            existing,
            artwork_url=existing.artwork_url or candidate.artwork_url,
            category_keys=tuple(dict.fromkeys(existing.category_keys + candidate.category_keys)),
            following=existing.following or candidate.following,
        )
    return list(merged.values())


def _first_str(raw: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# A small, deliberately non-authoritative curated set per interest category,
# used only when fresh discovery is cold, empty, stale, or failed. These are
# widely recognized public channels; the set is not an endorsement and is
# intentionally short so it reads as a starting point, not a ranking.
CURATED_CHANNELS: dict[str, tuple[tuple[str, str], ...]] = {
    "science-technology": (
        ("Veritasium", "https://www.youtube.com/@veritasium"),
        ("Kurzgesagt", "https://www.youtube.com/@kurzgesagt"),
        ("Marques Brownlee", "https://www.youtube.com/@mkbhd"),
    ),
    "education": (
        ("TED-Ed", "https://www.youtube.com/@TEDEd"),
        ("CrashCourse", "https://www.youtube.com/@crashcourse"),
        ("Khan Academy", "https://www.youtube.com/@khanacademy"),
    ),
    "music": (
        ("NPR Music", "https://www.youtube.com/@nprmusic"),
        ("COLORS", "https://www.youtube.com/@COLORSxSTUDIOS"),
    ),
    "gaming": (
        ("Game Maker's Toolkit", "https://www.youtube.com/@GMTK"),
        ("People Make Games", "https://www.youtube.com/@PeopleMakeGames"),
    ),
    "cooking": (
        ("Bon Appétit", "https://www.youtube.com/@bonappetit"),
        ("Adam Ragusea", "https://www.youtube.com/@aragusea"),
    ),
    "documentaries": (
        ("DW Documentary", "https://www.youtube.com/@DWDocumentary"),
        ("Real Stories", "https://www.youtube.com/@realstories"),
    ),
    "news": (
        ("AP Archive", "https://www.youtube.com/@APArchive"),
        ("PBS NewsHour", "https://www.youtube.com/@PBSNewsHour"),
    ),
    "history": (
        ("Kings and Generals", "https://www.youtube.com/@KingsandGenerals"),
        ("The History Guy", "https://www.youtube.com/@TheHistoryGuyChannel"),
    ),
    "nature": (
        ("BBC Earth", "https://www.youtube.com/@bbcearth"),
        ("National Geographic", "https://www.youtube.com/@NatGeo"),
    ),
    "comedy": (
        ("CollegeHumor", "https://www.youtube.com/@collegehumor"),
    ),
    "fitness": (
        ("Yoga With Adriene", "https://www.youtube.com/@yogawithadriene"),
    ),
    "travel": (
        ("Kara and Nate", "https://www.youtube.com/@karaandnate"),
    ),
}
