from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

# A subscriber that falls this far behind is disconnected; the browser's
# EventSource reconnects and refetches authoritative snapshots (resync).
MAX_QUEUED_EVENTS = 256
MAX_STREAMS_PER_USER = 8
REVALIDATE_SECONDS = 15.0


class Subscriber:
    __slots__ = ("user_id", "queue", "overflowed", "restricted")

    def __init__(self, user_id: str, restricted: bool = False) -> None:
        self.user_id = user_id
        # A member with library limits (ADR 0019) never receives another account's item data: broadcasts reach them
        # as a content-free hint. Refreshed on every revalidation so a changed limit applies within REVALIDATE_SECONDS.
        self.restricted = restricted
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)
        self.overflowed = False


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def open_streams(self, user_id: str) -> int:
        return sum(1 for subscriber in self._subscribers if subscriber.user_id == user_id)

    def stream_count(self) -> int:
        return len(self._subscribers)

    def reserve(self, user_id: str, restricted: bool = False) -> Subscriber | None:
        """Count and register in one synchronous step (atomic on the event loop); None when at the cap."""
        if self.open_streams(user_id) >= MAX_STREAMS_PER_USER:
            return None
        subscriber = Subscriber(user_id, restricted)
        self._subscribers.add(subscriber)
        return subscriber

    def release(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @asynccontextmanager
    async def subscribe(self, user_id: str, subscriber: Subscriber | None = None) -> AsyncIterator[Subscriber]:
        subscriber = subscriber or Subscriber(user_id)
        self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self.release(subscriber)

    def _deliver(self, event_type: str, payload: dict) -> None:
        message = {"type": event_type, "payload": payload}
        hint = {"type": event_type, "payload": {"broadcast": True}}  # no item: the client keeps its state until it refetches
        audience = payload.get("user_id") if isinstance(payload, dict) else None
        broadcast = isinstance(payload, dict) and payload.get("broadcast") is True
        for subscriber in list(self._subscribers):
            # Authorize before buffering: another member's events never enter this queue.
            if subscriber.overflowed or (not broadcast and audience != subscriber.user_id):
                continue
            try:
                subscriber.queue.put_nowait(hint if broadcast and subscriber.restricted else message)
            except asyncio.QueueFull:
                subscriber.overflowed = True

    def publish(self, event_type: str, payload: dict) -> None:
        """Loop-safe from any thread: off-loop callers are marshalled onto the bound loop."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if not on_loop:
                loop.call_soon_threadsafe(self._deliver, event_type, payload)
                return
        self._deliver(event_type, payload)


async def sse_stream(
    bus: EventBus,
    user_id: str,
    still_authorized: Callable[[], Awaitable[bool]],
    subscriber: Subscriber | None = None,
) -> AsyncIterator[str]:
    async with bus.subscribe(user_id, subscriber) as subscriber:
        yield "event: connected\ndata: {}\n\n"
        loop = asyncio.get_running_loop()
        next_check = loop.time() + REVALIDATE_SECONDS
        while not subscriber.overflowed:
            remaining = next_check - loop.time()
            if remaining <= 0:
                if not await still_authorized():
                    return  # revoked session / deactivated account: close the stream
                next_check = loop.time() + REVALIDATE_SECONDS
                yield ": keepalive\n\n"
                continue
            try:
                message = await asyncio.wait_for(subscriber.queue.get(), remaining)
            except TimeoutError:
                continue
            yield f"event: {message['type']}\ndata: {json.dumps(message['payload'])}\n\n"
