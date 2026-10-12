"""Profanity mute for Lumina's web player.

A Mute range is computed per request from the item's best-timed Transcript and the member's word
list; it is never stored and never a Media segment. Words match casefolded ``\\w+`` tokens, a trailing
``*`` matches any suffix, and YouTube's auto-caption placeholder ``[ __ ]`` always matches. Caption
masking follows the member's preference, never a URL parameter. Infuse and other apps play unfiltered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.media_schemas import MuteRange, ProfanityPref
from app.models import AppSettings, TranscriptCue, User, UserSettings
from app.services.subtitle_sync import merge
from app.services.transcripts import TranscriptService

DEFAULT_WORDS = (
    "fuck*", "motherfuck*", "shit*", "bullshit*", "cunt*", "bitch*", "bastard*", "asshole*", "dickhead*",
    "cock", "cocksucker*", "twat*", "wanker*", "prick", "goddamn*", "piss*", "slut*", "whore*",
)
WORD_PAD_MS = 100  # around an ASR word's own timing
CUE_WINDOW_MS = 400  # either side of the word's estimated time when only cue timing exists
MASK = "****"
YOUTUBE_PLACEHOLDER = "[ __ ]"
FEATURE_KEY = "mute_strong_language"  # owner-approved AI kill switch (ADR 0013); this is its entry point
_TOKEN = re.compile(r"\[ __ \]|\w+")
_VTT_HEADERS = ("WEBVTT", "NOTE", "STYLE", "REGION")


@dataclass(frozen=True)
class WordFilter:
    exact: frozenset[str]
    prefixes: tuple[str, ...]

    def matches(self, token: str) -> bool:
        token = token.casefold()
        return token == YOUTUBE_PLACEHOLDER or token in self.exact or token.startswith(self.prefixes)


def build_filter(words: Iterable[str]) -> WordFilter:
    cleaned = {word.strip().casefold() for word in (*DEFAULT_WORDS, *words) if word.strip()}
    return WordFilter(
        exact=frozenset(word for word in cleaned if not word.endswith("*")),
        prefixes=tuple(sorted(word[:-1] for word in cleaned if word.endswith("*") and len(word) > 1)),
    )


def member_filter(db: Session, user: User) -> WordFilter | None:
    """None when the pref is off, malformed, or the admin has disabled the feature."""
    app_settings = db.get(AppSettings, 1)
    if app_settings is not None and FEATURE_KEY in (app_settings.ai_features_disabled or ()):
        return None
    record = db.query(UserSettings).filter(UserSettings.user_id == user.id).one_or_none()
    raw = (record.ui_prefs or {}).get("profanity") if record is not None else None
    try:
        pref = ProfanityPref.model_validate(raw or {})
    except ValidationError:
        return None
    return build_filter(pref.words) if pref.enabled else None


def mute_ranges(cues: Iterable[tuple[int, int, str, list | None]], word_filter: WordFilter) -> list[tuple[int, int]]:
    """Merged [start_ms, end_ms) ranges: ASR word timings + padding, else a window around the word's place in the cue."""
    ranges: list[tuple[int, int]] = []
    for start, end, text, words in cues:
        if words:
            for word_start, word_end, word in words:
                if any(word_filter.matches(token) for token in _TOKEN.findall(word)):
                    ranges.append((max(0, word_start - WORD_PAD_MS), word_end + WORD_PAD_MS))
            continue
        length = max(1, len(text))
        for match in _TOKEN.finditer(text):
            if word_filter.matches(match.group()):
                at = start + (end - start) * (match.start() + match.end()) / 2 / length
                ranges.append((max(start, round(at - CUE_WINDOW_MS)), min(end, round(at + CUE_WINDOW_MS))))
    return merge(ranges)


def mask_text(text: str, word_filter: WordFilter) -> str:
    return _TOKEN.sub(lambda match: MASK if word_filter.matches(match.group()) else match.group(), text)


def mask_vtt(vtt: str, word_filter: WordFilter) -> str:
    """Mask cue text lines only; headers, blank lines and timing lines pass through."""
    return "\n".join(
        line if not line.strip() or "-->" in line or line.startswith(_VTT_HEADERS) else mask_text(line, word_filter)
        for line in vtt.split("\n")
    )


def item_mute_ranges(db: Session, item_id: str, user: User) -> list[MuteRange]:
    word_filter = member_filter(db, user)
    transcript = TranscriptService(db).timing_transcript(item_id) if word_filter is not None else None
    if transcript is None:
        return []
    rows = db.query(TranscriptCue).filter(TranscriptCue.transcript_id == transcript.id).order_by(TranscriptCue.ordinal).all()
    ranges = mute_ranges(((row.start_ms, row.end_ms, row.text, row.words) for row in rows), word_filter)
    return [MuteRange(start_seconds=start / 1000, end_seconds=end / 1000) for start, end in ranges]
