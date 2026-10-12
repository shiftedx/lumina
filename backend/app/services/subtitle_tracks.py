"""Subtitle tracks for the web player: one list per item and WebVTT on demand.

Track ids (media_schemas.SUBTITLE_TRACK_ID_PATTERN): ``e:{stream}`` embedded text, ``s:{n}`` the
item's n-th scanner sidecar, ``i:{stream}`` an image track (burned in by a session, never VTT).
The route appends ``t:{transcript}`` tracks to ``list_tracks`` and answers them in the route; the
order is append-only because Jellyfin clients address MediaStreams by position. Cue times stay
absolute (hls.js timelineOffset keeps media time absolute). ASS styling is lost (JASSUB could restore it).
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import settings
from app.media_schemas import SUBTITLE_TRACK_ID_PATTERN, SubtitleTrack
from app.models import LibraryItem
from app.services.media_artifacts import artifact_file
from app.services.media_probe import LOCAL_INPUT_ARGS
from app.services.playback_decision import IMAGE_SUBTITLE_CODECS

TEXT_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"})
SIDECAR_INPUT_ARGS = ["-protocol_whitelist", "file", "-format_whitelist", "srt,ass,webvtt"]
EXTRACT_TIMEOUT_SECONDS = 300  # An embedded track is read from the whole file once, then cached
MAX_VTT_BYTES = 20 * 1024 * 1024
CACHE_MAX_BYTES = 500 * 1024 * 1024
_extract_slots = threading.BoundedSemaphore(2)


class SubtitleError(RuntimeError):
    pass


def subtitle_cache_root() -> Path:
    return settings.data_dir / "subtitle-cache"


def _url(item_id: str, track_id: str) -> str:
    return f"/api/library/{quote(item_id, safe='')}/subtitle-tracks/{quote(track_id, safe='')}.vtt"


def _label(name: str | None, language: str | None, *, forced: bool = False, hearing_impaired: bool = False, image: bool = False) -> str:
    parts = [name or (language.upper() if language else "Unknown")]
    parts += ["Forced"] * forced + ["SDH"] * hearing_impaired + ["Burned in"] * image
    return " · ".join(parts)


def sidecars(item: LibraryItem) -> list[dict[str, Any] | None]:
    """The item's scanner sidecars by position (``s:{n}``). An entry whose filename is not a plain visible name is None."""
    entries = (item.metadata_json or {}).get("lumina_subtitles")
    if not isinstance(entries, list):
        return []
    return [
        entry if isinstance(entry, dict) and isinstance(entry.get("filename"), str) and entry["filename"] == Path(entry["filename"]).name and not entry["filename"].startswith(".") else None
        for entry in entries
    ]


def list_tracks(facts: dict[str, Any], item: LibraryItem) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []
    for stream in facts.get("streams") or []:
        if stream.get("type") != "subtitle":
            continue
        image = stream.get("codec") in IMAGE_SUBTITLE_CODECS
        if not image and stream.get("codec") not in TEXT_CODECS:
            continue  # closed captions and data tracks have nothing to show
        track_id = f"{'i' if image else 'e'}:{stream['index']}"
        tracks.append(SubtitleTrack(
            id=track_id, language=stream.get("language"), origin="embedded", format="image" if image else "text",
            forced=bool(stream.get("forced")), default=bool(stream.get("default")), hearing_impaired=bool(stream.get("hearing_impaired")),
            label=_label(stream.get("title"), stream.get("language"), forced=bool(stream.get("forced")), hearing_impaired=bool(stream.get("hearing_impaired")), image=image),
            url=None if image else _url(item.id, track_id),
        ))
    for n, entry in enumerate(sidecars(item)):
        if entry is None:
            continue
        track_id = f"s:{n}"
        language = entry.get("language") if isinstance(entry.get("language"), str) else None
        tracks.append(SubtitleTrack(
            id=track_id, language=language, origin="sidecar", format="text", forced=bool(entry.get("forced")),
            default=bool(entry.get("default")), hearing_impaired=bool(entry.get("hearing_impaired")),
            label=_label(None, language, forced=bool(entry.get("forced")), hearing_impaired=bool(entry.get("hearing_impaired"))),
            url=_url(item.id, track_id),
        ))
    return tracks  # transcript tracks (t:) are appended here


def vtt_body(track_id: str, *, facts: dict[str, Any], sidecar_entries: list[dict[str, Any] | None], source: Path, root: Path, artifact_id: str, fingerprint: str, ffmpeg: str) -> str:
    """WebVTT for one text track, extracted once per file version. LookupError when the id names nothing convertible."""
    if not re.fullmatch(SUBTITLE_TRACK_ID_PATTERN, track_id):
        raise LookupError(track_id)
    kind, _, value = track_id.partition(":")
    if kind == "e":
        stream = next((s for s in facts.get("streams") or [] if s.get("index") == int(value) and s.get("type") == "subtitle" and s.get("codec") in TEXT_CODECS), None)
        if stream is None:
            raise LookupError(track_id)
        args, version = [*LOCAL_INPUT_ARGS, "-i", str(source), "-map", f"0:{stream['index']}"], fingerprint
    elif kind == "s":
        n = int(value)
        entry = sidecar_entries[n] if n < len(sidecar_entries) else None
        if entry is None:
            raise LookupError(track_id)
        try:  # refuses symlinks and anything outside the item's storage root
            path = artifact_file(root, (source.parent / entry["filename"]).relative_to(root).as_posix())
        except (FileNotFoundError, ValueError) as exc:
            raise LookupError(track_id) from exc
        args, version = [*SIDECAR_INPUT_ARGS, "-i", str(path)], f"{fingerprint}-{path.stat().st_mtime_ns}"
    else:
        raise LookupError(track_id)  # t: tracks are answered by the route; i: tracks are burned in, never VTT
    cache = subtitle_cache_root() / f"{artifact_id}-{version.replace(':', '-')}-{kind}{value}.vtt"
    if not cache.is_file():
        _extract(ffmpeg, args, cache)
    os.utime(cache)  # prune_cache() drops the least recently used first
    return cache.read_text(encoding="utf-8", errors="replace")


def _extract(ffmpeg: str, args: list[str], cache: Path) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_name(f"{cache.name}.{secrets.token_hex(4)}.part")
    try:
        with _extract_slots:
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", *args, "-c:s", "webvtt", "-f", "webvtt", "-y", str(partial)],
                capture_output=True, timeout=EXTRACT_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL,
            )
        if result.returncode != 0 or not partial.is_file() or partial.stat().st_size > MAX_VTT_BYTES:
            raise SubtitleError("subtitle_failed")
        os.replace(partial, cache)
    except subprocess.TimeoutExpired as exc:
        raise SubtitleError("subtitle_timeout") from exc
    finally:
        partial.unlink(missing_ok=True)


def prune_cache() -> None:
    """Drop the least recently used VTT files until the cache fits CACHE_MAX_BYTES (from sessions.reap())."""
    try:
        files = sorted((path.stat().st_mtime, path.stat().st_size, path) for path in subtitle_cache_root().iterdir() if path.is_file())
    except OSError:
        return  # no cache yet, or a file vanished mid-listing: next tick
    total = sum(size for _mtime, size, _path in files)
    for _mtime, size, path in files:
        if total <= CACHE_MAX_BYTES:
            return
        path.unlink(missing_ok=True)
        total -= size
