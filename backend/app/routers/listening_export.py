"""Member-owned, opt-in listening history connections and replay-safe events."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ListeningExportConnection, ListeningPlayEvent, User
from app.security import get_current_user
from app.services import listening_export as exports
from app.services.local_ai import LocalAiError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenBody(Strict):
    token: str = Field(min_length=1, max_length=256)


class LastfmBody(Strict):
    api_key: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    api_secret: str = Field(pattern=r"^[a-fA-F0-9]{32}$")


class EventBody(Strict):
    id: UUID
    owner_id: str = Field(min_length=1, max_length=36)
    account_generations: dict[Literal["lastfm", "listenbrainz"], Annotated[str, Field(min_length=1, max_length=36)]] = Field(max_length=2)
    item_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    started_at: int = Field(ge=0)
    listened_seconds: float = Field(ge=0, le=604800, allow_inf_nan=False)


def connections(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = {row.provider: row for row in db.scalars(select(ListeningExportConnection).where(ListeningExportConnection.user_id == user.id))}
    return {"items": [exports.account_snapshot(rows[provider]) if provider in rows else {"provider": provider, "enabled": False, "username": None,
                       "credentials_set": False, "authorization_pending": False, "generation": None} for provider in ("lastfm", "listenbrainz")]}


def listenbrainz(body: TokenBody, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    previous = db.get(ListeningExportConnection, (user.id, "listenbrainz"))
    generation = previous.generation if previous else None
    try:
        result = exports.listenbrainz(body.token, "1/validate-token")
        username = result.get("user_name")
        if result.get("valid") is not True or not isinstance(username, str) or not 1 <= len(username) <= 120:
            raise HTTPException(400, "ListenBrainz token was not accepted")
    except LocalAiError:
        raise HTTPException(502, "ListenBrainz token could not be verified") from None
    return exports.account_snapshot(exports.replace_account(db, user, "listenbrainz", {"token": body.token}, username=username, enabled=True, expected_generation=generation, compare_generation=True))


def lastfm_authorize(body: LastfmBody, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    credentials = body.model_dump()
    previous = db.get(ListeningExportConnection, (user.id, "lastfm"))
    generation = previous.generation if previous else None
    try:
        result = exports.lastfm(credentials, "auth.getToken")
        token = result.get("token")
        if not isinstance(token, str) or not 1 <= len(token) <= 256 or not token.isalnum():
            raise HTTPException(502, "Last.fm authorization could not start")
    except (LocalAiError, exports.ExportRejected):
        raise HTTPException(502, "Last.fm authorization could not start") from None
    exports.stage_lastfm(db, user, {**credentials, "request_token": token}, generation)
    return {"authorization_url": "https://www.last.fm/api/auth/?" + urlencode({"api_key": body.api_key, "token": token})}


def lastfm_finish(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    row = db.get(ListeningExportConnection, (user.id, "lastfm"))
    credentials = dict((row.credentials or {}).get("_pending") or {}) if row else {}
    if not credentials.get("request_token"):
        raise HTTPException(409, "Start Last.fm authorization first")
    generation = row.generation
    try:
        result = exports.lastfm(credentials, "auth.getSession", token=credentials["request_token"])
        session = result.get("session", {})
        key, username = session.get("key"), session.get("name")
        if not isinstance(key, str) or not 1 <= len(key) <= 256 or not isinstance(username, str) or not 1 <= len(username) <= 120:
            raise HTTPException(502, "Last.fm authorization is incomplete")
    except (LocalAiError, exports.ExportRejected):
        raise HTTPException(502, "Approve Lumina in Last.fm, then finish connecting") from None
    db.refresh(row)
    if row.generation != generation:
        raise HTTPException(409, "Last.fm connection changed")
    request_token = credentials.pop("request_token")
    credentials["session_key"] = key
    return exports.account_snapshot(exports.replace_account(db, user, "lastfm", credentials, username=username, enabled=True, expected_generation=generation, expected_request_token=request_token))


def disconnect(provider: Literal["lastfm", "listenbrainz"], user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return exports.account_snapshot(exports.replace_account(db, user, provider, None))


def event(body: EventBody, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    if body.owner_id != user.id:
        raise HTTPException(409, "Listening event owner changed")
    return exports.record(db, user, str(body.id), body.item_id, body.started_at, body.listened_seconds, body.account_generations)


def events(page: int = Query(default=1, ge=1, le=100_000), user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.scalars(select(ListeningPlayEvent).where(ListeningPlayEvent.user_id == user.id).order_by(ListeningPlayEvent.started_at.desc()).offset((page - 1)*50).limit(50))
    return {"items": [exports.event_snapshot(row) for row in rows], "page": page}


def reconcile(event_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return exports.reconcile(db, user, str(event_id))


def register(app: FastAPI) -> None:
    app.get("/api/me/listening-export")(connections)
    app.put("/api/me/listening-export/listenbrainz")(listenbrainz)
    app.post("/api/me/listening-export/lastfm/authorize")(lastfm_authorize)
    app.post("/api/me/listening-export/lastfm/finish")(lastfm_finish)
    app.delete("/api/me/listening-export/{provider}")(disconnect)
    app.post("/api/me/listening-events")(event)
    app.get("/api/me/listening-events")(events)
    app.post("/api/me/listening-events/{event_id}/reconcile")(reconcile)
