"""Repeatable legacy/current benchmark for ``TitleService.letters``.

Run from the repository root:
    PYTHONPATH=backend backend/.venv/bin/python scripts/perf/title_letters_bench.py
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import LibraryItem, MediaTitle, User
from app.services.titles import TITLE_SORTS, TitleFilters, TitleService

WARMUPS = 5
SAMPLES = 30
TIERS = (2_585, 50_000)


def timed(call) -> list[float]:  # noqa: ANN001
    for _ in range(WARMUPS):
        call()
    values = []
    for _ in range(SAMPLES):
        started = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return values


def summary(values: list[float]) -> dict[str, object]:
    ordered = sorted(values)
    return {
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[int(len(ordered) * 0.95) - 1],
        "raw_ms": values,
    }


def one_tier(count: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as raw:
        db_path = Path(raw) / "catalog.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        now = datetime(2026, 1, 1)
        with Session(engine, expire_on_commit=False) as db:
            user = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
            db.add(user)
            db.commit()
            titles, items = [], []
            for index in range(count):
                if index % 101 == 0:
                    name = f"{index % 10} Film {index:05d}"
                elif index % 103 == 0:
                    name = f"_Film {index:05d}"
                elif index % 107 == 0:
                    name = f"Été Film {index:05d}"
                else:
                    name = f"{chr(65 + index % 26)} Film {index:05d}"
                title_id = f"title-{index:05d}"
                titles.append({
                    "id": title_id, "type": "movie", "key": f"bench:{index}", "name": name,
                    "metadata_json": {}, "images": {}, "provider_ids": {}, "field_sources": {},
                    "created_at": now, "updated_at": now,
                })
                items.append({
                    "id": f"item-{index:05d}", "user_id": user.id, "visibility": "shared", "extractor": "local",
                    "remote_id": f"remote-{index:05d}", "title": name, "kind": "movie", "status": "available",
                    "title_id": title_id, "metadata_json": {}, "metadata_summary": {}, "created_at": now, "updated_at": now,
                })
            with engine.begin() as connection:
                connection.execute(insert(MediaTitle), titles)
                connection.execute(insert(LibraryItem), items)

            service = TitleService(db)
            filters = TitleFilters()
            predicates = service.list_predicates(user, ("movie",), filters)
            initial = func.upper(func.substr(func.coalesce(MediaTitle.sort_name, MediaTitle.name), 1, 1))

            def legacy_letters() -> tuple[list[tuple[str, int]], int]:
                """The complete implementation from baseline commit 392719d5."""
                anchors: dict[str, int] = {}
                total = 0
                rows = db.scalars(select(initial).where(*predicates).order_by(*TITLE_SORTS["name"]))
                for total, first in enumerate(rows, start=1):
                    anchors.setdefault(first if len(first) == 1 and "A" <= first <= "Z" else "#", total - 1)
                return list(anchors.items()), total

            def current_letters() -> tuple[list[tuple[str, int]], int]:
                rail, total = service.letters(user, types=("movie",), filters=filters)
                return [(row.letter, row.index) for row in rail], total

            expected = legacy_letters()
            actual = current_letters()
            assert actual == expected, {"legacy": expected, "current": actual}
            legacy, current = timed(legacy_letters), timed(current_letters)
        engine.dispose()
        p95_index = int(SAMPLES * 0.95) - 1
        return {
            "titles": count,
            "items": count,
            "database": "file-backed SQLite, fresh schema, unanalyzed",
            "visible": "all shared to one viewer",
            "initial_mix": "ASCII A-Z plus digit, underscore, and trailing É groups",
            "legacy": summary(legacy),
            "current": summary(current),
            "p50_speedup": statistics.median(legacy) / statistics.median(current),
            "p95_speedup": sorted(legacy)[p95_index] / sorted(current)[p95_index],
        }


result = {
    "benchmark": "title_letter_rail_sql_aggregation",
    "baseline_commit": "392719d58460be00e8080fed96a50e221537b94a",
    "head_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "implementation": "working tree",
    "python": platform.python_version(),
    "platform": platform.platform(),
    "cpu_count": os.cpu_count(),
    "load_average_at_start": os.getloadavg(),
    "warmups": WARMUPS,
    "samples": SAMPLES,
    "command": "PYTHONPATH=backend backend/.venv/bin/python scripts/perf/title_letters_bench.py",
    "tiers": [one_tier(count) for count in TIERS],
    "load_average_at_end": os.getloadavg(),
}
print(json.dumps(result, indent=2, sort_keys=True))
