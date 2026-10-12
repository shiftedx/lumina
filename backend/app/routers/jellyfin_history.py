"""Jellyfin watch history into Lumina (ADR 0010 amendment): a member imports their own; an admin brings the household over."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    JellyfinHousehold, JellyfinHouseholdRequest, JellyfinImportRequest, JellyfinImportStatus, JellyfinImportSummary,
)
from app.models import User
from app.persistence import write_transaction
from app.security import get_admin_user, get_current_user
from app.services import jellyfin_household
from app.services.jellyfin_history import apply, plan
from app.services.jellyfin_history_client import JellyfinHistory, JellyfinImportError, admin_session, fetch_history, list_users
from app.services.rate_limit import enforce_rate_limit
from app.services.yt_dlp_service import YtDlpService

URL = "/api/me/jellyfin-import"
HOUSEHOLD_URL = "/api/admin/jellyfin-household"
NOT_SET = "Ask a vault owner to set the Jellyfin server address."
NOT_SET_ADMIN = "Set the Jellyfin server address under Media server first."


def get_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> JellyfinImportStatus:
    return JellyfinImportStatus(server=YtDlpService(db).get_app_settings().jellyfin_import_url or None)


def _server(db: Session, request: Request, user: User, not_set: str) -> str:
    enforce_rate_limit("jellyfin_import", request, user_id=user.id)
    server = YtDlpService(db).get_app_settings().jellyfin_import_url
    if not server:
        raise HTTPException(status_code=409, detail=not_set)
    return server


def _history(db: Session, request: Request, user: User, payload: JellyfinImportRequest) -> JellyfinHistory:
    server = _server(db, request, user, NOT_SET)
    try:
        return fetch_history(server, payload.username, payload.password)
    except JellyfinImportError as exc:
        # 400, not 401: the web app treats a 401 as its own session ending.
        raise HTTPException(status_code=400, detail=str(exc)) from None


def preview_import(
    payload: JellyfinImportRequest, request: Request,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinImportSummary:
    history = _history(db, request, current_user, payload)
    return plan(db, current_user, history).summary()


def run_import(
    payload: JellyfinImportRequest, request: Request,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinImportSummary:
    history = _history(db, request, current_user, payload)  # network first: the write transaction never waits on Jellyfin
    with write_transaction(db, name="jellyfin_history_import"):
        result = plan(db, current_user, history)  # recomputed inside the one write, against what is true now
        apply(db, current_user, result)
    return result.summary()


def _household(db: Session, request: Request, admin: User, payload: JellyfinImportRequest, chosen: set[str] | None) -> JellyfinHousehold:
    server = _server(db, request, admin, NOT_SET_ADMIN)
    try:
        with admin_session(server, payload.username, payload.password) as (session, admin_jellyfin_id):
            members = jellyfin_household.run(db, admin, session, admin_jellyfin_id, list_users(session), chosen)
    except JellyfinImportError as exc:  # sign-in, not an administrator, or the user list; per-user failures are rows
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return JellyfinHousehold(members=members)


def preview_household(
    payload: JellyfinImportRequest, request: Request,
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinHousehold:
    return _household(db, request, current_user, payload, None)


def run_household(
    payload: JellyfinHouseholdRequest, request: Request,
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinHousehold:
    return _household(db, request, current_user, payload, set(payload.jellyfin_ids))


def register(app: FastAPI) -> None:
    app.get(URL, response_model=JellyfinImportStatus)(get_status)
    app.post(URL + "/preview", response_model=JellyfinImportSummary)(preview_import)
    app.post(URL, response_model=JellyfinImportSummary)(run_import)
    app.post(HOUSEHOLD_URL + "/preview", response_model=JellyfinHousehold)(preview_household)
    app.post(HOUSEHOLD_URL, response_model=JellyfinHousehold)(run_household)
