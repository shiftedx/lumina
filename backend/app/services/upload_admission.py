"""Fail-fast transient admission for managed book and photo uploads."""
from __future__ import annotations

import shutil
import threading
from collections import Counter
from pathlib import Path
from types import TracebackType


MAX_ACTIVE_UPLOADS = 2
MAX_ACTIVE_UPLOADS_PER_MEMBER = 1
MIN_FREE_SPACE_BYTES = 512 * 1024 * 1024


class UploadBusy(RuntimeError):
    pass


class UploadStorageFull(RuntimeError):
    pass


class UploadLease:
    def __init__(self, admission: UploadAdmission, member_id: str, reserved_bytes: int) -> None:
        self._admission = admission
        self.member_id = member_id
        self.reserved_bytes = reserved_bytes
        self._released = False

    def __enter__(self) -> UploadLease:
        return self

    def __exit__(
        self, _exc_type: type[BaseException] | None, _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()

    def release(self) -> None:
        self._admission.release(self)


class UploadAdmission:
    """Reserve one staging lane and its expected bytes without waiting."""

    def __init__(
        self, *, max_active: int = MAX_ACTIVE_UPLOADS,
        max_per_member: int = MAX_ACTIVE_UPLOADS_PER_MEMBER,
        min_free_bytes: int = MIN_FREE_SPACE_BYTES,
    ) -> None:
        self.max_active = max_active
        self.max_per_member = max_per_member
        self.min_free_bytes = min_free_bytes
        self._lock = threading.Lock()
        self._active = 0
        self._by_member: Counter[str] = Counter()
        self._reserved_bytes = 0

    @staticmethod
    def _expected_bytes(content_length: str | None, maximum_bytes: int) -> int:
        if content_length is None:
            return maximum_bytes
        try:
            declared = int(content_length)
        except ValueError:
            return maximum_bytes
        return declared if 0 <= declared <= maximum_bytes else maximum_bytes

    def acquire(
        self, member_id: str, root: Path, *, content_length: str | None, maximum_bytes: int,
    ) -> UploadLease:
        expected = self._expected_bytes(content_length, maximum_bytes)
        with self._lock:
            if self._active >= self.max_active or self._by_member[member_id] >= self.max_per_member:
                raise UploadBusy("upload_busy")
            try:
                free = shutil.disk_usage(root).free
            except OSError as exc:
                raise UploadStorageFull("storage_full") from exc
            if free < self.min_free_bytes + self._reserved_bytes + expected:
                raise UploadStorageFull("storage_full")
            lease = UploadLease(self, member_id, expected)
            self._active += 1
            self._by_member[member_id] += 1
            self._reserved_bytes += expected
            return lease

    def release(self, lease: UploadLease) -> None:
        with self._lock:
            if lease._released:
                return
            lease._released = True
            self._active -= 1
            self._by_member[lease.member_id] -= 1
            if self._by_member[lease.member_id] == 0:
                del self._by_member[lease.member_id]
            self._reserved_bytes -= lease.reserved_bytes


upload_admission = UploadAdmission()
