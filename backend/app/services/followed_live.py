"""Cached, background live-status checks for a member's followed channels."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import time
from urllib.parse import urlsplit

from app.schemas import YouTubeSearchResult
from app.services import provider_budget
from app.services.kick_public import is_kick_host

logger = logging.getLogger(__name__)


def live_probe_url(channel_url: str) -> str:
    parts = urlsplit(channel_url)
    host = (parts.netloc or "").lower()
    # Twitch and Kick channel pages are themselves the live endpoint.
    if "twitch.tv" in host or is_kick_host(channel_url):
        return channel_url
    trimmed = channel_url.rstrip("/")
    if trimmed.endswith("/live"):
        return trimmed
    return f"{trimmed}/live"


def followed_source(channel_url: str) -> str:
    if is_kick_host(channel_url):
        return "kick"
    return "twitch" if "twitch.tv" in (urlsplit(channel_url).netloc or "").lower() else "youtube"


@dataclass(frozen=True)
class _CacheEntry:
    checked_at: float
    entry: YouTubeSearchResult | None  # None -> known not-live
    failed: bool = False  # the latest probe was refused or broke; entry is last-known


# A probe is a full extraction (~3 requests). Rows keep priority (YouTube: the "followed" level; Twitch: FOLLOWED_KEEP);
# with many follows the shared budget stretches each channel's re-check instead of flooding the provider. YouTube probes
# are charged at the extraction seam; Kick has no budget (one cheap API call).
FOLLOWED_KEEP = 5


def _afford_probe(channel_url: str) -> bool:
    source = followed_source(channel_url)
    if source == "youtube":
        return provider_budget.affordable(provider_budget.youtube, provider_budget.PROBE_REQUESTS, "followed")
    budget = getattr(provider_budget, source, None)
    return budget is None or budget.take(provider_budget.PROBE_REQUESTS, keep=FOLLOWED_KEEP)


class FollowedLiveChecker:
    """Serves last-known live results instantly; refreshes in the background.

    Never performs provider I/O on the calling (request) thread. A stale or
    missing channel returns its last-known value (or nothing) while a probe is
    scheduled; the surface's normal polling picks up the refreshed answer.
    """

    def __init__(
        self,
        probe: Callable[[str], YouTubeSearchResult | None],
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time,
        executor=None,
    ) -> None:
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._owns_executor = executor is None
        # Own the executor eagerly so concurrent first requests never race to
        # lazily build (and leak) competing pools.
        self._executor = executor if executor is not None else ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="followed-live"
        )
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._inflight: set[str] = set()
        self._closed = False

    def live_entries(self, channel_urls: Sequence[str]) -> list[YouTubeSearchResult]:
        now = self._clock()
        entries: list[YouTubeSearchResult] = []
        to_probe: list[str] = []
        with self._lock:
            if self._closed:
                return []
            # Snapshot the executor under the lock: close() may null it out right
            # after we release, and a stale None would raise AttributeError on the
            # request thread instead of the RuntimeError we handle below.
            executor = self._executor
            for channel_url in channel_urls:
                probe_url = live_probe_url(channel_url)
                cached = self._cache.get(probe_url)
                if cached is not None and cached.entry is not None:
                    entries.append(cached.entry)
                fresh = cached is not None and (now - cached.checked_at) < self._ttl_seconds
                if not fresh and probe_url not in self._inflight and _afford_probe(channel_url):
                    self._inflight.add(probe_url)
                    to_probe.append(probe_url)
        for probe_url in to_probe:
            try:
                executor.submit(self._run_probe, probe_url)
            except RuntimeError:
                # The executor was shut down between our _closed check and this
                # submit; drop the reservation so the channel can be retried later.
                with self._lock:
                    self._inflight.discard(probe_url)
        return entries

    def _run_probe(self, probe_url: str) -> None:
        failed = False
        entry: YouTubeSearchResult | None = None
        source = followed_source(probe_url)
        try:
            with provider_budget.priority("followed"):
                entry = self._probe(probe_url)
        except provider_budget.BudgetExhausted:
            with self._lock:  # not checked, not failed: the next poll tries again
                self._inflight.discard(probe_url)
            return
        except Exception as error:  # noqa: BLE001 - an outage is not a "not live" answer
            logger.debug("Followed-live probe failed for %s", probe_url, exc_info=True)
            failed = True
            budget = getattr(provider_budget, source, None)
            if budget is not None and source != "youtube":  # YouTube's seam already counted it
                budget.failed(error)  # a rate-limit answer pauses the provider for every discovery caller
        with self._lock:
            self._inflight.discard(probe_url)
            if not self._closed:
                if failed:
                    # Keep the last-known status through a provider outage; only
                    # the check time advances so the channel is not re-probed hot.
                    previous = self._cache.get(probe_url)
                    entry = previous.entry if previous is not None else None
                self._cache[probe_url] = _CacheEntry(checked_at=self._clock(), entry=entry, failed=failed)

    def unavailable_sources(self, channel_urls: Sequence[str]) -> dict[str, float]:
        """Source -> latest check time, for sources whose latest followed-channel check failed."""
        unavailable: dict[str, float] = {}
        with self._lock:
            for channel_url in channel_urls:
                cached = self._cache.get(live_probe_url(channel_url))
                if cached is not None and cached.failed:
                    source = followed_source(channel_url)
                    unavailable[source] = max(cached.checked_at, unavailable.get(source, 0.0))
        return unavailable

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executor = self._executor if self._owns_executor else None
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
