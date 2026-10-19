"""Bounded fallback stills for local episodes without supplied artwork.

The rendition worker calls :func:`ensure`; this module never starts a worker and
never writes to a library root.  A generated still is disposable private cache
content whose identity includes the scanner's artifact fingerprint.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, load_only

from app.config import settings
from app.db import SessionLocal
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, TitleArtwork, utcnow
from app.persistence import read_regular_file, write_transaction
from app.services import art_urls
from app.services.media_artifacts import artifact_file
from app.services.media_titles import apply_field
from app.services.media_probe import LOCAL_INPUT_ARGS, media_tool
from app.services.storage_roots import ONLINE_STATES, StorageRootService

MAX_BYTES = 2 * 1024 * 1024
CACHE_CAP_BYTES = 256 * 1024 * 1024
TIMEOUT_SECONDS = 15.0
RETRY_AFTER = timedelta(hours=1)
_KEY = re.compile(r"^[0-9a-f]{64}$")
_SWEEP_INTERVAL_BYTES = 8 * 1024 * 1024
_sweep_lock = threading.Lock()
_written_since_sweep = 0


@dataclass(frozen=True)
class Candidate:
    title_id: str
    source_key: str
    fingerprint: str
    artifact_id: str
    missing: bool


def cache_root() -> Path:
    return settings.data_dir / "artwork-cache" / "episode-stills"


def _cache_path(key: str) -> Path:
    if not _KEY.fullmatch(key):
        raise ValueError("invalid episode-still key")
    return cache_root() / key[:2] / f"{key}.jpg"


def _private_dir(path: Path) -> Path:
    """Create the cache path without following a symlink below app data."""
    try:
        relative = path.relative_to(settings.data_dir)
    except ValueError as exc:
        raise OSError("Episode still cache escapes app data.") from exc
    current = settings.data_dir
    for part in relative.parts:
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise OSError("Episode still cache is not private.")
    return path


def _fingerprint(artifact: MediaArtifact) -> str | None:
    if not artifact.id or artifact.size is None or artifact.mtime_ns is None:
        return None
    if artifact.size < 0 or artifact.mtime_ns < 0:
        return None
    return f"frame:v1:{artifact.id}:{artifact.size}:{artifact.mtime_ns}"


def _is_generated(entry: object) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("frame"), str) and isinstance(entry.get("tag"), str)


def _candidate(title: MediaTitle, artifact: MediaArtifact, root: StorageRoot) -> Candidate | None:
    """The one deterministic eligible artifact for a title, using scanner facts only."""
    if title.type != "episode" or title.locked or artifact.lifecycle != "available" or not root.enabled:
        return None
    if isinstance(root.observation, dict) and root.observation.get("state") not in (None, *ONLINE_STATES):
        return None
    images = title.images or {}
    entry = images.get("Primary")
    if isinstance(entry, dict) and entry.get("removed"):
        return None
    source = (title.field_sources or {}).get("images.Primary")
    if source in {"user", "nfo"}:
        return None
    if entry is not None and not _is_generated(entry):
        return None
    fingerprint = _fingerprint(artifact)
    if fingerprint is None:
        return None
    return Candidate(
        title_id=title.id,
        source_key=art_urls.source_key(title.id, "Primary", fingerprint),
        fingerprint=fingerprint,
        artifact_id=artifact.id,
        missing=entry is None,
    )


def _rows(db: Session, title_id: str | None = None):
    image_frame = func.json_extract(MediaTitle.images, "$.Primary.frame")
    image_primary = func.json_extract(MediaTitle.images, "$.Primary")
    field_source = func.json_extract(MediaTitle.field_sources, '$."images.Primary"')
    query = (
        select(MediaTitle, MediaArtifact, StorageRoot)
        .join(LibraryItem, LibraryItem.title_id == MediaTitle.id)
        .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
        .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
        .where(
            MediaTitle.type == "episode", LibraryItem.extra_type.is_(None), LibraryItem.status == "available",
            MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True), MediaTitle.locked.is_(False),
            or_(image_primary.is_(None), image_frame.is_not(None)), func.coalesce(field_source, "").notin_(("user", "nfo")),
        )
        .order_by(MediaTitle.id, LibraryItem.id, MediaArtifact.id)
    )
    if title_id is not None:
        return query.where(MediaTitle.id == title_id)
    return query.options(
        load_only(MediaTitle.id, MediaTitle.type, MediaTitle.images, MediaTitle.locked, MediaTitle.field_sources),
        load_only(MediaArtifact.id, MediaArtifact.size, MediaArtifact.mtime_ns, MediaArtifact.lifecycle),
        load_only(StorageRoot.id, StorageRoot.enabled, StorageRoot.observation),
    )


def _cached_keys() -> set[str]:
    root = cache_root()
    # Scan the fixed two-level cache layout once; never walk arbitrary cache descendants.
    keys: set[str] = set()
    try:
        buckets = list(root.iterdir())
    except OSError:
        return keys
    for bucket in buckets:
        if bucket.is_symlink() or not bucket.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", bucket.name):
            continue
        with contextlib.suppress(OSError):
            for path in bucket.iterdir():
                match = re.fullmatch(r"([0-9a-f]{64})\.jpg", path.name)
                if match and not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_BYTES:
                    keys.add(match[1])
    return keys


def scan(db: Session, *, include_deferred: bool = False) -> list[Candidate]:
    """Eligible still jobs, without statting media files; a cached current generated still is omitted."""
    cached = _cached_keys()
    artwork = {} if include_deferred else {
        (row.title_id, row.image_type): row for row in db.scalars(select(TitleArtwork).where(TitleArtwork.image_type == "Primary"))
    }
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for title, artifact, root in db.execute(_rows(db)).yield_per(500):
        if title.id in seen:
            continue
        candidate = _candidate(title, artifact, root)
        if candidate is None:
            continue
        seen.add(title.id)
        entry = (title.images or {}).get("Primary")
        if _is_generated(entry) and entry.get("tag") == candidate.fingerprint and candidate.source_key in cached:
            continue
        if not include_deferred:
            row = artwork.get((candidate.title_id, "Primary"))
            if row is not None and row.source_key == candidate.source_key and row.state == "failed" and row.updated_at >= utcnow() - RETRY_AFTER:
                continue
        candidates.append(candidate)
    return candidates


def _current(db: Session, title_id: str) -> tuple[Candidate, MediaTitle, MediaArtifact, StorageRoot] | None:
    for title, artifact, root in db.execute(_rows(db, title_id)):
        candidate = _candidate(title, artifact, root)
        if candidate is not None:
            return candidate, title, artifact, root
    return None


def candidate_key(title_id: str) -> str | None:
    """The current eligible fallback key, for the worker's ordinary offline backoff."""
    with SessionLocal() as db:
        current = _current(db, title_id)
        return current[0].source_key if current is not None else None


