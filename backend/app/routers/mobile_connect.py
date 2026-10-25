"""One-use QR pairing for Lumina's native mobile clients."""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, timedelta
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppSettings, MobilePairing, User, utcnow
from app.persistence import write_transaction
from app.security import get_admin_user, get_current_user, require_browser_session, session_digest
from app.services import email_layout, qr, two_factor
from app.services.connected_apps import new_app_password
from app.services.rate_limit import enforce_rate_limit
from app.services.requests import notify

URL = "/api/mobile-connect"
logger = logging.getLogger(__name__)
audit_log = logging.getLogger("lumina.audit")
PAIR_MINUTES = 5
SHARE_MINUTES = 15


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_url: str = Field(min_length=1, max_length=512)
    user_id: str | None = Field(default=None, max_length=36)
    share: bool = False
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
    replace_id: str | None = Field(default=None, max_length=36)


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=512, repr=False)
    name: str = Field(min_length=1, max_length=120)


def _origin(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Use a server address such as https://lumina.example.com.") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0 or "@" in parsed.netloc or parsed.netloc.endswith(":")
            or parsed.path not in ("", "/") or "?" in value or "#" in value or "\\" in value
            or any(ord(char) < 32 or char.isspace() for char in value)):
        raise ValueError("Use a server address such as https://lumina.example.com.")
    host = parsed.hostname.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return f"{parsed.scheme.lower()}://{netloc}"


def _payload(server_url: str, token: str) -> str:
    return json.dumps({"type": "lumina-pair", "version": 1, "server_url": server_url, "token": token}, separators=(",", ":"))


def _credential_digest(user: User | None) -> str | None:
    return session_digest(user.password_hash or f"managed:{user.id}") if user and user.is_active else None


def _expires_at(value):  # noqa: ANN001
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _status(record: MobilePairing) -> str:
    if record.revoked_at is not None:
        return "revoked"
    if record.used_at is not None:
        return "used"
    return "expired" if record.expires_at <= utcnow() else "pending"


