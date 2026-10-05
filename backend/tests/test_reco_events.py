"""Recommendations R1: channel keys, the play-session rules, the served-list cache and the event store."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, insert, select, text

from app.db import ITEM_CHANNEL_ID_SQL
from app.media_schemas import RecoEventBatch, RecoEventIn
from app.models import RecoEvent, RecoPool
from app.persistence import write_transaction
from app.services.reco import events
from app.services.reco import Candidate, Ranked, ServedList
from app.services.reco.events import (
    CHANNEL_PREFIX, DepthStep, channel_key, depth_step, is_skip, library_channel_key, name_key, title_event_key,
)
from app.services.reco.events import MemberGenerations, RecoEventService, ServedListCache, generations
from discovery_support import add_movie, add_series, add_title
from support import make_user, memory_session_factory

UC = "UC" + "a" * 22
NOW = datetime(2026, 9, 30, 12, 0, 0)


@pytest.mark.parametrize(
    ("extractor", "channel_id", "channel_url", "uploader", "expected"),
    [
        ("youtube", UC, "https://www.youtube.com/@someone", "Someone", f"https://www.youtube.com/channel/{UC}"),
        ("youtube", None, "https://youtube.com/@Someone/videos", "Someone", "https://www.youtube.com/@someone"),
        ("youtube", None, f"https://www.youtube.com/channel/{UC}/videos", None, f"https://www.youtube.com/channel/{UC}"),
        ("youtube", "not-a-uc-id", None, "  Some One ", "name:youtube:some one"),
        ("youtube:tab", None, "https://www.youtube.com/watch?v=abc", "Some One", "name:youtube:some one"),
        ("twitch", None, None, "Streamer", "name:twitch:streamer"),
        (None, None, None, "Streamer", "name:web:streamer"),
        ("youtube", None, None, None, None),
        ("youtube", None, None, "   ", None),
        ("youtube", None, "https://www.youtube.com/@" + "x" * 300, "Fallback", "name:youtube:fallback"),
    ],
)
def test_channel_key_prefers_the_id_then_the_address_then_the_name(extractor, channel_id, channel_url, uploader, expected) -> None:  # noqa: ANN001
    assert channel_key(extractor, channel_id, channel_url, uploader) == expected


def test_name_key_is_bounded() -> None:
    assert name_key("youtube", "N" * 300) == "name:youtube:" + "n" * 200


@pytest.mark.parametrize(
    "metadata",
    [
        {"channel_id": UC},
        {"uploader_id": UC},
        {"channel_id": "@handle", "uploader_id": UC},
        {"channel_url": f"https://www.youtube.com/channel/{UC}"},
        {"uploader_url": f"https://www.youtube.com/channel/{UC}/videos"},
        {"channel_url": "https://www.youtube.com/@someone"},
        {"channel_id": "UCshort"},
        {"channel_id": UC[:-1] + "!"},
        {"channel_url": "https://www.youtube.com/channel/UCshort"},
        {},
    ],
)
def test_library_channel_key_matches_the_step_9_backfill(metadata) -> None:  # noqa: ANN001
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        found = connection.execute(
            text(f"SELECT {ITEM_CHANNEL_ID_SQL} FROM (SELECT :metadata AS metadata_json)"), {"metadata": json.dumps(metadata)}
        ).scalar()
    assert library_channel_key("youtube", metadata) == (None if found is None else CHANNEL_PREFIX + found)


def test_library_channel_key_is_for_youtube_items_only() -> None:
    assert library_channel_key("vimeo", {"channel_id": UC}) is None
    assert library_channel_key(None, {"channel_id": UC}) is None


def step(**changes):  # noqa: ANN003, ANN201
    base = dict(
        known=True, last_watched_at=NOW - timedelta(minutes=1), was_completed=False, was_cleared=False, max_fraction=0.0,
        completed=False, position_seconds=60, duration_seconds=600, now=NOW,
    )
    return depth_step(**{**base, **changes})


def test_depth_step_boundaries() -> None:
    assert step(known=False, last_watched_at=None) == DepthStep(fraction=pytest.approx(0.1), max_fraction=pytest.approx(0.1), play=True, complete=False)
    # Exactly 30 minutes is still the same session; one second more starts a new one.
    assert step(last_watched_at=NOW - timedelta(minutes=30)).play is False
    assert step(last_watched_at=NOW - timedelta(minutes=30, seconds=1)).play is True
    # A restart after a completion counts only below 10%: 59/600 restarts, exactly 60/600 does not.
    assert step(was_completed=True, position_seconds=59).play is True
    assert step(was_completed=True, position_seconds=60).play is False
    # A cleared row with no known duration restarts too.
    assert step(was_cleared=True, position_seconds=5, duration_seconds=None).play is True
    # Completion is reported once: it turns true, then stays true.
    assert step(completed=True).complete is True
    assert step(completed=True, was_completed=True).complete is False
    # Depth never decreases and never exceeds 1.0; unknown or zero duration is depth 0.
    assert step(max_fraction=0.8, position_seconds=60).max_fraction == 0.8
    assert step(position_seconds=5000).max_fraction == 1.0
    assert step(duration_seconds=0).fraction == 0.0
    assert step(duration_seconds=None).fraction == 0.0
    assert step(completed=True, position_seconds=0).max_fraction == 1.0


def test_is_skip_boundaries() -> None:
    skipped = dict(position_seconds=5, max_fraction=0.02, completions=0, last_watched_at=NOW - timedelta(minutes=31), now=NOW)
    assert is_skip(**skipped) is True
    assert is_skip(**{**skipped, "position_seconds": 30}) is False
    assert is_skip(**{**skipped, "max_fraction": 0.1}) is False
    assert is_skip(**{**skipped, "completions": 1}) is False
    assert is_skip(**{**skipped, "last_watched_at": NOW - timedelta(minutes=30)}) is False


def test_title_event_key_is_the_movie_or_the_series_of_an_episode() -> None:
    factory = memory_session_factory()
    with factory.begin() as db:
        add_movie(db, "m", "Movie")
        add_series(db, "show", "Show", seasons={1: 1})
        add_title(db, "box", "boxset", "Saga")
    with factory() as db:
        assert title_event_key(db, "m") == "m"
        assert title_event_key(db, "show-s1e1") == "show"
        assert title_event_key(db, "show-s1") == "show"
        assert title_event_key(db, "show") == "show"
        assert title_event_key(db, "box") is None
        assert title_event_key(db, "missing") is None


def candidate(key: str, *, channel: str | None = CHANNEL_PREFIX + UC, target_kind: str = "remote") -> Candidate:
    return Candidate(
        key=key, target_kind=target_kind, title="A video", channel_key=channel, channel_name="A channel",
        tokens=frozenset(), published_at=None, sources=0,
    )


def served_list(list_id: str, *, user: str = "alice", surface: str = "home_picked", context: str = "-", at: datetime = NOW,
                keys: tuple[str, ...] = ("a" * 64,)) -> ServedList:
    return ServedList(
        list_id=list_id, user_id=user, surface=surface, context_key=context, created_at=at,
        items=tuple(
            Ranked(candidate=candidate(key), position=index, slot="exploit", score=1.0, p_shown=None, reason_code="channel", reason="From a channel you watch")
            for index, key in enumerate(keys)
        ),
    )


def lid(number: int) -> str:
    return f"{number:016x}"


def test_generations_count_per_member_and_are_thread_safe() -> None:
    counter = MemberGenerations()
    assert (counter.get("alice"), counter.get("bob")) == (0, 0)
    counter.bump("alice")
    assert (counter.get("alice"), counter.get("bob")) == (1, 0)

    def hammer() -> None:
        for _ in range(500):
            counter.bump("bob")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert counter.get("bob") == 4000


def test_a_list_is_never_readable_by_another_member() -> None:
    cache = ServedListCache()
    cache.put(served_list(lid(1), user="alice"))
    assert cache.get("alice", lid(1), NOW) is not None
    assert cache.get("bob", lid(1), NOW) is None
    assert cache.get("alice", lid(1), NOW) is not None  # bob's miss did not evict it
    assert cache.find("bob", "home_picked", "-", NOW) is None


def test_a_list_expires_after_thirty_minutes() -> None:
    cache = ServedListCache()
    cache.put(served_list(lid(1)))
    assert cache.get("alice", lid(1), NOW + timedelta(minutes=29, seconds=59)) is not None
    assert cache.get("alice", lid(1), NOW + timedelta(minutes=30)) is None
    assert cache.find("alice", "home_picked", "-", NOW + timedelta(minutes=30)) is None


def test_the_cache_evicts_the_least_recently_used_list() -> None:
    cache = ServedListCache(size=2)
    cache.put(served_list(lid(1)))
    cache.put(served_list(lid(2)))
    assert cache.get("alice", lid(1), NOW) is not None  # touching 1 makes 2 the oldest
    cache.put(served_list(lid(3)))
    assert cache.get("alice", lid(2), NOW) is None
    assert cache.get("alice", lid(1), NOW) is not None and cache.get("alice", lid(3), NOW) is not None


def test_find_returns_the_newest_list_for_the_member_surface_and_context() -> None:
    cache = ServedListCache()
    cache.put(served_list(lid(1), surface="up_next", context="video-1"))
    cache.put(served_list(lid(2), surface="up_next", context="video-1", at=NOW + timedelta(minutes=1)))
    cache.put(served_list(lid(3), surface="up_next", context="video-2"))
    cache.put(served_list(lid(4), surface="home_picked"))
    cache.put(served_list(lid(5), surface="up_next", context="video-1", user="bob"))
    found = cache.find("alice", "up_next", "video-1", NOW + timedelta(minutes=2))
    assert found is not None and found.list_id == lid(2)
    assert cache.find("alice", "up_next", "video-9", NOW) is None


def test_find_prefers_the_newest_list_over_the_most_recently_touched_one() -> None:
    cache = ServedListCache()
    cache.put(served_list(lid(1), surface="up_next", context="video-1"))
    cache.put(served_list(lid(2), surface="up_next", context="video-1", at=NOW + timedelta(minutes=10)))
    assert cache.get("alice", lid(1), NOW + timedelta(minutes=11)) is not None  # an event on the older list touches it
    found = cache.find("alice", "up_next", "video-1", NOW + timedelta(minutes=12))
    assert found is not None and found.list_id == lid(2)
    # the touched older list expires first; the newer one is still served
    found = cache.find("alice", "up_next", "video-1", NOW + timedelta(minutes=35))
    assert found is not None and found.list_id == lid(2)


def test_drop_removes_one_surface_or_every_list_of_the_member_only() -> None:
    cache = ServedListCache()
    for number, (user, surface) in enumerate([("alice", "home_picked"), ("alice", "up_next"), ("bob", "home_picked")], start=1):
        cache.put(served_list(lid(number), user=user, surface=surface))
    cache.drop("alice", "home_picked")
    assert [cache.get("alice", lid(1), NOW), cache.get("alice", lid(2), NOW)][0] is None
    assert cache.get("alice", lid(2), NOW) is not None and cache.get("bob", lid(3), NOW) is not None
    cache.drop("alice")
    assert cache.get("alice", lid(2), NOW) is None and cache.get("bob", lid(3), NOW) is not None
    cache.clear()
    assert cache.get("bob", lid(3), NOW) is None


def test_drop_without_a_surface_drops_every_cached_list_of_that_member(  # the policy calls drop(user_id) on any explicit feedback
) -> None:
    cache = ServedListCache()
    surfaces = ["home_picked", "home_recommended", "home_because", "explore_for_you", "explore_popular", "up_next", "title_similar"]
    for number, surface in enumerate(surfaces, start=1):
        cache.put(served_list(lid(number), user="alice", surface=surface, context=f"ctx-{number}"))
    cache.put(served_list(lid(99), user="bob", surface="home_picked"))
    cache.drop("alice", None)
    assert [cache.get("alice", lid(number), NOW) for number in range(1, 8)] == [None] * 7
    assert cache.get("bob", lid(99), NOW) is not None


def test_the_cache_survives_concurrent_use_and_stays_bounded() -> None:
    cache = ServedListCache(size=64)
    errors: list[BaseException] = []

    def hammer(worker: int) -> None:
        try:
            for round_ in range(300):
                number = worker * 1000 + round_
                cache.put(served_list(lid(number), user=f"user-{worker}"))
                cache.get(f"user-{worker}", lid(number), NOW)
                cache.find(f"user-{worker}", "home_picked", "-", NOW)
                if round_ % 50 == 0:
                    cache.drop(f"user-{worker}", "home_picked")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(worker,)) for worker in range(8)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert errors == []
    assert len(cache._lists) <= 64


KEY_A, KEY_B = "a" * 64, "b" * 64


@pytest.fixture
def store():  # noqa: ANN201
    factory = memory_session_factory()
    with factory.begin() as db:
        db.add_all([make_user("alice"), make_user("bob")])
    lists = ServedListCache()
    events._drops.reset()
    return factory, lists


def run(factory, lists, action):  # noqa: ANN001, ANN201
    """One write transaction, like a route: the commit drains the deferred actions."""
    with factory() as db, write_transaction(db, name="test"):
        return action(RecoEventService(db, lists))


def rows(factory, user: str | None = None) -> list[RecoEvent]:  # noqa: ANN001
    with factory() as db:
        query = select(RecoEvent).order_by(RecoEvent.id)
        if user:
            query = query.where(RecoEvent.user_id == user)
        return list(db.scalars(query))


def batch(*items: tuple[str, str, str, int]) -> RecoEventBatch:
    return RecoEventBatch(events=[RecoEventIn(kind=kind, list_id=list_id, key=key, age_ms=age) for kind, list_id, key, age in items])


def test_the_server_fills_the_context_from_the_list_it_served(store) -> None:  # noqa: ANN001
    factory, lists = store
    served = ServedList(
        list_id=lid(7), user_id="alice", surface="explore_for_you", context_key="-", created_at=NOW,
        items=(
            Ranked(candidate(KEY_A), 0, "exploit", 0.9, None, "channel", "From a channel you watch"),
            Ranked(candidate(KEY_B, channel="name:youtube:other"), 1, "explore", 0.4, 0.25, "explore", "Something new"),
        ),
    )
    lists.put(served)
    stored = run(factory, lists, lambda service: service.record_client(make_user("alice"), batch(("impression", lid(7), KEY_B, 2000)), now=NOW))
    assert stored == 1
    (row,) = rows(factory)
    assert (row.user_id, row.kind, row.surface, row.list_id, row.position, row.slot, row.p_shown, row.reason_code) == (
        "alice", "impression", "explore_for_you", lid(7), 1, "explore", 0.25, "explore",
    )
    assert (row.target_kind, row.item_key, row.channel_key, row.at) == ("remote", KEY_B, "name:youtube:other", NOW - timedelta(seconds=2))


def test_unknown_foreign_expired_and_foreign_key_events_are_dropped_and_counted(store) -> None:  # noqa: ANN001
    factory, lists = store
    lists.put(served_list(lid(1), user="alice", keys=(KEY_A,)))
    lists.put(served_list(lid(2), user="alice", keys=(KEY_A,), at=NOW - timedelta(minutes=31)))
    stored = run(factory, lists, lambda service: service.record_client(make_user("bob"), batch(
        ("impression", lid(1), KEY_A, 0),   # alice's list, posted by bob
        ("impression", lid(9), KEY_A, 0),   # never issued
    ), now=NOW))
    assert stored == 0
    stored = run(factory, lists, lambda service: service.record_client(make_user("alice"), batch(
        ("open", lid(2), KEY_A, 0),         # expired
        ("open", lid(1), KEY_B, 0),         # a key that list never held
    ), now=NOW))
    assert stored == 0 and rows(factory) == []
    with factory() as db:
        assert RecoEventService(db, lists).dropped_24h(NOW) == 4


def test_an_impression_is_stored_once_per_list_and_key(store) -> None:  # noqa: ANN001
    factory, lists = store
    lists.put(served_list(lid(1), keys=(KEY_A, KEY_B)))
    first = batch(("impression", lid(1), KEY_A, 0), ("impression", lid(1), KEY_A, 10), ("open", lid(1), KEY_A, 0), ("open", lid(1), KEY_A, 5))
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), first, now=NOW)) == 3  # one impression, two opens
    # A retried beacon stores no second impression; the opens are real activations and stay.
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), first, now=NOW)) == 2
    assert [row.kind for row in rows(factory)].count("impression") == 1
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), batch(("impression", lid(1), KEY_B, 0)), now=NOW)) == 1


def test_the_daily_cap_stops_storage_and_counts_the_rest(store, monkeypatch) -> None:  # noqa: ANN001
    factory, lists = store
    monkeypatch.setattr(events, "DAILY_EVENT_CAP", 5)
    lists.put(served_list(lid(1)))
    eight = batch(*[("open", lid(1), KEY_A, 0)] * 8)
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), eight, now=NOW)) == 5
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), eight, now=NOW)) == 0
    with factory() as db:
        assert RecoEventService(db, lists).dropped_24h(NOW) == 3 + 8
    tomorrow = NOW + timedelta(days=1)
    lists.put(served_list(lid(2), at=tomorrow))
    assert run(factory, lists, lambda service: service.record_client(make_user("alice"), batch(("open", lid(2), KEY_A, 0)), now=tomorrow)) == 1
    # Another member's cap is their own.
    lists.put(served_list(lid(3), user="bob"))
    assert run(factory, lists, lambda service: service.record_client(make_user("bob"), batch(("open", lid(3), KEY_A, 0)), now=NOW)) == 1


def test_the_drop_counter_holds_one_bucket_per_hour() -> None:
    counter = events._DropCounter()
    for hour in range(0, 200):
        counter.add(NOW + timedelta(hours=hour), 3)
    assert len(counter._hours) <= 25
    last = NOW + timedelta(hours=199)
    assert counter.last_24h(last) == 24 * 3
    assert counter.last_24h(last + timedelta(hours=30)) == 0


def open_event(factory, lists, key: str, at: datetime, *, user: str = "alice", surface: str = "up_next", position: int = 3) -> None:  # noqa: ANN001
    with factory.begin() as db:
        db.add(RecoEvent(user_id=user, at=at, kind="open", surface=surface, list_id=lid(5), position=position, slot="pinned",
                         p_shown=None, reason_code="next_part", target_kind="remote", item_key=key, channel_key=None))


def test_a_play_copies_its_attribution_from_the_newest_open_within_thirty_minutes(store) -> None:  # noqa: ANN001
    factory, lists = store
    open_event(factory, lists, KEY_A, NOW - timedelta(minutes=40), surface="home_picked", position=9)   # too old
    open_event(factory, lists, KEY_A, NOW - timedelta(minutes=10), surface="up_next", position=3)
    open_event(factory, lists, KEY_A, NOW - timedelta(minutes=20), surface="explore_popular", position=1)  # older than the newest
    open_event(factory, lists, KEY_A, NOW - timedelta(minutes=1), user="bob", surface="title_similar", position=0)  # another member
    run(factory, lists, lambda service: service.record_play("alice", target_kind="remote", item_key=KEY_A, channel_key=None, fraction=0.02, now=NOW))
    play = rows(factory, "alice")[-1]
    assert (play.kind, play.surface, play.list_id, play.position, play.slot, play.reason_code, play.fraction) == (
        "play", "up_next", lid(5), 3, "pinned", "next_part", 0.02,
    )
    run(factory, lists, lambda service: service.record_play("alice", target_kind="remote", item_key=KEY_B, channel_key=None, fraction=0.0, now=NOW))
    assert rows(factory, "alice")[-1].surface is None  # nothing to attribute


def test_a_second_play_or_completion_inside_thirty_minutes_is_not_written_twice(store) -> None:  # noqa: ANN001
    factory, lists = store
    for minutes in (0, 5, 31):
        at = NOW + timedelta(minutes=minutes)
        run(factory, lists, lambda service, at=at: service.record_play("alice", target_kind="title", item_key="show", channel_key=None, fraction=0.0, now=at))
    assert [row.at for row in rows(factory) if row.kind == "play"] == [NOW, NOW + timedelta(minutes=31)]
    for minutes in (0, 1):
        at = NOW + timedelta(minutes=minutes)
        run(factory, lists, lambda service, at=at: service.record_complete("alice", target_kind="title", item_key="show", channel_key=None, fraction=1.0, now=at))
    assert [row.kind for row in rows(factory)].count("complete") == 1


def test_a_completion_bumps_the_generation_after_the_commit(store) -> None:  # noqa: ANN001
    factory, lists = store
    before = generations.get("gen-member")
    with factory() as db:
        with write_transaction(db, name="test"):
            RecoEventService(db, lists).record_complete("gen-member", target_kind="title", item_key="m", channel_key=None, fraction=1.0, now=NOW)
            assert generations.get("gen-member") == before  # not before the commit: a rebuilt profile would miss the row
    assert generations.get("gen-member") == before + 1


def test_feedback_reads_the_members_own_list_and_drops_that_surface(store) -> None:  # noqa: ANN001
    factory, lists = store
    lists.put(served_list(lid(1), user="alice", surface="home_picked", keys=(KEY_A,)))
    lists.put(served_list(lid(2), user="alice", surface="up_next", keys=(KEY_A,)))
    lists.put(served_list(lid(3), user="bob", surface="home_picked", keys=(KEY_A,)))
    run(factory, lists, lambda service: service.record_feedback(
        "alice", "not_interested", target_kind="remote", item_key=KEY_A, channel_key="c", list_id=lid(1), now=NOW))
    feedback = rows(factory, "alice")[-1]
    assert (feedback.kind, feedback.surface, feedback.list_id, feedback.position, feedback.channel_key) == ("not_interested", "home_picked", lid(1), 0, "c")
    assert lists.get("alice", lid(1), NOW) is None and lists.get("alice", lid(2), NOW) is not None and lists.get("bob", lid(3), NOW) is not None
    # bob's list id sent by alice is not read; her whole cache is dropped because the surface is unknown.
    run(factory, lists, lambda service: service.record_feedback(
        "alice", "fewer", target_kind="remote", item_key=KEY_A, channel_key="c", list_id=lid(3), now=NOW))
    foreign = rows(factory, "alice")[-1]
    assert (foreign.kind, foreign.surface, foreign.list_id) == ("fewer", None, None)
    assert lists.get("alice", lid(2), NOW) is None and lists.get("bob", lid(3), NOW) is not None
    with pytest.raises(ValueError):
        run(factory, lists, lambda service: service.record_feedback(
            "alice", "impression", target_kind="remote", item_key=KEY_A, channel_key=None, list_id=None, now=NOW))  # type: ignore[arg-type]


def test_the_sweep_keeps_what_the_retention_table_keeps_and_works_in_batches(store) -> None:  # noqa: ANN001
    factory, lists = store
    day = timedelta(days=1)
    aged = [
        ("impression", 36 * day), ("open", 35 * day + timedelta(seconds=1)),  # past 35 days: swept
        ("impression", 35 * day), ("open", 34 * day),                          # kept
        ("play", 36 * day), ("complete", 399 * day), ("not_interested", 399 * day),  # long kinds: kept
        ("play", 401 * day), ("restore", 400 * day + timedelta(seconds=1)),          # swept
    ]
    with factory.begin() as db:
        for kind, age in aged:
            db.add(RecoEvent(user_id="alice", at=NOW - age, kind=kind, target_kind="remote", item_key=KEY_A))
    assert run(factory, lists, lambda service: service.sweep(NOW, batch=2)) == 2
    assert run(factory, lists, lambda service: service.sweep(NOW, batch=2)) == 2
    assert run(factory, lists, lambda service: service.sweep(NOW, batch=2)) == 0
    assert sorted((row.kind, NOW - row.at) for row in rows(factory)) == sorted(
        [("impression", 35 * day), ("open", 34 * day), ("play", 36 * day), ("complete", 399 * day), ("not_interested", 399 * day)]
    )


def test_clear_erases_the_members_events_and_pool_and_nobody_elses(store) -> None:  # noqa: ANN001
    factory, lists = store
    with factory.begin() as db:
        db.add_all([
            RecoEvent(user_id="alice", at=NOW, kind="open", target_kind="remote", item_key=KEY_A),
            RecoEvent(user_id="bob", at=NOW, kind="open", target_kind="remote", item_key=KEY_A),
            RecoPool(user_id="alice", item_key=KEY_A, sources=1), RecoPool(user_id="bob", item_key=KEY_A, sources=1),
        ])
    lists.put(served_list(lid(1), user="alice"))
    lists.put(served_list(lid(2), user="bob"))
    before = generations.get("alice")
    run(factory, lists, lambda service: service.clear("alice"))
    assert [row.user_id for row in rows(factory)] == ["bob"]
    with factory() as db:
        assert [pool.user_id for pool in db.scalars(select(RecoPool))] == ["bob"]
    assert lists.get("alice", lid(1), NOW) is None and lists.get("bob", lid(2), NOW) is not None
    assert generations.get("alice") == before + 1


def test_every_query_on_reco_events_uses_one_of_its_indexes(store) -> None:  # noqa: ANN001
    factory, lists = store
    engine = factory.kw["bind"]
    with factory.begin() as db:  # a populated, unanalyzed table: production shape
        db.execute(insert(RecoEvent), [
            {"user_id": f"user-{n % 3}", "at": NOW - timedelta(minutes=n), "kind": ("impression", "open", "play")[n % 3],
             "target_kind": "remote", "item_key": f"{n % 400:064x}", "list_id": lid(n % 50)}
            for n in range(1500)
        ])
    lists.put(served_list(lid(1), user="alice", keys=(KEY_A, KEY_B)))
    statements: list[tuple[str, tuple]] = []

    def capture(connection, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        if statement.lstrip().upper().startswith(("SELECT", "DELETE")):
            statements.append((statement, tuple(parameters)))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        run(factory, lists, lambda service: service.record_client(make_user("alice"), batch(("impression", lid(1), KEY_A, 0), ("open", lid(1), KEY_B, 0)), now=NOW))
        run(factory, lists, lambda service: service.record_play("alice", target_kind="remote", item_key=KEY_A, channel_key=None, fraction=0.0, now=NOW))
        run(factory, lists, lambda service: service.sweep(NOW))
        run(factory, lists, lambda service: service.clear("alice"))
        with factory() as db:
            RecoEventService(db, lists).export("alice")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements
    with engine.connect() as connection:
        for statement, parameters in statements:
            if "reco_events" not in statement:
                continue
            plan = " | ".join(row[3] for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).fetchall())
            assert re.search(r"SCAN (TABLE )?reco_events(?! USING)", plan) is None, (statement, plan)
