"""Admin-only redacted diagnostics. Members get only ``/api/runtime-health`` (status + version).

Every free-text field passes through ``redact(..., paths=True)``: no server paths, tokens, cookies or keys
leave here, so the report is safe to copy into an issue.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import cache
import platform
import shutil
import subprocess
from typing import Any, Callable

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import APP_VERSION, settings
from app.db import get_db
from app.events import EventBus
from app.media_schemas import ArtworkProgress, MediaLoading, RecoDiagnostics, TranscodeDiagnostics
from app.models import DownloadJob, ImportRun, StorageRoot, Summary
from app.persistence import persistence_metrics_snapshot
from app.services import client_metrics
from app.routers import admin_library_automation as automation_api, admin_media_server
from app.services.reco import enabled as reco_enabled, metrics as reco_metrics
from app.services.renditions import renditions
from app.security import get_admin_user, utcnow
from app.services import local_playback_sessions, model_endpoints
from app.services.hwaccel import hwaccel
from app.services.local_ai import check_connection, effective_config
from app.services.media_probe import media_tool
from app.services.playback_log import recent_failures as playback_failures
from app.services.redaction import redact
from app.services.yt_dlp_service import YtDlpService

RECENT_PER_SOURCE = 5
MESSAGE_CHARS = 300


class RecentError(BaseModel):
    source: str
    at: datetime | None
    message: str


class LibraryAutomationDiagnostics(BaseModel):
    poller: dict[str, Any]
    driver: dict[str, Any]
    runs_24h: dict[str, int]
    roots: list[dict[str, Any]]


class DiagnosticsResponse(BaseModel):
    generated_at: datetime
    status: str
    versions: dict[str, str | None]
    runtime: dict[str, bool]
    storage_roots: list[dict[str, Any]]
    queue: dict[str, Any]
    maintenance_sweeps: dict[str, Any]
    persistence: dict[str, dict[str, Any]]
    recent_errors: list[RecentError]
    ai: dict[str, Any]
    playback: TranscodeDiagnostics | None = None
    artwork: ArtworkProgress | None = None  # filled in by the owning feature
    media_loading: MediaLoading | None = None  # filled in by the owning feature
    recommendations: RecoDiagnostics | None = None  # filled in by the owning feature
    library_automation: LibraryAutomationDiagnostics | None = None  # filled in by the owning feature


@cache  # one subprocess per process, not per diagnostics load; restart picks up an upgraded ffmpeg
def ffmpeg_version() -> str | None:
    path = shutil.which("ffmpeg")
    if not path:
        return None
    try:
        first = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=2, check=False).stdout.split("\n", 1)[0]
    except (OSError, subprocess.SubprocessError):
        return None
    return first.split(" ")[2] if first.startswith("ffmpeg version ") else None


def health_status(sweeps: dict[str, Any]) -> str:
    return "degraded" if sweeps.get("consecutive_failures") or not shutil.which("ffmpeg") else "ok"


def _clean(text: str | None, known_paths: list[str]) -> str:
    text = text or ""
    for path in known_paths:  # exact known roots first, so paths with spaces vanish whole
        text = text.replace(path, "[path]")
    return redact(text, paths=True)[:MESSAGE_CHARS]


def _recent_errors(db: Session, known_paths: list[str]) -> list[RecentError]:
    sources = (
        ("download", db.query(DownloadJob.error, DownloadJob.finished_at).filter(DownloadJob.status == "failed", DownloadJob.error.isnot(None)).order_by(DownloadJob.finished_at.desc())),
        ("import", db.query(ImportRun.error, ImportRun.updated_at).filter(ImportRun.error.isnot(None)).order_by(ImportRun.updated_at.desc())),
        ("summary", db.query(Summary.error, Summary.completed_at).filter(Summary.state == "failed", Summary.error.isnot(None)).order_by(Summary.completed_at.desc())),
    )
    errors = [RecentError(source=source, at=at, message=_clean(error, known_paths)) for source, query in sources for error, at in query.limit(RECENT_PER_SOURCE).all()]
    errors += [RecentError(source="playback", at=at, message=_clean(line, known_paths)) for at, line in list(playback_failures)[:RECENT_PER_SOURCE]]
    return sorted(errors, key=lambda error: error.at or datetime.min, reverse=True)


def _ai(db: Session) -> dict[str, Any]:
    record = YtDlpService(db).get_app_settings()
    config = effective_config(record)
    asr_configured = model_endpoints.speech_choice(record) is not None
    if not config.enabled:
        return {"enabled": False, "asr_configured": asr_configured}
    result = check_connection(config)  # bounded probe; the key is sent, never returned
    return {"enabled": True, "ok": result["ok"], "model_available": result["model_available"], "error": result["error"], "asr_configured": asr_configured}


def _known_paths(db: Session) -> list[str]:
    roots = [path for path, in db.query(StorageRoot.path).all()]
    return sorted({str(settings.data_dir), *roots}, key=len, reverse=True)


def playback_diagnostics(db: Session, known_paths: list[str]) -> TranscodeDiagnostics:
    """Hardware mode and probe, tone-mapping, live sessions and recent ffmpeg errors."""
    record = YtDlpService(db).get_app_settings()
    mode = record.hwaccel or "auto"  # a fresh, uncommitted AppSettings leaves column defaults unset
    status = hwaccel.status(media_tool(db, "ffmpeg"), mode)
    hardware = status.active != "none" and not hwaccel.disabled
    return TranscodeDiagnostics(
        hwaccel=mode,
        active=status.active if hardware else "none",
        probe_ok=status.probe_ok,
        probe_error=_clean(status.probe_error, known_paths) or None,
        tonemap=status.tonemap if hardware else status.software_tonemap,
        hardware_disabled=hwaccel.disabled,
        software_fallbacks=hwaccel.fallbacks,
        cache_cap_bytes=(record.transcode_cache_gb or 10) * 1024**3,
        recent_errors=[_clean(error, known_paths) for error in local_playback_sessions.recent_errors],
        **local_playback_sessions.sessions.snapshot(),
    )


def _refresher_counters() -> dict[str, int]:
    """The recommender's 24 h provider counters. Read at request time: main.py builds the refresher after this module imports."""
    from app import main as main_module

    refresher = getattr(main_module, "reco_refresher", None)
    return dict(refresher.counters()) if refresher is not None else {}


