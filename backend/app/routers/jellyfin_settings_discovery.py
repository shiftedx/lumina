"""Personal discovery and connection settings for authenticated Connected apps.

This adapter exposes only member-owned settings through the Connected-app
namespace.  Browser-session routes and household administration remain outside
this surface.  Credentials are request-scoped, never returned, and require a
fresh one-use reauthentication proof before a mutation.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import JellyfinImportRequest, JellyfinImportStatus, JellyfinImportSummary, PlexImportRequest
from app.models import User, utcnow
from app.persistence import write_transaction
from app.routers import jellyfin_history, listening_export, plex_history, youtube_session
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.routers.jellyfin_settings import _require_reauth, require_native_youtube_settings
from app.schemas import (
    InterestCategoryResponse,
    MemberInterestsResponse,
    MemberInterestsUpdateRequest,
    SuppressionListResponse,
    SuppressionResponse,
)
from app.services.member_recommendations import MemberInterestService
from app.services.member_suppressions import MemberSuppressionService, queue_member_changed
from app.services.popular_discovery import POPULAR_CATEGORIES
from app.services.reco.events import RecoEventService


router = APIRouter(prefix="/jellyfin/lumina/settings")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


def _live_user(db: Session, caller: JellyfinCaller) -> User:
    user = db.get(User, caller.user.id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return user


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _interests_response(db: Session, user: User) -> MemberInterestsResponse:
    labels = {category.key: category.label for category in POPULAR_CATEGORIES}
    return MemberInterestsResponse(
        categories=[InterestCategoryResponse(key=key, label=label) for key, label in labels.items()],
        selected_keys=list(MemberInterestService(db).list_for(user)),
    )


@router.get("/interests")
def get_interests(response: Response, caller: Caller, db: Db) -> MemberInterestsResponse:
    _no_store(response)
    return _interests_response(db, _live_user(db, caller))


@router.put("/interests")
def put_interests(body: MemberInterestsUpdateRequest, response: Response, caller: Caller, db: Db) -> MemberInterestsResponse:
    user = _live_user(db, caller)
    try:
        with write_transaction(db, name="native_member_interests_update"):
            MemberInterestService(db).replace(user, body.keys)
            # The refresher is process-owned by main; defer importing it until app startup is complete.
            from app.main import reco_refresher
            queue_member_changed(db, user.id, reco_refresher)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _no_store(response)
    return _interests_response(db, user)


@router.get("/suppressions")
def get_suppressions(response: Response, caller: Caller, db: Db) -> SuppressionListResponse:
    _no_store(response)
    records = MemberSuppressionService(db).list_for(_live_user(db, caller))

    def scoped(scope: str) -> list[SuppressionResponse]:
        return [SuppressionResponse.model_validate(record) for record in records if record.scope == scope]

    return SuppressionListResponse(items=scoped("item"), channels=scoped("channel"), fewer=scoped("fewer"), titles=scoped("title"))


@router.delete("/suppressions/{suppression_id}", status_code=204)
def delete_suppression(suppression_id: str, caller: Caller, db: Db) -> Response:
    user = _live_user(db, caller)
    service = MemberSuppressionService(db)
    with write_transaction(db, name="native_member_suppression_restore"):
        record = service.get(user, suppression_id)
        if record is not None and service.restore(user, suppression_id):
            RecoEventService(db).record_feedback(
                user.id,
                "restore",
                target_kind="title" if record.scope == "title" else "remote",
                item_key=record.id,
                channel_key=record.channel_key,
                list_id=None,
                now=utcnow(),
            )
            from app.main import reco_refresher
            queue_member_changed(db, user.id, reco_refresher)
    response = Response(status_code=204)
    _no_store(response)
    return response


@router.get("/listening-export")
def get_listening_export(response: Response, caller: Caller, db: Db) -> dict:
    _no_store(response)
    return listening_export.connections(_live_user(db, caller), db)


@router.put("/listening-export/listenbrainz")
def put_listenbrainz(body: listening_export.TokenBody, request: Request, response: Response, caller: Caller, db: Db) -> dict:
    _require_reauth(request, caller)
    _no_store(response)
    return listening_export.listenbrainz(body, _live_user(db, caller), db)


@router.post("/listening-export/lastfm/authorize")
def authorize_lastfm(body: listening_export.LastfmBody, request: Request, response: Response, caller: Caller, db: Db) -> dict:
    _require_reauth(request, caller)
    _no_store(response)
    return listening_export.lastfm_authorize(body, _live_user(db, caller), db)


@router.post("/listening-export/lastfm/finish")
def finish_lastfm(request: Request, response: Response, caller: Caller, db: Db) -> dict:
    _require_reauth(request, caller)
    _no_store(response)
    return listening_export.lastfm_finish(_live_user(db, caller), db)


@router.delete("/listening-export/{provider}")
def delete_listening_export(provider: Literal["lastfm", "listenbrainz"], request: Request, response: Response, caller: Caller, db: Db) -> dict:
    _require_reauth(request, caller)
    _no_store(response)
    return listening_export.disconnect(provider, _live_user(db, caller), db)


@router.get("/listening-events/page/{page}")
def get_listening_events(page: Annotated[int, Path(ge=1, le=100_000)], response: Response, caller: Caller, db: Db) -> dict:
    _no_store(response)
    return listening_export.events(page, _live_user(db, caller), db)


@router.post("/listening-events/{event_id}/reconcile")
def reconcile_listening_event(event_id: UUID, request: Request, response: Response, caller: Caller, db: Db) -> dict:
    _require_reauth(request, caller)
    _no_store(response)
    return listening_export.reconcile(event_id, _live_user(db, caller), db)


@router.get("/youtube-session")
def get_youtube_session(response: Response, caller: Caller, db: Db) -> dict:
    user = _live_user(db, caller)
    require_native_youtube_settings(db, user)
    _no_store(response)
    return youtube_session.status(response, user, db)


@router.put("/youtube-session")
def put_youtube_session(body: youtube_session.ConnectBody, request: Request, response: Response, caller: Caller, db: Db) -> dict:
    user = _live_user(db, caller)
    require_native_youtube_settings(db, user)
    _require_reauth(request, caller)
    _no_store(response)
    return youtube_session.connect(body, request, response, user, db)


@router.delete("/youtube-session")
def delete_youtube_session(request: Request, response: Response, caller: Caller, db: Db) -> dict:
    user = _live_user(db, caller)
    require_native_youtube_settings(db, user)
    _require_reauth(request, caller)
    _no_store(response)
    return youtube_session.disconnect(response, user, db)


@router.get("/jellyfin-import")
def get_jellyfin_import(response: Response, caller: Caller, db: Db) -> JellyfinImportStatus:
    _no_store(response)
    return jellyfin_history.get_status(_live_user(db, caller), db)


@router.post("/jellyfin-import/preview")
def preview_jellyfin_import(body: JellyfinImportRequest, request: Request, response: Response, caller: Caller, db: Db) -> JellyfinImportSummary:
    _require_reauth(request, caller)
    _no_store(response)
    return jellyfin_history.preview_import(body, request, _live_user(db, caller), db)


@router.post("/jellyfin-import")
def run_jellyfin_import(body: JellyfinImportRequest, request: Request, response: Response, caller: Caller, db: Db) -> JellyfinImportSummary:
    _require_reauth(request, caller)
    _no_store(response)
    return jellyfin_history.run_import(body, request, _live_user(db, caller), db)


@router.get("/plex-import")
def get_plex_import(response: Response, caller: Caller, db: Db) -> JellyfinImportStatus:
    _no_store(response)
    return plex_history.get_status(_live_user(db, caller), db)


@router.post("/plex-import/preview")
def preview_plex_import(body: PlexImportRequest, request: Request, response: Response, caller: Caller, db: Db) -> JellyfinImportSummary:
    _require_reauth(request, caller)
    _no_store(response)
    return plex_history.preview_import(body, request, _live_user(db, caller), db)


@router.post("/plex-import")
def run_plex_import(body: PlexImportRequest, request: Request, response: Response, caller: Caller, db: Db) -> JellyfinImportSummary:
    _require_reauth(request, caller)
    _no_store(response)
    return plex_history.run_import(body, request, _live_user(db, caller), db)
