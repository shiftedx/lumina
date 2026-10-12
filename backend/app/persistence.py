"""Single seam for durable writes: bounded writer admission and short transactions.

Every dialect-specific write concern lives here — SQLite writer admission,
eager BEGIN IMMEDIATE, and busy/timeout classification. Callers name their
write so operators can attribute contention from the runtime-health metrics.
The private file helpers at the bottom cover durable writes outside the database.
"""

from __future__ import annotations

import os
import stat
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Lock, RLock
from typing import Iterator

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


WRITER_ADMISSION_TIMEOUT_SECONDS = 5
_WRITE_DEPTH_KEY = "persistence_write_transaction_depth"
_AFTER_COMMIT_ACTIONS_KEY = "persistence_after_commit_actions"
# db.info: members whose just-ahead chain this session already queued (playback). It guards a queued action, so a
# rollback that discards the action clears it too, or the retried completion would queue nothing.
JUST_AHEAD_QUEUED = "just_ahead_queued"

# RLock only keeps a thread that already owns the slot from deadlocking on a
# nested acquire. Same-thread writes on a second session are NOT a supported
# pattern: they would still contend on SQLite's own write lock and stall or
# fail busy regardless of this admission lock.
writer_admission_lock = RLock()

_metrics_lock = Lock()
_metrics: dict[str, dict[str, float]] = {}


def persistence_metrics_snapshot() -> dict:
    with _metrics_lock:
        return {name: dict(values) for name, values in _metrics.items()}


def reset_persistence_metrics() -> None:
    with _metrics_lock:
        _metrics.clear()


def _record(name: str, *, wait_ms: float, duration_ms: float, error: bool, busy: bool) -> None:
    with _metrics_lock:
        metric = _metrics.setdefault(
            name,
            {"count": 0, "errors": 0, "busy_timeouts": 0, "total_ms": 0.0, "max_ms": 0.0, "total_wait_ms": 0.0, "max_wait_ms": 0.0},
        )
        metric["count"] += 1
        if error:
            metric["errors"] += 1
        if busy:
            metric["busy_timeouts"] += 1
        metric["total_ms"] += duration_ms
        metric["max_ms"] = max(metric["max_ms"], duration_ms)
        metric["total_wait_ms"] += wait_ms
        metric["max_wait_ms"] = max(metric["max_wait_ms"], wait_ms)


def _is_busy_error(error: OperationalError) -> bool:
    message = str(error.orig if error.orig is not None else error).lower()
    return "locked" in message or "busy" in message


def _begin_immediate(db: Session) -> None:
    # Claim the SQLite write lock up front so a reader-to-writer upgrade can
    # never deadlock against another deferred transaction. Composed through
    # the session's connection so SQLAlchemy's transaction bookkeeping and the
    # eventual session.commit() stay authoritative.
    connection = db.connection()
    driver = connection.connection.driver_connection
    if driver is not None and getattr(driver, "in_transaction", False):
        # A transaction is already open on this connection (a caller flushed
        # before entering the seam, or a mid-seam repair rolled back and
        # reflushed). Issuing BEGIN IMMEDIATE now would fail; accept the
        # already-open transaction even though it may have begun deferred.
        return
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def queue_after_commit(db: Session, action: Callable[[], None]) -> None:
    """Defer `action` until the session's current write transaction commits durably.

    Actions run after the writer slot is released, so slow delivery never
    extends another writer's admission wait; they are discarded when the
    transaction rolls back. Writes made outside a seam call commit at the
    request teardown (`session_scope`), which drains the queue the same way —
    trivially after any writer lock, since teardown holds no admission slot.
    """
    db.info.setdefault(_AFTER_COMMIT_ACTIONS_KEY, []).append(action)


def drain_after_commit_actions(db: Session) -> None:
    """Run the queued actions after a durable commit; call only once the writer slot is free."""
    for action in db.info.pop(_AFTER_COMMIT_ACTIONS_KEY, []):
        try:
            action()
        except Exception:
            # The commit is already durable. Delivery is best-effort and must
            # not make the completed write appear to fail.
            continue


def discard_after_commit_actions(db: Session) -> None:
    db.info.pop(_AFTER_COMMIT_ACTIONS_KEY, None)
    db.info.pop(JUST_AHEAD_QUEUED, None)


@contextmanager
def write_transaction(db: Session, *, name: str) -> Iterator[None]:
    """Own the writer slot for one named, short transaction through durable commit or rollback."""
    depth = db.info.get(_WRITE_DEPTH_KEY, 0)
    if depth:
        db.info[_WRITE_DEPTH_KEY] = depth + 1
        try:
            yield
        finally:
            db.info[_WRITE_DEPTH_KEY] -= 1
        return

    wait_started = time.perf_counter()
    acquired = writer_admission_lock.acquire(timeout=WRITER_ADMISSION_TIMEOUT_SECONDS)
    wait_ms = (time.perf_counter() - wait_started) * 1000.0
    if not acquired:
        discard_after_commit_actions(db)
        db.rollback()
        _record(name, wait_ms=wait_ms, duration_ms=0.0, error=True, busy=True)
        raise OperationalError(
            "acquire SQLite writer slot",
            {},
            TimeoutError("SQLite writer slot is busy"),
        )

    db.info[_WRITE_DEPTH_KEY] = 1
    started = time.perf_counter()
    error = False
    busy = False
    try:
        _begin_immediate(db)
        yield
        db.commit()
    except OperationalError as exc:
        error = True
        busy = _is_busy_error(exc)
        discard_after_commit_actions(db)
        if busy:
            db.rollback()
        else:
            # A non-busy OperationalError (e.g. "disk I/O error") can leave the
            # pooled SQLite descriptor permanently poisoned: the dialect's
            # is_disconnect never fires for it and there is no pool pre-ping,
            # so a plain rollback would return the broken descriptor to the
            # pool and every later admission would fail the same way.
            # Invalidate instead — the pool discards the descriptor without
            # issuing ROLLBACK on it, and the next checkout opens a fresh
            # connection. The session itself stays reusable.
            db.invalidate()
        raise
    except BaseException:
        error = True
        discard_after_commit_actions(db)
        db.rollback()
        raise
    finally:
        db.info[_WRITE_DEPTH_KEY] = 0
        writer_admission_lock.release()
        _record(name, wait_ms=wait_ms, duration_ms=(time.perf_counter() - started) * 1000.0, error=error, busy=busy)
    # Reached only after a durable commit, with the writer slot released:
    # deferred actions must never extend another writer's admission wait.
    drain_after_commit_actions(db)


def atomic_write(path: Path, data: bytes | str) -> None:
    """Replace ``path`` with ``data`` all-or-nothing: private (0600) temp file, fsync, rename, fsync dir."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data.encode("utf-8") if isinstance(data, str) else data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_regular_file(path: Path, max_bytes: int) -> bytes:
    """A regular file's bytes, never through a symlink; OSError if it is anything else or exceeds ``max_bytes``."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > max_bytes:
            raise OSError(f"{path.name} is not a regular file within {max_bytes} bytes.")
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise OSError(f"{path.name} exceeds {max_bytes} bytes.")
    return content
