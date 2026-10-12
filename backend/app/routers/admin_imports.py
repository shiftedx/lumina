"""Admin-only external library import runs: start, observe, cancel, resume, confirm."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ImportEntry, ImportRun, User
from app.security import get_admin_user
from app.services.library_import import ImportRunError, LibraryImportService, drive

URL = "/api/admin/imports"


class ImportStartRequest(BaseModel):
    root_id: str
    visibility: Literal["private", "shared"] = "shared"


class ImportRunResponse(BaseModel):
    id: str
    root_id: str
    state: str
    visibility: str
    counters: dict
    coverage: str
    error: str | None
    root_observation: dict
    created_at: datetime
    finished_at: datetime | None
    status_url: str


class ImportEntryResponse(BaseModel):
    relative_path: str
    outcome: str
    error: str | None
    library_item_id: str | None


def _serialize(run: ImportRun) -> ImportRunResponse:
    return ImportRunResponse(
        id=run.id,
        root_id=run.root_id,
        state=run.state,
        visibility=run.visibility,
        counters=run.counters or {},
        coverage=run.coverage,
        error=run.error,
        root_observation=run.root_observation or {},
        created_at=run.created_at,
        finished_at=run.finished_at,
        status_url=f"{URL}/{run.id}",
    )


def _get_run(db: Session, run_id: str) -> ImportRun:
    run = db.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return run


def start_import(
    payload: ImportStartRequest,
    background: BackgroundTasks,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> ImportRunResponse:
    try:
        run = LibraryImportService(db).start(payload.root_id, admin.id, payload.visibility)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImportRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(drive, run.id)
    return _serialize(run)


def list_imports(root_id: str | None = None, db: Session = Depends(get_db, scope="function")) -> list[ImportRunResponse]:
    query = db.query(ImportRun)
    if root_id:
        query = query.filter(ImportRun.root_id == root_id)
    return [_serialize(run) for run in query.order_by(ImportRun.created_at.desc()).limit(50)]


def get_import(run_id: str, db: Session = Depends(get_db, scope="function")) -> ImportRunResponse:
    return _serialize(_get_run(db, run_id))


def list_import_entries(
    run_id: str,
    outcome: Literal["failed", "skipped", "review"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db, scope="function"),
) -> list[ImportEntryResponse]:
    _get_run(db, run_id)
    query = db.query(ImportEntry).filter(ImportEntry.run_id == run_id)
    if outcome:
        query = query.filter(ImportEntry.outcome == outcome)
    return [
        ImportEntryResponse(relative_path=entry.relative_path, outcome=entry.outcome, error=entry.error, library_item_id=entry.library_item_id)
        for entry in query.order_by(ImportEntry.id.asc()).limit(limit)
    ]


def cancel_import(run_id: str, db: Session = Depends(get_db, scope="function")) -> ImportRunResponse:
    return _serialize(LibraryImportService(db).cancel(_get_run(db, run_id)))


def resume_import(run_id: str, background: BackgroundTasks, db: Session = Depends(get_db, scope="function")) -> ImportRunResponse:
    try:
        run = LibraryImportService(db).resume(_get_run(db, run_id))
    except ImportRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(drive, run.id)
    return _serialize(run)


def confirm_import(run_id: str, db: Session = Depends(get_db, scope="function")) -> ImportRunResponse:
    """An admin confirms a held mass-missing run: its unseen files really are gone."""
    try:
        run = LibraryImportService(db).confirm(_get_run(db, run_id))
    except ImportRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(run)


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.post(URL, response_model=ImportRunResponse, status_code=202)(start_import)
    app.get(URL, response_model=list[ImportRunResponse], dependencies=admin)(list_imports)
    app.get(URL + "/{run_id}", response_model=ImportRunResponse, dependencies=admin)(get_import)
    app.get(URL + "/{run_id}/entries", response_model=list[ImportEntryResponse], dependencies=admin)(list_import_entries)
    app.post(URL + "/{run_id}/cancel", response_model=ImportRunResponse, status_code=202, dependencies=admin)(cancel_import)
    app.post(URL + "/{run_id}/resume", response_model=ImportRunResponse, status_code=202, dependencies=admin)(resume_import)
    app.post(URL + "/{run_id}/confirm", response_model=ImportRunResponse, status_code=202, dependencies=admin)(confirm_import)
