"""Keep offline restore excluded until the ASGI lifespan has drained its writers."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

from app import db
from app.config import settings
from app.services import backups

logger = logging.getLogger(__name__)

# The raw descriptor survives exception and interpreter cleanup. If a writer
# outlives its deadline, only process exit releases the lock.
_server_locks: dict[Path, int] = {}


class LifespanCleanup:
    def __init__(self) -> None:
        self.data_dir = settings.data_dir.resolve()
        if self.data_dir in _server_locks:
            raise RuntimeError("This process still holds the server data directory")
        with backups.hold_server_lock() as handle:
            _server_locks[self.data_dir] = os.dup(handle.fileno())
        self.tasks: list[asyncio.Task] = []
        self._startup_stops: list[Callable[[], Awaitable[None]]] = []
        self._stop_failures: list[BaseException] = []
        self._loop_failures: list[BaseException] = []

    def on_startup_failure(self, action: Callable[[], Any], *, blocking: bool = True) -> None:
        # Register before startup, so a starter that raises after allocating
        # a worker still receives its stop call.
        self._startup_stops.append(partial(self.stop, action, blocking=blocking))

    async def stop(self, *actions: Callable[[], Any], blocking: bool = True) -> None:
        for action in actions:
            try:
                complete = await asyncio.to_thread(action) if blocking else await action()
                if complete is False:
                    raise RuntimeError(f"{action.__qualname__} did not finish shutting down")
            except BaseException as exc:
                self._stop_failures.append(exc)
                logger.error("Lifespan cleanup failed at %s: %s", action.__qualname__, type(exc).__name__)

    async def settle(self, *tasks: asyncio.Task) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=20)
        if pending:
            self._stop_failures.append(RuntimeError("Background service loops did not stop during shutdown"))
            logger.error("Background service loops did not stop during shutdown")
        for task in tasks:
            if task in done and not task.cancelled() and (exc := task.exception()) is not None:
                self._loop_failures.append(exc)

    async def release_lock(self) -> None:
        if not self._stop_failures:
            await self.stop(db.engine.dispose)
        if not self._stop_failures:
            os.close(_server_locks[self.data_dir])
            del _server_locks[self.data_dir]

    async def rollback_startup(self) -> None:
        for task in self.tasks:
            task.cancel()
        await self.settle(*self.tasks)
        for stop in reversed(self._startup_stops):
            await stop()
        await self.release_lock()

    def raise_failures(self) -> None:
        failures = [*self._stop_failures, *self._loop_failures]
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("Background services failed during shutdown", failures)
