"""Original audio first, with bounded compatibility conversions."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from app.models import LibraryItem, User

from app.config import settings
from app.services.media_response import MediaFileResponse, media_content_type

_encoding = threading.BoundedSemaphore(2)
_cache_lock = threading.Lock()
_conversion_locks = tuple(threading.Lock() for _ in range(64))
_leased_files: dict[Path, int] = {}
MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024


class PlaybackFile:
    """A tuple-compatible playback result that pins cached files while retained."""

    def __init__(self, path: Path, content_type: str, *, leased: bool = False) -> None:
        self.path = path
        self.content_type = content_type
        self._leased = leased
        self._release_lock = threading.Lock()

    def __iter__(self) -> Iterator[Path | str]:
        yield self.path
        yield self.content_type

    def release(self) -> None:
        with self._release_lock:
            if not self._leased:
                return
            self._leased = False
        with _cache_lock:
            remaining = _leased_files.get(self.path, 0) - 1
            if remaining > 0:
                _leased_files[self.path] = remaining
            else:
                _leased_files.pop(self.path, None)

    def __del__(self) -> None:
        self.release()


def _lease_locked(path: Path) -> None:
    _leased_files[path] = _leased_files.get(path, 0) + 1


def _cached_playback_locked(target: Path, content_type: str) -> PlaybackFile | None:
    if not target.is_file():
        return None
    os.utime(target, None)
    _lease_locked(target)
    return PlaybackFile(target, content_type, leased=True)


def _probe_audio(source: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    from app.services.media_probe import run_ffprobe
    try:
        probe = run_ffprobe(ffprobe, source)
        return next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), {})
    except (ValueError, OSError, subprocess.SubprocessError):
        return {}


def _integer(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _wav_codec(stream: dict[str, Any]) -> str:
    codec = str(stream.get("codec_name") or "").casefold()
    sample_format = str(stream.get("sample_fmt") or "").casefold()
    if codec.startswith("pcm_f") or sample_format in {"flt", "fltp", "dbl", "dblp"}:
        return "pcm_f32le"
    bits = _integer(stream.get("bits_per_raw_sample")) or _integer(stream.get("bits_per_sample"))
    if bits is None:
        if codec in {"aac", "mp3", "opus", "vorbis"}:
            return "pcm_f32le"
        bits = 32
    if bits <= 16:
        return "pcm_s16le"
    if bits <= 24:
        return "pcm_s24le"
    return "pcm_s32le"


def _evict_locked(root: Path, incoming_size: int) -> None:
    files: list[tuple[Path, os.stat_result]] = []
    for path in root.iterdir():
        if path.suffix not in (".flac", ".wav") or path.name.startswith("encoding-"):
            continue
        try:
            files.append((path, path.stat()))
        except FileNotFoundError:
            continue
    files.sort(key=lambda entry: entry[1].st_mtime)
    total = sum(stat.st_size for _path, stat in files)
    for old, stat in files:
        if total + incoming_size <= MAX_CACHE_BYTES:
            break
        if _leased_files.get(old, 0):
            continue
        try:
            old.unlink()
        except FileNotFoundError:
            pass
        else:
            total -= stat.st_size
    if total + incoming_size > MAX_CACHE_BYTES:
        raise HTTPException(503, "Audio cache capacity is currently in use", headers={"Retry-After": "3"})


def playback_file(source: Path, format_: str = "native") -> PlaybackFile:
    if format_ == "native":
        return PlaybackFile(source, media_content_type(source, audio=True))
    if format_ not in ("flac", "wav", "sonos", "sonos_s1"):
        raise HTTPException(422, "Unsupported audio output")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(503, "Audio conversion is unavailable")
    stat = source.stat()
    digest = hashlib.sha256(f"{source}:{stat.st_size}:{stat.st_mtime_ns}:{format_}".encode()).hexdigest()
    root = settings.data_dir / "music-audio"
    root.mkdir(parents=True, exist_ok=True)
    extension = "wav" if format_ == "wav" else "flac"
    content_type = f"audio/{extension}"
    target = root / f"{digest}.{extension}"
    conversion_lock = _conversion_locks[int(digest[:8], 16) % len(_conversion_locks)]
    with conversion_lock:
        with _cache_lock:
            cached = _cached_playback_locked(target, content_type)
        if cached is not None:
            return cached
        if not _encoding.acquire(timeout=1):
            raise HTTPException(503, "Audio conversion is busy", headers={"Retry-After": "3"})
        temporary: Path | None = None
        try:
            # A colliding stripe may have waited behind another digest. Recheck
            # after taking the global encoder slot as well as before it.
            with _cache_lock:
                cached = _cached_playback_locked(target, content_type)
            if cached is not None:
                return cached
            descriptor, name = tempfile.mkstemp(prefix="encoding-", suffix=f".{extension}", dir=root)
            os.close(descriptor)
            temporary = Path(name)
            stream = _probe_audio(source)
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source), "-vn", "-map", "0:a:0"]
            if format_ in ("sonos", "sonos_s1"):
                rate = _integer(stream.get("sample_rate")) or 48000
                channels = _integer(stream.get("channels")) or 2
                args.extend(["-ar", str(min(48000, rate)), "-ac", str(min(2, channels)), "-sample_fmt", "s16" if format_ == "sonos_s1" else "s32"])
            codec = _wav_codec(stream) if extension == "wav" else "flac"
            args.extend(["-c:a", codec, "-threads", "1", str(temporary)])
            try:
                subprocess.run(args, check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (subprocess.SubprocessError, OSError) as error:
                raise HTTPException(422, "This audio file could not be converted") from error
            size = temporary.stat().st_size
            if not size or size > MAX_CACHE_BYTES:
                raise HTTPException(507, "This track exceeds the audio cache capacity")
            current = source.stat()
            if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                raise HTTPException(409, "The audio source changed during conversion")
            with _cache_lock:
                _evict_locked(root, size)
                os.replace(temporary, target)
                _lease_locked(target)
                temporary = None
            return PlaybackFile(target, content_type, leased=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            _encoding.release()


__all__ = ["MAX_CACHE_BYTES", "PlaybackFile", "playback_file"]


class _MusicAudioResponse(MediaFileResponse):
    """Keep a converted cache entry leased until every response exit path."""

    def __init__(self, *args, release, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._release_music_file = release

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release_music_file()


def audio_response(playback, *, headers: dict[str, str]) -> MediaFileResponse:  # noqa: ANN001
    path, content_type = playback
    release = getattr(playback, "release", lambda: None)
    try:
        return _MusicAudioResponse(path, media_type=content_type, headers=headers, release=release)
    except BaseException:
        release()
        raise


def guard_resources(db: Session, request: Request, user: User, item: LibraryItem, playback, device: str) -> None:
    from app.services.media_probe import MediaProbeService
    from app.services.playback_decision import ANY, ClientCaps, DirectProfile
    from app.services.playback_resources import playback_resources
    if not playback_resources.enabled(db):
        return
    try:
        facts = dict(MediaProbeService(db).facts(item))
        path, _content_type = playback
        duration = facts.get("duration")
        # Compatibility WAV/FLAC can be larger than the source. Budget the file actually sent.
        facts["bit_rate"] = int(path.stat().st_size * 8 / duration) if duration and duration > 0 else None
        caps = ClientCaps(direct=(DirectProfile(ANY, ANY, ANY),), video={}, audio=ANY)
        admission = playback_resources.admit_direct(db, user, request, item.id, device, facts, caps)
        if admission is not None and admission.decision.mode != "direct":
            playback_resources.release(admission)
            raise HTTPException(409, "This audio format cannot fit the household playback bandwidth. Choose a smaller format or download it first.")
        request.state.playback_admission = admission
    except BaseException:
        getattr(playback, "release", lambda: None)()
        raise
