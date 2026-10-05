"""Anonymous client metrics, daily histograms and the Media loading payload."""
from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import db as db_module
from app.config import settings
from app.db import Base, session_scope
from app.media_schemas import ClientMetricSample
from app.models import ClientMetricDay
from app.security import CSRF_HEADER, hash_password
from app.services import client_metrics
from support import make_user

PASSWORD = "Test-only-passphrase-1"
DAY = date(2026, 9, 28)
BATCH = {"samples": [{"metric": "ttff_ms", "label": "direct", "value": 1200}]}


@pytest.fixture(autouse=True)
def empty_store():  # noqa: ANN201
    client_metrics._pending.clear()
    yield
    client_metrics._pending.clear()


@pytest.fixture
def tables() -> None:
    Base.metadata.create_all(bind=db_module.engine)


def origin() -> dict[str, str]:
    return {"Origin": settings.allowed_origins_list[0]}


def sample(metric: str = "ttff_ms", label: str = "direct", value: float = 1000) -> ClientMetricSample:
    return ClientMetricSample(metric=metric, label=label, value=value)


def hist(counts: dict[int, int]) -> list[int]:
    values = [0] * 25
    for index, count in counts.items():
        values[index] = count
    return values


def test_edges_and_buckets_follow_the_contract() -> None:
    assert len(client_metrics.MS_EDGES) == 25
    assert client_metrics.MS_EDGES[:3] == (10, 14, 21) and client_metrics.MS_EDGES[-1] == 60_000
    assert [client_metrics.bucket("ttff_ms", value) for value in (0, 10, 11, 1500, 60_000, 3_600_000)] == [0, 0, 1, 14, 24, 24]
    assert [client_metrics.bucket("image_failed", value) for value in (0, 1, 7, 3_600_000)] == [0, 1, 7, 24]


def test_percentiles_are_the_upper_edge_of_the_rank_bucket() -> None:
    histogram = hist({13: 18, 16: 2})
    assert client_metrics.percentile("ttff_ms", histogram, 0.5) == 1113.0
    assert client_metrics.percentile("ttff_ms", histogram, 0.95) == 3302.0
    assert client_metrics.percentile("ttff_ms", [0] * 25, 0.5) is None


def test_flush_adds_into_the_day_row_and_drops_rows_older_than_30_days(tables: None) -> None:
    client_metrics.record([sample(value=1000), sample(value=1500)], day=DAY)
    client_metrics.flush(today=DAY)
    client_metrics.record([sample(value=5)], day=DAY)
    with session_scope() as db:
        db.add(ClientMetricDay(day=DAY - timedelta(days=31), metric="ttff_ms", label="direct", histogram=hist({0: 1}), count=1))
        db.add(ClientMetricDay(day=DAY - timedelta(days=30), metric="ttff_ms", label="remux", histogram=hist({0: 1}), count=1))
    client_metrics.flush(today=DAY)
    with session_scope() as db:
        rows = {(row.day, row.label): (row.count, list(row.histogram)) for row in db.scalars(select(ClientMetricDay))}
    assert set(rows) == {(DAY, "direct"), (DAY - timedelta(days=30), "remux")}
    assert rows[(DAY, "direct")] == (3, hist({0: 1, 13: 1, 14: 1}))
    assert client_metrics._pending == {}


