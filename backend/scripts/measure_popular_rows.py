"""Real-network measurement (not a test): videos per Popular row before vs after Track P.

Run: cd backend && PYTHONPATH=. .venv/bin/python scripts/measure_popular_rows.py [N categories, default 25]
Before = one ytsearch of 8, then the 24-item feed cut the rails were built from.
After  = 40 per category (2 result pages) + related-query backfill, uncut. Makes ~1-2 real YouTube searches per category.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from app.db import SessionLocal
from app.services.popular_discovery import OMIT_ROW_BELOW, POPULAR_CATEGORIES, PopularDiscovery
from app.services.yt_dlp_service import YtDlpService


def search(query: str, limit: int):
    with SessionLocal() as db:
        return YtDlpService(db).youtube_search(query, limit, deep=limit > 24)


def rows(items, categories):
    return {c.key: sum(1 for i in items if c.key in i.category_keys) for c in categories}


class Inline:
    def submit(self, fn):  # noqa: ANN001
        fn()


def run(limit: int, categories, **kw):
    path = Path(tempfile.mkdtemp()) / "p.json"
    service = PopularDiscovery(path, search, categories=categories, executor=Inline(), batch_size=len(categories),
                               max_concurrency=2, per_category_limit=limit, **kw)
    service.get_snapshot()
    return service


if __name__ == "__main__":
    cats = POPULAR_CATEGORIES[: int(sys.argv[1]) if len(sys.argv) > 1 else 25]
    before = run(8, tuple(type(c)(c.key, c.label, c.query) for c in cats))  # no related queries: today's behaviour
    b = rows(before.get_snapshot().items, cats)  # the 24-item feed cut
    after = run(40, cats)
    a = rows(after.candidates(), cats)
    print(f"{'category':22} before  after")
    for c in cats:
        print(f"{c.key:22} {b[c.key]:6} {a[c.key]:6}")
    print(f"rows shown (>= {OMIT_ROW_BELOW}): before {sum(v >= OMIT_ROW_BELOW for v in b.values())}, after {sum(v >= OMIT_ROW_BELOW for v in a.values())}")
    print(f"rows with <=1 video: before {sum(v <= 1 for v in b.values())}, after {sum(v <= 1 for v in a.values())}")
