"""Admin overview: truthful aggregates from real services, a fixed handful of GROUP BY queries.

Unknown sensors are ``None`` (unavailable), never 0. No titles, notes or per-member history leave here.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.events import EventBus
from app.models import DownloadJob, LibraryItem, MediaArtifact, StorageRoot
from app.persistence import persistence_metrics_snapshot
from app.security import get_admin_user, utcnow
from app.services.job_manager import _free_bytes  # the same measurement admission uses
from app.services.yt_dlp_service import YtDlpService

FAILURE_WINDOW = timedelta(days=7)
FAILURE_REASONS = 5
REASON_CHARS = 240


class FailureReason(BaseModel):
    reason: str
    count: int
    last_at: datetime | None


class RootSummary(BaseModel):
    id: str
    label: str
    mode: str
    enabled: bool
    state: str | None
    checked_at: str | None
    free_bytes: int | None
    total_bytes: int | None
    minimum_free_bytes: int
    artifact_count: int
    artifact_bytes: int


class AdminOverviewResponse(BaseModel):
    generated_at: datetime
    jobs_by_status: dict[str, int]
    recent_failures: list[FailureReason]
    failure_window_days: int
    concurrency: int
    max_active_jobs_per_user: int
    min_free_disk_mb: int
    library_free_bytes: int | None
    library_items_by_status: dict[str, int]
    roots: list[RootSummary]
    event_streams: int
    persistence: dict[str, dict]


def build_overview(db: Session, events: EventBus) -> AdminOverviewResponse:
    now = utcnow()
    jobs = dict(db.query(DownloadJob.status, func.count()).group_by(DownloadJob.status).all())
    reason = func.substr(DownloadJob.error, 1, REASON_CHARS)
    failures = (
        db.query(reason, func.count(), func.max(DownloadJob.finished_at))
        .filter(DownloadJob.status == "failed", DownloadJob.error.isnot(None), DownloadJob.finished_at >= now - FAILURE_WINDOW)
        .group_by(reason)
        .order_by(func.max(DownloadJob.finished_at).desc())
        .limit(FAILURE_REASONS)
        .all()
    )
    items = dict(db.query(LibraryItem.status, func.count()).group_by(LibraryItem.status).all())
    # One row per physical file, so items sharing an artifact are never double-counted.
    artifacts = {
        root_id: (count, int(size or 0))
        for root_id, count, size in db.query(MediaArtifact.root_id, func.count(), func.sum(MediaArtifact.size)).group_by(MediaArtifact.root_id).all()
    }
    roots = []
    for root in db.query(StorageRoot).order_by(StorageRoot.created_at.asc()).all():
        seen = root.observation or {}  # last probe, with its own checked_at: freshness is explicit
        count, size = artifacts.get(root.id, (0, 0))
        roots.append(RootSummary(
            id=root.id, label=root.label, mode=root.mode, enabled=bool(root.enabled), state=seen.get("state"),
            checked_at=seen.get("checked_at"), free_bytes=seen.get("free_bytes"), total_bytes=seen.get("total_bytes"),
            minimum_free_bytes=root.minimum_free_bytes or 0, artifact_count=count, artifact_bytes=size,
        ))
    limits = YtDlpService(db).get_app_settings()
    return AdminOverviewResponse(
        generated_at=now,
        jobs_by_status=jobs,
        recent_failures=[FailureReason(reason=text, count=count, last_at=last) for text, count, last in failures],
        failure_window_days=FAILURE_WINDOW.days,
        concurrency=limits.concurrency,
        max_active_jobs_per_user=limits.max_active_jobs_per_user,
        min_free_disk_mb=limits.min_free_disk_mb,
        library_free_bytes=_free_bytes(settings.library_root),
        library_items_by_status=items,
        roots=roots,
        event_streams=events.stream_count(),
        persistence=persistence_metrics_snapshot(),
    )


def register(app: FastAPI, events: EventBus) -> None:
    @app.get("/api/admin/overview", response_model=AdminOverviewResponse, dependencies=[Depends(get_admin_user)])
    def admin_overview(db: Session = Depends(get_db, scope="function")) -> AdminOverviewResponse:
        return build_overview(db, events)
