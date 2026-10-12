from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.schemas import YouTubeSearchResult
from app.services.followed_live import FollowedLiveChecker, live_probe_url


class ImmediateExecutor:
    """Runs submitted work synchronously so tests are deterministic."""

    def submit(self, fn, *args):
        class Done:
            pass

        fn(*args)
        return Done()

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def _entry(url: str) -> YouTubeSearchResult:
    return YouTubeSearchResult(id=url, title="live now", webpage_url=url)


def test_live_probe_url_shapes() -> None:
    assert live_probe_url("https://www.youtube.com/@handle") == "https://www.youtube.com/@handle/live"
    assert live_probe_url("https://www.youtube.com/channel/UC123/") == "https://www.youtube.com/channel/UC123/live"
    assert live_probe_url("https://www.youtube.com/@handle/live") == "https://www.youtube.com/@handle/live"
    assert live_probe_url("https://www.twitch.tv/somestreamer") == "https://www.twitch.tv/somestreamer"


def test_first_call_returns_empty_and_schedules_probe() -> None:
    calls: list[str] = []

    def probe(url: str):
        calls.append(url)
        return _entry(url)

    clock_now = [1000.0]
    checker = FollowedLiveChecker(probe, ttl_seconds=120, clock=lambda: clock_now[0], executor=ImmediateExecutor())
    first = checker.live_entries(["https://www.youtube.com/@a"])
    assert first == []  # cache-only: never blocks the request thread
    assert calls == ["https://www.youtube.com/@a/live"]
    second = checker.live_entries(["https://www.youtube.com/@a"])
    assert [item.webpage_url for item in second] == ["https://www.youtube.com/@a/live"]
    assert calls == ["https://www.youtube.com/@a/live"]  # fresh cache: no re-probe


def test_not_live_and_errors_are_cached_as_not_live() -> None:
    probes: list[str] = []

    def probe(url: str):
        probes.append(url)
        if "erroring" in url:
            raise RuntimeError("probe blew up")
        return None

    clock_now = [1000.0]
    checker = FollowedLiveChecker(probe, ttl_seconds=120, clock=lambda: clock_now[0], executor=ImmediateExecutor())
    channels = ["https://www.youtube.com/@quiet", "https://www.youtube.com/@erroring"]
    checker.live_entries(channels)
    assert len(probes) == 2  # both channels probed on the first pass
    assert checker.live_entries(channels) == []
    assert len(probes) == 2  # fresh not-live cache: the second call must not re-probe


def test_stale_cache_reprobes_after_ttl() -> None:
    calls: list[str] = []

    def probe(url: str):
        calls.append(url)
        return _entry(url)

    clock_now = [1000.0]
    checker = FollowedLiveChecker(probe, ttl_seconds=120, clock=lambda: clock_now[0], executor=ImmediateExecutor())
    checker.live_entries(["https://www.twitch.tv/a"])
    clock_now[0] = 1121.0  # past ttl
    stale_serve = checker.live_entries(["https://www.twitch.tv/a"])
    assert [item.webpage_url for item in stale_serve] == ["https://www.twitch.tv/a"]  # serves last-known while re-probing
    assert calls == ["https://www.twitch.tv/a", "https://www.twitch.tv/a"]


def test_owned_executor_is_shut_down_on_close() -> None:
    import pytest

    checker = FollowedLiveChecker(lambda url: None, ttl_seconds=120)  # owns an eager executor
    executor = checker._executor
    assert executor is not None
    checker.close()
    with pytest.raises(RuntimeError):
        executor.submit(lambda: None)  # a shut-down executor refuses new work


def test_live_entries_survives_submit_after_executor_shutdown() -> None:
    # Reproduces the close()/live_entries race: the owned executor is gone but a
    # request thread already passed its _closed check, so submit raises on it.
    checker = FollowedLiveChecker(lambda url: _entry(url), ttl_seconds=120)
    checker._executor.shutdown()  # kill the executor while _closed stays False
    result = checker.live_entries(["https://www.twitch.tv/a"])
    assert result == []  # no cached entry yet, and the probe never ran
    assert checker._inflight == set()  # the failed channel is not left stuck in-flight
    checker.close()


def test_inflight_probe_not_duplicated_with_real_executor() -> None:
    import threading

    release = threading.Event()
    calls: list[str] = []

    def probe(url: str):
        calls.append(url)
        release.wait(timeout=5)
        return None

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        checker = FollowedLiveChecker(probe, ttl_seconds=120, executor=executor)
        checker.live_entries(["https://www.twitch.tv/a"])
        checker.live_entries(["https://www.twitch.tv/a"])  # same stale channel again while probe running
        release.set()
        executor.shutdown(wait=True)
        assert calls == ["https://www.twitch.tv/a"]
    finally:
        release.set()
        executor.shutdown(wait=True)
