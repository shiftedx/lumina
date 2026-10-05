"""Candidate sources and the refresher."""
from __future__ import annotations

import inspect
import logging
import threading
from array import array
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select, update

from app import db as db_module
from app.db import Base
import app.main as main
from app.models import AppSession, AppSettings, LibraryItem, MediaTitle, RecoPool, RemoteMedia, RemotePlaybackProgress, SourceAutomation, User, utcnow
from app.schemas import YouTubeSearchResult
from app.services import embeddings, local_ai
from app.services.popular_discovery import PopularCategory, PopularDiscovery
from app.services.reco import (
    SOURCE_CHANNEL, SOURCE_CURRENT_CHANNEL, SOURCE_FOLLOW, SOURCE_HISTORY, SOURCE_INTEREST, SOURCE_POPULAR, SOURCE_SEED, MemberProfile, SatisfiedItem, pool,
)
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.yt_dlp_service import SearchBusyError, YtDlpService
from app.services.youtube_channels import ChannelTabPage
from discovery_support import BASE, add_movie, add_progress, add_series
from model_support import wait_until
from support import make_user, memory_session_factory, popular_item, seed_app_settings

NOW = datetime(2026, 9, 30, 12, 0, 0)
CHANNEL_A = "UC" + "a" * 22
CHANNEL_KEY_A = f"https://www.youtube.com/channel/{CHANNEL_A}"


@dataclass
class _Done:
    def done(self) -> bool:
        return True


class _ImmediateExecutor:
    def submit(self, function):  # noqa: ANN001
        function()
        return _Done()


class _QueuedExecutor:
    def __init__(self) -> None:
        self.tasks: list = []

    def submit(self, function):  # noqa: ANN001
        self.tasks.append(function)
        return _Done()


def _popular(tmp_path: Path, executor, *, feed_limit: int = 5) -> PopularDiscovery:
    def search(query: str, limit: int):
        return {"items": [{"id": f"{query}-{n}", "title": f"{query} {n}", "view_count": 100 + n} for n in range(8)]}

    return PopularDiscovery(
        tmp_path / "popular.json", search, categories=[PopularCategory(key, key.title(), key) for key in ("a", "b", "c", "d")],
        executor=executor, clock=lambda: 1_700_000_000.0, random=lambda: 0.5, batch_size=4, max_concurrency=1, feed_limit=feed_limit,
    )


def test_candidates_are_every_ranked_item_not_the_feed_cut(tmp_path: Path) -> None:
    discovery = _popular(tmp_path, _ImmediateExecutor())
    feed = discovery.get_snapshot().items

    everything = discovery.candidates()

    assert len(feed) == 5
    assert len(everything) == 32 and len({item.id for item in everything}) == 32  # 4 categories x 8 items
    assert [item.id for item in everything[:5]] == [item.id for item in feed]  # the feed is the head of the same ranking


def test_candidates_is_a_pure_read_that_schedules_nothing(tmp_path: Path) -> None:
    executor = _QueuedExecutor()
    discovery = _popular(tmp_path, executor)

    assert discovery.candidates() == ()
    assert executor.tasks == []


def result(video_id: str | None, **fields) -> YouTubeSearchResult:
    """A flat YouTube search result of the invented channel "Alpha Channel"; ``fields`` override."""
    values = dict(
        id=video_id, title=f"Video {video_id} about harbour cranes", uploader="Alpha Channel", uploader_id=CHANNEL_A,
        uploader_url=CHANNEL_KEY_A, duration=600, view_count=1_000, published_at=NOW - timedelta(days=1),
        webpage_url=f"https://www.youtube.com/watch?v={video_id}", kind="video",
    )
    return YouTubeSearchResult(**(values | fields))


def key_of(video_id: str) -> str:
    return RemotePlaybackProgressService.source_identity_key(f"youtube:{video_id}")


def nominations(results, source: int, seed_ref: str | None = None) -> list[pool.Nomination]:  # noqa: ANN001
    return [pool.Nomination(media, source, seed_ref) for found in results if (media := pool.media_from_result(found))]


@pytest.fixture
def db():
    session = memory_session_factory()()
    yield session
    session.close()


def test_media_keys_match_the_progress_service_and_carry_a_stable_channel_key() -> None:
    media = pool.media_from_result(result("abc123"))

    assert media.key == key_of("abc123")
    assert (media.extractor, media.remote_id, media.kind, media.eligible) == ("youtube", "abc123", "video", True)
    assert media.channel_key == CHANNEL_KEY_A


def test_hostile_entries_are_skipped_or_excluded() -> None:
    assert pool.media_from_result(result(None, webpage_url=None)) is None  # no id and no address
    assert pool.media_from_result(result(None, source="twitch", webpage_url="https://user:pw@example.com/v")) is None  # credentials
    assert len(pool.media_from_result(result("x2", title="w" * 10_000)).title) == pool.TITLE_MAX
    assert pool.media_from_result(result("x3", duration=30)).eligible is False
    assert pool.media_from_result(result("x4", kind="short")).eligible is False
    assert pool.media_from_result(result("x5", kind="live")).eligible is False
    assert pool.media_from_result(result("x6", availability="private")).eligible is False
    assert pool.media_from_result(result("x7", published_at=NOW + timedelta(days=400))).eligible is True  # a date is data, not an error


def test_feed_entries_become_media() -> None:
    entry = {
        "id": "feed1", "title": "Quay wall repairs", "duration": 900, "thumbnail": "https://img.example/t.jpg",
        "webpage_url": "https://www.youtube.com/watch?v=feed1", "uploader": "Alpha Channel", "availability": "public",
        "published_at": "2026-09-29T10:00:00Z", "capabilities": {"provider": "youtube", "lifecycle": "vod"},
        "channel_id": CHANNEL_A, "view_count": 77,
    }

    media = pool.media_from_feed(entry)

    assert (media.key, media.published_at, media.view_count, media.channel_key) == (key_of("feed1"), datetime(2026, 9, 29, 10, 0), 77, CHANNEL_KEY_A)
    assert pool.media_from_feed({**entry, "capabilities": {"provider": "youtube", "lifecycle": "upcoming"}}).eligible is False
    assert pool.media_from_feed({"title": "no id and no address"}) is None


