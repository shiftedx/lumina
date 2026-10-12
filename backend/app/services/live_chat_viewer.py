"""Live chat for viewers: one shared, poll-driven buffer per source.

Nothing runs in the background. The provider is asked for a new page only when a
viewer asks for messages and the last fetch is older than the poll interval, so
every member watching one stream shares one upstream poll. A source nobody has
asked about for ``idle_seconds`` is forgotten, so chat polling stops when people
stop watching. The browser sees normalized events only, never continuations,
request headers or upstream addresses.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Any

from app.services.live_chat_seam import LiveChatFetcher
from app.services.timed_chat import TimedChatBudget, apply_live_action, new_build_state


@dataclass
class _Source:
    lock: Lock = field(default_factory=Lock)
    status: str = "new"  # new | active | ended | unavailable
    continuation: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    state: Any = None
    events: deque[tuple[int, dict[str, Any]]] = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    next_seq: int = 1
    fetched_at: float = float("-inf")
    used_at: float = 0.0
    failures: int = 0


class LiveChatViewer:
    def __init__(
        self,
        fetcher: LiveChatFetcher,
        *,
        clock: Callable[[], float] = monotonic,
        poll_interval_seconds: float = 2.0,
        keep_events: int = 200,
        idle_seconds: float = 120.0,
        max_consecutive_failures: int = 4,
    ) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self._poll = poll_interval_seconds
        self._keep = keep_events
        self._idle = idle_seconds
        self._max_failures = max_consecutive_failures
        self._lock = Lock()
        self._sources: dict[str, _Source] = {}

    def read(self, source_url: str, user_id: str, *, after: int = 0) -> dict[str, Any]:
        """Events newer than ``after`` (a cursor from an earlier read), fetching a page when one is due."""

        now = self._clock()
        with self._lock:
            for key in [key for key, source in self._sources.items() if now - source.used_at > self._idle]:
                self._close(self._sources.pop(key))
            source = self._sources.setdefault(source_url, _Source())
            source.used_at = now
        with source.lock:
            if source.status == "new":
                self._bootstrap(source, source_url, user_id)
            elif source.status == "active" and now - source.fetched_at >= self._poll:
                self._fetch(source, now)
            events = [event for seq, event in source.events if seq > after]
            return {"status": source.status, "events": events, "cursor": source.next_seq - 1, "poll_after_ms": int(self._poll * 1000)}

    def _close(self, source: _Source) -> None:
        close = getattr(self._fetcher, "close", None)
        if close is not None and source.continuation:
            try:
                close(source.continuation)
            except Exception:
                pass

    def _bootstrap(self, source: _Source, source_url: str, user_id: str) -> None:
        try:
            boot = self._fetcher.bootstrap(source_url, user_id)
        except Exception:
            boot = None
        if boot is None or boot.status != "available" or not boot.continuation:
            source.status = "unavailable"
            return
        source.status = "active"
        source.continuation = boot.continuation
        source.headers = dict(boot.headers)
        source.state = new_build_state(TimedChatBudget(max_events=self._keep), rolling=True)
        self._fetch(source, self._clock())

    def _fetch(self, source: _Source, now: float) -> None:
        source.fetched_at = now
        try:
            page = self._fetcher.fetch_page(source.continuation or "", headers=source.headers)
        except Exception:
            source.failures += 1
            if source.failures >= self._max_failures:
                source.status = "ended"
                self._close(source)
            return
        source.failures = 0
        for action in page.actions:
            apply_live_action(source.state, action)
        # A later deletion or ban only reaches viewers who have not read that message yet;
        # re-sending moderation updates by id is the upgrade if it matters.
        for event in source.state.events:
            if event.id not in source.seen:
                source.events.append((source.next_seq, event.to_public_dict()))
                source.next_seq += 1
        # The rolling state is the dedupe window: exactly the ids it still holds.
        source.seen = {event.id for event in source.state.events}
        while len(source.events) > self._keep:
            source.events.popleft()
        if page.status != "active" or page.continuation is None:
            source.status = "ended"
            self._close(source)
        else:
            source.continuation = page.continuation
