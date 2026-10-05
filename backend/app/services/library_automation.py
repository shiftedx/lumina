"""Library automation: scheduled scans, the polling folder watcher and the single driver.

This facade is what main.py's lifespan and
routers/admin_library_automation.py call; signatures and return shapes are frozen.
Nothing in this module ever calls LibraryImportService.confirm.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import SessionLocal, session_scope
from app.models import AppSettings, ImportRun, LibraryWatchDir, StorageRoot
from app.persistence import write_transaction
from app.services.library_import import ACTIVE_STATES, ImportRunError, LibraryImportService, drive
from app.services.library_retention import prune_history
from app.services.library_watch import FAILED_RETRY_S, CommitRows, Fs, OsFs, RootWatch, ScopePlan  # noqa: F401  (FAILED_RETRY_S: re-exported)
from app.services.storage_roots import ONLINE_STATES, StorageRootService

logger = logging.getLogger(__name__)

TICK_S = 15
FS_BATCH_STALL_S = 30  # an in-flight filesystem batch older than this makes the root "unresponsive"
PROBE_TIMEOUT_S = 5
HEARTBEAT_STALE_S = 120
ORPHAN_AFTER_S = 120  # an active run untouched this long and not driven by us is adopted
ADOPT_BACKOFF_S = 60
BASELINE_ROWS_PER_TX = 500  # watch rows per write transaction (baseline and commit)
FIRST_TICK_DELAY_S = 20  # the lifespan's poller waits this long before its first tick (tests never reach it)
TOUCH_EVERY_S = 3600  # a watched root's rows have updated_at refreshed at most this often (7-day restore rule)

_INTERVALS = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "6h": timedelta(hours=6)}


@dataclass(frozen=True)
class RunView:
    id: str
    trigger: str | None
    state: str
    scope_dirs: int | None
    created_at: datetime
    finished_at: datetime | None
    updated_at: datetime
    user_id: str
    visibility: str
    error: str | None
    counters: dict


@dataclass(frozen=True)
class RootView:
    id: str
    label: str
    path: str
    identity: str | None
    mode: str
    enabled: bool
    schedule: str
    watch: bool
    watch_interval_s: int
    newest_run: RunView | None  # any trigger, newest by (created_at, id)
    last_full_run: RunView | None  # newest with scope IS NULL, any trigger
    active_run: RunView | None


@dataclass
class Clock:
    now: Callable[[], datetime]  # aware, server-local
    monotonic: Callable[[], float]


def default_clock() -> Clock:
    return Clock(lambda: datetime.now().astimezone(), time.monotonic)


def local(dt: datetime) -> datetime:
    """The DB stores naive UTC; return it as aware server-local time."""
    return dt.replace(tzinfo=UTC).astimezone()


def next_scan_at(schedule: str, night_hour: int, last_full: RunView | None) -> datetime | None:
    if schedule == "off" or last_full is None:
        return None
    if last_full.state in ACTIVE_STATES or last_full.state == "needs_confirmation":
        return None  # not due while busy or held
    if schedule == "nightly":
        # Wall-clock arithmetic on a naive local time, then re-localised, so DST shifts do not skew the slot.
        anchor = local(last_full.created_at).replace(tzinfo=None)
        slot = anchor.replace(hour=night_hour, minute=0, second=0, microsecond=0)
        if slot <= anchor:
            slot += timedelta(days=1)
        return slot.astimezone()
    ended = local(last_full.finished_at or last_full.created_at)
    return ended + _INTERVALS[schedule]


def is_due(root: RootView, night_hour: int, now_local: datetime) -> bool:
    due = next_scan_at(root.schedule, night_hour, root.last_full_run)
    return due is not None and now_local >= due


def due_order(roots: list[RootView], night_hour: int, now_local: datetime) -> list[RootView]:
    """Due roots, oldest-due first (then label, then id)."""
    due = [(next_scan_at(r.schedule, night_hour, r.last_full_run), r) for r in roots]
    return [r for d, r in sorted(((d, r) for d, r in due if d is not None and now_local >= d), key=lambda x: (x[0], x[1].label, x[1].id))]


def skip_reason(root: RootView, *, probe_alive: bool, online: bool | None) -> str | None:
    if root.newest_run is None:
        return "needs_first_import"
    if root.newest_run.state == "needs_confirmation":
        return "waiting_confirmation"
    if root.active_run is not None:
        return "scanning"
    if probe_alive:
        return "unresponsive"
    if online is False:
        return "offline"
    return None  # online None (not probed yet) is unknown: callers probe first


def start_run_default(root_id: str, user_id: str, visibility: str, *, trigger: str, scope: list | None) -> str:
    """The start_run seam: create and commit the run, return its id (raises ImportRunError when the root is busy)."""
    with session_scope() as db:
        return LibraryImportService(db).start(root_id, user_id, visibility, trigger=trigger, scope=scope).id


def default_probe(root: RootView) -> str:
    """The probe_fn seam: the read-only storage state of a transient copy of the root (runs on a probe thread)."""
    transient = StorageRoot(id=root.id, label=root.label, path=root.path, mode=root.mode, enabled=root.enabled,
                            identity=root.identity, minimum_free_bytes=0)
    # _observe is private-by-convention; it gives the state name (offline | identity_mismatch | ...) is_online hides.
    return StorageRootService(None)._observe(transient, verify=False)["state"]  # type: ignore[arg-type]


class _Lane:
    """One daemon worker over a FIFO queue; close() never waits on a stuck job.

    A job stuck on a dead mount holds its lane until the mount answers; the other lanes and the poller
    are unaffected, and the daemon thread is abandoned at exit.
    """

    def __init__(self, name: str):
        self.name = name
        self.current: str | None = None
        self._q: queue.Queue[tuple[str, Callable[[], None]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name=self.name, daemon=True)
            self._thread.start()

    def submit(self, label: str, job: Callable[[], None]) -> None:
        self._q.put((label, job))

    @property
    def busy(self) -> bool:
        return self.current is not None or not self._q.empty()

    @property
    def queued(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        self._q.put(None)

    def _work(self) -> None:
        while (item := self._q.get()) is not None:
            self.current = item[0]
            try:
                item[1]()
            except Exception as exc:  # a failed job never kills the lane
                logger.warning("Library automation %s job %s failed: %s", self.name, item[0], type(exc).__name__)  # no repr: it can carry paths
            finally:
                self.current = None


@dataclass
class _Probe:
    started: float
    thread: threading.Thread | None = None
    state: str | None = None  # the observed storage state, set when the thread returns


@dataclass
class _Batch:
    """The root's one in-flight filesystem batch (RootWatch.run_batch on a daemon thread); never joined by the poller."""
    started: float
    thread: threading.Thread

    @classmethod
    def start(cls, root_id: str, watch: RootWatch, started: float) -> _Batch:
        def run() -> None:
            try:
                watch.run_batch()
            except Exception as exc:  # a bug or an unexpected error: the next batch retries
                logger.warning("Library watch batch failed: %.300s", type(exc).__name__)

        thread = threading.Thread(target=run, name=f"library-watch-{root_id}", daemon=True)
        thread.start()
        return cls(started, thread)


