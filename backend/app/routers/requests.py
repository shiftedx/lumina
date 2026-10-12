"""Requests API (requests spec, BE-B): member requests, quota and notifications; admin decisions, servers, settings, policies.

Members only ever see their own requests (``scope=mine``); everything else here that is not a member's own action is
admin-only server-side. Sonarr/Radarr API keys and the SMTP password are write-only: never returned or logged.
"""
from __future__ import annotations

import math
import re
import uuid
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ArrServer, MediaRequest, RequestPolicy, User, UserSettings, utcnow
from app.persistence import write_transaction
from app.security import get_admin_user, get_current_user
from app.services.local_ai import validate_endpoint_url
from app.services.rate_limit import enforce_rate_limit
from app.services.requests import arr, engine, notify
from app.services.user_settings import UserSettingsService
from app.services.yt_dlp_service import YtDlpService

URL = "/api/requests"
ADMIN = "/api/admin/requests"
PAGE_SIZE = 50
Kind = Literal["movie", "show", "anime"]
Seasons = list[int] | Literal["all"]
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_seasons(cls: type, value: Any) -> Any:  # noqa: ARG001
    if isinstance(value, list):
        if not value or len(value) > 100 or any(n < 0 or n > 1000 for n in value):
            raise ValueError("Choose between 1 and 100 season numbers.")
        return sorted(set(value))
    return value


def _check_url(cls: type, value: str | None) -> str | None:  # noqa: ARG001
    if value is None:
        return None
    value = validate_endpoint_url(value)
    if not value:
        raise ValueError("Enter the server's address, like http://sonarr.local:8989")
    return value


class CreateBody(_Body):
    kind: Kind
    tmdb_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    tvdb_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    anilist_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    media_type: Literal["movie", "tv"] | None = None  # anime only: an anime film goes to Radarr; default "tv"
    seasons: Seasons | None = None
    language: Literal["dub", "sub"] | None = None

    _seasons = field_validator("seasons")(_check_seasons)


class ApproveBody(_Body):
    seasons: Seasons | None = None
    language: Literal["dub", "sub"] | None = None

    _seasons = field_validator("seasons")(_check_seasons)


class DeclineBody(_Body):
    reason: str | None = Field(default=None, max_length=500)


class NotificationsBody(_Body):
    email: str | None = Field(default=None, max_length=320)
    enabled: bool

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = (value or "").strip() or None
        if value is not None and not EMAIL.match(value):
            raise ValueError("Enter an email address like name@example.com.")
        return value


class PathMapping(_Body):
    remote: str = Field(min_length=1, max_length=1024, pattern=r"^/")
    local: str = Field(min_length=1, max_length=1024, pattern=r"^/")


class ServerBody(_Body):
    """POST needs kind, name, base_url and api_key; PUT takes any subset. An omitted api_key keeps the stored one."""

    kind: Literal["sonarr", "radarr"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=256)
    root_folder: str | None = Field(default=None, max_length=1024)
    quality_profile_id: int | None = None
    anime_root_folder: str | None = Field(default=None, max_length=1024)
    anime_quality_profile_id: int | None = None
    dub_profile_id: int | None = None
    sub_profile_id: int | None = None
    path_mappings: list[PathMapping] | None = Field(default=None, max_length=20)
    enabled: bool | None = None

    _url = field_validator("base_url")(_check_url)


class ServerTestBody(_Body):
    kind: Literal["sonarr", "radarr"]
    base_url: str = Field(max_length=2048)
    api_key: str | None = Field(default=None, max_length=256)
    id: str | None = Field(default=None, max_length=36)

    _url = field_validator("base_url")(_check_url)


class SmtpBody(_Body):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] | None = None
    username: str | None = Field(default=None, max_length=255)
    sender: str | None = Field(default=None, alias="from", max_length=320)
    password: str | None = Field(default=None, max_length=1024)


class SettingsBody(_Body):
    requests_enabled: bool | None = None
    smtp: SmtpBody | None = None


class SmtpTestBody(_Body):
    to: str = Field(max_length=320, pattern=EMAIL.pattern)


class PolicyBody(_Body):
    kind: Kind
    can_request: bool
    auto_approve: bool
    quota_count: int | None = Field(default=None, ge=0, le=10_000)
    quota_days: int | None = Field(default=None, ge=1, le=3650)


