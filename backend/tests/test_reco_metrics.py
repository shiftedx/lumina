"""Diagnostics' recommendations section: household totals, never a member, blank under 50."""
from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta

import pytest

from app import main as main_module
from sqlalchemy import select
from app.models import User, PlaybackProgress, RecoEvent, RecoPool, RemoteMedia, RemotePlaybackProgress
from app.routers import admin_diagnostics
from app.security import hash_password, utcnow
from app.services.reco import FEATURE_KEY, metrics
from app.services.yt_dlp_service import YtDlpService
from test_v1_diagnostics import fixed_hardware, offline_ai  # noqa: F401  (autouse fixtures)
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)
from discovery_support import add_movie, add_series
from support import make_user, memory_session_factory

NOW = datetime(2026, 10, 1, 12)
LIST = "a" * 16


def key(n: int) -> str:
    return hashlib.sha256(f"youtube:perf-{n}".encode()).hexdigest()


@pytest.fixture
def db():  # noqa: ANN201
    with memory_session_factory()() as session:
        yield session


def add(db, kind: str, count: int = 1, *, first: int = 0, user: str = "u1", surface: str | None = "home_picked", list_id: str | None = LIST,
        slot: str = "exploit", target: str = "remote", at: datetime = NOW - timedelta(days=1)) -> None:  # noqa: ANN001
    db.add_all(RecoEvent(user_id=user, at=at, kind=kind, surface=surface, list_id=list_id, position=0, slot=slot, target_kind=target,
                         item_key=key(first + n)) for n in range(count))
    db.flush()


def run(db, *, window_days: int = 28):  # noqa: ANN001, ANN201
    return metrics.report(db, now=NOW, enabled=True, refresher={}, dropped_events_24h=0, window_days=window_days)


def surface(db, name: str = "home_picked", **kwargs):  # noqa: ANN001, ANN201
    return next(row for row in run(db, **kwargs).surfaces if row.surface == name)


def test_every_surface_is_listed_in_the_contract_order_even_when_empty(db) -> None:  # noqa: ANN001
    assert [row.surface for row in run(db).surfaces] == [
        "home_picked", "home_recommended", "home_because", "explore_for_you", "explore_popular", "up_next", "title_similar",
    ]
    assert surface(db).impressions == 0 and surface(db).ctr is None


def test_opens_are_distinct_per_member_list_and_key(db) -> None:  # noqa: ANN001
    add(db, "impression", 60)
    add(db, "open", 6)
    add(db, "open", 6)  # the same six again on the same list: still six
    add(db, "open", 6, list_id="b" * 16)  # the same keys on another list: six more
    add(db, "open", 6, user="u2")  # another member opening the same keys: six more
    stats = surface(db)
    assert (stats.impressions, stats.opens, stats.ctr) == (60, 18, 0.3)


def test_every_rate_is_blank_under_fifty_but_counts_stay(db) -> None:  # noqa: ANN001
    add(db, "impression", 49)
    add(db, "open", 7)
    add(db, "play", 7)
    add(db, "complete", 3)
    add(db, "not_interested", 2)
    stats = surface(db)
    assert (stats.impressions, stats.opens, stats.plays) == (49, 7, 7)
    assert (stats.ctr, stats.negative_rate, stats.completion_rate, stats.play_through_median, stats.explore_play_rate, stats.exploit_play_rate) == (None,) * 6
    add(db, "impression", 1, first=100)  # exactly 50
    stats = surface(db)
    assert (stats.ctr, stats.negative_rate, stats.exploit_play_rate) == (0.14, 0.04, 0.14)
    assert stats.explore_play_rate is None and stats.completion_rate is None  # 0 explore impressions; 7 plays


def test_plays_need_a_list_and_completion_is_over_attributed_plays(db) -> None:  # noqa: ANN001
    add(db, "play", 60)
    add(db, "play", 40, list_id=None, surface=None, first=500)  # not attributed: never P
    add(db, "complete", 30)
    add(db, "complete", 10, list_id=None, surface=None, first=500)
    stats = surface(db)
    assert stats.plays == 60 and stats.completion_rate == 0.5


def test_negative_feedback_over_impressions_counts_all_three_kinds_on_that_surface_only(db) -> None:  # noqa: ANN001
    add(db, "impression", 100)
    add(db, "not_interested")
    add(db, "fewer")
    add(db, "hide_channel")
    add(db, "not_interested", 5, surface="up_next")  # another surface
    assert surface(db).negative_rate == 0.03


