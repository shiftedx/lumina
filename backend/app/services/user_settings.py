from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserSettings
from app.schemas import (
    AutomationRuleSet,
    FormatSelection,
    OutputProfile,
    REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS,
    REMOTE_PLAYBACK_STORAGE_LIMITS_MB,
    RemotePlaybackCacheSettings,
    UserAutomationDefaults,
    UserDownloadDefaults,
    UserSettingsResponse,
    UserSettingsUpdateRequest,
)
from app.services.output_policy import OutputPolicy
from app.services.remote_streaming import PLAYBACK_CEILINGS


def playback_ceiling(db: Session, user_id: str) -> int | None:
    """The member's playback ceiling (``ui_prefs.playback_max_height``); None is Best available."""
    prefs = db.scalar(select(UserSettings.ui_prefs).where(UserSettings.user_id == user_id))
    value = prefs.get("playback_max_height") if isinstance(prefs, dict) else None
    return value if value in PLAYBACK_CEILINGS else None


def normalize_loudness(db: Session, user_id: str) -> bool:
    """Whether the member evens out loudness (``ui_prefs.normalize_loudness``); on unless explicitly turned off."""
    prefs = db.scalar(select(UserSettings.ui_prefs).where(UserSettings.user_id == user_id))
    return not (isinstance(prefs, dict) and prefs.get("normalize_loudness") is False)


