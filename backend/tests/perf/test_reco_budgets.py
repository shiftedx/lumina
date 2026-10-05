"""Timed recommendation API budgets on the title seed plus the recommender's own rows.

    make perf-titles

Skipped unless PERF_TITLES_ROOT names a directory seeded by scripts/perf/seed_titles.py. This test adds to that seed: two more members
(five in all), a 2,000-video remote_media catalogue with 768-dim vectors, 400-item pools per member, 45,000 events, 50 follows, satisfied
history, and resident title vectors. The provider and the embedder are stubbed to record any call: a request thread must make none.
Each budget is the p95 of 20 warm requests after 2 warm-ups. This machine is faster than the reference host, so passing is a floor.
"""
from __future__ import annotations

import hashlib
import itertools
import math
import os
import random
import threading
import time
import uuid
from array import array
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select

from perf.test_title_budgets import ROOT, seeded  # noqa: F401  (the fixture and the seed root)

pytestmark = pytest.mark.skipif(not ROOT, reason="set PERF_TITLES_ROOT (make perf-titles)")
WARMUP, SAMPLES = 2, 20
CATALOGUE, POOL, EVENTS_PER_MEMBER, FOLLOWS, HISTORY, DIMS = 2_000, 400, 9_000, 10, 120, 768
MODEL = "perf-768"
CATEGORIES = ("music", "gaming", "cooking", "travel", "sports", "science-technology")
SURFACES = ("home_picked", "home_recommended", "home_because", "explore_for_you", "explore_popular", "up_next", "title_similar")


def _unit_vectors(rng: random.Random, count: int) -> list[array]:
    vectors = []
    for _ in range(count):
        values = array("f", (rng.gauss(0, 1) for _ in range(DIMS)))
        norm = math.sqrt(sum(value * value for value in values))
        vectors.append(array("f", (value / norm for value in values)))
    return vectors


