"""Browser-only external login endpoints; all callback input is untrusted."""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import check_sign_in_limits
from app.services import oauth, two_factor
from app.services.rate_limit import rate_limit_dependency, resolve_client_key

logger = logging.getLogger("lumina.audit")
URL = "/api/session/oauth"


class StartRequest(BaseModel):
    remember_on_device: bool = False


def configured(provider_id: str) -> oauth.Provider:
    provider = oauth.provider_for(provider_id)
    if provider is None:
        raise HTTPException(404, "Identity provider is not configured")
    return provider


def list_providers() -> list[dict[str, str]]:
    return [{"id": provider.id, "name": provider.name} for provider in oauth.providers()]


def start(provider_id: str, payload: StartRequest, response: Response) -> dict[str, str]:
    provider = configured(provider_id)
    try:
        state, binding, attempt = oauth.new_attempt(provider, payload.remember_on_device)
        url = oauth.authorization_url(provider, state, attempt)
    except Exception:
        # Provider exceptions can contain tokens, client secrets or private endpoint details.
        raise HTTPException(503, "Identity provider is unavailable. Try again or use your Lumina password.") from None
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(oauth.browser_cookie(provider), binding, max_age=oauth.ATTEMPT_SECONDS,
                        path="/", httponly=True,
                        secure=settings.remote_https_enabled, samesite="none" if provider.apple else "lax")
    return {"authorization_url": url}


def complete(provider_id: str, request: Request, params: dict[str, str], db: Session) -> RedirectResponse:
    provider = configured(provider_id)
    response = RedirectResponse(settings.resolved_frontend_public_url + "/#oauth-error", status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(oauth.browser_cookie(provider), path="/",
                           secure=settings.remote_https_enabled, samesite="none" if provider.apple else "lax")
    try:
        attempt = oauth.consume(provider, params.get("state", ""), request.cookies.get(oauth.browser_cookie(provider), ""))
        if params.get("error") or not params.get("code"):
            return response
        subject = oauth.exchange(provider, attempt, params["code"])
        user_id = provider.subjects.get(subject)
        user = db.get(User, user_id) if user_id else None
        if user is None or not user.is_active:
            return response
    except Exception:
        logger.warning("login.oauth_refused provider=%s", provider.id)
        return response
    check_sign_in_limits(request, user.username)
    if two_factor.enabled(user):
        challenge = two_factor.create_challenge(user, resolve_client_key(request), remember_on_device=attempt.remember)
        response.headers["location"] = settings.resolved_frontend_public_url + "/#oauth-two-factor=" + quote(challenge, safe="")
    else:
        from app.main import _complete_sign_in
        _complete_sign_in(db, request, response, user, remember_on_device=attempt.remember)
        response.headers["location"] = settings.resolved_frontend_public_url + "/"
    logger.info("login.oauth_verified provider=%s user=%s", provider.id, user.id)
    return response


def callback(provider_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> RedirectResponse:
    if configured(provider_id).apple:
        raise HTTPException(405, "This provider requires a form-post callback")
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise HTTPException(400, "Invalid callback parameters")
    return complete(provider_id, request, dict(request.query_params), db)


async def form_callback(provider_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> RedirectResponse:
    if not configured(provider_id).apple:
        raise HTTPException(405, "This provider requires a query callback")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected a form callback")
    body = await request.body()
    if len(body) > 16384:
        raise HTTPException(413, "Callback too large")
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=12)
        if any(len(items) != 1 for items in values.values()):
            raise ValueError("Repeated callback parameters")
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid callback parameters") from None
    return await run_in_threadpool(complete, provider_id, request, {key: items[0] for key, items in values.items()}, db)


def register(app: FastAPI) -> None:
    app.get(URL + "/providers")(list_providers)
    limited = [Depends(rate_limit_dependency("session_login"))]
    app.post(URL + "/{provider_id}/start", dependencies=limited)(start)
    app.get(URL + "/{provider_id}/callback", dependencies=limited)(callback)
    app.post(URL + "/{provider_id}/callback", dependencies=limited)(form_callback)
