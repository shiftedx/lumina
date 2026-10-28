"""Narrow native metadata actions; Connected apps never gain browser API access."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import EditRequest, IdentifyCandidate, IdentifyRequest, MetadataRefreshResponse, TitleMetadataDoc
from app.models import AppSettings, DeviceToken, User
from app.routers import admin_metadata, metadata_editor
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.services import metadata_editor as me, two_factor
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/jellyfin/lumina/metadata")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


def _authority(caller: JellyfinCaller, db: Session) -> tuple[User, bool, bool]:
    user = db.get(User, caller.user.id, populate_existing=True)
    token = db.get(DeviceToken, caller.token_id, populate_existing=True)
    if user is None or not user.is_active or token is None or token.user_id != user.id or token.kind != "jellyfin":
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    # App-password descendants retain browse/play-only authority, even for an owner.
    writable = token.scope == "write" and token.app_password_id is None
    return user, writable and me.can_edit_details(user, db.get(AppSettings, 1)), writable and two_factor.owner_capable(db, user)


def _editor(caller: JellyfinCaller, db: Session, *, identify: bool = False) -> User:
    user, edit, match = _authority(caller, db)
    if not (match if identify else edit):
        raise HTTPException(status_code=403, detail="identify_requires_owner" if identify else "editing_not_allowed")
    return user


@router.get("/capabilities")
def capabilities(response: Response, caller: Caller, db: Db) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    _, edit, match = _authority(caller, db)
    return {"can_edit_details": edit, "can_identify": match}


@router.get("/titles/{title_id}")
def document(title_id: str, response: Response, caller: Caller, db: Db):  # noqa: ANN201
    user = _editor(caller, db)
    response.headers["Cache-Control"] = "no-store"
    result = metadata_editor.get_doc(title_id, user=user, db=db)
    if isinstance(result, TitleMetadataDoc):
        result.can_identify = result.can_identify and two_factor.owner_capable(db, user)
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
    return result


@router.post("/edits")
def save(body: EditRequest, response: Response, caller: Caller, db: Db):  # noqa: ANN201
    user = _editor(caller, db)
    response.headers["Cache-Control"] = "no-store"
    result = metadata_editor.save(body, user=user, db=db)
    result.headers["Cache-Control"] = "no-store"
    return result


@router.get("/titles/{title_id}/matches", response_model=list[IdentifyCandidate])
def matches(title_id: str, request: Request, response: Response, caller: Caller, db: Db,
            q: str | None = Query(default=None, max_length=200), year: int | None = Query(default=None, ge=1870, le=2100)) -> list[IdentifyCandidate]:
    user = _editor(caller, db, identify=True)
    response.headers["Cache-Control"] = "no-store"
    enforce_rate_limit("metadata_preview", request, user_id=user.id)
    return admin_metadata.search_title_matches(title_id, q, year, user, db)


@router.post("/titles/{title_id}/match", response_model=MetadataRefreshResponse, status_code=202)
def identify(title_id: str, body: IdentifyRequest, response: Response, caller: Caller, db: Db):  # noqa: ANN201
    user = _editor(caller, db, identify=True)
    response.headers["Cache-Control"] = "no-store"
    return admin_metadata.identify_title_match(title_id, body, user, db)
