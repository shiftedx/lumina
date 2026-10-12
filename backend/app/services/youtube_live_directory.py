"""YouTube's live directory: many currently-live streams per live category.

Research (2026-10): YouTube's "Live" destination and topic channels expose no
tab yt-dlp can read, but the live-filtered search results page returns ~100
live streams per query. Several parallel queries per category, merged and
ranked by concurrent viewers, give 350-750 live streams per category in ~6 s.
"""
from __future__ import annotations

import base64
import binascii
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait

from app.schemas import YouTubeSearchResponse, YouTubeSearchResult
from app.services import provider_budget

logger = logging.getLogger(__name__)

CATEGORY_QUERIES: dict[str, tuple[str, ...]] = {
    "gaming": ("gaming", "minecraft", "fortnite", "valorant", "gta", "esports", "roblox", "speedrun"),
    "music": ("live music", "lofi radio", "concert", "dj set", "radio", "24/7 music"),
    "news": ("news", "breaking news", "live news today", "world news", "weather"),
    "sports": ("sports", "football", "soccer", "basketball", "cricket", "baseball"),
    "creative": ("irl", "art", "just chatting", "cooking", "podcast", "talk show"),
    "other": ("live", "livestream", "24/7", "webcam", "animals", "space"),
}
QUERY_RESULTS = 100
TIME_BUDGET_SECONDS = 8.0
# A category's pool is ~30-40 search requests: it refreshes hourly at most, and only when the shared YouTube budget can
# afford the whole pool above the background reserve (so rows and followed checks come first).
CACHE_TTL_SECONDS = 60 * 60.0
_WORKERS = 4

Search = Callable[[str, int], YouTubeSearchResponse]


def _default_search(query: str, limit: int) -> YouTubeSearchResponse:
    # Lazy imports: the guarded yt-dlp path needs a DB session, as main.py's live search does.
    from app.db import SessionLocal
    from app.services.yt_dlp_service import YtDlpService

    with SessionLocal() as db:
        return YtDlpService(db).youtube_live_search(query, limit, max_limit=QUERY_RESULTS)


_lock = threading.Lock()
_cache: dict[str, tuple[float, list[YouTubeSearchResult]]] = {}


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o{offset}".encode()).decode().rstrip("=")


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        return max(0, int(raw[1:])) if raw.startswith("o") else 0
    except (binascii.Error, ValueError):
        return 0


def _fetch(category_key: str, search: Search, budget: float) -> list[YouTubeSearchResult] | None:
    """The category's merged pool, or None when the shared YouTube budget cannot afford it now (keep the last one)."""
    queries = CATEGORY_QUERIES[category_key]
    if not provider_budget.affordable(provider_budget.youtube, len(queries) * provider_budget.youtube_search_cost(QUERY_RESULTS), "background"):
        return None

    def run(query: str) -> YouTubeSearchResponse:
        # Each query spends at the seam; a rate-limit answer from a sibling pauses YouTube and the rest stop there.
        with provider_budget.priority("background"):
            return search(query, QUERY_RESULTS)

    pool = ThreadPoolExecutor(_WORKERS)
    futures = [pool.submit(run, q) for q in queries]
    done, _pending = wait(futures, timeout=budget)
    pool.shutdown(wait=False, cancel_futures=True)  # Stragglers finish in background, bounded by yt-dlp timeouts
    seen: dict[str, YouTubeSearchResult] = {}
    for future in done:
        try:
            items = future.result().items
        except Exception:  # one failing query must not sink the category
            logger.warning("youtube live directory query failed", exc_info=True)
            continue
        for item in items:
            if item.id and item.id not in seen and item.capabilities and item.capabilities.lifecycle == "live":
                seen[item.id] = item
    return sorted(seen.values(), key=lambda r: r.view_count or 0, reverse=True)


def youtube_live_directory(
    category_key: str, limit: int, cursor: str | None = None
) -> tuple[list[YouTubeSearchResult], str | None, int | None]:
    """Items, next cursor, count. Count is None: YouTube states no live total."""
    if category_key not in CATEGORY_QUERIES:
        return [], None, None
    now = time.monotonic()
    with _lock:
        cached = _cache.get(category_key)
    if cached is None or now - cached[0] > CACHE_TTL_SECONDS:
        items = _fetch(category_key, _default_search, TIME_BUDGET_SECONDS)
        if items is None:  # unaffordable now: serve the last list (or nothing) and try on a later refresh
            items = cached[1] if cached is not None else []
        elif items or cached is None:
            with _lock:
                _cache[category_key] = (now, items)
        else:
            items = cached[1]  # all queries failed: keep the last-known list
    else:
        items = cached[1]
    offset = _decode(cursor)
    page = items[offset : offset + max(1, limit)]
    end = offset + len(page)
    return page, (_encode(end) if end < len(items) else None), None
