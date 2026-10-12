"""Coordinated execution + restart recovery for live recordings (issue #97).

The :class:`LiveRecordingService` owns the durable transitions and the honesty
rule; this manager owns the concurrency. For one recording it runs the media
recording and the forward-only chat capture as concurrent sibling outputs,
watching a shared stop/cancel control so a deliberate stop finalizes usable
outputs while an abrupt cancel discards in-flight work. The two siblings fail
independently: a media failure never stops chat and a chat failure never stops
media. On startup :meth:`recover` re-launches or finalizes every recording left
non-terminal by a process restart, so a long-running recording survives a crash.

The media recorder and the chat capturer are injected collaborators
(:class:`LiveMediaRecorder` / :class:`LiveChatCapturer`), so the coordination is
tested against fakes and the real guarded-transport adapters plug in unchanged.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSettings, LiveRecording, User
from app.persistence import write_transaction
from app.services.acquisition_batch import RedactedFailure
from app.services.live_recording import (
    DEFAULT_MAX_WAITING_SECONDS,
    DEFAULT_SCHEDULE_GRACE_SECONDS,
    NON_TERMINAL_STATUSES,
    LiveRecordingService,
    RecoveryDecision,
    resolve_history,
)
from app.services.live_recording_waiter import WaitDecision, WaitOutcome


@dataclass(frozen=True)
class LiveRecordingSubmission:
    """Session-independent result of :meth:`LiveRecordingManager.submit`.

    ``created`` is ``True`` only when a new active recording was launched; a
    duplicate submission returns the existing recording id with ``created=False``
    and never starts a second worker.

    ``recording`` is the detached row as of the moment of submission (read from a
    session with ``expire_on_commit=False``, so its already-loaded scalar columns
    stay valid after the session closes). A caller building a "just submitted"
    response must serialize from THIS snapshot, never a fresh re-read: ``submit``
    launches the worker before returning, and a fresh read can race a worker that
    finishes before the caller's own read lands, reporting a later status than
    what was actually just created.
    """

    recording_id: str
    created: bool
    status: str
    recording: LiveRecording


@dataclass(frozen=True)
class LiveMediaResult:
    """The terminal outcome of one media recording run.

    ``status`` is ``completed`` (a source-end capture) or ``partial`` (a usable but
    incomplete capture) or ``failed``. A deliberate stop finalizes a usable partial
    recording; an abrupt cancel discards it and reports ``failed``.

    The from-start provenance (issue #98) rides along so the durable row records
    the honest answer to "did capture begin at the beginning or the live edge?":
    ``capture_origin`` (``source_beginning``/``live_edge``, or ``None`` when nothing
    was captured), ``media_history_complete`` (whether the from-start beginning had
    no gap), ``from_start_supported`` (what the source actually offered), and
    ``awaiting_fallback_choice`` (a from-start-unavailable run that refused to
    silently fall back per the member's saved intent).
    """

    status: str
    library_item_id: str | None = None
    failure: RedactedFailure | None = None
    capture_origin: str | None = None
    media_history_complete: bool = False
    from_start_supported: bool | None = None
    awaiting_fallback_choice: bool = False
    # Why capture ended: source_ended | stopped | time_limit | size_limit |
    # disk_low | owner_disabled | shutdown | interrupted. None when nothing ran.
    end_reason: str | None = None


@dataclass(frozen=True)
class LiveChatResult:
    """The terminal outcome of one forward-only chat capture run.

    ``status`` is ``completed`` (a timed chat asset with events was published),
    ``unavailable`` (the source carried no chat to capture), or ``failed``.
    ``covered_from_start`` (issue #98) reports whether the chat reached back to the
    media beginning; when a from-start media capture's chat does not, the missing
    earlier chat is an explicit partial-history condition, never dropped silently.
    """

    status: str
    chat_asset_id: str | None = None
    failure: RedactedFailure | None = None
    covered_from_start: bool = False


@dataclass
class RecordingContext:
    """Everything a recorder or capturer needs, plus the shared stop/cancel control.

    ``offset_base`` is the media start; the chat capturer bases its media offsets
    on it so a completed Library item and its timed chat asset stay synchronized.
    ``resume`` is ``True`` when this run recovers an interrupted recording after a
    restart, so the collaborator continues forward from the current edge.

    The from-start + scheduling fields (issue #98) let the recorder honor the
    member INTENT (``start_intent`` / ``fallback_policy``) and let the waiter read
    and durably update the ``scheduled_start_at`` for an upcoming broadcast.
    ``from_start_supported`` is filled once the source has been inspected at connect.
    """

    recording_id: str
    user_id: str
    source_url: str
    source_identity: str
    format_selection: dict
    output_profile: dict
    offset_base: datetime | None
    resume: bool
    session_factory: Callable[[], Session]
    _stop: threading.Event
    _cancel: threading.Event
    _either: threading.Event
    start_intent: str = "live_edge"
    fallback_policy: str = "allow_live_edge"
    scheduled_start_at: datetime | None = None
    from_start_supported: bool | None = None

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def wait_for_end(self, timeout: float | None = None) -> bool:
        """Block until the member asks to stop or cancel (or the timeout)."""
        return self._either.wait(timeout)

    # -- waiting-phase view (issue #98) -------------------------------------

    @property
    def scheduled_start_epoch(self) -> float | None:
        if self.scheduled_start_at is None:
            return None
        return self.scheduled_start_at.replace(tzinfo=UTC).timestamp()

    def note_schedule_change(self, epoch: float) -> None:
        """Durably record a rescheduled provider start time during the wait."""
        new_start = datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None)
        self.scheduled_start_at = new_start
        with self.session_factory() as db:
            LiveRecordingService(db).record_schedule_change(self.recording_id, new_start)


class LiveMediaRecorder(Protocol):
    def record(self, ctx: RecordingContext) -> LiveMediaResult: ...

    # Optional: publish bytes a crashed run left behind as a partial item.
    # def salvage(self, ctx: RecordingContext) -> LiveMediaResult | None: ...


class LiveChatCapturer(Protocol):
    def capture(self, ctx: RecordingContext) -> LiveChatResult: ...


class LiveWaiter(Protocol):
    """Waits for an upcoming broadcast to become recordable (issue #98)."""

    def wait(self, ctx: RecordingContext) -> WaitDecision: ...


@dataclass
class _Control:
    stop: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    either: threading.Event = field(default_factory=threading.Event)
    # Who asked for the stop when it was not the member: time_limit,
    # disk_low, owner_disabled or shutdown. None means the member stopped it.
    reason: str | None = None

    def request_stop(self, reason: str | None = None) -> None:
        if reason is not None and not self.stop.is_set():
            self.reason = reason
        self.stop.set()
        self.either.set()


class LiveRecordingManager:
    """Owns the worker threads, the shared controls, and restart recovery."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        recorder: LiveMediaRecorder,
        chat_capturer: LiveChatCapturer,
        waiter: LiveWaiter | None = None,
        events: Any | None = None,
        max_active_per_user: int = 2,
        max_active_global: int = 6,
        max_recovery_attempts: int = 3,
        max_runtime_seconds: float | None = None,
        schedule_grace_seconds: float = DEFAULT_SCHEDULE_GRACE_SECONDS,
        max_waiting_seconds: float = DEFAULT_MAX_WAITING_SECONDS,
        clock: Callable[[], datetime] | None = None,
        disk_path: str | None = None,
        resource_check_seconds: float = 15.0,
    ) -> None:
        self.session_factory = session_factory
        self.recorder = recorder
        self.chat_capturer = chat_capturer
        # The scheduled-waiter is only needed for upcoming broadcasts (issue #98).
        # A manager built without one still runs #97 record-from-now recordings; a
        # ``waiting`` recording with no waiter fails closed rather than hanging.
        self.waiter = waiter
        self.events = events
        self.max_active_per_user = max_active_per_user
        self.max_active_global = max_active_global
        self.max_recovery_attempts = max_recovery_attempts
        # Restart-durable waiting-phase bounds (issue #98) threaded into the service
        # so plan_recovery can terminate a wait that outlived its durable deadline.
        self.schedule_grace_seconds = schedule_grace_seconds
        self.max_waiting_seconds = max_waiting_seconds
        # A controllable clock for the recovery deadline decision (deterministic in
        # tests); None lets the service use its wall clock.
        self._clock = clock
        # A per-recording runtime ceiling that requests a deliberate stop when
        # reached, so BOTH sibling outputs wind down and the recording finalizes
        # as a usable partial rather than running unbounded.
        self.max_runtime_seconds = max_runtime_seconds
        # While capturing, the monitor also re-checks that the owner is still
        # active and that the recording volume keeps the admin's free-space floor.
        self.disk_path = disk_path
        self.resource_check_seconds = resource_check_seconds
        self._controls: dict[str, _Control] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def _service(self, db: Session) -> LiveRecordingService:
        return LiveRecordingService(
            db,
            max_active_per_user=self.max_active_per_user,
            max_active_global=self.max_active_global,
            max_recovery_attempts=self.max_recovery_attempts,
            schedule_grace_seconds=self.schedule_grace_seconds,
            max_waiting_seconds=self.max_waiting_seconds,
            clock=self._clock,
        )

    # -- submit -------------------------------------------------------------

    def submit(
        self,
        *,
        user_id: str,
        source_url: str,
        source_identity: str,
        extractor: str | None = None,
        remote_id: str | None = None,
        title: str | None = None,
        format_selection: dict | None = None,
        output_profile: dict | None = None,
        await_schedule: bool = False,
        scheduled_start_at: datetime | None = None,
        start_intent: str = "live_edge",
        fallback_policy: str = "allow_live_edge",
        from_start_supported: bool | None = None,
    ) -> LiveRecordingSubmission:
        """Member-scoped idempotent create; launches at most one worker.

        A ``scheduled_start_at`` starts the recording in the durable ``waiting``
        phase (issue #98); ``start_intent`` / ``fallback_policy`` carry the
        from-start member intent. A duplicate submission returns the existing
        recording and never launches a second waiter or worker.
        """

        with self.session_factory() as db:
            service = self._service(db)
            user = db.get(User, user_id)
            if user is None:
                raise LookupError(user_id)
            claim = service.begin(
                user=user,
                source_url=source_url,
                source_identity=source_identity,
                extractor=extractor,
                remote_id=remote_id,
                title=title,
                format_selection=format_selection,
                output_profile=output_profile,
                await_schedule=await_schedule,
                scheduled_start_at=scheduled_start_at,
                start_intent=start_intent,
                fallback_policy=fallback_policy,
                from_start_supported=from_start_supported,
            )
            submission = LiveRecordingSubmission(
                recording_id=claim.recording.id,
                created=claim.created,
                status=claim.recording.status,
                recording=claim.recording,
            )
        if submission.created:
            self._launch(submission.recording_id, resume=False)
        return submission

    # -- member intent ------------------------------------------------------

    def stop(self, user_id: str, recording_id: str, *, reason: str | None = None) -> None:
        with self.session_factory() as db:
            service = self._service(db)
            service.request_stop(db.get(User, user_id), recording_id)
        control = self._controls.get(recording_id)
        if control is not None:
            control.request_stop(reason)

    def cancel(self, user_id: str, recording_id: str) -> None:
        with self.session_factory() as db:
            service = self._service(db)
            service.request_cancel(db.get(User, user_id), recording_id)
        control = self._controls.get(recording_id)
        if control is not None:
            control.cancel.set()
            control.either.set()

    # -- restart recovery ---------------------------------------------------

    def recover(self) -> None:
        """Re-launch or finalize every recording left non-terminal by a restart."""

        with self.session_factory() as db:
            non_terminal = list(
                db.scalars(
                    select(LiveRecording).where(LiveRecording.status.in_(tuple(NON_TERMINAL_STATUSES)))
                )
            )
            service = self._service(db)
            plans = [(recording.id, service.plan_recovery(recording)) for recording in non_terminal]
        for recording_id, decision in plans:
            if decision is RecoveryDecision.RESUME:
                self._launch(recording_id, resume=True)
            elif decision is RecoveryDecision.FINALIZE:
                # Off the startup path: salvaging a crashed capture remuxes media.
                self._spawn(recording_id, self._finalize_interrupted, recording_id)

    def _finalize_interrupted(self, recording_id: str) -> None:
        with self.session_factory() as db:
            existing = self._service(db).get_by_id(recording_id)
        if existing is None:
            return
        # A waiting recording that outlived its durable waiting deadline without a
        # member stop/cancel never captured anything: it ends as an honest failed
        # schedule with the same excessive-delay reason the waiter uses for a
        # broadcast that never went live in time, not a generic finalize. (Waiting
        # re-entries no longer accrue capture attempts (I1), so the durable
        # deadline in plan_recovery is what routes a stale wait here.)
        stale_wait = existing.status == "waiting" and not existing.stop_requested and not existing.cancel_requested
        # Salvage runs outside any session: it remuxes and publishes in its own.
        salvaged = None if stale_wait or existing.library_item_id is not None else self._salvage(existing)
        with self.session_factory() as db:
            service = self._service(db)
            if stale_wait:
                recording = service.fail_scheduled(recording_id, reason="excessive_delay")
            else:
                with write_transaction(db, name="live_recording_finalize_interrupted"):
                    service.enter_finalizing(recording_id)
                    if salvaged is not None:
                        service.set_media_status(
                            recording_id, salvaged.status, library_item_id=salvaged.library_item_id,
                            end_reason=salvaged.end_reason,
                        )
                recording = service.finalize(recording_id)
        if recording is not None:
            self._publish(recording)

    def _salvage(self, recording: LiveRecording) -> LiveMediaResult | None:
        """Publish (or, on cancel, discard) bytes a crashed run left on disk."""
        salvage = getattr(self.recorder, "salvage", None)
        if salvage is None:
            return None
        control = _Control()
        if recording.cancel_requested:
            control.cancel.set()
        try:
            return salvage(self._context(recording, control, resume=True))
        except Exception:  # noqa: BLE001 - salvage is best-effort; finalize stays honest
            return None

    # -- worker -------------------------------------------------------------

    def _launch(self, recording_id: str, *, resume: bool) -> None:
        with self._lock:
            if recording_id in self._running:
                return
            self._running.add(recording_id)
            control = _Control()
            self._controls[recording_id] = control
        self._spawn(recording_id, self._run, recording_id, resume, control)

    def _spawn(self, recording_id: str, target: Callable[..., None], *args: Any) -> None:
        worker = threading.Thread(target=target, args=args, name=f"live-rec-{recording_id[:8]}", daemon=True)
        with self._lock:
            self._workers[recording_id] = worker
        worker.start()

    def _monitor(self, user_id: str, control: _Control) -> None:
        """Stop a capture at its runtime ceiling, or when its owner or disk gives out.

        Armed only once capture starts, so a long scheduled wait never eats the
        runtime budget. Exits when the run ends (``either`` is set in ``_run``).
        """
        deadline = time.monotonic() + self.max_runtime_seconds if self.max_runtime_seconds else None
        while True:
            wait = self.resource_check_seconds
            if deadline is not None:
                wait = max(0.0, min(wait, deadline - time.monotonic()))
            if control.either.wait(wait):
                return
            if deadline is not None and time.monotonic() >= deadline:
                control.request_stop("time_limit")
                return
            reason = self._resource_stop_reason(user_id)
            if reason is not None:
                control.request_stop(reason)
                return

    def _resource_stop_reason(self, user_id: str) -> str | None:
        from app.services.job_manager import _free_bytes

        try:
            with self.session_factory() as db:
                user = db.get(User, user_id)
                if user is None or not user.is_active:
                    return "owner_disabled"
                app_settings = db.get(AppSettings, 1)
                floor = (app_settings.min_free_disk_mb if app_settings else 0) * 1024 * 1024
        except Exception:  # noqa: BLE001 - a transient DB hiccup never kills a capture
            return None
        if self.disk_path and floor:
            free = _free_bytes(self.disk_path)
            if free is not None and free < floor:
                return "disk_low"
        return None

    def _context(self, recording: LiveRecording, control: _Control, *, resume: bool) -> RecordingContext:
        return RecordingContext(
            recording_id=recording.id,
            user_id=recording.user_id,
            source_url=recording.source_url,
            source_identity=recording.source_identity,
            format_selection=dict(recording.format_selection or {}),
            output_profile=dict(recording.output_profile or {}),
            offset_base=recording.recording_started_at,
            resume=resume,
            session_factory=self.session_factory,
            _stop=control.stop,
            _cancel=control.cancel,
            _either=control.either,
            start_intent=recording.start_intent,
            fallback_policy=recording.fallback_policy,
            scheduled_start_at=recording.scheduled_start_at,
            from_start_supported=recording.from_start_supported,
        )

    def _run(self, recording_id: str, resume: bool, control: _Control) -> None:
        try:
            with self.session_factory() as db:
                service = self._service(db)
                recording = service.claim_for_run(recording_id)
                if recording is None:
                    return
                # If the member already asked to stop/cancel before this run
                # started, prime the shared control so the collaborators see it.
                # A deactivated owner's recording (e.g. resumed after a restart) stops too.
                owner = db.get(User, recording.user_id)
                if owner is not None and not owner.is_active:
                    control.request_stop("owner_disabled")
                if recording.stop_requested:
                    control.request_stop()
                if recording.cancel_requested:
                    control.cancel.set()
                    control.either.set()
                was_waiting = recording.status == "waiting"
                ctx = self._context(recording, control, resume=resume)
            # Scheduling phase (issue #98): wait for an upcoming broadcast to become
            # recordable before any capture starts. A terminal wait outcome (source
            # cancelled, excessive delay, auth expiry, member cancel/stop) is
            # committed inside and the worker exits without a false capture.
            if was_waiting and not self._await_scheduled_source(recording_id, ctx):
                return
            with self.session_factory() as db:
                service = self._service(db)
                service.mark_recording_started(recording_id)
                refreshed = service.get_by_id(recording_id)
                if refreshed is not None:
                    ctx.offset_base = refreshed.recording_started_at
            threading.Thread(
                target=self._monitor, args=(ctx.user_id, control), name=f"live-mon-{recording_id[:8]}", daemon=True
            ).start()
            media_result: dict[str, LiveMediaResult] = {}
            chat_result: dict[str, LiveChatResult] = {}
            chat_thread = threading.Thread(
                target=self._capture_chat, args=(ctx, chat_result), name=f"live-chat-{recording_id[:8]}", daemon=True
            )
            chat_thread.start()
            media_result["r"] = self._record_media(ctx)
            # Media ended on its own (source end, size limit, interruption): wind
            # chat down too instead of letting it poll a finished recording.
            control.stop.set()
            control.either.set()
            chat_thread.join()
            self._commit_outcome(recording_id, media_result["r"], chat_result.get("r"), stop_reason=control.reason)
        finally:
            control.either.set()  # releases the monitor
            with self._lock:
                self._running.discard(recording_id)

    def _await_scheduled_source(self, recording_id: str, ctx: RecordingContext) -> bool:
        """Run the waiter for an upcoming broadcast; True to proceed to capture.

        Every non-ready outcome is a deterministic bounded terminal state committed
        durably here, so restart recovery of a ``waiting`` row re-runs this exact
        decision. A missing waiter is a misconfiguration that fails closed rather
        than launching an unbounded wait.
        """

        if self.waiter is None:
            self._commit_scheduled_failure(recording_id, reason="excessive_delay")
            return False
        decision = self.waiter.wait(ctx)
        if decision.outcome is WaitOutcome.READY:
            with self.session_factory() as db:
                service = self._service(db)
                service.mark_connecting(recording_id, from_start_supported=decision.from_start_supported)
            ctx.from_start_supported = decision.from_start_supported
            return True
        if decision.outcome in (WaitOutcome.MEMBER_CANCELLED, WaitOutcome.MEMBER_STOPPED):
            with self.session_factory() as db:
                recording = self._service(db).cancel_scheduled(recording_id)
            if recording is not None:
                self._publish(recording)
            return False
        reason = {
            WaitOutcome.SOURCE_CANCELLED: "source_cancelled",
            WaitOutcome.EXPIRED: "excessive_delay",
            WaitOutcome.AUTH_EXPIRED: "auth_expired",
        }[decision.outcome]
        self._commit_scheduled_failure(recording_id, reason=reason)
        return False

    def _commit_scheduled_failure(self, recording_id: str, *, reason: str) -> None:
        with self.session_factory() as db:
            recording = self._service(db).fail_scheduled(recording_id, reason=reason)
        if recording is not None:
            self._publish(recording)

    def _record_media(self, ctx: RecordingContext) -> LiveMediaResult:
        try:
            return self.recorder.record(ctx)
        except Exception as exc:  # noqa: BLE001
            return LiveMediaResult(status="failed", failure=_redact("media_failed", exc))

    def _capture_chat(self, ctx: RecordingContext, sink: dict[str, LiveChatResult]) -> None:
        try:
            sink["r"] = self.chat_capturer.capture(ctx)
        except Exception as exc:  # noqa: BLE001
            sink["r"] = LiveChatResult(status="failed", failure=_redact("chat_failed", exc))

    def _commit_outcome(
        self, recording_id: str, media: LiveMediaResult, chat: LiveChatResult | None, *, stop_reason: str | None = None
    ) -> None:
        end_reason = stop_reason if media.end_reason == "stopped" and stop_reason else media.end_reason
        chat = chat or LiveChatResult(status="failed", failure=RedactedFailure("chat_failed", "Chat capture failed."))
        with self.session_factory() as db:
            service = self._service(db)
            existing = service.get_by_id(recording_id)
            start_intent = existing.start_intent if existing is not None else "live_edge"
            # Enter `finalizing` and record BOTH sibling sub-statuses in ONE durable
            # write. A `finalizing` row must only ever be observable once
            # the outputs it will finalize are committed: were `finalizing` durable
            # with stale sub-statuses (media still `recording`, chat still `pending`),
            # a crash here would recover as FINALIZE and mislabel a completed+
            # published recording as a false total failure — or, worse under the old
            # RESUME path, re-capture over it. Keeping the three writes atomic makes
            # the observable `finalizing` window crash-consistent; `finalize` then
            # resolves the honest overall in its own short transaction.
            with write_transaction(db, name="live_recording_finalize_outcome"):
                service.enter_finalizing(recording_id)
                if media.capture_origin is not None:
                    # Combine the media run's from-start provenance with the chat's
                    # earliest-coverage report into the honest recording-level history:
                    # missing earlier media OR chat is an explicit partial-history.
                    history = resolve_history(
                        start_intent=start_intent,
                        capture_origin=media.capture_origin,
                        media_history_complete=media.media_history_complete,
                        chat_covered_from_start=chat.covered_from_start,
                    )
                    service.set_media_status(
                        recording_id,
                        media.status,
                        failure=media.failure,
                        library_item_id=media.library_item_id,
                        capture_origin=media.capture_origin,
                        history=history,
                        from_start_supported=media.from_start_supported,
                        awaiting_fallback_choice=media.awaiting_fallback_choice,
                        end_reason=end_reason,
                    )
                else:
                    # Nothing was captured (a media failure, or a from-start-unavailable
                    # run that refused to silently fall back): leave capture provenance
                    # pending and record only the inspected support + choice flag.
                    service.set_media_status(
                        recording_id,
                        media.status,
                        failure=media.failure,
                        library_item_id=media.library_item_id,
                        from_start_supported=media.from_start_supported,
                        awaiting_fallback_choice=media.awaiting_fallback_choice,
                        end_reason=end_reason,
                    )
                service.set_chat_status(
                    recording_id, chat.status, failure=chat.failure, chat_asset_id=chat.chat_asset_id
                )
            recording = service.finalize(recording_id)
        if recording is not None:
            self._publish(recording)

    def _publish(self, recording: LiveRecording) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(
                "live_recording_updated",
                {"user_id": recording.user_id, "recording": {"id": recording.id, "status": recording.status}},
            )
        except Exception:
            # Event delivery is best-effort; the durable recording is authoritative.
            pass

    # -- lifecycle ----------------------------------------------------------

    def wait_all(self, timeout: float = 30.0) -> None:
        """Join every worker started so far (used by tests and shutdown)."""
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join(timeout=timeout)

    def close_all(self) -> None:
        """Ask every in-flight recording to stop and wait briefly for finalization."""
        with self._lock:
            controls = list(self._controls.values())
        for control in controls:
            control.request_stop("shutdown")
        self.wait_all(timeout=30.0)


def _redact(category: str, exc: Exception) -> RedactedFailure:
    del exc  # Never surface an upstream exception string across the boundary.
    message = {
        "media_failed": "The live recording could not be completed.",
        "chat_failed": "The live chat capture could not be completed.",
    }.get(category, "The live recording could not be completed.")
    return RedactedFailure(category, message)
