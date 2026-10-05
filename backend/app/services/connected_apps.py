"""Connected apps (ADR 0010): per-device, scoped, revocable bearer credentials for non-browser clients.

Resolution lives in app.security (resolve_device_token); this module mints, lists and revokes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import unquote

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import ConnectedApp, ConnectedAppScope
from app.models import AppSettings, DeviceToken, User
from app.persistence import write_transaction
from app.security import DEVICE_TOKEN_IDLE_DAYS, client_ip, resolve_device_token, session_digest, utcnow
from app.services.local_playback_sessions import sessions
from app.services import member_access, two_factor
from app.services.media_titles import parse_item_id
from app.services.yt_dlp_service import YtDlpService

audit_log = logging.getLogger("lumina.audit")


def to_connected_app(record: DeviceToken, owner_display_name: str | None = None) -> ConnectedApp:
    return ConnectedApp(
        id=record.id,
        user_id=record.user_id,
        owner_display_name=owner_display_name,
        kind=record.kind,
        scope=record.scope,
        device_name=record.device_name,
        client=record.client,
        client_version=record.client_version,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
    )


def create_agent_token(db: Session, user: User, name: str, scope: ConnectedAppScope) -> tuple[DeviceToken, str]:
    """Mint an agent token; the raw value is returned once and only its digest is stored."""
    device_name = name.strip()
    if not device_name:
        raise ValueError("Name the connected app.")
    token = secrets.token_urlsafe(32)
    record = DeviceToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        kind="agent",
        scope=scope,
        token_digest=session_digest(token),
        # Prefixed so a Jellyfin client's DeviceId can never collide with an agent row.
        device_id=f"agent:{uuid.uuid4()}",
        device_name=device_name,
    )
    with write_transaction(db, name="agent_token_create"):
        db.add(record)
        db.flush()
    audit_log.info("connected_app.create user=%s app=%s kind=agent scope=%s", user.id, record.id, scope)
    return record, token


def create_app_password(db: Session, user: User, name: str) -> tuple[DeviceToken, str]:
    """An app password (2.9.0): what a Jellyfin app or HTTP Basic client types instead of the account password, so a
    two-step account can still use apps that cannot ask for a code. Shown once; only its digest is stored."""
    label = name.strip()
    if not label:
        raise ValueError("Name the app, for example Infuse on the living-room Apple TV.")
    password = two_factor.random_code(5)
    record = DeviceToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        kind="app_password",
        scope="write",
        token_digest=session_digest(two_factor.normalize_code(password)),
        device_id=f"apppw:{uuid.uuid4()}",  # its own namespace: a client DeviceId can never collide with it
        device_name=label[:255],
    )
    with write_transaction(db, name="app_password_create"):
        db.add(record)
        db.flush()
    audit_log.info("connected_app.create user=%s app=%s kind=app_password", user.id, record.id)
    return record, password


def sign_out_all_apps(db: Session, user: User) -> int:
    """Revoke every Jellyfin and agent sign-in of the member; app passwords stay, so apps can sign in again with one."""
    with write_transaction(db, name="connected_apps_sign_out_all"):
        ids = [row.id for row in db.query(DeviceToken.id).filter(DeviceToken.user_id == user.id, DeviceToken.kind.in_(("jellyfin", "agent")))]
        if ids:
            db.query(DeviceToken).filter(DeviceToken.id.in_(ids)).delete(synchronize_session=False)
            db.flush()
    for app_id in ids:
        sessions.stop_where(user_id=user.id, device=app_id)
    audit_log.info("connected_app.sign_out_all user=%s count=%s", user.id, len(ids))
    return len(ids)


def list_connected_apps(db: Session, user: User) -> list[ConnectedApp]:
    """The caller's apps; an admin sees every member's, with an owner name."""
    # Idle rows are hidden here and deleted on their next use, not swept; add a sweep if the table grows.
    cutoff = utcnow() - timedelta(days=DEVICE_TOKEN_IDLE_DAYS)
    query = (
        db.query(DeviceToken, User.display_name)
        .join(User, User.id == DeviceToken.user_id)
        # App passwords are used only when an app signs in, so they never idle out.
        .filter(or_(DeviceToken.last_seen_at > cutoff, DeviceToken.kind == "app_password"))
    )
    is_admin = two_factor.owner_capable(db, user)
    if not is_admin:
        query = query.filter(DeviceToken.user_id == user.id)
    rows = query.order_by(DeviceToken.last_seen_at.desc(), DeviceToken.id).all()
    return [to_connected_app(record, owner if is_admin else None) for record, owner in rows]


def revoke_connected_app(db: Session, actor: User, app_id: str) -> None:
    """Delete one app; members revoke their own, admins anyone's. Invisible means LookupError (404)."""
    with write_transaction(db, name="connected_app_revoke"):
        record = db.get(DeviceToken, app_id, populate_existing=True)
        if record is None or (record.user_id != actor.id and not two_factor.owner_capable(db, actor)):
            raise LookupError(app_id)
        user_id = record.user_id  # Read before the delete expires the row
        # Revoking an app password signs out the apps that signed in with it.
        signed_in = [row.id for row in db.query(DeviceToken.id).filter(DeviceToken.app_password_id == app_id)] if record.kind == "app_password" else []
        db.delete(record)
        if signed_in:
            db.query(DeviceToken).filter(DeviceToken.id.in_(signed_in)).delete(synchronize_session=False)
        db.flush()
    for device in (app_id, *signed_in):
        sessions.stop_where(user_id=user_id, device=device)  # its HLS encodes end with it
    audit_log.info("connected_app.revoke actor=%s app=%s", actor.id, app_id)


