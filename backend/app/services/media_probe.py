"""Probe local media with ffprobe and cache the facts per file version.

Facts come from the probed container and streams, never the filename alone. What a client
can play is decided from them by ``playback_decision.decide``.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import AppSettings, LibraryItem, MediaArtifact
from app.services.chapters import normalize_chapters
from app.persistence import write_transaction
from app.services.media_artifacts import MediaArtifactService
from app.services.mpegts_dolby_vision import M2TS_PACKET_SIZE, parse_m2ts_dolby_vision_pair
from app.services.playback_decision import BROWSER_DEFAULT, decide, has_verified_ten_bit_video
from app.services.yt_dlp_service import YtDlpService

PROBE_TIMEOUT_SECONDS = 20
PROBE_OUTPUT_LIMIT = 1_000_000
PROBE_TERMINATE_GRACE_SECONDS = 0.25
PROBE_CACHE_VERSION = 7
M2TS_DOVI_SCAN_BYTES = 4 * 1024 * 1024
_PROBE_CACHE_VERSION_KEY = "_probe_cache_version"
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


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=PROBE_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()
    else:
        process.wait()


def _read_pipe(file_descriptor: int, size: int) -> bytes:
    return os.read(file_descriptor, size)


def _append_output(buffer: bytearray, chunk: bytes) -> bool:
    remaining = PROBE_OUTPUT_LIMIT - len(buffer)
    buffer.extend(chunk[:remaining])
    return len(chunk) > remaining


def _run_bounded_process(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run one probe while retaining at most ``PROBE_OUTPUT_LIMIT`` bytes per output pipe."""
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ProbeError("probe_failed") from exc
    selector: selectors.BaseSelector | None = None
    pipes: tuple[Any, ...] = ()
    try:
        assert process.stdout is not None and process.stderr is not None
        pipes = (process.stdout, process.stderr)
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        try:
            selector = selectors.DefaultSelector()
        except OSError as exc:
            raise ProbeError("probe_failed") from exc
        deadline = time.monotonic() + (PROBE_TIMEOUT_SECONDS if timeout is None else timeout)
        failure: str | None = None
        cause: OSError | None = None
        for pipe in output:
            selector.register(pipe, selectors.EVENT_READ)
        while selector.get_map():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                failure = "probe_timeout"
                break
            events = selector.select(remaining_time)
            if not events:
                failure = "probe_timeout"
                break
            for key, _mask in events:
                pipe = key.fileobj
                buffered = output[pipe]
                remaining_output = PROBE_OUTPUT_LIMIT - len(buffered)
                try:
                    chunk = _read_pipe(key.fd, min(64 * 1024, remaining_output + 1))
                except OSError as exc:
                    failure, cause = "probe_failed", exc
                    break
                if not chunk:
                    selector.unregister(pipe)
                    pipe.close()
                    continue
                if _append_output(buffered, chunk):
                    failure = "probe_failed"
                    break
            if failure:
                break
        if failure:
            _terminate_and_reap(process)
            error = ProbeError(failure)
            if cause is not None:
                raise error from cause
            raise error
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            _terminate_and_reap(process)
            raise ProbeError("probe_timeout") from exc
        return subprocess.CompletedProcess(
            command, process.returncode, bytes(output[process.stdout]), bytes(output[process.stderr]),
        )
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        for pipe in pipes:
            try:
                pipe.close()
            except Exception:
                pass
        _terminate_and_reap(process)


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


