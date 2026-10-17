"""App activity preempts optional media children before request handlers do work."""
from datetime import datetime
import signal

import pytest
from pydantic import ValidationError

from app.config import EnvSettings, settings
from app.services import background_work as module
from app.services.background_work import BackgroundWork


class Child:
    def __init__(self):
        self.signals = []
        self.finished = False

    def poll(self):
        return 0 if self.finished else None

    def send_signal(self, sig):
        self.signals.append(sig)


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    return now


def hour(monkeypatch, value):
    class Clock:
        @staticmethod
        def now():
            return datetime(2026, 1, 1, value)
    monkeypatch.setattr(module, "datetime", Clock)


def test_activity_stops_children_synchronously_and_holds_until_request_finishes(clock):
    gate, child = BackgroundWork(), Child()
    gate.register(child)
    scope = {"type": "http"}
    gate.begin_request(scope)
    gate.begin_request(scope)  # repeated auth dependencies register this request only once
    assert child.signals == [signal.SIGSTOP]
    clock[0] += 1000
    assert gate.sync(child)
    gate.end_request(scope)
    gate.end_request(scope)
    assert gate.busy()
    clock[0] += 30
    assert not gate.sync(child)
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert not gate.busy()


def test_overlapping_requests_and_new_child_remain_paused(clock):
    gate, child = BackgroundWork(), Child()
    first, second = {"type": "http"}, {"type": "http"}
    gate.begin_request(first)
    gate.begin_request(second)
    gate.register(child)
    assert child.signals == [signal.SIGSTOP]
    gate.end_request(first)
    clock[0] += 100
    assert gate.sync(child)
    gate.end_request(second)
    clock[0] += 30
    assert not gate.sync(child)


@pytest.mark.parametrize(("start", "end", "current", "allowed"), [
    (None, None, 3, False), (2, 4, 1, False), (2, 4, 2, True),
    (2, 4, 4, False), (22, 3, 23, True), (22, 3, 2, True), (22, 3, 3, False),
])
def test_daily_window_boundaries_and_opt_in(monkeypatch, start, end, current, allowed):
    monkeypatch.setattr(settings, "background_backfill_start_hour", start)
    monkeypatch.setattr(settings, "background_backfill_end_hour", end)
    hour(monkeypatch, current)
    assert BackgroundWork().backfill_allowed() is allowed


def test_window_closure_pauses_running_backfill_but_demanded_work_can_resume(monkeypatch, clock):
    gate, child = BackgroundWork(), Child()
    monkeypatch.setattr(settings, "background_backfill_start_hour", 2)
    monkeypatch.setattr(settings, "background_backfill_end_hour", 4)
    hour(monkeypatch, 2)
    gate.register(child, backfill=True)
    assert not child.signals
    hour(monkeypatch, 4)
    assert gate.sync(child, backfill=True)
    assert child.signals == [signal.SIGSTOP]
    demanded = Child()
    gate.register(demanded)
    assert not demanded.signals
    gate.touch()
    assert demanded.signals == [signal.SIGSTOP]
    assert not gate.backfill_allowed()


def test_unregistered_or_finished_children_receive_no_activity_signal(clock):
    gate, removed, finished = BackgroundWork(), Child(), Child()
    gate.register(removed)
    gate.unregister(removed)
    gate.register(finished)
    finished.finished = True
    gate.touch()
    assert not removed.signals and not finished.signals


def test_background_children_share_one_slot_and_waiters_resume_after_release(clock):
    gate, first, second = BackgroundWork(), Child(), Child()
    gate.register(first)
    gate.register(second)
    assert not first.signals
    assert second.signals == [signal.SIGSTOP]
    assert gate.sync(second)
    gate.unregister(first)
    assert not gate.sync(second)
    assert second.signals == [signal.SIGSTOP, signal.SIGCONT]


@pytest.mark.parametrize("values", [
    {"background_backfill_start_hour": 2},
    {"background_backfill_start_hour": 2, "background_backfill_end_hour": 2},
    {"background_backfill_start_hour": -1, "background_backfill_end_hour": 3},
    {"background_backfill_start_hour": 2, "background_backfill_end_hour": 24},
])
def test_invalid_or_incomplete_window_is_rejected(values):
    with pytest.raises(ValidationError):
        EnvSettings(_env_file=None, **values)


