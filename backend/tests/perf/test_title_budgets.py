"""Timed title API budgets on the production-scale seed .

    make perf-titles

Skipped unless PERF_TITLES_ROOT names a directory seeded by scripts/perf/seed_titles.py, so make check collects
and skips it. Each budget is the p95 of 20 warm requests (after 2 warm-ups) through the real routes and database.
This machine is faster than the reference host, so passing here is a floor, not proof.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import aliased

ROOT = os.environ.get("PERF_TITLES_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="set PERF_TITLES_ROOT (make perf-titles)")
WARMUP, SAMPLES = 2, 20
FILTERS = (
    ("unwatched", {"unwatched": "true"}), ("in progress", {"in_progress": "true"}), ("favorites", {"favorites": "true"}),
    ("genre", {"genre": "Drama"}), ("years", {"year_from": 1990, "year_to": 2010}), ("4k", {"resolution": "4k"}),
)


@pytest.fixture
def seeded(monkeypatch):  # noqa: ANN001, ANN201
    """Point the app at the seed after conftest's per-test isolation has run."""
    from app import db as db_module
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", Path(ROOT) / "data")
    engine = create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False, "timeout": 10})
    event.listens_for(engine, "connect")(db_module.configure_sqlite_connection)
    monkeypatch.setattr(db_module, "engine", engine)
    db_module.SessionLocal.configure(bind=engine)
    from app.services import art_urls, renditions

    monkeypatch.setattr(art_urls, "_extension", art_urls.extension())  # restored after: use_host_format sets it process-wide
    renditions.use_host_format()  # jpg when this host's ffmpeg has no WebP encoder
    yield db_module.SessionLocal
    engine.dispose()


def p95_ms(client, paths: list[str]) -> float:  # noqa: ANN001
    """Warm every path, then time SAMPLES requests cycling through them; p95 in ms."""
    for path in paths:
        for _ in range(WARMUP):
            assert client.get(path).status_code == 200, path
    samples = []
    for n in range(SAMPLES):
        path = paths[n % len(paths)]
        started = time.perf_counter()
        response = client.get(path)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200, (path, response.text[:200])
    return sorted(samples)[int(SAMPLES * 0.95) - 1]


def _ai_fixtures(db, member, series_id: str, movie_id: str) -> None:  # noqa: ANN001
    """Ten finished episodes of the big show and one finished film, each with a summary and cues."""
    import uuid
    from datetime import datetime

    from app.models import LibraryItem, MediaTitle, PlaybackProgress, Summary, TranscriptCue

    season, episode = aliased(MediaTitle), aliased(MediaTitle)
    season_one = db.scalars(
        select(LibraryItem.id).join(episode, episode.id == LibraryItem.title_id).join(season, season.id == episode.parent_id)
        .where(season.parent_id == series_id, season.index_number == 1).order_by(episode.index_number).limit(10)
    ).all()
    film = db.scalar(select(LibraryItem.id).where(LibraryItem.title_id == movie_id).limit(1))
    for n, item in enumerate([*season_one, film]):
        row = db.scalar(select(PlaybackProgress).where(PlaybackProgress.user_id == member.id, PlaybackProgress.item_id == item))
        if row is None:  # the seed may already hold a row for this (member, item): one row per pair
            row = PlaybackProgress(id=str(uuid.uuid4()), user_id=member.id, item_id=item, duration_seconds=1500)
            db.add(row)
        row.position_seconds, row.completed, row.last_watched_at = 0, True, datetime(2026, 9, 1, 12, n)
        transcript = f"perf-t-{item}"
        db.add(Summary(id=str(uuid.uuid4()), library_item_id=item, transcript_id=transcript, transcript_revision=1, model_id="perf",
                       state="succeeded", overview="A summary of what happened. " * 10, completed_at=datetime(2026, 9, 1),
                       key_points=[{"text": f"Point {k}", "cue_ordinals": [k]} for k in range(1, 6)]))
        db.add_all([TranscriptCue(transcript_id=transcript, ordinal=k, start_ms=k * 60_000, end_ms=k * 60_000 + 900,
                                  text="A line of dialogue long enough to quote.") for k in range(1, 6)])
    db.commit()


def _art_urls(db, app) -> list[str]:  # noqa: ANN001
    """Fifty ready poster renditions made by T1's pipeline; their /api/art URLs at 240w."""
    from app.models import MediaTitle
    from app.services import art_urls
    from app.services.renditions import install, ready_row, render
    from app.services.titles import title_image_bytes, title_image_source

    urls = []
    for title in db.scalars(select(MediaTitle).where(MediaTitle.type == "movie").limit(200)):
        if not art_urls.supported((title.images or {}).get("Primary")):
            continue
        content_type, data = title_image_bytes(db, title, "Primary", app.state.artwork)
        rendered = render(data, content_type, title_type="movie", image_type="Primary", timeout=20)
        key = art_urls.source_key(title.id, "Primary", title_image_source(title, "Primary"))
        install(key, rendered)
        db.merge(ready_row(title.id, "Primary", key, rendered))
        urls.append(f"/api/art/{art_urls.signature(title.id, 'Primary', key)}/{title.id}/Primary/{key}-240.{rendered.ext}")
        if len(urls) == 50:
            break
    db.commit()
    return urls


