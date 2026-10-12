from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app import config
from app.models import AccountToken, AppSession, AppSettings, DeviceToken, DownloadJob, User
from app.schemas import (
    AppSettingsResponse,
    AppSettingsUpdateRequest,
    InvitationRedeemRequest,
    InvitationSummaryResponse,
    UserCreateRequest,
    UserResponse,
    UserSelfUpdateRequest,
    UserUpdateRequest,
)
from app.persistence import write_transaction
from app.security import hash_password, has_local_password, session_digest, utcnow, validate_password_policy, verify_password
from app.services import network_policy, two_factor
from app.services.local_playback_sessions import sessions


# Audit trail is an append-only log stream, not a table; add a table when an admin UI needs to browse it.
audit_log = logging.getLogger("lumina.audit")

RESET_LINK_HOURS = 24


def reset_link_url(token: str) -> str:
    # The secret rides in the URL fragment: never sent to the server, logs or Referer.
    return f"{config.settings.resolved_frontend_public_url}/#reset={token}"


class InvalidAccountToken(Exception):
    """Unknown, used, revoked, or expired invitation/reset link (callers show one generic message)."""


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def needs_bootstrap(self) -> bool:
        return self.db.query(User).count() == 0

    def require_remote_bootstrap_ready(self, remote_https_enabled: bool) -> None:
        if remote_https_enabled and self.needs_bootstrap():
            raise RuntimeError("Remote HTTPS requires completing first-run administrator setup over loopback before startup.")

    def create_initial_admin(self, username: str, password: str, display_name: str | None = None) -> User:
        # Fast path: the consumed-setup state is the normal operating state, so a
        # known-consumed setup never spends a writer slot. The re-check inside the
        # serialized writer transaction below is authoritative: two synchronized
        # requests can both pass this pre-check, and only the first may commit.
        if not self.needs_bootstrap():
            raise ValueError("Initial admin has already been created")
        normalized_username = self._normalize_username(username)
        validate_password_policy(password, username=normalized_username)
        with write_transaction(self.db, name="bootstrap_initial_admin"):
            if not self.needs_bootstrap():
                # Re-executed after writer admission, under the SQLite write lock
                # (BEGIN IMMEDIATE): a concurrent bootstrap that committed between
                # the pre-check and lock acquisition is seen here. Rolling back
                # commits nothing — no partial user or settings rows.
                raise ValueError("Initial admin has already been created")
            user = User(
                id=str(uuid.uuid4()),
                username=normalized_username,
                display_name=(display_name or username).strip() or username.strip(),
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
            )
            self.db.add(user)
            self.db.flush()
            from app.services.user_settings import UserSettingsService

            UserSettingsService(self.db).ensure_for_user(user)
            self.db.flush()
            return user

    def list_users(self) -> list[User]:
        return self.db.query(User).order_by(User.created_at.asc()).all()

    def create_user(self, payload: UserCreateRequest) -> User:
        username = self._normalize_username(payload.username)
        if self.db.query(User).filter(User.username == username).first() is not None:
            raise ValueError("Username already exists")
        validate_password_policy(payload.password, username=username)
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            display_name=(payload.display_name or payload.username).strip() or payload.username.strip(),
            password_hash=hash_password(payload.password),
            role=payload.role,
            is_active=payload.is_active,
            bio=(payload.bio or "").strip() or None,
        )
        self.db.add(user)
        self.db.flush()
        from app.services.user_settings import UserSettingsService

        UserSettingsService(self.db).ensure_for_user(user)
        return user

    def create_member_without_password(self, actor: User, username: str, display_name: str) -> User:
        """A household member who cannot sign in until they redeem a reset link. The caller owns the write transaction
        and audits the creation once it commits."""
        normalized = self._normalize_username(username)
        user = User(
            id=str(uuid.uuid4()),
            username=normalized,
            display_name=display_name.strip()[:120] or normalized,
            password_hash=None,  # has_local_password() is false, so session login refuses it
            role="viewer",
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()
        from app.services.user_settings import UserSettingsService

        UserSettingsService(self.db).ensure_for_user(user)
        return user

    def update_user(self, user_id: str, payload: UserUpdateRequest) -> User:
        # The last-admin guard and the role/active mutation commit or roll back
        # together in one serialized writer transaction: the owner check is
        # re-read under the writer lock, not only in a pre-query.
        with write_transaction(self.db, name="update_user"):
            user = self.db.get(User, user_id, populate_existing=True)
            if user is None:
                raise ValueError("User not found")
            self._protect_active_owner(user, payload)
            return self._apply_user_update(user, payload)

    def manage_user(self, actor: User, user_id: str, payload: UserUpdateRequest) -> User:
        """Apply owner-only household account changes behind one policy boundary."""
        if actor.role != "admin" or not actor.is_active:
            raise PermissionError("Only an active vault owner can manage household accounts")
        # Self-removal and last-admin protection are enforced inside the same
        # serialized writer transaction as the mutation, so the check and the
        # change commit or roll back together (no TOCTOU between pre-check and write).
        with write_transaction(self.db, name="manage_user"):
            user = self.db.get(User, user_id, populate_existing=True)
            if user is None:
                raise ValueError("User not found")
            if actor.id == user.id and self._removes_active_owner(user, payload):
                raise ValueError("You cannot remove your own vault owner access")
            self._protect_active_owner(user, payload)
            updated = self._apply_user_update(user, payload)
        audit_log.info("user.update actor=%s user=%s role=%s active=%s", actor.id, user.id, payload.role, payload.is_active)
        return updated

    def revoke_sessions(self, user_id: str) -> None:
        """Sign the member out everywhere: browser sessions and every Connected app (ADR 0010)."""
        self.db.query(AppSession).filter(AppSession.user_id == user_id).delete(synchronize_session=False)
        self.db.query(DeviceToken).filter(DeviceToken.user_id == user_id).delete(synchronize_session=False)
        sessions.stop_where(user_id=user_id)  # every stream of the member ends with its sign-ins

    def _apply_user_update(self, user: User, payload: UserUpdateRequest) -> User:
        if payload.display_name is not None:
            user.display_name = payload.display_name.strip() or user.display_name
        if payload.bio is not None:
            user.bio = payload.bio.strip() or None
        if payload.role is not None and payload.role != user.role:
            user.role = payload.role
            # A role change signs the member out of every browser: a promoted member's
            # remembered device-ring session must never become a vault owner's bearer.
            self.db.query(AppSession).filter(AppSession.user_id == user.id).delete(synchronize_session=False)
        if payload.is_active is not None:
            user.is_active = payload.is_active
            if not payload.is_active:
                self.revoke_sessions(user.id)
        self.db.flush()
        return user

    def _protect_active_owner(self, user: User, payload: UserUpdateRequest) -> None:
        if not self._removes_active_owner(user, payload):
            return
        another_active_owner = (
            self.db.query(User.id)
            .filter(User.id != user.id, User.role == "admin", User.is_active.is_(True))
            .first()
        )
        if another_active_owner is None:
            raise ValueError("The household must keep at least one active vault owner")

    @staticmethod
    def _removes_active_owner(user: User, payload: UserUpdateRequest) -> bool:
        if user.role != "admin" or not user.is_active:
            return False
        next_role = payload.role if payload.role is not None else user.role
        next_active = payload.is_active if payload.is_active is not None else user.is_active
        return next_role != "admin" or not next_active

    def update_current_user(self, user: User, payload: UserSelfUpdateRequest) -> User:
        if payload.display_name is not None:
            user.display_name = payload.display_name.strip() or user.display_name
        if payload.bio is not None:
            user.bio = payload.bio.strip() or None
        self.db.flush()
        return user

    def change_password(self, user: User, current_password: str, new_password: str) -> None:
        """Self-service change: proves the current password, then revokes every session of the account."""
        if not verify_password(current_password, user.password_hash):
            raise PermissionError("Current password is incorrect.")
        validate_password_policy(new_password, username=user.username)
        with write_transaction(self.db, name="password_change"):
            user.password_hash = hash_password(new_password)
            self.revoke_sessions(user.id)
            self.db.flush()
        audit_log.info("password.change user=%s", user.id)

    def redeem_password_reset(self, token: str, new_password: str) -> User:
        with write_transaction(self.db, name="password_reset_redeem"):
            record = self._consume_account_token(token, "reset")
            user = self.db.get(User, record.user_id) if record.user_id else None
            if user is None:
                raise InvalidAccountToken()
            validate_password_policy(new_password, username=user.username)
            user.password_hash = hash_password(new_password)
            self.revoke_sessions(user.id)
            self.db.flush()
        audit_log.info("reset.redeem token_id=%s user=%s", record.id, user.id)
        return user

    def issue_account_token(
        self, actor: User, kind: str, *, expires_in_hours: int, role: str | None = None, user_id: str | None = None,
        audit: bool = True,  # False when an outer write transaction audits the issue itself once it commits
    ) -> tuple[AccountToken, str]:
        token = secrets.token_urlsafe(32)
        record = AccountToken(
            id=str(uuid.uuid4()),
            token_digest=session_digest(token),
            kind=kind,
            role=role,
            user_id=user_id,
            issued_by=actor.id,
            expires_at=utcnow() + timedelta(hours=expires_in_hours),
        )
        with write_transaction(self.db, name="account_token_issue"):
            if kind == "reset":
                # Only the newest reset link for a user is live.
                self.db.query(AccountToken).filter(
                    AccountToken.kind == "reset",
                    AccountToken.user_id == user_id,
                    AccountToken.used_at.is_(None),
                    AccountToken.revoked_at.is_(None),
                ).update({AccountToken.revoked_at: utcnow()}, synchronize_session=False)
            self.db.add(record)
            self.db.flush()
        if audit:
            audit_log.info("%s.issue actor=%s token_id=%s role=%s user=%s", kind, actor.id, record.id, role, user_id)
        return record, token

    @staticmethod
    def _summarize_invitation(record: AccountToken) -> InvitationSummaryResponse:
        status = "used" if record.used_at else "revoked" if record.revoked_at else "expired" if record.expires_at <= utcnow() else "pending"
        return InvitationSummaryResponse(id=record.id, role=record.role or "viewer", status=status, created_at=record.created_at, expires_at=record.expires_at)

    def list_invitations(self, limit: int = 50) -> list[InvitationSummaryResponse]:
        records = self.db.query(AccountToken).filter(AccountToken.kind == "invite").order_by(AccountToken.created_at.desc()).limit(limit).all()
        return [self._summarize_invitation(record) for record in records]

    def revoke_invitation(self, actor: User, invitation_id: str) -> InvitationSummaryResponse:
        with write_transaction(self.db, name="invitation_revoke"):
            record = self.db.get(AccountToken, invitation_id, populate_existing=True)
            if record is None or record.kind != "invite":
                raise LookupError(invitation_id)
            if record.used_at:
                raise ValueError("This invitation was already used; deactivate the member instead.")
            if not record.revoked_at:
                record.revoked_at = utcnow()
                self.db.flush()
        audit_log.info("invite.revoke actor=%s token_id=%s", actor.id, record.id)
        return self._summarize_invitation(record)

    def _consume_account_token(self, token: str, kind: str) -> AccountToken:
        """Mark a link used; call inside the writer transaction so consumption and its effect commit together."""
        record = (
            self.db.query(AccountToken)
            .filter(AccountToken.token_digest == session_digest(token), AccountToken.kind == kind)
            .populate_existing()
            .first()
        )
        if record is None or record.used_at or record.revoked_at or record.expires_at <= utcnow():
            raise InvalidAccountToken()
        record.used_at = utcnow()
        return record

    def redeem_invitation(self, payload: InvitationRedeemRequest) -> User:
        with write_transaction(self.db, name="invitation_redeem"):
            record = self._consume_account_token(payload.token, "invite")
            user = self.create_user(
                UserCreateRequest(
                    username=payload.username,
                    password=payload.password,
                    display_name=payload.display_name or (record.access or {}).get("display_name"),
                    role=record.role or "viewer",
                )
            )
            record.user_id = user.id
            if record.access is not None:  # an emailed invite carries libraries and limits
                from app.services import invites  # local: invites imports this module

                invites.apply_access(self.db, user, record)
            self.db.flush()
        audit_log.info("invite.redeem token_id=%s user=%s role=%s", record.id, user.id, user.role)
        return user

    def serialize_user(self, user: User) -> UserResponse:
        from app.services.metadata_editor import can_edit_details  # local: the editor imports the library stack
        return UserResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            onboarding_status=user.onboarding_status,
            has_local_password=has_local_password(user.password_hash),
            bio=user.bio,
            can_edit_details=can_edit_details(user, app_settings := self.db.get(AppSettings, 1)),
            two_factor_enabled=two_factor.enabled(user),
            two_factor_setup_required=user.role == "admin" and not two_factor.enabled(user) and bool(app_settings and app_settings.require_owner_two_factor),
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def serialize_settings(self, settings: AppSettings) -> AppSettingsResponse:
        return AppSettingsResponse(
            temp_root=settings.temp_root,
            archive_path=settings.archive_path,
            concurrency=settings.concurrency,
            max_active_jobs_per_user=settings.max_active_jobs_per_user,
            min_free_disk_mb=settings.min_free_disk_mb,
            max_playback_sessions=settings.max_playback_sessions,
            ffmpeg_path=settings.ffmpeg_path,
            yt_dlp_defaults=settings.yt_dlp_defaults,
            ui_prefs=settings.ui_prefs,
            webhook_url=(settings.ui_prefs.get("webhook_url") or "").strip() or None,
            webhook_enabled=bool(settings.ui_prefs.get("webhook_enabled", False)),
            webhook_notify_new_videos=bool(settings.ui_prefs.get("webhook_notify_new_videos", True)),
            webhook_notify_failures=bool(settings.ui_prefs.get("webhook_notify_failures", True)),
            extra_source_ports=[p for p in settings.ui_prefs.get("extra_source_ports") or [] if type(p) is int],
            require_owner_two_factor=bool(settings.require_owner_two_factor),
            updated_at=settings.updated_at,
        )

    def update_settings(self, record: AppSettings, payload: AppSettingsUpdateRequest) -> AppSettings:
        if payload.temp_root is not None:
            record.temp_root = str(Path(payload.temp_root).expanduser())
        if payload.archive_path is not None:
            record.archive_path = str(Path(payload.archive_path).expanduser())
        if payload.concurrency is not None:
            record.concurrency = payload.concurrency
        if payload.max_active_jobs_per_user is not None:
            record.max_active_jobs_per_user = payload.max_active_jobs_per_user
        if payload.min_free_disk_mb is not None:
            record.min_free_disk_mb = payload.min_free_disk_mb
        if payload.max_playback_sessions is not None:
            record.max_playback_sessions = payload.max_playback_sessions
        if payload.ffmpeg_path is not None:
            record.ffmpeg_path = payload.ffmpeg_path.strip() or None
        if payload.yt_dlp_defaults is not None:
            record.yt_dlp_defaults = payload.yt_dlp_defaults
        if payload.ui_prefs is not None:
            record.ui_prefs = payload.ui_prefs
        if payload.require_owner_two_factor is not None:
            record.require_owner_two_factor = payload.require_owner_two_factor
        if payload.webhook_url is not None:
            record.ui_prefs = {
                **(record.ui_prefs or {}),
                "webhook_url": payload.webhook_url.strip(),
            }
        if payload.webhook_enabled is not None:
            record.ui_prefs = {
                **(record.ui_prefs or {}),
                "webhook_enabled": payload.webhook_enabled,
            }
        if payload.webhook_notify_new_videos is not None:
            record.ui_prefs = {
                **(record.ui_prefs or {}),
                "webhook_notify_new_videos": payload.webhook_notify_new_videos,
            }
        if payload.webhook_notify_failures is not None:
            record.ui_prefs = {
                **(record.ui_prefs or {}),
                "webhook_notify_failures": payload.webhook_notify_failures,
            }
        if payload.extra_source_ports is not None:
            record.ui_prefs = {**(record.ui_prefs or {}), "extra_source_ports": payload.extra_source_ports}
        network_policy.set_extra_ports((record.ui_prefs or {}).get("extra_source_ports"))

        Path(record.temp_root).mkdir(parents=True, exist_ok=True)
        Path(record.archive_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(record.archive_path).expanduser().touch(exist_ok=True)

        self.db.flush()
        return record

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("Username is required")
        return normalized

