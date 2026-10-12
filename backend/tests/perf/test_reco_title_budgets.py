"""Title recommendation budgets on 10,000 visible titles, no embedder (reco review I2).

    PERF_TITLES_ROOT=1 .venv/bin/python -m pytest tests/perf/test_reco_title_budgets.py -s

Skipped unless PERF_TITLES_ROOT is set, so make check collects and skips it. It seeds its own in-memory database: 10,000
movies with NFO-style cast (no person ids, so tokens hash names) and 40 finished films for the member. Every sample is a
cold served list (``served_lists`` cleared) in a warm process: the path every new title page takes. Asserted: the p95 of
20 samples within the budget, and the median within 60% of it: p95 on a loaded machine is noise-bound, the
median is not (a full GC adds ~50 ms to one request in five). Measured here, 10 cores at load 12: similar median
61-69 ms, p95 113-127 ms; title rows median 123-140 ms, p95 178-200 ms.
The first call of a process also tokenises every title (about 0.3 s here) and is printed, not asserted.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import timedelta

import pytest

from app.models import LibraryItem, MediaTitle, PlaybackProgress, utcnow
from app.services.reco.events import served_lists
from support import make_user, memory_session_factory

pytestmark = pytest.mark.skipif(not os.environ.get("PERF_TITLES_ROOT"), reason="set PERF_TITLES_ROOT (make perf-titles)")
WARMUP, SAMPLES = 2, 20
TITLES = 10_000
HEADROOM = 0.6  # the median must sit within this share of the budget
MEMBER = make_user("reco-perf")
GENRES = ("Drama", "Comedy", "Thriller", "Horror", "Romance", "Action", "Documentary", "Animation", "Crime", "Family",
          "Fantasy", "Mystery", "Science Fiction", "War", "Western", "History", "Music", "Adventure", "TV Movie", "Sport")


def _tid(n: int) -> str:
    return str(uuid.UUID(int=n + 1))


@pytest.fixture
def db_factory():  # noqa: ANN201 - overrides conftest's: api_client reads this seeded factory
    factory = memory_session_factory()
    now = utcnow()
    with factory() as session:
        session.add(MEMBER)
        for n in range(TITLES):
            people = [{"person_id": None, "name": f"Actor {(n * 7 + k) % 3000}", "role": None, "type": "Actor"} for k in range(8)]
            people.append({"person_id": None, "name": f"Director {n % 900}", "role": None, "type": "Director"})
            session.add(MediaTitle(
                id=_tid(n), type="movie", key=f"perf:{n}", name=f"Film {n}", boxset_id=_tid(TITLES + n // 4) if n % 10 == 0 else None,
                year=1950 + n % 75, provider_ids={}, field_sources={}, images={}, category="movies",
                metadata_json={"genres": [GENRES[n % 20], GENRES[n * 3 % 20]], "people": people, "community_rating": 5 + n % 5,
                               "overview": f"Overview of film {n}."},
                created_at=now - timedelta(days=n % 400),
            ))
            session.add(LibraryItem(id=f"{_tid(n)}-v", user_id="owner", visibility="shared", title=f"Film {n}", title_id=_tid(n),
                                    duration=6000, file_size=1_000, metadata_json={}, status="available", kind="movie"))
        for n in range(40):  # the newest three are the Because anchors
            session.add(PlaybackProgress(id=str(uuid.uuid4()), user_id=MEMBER.id, item_id=f"{_tid(n * 211)}-v", position_seconds=6000,
                                         duration_seconds=6000, completed=True, last_watched_at=now - timedelta(days=1, hours=n)))
        session.commit()
    try:
        yield factory
    finally:
        factory.kw["bind"].dispose()


def timings(client, path: str) -> tuple[float, float, float]:  # noqa: ANN001
    """(first call of the process, median, p95) in ms over SAMPLES calls after WARMUP, each with a cold served list."""
    def timed() -> float:
        served_lists.clear()
        started = time.perf_counter()
        response = client.get(path)
        elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200, (path, response.text[:200])
        return elapsed
    first = timed()
    for _ in range(WARMUP - 1):
        timed()
    samples = sorted(timed() for _ in range(SAMPLES))
    return first, samples[SAMPLES // 2], samples[int(SAMPLES * 0.95) - 1]


def test_more_like_this_on_10000_titles(api_client) -> None:  # noqa: ANN001
    client = api_client(user=MEMBER, base_url="http://localhost")
    first, median, p95 = timings(client, f"/api/titles/{_tid(5)}/similar?limit=12")
    print(f"similar: first {first:.0f} ms, median {median:.0f} ms, p95 {p95:.0f} ms")  # noqa: T201 - the report reads these
    assert len(client.get(f"/api/titles/{_tid(5)}/similar?limit=12").json()) == 12
    assert p95 <= 120 and median <= 120 * HEADROOM


def test_title_rows_on_10000_titles(api_client) -> None:  # noqa: ANN001
    client = api_client(user=MEMBER, base_url="http://localhost")
    first, median, p95 = timings(client, "/api/home/title-rows")
    print(f"title rows: first {first:.0f} ms, median {median:.0f} ms, p95 {p95:.0f} ms")  # noqa: T201
    assert [row["kind"] for row in client.get("/api/home/title-rows").json()["rows"]] == ["because_you_watched"] * 3 + ["recommended"]
    assert p95 <= 250 and median <= 250 * HEADROOM
