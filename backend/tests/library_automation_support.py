"""Fakes and builders for the library automation tests: clock, probe, lanes, roots and runs."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db as db_module
from app.models import ImportRun, StorageRoot
from app.services.library_automation import TICK_S, Clock, LibraryAutomation, RootView, start_run_default

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = NOW, mono: float = 1000.0):
        self._now, self._mono = now, mono

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    def clock(self) -> Clock:
        return Clock(lambda: self._now, lambda: self._mono)

    @property
    def naive_utc(self) -> datetime:
        return self._now.astimezone(UTC).replace(tzinfo=None)


class FakeProbe:
    """probe_fn seam: per root "online", a storage state ("offline", "identity_mismatch"), or a threading.Event to hang on."""

    def __init__(self):
        self.modes: dict[str, object] = {}
        self.calls: Counter[str] = Counter()

    def __call__(self, root: RootView) -> str:
        self.calls[root.id] += 1
        mode = self.modes.get(root.id, "online")
        if isinstance(mode, threading.Event):
            mode.wait()
            return "available"
        return "available" if mode == "online" else str(mode)


class SyncLane:
    """A lane that runs each job inline, so dispatch is deterministic."""

    def __init__(self, name: str = ""):
        self.name, self.current, self.queued, self.labels = name, None, 0, []

    busy = False

    def submit(self, label: str, job) -> None:
        self.labels.append(label)
        job()

    def start(self) -> None: ...

    def close(self) -> None: ...


def probe_threads(root_id: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == f"library-probe-{root_id}" and t.is_alive()]


class Env:
    """One automation over a temp tree, a fake clock and fake probe/start/drive seams."""

    def __init__(self, tmp_path: Path, **overrides):
        self.tmp_path = tmp_path
        self.fake_clock = FakeClock()
        self.probe = FakeProbe()
        self.starts: list[tuple[str, str, object]] = []
        self.driven: list[str] = []
        kwargs = dict(clock=self.fake_clock.clock(), start_run=self._start, drive_fn=self.driven.append, probe_fn=self.probe, lane_factory=SyncLane)
        kwargs.update(overrides)
        self.automation = LibraryAutomation(**kwargs)

    def _start(self, root_id, user_id, visibility, *, trigger, scope):
        run_id = start_run_default(root_id, user_id, visibility, trigger=trigger, scope=scope)
        self.starts.append((root_id, trigger, scope))
        return run_id

    def root(self, label: str, *, mode: str = "external", schedule: str = "off", watch: bool = False, interval: int = 300, enabled: bool = True) -> str:
        path = self.tmp_path / "mnt" / label
        path.mkdir(parents=True)
        root_id = str(uuid.uuid4())
        with db_module.SessionLocal() as db:
            db.add(StorageRoot(
                id=root_id, label=label, path=str(path), mode=mode, enabled=enabled, identity=str(os.stat(path).st_dev),
                scan_schedule=schedule, watch_enabled=watch, watch_interval_s=interval,
            ))
            db.commit()
        return root_id

    def run(self, root_id: str, *, state: str = "succeeded", trigger: str = "manual", scope=None, created: datetime | None = None,
            finished: datetime | None = None, user: str = "admin") -> str:
        created = created or self.fake_clock.naive_utc - timedelta(hours=1)
        if finished is None and state not in ("running", "cancel_requested", "needs_confirmation"):
            finished = created + timedelta(minutes=5)
        run_id = str(uuid.uuid4())
        with db_module.SessionLocal() as db:
            db.add(ImportRun(id=run_id, root_id=root_id, user_id=user, visibility="shared", state=state, cursor="[]", counters={},
                             trigger=trigger, scope=scope, created_at=created, finished_at=finished))
            db.commit()
        return run_id

    def finish(self, run_id: str, state: str = "succeeded") -> None:
        with db_module.SessionLocal() as db:
            run = db.get(ImportRun, run_id)
            run.state, run.finished_at = state, self.fake_clock.naive_utc
            db.commit()

    def active_runs(self) -> list[ImportRun]:
        with db_module.SessionLocal() as db:
            return db.query(ImportRun).filter(ImportRun.state.in_(("running", "cancel_requested"))).all()

    def tick(self, n: int = 1, advance: float = TICK_S) -> None:
        """Advance the clock and tick; then let finished-able probes return so the next tick harvests them."""
        for _ in range(n):
            self.fake_clock.advance(advance)
            self.automation.tick()
            hung = {rid for rid, mode in self.probe.modes.items() if isinstance(mode, threading.Event) and not mode.is_set()}
            for thread in threading.enumerate():
                if thread.name.startswith("library-probe-") and thread.name.removeprefix("library-probe-") not in hung:
                    thread.join(2)

    def state(self, root_id: str) -> str | None:
        rs = self.automation._roots.get(root_id)
        return rs.state if rs else None


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