def test_one_video_from_two_sources_is_one_row_with_both_bits(db) -> None:
    same = result("dup")

    pool.nominate(db, "a", nominations([same], SOURCE_FOLLOW, "follow-1") + nominations([same], SOURCE_SEED, "progress-9"), now=NOW)
    db.commit()

    assert db.scalar(select(func.count()).select_from(RemoteMedia)) == 1
    entry = db.scalars(select(RecoPool)).one()
    assert (entry.sources, entry.seed_ref) == (SOURCE_FOLLOW | SOURCE_SEED, "progress-9")
    assert db.scalars(select(RemoteMedia)).one().tokens  # remote_tokens ran


def test_an_ineligible_entry_is_never_pooled_but_a_history_anchor_may_be_short(db) -> None:
    short = result("short1", duration=20)

    pool.nominate(db, "a", nominations([short], SOURCE_SEED), now=NOW)
    assert db.scalar(select(func.count()).select_from(RecoPool)) == 0

    pool.nominate(db, "a", nominations([short], SOURCE_HISTORY), now=NOW)  # watched items still need a vector for the centroids
    assert db.scalars(select(RecoPool)).one().sources == SOURCE_HISTORY


def test_a_title_change_drops_the_vector_and_nothing_blanks_a_known_field(db) -> None:
    pool.nominate(db, "a", nominations([result("v1", title="Old title here")], SOURCE_FOLLOW), now=NOW)
    row = db.get(RemoteMedia, key_of("v1"))
    row.vector_model, row.vector_signature, row.vector = "m", "s", b"\x00" * 8
    db.commit()

    pool.nominate(db, "a", nominations([result("v1", title="Old title here", view_count=None, uploader=None)], SOURCE_CHANNEL), now=NOW + timedelta(hours=1))
    row = db.get(RemoteMedia, key_of("v1"))
    assert (row.view_count, row.uploader, row.vector_model) == (1_000, "Alpha Channel", "m")  # a thinner listing blanks nothing

    pool.nominate(db, "a", nominations([result("v1", title="A new title entirely")], SOURCE_CHANNEL), now=NOW + timedelta(hours=2))
    row = db.get(RemoteMedia, key_of("v1"))
    assert (row.title, row.vector, row.vector_model, row.vector_signature) == ("A new title entirely", None, None, None)
    assert row.last_nominated_at == NOW + timedelta(hours=2)


@pytest.mark.parametrize("bit, cap", [(SOURCE_FOLLOW, 160), (SOURCE_CHANNEL, 100), (SOURCE_SEED, 80), (SOURCE_INTEREST, 40)])
def test_per_source_caps_keep_the_newest(db, bit: int, cap: int) -> None:
    found = [result(f"v{n:03d}", published_at=NOW - timedelta(hours=n)) for n in range(cap + 10)]  # v000 is the newest

    pool.nominate(db, "a", nominations(found, bit), now=NOW)
    db.commit()

    kept = set(db.scalars(select(RecoPool.item_key).where(RecoPool.user_id == "a")))
    assert len(kept) == cap
    assert key_of(f"v{cap - 1:03d}") in kept and key_of(f"v{cap:03d}") not in kept


def _completed(db, member: str, video_id: str) -> None:  # noqa: ANN001
    db.add(RemotePlaybackProgress(
        id=f"p-{video_id}", user_id=member, source_identity=f"youtube:{video_id}", source_identity_key=key_of(video_id),
        source_url=f"https://www.youtube.com/watch?v={video_id}", completed=True,
    ))
    db.flush()


def test_prune_drops_stale_rows_and_completed_items_but_keeps_history_rows(db) -> None:
    pool.nominate(db, "a", nominations([result("stale")], SOURCE_FOLLOW), now=NOW - timedelta(days=30))
    _completed(db, "a", "done_pooled")
    _completed(db, "a", "done_follow")

    pool.nominate(
        db, "a",
        nominations([result("done_pooled"), result("done_follow"), result("fresh")], SOURCE_FOLLOW) + nominations([result("done_pooled")], SOURCE_HISTORY),
        now=NOW,
    )
    db.commit()

    rows = {row.item_key: row.sources for row in db.scalars(select(RecoPool).where(RecoPool.user_id == "a"))}
    assert rows == {key_of("done_pooled"): SOURCE_HISTORY, key_of("fresh"): SOURCE_FOLLOW}  # stale and completed-only rows are gone


def test_sweep_deletes_unreferenced_old_media_and_nothing_a_pool_references(db) -> None:
    pool.nominate(db, "a", nominations([result("kept_ref")], SOURCE_FOLLOW), now=NOW)
    pool.upsert_media(db, [pool.media_from_result(result(name)) for name in ("old_orphan", "new_orphan")], now=NOW)
    db.execute(update(RemoteMedia).where(RemoteMedia.key.in_([key_of("old_orphan"), key_of("kept_ref")])).values(last_nominated_at=NOW - timedelta(days=20)))
    db.commit()

    removed = pool.sweep(db, now=NOW)
    db.commit()

    assert removed == 1
    assert set(db.scalars(select(RemoteMedia.key))) == {key_of("kept_ref"), key_of("new_orphan")}


CHANNEL_B, CHANNEL_C = "UC" + "b" * 22, "UC" + "c" * 22
CHANNEL_KEY_B, CHANNEL_KEY_C = (f"https://www.youtube.com/channel/{channel}" for channel in (CHANNEL_B, CHANNEL_C))