def _verified_file(artifact: MediaArtifact, root: StorageRoot, candidate: Candidate) -> Path:
    path = artifact_file(root.path, artifact.relative_path)
    stat = path.stat()
    if stat.st_size != artifact.size or stat.st_mtime_ns != artifact.mtime_ns or _fingerprint(artifact) != candidate.fingerprint:
        raise FileNotFoundError("Episode changed since its library scan")
    return path


def _seek_seconds(artifact: MediaArtifact) -> float:
    duration = (artifact.probe or {}).get("duration") if isinstance(artifact.probe, dict) else None
    if not isinstance(duration, (int, float)) or duration <= 0:
        return 60.0
    return min(60.0, max(1.0, float(duration) * 0.1))


def _command(ffmpeg: str, source: Path, destination: Path, seek: float) -> list[str]:
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-filter_threads", "1", "-threads", "1",
        *LOCAL_INPUT_ARGS, "-ss", f"{seek:.3f}", "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-an",
    ]
    return [*command, "-vf", "scale='min(iw,800)':-2:flags=lanczos", "-c:v", "mjpeg", "-q:v", "4", "-threads", "1",
            "-fs", str(MAX_BYTES + 1), "-f", "image2", str(destination)]


def _run(command: list[str], *, timeout: float, on_process: Callable[[subprocess.Popen | None], None] | None) -> int:
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return -1
    if on_process is not None:
        on_process(process)
    with contextlib.suppress(OSError):
        os.setpriority(os.PRIO_PROCESS, process.pid, 19)
    try:
        process.communicate(timeout=timeout)
        return process.returncode or 0
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(OSError):
            process.wait()
        return -1
    finally:
        if on_process is not None:
            on_process(None)


def _render(ffmpeg: str, source: Path, destination: Path, artifact: MediaArtifact, stop: Callable[[], bool] | None,
            on_process: Callable[[subprocess.Popen | None], None] | None) -> bool:
    seek = _seek_seconds(artifact)
    if stop is not None and stop():
        return False
    code = _run(_command(ffmpeg, source, destination, seek), timeout=TIMEOUT_SECONDS, on_process=on_process)
    try:
        return code == 0 and destination.is_file() and not destination.is_symlink() and 0 < destination.stat().st_size <= MAX_BYTES
    except OSError:
        return False


def _mark_failed(title_id: str, candidate: Candidate) -> None:
    with SessionLocal() as db, write_transaction(db, name="episode_still_failed"):
        current = _current(db, title_id)
        if current is None or current[0].source_key != candidate.source_key:
            return
        row = db.get(TitleArtwork, (title_id, "Primary"))
        if row is None:
            db.add(TitleArtwork(title_id=title_id, image_type="Primary", source_key=candidate.source_key, state="failed", error="frame_failed"))
        else:
            row.source_key, row.state, row.error = candidate.source_key, "failed", "frame_failed"
            row.updated_at = utcnow()  # a repeated identical failure starts a new bounded retry window


