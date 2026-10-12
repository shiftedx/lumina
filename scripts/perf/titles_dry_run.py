"""Real-library dry run. Lead-only: on the reference host, in a one-off RC container.

    python scripts/perf/titles_dry_run.py DATA_DIR [--repeats 20] [--sample 200] [--seed 7] [--music-root /media/music]

DATA_DIR is a copy of production's app-data (at least app.db, made with sqlite3 .backup) under the system temp
directory, with the media roots mounted read-only as in production. The run migrates that copy (init_db first
writes its pre-upgrade backup to DATA_DIR/backups/, still inside the copy) and writes renditions into
DATA_DIR/artwork-cache; it writes nothing else. It never reaches the network: only locally stored art is rendered,
the AI, speech and TMDB settings are blanked, and every outbound connection except loopback and unix sockets is
refused and counted (`refused_connections`: non-zero means an endpoint tried to go out). It times every endpoint of success criteria 3-5
in-process as each active member (p50/p95 over warm requests), renders a random sample of images for time and
size, and reports artwork coverage per title and image type. It prints one JSON object of endpoint names,
timings and counts: no names, titles, ids or paths.

The category walls, sections and music endpoints are timed too; the report
adds the upgrade's duration (step 8's category backfill included), title counts per type and category (criterion 4:
`uncategorised` must be 0), the re-sort's duration (criterion 6), and with --music-root one import of that read-only
mount into the copy followed by a no-change rescan (criterion 8): durations, counters and counts only.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import random
import socket
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# Success criteria 3-5: the p95 (ms) each endpoint must meet on the reference host.
BUDGETS_MS = {
    "movies first page (name, total, letters)": 150, "movies first page (created)": 150, "movies unwatched chip": 250,
    "movies genre filter": 250, "movies later page": 150, "movies facets": 200,
    "series first page (name, total, letters)": 250, "series first page (created)": 250, "series unwatched chip": 400,
    "series genre filter": 400, "series later page": 250, "series facets": 200,
    "anime first page (name, total, letters)": 150, "anime first page (created)": 150, "anime unwatched chip": 250,
    "anime genre filter": 250, "anime later page": 150, "anime facets": 200,
    "movie detail": 100, "series detail (largest)": 200, "season episodes": 100,
    "episode summaries": 150, "key scenes": 150, "recap": 150, "art hit": 15,
    "sections": 150, "albums first page (name, total, letters)": 100, "albums first page (created)": 100,
    "album detail (largest)": 120, "artist detail": 100,
}
# The wall each row family lists: Movies and Shows by category, as the 1.6 walls ask.
WALLS = (("movies", {"category": "movies"}), ("series", {"category": "shows"}), ("anime", {"category": "anime"}))
# Kill switches for everything that could reach another host (the socket guard below catches the rest).
OFFLINE_ENV = {"LUMINA_AI_BASE_URL": "", "LUMINA_ASR_BASE_URL": "", "LUMINA_TMDB_API_KEY": "", "LUMINA_ARTWORK_PASS": "false"}
refused_connections = 0


def guard_network():  # noqa: ANN201
    """Refuse every socket connection except loopback and unix sockets, counting them; returns an undo."""
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(sock: socket.socket, address) -> None:  # noqa: ANN001
        global refused_connections
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        host = address[0] if isinstance(address, tuple) else str(address)
        try:
            if ipaddress.ip_address(host.split("%", 1)[0]).is_loopback:
                return
        except ValueError:
            if host == "localhost":
                return
        refused_connections += 1
        raise ConnectionRefusedError("titles_dry_run: outbound connection refused (only loopback is allowed)")

    def connect(sock: socket.socket, address) -> None:  # noqa: ANN001
        check(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock: socket.socket, address) -> int:  # noqa: ANN001
        check(sock, address)
        return original_connect_ex(sock, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex

    def restore() -> None:
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex

    return restore


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def check_copy(data_dir: Path) -> Path:
    resolved = data_dir.resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if temp not in resolved.parents:
        raise SystemExit("DATA_DIR must be a copy under the temporary directory, never the live app-data")
    return resolved


def _timed(client, urls: list[str], repeats: int, before=None) -> dict:  # noqa: ANN001
    """p50/p95 over ``repeats`` warm requests; ``before`` runs ahead of each (to time an uncached answer)."""
    def get(url: str) -> int:
        if before is not None:
            before()
        return client.get(url).status_code

    statuses = {get(url) for url in urls}  # warm-up
    samples = []
    for index in range(repeats):
        started = time.perf_counter()
        statuses.add(get(urls[index % len(urls)]))
        samples.append((time.perf_counter() - started) * 1000)
    return {"status": max(statuses), "p50": round(statistics.median(samples), 1), "p95": round(p95(samples), 1)}


def targets(client, db) -> dict[str, str]:  # noqa: ANN001
    """Endpoint name -> URL for criteria 3 and 4, chosen from what the current member can see."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from app.models import LibraryItem, MediaTitle

    urls: dict[str, str] = {"sections": "/api/library/sections"}
    for kind, wall in WALLS:
        base = {**wall, "limit": 60}
        urls[f"{kind} first page (name, total, letters)"] = f"/api/titles?{urlencode({**base, 'sort': 'name'})}"
        urls[f"{kind} first page (created)"] = f"/api/titles?{urlencode({**base, 'sort': 'created'})}"
        urls[f"{kind} unwatched chip"] = f"/api/titles?{urlencode({**base, 'sort': 'created', 'unwatched': 'true'})}"
        urls[f"{kind} facets"] = f"/api/titles/facets?{urlencode(wall)}"
        genres = client.get(urls[f"{kind} facets"]).json().get("genres") or []
        if genres:
            genre = max(genres, key=lambda facet: facet["count"])["name"]
            urls[f"{kind} genre filter"] = f"/api/titles?{urlencode({**base, 'sort': 'created', 'genre': genre})}"
        first = client.get(urls[f"{kind} first page (created)"]).json()
        if first.get("next_cursor"):
            urls[f"{kind} later page"] = f"/api/titles?{urlencode({**base, 'sort': 'created', 'cursor': first['next_cursor']})}"
        if kind == "movies" and first.get("items"):
            movie = first["items"][0]["id"]
            urls["movie detail"] = f"/api/titles/{movie}"
            urls["key scenes"] = f"/api/titles/{movie}/key-scenes"
    albums = {"type": "album", "limit": 60}
    urls["albums first page (name, total, letters)"] = f"/api/titles?{urlencode({**albums, 'sort': 'name'})}"
    urls["albums first page (created)"] = f"/api/titles?{urlencode({**albums, 'sort': 'created'})}"
    largest_albums = db.scalars(
        select(LibraryItem.title_id).join(MediaTitle, MediaTitle.id == LibraryItem.title_id)
        .where(MediaTitle.type == "album").group_by(LibraryItem.title_id).order_by(func.count().desc()).limit(50)
    )
    for album_id in largest_albums:
        if client.get(f"/api/titles/{album_id}").status_code == 200:
            urls["album detail (largest)"] = f"/api/titles/{album_id}"
            break
    artists = client.get(f"/api/titles?{urlencode({'type': 'artist', 'sort': 'name', 'limit': 1})}").json().get("items") or []
    if artists:
        urls["artist detail"] = f"/api/titles/{artists[0]['id']}"
    season = aliased(MediaTitle)
    largest = db.execute(
        select(season.parent_id).select_from(MediaTitle).join(season, MediaTitle.parent_id == season.id)
        .where(MediaTitle.type == "episode").group_by(season.parent_id).order_by(func.count().desc()).limit(50)
    ).scalars()
    for series_id in largest:
        detail = client.get(f"/api/titles/{series_id}")
        if detail.status_code != 200:
            continue
        urls["series detail (largest)"] = f"/api/titles/{series_id}"
        numbers = [child["index_number"] for child in detail.json().get("children") or [] if child.get("type") == "season" and child.get("index_number") is not None]
        if numbers:
            number = max(numbers)
            urls["season episodes"] = f"/api/titles/{series_id}/episodes?season={number}"
            urls["episode summaries"] = f"/api/titles/{series_id}/episode-summaries?season={number}"
            episodes = client.get(urls["season episodes"]).json()
            if episodes:
                urls["recap"] = f"/api/titles/{episodes[-1]['id']}/recap"
        break
    return urls


