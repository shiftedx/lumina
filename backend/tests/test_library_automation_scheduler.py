from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.library_automation import RootView, RunView, due_order, is_due, local, next_scan_at, skip_reason


@pytest.fixture(autouse=True)
def tz():
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def at(day, hm, sec=0):  # naive UTC, as stored
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m, sec)


def run(created, finished=None, state="completed", rid="r"):
    return RunView(rid, "manual", state, None, created, finished, finished or created, "u", "household", None, {})


def root(schedule="nightly", last=None, newest="same", active=None, label="a", rid="a"):
    newest = last if newest == "same" else newest
    return RootView(rid, label, "/x", None, "external", True, schedule, False, 300, newest, last, active)


@pytest.mark.parametrize("schedule,created,finished,night,expect", [
    ("off", at(1, "10:00"), at(1, "10:20"), 3, None),
    ("15m", at(1, "10:00"), at(1, "10:20"), 3, at(1, "10:35")),
    ("1h", at(1, "10:00"), at(1, "10:20"), 3, at(1, "11:20")),
    ("6h", at(1, "10:00"), at(1, "10:20"), 3, at(1, "16:20")),
    ("nightly", at(1, "02:50"), at(1, "02:55"), 3, at(1, "03:00")),
    ("nightly", at(1, "03:00", 30), at(1, "03:20"), 3, at(2, "03:00")),
    ("nightly", at(1, "23:30"), at(1, "23:50"), 0, at(2, "00:00")),
    ("nightly", at(1, "05:00"), at(1, "05:10"), 23, at(1, "23:00")),
])
def test_next_scan_at(schedule, created, finished, night, expect):
    got = next_scan_at(schedule, night, run(created, finished))
    assert (got.astimezone(UTC).replace(tzinfo=None) if got else None) == expect


@pytest.mark.parametrize("state", ["running", "cancel_requested", "needs_confirmation"])
def test_never_due_while_active_or_held_or_never_imported(state):
    assert next_scan_at("15m", 3, run(at(1, "10:00"), state=state)) is None
    assert next_scan_at("15m", 3, None) is None


def test_nightly_dst_spring_forward_and_fall_back(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    # spring forward 2026-03-08: last run 01:30 local (06:30 UTC), night hour 2 does not exist
    last = run(datetime(2026, 3, 8, 6, 30), datetime(2026, 3, 8, 6, 40))
    due = next_scan_at("nightly", 2, last)
    assert due is not None and due.astimezone(UTC).replace(tzinfo=None) == datetime(2026, 3, 8, 7, 0)
    assert due - local(last.created_at) < timedelta(hours=1)
    # fall back 2026-11-01: last run 00:30 local, 01:00 repeats; due once at first 01:00
    last = run(datetime(2026, 11, 1, 4, 30), datetime(2026, 11, 1, 4, 40))
    due = next_scan_at("nightly", 1, last)
    assert due.astimezone(UTC).replace(tzinfo=None) == datetime(2026, 11, 1, 5, 0)
    # a run during the repeated hour (second 01:30, 06:30 UTC) is not followed by another 01:00 that day
    last = run(datetime(2026, 11, 1, 6, 30), datetime(2026, 11, 1, 6, 40))
    assert next_scan_at("nightly", 1, last).astimezone(UTC).replace(tzinfo=None) == datetime(2026, 11, 2, 6, 0)  # 01:00 EST


@pytest.mark.parametrize("state", ["failed", "partial", "cancelled"])
def test_failed_partial_cancelled_count_as_ended(state):
    due = next_scan_at("1h", 3, run(at(1, "10:00"), at(1, "10:05"), state=state))
    assert due.astimezone(UTC).replace(tzinfo=None) == at(1, "11:05")


def test_three_day_outage_is_one_due_root():
    r = root("15m", run(at(1, "10:00"), at(1, "10:20")))
    now = local(at(4, "10:00"))
    assert [x.id for x in due_order([r], 3, now)] == ["a"]


def test_a_manual_full_run_satisfies_the_schedule():
    r = root("nightly", run(at(1, "02:50"), at(1, "02:55")))
    assert not is_due(r, 3, local(at(1, "02:59")))
    assert is_due(r, 3, local(at(1, "03:00")))
    r2 = root("nightly", run(at(1, "03:05"), at(1, "03:20")))  # manual full run just after the slot
    assert not is_due(r2, 3, local(at(1, "23:00")))


def test_due_order_oldest_first():
    a = root("15m", run(at(1, "10:30"), at(1, "10:40")), label="a", rid="a")
    b = root("15m", run(at(1, "10:00"), at(1, "10:10")), label="b", rid="b")
    assert [x.id for x in due_order([a, b], 3, local(at(2, "00:00")))] == ["b", "a"]


def test_skip_reason_order():
    done = run(at(1, "10:00"), at(1, "10:10"))
    held = run(at(1, "10:00"), state="needs_confirmation")
    act = run(at(1, "10:00"), state="running")
    assert skip_reason(root(newest=None), probe_alive=True, online=False) == "needs_first_import"
    assert skip_reason(root(last=held), probe_alive=True, online=False) == "waiting_confirmation"
    assert skip_reason(root(last=done, active=act), probe_alive=True, online=False) == "scanning"
    assert skip_reason(root(last=done), probe_alive=True, online=False) == "unresponsive"
    assert skip_reason(root(last=done), probe_alive=False, online=False) == "offline"
    assert skip_reason(root(last=done), probe_alive=False, online=True) is None
    assert skip_reason(root(last=done), probe_alive=False, online=None) is None
