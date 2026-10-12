"""Household load run: 6 members hit the hot endpoints concurrently; prints p50/p95/max and max SQL count per endpoint.

    backend/.venv/bin/python scripts/perf/load.py DATA_DIR [--seconds 60] [--port 8799] [--think 0.25] [--ui]

DATA_DIR must come from seed_household.py. The backend is started from serve.py
(provider network stubbed) and stopped when the run ends; --ui needs a built frontend/dist.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
USERS = ["perfadmin", "alex", "blair", "casey", "devon", "emery"]
PASSWORD = "perf-household-password"
WORDS = ["river", "piano", "storm", "orbit harbor", "garden", "jazz", "cedar", "mountain light"]


def sample_ids(data_dir: Path) -> dict[str, list[str]]:
    with sqlite3.connect(f"file:{data_dir / 'app.db'}?mode=ro", uri=True) as db:
        pick = lambda sql: [row[0] for row in db.execute(sql)]  # noqa: E731
        return {
            "transcript": pick("SELECT t.id FROM transcripts t JOIN library_items i ON i.id = t.library_item_id WHERE i.visibility = 'shared' LIMIT 100"),
            "noted": pick("SELECT DISTINCT n.item_id FROM library_notes n JOIN library_items i ON i.id = n.item_id WHERE i.visibility = 'shared' LIMIT 100"),
            "series": pick("SELECT DISTINCT uploader FROM library_items WHERE kind = 'episode' LIMIT 20"),
            "transcribed": pick("SELECT t.library_item_id FROM transcripts t JOIN library_items i ON i.id = t.library_item_id WHERE i.visibility = 'shared' LIMIT 1"),
        }


def endpoints(ids: dict[str, list[str]]) -> dict[str, callable]:
    r = random.choice
    return {
        "session/me": lambda: ("GET", "/api/session/me"),
        "settings/me": lambda: ("GET", "/api/settings/me"),
        "library recent": lambda: ("GET", "/api/library?limit=60"),
        "library title": lambda: ("GET", "/api/library?limit=60&sort=title"),
        "library movies": lambda: ("GET", "/api/library?limit=60&kind=movie"),
        "library episodes group": lambda: ("GET", f"/api/library?limit=60&kind=episode&group={r(ids['series'])}"),
        "library music title": lambda: ("GET", "/api/library?limit=60&kind=music&sort=title"),
        "library videos youtube": lambda: ("GET", "/api/library?limit=60&kind=video&source=youtube"),
        "library imported": lambda: ("GET", "/api/library?limit=60&source=imported"),
        "library missing": lambda: ("GET", "/api/library?limit=60&status=missing"),
        "library recordings": lambda: ("GET", "/api/library?limit=60&kind=recording"),
        "library FTS page": lambda: ("GET", f"/api/library?limit=60&search={r(WORDS)}"),
        "groups episode": lambda: ("GET", "/api/library/groups?kind=episode"),
        "groups music": lambda: ("GET", "/api/library/groups?kind=music"),
        "search (local)": lambda: ("GET", f"/api/search?q={r(WORDS)}"),
        "source-search (stub)": lambda: ("POST", "/api/source-search", {"query": r(WORDS), "limit": 20}),
        "home": lambda: ("GET", "/api/discovery/home"),
        "continue watching": lambda: ("GET", "/api/playback/continue"),
        "follows": lambda: ("GET", "/api/automations"),
        "watch queue": lambda: ("GET", "/api/me/watch-queue"),
        "collections": lambda: ("GET", "/api/collections"),
        "search history": lambda: ("GET", "/api/search/history"),
        "jobs": lambda: ("GET", "/api/jobs"),
        "transcript cues": lambda: ("GET", f"/api/transcripts/{r(ids['transcript'])}/cues?limit=100"),
        "transcript search": lambda: ("GET", f"/api/transcripts/{r(ids['transcript'])}/search?q={r(WORDS)}"),
        "notes": lambda: ("GET", f"/api/library/{r(ids['noted'])}/notes"),
        "admin overview": lambda: ("GET", "/api/admin/overview"),
        "admin tasks": lambda: ("GET", "/api/admin/tasks"),
        "admin diagnostics": lambda: ("GET", "/api/admin/diagnostics"),
        "admin users": lambda: ("GET", "/api/admin/users"),
        "SSE connect": lambda: ("SSE", "/api/events"),
    }


def login(base: str, username: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30, headers={"Origin": base})
    response = client.post("/api/session/login", json={"username": username, "password": PASSWORD})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def call(client: httpx.Client, spec: tuple) -> tuple[int, int]:
    method, path, *body = spec
    if method == "SSE":
        with client.stream("GET", path) as response:
            next(response.iter_raw(), None)  # time to first byte
            return response.status_code, int(response.headers.get("x-perf-queries", 0))
    response = client.request(method, path, json=body[0] if body else None)
    return response.status_code, int(response.headers.get("x-perf-queries", 0))


def run(base: str, ids: dict, seconds: float, think: float) -> dict[str, list[tuple[float, int, int]]]:
    results: dict[str, list[tuple[float, int, int]]] = defaultdict(list)
    lock = threading.Lock()
    deadline = time.monotonic() + seconds

    def member(username: str) -> None:
        client = login(base, username)
        table = endpoints(ids)
        names = [name for name in table if username == "perfadmin" or not name.startswith("admin")]
        rng = random.Random(username)
        while time.monotonic() < deadline:
            name = rng.choice(names)
            started = time.perf_counter()
            status, queries = call(client, table[name]())
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                results[name].append((elapsed, status, queries))
            time.sleep(rng.uniform(0, 2 * think))  # member think time; --think 0 is a closed-loop stress run

    threads = [threading.Thread(target=member, args=(user,)) for user in USERS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def report(results: dict) -> None:
    print(f"{'endpoint':28} {'n':>5} {'p50ms':>7} {'p95ms':>7} {'maxms':>7} {'sql':>5} non2xx")
    for name, rows in sorted(results.items(), key=lambda kv: -statistics.quantiles([r[0] for r in kv[1]], n=20)[18] if len(kv[1]) > 1 else 0):
        times = sorted(r[0] for r in rows)
        p95 = statistics.quantiles(times, n=20)[18] if len(times) > 1 else times[0]
        bad = sorted({r[1] for r in rows if not 200 <= r[1] < 300})
        print(f"{name:28} {len(times):5} {statistics.median(times):7.1f} {p95:7.1f} {times[-1]:7.1f} {max(r[2] for r in rows):5} {bad or ''}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--ui", action="store_true", help="also run the browser measurement (scripts/perf/ui.mjs)")
    parser.add_argument("--think", type=float, default=0.25, help="mean seconds between one member's requests")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py"), str(args.data_dir), str(args.port)])
    try:
        for _ in range(600):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            sys.exit("backend did not start")
        ids = sample_ids(args.data_dir)
        warm = login(base, "perfadmin")
        for name, spec in endpoints(ids).items():  # warm caches once so the run measures steady state
            call(warm, spec())
        results = run(base, ids, args.seconds, args.think)
        print(f"{sum(map(len, results.values())) / args.seconds:.0f} req/s over {args.seconds:.0f}s, 6 members, think {args.think}s")
        report(results)
        rss = subprocess.run(["ps", "-o", "rss=", "-p", str(server.pid)], capture_output=True, text=True).stdout.strip()
        print(f"backend RSS after run: {int(rss or 0) // 1024}MB")
        if args.ui:
            subprocess.run(["node", str(HERE / "ui.mjs"), base, ids["transcribed"][0]], check=True)
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
