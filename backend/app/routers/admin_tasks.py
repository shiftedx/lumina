"""Admin task view: one bounded, keyset-paged list per kind of background work.

Commands route to the owning managers (JobManager, local ASR, summaries); nothing here
deletes outputs. Other members' private titles never leave: a download's title and
source are shown only to its owner, an enrichment job's title only if the admin can
view the library item.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AsrJob, DownloadJob, ImportRun, LibraryItem, StorageRoot, Summary, User
from app.routers import admin_library_automation as automation_api
from app.security import get_admin_user
from app.services import enrichment, local_asr, summaries
from app.services.job_manager import JobAdmissionError, JobConflictError, JobManager
from app.services.library import LibraryService, decode_library_cursor, encode_library_cursor
from app.services.library_import import RESUMABLE_STATES, ImportRunError, LibraryImportService
from app.services.network_policy import PublicSourcePolicyError

URL = "/api/admin/tasks"
Kind = Literal["download", "asr", "summary", "import"]
Filter = Literal["all", "active", "failed", "finished"]
DOWNLOAD_GROUPS = {
    "active": ("queued", "running", "postprocessing"),
    "failed": ("failed", "interrupted", "cancelled"),
    "finished": ("completed",),
}
IMPORT_GROUPS = {
    "active": ("running", "cancel_requested", "needs_confirmation"),
    "failed": ("failed", "interrupted", "cancelled"),
    "finished": ("succeeded", "partial"),
}
TRIGGER_LABELS = {"manual": "Manual", "scheduled": "Scheduled", "watch": "Watched folder"}
IN_FLIGHT = ("queued", "running")
# Another member's error keeps its reason but never names the source: URLs and yt-dlp "[site] <id>:" go.
SOURCE_REFS = re.compile(r"https?://\S+|\[[\w:.-]+\] [^\s:]+:")


class TaskResponse(BaseModel):
    kind: Kind
    id: str
    status: str
    title: str | None  # None when the admin may not see it
    detail: str | None  # extractor for downloads, model for enrichment jobs
    owner: str | None
    error: str | None
    attempts: int
    library_item_id: str | None
    created_at: datetime
    finished_at: datetime | None
    can_cancel: bool
    can_retry: bool


class TaskPageResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None
    counts: dict[str, dict[str, int]]


def _enrichment_counts(db: Session, model, running: set[str]) -> dict[str, int]:
    """DB states, with queued/running rows that have no worker in this process reported as interrupted."""
    counts = dict(db.query(model.state, func.count()).group_by(model.state).all())
    in_flight = sum(counts.pop(state, 0) for state in IN_FLIGHT)
    active = min(len(running), in_flight)
    return {**counts, **({"active": active} if active else {}), **({"interrupted": in_flight - active} if in_flight > active else {})}


def _running_ids(kind: Kind) -> set[str]:
    return (local_asr if kind == "asr" else summaries).jobs.running_ids()


def list_tasks(
    kind: Kind = "download",
    status: Filter = "all",
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> TaskPageResponse:
    asr_running, summary_running = _running_ids("asr"), _running_ids("summary")
    counts = {
        "download": dict(db.query(DownloadJob.status, func.count()).filter(DownloadJob.status != "staged").group_by(DownloadJob.status).all()),
        "asr": _enrichment_counts(db, AsrJob, asr_running),
        "summary": _enrichment_counts(db, Summary, summary_running),
        "import": dict(db.query(ImportRun.state, func.count()).group_by(ImportRun.state).all()),
    }
    if kind == "download":
        model, state_col = DownloadJob, DownloadJob.status
        query = db.query(DownloadJob, User).outerjoin(User, User.id == DownloadJob.user_id).filter(DownloadJob.status != "staged")
        if status != "all":
            query = query.filter(state_col.in_(DOWNLOAD_GROUPS[status]))
    elif kind == "import":
        model = ImportRun
        query = db.query(ImportRun, User, StorageRoot).outerjoin(User, User.id == ImportRun.user_id).outerjoin(StorageRoot, StorageRoot.id == ImportRun.root_id)
        if status != "all":
            query = query.filter(ImportRun.state.in_(IMPORT_GROUPS[status]))
    else:
        model = AsrJob if kind == "asr" else Summary
        running = asr_running if kind == "asr" else summary_running
        query = (
            db.query(model, User, LibraryItem)
            .outerjoin(User, User.id == model.requested_by)
            .outerjoin(LibraryItem, LibraryItem.id == model.library_item_id)
        )
        live = and_(model.state.in_(IN_FLIGHT), model.id.in_(running))
        if status == "active":
            query = query.filter(or_(live, model.state == "pending"))
        elif status == "failed":
            query = query.filter(or_(model.state.in_(("failed", "canceled")), and_(model.state.in_(IN_FLIGHT), model.id.notin_(running))))
        elif status == "finished":
            query = query.filter(model.state == "succeeded")
    if cursor:
        try:
            payload = decode_library_cursor(cursor)
            created, last_id = datetime.fromisoformat(payload["c"]), str(payload["i"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid task page cursor") from exc
        query = query.filter(or_(model.created_at < created, and_(model.created_at == created, model.id < last_id)))
    rows = query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1).all()
    items = [_task(kind, row, admin) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1][0]
        next_cursor = encode_library_cursor({"c": last.created_at.isoformat(), "i": last.id})
    return TaskPageResponse(items=items, next_cursor=next_cursor, counts=counts)


def _owner(user: User | None) -> str | None:
    return (user.display_name or user.username) if user else None


def _task(kind: Kind, row, admin: User) -> TaskResponse:
    if kind == "download":
        job, owner = row
        snapshot = job.preview_snapshot or {}
        mine = job.user_id == admin.id
        return TaskResponse(
            kind=kind, id=job.id, status=job.status, title=snapshot.get("title") if mine else None,
            detail=snapshot.get("extractor") or snapshot.get("extractor_key"), owner=_owner(owner),
            error=job.error if mine or not job.error else SOURCE_REFS.sub("[source]", job.error),
            attempts=len(job.attempts or []), library_item_id=None, created_at=job.created_at, finished_at=job.finished_at,
            can_cancel=job.status in DOWNLOAD_GROUPS["active"], can_retry=job.status in DOWNLOAD_GROUPS["failed"],
        )
    if kind == "import":
        run, owner, root = row
        folders = len(run.scope) if run.scope else 0
        scope = "full scan" if not folders else f"{folders} folder{'s' if folders != 1 else ''}"
        return TaskResponse(
            kind=kind, id=run.id, status=run.state, title=f"{root.label if root else 'Folder'} · {scope}",
            detail=TRIGGER_LABELS.get(run.trigger or "manual"), owner=_owner(owner), error=run.error, attempts=0, library_item_id=None,
            created_at=run.created_at, finished_at=run.finished_at, can_cancel=run.state == "running", can_retry=run.state in RESUMABLE_STATES,
        )
    job, owner, item = row
    state = (local_asr.state_of if kind == "asr" else summaries.state_of)(job)
    visible = item is not None and LibraryService.can_view(item, admin)
    return TaskResponse(
        kind=kind, id=job.id, status=state, title=item.title if visible else None, detail=job.model_id if kind != "asr" or job.kind == "asr" else f"{job.kind} · {job.model_id}",
        owner=_owner(owner), error=job.error, attempts=0, library_item_id=item.id if visible else None,
        created_at=job.created_at, finished_at=job.completed_at, can_cancel=state in (*IN_FLIGHT, "pending"), can_retry=False,
    )


def register(app: FastAPI, jobs: JobManager) -> None:
    admin = [Depends(get_admin_user)]

    def _download_owner(db: Session, job_id: str) -> User:
        job = db.get(DownloadJob, job_id)
        owner = db.get(User, job.user_id) if job and job.user_id else None
        if owner is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return owner

    def cancel_task(kind: Kind, task_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, str]:
        if kind == "download":
            # The owner's own cancel path: same cancel flag, same attempt, outputs untouched.
            try:
                return {"status": jobs.cancel(db, task_id, _download_owner(db, task_id)).status}
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Task not found") from exc
        if kind == "import":
            run = db.get(ImportRun, task_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Task not found")
            if run.state != "running":
                raise HTTPException(status_code=409, detail="This import is not running")
            return {"status": LibraryImportService(db).cancel(run).state}
        if kind == "asr" and enrichment.cancel_pending(task_id):
            return {"status": "canceled"}
        signaled = local_asr.cancel(task_id) if kind == "asr" else summaries.cancel(task_id)
        if not signaled:
            raise HTTPException(status_code=409, detail="This task is not running")
        return {"status": "canceling"}

    def retry_task(kind: Literal["download", "import"], task_id: str, background: BackgroundTasks, db: Session = Depends(get_db, scope="function")) -> dict[str, str]:
        if kind == "import":
            run = db.get(ImportRun, task_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Task not found")
            try:
                run = LibraryImportService(db).resume(run)
            except ImportRunError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            background.add_task(automation_api.automation_service().submit, run.id)  # after the response, so the resumed state is committed first
            return {"status": run.state}
        # Admitted against the owner's own limits, exactly as if they had pressed Retry.
        try:
            return {"status": jobs.retry(db, task_id, _download_owner(db, task_id)).status}
        except JobAdmissionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except (JobConflictError, PublicSourcePolicyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    app.get(URL, response_model=TaskPageResponse)(list_tasks)
    app.post(URL + "/{kind}/{task_id}/cancel", dependencies=admin)(cancel_task)
    app.post(URL + "/{kind}/{task_id}/retry", dependencies=admin)(retry_task)