def ensure(title_id: str, *, stop: Callable[[], bool] | None = None,
           on_process: Callable[[subprocess.Popen | None], None] | None = None) -> bool:
    """Generate one source-less episode still. False means no safe still was made or retained."""
    with SessionLocal() as db:
        current = _current(db, title_id)
        if current is None:
            return False
        candidate, title, artifact, root = current
        row = db.get(TitleArtwork, (title_id, "Primary"))
        if row is not None and row.source_key == candidate.source_key and row.state == "failed" and row.updated_at >= utcnow() - RETRY_AFTER:
            return False
        initial_primary = (title.images or {}).get("Primary")
        path = _cache_path(candidate.source_key)
        try:
            if not StorageRootService(db).is_online(root):
                return False
            source = _verified_file(artifact, root, candidate)
            cached = path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES
        except (FileNotFoundError, OSError):
            return False
        if not cached:
            try:
                folder = _private_dir(path.parent)
                temporary = folder / f".{candidate.source_key}.{os.getpid()}.{time.time_ns()}.tmp"
                ffmpeg = media_tool(db, "ffmpeg")
                made = _render(ffmpeg, source, temporary, artifact, stop, on_process) if ffmpeg else False
                if not made:
                    with contextlib.suppress(FileNotFoundError):
                        temporary.unlink()
                    if stop is None or not stop():
                        _mark_failed(title_id, candidate)
                    return False
                if stop is not None and stop():
                    temporary.unlink(missing_ok=True)
                    return False
                os.replace(temporary, path)
                _enforce_cap(path.stat().st_size)
            except OSError:
                if stop is None or not stop():
                    _mark_failed(title_id, candidate)
                return False
    # The media and title can change while ffmpeg runs. Publish only if this is still its exact fallback source.
    with SessionLocal() as db, write_transaction(db, name="episode_still_publish"):
        current = _current(db, title_id)
        if current is None or current[0].source_key != candidate.source_key:
            return False
        _, fresh_title, fresh_artifact, fresh_root = current
        if (fresh_title.images or {}).get("Primary") != initial_primary:
            return False
        try:
            if not StorageRootService(db).is_online(fresh_root):
                return False
            _verified_file(fresh_artifact, fresh_root, candidate)
        except FileNotFoundError:
            return False
        entry = {"frame": candidate.artifact_id, "tag": candidate.fingerprint}
        row = db.get(TitleArtwork, (title_id, "Primary"))
        if row is not None and row.source_key == candidate.source_key and row.state == "failed":
            db.delete(row)  # an aged fallback failure is now retried by the normal rendition path
        if (fresh_title.images or {}).get("Primary") == entry:
            return True
        if not apply_field(fresh_title, "images.Primary", entry, "path"):
            return False
    return True


def image_bytes(entry: Mapping[str, object], title_id: str) -> bytes:
    """Read only the computed private JPEG for this title/source; never accepts a caller path."""
    tag = entry.get("tag")
    if not isinstance(tag, str):
        raise FileNotFoundError("Generated still has no tag")
    key = art_urls.source_key(title_id, "Primary", tag)
    try:
        return read_regular_file(_cache_path(key), MAX_BYTES)
    except OSError as exc:
        raise FileNotFoundError("Generated still is unavailable") from exc


def sweep() -> None:
    """Evict least-recently-modified generated stills over the private 256 MiB cache cap."""
    root = cache_root()
    if root.is_symlink():
        return
    paths: list[tuple[float, int, Path]] = []
    try:
        buckets = list(root.iterdir())
    except OSError:
        return
    total = 0
    for bucket in buckets:
        if bucket.is_symlink() or not bucket.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", bucket.name):
            continue
        with contextlib.suppress(OSError):
            for path in bucket.iterdir():
                if not re.fullmatch(r"[0-9a-f]{64}\.jpg", path.name) or path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                total += stat.st_size
                paths.append((stat.st_mtime, stat.st_size, path))
    for _, size, path in sorted(paths):
        if total <= CACHE_CAP_BYTES:
            break
        with contextlib.suppress(OSError):
            path.unlink()
            total -= size


def _enforce_cap(written: int) -> None:
    """Keep the frame cache bounded during a backfill without rescanning it per still."""
    global _written_since_sweep
    with _sweep_lock:
        # The first write after startup also trims an existing over-cap cache. Later writes may overshoot by at most 8 MiB.
        if _written_since_sweep == 0 or _written_since_sweep + written >= _SWEEP_INTERVAL_BYTES:
            sweep()
            _written_since_sweep = 0
        _written_since_sweep += written
