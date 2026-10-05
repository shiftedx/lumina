"""Watch wiring, held-run pause, run adoption and the lifespan hook."""
from __future__ import annotations

import inspect
import math
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from library_automation_support import Env
from test_library_automation_watcher import FakeClock as FsClock
from test_library_automation_watcher import FakeFs

from app import db as db_module
from app.models import ImportRun, LibraryWatchDir, StorageRoot
from app.persistence import persistence_metrics_snapshot, reset_persistence_metrics, write_transaction
from app.services import library_automation as la
from app.services import library_watch
from app.services.library_automation import FS_BATCH_STALL_S, TICK_S
from app.services.library_import import LibraryImportService, drive
from app.services.library_import import stop_event as import_stop_event

REAL_CONFIRM = LibraryImportService.confirm


@pytest.fixture(autouse=True)
def no_confirm(monkeypatch):
    def refuse(self, run):
        raise AssertionError("automation must never confirm a held run")

    monkeypatch.setattr(LibraryImportService, "confirm", refuse)
    yield
    assert not [t for t in threading.enumerate() if t.name == "library-automation" and t.is_alive()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    import_stop_event.clear()  # an earlier lifespan test's shutdown leaves it set, and drive() would return at once
    yield lambda **kw: Env(tmp_path, **kw)
    monkeypatch.undo()
    time.tzset()


BATCH_JOIN_S = 60


def tick(env: Env, n: int = 1, advance: float = TICK_S, hung: tuple[str, ...] = ()) -> None:
    """env.tick, then wait for every watch batch thread that is not deliberately hung.

    The wait is generous on purpose: FakeFs.list_dir scans every dir, so a ~2.9k-dir pass takes 1-2 s of wall time
    on a loaded machine. A shorter join left the batch alive, the next tick rightly refused to harvest it, and the
    baseline landed a tick late (the flake in the crash-midway-through-a-large-commit test).
    """
    for _ in range(n):
        env.tick(advance=advance)
        for thread in threading.enumerate():
            if thread.name.startswith("library-watch-") and thread.name.removeprefix("library-watch-") not in hung:
                thread.join(BATCH_JOIN_S)
                assert not thread.is_alive(), f"{thread.name} still listing after {BATCH_JOIN_S} s"


def library(fs: FakeFs) -> FakeFs:
    fs.add("Show/Season 01/e1.mkv")
    fs.add("Show/tvshow.nfo")
    fs.add("Movie/movie.mkv")
    return fs


def watched(env: Env, label: str = "TV", *, schedule: str = "off", interval: int = 60) -> str:
    root_id = env.root(label, schedule=schedule, watch=True, interval=interval)
    env.run(root_id)
    return root_id


def runs(root_id: str) -> list[ImportRun]:
    with db_module.SessionLocal() as db:
        return db.query(ImportRun).filter(ImportRun.root_id == root_id).order_by(ImportRun.created_at).all()


def rows(root_id: str) -> dict[str, int]:
    with db_module.SessionLocal() as db:
        return {r.path: r.mtime_ns for r in db.query(LibraryWatchDir).filter(LibraryWatchDir.root_id == root_id)}


def watch_starts(env: Env) -> list:
    return [scope for _, trigger, scope in env.starts if trigger == "watch"]


def baselined(env: Env, fs: FakeFs, root_id: str) -> None:
    tick(env, 3)
    assert env.automation._watches[root_id].phase == "watching" and rows(root_id) == fs.dirs


# --- 9.4-M: mass-missing is held, never confirmed by automation --------------------------------------------------

def test_mass_missing_is_never_auto_confirmed_9_4_M(env, tmp_path):
    e = env(drive_fn=drive)
    e.fake_clock._now = datetime.now(UTC)  # real files, real runs: the fake clock starts at the wall clock
    root_id = e.root("TV", schedule="15m", watch=True, interval=60)
    root = tmp_path / "mnt" / "TV"
    for i in range(60):
        (root / "Keep").mkdir(exist_ok=True)
        (root / "Keep" / f"k{i:02}.mkv").write_bytes(b"k" * (i + 1))
    for i in range(40):
        (root / "Gone").mkdir(exist_ok=True)
        (root / "Gone" / f"g{i:02}.mkv").write_bytes(b"g" * (i + 1))
    time.sleep(0.01)
    first = la.start_run_default(root_id, "admin", "shared", trigger="manual", scope=None)
    drive(first)
    assert runs(root_id)[0].state == "succeeded"

    tick(e, 3)  # probe, baseline, rows
    for path in (root / "Gone").iterdir():
        path.unlink()
    for _ in range(12):
        tick(e)
        if len(runs(root_id)) == 2:
            break
    held = runs(root_id)[-1]
    assert (held.trigger, held.scope, held.state) == ("watch", [{"dir": "Gone", "deep": False}], "needs_confirmation")

    for _ in range(7 * 24):  # a week of ticks with a 15 min schedule and Watch on
        tick(e, advance=3600)
    assert len(runs(root_id)) == 2
    assert e.automation.root_entry(root_id)["state"] == "waiting_confirmation"

    with db_module.SessionLocal() as db:  # the admin confirms (the real confirm, for this step only)
        REAL_CONFIRM(LibraryImportService(db), db.get(ImportRun, held.id))
        db.commit()
    confirmed = runs(root_id)[1]
    assert confirmed.state == "succeeded" and confirmed.counters["missing"] == 40
    tick(e, 3)
    assert runs(root_id)[2].trigger == "scheduled"  # automation resumed (later runs: the fake clock is a week ahead)


def test_a_held_run_pauses_watch_polling_and_keeps_no_new_dirty_state(env):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    e.run(root_id, state="needs_confirmation", trigger="scheduled", created=e.fake_clock.naive_utc)
    tick(e)
    calls = fs.stat_calls, fs.list_calls, fs.read_calls
    fs.add("Movie/new.mkv")
    tick(e, 40)
    assert (fs.stat_calls, fs.list_calls, fs.read_calls) == calls
    assert not e.automation._watches[root_id].dirty and watch_starts(e) == []
    assert e.automation.root_entry(root_id)["state"] == "waiting_confirmation"


def test_a_manual_rescan_resolves_the_pause(env):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    e.run(root_id, state="needs_confirmation", trigger="watch", created=e.fake_clock.naive_utc)
    tick(e, 4)
    calls = fs.stat_calls
    e.run(root_id, state="succeeded", trigger="manual", created=e.fake_clock.naive_utc + timedelta(seconds=1))
    fs.rm("Movie/movie.mkv")
    tick(e, 10)
    assert fs.stat_calls > calls and e.automation.root_entry(root_id)["state"] == "scanning"  # the watch run (fake drive)
    assert watch_starts(e) == [[{"dir": "Movie", "deep": False}]]


def test_no_automation_code_path_calls_confirm():
    for module in (la, library_watch):
        assert "confirm(" not in inspect.getsource(module)


def test_identity_mismatch_is_offline_and_snapshot_untouched(env):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    watch = e.automation._watches[root_id]
    e.probe.modes[root_id] = "identity_mismatch"
    tick(e, 2)
    before, calls = dict(watch.dirs), fs.stat_calls
    fs.add("Other/x.mkv")
    tick(e, 20)
    assert e.automation.root_entry(root_id)["state"] == "offline"
    assert fs.stat_calls == calls and watch.dirs == before and rows(root_id) == before
    assert e.automation._watches[root_id] is watch and watch.phase == "watching"


class HangingFs(FakeFs):
    def __init__(self) -> None:
        super().__init__(FsClock())
        self.release = threading.Event()

    def stat_dir(self, path: str) -> int:
        self.release.wait()
        return super().stat_dir(path)


def test_a_hung_stat_in_a_fs_batch_makes_that_root_unresponsive_not_the_poller(env):
    fs = library(HangingFs())
    e = env(fs_factory=lambda root: fs)
    a = watched(e, "A")
    b = e.root("B", schedule="15m")
    e.run(b, created=e.fake_clock.naive_utc - timedelta(hours=2))

    def batches() -> list[threading.Thread]:
        return [t for t in threading.enumerate() if t.name == f"library-watch-{a}" and t.is_alive()]

    try:
        beat = []
        for _ in range(10):
            tick(e, hung=(a,))
            beat.append(e.automation._heartbeat_at)
        assert beat == sorted(set(beat)) and len(beat) == 10
        assert e.automation.root_entry(a)["state"] == "unresponsive"
        assert e.state(a) == "unresponsive"
        assert len(batches()) == 1
        assert [r for r, trigger, _ in e.starts if trigger == "scheduled"] == [b]
    finally:
        fs.release.set()
    assert all(t.join(2) is None and not t.is_alive() for t in batches())
    tick(e, 2)
    assert e.automation.root_entry(a)["state"] in ("idle", "scanning")
    assert e.state(a) != "unresponsive"


def test_unresponsive_needs_the_stall_to_outlast_FS_BATCH_STALL_S(env):
    fs = library(HangingFs())
    e = env(fs_factory=lambda root: fs)
    a = watched(e, "A")
    try:
        tick(e, 2, hung=(a,))  # probe, then the batch starts and hangs
        assert e.state(a) == "idle"
        tick(e, int(FS_BATCH_STALL_S // TICK_S) + 1, hung=(a,))
        assert e.state(a) == "unresponsive"
    finally:
        fs.release.set()


# --- adoption ------------------------------------------------------------------------------------------------------

def set_updated(run_id: str, at: datetime) -> None:
    with db_module.SessionLocal() as db:
        db.query(ImportRun).filter(ImportRun.id == run_id).update({"updated_at": at}, synchronize_session=False)
        db.commit()


def test_a_scheduled_run_left_running_is_adopted_after_restart(env):
    first = env()
    root_id = first.root("TV")
    first.run(root_id, created=first.fake_clock.naive_utc - timedelta(days=1))
    orphan = first.run(root_id, state="running", trigger="scheduled", created=first.fake_clock.naive_utc - timedelta(minutes=10))
    set_updated(orphan, first.fake_clock.naive_utc - timedelta(minutes=5))
    second = env(drive_fn=drive)  # a new process
    tick(second)
    assert runs(root_id)[-1].state == "succeeded"
    assert second.automation._driving == set()


def test_a_run_from_before_this_process_is_adopted_even_when_maintenance_keeps_it_fresh(env):
    e = env()
    root_id = e.root("TV")
    e.run(root_id, created=e.fake_clock.naive_utc - timedelta(days=1))
    orphan = e.run(root_id, state="running", trigger="manual", created=e.fake_clock.naive_utc - timedelta(minutes=3))
    set_updated(orphan, e.fake_clock.naive_utc - timedelta(seconds=10))  # advance_active steps it every minute
    tick(e)
    assert e.driven == [orphan] and e.automation._manual.labels == [orphan]


def test_a_fresh_manual_run_being_driven_elsewhere_is_not_adopted(env):
    e = env()
    root_id = e.root("TV")
    e.run(root_id, created=e.fake_clock.naive_utc - timedelta(days=1))
    e.fake_clock.advance(60)
    run = e.run(root_id, state="running", trigger="manual", created=e.fake_clock.naive_utc)  # a BackgroundTask's run
    set_updated(run, e.fake_clock.naive_utc - timedelta(seconds=10))
    tick(e, 4)
    assert e.driven == []
    set_updated(run, e.fake_clock.naive_utc - timedelta(seconds=la.ORPHAN_AFTER_S + 1))  # its driver died
    tick(e, advance=0)
    assert e.driven == [run]


def test_adoption_backs_off_60s_after_a_failed_drive(env):
    attempts = []

    def boom(run_id):
        attempts.append(run_id)
        raise RuntimeError("mount went away")

    e = env(drive_fn=boom)
    root_id = e.root("TV")
    orphan = e.run(root_id, state="running", trigger="watch", created=e.fake_clock.naive_utc - timedelta(minutes=10))
    set_updated(orphan, e.fake_clock.naive_utc - timedelta(minutes=5))
    tick(e)
    assert attempts == [orphan] and e.automation._driving == set()
    tick(e, 3)  # 45 s
    assert attempts == [orphan]
    tick(e, 2)  # 75 s
    assert attempts == [orphan, orphan]
    assert e.automation._auto[root_id].labels == [orphan, orphan]


# --- watch rows and restart ----------------------------------------------------------------------------------------

def test_restart_redetects_uncommitted_dirs(env):
    fs = library(FakeFs(FsClock()))
    first = env(fs_factory=lambda root: fs)
    root_id = watched(first)
    baselined(first, fs, root_id)
    fs.rm("Movie/movie.mkv")
    tick(first, 10)
    assert watch_starts(first) == [[{"dir": "Movie", "deep": False}]]
    first.finish(first.driven[0])  # the run finished, the process died before the poller committed the rows
    assert rows(root_id)["Movie"] != fs.dirs["Movie"]
    second = env(fs_factory=lambda root: fs)
    tick(second, 8)
    assert watch_starts(second) == [[{"dir": "Movie", "deep": False}]]


def test_turning_watch_off_keeps_rows_and_back_on_within_7_days_does_not_rebaseline(env):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    kept = rows(root_id)
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, root_id).watch_enabled = False
        db.commit()
    e.automation.roots_changed()
    tick(e, 5)
    assert root_id not in e.automation._watches and rows(root_id) == kept
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, root_id).watch_enabled = True
        db.commit()
    e.automation.roots_changed()
    lists = fs.list_calls
    tick(e, 2)
    watch = e.automation._watches[root_id]
    assert watch.phase == "watching" and watch.dirs == kept and fs.list_calls == lists


def spy_writes(monkeypatch, crash_at: int | None = None) -> list[str]:
    """Record every automation write transaction's name; optionally die (a killed process) in the crash_at-th one."""
    names: list[str] = []
    real = la.write_transaction

    def spy(db, *, name):
        names.append(name)
        if len(names) == crash_at:
            raise RuntimeError("killed mid-write")
        return real(db, name=name)

    monkeypatch.setattr(la, "write_transaction", spy)
    return names


def many(fs: FakeFs, top: str = "Many", n: int = 1200) -> FakeFs:
    for i in range(n):
        fs.mkdir(f"{top}/d{i:04}")
    return fs


def test_watch_rows_are_written_only_on_baseline_and_commit_in_chunks(env, monkeypatch):
    """~19.5k TV dirs in one transaction held the writer slot for seconds; at most 500 rows per transaction."""
    fs = many(library(FakeFs(FsClock())))
    names = spy_writes(monkeypatch)
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    chunks = math.ceil(len(fs.dirs) / la.BASELINE_ROWS_PER_TX)
    assert len(fs.dirs) > 1000 and set(names) == {"library_watch_baseline"} and len(names) >= chunks + 1
    written = len(names)
    tick(e, 20)
    assert len(names) == written  # an idle watch writes nothing
    fs.rm("Movie/movie.mkv")
    tick(e, 6)
    assert len(watch_starts(e)) == 1 and len(names) == written  # dispatched, not yet committed
    e.finish(e.driven[0])
    tick(e)
    assert set(names[written:]) == {"library_watch_commit"}
    assert rows(root_id) == fs.dirs
    written = len(names)
    many(fs, "New")
    tick(e, 12)
    e.finish(e.driven[-1])
    tick(e)
    assert len(names) - written >= chunks and set(names[written:]) == {"library_watch_commit"}
    assert rows(root_id) == fs.dirs


def test_a_crash_midway_through_the_baseline_rebaselines_instead_of_trusting_a_partial_set(env, monkeypatch):
    fs = many(library(FakeFs(FsClock())))
    spy_writes(monkeypatch, crash_at=3)
    first = env(fs_factory=lambda root: fs)
    root_id = watched(first)
    tick(first, 3)  # the baseline's third transaction dies: some chunks are on disk, the rest never will be
    assert 0 < len(rows(root_id)) < len(fs.dirs)
    monkeypatch.setattr(la, "write_transaction", write_transaction)
    lists = fs.list_calls
    second = env(fs_factory=lambda root: fs)
    baselined(second, fs, root_id)
    assert fs.list_calls - lists >= len(fs.dirs)  # rebaselined: every directory listed again
    lists = fs.list_calls
    third = env(fs_factory=lambda root: fs)
    tick(third, 2)
    assert third.automation._watches[root_id].dirs == fs.dirs and fs.list_calls == lists  # a complete set is reused


def test_a_crash_midway_through_a_large_commit_never_restores_a_partial_set(env, monkeypatch):
    fs = many(library(FakeFs(FsClock())))
    names = spy_writes(monkeypatch)
    first = env(fs_factory=lambda root: fs)
    root_id = watched(first)
    baselined(first, fs, root_id)
    before = rows(root_id)
    many(fs, "New")
    tick(first, 12)
    first.finish(first.driven[-1])
    spy_writes(monkeypatch, crash_at=3)
    tick(first)  # the commit's third transaction dies
    assert rows(root_id) not in (before, fs.dirs)  # half-written
    monkeypatch.setattr(la, "write_transaction", write_transaction)
    lists = fs.list_calls
    second = env(fs_factory=lambda root: fs)
    baselined(second, fs, root_id)
    assert fs.list_calls - lists >= len(fs.dirs)  # rebaselined, not watching the half-written set
    assert names  # the first spy saw the baseline


def test_chunked_watch_writes_hold_the_writer_slot_briefly(env, monkeypatch):
    """Each watch transaction stays far below WRITER_ADMISSION_TIMEOUT_SECONDS (5 s) at ~5k dirs."""
    fs = many(library(FakeFs(FsClock())), n=5000)
    reset_persistence_metrics()
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    for _ in range(20):
        tick(e)
        if rows(root_id):
            break
    assert rows(root_id) == fs.dirs
    held = persistence_metrics_snapshot()["library_watch_baseline"]["max_ms"]
    assert held < 500, held


@pytest.mark.parametrize("ended", ["failed", "partial"])
def test_failed_watch_run_retries_no_sooner_than_10_minutes(env, ended):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    fs.rm("Movie/movie.mkv")
    tick(e, 6)
    assert len(watch_starts(e)) == 1
    e.finish(e.driven[0], ended)
    tick(e, int(la.FAILED_RETRY_S // TICK_S) - 2)
    assert len(watch_starts(e)) == 1 and e.automation._watches[root_id].dirty
    tick(e, 4)
    assert watch_starts(e) == [[{"dir": "Movie", "deep": False}]] * 2
    assert rows(root_id)["Movie"] != fs.dirs["Movie"]


# --- lifespan --------------------------------------------------------------------------------------------------

def test_lifespan_starts_and_stops_the_poller():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://localhost"):
        assert [t for t in threading.enumerate() if t.name == "library-automation" and t.is_alive()]
    # the autouse fixture asserts the poller thread is gone


# --- bfx: per-root isolation, the 7-day rule, startup delay ----------------------------------------------------

def test_one_roots_error_does_not_abort_the_tick_for_other_roots(env, monkeypatch):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    bad, good = watched(e, "Bad"), watched(e, "Good")
    real = la.LibraryAutomation._new_watch

    def flaky(self, root):
        if root.id == bad:
            raise OSError("db is locked")
        return real(self, root)

    monkeypatch.setattr(la.LibraryAutomation, "_new_watch", flaky)
    tick(e, 3)
    assert good in e.automation._watches and e.automation._watches[good].phase == "watching"
    assert "Bad: OSError" in e.automation.diagnostics()["poller"]["last_error"]


def test_completed_passes_refresh_row_age_so_a_restart_after_weeks_reuses_the_snapshot(env):
    fs = library(FakeFs(FsClock()))
    e = env(fs_factory=lambda root: fs)
    root_id = watched(e)
    baselined(e, fs, root_id)
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    with db_module.SessionLocal() as db:
        db.query(LibraryWatchDir).update({LibraryWatchDir.updated_at: old}, synchronize_session=False)
        db.commit()
    tick(e, 3)  # inside the hour: no write
    tick(e, 2, advance=la.TOUCH_EVERY_S)
    with db_module.SessionLocal() as db:
        assert all(r.updated_at > old + timedelta(days=29) for r in db.query(LibraryWatchDir))
    second = env(fs_factory=lambda root: fs)
    tick(second, 2)
    assert second.automation._watches[root_id].dirs == fs.dirs and not second.automation._roots[root_id].baselining


def test_poller_first_tick_waits_for_the_startup_delay():
    auto = la.LibraryAutomation()
    auto.start(first_tick_delay_s=60)
    time.sleep(0.3)
    assert auto._heartbeat_at is None
    auto.stop()


# --- 2.1.0 final review I5: a scan hung on a dead share shows unresponsive -------------------------------------------

@pytest.mark.parametrize("watch", [True, False])
def test_a_scan_stuck_on_a_hung_share_shows_unresponsive_not_scanning(env, watch):
    e = env()
    root_id = e.root("TV", watch=watch)
    e.run(root_id)
    run_id = e.run(root_id, state="running", created=e.fake_clock.naive_utc)
    e.automation._driving.add(run_id)  # our own lane drives it (hung inside its step), so it is never adopted
    hang = threading.Event()
    try:
        tick(e)
        assert e.automation.root_entry(root_id)["state"] == "scanning"
        e.probe.modes[root_id] = hang
        set_updated(run_id, e.fake_clock.naive_utc)
        tick(e, 2)  # the probe hangs past PROBE_TIMEOUT_S, but the run made progress just now: still scanning
        assert e.automation.root_entry(root_id)["state"] == "scanning"
        tick(e, int(FS_BATCH_STALL_S // TICK_S) + 1)  # no batch committed for longer than the stall window
        assert e.automation.root_entry(root_id)["state"] == "unresponsive"
    finally:
        hang.set()
    tick(e, 2)
    assert e.automation.root_entry(root_id)["state"] == "scanning"


def test_the_app_lifespan_starts_automation_with_the_first_tick_delay(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app, library_automation

    calls = []
    monkeypatch.setattr(library_automation, "start", lambda first_tick_delay_s=0: calls.append(first_tick_delay_s))
    monkeypatch.setattr(library_automation, "stop", lambda *a, **k: None)
    with TestClient(app):
        pass
    assert calls == [la.FIRST_TICK_DELAY_S]
