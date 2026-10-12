import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import yt_dlp_service as yt_dlp_service_module
from app.services.yt_dlp_service import SearchBusyError, YtDlpService, _SEARCH_CACHE
from support import memory_session_factory, seed_app_settings


def make_session():
    session = memory_session_factory()()
    seed_app_settings(session)
    return session


def test_youtube_search_normalizes_results() -> None:
    _SEARCH_CACHE.clear()
    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            assert source_url == "ytsearch2:lumina"
            assert download is False
            return {
                "_type": "playlist",
                "entries": [
                    {
                        "id": "abc123",
                        "title": "Lumina Demo",
                        "uploader": "Lumina",
                        "duration": 187,
                        "thumbnail": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg",
                        "webpage_url": "https://www.youtube.com/watch?v=abc123",
                        "view_count": 123456,
                        "availability": "public",
                        "timestamp": 1774656000,
                    },
                    {
                        "id": "def456",
                        "title": "Second result",
                        "channel": "Creator",
                    },
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    session = make_session()
    response = YtDlpService(session, ydl_factory=FakeYDL).youtube_search("lumina", limit=2)

    assert response.query == "lumina"
    assert len(response.items) == 2
    assert response.items[0].thumbnail == "https://i.ytimg.com/vi/abc123/sddefault.jpg"
    assert response.items[0].view_count == 123456
    assert response.items[1].webpage_url == "https://www.youtube.com/watch?v=def456"
    assert response.items[1].uploader == "Creator"


def test_youtube_search_rejects_blank_query() -> None:
    session = make_session()
    try:
        YtDlpService(session).youtube_search("   ")
    except ValueError as exc:
        assert str(exc) == "Enter a YouTube search query."
    else:
        raise AssertionError("Expected ValueError for blank query")


def test_soundcloud_search_normalizes_results() -> None:
    _SEARCH_CACHE.clear()
    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            assert source_url == "scsearch2:doja cat"
            assert download is False
            return {
                "_type": "playlist",
                "entries": [
                    {
                        "id": "track-1",
                        "title": "Say So",
                        "uploader": "DOJA CAT",
                        "duration": 237,
                        "url": "https://soundcloud.com/amalaofficial/say-so",
                        "view_count": 5500,
                        "timestamp": 1_700_000_000,
                        "thumbnails": [
                            {"url": "https://i1.sndcdn.com/artworks-small.jpg", "width": 32, "height": 32},
                            {"url": "https://i1.sndcdn.com/artworks-t500x500.jpg", "width": 500, "height": 500},
                        ],
                    },
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    session = make_session()
    items = YtDlpService(session, ydl_factory=FakeYDL)._search_provider("soundcloud", "doja cat", 2)

    assert len(items) == 1
    assert items[0].source == "soundcloud"
    assert items[0].source_label == "SoundCloud"
    assert items[0].thumbnail == "https://i1.sndcdn.com/artworks-t500x500.jpg"
    assert items[0].webpage_url == "https://soundcloud.com/amalaofficial/say-so"


def test_source_search_interleaves_soundcloud_and_youtube_results(monkeypatch) -> None:
    session = make_session()
    service = YtDlpService(session)

    def fake_provider_search(provider: str, query: str, limit: int = 10):  # noqa: ARG001
        if provider == "soundcloud":
            return [
                YtDlpService.normalize_search_result(
                    {"id": "sc-1", "title": "SC track", "url": "https://soundcloud.com/demo/sc-track"}, "soundcloud"
                )
            ]
        return [
            YtDlpService.normalize_search_result(
                {"id": "yt-1", "title": "YT track", "webpage_url": "https://www.youtube.com/watch?v=yt-1"}, "youtube"
            ),
            YtDlpService.normalize_search_result(
                {"id": "yt-2", "title": "YT second", "webpage_url": "https://www.youtube.com/watch?v=yt-2"}, "youtube"
            ),
        ]

    monkeypatch.setattr(service, "_search_provider", fake_provider_search)

    response = service.source_search("lumina", limit=3)

    assert [item.source for item in response.items] == ["soundcloud", "youtube", "youtube"]
    assert response.items[0].title == "SC track"


def test_source_search_more_asks_each_provider_for_a_bigger_page(monkeypatch) -> None:
    service = YtDlpService(make_session())
    asked: list[tuple[str, int]] = []

    def fake_provider_search(provider: str, query: str, limit: int = 10):  # noqa: ARG001
        asked.append((provider, limit))
        return [
            YtDlpService.normalize_search_result({"id": f"{provider}-{n}", "webpage_url": f"https://example.test/{provider}/{n}"}, provider)
            for n in range(limit)
        ]

    monkeypatch.setattr(service, "_search_provider", fake_provider_search)

    assert len(service.source_search("lumina", limit=12).items) == 12
    assert len(service.source_search("lumina", limit=24).items) == 24
    assert asked == [("soundcloud", 8), ("youtube", 8), ("soundcloud", 16), ("youtube", 16)]


def test_youtube_search_uses_cached_results_for_repeat_queries() -> None:
    _SEARCH_CACHE.clear()
    calls = {"count": 0}

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            calls["count"] += 1
            assert source_url == "ytsearch2:lumina"
            assert download is False
            return {"_type": "playlist", "entries": [{"id": "abc123", "title": "Lumina Demo"}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    session = make_session()
    service = YtDlpService(session, ydl_factory=FakeYDL)

    first = service.youtube_search("lumina", limit=2)
    second = service.youtube_search("lumina", limit=2)

    assert len(first.items) == 1
    assert len(second.items) == 1
    assert calls["count"] == 1


def test_youtube_search_normalizes_cache_keys_and_reuses_larger_result_sets() -> None:
    _SEARCH_CACHE.clear()
    calls: list[str] = []

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            calls.append(source_url)
            return {
                "_type": "playlist",
                "entries": [{"id": f"video-{index}", "title": f"Video {index}"} for index in range(4)],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=FakeYDL)
    larger = service.youtube_search("  Lumina   Demo  ", limit=4)
    smaller = service.youtube_search("lumina demo", limit=2)

    assert len(larger.items) == 4
    assert len(smaller.items) == 2
    assert calls == ["ytsearch4:Lumina Demo"]


def test_youtube_search_coalesces_concurrent_duplicate_queries() -> None:
    _SEARCH_CACHE.clear()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class SlowYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(timeout=2)
            return {"_type": "playlist", "entries": [{"id": "video-1", "title": "Video 1"}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=SlowYDL)
    service.build_base_options = lambda: {}  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.youtube_search, "Lumina", 2)
        assert entered.wait(timeout=2)
        second = executor.submit(service.youtube_search, "lumina", 2)
        release.set()
        assert len(first.result(timeout=2).items) == 1
        assert len(second.result(timeout=2).items) == 1

    assert calls == 1


def test_youtube_search_bounds_waiting_behind_a_stalled_duplicate(monkeypatch) -> None:
    _SEARCH_CACHE.clear()
    entered = threading.Event()
    release = threading.Event()

    class SlowYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            entered.set()
            assert release.wait(timeout=2)
            return {"_type": "playlist", "entries": [{"id": "video-1", "title": "Video 1"}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    monkeypatch.setattr(yt_dlp_service_module, "SEARCH_COALESCED_WAIT_TIMEOUT_SECONDS", 0.01)
    service = YtDlpService(make_session(), ydl_factory=SlowYDL)
    service.build_base_options = lambda: {}  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        leader = executor.submit(service.youtube_search, "Lumina", 2)
        assert entered.wait(timeout=2)
        with pytest.raises(SearchBusyError, match="still being prepared"):
            service.youtube_search("lumina", 2)
        release.set()
        assert len(leader.result(timeout=2).items) == 1


def test_source_search_keeps_other_provider_when_lazy_paging_raises_a_transport_error() -> None:
    """yt-dlp raises RequestError unwrapped while paging a lazy playlist (S71 real-stack finding)."""
    from yt_dlp.networking.exceptions import RequestError

    _SEARCH_CACHE.clear()

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            if source_url.startswith("scsearch"):
                raise RequestError("network unreachable")
            return {"_type": "playlist", "entries": [{"id": "abc123", "title": "Lumina Demo"}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    response = YtDlpService(make_session(), ydl_factory=FakeYDL).source_search("lumina transport", limit=4)

    assert [item.title for item in response.items] == ["Lumina Demo"]
    assert [error.source for error in response.errors] == ["soundcloud"]


def test_a_cache_free_search_neither_reads_nor_populates_the_shared_cache() -> None:
    """The refresher's history-derived queries are never keys in shared storage."""
    _SEARCH_CACHE.clear()
    calls: list[str] = []

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            calls.append(source_url)
            return {"entries": [{"id": "abc123", "title": "T", "url": "https://www.youtube.com/watch?v=abc123"}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=FakeYDL)
    service.youtube_search("sourdough levain", 2, cache=False)
    assert not _SEARCH_CACHE and not yt_dlp_service_module._SEARCH_INFLIGHT

    service.youtube_search("sourdough levain", 2)  # an interactive search populates it ...
    assert _SEARCH_CACHE
    service.youtube_search("sourdough levain", 2, cache=False)  # ... and a cache-free one still goes to the provider
    assert len(calls) == 3
    _SEARCH_CACHE.clear()


def test_deep_youtube_search_lifts_the_24_cap_only_when_asked() -> None:
    _SEARCH_CACHE.clear()
    seen = []

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            seen.append(source_url)
            return {"entries": []}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=FakeYDL)
    service.youtube_search("deep-a", 40)
    service.youtube_search("deep-b", 40, deep=True)
    assert seen == ["ytsearch24:deep-a", "ytsearch40:deep-b"]