class Clock:
    """A fake clock: ``sleep`` advances it, so pacing is visible without waiting."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Middle:
    """A jitter-free rng: provider spacing is exactly 15 s."""

    def uniform(self, low: float, high: float) -> float:
        return 0.0


class Search:
    """A recording provider search. Its results never echo the query, so a privacy scan can tell them apart."""

    def __init__(self, clock: Clock, *, error: Exception | None = None) -> None:
        self.clock, self.error, self.calls = clock, error, []

    def __call__(self, query: str, limit: int):  # noqa: ANN204
        self.calls.append((query, limit, self.clock.now))
        if self.error is not None:
            raise self.error
        number = len(self.calls)
        return [result(f"r{number}x{n}", title=f"Quay result {number}-{n} tour") for n in range(3)]


def tab_of(channel: str, count: int) -> ChannelTabPage:
    return ChannelTabPage(entries=tuple(
        result(f"{channel[2:6]}{index:02d}", uploader_id=channel, uploader_url=f"https://www.youtube.com/channel/{channel}",
               published_at=NOW - timedelta(days=index))
        for index in range(count)
    ), has_more=False)


class Pages:
    """ChannelPages as the refresher uses it: ``peek_tab`` reads the cache, ``page`` is one extraction."""

    def __init__(self, clock: Clock, *, cached: dict | None = None, tabs: dict | None = None) -> None:
        self.clock, self.cached, self.tabs = clock, cached or {}, tabs or {}
        self.pages_called: list[str] = []
        self.times: list[datetime] = []

    def peek_tab(self, channel_id: str, tab: str, limit: int):  # noqa: ANN201
        assert (tab, limit) == ("videos", 60)
        return self.cached.get(channel_id)

    def page(self, channel_id: str, tab: str, limit: int):  # noqa: ANN201
        assert (tab, limit) == ("videos", 60)
        self.pages_called.append(channel_id)
        self.times.append(self.clock.now)
        return None, self.tabs[channel_id], self.clock.now.timestamp(), False


def make_profile(**patch) -> MemberProfile:  # noqa: ANN003
    """A MemberProfile with nothing in it (R3 builds the real one); ``patch`` overrides fields."""
    values = dict(
        user_id="a", built_at=NOW, generation=0, affinity={}, channel_aliases={}, followed=frozenset(), interests=frozenset(), satisfied=(),
        centroids=(), centroid_mass=(), taste_mix={}, item_fatigue={}, channel_fatigue={}, fatigued_out=frozenset(), fewer={}, spill={},
        hidden_items=frozenset(), hidden_channels=frozenset(), hidden_titles=frozenset(), excluded=frozenset(),
    )
    return MemberProfile(**(values | patch))


@pytest.fixture
def profile_of(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Swap R3's ProfileCache for a fake: ``profile_of["profile"] = make_profile(...)``."""
    holder = {"profile": make_profile()}
    monkeypatch.setattr(
        pool.reco_profile, "profiles", SimpleNamespace(get=lambda db, member_id, *, now: holder["profile"]), raising=False,
    )  # raising=False: the ProfileCache (profile.profiles) may not exist yet
    return holder


@pytest.fixture
def factory():
    sessions = memory_session_factory()
    with sessions() as session:
        seed_app_settings(session)
    return sessions


def make_refresher(factory, clock: Clock, search, pages) -> pool.RecoRefresher:  # noqa: ANN001
    return pool.RecoRefresher(factory, search, pages, clock=clock, sleep=clock.sleep, rng=Middle())


def feed_entry(video_id: str) -> dict:
    return {
        "id": video_id, "title": f"Feed upload {video_id}", "duration": 900, "thumbnail": None, "uploader": "Alpha Channel",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}", "availability": "public", "published_at": "2026-09-29T10:00:00",
        "capabilities": {"provider": "youtube", "lifecycle": "vod"}, "channel_id": CHANNEL_A, "view_count": 10,
    }


def seed_member(factory, *, follow: bool = True, satisfied: bool = True) -> None:  # noqa: ANN001
    """Member "a": one follow with two feed entries, and one finished video (a history anchor and a seed) in the documentaries category."""
    with factory.begin() as session:
        session.add(make_user("a"))
        if follow:
            session.add(SourceAutomation(
                id="follow-1", user_id="a", label="Alpha Channel", source_url=CHANNEL_KEY_A, source_type="channel",
                cron_expression="0 * * * *", active=True, feed_entries=[feed_entry("f1"), feed_entry("f2")],
            ))
        if satisfied:
            row = RemotePlaybackProgress(
                id="p-seed", user_id="a", source_identity="youtube:seed1", source_identity_key=key_of("seed1"),
                source_url="https://www.youtube.com/watch?v=seed1", extractor="youtube", remote_id="seed1", title="Zyxqwv repair gadget",
                uploader="Alpha Channel", duration_seconds=600, max_fraction=0.9, completed=False, last_watched_at=NOW - timedelta(days=2),
            )
            session.add(row)
            session.flush()
            pool.upsert_media(session, [replace(pool.media_from_progress(row), category_keys=("documentaries",))], now=NOW)


def pool_bits(factory, member: str = "a") -> list[int]:  # noqa: ANN001
    with factory() as session:
        return [row.sources for row in session.scalars(select(RecoPool).where(RecoPool.user_id == member))]


