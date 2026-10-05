"""Deterministic scheduled-waiter for an upcoming broadcast (issue #98).

The waiter wakes near a broadcast's best-available provider start time and, with
BOUNDED retries, resolves one of a fixed set of honest outcomes. Every test uses
a CONTROLLABLE injected clock + an injected wait (which advances that clock) and a
scripted source probe, so there are no real sleeps and no wall-clock nondeterminism.
"""

from __future__ import annotations

from app.services.live_recording_waiter import (
    ScheduledLiveWaiter,
    SourceProbe,
    WaitOutcome,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _FakeWaitCtx:
    """Duck-typed waiting context: schedule + stop/cancel + persistence hook."""

    def __init__(self, scheduled_epoch: float | None, *, stop: bool = False, cancel: bool = False) -> None:
        self.scheduled_start_epoch = scheduled_epoch
        self._stop = stop
        self._cancel = cancel
        self.schedule_changes: list[float] = []

    def cancel_requested(self) -> bool:
        return self._cancel

    def stop_requested(self) -> bool:
        return self._stop

    def note_schedule_change(self, epoch: float) -> None:
        self.schedule_changes.append(epoch)
        self.scheduled_start_epoch = epoch


class _ScriptProbe:
    def __init__(self, results: list[SourceProbe]) -> None:
        self._results = results
        self.calls = 0

    def probe(self, ctx: object) -> SourceProbe:
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return result


def _waiter(clock: _FakeClock, probe: _ScriptProbe, **kwargs) -> ScheduledLiveWaiter:
    def wait(ctx: object, seconds: float) -> bool:
        clock.advance(seconds)
        return False

    defaults = dict(
        clock=clock,
        wait=wait,
        poll_interval_seconds=10.0,
        pre_roll_seconds=30.0,
        max_probe_interval_seconds=60.0,
        grace_seconds=3600.0,
        max_probe_attempts=1000,
    )
    defaults.update(kwargs)
    return ScheduledLiveWaiter(probe=probe, **defaults)


def test_ready_when_the_source_goes_live() -> None:
    clock = _FakeClock(start=1000.0)
    probe = _ScriptProbe(
        [
            SourceProbe(state="upcoming", scheduled_start_epoch=1000.0),
            SourceProbe(state="upcoming", scheduled_start_epoch=1000.0),
            SourceProbe(state="live", from_start_supported=True),
        ]
    )
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(1000.0))
    assert decision.outcome is WaitOutcome.READY
    assert decision.from_start_supported is True


def test_ready_carries_from_start_unsupported() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="live", from_start_supported=False)])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t))
    assert decision.outcome is WaitOutcome.READY
    assert decision.from_start_supported is False


def test_source_cancelled_is_a_distinct_outcome() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="cancelled")])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t))
    assert decision.outcome is WaitOutcome.SOURCE_CANCELLED


def test_already_ended_before_connect_is_source_cancelled() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="ended")])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t))
    assert decision.outcome is WaitOutcome.SOURCE_CANCELLED


def test_auth_expiry_is_a_distinct_outcome() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="auth_expired")])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t))
    assert decision.outcome is WaitOutcome.AUTH_EXPIRED


def test_excessive_delay_expires_after_the_grace_bound() -> None:
    clock = _FakeClock(start=1000.0)
    # The broadcast is perpetually "upcoming" and never starts.
    probe = _ScriptProbe([SourceProbe(state="upcoming", scheduled_start_epoch=1000.0)])
    decision = _waiter(clock, probe, grace_seconds=120.0, poll_interval_seconds=30.0).wait(
        _FakeWaitCtx(1000.0)
    )
    assert decision.outcome is WaitOutcome.EXPIRED
    # It gave up only after passing the scheduled time by more than the grace bound.
    assert clock.t - 1000.0 > 120.0


def test_bounded_probe_attempts_expire_even_without_a_schedule() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="upcoming", scheduled_start_epoch=None)])
    decision = _waiter(clock, probe, max_probe_attempts=5).wait(_FakeWaitCtx(None))
    assert decision.outcome is WaitOutcome.EXPIRED
    assert probe.calls == 5


def test_member_cancel_during_wait_is_reported() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="upcoming", scheduled_start_epoch=clock.t)])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t, cancel=True))
    assert decision.outcome is WaitOutcome.MEMBER_CANCELLED
    assert probe.calls == 0  # never probed; the member cancelled before waiting


def test_member_stop_during_wait_is_reported() -> None:
    clock = _FakeClock()
    probe = _ScriptProbe([SourceProbe(state="upcoming", scheduled_start_epoch=clock.t)])
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t, stop=True))
    assert decision.outcome is WaitOutcome.MEMBER_STOPPED


def test_schedule_change_is_persisted_and_rebases_the_grace_bound() -> None:
    clock = _FakeClock(start=1000.0)
    # First the broadcast is at 1000 but gets pushed to 1000 + 5000; then it goes
    # live. The push must be persisted AND must extend the excessive-delay bound so
    # a legitimately rescheduled broadcast is not wrongly expired.
    probe = _ScriptProbe(
        [
            SourceProbe(state="upcoming", scheduled_start_epoch=6000.0),
            SourceProbe(state="live", from_start_supported=True),
        ]
    )
    ctx = _FakeWaitCtx(1000.0)
    decision = _waiter(clock, probe, grace_seconds=120.0).wait(ctx)
    assert ctx.schedule_changes == [6000.0]
    assert decision.outcome is WaitOutcome.READY


def test_transient_probe_error_is_retried_then_bounded() -> None:
    clock = _FakeClock()
    # A run of transient errors, then live: the waiter tolerates them and proceeds.
    probe = _ScriptProbe(
        [
            SourceProbe(state="error"),
            SourceProbe(state="error"),
            SourceProbe(state="live"),
        ]
    )
    decision = _waiter(clock, probe).wait(_FakeWaitCtx(clock.t))
    assert decision.outcome is WaitOutcome.READY
