"""Short-lived TV enrollment approved from an already authenticated native app.

This is deliberately separate from browser-to-phone mobile connect and from
Jellyfin Quick Connect.  A TV receives an opaque one-use app password only
after the member's existing native device explicitly approves its display code.
"""
from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import UTC, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DevicePairing, DeviceToken, User, utcnow
from app.persistence import write_transaction
from app.security import session_digest, validate_device_token_record
from app.services.connected_apps import jellyfin_server_id, jellyfin_user, new_app_password, parse_client_auth, require_jellyfin_enabled
from app.services.rate_limit import enforce_rate_limit

PREFIX = "/jellyfin/lumina/devicepairing"
PAIR_MINUTES = 5
POLL_INTERVAL_SECONDS = 3
MAX_PENDING_PER_DEVICE = 3
MAX_PENDING = 64
PRUNE_AFTER = timedelta(hours=1)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_name: str = Field(min_length=1, max_length=120)


class SecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=36, max_length=36)
    device_secret: str = Field(min_length=32, max_length=512, repr=False)


class CodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=8, max_length=8)


def _expires_at(value) -> str:  # noqa: ANN001
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _credential_digest(user: User | None) -> str | None:
    return session_digest(user.password_hash or f"managed:{user.id}") if user and user.is_active else None


def _state(record: DevicePairing) -> str:
    if record.claimed_at is not None:
        return "consumed"
    if record.denied_at is not None:
        return "denied"
    if record.expires_at <= utcnow():
        return "expired"
    return "approved" if record.approved_at is not None else "pending"


def _code_digest(code: str) -> str:
    normalized = code.strip().upper()
    if len(normalized) != 8 or any(char not in _CODE_ALPHABET for char in normalized):
        raise HTTPException(status_code=422, detail="Enter the eight-character code shown on the TV.")
    return session_digest(normalized)


def _tv_device_id(request: Request) -> str:
    """Require the initiating TV to keep presenting its MediaBrowser DeviceId."""
    auth = parse_client_auth(request)
    device_id = auth.device_id
    if not device_id or len(device_id) > 255 or device_id.lower().startswith(("agent:", "apppw:")):
        raise HTTPException(status_code=400, detail="The TV did not send a usable DeviceId.")
    return device_id


def _pair_for_tv(db: Session, body: SecretRequest, device_id: str) -> DevicePairing:
    record = db.get(DevicePairing, body.id)
    if record is None or record.device_id != device_id or not hmac.compare_digest(record.device_secret_digest, session_digest(body.device_secret)):
        raise HTTPException(status_code=401, detail="This TV enrollment request is not available.")
    return record


def _pair_for_phone(db: Session, body: CodeRequest) -> DevicePairing:
    record = db.query(DevicePairing).filter(DevicePairing.code_digest == _code_digest(body.code)).first()
    if record is None:
        raise HTTPException(status_code=404, detail="TV enrollment request not found")
    return record


def _approval_app(request: Request, db: Session, user: User) -> DeviceToken:
    """The approving phone must retain a write-scoped Jellyfin device token."""
    app_id = getattr(request.state, "connected_app_id", None)
    record = db.get(DeviceToken, app_id) if app_id else None
    if record is None or record.user_id != user.id or record.kind != "jellyfin" or record.scope != "write":
        raise HTTPException(status_code=403, detail="A write-scoped native app is required.")
    return record


def _response(response: Response, record: DevicePairing) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"status": _state(record), "expires_at": _expires_at(record.expires_at)}


def start(
    body: StartRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    _enabled=Depends(require_jellyfin_enabled),  # noqa: ANN001
) -> dict:
    enforce_rate_limit("device_pair_start", request)
    device_id = _tv_device_id(request)
    device_name = body.device_name.strip()
    if not device_name:
        raise HTTPException(status_code=422, detail="Name the TV.")
    now = utcnow()
    raw_secret = secrets.token_urlsafe(32)
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    record = DevicePairing(
        id=str(uuid.uuid4()),
        code_digest=session_digest(code),
        device_secret_digest=session_digest(raw_secret),
        device_id=device_id,
        device_name=device_name,
        expires_at=now + timedelta(minutes=PAIR_MINUTES),
    )
    with write_transaction(db, name="device_pair_start"):
        db.query(DevicePairing).filter(DevicePairing.expires_at < now - PRUNE_AFTER).delete(synchronize_session=False)
        pending = (
            db.query(DevicePairing.id)
            .filter(
                DevicePairing.device_id == device_id,
                DevicePairing.expires_at > now,
                DevicePairing.denied_at.is_(None),
                DevicePairing.claimed_at.is_(None),
            )
            .count()
        )
        if pending >= MAX_PENDING_PER_DEVICE:
            raise HTTPException(status_code=429, detail="Too many pending TV enrollment requests.")
        total_pending = (
            db.query(DevicePairing.id)
            .filter(DevicePairing.expires_at > now, DevicePairing.denied_at.is_(None), DevicePairing.claimed_at.is_(None))
            .count()
        )
        if total_pending >= MAX_PENDING:
            raise HTTPException(status_code=429, detail="TV enrollment is temporarily busy.")
        while db.query(DevicePairing.id).filter(DevicePairing.code_digest == record.code_digest).first() is not None:
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            record.code_digest = session_digest(code)
        db.add(record)
        db.flush()
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": record.id,
        "device_secret": raw_secret,
        "code": code,
        "expires_at": _expires_at(record.expires_at),
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "server_id": jellyfin_server_id(db),
    }


