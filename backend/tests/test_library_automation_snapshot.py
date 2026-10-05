"""Snapshot read model, scan-now, settings hook and the facade signatures."""
from __future__ import annotations

import inspect
import threading
import time
from datetime import timedelta

import pytest
from library_automation_support import Env

from app import db as db_module
from app.models import LibraryWatchDir, StorageRoot
from app.services import library_automation as la
from app.services.library_automation import LibraryAutomation


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    yield Env(tmp_path)
    monkeypatch.undo()
    time.tzset()


def imported(env: Env, label: str, **kw) -> str:
    root_id = env.root(label, **kw)
    env.run(root_id)
    return root_id


def entry(env: Env, root_id: str) -> dict:
    return env.automation.root_entry(root_id)


def test_snapshot_matches_the_6_1_shape_and_never_includes_paths(env):
    root_id = imported(env, "TV", schedule="nightly", watch=True)
    env.tick(2)
    snap = env.automation.snapshot()
    assert set(snap) == {"server_timezone", "night_hour", "poller", "roots"}
    assert snap["server_timezone"] == "UTC" and snap["night_hour"] == 3
    assert set(snap["poller"]) == {"heartbeat_at", "stalled"} and snap["poller"]["stalled"] is False
    (root,) = snap["roots"]
    assert set(root) == {"root_id", "label", "schedule", "watch", "watch_interval_s", "state", "state_detail", "active_run", "last_run",
                         "last_full_run_at", "next_scan_at", "last_skip"}
    assert root["root_id"] == root_id and set(root["state_detail"]) == {"pending_files", "dirs_listed", "dirs_total", "since"}
    assert set(root["last_run"]) == {"id", "trigger", "state", "finished_at", "scope_dirs", "counters", "error"}
    assert str(env.tmp_path) not in repr(snap)


def test_managed_roots_are_not_listed(env):
    env.root("Managed", mode="managed")
    imported(env, "TV")
    env.tick()
    assert [r["label"] for r in env.automation.snapshot()["roots"]] == ["TV"]


def test_state_precedence_and_state_detail(env):
    first = env.root("A-first")
    held = imported(env, "B-held")
    env.run(held, state="needs_confirmation", created=env.fake_clock.naive_utc)
    busy = imported(env, "C-busy")
    env.run(busy, state="running", created=env.fake_clock.naive_utc)
    off = imported(env, "D-off", schedule="1h")
    env.probe.modes[off] = "offline"
    hung = imported(env, "E-hung", schedule="1h")
    env.probe.modes[hung] = threading.Event()
    idle = imported(env, "F-idle", schedule="1h")
    env.tick()
    env.fake_clock.advance(la.PROBE_TIMEOUT_S + 1)
    env.tick(advance=0)
    try:
        states = {r["label"]: r["state"] for r in env.automation.snapshot()["roots"]}
    finally:
        env.probe.modes[hung].set()
    assert states == {"A-first": "needs_first_import", "B-held": "waiting_confirmation", "C-busy": "scanning", "D-off": "offline",
                      "E-hung": "unresponsive", "F-idle": "idle"}
    assert first and idle


def test_watch_phases_drive_preparing_waiting_files_and_too_large(env):
    root_id = imported(env, "TV", watch=True)
    env.tick()
    from dataclasses import replace

    from app.services.library_watch import WatchStats

    class W:
        phase, interval_s = "baseline", 300
        stats = WatchStats(dirs_total=10, dirs_listed=4)

    watch = W()
    env.automation._watches[root_id] = watch
    assert entry(env, root_id)["state"] == "preparing" and entry(env, root_id)["state_detail"]["dirs_total"] == 10
    watch.phase, watch.stats = "watching", replace(watch.stats, pending_files=2)
    assert entry(env, root_id)["state"] == "waiting_files" and entry(env, root_id)["state_detail"]["pending_files"] == 2
    watch.phase = "too_large"
    assert entry(env, root_id)["state"] == "too_large"


def test_next_scan_at_null_when_off_paused_or_not_imported(env):
    off = imported(env, "Off")
    on = imported(env, "On", schedule="1h")
    held = env.root("Held", schedule="1h")
    env.run(held, state="needs_confirmation")
    fresh = env.root("Fresh", schedule="1h")
    env.tick()
    assert entry(env, off)["next_scan_at"] is None
    assert entry(env, on)["next_scan_at"] is not None and entry(env, on)["last_full_run_at"] is not None
    assert entry(env, held)["next_scan_at"] is None
    assert entry(env, fresh)["next_scan_at"] is None and entry(env, fresh)["last_full_run_at"] is None


