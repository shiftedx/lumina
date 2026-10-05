"""Shared per-provider request budgets for background discovery (live rails, live walls, followed-live checks).

One household shares one home IP. Unbudgeted, a single open Live tab asked YouTube for ~2,300 search pages an hour and
got the IP rate-limited (HTTP 403 on every request). Every discovery call now spends from its provider's token bucket:
at most ``burst`` requests at once, refilled at ``per_hour``. A rate-limit answer (HTTP 403/429, or a Retry-After)
pauses the provider for every caller, doubling while it persists. Since 2.6.1 every YouTube extraction spends here at
its caller's priority (yt_dlp_service.youtube_budget): clicks, playback resolves and downloads are counted but never
refused; googlevideo media fetches are not counted.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

_RATE_LIMITED = re.compile(r"HTTP Error (?:403|429)|\b429\b|Too Many Requests")


class BudgetExhausted(RuntimeError):
    """The provider's discovery budget is spent (or paused): keep the last-known answer and try again later."""


class ProviderBudget:
    def __init__(
        self,
        *,
        per_hour: float,
        burst: float,
        cooldown_seconds: float = 15 * 60,
        max_cooldown_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_hour, self.burst = per_hour, burst
        self._cooldown, self._max_cooldown = cooldown_seconds, max_cooldown_seconds
        self._clock = clock
        self._tokens = burst
        self._at = clock()
        self._paused_until = 0.0
        self._strikes = 0
        self._lock = threading.Lock()

    def take(self, cost: float = 1, *, keep: float = 0) -> bool:
        """Spend ``cost`` requests if the provider is not paused and ``keep`` tokens stay for higher-priority callers."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._at) * self.per_hour / 3600)
            self._at = now
            if now < self._paused_until or self._tokens - cost < keep:
                return False
            self._tokens -= cost
            return True

    def succeeded(self) -> None:
        with self._lock:
            self._strikes = 0

    def failed(self, error: BaseException, retry_after: float | None = None) -> None:
        """Pause the provider for everyone when ``error`` is a rate-limit answer (Retry-After wins when longer)."""
        if retry_after is None and not _RATE_LIMITED.search(str(error)):
            return
        with self._lock:
            self._strikes += 1
            pause = min(self._max_cooldown, self._cooldown * 2 ** (self._strikes - 1))
            self._paused_until = max(self._paused_until, self._clock() + max(pause, retry_after or 0))


# A YouTube search page is ~20 results; a full followed-channel extraction is ~3 requests.
YOUTUBE_RESULTS_PER_REQUEST = 20
PROBE_REQUESTS = 3
_LIMITS = {"youtube": {"per_hour": 300, "burst": 60}, "twitch": {"per_hour": 600, "burst": 60}}


def fresh(name: str, clock: Callable[[], float] = time.monotonic) -> ProviderBudget:
    return ProviderBudget(**_LIMITS[name], clock=clock)


youtube = fresh("youtube")
twitch = fresh("twitch")


def youtube_search_cost(limit: int) -> int:
    return max(1, -(-limit // YOUTUBE_RESULTS_PER_REQUEST))


# Household-wide priorities (2.6.1). Every YouTube extraction spends from ``youtube`` at the priority of the work that
# asked for it: what a member clicked always goes through (it is counted, so background work yields to it), background
# discovery keeps a reserve for clicks, and hover prefetch yields to both. Set with ``with priority("background"):``.
# Live discovery keeps its pre-2.6.1 order under the click reserve: the rails an open Live page shows ("live"), then
# followed-live checks ("followed"), then everything else in the background (the deep live-wall pools among it).
Priority = Literal["interactive", "live", "followed", "background", "prefetch"]
KEEP: dict[str, float] = {"interactive": 0, "live": 5, "followed": 10, "background": 20, "prefetch": 40}
_priority: ContextVar[str] = ContextVar("provider_budget_priority", default="interactive")


@contextmanager
def priority(level: Priority) -> Iterator[None]:
    token = _priority.set(level)
    try:
        yield
    finally:
        _priority.reset(token)


def current_priority() -> str:
    return _priority.get()


def spend(budget: ProviderBudget, cost: float = 1) -> None:
    """Charge ``cost`` at the current priority; raise BudgetExhausted for non-interactive work that must wait.
    Interactive work is never refused: it drives the bucket down (even below zero) so background work backs off."""
    level = _priority.get()
    if level == "interactive":
        with budget._lock:
            now = budget._clock()
            budget._tokens = min(budget.burst, budget._tokens + (now - budget._at) * budget.per_hour / 3600) - cost
            budget._at = now
        return
    if not budget.take(cost, keep=KEEP[level]):
        raise BudgetExhausted(f"{level} budget spent")


def affordable(budget: ProviderBudget, cost: float, level: Priority) -> bool:
    """Whether ``cost`` could be spent at ``level`` now, spending nothing (to admit an all-or-nothing batch)."""
    return budget.take(0, keep=KEEP[level] + cost)


def paused(budget: ProviderBudget) -> bool:
    with budget._lock:
        return budget._clock() < budget._paused_until
