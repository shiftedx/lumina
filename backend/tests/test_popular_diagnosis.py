"""Why Popular rows end with one video: survivors after each stage over a realistic 25 x 8 snapshot (2.4.0)."""
from __future__ import annotations

import random
from collections import Counter

from app.services.popular_discovery import POPULAR_CATEGORIES, PopularDiscovery

NOW = 1_700_000_000.0


class _Now:
    def submit(self, function):  # noqa: ANN001
        function()


def _snapshot(tmp_path, **kw):
    rng = random.Random(7)
    pool = [f"v{i}" for i in range(120)]  # shared videos create cross-category overlap

    def search(query: str, limit: int):
        items = []
        for i in range(8):
            vid = rng.choice(pool) if rng.random() < 0.15 else f"{query}-{i}"
            short = rng.random() < 0.08
            items.append({"id": vid, "title": vid, "webpage_url": f"https://www.youtube.com/watch?v={vid}",
                          "duration": 30 if short else 600, "view_count": rng.randint(1_000, 5_000_000)})
        return {"items": items}

    service = PopularDiscovery(tmp_path / "p.json", search, executor=_Now(), clock=lambda: NOW, batch_size=25,
                               max_concurrency=1, random=lambda: 0.5, **kw)
    service.get_snapshot()
    return service


def test_the_feed_cut_not_the_filters_is_what_starves_rows(tmp_path):
    service = _snapshot(tmp_path, per_category_limit=8)
    cut = service.get_snapshot().items  # what the rails are built from today
    uncut = service.candidates()
    per_row = lambda items: Counter(k for item in items for k in item.category_keys)  # noqa: E731
    provider = 25 * 8
    shorts = sum(1 for i in uncut if (i.duration or 999) < 60)
    print(f"provider={provider} unique={len(uncut)} shorts={shorts} after_feed_cut={len(cut)}")
    cut_rows, uncut_rows = per_row(cut), per_row(uncut)
    print("rows with <=1 video: cut", sum(1 for c in POPULAR_CATEGORIES if cut_rows[c.key] <= 1),
          "uncut", sum(1 for c in POPULAR_CATEGORIES if uncut_rows[c.key] <= 1))
    lost_to_cut = len(uncut) - len(cut)
    lost_to_shorts = shorts
    assert lost_to_cut > 5 * lost_to_shorts  # the 24-item cut dominates
    assert sum(1 for c in POPULAR_CATEGORIES if cut_rows[c.key] <= 1) >= 10
    assert all(uncut_rows[c.key] >= 6 for c in POPULAR_CATEGORIES)
