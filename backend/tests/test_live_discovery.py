from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas import YouTubeSearchResponse, YouTubeSearchResult
from app.services.live_discovery import (
    LIVE_CATEGORIES,
    TWITCH_CATEGORY_SOURCES,
    LiveSearch,
    LiveWall,
    LiveWallError,
    create_live_discovery,
    TwitchHealth,
    merge_live_entries,
    twitch_stream_to_result,
)
from app.services.twitch_gql_directory import TwitchDirectoryError, TwitchLiveStream, TwitchPage


def _yt(item_id: str, viewers: int | None) -> YouTubeSearchResult:
    return YouTubeSearchResult(id=item_id, title=item_id, view_count=viewers, source="youtube")


def _stream(login: str, viewers: int | None = 500, *, category_name: str | None = "Music") -> TwitchLiveStream:
    return TwitchLiveStream(login=login, display_name=login.title(), title=f"{login} live", viewers_count=viewers, preview_image_url=None, category_name=category_name)


class FakeDirectory:
    def __init__(self, *, fail: bool = False, pages: dict | None = None):
        self.fail = fail
        self.pages = pages or {}  # (name, after) -> TwitchPage
        self.calls: list[tuple] = []

    def top_page(self, limit, after=None):
        self.calls.append(("top", limit, after))
        if self.fail:
            raise TwitchDirectoryError("down")
        return self.pages.get(("", after)) or TwitchPage([_stream("topdog", 9000)], None, None)

    def game_page(self, name, limit, after=None):
        self.calls.append(("game", name, limit, after))
        if self.fail:
            raise TwitchDirectoryError("down")
        return self.pages.get((name, after)) or TwitchPage([_stream("cello", 700)], None, 495)

    def top_games(self, limit=100):
        if self.fail:
            raise TwitchDirectoryError("down")
        return [("Just Chatting", 5000), ("Fortnite", 800), ("Apex", 700)]


def test_live_categories_are_unique_and_cover_twitch_sources() -> None:
    keys = [c.key for c in LIVE_CATEGORIES]
    queries = [c.query for c in LIVE_CATEGORIES]
    assert keys == ["gaming", "music", "news", "sports", "creative", "other"]
    assert len(set(queries)) == len(queries)  # reverse map soundness
    assert set(TWITCH_CATEGORY_SOURCES) == set(keys)


def test_twitch_stream_to_result_is_live_playable_twitch() -> None:
    result = twitch_stream_to_result(_stream("cello", 700))
    assert result.webpage_url == "https://www.twitch.tv/cello"
    assert result.uploader == "Cello"
    assert result.view_count == 700
    assert result.source == "twitch"
    assert result.source_label == "Twitch"
    assert result.capabilities is not None
    assert result.capabilities.provider == "twitch"
    assert result.capabilities.lifecycle == "live"
    assert result.capabilities.can_play is True


def test_merge_ranks_by_viewers_with_unranked_after() -> None:
    merged = merge_live_entries(
        [_yt("a", 100), _yt("b", None)],
        [twitch_stream_to_result(_stream("c", 900)), twitch_stream_to_result(_stream("d", None))],
    )
    assert [item.id for item in merged] == ["twitch:c", "a", "b", "twitch:d"]


def test_live_search_merges_both_providers_for_music() -> None:
    directory = FakeDirectory()
    music_query = next(c.query for c in LIVE_CATEGORIES if c.key == "music")
    search = LiveSearch(lambda q, n: YouTubeSearchResponse(query=q, items=[_yt("yt1", 50)]), directory)
    response = search(music_query, 8)
    ids = [item.id for item in response.items]
    assert "yt1" in ids and "twitch:cello" in ids
    assert ("game", "Music", 8, None) in directory.calls
    assert search.health.available is True


def test_gaming_reads_busiest_non_routed_directories() -> None:
    # Just Chatting is routed to another rail, so gaming reads Fortnite and Apex instead.
    directory = FakeDirectory()
    gaming_query = next(c.query for c in LIVE_CATEGORIES if c.key == "gaming")
    LiveSearch(lambda q, n: YouTubeSearchResponse(query=q, items=[]), directory)(gaming_query, 8)
    assert [c[1] for c in directory.calls if c[0] == "game"] == ["Fortnite", "Apex"]


def test_twitch_failure_fails_closed_to_youtube_only() -> None:
    directory = FakeDirectory(fail=True)
    music_query = next(c.query for c in LIVE_CATEGORIES if c.key == "music")
    search = LiveSearch(lambda q, n: YouTubeSearchResponse(query=q, items=[_yt("yt1", 50)]), directory)
    response = search(music_query, 8)
    assert [item.id for item in response.items] == ["yt1"]
    assert search.health.available is False


def test_youtube_failure_still_serves_twitch() -> None:
    def broken_youtube(q, n):
        raise RuntimeError("yt down")

    directory = FakeDirectory()
    music_query = next(c.query for c in LIVE_CATEGORIES if c.key == "music")
    response = LiveSearch(broken_youtube, directory)(music_query, 8)
    assert [item.id for item in response.items] == ["twitch:cello"]


def test_both_providers_failing_raises_so_category_goes_stale() -> None:
    def broken_youtube(q, n):
        raise RuntimeError("yt down")

    directory = FakeDirectory(fail=True)
    music_query = next(c.query for c in LIVE_CATEGORIES if c.key == "music")
    with pytest.raises(RuntimeError):
        LiveSearch(broken_youtube, directory)(music_query, 8)


def _query(key: str) -> str:
    return next(c.query for c in LIVE_CATEGORIES if c.key == key)


