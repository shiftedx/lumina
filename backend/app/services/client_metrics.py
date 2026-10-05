"""Anonymous real-user loading metrics.

A sample is (metric, fixed label, number): nothing names a member, title, URL or path. Samples are bucketed
into fixed 25-bucket histograms per UTC day in memory, added into ``client_metric_days`` every 5 minutes by
the maintenance cycle, and rows older than 30 days are deleted. Diagnostics reads today and the last 7 days.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import session_scope
from app.media_schemas import IMAGE_KINDS, ClientMetricSample, MediaLoading, MetricSummary
from app.models import ClientMetricDay
from app.persistence import write_transaction
from app.services.rate_limit import RATE_LIMIT_RULES, RateLimitRule

logger = logging.getLogger(__name__)

BUCKETS = 25
MS_EDGES = tuple(round(10 * 6000 ** (index / 24)) for index in range(BUCKETS))  # upper edges, 10 ms … 60 s
COUNT_EDGES = (0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500, 1000, 3_600_000)
FLUSH_SECONDS = 300
KEEP_DAYS = 30
WEEK_DAYS = 7
RATE_BUCKET = "client_metrics"
# Registered here because rate_limit.py has no gallery owner ; move it there when it does.
RATE_LIMIT_RULES.setdefault(RATE_BUCKET, RateLimitRule(max_requests=12, window_seconds=60))

METRIC_ORDER = (
    "wall_first_screen_ms", "wall_sharp_ms", "detail_hero_ms", "home_first_screen_ms", "home_hero_ms",
    "image_load_ms", "ttff_ms", "long_tasks", "image_failed",
)
# (p50, p95): p50 is the warm reference-host budget, p95 the cold one.
BUDGETS: dict[tuple[str, str], tuple[float, float]] = {
    **{("wall_first_screen_ms", lens): (600, 1000) for lens in ("movies", "shows", "all", "anime", "albums", "artists")},
    **{("wall_first_screen_ms", lens): (800, 1500) for lens in ("youtube", "recordings")},
    **{("wall_sharp_ms", lens): (800, 2000) for lens in ("movies", "shows", "anime", "albums", "artists")},
    ("detail_hero_ms", "click"): (500, 1200),
    ("detail_hero_ms", "deep_link"): (800, 1800),
    ("home_first_screen_ms", "home"): (600, 1000),
    ("home_hero_ms", "home"): (600, 1500),
    **{("image_load_ms", f"{kind}:hit"): (10, 40) for kind in IMAGE_KINDS},
    **{("image_load_ms", f"{kind}:net"): (80, 300) for kind in IMAGE_KINDS},
    ("ttff_ms", "direct"): (1500, 2000),
    ("ttff_ms", "direct_resume"): (2000, 2500),
    ("ttff_ms", "transcode_hw"): (4000, 5000),
    ("ttff_ms", "transcode_sw"): (6000, 7000),
    ("ttff_ms", "remux"): (3000, 4000),
    ("long_tasks", "wall_scroll"): (0, 2),
}

_lock = threading.Lock()
_pending: dict[tuple[date, str, str], list[int]] = {}
_last_flush = time.monotonic()


def _today() -> date:
    return datetime.now(UTC).date()


def edges(metric: str) -> tuple[int, ...]:
    return MS_EDGES if metric.endswith("_ms") else COUNT_EDGES


def bucket(metric: str, value: float) -> int:
    """The first bucket whose upper edge is >= value, else the last."""
    return next((index for index, edge in enumerate(edges(metric)) if value <= edge), BUCKETS - 1)


def percentile(metric: str, histogram: list[int], fraction: float) -> float | None:
    """The upper edge of the bucket holding rank ceil(fraction * count); None without samples."""
    total = sum(histogram)
    if not total:
        return None
    rank, running = max(1, math.ceil(total * fraction)), 0
    for index, count in enumerate(histogram):
        running += count
        if running >= rank:
            return float(edges(metric)[index])
    return float(edges(metric)[-1])


def _merge(target: dict, key: tuple, histogram: list[int]) -> None:
    current = target.setdefault(key, [0] * BUCKETS)
    for index, count in enumerate(histogram):
        current[index] += count


def record(samples: Iterable[ClientMetricSample], *, day: date | None = None) -> None:
    day = day or _today()
    with _lock:
        for sample in samples:
            histogram = _pending.setdefault((day, sample.metric, sample.label), [0] * BUCKETS)
            histogram[bucket(sample.metric, sample.value)] += 1


def flush(*, today: date | None = None) -> None:
    """Add pending histograms into client_metric_days and delete rows older than 30 days.

    A database error puts the samples back, so the next flush retries them.
    """
    global _last_flush
    with _lock:
        pending = dict(_pending)
        _pending.clear()
        _last_flush = time.monotonic()
    today = today or _today()
    try:
        with session_scope() as db, write_transaction(db, name="client_metrics_flush"):
            for (day, metric, label), histogram in pending.items():
                row = db.get(ClientMetricDay, (day, metric, label))
                if row is None:
                    db.add(ClientMetricDay(day=day, metric=metric, label=label, histogram=histogram, count=sum(histogram)))
                else:
                    row.histogram = [old + new for old, new in zip(row.histogram, histogram)]  # a new list, so the JSON change is saved
                    row.count += sum(histogram)
            db.execute(delete(ClientMetricDay).where(ClientMetricDay.day < today - timedelta(days=KEEP_DAYS)))
    except OperationalError as exc:
        with _lock:
            for key, histogram in pending.items():
                _merge(_pending, key, histogram)
        logger.warning("Client metrics flush failed; keeping the samples for the next one: %s", type(exc).__name__)


def flush_due() -> None:
    """Every maintenance cycle calls this; it flushes at most every 5 minutes."""
    if time.monotonic() - _last_flush >= FLUSH_SECONDS:
        flush()


def _within(metric: str, value: float | None, budget: float) -> bool:
    """Within budget when the percentile's bucket is the budget's own bucket or below (buckets are coarse)."""
    return value is not None and bucket(metric, value) <= bucket(metric, budget)


def _summary(metric: str, label: str, today: list[int], week: list[int]) -> MetricSummary:
    budget = BUDGETS.get((metric, label))
    week_p50, week_p95 = percentile(metric, week, 0.5), percentile(metric, week, 0.95)
    within = None if budget is None or week_p50 is None else _within(metric, week_p50, budget[0]) and _within(metric, week_p95, budget[1])
    return MetricSummary(
        metric=metric, label=label,
        today_count=sum(today), today_p50=percentile(metric, today, 0.5), today_p95=percentile(metric, today, 0.95),
        week_count=sum(week), week_p50=week_p50, week_p95=week_p95,
        budget_p50=budget[0] if budget else None, budget_p95=budget[1] if budget else None, within_budget=within,
    )


def _failure_rate(histograms: dict[tuple[str, str], list[int]]) -> float | None:
    failed = sum(sum(histogram) for (metric, _label), histogram in histograms.items() if metric == "image_failed")
    loaded = sum(sum(histogram) for (metric, _label), histogram in histograms.items() if metric == "image_load_ms")
    return failed / (failed + loaded) if failed + loaded else None


def report(db: Session, *, today: date | None = None) -> MediaLoading:
    """Today and the last 7 UTC days (stored rows plus what is still in memory), with budgets and the image failure rate."""
    today = today or _today()
    since = today - timedelta(days=WEEK_DAYS - 1)
    rows = [(row.day, row.metric, row.label, list(row.histogram)) for row in db.scalars(select(ClientMetricDay).where(ClientMetricDay.day >= since))]
    with _lock:
        rows += [(day, metric, label, list(histogram)) for (day, metric, label), histogram in _pending.items() if day >= since]
    week: dict[tuple[str, str], list[int]] = {}
    day_totals: dict[tuple[str, str], list[int]] = {}
    for day, metric, label, histogram in rows:
        _merge(week, (metric, label), histogram)
        if day == today:
            _merge(day_totals, (metric, label), histogram)
    ordered = sorted(week, key=lambda key: (METRIC_ORDER.index(key[0]), key[1]))
    return MediaLoading(
        metrics=[_summary(metric, label, day_totals.get((metric, label), [0] * BUCKETS), week[(metric, label)]) for metric, label in ordered],
        image_failure_rate_today=_failure_rate(day_totals),
        image_failure_rate_week=_failure_rate(week),
    )
