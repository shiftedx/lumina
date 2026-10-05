from __future__ import annotations

from app.services.popular_discovery import POPULAR_CATEGORIES, PopularCategory, PopularDiscovery, viable_category_keys


class _Now:
    def submit(self, function):  # noqa: ANN001
        function()


def _item(i, duration=600):
    return {"id": f"{i}", "title": f"t{i}", "webpage_url": f"https://www.youtube.com/watch?v={i}", "duration": duration}


def _service(tmp_path, search, categories):
    return PopularDiscovery(tmp_path / "p.json", search, categories=categories, executor=_Now(), clock=lambda: 1e9,
                            batch_size=len(categories), max_concurrency=1, random=lambda: 0.5)


def test_every_category_has_one_to_three_related_queries():
    assert all(1 <= len(c.related) <= 3 for c in POPULAR_CATEGORIES)


def test_thin_row_is_backfilled_from_related_queries_and_shorts_are_dropped(tmp_path):
    calls = []

    def search(query, limit):
        calls.append((query, limit))
        if query == "main":
            return [_item("a"), _item("s", duration=20), _item("b")]
        if query == "r1":
            return [_item("b")] + [_item(f"r{n}") for n in range(3)]
        return [_item(f"x{n}") for n in range(6)]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main", related=("r1", "r2", "r3"))])
    service.get_snapshot()
    ids = [i.id for i in service.candidates()]
    assert "s" not in ids and len(ids) == len(set(ids)) >= 8
    assert calls[0] == ("main", 40) and ("r3", 40) not in calls  # stops once the row is full


def test_full_row_does_no_backfill(tmp_path):
    calls = []
    service = _service(tmp_path, lambda q, n: calls.append(q) or [_item(i) for i in range(10)],
                       [PopularCategory("c", "C", "main", related=("r1",))])
    service.get_snapshot()
    assert calls == ["main"]


def test_failed_backfill_keeps_the_primary_results(tmp_path):
    def search(query, limit):
        if query != "main":
            raise RuntimeError("429")
        return [_item(i) for i in range(5)]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main", related=("r1",))])
    service.get_snapshot()
    assert len(service.candidates()) == 5


def test_rows_under_four_are_omitted():
    class I:
        def __init__(self, *keys): self.category_keys = keys

    items = [I("a")] * 4 + [I("b")] * 3
    assert viable_category_keys(items, [PopularCategory("a", "A", ""), PopularCategory("b", "B", "")]) == {"a"}


def test_wall_pages_with_opaque_cursors_and_fetches_deeper_only_when_asked(tmp_path):
    calls = []

    def search(query, limit):
        calls.append(limit)
        return [_item(i) for i in range(min(limit, 90))]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main")])
    service.get_snapshot()
    assert calls == [40]
    page1, cursor = service.wall("c", None, 20)
    assert len(page1) == 20 and cursor and "2" not in cursor[:0]
    page2, cursor = service.wall("c", cursor, 20)
    assert calls == [40] and page2[0].id == "20"  # served from cache
    page3, cursor = service.wall("c", cursor, 20)
    assert len(calls) == 2 and calls[1] > 40 and page3[0].id == "40"
    ids = [i.id for i in (*page1, *page2, *page3)]
    assert len(ids) == len(set(ids))
    while cursor:
        page, cursor = service.wall("c", cursor, 40)
    assert calls[-1] <= 120


def test_wall_rejects_bad_cursor_and_unknown_category(tmp_path):
    import pytest
    service = _service(tmp_path, lambda q, n: [], [PopularCategory("c", "C", "main")])
    with pytest.raises(ValueError):
        service.wall("c", "not-a-cursor!", 10)
    with pytest.raises(KeyError):
        service.wall("nope", None, 10)