class PoliciesBody(_Body):
    user_id: str | None = Field(default=None, max_length=36)
    policies: list[PolicyBody] = Field(max_length=3)


def _fail(error: engine.RequestError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content={"detail": error.code})


def _load(db: Session, request_id: str) -> MediaRequest:
    req = db.get(MediaRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


def _one(db: Session, req: MediaRequest, viewer: User | None = None) -> dict[str, Any]:
    db.refresh(req)
    return engine.serialize(db, [req], viewer)[0]


# ---- Member -----------------------------------------------------------------------------------------------------

def create_request(body: CreateBody, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    enforce_rate_limit("media_request", request, user_id=user.id)
    try:
        req, created = engine.create(db, user, body.model_dump(exclude_none=True))
    except engine.RequestError as error:
        return _fail(error)
    return JSONResponse(status_code=201 if created else 200, content=_one(db, req, user))


def list_requests(
    scope: Literal["mine", "all"] = "mine", status: str | None = Query(default=None, max_length=24), page: int = Query(default=1, ge=1, le=10_000),
    user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if scope == "all" and user.role != "admin":
        raise HTTPException(status_code=403, detail="not_allowed")
    where = [] if scope == "all" else [MediaRequest.requested_by == user.id]
    counts = {state: 0 for state in engine.STATES}
    counts.update(dict(db.execute(select(MediaRequest.status, func.count()).where(*where).group_by(MediaRequest.status)).all()))
    if status:
        where.append(MediaRequest.status == status)
    total = int(db.scalar(select(func.count()).select_from(MediaRequest).where(*where)) or 0)
    rows = list(db.scalars(select(MediaRequest).where(*where).order_by(MediaRequest.created_at.desc(), MediaRequest.id)
                           .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
    return {"items": engine.serialize(db, rows, user), "page": page, "total_pages": max(1, math.ceil(total / PAGE_SIZE)), "counts": counts}


def get_quota(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return {"quotas": [engine.quota(db, user, kind) for kind in engine.KINDS]}


def approve_request(request_id: str, body: ApproveBody | None = None, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    req = _load(db, request_id)
    try:
        engine.approve(db, admin, req, body.seasons if body else None, body.language if body else None)
    except engine.RequestError as error:
        return _fail(error)
    return _one(db, req)


def decline_request(request_id: str, body: DeclineBody | None = None, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    req = _load(db, request_id)
    try:
        engine.decline(db, admin, req, body.reason if body else None)
    except engine.RequestError as error:
        return _fail(error)
    return _one(db, req)


def retry_request(request_id: str, db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    req = _load(db, request_id)
    try:
        engine.retry(db, req)
    except engine.RequestError as error:
        return _fail(error)
    return _one(db, req)


def cancel_request(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    req = db.get(MediaRequest, request_id)
    if req is None or (user.role != "admin" and req.requested_by != user.id):  # never confirm another member's request exists
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        engine.cancel(db, user, req)
    except engine.RequestError as error:
        return _fail(error)
    return Response(status_code=204)


def get_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    record = db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()
    return {"email": record.notify_email if record else None, "enabled": bool(record.notify_requests) if record else True}


def put_notifications(body: NotificationsBody, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    with write_transaction(db, name="request_notifications"):
        record = UserSettingsService(db).ensure_for_user(user)
        record.notify_email, record.notify_requests = body.email, body.enabled
    return {"email": record.notify_email, "enabled": record.notify_requests}


# ---- Admin: servers ---------------------------------------------------------------------------------------------

SERVER_FIELDS = ("id", "kind", "name", "base_url", "root_folder", "quality_profile_id", "anime_root_folder", "anime_quality_profile_id",
                 "dub_profile_id", "sub_profile_id", "path_mappings", "enabled", "last_ok_at", "last_error")


def serialize_server(server: ArrServer) -> dict[str, Any]:
    out = {field: getattr(server, field) for field in SERVER_FIELDS}
    out["last_ok_at"] = server.last_ok_at.isoformat() + "Z" if server.last_ok_at else None
    out["path_mappings"] = list(server.path_mappings or [])
    out["api_key_set"] = bool(server.api_key)
    return out


def _server(db: Session, server_id: str) -> ArrServer:
    server = db.get(ArrServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def list_servers(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return {"servers": [serialize_server(s) for s in db.scalars(select(ArrServer).order_by(ArrServer.created_at))]}


def _apply(server: ArrServer, body: ServerBody) -> None:
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "api_key":
            if value:  # omitted or "" keeps the stored key
                server.api_key = value.strip()
        elif field in ("kind", "name", "base_url", "enabled") and value is None:
            continue
        else:
            setattr(server, field, value)


def create_server(body: ServerBody, db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    if not (body.kind and body.name and body.base_url and body.api_key):
        raise HTTPException(status_code=422, detail="kind, name, base_url and api_key are required")
    with write_transaction(db, name="arr_server_create"):
        server = ArrServer(id=str(uuid.uuid4()), kind=body.kind, name=body.name, base_url=body.base_url, path_mappings=[], enabled=True)
        _apply(server, body)
        db.add(server)
    return JSONResponse(status_code=201, content=serialize_server(server))


def update_server(server_id: str, body: ServerBody, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    server = _server(db, server_id)
    if body.base_url is not None and body.base_url != server.base_url and not body.api_key:
        raise HTTPException(status_code=400, detail="api_key_required")  # the stored key never follows to another address
    with write_transaction(db, name="arr_server_update"):
        _apply(server, body)
    return serialize_server(server)


def delete_server(server_id: str, db: Session = Depends(get_db, scope="function")) -> Response:
    server = _server(db, server_id)
    with write_transaction(db, name="arr_server_delete"):
        db.delete(server)
    return Response(status_code=204)


def test_server(body: ServerTestBody, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    stored = db.get(ArrServer, body.id) if body.id else None
    if body.id and stored is None:
        raise HTTPException(status_code=404, detail="Server not found")
    # The stored key is only ever sent to the address (and kind) it was saved for.
    same = stored is not None and stored.base_url == body.base_url and stored.kind == body.kind
    key = body.api_key or (stored.api_key if same else None)
    if not key:
        raise HTTPException(status_code=400, detail="api_key_required")
    try:
        result, error = arr.ArrClient(body.kind, body.base_url, key).test(), None
    except arr.ArrError as exc:
        result, error = {"version": None, "root_folders": [], "quality_profiles": []}, str(exc)
    if stored is not None and stored.base_url == body.base_url:
        with write_transaction(db, name="arr_server_test"):
            stored.last_error = error
            if error is None:
                stored.last_ok_at = utcnow()
    return {"ok": error is None, **result, **({"error": error} if error else {})}


def anime_language_profiles(server_id: str, db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    server = _server(db, server_id)
    if server.kind != "sonarr":
        raise HTTPException(status_code=400, detail="Anime language profiles are a Sonarr feature")
    try:
        dub, sub = arr.ensure_anime_language_profiles(arr.ArrClient.of(server), server)
    except arr.ArrError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})
    except (KeyError, TypeError, ValueError):
        return JSONResponse(status_code=502, content={"detail": "Sonarr sent an unexpected reply"})
    with write_transaction(db, name="arr_server_anime_profiles"):
        server.dub_profile_id, server.sub_profile_id = dub, sub
    return serialize_server(server)


# ---- Admin: settings and policies -------------------------------------------------------------------------------

def _settings_out(record: Any) -> dict[str, Any]:
    return {"requests_enabled": bool(record.requests_enabled), "smtp": {
        "host": record.smtp_host, "port": record.smtp_port, "security": record.smtp_security or "starttls",
        "username": record.smtp_username, "from": record.smtp_from, "password_set": bool(record.smtp_password)}}


def get_settings(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return _settings_out(YtDlpService(db).get_app_settings())


def put_settings(body: SettingsBody, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    with write_transaction(db, name="request_settings"):
        record = YtDlpService(db).ensure_app_settings()
        if body.requests_enabled is not None:
            record.requests_enabled = body.requests_enabled
        if body.smtp is not None:
            for field, value in body.smtp.model_dump(exclude_unset=True).items():
                column = "smtp_from" if field == "sender" else f"smtp_{field}"
                if field == "password":
                    if value is None:
                        continue  # omitted or null keeps the stored password; "" clears it
                    value = value or None
                elif field == "security" and value is None:
                    continue
                elif isinstance(value, str):
                    value = value.strip() or None
                setattr(record, column, value)
    return _settings_out(record)


def smtp_test(body: SmtpTestBody, request: Request, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    enforce_rate_limit("webhook_test", request, user_id=admin.id)
    smtp = notify.Smtp.of(YtDlpService(db).get_app_settings())
    if smtp is None:
        return {"ok": False, "error": "Set the mail server and From address first."}
    try:
        notify.send(smtp, body.to, *notify.smtp_test_email())
    except Exception as exc:  # noqa: BLE001 - fixed text only, never the server's reply
        return {"ok": False, "error": notify.failure_message(exc)}
    return {"ok": True}


def get_policies(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    rows = list(db.scalars(select(RequestPolicy)))
    defaults = {r.kind: r for r in rows if r.user_id is None}
    overrides: dict[str, list[RequestPolicy]] = {}
    for r in rows:
        if r.user_id is not None:
            overrides.setdefault(r.user_id, []).append(r)
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
    return {
        "defaults": [engine.policy_dict(defaults.get(kind), kind) for kind in engine.KINDS],
        "members": [{"user": {"id": u.id, "name": u.display_name, "role": u.role},
                     "overrides": [engine.policy_dict(r, r.kind) for r in sorted(overrides.get(u.id, []), key=lambda r: engine.KINDS.index(r.kind))]}
                    for u in users],
    }


def put_policies(body: PoliciesBody, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if body.user_id is not None and db.get(User, body.user_id) is None:
        raise HTTPException(status_code=404, detail="Member not found")
    with write_transaction(db, name="request_policies"):
        for policy in body.policies:
            match = RequestPolicy.user_id.is_(None) if body.user_id is None else RequestPolicy.user_id == body.user_id
            row = db.scalars(select(RequestPolicy).where(match, RequestPolicy.kind == policy.kind)).first()
            if row is None:
                row = RequestPolicy(id=str(uuid.uuid4()), user_id=body.user_id, kind=policy.kind)
                db.add(row)
            row.can_request, row.auto_approve = policy.can_request, policy.auto_approve
            row.quota_count, row.quota_days = policy.quota_count, policy.quota_days
    return get_policies(db)


def delete_policies(user_id: str, db: Session = Depends(get_db, scope="function")) -> Response:
    with write_transaction(db, name="request_policies_reset"):
        db.query(RequestPolicy).filter(RequestPolicy.user_id == user_id).delete(synchronize_session=False)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.post(URL, status_code=201)(create_request)
    app.get(URL)(list_requests)
    app.get(URL + "/quota")(get_quota)
    app.get(URL + "/notifications")(get_notifications)
    app.put(URL + "/notifications")(put_notifications)
    app.post(URL + "/{request_id}/approve")(approve_request)
    app.post(URL + "/{request_id}/decline")(decline_request)
    app.post(URL + "/{request_id}/retry", dependencies=admin)(retry_request)
    app.delete(URL + "/{request_id}", status_code=204)(cancel_request)
    app.get(ADMIN + "/servers", dependencies=admin)(list_servers)
    app.post(ADMIN + "/servers", status_code=201, dependencies=admin)(create_server)
    app.post(ADMIN + "/servers/test", dependencies=admin)(test_server)
    app.put(ADMIN + "/servers/{server_id}", dependencies=admin)(update_server)
    app.delete(ADMIN + "/servers/{server_id}", status_code=204, dependencies=admin)(delete_server)
    app.post(ADMIN + "/servers/{server_id}/anime-language-profiles", dependencies=admin)(anime_language_profiles)
    app.get(ADMIN + "/settings", dependencies=admin)(get_settings)
    app.put(ADMIN + "/settings", dependencies=admin)(put_settings)
    app.post(ADMIN + "/smtp/test")(smtp_test)
    app.get(ADMIN + "/policies", dependencies=admin)(get_policies)
    app.put(ADMIN + "/policies", dependencies=admin)(put_policies)
    app.delete(ADMIN + "/policies/{user_id}", status_code=204, dependencies=admin)(delete_policies)