def _attached_picture(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and bool(disposition.get("attached_pic"))


def _hdr_side_data(stream: dict[str, Any]) -> dict[str, bool]:
    """Only retain HDR labels ffprobe states explicitly; an absent label stays unknown."""
    names = [str(entry.get("side_data_type") or "").casefold() for entry in stream.get("side_data_list") or [] if isinstance(entry, dict)]
    return {
        "hdr10": any("mastering display metadata" in name or "content light level metadata" in name for name in names),
        "hdr10plus": any("smpte2094-40" in name or "hdr10+" in name or "hdr dynamic metadata" in name for name in names),
    }


def run_ffprobe(ffprobe: str, path: Path) -> dict[str, Any]:
    """ffprobe JSON for one file; bounded time and output, no shell."""
    result = _run_bounded_process(
        [ffprobe, "-v", "error", *LOCAL_INPUT_ARGS, "-print_format", "json", "-show_format", "-show_programs", "-show_streams", "-show_chapters", "--", str(path)],
    )
    if result.returncode != 0:
        raise ProbeError("probe_failed")
    try:
        raw = json.loads(result.stdout)
    except ValueError as exc:
        raise ProbeError("probe_failed") from exc
    if not isinstance(raw, dict):
        raise ProbeError("probe_failed")
    # Some Matroska HEVC files carry HDR transfer, primaries, or HDR side data
    # only on decoded frames. Restrict the extra bounded probe to plausible HDR
    # streams, including a PQ stream whose static/dynamic metadata is frame-only.
    streams = raw.get("streams")
    if not isinstance(streams, list):
        return raw
    first_video = next((
        stream for stream in streams
        if isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        and not _attached_picture(stream)
    ), None)
    transfer = first_video.get("color_transfer") if first_video else None
    needs_hdr_facts = first_video is not None and (
        (first_video.get("color_space") in {"bt2020nc", "bt2020c"} and not (isinstance(transfer, str) and transfer.strip()))
        or transfer in {"smpte2084", "arib-std-b67"}
    )
    if not needs_hdr_facts:
        return raw
    hdr_stream = first_video
    stream_index = hdr_stream.get("index")
    if not isinstance(stream_index, int) or isinstance(stream_index, bool):
        raise ProbeError("probe_failed")
    frame = _run_bounded_process(
        [
            ffprobe, "-v", "error", *LOCAL_INPUT_ARGS, "-select_streams", str(stream_index),
            "-read_intervals", "%+#1", "-print_format", "json", "-show_frames", "--", str(path),
        ],
    )
    if frame.returncode != 0:
        raise ProbeError("probe_failed")
    try:
        frame_raw = json.loads(frame.stdout)
    except ValueError as exc:
        raise ProbeError("probe_failed") from exc
    if not isinstance(frame_raw, dict) or not isinstance(frame_raw.get("frames"), list):
        raise ProbeError("probe_failed")
    first = next(
        (
            item for item in frame_raw["frames"]
            if isinstance(item, dict)
            and isinstance(item.get("stream_index"), int)
            and not isinstance(item.get("stream_index"), bool)
            and item["stream_index"] == stream_index
        ),
        None,
    )
    if first is None:
        raise ProbeError("probe_failed")
    if not isinstance(first.get("color_transfer"), str) or not first["color_transfer"].strip():
        if not (isinstance(transfer, str) and transfer.strip()):
            raise ProbeError("probe_failed")
    for key in ("color_space", "color_transfer", "color_primaries", "color_range", "pix_fmt"):
        if first.get(key):
            hdr_stream[key] = first[key]
    if isinstance(first.get("side_data_list"), list):
        existing = hdr_stream.get("side_data_list")
        known = existing if isinstance(existing, list) else []
        # Preserve stream-header side data and append only distinct frame facts.
        hdr_stream["side_data_list"] = known + [entry for entry in first["side_data_list"] if isinstance(entry, dict) and entry not in known]
    return raw


_STREAM_TYPES = {"video", "audio", "subtitle"}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dovi_flag(value: Any) -> bool | None:
    """ffprobe writes Dolby Vision presence flags as integer 0 or 1."""
    return {0: False, 1: True}.get(value) if type(value) is int else None


def _rate(value: Any) -> float | None:
    """ffprobe "24000/1001" -> 23.976; "0/0" and junk -> None."""
    try:
        num, _, den = str(value).partition("/")
        rate = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if rate > 0 else None


def _seconds(value: Any) -> float | None:
    """ffprobe emits chapter timestamps as decimal strings."""
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) else None