def _smart_collection(db, member) -> str:  # noqa: ANN001
    """One smart collection the member owns, as Home's Collections shelf reads it."""
    import uuid

    from app.models import HouseholdCollection

    collection = HouseholdCollection(
        id=str(uuid.uuid4()), owner_user_id=member.id, name="Perf dramas", name_key="perf dramas", visibility="private",
        rules={"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": "Drama"}]},
    )
    db.add(collection)
    db.commit()
    return collection.id


def test_title_api_budgets(seeded) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import titles as titles_router
    from app.models import MediaTitle, User
    from app.security import get_current_user

    season = aliased(MediaTitle)
    with seeded() as db:
        member = db.scalar(select(User).where(User.username == "alex"))
        big = db.scalar(select(season.parent_id).where(season.type == "season").group_by(season.parent_id).having(func.count() >= 10).limit(1))
        movie = db.scalar(select(MediaTitle.id).where(MediaTitle.type == "movie").order_by(MediaTitle.id).limit(1))
        _ai_fixtures(db, member, big, movie)
        art = _art_urls(db, app)
        smart = _smart_collection(db, member)
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app, base_url="http://localhost")
    results: list[tuple[str, float, float | None]] = []  # a None budget is reported, not asserted

    def check(label: str, budget: float, *paths: str) -> None:
        results.append((label, p95_ms(client, list(paths)), budget))

    def titles(**params) -> str:
        return "/api/titles?" + "&".join(f"{key}={value}" for key, value in params.items())

    try:
        for kind, first, filtered, later in (("movie", 150, 250, 150), ("series", 250, 400, 250)):
            check(f"{kind} first page (recently added)", first, titles(type=kind, sort="created", limit=60))
            check(f"{kind} first page (A–Z, letters)", first, titles(type=kind, sort="name", limit=60))
            for name, params in FILTERS:
                check(f"{kind} + {name}", filtered, titles(type=kind, sort="created", limit=60, **params))
            cursor = client.get(titles(type=kind, sort="created", limit=60)).json()["next_cursor"]
            check(f"{kind} later page", later, titles(type=kind, sort="created", limit=120, cursor=cursor))
            check(f"{kind} letter jump", later, titles(type=kind, sort="name", limit=120, letter="M"))
            titles_router._facets.clear()  # noqa: SLF001 - one uncached call: the drawer's first open per minute
            started = time.perf_counter()
            assert client.get(f"/api/titles/facets?type={kind}").status_code == 200
            results.append((f"{kind} facets cold (one call, not a budget)", (time.perf_counter() - started) * 1000, None))
            check(f"{kind} facets", 200, f"/api/titles/facets?type={kind}")
        check("movie detail", 100, f"/api/titles/{movie}")
        check("series detail (10 seasons, 200 episodes)", 200, f"/api/titles/{big}")
        check("episodes of a season", 100, f"/api/titles/{big}/episodes?season=3")
        check("episode summaries", 150, f"/api/titles/{big}/episode-summaries?season=1")
        check("key scenes (series)", 150, f"/api/titles/{big}/key-scenes")
        check("key scenes (movie)", 150, f"/api/titles/{movie}/key-scenes")
        check("/api/art hit", 15, *art)
        # Home shelves: each source at Home's page size. title-rows is the slowest, so it is never on
        # Home's first screen in the default order.
        check("home next up", 150, "/api/titles/next-up?limit=20")
        check("home new in your library", 150, titles(sort="created", limit=20))
        check("home new in anime", 150, titles(category="anime", sort="created", limit=20))
        check("home recently added music", 150, titles(type="album", sort="created", limit=20))
        check("home title rows", 400, "/api/home/title-rows")
        check("home smart collection", 250, f"/api/collections/{smart}")
        check("home live snapshot", 100, "/api/discovery/live")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        client.close()
    load, cores = os.getloadavg()[0], os.cpu_count() or 1
    header = f"load {load:.1f} on {cores} cores" + (" -- WARNING: load exceeds cores; these numbers are not a valid budget verdict" if load > cores else "")
    table = "\n".join([header, *(
        f"{'info ' if budget is None else 'OVER ' if p95 > budget else 'ok   '}{p95:8.1f} ms / "
        f"{'    -' if budget is None else f'{budget:5.0f}'} ms  {label}"
        for label, p95, budget in results
    )])
    print("\n" + table)
    assert all(budget is None or p95 <= budget for _label, p95, budget in results), table
