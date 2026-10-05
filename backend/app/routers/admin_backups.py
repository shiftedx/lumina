"""Admin-only database backups. Copies hold password hashes: admin-only, never cached."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.persistence import write_transaction
from app.models import User
from app.security import get_admin_user, get_owner_session_user, verify_password
from app.services import backups
from app.services.rate_limit import enforce_rate_limit
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/backups"


class BackupSchedule(BaseModel):
    daily: bool
    keep: int = Field(ge=1, le=90)


class BackupListResponse(BaseModel):
    backups: list[dict]
    schedule: BackupSchedule


class BackupDownloadRequest(BaseModel):
    password: str = Field(max_length=256)


class BackupVerifyResponse(BaseModel):
    ok: bool
    problems: list[str]


def schedule_of(record) -> BackupSchedule:  # noqa: ANN001
    # A not-yet-persisted settings row has no column defaults yet.
    return BackupSchedule(daily=record.backup_daily is not False, keep=record.backup_keep or 7)


def _path(name: str):  # noqa: ANN202
    try:
        return backups.backup_path(name)
    except backups.BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def list_backups(db: Session = Depends(get_db, scope="function")) -> BackupListResponse:
    return BackupListResponse(backups=backups.list_backups(), schedule=schedule_of(YtDlpService(db).get_app_settings()))


async def create_backup() -> dict:
    try:
        return await run_in_threadpool(backups.create_backup)
    except backups.BackupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def verify_backup(name: str) -> BackupVerifyResponse:
    problems = await run_in_threadpool(backups.verify_file, _path(name))
    return BackupVerifyResponse(ok=not problems, problems=problems)


def download_backup(
    name: str, payload: BackupDownloadRequest, request: Request, admin: User = Depends(get_owner_session_user)
) -> FileResponse:
    """A backup holds every account's password hash, so a download re-proves the admin password."""
    path = _path(name)
    enforce_rate_limit("session_login", request, user_id=admin.id)
    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Password is incorrect.")
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=f"{name}.db", headers={"Cache-Control": "no-store"})


def delete_backup(name: str) -> None:
    _path(name)
    backups.delete_backup(name)


def update_schedule(payload: BackupSchedule, db: Session = Depends(get_db, scope="function")) -> BackupSchedule:
    with write_transaction(db, name="backup_schedule_update"):
        record = YtDlpService(db).ensure_app_settings()
        record.backup_daily, record.backup_keep = payload.daily, payload.keep
    return payload


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=BackupListResponse, dependencies=admin)(list_backups)
    app.post(URL, status_code=201, dependencies=admin)(create_backup)
    app.put(URL + "/schedule", response_model=BackupSchedule, dependencies=admin)(update_schedule)
    app.post(URL + "/{name}/verify", response_model=BackupVerifyResponse, dependencies=admin)(verify_backup)
    app.post(URL + "/{name}/download")(download_backup)
    app.delete(URL + "/{name}", status_code=204, dependencies=admin)(delete_backup)
