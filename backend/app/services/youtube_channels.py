"""YouTube channel pages, cached in memory.

One flat extraction of the channel root (the header) and one of a tab, the same request type a follow refresh already
makes. A fresh hit returns at once; a stale hit returns at once and schedules one background refresh; a miss waits up
to 15 s for a bounded 2-worker pool, and concurrent requests for one key share one extraction through its Future. A
timed-out wait never cancels the work, so the next visit finds it cached. Public channel data only: no member ids.
The cache is lost on restart, which is acceptable, and never exported.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import yt_dlp
from sqlalchemy.orm import Session

from app.models import SourceAutomation, User
from app.schemas import YouTubeSearchResult
from app.services.channel_discovery import normalize_channel_source_url
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

HEADER_FRESH_SECONDS, HEADER_STALE_SECONDS = 6 * 3600, 7 * 86400
TAB_FRESH_SECONDS, TAB_STALE_SECONDS = 30 * 60, 86400
RESOLVE_SECONDS = 7 * 86400
CACHE_SIZE, RESOLVE_CACHE_SIZE = 256, 512
COLD_WAIT_SECONDS = 15.0
WORKERS = 2


class ChannelUnavailable(Exception):
    """The extractor says the channel does not exist or was terminated (404 channel_unavailable)."""


class ChannelTimeout(Exception):
    """A cold miss outlived the request's wait; the extraction still completes into the cache (504)."""


@dataclass(frozen=True)
class _Slot:
    value: Any
    fetched_at: float


class ChannelPages:
    def __init__(
        self,
        load_header: Callable[[str], Any],
        load_tab: Callable[[str, str, int], Any],
        resolve_url: Callable[[str], tuple[str, Any]],
        *,
        clock: Callable[[], float] = time.time,
        executor: ThreadPoolExecutor | None = None,
        wait_seconds: float = COLD_WAIT_SECONDS,
    ) -> None:
        self._load_header = load_header
        self._load_tab = load_tab
        self._resolve_url = resolve_url
        self._clock = clock
        self._wait_seconds = wait_seconds
        self._executor = executor or ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="channel-pages")
        self._lock = threading.Lock()
        self._headers: OrderedDict[tuple, _Slot] = OrderedDict()
        self._tabs: OrderedDict[tuple, _Slot] = OrderedDict()
        self._resolved: OrderedDict[tuple, _Slot] = OrderedDict()
        self._inflight: dict[tuple, Future] = {}

    # ---- public ----
    def page(self, channel_id: str, tab: str, limit: int) -> tuple[Any, Any, float, bool]:
        """(header, tab page, the older fetch time, served stale). Raises ChannelTimeout or the loader's error."""
        header = self._lookup(self._headers, ("header", channel_id), HEADER_FRESH_SECONDS, HEADER_STALE_SECONDS, CACHE_SIZE,
                              lambda: self._load_header(channel_id))
        listing = self._lookup(self._tabs, ("tab", channel_id, tab, limit), TAB_FRESH_SECONDS, TAB_STALE_SECONDS, CACHE_SIZE,
                               lambda: self._load_tab(channel_id, tab, limit))
        self._await(channel_id, header, listing)
        (header_value, header_at, header_stale), (tab_value, tab_at, tab_stale) = self._settle(header), self._settle(listing)
        return header_value, tab_value, min(header_at, tab_at), header_stale or tab_stale

    def peek_tab(self, channel_id: str, tab: str, limit: int, max_age: float = TAB_STALE_SECONDS) -> Any | None:
        """A cached tab page no older than max_age (default: stale-servable) without loading; None otherwise."""
        with self._lock:
            slot = self._tabs.get(("tab", channel_id, tab, limit))
        return slot.value if slot and self._clock() - slot.fetched_at < max_age else None

    def resolve(self, url: str) -> str:
        """An @handle, /c/ or /user/ address to its UC… id; the answer is kept 7 days and warms the header cache."""
        found = self._lookup(self._resolved, ("resolve", url), RESOLVE_SECONDS, RESOLVE_SECONDS, RESOLVE_CACHE_SIZE,
                             lambda: self._resolve_and_warm(url))
        self._await(url, found)
        return self._settle(found)[0]

    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ---- internals ----
    def _resolve_and_warm(self, url: str) -> str:
        channel_id, header = self._resolve_url(url)
        with self._lock:
            self._store(self._headers, ("header", channel_id), _Slot(header, self._clock()), CACHE_SIZE)
        return channel_id

    def _lookup(self, cache: OrderedDict, key: tuple, fresh: float, stale: float, size: int, load: Callable[[], Any]) -> Future | tuple[_Slot, bool]:
        now = self._clock()
        with self._lock:
            slot = cache.get(key)
            if slot is not None and now - slot.fetched_at < stale:
                cache.move_to_end(key)
                if now - slot.fetched_at >= fresh:
                    self._start(cache, key, size, load)  # one background refresh; the stale value answers now
                    return slot, True
                return slot, False
            return self._start(cache, key, size, load)

    def _start(self, cache: OrderedDict, key: tuple, size: int, load: Callable[[], Any]) -> Future:
        """The caller holds the lock, so _run cannot pop the key before it is recorded."""
        future = self._inflight.get(key)
        if future is None:
            future = self._executor.submit(self._run, cache, key, size, load)
            self._inflight[key] = future
        return future

    def _run(self, cache: OrderedDict, key: tuple, size: int, load: Callable[[], Any]) -> _Slot:
        try:
            value = load()
        except BaseException:
            logger.debug("Channel page load failed for %s", key[1], exc_info=True)
            with self._lock:
                self._inflight.pop(key, None)
            raise
        slot = _Slot(value, self._clock())
        with self._lock:
            self._inflight.pop(key, None)
            self._store(cache, key, slot, size)
        return slot

    @staticmethod
    def _store(cache: OrderedDict, key: tuple, slot: _Slot, size: int) -> None:
        cache[key] = slot
        cache.move_to_end(key)
        while len(cache) > size:
            cache.popitem(last=False)

    def _await(self, label: str, *found: Future | tuple[_Slot, bool]) -> None:
        pending = [item for item in found if isinstance(item, Future)]
        if pending and wait(pending, timeout=self._wait_seconds).not_done:
            raise ChannelTimeout(label)

    @staticmethod
    def _settle(found: Future | tuple[_Slot, bool]) -> tuple[Any, float, bool]:
        if isinstance(found, Future):
            slot = found.result()  # re-raises the loader's error
            return slot.value, slot.fetched_at, False
        slot, stale = found
        return slot.value, slot.fetched_at, stale