def test_window_reopening_stops_current_child_before_resuming_older_waiter(monkeypatch):
    sequence = []

    class OrderedChild(Child):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def send_signal(self, sig):
            super().send_signal(sig)
            sequence.append((self.name, sig))

    gate = BackgroundWork()
    automatic, demanded = OrderedChild("automatic"), OrderedChild("demanded")
    monkeypatch.setattr(settings, "background_backfill_start_hour", 2)
    monkeypatch.setattr(settings, "background_backfill_end_hour", 4)
    hour(monkeypatch, 1)
    gate.register(automatic, backfill=True)
    gate.register(demanded)
    assert sequence == [("automatic", signal.SIGSTOP)]
    hour(monkeypatch, 2)
    assert not gate.sync(automatic, backfill=True)
    assert sequence[-2:] == [("demanded", signal.SIGSTOP), ("automatic", signal.SIGCONT)]


def test_activity_stops_and_resumes_a_real_media_child(tmp_path, clock):
    import subprocess
    import sys
    import time

    progress = tmp_path / "progress"
    child = subprocess.Popen([
        sys.executable, "-c",
        "import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n with p.open('ab') as f: f.write(b'x')\n time.sleep(.01)",
        str(progress),
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gate = BackgroundWork()
    try:
        deadline = time.perf_counter() + 3
        while not progress.exists() and time.perf_counter() < deadline:
            time.sleep(.01)
        assert progress.exists()
        gate.register(child)
        scope = {"type": "http"}
        gate.begin_request(scope)
        time.sleep(.1)  # allow the kernel to deliver SIGSTOP
        stopped_size = progress.stat().st_size
        time.sleep(.1)
        assert progress.stat().st_size == stopped_size
        gate.end_request(scope)
        clock[0] += 30
        assert not gate.sync(child)
        deadline = time.perf_counter() + 3
        while progress.stat().st_size == stopped_size and time.perf_counter() < deadline:
            time.sleep(.01)
        assert progress.stat().st_size > stopped_size
    finally:
        gate.unregister(child)
        child.send_signal(signal.SIGCONT)
        child.kill()
        child.wait()


@pytest.mark.parametrize("promoted", [False, True])
def test_close_reaps_a_stopped_child_and_rejects_late_admission(clock, promoted):
    import subprocess
    import sys

    from app.services.background_work import BackgroundWorkClosed

    gate = BackgroundWork()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    late = None
    try:
        gate.register(child)
        gate.touch()
        if promoted:
            gate.promote(child)
        gate.close()
        assert gate.closed and gate.busy()
        assert child.poll() is not None
        assert not gate._processes
        late = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with pytest.raises(BackgroundWorkClosed):
            gate.register(late)
        assert late.poll() is not None
        gate.start()
        clock[0] += 30
        assert not gate.busy()
    finally:
        for process in (child, late):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGCONT)
                process.kill()
                process.wait()


def test_promoted_child_bypasses_activity_and_blocks_optional_work_until_done(clock):
    gate, first, requested = BackgroundWork(), Child(), Child()
    gate.register(first)
    gate.register(requested)
    assert requested.signals == [signal.SIGSTOP]
    gate.promote(requested)
    assert first.signals == [signal.SIGSTOP]
    assert requested.signals == [signal.SIGSTOP, signal.SIGCONT]
    gate.touch()
    assert requested.signals == [signal.SIGSTOP, signal.SIGCONT]
    clock[0] += 30
    assert gate.busy()
    assert gate.sync(first)
    assert not gate.sync(requested)
    assert requested in gate._processes  # promotion retains deterministic shutdown ownership
    gate.unregister(requested)
    assert not gate.busy()
    assert not gate.sync(first)


def test_shutdown_reaps_other_children_even_if_one_does_not_exit():
    import subprocess

    class Reaper(Child):
        def __init__(self, stuck):
            super().__init__()
            self.stuck = stuck

        def terminate(self):
            self.finished = not self.stuck

        def kill(self):
            self.finished = not self.stuck

        def wait(self, timeout=None):
            if self.finished:
                return 0
            raise subprocess.TimeoutExpired(["synthetic-child"], timeout)

    gate, stuck, healthy = BackgroundWork(), Reaper(True), Reaper(False)
    gate.register(stuck)
    gate.register(healthy)
    with pytest.raises(RuntimeError, match="Background media child did not stop"):
        gate.close()
    assert healthy.finished and healthy not in gate._processes
    assert stuck in gate._processes
