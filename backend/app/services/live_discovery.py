"""Anonymous live-stream discovery: unified categories over YouTube + Twitch."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.schemas import YouTubeSearchResponse, YouTubeSearchResult
from app.services import provider_budget
from app.services.media_capabilities import derive_media_capabilities
from app.services.popular_discovery import PopularCategory, PopularDiscovery
from app.services.twitch_gql_directory import (
    MAX_PAGE,
    TwitchBudgetExhausted,
    TwitchDirectoryError,
    TwitchGqlDirectory,
    TwitchLiveStream,
)
from app.services.youtube_live_directory import youtube_live_directory

logger = logging.getLogger(__name__)

LIVE_CATEGORIES: tuple[PopularCategory, ...] = (
    PopularCategory("gaming", "Gaming", "gaming"),
    PopularCategory("music", "Music", "live music"),
    PopularCategory("news", "News", "news"),
    PopularCategory("sports", "Sports", "sports"),
    PopularCategory("creative", "Creative & IRL", "irl creative stream"),
    PopularCategory("other", "More Live", "live"),
)

# Twitch directory names per unified category. None -> top streams overall;
# empty tuple -> YouTube-only category (no good Twitch directory equivalent).
TWITCH_CATEGORY_SOURCES: Mapping[str, tuple[str, ...] | None] = {
    "gaming": None,
    "music": ("Music",),
    "news": (),
    "sports": ("Sports",),
    "creative": ("Just Chatting", "Art", "Food & Drink", "Travel & Outdoors", "ASMR"),
    "other": ("Talk Shows & Podcasts", "Special Events", "Science & Technology", "Software and Game Development"),
}
# Anonymous Twitch refuses every page after the first ("failed integrity check"), so depth comes from breadth: the gaming
# rail reads the first page of each of the busiest non-routed directories instead of paging one list.
_GAMING_DIRECTORIES = 40
_WALL_SOURCES_PER_PAGE = 3  # directory fetches one wall request may make
YOUTUBE_POOL_LIMIT = 400  # distinct live streams held per category from the background YouTube refresh
SNAPSHOT_CATEGORY_LIMIT = 60
# Each category's rows refresh every 10 minutes (3 YouTube requests + 1-41 Twitch calls); provider_budget caps the rest.
LIVE_REFRESH_SECONDS = 10 * 60

_CATEGORY_KEY_BY_QUERY = {category.query: category.key for category in LIVE_CATEGORIES}

# Directory names already routed to a specific rail. The gaming rail uses Twitch's
# overall top streams, which are chronically dominated by Just Chatting/music/IRL;
# excluding these keeps the rail purely gaming. Derived from the mapping so a new
# routed directory is covered automatically.
_ROUTED_DIRECTORY_NAMES: frozenset[str] = frozenset(
    name for sources in TWITCH_CATEGORY_SOURCES.values() if sources for name in sources
)


class TwitchHealth:
    """Latest-known availability of the anonymous Twitch directory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = True

    def record_success(self) -> None:
        with self._lock:
            self._available = True

    def record_failure(self) -> None:
        with self._lock:
            self._available = False

    @property
    def available(self) -> bool:
        with self._lock:
            return self._available


def twitch_stream_to_result(stream: TwitchLiveStream) -> YouTubeSearchResult:
    url = f"https://www.twitch.tv/{stream.login}"
    capabilities = derive_media_capabilities(
        {
            "extractor_key": "twitch",
            "is_live": True,
            "live_status": "is_live",
            "id": stream.login,
            "title": stream.title,
            "webpage_url": url,
        }
    )
    return YouTubeSearchResult(
        id=f"twitch:{stream.login}",
        title=stream.title,
        uploader=stream.display_name,
        uploader_url=url,
        uploader_id=stream.login,
        duration=None,
        thumbnail=stream.preview_image_url,
        webpage_url=url,
        view_count=stream.viewers_count,
        source="twitch",
        source_label="Twitch",
        capabilities=capabilities,
    )


def merge_live_entries(
    youtube_items: Sequence[YouTubeSearchResult],
    twitch_items: Sequence[YouTubeSearchResult],
) -> list[YouTubeSearchResult]:
    seen: set[str] = set()
    combined = []
    for item in (*youtube_items, *twitch_items):  # one stream can appear via search and the directory
        key = item.webpage_url or item.id
        if key not in seen:
            seen.add(key)
            combined.append(item)
    ranked = sorted(
        (item for item in combined if item.view_count is not None),
        key=lambda item: -(item.view_count or 0),
    )
    unranked = [item for item in combined if item.view_count is None]
    return [*ranked, *unranked]


YouTubeLiveDirectory = Callable[[str, int, "str | None"], "tuple[list[YouTubeSearchResult], str | None, int | None]"]


def _sum_known(counts: Sequence[int | None]) -> int | None:
    known = [count for count in counts if count is not None]
    return sum(known) if known else None