@dataclass
class RootState:
    online: bool | None = None  # None until the first probe returns
    state: str | None = None  # last classified state (idle or a skip reason); logged once per change
    last_skip: dict | None = None  # {at, reason} from the last dispatch that skipped this due root
    last_dispatch: datetime | None = None
    batch: _Batch | None = None  # at most one filesystem batch per root, ever
    pending: tuple[str, ScopePlan] | None = None  # the dispatched watch run and the plan it covers
    error: str | None = None  # the last tick's failure for this root (cleared by its next clean step)
    touched: float | None = None  # monotonic time the root's rows last had updated_at refreshed
    baselining: bool = False  # the watch started from no rows: write them all once the baseline completes


def _run_view(run: ImportRun | None) -> RunView | None:
    if run is None:
        return None
    return RunView(run.id, run.trigger, run.state, None if run.scope is None else len(run.scope), run.created_at,
                   run.finished_at, run.updated_at, run.user_id, run.visibility, run.error, dict(run.counters or {}))


class LibraryAutomation:
    """Scheduled scans and dispatch for external roots.

    | Thread | Does | Never does |
    | `library-automation` poller (daemon), every TICK_S | one read-only DB session per tick (<= 3 indexed queries per
      external root), heartbeat, harvest finished probes, scheduled dispatch | touch a root's path (no stat, scandir,
      is_online) |
    | `library-auto-<root>` (one per root), `library-manual` lanes (daemon) | drive(run_id); a root's automatic runs on
      its own lane, "Scan now" on manual | run two jobs of one lane at once |
    | probe (daemon, <= 1 per root) | the storage state of the root; the poller reads it next tick | pile up (an alive
      probe older than PROBE_TIMEOUT_S makes the root unresponsive, and no second probe starts) |
    """

    def __init__(self, *, clock: Clock | None = None, start_run: Callable[..., str] | None = None,
                 drive_fn: Callable[[str], None] | None = None, probe_fn: Callable[[RootView], str] | None = None,
                 lane_factory: Callable[[str], Any] | None = None, fs_factory: Callable[[RootView], Fs] | None = None):
        self._clock = clock or default_clock()
        self._start_run = start_run or start_run_default
        self._drive = drive_fn or drive
        self._probe_fn = probe_fn or default_probe
        self._fs = fs_factory or (lambda root: OsFs(Path(root.path)))
        self._lane = lane_factory or _Lane
        self._auto: dict[str, Any] = {}  # root id -> its automatic lane, so a scan hung on one share holds only that root
        self._manual = self._lane("library-manual")
        self._roots: dict[str, RootState] = {}
        self._views: dict[str, RootView] = {}  # the last load, in label order: the read model's source (no DB per request)
        self._night = 3
        self._watches: dict[str, RootWatch] = {}
        self._driving: set[str] = set()
        self._probes: dict[str, _Probe] = {}
        self._heartbeat_at: datetime | None = None
        self._last_error: str | None = None
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None
        self._last_prune: float | None = None
        self._adopt_blocked_until: dict[str, float] = {}
        self._born = self._clock.now()  # runs created before this are a previous process's orphans

    def start(self, first_tick_delay_s: float = 0) -> None:
        """Start the poller and driver threads (lifespan, after probe_warming.start())."""
        if self._poller is not None and self._poller.is_alive():
            return
        self._stop = threading.Event()
        self._born = self._clock.now()
        self._manual.start()
        self._poller = threading.Thread(target=self._loop, args=(self._stop, first_tick_delay_s), name="library-automation", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        """Stop the threads (lifespan, before import_stop_event.set()); never joins a stuck probe or drive."""
        self._stop.set()
        for lane in self._auto.values():
            lane.close()
        self._auto.clear()
        self._manual.close()
        if self._poller is not None:
            self._poller.join(2)

    def _loop(self, stop: threading.Event, first_delay_s: float = 0) -> None:
        if first_delay_s and stop.wait(first_delay_s):
            return
        while True:
            try:
                self.tick()
                self._last_error = None
            except Exception as exc:
                self._last_error = repr(exc)[:300]
                logger.warning("Library automation tick failed: %.300s", repr(exc))
            if stop.wait(TICK_S):
                return

    def tick(self) -> None:
        now = self._clock.now()
        self._heartbeat_at = now
        roots, night, active = self._load()
        self._views, self._night = {r.id: r for r in roots}, night
        for root in roots:
            # A scanning root is probed too, so a scan stuck on a dead share can show unresponsive.
            if root.mode == "external" and root.enabled and (root.schedule != "off" or root.watch or root.active_run):
                self._guard(root, self._classify, root)
        # A run stuck on an unresponsive share (and the lane blocked under it) no longer counts as busy (#160): its
        # thread cannot be cancelled, so the other roots carry on beside it and it resumes when the share answers.
        stuck = {r.active_run.id for r in roots if r.active_run is not None and self._state(r) == "unresponsive"}
        lanes_busy = any(lane.busy and lane.current not in stuck for lane in (self._manual, *self._auto.values()))
        busy = self._adopt(roots) if not lanes_busy else False  # adoption only when every lane is idle
        busy = busy or bool(active - stuck) or lanes_busy
        for root in roots:
            if root.mode == "external" and root.enabled and root.watch:
                busy = self._guard(root, self._step_watch, root, busy, default=False) or busy
        self._dispatch_scheduled(roots, night, now, busy)
        self._maintain_hourly()

    # --- adoption ------------------------------------------------------------------

    def _adopt(self, roots: list[RootView]) -> bool:
        """Drive every active run nobody drives: a previous process's run, or one untouched for ORPHAN_AFTER_S.

        Maintenance's advance_active bumps an orphan's updated_at every minute, so a run created before this process
        started is adopted without waiting. A run some other driver also steps is safe: the root's step lock serializes steps.
        """
        mono, now, adopted = self._clock.monotonic(), self._clock.now(), False
        for root in roots:
            run = root.active_run
            if run is None or run.id in self._driving or self._adopt_blocked_until.get(run.id, -math.inf) > mono:
                continue
            if local(run.created_at) >= self._born and now - local(run.updated_at) < timedelta(seconds=ORPHAN_AFTER_S):
                continue
            logger.info("Library automation: driving the unfinished import of %s", root.label)
            lane = self._auto_lane(root.id) if run.trigger in ("scheduled", "watch") else self._manual
            self._driving.add(run.id)
            lane.submit(run.id, lambda run_id=run.id: self._drive_tracked(run_id))
            adopted = True
        return adopted

    # --- folder watching ------------------------------------------------------------

    def _guard(self, root: RootView, fn: Callable[..., Any], *args: Any, default: Any = None) -> Any:
        """One root's DB or FS error is logged and kept for Diagnostics; it never aborts the tick for the other roots."""
        rs = self._roots.setdefault(root.id, RootState())
        try:
            result = fn(*args)
            rs.error = None
            return result
        except Exception as exc:
            rs.error = repr(exc)[:300]
            logger.warning("Library automation: %s failed for %s: %.300s", fn.__name__, root.label, repr(exc))
            return default

    def _step_watch(self, root: RootView, busy: bool) -> bool:
        """Settle the pending run, harvest the finished batch, dispatch a ready plan or start the next batch.

        Only for an online, idle root: a held (needs_confirmation), scanning, offline or unresponsive root polls
        nothing and keeps no new dirty state. Returns True when it started a run.
        """
        rs = self._roots.get(root.id)
        if rs is None or rs.online is not True or rs.state != "idle":
            return False
        watch = self._watches.get(root.id)
        if watch is None:
            watch = self._watches.setdefault(root.id, self._new_watch(root))
        if rs.pending is not None:
            self._settle(root, rs, watch)
        if rs.batch is not None:
            if rs.batch.thread.is_alive():
                return False  # still listing, or stalled (classified unresponsive): never a second batch
            rs.batch = None
            if rs.baselining and watch.phase == "watching":
                self._write_baseline(root.id, watch)
                rs.baselining, rs.touched = False, self._clock.monotonic()
            elif watch.pass_complete():
                self._touch(root.id, rs)
        mono = self._clock.monotonic()
        if not busy and (plan := watch.plan(now_s=mono)) is not None:
            return self._dispatch_watch(root, rs, watch, plan)
        rs.batch = _Batch.start(root.id, watch, mono)
        return False

    def _settle(self, root: RootView, rs: RootState, watch: RootWatch) -> None:
        """The pending run is no longer active: commit its rows, or keep its dirs dirty for FAILED_RETRY_S.

        A held run never reaches here (the root is waiting_confirmation and not stepped); once confirmed it is
        succeeded and commits. One a newer run superseded stays needs_confirmation and counts as failed.
        """
        run_id, plan = rs.pending
        with SessionLocal() as db:
            run = db.get(ImportRun, run_id)
            state = run.state if run is not None else "failed"
        rs.pending = None
        if state == "succeeded":  # a partial run is not covered: its dirs stay dirty and retry after FAILED_RETRY_S
            self._write_commit(root.id, watch.committed(plan, state))
        else:
            watch.failed(plan, self._clock.monotonic())

    def _dispatch_watch(self, root: RootView, rs: RootState, watch: RootWatch, plan: ScopePlan) -> bool:
        try:
            run_id = self._start_run(root.id, root.newest_run.user_id, root.newest_run.visibility, trigger="watch", scope=plan.scope)
        except (ImportRunError, LookupError) as exc:  # a race with another start: retry later
            logger.info("Library automation: watch scan of %s not started: %s", root.label, exc)
            watch.failed(plan, self._clock.monotonic())
            return False
        rs.pending, rs.last_dispatch = (run_id, plan), self._clock.now()
        self._auto_lane(root.id).submit(run_id, lambda: self._drive_tracked(run_id))
        return True

    def _write_baseline(self, root_id: str, watch: RootWatch) -> None:
        self._write_rows(root_id, dict(watch.dirs), (), replace=True, name="library_watch_baseline")

    def _write_rows(self, root_id: str, upserts: dict[str, int], deletes: Iterable[str], *, replace: bool, name: str) -> None:
        """Watch rows in transactions of at most BASELINE_ROWS_PER_TX rows, so playback writes never wait seconds for the
        writer slot (~19.5k TV dirs). Crash-safe through the root row "", the completion marker: it is removed first and
        written last, and _new_watch trusts no set without it (it rebaselines instead of watching a partial set)."""
        with SessionLocal() as db:
            with write_transaction(db, name=name):
                marker = db.get(LibraryWatchDir, (root_id, ""))
                kept = None if marker is None else marker.mtime_ns
                if marker is not None:
                    db.delete(marker)
            if replace:  # the baseline: every row it does not hold goes
                deletes = {p for (p,) in db.query(LibraryWatchDir.path).filter(LibraryWatchDir.root_id == root_id)} - upserts.keys()
            for chunk in batched(sorted(deletes), BASELINE_ROWS_PER_TX):
                with write_transaction(db, name=name):
                    db.query(LibraryWatchDir).filter(LibraryWatchDir.root_id == root_id, LibraryWatchDir.path.in_(chunk)).delete(
                        synchronize_session=False)
            now = datetime.now(UTC).replace(tzinfo=None)
            for chunk in batched(sorted(p for p in upserts if p), BASELINE_ROWS_PER_TX):
                insert = sqlite_insert(LibraryWatchDir).values(
                    [{"root_id": root_id, "path": p, "mtime_ns": upserts[p], "updated_at": now} for p in chunk])
                with write_transaction(db, name=name):
                    db.execute(insert.on_conflict_do_update(
                        index_elements=["root_id", "path"], set_={"mtime_ns": insert.excluded.mtime_ns, "updated_at": now}))
            if (mtime := upserts.get("", kept)) is not None:
                with write_transaction(db, name=name):
                    db.add(LibraryWatchDir(root_id=root_id, path="", mtime_ns=mtime, updated_at=now))

    def _touch(self, root_id: str, rs: RootState) -> None:
        """A completed pass proves the rows are current: refresh updated_at (hourly) so a restart after weeks reuses them."""
        mono = self._clock.monotonic()
        if rs.touched is not None and mono - rs.touched < TOUCH_EVERY_S:
            return
        with SessionLocal() as db, write_transaction(db, name="library_watch_touch"):
            db.query(LibraryWatchDir).filter(LibraryWatchDir.root_id == root_id).update(
                {LibraryWatchDir.updated_at: datetime.now(UTC).replace(tzinfo=None)}, synchronize_session=False)
        rs.touched = mono

    def _write_commit(self, root_id: str, rows: CommitRows) -> None:
        if rows.upserts or rows.deletes:
            self._write_rows(root_id, rows.upserts, rows.deletes, replace=False, name="library_watch_commit")

    def _maintain_hourly(self) -> None:
        """History retention at most once an hour; a failure is logged and retried next hour."""
        mono = self._clock.monotonic()
        if self._last_prune is not None and mono - self._last_prune < 3600:
            return
        self._last_prune = mono
        try:
            with SessionLocal() as db:
                prune_history(db)
        except Exception as exc:
            logger.warning("Library history retention failed: %.300s", repr(exc))

    def _auto_lane(self, root_id: str) -> Any:
        lane = self._auto.get(root_id)
        if lane is None:
            lane = self._auto[root_id] = self._lane(f"library-auto-{root_id}")
            lane.start()
        return lane

    def _load(self) -> tuple[list[RootView], int, set[str]]:
        """One read-only session: every root with its newest, last full and active run; the night hour; every active run id."""
        with SessionLocal() as db:
            record = db.get(AppSettings, 1)
            night = record.library_scan_night_hour if record is not None else 3
            active_ids = {run_id for (run_id,) in db.query(ImportRun.id).filter(ImportRun.state.in_(ACTIVE_STATES))}
            views = []
            for root in db.query(StorageRoot).order_by(StorageRoot.label, StorageRoot.id).all():
                newest = full = active = None
                if root.mode == "external":
                    runs = db.query(ImportRun).filter(ImportRun.root_id == root.id)
                    order = (ImportRun.created_at.desc(), ImportRun.id.desc())
                    newest = runs.order_by(*order).first()
                    full = newest if newest is None or newest.scope is None else runs.filter(ImportRun.scope.is_(None)).order_by(*order).first()
                    active = runs.filter(ImportRun.state.in_(ACTIVE_STATES)).first()
                views.append(RootView(root.id, root.label, root.path, root.identity, root.mode, root.enabled, root.scan_schedule,
                                      root.watch_enabled, root.watch_interval_s, _run_view(newest), _run_view(full), _run_view(active)))
        return views, night, active_ids

    def _classify(self, root: RootView) -> None:
        """Harvest the root's finished probe, classify it, and start the next probe if none is in flight."""
        rs = self._roots.setdefault(root.id, RootState())
        probe = self._probes.get(root.id)
        alive = probe is not None and probe.thread is not None and probe.thread.is_alive()
        if probe is not None and not alive:
            rs.online = probe.state in ONLINE_STATES
            self._probes.pop(root.id, None)  # a request thread's _probed_online may have harvested it already
        hung = alive and self._clock.monotonic() - probe.started >= PROBE_TIMEOUT_S
        state = skip_reason(root, probe_alive=hung or self._batch_stalled(root.id), online=rs.online) or "idle"
        if state != rs.state:
            logger.info("Library automation: %s is now %s", root.label, state)
            rs.state = state
        if not alive and state not in ("needs_first_import", "waiting_confirmation"):
            self._begin_probe(root)

    def _begin_probe(self, root: RootView) -> None:
        probe = _Probe(started=self._clock.monotonic())

        def run() -> None:
            try:
                probe.state = self._probe_fn(root)
            except Exception:
                probe.state = "offline"

        probe.thread = threading.Thread(target=run, name=f"library-probe-{root.id}", daemon=True)
        self._probes[root.id] = probe
        probe.thread.start()

    def _dispatch_scheduled(self, roots: list[RootView], night: int, now: datetime, busy: bool) -> None:
        """Start at most one due root; never beside an active run or a busy lane (manual work first), stuck ones aside."""
        if busy:
            return
        candidates = [r for r in roots if r.mode == "external" and r.enabled and r.schedule != "off"]
        for root in due_order(candidates, night, now):
            rs = self._roots.get(root.id)
            if rs is None or rs.online is None:
                continue  # not probed yet
            if rs.state != "idle":
                rs.last_skip = {"at": now, "reason": rs.state}
                continue
            try:
                run_id = self._start_run(root.id, root.newest_run.user_id, root.newest_run.visibility, trigger="scheduled", scope=None)
            except (ImportRunError, LookupError) as exc:  # a race with another start
                logger.info("Library automation: scheduled scan of %s not started: %s", root.label, exc)
                return
            rs.last_dispatch = now
            self._auto_lane(root.id).submit(run_id, lambda: self._drive_tracked(run_id))
            return

    # --- read model and facade ---------------------------------------------------------------------

    def _drive_tracked(self, run_id: str) -> None:
        """Every drive the automation starts: tracked while it runs; one that raised is not re-adopted for 60 s."""
        self._driving.add(run_id)
        try:
            self._drive(run_id)
        except Exception as exc:
            self._adopt_blocked_until[run_id] = self._clock.monotonic() + ADOPT_BACKOFF_S
            logger.warning("Library automation: driving an import failed: %.300s", repr(exc))
        finally:
            self._driving.discard(run_id)

    def _batch_stalled(self, root_id: str) -> bool:
        rs = self._roots.get(root_id)
        batch = rs.batch if rs is not None else None
        return batch is not None and batch.thread.is_alive() and self._clock.monotonic() - batch.started > FS_BATCH_STALL_S

    def _probe_hung(self, root_id: str) -> bool:
        """The root is not answering: its probe is past PROBE_TIMEOUT_S or its watch batch past FS_BATCH_STALL_S."""
        probe = self._probes.get(root_id)
        return (probe is not None and probe.thread is not None and probe.thread.is_alive()
                and self._clock.monotonic() - probe.started >= PROBE_TIMEOUT_S) or self._batch_stalled(root_id)

    def _state(self, root: RootView) -> str:
        """Precedence: waiting_confirmation > scanning (unresponsive when stuck) > unresponsive > offline > too_large > preparing > waiting_files > idle."""
        if root.newest_run is None:
            return "needs_first_import"
        if root.newest_run.state == "needs_confirmation":
            return "waiting_confirmation"
        if root.active_run is not None:
            # A step hung on a dead share commits nothing: a hung probe plus no progress past the stall window.
            stuck = self._probe_hung(root.id) and (
                self._clock.now() - local(root.active_run.updated_at)).total_seconds() > FS_BATCH_STALL_S
            return "unresponsive" if stuck else "scanning"
        if self._probe_hung(root.id):
            return "unresponsive"
        rs = self._roots.get(root.id)
        if rs is not None and rs.online is False:
            return "offline"
        watch = self._watches.get(root.id) if root.watch else None
        if watch is not None:
            if watch.phase == "too_large":
                return "too_large"
            if watch.phase == "baseline":
                return "preparing"
            if watch.stats.pending_files:
                return "waiting_files"
        return "idle"

    def _entry(self, root: RootView) -> dict[str, Any]:
        rs, watch = self._roots.get(root.id), (self._watches.get(root.id) if root.watch else None)
        state, run, last = self._state(root), root.active_run, root.newest_run
        held = root.newest_run is not None and root.newest_run.state == "needs_confirmation"
        full = root.last_full_run
        due = None if held else next_scan_at(root.schedule, self._night, full)
        counters = last.counters if last else {}
        return {
            "root_id": root.id, "label": root.label, "schedule": root.schedule, "watch": root.watch,
            "watch_interval_s": root.watch_interval_s, "state": state,
            "state_detail": {"pending_files": watch.stats.pending_files if watch else 0, "dirs_listed": watch.stats.dirs_listed if watch else 0,
                             "dirs_total": watch.stats.dirs_total if watch else None, "since": None},
            "active_run": None if run is None else {"id": run.id, "trigger": run.trigger or "manual", "scope_dirs": run.scope_dirs,
                                                    "inspected": run.counters.get("inspected", 0)},
            "last_run": None if last is None or run is not None else {
                "id": last.id, "trigger": last.trigger or "manual", "state": last.state,
                "finished_at": local(last.finished_at) if last.finished_at else None, "scope_dirs": last.scope_dirs,
                "counters": {k: counters.get(k, 0) for k in ("indexed", "updated", "relinked", "missing")}, "error": last.error},
            "last_full_run_at": local(full.finished_at or full.created_at) if full else None,
            "next_scan_at": due, "last_skip": dict(rs.last_skip) if rs and rs.last_skip else None,
        }

    def _external(self) -> list[RootView]:
        return [r for r in self._views.values() if r.mode == "external"]

    def _stalled(self) -> bool:
        return self._heartbeat_at is None or (self._clock.now() - self._heartbeat_at).total_seconds() > HEARTBEAT_STALE_S

    def snapshot(self) -> dict[str, Any]:
        """media_schemas.LibraryAutomation as a dict; memory only, never touches the NAS or the database."""
        return {"server_timezone": self._clock.now().tzname() or "UTC", "night_hour": self._night,
                "poller": {"heartbeat_at": self._heartbeat_at, "stalled": self._stalled()},
                "roots": [self._entry(r) for r in self._external()]}

    def root_entry(self, root_id: str) -> dict[str, Any] | None:
        """One media_schemas.AutomationRoot as a dict; None for an unknown root."""
        root = self._views.get(root_id)
        return self._entry(root) if root is not None and root.mode == "external" else None

    def roots_changed(self) -> None:
        """The router committed new per-root settings: reload them, baseline a root whose Watch turns on, drop one turned off."""
        roots, night, _ = self._load()
        self._views, self._night = {r.id: r for r in roots}, night
        for root in self._external():
            watch = self._watches.get(root.id)
            if root.watch and watch is None:
                self._watches.setdefault(root.id, self._new_watch(root))  # setdefault: the poller may have created one first
            elif not root.watch and watch is not None:
                self._watches.pop(root.id, None)  # the rows stay (a quick re-enable skips the baseline)
                if root.id in self._roots:
                    self._roots[root.id].pending = None
            elif watch is not None:
                watch.interval_s = root.watch_interval_s
        live = {r.id for r in self._external()}
        for gone in set(list(self._watches)) - live:
            self._watches.pop(gone, None)
        for gone in (set(list(self._roots)) | set(list(self._probes))) - live:  # deleted roots: drop their state too
            self._roots.pop(gone, None)
            self._probes.pop(gone, None)

    def _new_watch(self, root: RootView) -> RootWatch:
        """Restore the root's rows (all under 7 days old), else a baseline that marks dirs newer than the last full run."""
        # One stale row re-baselines the whole root; per-row ages if partial staleness ever matters.
        stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)
        with SessionLocal() as db:
            rows = db.query(LibraryWatchDir).filter(LibraryWatchDir.root_id == root.id).all()
        # No root row "" = a write died midway (see _write_rows): never watch a partial set.
        known = {r.path: r.mtime_ns for r in rows}
        if "" not in known or not all(r.updated_at and r.updated_at >= stale for r in rows):
            known = {}
        full = root.last_full_run
        cutoff = None if full is None else round(full.created_at.replace(tzinfo=UTC).timestamp() * 1e9)
        rs = self._roots.setdefault(root.id, RootState())
        rs.baselining, rs.touched = not known, self._clock.monotonic()
        return RootWatch(root.id, self._fs(root), known=known, full_run_cutoff_ns=cutoff, interval_s=root.watch_interval_s,
                         monotonic=self._clock.monotonic)

    def _probed_online(self, root: RootView) -> str | None:
        """A skip reason from cached state; a never-probed root gets one bounded fresh probe (never blocks past PROBE_TIMEOUT_S)."""
        rs = self._roots.setdefault(root.id, RootState())
        if not root.enabled:
            return "offline"
        if (early := skip_reason(root, probe_alive=False, online=True)) in ("needs_first_import", "waiting_confirmation", "scanning"):
            return early  # these skip regardless of reachability: no NAS probe
        if rs.online is None and root.id not in self._probes:
            self._begin_probe(root)
        probe = self._probes.get(root.id)
        if rs.online is None and probe is not None and probe.thread is not None:
            probe.thread.join(PROBE_TIMEOUT_S)
            if not probe.thread.is_alive():
                rs.online = probe.state in ONLINE_STATES
                self._probes.pop(root.id, None)
        return skip_reason(root, probe_alive=self._probe_hung(root.id) or (rs.online is None), online=rs.online)

    def scan_now(self, admin_id: str, root_id: str | None = None) -> dict[str, Any]:
        """Full manual runs for one or every eligible root, on the manual lane in label order."""
        roots, night, _ = self._load()
        self._views, self._night = {r.id: r for r in roots}, night
        targets = [r for r in self._external() if r.enabled or r.id == root_id]
        if root_id is not None:
            targets = [r for r in targets if r.id == root_id]
            if not targets:
                raise LookupError(root_id)
        result: dict[str, list] = {"started": [], "queued": [], "skipped": []}
        first = True
        for root in targets:
            reason = self._probed_online(root)
            if reason is not None:
                result["skipped"].append({"root_id": root.id, "reason": reason})
            elif first:
                try:
                    run_id = self._start_run(root.id, admin_id, root.newest_run.visibility, trigger="manual", scope=None)
                except ImportRunError:
                    result["skipped"].append({"root_id": root.id, "reason": "scanning"})
                    continue
                first = False
                result["started"].append({"root_id": root.id, "run_id": run_id})
                self._manual.submit(run_id, lambda run_id=run_id: self._drive_tracked(run_id))
            else:
                result["queued"].append(root.id)
                self._manual.submit(f"scan:{root.id}", lambda root=root: self._run_queued(admin_id, root.id))
        return result

    def _run_queued(self, admin_id: str, root_id: str) -> None:
        """A queued manual scan's turn: re-check the root's state first (it may have changed while it waited)."""
        roots, night, _ = self._load()
        self._views, self._night = {r.id: r for r in roots}, night
        root = self._views.get(root_id)
        reason = None if root is None else self._probed_online(root)
        if root is None or reason is not None:
            if root is not None:
                self._roots.setdefault(root_id, RootState()).last_skip = {"at": self._clock.now(), "reason": reason}
            return
        try:
            run_id = self._start_run(root_id, admin_id, root.newest_run.visibility, trigger="manual", scope=None)
        except ImportRunError:
            return
        self._drive_tracked(run_id)  # already on the manual lane

    def submit(self, run_id: str) -> None:
        """Queue drive(run_id) on the manual lane (a resumed import)."""
        self._manual.submit(run_id, lambda: self._drive_tracked(run_id))

    def _root_error(self) -> str | None:
        return next((f"{r.label}: {rs.error}" for r in self._external() if (rs := self._roots.get(r.id)) and rs.error), None)

    def diagnostics(self) -> dict[str, Any]:
        """media_schemas.LibraryAutomationDiagnostics as a dict, without runs_24h, with raw last_error (S3 redacts)."""
        roots = []
        for root in self._external():
            entry, rs = self._entry(root), self._roots.get(root.id)
            stats = self._watches[root.id].stats if root.watch and root.id in self._watches else None
            roots.append({
                "label": root.label, "schedule": root.schedule, "watch": root.watch, "state": entry["state"],
                "dirs_watched": stats.dirs_watched if stats else 0, "pending_files": stats.pending_files if stats else 0,
                "last_pass_at": stats.last_pass_at if stats else None, "last_pass_seconds": stats.last_pass_seconds if stats else None,
                "stats_last_pass": stats.stats_last_pass if stats else 0, "watch_errors_last_pass": stats.watch_errors_last_pass if stats else 0,
                "last_change_at": stats.last_change_at if stats else None, "last_dispatch_at": rs.last_dispatch if rs else None,
                "last_skip": entry["last_skip"], "next_scan_at": entry["next_scan_at"]})
        return {"poller": {"heartbeat_at": self._heartbeat_at, "stalled": self._stalled(), "last_error": self._last_error or self._root_error()},
                "driver": {"active_run_id": self._manual.current or next((lane.current for lane in self._auto.values() if lane.current), None),
                           "queued": self._manual.queued + sum(lane.queued for lane in self._auto.values())},
                "roots": roots}


automation = LibraryAutomation()