def coverage(db) -> dict:  # noqa: ANN001
    """Per title type and image type: entries, supported sources, and readable supported files."""
    from sqlalchemy import select

    from app.models import MediaTitle, StorageRoot
    from app.services import art_urls
    from app.services.media_artifacts import artifact_file

    roots = {root.id: root for root in db.scalars(select(StorageRoot))}
    counts: dict[str, Counter] = defaultdict(Counter)
    for title in db.scalars(select(MediaTitle)):
        for image_type, entry in (title.images or {}).items():
            bucket = counts[f"{title.type}:{image_type}"]
            bucket["entries"] += 1
            if not art_urls.supported(entry):
                continue
            bucket["supported"] += 1
            root, relative = roots.get(title.root_id), entry.get("path")
            try:
                readable = root is not None and isinstance(relative, str) and artifact_file(root.path, relative).is_file()
            except (OSError, ValueError):
                readable = False
            bucket["readable"] += int(readable)
    return {key: {**value, "share": round(value["readable"] / value["entries"], 4)} for key, value in sorted(counts.items())}


def category_counts(db) -> dict:  # noqa: ANN001
    """Titles per type and category, and criterion 4's count of movies, shows, seasons and episodes without one."""
    from sqlalchemy import func, select

    from app.models import CATEGORY_OF_TYPE, MediaTitle

    rows = db.execute(select(MediaTitle.type, MediaTitle.category, func.count()).group_by(MediaTitle.type, MediaTitle.category)).all()
    return {
        "by_type": {f"{kind}:{category or '-'}": count for kind, category, count in rows},
        "uncategorised": sum(count for kind, category, count in rows if kind in CATEGORY_OF_TYPE and category is None),
    }