class GamingDirectories:
    """The busiest non-routed Twitch directories (name, live broadcasters), cached so a wall never re-asks per page."""

    def __init__(self, directory: TwitchGqlDirectory, *, ttl: float = LIVE_REFRESH_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._directory, self._ttl, self._clock = directory, ttl, clock
        self._games: list[tuple[str, int]] = []
        self._at: float | None = None
        self._lock = threading.Lock()

    def get(self) -> list[tuple[str, int]]:
        with self._lock:
            if self._at is None or self._clock() - self._at >= self._ttl:
                try:
                    self._games = [g for g in self._directory.top_games(100) if g[0] not in _ROUTED_DIRECTORY_NAMES]
                    self._at = self._clock()
                except TwitchDirectoryError:
                    if not self._games:
                        raise  # nothing cached to fall back on
            return list(self._games)


def _sources(category_key: str, gaming: GamingDirectories) -> list[str]:
    sources = TWITCH_CATEGORY_SOURCES.get(category_key, ())
    return [name for name, _ in gaming.get()[:_GAMING_DIRECTORIES]] if sources is None else list(sources)


class LiveSearch:
    """The PopularDiscovery search callback: one category query -> merged live rail (background refresh only).

    Also remembers each category's provider-reported live count (``counts``) and the full YouTube list (``youtube_pool``)
    so the live wall pages from memory and never calls YouTube on a request.
    """

    def __init__(
        self,
        youtube_live_search: Callable[[str, int], YouTubeSearchResponse],
        twitch_directory: TwitchGqlDirectory,
        *,
        health: TwitchHealth | None = None,
        youtube_directory: YouTubeLiveDirectory = youtube_live_directory,
        gaming: GamingDirectories | None = None,
    ) -> None:
        self._youtube_live_search = youtube_live_search
        self._twitch_directory = twitch_directory
        self._youtube_directory = youtube_directory
        self.gaming = gaming or GamingDirectories(twitch_directory)
        self.health = health or TwitchHealth()
        self._counts: dict[str, int | None] = {}
        self._pools: dict[str, list[YouTubeSearchResult]] = {}
        self._lock = threading.Lock()

    @property
    def counts(self) -> dict[str, int | None]:
        with self._lock:
            return dict(self._counts)

    def youtube_pool(self, category_key: str) -> list[YouTubeSearchResult]:
        with self._lock:
            return list(self._pools.get(category_key, ()))

    def __call__(self, query: str, limit: int) -> YouTubeSearchResponse:
        category_key = _CATEGORY_KEY_BY_QUERY.get(query, "other")
        youtube_items, youtube_error = self._youtube_items(query, category_key, limit)
        twitch_items, twitch_count, twitch_error = self._twitch_items(category_key, limit)
        if not youtube_items and not twitch_items:
            if twitch_error is not None:
                raise twitch_error  # carries Retry-After into the refresh back-off
            if youtube_error is not None:
                raise youtube_error
        # YouTube states no total, so its share is the distinct streams actually held; Twitch's is the directory totals.
        with self._lock:
            held = len(self._pools.get(category_key, ()))
            count = _sum_known([twitch_count, held or None])
            if count is not None or category_key not in self._counts:
                self._counts[category_key] = count  # a failed refresh keeps the last known count
        return YouTubeSearchResponse(query=query, items=merge_live_entries(youtube_items, twitch_items)[:limit])

    def _youtube_items(self, query: str, category_key: str, limit: int) -> tuple[list[YouTubeSearchResult], Exception | None]:
        items: list[YouTubeSearchResult] = []
        errors: list[Exception] = []
        try:  # the extraction seam charges the shared YouTube budget (BudgetExhausted when spent or paused)
            with provider_budget.priority("live"):
                items.extend(self._youtube_live_search(query, limit).items)
        except Exception as error:  # noqa: BLE001 - one provider must not sink the rail
            errors.append(error)
        try:
            pool = list(self._youtube_directory(category_key, YOUTUBE_POOL_LIMIT, None)[0])
            if pool:  # keep the last good list on an empty or failed refresh
                with self._lock:
                    self._pools[category_key] = pool
            items.extend(pool)
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        return items, (errors[0] if errors and not items else None)

    def _twitch_items(self, category_key: str, limit: int) -> tuple[list[YouTubeSearchResult], int | None, TwitchDirectoryError | None]:
        if TWITCH_CATEGORY_SOURCES.get(category_key, ()) == ():
            return [], None, None
        streams: list[TwitchLiveStream] = []
        counts: list[int | None] = []
        errors: list[TwitchDirectoryError] = []
        try:
            names = _sources(category_key, self.gaming)
            if TWITCH_CATEGORY_SOURCES[category_key] is None:
                counts.append(sum(count for _, count in self.gaming.get()))
        except TwitchDirectoryError as error:
            names, errors = [], [error]
        # One failing directory keeps the others' streams; health reports the failure so the surface labels Twitch partial.
        for name in names:
            try:
                page = self._twitch_directory.game_page(name, limit)
            except TwitchDirectoryError as error:
                errors.append(error)
                continue
            streams.extend(page.streams)
            if TWITCH_CATEGORY_SOURCES[category_key] is not None:
                counts.append(page.live_count)
        # A budget skip keeps the last rows and is not a Twitch outage the surface should announce.
        (self.health.record_failure if any(not isinstance(e, TwitchBudgetExhausted) for e in errors) else self.health.record_success)()
        streams.sort(key=lambda stream: -(stream.viewers_count or 0))
        error = errors[0] if errors and not streams else None
        return [twitch_stream_to_result(stream) for stream in streams[:limit]], _sum_known(counts), error


WALL_CACHE_SECONDS = 60
_WALL_CACHE_MAX = 256


class LiveWallError(Exception):
    """A wall page could not be built: ``unknown`` category, ``cursor`` invalid, or ``unavailable`` upstream."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _encode_cursor(twitch_index: int | None, youtube_offset: int | None) -> str:
    return base64.urlsafe_b64encode(json.dumps([twitch_index, youtube_offset]).encode()).decode().rstrip("=")


def _decode_cursor(token: str) -> tuple[int | None, int | None]:
    try:
        twitch_index, youtube_offset = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if all(v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0) for v in (twitch_index, youtube_offset)):
            return twitch_index, youtube_offset
    except (ValueError, TypeError, binascii.Error):
        pass
    raise LiveWallError("cursor")


class LiveWall:
    """Paginated live wall for one category: Twitch directories + the held YouTube list.

    The cursor is an opaque token (next Twitch directory index, next YouTube offset); a side that ran out is null. Twitch
    pages are fetched only when asked (each is one directory's first page); YouTube is paged from the pool the background
    refresh holds, never fetched on a request. Pages are cached briefly.
    """

    def __init__(
        self,
        twitch_directory: TwitchGqlDirectory,
        *,
        youtube_pool: Callable[[str], Sequence[YouTubeSearchResult]],
        gaming: GamingDirectories | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._twitch = twitch_directory
        self._youtube_pool = youtube_pool
        self._gaming = gaming or GamingDirectories(twitch_directory)
        self._clock = clock
        self._cache: dict[tuple, tuple[float, tuple]] = {}
        self._lock = threading.Lock()

    def page(self, category_key: str, cursor: str | None, limit: int) -> tuple[list[YouTubeSearchResult], str | None, int | None]:
        if category_key not in {category.key for category in LIVE_CATEGORIES}:
            raise LiveWallError("unknown")
        positions = _decode_cursor(cursor) if cursor else None
        key = (category_key, cursor, limit)
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < WALL_CACHE_SECONDS:
                return hit[1]
        result = self._build(category_key, positions, limit)
        with self._lock:
            if len(self._cache) >= _WALL_CACHE_MAX:
                self._cache.clear()  # Wholesale clear, LRU if wall traffic ever makes this churn
            self._cache[key] = (now, result)
        return result

    def _build(self, category_key: str, positions: tuple[int | None, int | None] | None, limit: int):
        pool = list(self._youtube_pool(category_key))
        twitch_at, youtube_at = (0, 0) if positions is None else positions
        items: list[YouTubeSearchResult] = []
        next_twitch: int | None = None
        failed = False
        if twitch_at is not None and TWITCH_CATEGORY_SOURCES.get(category_key, ()) != ():
            try:
                names = _sources(category_key, self._gaming)
            except TwitchDirectoryError:
                names, failed = [], True
            stop = twitch_at + _WALL_SOURCES_PER_PAGE
            for name in names[twitch_at:stop]:
                try:
                    items.extend(twitch_stream_to_result(s) for s in self._twitch.game_page(name, MAX_PAGE).streams)
                except TwitchDirectoryError:
                    failed = True  # skipped: one directory failing must not end the wall
                if len(items) >= limit:
                    stop = names.index(name) + 1
                    break
            next_twitch = stop if stop < len(names) else None
        next_youtube = None
        if youtube_at is not None:
            chunk = pool[youtube_at : youtube_at + limit]
            items.extend(chunk)
            next_youtube = youtube_at + limit if youtube_at + limit < len(pool) else None
        if failed and not items:
            raise LiveWallError("unavailable")
        token = _encode_cursor(next_twitch, next_youtube) if next_twitch is not None or next_youtube is not None else None
        return merge_live_entries([], items), token, None


def create_live_discovery(
    snapshot_path: Path, search: Callable[[str, int], object], **overrides: object
) -> PopularDiscovery:
    return PopularDiscovery(
        snapshot_path,
        search,
        **overrides,  # tests: clock, executor, random
        categories=LIVE_CATEGORIES,
        per_category_limit=SNAPSHOT_CATEGORY_LIMIT,
        feed_limit=SNAPSHOT_CATEGORY_LIMIT * len(LIVE_CATEGORIES),
        category_ttl_seconds=LIVE_REFRESH_SECONDS,  # +-20% jitter; failures back off 60s doubling to 1h, honouring Retry-After
        max_concurrency=1,  # a cold YouTube refresh is ~35 upstream requests, so categories refresh serially
    )
