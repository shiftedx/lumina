"""Process boundary for native PDF and RAR parsers; originals are always read-only."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from app.services.book_archives import BookArchiveError, MAX_IMAGE_BYTES

_WORKERS = threading.BoundedSemaphore(2)
_VALIDATED_FILES = 128
_VALIDATED: OrderedDict[tuple[str, int, int, int, int, int], None] = OrderedDict()
_VALIDATED_LOCK = threading.Lock()


def _identity(path: Path, format_: str) -> tuple[str, int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return format_, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _is_validated(identity: tuple[str, int, int, int, int, int] | None) -> bool:
    if identity is None:
        return False
    with _VALIDATED_LOCK:
        if identity not in _VALIDATED:
            return False
        _VALIDATED.move_to_end(identity)
        return True


def _mark_validated(identity: tuple[str, int, int, int, int, int] | None) -> None:
    if identity is None:
        return
    with _VALIDATED_LOCK:
        _VALIDATED[identity] = None
        _VALIDATED.move_to_end(identity)
        while len(_VALIDATED) > _VALIDATED_FILES:
            _VALIDATED.popitem(last=False)


def native_book(path: Path, format_: str, member: str | None = None) -> dict | bytes:
    identity = _identity(path, format_)
    validated = member is not None and _is_validated(identity)
    with _WORKERS, tempfile.TemporaryFile() as output:
        try:
            subprocess.run(
                [sys.executable, "-m", "app.services.book_native_worker", str(path), format_, member or "", "1" if validated else "0"],
                cwd=Path(__file__).resolve().parents[2], stdout=output, stderr=subprocess.DEVNULL, check=True, timeout=30,
            )
            output.seek(0)
            data = output.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise BookArchiveError("Book resource exceeds the allowed size")
            result = data if member is not None else json.loads(data)
            if _identity(path, format_) == identity:
                _mark_validated(identity)
            return result
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise BookArchiveError("Book is invalid, encrypted, or exceeds native reader limits") from exc
