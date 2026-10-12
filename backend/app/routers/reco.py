"""POST /api/reco/events and DELETE /api/reco/history. Member-scoped, never anonymous."""
from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.media_schemas import RecoEventBatch
from app.models import User, utcnow
from app.persistence import write_transaction
from app.security import CSRF_HEADER, CSRF_REJECTED_DETAIL, csrf_token_for, get_current_user, resolve_session_user
from app.services.rate_limit import rate_limiter
from app.services.reco.events import RecoEventService

MAX_BODY_BYTES = 32 * 1024
RATE_BUCKET = "reco_events"


def _member(request: Request, db: Session = Depends(get_db, scope="function")) -> User:
    """A cookie session only: sendBeacon sends no Basic or bearer credentials, and the CSRF token is bound to the cookie."""
    user = resolve_session_user(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def _small_body(request: Request) -> None:
    if len(await request.body()) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body is too large.")


def _within_rate(user: User = Depends(_member)) -> None:
    rate_limiter.check(RATE_BUCKET, f"user:{user.id}", user_id=user.id)


def post_reco_events(
    batch: RecoEventBatch, request: Request, user: User = Depends(_member), _size: None = Depends(_small_body), _rate: None = Depends(_within_rate),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    # sendBeacon cannot set X-CSRF-Token, so the pagehide flush carries the token in the body (same rule as /api/metrics/client).
    supplied = request.headers.get(CSRF_HEADER) or batch.csrf or ""
    if not hmac.compare_digest(supplied.encode(), csrf_token_for(request.cookies[settings.session_cookie_name]).encode()):
        raise HTTPException(status_code=403, detail=CSRF_REJECTED_DETAIL)
    with write_transaction(db, name="reco_events"):
        RecoEventService(db).record_client(user, batch, now=utcnow())
    return Response(status_code=204)


def delete_reco_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    """Clear recommendation history: the member's events and pool, their cached lists and profile. Playback progress and
    the hidden lists stay; they have their own controls."""
    with write_transaction(db, name="reco_history_clear"):
        RecoEventService(db).clear(current_user.id)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.post("/api/reco/events", status_code=204, response_class=Response)(post_reco_events)
    app.delete("/api/reco/history", status_code=204, response_class=Response)(delete_reco_history)