def _no_youtube(q, n):
    return YouTubeSearchResponse(query=q, items=[])


def _query(key: str) -> str:
    return next(c.query for c in LIVE_CATEGORIES if c.key == key)


def _no_youtube(q, n):
    return YouTubeSearchResponse(query=q, items=[])


def _none(k, n, c):
    return [], None, None


def test_category_count_sums_directory_totals_and_gaming_excludes_routed() -> None:
    search = LiveSearch(_no_youtube, FakeDirectory(), youtube_directory=_none)
    search(_query("creative"), 60)
    search(_query("gaming"), 60)
    assert search.counts["creative"] == 495 * len(TWITCH_CATEGORY_SOURCES["creative"])
    assert search.counts["gaming"] == 1500  # Fortnite + Apex; Just Chatting is routed to another rail


def test_youtube_directory_feeds_snapshot_pool_and_count_is_streams_held() -> None:
    search = LiveSearch(_no_youtube, FakeDirectory(), youtube_directory=lambda k, n, c: ([_yt("ydir", 5)], "next", None))
    response = search(_query("music"), 60)
    assert "ydir" in [item.id for item in response.items]
    assert [i.id for i in search.youtube_pool("music")] == ["ydir"]
    assert search.counts["music"] == 495 + 1


def test_count_is_unknown_when_no_provider_reports_one() -> None:
    search = LiveSearch(_no_youtube, FakeDirectory(pages={("Music", None): TwitchPage([_stream("a")], None, None)}), youtube_directory=_none)
    search(_query("music"), 60)
    assert search.counts["music"] is None


def test_failed_refresh_keeps_last_known_count_and_pool() -> None:
    directory = FakeDirectory()
    search = LiveSearch(_no_youtube, directory, youtube_directory=lambda k, n, c: ([_yt("y", 1)], None, None))
    search(_query("music"), 60)
    directory.fail = True
    search._youtube_directory = _none
    with pytest.raises(TwitchDirectoryError):
        search(_query("music"), 60)
    assert search.counts["music"] == 496
    assert [i.id for i in search.youtube_pool("music")] == ["y"]


def test_duplicate_stream_from_search_and_directory_is_listed_once() -> None:
    item = YouTubeSearchResult(id="v", title="v", webpage_url="https://www.youtube.com/watch?v=v", view_count=3, source="youtube")
    search = LiveSearch(lambda q, n: YouTubeSearchResponse(query=q, items=[item]), FakeDirectory(), youtube_directory=lambda k, n, c: ([item], None, None))
    assert [i.id for i in search(_query("news"), 60).items] == ["v"]


def _wall(directory, pool=()):
    return LiveWall(directory, youtube_pool=lambda key: list(pool))


def test_wall_walks_directories_with_opaque_cursor() -> None:
    directory = FakeDirectory(pages={("Just Chatting", None): TwitchPage([_stream("a", 90)], None, 1), ("Art", None): TwitchPage([_stream("b", 80)], None, 1)})
    wall = _wall(directory)
    ids, cursor = [], None
    for _ in range(5):
        items, cursor, _count = wall.page("creative", cursor, 1)
        ids += [i.id for i in items]
        if cursor is None:
            break
    assert "twitch:a" in ids and "twitch:b" in ids and cursor is None


def test_wall_pages_the_held_youtube_list_without_calling_youtube() -> None:
    pool = [_yt(f"y{i}", 1000 - i) for i in range(5)]
    wall = _wall(FakeDirectory(), pool)
    items, cursor, _ = wall.page("news", None, 2)  # news has no Twitch directory
    assert [i.id for i in items] == ["y0", "y1"] and cursor
    items, cursor, _ = wall.page("news", cursor, 2)
    assert [i.id for i in items] == ["y2", "y3"]
    items, cursor, _ = wall.page("news", cursor, 2)
    assert [i.id for i in items] == ["y4"] and cursor is None


def test_wall_caches_pages() -> None:
    directory = FakeDirectory()
    wall = _wall(directory)
    wall.page("music", None, 40)
    wall.page("music", None, 40)
    assert len([c for c in directory.calls if c[0] == "game"]) == 1


def test_wall_rejects_unknown_category_and_bad_cursor() -> None:
    wall = _wall(FakeDirectory())
    with pytest.raises(LiveWallError) as unknown:
        wall.page("nope", None, 40)
    assert unknown.value.kind == "unknown"
    for bad in ("!!not-a-cursor", "bnVsbA"):
        with pytest.raises(LiveWallError) as err:
            wall.page("music", bad, 40)
        assert err.value.kind == "cursor"


def test_wall_unavailable_only_when_nothing_could_be_served() -> None:
    with pytest.raises(LiveWallError) as down:
        _wall(FakeDirectory(fail=True)).page("music", None, 40)
    assert down.value.kind == "unavailable"
    items, _, _ = _wall(FakeDirectory(fail=True), [_yt("y", 1)]).page("music", None, 40)
    assert [i.id for i in items] == ["y"]


def test_live_refresh_is_ten_minutes_serial_with_sixty_per_category() -> None:
    # 3 minutes cost ~2,300 YouTube requests an hour from one open tab (26-pp); provider_budget caps the rest.
    discovery = create_live_discovery(Path("/nonexistent/live.json"), lambda q, n: None)
    assert (discovery._category_ttl_seconds, discovery._per_category_limit, discovery._max_concurrency) == (600, 60, 1)
    assert discovery._feed_limit >= 60 * len(LIVE_CATEGORIES)
