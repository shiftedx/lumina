"""Process boundary for native PDF and RAR parsers; originals are always read-only."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from app.services.book_archives import BookArchiveError, MAX_IMAGE_BYTES

_WORKERS = threading.BoundedSemaphore(2)


def native_book(path: Path, format_: str, member: str | None = None) -> dict | bytes:
    with _WORKERS, tempfile.TemporaryFile() as output:
        try:
            subprocess.run(
                [sys.executable, "-m", "app.services.book_native_worker", str(path), format_, member or ""],
                cwd=Path(__file__).resolve().parents[2], stdout=output, stderr=subprocess.DEVNULL, check=True, timeout=30,
            )
            output.seek(0)
            data = output.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise BookArchiveError("Book resource exceeds the allowed size")
            return data if member is not None else json.loads(data)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise BookArchiveError("Book is invalid, encrypted, or exceeds native reader limits") from exc
