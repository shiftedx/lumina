from __future__ import annotations

import asyncio

from app.events import EventBus, sse_stream


async def _authorized() -> bool:
    return True


def test_authenticated_stream_only_emits_events_for_its_user() -> None:
    async def scenario() -> str:
        bus = EventBus()
        stream = sse_stream(bus, "user-a", _authorized)
        connected = await anext(stream)
        assert "event: connected" in connected

        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        bus.publish("job_progress", {"job": {"id": "unscoped"}})
        bus.publish("job_progress", {"user_id": "user-b", "job": {"id": "foreign"}})
        bus.publish("job_progress", {"user_id": "user-a", "job": {"id": "owned"}})

        event = await asyncio.wait_for(next_event, timeout=1)
        await stream.aclose()
        return event

    event = asyncio.run(scenario())

    assert '"id": "owned"' in event
    assert "unscoped" not in event
    assert "foreign" not in event


def test_authenticated_stream_emits_explicit_broadcast_events() -> None:
    async def scenario() -> str:
        bus = EventBus()
        stream = sse_stream(bus, "user-a", _authorized)
        await anext(stream)

        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        bus.publish("library_item_upserted", {"broadcast": True, "item": {"id": "shared"}})

        event = await asyncio.wait_for(next_event, timeout=1)
        await stream.aclose()
        return event

    event = asyncio.run(scenario())

    assert '"broadcast": true' in event
    assert '"id": "shared"' in event


def test_restricted_member_gets_content_free_broadcasts_while_others_get_items() -> None:
    """ADR 0019: a member with library limits never receives a shared item's title or artwork over the stream."""
    from app.models import User
    from app.services.member_access import ACCESS_ATTR, EffectiveAccess, snapshot_limits_library

    def snapshot(role: str, access: EffectiveAccess | None) -> User:
        user = User(id=role, username=role, role=role)
        user.__dict__[ACCESS_ATTR] = access
        return user

    kid = snapshot("viewer", EffectiveAccess(tv_rating_max="TV-Y7"))
    assert snapshot_limits_library(kid)
    assert not snapshot_limits_library(snapshot("viewer", None))
    assert not snapshot_limits_library(snapshot("viewer", EffectiveAccess(daily_limit_minutes=60)))  # time only
    assert not snapshot_limits_library(snapshot("admin", EffectiveAccess(sections=frozenset())))

    async def scenario() -> tuple[str, str, str]:
        bus = EventBus()
        kid_stream = sse_stream(bus, "kid", _authorized, bus.reserve("kid", restricted=True))
        adult_stream = sse_stream(bus, "adult", _authorized, bus.reserve("adult"))
        await anext(kid_stream), await anext(adult_stream)
        kid_event, adult_event = asyncio.create_task(anext(kid_stream)), asyncio.create_task(anext(adult_stream))
        await asyncio.sleep(0)
        bus.publish("library_item_upserted", {"broadcast": True, "item": {"id": "hidden", "title": "R-rated", "thumbnail_url": "/x"}})
        result = (await asyncio.wait_for(kid_event, 1), await asyncio.wait_for(adult_event, 1))
        bus.publish("library_item_missing", {"broadcast": True, "item": {"id": "hidden", "title": "R-rated", "status": "missing"}})
        missing = await asyncio.wait_for(anext(kid_stream), 1)
        await kid_stream.aclose(), await adult_stream.aclose()
        return (*result, missing)

    kid_event, adult_event, kid_missing = asyncio.run(scenario())
    # #167: the client refetches its own visible page on this hint, so the hint itself stays content-free.
    assert kid_event == 'event: library_item_upserted\ndata: {"broadcast": true}\n\n'
    assert kid_missing == 'event: library_item_missing\ndata: {"broadcast": true}\n\n'
    assert '"title": "R-rated"' in adult_event