def _seed_reco(db, rng: random.Random):  # noqa: ANN001, ANN202
    """Add the recommender's rows; returns the five member ids (the seed's three plus two) and the catalogue keys."""
    from app.models import MemberInterest, RecoEvent, RecoPool, RemoteMedia, RemotePlaybackProgress, SourceAutomation, User
    from app.security import utcnow

    now = utcnow()
    members = [db.scalar(select(User.id).where(User.username == name)) for name in ("perfadmin", "alex", "blair")]
    for name in ("casey", "drew"):
        member_id = str(uuid.uuid4())
        db.add(User(id=member_id, username=name, display_name=name.title(), role="viewer", is_active=True, onboarding_status="completed"))
        members.append(member_id)
    db.flush()
    vectors = _unit_vectors(rng, 256)  # 256 distinct vectors cycled; cosine timing does not depend on the values
    keys = [hashlib.sha256(f"youtube:perf-{n}".encode()).hexdigest() for n in range(CATALOGUE)]
    db.execute(insert(RemoteMedia), [{
        "key": keys[n], "source_identity": f"youtube:perf-{n}", "extractor": "youtube", "remote_id": f"perf-{n}",
        "webpage_url": f"https://www.youtube.com/watch?v=perf-{n}", "title": f"Perf video {n}", "uploader": f"Perf channel {n % 150}",
        "channel_key": f"https://www.youtube.com/channel/UC{n % 150:022d}", "channel_url": f"https://www.youtube.com/channel/UC{n % 150:022d}",
        "duration": 600, "view_count": 1_000 * n, "published_at": now - timedelta(hours=n), "kind": "video", "category_keys": [CATEGORIES[n % 6]],
        "tokens": [f"t{(n * 7 + i) % 500}" for i in range(40)], "vector_model": MODEL, "vector_signature": keys[n], "vector": vectors[n % 256].tobytes(),
        "fetched_at": now, "last_nominated_at": now,
    } for n in range(CATALOGUE)])
    for member in members:
        pool = rng.sample(range(CATALOGUE), POOL)
        db.execute(insert(RecoPool), [{
            "user_id": member, "item_key": keys[n], "sources": 1 << (i % 4), "seed_ref": None,
            "first_seen_at": now - timedelta(days=1), "last_nominated_at": now - timedelta(hours=1),
        } for i, n in enumerate(pool)])
        lists = [f"{rng.getrandbits(64):016x}" for _ in range(40)]
        db.execute(insert(RecoEvent), [{
            "user_id": member, "at": now - timedelta(minutes=rng.randint(0, 28 * 24 * 60)),
            "kind": rng.choices(("impression", "open", "play", "complete", "not_interested"), (70, 12, 10, 6, 2))[0],
            "surface": rng.choice(SURFACES), "list_id": rng.choice(lists), "position": rng.randint(0, 23), "slot": rng.choice(("exploit", "explore")),
            "target_kind": "remote", "item_key": keys[rng.choice(pool)],
        } for _ in range(EVENTS_PER_MEMBER)])
        db.add_all(MemberInterest(id=str(uuid.uuid4()), user_id=member, category_key=CATEGORIES[n], created_at=now - timedelta(days=30)) for n in range(3))
        db.add_all(SourceAutomation(
            id=str(uuid.uuid4()), user_id=member, label=f"Perf channel {n}", source_url=f"https://www.youtube.com/channel/UC{n:022d}",
            source_type="channel", cron_expression="0 * * * *", created_at=now - timedelta(days=60), feed_entries=[],
        ) for n in range(FOLLOWS))
        for i, n in enumerate(pool[:HISTORY]):
            at = now - timedelta(days=2 + i // 4, minutes=i)
            db.add(RemotePlaybackProgress(
                id=str(uuid.uuid4()), user_id=member, source_identity=f"youtube:perf-{n}", source_identity_key=keys[n], extractor="youtube",
                remote_id=f"perf-{n}", source_url=f"https://www.youtube.com/watch?v=perf-{n}", title=f"Perf video {n}", uploader=f"Perf channel {n % 150}",
                position_seconds=600.0, duration_seconds=600.0, completed=True, last_watched_at=at, created_at=at,
                max_fraction=1.0, plays=1, completions=1, channel_key=f"https://www.youtube.com/channel/UC{n % 150:022d}",
            ))
    db.commit()
    return members, vectors


def p95_ms(call, *, before=lambda: None) -> float:  # noqa: ANN001
    for _ in range(WARMUP):
        before()
        assert call().status_code in (200, 204)
    samples = []
    for _ in range(SAMPLES):
        before()
        started = time.perf_counter()
        response = call()
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code in (200, 204), response.text[:200]
    return sorted(samples)[int(SAMPLES * 0.95) - 1]


def test_reco_api_budgets(seeded, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import MediaTitle, RecoEvent, User
    from app.routers import admin_diagnostics
    from app.config import settings
    from app.security import create_app_session, csrf_token_for, get_current_user
    from app.services import embeddings
    from app.services.rate_limit import rate_limiter
    from app.services.reco import events
    from app.services.yt_dlp_service import YtDlpService

    called: list[str] = []

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        if threading.current_thread().name.startswith("popular-"):  # the household Popular snapshot's own background refresh, never a request thread
            raise AssertionError("popular refresh blocked: the budgets run offline")
        called.append(threading.current_thread().name)
        raise AssertionError("a request thread called the provider or the embedder")

    class Watched(dict):
        """The resident vector map: counts reads, so the budgets below prove the policy used the vectors."""

        reads = 0

        def get(self, key, default=None):  # noqa: ANN001, ANN201
            Watched.reads += 1
            return super().get(key, default)

    rng = random.Random(13)
    with seeded() as db:
        members, vectors = _seed_reco(db, rng)
        member = db.get(User, members[1])  # alex
        title_ids = db.scalars(select(MediaTitle.id).where(MediaTitle.type.in_(("movie", "series", "episode"))).limit(10_000)).all()
        movies = db.scalars(select(MediaTitle.id).where(MediaTitle.type == "movie").order_by(MediaTitle.id).limit(60)).all()
    Watched.reads = 0
    monkeypatch.setattr(embeddings, "_vectors", Watched({MODEL: {title_id: ("title", vectors[n % 256]) for n, title_id in enumerate(title_ids)}}))
    monkeypatch.setattr(embeddings, "serving", lambda _db: SimpleNamespace(model_id=MODEL))
    monkeypatch.setattr(embeddings, "embed", boom)
    monkeypatch.setattr(YtDlpService, "youtube_search", boom)
    monkeypatch.setattr(events, "DAILY_EVENT_CAP", 10**9)  # the cap would turn later samples into cheap drops
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app, base_url="http://localhost")
    results: list[tuple[str, float, float | None]] = []
    counter = itertools.count()

    def check(label: str, budget: float | None, call, *, before=lambda: None) -> None:  # noqa: ANN001
        results.append((label, p95_ms(call, before=before), budget))

    def cold(label: str, budget: float, call) -> None:  # noqa: ANN001
        """First call after the profile and served lists are gone (a generation bump forces the rebuild), as after 10 idle minutes."""
        events.generations.bump(member.id)
        events.served_lists.drop(member.id)
        started = time.perf_counter()
        assert call().status_code == 200
        results.append((f"{label} (cold profile)", (time.perf_counter() - started) * 1000, budget))

    try:
        home = lambda: client.get("/api/discovery/home")  # noqa: E731
        up_next = lambda: client.post("/api/discovery/up-next", json={  # noqa: E731
            "source_url": "https://www.youtube.com/watch?v=perf-1", "source_id": "perf-1", "uploader": "Perf channel 1", "category_keys": [],
            "limit": 12, "channel_id": f"UC{1:022d}"})
        popular = lambda: client.get("/api/discovery/popular")  # noqa: E731
        rows = lambda: client.get("/api/home/title-rows")  # noqa: E731
        similar = lambda: client.get(f"/api/titles/{movies[next(counter) % len(movies)]}/similar?limit=12")  # noqa: E731
        check("home picked for you (k = 20)", 150, home)
        cold("home picked for you", 300, home)
        check("up next (k = 12)", 120, up_next)
        cold("up next", 250, up_next)
        check("explore popular (for you + order)", 150, popular)
        cold("explore popular", 300, popular)
        check("home title rows", 250, rows)
        cold("home title rows", 400, rows)
        check("similar titles (k = 12)", 120, similar)
        cold("similar titles", 250, similar)

        served = client.get("/api/discovery/home").json()["items"]
        annotations = [item["reco"] for item in served if item.get("reco")]
        assert len(annotations) >= 10, "Home served no annotated items: the events below would be dropped, not measured"
        batch = [{"kind": "open", "list_id": annotation["list_id"], "key": annotation["key"], "age_ms": 1_000} for annotation in (annotations * 20)[:200]]
        with seeded() as db:
            before_rows = db.scalar(select(func.count()).select_from(RecoEvent).where(RecoEvent.user_id == member.id, RecoEvent.kind == "open"))
        with seeded() as db:  # the events route is cookie-session only (sendBeacon), so it gets a real session and its CSRF token
            _record, token = create_app_session(db, db.get(User, member.id))
            db.commit()
        beacon = {"X-CSRF-Token": csrf_token_for(token), "Origin": settings.allowed_origins_list[0]}
        check("events (200 per call)", 50, lambda: client.post(
            "/api/reco/events", json={"events": batch}, headers=beacon, cookies={settings.session_cookie_name: token}), before=rate_limiter.clear)
        with seeded() as db:
            stored = db.scalar(select(func.count()).select_from(RecoEvent).where(RecoEvent.user_id == member.id, RecoEvent.kind == "open")) - before_rows
        assert stored == 200 * (WARMUP + SAMPLES), f"{stored} stored: events were deduped or dropped, so the budget measured nothing"

        n = lambda: next(counter)  # noqa: E731
        bodies = {
            "item": lambda k: {"scope": "item", "source_id": f"perf-s-{k}", "title": f"Perf suppressed {k}", "uploader": f"Perf channel {k}"},
            "channel": lambda k: {"scope": "channel", "uploader": f"Perf channel {k}", "channel_id": f"UC{k:022d}"},
            "fewer": lambda k: {"scope": "fewer", "uploader": f"Perf channel {k + 200}", "channel_id": f"UC{k + 200:022d}"},
            "title": lambda k: {"scope": "title", "title_id": movies[k % len(movies)]},
        }
        for scope, body in bodies.items():
            check(f"suppression ({scope})", 50, lambda body=body: client.post("/api/discovery/suppressions", json=body(n())))
        with seeded() as db:
            def section() -> SimpleNamespace:
                admin_diagnostics.recommendations(db)
                return SimpleNamespace(status_code=200, text="")

            results.append(("Diagnostics recommendations section (added work)", p95_ms(section), 80))
        assert not called, called
        assert Watched.reads > 0, "the policy never read the title vectors: these budgets would be vacuous"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        client.close()
    load, cores = os.getloadavg()[0], os.cpu_count() or 1
    header = f"load {load:.1f} on {cores} cores" + (" -- WARNING: load exceeds cores; these numbers are not a valid budget verdict" if load > cores else "")
    table = "\n".join([header, *(
        f"{'info ' if budget is None else 'OVER ' if p95 > budget else 'ok   '}{p95:8.1f} ms / {'    -' if budget is None else f'{budget:5.0f}'} ms  {label}"
        for label, p95, budget in results
    )])
    print("\n" + table)
    assert all(budget is None or p95 <= budget for _label, p95, budget in results), table
