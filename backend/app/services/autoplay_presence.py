"""One durable, public projection of the household episode-autoplay presence policy."""
from __future__ import annotations

from pydantic import ValidationError

from app.media_schemas import AutoplayPresencePolicy
from app.models import AppSettings


def serialize(record: AppSettings | None) -> AutoplayPresencePolicy:
    """Return bounded defaults for fresh or pre-policy settings rows."""
    raw = record.autoplay_presence if record is not None else {}
    try:
        return AutoplayPresencePolicy.model_validate(raw if isinstance(raw, dict) else {})
    except ValidationError:
        # A malformed legacy JSON value must not make member playback unavailable.
        return AutoplayPresencePolicy()


def update(record: AppSettings, policy: AutoplayPresencePolicy) -> None:
    """Persist only this policy, leaving unrelated app settings untouched."""
    record.autoplay_presence = policy.model_dump()
