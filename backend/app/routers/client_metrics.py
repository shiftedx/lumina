"""POST /api/metrics/client: anonymous loading samples from the web app."""
from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.media_schemas import ClientMetricsBatch
from app.models import User
from app.security import CSRF_HEADER, CSRF_REJECTED_DETAIL, csrf_token_for, resolve_session_user
from app.services import client_metrics
from app.services.rate_limit import rate_limiter

MAX_BODY_BYTES = 32 * 1024


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
    rate_limiter.check(client_metrics.RATE_BUCKET, f"user:{user.id}", user_id=user.id)


def post_client_metrics(
    batch: ClientMetricsBatch, request: Request, _user: User = Depends(_member), _size: None = Depends(_small_body), _rate: None = Depends(_within_rate),
) -> Response:
    # sendBeacon cannot set X-CSRF-Token, so the pagehide flush carries the token in the body.
    supplied = request.headers.get(CSRF_HEADER) or batch.csrf or ""
    if not hmac.compare_digest(supplied.encode(), csrf_token_for(request.cookies[settings.session_cookie_name]).encode()):
        raise HTTPException(status_code=403, detail=CSRF_REJECTED_DETAIL)
    client_metrics.record(batch.samples)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.post("/api/metrics/client", status_code=204, response_class=Response)(post_client_metrics)