def test_wall_route_pages_filters_and_rejects(monkeypatch):
    from tests.test_member_suppressions_api import _session, _seed_music_member
    from app import main

    db = _session()
    member = _seed_music_member(db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_a, **_k: None)
    from app.services.popular_discovery import PopularItem
    item = PopularItem(id="w1", title="W", uploader="U", duration=300, thumbnail=None, artwork_url=None,
                       webpage_url="https://www.youtube.com/watch?v=w1", view_count=1, availability=None, published_at=None,
                       source="youtube", source_label="YouTube", capabilities=None, category_keys=("music",))
    monkeypatch.setattr(main.popular_discovery, "wall", lambda key, cursor, limit: ([item], "abc"))
    out = main.popular_wall("music", object(), None, 40, member, db)
    assert [i.id for i in out.items] == ["w1"] and out.next_cursor == "abc"

    def bad(key, cursor, limit):
        raise ValueError
    monkeypatch.setattr(main.popular_discovery, "wall", bad)
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        main.popular_wall("music", object(), "zz", 40, member, db)
    assert exc.value.status_code == 400


def test_cap_rows_pins_twenty_per_row_and_trims_keys():
    from app.schemas import PopularItemResponse
    from app.services.popular_discovery import cap_rows

    items = [PopularItemResponse(id=str(i), title="t", category_keys=["a"] + (["b"] if i % 2 else []), source="youtube")
             for i in range(60)]
    kept = cap_rows(items)
    for key in ("a", "b"):
        assert sum(key in i.category_keys for i in kept) <= 20
    assert sum("a" in i.category_keys for i in kept) == 20
    assert len(kept) < len(items)


def test_wall_backing_off_serves_cache_without_calling_youtube(tmp_path):
    calls = []

    def search(query, limit):
        calls.append(limit)
        if len(calls) > 1:
            raise RuntimeError("429")
        return [_item(i) for i in range(40)]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main")])
    service.get_snapshot()
    page, cursor = service.wall("c", None, 40)
    assert cursor  # more may exist upstream
    page, cursor = service.wall("c", cursor, 40)  # deep fetch fails -> back-off, cached (empty) page, no cursor
    assert cursor is None and len(calls) == 2
    page, cursor = service.wall("c", "dzQw", 40)  # w40: still backing off
    assert len(calls) == 2 and cursor is None


def test_concurrent_deep_fetches_share_one_upstream_call(tmp_path):
    import threading, time
    calls = []

    def search(query, limit):
        calls.append(limit)
        time.sleep(0.2 if limit > 40 else 0)
        return [_item(i) for i in range(min(limit, 100))]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main")])
    service.get_snapshot()
    results = []
    threads = [threading.Thread(target=lambda: results.append(service.wall("c", "dzQw", 40))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len([c for c in calls if c > 40]) == 1 and len(results) == 4
    assert all(len(r[0]) == 40 for r in results)


def test_a_spent_budget_keeps_the_last_rows_and_retries_later_without_a_failure(tmp_path):
    from app.services import provider_budget

    now = [1e9]
    spent = []
    seen = []

    def search(query, limit):
        seen.append(provider_budget.current_priority())
        if spent:
            raise provider_budget.BudgetExhausted("background budget spent")
        return [_item(n) for n in range(10)]

    service = PopularDiscovery(tmp_path / "p.json", search, categories=[PopularCategory("c", "C", "main")], executor=_Now(),
                               clock=lambda: now[0], batch_size=1, max_concurrency=1, random=lambda: 0.5)
    assert service._category_ttl_seconds == 2 * 60 * 60  # the default
    service.get_snapshot()
    ids = [i.id for i in service.candidates()]
    spent.append(True)
    now[0] += 3 * 60 * 60
    service.get_snapshot()
    record = service._load_store(now[0])["categories"]["c"]
    assert seen == ["background", "background"] and [i.id for i in service.candidates()] == ids
    assert record["failure_count"] == 0 and record["error"] is None
    assert record["next_refresh_at"] == now[0] + 15 * 60


def test_a_wall_asks_interactively_for_its_first_page_and_in_the_background_deeper(tmp_path):
    from app.services import provider_budget

    seen = []

    def search(query, limit):
        seen.append(provider_budget.current_priority())
        return [_item(n) for n in range(limit)]

    service = _service(tmp_path, search, [PopularCategory("c", "C", "main")])
    service.wall("c", None, 10)
    service.wall("c", service._encode_cursor(40), 10)
    assert seen[-2:] == ["interactive", "background"]