def test_active_run_and_last_run_scope_dirs_counts(env):
    root_id = imported(env, "TV")
    env.run(root_id, state="partial", trigger="watch", scope=[{"dir": "a", "deep": False}, {"dir": "b", "deep": True}],
            created=env.fake_clock.naive_utc - timedelta(minutes=30))
    env.tick()
    last = entry(env, root_id)["last_run"]
    assert (last["trigger"], last["state"], last["scope_dirs"], last["counters"]) == (
        "watch", "partial", 2, {"indexed": 0, "updated": 0, "relinked": 0, "missing": 0})
    assert entry(env, root_id)["active_run"] is None
    run = env.run(root_id, state="running", trigger="scheduled", created=env.fake_clock.naive_utc)
    env.tick()
    active = entry(env, root_id)["active_run"]
    assert active == {"id": run, "trigger": "scheduled", "scope_dirs": None, "inspected": 0}


def test_diagnostics_matches_6_3(env):
    root_id = imported(env, "TV", schedule="1h", watch=True)
    env.tick()
    diag = env.automation.diagnostics()
    assert set(diag) == {"poller", "driver", "roots"}  # no runs_24h: S3 adds it
    assert set(diag["poller"]) == {"heartbeat_at", "stalled", "last_error"}
    assert diag["driver"] == {"active_run_id": None, "queued": 0}
    (root,) = diag["roots"]
    assert set(root) == {"label", "schedule", "watch", "state", "dirs_watched", "pending_files", "last_pass_at", "last_pass_seconds",
                         "stats_last_pass", "watch_errors_last_pass", "last_change_at", "last_dispatch_at", "last_skip", "next_scan_at"}
    assert root["label"] == "TV" and root_id and str(env.tmp_path) not in repr(diag)


def test_diagnostics_returns_the_raw_poller_error(env):
    env.automation._last_error = "RuntimeError('boom')"
    assert env.automation.diagnostics()["poller"]["last_error"] == "RuntimeError('boom')"


def test_poller_stalled_when_heartbeat_older_than_120s(env):
    assert env.automation.snapshot()["poller"] == {"heartbeat_at": None, "stalled": True}
    env.tick()
    assert env.automation.snapshot()["poller"]["stalled"] is False
    env.fake_clock.advance(la.HEARTBEAT_STALE_S + 1)
    assert env.automation.snapshot()["poller"]["stalled"] is True
    assert env.automation.diagnostics()["poller"]["stalled"] is True


def test_scan_now_all_roots_in_label_order_first_starts_rest_queued(env):
    b = imported(env, "B", schedule="off")
    a = imported(env, "A")
    result = env.automation.scan_now("admin-1")
    # SyncLane runs queued jobs inline at submit, so the queued roots start too; the contract shape is what matters.
    assert [s["root_id"] for s in result["started"]] == [a] and result["queued"] == [b] and result["skipped"] == []
    assert [root for root, _, _ in env.starts][0] == a


def test_scan_now_skips_with_reason(env):
    held, busy, off, fresh, hung = (env.root(n) for n in ("held", "busy", "off", "fresh", "hung"))
    for rid, state in ((held, "needs_confirmation"), (busy, "running"), (off, "succeeded")):
        env.run(rid, state=state)
    env.run(hung)
    env.probe.modes[off] = "offline"
    env.probe.modes[hung] = threading.Event()
    env.tick()
    env.fake_clock.advance(la.PROBE_TIMEOUT_S + 1)
    env.tick(advance=0)
    try:
        result = env.automation.scan_now("admin-1")
    finally:
        env.probe.modes[hung].set()
    reasons = {s["root_id"]: s["reason"] for s in result["skipped"]}
    assert reasons == {held: "waiting_confirmation", busy: "scanning", off: "offline", fresh: "needs_first_import", hung: "unresponsive"}
    assert result["started"] == [] and result["queued"] == []