def time_recategorise(db) -> float:  # noqa: ANN001
    """Criterion 6: the four unscoped statements with the saved folders, as categories.recategorise() runs them (ms)."""
    from app.services.categories import anime_folders, category_sql

    started = time.perf_counter()
    for clause in category_sql(anime_folders(db)):
        db.execute(clause)
    db.commit()
    return round((time.perf_counter() - started) * 1000, 1)


def scan_music(music_root: Path) -> dict:
    """Register ``music_root`` as an external root in the copy, import it, then rescan it unchanged (criterion 8).
    Durations, the runs' integer counters and title/track counts only: no names or paths."""
    import uuid

    from sqlalchemy import func, select

    from app import db as db_module
    from app.models import ImportRun, LibraryItem, MediaTitle, StorageRoot, User
    from app.services.library_import import LibraryImportService

    report: dict = {}
    with db_module.SessionLocal() as db:
        member = db.scalar(select(User.id).where(User.is_active).order_by(User.role != "admin", User.id).limit(1))
        root = StorageRoot(id=str(uuid.uuid4()), label="Music", path=str(music_root.resolve()), mode="external", enabled=True)
        db.add(root)
        db.commit()
        for name in ("first_scan", "rescan"):
            service = LibraryImportService(db)
            run_id = service.start(root.id, member, "shared").id
            db.commit()
            started = time.perf_counter()
            while not service.step(run_id):
                pass
            report[f"{name}_ms"] = round((time.perf_counter() - started) * 1000, 1)
            run = db.get(ImportRun, run_id)
            report[f"{name}_state"] = run.state
            report[f"{name}_counters"] = {key: value for key, value in (run.counters or {}).items() if type(value) is int}
        for kind in ("album", "artist"):
            report[f"{kind}s"] = db.scalar(select(func.count()).select_from(MediaTitle).where(MediaTitle.type == kind)) or 0
        report["tracks"] = db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.kind == "track")) or 0
    return report


