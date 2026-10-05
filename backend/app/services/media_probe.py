"""Probe local media with ffprobe and cache the facts per file version.

Facts come from the probed container and streams, never the filename alone. What a client
can play is decided from them by ``playback_decision.decide``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import AppSettings, LibraryItem, MediaArtifact
from app.persistence import write_transaction
from app.services.media_artifacts import MediaArtifactService
from app.services.playback_decision import BROWSER_DEFAULT, decide
from app.services.yt_dlp_service import YtDlpService

PROBE_TIMEOUT_SECONDS = 20
PROBE_OUTPUT_LIMIT = 1_000_000
_probe_slots = threading.BoundedSemaphore(2)
_artifact_locks: dict[str, threading.Lock] = {}
_artifact_locks_guard = threading.Lock()

# Input options for every ffmpeg/ffprobe read of a local media file: only the
# file protocol and real media demuxers, so a playlist/concat file (.m3u8, .m3u,
# ffconcat) saved as media can never steer ffmpeg to other paths or URLs.
LOCAL_MEDIA_DEMUXERS = "mov,matroska,webm,mpegts,avi,asf,mpeg,flv,mp3,aac,flac,ogg,wav"
LOCAL_INPUT_ARGS = ["-protocol_whitelist", "file", "-format_whitelist", LOCAL_MEDIA_DEMUXERS]

TARGET_LUFS = -18.0
MAX_GAIN_DB = 12.0
PEAK_CEILING_DBTP = -1.0


def loudness_gain_db(loudness: Any) -> float | None:
    """Gain toward -18 LUFS, clamped to ±12 dB; a boost never lifts true peak above -1 dBTP."""
    if not isinstance(loudness, dict) or not isinstance(loudness.get("i"), (int, float)):
        return None
    gain = min(max(TARGET_LUFS - loudness["i"], -MAX_GAIN_DB), MAX_GAIN_DB)
    if gain > 0:
        gain = min(gain, max(0.0, PEAK_CEILING_DBTP - float(loudness.get("tp", 0.0))))
    return round(gain, 1) + 0.0  # + 0.0 turns -0.0 into 0.0


class ProbeError(RuntimeError):
    pass


def media_tool(db: Session, name: str) -> str | None:
    """ffmpeg/ffprobe next to the configured ffmpeg, else on PATH."""
    record = db.get(AppSettings, 1)
    ffmpeg = YtDlpService.resolve_ffmpeg_path(record.ffmpeg_path if record else None)
    if ffmpeg:
        base = Path(ffmpeg)
        sibling = (base if base.is_dir() else base.parent) / name
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return shutil.which(name)


def run_ffprobe(ffprobe: str, path: Path) -> dict[str, Any]:
    """ffprobe JSON for one file; bounded time and output, no shell."""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", *LOCAL_INPUT_ARGS, "-print_format", "json", "-show_format", "-show_streams", "--", str(path)],
            capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("probe_timeout") from exc
    # Output is buffered then capped; stream-read with a hard cut if ffprobe output ever grows huge.
    if result.returncode != 0 or len(result.stdout) > PROBE_OUTPUT_LIMIT:
        raise ProbeError("probe_failed")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ProbeError("probe_failed") from exc


_STREAM_TYPES = {"video", "audio", "subtitle"}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate(value: Any) -> float | None:
    """ffprobe "24000/1001" -> 23.976; "0/0" and junk -> None."""
    try:
        num, _, den = str(value).partition("/")
        rate = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if rate > 0 else None


def _text(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if isinstance(value, str) and value.strip() else None


def _stream(raw: dict[str, Any]) -> dict[str, Any]:
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    disposition = raw.get("disposition") if isinstance(raw.get("disposition"), dict) else {}
    dovi = next((d for d in raw.get("side_data_list") or [] if isinstance(d, dict) and "dv_profile" in d), {})
    language = (_text(tags.get("language"), 16) or "").lower()
    return {
        "index": _int(raw.get("index")),
        "type": raw.get("codec_type"),
        "codec": _text(raw.get("codec_name"), 40),
        "profile": _text(raw.get("profile"), 60),
        "level": _int(raw.get("level")),
        "language": language if language and language != "und" else None,
        "title": _text(tags.get("title"), 200),
        "default": bool(disposition.get("default")),
        "forced": bool(disposition.get("forced")),
        "hearing_impaired": bool(disposition.get("hearing_impaired")),
        "width": _int(raw.get("width")),
        "height": _int(raw.get("height")),
        "channels": _int(raw.get("channels")),
        "channel_layout": _text(raw.get("channel_layout"), 40),
        "sample_rate": _int(raw.get("sample_rate")),
        "bit_rate": _int(raw.get("bit_rate")),
        "pix_fmt": _text(raw.get("pix_fmt"), 40),
        "color_transfer": _text(raw.get("color_transfer"), 40),
        "frame_rate": _rate(raw.get("avg_frame_rate") or raw.get("r_frame_rate")),
        "dv_profile": _int(dovi.get("dv_profile")),
        "dv_compat": _int(dovi.get("dv_bl_signal_compatibility_id")),
    }


def normalize(raw: dict[str, Any], filename: str) -> dict[str, Any]:
    """Compact facts (container, first real video/audio stream, subtitle codecs) plus every stream."""
    streams = [s for s in raw.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format") or {}
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    listed = [
        _stream(s) for s in streams
        if s.get("codec_type") in _STREAM_TYPES and _int(s.get("index")) is not None
        and not (s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic"))
    ]
    return {
        "container": str(fmt.get("format_name") or ""),
        # Matroska and WebM share one ffprobe format name; the served suffix decides the browser MIME type.
        "webm_suffix": filename.lower().endswith(".webm"),
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "duration": duration,
        "bit_rate": _int(fmt.get("bit_rate")),
        "audio_tracks": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "subtitles": sorted({str(s.get("codec_name")) for s in streams if s.get("codec_type") == "subtitle"}),
        "streams": listed,
    }


def playback_mode(facts: dict[str, Any]) -> tuple[str, str | None]:
    """(mode, comma-joined TranscodeReasons) for a browser with default caps."""
    decision = decide(BROWSER_DEFAULT, facts)
    return decision.mode, ",".join(decision.reasons) or None


def _fingerprint(path: Path) -> str:
    info = path.stat()
    return f"{info.st_size}:{info.st_mtime_ns}"


def is_stale(cached: dict[str, Any] | None, fingerprint: str) -> bool:
    """Re-probe when the file changed or the cache predates per-stream facts; cached errors stay."""
    cached = cached or {}
    return cached.get("fingerprint") != fingerprint or ("streams" not in cached and "error" not in cached)


class MediaProbeService:
    def __init__(self, db: Session):
        self.db = db

    def facts(self, item: LibraryItem) -> dict[str, Any]:
        """Cached probe facts of a visible item's file; FileNotFoundError when it is not available."""
        artifacts = MediaArtifactService(self.db)
        path, _root = artifacts.locate(item)
        artifact, _ = artifacts.artifact_for(item.id)
        return self.cached_facts(artifact, path)

    def cached_facts(self, artifact: MediaArtifact, path: Path) -> dict[str, Any]:
        with _artifact_locks_guard:
            lock = _artifact_locks.setdefault(artifact.id, threading.Lock())
        with lock:  # one probe per artifact; waiters reuse the result
            self.db.refresh(artifact)
            fingerprint = _fingerprint(path)
            cached = artifact.probe or {}
            if is_stale(cached, fingerprint):
                cached = {"fingerprint": fingerprint, **self._probe(path)}
                with write_transaction(self.db, name="media_probe_cache"):
                    artifact.probe = cached
        return {key: value for key, value in cached.items() if key != "fingerprint"}

    def plan(self, item: LibraryItem) -> dict[str, Any]:
        """Cached facts plus the default-browser decision (kept for PlaybackInfo callers; see ``decide``)."""
        facts = self.facts(item)
        if facts.get("error"):
            return {"mode": "unavailable", "reason": facts["error"], "facts": None}
        mode, reason = playback_mode(facts)
        return {"mode": mode, "reason": reason, "facts": facts}

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = media_tool(self.db, "ffprobe")
        if not ffprobe:
            return {"error": "ffprobe_unavailable"}
        with _probe_slots:
            try:
                return normalize(run_ffprobe(ffprobe, path), path.name)
            except ProbeError as exc:
                return {"error": str(exc)}
