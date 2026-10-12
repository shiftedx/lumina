"""Event delivery is authorized before buffering, bounded per
subscriber, loop-safe from threads, and long-lived streams re-authenticate."""
from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import events as events_module
from app.config import settings
from app.events import MAX_QUEUED_EVENTS, MAX_STREAMS_PER_USER, EventBus, Subscriber, sse_stream
from app.main import app, events, events_endpoint
from app.models import User
from app.security import CSRF_HEADER, hash_password
from app.services.rate_limit import rate_limiter
from test_v1_sessions import PASSWORD, login


@pytest.fixture
def client():
    """The real app database: /api/events authenticates through SessionLocal, not get_db."""
    from app import db as db_module

    db_module.Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal.begin() as db:
        db.add(User(id="u1", username="local", display_name="Local", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
    rate_limiter.clear()
    test_client = TestClient(app, base_url="http://localhost")
    yield test_client
    test_client.close()


async def _authorized() -> bool:
    return True


def test_slow_subscriber_bounded() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = sse_stream(bus, "user-a", _authorized)
        await anext(stream)  # connected; the consumer now stops reading
        subscriber = next(iter(bus._subscribers))
        for index in range(10_000):
            bus.publish("job_progress", {"user_id": "user-b", "job": {"id": f"foreign-{index}"}})
            bus.publish("job_progress", {"user_id": "user-a", "job": {"id": "owned", "progress": index}})
        assert subscriber.queue.qsize() <= MAX_QUEUED_EVENTS
        assert subscriber.overflowed
        assert all(message["payload"]["user_id"] == "user-a" for message in subscriber.queue._queue)  # type: ignore[attr-defined]
        # The overflowed stream ends so the client reconnects and refetches snapshots.
        with pytest.raises(StopAsyncIteration):
            for _ in range(MAX_QUEUED_EVENTS + 2):
                await anext(stream)
        assert not bus._subscribers

    asyncio.run(scenario())


def test_revoked_stream_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_module, "REVALIDATE_SECONDS", 0.01)
    checks: list[bool] = [True, False]

    async def authorized() -> bool:
        return checks.pop(0)

    async def scenario() -> list[str]:
        bus = EventBus()
        frames = []
        async for frame in sse_stream(bus, "user-a", authorized):
            frames.append(frame)
        assert not bus._subscribers
        return frames

    frames = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert frames == ["event: connected\ndata: {}\n\n", ": keepalive\n\n"]


def test_thread_publish_is_loop_safe() -> None:
    async def scenario() -> int:
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        subscriber = Subscriber("user-a")
        bus._subscribers.add(subscriber)

        def produce() -> None:
            for _ in range(25):
                bus.publish("job_progress", {"user_id": "user-a"})

        threads = [threading.Thread(target=produce) for _ in range(8)]
        for thread in threads:
            thread.start()
        await asyncio.to_thread(lambda: [thread.join() for thread in threads])
        await asyncio.sleep(0.05)
        return subscriber.queue.qsize()

    assert asyncio.run(scenario()) == 200


def test_per_user_stream_limit(client: TestClient) -> None:
    login(client)
    extra = {Subscriber("u1") for _ in range(MAX_STREAMS_PER_USER)}
    events._subscribers.update(extra)
    try:
        assert client.get("/api/events").status_code == 429
    finally:
        events._subscribers.difference_update(extra)


def test_real_stream_closes_after_logout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real /api/events handler + real session auth: logout ends an open stream."""
    monkeypatch.setattr(events_module, "REVALIDATE_SECONDS", 0.05)
    csrf = login(client)
    cookie = client.cookies.get(settings.session_cookie_name)
    request = Request({
        "type": "http", "method": "GET", "path": "/api/events", "query_string": b"",
        "headers": [(b"cookie", f"{settings.session_cookie_name}={cookie}".encode())],
    })

    async def scenario() -> list[str]:
        response = await events_endpoint(request, None)
        frames = [await anext(response.body_iterator)]
        logout = await asyncio.to_thread(
            client.post, "/api/session/logout", headers={"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf}
        )
        assert logout.status_code == 204
        async for frame in response.body_iterator:  # ends only because the server closes the revoked stream
            frames.append(frame)
        return frames

    frames = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert frames[0].startswith("event: connected")
    assert all(frame == ": keepalive\n\n" for frame in frames[1:])
    assert events.open_streams("u1") == 0
