"""Admin-only live-recording retention policy. The sweep runs in the maintenance loop."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/recording-retention"


class RecordingRetention(BaseModel):
    # 0 disables each bound. Recordings a member kept are always exempt.
    keep_days: int = Field(ge=0, le=3650)
    max_gb: int = Field(ge=0, le=100_000)


def get_retention(db: Session = Depends(get_db, scope="function")) -> RecordingRetention:
    record = YtDlpService(db).get_app_settings()
    return RecordingRetention(keep_days=record.recording_keep_days or 0, max_gb=record.recording_max_gb or 0)


def update_retention(payload: RecordingRetention, db: Session = Depends(get_db, scope="function")) -> RecordingRetention:
    with write_transaction(db, name="recording_retention_update"):
        record = YtDlpService(db).ensure_app_settings()
        record.recording_keep_days, record.recording_max_gb = payload.keep_days, payload.max_gb
    return payload


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=RecordingRetention, dependencies=admin)(get_retention)
    app.put(URL, response_model=RecordingRetention, dependencies=admin)(update_retention)
