"""Admin API for scheduled library scans and folder watching (ADR 0017).

State and the scan queue live in services.library_automation. This router validates, persists the
per-root columns, and relays. Root paths never leave: labels only.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ImportRun, StorageRoot, User
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/library/automation"


def automation_service():
    """The one seam: tests replace it; S2's singleton is imported lazily so this router loads without it."""
    from app.services.library_automation import automation

    return automation


class RootPatch(BaseModel):
    schedule: Literal["off", "15m", "1h", "6h", "nightly"] | None = None
    watch: bool | None = None
    watch_interval_s: Literal[60, 300, 900] | None = None


class NightHourPatch(BaseModel):
    night_hour: int = Field(ge=0, le=23)


class ScanRequest(BaseModel):
    root_id: str | None = None


class LibraryAutomationResponse(BaseModel):
    server_timezone: str
    night_hour: int
    poller: dict[str, Any]
    roots: list[dict[str, Any]]


def get_automation() -> LibraryAutomationResponse:
    return LibraryAutomationResponse(**automation_service().snapshot())


def patch_night_hour(payload: NightHourPatch, db: Session = Depends(get_db, scope="function")) -> LibraryAutomationResponse:
    with write_transaction(db, name="library_scan_night_hour"):
        YtDlpService(db).ensure_app_settings().library_scan_night_hour = payload.night_hour
        db.flush()
    return get_automation()


def patch_root(root_id: str, payload: RootPatch, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    root = db.get(StorageRoot, root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Storage root not found")
    if root.mode != "external":
        raise HTTPException(status_code=409, detail="Only external folders can be scheduled or watched")
    if db.query(ImportRun.id).filter(ImportRun.root_id == root_id).first() is None:
        raise HTTPException(status_code=409, detail="Import this folder once before scheduling it")
    with write_transaction(db, name="library_automation_root"):
        for field, column in (("schedule", "scan_schedule"), ("watch", "watch_enabled"), ("watch_interval_s", "watch_interval_s")):
            value = getattr(payload, field)
            if value is not None:
                setattr(root, column, value)
        db.flush()
    # Committed before the automation service reloads, so its baseline reads the new columns.
    service = automation_service()
    service.roots_changed()
    entry = service.root_entry(root_id)
    if entry is None:  # the root was deleted between the commit and the read
        raise HTTPException(status_code=404, detail="Storage root not found")
    return entry


def scan_now(payload: ScanRequest | None = None, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    root_id = payload.root_id if payload else None
    if root_id and db.get(StorageRoot, root_id) is None:
        raise HTTPException(status_code=404, detail="Storage root not found")
    return automation_service().scan_now(admin.id, root_id)


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=LibraryAutomationResponse, dependencies=admin)(get_automation)
    app.patch(URL, response_model=LibraryAutomationResponse, dependencies=admin)(patch_night_hour)
    app.patch(URL + "/roots/{root_id}", dependencies=admin)(patch_root)
    app.post(URL + "/scan", status_code=202, dependencies=admin)(scan_now)