# ---- Jellyfin clients ------------------------------------------------

_AUTH_SCHEMES = frozenset({"mediabrowser", "emby"})
# key="quoted value" or key=bare; values may be URL-encoded (Device="Dana%27s%20iPhone").
_AUTH_PARAM = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]*))')
_SERVER_ID_LABEL = b"lumina-jellyfin-server-id"


@dataclass(frozen=True)
class ClientAuth:
    """What a Jellyfin client said about itself; every field is untrusted input."""

    token: str | None = None
    client: str | None = None
    device: str | None = None
    device_id: str | None = None
    version: str | None = None


def _auth_params(value: str | None) -> dict[str, str]:
    scheme, _, rest = (value or "").strip().partition(" ")
    if scheme.lower() not in _AUTH_SCHEMES:
        return {}
    return {
        match.group(1).lower(): unquote(match.group(2) if match.group(2) is not None else match.group(3)).strip()
        for match in _AUTH_PARAM.finditer(rest)
    }


def parse_client_auth(request: Request) -> ClientAuth:
    """Token sources in order: Authorization, X-Emby-Authorization (MediaBrowser/Emby scheme),
    X-Emby-Token, X-MediaBrowser-Token, query api_key/apikey (any case). Cookies are never read."""
    params = {**_auth_params(request.headers.get("x-emby-authorization")), **_auth_params(request.headers.get("authorization"))}
    query = {key.lower(): value for key, value in request.query_params.multi_items()}
    token = (
        params.get("token")
        or request.headers.get("x-emby-token")
        or request.headers.get("x-mediabrowser-token")
        or query.get("api_key")
        or query.get("apikey")
    )
    return ClientAuth(
        token=(token or "").strip() or None,
        client=params.get("client") or None,
        device=params.get("device") or None,
        device_id=params.get("deviceid") or None,
        version=params.get("version") or None,
    )


def _clip(value: str | None, limit: int) -> str | None:
    return value[:limit] if value else None


def mint_device_token(
    db: Session, user: User, auth: ClientAuth, *, last_ip: str | None = None, app_password_id: str | None = None,
) -> tuple[DeviceToken, str]:
    """Sign a Jellyfin client in: one write-scoped token per (member, DeviceId); signing in again replaces it."""
    # agent: and apppw: are DeviceId namespaces of other rows; a sign-in must never overwrite one.
    if not auth.device_id or len(auth.device_id) > 255 or auth.device_id.lower().startswith(("agent:", "apppw:")):
        raise ValueError("The client did not send a usable DeviceId.")
    token = secrets.token_urlsafe(32)
    with write_transaction(db, name="device_token_mint"):
        record = (
            db.query(DeviceToken)
            .filter(DeviceToken.user_id == user.id, DeviceToken.device_id == auth.device_id)
            .populate_existing()
            .first()
        )
        if record is None:
            record = DeviceToken(id=str(uuid.uuid4()), user_id=user.id, device_id=auth.device_id)
            db.add(record)
        record.kind, record.scope = "jellyfin", "write"  # players report progress
        record.token_digest = session_digest(token)
        record.device_name = _clip(auth.device or auth.client, 255) or "Jellyfin app"
        record.client, record.client_version = _clip(auth.client, 120), _clip(auth.version, 64)
        record.last_seen_at, record.last_ip = utcnow(), last_ip
        record.app_password_id = app_password_id
        db.flush()
    audit_log.info("connected_app.sign_in user=%s app=%s kind=jellyfin", user.id, record.id)
    return record, token


def jellyfin_server_key(db: Session) -> bytes:
    """The 32-byte secret behind ServerId and image-tag HMACs; created on first use, never rotated."""
    stored = YtDlpService(db).get_app_settings().jellyfin_server_key
    if not stored:
        with write_transaction(db, name="jellyfin_server_key_create"):
            record = db.get(AppSettings, 1, populate_existing=True) or YtDlpService(db).ensure_app_settings()
            if not record.jellyfin_server_key:
                record.jellyfin_server_key = secrets.token_hex(32)
                db.flush()
            stored = record.jellyfin_server_key
    return bytes.fromhex(stored)


def jellyfin_server_id(db: Session) -> str:
    return hmac.new(jellyfin_server_key(db), _SERVER_ID_LABEL, hashlib.sha256).hexdigest()[:32]


def require_jellyfin_enabled(db: Session = Depends(get_db, scope="function")) -> None:
    """Every /jellyfin route 404s while the admin has the surface switched off."""
    if not YtDlpService(db).get_app_settings().jellyfin_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def jellyfin_user(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    _enabled: None = Depends(require_jellyfin_enabled),
) -> User:
    """Authenticate a /jellyfin request by its jellyfin device token.

    Returns a transient User snapshot, so no DB session is held while a route streams a file.
    A legacy ``{uid}`` path segment or ``userId`` query value must be the caller, else 404.
    """
    resolved = resolve_device_token(db, request, parse_client_auth(request).token, kind="jellyfin")
    if resolved is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    record, user = resolved
    claimed = [value for key, value in request.query_params.multi_items() if key.lower() == "userid"]
    if "uid" in request.path_params:
        claimed.append(request.path_params["uid"])
    # Every claim must be the caller: a route may read any one of repeated userId values.
    if any(parse_item_id(value) != parse_item_id(user.id) for value in claimed):
        raise HTTPException(status_code=404, detail="Not Found")
    request.state.connected_app_id = record.id
    snapshot = User(id=user.id, username=user.username, display_name=user.display_name, role=user.role, is_active=user.is_active)
    return member_access.carry_access(db, snapshot, user)  # ADR 0019: the Jellyfin API's queries take the literal clause
