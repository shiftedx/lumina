"""Optional media work yields to authenticated app use; automatic backfills are opt-in."""
from __future__ import annotations

import contextlib
import signal
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from app.config import settings

QUIET_SECONDS = 30
REQUEST_MARKER = "background_work_active"


class BackgroundWorkClosed(RuntimeError):
    """Shutdown has closed background media admission."""


class BackgroundWork:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = 0
        self._last_activity = float("-inf")
        self._processes: dict[subprocess.Popen, tuple[bool, bool]] = {}
        self._interactive: set[subprocess.Popen] = set()
        self.closed = False

    def _busy(self) -> bool:
        return (self.closed or self._active > 0 or time.monotonic() - self._last_activity < QUIET_SECONDS
                or any(process.poll() is None for process in self._interactive))

    def busy(self) -> bool:
        with self._lock:
            return self._busy()

    @staticmethod
    def _in_window() -> bool:
        start, end = settings.background_backfill_start_hour, settings.background_backfill_end_hour
        if start is None or end is None:
            return False
        hour = datetime.now().astimezone().hour
        return start <= hour < end if start < end else hour >= start or hour < end

    def backfill_allowed(self) -> bool:
        with self._lock:
            return not self._busy() and self._in_window()

    def _sync(self, process: subprocess.Popen, backfill: bool, paused: bool) -> bool:
        self._processes[process] = (backfill, paused)
        if process in self._interactive:
            blocked = self.closed
            if blocked != paused and process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGSTOP if blocked else signal.SIGCONT)
            self._processes[process] = (backfill, blocked)
            return blocked
        blocked = self._busy() or backfill and not self._in_window()
        if not blocked:
            first = next((child for child, (automatic, _) in self._processes.items()
                          if child not in self._interactive and child.poll() is None
                          and (not automatic or self._in_window())), None)
            blocked = first is not None and first is not process
            if not blocked:
                for other, (automatic, stopped) in list(self._processes.items()):
                    if other is not process and not stopped and other.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            other.send_signal(signal.SIGSTOP)
                        self._processes[other] = (automatic, True)
        if blocked != paused and process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGSTOP if blocked else signal.SIGCONT)
        self._processes[process] = (backfill, blocked)
        return blocked

    def touch(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()
            for process, (backfill, paused) in list(self._processes.items()):
                self._sync(process, backfill, paused)

    def begin_request(self, scope: dict[str, Any]) -> None:
        if scope.get("type") != "http":
            return
        with self._lock:
            state = scope.setdefault("state", {})
            if state.get(REQUEST_MARKER):
                return
            state[REQUEST_MARKER] = True
            self._active += 1
            self.touch()

    def end_request(self, scope: dict[str, Any]) -> None:
        with self._lock:
            if scope.get("state", {}).pop(REQUEST_MARKER, False):
                self._active -= 1
                self.touch()

    def register(self, process: subprocess.Popen, *, backfill: bool = False) -> None:
        with self._lock:
            if self.closed:
                self._reap(process)
                raise BackgroundWorkClosed("Background media admission is closed")
            self._sync(process, backfill, False)

    def sync(self, process: subprocess.Popen, *, backfill: bool = False) -> bool:
        with self._lock:
            _, paused = self._processes.get(process, (backfill, False))
            return self._sync(process, backfill, paused)

    def unregister(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.pop(process, None)
            self._interactive.discard(process)

    def promote(self, process: subprocess.Popen) -> None:
        """Requested work bypasses background pauses while remaining owned by shutdown."""
        with self._lock:
            self._interactive.add(process)
            for other, (automatic, paused) in list(self._processes.items()):
                if other is not process:
                    self._sync(other, automatic, paused)
            automatic, paused = self._processes.get(process, (False, False))
            self._sync(process, automatic, paused)

    def start(self) -> None:
        with self._lock:
            self.closed = False

    @staticmethod
    def _reap(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGCONT)
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Background media child did not stop") from None

    def close(self) -> None:
        """Reap every registered child before lifespan teardown can exit the native server."""
        with self._lock:
            self.closed = True
            failure = None
            for process in list(self._processes):
                try:
                    self._reap(process)
                except RuntimeError as exc:
                    failure = exc
                else:
                    self._processes.pop(process, None)
                    self._interactive.discard(process)
            if failure is not None:
                raise failure


background_work = BackgroundWork()