def test_a_refresh_nominates_every_source_within_the_provider_budget(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    profile_of["profile"] = make_profile(
        affinity={CHANNEL_KEY_B: 2.0, CHANNEL_KEY_C: 1.0, CHANNEL_KEY_A: 3.0, "name:youtube:legacy": 5.0, "https://www.youtube.com/channel/UCnegative": -1.0},
        followed=frozenset({CHANNEL_KEY_A}), interests=frozenset({"documentaries", "not-a-category"}),
    )
    search, pages = Search(clock), Pages(clock, cached={CHANNEL_C: tab_of(CHANNEL_C, 4)}, tabs={CHANNEL_B: tab_of(CHANNEL_B, 12)})

    outcome = make_refresher(factory, clock, search, pages).refresh("a")

    assert pages.pages_called == [CHANNEL_B]  # followed A, name-keyed and negative-affinity channels are not listed; C was a cache hit
    assert set(search.calls[0][0].split()) == {"zyxqwv", "repair", "gadget"}  # the seed's words
    assert search.calls[1][0].startswith("documentary films ")  # the interest phrase and one token
    assert [call[1] for call in search.calls] == [20, 20]
    assert (outcome.nominated, outcome.provider_calls, outcome.budget_hit, outcome.error) == (23, 3, False, None)
    assert Counter(pool_bits(factory)) == {SOURCE_FOLLOW: 2, SOURCE_CHANNEL: 14, SOURCE_SEED: 3, SOURCE_INTEREST: 3, SOURCE_HISTORY: 1}
    assert [pages.times[0], search.calls[0][2], search.calls[1][2]] == [NOW, NOW + timedelta(seconds=15), NOW + timedelta(seconds=30)]


def test_the_hourly_ceiling_ends_the_refresh_with_what_it_has(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    profile_of["profile"] = make_profile(affinity={CHANNEL_KEY_B: 2.0}, interests=frozenset({"documentaries"}))
    search, pages = Search(clock), Pages(clock, tabs={CHANNEL_B: tab_of(CHANNEL_B, 5)})
    refresher = make_refresher(factory, clock, search, pages)
    refresher._gate._calls.extend(NOW - timedelta(minutes=5) for _ in range(pool.CALLS_PER_HOUR))

    outcome = refresher.refresh("a")

    assert (outcome.provider_calls, outcome.budget_hit, outcome.error) == (0, True, None)
    assert search.calls == [] and pages.pages_called == []
    assert refresher.counters()["budget_hits_24h"] == 1
    assert Counter(pool_bits(factory)) == {SOURCE_FOLLOW: 2, SOURCE_HISTORY: 1}  # the follows cost no call and are still written


def test_the_daily_ceiling_counts_calls_older_than_an_hour(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    search = Search(clock)
    refresher = make_refresher(factory, clock, search, None)
    refresher._gate._calls.extend(NOW - timedelta(hours=3, seconds=n) for n in range(pool.CALLS_PER_DAY))

    outcome = refresher.refresh("a")

    assert (outcome.provider_calls, outcome.budget_hit) == (0, True) and search.calls == []


def test_search_busy_defers_five_minutes(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    search = Search(clock, error=SearchBusyError("busy"))
    refresher = make_refresher(factory, clock, search, None)

    first = refresher.refresh("a")

    assert (first.provider_calls, first.budget_hit, first.error) == (1, False, None)
    assert refresher._retry["a"] == refresher._gate.blocked_until() == clock.now + pool.BUSY_DEFER
    assert refresher.refresh("a").provider_calls == 0 and len(search.calls) == 1  # still deferred: no call at all
    clock.now += pool.BUSY_DEFER
    search.error = None
    assert refresher.refresh("a").provider_calls == 1 and len(search.calls) == 2


class Boom(Exception):
    def __init__(self, retry_after: object = None) -> None:
        super().__init__("boom")
        self.retry_after = retry_after


def failing(error: Exception):  # noqa: ANN201
    def run():  # noqa: ANN202
        raise error

    return run


def test_three_failures_back_off_and_retry_after_is_capped_at_six_hours() -> None:
    clock = Clock()
    gate = pool._ProviderGate(clock, clock.sleep, Middle())
    delays = []
    for _ in range(10):
        if gate.blocked_until():
            clock.now = max(clock.now, gate.blocked_until())
        with pytest.raises(pool._Skip):
            gate.call(failing(Boom()))
        blocked = gate.blocked_until()
        delays.append((blocked - clock.now).total_seconds() if blocked and blocked > clock.now else 0)
    assert delays == [0, 0, 60, 120, 240, 480, 960, 1920, 3600, 3600]  # two failures are skipped calls; then 60 s doubling to 1 h
    with pytest.raises(pool._Stop):
        gate.call(lambda: "blocked")

    for retry_after, expected in ((7200, timedelta(hours=2)), (99_999_999, timedelta(hours=6)), ("not a number", None)):
        clock = Clock()
        gate = pool._ProviderGate(clock, clock.sleep, Middle())
        with pytest.raises(pool._Skip):
            gate.call(failing(Boom(retry_after)))
        assert gate.blocked_until() == (None if expected is None else NOW + expected)  # one failure with a usable Retry-After backs off at once
    clock = Clock()
    gate = pool._ProviderGate(clock, clock.sleep, Middle())
    with pytest.raises(pool._Skip):
        gate.call(failing(Boom("Wed, 30 Sep 2026 14:00:00 GMT")))  # an HTTP date: two hours from NOW
    assert gate.blocked_until() == NOW + timedelta(hours=2)


def test_a_spent_youtube_budget_stops_the_refresh_like_a_busy_provider_at_background_priority() -> None:
    from app.services import provider_budget

    clock = Clock()
    gate = pool._ProviderGate(clock, clock.sleep, Middle())
    seen = []

    def spent():
        seen.append(provider_budget.current_priority())
        raise provider_budget.BudgetExhausted("background budget spent")

    with pytest.raises(pool._Stop):
        gate.call(spent)
    assert seen == ["background"] and gate.blocked_until() == NOW + pool.BUDGET_DEFER
    with pytest.raises(pool._Stop):
        gate.call(lambda: "blocked")


def test_a_seed_is_expanded_once_a_week(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    search = Search(clock)
    refresher = make_refresher(factory, clock, search, None)

    refresher.refresh("a")
    clock.now += timedelta(days=1)
    refresher.refresh("a")
    assert len(search.calls) == 1
    clock.now += timedelta(days=7)
    refresher.refresh("a")
    assert len(search.calls) == 2


def test_the_switch_off_stops_all_provider_work_and_writes_nothing(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    with factory() as session:
        session.get(AppSettings, 1).ai_features_disabled = ["personal_recommendations"]
        session.commit()
    search, pages = Search(clock), Pages(clock)
    refresher = make_refresher(factory, clock, search, pages)

    assert refresher.refresh("a") == pool.RefreshResult("a", 0, 0, False)

    assert search.calls == [] and pages.pages_called == [] and pool_bits(factory) == []


def test_a_failing_plan_returns_the_error_name_and_does_not_raise(factory, profile_of, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    seed_member(factory)
    refresher = make_refresher(factory, clock, Search(clock), None)
    monkeypatch.setattr(refresher, "_read_plan", lambda db, member_id, now: (_ for _ in ()).throw(RuntimeError("secret detail")))

    outcome = refresher.refresh("a")

    assert (outcome.error, outcome.nominated) == ("RuntimeError", 0)  # the type name only: never the message


def test_due_members_follow_the_refresh_rules(factory) -> None:
    clock = Clock()
    with factory.begin() as session:
        for member in ("fresh", "aged", "never", "idle", "watcher", "quiet"):
            session.add(make_user(member))
        session.add(make_user("gone", is_active=False))
        for member in ("fresh", "aged", "never", "watcher", "quiet", "gone"):
            session.add(AppSession(id=f"s-{member}", user_id=member, expires_at=NOW + timedelta(days=1), last_seen_at=NOW - timedelta(hours=1)))
        session.add(AppSession(id="s-idle", user_id="idle", expires_at=NOW + timedelta(days=1), last_seen_at=NOW - timedelta(days=4)))  # active window is 3 days
        for member, age in (("fresh", timedelta(hours=1)), ("aged", timedelta(hours=7)), ("watcher", timedelta(hours=2)), ("quiet", timedelta(hours=2)), ("gone", timedelta(hours=9))):
            session.add(RecoPool(user_id=member, item_key=key_of(member), sources=SOURCE_FOLLOW, first_seen_at=NOW - age, last_nominated_at=NOW - age))
        for member, finished in (("watcher", timedelta(minutes=30)), ("quiet", timedelta(hours=3))):
            session.add(RemotePlaybackProgress(
                id=f"done-{member}", user_id=member, source_identity=f"youtube:{member}", source_identity_key=key_of(member), source_url="https://example.com/v",
                completed=True, last_watched_at=NOW - finished, updated_at=NOW - finished,
            ))
    refresher = make_refresher(factory, clock, Search(clock), None)

    with factory() as db:
        # never: no pool. aged: 7 h. watcher: finished a video after its pool was written. Not: fresh, quiet (finished before its pool), idle, gone.
        assert refresher._due_members(db, NOW) == ["never", "aged", "watcher"]

    refresher.trigger("fresh")  # debounced from the first trigger (10 min), then at most one trigger-driven refresh an hour
    with factory() as db:
        assert "fresh" not in refresher._due_members(db, NOW + timedelta(minutes=9))
        assert "fresh" in refresher._due_members(db, NOW + timedelta(minutes=11))
    refresher._last_refresh["fresh"] = NOW + timedelta(minutes=11)
    with factory() as db:
        assert "fresh" not in refresher._due_members(db, NOW + timedelta(minutes=40))
        assert "fresh" in refresher._due_members(db, NOW + timedelta(minutes=72))


def test_schedule_due_is_non_blocking_and_single_flight(factory, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    refresher = make_refresher(factory, clock, Search(clock), None)
    started, release, runs = threading.Event(), threading.Event(), []

    def blocked_run() -> None:
        runs.append(1)
        started.set()
        release.wait(5)

    monkeypatch.setattr(refresher, "_run_due", blocked_run)
    try:
        refresher.schedule_due()
        assert started.wait(5)
        refresher.schedule_due()  # a run is in flight: nothing is queued behind it
        release.set()
        wait_until(lambda: not refresher._scheduled)
        assert runs == [1]
        refresher.schedule_due()
        wait_until(lambda: len(runs) == 2)
    finally:
        release.set()
        refresher.close()


def test_warm_channel_loads_a_missing_tab_once_and_never_on_a_hit(factory, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    pages = Pages(clock, cached={CHANNEL_C: tab_of(CHANNEL_C, 3)}, tabs={CHANNEL_B: tab_of(CHANNEL_B, 3)})
    refresher = make_refresher(factory, clock, Search(clock), pages)
    monkeypatch.setattr(refresher, "_submit", lambda function, *args: (function(*args), True)[1])  # run on this thread

    refresher.warm_channel(CHANNEL_B)
    refresher.warm_channel(CHANNEL_C)  # cached: no load
    refresher.warm_channel("not-a-channel-id")  # refused before anything is queued
    refresher._warming.add(CHANNEL_A)
    refresher.warm_channel(CHANNEL_A)  # already in flight: no second load

    assert pages.pages_called == [CHANNEL_B]


def test_close_during_a_running_refresh_makes_no_further_provider_call(factory, profile_of) -> None:
    clock = Clock()
    seed_member(factory)
    profile_of["profile"] = make_profile(affinity={CHANNEL_KEY_B: 2.0}, interests=frozenset({"documentaries"}))
    search, pages = Search(clock), Pages(clock, tabs={CHANNEL_B: tab_of(CHANNEL_B, 3)})
    pacing, release = threading.Event(), threading.Event()

    def blocked_sleep(seconds: float) -> None:  # the 15 s pacing before the second call: hold it until close()
        pacing.set()
        release.wait(5)
        clock.sleep(seconds)

    refresher = pool.RecoRefresher(factory, search, pages, clock=clock, sleep=blocked_sleep, rng=Middle())
    try:
        refresher.schedule_due()
        assert pacing.wait(5)
        refresher.close()
        release.set()
        wait_until(lambda: not refresher._scheduled)
        assert (pages.pages_called, search.calls) == ([CHANNEL_B], [])  # the paced call never went out
    finally:
        release.set()
        refresher.close()


CHANNEL_B = "UC" + "b" * 22


def keys_of(candidates) -> set[str]:  # noqa: ANN001
    return {candidate.key for candidate in candidates}


def add_pooled(db, member: str, video_id: str, *, bits: int = SOURCE_FOLLOW, nominated: datetime = NOW, first_seen: datetime | None = None, **media) -> RemoteMedia:  # noqa: ANN001, ANN003
    """A remote_media row and the member's pool row for it, written directly; ``media`` overrides row columns."""
    row = db.get(RemoteMedia, key_of(video_id)) or RemoteMedia(
        key=key_of(video_id), source_identity=f"youtube:{video_id}", extractor="youtube", remote_id=video_id,
        webpage_url=f"https://www.youtube.com/watch?v={video_id}", title=f"Video {video_id} about harbour cranes", uploader="Alpha Channel",
        channel_key=CHANNEL_KEY_A, duration=600, view_count=1_000, published_at=NOW - timedelta(days=1), kind="video",
        category_keys=[], tokens=["harbour"], fetched_at=NOW, last_nominated_at=NOW,
    )
    for column, value in media.items():
        setattr(row, column, value)
    db.add(row)
    db.add(RecoPool(user_id=member, item_key=row.key, sources=bits, first_seen_at=first_seen or nominated, last_nominated_at=nominated))
    db.flush()
    return row


def test_a_member_never_receives_an_item_only_another_pools(db) -> None:
    db.add_all([make_user("a"), make_user("b")])
    add_pooled(db, "a", "only_a", bits=SOURCE_SEED)
    add_pooled(db, "a", "both")
    add_pooled(db, "b", "both")
    add_pooled(db, "b", "only_b")
    db.commit()

    for_a, for_b = pool.remote_candidates(db, "a"), pool.remote_candidates(db, "b")

    assert keys_of(for_a) == {key_of("only_a"), key_of("both")}
    assert keys_of(for_b) == {key_of("both"), key_of("only_b")}  # the shared remote_media row is public; a's seed item is not b's candidate
    assert {c.key: c.sources for c in for_a} == {key_of("only_a"): SOURCE_SEED, key_of("both"): SOURCE_FOLLOW}  # each member's own bits
    assert pool.remote_candidates(db, "nobody") == []


def test_a_channel_percentile_counts_only_the_members_own_rows(db) -> None:
    db.add_all([make_user("a"), make_user("b")])
    for n in range(4):
        add_pooled(db, "a", f"a{n}", view_count=100 * (n + 1))
    for n in range(6):
        add_pooled(db, "b", f"b{n}", view_count=10 * (n + 1))  # the same channel, in b's pool only
    db.commit()

    assert {c.channel_view_percentile for c in pool.remote_candidates(db, "a")} == {None}  # 4 rows: under 5, whatever b holds
    add_pooled(db, "a", "a4", view_count=500)
    db.commit()
    percentiles = {c.key: c.channel_view_percentile for c in pool.remote_candidates(db, "a")}
    assert percentiles[key_of("a4")] == 1.0 and percentiles[key_of("a0")] == 0.0 and percentiles[key_of("a2")] == 0.5


def test_remote_candidates_leave_out_what_a_member_cannot_or_should_not_be_offered(db) -> None:
    db.add_all([make_user("a"), make_user("b")])
    for video_id in ("ok", "theirs", "gone"):
        add_pooled(db, "a", video_id)
    add_pooled(db, "a", "anchor", bits=SOURCE_HISTORY)
    add_pooled(db, "a", "short1", kind="short")
    add_pooled(db, "a", "live1", kind="live")
    add_pooled(db, "a", "private1", availability="Private")
    add_pooled(db, "a", "tiny", duration=20)
    add_pooled(db, "a", "saved")
    db.add_all([
        LibraryItem(id="i1", user_id="a", visibility="private", extractor="YouTube", remote_id="saved", title="x", status="available"),
        LibraryItem(id="i2", user_id="b", visibility="private", extractor="youtube", remote_id="theirs", title="x", status="available"),
        LibraryItem(id="i3", user_id="a", visibility="private", extractor="youtube", remote_id="gone", title="x", status="missing"),
    ])
    db.commit()

    # a history anchor, shorts, live, unplayable and too-short videos and a saved copy are out; another member's private copy and a missing one are not.
    assert keys_of(pool.remote_candidates(db, "a")) == {key_of("ok"), key_of("theirs"), key_of("gone")}


def test_as_of_hides_what_was_not_there_yet(db) -> None:
    db.add(make_user("a"))
    add_pooled(db, "a", "old", first_seen=NOW - timedelta(days=5), published_at=NOW - timedelta(days=6))
    add_pooled(db, "a", "late_pool", first_seen=NOW - timedelta(days=1), published_at=NOW - timedelta(days=6))
    add_pooled(db, "a", "late_publish", first_seen=NOW - timedelta(days=5), published_at=NOW - timedelta(days=1))
    db.commit()

    assert keys_of(pool.remote_candidates(db, "a", as_of=NOW - timedelta(days=2))) == {key_of("old")}
    assert len(pool.remote_candidates(db, "a")) == 3


def test_popular_candidates_are_household_rows_with_no_pool_entry(db) -> None:
    live = SimpleNamespace(lifecycle="live")
    items = [
        popular_item("pop1", ("music",)), popular_item("pop2", duration=30), popular_item("pop3", availability="private"),
        popular_item("pop4", capabilities=live), popular_item("pop1", ("music",)),
    ]

    candidates = pool.popular_candidates(db, items)

    assert [(c.key, c.sources) for c in candidates] == [(key_of("pop1"), SOURCE_POPULAR)]  # one row, deduplicated; shorts, live and private are out
    assert db.scalar(select(func.count()).select_from(RecoPool)) == 0
    row = db.get(RemoteMedia, key_of("pop1"))
    assert row.category_keys == ["music"] and row.tokens
    touched = row.last_nominated_at
    pool.popular_candidates(db, items)
    assert db.get(RemoteMedia, key_of("pop1")).last_nominated_at == touched  # a fresh row is not written again
    pool.popular_candidates(db, [popular_item("pop1", ("music", "gaming"))])
    assert db.get(RemoteMedia, key_of("pop1")).category_keys == ["gaming", "music"]  # a new category is
    row.last_nominated_at = utcnow() - timedelta(hours=7)
    db.commit()
    pool.popular_candidates(db, items)
    assert db.get(RemoteMedia, key_of("pop1")).last_nominated_at > touched - timedelta(seconds=1)  # an untouched row is renominated


def test_a_popular_item_with_an_aware_publish_time_ranks_beside_naive_now(db) -> None:
    """Popular items carry tz-aware publish times; the cached row and its candidate must be naive UTC like every other timestamp."""
    from datetime import UTC

    candidate = pool.popular_candidates(db, [popular_item("aware1", ("music",), published_at=datetime(2026, 9, 1, tzinfo=UTC))])[0]
    assert candidate.published_at == datetime(2026, 9, 1) and candidate.published_at.tzinfo is None


def test_listing_candidates_cache_the_peeked_page_without_a_pool_row(db) -> None:
    candidates = pool.listing_candidates(db, tab_of(CHANNEL_B, 6))

    assert len(candidates) == 6 and {c.sources for c in candidates} == {SOURCE_CURRENT_CHANNEL}
    assert all(c.channel_view_percentile is not None for c in candidates)  # six rows of one channel with views
    assert db.scalar(select(func.count()).select_from(RecoPool)) == 0
    assert db.scalar(select(func.count()).select_from(RemoteMedia)) == 6
    assert pool.listing_candidates(db, None) == []
    assert pool.listing_candidates(db, ChannelTabPage(entries=(), has_more=False, restricted=True)) == []


def test_main_owns_one_refresher_whose_search_is_the_guarded_provider_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(main.reco_refresher, pool.RecoRefresher)

    class Busy:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        def youtube_search(self, query: str, limit: int, *, cache: bool = True):  # noqa: ANN201
            raise SearchBusyError("busy")

    monkeypatch.setattr(main, "YtDlpService", Busy)
    with pytest.raises(SearchBusyError):  # the refresher defers on busy: it must not be swallowed here
        main._reco_search("any", 5)


def test_the_maintenance_cycle_schedules_the_refresher_and_the_lifespan_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    db_module.init_db()
    scheduled: list[int] = []
    monkeypatch.setattr(main.reco_refresher, "schedule_due", lambda: scheduled.append(1))
    monkeypatch.setattr(main, "start_embedding_backfill", lambda: None)

    main.run_library_maintenance_cycle()

    assert scheduled == [1]
    assert "reco_refresher.close" in inspect.getsource(main.lifespan)
    main.reco_refresher.close()
    main.reco_refresher.close()  # closing twice is harmless


# ---- title candidates ----
def library(db) -> None:  # noqa: ANN001
    """Owner "owner" and member "a". Sci-fi titles are old or new; sixty newer drama fillers push the old ones out of the newest 50."""
    db.add_all([make_user("owner"), make_user("a")])
    add_movie(db, "m-scifi", "Starfall", genres=["Science Fiction"], days=1, boxset_id="box")
    add_movie(db, "m-other", "Quiet Farm", genres=["Drama"], days=2)
    add_movie(db, "m-started", "Half Seen", genres=["Science Fiction"], days=3)
    add_movie(db, "m-private", "Home Tape", owner="owner", visibility="private", genres=["Science Fiction"], days=4)
    add_movie(db, "m-hidden", "Muted", genres=["Science Fiction"], days=4)
    add_series(db, "s-scifi", "Deep Void", seasons={1: 2}, genres=["Science Fiction"], days=5)
    add_movie(db, "m-old-scifi", "Ancient Orbit", genres=["Science Fiction"], days=-100)
    add_movie(db, "m-old-drama", "Ancient Field", genres=["Drama"], days=-101)
    add_movie(db, "m-old-box", "Boxed Cousin", genres=["Western"], days=-102, boxset_id="box")
    for n in range(1, 61):
        add_movie(db, f"f{n:02d}", f"Filler {n}", genres=["Drama"], days=-n)
    add_progress(db, "a", "m-started-v", completed=False)
    db.commit()


def finished_scifi(db) -> SatisfiedItem:  # noqa: ANN001
    tokens = frozenset(token for token in pool.reco_profile.title_tokens(db.get(MediaTitle, "s-scifi")) if token.startswith("genre:"))
    return SatisfiedItem(key="seen", target_kind="title", title="Seen", channel_key=None, tokens=tokens, weight=1.0, at=BASE)


def test_title_candidates_union_taste_overlap_and_arrivals_and_skip_what_must_be_skipped(db) -> None:
    library(db)
    profile = make_profile(satisfied=(finished_scifi(db),), hidden_titles=frozenset({"m-hidden"}))

    keys = keys_of(pool.title_candidates(db, db.get(User, "a"), profile))

    assert {"m-scifi", "m-other", "s-scifi", "f01", "f47", "m-old-scifi"} <= keys  # the 50 newest, plus an old title by overlap
    assert not keys & {"m-started", "m-private", "m-hidden", "f48", "m-old-drama", "m-old-box"}  # started, invisible, suppressed, neither newest nor alike
    assert not keys & {"s-scifi-s1", "s-scifi-s1e1"}  # movies and series only


def test_an_anchor_adds_its_neighbours_and_boxset_and_is_never_its_own_candidate(db) -> None:
    library(db)

    keys = keys_of(pool.title_candidates(db, db.get(User, "a"), make_profile(), anchor=db.get(MediaTitle, "m-scifi")))

    assert "m-scifi" not in keys
    assert {"m-old-scifi", "m-old-box"} <= keys  # a shared genre, and the same boxset with nothing else in common
    assert "m-old-drama" not in keys


def test_as_of_leaves_out_titles_that_arrived_later(db) -> None:
    library(db)

    keys = keys_of(pool.title_candidates(db, db.get(User, "a"), make_profile(), as_of=BASE + timedelta(days=2)))

    assert "m-scifi" in keys and "m-other" in keys and "s-scifi" not in keys


def test_taste_retrieval_takes_the_nearest_vectors_to_a_centroid(db, monkeypatch: pytest.MonkeyPatch) -> None:
    db.add_all([make_user("owner"), make_user("a")])
    for title_id in ("t1", "t2", "t3"):
        add_movie(db, title_id, title_id.upper(), genres=["Drama"])
    db.commit()
    stored = {"t1": ("title", array("f", [1.0, 0.0])), "t2": ("title", array("f", [0.6, 0.8])), "t3": ("title", array("f", [0.0, 1.0]))}
    monkeypatch.setattr(embeddings, "serving", lambda session: SimpleNamespace(model_id="m"))
    monkeypatch.setattr(embeddings, "vector_map", lambda session, model_id: stored)
    monkeypatch.setattr(pool, "TITLE_NEWEST", 0)
    monkeypatch.setattr(pool, "TITLE_BY_OVERLAP", 0)
    profile = make_profile(centroids=(array("f", [1.0, 0.0]),))

    monkeypatch.setattr(pool, "TITLE_BY_TASTE", 1)
    nearest = pool.title_candidates(db, db.get(User, "a"), profile)
    monkeypatch.setattr(pool, "TITLE_BY_TASTE", 2)
    two = pool.title_candidates(db, db.get(User, "a"), profile)

    assert keys_of(nearest) == {"t1"} and list(nearest[0].vector) == [1.0, 0.0]
    assert keys_of(two) == {"t1", "t2"}


def test_cached_title_tokens_follow_a_metadata_edit_and_a_raw_category_rewrite(db) -> None:
    db.add_all([make_user("owner"), make_user("a")])
    title = add_movie(db, "m", "Movie", genres=["Drama"])
    db.commit()
    member = db.get(User, "a")
    tokens = lambda: next(iter(pool.title_base(db, member).titles)).tokens  # noqa: E731

    assert "genre:drama" in tokens()
    title.metadata_json = {**title.metadata_json, "genres": ["Western"]}  # an ORM write bumps updated_at
    db.commit()
    assert "genre:western" in tokens() and "genre:drama" not in tokens()
    db.execute(update(MediaTitle).values(category="anime").execution_options(synchronize_session=False))  # as db.CATEGORY_SQL does
    db.commit()
    assert "cat:anime" in tokens() and "cat:movies" not in tokens()


def test_a_request_builds_the_title_base_once_for_all_its_lists(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.reco import policy as policy_module

    library(db)
    add_progress(db, "a", "m-old-drama-v", completed=True, at=utcnow() - timedelta(days=1))
    add_progress(db, "a", "f01-v", completed=True, at=utcnow() - timedelta(days=2))
    db.commit()
    built: list[int] = []
    real = policy_module.title_base
    monkeypatch.setattr(policy_module, "title_base", lambda *args, **kwargs: built.append(1) or real(*args, **kwargs))

    rows = policy_module.RecommendationPolicy(db, refresher=None, channel_pages=None).title_rows(db.get(User, "a"))

    assert [kind for kind, _anchor, _served in rows] == ["because_you_watched", "because_you_watched", "recommended"]
    assert built == [1]


def all_text(factory) -> str:  # noqa: ANN001
    """Every value of every table, lower-cased and joined: where a leaked query would show up."""
    parts: list[str] = []
    with factory() as session:
        for table in Base.metadata.sorted_tables:
            parts.extend(" ".join(str(value) for value in row) for row in session.execute(select(table)).all())
    return "\n".join(parts).lower()


def test_no_query_is_persisted_or_logged(factory, profile_of, caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    seed_member(factory)
    profile_of["profile"] = make_profile(interests=frozenset({"documentaries"}))
    search = Search(clock)

    with caplog.at_level(logging.DEBUG):
        outcome = make_refresher(factory, clock, search, None).refresh("a")

    assert outcome.error is None and len(search.calls) == 2  # a seed expansion and an interest query
    stored, logged = all_text(factory), caplog.text.lower()
    for query, _limit, _at in search.calls:
        assert query.lower() not in stored  # not a column, not a JSON value, not a cache key
        assert query.lower() not in logged  # logs carry counts only
    assert "recommendation refresh: nominated=" in logged


def test_no_loader_calls_a_provider_or_an_embedder(db, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("a request thread reached a provider or an embedder")

    monkeypatch.setattr(YtDlpService, "youtube_search", forbidden)
    monkeypatch.setattr(embeddings, "query_vector", forbidden)
    monkeypatch.setattr(embeddings, "embed", forbidden)
    monkeypatch.setattr(local_ai, "embed", forbidden)
    db.add_all([make_user("owner"), make_user("a")])
    add_movie(db, "m", "Movie", genres=["Drama"])
    add_pooled(db, "a", "v1")
    db.commit()
    member = db.get(User, "a")

    assert pool.remote_candidates(db, "a")
    assert pool.popular_candidates(db, [popular_item("p1")])
    assert pool.listing_candidates(db, tab_of(CHANNEL_B, 2))
    assert pool.title_candidates(db, member, make_profile())


def plan_of(db, action) -> str:  # noqa: ANN001
    """The EXPLAIN QUERY PLAN lines of every SELECT, UPDATE and DELETE ``action`` runs, on a populated, unanalyzed database."""
    engine, captured = db.get_bind(), []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    connection, lines = db.connection(), []
    for statement, parameters in captured:
        if statement.lstrip().upper().startswith(("SELECT", "UPDATE", "DELETE")):
            lines.extend(str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))
    return "\n".join(lines)


def assert_no_scan(plan: str, table: str) -> None:
    offenders = [line for line in plan.splitlines() if f"SCAN {table}" in line and "USING" not in line]
    assert not offenders, f"unindexed scan of {table}:\n{plan}"


def test_query_plans_seek_the_pool_indexes(db) -> None:
    members = ("m1", "m2", "m3")
    db.add_all(make_user(member) for member in members)
    for member in members:
        for n in range(400):
            add_pooled(db, member, f"{member}v{n:03d}", nominated=NOW - timedelta(minutes=n))
    db.add(LibraryItem(id="lib1", user_id="m1", visibility="shared", extractor="youtube", remote_id="m1v000", title="x", status="available"))
    db.commit()
    refresher = pool.RecoRefresher(lambda: None, Search(Clock()), None)

    candidates = plan_of(db, lambda: pool.remote_candidates(db, "m1"))
    documents = plan_of(db, lambda: embeddings._remote_documents(db, "model", 32, now=NOW))
    swept = plan_of(db, lambda: pool.sweep(db, now=NOW))
    due = plan_of(db, lambda: refresher._due_members(db, NOW))

    for plan in (candidates, documents, swept, due):
        for table in ("reco_pool", "remote_media", "library_items"):
            assert_no_scan(plan, table)
    assert "ix_remote_media_nominated" in documents
    assert "ix_reco_pool_user_nominated" in candidates or "reco_pool USING" in candidates