def _dropped_events_24h(db: Session, now: datetime) -> int:
    try:
        from app.services.reco.events import RecoEventService
    except ImportError:
        return 0
    return RecoEventService(db).dropped_24h(now)


def recommendations(db: Session) -> RecoDiagnostics:
    """Household totals for the last 28 days. One range scan of reco_events plus pool health; no provider or embedding call."""
    now = utcnow()
    return reco_metrics.report(db, now=now, enabled=reco_enabled(db), refresher=_refresher_counters(), dropped_events_24h=_dropped_events_24h(db, now))


def library_automation(db: Session, known_paths: list[str]) -> LibraryAutomationDiagnostics | None:
    try:
        report = automation_api.automation_service().diagnostics()
    except (ImportError, NotImplementedError):  # the automation service is absent or still a stub
        return None
    since = utcnow() - timedelta(hours=24)
    counts = {"manual": 0, "scheduled": 0, "watch": 0, "needs_confirmation": 0, "failed": 0}
    for trigger, state in db.query(ImportRun.trigger, ImportRun.state).filter(ImportRun.created_at >= since).all():
        counts[trigger if trigger in ("manual", "scheduled", "watch") else "manual"] += 1
        if state in ("needs_confirmation", "failed"):
            counts[state] += 1
    # roots[] carries labels and enums only; any free-text field the automation service adds there must be cleaned at its source.
    poller = {**report["poller"], "last_error": _clean(report["poller"].get("last_error"), known_paths) or None}
    return LibraryAutomationDiagnostics(poller=poller, driver=report["driver"], runs_24h=counts, roots=report["roots"])


def build_diagnostics(db: Session, events: EventBus, sweeps: dict[str, Any]) -> DiagnosticsResponse:
    roots = db.query(StorageRoot).order_by(StorageRoot.created_at.asc()).all()
    known_paths = _known_paths(db)
    runtime = YtDlpService.runtime_diagnostics()
    limits = YtDlpService(db).get_app_settings()
    automation = library_automation(db, known_paths)
    status = health_status(sweeps)
    if status == "ok" and automation and automation.poller.get("stalled"):
        status = "degraded"
    return DiagnosticsResponse(
        generated_at=utcnow(),
        status=status,
        versions={
            "lumina": APP_VERSION,
            "python": platform.python_version(),
            "yt_dlp": runtime["yt_dlp_version"],
            "ffmpeg": ffmpeg_version(),
            "node": runtime["node_version"],
        },
        runtime={
            "ffmpeg_available": bool(shutil.which("ffmpeg")),
            "js_runtime_available": runtime["js_runtime_available"],
            "yt_dlp_ejs_available": runtime["yt_dlp_ejs_available"],
        },
        storage_roots=[
            {"label": root.label, "mode": root.mode, "enabled": bool(root.enabled), "state": (root.observation or {}).get("state"), "checked_at": (root.observation or {}).get("checked_at")}
            for root in roots
        ],
        queue={
            "jobs_by_status": dict(db.query(DownloadJob.status, func.count()).group_by(DownloadJob.status).all()),
            "concurrency": limits.concurrency,
            "event_streams": events.stream_count(),
        },
        maintenance_sweeps={**sweeps, "last_error": _clean(sweeps.get("last_error"), known_paths) or None},
        persistence=persistence_metrics_snapshot(),
        recent_errors=_recent_errors(db, known_paths),
        artwork=renditions.progress(),
        ai=_ai(db),
        playback=playback_diagnostics(db, known_paths),
        media_loading=client_metrics.report(db),
        recommendations=recommendations(db),
        library_automation=automation,
    )


def register(app: FastAPI, events: EventBus, sweeps: Callable[[], dict[str, Any]]) -> None:
    @app.get("/api/admin/diagnostics", response_model=DiagnosticsResponse, dependencies=[Depends(get_admin_user)])
    def admin_diagnostics(db: Session = Depends(get_db, scope="function")) -> DiagnosticsResponse:
        return build_diagnostics(db, events, sweeps())  # sync route: the blocking probes run in the threadpool

    @app.post(f"{admin_media_server.URL}/transcode-diagnostics", response_model=TranscodeDiagnostics, dependencies=[Depends(get_admin_user)])
    def run_transcode_diagnostics(db: Session = Depends(get_db, scope="function")) -> TranscodeDiagnostics:
        hwaccel.reprobe()  # e.g. after fixing /dev/dri permissions; a disabled fallback stays off until restart
        return playback_diagnostics(db, _known_paths(db))
