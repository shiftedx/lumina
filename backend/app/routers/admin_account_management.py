"""Admin account, invitation, and application-settings routes."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.account_schemas import AdminUserResponse, AppSettingsResponse, AppSettingsUpdateRequest, InvitationCreateRequest, InvitationRedeemRequest, InvitationResponse, InvitationSummaryResponse, PasswordResetLinkResponse, UserResponse, UserUpdateRequest, WebhookTestResponse
from app.db import get_db
from app.models import DownloadJob, LiveRecording, User
from app.security import ACCOUNT_LOCK_BUCKET, PasswordPolicyError, account_lock_key, audit_log, get_admin_user, get_owner_session_user
from app.services import invites as invite_service, member_access, two_factor
from app.services.job_manager import JobManager
from app.services.live_recording import TERMINAL_STATUSES
from app.services.live_recording_manager import LiveRecordingManager
from app.services.rate_limit import enforce_rate_limit, rate_limit_dependency, rate_limiter
from app.services.users import RESET_LINK_HOURS, InvalidAccountToken, UserService, reset_link_url
from app.services.webhooks import WebhookService
from app.services.yt_dlp_service import YtDlpService


def register(app: FastAPI, jobs: JobManager, live_recording_manager: LiveRecordingManager) -> None:
    @app.get("/api/admin/users", response_model=list[AdminUserResponse])
    def list_users(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[AdminUserResponse]:
        del current_user
        service = UserService(db)
        users = service.list_users()
        extra = member_access.admin_view(db, [user.id for user in users])  # member access spec: access + today's screen time
        rows = []
        for user in users:
            access = extra[user.id]["access"]
            rows.append(AdminUserResponse(**service.serialize_user(user).model_dump(), access=None if access is None else {**access, "restricted": True}, screen_time_today_seconds=extra[user.id]["screen_time_today_seconds"], sign_in_locked=rate_limiter.is_full(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username))))
        return rows

    @app.post("/api/admin/users/{user_id}/unlock", status_code=204)
    def unlock_user_sign_in(user_id: str, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> Response:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        rate_limiter.reset(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username))
        audit_log.info("login.unlock actor=%s user=%s", current_user.id, user.id)
        return Response(status_code=204)

    @app.post("/api/admin/invitations", response_model=InvitationResponse, status_code=201)
    def create_invitation(payload: InvitationCreateRequest, current_user: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> InvitationResponse:
        record, token = UserService(db).issue_account_token(current_user, "invite", role=payload.role, expires_in_hours=payload.expires_in_hours)
        return InvitationResponse(id=record.id, role=payload.role, expires_at=record.expires_at, invitation_url=invite_service.link(token))

    @app.get("/api/admin/invitations", response_model=list[InvitationSummaryResponse])
    def list_invitations(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[InvitationSummaryResponse]:
        del current_user
        return UserService(db).list_invitations()

    @app.post("/api/admin/invitations/{invitation_id}/revoke", response_model=InvitationSummaryResponse)
    def revoke_invitation(invitation_id: str, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> InvitationSummaryResponse:
        try:
            return UserService(db).revoke_invitation(current_user, invitation_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Invitation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/invitations/redeem", response_model=UserResponse, status_code=201, dependencies=[Depends(rate_limit_dependency("account_token_redeem"))])
    def redeem_invitation(payload: InvitationRedeemRequest, db: Session = Depends(get_db, scope="function")) -> UserResponse:
        service = UserService(db)
        try:
            user = service.redeem_invitation(payload)
        except InvalidAccountToken as exc:
            raise HTTPException(status_code=410, detail="This invitation link is invalid, already used, or expired.") from exc
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.serialize_user(user)

    @app.put("/api/admin/users/{user_id}", response_model=UserResponse)
    def update_user(user_id: str, payload: UserUpdateRequest, current_user: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> UserResponse:
        service = UserService(db)
        try:
            user = service.manage_user(current_user, user_id, payload)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc
        if payload.is_active is False:
            active = db.query(DownloadJob.id).filter(DownloadJob.user_id == user.id, DownloadJob.status.in_(["queued", "running", "postprocessing"]))
            for (job_id,) in active.all():
                jobs.cancel(db, job_id, user)
            recordings = db.query(LiveRecording.id).filter(LiveRecording.user_id == user.id, LiveRecording.status.notin_(tuple(TERMINAL_STATUSES)))
            for (recording_id,) in recordings.all():
                live_recording_manager.stop(user.id, recording_id, reason="owner_disabled")
        return service.serialize_user(user)

    @app.post("/api/admin/users/{user_id}/reset-link", response_model=PasswordResetLinkResponse, status_code=201)
    def create_password_reset_link(user_id: str, current_user: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> PasswordResetLinkResponse:
        if db.get(User, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        record, token = UserService(db).issue_account_token(current_user, "reset", user_id=user_id, expires_in_hours=RESET_LINK_HOURS)
        return PasswordResetLinkResponse(expires_at=record.expires_at, reset_url=reset_link_url(token))

    @app.get("/api/admin/settings", response_model=AppSettingsResponse)
    def get_admin_settings(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> AppSettingsResponse:
        del current_user
        return UserService(db).serialize_settings(YtDlpService(db).get_app_settings())

    @app.put("/api/admin/settings", response_model=AppSettingsResponse)
    def update_admin_settings(payload: AppSettingsUpdateRequest, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> AppSettingsResponse:
        if payload.require_owner_two_factor and not two_factor.enabled(current_user):
            raise HTTPException(status_code=409, detail="Turn on two-step verification for yourself first (Settings → You).")
        service = UserService(db)
        record = YtDlpService(db).ensure_app_settings()
        before = bool(record.require_owner_two_factor)
        updated = service.update_settings(record, payload)
        if bool(updated.require_owner_two_factor) != before:
            audit_log.info("two_factor.require_owners actor=%s value=%s", current_user.id, updated.require_owner_two_factor)
        return service.serialize_settings(updated)

    @app.post("/api/admin/settings/webhook/test", response_model=WebhookTestResponse)
    def test_admin_webhook(request: Request, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> WebhookTestResponse:
        enforce_rate_limit("webhook_test", request, user_id=current_user.id)
        del current_user
        ok, message = WebhookService(db).send_test()
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return WebhookTestResponse(ok=ok, message=message)
