"""Bounded, disposable storage for photo derivatives and crash staging."""
from __future__ import annotations

import os
import shutil
import stat
import threading
import time
from pathlib import Path

from app.config import settings
from app.services import photo_derivatives

_TRIM_INTERVAL_SECONDS = 60 * 60
_lock = threading.Lock()
_last_trim = 0.0
_known_total: int | None = None


def _regular_files(root: Path):  # noqa: ANN202 - compact private iterator
    if not root.exists():
        return
    for first in root.iterdir():
        if first.is_symlink() or not first.is_dir():
            continue
        for candidate in first.iterdir():
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and candidate.suffix == ".webp":
                yield candidate, info


def recover() -> None:
    """Remove only private transient files left by an interrupted photo job."""
    root = photo_derivatives.photos_root()
    for name in (".staging", ".snapshots"):
        shutil.rmtree(root / name, ignore_errors=True)
    if root.exists():
        for candidate in root.glob(".upload-*.tmp"):
            try:
                if stat.S_ISREG(candidate.lstat().st_mode):
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue
    trim(force=True)


def touch(key: str) -> None:
    """Record a successful cache hit without changing catalog state."""
    try:
        os.utime(photo_derivatives.derivative_path(key), None, follow_symlinks=False)
    except OSError:
        pass


def purge(keys: set[str]) -> None:
    for key in keys:
        try:
            photo_derivatives.derivative_path(key).unlink(missing_ok=True)
        except (FileNotFoundError, OSError):
            pass


def published(bytes_added: int) -> None:
    """Account for a completed atomic publication and enforce the hard cap."""
    global _known_total
    if bytes_added <= 0:
        return
    with _lock:
        ceiling = max(0, settings.photo_derivative_cache_max_bytes)
        if _known_total is None:
            over = ceiling != 0
        else:
            _known_total += bytes_added
            over = ceiling != 0 and _known_total > ceiling
    if over:
        trim(force=True)


def trim(*, force: bool = False) -> None:
    """Evict least-recently-used derivatives down to the configured byte cap."""
    global _known_total, _last_trim
    now = time.monotonic()
    with _lock:
        if not force and now - _last_trim < _TRIM_INTERVAL_SECONDS:
            return
        _last_trim = now
        files = list(_regular_files(photo_derivatives.photos_root() / "derivatives"))
        total = sum(info.st_size for _path, info in files)
        ceiling = max(0, settings.photo_derivative_cache_max_bytes)
        if ceiling == 0 or total <= ceiling:
            _known_total = total
            return
        target = int(ceiling * 0.9)
        for path, info in sorted(files, key=lambda item: (item[1].st_mtime_ns, str(item[0]))):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= info.st_size
            if total <= target:
                break
        _known_total = max(0, total)