class UserSettingsService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_for_user(self, user: User) -> UserSettings:
        record = self.db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
        if record is not None:
            changed = self._normalize_record(record)
            if changed:
                self.db.flush()
            return record
        record = UserSettings(
            id=str(uuid.uuid4()),
            user_id=user.id,
            download_defaults=self.default_download_defaults().model_dump(),
            automation_defaults=self.default_automation_defaults().model_dump(),
            ui_prefs={},
            notification_prefs={},
            remote_playback_cache=self.default_remote_playback_cache().model_dump(),
        )
        self.db.add(record)
        self.db.flush()
        return record

    def snapshot_for_user(self, user: User) -> UserSettings:
        """Return an unpersisted settings snapshot for read-only resolution.

        Unlike ensure_for_user this never creates, mutates, or flushes the
        stored row, so callers that must not open a write transaction (for
        example automation builders that later resolve DNS or inspect a
        source) can resolve defaults safely. The snapshot matches what
        ensure_for_user would return: stored values are normalized in memory,
        and an absent row resolves to the same defaults a fresh row receives.
        """
        record = self.db.query(UserSettings).filter(UserSettings.user_id == user.id).first()

        def copied(value: object) -> dict:
            return dict(value) if isinstance(value, dict) else {}

        snapshot = UserSettings(
            id=record.id if record is not None else "",
            user_id=user.id,
            download_defaults=copied(record.download_defaults) if record is not None else {},
            automation_defaults=copied(record.automation_defaults) if record is not None else {},
            ui_prefs=copied(record.ui_prefs) if record is not None else {},
            notification_prefs=copied(record.notification_prefs) if record is not None else {},
            remote_playback_cache=copied(record.remote_playback_cache) if record is not None else {},
        )
        self._normalize_record(snapshot)
        return snapshot

    def ensure_for_all_users(self) -> None:
        for user in self.db.query(User).all():
            self.ensure_for_user(user)

    def update_for_user(self, user: User, payload: UserSettingsUpdateRequest) -> UserSettings:
        record = self.ensure_for_user(user)
        if payload.download_defaults is not None:
            record.download_defaults = self._normalize_download_defaults(payload.download_defaults).model_dump()
        if payload.automation_defaults is not None:
            record.automation_defaults = self._normalize_automation_defaults(payload.automation_defaults).model_dump()
        if payload.ui_prefs is not None:
            record.ui_prefs = {**(record.ui_prefs or {}), **payload.ui_prefs}
        if payload.notification_prefs is not None:
            record.notification_prefs = {**(record.notification_prefs or {}), **payload.notification_prefs}
        if payload.remote_playback_cache is not None:
            record.remote_playback_cache = payload.remote_playback_cache.model_dump()
        self.db.flush()
        return record

    def serialize(self, record: UserSettings) -> UserSettingsResponse:
        download_defaults = self._normalize_download_defaults(UserDownloadDefaults.model_validate(record.download_defaults or {}))
        automation_defaults = self._normalize_automation_defaults(UserAutomationDefaults.model_validate(record.automation_defaults or {}))
        remote_playback_cache = self._normalize_remote_playback_cache(record.remote_playback_cache)
        resolved_download = self.resolve_download_defaults(record)
        resolved_automation = self.resolve_automation_defaults(record)
        return UserSettingsResponse(
            id=record.id,
            user_id=record.user_id,
            download_defaults=download_defaults,
            automation_defaults=automation_defaults,
            ui_prefs=record.ui_prefs or {},
            notification_prefs=record.notification_prefs or {},
            remote_playback_cache=remote_playback_cache,
            resolved_download_defaults=resolved_download,
            resolved_automation_defaults=resolved_automation,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def resolve_download_defaults(self, record: UserSettings) -> UserDownloadDefaults:
        return self._normalize_download_defaults(UserDownloadDefaults.model_validate(record.download_defaults or {}))

    def resolve_automation_defaults(self, record: UserSettings) -> UserAutomationDefaults:
        return self._normalize_automation_defaults(UserAutomationDefaults.model_validate(record.automation_defaults or {}))

    @staticmethod
    def default_download_defaults() -> UserDownloadDefaults:
        return UserDownloadDefaults(
            format_selection=FormatSelection(),
            output_profile=OutputProfile(),
        )

    @staticmethod
    def default_automation_defaults() -> UserAutomationDefaults:
        return UserAutomationDefaults(
            cron_expression="*/30 * * * *",
            auto_download=True,
            format_selection=FormatSelection(),
            output_profile=OutputProfile(organize_by="playlist"),
            rules=AutomationRuleSet(),
            duplicate_policy="skip_same_source",
            max_items_per_run=25,
            max_items_per_day=None,
            backfill_limit=50,
        )

    @staticmethod
    def default_remote_playback_cache() -> RemotePlaybackCacheSettings:
        return RemotePlaybackCacheSettings(enabled=True, recent_video_limit=5, storage_limit_mb=2048)

    @classmethod
    def _normalize_download_defaults(cls, defaults: UserDownloadDefaults) -> UserDownloadDefaults:
        if defaults.format_selection.preset == "audio_only":
            defaults.format_selection.extract_audio = True
            defaults.format_selection.audio_format = defaults.format_selection.audio_format or "mp3"
        return defaults

    @classmethod
    def _normalize_automation_defaults(cls, defaults: UserAutomationDefaults) -> UserAutomationDefaults:
        if defaults.format_selection.preset == "audio_only":
            defaults.format_selection.extract_audio = True
            defaults.format_selection.audio_format = defaults.format_selection.audio_format or "mp3"
        return defaults

    def _normalize_record(self, record: UserSettings) -> bool:
        changed = False
        if not isinstance(record.download_defaults, dict) or not record.download_defaults:
            record.download_defaults = self.default_download_defaults().model_dump()
            changed = True
        if not isinstance(record.automation_defaults, dict) or not record.automation_defaults:
            record.automation_defaults = self.default_automation_defaults().model_dump()
            changed = True
        download_defaults = dict(record.download_defaults)
        safe_download_output = OutputPolicy.sanitize_profile(download_defaults.get("output_profile")).model_dump()
        if download_defaults.get("output_profile") != safe_download_output:
            download_defaults["output_profile"] = safe_download_output
            record.download_defaults = download_defaults
            changed = True
        automation_defaults = dict(record.automation_defaults)
        safe_automation_output = OutputPolicy.sanitize_profile(
            automation_defaults.get("output_profile"),
            organize_by="playlist",
        ).model_dump()
        if automation_defaults.get("output_profile") != safe_automation_output:
            automation_defaults["output_profile"] = safe_automation_output
            record.automation_defaults = automation_defaults
            changed = True
        if not isinstance(record.ui_prefs, dict):
            record.ui_prefs = {}
            changed = True
        if not isinstance(record.notification_prefs, dict):
            record.notification_prefs = {}
            changed = True
        normalized_cache = self._normalize_remote_playback_cache(record.remote_playback_cache).model_dump()
        if record.remote_playback_cache != normalized_cache:
            record.remote_playback_cache = normalized_cache
            changed = True
        return changed

    @classmethod
    def _normalize_remote_playback_cache(cls, value: object) -> RemotePlaybackCacheSettings:
        """Clamp stored values to the supported presets."""

        raw: dict[str, object] = value if isinstance(value, dict) else {}
        enabled = raw.get("enabled") if isinstance(raw.get("enabled"), bool) else True
        recent_video_limit = cls._nearest_preset(
            raw.get("recent_video_limit"),
            default=5,
            presets=REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS,
        )
        storage_limit_mb = cls._nearest_preset(
            raw.get("storage_limit_mb"),
            default=2048,
            presets=REMOTE_PLAYBACK_STORAGE_LIMITS_MB,
        )
        return RemotePlaybackCacheSettings(
            enabled=enabled,
            recent_video_limit=recent_video_limit,
            storage_limit_mb=storage_limit_mb,
        )

    @staticmethod
    def _clamped_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return default
        return max(minimum, min(maximum, parsed))

    @classmethod
    def _nearest_preset(cls, value: object, *, default: int, presets: tuple[int, ...]) -> int:
        parsed = cls._clamped_int(value, default=default, minimum=min(presets), maximum=max(presets))
        return min(presets, key=lambda preset: (abs(preset - parsed), preset))