def test_explore_and_exploit_play_rates_are_per_slot(db) -> None:  # noqa: ANN001
    add(db, "impression", 100, slot="explore")
    add(db, "impression", 200, slot="exploit", first=1000)
    add(db, "play", 10, slot="explore")
    add(db, "play", 40, slot="exploit", first=1000)
    stats = surface(db)
    assert (stats.explore_play_rate, stats.exploit_play_rate) == (0.1, 0.2)


def test_reco_share_counts_only_remote_plays(db) -> None:  # noqa: ANN001
    add(db, "play", 30)  # remote, attributed
    add(db, "play", 30, list_id=None, surface=None, first=100)  # remote, not attributed
    add(db, "play", 80, target="title", first=200)  # title plays never count, attributed or not
    assert run(db).reco_share_of_remote_plays == 0.5


def test_reco_share_is_blank_under_fifty_remote_plays(db) -> None:  # noqa: ANN001
    add(db, "play", 49)
    assert run(db).reco_share_of_remote_plays is None


def test_the_window_drops_older_events(db) -> None:  # noqa: ANN001
    add(db, "impression", 3, at=NOW - timedelta(days=27, hours=23))
    add(db, "impression", 4, first=10, at=NOW - timedelta(days=28, seconds=1))
    assert surface(db).impressions == 3
    assert surface(db, window_days=29).impressions == 7


def test_no_member_id_reaches_the_report(db) -> None:  # noqa: ANN001
    add(db, "impression", 60, user="member-secret-id")
    add(db, "play", 60, user="member-secret-id")
    assert "member-secret-id" not in run(db).model_dump_json()


def test_play_through_is_the_current_max_fraction_behind_the_plays(db) -> None:  # noqa: ANN001
    for n in range(50):
        db.add(RemotePlaybackProgress(
            id=str(uuid.uuid4()), user_id="u1", source_identity=f"youtube:perf-{n}", source_identity_key=key(n), extractor="youtube",
            remote_id=f"perf-{n}", source_url=f"https://www.youtube.com/watch?v=perf-{n}", title="t", uploader="Chan", position_seconds=10.0,
            duration_seconds=100.0, completed=False, last_watched_at=NOW, created_at=NOW, max_fraction=0.3 if n < 25 else 0.9,
        ))
    add(db, "play", 50)
    assert surface(db).play_through_median == pytest.approx(0.6)  # median of 25 x 0.3 and 25 x 0.9


def test_play_through_for_a_title_is_the_best_of_its_movie_or_episodes(db) -> None:  # noqa: ANN001
    add_movie(db, "movie", "A film")
    add_series(db, "show", "A show", seasons={1: 2})
    for item, fraction in (("movie-v", 0.4), ("show-s1e1-v", 0.2), ("show-s1e2-v", 0.8)):
        db.add(PlaybackProgress(id=str(uuid.uuid4()), user_id="u1", item_id=item, position_seconds=1, duration_seconds=10,
                                completed=False, last_watched_at=NOW, max_fraction=fraction))
    db.flush()
    found = metrics._play_through(db, {("u1", "title", "movie"), ("u1", "title", "show"), ("u2", "title", "movie")})
    assert found == {("u1", "title", "movie"): pytest.approx(0.4), ("u1", "title", "show"): pytest.approx(0.8)}


def _media(n: int, *, vector: bool) -> RemoteMedia:
    return RemoteMedia(
        key=key(n), source_identity=f"youtube:perf-{n}", extractor="youtube", remote_id=f"perf-{n}", webpage_url=f"https://www.youtube.com/watch?v=perf-{n}",
        title=f"Video {n}", uploader="Chan", published_at=NOW, category_keys=[], tokens=[], vector=b"\x00" * 8 if vector else None,
        fetched_at=NOW, last_nominated_at=NOW,
    )


def _pooled(user: str, n: int, nominated: datetime) -> RecoPool:
    return RecoPool(user_id=user, item_key=key(n), sources=1, first_seen_at=NOW - timedelta(days=2), last_nominated_at=nominated)


