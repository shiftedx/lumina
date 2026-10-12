from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.popular_discovery import PUBLIC_PROVIDER_ERROR, PopularCategory, PopularDiscovery


@dataclass
class _Completed:
    def done(self) -> bool:
        return True


class _ImmediateExecutor:
    def submit(self, function):  # noqa: ANN001
        function()
        return _Completed()


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _QueuedExecutor:
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, function):  # noqa: ANN001
        self.tasks.append(function)
        return _Completed()

    def run_next(self) -> None:
        self.tasks.pop(0)()


def _category(key: str) -> PopularCategory:
    return PopularCategory(key=key, label=key.title(), query=f"query {key}")


def test_cold_feed_refreshes_only_one_small_keyless_category_batch(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def search(query: str, limit: int):
        calls.append((query, limit))
        return {"items": [{"id": f"{query}-{n}", "title": query, "view_count": 10} for n in range(8)]}

    snapshot = PopularDiscovery(
        tmp_path / "popular.json",
        search,
        executor=_ImmediateExecutor(),
        clock=lambda: 1_700_000_000.0,
        random=lambda: 0.5,
        batch_size=3,
        max_concurrency=1,
    ).get_snapshot()

    assert len(calls) == 3  # full rows need no related-query backfill
    assert all(limit == 40 for _query, limit in calls)
    assert snapshot.state == "partial"
    assert snapshot.refreshing is False
    assert len(snapshot.items) == 24
    assert (tmp_path / "popular.json").is_file()


def test_popular_snapshot_preserves_safe_source_capabilities(tmp_path: Path) -> None:
    snapshot = PopularDiscovery(
        tmp_path / "popular.json",
        lambda _query, _limit: {"items": [{
            "id": "live-1", "title": "Live now", "source": "youtube",
            "capabilities": {
                "provider": "youtube", "lifecycle": "live", "can_play": False,
                "play_reason": "live_playback_not_supported", "can_acquire": False,
                "acquire_reason": "live_acquisition_not_supported",
                "chat": {"live": "unavailable", "replay": "unavailable"},
            },
        }]},
        categories=(_category("one"),), executor=_ImmediateExecutor(), clock=lambda: 1_700_000_000.0,
        random=lambda: 0.5, batch_size=1, max_concurrency=1,
    ).get_snapshot()

    assert snapshot.items[0].capabilities is not None
    assert snapshot.items[0].capabilities.lifecycle == "live"
    assert snapshot.items[0].capabilities.can_acquire is False


def test_popular_snapshot_preserves_channel_identity_for_follow_derivation(tmp_path: Path) -> None:
    # #87 derives followable channel candidates from the fresh category
    # discovery snapshot, so the channel address and id must survive the
    # normalize -> persist -> rebuild round trip rather than being dropped
    # with the rest of the raw provider metadata.
    snapshot = PopularDiscovery(
        tmp_path / "popular.json",
        lambda _query, _limit: {"items": [{
            "id": "vid-1", "title": "A video", "uploader": "Veritasium",
            "uploader_url": "https://www.youtube.com/@veritasium",
            "uploader_id": "@veritasium", "source": "youtube",
        }]},
        categories=(_category("one"),), executor=_ImmediateExecutor(), clock=lambda: 1_700_000_000.0,
        random=lambda: 0.5, batch_size=1, max_concurrency=1,
    ).get_snapshot()

    assert snapshot.items[0].uploader == "Veritasium"
    assert snapshot.items[0].uploader_url == "https://www.youtube.com/@veritasium"
    assert snapshot.items[0].uploader_id == "@veritasium"


def test_warm_snapshot_survives_restart_and_missing_categories_warm_at_the_batch_rate(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    calls: list[str] = []
    categories = (_category("one"), _category("two"))

    def search(query: str, _limit: int):
        calls.append(query)
        return [{"id": query, "title": query}]

    arguments = {
        "categories": categories,
        "executor": _ImmediateExecutor(),
        "clock": clock,
        "random": lambda: 0.5,
        "batch_size": 1,
        "max_concurrency": 1,
        "batch_interval_seconds": 30,
        "category_ttl_seconds": 100,
    }
    path = tmp_path / "popular.json"

    first = PopularDiscovery(path, search, **arguments).get_snapshot()
    restarted = PopularDiscovery(path, search, **arguments)
    before_gate = restarted.get_snapshot()
    clock.now = 129.0
    restarted.get_snapshot()

    assert calls == ["query one"]
    assert first.state == "partial"
    assert before_gate.items == first.items

    clock.now = 130.0
    warmed = restarted.get_snapshot()
    persisted = PopularDiscovery(path, lambda *_args: (_ for _ in ()).throw(AssertionError("warm cache searched")), **arguments).get_snapshot()

    assert calls == ["query one", "query two"]
    assert warmed.state == "ready"
    assert persisted.state == "ready"
    assert [item.id for item in persisted.items] == ["query one", "query two"]


def test_repeated_instances_coalesce_one_background_refresh(tmp_path: Path) -> None:
    executor = _QueuedExecutor()
    calls = 0

    def search(_query: str, _limit: int):
        nonlocal calls
        calls += 1
        return [{"id": "only", "title": "Only"}]

    arguments = {
        "categories": (_category("one"),),
        "executor": executor,
        "clock": lambda: 100.0,
        "random": lambda: 0.5,
        "max_concurrency": 1,
    }
    first_service = PopularDiscovery(tmp_path / "popular.json", search, **arguments)
    second_service = PopularDiscovery(tmp_path / "popular.json", search, **arguments)

    first = first_service.get_snapshot()
    second = second_service.get_snapshot()

    assert first.state == "loading"
    assert first.refreshing is True
    assert second.refreshing is True
    assert len(executor.tasks) == 1
    assert calls == 0

    executor.run_next()
    refreshed = second_service.get_snapshot()

    assert calls == 1
    assert refreshed.state == "ready"
    assert refreshed.refreshing is False


def test_retry_after_and_exponential_backoff_keep_the_last_success_without_leaking_errors(tmp_path: Path) -> None:
    class ProviderUnavailable(RuntimeError):
        headers = {"Retry-After": "40"}

    clock = _Clock(1_000.0)
    calls = 0

    def search(_query: str, _limit: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"id": "survivor", "title": "Still useful", "view_count": 20}]
        if calls == 2:
            raise ProviderUnavailable("cookie=/private/auth.txt token=secret-provider-detail")
        raise RuntimeError("another technical provider failure")

    service = PopularDiscovery(
        tmp_path / "popular.json",
        search,
        categories=(_category("one"),),
        executor=_ImmediateExecutor(),
        clock=clock,
        random=lambda: 0.5,
        max_concurrency=1,
        category_ttl_seconds=10,
        batch_interval_seconds=0,
        backoff_base_seconds=5,
        backoff_max_seconds=20,
    )

    assert service.get_snapshot().state == "ready"
    clock.now += 11
    stale = service.get_snapshot()

    assert calls == 2
    assert stale.state == "stale"
    assert stale.stale is True
    assert [item.id for item in stale.items] == ["survivor"]
    assert stale.error == PUBLIC_PROVIDER_ERROR
    assert stale.next_refresh_at == datetime.fromtimestamp(clock.now + 40, UTC)
    persisted = (tmp_path / "popular.json").read_text(encoding="utf-8")
    assert "private/auth" not in persisted
    assert "secret-provider-detail" not in persisted

    clock.now += 39
    service.get_snapshot()
    assert calls == 2
    clock.now += 1
    second_failure = service.get_snapshot()
    assert calls == 3
    assert second_failure.state == "stale"
    assert second_failure.next_refresh_at == datetime.fromtimestamp(clock.now + 10, UTC)


def test_partial_categories_deduplicate_and_rank_signals_then_round_robin_fallback(tmp_path: Path) -> None:
    categories = (_category("one"), _category("two"), _category("three"), _category("four"))
    now = 1_700_000_000.0

    def search(query: str, _limit: int):
        if query == "query one":
            return [
                {"id": "duplicate", "title": "Popular", "view_count": 1_000_000},
                {"id": "one-a", "title": "One A"},
                {"id": "one-b", "title": "One B"},
            ]
        if query == "query two":
            return [
                {"id": "duplicate", "title": "Popular", "uploader": "Creator", "view_count": 2_000_000},
                {"id": "two-a", "title": "Two A"},
            ]
        if query == "query three":
            return [
                {"id": "recent", "title": "Recent", "published_at": datetime.fromtimestamp(now - 60, UTC)},
                {"id": "three-a", "title": "Three A"},
            ]
        raise RuntimeError("technical provider failure with credentials")

    snapshot = PopularDiscovery(
        tmp_path / "popular.json",
        search,
        categories=categories,
        executor=_ImmediateExecutor(),
        clock=lambda: now,
        random=lambda: 0.5,
        batch_size=4,
        max_concurrency=1,
        feed_limit=10,
    ).get_snapshot()

    assert snapshot.state == "partial"
    assert snapshot.error == PUBLIC_PROVIDER_ERROR
    assert [category.state for category in snapshot.categories] == ["ready", "ready", "ready", "failed"]
    assert [item.id for item in snapshot.items] == [
        "duplicate",
        "recent",
        "one-a",
        "two-a",
        "three-a",
        "one-b",
    ]
    duplicate = snapshot.items[0]
    assert duplicate.view_count == 2_000_000
    assert duplicate.uploader == "Creator"
    assert duplicate.category_keys == ("one", "two")


def test_owned_executor_restarts_cleanly_after_shutdown(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    calls = 0
    first_complete = threading.Event()
    second_complete = threading.Event()

    def search(query: str, _limit: int):
        nonlocal calls
        calls += 1
        (first_complete if calls == 1 else second_complete).set()
        return [{"id": f"{query}-{calls}", "title": query}]

    service = PopularDiscovery(
        tmp_path / "popular.json", search, categories=(_category("one"),),
        clock=clock, random=lambda: 0.5, batch_size=1, max_concurrency=1,
        category_ttl_seconds=1, batch_interval_seconds=0,
    )
    service.get_snapshot()
    assert first_complete.wait(timeout=2)
    service.close()

    clock.now += 2
    service.get_snapshot()
    assert second_complete.wait(timeout=2)
    service.close()
    assert calls == 2


def test_owned_executor_shutdown_waits_for_an_active_refresh(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def search(_query: str, _limit: int):
        entered.set()
        assert release.wait(timeout=2)
        return [{"id": "one", "title": "One"}]

    service = PopularDiscovery(
        tmp_path / "popular.json", search, categories=(_category("one"),),
        batch_size=1, max_concurrency=1,
    )
    service.get_snapshot()
    assert entered.wait(timeout=2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(service.close)
        assert not closing.done()
        release.set()
        closing.result(timeout=2)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"id": " abc ", "source": " SoundCloud "}, "id:soundcloud:abc"),
        ({"webpage_url": " https://youtu.be/dQw4w9WgXcQ "}, "id:youtube:dQw4w9WgXcQ"),
        ({"webpage_url": "https://M.YouTube.com/watch?v=dQw4w9WgXcQ&t=5"}, "id:youtube:dQw4w9WgXcQ"),
        ({"webpage_url": "HTTPS://Example.COM/a/?q=1#frag"}, "url:https://example.com/a?q=1"),
        ({"webpage_url": "http://[::1"}, "url:http://[::1"),
        ({"title": " Song ", "uploader": " Artist "}, "text:youtube:song:artist"),
    ],
)
def test_dedupe_key_is_the_recommendation_source_key(item, expected) -> None:  # noqa: ANN001
    # One identity everywhere: Popular dedupe and member recommendations agree byte-for-byte.
    assert PopularDiscovery._dedupe_key(item) == expected
