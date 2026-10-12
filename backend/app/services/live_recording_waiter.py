"""Durable, deterministic scheduled-waiter for an upcoming broadcast (issue #98).

An UPCOMING YouTube broadcast is recorded by first WAITING for it. This module
owns the waiting policy: wake near the broadcast's best-available provider start
time, re-inspect with BOUNDED retries, tolerate a rescheduled or transiently
failing source, and resolve exactly one honest bounded outcome — ready to record,
the source was cancelled, the source requires a sign-in Lumina cannot use, the
member cancelled/stopped, or the broadcast was delayed excessively.

The waiter is a collaborator the :class:`~app.services.live_recording_manager.LiveRecordingManager`
drives, exactly like the media recorder and chat capturer, so restart recovery
re-runs it against the same durable ``waiting`` row. Time is a CONTROLLABLE
injected clock and the between-probe pause is an injected wait, so the scheduling
is fully deterministic in tests with no real sleeps and no wall-clock coupling;
the real waiter delegates the pause to the shared stop/cancel control so a member
can interrupt the wait immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class WaitOutcome(Enum):
    """The one honest outcome the waiter resolves for a scheduled acquisition."""

    READY = "ready"
    SOURCE_CANCELLED = "source_cancelled"
    EXPIRED = "expired"
    AUTH_EXPIRED = "auth_expired"
    MEMBER_CANCELLED = "member_cancelled"
    MEMBER_STOPPED = "member_stopped"


@dataclass(frozen=True)
class WaitDecision:
    outcome: WaitOutcome
    # Only meaningful for READY: whether the now-live source offered from-start, so
    # the recorder never implies from-start was possible when it was not.
    from_start_supported: bool = False


@dataclass(frozen=True)
class SourceProbe:
    """One inspection of the waited source, normalized to a safe waiting signal.

    ``state`` is ``upcoming`` (still scheduled), ``live`` (connect now),
    ``cancelled`` (the broadcast was called off), ``ended`` (it already finished
    before Lumina could connect), ``auth_expired`` (the member's sign-in lapsed),
    or ``error`` (a transient inspection failure to retry). ``scheduled_start_epoch``
    carries an updated best-available start time so a reschedule is persisted.
    """

    state: str
    scheduled_start_epoch: float | None = None
    from_start_supported: bool = False


class WaitingSource(Protocol):
    """The waiting-phase view of a recording the waiter needs."""

    scheduled_start_epoch: float | None

    def stop_requested(self) -> bool: ...

    def cancel_requested(self) -> bool: ...

    def note_schedule_change(self, epoch: float) -> None: ...


class LiveSourceProbe(Protocol):
    def probe(self, ctx: WaitingSource) -> SourceProbe: ...


_TERMINAL_STATES = {"cancelled", "ended", "auth_expired"}


class ScheduledLiveWaiter:
    """Wait for an upcoming broadcast to become recordable, with bounded retries."""

    def __init__(
        self,
        *,
        probe: LiveSourceProbe,
        clock: Callable[[], float],
        wait: Callable[[WaitingSource, float], bool] | None = None,
        poll_interval_seconds: float = 15.0,
        pre_roll_seconds: float = 60.0,
        max_probe_interval_seconds: float = 120.0,
        grace_seconds: float = 3 * 60 * 60,
        max_probe_attempts: int = 5000,
    ) -> None:
        self._probe = probe
        self._clock = clock
        # The default pause waits on the member's stop/cancel control so a wait is
        # interrupted immediately; tests inject a wait that advances the fake clock.
        self._wait = wait or (lambda ctx, seconds: ctx.wait_for_end(seconds))  # type: ignore[attr-defined]
        self._poll_interval = max(0.0, poll_interval_seconds)
        self._pre_roll = max(0.0, pre_roll_seconds)
        self._max_probe_interval = max(self._poll_interval, max_probe_interval_seconds)
        self._grace = max(0.0, grace_seconds)
        self._max_probe_attempts = max(1, max_probe_attempts)

    def wait(self, ctx: WaitingSource) -> WaitDecision:
        probes = 0
        while True:
            # The member's durable intent wins immediately over any wait.
            if ctx.cancel_requested():
                return WaitDecision(WaitOutcome.MEMBER_CANCELLED)
            if ctx.stop_requested():
                return WaitDecision(WaitOutcome.MEMBER_STOPPED)

            now = self._clock()
            scheduled = ctx.scheduled_start_epoch
            # Excessive delay: past the (possibly rescheduled) start by more than the
            # grace bound and still not live -> give up honestly, never hang forever.
            if scheduled is not None and now - scheduled > self._grace:
                return WaitDecision(WaitOutcome.EXPIRED)
            if probes >= self._max_probe_attempts:
                return WaitDecision(WaitOutcome.EXPIRED)

            probes += 1
            result = self._probe.probe(ctx)
            if result.state == "live":
                return WaitDecision(WaitOutcome.READY, from_start_supported=result.from_start_supported)
            if result.state == "cancelled" or result.state == "ended":
                return WaitDecision(WaitOutcome.SOURCE_CANCELLED)
            if result.state == "auth_expired":
                return WaitDecision(WaitOutcome.AUTH_EXPIRED)

            # Still upcoming (or a transient error to retry). Persist a reschedule so
            # it survives a restart and rebases the excessive-delay bound.
            if result.scheduled_start_epoch is not None and result.scheduled_start_epoch != scheduled:
                ctx.note_schedule_change(result.scheduled_start_epoch)
                scheduled = result.scheduled_start_epoch

            if self._wait(ctx, self._next_delay(now, scheduled)):
                # Interrupted by the member's stop/cancel: loop re-checks it at the top.
                continue

    def _next_delay(self, now: float, scheduled: float | None) -> float:
        """Wait longer when the start is far off; tighten to the poll interval near it."""
        if scheduled is None:
            return self._poll_interval
        remaining = scheduled - now
        if remaining > self._pre_roll:
            return min(remaining - self._pre_roll, self._max_probe_interval)
        return self._poll_interval
