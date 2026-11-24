from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.network_policy import check_extra_ports

UserRole = Literal["admin", "viewer"]
MovieRating = Literal["G", "PG", "PG-13", "R", "NC-17"]
TvRating = Literal["TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CLOCK = r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$"
SECTION_ID = re.compile(r"^(?:movies|shows|anime|music|photos|books(?::ebook|:manga)?|vault|root:[0-9a-f-]{1,36})$")


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    onboarding_status: str = "completed"
    vault_setup_pending: bool = False
    has_local_password: bool = True
    bio: str | None = None
    can_edit_details: bool = False  # Owners always; members while the household switch is on
    two_factor_enabled: bool = False
    two_factor_setup_required: bool = False  # an owner the household requires to turn it on (owner settings refuse until then)
    created_at: datetime
    updated_at: datetime


class AdminMediaOnboardingContextResponse(BaseModel):
    mount_parents: list[str]


class SessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str | None = None


class DeviceMember(BaseModel):
    user_id: str
    display_name: str
    username: str
    role: str
    switch: Literal["instant", "password"]
    active: bool


class SessionSwitchRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class DeviceForgetRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class UserCreateRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    role: UserRole = "viewer"
    is_active: bool = True
    bio: str | None = None


class UserUpdateRequest(BaseModel):
    # No password field: admins issue a single-use reset link instead.
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    bio: str | None = None


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: UserRole = "viewer"
    expires_in_hours: int = Field(default=48, ge=1, le=168)


class InvitationResponse(BaseModel):
    id: str
    role: UserRole
    expires_at: datetime
    invitation_url: str


class InvitationSummaryResponse(BaseModel):
    """An issued invitation without its secret: the link is shown once, at issuance."""

    id: str
    role: UserRole
    status: Literal["pending", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime


class InvitationRedeemRequest(BaseModel):
    # extra="forbid": the recipient can never choose or tamper with the bound role.
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    password: str = Field(max_length=256)


class UserSelfUpdateRequest(BaseModel):
    # Password changes go through POST /api/me/password with the current password.
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    bio: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class PasswordResetLinkResponse(BaseModel):
    expires_at: datetime
    reset_url: str


class PasswordResetRedeemRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(max_length=256)


class AppSettingsResponse(BaseModel):
    temp_root: str
    archive_path: str
    concurrency: int
    max_active_jobs_per_user: int
    min_free_disk_mb: int
    max_playback_sessions: int = 2
    # When a saved change takes effect: download workers start with the process.
    setting_effects: dict[str, Literal["restart", "new_jobs"]] = Field(
        default_factory=lambda: {"concurrency": "restart", "max_active_jobs_per_user": "new_jobs", "min_free_disk_mb": "new_jobs"}
    )
    ffmpeg_path: str | None = None
    yt_dlp_defaults: dict[str, Any]
    ui_prefs: dict[str, Any]
    webhook_url: str | None = None
    webhook_enabled: bool = False
    webhook_notify_new_videos: bool = True
    webhook_notify_failures: bool = True
    extra_source_ports: list[int] = Field(default_factory=list)  # beyond 80/443, for sources such as Icecast on :8000
    require_owner_two_factor: bool = False
    updated_at: datetime


class AppSettingsUpdateRequest(BaseModel):
    temp_root: str | None = None
    archive_path: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)
    max_active_jobs_per_user: int | None = Field(default=None, ge=1, le=500)
    min_free_disk_mb: int | None = Field(default=None, ge=0, le=1_048_576)
    max_playback_sessions: int | None = Field(default=None, ge=1, le=16)
    ffmpeg_path: str | None = None
    yt_dlp_defaults: dict[str, Any] | None = None
    ui_prefs: dict[str, Any] | None = None
    webhook_url: str | None = None
    webhook_enabled: bool | None = None
    webhook_notify_new_videos: bool | None = None
    webhook_notify_failures: bool | None = None
    extra_source_ports: list[int] | None = Field(default=None, max_length=32)

    @field_validator("extra_source_ports")
    @classmethod
    def validate_extra_source_ports(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else check_extra_ports(value)
    require_owner_two_factor: bool | None = None

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return value
        try:
            parsed = urlsplit(normalized)
        except ValueError as exc:
            raise ValueError("Webhooks require an HTTPS Discord webhook URL.") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != "discord.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or not parsed.path.startswith("/api/webhooks/")
            or parsed.fragment
        ):
            raise ValueError("Webhooks require an HTTPS Discord webhook URL.")
        return normalized


class WebhookTestResponse(BaseModel):
    ok: bool
    message: str


class StreamingAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    youtube: bool = True
    twitch: bool = True
    kick: bool = True
    live: bool = True
    open_search: bool = True
    followed_only: bool = False


class MemberAccessIn(BaseModel):
    """What an admin sets for a member (or an invite carries). sections None = every library; schedule None = any time."""

    model_config = ConfigDict(extra="forbid")
    sections: list[str] | None = Field(default=None, max_length=64)
    movie_rating_max: MovieRating | None = None
    tv_rating_max: TvRating | None = None
    unrated: Literal["allow", "hide"] = "allow"
    streaming: StreamingAccess = Field(default_factory=StreamingAccess)
    schedule: dict[Weekday, list[tuple[str, str]]] | None = None
    daily_limit_minutes: int | None = Field(default=None, ge=1, le=1440)
    can_download: bool = True

    @field_validator("sections")
    @classmethod
    def _sections(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (bad := [s for s in value if not SECTION_ID.match(s)]):
            raise ValueError(f"Unknown library: {bad[0]}")
        return None if value is None else sorted(set(value))

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, value: dict[str, list[tuple[str, str]]] | None) -> dict[str, list[tuple[str, str]]] | None:
        for windows in (value or {}).values():
            if len(windows) > 8:
                raise ValueError("At most 8 viewing windows a day.")
            for start, end in windows:
                if not (re.match(CLOCK, start) and re.match(CLOCK, end)) or start >= end:
                    raise ValueError(f"{start}–{end} is not a viewing window.")
        return value


class MemberAccessOut(MemberAccessIn):
    restricted: bool  # False = no row: every library, no limits
    bonus_date: date | None = None
    bonus_minutes: int = 0
    bonus_minutes_today: int = 0
    screen_time_today_seconds: int = 0


class AdminUserResponse(UserResponse):
    """GET /api/admin/users rows: plus the member's access (None = every library, no limits) and today's screen time."""

    access: MemberAccessOut | None = None
    screen_time_today_seconds: int = 0
    sign_in_locked: bool = False  # too many wrong passwords: public sign-in paused until it drains or an owner unlocks


class MyAccessResponse(BaseModel):
    """GET /api/me/access: the signed-in member's own effective access, for hiding what they cannot use."""

    restricted: bool
    sections: list[str] | None
    blocked_streaming: list[str]
    followed_only: bool
    can_download: bool
    # Filled with {allowed_now, until, remaining_minutes}; None = no schedule or daily limit.
    schedule_state: dict[str, Any] | None = None
    allowed_now: bool = True  # flat copies of schedule_state for the member UI
    until: datetime | None = None
    remaining_minutes: int | None = None


class MemberFollowIn(BaseModel):
    """An admin follows a channel on a member's behalf (the way past followed_only, which refuses the member's own new follows)."""

    model_config = ConfigDict(extra="forbid")
    source_url: str = Field(min_length=1, max_length=2048)
    display_name: str = Field(default="", max_length=255)  # blank = the channel address


class MemberFollowOut(BaseModel):
    id: str
    label: str
    source_url: str


class SectionInfo(BaseModel):
    id: str
    label: str
    count: int
    description: str | None = None
