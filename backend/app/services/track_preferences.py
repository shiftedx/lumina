"""Member track defaults, resolved before the pure playback decision."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserSettings
from app.services.transcripts import to_iso639_2


def language(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 32:
        return None
    return to_iso639_2(value) or value.strip().lower().replace('_', '-').split('-')[0]


def preferences(db: Session, user_id: str) -> dict | None:
    prefs = db.scalar(select(UserSettings.ui_prefs).where(UserSettings.user_id == user_id))
    captions = prefs.get('captions') if isinstance(prefs, dict) else None
    stored = captions.get('tracks') if isinstance(captions, dict) else None
    if not isinstance(stored, dict):
        return None
    result = {key: stored.get(key) is True for key in ('subtitlesEnabled', 'forcedOnly', 'preferSdh')}
    for key in ('audioLanguages', 'subtitleLanguages'):
        raw = stored.get(key)
        result[key] = list(dict.fromkeys(code for value in raw[:10] if (code := language(value)))) if isinstance(raw, list) else []
    return result


def audio_index(streams: list[dict], prefs: dict) -> int | None:
    for wanted in prefs['audioLanguages']:
        match = next((stream for stream in streams if stream.get('type') == 'audio' and language(stream.get('language')) == wanted), None)
        if match is not None:
            return match['index']
    return None


def subtitle_index(streams: list[dict], prefs: dict) -> int | None:
    if not prefs['subtitlesEnabled']:
        return None
    candidates = [stream for stream in streams if stream.get('type') == 'subtitle' and (not prefs['forcedOnly'] or stream.get('forced'))]
    for wanted in prefs['subtitleLanguages'] or [None]:
        matches = [stream for stream in candidates if wanted is None or language(stream.get('language')) == wanted]
        matches.sort(key=lambda stream: (bool(stream.get('hearing_impaired')) != prefs['preferSdh'], not stream.get('default', False)))
        if matches:
            return matches[0]['index']
    return None
