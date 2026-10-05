"""ChannelPages: stale-while-revalidate, coalescing, a bounded pool, a 15 s cold wait."""
from __future__ import annotations

import threading
import time

import pytest

from app.services import youtube_channels
from app.services.youtube_channels import ChannelPages, ChannelTimeout

CID = "UCabcdefghijklmnopqrstuv"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Loads:
    """Injected loaders that count calls, record their threads, and can be held open."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.threads: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()
        self.running = 0
        self.max_running = 0
        self._lock = threading.Lock()

    def _enter(self, call: tuple):  # noqa: ANN202
        with self._lock:
            self.calls.append(call)
            self.threads.add(threading.current_thread().name)
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        assert self.gate.wait(5)
        with self._lock:
            self.running -= 1

    def header(self, channel_id: str):  # noqa: ANN201
        self._enter(("header", channel_id))
        return f"header:{channel_id}:{len(self.calls)}"

    def tab(self, channel_id: str, tab: str, limit: int):  # noqa: ANN201
        self._enter(("tab", channel_id, tab, limit))
        return f"tab:{channel_id}:{tab}:{limit}:{len(self.calls)}"

    def resolve(self, url: str):  # noqa: ANN201
        self._enter(("resolve", url))
        return CID, f"header:{CID}:resolved"


def pages(loads: Loads, clock: Clock, wait_seconds: float = 5.0) -> ChannelPages:
    return ChannelPages(loads.header, loads.tab, loads.resolve, clock=clock, wait_seconds=wait_seconds)


def settle(cache: ChannelPages) -> None:
    deadline = time.monotonic() + 5
    while cache.inflight() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not cache.inflight()


def test_a_fresh_hit_returns_without_extracting() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    first = cache.page(CID, "videos", 60)
    clock.now += 29 * 60
    second = cache.page(CID, "videos", 60)
    assert first == second and len(loads.calls) == 2
    assert first[2] == 1_000_000.0 and first[3] is False
    assert not any(name == threading.current_thread().name for name in loads.threads)  # never on the request thread
    cache.close()


def test_a_stale_hit_returns_at_once_and_refreshes_once_in_the_background() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    cache.page(CID, "videos", 60)
    clock.now += 31 * 60  # tab stale (30 min), header still fresh (6 h)
    loads.gate.clear()
    started = time.monotonic()
    header, tab, fetched_at, stale = cache.page(CID, "videos", 60)
    again = cache.page(CID, "videos", 60)
    assert time.monotonic() - started < 0.5 and stale is True and again[3] is True
    assert tab.startswith(f"tab:{CID}:videos:60:") and fetched_at == 1_000_000.0
    loads.gate.set()
    settle(cache)
    assert [call[0] for call in loads.calls] == ["header", "tab", "tab"]  # exactly one refresh
    assert cache.page(CID, "videos", 60)[3] is False
    cache.close()


def test_a_cold_miss_times_out_and_still_fills_the_cache() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock, wait_seconds=0.05)
    loads.gate.clear()
    with pytest.raises(ChannelTimeout):
        cache.page(CID, "videos", 60)
    loads.gate.set()
    settle(cache)
    calls = len(loads.calls)
    assert cache.page(CID, "videos", 60)[3] is False and len(loads.calls) == calls
    cache.close()


def test_concurrent_misses_share_one_extraction() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    loads.gate.clear()
    results: list[tuple] = []
    threads = [threading.Thread(target=lambda: results.append(cache.page(CID, "videos", 60))) for _ in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    loads.gate.set()
    for thread in threads:
        thread.join(5)
    assert len(results) == 4 and len(set(results)) == 1
    assert sorted(call[0] for call in loads.calls) == ["header", "tab"]
    cache.close()


def test_the_pool_runs_at_most_two_extractions() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock, wait_seconds=0.05)
    loads.gate.clear()
    for tab in ("videos", "streams", "shorts"):
        with pytest.raises(ChannelTimeout):
            cache.page(CID, tab, 60)
    time.sleep(0.1)
    assert loads.max_running == 2
    loads.gate.set()
    settle(cache)
    cache.close()


def test_120_extracts_even_when_60_is_fresh_and_the_lru_forgets_the_oldest(monkeypatch) -> None:  # noqa: ANN001
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    cache.page(CID, "videos", 60)
    cache.page(CID, "videos", 120)
    assert ("tab", CID, "videos", 120) in loads.calls
    monkeypatch.setattr(youtube_channels, "CACHE_SIZE", 2)
    cache.page(CID, "streams", 60)  # evicts (videos, 60)
    before = len(loads.calls)
    cache.page(CID, "videos", 60)
    assert len(loads.calls) == before + 1
    assert cache.peek_tab(CID, "shorts", 60) is None and cache.peek_tab(CID, "videos", 60) is not None
    cache.close()


def test_a_header_past_seven_days_is_extracted_again() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    cache.page(CID, "videos", 60)
    clock.now += 7 * 86400 + 1
    cache.page(CID, "videos", 60)
    assert [call[0] for call in loads.calls].count("header") == 2
    cache.close()


def test_resolve_caches_its_answer_and_warms_the_header() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    assert cache.resolve("https://www.youtube.com/@harborfilms") == CID
    assert cache.resolve("https://www.youtube.com/@harborfilms") == CID
    header = cache.page(CID, "videos", 60)[0]
    assert header == f"header:{CID}:resolved"
    assert [call[0] for call in loads.calls] == ["resolve", "tab"]
    cache.close()


def test_a_load_error_reaches_the_waiting_request_and_is_not_cached() -> None:
    clock = Clock()
    failures = iter([RuntimeError("boom")])

    def header(channel_id: str):  # noqa: ANN202
        error = next(failures, None)
        if error:
            raise error
        return "header"

    cache = ChannelPages(header, lambda cid, tab, limit: "tab", lambda url: (CID, "header"), clock=clock)
    with pytest.raises(RuntimeError, match="boom"):
        cache.page(CID, "videos", 60)
    assert cache.page(CID, "videos", 60)[0] == "header"
    cache.close()


# ---- parsing ----
import yt_dlp  # noqa: E402

from app.services.youtube_channels import (  # noqa: E402
    ChannelUnavailable, channel_pages, header_from_info, parse_channel_address, tab_from_info,
)
from app.services.yt_dlp_service import YtDlpService  # noqa: E402


def root_info(**fields) -> dict:  # noqa: ANN003
    return {
        "_type": "playlist", "id": CID, "channel_id": CID, "channel": "Harbor Films", "uploader_id": "@harborfilms",
        "channel_follower_count": 1_200_000, "description": "Films about harbors.", "channel_is_verified": True,
        "thumbnails": [
            {"url": "https://yt3.googleusercontent.com/banner=w1060", "width": 1060, "height": 175, "id": "banner_uncropped"},
            {"url": "https://yt3.googleusercontent.com/banner=w2560", "width": 2560, "height": 424},
            {"url": "https://yt3.googleusercontent.com/avatar=s900", "width": 900, "height": 900, "id": "avatar_uncropped"},
        ],
        "entries": [
            {"_type": "url", "url": f"https://www.youtube.com/channel/{CID}/videos"},
            {"_type": "url", "url": f"https://www.youtube.com/channel/{CID}/streams"},
            {"_type": "url", "url": f"https://www.youtube.com/channel/{CID}/shorts"},
            {"_type": "url", "url": f"https://www.youtube.com/channel/{CID}/community"},
        ],
        **fields,
    }


def test_the_banner_is_the_widest_image_wider_than_three_to_one() -> None:
    assert YtDlpService.resolve_channel_banner(root_info()) == "https://yt3.googleusercontent.com/banner=w2560"
    assert YtDlpService.resolve_channel_banner({"thumbnails": [{"url": "https://a", "width": 300, "height": 100}]}) is None
    assert YtDlpService.resolve_channel_banner({"thumbnails": [{"url": "https://a", "width": True, "height": 1}, {"url": 3}]}) is None
    assert YtDlpService.resolve_channel_banner({}) is None


def test_a_header_reads_name_handle_counts_tabs_and_art() -> None:
    header = header_from_info(CID, root_info())
    assert (header.name, header.handle, header.follower_count, header.verified) == ("Harbor Films", "@harborfilms", 1_200_000, True)
    assert header.tabs == ("videos", "streams", "shorts")
    assert header.avatar == "https://yt3.googleusercontent.com/avatar=s900"
    assert header.banner == "https://yt3.googleusercontent.com/banner=w2560"
    assert header.url == f"https://www.youtube.com/channel/{CID}" and header.video_count is None
    bare = header_from_info(CID, {"entries": None, "uploader_id": CID, "description": "x" * 6000})
    assert (bare.name, bare.handle, bare.tabs, len(bare.description or "")) == (CID, None, (), 5000)


def test_a_streams_tab_counts_viewers_and_knows_more_is_there() -> None:
    entries = [
        {"id": "aaaaaaaaaaa", "title": "Now", "live_status": "is_live", "concurrent_view_count": 12412, "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
        {"id": "bbbbbbbbbbb", "title": "Later", "live_status": "is_upcoming", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
        {"id": "ccccccccccc", "title": "Past", "live_status": "was_live", "view_count": 90, "url": "https://www.youtube.com/watch?v=ccccccccccc"},
    ]
    page = tab_from_info("streams", {"entries": entries}, 2)
    assert page.has_more is True and len(page.entries) == 2
    assert page.entries[0].view_count == 12412 and page.entries[0].capabilities.lifecycle == "live"
    assert page.entries[1].capabilities.lifecycle == "upcoming"
    assert tab_from_info("videos", {"entries": entries[2:]}, 60).has_more is False


@pytest.mark.parametrize(("url", "expected"), [
    (f"https://www.youtube.com/channel/{CID}", ("id", CID)),
    (f"https://m.youtube.com/channel/{CID}/videos?x=1", ("id", CID)),
    ("https://youtube.com/@harborfilms/streams", ("lookup", "https://www.youtube.com/@harborfilms")),
    ("https://www.youtube.com/c/Harbor", ("lookup", "https://www.youtube.com/c/Harbor")),
    ("https://www.youtube.com/user/harbor", ("lookup", "https://www.youtube.com/user/harbor")),
    ("http://www.youtube.com/@x", None), ("https://evil.com/@x", None), ("https://www.youtube.com/watch?v=abc", None),
    (f"https://www.youtube.com/channel/{CID}x", None), ("https://www.youtube.com/@", None), ("https://user:pw@www.youtube.com/@x", None),
    ("https://music.youtube.com/@x", None), ("not a url", None),
])
def test_only_youtube_channel_addresses_resolve(url: str, expected) -> None:  # noqa: ANN001
    assert parse_channel_address(url) == expected


def test_the_real_loaders_extract_the_root_and_one_tab_and_classify_errors() -> None:
    requested: list[tuple[str, int]] = []

    def extract(url: str, limit: int) -> dict:
        requested.append((url, limit))
        if url.endswith("/shorts"):
            raise yt_dlp.utils.DownloadError("ERROR: This channel does not have a shorts tab")
        if url.endswith("/playlists"):
            raise yt_dlp.utils.DownloadError("ERROR: This video is available to this channel's members-only")
        if "Gone" in url:
            raise yt_dlp.utils.DownloadError("ERROR: This account has been terminated")
        if "@" in url:
            return root_info()
        return root_info() if url.endswith(CID) else {"entries": [{"id": "aaaaaaaaaaa", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]}

    cache = channel_pages(extract)
    header, page, _, _ = cache.page(CID, "videos", 60)
    # The two loads run on the pool at once, so their order is not fixed.
    assert sorted(requested) == [(f"https://www.youtube.com/channel/{CID}", 10), (f"https://www.youtube.com/channel/{CID}/videos", 61)]
    assert header.name == "Harbor Films" and [entry.id for entry in page.entries] == ["aaaaaaaaaaa"]
    assert cache.page(CID, "shorts", 60)[1].entries == ()
    assert cache.page(CID, "playlists", 60)[1].restricted is True
    assert cache.resolve("https://www.youtube.com/@harborfilms") == CID
    with pytest.raises(ChannelUnavailable):
        cache.resolve("https://www.youtube.com/@Gone")
    cache.close()


def test_peek_tab_can_refuse_a_page_older_than_max_age() -> None:
    loads, clock = Loads(), Clock()
    cache = pages(loads, clock)
    cache.page(CID, "streams", 60)
    clock.now += 31 * 60
    assert cache.peek_tab(CID, "streams", 60) is not None
    assert cache.peek_tab(CID, "streams", 60, max_age=youtube_channels.TAB_FRESH_SECONDS) is None
    cache.close()