def render_sample(db, artwork, size: int, rng: random.Random) -> tuple[dict, list[str]]:  # noqa: ANN001
    """Render `size` random supported images; returns timings, sizes, failure reasons and one /api/art URL each."""
    from sqlalchemy import select

    from app.models import MediaTitle
    from app.services import art_urls, renditions
    from app.services.titles import title_image_bytes, title_image_source

    # Locally stored art only: a {"tmdb": …} entry would be fetched from TMDB on a cache miss.
    candidates = [
        (title, image_type) for title in db.scalars(select(MediaTitle)) for image_type in art_urls.ART_TYPES
        if art_urls.supported(entry := (title.images or {}).get(image_type)) and entry.get("path")
    ]
    render_ms: dict[str, list[float]] = defaultdict(list)
    sizes: dict[str, list[int]] = defaultdict(list)
    failures: Counter = Counter()
    urls: list[str] = []
    for title, image_type in rng.sample(candidates, min(size, len(candidates))):
        kind = "still" if title.type == "episode" and image_type == "Primary" else image_type.lower()
        try:
            content_type, data = title_image_bytes(db, title, image_type, artwork)
            started = time.perf_counter()
            rendered = renditions.render(data, content_type, title_type=title.type, image_type=image_type, timeout=30)
        except FileNotFoundError:
            failures["unreadable"] += 1
            continue
        except renditions.RenditionError as exc:
            failures[exc.reason] += 1
            continue
        render_ms[kind].append((time.perf_counter() - started) * 1000)
        for width, path in rendered.files.items():
            sizes[f"{kind}-{width}"].append(path.stat().st_size)
        key = art_urls.source_key(title.id, image_type, title_image_source(title, image_type))
        renditions.install(key, rendered)
        urls.append(f"/api/art/{art_urls.signature(title.id, image_type, key)}/{title.id}/{image_type}/{key}-{min(rendered.files)}.{rendered.ext}")
    summary = {
        "render_ms": {kind: {"p50": round(statistics.median(values), 1), "p95": round(p95(values), 1), "n": len(values)} for kind, values in sorted(render_ms.items())},
        "bytes": {name: {"p50": int(statistics.median(values)), "max": max(values)} for name, values in sorted(sizes.items())},
        "failures": dict(failures),
    }
    return summary, urls


def time_art_hits(client, urls: list[str], repeats: int, engine) -> dict:  # noqa: ANN001
    """/api/art hits after one warming request each; also counts SQL statements (criterion 5: zero)."""
    from sqlalchemy import event

    statements: list[int] = []

    def count(*_args) -> None:  # noqa: ANN002
        statements.append(1)

    for url in urls:
        client.get(url)
    event.listen(engine, "before_cursor_execute", count)
    try:
        timed = _timed(client, urls, repeats)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return {**timed, "sql_statements": len(statements)}


def measure(*, repeats: int, sample: int, seed: int) -> dict:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app import db as db_module
    from app.config import settings
    from app.main import app, artwork
    from app.models import User
    from app.routers import library_sections
    from app.security import get_current_user
    from app.services.renditions import use_host_format

    host = next((name for name in settings.trusted_hosts_list if "*" not in name), "localhost")
    endpoints: dict[str, dict] = defaultdict(dict)
    report: dict = {"endpoints": endpoints, "over_budget": []}
    with db_module.SessionLocal() as db:
        members = list(db.scalars(select(User).where(User.is_active).order_by(User.id)))
        report["members"] = len(members)
        report["coverage"] = coverage(db)
        report["categories"] = category_counts(db)
        report["recategorise_ms"] = time_recategorise(db)
        report["rendition_format"] = use_host_format()  # JPEG when this host's ffmpeg has no WebP encoder
        report["renditions"], art = render_sample(db, artwork, sample, random.Random(seed)) if sample else ({}, [])
        client = TestClient(app, base_url=f"http://{host}")
        try:
            for number, member in enumerate(members, 1):
                app.dependency_overrides[get_current_user] = lambda member=member: member
                for name, url in targets(client, db).items():
                    before = library_sections.clear if name == "sections" else None  # time the uncached answer
                    endpoints[name][f"member-{number}"] = _timed(client, [url], repeats, before)
            if art:
                endpoints["art hit"]["any"] = time_art_hits(client, art[:20], repeats, db_module.engine)
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            client.close()
    report["refused_connections"] = refused_connections
    report["over_budget"] = sorted(name for name, rows in endpoints.items() if name in BUDGETS_MS and any(row["p95"] > BUDGETS_MS[name] for row in rows.values()))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Gallery real-library dry run (lead-only).")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--music-root", type=Path, default=None, help="the read-only music mount to import into the copy (criterion 8)")
    args = parser.parse_args()
    data_dir = check_copy(args.data_dir)
    with sqlite3.connect(data_dir / "app.db") as connection:
        schema_before = connection.execute("PRAGMA user_version").fetchone()[0]
    guard_network()  # before the app is imported, for the whole run
    os.environ.update(OFFLINE_ENV)
    os.environ["LUMINA_DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.db import init_db

    started = time.perf_counter()
    init_db()  # the copy gains every schema step up to 8 (the category backfill included), exactly as production will
    upgrade_ms = round((time.perf_counter() - started) * 1000, 1)
    music = scan_music(args.music_root) if args.music_root else None
    report = measure(repeats=args.repeats, sample=args.sample, seed=args.seed)
    print(json.dumps({"schema_version_before": schema_before, "upgrade_ms": upgrade_ms, "music": music, **report}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
