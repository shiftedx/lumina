"""Profile Lumina's Jellyfin item page/search service and JSON response paths.

Use the database produced by ``compare_stack.py setup`` after its container is
stopped, or another representative Lumina database::

    PYTHONPATH=backend backend/.venv/bin/python scripts/perf/jellyfin_hotpaths_profile.py \
        /path/to/lumina-data/app.db --output /tmp/jellyfin-hotpaths.json

Set ``LUMINA_BENCH_BACKEND`` to a detached baseline's backend directory to run
the identical harness against that revision.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import platform
import pstats
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("LUMINA_BENCH_BACKEND", str(ROOT / "backend")))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import User  # noqa: E402
from app.routers.jellyfin_integration import search_items  # noqa: E402
from app.services import member_access  # noqa: E402
from app.services.jellyfin import ItemsQuery, JellyfinMapper  # noqa: E402


def distribution(values: list[float]) -> dict[str, object]:
    ordered = sorted(values)
    return {
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[max(0, int(len(ordered) * 0.95) - 1)],
        "raw_ms": values,
    }


def profile(action) -> str:  # noqa: ANN001
    profiler = cProfile.Profile()
    profiler.enable()
    action()
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumulative").print_stats(30)
    return output.getvalue()


def timed(action, samples: int, warmups: int) -> tuple[list[float], object]:  # noqa: ANN001
    for _ in range(warmups):
        action()
    expected = action()
    values = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        actual = action()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
        assert actual == expected
    return values, expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--username", default="owner")
    parser.add_argument("--search-term", default="Benchmark Movie 2")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"database does not exist: {args.database}")

    engine = create_engine(
        f"sqlite:///{args.database.resolve()}", future=True,
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    sql = {"count": 0, "milliseconds": 0.0}

    def before(_conn, _cursor, _statement, _parameters, context, _executemany) -> None:  # noqa: ANN001
        context._lumina_profile_started = time.perf_counter_ns()

    def after(_conn, _cursor, _statement, _parameters, context, _executemany) -> None:  # noqa: ANN001
        sql["count"] += 1
        sql["milliseconds"] += (time.perf_counter_ns() - context._lumina_profile_started) / 1_000_000

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)

    page_query = ItemsQuery(recursive=True, includeitemtypes=["Movie"], limit=60, fields=["MediaSources"])
    search_query = ItemsQuery(recursive=True, includeitemtypes=["Movie"], searchterm=args.search_term, limit=60)

    def request_user(db: Session) -> User:
        source = db.scalar(select(User).where(User.username == args.username))
        if source is None:
            raise LookupError(args.username)
        snapshot = User(
            id=source.id, username=source.username, display_name=source.display_name,
            role=source.role, is_active=source.is_active,
        )
        return member_access.carry_access(db, snapshot, source)

    def page() -> dict:
        with sessions() as db:
            return JellyfinMapper(db, request_user(db), page_query.csv("fields")).items(page_query)

    def search() -> dict:
        with sessions() as db:
            return search_items(db, request_user(db), search_query)

    service = {}
    payloads = {}
    for name, action in (("items_page", page), ("items_search", search)):
        before_count, before_ms = sql["count"], sql["milliseconds"]
        samples, payload = timed(action, args.samples, args.warmups)
        payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payloads[name] = json.loads(payload)
        calls = args.samples + args.warmups + 1
        service[name] = {
            **distribution(samples),
            "response_bytes": len(payload.encode()),
            "sql_statements_per_call": (sql["count"] - before_count) / calls,
            "sql_ms_per_call": (sql["milliseconds"] - before_ms) / calls,
            "profile_one_call": profile(action),
        }

    # Same response objects through FastAPI's normal return-dict machinery and
    # through an explicit Response, which bypasses response-model/jsonable copying.
    serialization = {}
    for name, payload in payloads.items():
        app = FastAPI()

        @app.get("/dict")
        def normal_dict() -> dict:
            return payload

        @app.get("/response")
        def direct_response():  # noqa: ANN202
            return JSONResponse(payload)

        with TestClient(app) as client:
            for path in ("/dict", "/response"):
                action = lambda path=path: client.get(path).content  # noqa: E731
                values, body = timed(action, args.samples, args.warmups)
                serialization[f"{name}:{path[1:]}"] = {**distribution(values), "response_bytes": len(body)}

    result = {
        "benchmark": "jellyfin_items_service_and_serialization",
        "database": str(args.database.resolve()),
        "database_bytes": args.database.stat().st_size,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "warmups": args.warmups,
        "samples": args.samples,
        "search_term": args.search_term,
        "backend": sys.path[0],
        "service": service,
        "serialization": serialization,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
