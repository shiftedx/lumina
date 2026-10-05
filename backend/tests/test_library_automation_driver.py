"""Poller, probes, lanes and scheduled dispatch."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from library_automation_support import Env, SyncLane, probe_threads, wait_for

from app import db as db_module
from app.services.library_automation import _Lane
from app.models import ImportRun
from app.services.library_import import ImportRunError, LibraryImportService


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    yield Env(tmp_path)
    monkeypatch.undo()
    time.tzset()


def due_root(env: Env, label: str, *, finished_hours_ago: float = 1.0, schedule: str = "15m", **kw) -> str:
    root_id = env.root(label, schedule=schedule, **kw)
    end = env.fake_clock.naive_utc - timedelta(hours=finished_hours_ago)
    env.run(root_id, created=end - timedelta(minutes=5), finished=end)
    return root_id


def started_roots(env: Env) -> list[str]:
    return [root_id for root_id, trigger, _ in env.starts if trigger == "scheduled"]


def test_one_automatic_run_at_a_time_two_roots_due(env):
    a = due_root(env, "A", finished_hours_ago=1)
    b = due_root(env, "B", finished_hours_ago=2)  # older due
    env.tick(3)
    assert started_roots(env) == [b]
    assert env.starts[0][2] is None  # a full run
    assert env.driven == [env.active_runs()[0].id]
    env.finish(env.driven[0])
    env.tick()
    assert started_roots(env) == [b, a]


def test_dispatch_picks_at_most_one_root_per_tick(env):
    for label in "ABC":
        due_root(env, label)
    env.tick(2)
    assert len(started_roots(env)) == 1


def test_an_active_manual_run_blocks_automatic_starts(env):
    a = due_root(env, "A")
    other = due_root(env, "Other", schedule="off")
    manual = env.run(other, state="running")
    env.tick(3)
    assert started_roots(env) == []
    env.finish(manual)
    env.tick()
    assert started_roots(env) == [a]


def test_a_busy_manual_lane_blocks_automatic_starts(env):
    busy = SyncLane()
    busy.busy = True
    env.automation._manual = busy
    due_root(env, "A")
    env.tick(3)
    assert started_roots(env) == []
    busy.busy = False
    env.tick()
    assert len(started_roots(env)) == 1


def test_a_manual_full_run_that_just_finished_satisfies_the_schedule(env):
    root_id = env.root("A", schedule="nightly")
    # now is 12:00; a manual full run started 03:05 after the 03:00 slot, so the next slot is tomorrow
    day = env.fake_clock.naive_utc.replace(hour=3, minute=5)
    env.run(root_id, created=day, finished=day + timedelta(minutes=15))
    env.tick(5)
    assert started_roots(env) == []


def test_automatic_run_never_blocks_a_manual_start_for_another_root(tmp_path):
    auto, manual = _Lane("library-auto"), _Lane("library-manual")
    auto.start()
    manual.start()
    hold, ran = threading.Event(), threading.Event()
    try:
        auto.submit("auto-run", hold.wait)
        assert wait_for(lambda: auto.current == "auto-run")
        manual.submit("manual-run", ran.set)
        assert ran.wait(2)
    finally:
        hold.set()
        auto.close()
        manual.close()


def test_an_offline_root_is_skipped_with_a_recorded_reason_and_runs_when_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    env = Env(tmp_path, probe_fn=None)  # None: the real read-only probe against the temp tree
    root_id = due_root(env, "A")
    path = tmp_path / "mnt" / "A"
    path.rename(tmp_path / "away")
    env.tick(3)
    assert started_roots(env) == []
    assert env.state(root_id) == "offline"
    assert env.automation._roots[root_id].last_skip["reason"] == "offline"
    (tmp_path / "away").rename(path)
    env.tick(2)
    assert started_roots(env) == [root_id]
    monkeypatch.undo()
    time.tzset()


def test_identity_mismatch_is_offline(env):
    root_id = due_root(env, "A")
    env.probe.modes[root_id] = "identity_mismatch"
    env.tick(3)
    assert env.state(root_id) == "offline"
    assert started_roots(env) == []


def test_offline_skip_is_logged_once_per_state_change(env, caplog):
    root_id = due_root(env, "A")
    env.probe.modes[root_id] = "offline"
    with caplog.at_level(logging.INFO, logger="app.services.library_automation"):
        env.tick(20)
    assert len([r for r in caplog.records if "offline" in r.getMessage()]) == 1
    assert str(env.tmp_path) not in caplog.text


def test_never_imported_root_is_needs_first_import_and_never_started(env):
    root_id = env.root("A", schedule="15m", watch=True)
    env.tick(5)
    assert env.state(root_id) == "needs_first_import"
    assert env.starts == [] and env.probe.calls[root_id] == 0


def test_disabled_and_managed_roots_are_never_dispatched(env):
    due_root(env, "Disabled", enabled=False)
    due_root(env, "Managed", mode="managed")
    env.tick(5)
    assert env.starts == [] and sum(env.probe.calls.values()) == 0


def test_a_root_with_automation_off_is_never_probed(env):
    root_id = due_root(env, "A", schedule="off")
    env.tick(3)
    assert env.probe.calls[root_id] == 0 and env.starts == []


def test_hung_probe_marks_root_unresponsive_and_never_stacks(env):
    a = due_root(env, "A", finished_hours_ago=2)  # older due, would go first
    b = due_root(env, "B", finished_hours_ago=1)
    release = threading.Event()
    env.probe.modes[a] = release
    try:
        env.tick(10)
        assert len(probe_threads(a)) == 1
        assert env.probe.calls[a] == 1
        assert env.state(a) == "unresponsive"
        assert started_roots(env) == [b]
    finally:
        release.set()
    assert wait_for(lambda: not probe_threads(a))
    env.finish(env.driven[0])
    env.tick()
    assert env.state(a) == "idle"
    assert started_roots(env) == [b, a]


def test_a_scan_hung_on_one_share_does_not_hold_automatic_scans_on_other_roots(tmp_path, monkeypatch):
    """#160: the hung walk's thread cannot be cancelled, so it is isolated on its root's lane and stops counting as busy."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    release, driven = threading.Event(), []
    env = Env(tmp_path, lane_factory=_Lane, drive_fn=lambda run_id: (driven.append(run_id), release.wait() if len(driven) == 1 else None))
    a = due_root(env, "A", finished_hours_ago=2)  # older due: goes first, then its share stops answering mid-walk
    b = due_root(env, "B", finished_hours_ago=1)
    try:
        env.tick(2)
        assert started_roots(env) == [a] and wait_for(lambda: len(driven) == 1)
        env.probe.modes[a] = release
        run_a = env.active_runs()[0].id
        with db_module.SessionLocal() as db:  # the hung step commits nothing
            db.query(ImportRun).filter(ImportRun.id == run_a).update({"updated_at": env.fake_clock.naive_utc}, synchronize_session=False)
            db.commit()
        env.tick(2)
        assert started_roots(env) == [a]  # not stuck yet: an ordinary busy scan
        env.tick(2)
        assert env.automation.root_entry(a)["state"] == "unresponsive"
        assert started_roots(env) == [a, b]
        assert wait_for(lambda: len(driven) == 2) and driven[1] != run_a
        assert env.automation._auto[a].current == run_a  # still parked, honestly reported above
    finally:
        release.set()
        env.automation.stop()
        monkeypatch.undo()
        time.tzset()