def test_scan_now_probes_a_never_probed_root_with_a_bound(env, monkeypatch):
    monkeypatch.setattr(la, "PROBE_TIMEOUT_S", 0.2)
    root_id = imported(env, "TV")
    release = threading.Event()
    env.probe.modes[root_id] = release
    started = time.monotonic()
    try:
        result = env.automation.scan_now("admin-1", root_id)
    finally:
        release.set()
    assert time.monotonic() - started < 2
    assert result["skipped"] == [{"root_id": root_id, "reason": "unresponsive"}]
    env.probe.modes[root_id] = "online"
    other = imported(env, "Other")
    assert env.automation.scan_now("admin-1", other)["started"][0]["root_id"] == other
    assert env.probe.calls[other] == 1


def test_queued_root_is_skipped_if_its_state_changes_before_its_turn(env):
    a, b = imported(env, "A"), imported(env, "B")
    queue_lane = []

    class HoldLane:
        name, current, queued, busy = "m", None, 0, False

        def submit(self, label, job):
            queue_lane.append((label, job))

        def start(self): ...
        def close(self): ...

    env.automation._manual = HoldLane()
    result = env.automation.scan_now("admin-1")
    assert [s["root_id"] for s in result["started"]] == [a] and result["queued"] == [b]
    env.run(b, state="needs_confirmation", created=env.fake_clock.naive_utc)
    for _, job in queue_lane[1:]:
        job()
    assert [r for r, t, _ in env.starts if t == "manual"] == [a]
    assert env.automation._roots[b].last_skip["reason"] == "waiting_confirmation"


def test_scan_now_is_a_full_manual_run_with_the_calling_admin_and_inherited_visibility(env):
    root_id = env.root("TV")
    env.run(root_id, user="owner")
    with db_module.SessionLocal() as db:
        from app.models import ImportRun
        db.query(ImportRun).update({"visibility": "private"})
        db.commit()
    result = env.automation.scan_now("admin-9", root_id)
    run_id = result["started"][0]["run_id"]
    with db_module.SessionLocal() as db:
        from app.models import ImportRun
        run = db.get(ImportRun, run_id)
        assert (run.trigger, run.scope, run.user_id, run.visibility) == ("manual", None, "admin-9", "private")
    assert env.driven == [run_id]


def test_root_entry_matches_snapshot_roots_item_and_is_none_for_unknown(env):
    root_id = imported(env, "TV", schedule="1h")
    env.tick()
    assert entry(env, root_id) == env.automation.snapshot()["roots"][0]
    assert env.automation.root_entry("nope") is None


def test_roots_changed_starts_a_baseline_when_watch_turns_on_and_off_keeps_rows(env):
    root_id = imported(env, "TV")
    env.tick()
    assert root_id not in env.automation._watches
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, root_id).watch_enabled = True
        db.add_all([LibraryWatchDir(root_id=root_id, path="", mtime_ns=4), LibraryWatchDir(root_id=root_id, path="Show", mtime_ns=5)])
        db.commit()
    env.automation.roots_changed()
    watch = env.automation._watches[root_id]
    assert watch.interval_s == 300 and watch.dirs == {"": 4, "Show": 5}  # fresh, complete rows are kept, not re-baselined
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, root_id).watch_interval_s = 900
        db.commit()
    env.automation.roots_changed()
    assert env.automation._watches[root_id] is watch and watch.interval_s == 900
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, root_id).watch_enabled = False
        db.commit()
    env.automation.roots_changed()
    assert root_id not in env.automation._watches
    with db_module.SessionLocal() as db:
        assert db.query(LibraryWatchDir).count() == 2
        db.get(StorageRoot, root_id).watch_enabled = True
        db.query(LibraryWatchDir).update({"updated_at": env.fake_clock.naive_utc - timedelta(days=8)})
        db.commit()
    env.automation.roots_changed()
    assert env.automation._watches[root_id].phase == "baseline"
    assert entry(env, root_id)["watch"] is True


def test_submit_runs_drive_on_the_manual_lane_tracked(env):
    seen = []
    env.automation._drive = lambda run_id: seen.append((run_id, run_id in env.automation._driving))
    env.automation.submit("r1")
    assert seen == [("r1", True)] and env.automation._driving == set()
    assert env.automation._manual.labels == ["r1"]


def test_facade_method_names_and_signatures_match_s3():
    expected = {"snapshot": [], "root_entry": ["root_id"], "roots_changed": [], "scan_now": ["admin_id", "root_id"],
                "submit": ["run_id"], "diagnostics": []}
    for name, params in expected.items():
        assert list(inspect.signature(getattr(LibraryAutomation, name)).parameters)[1:] == params
    assert inspect.signature(LibraryAutomation.scan_now).parameters["root_id"].default is None
