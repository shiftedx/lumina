"""Connected apps settings API: list, create agent token (shown once), revoke."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.media_schemas import (
    AppPasswordCreated, AppPasswordCreateRequest, ConnectedApp, ConnectedAppCreateRequest, ConnectedAppCreated, SignOutAllAppsResponse,
)
from app.models import User
from app.security import get_current_user, require_browser_session
from app.services import public_address
from app.services.connected_apps import (
    create_agent_token, create_app_password, list_connected_apps, revoke_connected_app, sign_out_all_apps, to_connected_app,
)

URL = "/api/connected-apps"


def list_apps(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[ConnectedApp]:
    return list_connected_apps(db, current_user)


def create_app(
    payload: ConnectedAppCreateRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ConnectedAppCreated:
    require_browser_session(request)  # a leaked token or app password must not mint successors that outlive its revocation
    try:
        record, token = create_agent_token(db, current_user, payload.name, payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return ConnectedAppCreated(app=to_connected_app(record), token=token)


def create_password(
    payload: AppPasswordCreateRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AppPasswordCreated:
    require_browser_session(request)  # never minted by a token or HTTP Basic, an app password included
    try:
        record, password = create_app_password(db, current_user, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return AppPasswordCreated(
        app=to_connected_app(record), password=password, username=current_user.username,
        server_address=public_address.origin() or public_address.local_origin() or settings.resolved_app_public_url or None,
        local_server_address=public_address.local_origin(),
    )


def sign_out_all(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> SignOutAllAppsResponse:
    require_browser_session(request)
    return SignOutAllAppsResponse(revoked=sign_out_all_apps(db, current_user))


def revoke_app(app_id: str, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    require_browser_session(request)
    try:
        revoke_connected_app(db, current_user, app_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Connected app not found") from exc
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.get(URL, response_model=list[ConnectedApp])(list_apps)
    app.post(URL, response_model=ConnectedAppCreated, status_code=201)(create_app)
    app.post(URL + "/app-passwords", response_model=AppPasswordCreated, status_code=201)(create_password)
    app.post(URL + "/sign-out-all", response_model=SignOutAllAppsResponse)(sign_out_all)
    app.delete(URL + "/{app_id}", status_code=204)(revoke_app)
