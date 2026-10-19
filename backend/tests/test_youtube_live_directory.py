from app.schemas import YouTubeSearchResponse
from app.services import youtube_live_directory as yld
from app.services.yt_dlp_service import YtDlpService


def _result(vid: str, views: int):
    return YtDlpService.normalize_search_result(
        {"id": vid, "title": vid, "channel": "C", "live_status": "is_live", "view_count": views,
         "thumbnails": [{"url": f"https://i.ytimg.com/vi/{vid}/hq2.jpg"}]},
        "youtube",
    )


def _fake_search(query, limit):
    if query == "gaming":
        raise RuntimeError("boom")
    n = {"minecraft": [("aaaaaaaaaaa", 5), ("bbbbbbbbbbb", 50)]}.get(query, [("bbbbbbbbbbb", 50), ("ccccccccccc", 9)])
    return YouTubeSearchResponse(query=query, items=[_result(*x) for x in n])


def test_directory_merges_dedupes_ranks_and_paginates(monkeypatch) -> None:
    yld._cache.clear()
    monkeypatch.setattr(yld, "_default_search", _fake_search)
    items, cursor, count = yld.youtube_live_directory("gaming", 2)
    assert [i.id for i in items] == ["bbbbbbbbbbb", "ccccccccccc"]
    assert items[0].capabilities.lifecycle == "live" and items[0].view_count == 50
    assert count is None and cursor and "http" not in cursor
    rest, cursor2, _ = yld.youtube_live_directory("gaming", 2, cursor)
    assert [i.id for i in rest] == ["aaaaaaaaaaa"] and cursor2 is None


def test_unknown_category_and_garbage_cursor(monkeypatch) -> None:
    yld._cache.clear()
    monkeypatch.setattr(yld, "_default_search", _fake_search)
    assert yld.youtube_live_directory("nope", 5) == ([], None, None)
    assert yld.youtube_live_directory("news", 5, "!!garbage")[0]


def test_directory_fetches_affordable_breadth_when_the_full_pool_will_not_fit(monkeypatch) -> None:
    from app.services import provider_budget
    budget = provider_budget.fresh('youtube')
    monkeypatch.setattr(provider_budget, 'youtube', budget)
    assert budget.take(35)  # five tokens available above the background reserve
    calls = []
    def search(query, limit):
        calls.append(query)
        with provider_budget.priority('background'):
            provider_budget.spend(budget, provider_budget.youtube_search_cost(limit))
        return YouTubeSearchResponse(query=query, items=[_result('live-video', 10)])
    result = yld._fetch('gaming', search, 1)
    assert result and len(calls) == 2
    assert budget._tokens >= provider_budget.KEEP['background']