# ---- parsing and the real loaders ----
CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
TABS = ("videos", "streams", "shorts", "playlists")
HEADER_ENTRIES = 10  # a channel root's flat entries are its tabs
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
DESCRIPTION_MAX = 5000
_NO_TAB = "does not have a"  # yt-dlp: "This channel does not have a shorts tab"


@dataclass(frozen=True)
class ChannelHeader:
    id: str
    name: str
    handle: str | None
    url: str
    avatar: str | None  # a provider URL: the route maps it through the artwork proxy
    banner: str | None
    follower_count: int | None
    video_count: int | None  # the flat root does not report it; kept for the wire shape
    description: str | None
    verified: bool
    tabs: tuple[str, ...]


@dataclass(frozen=True)
class ChannelTabPage:
    entries: tuple[YouTubeSearchResult, ...]
    has_more: bool
    restricted: bool = False


def channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def header_from_info(channel_id: str, info: dict[str, Any]) -> ChannelHeader:
    tabs: list[str] = []
    for entry in info.get("entries") or []:
        url = entry.get("url") if isinstance(entry, dict) else None
        tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] if isinstance(url, str) else ""
        if tail in TABS and tail not in tabs:
            tabs.append(tail)
    handle = info.get("uploader_id")
    description = info.get("description") if isinstance(info.get("description"), str) else None
    return ChannelHeader(
        id=channel_id,
        name=str(info.get("channel") or info.get("uploader") or channel_id),
        handle=handle if isinstance(handle, str) and handle.startswith("@") and len(handle) > 1 else None,
        url=channel_url(channel_id),
        avatar=YtDlpService.resolve_channel_avatar(info),
        banner=YtDlpService.resolve_channel_banner(info),
        follower_count=_count(info.get("channel_follower_count")),
        video_count=None,
        description=description[:DESCRIPTION_MAX] if description else None,
        verified=info.get("channel_is_verified") is True,
        tabs=tuple(tabs),
    )


