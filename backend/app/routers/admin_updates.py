"""Admin-only stable release status and preferences."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import get_admin_user
from app.services import release_updates

URL = "/api/admin/updates"


class UpdatePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_checks: bool | None = None
    email_notifications: bool | None = None


def get_updates(db: Session = Depends(get_db, scope="function")) -> release_updates.UpdateStatus:
    return release_updates.get_status(db, checking=release_updates.coordinator.checking)


def check_updates(db: Session = Depends(get_db, scope="function")) -> release_updates.UpdateStatus:
    result = release_updates.coordinator.request_manual()
    if result == "cooldown":
        raise HTTPException(status_code=429, detail="Wait one minute before checking again.", headers={"Retry-After": "60"})
    if result == "closed":
        raise HTTPException(status_code=503, detail="Update checks are unavailable while Lumina is stopping.")
    return release_updates.get_status(db, checking=release_updates.coordinator.checking)


def patch_updates(payload: UpdatePreferences, db: Session = Depends(get_db, scope="function")) -> release_updates.UpdateStatus:
    return release_updates.update_preferences(
        db,
        automatic_checks=payload.automatic_checks,
        email_notifications=payload.email_notifications,
    )


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=release_updates.UpdateStatus, dependencies=admin)(get_updates)
    app.post(URL + "/check", response_model=release_updates.UpdateStatus, status_code=202, dependencies=admin)(check_updates)
    app.patch(URL, response_model=release_updates.UpdateStatus, dependencies=admin)(patch_updates)