def test_pool_health_summarises_pools_without_naming_a_member(db) -> None:  # noqa: ANN001
    db.add_all([make_user("a"), make_user("b"), make_user("gone", is_active=False)])
    db.add_all(_media(n, vector=n % 2 == 0) for n in range(4))
    db.add_all([
        _pooled("a", 0, NOW - timedelta(minutes=30)), _pooled("a", 1, NOW - timedelta(minutes=60)), _pooled("a", 2, NOW - timedelta(minutes=60)),
        _pooled("b", 3, NOW - timedelta(hours=3)),
        _pooled("gone", 0, NOW - timedelta(days=9)),  # a deactivated member's pool counts nowhere but the item set
    ])
    db.flush()
    pool = metrics.report(db, now=NOW, enabled=True, refresher={"provider_calls_24h": 12, "budget_hits_24h": 2}, dropped_events_24h=5).pool
    assert pool.members_with_pool == 2 and pool.median_pool_size == 2
    assert pool.oldest_refresh_age_minutes == 180  # b's newest nomination is 3 h old; a's is 30 min
    assert pool.remote_vector_coverage == 0.5  # items 0 and 2 have a vector, 1 and 3 do not
    assert (pool.provider_calls_24h, pool.budget_hits_24h, pool.dropped_events_24h) == (12, 2, 5)


def test_pool_health_on_an_empty_database(db) -> None:  # noqa: ANN001
    pool = run(db).pool
    assert (pool.members_with_pool, pool.median_pool_size, pool.oldest_refresh_age_minutes, pool.remote_vector_coverage) == (0, 0, None, None)
    assert (pool.provider_calls_24h, pool.budget_hits_24h) == (0, 0)


def test_the_report_carries_the_switch_and_the_window(db) -> None:  # noqa: ANN001
    off = metrics.report(db, now=NOW, enabled=False, refresher={}, dropped_events_24h=0, window_days=14)
    assert (off.enabled, off.window_days) == (False, 14)


def test_every_event_statement_of_the_section_is_answered_from_the_covering_index(db) -> None:  # noqa: ANN001
    from sqlalchemy import event

    for kind in ("impression", "open", "play", "complete"):
        add(db, kind, 40)
    statements: list[tuple[str, object]] = []

    def capture(_conn, _cursor, statement, parameters, _context, _many) -> None:  # noqa: ANN001
        if "FROM reco_events" in statement:
            statements.append((statement, parameters))

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        run(db)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 3
    for statement, parameters in statements:
        plan = "\n".join(str(row[-1]) for row in db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))
        assert "USING COVERING INDEX ix_reco_events_kind_surface" in plan and "SCAN reco_events" not in plan, plan


def test_the_refresher_counters_are_read_at_request_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "reco_refresher", SimpleNamespace(counters=lambda: {"provider_calls_24h": 3}), raising=False)
    assert admin_diagnostics._refresher_counters() == {"provider_calls_24h": 3}
    monkeypatch.delattr(main_module, "reco_refresher")
    assert admin_diagnostics._refresher_counters() == {}


def test_diagnostics_carries_the_recommendations_section_and_no_member(client, factory, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(admin_diagnostics, "_refresher_counters", lambda: {"provider_calls_24h": 7, "budget_hits_24h": 1})
    with factory.begin() as db:
        db.add_all(RecoEvent(user_id="u1", at=utcnow(), kind="impression", surface="home_picked", list_id=LIST, position=n, slot="exploit",
                             target_kind="remote", item_key=key(n)) for n in range(3))
    login(client)
    response = client.get("/api/admin/diagnostics")
    assert response.status_code == 200, response.text
    section = response.json()["recommendations"]
    assert section["enabled"] is True and section["window_days"] == 28
    assert [row["surface"] for row in section["surfaces"]] == list(metrics.SURFACES)
    assert section["surfaces"][0]["impressions"] == 3 and section["surfaces"][0]["ctr"] is None  # three impressions: the rate stays blank
    assert (section["pool"]["provider_calls_24h"], section["pool"]["budget_hits_24h"]) == (7, 1)
    assert '"u1"' not in json.dumps(section)


def test_the_switch_reads_off_in_the_section(client, factory) -> None:  # noqa: ANN001
    with factory.begin() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_features_disabled = [FEATURE_KEY]
    login(client)
    assert client.get("/api/admin/diagnostics").json()["recommendations"]["enabled"] is False


def test_a_member_still_cannot_read_diagnostics(client, factory) -> None:  # noqa: ANN001
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    login(client, "member")
    assert client.get("/api/admin/diagnostics").status_code == 403