def test_poller_never_calls_the_filesystem(env, monkeypatch):
    due_root(env, "A")
    env.root("B", schedule="nightly", watch=True)
    mnt = str(env.tmp_path / "mnt")

    def guard(real):
        def wrapped(path, *args, **kwargs):
            if str(path).startswith(mnt):
                raise AssertionError(f"poller touched {path}")
            return real(path, *args, **kwargs)
        return wrapped

    for name in ("stat", "lstat", "scandir", "listdir"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: (_ for _ in ()).throw(AssertionError(str(self))) if str(self).startswith(mnt) else True)
    env.tick(5)
    assert len(started_roots(env)) == 1


def test_heartbeat_advances_every_tick(env):
    env.tick()
    first = env.automation._heartbeat_at
    env.tick()
    assert env.automation._heartbeat_at - first == timedelta(seconds=15)


def test_stop_returns_within_2s_even_with_a_hung_probe_and_hung_drive(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    env = Env(tmp_path, lane_factory=_Lane)
    root_id = due_root(env, "A")
    release = threading.Event()
    env.probe.modes[root_id] = release
    automation = env.automation
    try:
        automation.start()
        assert wait_for(lambda: probe_threads(root_id))
        automation._auto_lane(root_id).submit("stuck", release.wait)
        assert wait_for(lambda: automation._auto[root_id].current == "stuck")
        began = time.monotonic()
        automation.stop()
        assert time.monotonic() - began < 2
        assert not automation._poller.is_alive()
    finally:
        release.set()
        monkeypatch.undo()
        time.tzset()


def test_a_lane_survives_a_failing_job(caplog):
    lane = _Lane("library-test")
    lane.start()
    done = threading.Event()

    def boom():
        raise RuntimeError("no such file /mnt/nas/secret")

    try:
        with caplog.at_level(logging.WARNING, logger="app.services.library_automation"):
            lane.submit("bad", boom)
            lane.submit("good", done.set)
            assert done.wait(2)
            assert wait_for(lambda: "failed" in caplog.text)
        assert "RuntimeError" in caplog.text and "/mnt/nas/secret" not in caplog.text
        assert wait_for(lambda: not lane.busy)
    finally:
        lane.close()


@pytest.mark.parametrize("seed", range(50))
def test_at_most_one_automatic_run_active_across_random_interleavings(seed, env):
    rng = random.Random(seed)
    roots = [due_root(env, label, finished_hours_ago=rng.uniform(0.1, 3), schedule=rng.choice(["15m", "1h", "nightly"]))
             for label in "ABC"]

    def manual_start(root_id):
        with db_module.SessionLocal() as db:
            try:
                LibraryImportService(db).start(root_id, "admin", "shared")
                db.commit()
            except ImportRunError:
                db.rollback()

    for _ in range(40):
        op = rng.choice(["tick", "tick", "advance", "finish", "manual", "cancel"])
        active = env.active_runs()
        if op == "tick":
            env.tick()
        elif op == "advance":
            env.fake_clock.advance(rng.uniform(60, 7200))
        elif op == "finish" and active:
            env.finish(rng.choice(active).id, rng.choice(["succeeded", "partial", "failed"]))
        elif op == "manual":
            manual_start(rng.choice(roots))
        elif op == "cancel" and active:
            env.finish(rng.choice(active).id, "cancelled")
        active = env.active_runs()
        assert len([r for r in active if r.trigger in ("scheduled", "watch")]) <= 1
        assert len({r.root_id for r in active}) == len(active)
