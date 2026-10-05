"""Two-step verification settings: a member's own (Settings → You) and an owner's reset of a member (Settings → Members)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.persistence import write_transaction
from app.schemas import (
    RecoveryCodesResponse, TwoFactorCodeRequest, TwoFactorConfirmRequest, TwoFactorPasswordRequest, TwoFactorSetupResponse,
    TwoFactorStatusResponse,
)
from app.security import audit_log, get_current_user, get_owner_session_user, require_browser_session, verify_password
from app.services import qr, two_factor
from app.services.rate_limit import enforce_rate_limit

URL = "/api/me/two-factor"
WRONG = "The password or code is not right."


def _guarded(request: Request, user: User) -> None:
    require_browser_session(request)
    enforce_rate_limit("session_login", request, user_id=user.id)  # every password or code guess here is metered


def _prove(db: Session, user: User, payload: TwoFactorConfirmRequest) -> None:
    """Password and a second factor: a 6-digit code, or else a recovery code (which is then spent)."""
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=403, detail=WRONG)
    code = payload.code.strip()
    is_totp = code.isdigit() and len(code) == two_factor.DIGITS
    if not two_factor.verify(db, user, code=code if is_totp else None, recovery_code=None if is_totp else code):
        raise HTTPException(status_code=403, detail=WRONG)


def status(db: Session = Depends(get_db, scope="function"), current_user: User = Depends(get_current_user)) -> TwoFactorStatusResponse:
    return TwoFactorStatusResponse(
        enabled=two_factor.enabled(current_user),
        recovery_codes_left=len(current_user.totp_recovery or []) if two_factor.enabled(current_user) else 0,
        required=current_user.role == "admin" and two_factor.owners_required(db),
    )


def setup(
    payload: TwoFactorPasswordRequest, request: Request, response: Response,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> TwoFactorSetupResponse:
    _guarded(request, current_user)
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(status_code=403, detail="That password is not right.")
    try:
        secret = two_factor.begin(db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    uri = two_factor.otpauth_uri(current_user.username, secret)
    size, path = qr.svg_path(uri)
    response.headers["Cache-Control"] = "no-store"
    return TwoFactorSetupResponse(secret=two_factor.b32(secret), otpauth_uri=uri, qr_size=size, qr_path=path)


def enable(
    payload: TwoFactorCodeRequest, request: Request, response: Response,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> RecoveryCodesResponse:
    _guarded(request, current_user)
    codes = two_factor.confirm(db, current_user, payload.code)
    if codes is None:
        raise HTTPException(status_code=400, detail="That code didn't work. Check the time on your phone and try the newest code.")
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesResponse(recovery_codes=codes)


def disable(
    payload: TwoFactorConfirmRequest, request: Request,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    _guarded(request, current_user)
    if not two_factor.enabled(current_user):
        return Response(status_code=204)
    if current_user.role == "admin" and two_factor.owners_required(db):
        raise HTTPException(status_code=409, detail="This household requires two-step verification for vault owners.")
    _prove(db, current_user, payload)
    two_factor.clear(db, current_user.id)
    audit_log.info("two_factor.disable user=%s", current_user.id)
    return Response(status_code=204)


def regenerate(
    payload: TwoFactorConfirmRequest, request: Request, response: Response,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> RecoveryCodesResponse:
    _guarded(request, current_user)
    if not two_factor.enabled(current_user):
        raise HTTPException(status_code=409, detail="Two-step verification is off.")
    _prove(db, current_user, payload)
    with write_transaction(db, name="two_factor_recovery_codes"):
        record = db.get(User, current_user.id, populate_existing=True)
        codes = two_factor.new_recovery_codes(record)
        db.flush()
    audit_log.info("two_factor.recovery_regenerate user=%s", current_user.id)
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesResponse(recovery_codes=codes)


def admin_reset(user_id: str, current_user: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> Response:
    """An owner turns a member's two-step verification off (a lost phone): they sign in with their password again."""
    if user_id == current_user.id:  # their own goes off with password and code (Settings → You), never this way
        raise HTTPException(status_code=409, detail="Turn your own two-step verification off under Settings → You.")
    try:
        two_factor.clear(db, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    audit_log.info("two_factor.reset actor=%s user=%s", current_user.id, user_id)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.get(URL, response_model=TwoFactorStatusResponse)(status)
    app.post(URL + "/setup", response_model=TwoFactorSetupResponse)(setup)
    app.post(URL + "/enable", response_model=RecoveryCodesResponse)(enable)
    app.post(URL + "/disable", status_code=204)(disable)
    app.post(URL + "/recovery-codes", response_model=RecoveryCodesResponse)(regenerate)
    app.delete("/api/admin/users/{user_id}/two-factor", status_code=204)(admin_reset)
