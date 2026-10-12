"""Query budget (spec "Testing"): hot lists run a fixed number of SQL statements whatever the page size.

Seeds 2,000 series (2 seasons x 5 episodes = 20,000 episodes; every 5th series is anime), 3,000 movies and one
member with 500 progress rows into conftest's file-backed database, then counts statements per request with a
SQLAlchemy cursor hook. Counts are deterministic; wall-clock is not asserted.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event, insert

from app import db as db_module
from app.db import Base
from app.main import app
from app.models import LibraryItem, MediaTitle, PlaybackProgress, User
from app.security import get_current_user
from app.services.media_titles import jellyfin_id, synthetic_id
from support import make_user, seed_app_settings
from title_support import ALICE, ALICE_TOKEN, T0, device_token, mediabrowser, uid

SERIES, SEASONS, EPISODES, MOVIES = 2_000, 2, 5, 3_000
BUDGET = 14  # TitleService.load's artwork query adds one to "next up"; 2.8.0: the caller's member access read (ADR 0019)
TV, MOVIES_VIEW = jellyfin_id(synthetic_id("view:tvshows")), jellyfin_id(synthetic_id("view:movies"))
ANIME_VIEW = jellyfin_id(synthetic_id("view:anime"))
CASES = {
    "series page": ("/Items", {"ParentId": TV, "IncludeItemTypes": "Series", "Recursive": "true", "Fields": "Overview,ProviderIds"}),
    "episode page with sources": ("/Items", {"ParentId": TV, "IncludeItemTypes": "Episode", "Recursive": "true", "Fields": "MediaSources"}),
    "movies by date added": ("/Items", {"ParentId": MOVIES_VIEW, "IncludeItemTypes": "Movie", "Recursive": "true",
                                                 "SortBy": "DateCreated", "SortOrder": "Descending"}),
    "played episodes": ("/Items", {"IncludeItemTypes": "Episode", "Recursive": "true", "Filters": "IsPlayed"}),
    "next up": ("/Shows/NextUp", {}),
    "resume": ("/UserItems/Resume", {}),
    "latest shows": ("/Items/Latest", {"ParentId": TV}),
    "anime series page": ("/Items", {"ParentId": ANIME_VIEW, "IncludeItemTypes": "Series", "Recursive": "true", "Fields": "Overview,ProviderIds"}),
    "latest anime": ("/Items/Latest", {"ParentId": ANIME_VIEW}),
    "views": ("/UserViews", {}),
    "lumina title grid": ("/api/titles", {"type": "episode"}),
}


def _seed() -> None:
    Base.metadata.create_all(bind=db_module.engine)
    titles: list[dict] = []
    items: list[dict] = []
    progress: list[dict] = []

    def watched(item_id: str, minute: int, *, position: int, completed: bool) -> None:
        progress.append({"id": uid(50_000_000 + len(progress)), "user_id": ALICE, "item_id": item_id, "position_seconds": position,
                         "duration_seconds": 1500, "completed": completed, "last_watched_at": T0 + timedelta(minutes=minute)})

    for s in range(SERIES):
        series_id = uid(10_000_000 + s)
        category = "anime" if s % 5 == 0 else "shows"  # 400 anime series: the Anime view pages like Shows
        titles.append({"id": series_id, "type": "series", "key": f"r:series{s}", "name": f"Series {s:05d}", "category": category,
                       "created_at": T0 + timedelta(minutes=s)})
        for n in range(1, SEASONS + 1):
            season_id = uid(20_000_000 + s * 10 + n)
            titles.append({"id": season_id, "type": "season", "parent_id": series_id, "key": f"r:series{s}#s{n}", "name": f"Season {n}",
                           "index_number": n, "category": category})
            for e in range(1, EPISODES + 1):
                episode_id, item_id = uid(30_000_000 + s * 100 + n * 10 + e), uid(40_000_000 + s * 100 + n * 10 + e)
                titles.append({"id": episode_id, "type": "episode", "parent_id": season_id, "key": f"r:series{s}#s{n}e{e}",
                               "name": f"Episode {e}", "index_number": e, "category": category})
                items.append({"id": item_id, "title": f"S{s} S{n}E{e}", "user_id": ALICE, "visibility": "shared", "title_id": episode_id,
                              "kind": "episode", "status": "available", "duration": 1500, "metadata_json": {},
                              "created_at": T0 + timedelta(minutes=s, seconds=n * 10 + e)})
                if s < 100 and n == 1 and e <= 4:
                    watched(item_id, s * 10 + e, position=0, completed=True)  # 400 rows: S1E1-E4 of 100 series
                elif 100 <= s < 200 and n == 1 and e == 1:
                    watched(item_id, s * 10, position=300, completed=False)  # 100 rows in progress
    for m in range(MOVIES):
        movie_id = uid(60_000_000 + m)
        titles.append({"id": movie_id, "type": "movie", "key": f"r:movie{m}", "name": f"Movie {m:05d}", "year": 1950 + m % 70,
                       "category": "movies", "created_at": T0 + timedelta(minutes=m)})
        items.append({"id": uid(70_000_000 + m), "title": f"Movie {m}", "user_id": ALICE, "visibility": "shared", "title_id": movie_id,
                      "kind": "movie", "status": "available", "duration": 6000, "metadata_json": {}, "created_at": T0 + timedelta(minutes=m)})
    with db_module.SessionLocal() as session:
        session.add(make_user(ALICE, username="alice"))
        device_token(session, ALICE, ALICE_TOKEN)
        session.execute(insert(MediaTitle), titles)
        session.execute(insert(LibraryItem), items)
        session.execute(insert(PlaybackProgress), progress)
        seed_app_settings(session, jellyfin_enabled=True)  # commits
    assert len(progress) == 500


@contextmanager
def _statements():
    seen: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        seen.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(db_module.engine, "before_cursor_execute", record)


def test_hot_lists_have_a_fixed_statement_budget() -> None:
    _seed()
    with db_module.SessionLocal() as session:
        alice = session.get(User, ALICE)
    app.dependency_overrides[get_current_user] = lambda: alice  # the Lumina grid authenticates by cookie in production
    client = TestClient(app, base_url="http://localhost")
    report: dict[str, list[int]] = {}
    try:
        for name, (path, params) in CASES.items():
            for limit in (20, 200):
                query = {**params, ("limit" if path.startswith("/api/") else "Limit"): limit}
                client.get(path, params=query, headers=mediabrowser(ALICE_TOKEN))  # warm-up: first-use writes happen once
                with _statements() as seen:
                    response = client.get(path, params=query, headers=mediabrowser(ALICE_TOKEN))
                assert response.status_code == 200, (name, response.text)
                if path == "/Items":
                    assert len(response.json()["Items"]) == limit, name
                report.setdefault(name, []).append(len(seen))
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        client.close()
    over = {name: counts for name, counts in report.items() if counts[0] != counts[1] or max(counts) > BUDGET}
    assert not over, f"[limit=20, limit=200] statement counts over {BUDGET} or size-dependent: {over} (all: {report})"
