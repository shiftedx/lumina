"""Import Plex accounts and video progress using request-scoped Plex tokens."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    JellyfinImportStatus, JellyfinImportSummary, PlexHousehold, PlexHouseholdMember,
    PlexHouseholdRequest, PlexImportRequest,
)
from app.models import User
from app.persistence import write_transaction
from app.security import get_admin_user, get_current_user
from app.services import jellyfin_household, plex_history_client
from app.services.jellyfin_history import apply, plan
from app.services.jellyfin_history_client import JellyfinHistory, JellyfinImportError
from app.services.rate_limit import enforce_rate_limit
from app.services.yt_dlp_service import YtDlpService

URL = "/api/me/plex-import"
HOUSEHOLD_URL = "/api/admin/plex-household"


def get_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> JellyfinImportStatus:
    return JellyfinImportStatus(server=YtDlpService(db).get_app_settings().plex_import_url or None)


def _server(db: Session, request: Request, user: User) -> str:
    enforce_rate_limit("plex_import", request, user_id=user.id)
    server = YtDlpService(db).get_app_settings().plex_import_url
    if not server:
        raise HTTPException(status_code=409, detail="Ask a vault owner to set the Plex server address under Media server first.")
    return server


def _history(db: Session, request: Request, user: User, payload: PlexImportRequest) -> JellyfinHistory:
    server = _server(db, request, user)
    try:
        return plex_history_client.fetch_history(server, payload.token)
    except JellyfinImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def preview_import(
    payload: PlexImportRequest, request: Request,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinImportSummary:
    return plan(db, current_user, _history(db, request, current_user, payload)).summary()


def run_import(
    payload: PlexImportRequest, request: Request,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> JellyfinImportSummary:
    history = _history(db, request, current_user, payload)
    with write_transaction(db, name="plex_history_import"):
        result = plan(db, current_user, history)
        apply(db, current_user, result)
    return result.summary()


def _household(db: Session, request: Request, admin: User, payload: PlexImportRequest, chosen: set[str] | None) -> PlexHousehold:
    server = _server(db, request, admin)
    try:
        owner_id, users, tokens = plex_history_client.household(server, payload.token)
        rows = jellyfin_household.run(
            db, admin, server, owner_id, users, chosen, source="Plex",
            read=lambda base_url, user_id: plex_history_client.read_history(base_url, tokens[user_id]),
        )
    except JellyfinImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return PlexHousehold(members=[PlexHouseholdMember(
        plex_id=row.jellyfin_id, plex_name=row.jellyfin_name,
        **row.model_dump(exclude={"jellyfin_id", "jellyfin_name"}),
    ) for row in rows])


def preview_household(
    payload: PlexImportRequest, request: Request,
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> PlexHousehold:
    return _household(db, request, current_user, payload, None)


def run_household(
    payload: PlexHouseholdRequest, request: Request,
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> PlexHousehold:
    return _household(db, request, current_user, payload, set(payload.plex_ids))


def register(app: FastAPI) -> None:
    app.get(URL, response_model=JellyfinImportStatus)(get_status)
    app.post(URL + "/preview", response_model=JellyfinImportSummary)(preview_import)
    app.post(URL, response_model=JellyfinImportSummary)(run_import)
    app.post(HOUSEHOLD_URL + "/preview", response_model=PlexHousehold)(preview_household)
    app.post(HOUSEHOLD_URL, response_model=PlexHousehold)(run_household)