def _text(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if isinstance(value, str) and value.strip() else None


def _ts_pid(value: Any) -> int | None:
    if type(value) is int:
        return value if 0 < value < 0x1FFF else None
    if not isinstance(value, str):
        return None
    try:
        pid = int(value, 16 if value.lower().startswith("0x") else 10)
    except ValueError:
        return None
    return pid if 0 < pid < 0x1FFF else None


def _program_membership(raw: dict[str, Any]) -> dict[int, int]:
    memberships: dict[int, int] = {}
    ambiguous: set[int] = set()
    for program in raw.get("programs") or []:
        if not isinstance(program, dict) or type(program.get("program_id")) is not int:
            continue
        number = program["program_id"]
        if not 0 < number <= 0xFFFF or not isinstance(program.get("streams"), list):
            continue
        for stream in program["streams"]:
            if not isinstance(stream, dict) or type(stream.get("index")) is not int:
                continue
            index = stream["index"]
            if index in memberships and memberships[index] != number:
                ambiguous.add(index)
            else:
                memberships[index] = number
    return {index: number for index, number in memberships.items() if index not in ambiguous}


def _stream(raw: dict[str, Any], program_numbers: dict[int, int]) -> dict[str, Any]:
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    disposition = raw.get("disposition") if isinstance(raw.get("disposition"), dict) else {}
    dovi = next((d for d in raw.get("side_data_list") or [] if isinstance(d, dict) and "dv_profile" in d), {})
    language = (_text(tags.get("language"), 16) or "").lower()
    index = _int(raw.get("index"))
    normalized = {
        "index": index,
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
        **({"bit_depth": bits} if (bits := _int(raw.get("bits_per_raw_sample")) or _int(raw.get("bits_per_sample"))) else {}),
        "bit_rate": _int(raw.get("bit_rate")),
        "pix_fmt": _text(raw.get("pix_fmt"), 40),
        "color_transfer": _text(raw.get("color_transfer"), 40),
        "color_primaries": _text(raw.get("color_primaries"), 40),
        "color_space": _text(raw.get("color_space"), 40),
        "color_range": _text(raw.get("color_range"), 40),
        "frame_rate": _rate(raw.get("avg_frame_rate") or raw.get("r_frame_rate")),
        "dv_profile": _int(dovi.get("dv_profile")),
        "dv_compat": _int(dovi.get("dv_bl_signal_compatibility_id")),
        "dv_level": _int(dovi.get("dv_level")),
        "dv_rpu_present": _dovi_flag(dovi.get("rpu_present_flag")),
        "dv_el_present": _dovi_flag(dovi.get("el_present_flag")),
        "dv_bl_present": _dovi_flag(dovi.get("bl_present_flag")),
        **_hdr_side_data(raw),
    }
    if (pid := _ts_pid(raw.get("id"))) is not None:
        normalized["ts_pid"] = pid
    if index is not None and index in program_numbers:
        normalized["program_number"] = program_numbers[index]
    return normalized


def normalize(raw: dict[str, Any], filename: str) -> dict[str, Any]:
    """Compact facts (container, first real video/audio stream, subtitle codecs) plus every stream."""
    streams = [s for s in raw.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video" and not _attached_picture(s)), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format") or {}
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    programs = _program_membership(raw)
    listed = [
        _stream(s, programs) for s in streams
        if s.get("codec_type") in _STREAM_TYPES and _int(s.get("index")) is not None
        and not (s.get("codec_type") == "video" and _attached_picture(s))
    ]
    raw_chapters = raw.get("chapters")
    provider_chapters = [
        {
            "start_time": _seconds(chapter.get("start_time")),
            "end_time": _seconds(chapter.get("end_time")),
            "title": (chapter.get("tags") or {}).get("title") if isinstance(chapter.get("tags"), dict) else None,
        }
        for chapter in (raw_chapters if isinstance(raw_chapters, list) else []) if isinstance(chapter, dict)
    ]
    chapters = normalize_chapters(
        provider_chapters=provider_chapters,
        description=None,
        duration=duration,
    )["chapters"]
    return {
        "container": str(fmt.get("format_name") or ""),
        # Matroska and WebM share one ffprobe format name; the served suffix decides the browser MIME type.
        "webm_suffix": filename.lower().endswith(".webm"),
        "m2ts_suffix": filename.lower().endswith(".m2ts"),
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "duration": duration,
        "bit_rate": _int(fmt.get("bit_rate")),
        "audio_tracks": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "subtitles": sorted({str(s.get("codec_name")) for s in streams if s.get("codec_type") == "subtitle"}),
        "streams": listed,
        "chapters": chapters,
    }


def _m2ts_transport_pair(facts: dict[str, Any], path: Path) -> dict[str, Any]:
    """Attach only a fully cross-checked, local M2TS P7 transport association."""
    if facts.get("m2ts_suffix") is not True or str(facts.get("container") or "").split(",") != ["mpegts"]:
        return facts
    try:
        # The parser intentionally permits a partial final packet in its bounded
        # scan window. The actual local file itself must still be complete M2TS.
        if path.stat().st_size % M2TS_PACKET_SIZE:
            return facts
        with path.open("rb") as source:
            pair = parse_m2ts_dolby_vision_pair(source.read(M2TS_DOVI_SCAN_BYTES))
    except OSError:
        return facts
    if pair is None:
        return facts
    videos = [stream for stream in facts.get("streams") or [] if isinstance(stream, dict) and stream.get("type") == "video"]
    if len(videos) != 2:
        return facts
    by_pid = {stream.get("ts_pid"): stream for stream in videos}
    base, enhancement = by_pid.get(pair.base_pid), by_pid.get(pair.enhancement_pid)
    if base is None or enhancement is None or base is enhancement:
        return facts
    if any(stream.get("codec") != "hevc" or not has_verified_ten_bit_video(stream) or stream.get("program_number") != pair.program_number for stream in (base, enhancement)):
        return facts
    # ffprobe can omit one or more EL DOVI fields.  When it does report one,
    # it must agree with the independently CRC-validated PMT descriptor.
    expected = {
        "dv_profile": pair.dv_profile, "dv_compat": pair.dv_compat, "dv_level": pair.dv_level,
        "dv_rpu_present": True, "dv_el_present": True, "dv_bl_present": False,
    }
    if any(enhancement.get(key) is not None and enhancement.get(key) != value for key, value in expected.items()):
        return facts
    facts["dv_transport_pair"] = {
        "packet_size": pair.packet_size, "program_number": pair.program_number, "pmt_pid": pair.pmt_pid,
        "base_pid": pair.base_pid, "enhancement_pid": pair.enhancement_pid,
        "base_stream_index": base.get("index"), "enhancement_stream_index": enhancement.get("index"),
        "dv_profile": pair.dv_profile, "dv_level": pair.dv_level, "dv_compat": pair.dv_compat,
    }
    # `normalize` summaries normally describe the first video stream. A
    # valid transport PMT can declare the EL before its proven base, so keep
    # title/catalogue metadata aligned with the stream playback actually uses.
    facts.update(video_codec=base.get("codec"), width=base.get("width"), height=base.get("height"))
    return facts


def playback_mode(facts: dict[str, Any]) -> tuple[str, str | None]:
    """(mode, comma-joined TranscodeReasons) for a browser with default caps."""
    decision = decide(BROWSER_DEFAULT, facts)
    return decision.mode, ",".join(decision.reasons) or None


def _fingerprint(path: Path) -> str:
    info = path.stat()
    return f"{info.st_size}:{info.st_mtime_ns}"


def is_stale(cached: dict[str, Any] | None, fingerprint: str) -> bool:
    """Re-probe when the file changed or the cache predates current facts; cached errors stay."""
    cached = cached or {}
    return cached.get("fingerprint") != fingerprint or (
        "error" not in cached and (
            "streams" not in cached
            or cached.get(_PROBE_CACHE_VERSION_KEY) != PROBE_CACHE_VERSION
            or ("chapters" not in cached and "chapter_probe_error" not in cached)
        )
    )


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
            # An old successful cache needs a one-time chapter upgrade. Do not erase its
            # usable stream facts when that additive probe cannot run; retry an unavailable
            # executable when it later appears, while other probe failures stay bounded until
            # the file changes.
            if cached.get("chapter_probe_error") == "ffprobe_unavailable" and media_tool(self.db, "ffprobe"):
                cached = {key: value for key, value in cached.items() if key != "chapter_probe_error"}
            if is_stale(cached, fingerprint):
                carry = {}
                if (
                    cached.get("fingerprint") == fingerprint
                    and isinstance(cached.get("streams"), list)
                    and "error" not in cached
                ):
                    carry = {
                        key: cached[key]
                        for key in ("loudness", "_scan_fingerprint")
                        if key in cached
                    }
                refreshed = self._probe(path)
                upgrade = (
                    cached.get("fingerprint") == fingerprint
                    and "error" not in cached and "streams" in cached and "chapters" not in cached
                )
                if upgrade and refreshed.get("error"):
                    cached = {**cached, _PROBE_CACHE_VERSION_KEY: PROBE_CACHE_VERSION, "chapter_probe_error": refreshed["error"]}
                else:
                    cached = {"fingerprint": fingerprint, _PROBE_CACHE_VERSION_KEY: PROBE_CACHE_VERSION, **refreshed}
                    if "error" not in refreshed:
                        cached.update(carry)
                with write_transaction(self.db, name="media_probe_cache"):
                    artifact.probe = cached
        return {
            key: value for key, value in cached.items()
            if key not in {"fingerprint", _PROBE_CACHE_VERSION_KEY, "chapter_probe_error"}
        }

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
                return _m2ts_transport_pair(normalize(run_ffprobe(ffprobe, path), path.name), path)
            except ProbeError as exc:
                return {"error": str(exc)}