def tab_from_info(tab: str, info: dict[str, Any], limit: int) -> ChannelTabPage:
    entries = [entry for entry in info.get("entries") or [] if isinstance(entry, dict)]
    items = []
    for entry in entries[:limit]:
        if tab == "streams":  # flat stream entries carry viewers as concurrent_view_count (as youtube_live_search)
            entry = {**entry, "view_count": entry.get("concurrent_view_count") or entry.get("view_count")}
        items.append(YtDlpService.normalize_search_result(entry, "youtube"))
    return ChannelTabPage(entries=tuple(items), has_more=len(entries) > limit)


def parse_channel_address(url: str) -> tuple[str, str] | None:
    """('id', UC…) answered locally, ('lookup', canonical address) needing one flat extraction, or None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme != "https" or (parts.hostname or "").lower() not in YOUTUBE_HOSTS or parts.username or parts.password:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    first = segments[0] if segments else ""
    if first == "channel" and len(segments) >= 2:
        return ("id", segments[1]) if CHANNEL_ID.fullmatch(segments[1]) else None
    if first.startswith("@") and len(first) > 1:
        return "lookup", f"https://www.youtube.com/{first}"
    if first in {"c", "user"} and len(segments) >= 2:
        return "lookup", f"https://www.youtube.com/{first}/{segments[1]}"
    return None


def _category(error: yt_dlp.utils.DownloadError) -> str | None:
    return YtDlpService.classify_download_error(str(error))


def channel_pages(extract: Callable[[str, int], dict[str, Any]]) -> ChannelPages:
    """ChannelPages over one flat extractor (`YtDlpService.extract_channel_listing` in the app, a fake in tests)."""

    def load_header(channel_id: str) -> ChannelHeader:
        try:
            info = extract(channel_url(channel_id), HEADER_ENTRIES)
        except yt_dlp.utils.DownloadError as exc:
            if _category(exc) == "removed":
                raise ChannelUnavailable(channel_id) from exc
            raise
        return header_from_info(channel_id, info)

    def load_tab(channel_id: str, tab: str, limit: int) -> ChannelTabPage:
        try:
            info = extract(f"{channel_url(channel_id)}/{tab}", limit + 1)  # one more than shown: has_more
        except yt_dlp.utils.DownloadError as exc:
            category = _category(exc)
            if category == "sign_in_required":
                return ChannelTabPage(entries=(), has_more=False, restricted=True)
            if _NO_TAB in str(exc).lower():
                return ChannelTabPage(entries=(), has_more=False)
            if category == "removed":
                raise ChannelUnavailable(channel_id) from exc
            raise
        return tab_from_info(tab, info, limit)

    def resolve_url(url: str) -> tuple[str, ChannelHeader]:
        try:
            info = extract(url, HEADER_ENTRIES)
        except yt_dlp.utils.DownloadError as exc:
            if _category(exc) == "removed":
                raise ChannelUnavailable(url) from exc
            raise
        channel_id = info.get("channel_id")
        if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id):
            raise ChannelUnavailable(url)
        return channel_id, header_from_info(channel_id, info)

    return ChannelPages(load_header, load_tab, resolve_url)


def channel_follow(db: Session, user: User, header: ChannelHeader) -> tuple[str, str] | None:
    """The member's follow of this channel, by its canonical /channel/ address or its @handle address.

    Returns (follow id, the follow's normalized address, the key the followed-live checker probes under). Computed per
    request after the cache, so the cache never holds member data."""
    wanted = {normalize_channel_source_url(header.url)}
    if header.handle:
        wanted.add(normalize_channel_source_url(f"https://www.youtube.com/{header.handle}"))
    rows = db.query(SourceAutomation.id, SourceAutomation.source_url).filter(
        SourceAutomation.user_id == user.id, SourceAutomation.source_type == "channel",
    ).order_by(SourceAutomation.created_at)
    for follow_id, source_url in rows:
        identity = normalize_channel_source_url(source_url)
        if identity and identity in wanted:
            return follow_id, identity
    return None