def _email(db: Session, address: str, payload: str) -> bool:
    smtp = notify.Smtp.of(db.get(AppSettings, 1))
    if smtp is None:
        return False
    link = f"lumina://pair#{quote(payload, safe='')}"
    subject = "Connect Lumina"
    text, html = email_layout.compose(
        subject=subject, preheader="Open Lumina or scan the code to connect this device.", eyebrow="Mobile connect", heading="Connect a device.", status="connected",
        paragraphs=("Open Lumina on this phone, or scan the code in Lumina on another device. This code expires in 15 minutes.",),
        button=("Open Lumina", link), fineprint=("This code connects one device. Keep it private.",),
        why="You're getting this because a Lumina owner shared mobile access with you.", image_cid="mobile-pair-qr",
    )
    try:
        notify.send(smtp, address.strip().lower(), subject, text, html, attachment=("lumina-mobile-connect.png", qr.png(payload), "image", "png"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mobile pairing email failed: %s", type(exc).__name__)
        return False
    return True


def status(response: Response, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:  # noqa: ARG001
    require_browser_session(request)
    response.headers["Cache-Control"] = "no-store"
    settings = db.get(AppSettings, 1)
    return {"enabled": bool(settings and settings.jellyfin_enabled), "email_configured": notify.Smtp.of(settings) is not None}


def create(body: PairRequest, request: Request, response: Response, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    require_browser_session(request)
    enforce_rate_limit("mobile_pair_create", request, user_id=current_user.id)
    if not (settings := db.get(AppSettings, 1)) or not settings.jellyfin_enabled:
        raise HTTPException(status_code=409, detail="Ask a vault owner to enable mobile apps.")
    try:
        server_url = _origin(body.server_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target_id = body.user_id or current_user.id
    if body.share:
        if not two_factor.owner_capable(db, current_user):
            raise HTTPException(status_code=403, detail="Vault owner access required")
    elif target_id != current_user.id or body.email:
        raise HTTPException(status_code=403, detail="You can connect only your own mobile app.")
    target = db.get(User, target_id)
    target_digest, issuer_digest = _credential_digest(target), _credential_digest(current_user)
    if target_digest is None or issuer_digest is None:
        raise HTTPException(status_code=422, detail="This member cannot connect a mobile app yet.")
    raw = secrets.token_urlsafe(32)
    record = MobilePairing(
        id=str(uuid.uuid4()), token_digest=session_digest(raw), user_id=target_id, issued_by=current_user.id, server_url=server_url,
        user_credential_digest=target_digest, issuer_credential_digest=issuer_digest, shared=body.share,
        expires_at=utcnow() + timedelta(minutes=SHARE_MINUTES if body.share else PAIR_MINUTES),
    )
    with write_transaction(db, name="mobile_pair_create"):
        if body.replace_id:
            prior = db.get(MobilePairing, body.replace_id, populate_existing=True)
            if prior is None or prior.issued_by != current_user.id:
                raise HTTPException(status_code=404, detail="Mobile connection not found")
            if _status(prior) == "pending":
                prior.revoked_at = utcnow()
        db.add(record)
        db.flush()
    audit_log.info("mobile_pair.create user=%s pair=%s shared=%s", current_user.id, record.id, body.share)
    payload = _payload(server_url, raw)
    email_sent = _email(db, body.email, payload) if body.share and body.email else None
    response.headers["Cache-Control"] = "no-store"
    size, path = qr.svg_path(payload)
    return {"id": record.id, "server_url": server_url, "expires_at": _expires_at(record.expires_at), "qr_payload": payload, "qr_size": size, "qr_path": path, "email_sent": email_sent}


def get_pair(pair_id: str, request: Request, response: Response, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    require_browser_session(request)
    record = db.get(MobilePairing, pair_id)
    if record is None or record.issued_by != current_user.id:
        raise HTTPException(status_code=404, detail="Mobile connection not found")
    response.headers["Cache-Control"] = "no-store"
    return {"status": _status(record)}


def revoke(pair_id: str, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    require_browser_session(request)
    with write_transaction(db, name="mobile_pair_revoke"):
        record = db.get(MobilePairing, pair_id, populate_existing=True)
        if record is None or record.issued_by != current_user.id:
            raise HTTPException(status_code=404, detail="Mobile connection not found")
        if _status(record) == "pending":
            record.revoked_at = utcnow()
            db.flush()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def enable(request: Request, response: Response, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:  # noqa: ARG001
    require_browser_session(request)
    with write_transaction(db, name="mobile_pair_enable"):
        settings = db.get(AppSettings, 1)
        if settings is None:
            settings = AppSettings(id=1)
            db.add(settings)
        settings.jellyfin_enabled = True
        db.flush()
    audit_log.info("mobile_pair.enable user=%s", current_user.id)
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": True, "email_configured": notify.Smtp.of(settings) is not None}


def claim(body: ClaimRequest, request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> dict:
    enforce_rate_limit("mobile_pair_claim", request)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name the mobile app.")
    unavailable = False
    target = None
    app = None
    password = None
    server_url = None
    with write_transaction(db, name="mobile_pair_claim"):
        settings = db.get(AppSettings, 1)
        record = db.query(MobilePairing).filter(MobilePairing.token_digest == session_digest(body.token)).populate_existing().first()
        target = db.get(User, record.user_id) if record else None
        issuer = db.get(User, record.issued_by) if record else None
        if not settings or not settings.jellyfin_enabled or not record or _status(record) != "pending" or not target or not issuer:
            unavailable = True
        elif (record.shared and not two_factor.owner_capable(db, issuer)) or _credential_digest(target) != record.user_credential_digest or _credential_digest(issuer) != record.issuer_credential_digest:
            record.revoked_at = utcnow()
            db.flush()
            unavailable = True
        else:
            app, password = new_app_password(target, body.name)
            record.used_at = utcnow()
            server_url = record.server_url
            db.add(app)
            db.flush()
    if unavailable:
        raise HTTPException(status_code=401, detail="This mobile connection is no longer available.")
    assert target is not None and app is not None and password is not None and server_url is not None
    audit_log.info("connected_app.create user=%s app=%s kind=app_password", target.id, app.id)
    response.headers["Cache-Control"] = "no-store"
    return {"username": target.username, "password": password, "server_url": server_url}


def register(app: FastAPI) -> None:
    app.get(URL)(status)
    app.post(URL, status_code=201)(create)
    app.get(URL + "/{pair_id}")(get_pair)
    app.delete(URL + "/{pair_id}", status_code=204)(revoke)
    app.post(URL + "/enable")(enable)
    app.post(URL + "/claim")(claim)