def test_a_failed_flush_keeps_the_samples_for_the_next_one(tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    client_metrics.record([sample()], day=DAY)
    real, calls = client_metrics.session_scope, []

    def flaky():  # noqa: ANN202
        if not calls:
            calls.append(1)
            raise OperationalError("flush", {}, Exception("disk I/O error"))
        return real()

    monkeypatch.setattr(client_metrics, "session_scope", flaky)
    client_metrics.flush(today=DAY)
    assert sum(sum(histogram) for histogram in client_metrics._pending.values()) == 1
    client_metrics.flush(today=DAY)
    with session_scope() as db:
        assert db.scalar(select(ClientMetricDay.count)) == 1


def test_flush_due_waits_five_minutes(tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    client_metrics.record([sample()])
    monkeypatch.setattr(client_metrics, "_last_flush", time.monotonic())
    client_metrics.flush_due()
    assert client_metrics._pending
    monkeypatch.setattr(client_metrics, "_last_flush", time.monotonic() - client_metrics.FLUSH_SECONDS)
    client_metrics.flush_due()
    assert not client_metrics._pending


def test_report_merges_stored_and_pending_days_with_budgets_and_failure_rate(tables: None) -> None:
    with session_scope() as db:
        db.add(ClientMetricDay(day=DAY - timedelta(days=6), metric="ttff_ms", label="direct", histogram=hist({13: 9}), count=9))
        db.add(ClientMetricDay(day=DAY - timedelta(days=7), metric="ttff_ms", label="direct", histogram=hist({24: 50}), count=50))
        db.add(ClientMetricDay(day=DAY - timedelta(days=1), metric="image_load_ms", label="poster:net", histogram=hist({5: 3}), count=3))
    client_metrics.record([sample(value=3000), sample("image_failed", "poster:transient", 1)], day=DAY)
    with session_scope() as db:
        loading = client_metrics.report(db, today=DAY)
    by_key = {(summary.metric, summary.label): summary for summary in loading.metrics}
    assert [summary.metric for summary in loading.metrics] == ["image_load_ms", "ttff_ms", "image_failed"]
    ttff = by_key[("ttff_ms", "direct")]
    assert (ttff.today_count, ttff.today_p50, ttff.week_count) == (1, 3302.0, 10)
    assert (ttff.week_p50, ttff.week_p95, ttff.budget_p50, ttff.budget_p95) == (1113.0, 3302.0, 1500, 2000)
    assert ttff.within_budget is False
    assert by_key[("image_load_ms", "poster:net")].within_budget is True
    failed = by_key[("image_failed", "poster:transient")]
    assert (failed.budget_p50, failed.within_budget) == (None, None)
    assert loading.image_failure_rate_today == 1.0
    assert loading.image_failure_rate_week == 0.25


@pytest.fixture
def member(db_factory, api_client):  # noqa: ANN001, ANN201
    with db_factory.begin() as session:
        session.add(make_user("alice", password_hash=hash_password(PASSWORD)))
        session.add(make_user("root", role="admin", password_hash=hash_password(PASSWORD)))
    client = api_client(base_url="http://localhost")
    login = client.post("/api/session/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return client, login.json()["csrf_token"]


def test_a_member_posts_with_the_header_or_the_beacon_body_token(member) -> None:  # noqa: ANN001
    client, token = member
    assert client.post("/api/metrics/client", json=BATCH, headers={**origin(), CSRF_HEADER: token}).status_code == 204
    assert client.post("/api/metrics/client", json={**BATCH, "csrf": token}, headers=origin()).status_code == 204
    assert sum(sum(histogram) for histogram in client_metrics._pending.values()) == 2


def test_a_stale_or_missing_token_is_refused(member, api_client) -> None:  # noqa: ANN001
    client, _token = member
    assert api_client(base_url="http://localhost").post("/api/metrics/client", json=BATCH, headers=origin()).status_code == 401
    stale = client.post("/api/metrics/client", json={**BATCH, "csrf": "0" * 64}, headers=origin())
    assert stale.status_code == 403
    assert stale.json() == {"detail": "CSRF token missing or invalid."}
    assert client.post("/api/metrics/client", json=BATCH, headers=origin()).status_code == 403
    assert client.post("/api/metrics/client", json=BATCH, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client_metrics._pending == {}


def test_invalid_batches_are_refused(member) -> None:  # noqa: ANN001
    client, token = member
    headers = {**origin(), CSRF_HEADER: token}
    assert client.post("/api/metrics/client", json={"samples": [], "csrf": "x" * 40_000}, headers=headers).status_code == 413
    for batch in (
        {"samples": [{"metric": "page_views", "label": "movies", "value": 1}]},
        {"samples": [{"metric": "ttff_ms", "label": "sideways", "value": 1}]},
        {"samples": [{"metric": "ttff_ms", "label": "direct", "value": 4_000_000}]},
        {"samples": [{"metric": "ttff_ms", "label": "direct", "value": 1}] * 501},
    ):
        assert client.post("/api/metrics/client", json=batch, headers=headers).status_code == 422
    assert client_metrics._pending == {}


def test_more_than_twelve_posts_a_minute_are_refused(member) -> None:  # noqa: ANN001
    client, token = member
    headers = {**origin(), CSRF_HEADER: token}
    assert [client.post("/api/metrics/client", json=BATCH, headers=headers).status_code for _ in range(13)] == [204] * 12 + [429]


def test_diagnostics_carry_media_loading_for_admins(member, api_client) -> None:  # noqa: ANN001
    client, token = member
    client.post("/api/metrics/client", json=BATCH, headers={**origin(), CSRF_HEADER: token})
    admin = api_client(base_url="http://localhost")
    assert admin.post("/api/session/login", json={"username": "root", "password": PASSWORD}).status_code == 200
    loading = admin.get("/api/admin/diagnostics").json()["media_loading"]
    ttff = next(summary for summary in loading["metrics"] if (summary["metric"], summary["label"]) == ("ttff_ms", "direct"))
    assert (ttff["today_count"], ttff["budget_p50"], ttff["budget_p95"]) == (1, 1500, 2000)
    assert client.get("/api/admin/diagnostics").status_code == 403