def status(
    body: SecretRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    _enabled=Depends(require_jellyfin_enabled),  # noqa: ANN001
) -> dict:
    enforce_rate_limit("device_pair_status", request)
    return _response(response, _pair_for_tv(db, body, _tv_device_id(request)))


def inspect(
    body: CodeRequest,
    request: Request,
    response: Response,
    user: User = Depends(jellyfin_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    enforce_rate_limit("device_pair_manage", request, user_id=user.id)
    record = _pair_for_phone(db, body)
    response.headers["Cache-Control"] = "no-store"
    return {
        "code": body.code.strip().upper(),
        "device_name": record.device_name,
        "expires_at": _expires_at(record.expires_at),
        "server_id": jellyfin_server_id(db),
        "status": _state(record),
    }


def approve(
    body: CodeRequest,
    request: Request,
    response: Response,
    user: User = Depends(jellyfin_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    enforce_rate_limit("device_pair_manage", request, user_id=user.id)
    approval_app = _approval_app(request, db, user)
    with write_transaction(db, name="device_pair_approve"):
        record = _pair_for_phone(db, body)
        state = _state(record)
        if state == "approved" and record.approved_user_id == user.id and record.approver_app_id == approval_app.id:
            pass  # retrying the same explicit approval is safe
        elif state != "pending":
            raise HTTPException(status_code=409, detail="This TV enrollment request is no longer pending.")
        else:
            credential_digest = _credential_digest(db.get(User, user.id, populate_existing=True))
            if credential_digest is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            record.approved_user_id = user.id
            record.approver_app_id = approval_app.id
            record.approver_credential_digest = credential_digest
            record.approved_at = utcnow()
            db.flush()
    response.headers["Cache-Control"] = "no-store"
    return {"status": "approved"}


def deny(
    body: CodeRequest,
    request: Request,
    response: Response,
    user: User = Depends(jellyfin_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    enforce_rate_limit("device_pair_manage", request, user_id=user.id)
    approval_app = _approval_app(request, db, user)
    with write_transaction(db, name="device_pair_deny"):
        record = _pair_for_phone(db, body)
        state = _state(record)
        if state == "approved" and (record.approved_user_id != user.id or record.approver_app_id != approval_app.id):
            raise HTTPException(status_code=409, detail="This TV enrollment request belongs to another phone.")
        if state not in {"pending", "approved", "denied"}:
            raise HTTPException(status_code=409, detail="This TV enrollment request is no longer available.")
        record.denied_at = record.denied_at or utcnow()
        db.flush()
    response.headers["Cache-Control"] = "no-store"
    return {"status": "denied"}


def claim(
    body: SecretRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    _enabled=Depends(require_jellyfin_enabled),  # noqa: ANN001
) -> dict:
    enforce_rate_limit("device_pair_claim", request)
    device_id = _tv_device_id(request)
    target = None
    app = None
    password = None
    rejected_status = None
    with write_transaction(db, name="device_pair_claim"):
        record = _pair_for_tv(db, body, device_id)
        state = _state(record)
        if state != "approved":
            rejected_status = state
        else:
            target = db.get(User, record.approved_user_id, populate_existing=True)
            approver = db.get(DeviceToken, record.approver_app_id, populate_existing=True)
            parent = db.get(DeviceToken, approver.app_password_id) if approver and approver.app_password_id else approver
            valid_approver = validate_device_token_record(db, request, (approver, target)) if approver and target else None
            if (
                target is None
                or _credential_digest(target) != record.approver_credential_digest
                or approver is None
                or approver.kind != "jellyfin"
                or approver.user_id != target.id
                or approver.scope != "write"
                or valid_approver is None
                or parent is None
                or parent.user_id != target.id
                or (approver.app_password_id is not None and parent.kind != "app_password")
            ):
                rejected_status = "unavailable"
            else:
                app, password = new_app_password(target, record.device_name)
                record.claimed_at = utcnow()
                db.add(app)
                db.flush()
    if rejected_status is not None:
        if rejected_status == "expired":
            raise HTTPException(status_code=410, detail="This TV enrollment request expired.")
        if rejected_status == "denied":
            raise HTTPException(status_code=409, detail="This TV enrollment request was denied.")
        if rejected_status == "pending":
            raise HTTPException(status_code=409, detail="Approve this TV on your phone first.")
        raise HTTPException(status_code=409, detail="This TV enrollment request is no longer available.")
    assert target is not None and app is not None and password is not None
    response.headers["Cache-Control"] = "no-store"
    return {"username": target.username, "password": password}


def register(app: FastAPI) -> None:
    # Root-form Jellyfin clients reach these through JellyfinPathMiddleware,
    # which canonicalizes the route to this lowercase private mount.
    app.post(PREFIX + "/start", status_code=201)(start)
    app.post(PREFIX + "/status")(status)
    app.post(PREFIX + "/inspect")(inspect)
    app.post(PREFIX + "/approve")(approve)
    app.post(PREFIX + "/deny")(deny)
    app.post(PREFIX + "/claim")(claim)
