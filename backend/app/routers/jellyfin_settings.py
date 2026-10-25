"""Explicit member Settings extensions for authenticated Jellyfin Connected apps.

The routes in this module are deliberately narrow.  A Connected-app token does
not become a browser ``/api`` credential, even after a member has reauthenticated.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.media_schemas import AppPasswordCreateRequest, AppPasswordCreated, ConnectedApp, ConnectedAppCreateRequest, ConnectedAppCreated, SignOutAllAppsResponse
from app.models import AccountToken, AppSettings, DeviceToken, User, utcnow
from app.persistence import write_transaction
from app.routers import admin_library_automation, requests as member_requests
from app.routers import profile_export, reco as reco_routes, search_history
from app.services.resource_policy import effective as effective_resource_allowances
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.schemas import (
    RecoveryCodesResponse, TwoFactorCodeRequest, TwoFactorSetupResponse, TwoFactorStatusResponse,
    UserResponse, UserSelfUpdateRequest, UserSettingsResponse, UserSettingsUpdateRequest,
)
from app.security import DEVICE_TOKEN_IDLE_DAYS, PasswordPolicyError, audit_log, hash_password, session_digest, validate_password_policy, verify_password
from app.services import invites, member_access, public_address, two_factor
from app.services.connected_apps import create_agent_token, create_app_password, revoke_connected_app as revoke_connected_app_service, sign_out_all_apps, to_connected_app
from app.services.rate_limit import enforce_rate_limit
from app.services.user_settings import UserSettingsService
from app.services.users import UserService


router = APIRouter(prefix="/jellyfin/lumina/settings")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]
_REAUTH_HEADER = "X-Lumina-Reauth"
_REAUTH_SECONDS = 300
_NATIVE_STREAMING_PROVIDERS = ("youtube", "twitch", "kick")


class ReauthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=256, repr=False)
    code: str | None = Field(default=None, max_length=16, repr=False)
    recovery_code: str | None = Field(default=None, max_length=40, repr=False)


class ReauthResponse(BaseModel):
    proof: str
    expires_in: int


class NewPasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(max_length=256, repr=False)


class _ReauthProofs:
    """One-use, in-memory proofs bound to the exact Connected-app token."""
    # ponytail: one API process matches the Docker runtime; multiple API workers need a shared atomic proof store.

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, caller: JellyfinCaller) -> str:
        raw = secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._items = {digest: value for digest, value in self._items.items() if value[2] > now}
            self._items[session_digest(raw)] = (caller.user.id, caller.token_id, now + _REAUTH_SECONDS)
        return raw

    def consume(self, raw: str | None, caller: JellyfinCaller) -> bool:
        if not raw:
            return False
        with self._lock:
            value = self._items.pop(session_digest(raw), None)
        return value is not None and value[2] > time.monotonic() and value[:2] == (caller.user.id, caller.token_id)


_reauth_proofs = _ReauthProofs()


def _live_user(db: Session, caller: JellyfinCaller) -> User:
    """JellyfinCaller deliberately carries a redacted User snapshot; settings needs its own row."""
    user = db.get(User, caller.user.id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return user


def _require_reauth(request: Request, caller: JellyfinCaller) -> None:
    if not _reauth_proofs.consume(request.headers.get(_REAUTH_HEADER), caller):
        raise HTTPException(status_code=401, detail="reauthentication_required")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _live_admin(db: Session, caller: JellyfinCaller, *, write: bool = False) -> User:
    """Authorize native admin quick actions from live member and token rows."""
    user = db.get(User, caller.user.id, populate_existing=True)
    token = db.get(DeviceToken, caller.token_id, populate_existing=True)
    if user is None or not user.is_active or token is None or token.kind != "jellyfin" or token.user_id != user.id:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if two_factor.setup_required(db, user):
        raise HTTPException(status_code=403, detail="two_factor_setup_required")
    if write and token.scope != "write":
        raise HTTPException(status_code=403, detail="This connected app has read-only access.")
    return user


@router.get("/admin/library")
def get_admin_library(response: Response, caller: Caller, db: Db) -> admin_library_automation.LibraryAutomationResponse:
    _live_admin(db, caller)
    _no_store(response)
    return admin_library_automation.get_automation()


@router.post("/admin/library/scan", status_code=202)
def scan_admin_library(
    response: Response, caller: Caller, db: Db,
    payload: admin_library_automation.ScanRequest | None = None,
) -> dict[str, Any]:
    admin = _live_admin(db, caller, write=True)
    _no_store(response)
    return admin_library_automation.scan_now(payload, admin=admin, db=db)


@router.get("/admin/invites")
def get_admin_invites(response: Response, caller: Caller, db: Db) -> list[dict]:
    _live_admin(db, caller)
    _no_store(response)
    rows = db.scalars(
        select(AccountToken).where(AccountToken.kind == "invite", AccountToken.email.is_not(None))
        .order_by(AccountToken.created_at.desc()).limit(100)
    ).all()
    return [invites.serialize(db, row) for row in rows]


@router.post("/admin/invites", status_code=201)
def create_admin_invite(
    body: invites.InviteCreate, request: Request, response: Response, caller: Caller, db: Db,
) -> dict:
    admin = _live_admin(db, caller, write=True)
    _require_reauth(request, caller)
    result = invites.create(db, admin, body)
    audit_log.info("invite.create actor=%s invite=%s sent=%s", admin.id, result["id"], result["email_sent"])
    _no_store(response)
    return result


@router.delete("/admin/invites/{invite_id}", status_code=204)
def revoke_admin_invite(invite_id: str, request: Request, caller: Caller, db: Db) -> Response:
    admin = _live_admin(db, caller, write=True)
    _require_reauth(request, caller)
    try:
        UserService(db).revoke_invitation(admin, invite_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Invitation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = Response(status_code=204)
    _no_store(response)
    return response


def _server_native_streaming_enabled(db: Session) -> bool:
    record = db.get(AppSettings, 1)
    return bool(record is not None and record.native_streaming_enabled)


def native_streaming_enabled(db: Session, user: User) -> bool:
    """The Connected-app-only external-provider capability for this member."""
    if not _server_native_streaming_enabled(db):
        return False
    access = member_access.for_user(db, user)
    effective = access or member_access.EffectiveAccess()
    return any(effective.streaming.get(provider, True) for provider in _NATIVE_STREAMING_PROVIDERS)


def native_youtube_session_enabled(db: Session, user: User) -> bool:
    if not _server_native_streaming_enabled(db):
        return False
    access = member_access.for_user(db, user)
    effective = access or member_access.EffectiveAccess()
    return effective.streaming.get("youtube", True)


def require_native_youtube_settings(db: Session, user: User) -> None:
    """Reject personal YouTube credentials before reading or consuming a proof."""
    if not _server_native_streaming_enabled(db):
        raise HTTPException(status_code=403, detail="native_streaming_disabled")
    if not native_youtube_session_enabled(db, user):
        raise HTTPException(status_code=403, detail="streaming_blocked:youtube")


def _cache_and_publish(user: User, serialized: UserSettingsResponse, *, cache_changed: bool) -> None:
    """Keep the same durable-cache and settings event behavior as the browser write."""
    if cache_changed:
        # These process-local objects are intentionally owned by main. Import at
        # request time to avoid an app-startup import cycle.
        from app.main import _remote_stream_cache_policies, _remote_stream_cache_policies_lock, remote_stream_cache
        from app.services.stream_cache import StreamCachePolicy

        preference = serialized.remote_playback_cache
        policy = StreamCachePolicy(
            recent_video_limit=preference.recent_video_limit if preference.enabled else 0,
            max_bytes=preference.storage_limit_mb * 1024 * 1024 if preference.enabled else 0,
        )
        with _remote_stream_cache_policies_lock:
            _remote_stream_cache_policies[user.id] = policy
        remote_stream_cache.prune_owner(user.id)
    from app.main import events
    events.publish("settings_updated", {"settings": serialized.model_dump(mode="json"), "user_id": user.id})


@router.get("")
@router.get("/")
def get_settings(response: Response, caller: Caller, db: Db) -> UserSettingsResponse:
    response.headers["Cache-Control"] = "no-store"
    return UserSettingsService(db).serialize(UserSettingsService(db).ensure_for_user(caller.user))


@router.put("")
@router.put("/")
def put_settings(body: UserSettingsUpdateRequest, caller: Caller, db: Db) -> UserSettingsResponse:
    service = UserSettingsService(db)
    with write_transaction(db, name="native_user_settings_update"):
        serialized = service.serialize(service.update_for_user(caller.user, body))
    _cache_and_publish(caller.user, serialized, cache_changed=body.remote_playback_cache is not None)
    return serialized


@router.get("/profile")
def get_profile(response: Response, caller: Caller, db: Db) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return UserService(db).serialize_user(_live_user(db, caller))


@router.put("/profile")
def put_profile(body: UserSelfUpdateRequest, caller: Caller, db: Db) -> UserResponse:
    with write_transaction(db, name="native_profile_update"):
        user = UserService(db).update_current_user(_live_user(db, caller), body)
        response = UserService(db).serialize_user(user)
    return response


@router.get("/notifications")
def get_notifications(response: Response, caller: Caller, db: Db) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return member_requests.get_notifications(caller.user, db)


@router.put("/notifications")
def put_notifications(body: member_requests.NotificationsBody, caller: Caller, db: Db) -> dict:
    return member_requests.put_notifications(body, caller.user, db)


@router.get("/export")
def export_data(response: Response, caller: Caller, db: Db) -> profile_export.ProfileExportResponse:
    response.headers["Cache-Control"] = "no-store"
    return profile_export.export_my_data(response, _live_user(db, caller), db)


@router.get("/access")
def get_access(response: Response, caller: Caller, db: Db) -> dict:
    response.headers["Cache-Control"] = "no-store"
    user = _live_user(db, caller)
    access = member_access.for_user(db, user)
    effective = access or member_access.EffectiveAccess()
    state = member_access.schedule_state(db, user, access)
    return {
        "restricted": access is not None,
        "sections": None if effective.sections is None else sorted(effective.sections),
        "blocked_streaming": effective.blocked_streaming,
        "followed_only": bool(effective.streaming.get("followed_only")),
        "can_download": effective.can_download,
        "schedule_state": state,
        **({"allowed_now": state["allowed_now"], "until": state["until"], "remaining_minutes": state["remaining_minutes"]} if state else {}),
    }


@router.get("/capabilities")
def get_capabilities(response: Response, caller: Caller, db: Db) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    user = _live_user(db, caller)
    return {
        "streaming_enabled": native_streaming_enabled(db, user),
        "youtube_session_enabled": native_youtube_session_enabled(db, user),
    }


@router.get("/navigation")
def get_navigation(response: Response, caller: Caller, db: Db) -> dict[str, list[str]]:
    """Native menus reflect the complete visible catalog, never just a Home page of titles."""
    from app.book_models import BookItem
    from app.models import LibraryItem, MediaTitle
    from app.services.books import BookService
    from app.services.library import LibraryService
    from app.services.titles import TitleService

    user = _live_user(db, caller)
    categories = set()
    for category in db.scalars(select(MediaTitle.category).where(
        MediaTitle.type.in_(("movie", "series")), TitleService.visible(user),
    ).distinct()):
        if category in ("movies", "shows", "anime"):
            categories.add("tv" if category == "shows" else category)
    if db.scalar(select(LibraryItem.id).where(
        LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing", LibraryService.visible_predicate(user),
    ).limit(1)):
        categories.add("music")
    if db.scalar(select(BookItem.id).where(
        BookService(db).catalog_predicate(user), BookItem.available.is_(True),
    ).limit(1)):
        categories.add("books")
    _no_store(response)
    return {"categories": [key for key in ("movies", "tv", "anime", "music", "books") if key in categories]}


def _resource_jobs(db: Session, user: User) -> list[dict[str, str]]:
    from app.services.ai_resources import MODELS

    kinds = ("enrichment", "summary", "graph", "recap")
    result: list[dict[str, str]] = []
    for kind, model in zip(kinds, MODELS, strict=True):
        rows = (
            db.query(model)
            .filter(model.requested_by == user.id, model.state.in_(("pending", "queued", "running")))
            .order_by(model.created_at)
            .all()
        )
        result.extend({"id": job.id, "kind": kind, "state": job.state} for job in rows)
    return result


@router.get("/resources")
def get_resources(response: Response, caller: Caller, db: Db) -> dict:
    response.headers["Cache-Control"] = "no-store"
    user = _live_user(db, caller)
    return {"overrides": user.resource_overrides or {}, "effective": effective_resource_allowances(db, user.id).model_dump()}


@router.get("/resource-jobs")
def get_resource_jobs(response: Response, caller: Caller, db: Db) -> list[dict[str, str]]:
    response.headers["Cache-Control"] = "no-store"
    return _resource_jobs(db, _live_user(db, caller))


@router.post("/resource-jobs/{kind}/{job_id}/cancel")
def cancel_resource_job(kind: str, job_id: str, caller: Caller, db: Db) -> dict[str, str]:
    from app.services import local_asr, summaries
    from app.services.ai_resources import MODELS

    models = dict(zip(("enrichment", "summary", "graph", "recap"), MODELS, strict=True))
    model = models.get(kind)
    user = _live_user(db, caller)
    job = db.get(model, job_id) if model else None
    if job is None or job.requested_by != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state not in {"pending", "queued", "running"}:
        return {"state": job.state}
    registry = local_asr.jobs if kind == "enrichment" else summaries.jobs
    if not registry.cancel(job_id):
        with write_transaction(db, name="native_member_cancel_ai_job"):
            job.state = "canceled"
            db.flush()
    return {"state": "canceling" if registry.is_active(job_id) else "canceled"}


@router.get("/search-history")
def get_search_history(response: Response, caller: Caller, db: Db) -> list[search_history.SearchHistoryResponse]:
    response.headers["Cache-Control"] = "no-store"
    return search_history.list_search_history(_live_user(db, caller), db)


@router.delete("/search-history")
def clear_search_history(caller: Caller, db: Db) -> search_history.SearchHistoryClearResponse:
    return search_history.clear_search_history(_live_user(db, caller), db)


@router.delete("/search-history/{entry_id}", status_code=204)
def delete_search_history(entry_id: str, caller: Caller, db: Db) -> Response:
    search_history.delete_search_history_entry(entry_id, _live_user(db, caller), db)
    return Response(status_code=204)


@router.delete("/discovery/recommendation-history", status_code=204)
def clear_recommendation_history(caller: Caller, db: Db) -> Response:
    return reco_routes.delete_reco_history(_live_user(db, caller), db)


@router.post("/reauth")
def reauthenticate(body: ReauthBody, request: Request, response: Response, caller: Caller, db: Db) -> ReauthResponse:
    """Prove the local account password and current second factor for one sensitive mutation."""
    user = _live_user(db, caller)
    enforce_rate_limit("session_login", request, user_id=user.id)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=403, detail="reauthentication_failed")
    if two_factor.enabled(user):
        if bool(body.code) == bool(body.recovery_code) or not two_factor.verify(
            db, user, code=body.code, recovery_code=body.recovery_code,
        ):
            raise HTTPException(status_code=403, detail="reauthentication_failed")
    response.headers["Cache-Control"] = "no-store"
    return ReauthResponse(proof=_reauth_proofs.issue(caller), expires_in=_REAUTH_SECONDS)


@router.post("/password", status_code=204)
def change_password(body: NewPasswordBody, request: Request, caller: Caller, db: Db) -> Response:
    _require_reauth(request, caller)
    user = _live_user(db, caller)
    try:
        validate_password_policy(body.new_password, username=user.username)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with write_transaction(db, name="native_password_change"):
        user = db.get(User, caller.user.id, populate_existing=True)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked token")
        user.password_hash = hash_password(body.new_password)
        UserService(db).revoke_sessions(user.id)
        db.flush()
    audit_log.info("password.change user=%s", caller.user.id)
    return Response(status_code=204)


@router.get("/two-factor")
def two_factor_status(response: Response, caller: Caller, db: Db) -> TwoFactorStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    user = _live_user(db, caller)
    return TwoFactorStatusResponse(
        enabled=two_factor.enabled(user),
        recovery_codes_left=len(user.totp_recovery or []) if two_factor.enabled(user) else 0,
        required=user.role == "admin" and two_factor.owners_required(db),
    )


@router.post("/two-factor/setup")
def two_factor_setup(request: Request, response: Response, caller: Caller, db: Db) -> TwoFactorSetupResponse:
    _require_reauth(request, caller)
    user = _live_user(db, caller)
    try:
        secret = two_factor.begin(db, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    from app.services import qr
    uri = two_factor.otpauth_uri(user.username, secret)
    size, path = qr.svg_path(uri)
    response.headers["Cache-Control"] = "no-store"
    return TwoFactorSetupResponse(secret=two_factor.b32(secret), otpauth_uri=uri, qr_size=size, qr_path=path)


@router.post("/two-factor/enable")
def two_factor_enable(body: TwoFactorCodeRequest, request: Request, response: Response, caller: Caller, db: Db) -> RecoveryCodesResponse:
    _require_reauth(request, caller)
    user = _live_user(db, caller)
    enforce_rate_limit("session_login", request, user_id=user.id)
    codes = two_factor.confirm(db, user, body.code)
    if codes is None:
        raise HTTPException(status_code=400, detail="That code didn't work. Check the time on your phone and try the newest code.")
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/two-factor/disable", status_code=204)
def two_factor_disable(request: Request, caller: Caller, db: Db) -> Response:
    _require_reauth(request, caller)
    user = _live_user(db, caller)
    if user.role == "admin" and two_factor.owners_required(db):
        raise HTTPException(status_code=409, detail="This household requires two-step verification for vault owners.")
    if two_factor.enabled(user):
        two_factor.clear(db, user.id)
        audit_log.info("two_factor.disable user=%s", user.id)
    return Response(status_code=204)


@router.post("/two-factor/recovery-codes")
def two_factor_recovery_codes(request: Request, response: Response, caller: Caller, db: Db) -> RecoveryCodesResponse:
    _require_reauth(request, caller)
    user = _live_user(db, caller)
    if not two_factor.enabled(user):
        raise HTTPException(status_code=409, detail="Two-step verification is off.")
    with write_transaction(db, name="native_two_factor_recovery_codes"):
        user = db.get(User, caller.user.id, populate_existing=True)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked token")
        codes = two_factor.new_recovery_codes(user)
        db.flush()
    audit_log.info("two_factor.recovery_regenerate user=%s", caller.user.id)
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesResponse(recovery_codes=codes)


def _own_apps(db: Session, user: User) -> list[ConnectedApp]:
    cutoff = utcnow() - timedelta(days=DEVICE_TOKEN_IDLE_DAYS)
    rows = db.scalars(
        select(DeviceToken).where(
            DeviceToken.user_id == user.id,
            (DeviceToken.last_seen_at > cutoff) | (DeviceToken.kind == "app_password"),
        ).order_by(DeviceToken.last_seen_at.desc(), DeviceToken.id)
    )
    return [to_connected_app(record) for record in rows]


@router.get("/connected-apps")
def connected_apps(response: Response, caller: Caller, db: Db) -> list[ConnectedApp]:
    response.headers["Cache-Control"] = "no-store"
    return _own_apps(db, caller.user)


@router.post("/connected-apps", status_code=201)
def create_connected_app(body: ConnectedAppCreateRequest, request: Request, response: Response, caller: Caller, db: Db) -> ConnectedAppCreated:
    _require_reauth(request, caller)
    try:
        record, token = create_agent_token(db, caller.user, body.name, body.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return ConnectedAppCreated(app=to_connected_app(record), token=token)


@router.post("/connected-apps/app-passwords", status_code=201)
def create_connected_app_password(body: AppPasswordCreateRequest, request: Request, response: Response, caller: Caller, db: Db) -> AppPasswordCreated:
    _require_reauth(request, caller)
    try:
        record, password = create_app_password(db, caller.user, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return AppPasswordCreated(
        app=to_connected_app(record), password=password, username=caller.user.username,
        server_address=public_address.origin() or public_address.local_origin() or settings.resolved_app_public_url or None,
        local_server_address=public_address.local_origin(),
    )


@router.delete("/connected-apps/{app_id}", status_code=204)
def revoke_connected_app(app_id: str, request: Request, caller: Caller, db: Db) -> Response:
    _require_reauth(request, caller)
    record = db.get(DeviceToken, app_id)
    if record is None or record.user_id != caller.user.id:
        raise HTTPException(status_code=404, detail="Connected app not found")
    revoke_connected_app_service(db, caller.user, app_id)
    return Response(status_code=204)


@router.post("/connected-apps/sign-out-all")
def sign_out_connected_apps(request: Request, caller: Caller, db: Db) -> SignOutAllAppsResponse:
    _require_reauth(request, caller)
    return SignOutAllAppsResponse(revoked=sign_out_all_apps(db, caller.user))
