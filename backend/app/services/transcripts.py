"""Versioned, bounded transcripts for library items.

Source captions written by a download (yt-dlp ``requested_subtitles``) are
parsed with the stdlib into millisecond cues, sanitized, deduped and stored as
immutable revisions. ASR writes through ``store`` with
``source_kind="asr"``. A caption failure never affects the media itself.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.media_schemas import SubtitleTrack
from app.models import LibraryItem, Transcript, TranscriptCue, User
from app.persistence import write_transaction
from app.services.library import LibraryService, resolve_download_output_path
from app.services.library_search import index_item_transcript

logger = logging.getLogger(__name__)

MAX_TRACK_BYTES = 8 * 1024 * 1024
MAX_CUES = 20_000
MAX_CUE_CHARS = 2_000
MAX_MS = 7 * 24 * 3600 * 1000
SOURCE_KINDS = frozenset({"source_caption", "asr", "synced", "translated"})
ORIGIN_BY_KIND = {"source_caption": "caption", "asr": "generated", "synced": "synced", "translated": "translated"}
# Transcripts that mirror an s: sidecar or e: embedded stream; the player already lists those tracks.
MIRROR_PREFIXES = ("sidecar:", "stream:")
# Transcripts whose timings follow the audio best, best first; a translation's text is another language.
TIMING_KINDS = ("asr", "synced", "source_caption")
_KIND_SUFFIX = {"asr": " (generated)", "synced": " (synced)", "translated": " (translated)"}
TEXT_TRACK_EXTS = frozenset({"vtt", "srt"})

Cue = tuple[int, int, str]

_TIMESTAMP = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{3})$")
_TAG = re.compile(r"<[^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_LANGUAGE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# ISO 639-2/B code, ISO 639-1 code, English name, then aliases (639-2/T codes and the full names
# ASR verbose_json returns). Shared with the Jellyfin API's MediaStream Language mapping.
_LANGUAGE_ROWS = """
afr af Afrikaans
alb sq Albanian sqi
ara ar Arabic
arm hy Armenian hye
baq eu Basque eus
ben bn Bengali bangla
bul bg Bulgarian
cat ca Catalan valencian
chi zh Chinese zho mandarin cantonese
cze cs Czech ces
dan da Danish
dut nl Dutch nld flemish
eng en English
est et Estonian
fil tl Filipino tgl tagalog
fin fi Finnish
fre fr French fra
geo ka Georgian kat
ger de German deu
gre el Greek ell
guj gu Gujarati
heb he Hebrew iw
hin hi Hindi
hrv hr Croatian
hun hu Hungarian
ice is Icelandic isl
ind id Indonesian in
ita it Italian
jpn ja Japanese
kan kn Kannada
kaz kk Kazakh
kor ko Korean
lat la Latin
lav lv Latvian
lit lt Lithuanian
mac mk Macedonian mkd
mal ml Malayalam
mar mr Marathi
may ms Malay msa
nor no Norwegian nob nb nno nn bokmal nynorsk
pan pa Punjabi panjabi
per fa Persian fas farsi
pol pl Polish
por pt Portuguese
rum ro Romanian ron moldavian moldovan
rus ru Russian
slo sk Slovak slk
slv sl Slovenian slovene
spa es Spanish castilian
srp sr Serbian
swa sw Swahili
swe sv Swedish
tam ta Tamil
tel te Telugu
tha th Thai
tur tr Turkish
ukr uk Ukrainian
urd ur Urdu
vie vi Vietnamese
wel cy Welsh cym
"""
ISO_LANGUAGES: dict[str, tuple[str, str]] = {}
for _row in _LANGUAGE_ROWS.strip().splitlines():
    _code, _short, _name, *_aliases = _row.split()
    for _key in (_code, _short, _name.casefold(), *_aliases):
        ISO_LANGUAGES[_key] = (_code, _name)


class TranscriptError(ValueError):
    pass


def _ms(value: str) -> int:
    match = _TIMESTAMP.match(value)
    if not match:
        raise TranscriptError("Malformed cue timestamp")
    hours, minutes, seconds, millis = (int(part or 0) for part in match.groups())
    if minutes > 59 or seconds > 59:
        raise TranscriptError("Malformed cue timestamp")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _clean(line: str) -> str:
    """Captions are plain text: drop markup, entities, controls and extra whitespace."""
    return " ".join(_CONTROL.sub(" ", html.unescape(_TAG.sub("", line))).split())


def _parse_text_track(raw: str) -> list[tuple[int, int, list[str]]]:
    """WebVTT or SRT: every block with a ``-->`` line is a cue; others are headers/notes/indices."""
    cues = []
    for block in re.split(r"\n\n+", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.split("\n")
        arrow = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if arrow is None:
            continue
        start, _, rest = lines[arrow].partition("-->")
        end = (rest.split() or [""])[0]  # VTT cue settings follow the end time
        cues.append((_ms(start.strip()), _ms(end), [_clean(line) for line in lines[arrow + 1 :]]))
    return cues


def _parse_json3(raw: str) -> list[tuple[int, int, list[str]]]:
    try:
        events = json.loads(raw).get("events")
    except (ValueError, AttributeError, RecursionError) as exc:
        raise TranscriptError("Malformed json3 track") from exc
    if not isinstance(events, list):
        raise TranscriptError("Malformed json3 track")
    cues = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue  # window/style events carry no text
        start, duration = event.get("tStartMs"), event.get("dDurationMs", 0)
        if not isinstance(start, int) or not isinstance(duration, int) or isinstance(start, bool):
            raise TranscriptError("Malformed json3 timing")
        text = "".join(seg.get("utf8", "") for seg in event["segs"] if isinstance(seg, dict) and isinstance(seg.get("utf8"), str))
        cues.append((start, start + duration, [_clean(line) for line in text.split("\n")]))
    return cues


def parse_caption(data: bytes, ext: str) -> list[Cue]:
    """Parse one bounded caption track into ordered, deduped cues or raise TranscriptError."""
    if len(data) > MAX_TRACK_BYTES:
        raise TranscriptError("Caption track exceeds the size limit")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TranscriptError("Caption track is not UTF-8") from exc
    parsed = _parse_json3(raw) if ext == "json3" else _parse_text_track(raw)
    if len(parsed) > MAX_CUES:
        raise TranscriptError("Caption track has too many cues")
    parsed.sort(key=lambda cue: (cue[0], cue[1]))
    cues: list[Cue] = []
    previous: set[str] = set()
    for start, end, lines in parsed:
        if not (0 <= start <= end <= MAX_MS):
            raise TranscriptError("Cue timing out of bounds")
        lines = [line for line in lines if line]
        # Rolling auto-captions repeat the previous cue's line before the new one.
        fresh = [line for line in lines if line not in previous]
        if lines:
            previous = set(lines)
        if not fresh:
            if cues and lines:
                cues[-1] = (cues[-1][0], max(cues[-1][1], end), cues[-1][2])
            continue
        cues.append((start, end, " ".join(fresh)[:MAX_CUE_CHARS]))
    if not cues:
        raise TranscriptError("Caption track has no cues")
    return cues


def normalize_language(value: object) -> str:
    return value if isinstance(value, str) and _LANGUAGE.match(value) else "und"


def _language(value: object) -> tuple[str, str] | None:
    """Any code, name or region tag ("en", "eng", "english", "en-US", "pt_BR") → (639-2/B, English name)."""
    if not isinstance(value, str) or not value.strip():
        return None
    return ISO_LANGUAGES.get(re.split(r"[-_]", value.strip().casefold(), maxsplit=1)[0])


def to_iso639_2(value: object) -> str | None:
    known = _language(value)
    return known[0] if known else None


def language_name(value: object) -> str | None:
    known = _language(value)
    return known[1] if known else None


def language_label(code: object, kind: str) -> str:
    """"English (generated)", "Spanish (translated)", plain "English" for source captions."""
    raw = code.strip() if isinstance(code, str) else ""
    name = language_name(code) or (raw if raw not in ("", "und") else "Unknown")
    return name + _KIND_SUFFIX.get(kind, "")


def _stamp(ms: int, separator: str) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render(cues: Iterable[Cue], fmt: str) -> str:
    """SRT or WebVTT for stored cues. Text is collapsed to one line, so no cue can inject another;
    VTT escapes ``& < >`` and SRT defuses a stray arrow."""
    if fmt not in ("srt", "vtt"):
        raise TranscriptError("Unsupported subtitle format")
    separator = "," if fmt == "srt" else "."
    blocks = []
    for number, (start, end, text) in enumerate(cues, 1):
        line = " ".join(text.split())
        line = html.escape(line, quote=False) if fmt == "vtt" else line.replace("-->", "->")
        timing = f"{_stamp(start, separator)} --> {_stamp(end, separator)}"
        blocks.append(f"{number}\n{timing}\n{line}" if fmt == "srt" else f"{timing}\n{line}")
    return ("WEBVTT\n\n" if fmt == "vtt" else "") + "\n\n".join(blocks) + "\n"


def jellyfin_subtitle_streams(
    tracks: list[SubtitleTrack], first_index: int, delivery_url: Callable[[int], str],
) -> list[dict[str, Any]]:
    """External MediaStreams for transcript tracks; the Jellyfin API appends them after embedded and sidecar streams."""
    return [
        {
            "Index": index, "Type": "Subtitle", "Codec": "subrip", "Language": track.language,
            "Title": track.label, "DisplayTitle": track.label,
            "IsDefault": False, "IsForced": False, "IsHearingImpaired": False, "IsExternal": True,
            "IsTextSubtitleStream": True, "SupportsExternalStream": True,
            "DeliveryMethod": "External", "DeliveryUrl": delivery_url(index),
        }
        for index, track in enumerate(tracks, first_index)
    ]


class TranscriptService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def store(
        self,
        library_item_id: str,
        *,
        language: str,
        source_kind: str,
        cues: list[Cue],
        model_label: str | None = None,
        derived_from: str | None = None,
        words: list[list[list] | None] | None = None,
    ) -> Transcript:
        """Store cues as a new immutable revision, or return the revision with the same digest.

        ``derived_from`` records a source transcript id or ``sidecar:{filename}`` (≤ 80 chars).
        ``words`` (ASR word timings ``[[start_ms, end_ms, word], ...]``) aligns with ``cues`` by index.
        """
        if source_kind not in SOURCE_KINDS or not cues or len(cues) > MAX_CUES:
            raise TranscriptError("Invalid transcript")
        if words is not None and len(words) != len(cues):
            raise TranscriptError("Word timings do not match the cues")
        language = normalize_language(language)
        digest = hashlib.sha256(json.dumps(cues, ensure_ascii=False).encode()).hexdigest()
        scope = (
            Transcript.library_item_id == library_item_id,
            Transcript.language == language,
            Transcript.source_kind == source_kind,
        )
        with write_transaction(self.db, name="transcript_store"):
            existing = self.db.query(Transcript).filter(*scope, Transcript.source_digest == digest).one_or_none()
            if existing is not None:
                return existing
            revision = (self.db.query(func.max(Transcript.revision)).filter(*scope).scalar() or 0) + 1
            transcript = Transcript(
                id=str(uuid.uuid4()),
                library_item_id=library_item_id,
                language=language,
                source_kind=source_kind,
                revision=revision,
                source_digest=digest,
                cue_count=len(cues),
                model_label=model_label,
                derived_from=derived_from[:80] if derived_from else None,
            )
            self.db.add(transcript)
            self.db.add_all(
                TranscriptCue(
                    transcript_id=transcript.id, ordinal=index, start_ms=start, end_ms=end, text=text,
                    words=words[index] if words else None,
                )
                for index, (start, end, text) in enumerate(cues)
            )
            self.db.flush()
            index_item_transcript(self.db, library_item_id)
        return transcript

    def ingest_download_captions(self, pairs: Iterable[tuple[dict[str, Any], LibraryItem]]) -> None:
        """Store the caption files a download wrote next to its media. Never raises."""
        for info, item in pairs:
            tracks = info.get("requested_subtitles")
            media_path = resolve_download_output_path(info)
            if not isinstance(tracks, dict) or not media_path:
                continue
            media_dir = Path(media_path).resolve().parent
            for language, track in tracks.items():
                if language == "live_chat" or not isinstance(track, dict):
                    continue
                ext, file_path = track.get("ext"), track.get("filepath")
                if ext not in TEXT_TRACK_EXTS | {"json3"} or not isinstance(file_path, str):
                    continue
                try:
                    path = Path(file_path).resolve()
                    if path.parent != media_dir:
                        raise TranscriptError("Caption file is outside the media folder")
                    with path.open("rb") as handle:
                        data = handle.read(MAX_TRACK_BYTES + 1)
                    self.store(item.id, language=language, source_kind="source_caption", cues=parse_caption(data, ext))
                except Exception as exc:  # noqa: BLE001 - a bad track never fails the download
                    logger.warning("Skipped caption track %s for library item %s: %s", language, item.id, exc)

    # --- reads: every access goes through the library item's own visibility ---

    def visible_item(self, item_id: str, user: User) -> LibraryItem | None:
        return LibraryService(self.db).get_item(item_id, user)

    def visible_transcript(self, transcript_id: str, user: User) -> Transcript | None:
        transcript = self.db.get(Transcript, transcript_id)
        if transcript is None or self.visible_item(transcript.library_item_id, user) is None:
            return None
        return transcript

    def list_for_item(self, item_id: str) -> list[Transcript]:
        return (
            self.db.query(Transcript)
            .filter(Transcript.library_item_id == item_id)
            .order_by(Transcript.language, Transcript.source_kind, Transcript.revision.desc(), Transcript.id)
            .all()
        )

    def cues(self, transcript_id: str, *, after: int, limit: int) -> list[TranscriptCue]:
        return (
            self.db.query(TranscriptCue)
            .filter(TranscriptCue.transcript_id == transcript_id, TranscriptCue.ordinal > after)
            .order_by(TranscriptCue.ordinal)
            .limit(limit)
            .all()
        )

    def search(self, transcript_id: str, query: str, *, limit: int) -> list[TranscriptCue]:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # LIKE scan over one transcript's bounded cues; add FTS if cross-transcript search lands.
        return (
            self.db.query(TranscriptCue)
            .filter(TranscriptCue.transcript_id == transcript_id, TranscriptCue.text.ilike(pattern, escape="\\"))
            .order_by(TranscriptCue.ordinal)
            .limit(limit)
            .all()
        )

    # --- subtitle tracks ---

    def track_transcripts(self, item_id: str) -> list[Transcript]:
        """Latest revision of each (language, source_kind), ordered by first appearance so indices only append.

        Only ``source_caption`` rows mirror an already-listed ``s:``/``e:`` track: a
        ``synced``/``translated`` row derived from that same mirror is a real result and stays a ``t:`` track.
        """
        latest: dict[tuple[str, str], Transcript] = {}
        rows = (
            self.db.query(Transcript)
            .filter(Transcript.library_item_id == item_id)
            .order_by(Transcript.created_at, Transcript.revision, Transcript.id)
            .all()
        )
        for row in rows:
            if row.source_kind == "source_caption" and row.derived_from and row.derived_from.startswith(MIRROR_PREFIXES):
                continue
            key = (row.language, row.source_kind)
            if key not in latest or row.revision > latest[key].revision:
                latest[key] = row  # a dict keeps the key's first position when its value is replaced
        return list(latest.values())

    def subtitle_tracks(self, item_id: str) -> list[SubtitleTrack]:
        """The ``t:`` tracks the shared track list appends after ``e:``, ``s:`` and ``i:``."""
        return [
            SubtitleTrack(
                id=f"t:{row.id}",
                label=language_label(row.language, row.source_kind),
                language=to_iso639_2(row.language),
                origin=ORIGIN_BY_KIND[row.source_kind],
                format="text",
                url=f"/api/library/{item_id}/subtitle-tracks/t:{row.id}.vtt",
            )
            for row in self.track_transcripts(item_id)
        ]

    def cue_tuples(self, transcript_id: str) -> list[Cue]:
        rows = self.db.query(TranscriptCue).filter(TranscriptCue.transcript_id == transcript_id).order_by(TranscriptCue.ordinal).all()
        return [(row.start_ms, row.end_ms, row.text) for row in rows]

    def render_transcript(self, transcript_id: str, fmt: str) -> str:
        return render(self.cue_tuples(transcript_id), fmt)

    def timing_transcript(self, item_id: str) -> Transcript | None:
        """One transcript per item for timing work and moment search (segments, profanity, FTS)."""
        rows = (
            self.db.query(Transcript)
            .filter(Transcript.library_item_id == item_id, Transcript.source_kind.in_(TIMING_KINDS))
            .order_by(Transcript.created_at.desc())
            .all()
        )
        return min(rows, key=lambda row: TIMING_KINDS.index(row.source_kind), default=None)  # min keeps the newest of a kind
