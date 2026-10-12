"""Server budgets, p95 of 20 warm requests after 2 warm-ups.

    make perf-titles

Skipped unless PERF_TITLES_ROOT is set, so make check collects and skips it. It seeds its own database (5,000 saved
YouTube videos over 400 channels, 50 follows, a 96-item live snapshot) in the test's temp root; the seeded title data
under PERF_TITLES_ROOT is not needed. This machine is faster than the reference host: passing here is a floor.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

import app.main as main
from app.db import Base, SessionLocal, get_db  # conftest rebinds SessionLocal to this test's root
from app.models import LibraryItem, PlaybackProgress, SourceAutomation
from app.schemas import YouTubeSearchResult
from app.services.popular_discovery import PopularCategorySnapshot, PopularItem, PopularSnapshot
from app.services.rate_limit import RATE_LIMIT_RULES, RateLimitRule
from app.services.remote_annotation import annotate_remote_entries
from app.services.youtube_channels import channel_pages
from support import make_user

pytestmark = pytest.mark.skipif(not os.environ.get("PERF_TITLES_ROOT"), reason="set PERF_TITLES_ROOT (make perf-titles)")
WARMUP, SAMPLES = 2, 20
CID = "UCabcdefghijklmnopqrstuv"
MEMBER = make_user("alice")


def p95_ms(client, path: str) -> float:  # noqa: ANN001
    for _ in range(WARMUP):
        assert client.get(path).status_code == 200, path
    samples = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        response = client.get(path)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200, (path, response.text[:200])
    return sorted(samples)[int(SAMPLES * 0.95) - 1]


@pytest.fixture
def seeded():  # noqa: ANN201 - per test: conftest gives every test its own temp root and engine
    from app import db as db_module

    Base.metadata.create_all(bind=db_module.engine)
    base = datetime(2026, 1, 1)
    with SessionLocal() as session:
        for index in range(5000):
            channel = index % 400
            session.add(LibraryItem(
                id=f"yt-{index:05d}", user_id=MEMBER.id, visibility="shared", extractor="youtube", remote_id=f"r{index:010d}",
                title=f"Video {index}", uploader=f"Channel {channel:03d}", kind="video", created_at=base + timedelta(minutes=index),
                metadata_json={"channel_id": f"UC{channel:022d}"},
            ))
            if index % 3 == 0:
                session.add(PlaybackProgress(id=f"p-{index}", user_id=MEMBER.id, item_id=f"yt-{index:05d}", position_seconds=600, duration_seconds=600, completed=index % 2 == 0))
        for index in range(50):
            session.add(SourceAutomation(
                id=f"f-{index}", user_id=MEMBER.id, label=f"Channel {index:03d}", source_url=f"https://www.youtube.com/channel/UC{index:022d}",
                source_type="channel", artwork_url=None, cron_expression="0 */6 * * *", active=True, auto_download=False, format_selection={},
                output_profile={}, rules={}, duplicate_policy="skip_same_source", last_run_summary={}, feed_entries=[], created_at=base, updated_at=base,
            ))
        session.commit()
    return MEMBER


def test_library_channels_on_5000_videos_and_400_channels(seeded, api_client) -> None:  # noqa: ANN001
    main.app.dependency_overrides.pop(get_db, None)  # the real session factory over the seeded root
    client = api_client(user=seeded, base_url="http://localhost")
    assert len(client.get("/api/library/channels").json()) == 400
    assert p95_ms(client, "/api/library/channels") <= 150


def test_annotation_of_120_entries_is_two_queries_within_10_ms(seeded) -> None:  # noqa: ANN001
    entries = [YouTubeSearchResult(id=f"r{index * 37:010d}", source="youtube") for index in range(120)]
    with SessionLocal() as session:
        annotate_remote_entries(session, seeded, entries)  # warm
        statements: list[str] = []
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(session.get_bind(), "before_cursor_execute", listener)
        samples = []
        for _ in range(SAMPLES):
            started = time.perf_counter()
            annotate_remote_entries(session, seeded, entries)
            samples.append((time.perf_counter() - started) * 1000)
        event.remove(session.get_bind(), "before_cursor_execute", listener)
    assert len(statements) == 2 * SAMPLES
    assert sorted(samples)[int(SAMPLES * 0.95) - 1] <= 10


def test_live_with_50_follows_and_a_96_item_snapshot(seeded, api_client, monkeypatch) -> None:  # noqa: ANN001
    now = datetime.now(UTC)
    items = tuple(PopularItem(
        id=f"live{index:07d}", title=f"Live {index}", uploader="Someone", duration=None, thumbnail=None, artwork_url=None,
        webpage_url=f"https://www.youtube.com/watch?v=live{index:07d}", view_count=1000 - index, availability=None, published_at=None,
        source="youtube", source_label="YouTube", capabilities=None, category_keys=("gaming",),
    ) for index in range(96))
    snapshot = PopularSnapshot(items=items, categories=(PopularCategorySnapshot(key="gaming", label="Gaming", state="ready", last_success_at=now, next_refresh_at=now),),
                               state="ready", refreshing=False, stale=False, last_success_at=now, refreshed_at=now, next_refresh_at=now, error=None)
    monkeypatch.setattr(main, "live_discovery", type("L", (), {"get_snapshot": lambda self: snapshot})())

    class Warm:
        def live_entries(self, urls):  # noqa: ANN001, ANN202
            return [YouTubeSearchResult(id=f"h{n}", webpage_url=f"https://www.youtube.com/watch?v=hero{n:07d}") for n in range(3)]

        def unavailable_sources(self, urls):  # noqa: ANN001, ANN202
            return {}

    monkeypatch.setattr(main, "followed_live_checker", Warm())
    main.app.dependency_overrides.pop(get_db, None)
    assert p95_ms(api_client(user=seeded, base_url="http://localhost"), "/api/discovery/live") <= 150


def test_a_channel_page_cache_hit(seeded, api_client, monkeypatch) -> None:  # noqa: ANN001
    listing = {"entries": [{"id": f"v{n:010d}", "title": f"Film {n}", "url": f"https://www.youtube.com/watch?v=v{n:010d}"} for n in range(61)]}
    pages = channel_pages(lambda url, limit: {"channel_id": CID, "channel": "Harbor", "entries": []} if url.endswith(CID) else listing)
    monkeypatch.setattr(main, "channel_pages", pages)
    monkeypatch.setitem(RATE_LIMIT_RULES, "preview", RateLimitRule(max_requests=10_000, window_seconds=60))  # the route is metered like preview; 22 timed hits would trip it
    main.app.dependency_overrides.pop(get_db, None)
    try:
        assert p95_ms(api_client(user=seeded, base_url="http://localhost"), f"/api/channels/youtube/{CID}") <= 120
    finally:
        pages.close()
