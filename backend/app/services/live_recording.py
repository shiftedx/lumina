"""Durable state machine for a multi-output live-media acquisition (issue #97).

One :class:`~app.models.LiveRecording` row coordinates two *sibling outputs* — a
media recording published as a Library item and a forward-only chat capture
published as a timed chat asset — with EXPLICIT partial outcomes. This module
owns the durable transitions and the single honesty rule that decides the
overall terminal state; the concurrent execution (recording + chat threads,
guarded transport, restart re-launch) lives in
:mod:`app.services.live_recording_manager`, which drives the transitions here.

The headline invariant, enforced in :func:`compute_overall_status`: Lumina never
reports the overall acquisition ``completed`` unless BOTH requested outputs
reached a documented terminal state. A usable media recording survives chat
failure, and a chat asset survives a media-finalization failure; either is an
honest ``partial`` — never a false success and never a false total failure. A
deliberate stop is distinct from an abrupt cancel and from a failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppSettings, LibraryItem, LiveRecording, User
from app.persistence import write_transaction
from app.services.acquisition_batch import RedactedFailure
from app.services.library import LibraryService, decode_library_cursor, encode_library_cursor
from app.services.remote_playback import RemotePlaybackProgressService


# Overall live-aware states. Non-terminal states never pretend a live source has
# a known total duration; they only report the phase of the coordinated work.
# ``waiting`` (issue #98) is the durable scheduled/upcoming phase: Lumina has a
# best-available provider start time and is waiting to connect near it. It is
# non-terminal, so it counts toward the active ceilings and the member-scoped
# active-uniqueness index — a duplicate schedule can never create a second waiter.
NON_TERMINAL_STATUSES = frozenset({"waiting", "queued", "live", "stopping", "finalizing"})
TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})

# Media terminal states: ``completed`` means the recording reached true
# source-end (the whole broadcast, to its end) and is the ONLY media state that
# permits an overall ``completed``. ``partial`` means a usable recording that did
# NOT reach source-end — a deliberate stop, a bounded window, or an interruption —
# so it is published but never claims a complete capture.
MEDIA_STATUSES = frozenset({"pending", "recording", "finalizing", "completed", "partial", "failed"})
CHAT_STATUSES = frozenset({"pending", "capturing", "completed", "unavailable", "failed"})

# Only a source-end media capture is a full success. A ``partial`` media output is
# usable-but-incomplete: it counts toward "something usable was published" but
# never toward an overall ``completed``. A chat output reached a documented
# terminal state both when it published events (``completed``) and when the source
# simply had no chat to capture (``unavailable``) — the latter is an honest
# terminal state, not a failure, so it does not by itself demote the acquisition.
_MEDIA_COMPLETE = "completed"
_MEDIA_USABLE = frozenset({"completed", "partial"})
_MEDIA_TERMINAL = frozenset({"completed", "partial", "failed"})
_CHAT_TERMINAL_OK = frozenset({"completed", "unavailable"})
_CHAT_PUBLISHED = "completed"

DEFAULT_MAX_ACTIVE_PER_USER = 2
DEFAULT_MAX_ACTIVE_GLOBAL = 6
DEFAULT_MAX_RECOVERY_ATTEMPTS = 3
# Restart-durable waiting-phase bounds (issue #98). ``schedule_grace_seconds``
# mirrors the waiter's excessive-delay grace but is applied durably in
# ``plan_recovery`` so it survives a restart even when the waiter's per-process
# probe counter has reset. ``max_waiting_seconds`` bounds a waiting recording that
# has NO announced start time, measured from its durable ``created_at``.
DEFAULT_SCHEDULE_GRACE_SECONDS = 6 * 60 * 60.0
DEFAULT_MAX_WAITING_SECONDS = 48 * 60 * 60.0

# From-start (issue #98) member INTENT — never a raw yt-dlp flag. ``from_start``
# asks to begin at the source beginning if the inspected source supports it;
# ``live_edge`` is the #97 record-from-now behavior.
START_INTENTS = frozenset({"from_start", "live_edge"})
# When from-start is unavailable at connect: ``allow_live_edge`` falls back to the
# current edge (recorded honestly as such); ``require_choice`` refuses to silently
# fall back and surfaces an explicit fallback-choice condition instead.
FALLBACK_POLICIES = frozenset({"allow_live_edge", "require_choice"})
# Where media capture began. ``pending`` until connect; then the honest answer to
# "did capture begin at the source beginning or at the live edge?".
CAPTURE_ORIGINS = frozenset({"pending", "source_beginning", "live_edge"})
# The from-start history honesty label. ``from_edge`` is a deliberate edge capture
# (not a defect); ``partial`` is the explicit partial-history condition (missing
# earlier media OR chat); ``complete`` is a true whole-broadcast-from-the-start
# capture. From-start is best-effort and NEVER silently reports ``complete``.
HISTORY_STATES = frozenset({"pending", "complete", "partial", "from_edge"})
# Why a scheduled/waiting acquisition left the waiting phase abnormally. These are
# honest bounded outcomes, distinct from a member cancel and from a capture failure.
WAITING_REASONS = frozenset(
    {"source_cancelled", "excessive_delay", "auth_expired", "from_start_unavailable"}
)


class LiveRecordingLimitError(RuntimeError):
    """A per-member or global active-recording ceiling was reached."""


class RecoveryDecision(Enum):
    """What a restart should do with a recording found in a non-terminal state."""

    RESUME = "resume"
    FINALIZE = "finalize"
    NONE = "none"


@dataclass(frozen=True)
class LiveRecordingClaim:
    """The outcome of :meth:`LiveRecordingService.begin`.

    ``created`` is ``True`` only when a brand-new active recording was inserted;
    a duplicate submission for the same member and source returns the existing
    recording with ``created=False`` so no second recording is ever launched.
    """

    recording: LiveRecording
    created: bool


@dataclass(frozen=True)
class MediaRunProvenance:
    """What a media run observed about a from-start capture (issue #98).

    Extends #97's ``reached_source_end`` honesty (about the END of a capture) with
    the symmetric from-start dimension (about its BEGINNING). ``began_at_source_start``
    is True only when capture actually began at the broadcast start;
    ``history_complete`` is True only when the from-start history had no gap at the
    beginning — yt-dlp's from-start is best-effort, so an incomplete beginning is
    reported honestly here rather than papered over. ``from_start_supported`` is the
    inspected answer to whether the source offered from-start at all;
    ``needs_fallback_choice`` signals a from-start-unavailable run that must NOT
    silently fall back (the member's saved intent required an explicit choice).
    """

    library_item_id: str | None
    reached_source_end: bool = False
    began_at_source_start: bool = False
    history_complete: bool = False
    from_start_supported: bool = False
    needs_fallback_choice: bool = False


def compute_overall_status(
    media_status: str,
    chat_status: str,
    *,
    cancel_requested: bool,
    history_partial: bool = False,
) -> str:
    """Decide the honest overall terminal state from the two sibling outputs.

    This is the single authority for the multi-output partial-outcome contract.
    It is pure so it can be exhaustively tested and reused by restart recovery.

    ``history_partial`` (issue #98) is the from-start honesty extension: when the
    two outputs are individually terminal-complete but the acquisition's beginning
    history is incomplete (a missing-earlier-chat gap most notably), the overall is
    an explicit ``partial`` — never a false ``completed``. It never upgrades a
    failure or a cancellation, so it only ever demotes a would-be ``completed``.
    """

    media_complete = media_status == _MEDIA_COMPLETE
    media_usable = media_status in _MEDIA_USABLE
    chat_ok = chat_status in _CHAT_TERMINAL_OK
    any_usable = media_usable or chat_status == _CHAT_PUBLISHED
    if cancel_requested and not any_usable:
        # The member asked to discard in-flight work and nothing usable was
        # published: a genuine cancellation, distinct from a failure.
        return "cancelled"
    if media_complete and chat_ok and not history_partial:
        # Overall completed requires a source-end media capture AND a terminal-ok
        # chat AND complete beginning history; a usable-but-partial media capture,
        # or an incomplete from-start history, can never reach here — so a
        # snapshot, a stopped recording, or a from-start gap is never silently
        # reported as complete.
        return "completed"
    if any_usable:
        # At least one usable output was published (or a late cancel raced one):
        # honest partial, never false success or false total failure.
        return "partial"
    return "failed"


def resolve_capture_provenance(
    *,
    start_intent: str,
    fallback_policy: str,
    outcome: MediaRunProvenance,
) -> tuple[str, str, bool]:
    """Map a from-start run outcome + member intent to media honesty (issue #98).

    Returns ``(capture_origin, media_status, media_history_complete)``. This is
    where the from-start honesty lives, symmetric to where #97 already decides
    ``completed`` vs ``partial`` from ``reached_source_end``:

    - ``from_start`` intent, began at the source start: ``completed`` only when the
      beginning history was complete AND the capture reached source-end; a
      from-start gap makes it a usable ``partial``, never a false complete.
    - ``from_start`` intent that fell back to the edge (fallback allowed): recorded
      as ``live_edge`` and ``partial`` — the member wanted the whole broadcast but
      only the edge onward was captured.
    - ``live_edge`` intent: unchanged #97 semantics — ``completed`` iff it reached
      source-end, and it never claimed earlier history.

    ``fallback_policy`` is accepted for symmetry with the recorder's pre-capture
    require-choice gate; the origin/status mapping does not depend on it once a
    capture has actually happened.
    """

    del fallback_policy  # honored at the pre-capture gate, not in this mapping
    if outcome.began_at_source_start:
        capture_origin = "source_beginning"
        media_status = "completed" if (outcome.reached_source_end and outcome.history_complete) else "partial"
        return capture_origin, media_status, outcome.history_complete
    # Capture began at the live edge.
    capture_origin = "live_edge"
    if start_intent == "from_start":
        # Wanted the beginning, got only the edge: a partial capture of the
        # intended whole broadcast, regardless of reaching source-end.
        return capture_origin, "partial", False
    # Deliberate edge intent (#97): a source-end capture is a full success.
    media_status = "completed" if outcome.reached_source_end else "partial"
    return capture_origin, media_status, False


def resolve_history(
    *,
    start_intent: str,
    capture_origin: str,
    media_history_complete: bool,
    chat_covered_from_start: bool,
) -> str:
    """Derive the recording-level from-start history label for the surface (issue #98).

    ``from_edge`` is a deliberate edge capture and not a defect; ``partial`` is the
    explicit partial-history condition — the earliest reliably available media
    point is later than the true beginning, OR chat is missing its earlier portion;
    ``complete`` requires both a full from-start media beginning AND chat coverage
    back to that beginning.
    """

    if capture_origin == "source_beginning":
        if media_history_complete and chat_covered_from_start:
            return "complete"
        return "partial"
    # capture_origin == "live_edge"
    if start_intent == "from_start":
        # Fell back to the edge contrary to the from-start intent: earlier history
        # is missing, so this is a partial-history condition, not from_edge.
        return "partial"
    return "from_edge"


class LiveRecordingService:
    """Member-scoped durable transitions for coordinated live acquisitions."""

    def __init__(
        self,
        db: Session,
        *,
        max_active_per_user: int = DEFAULT_MAX_ACTIVE_PER_USER,
        max_active_global: int = DEFAULT_MAX_ACTIVE_GLOBAL,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
        schedule_grace_seconds: float = DEFAULT_SCHEDULE_GRACE_SECONDS,
        max_waiting_seconds: float = DEFAULT_MAX_WAITING_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.max_active_per_user = max_active_per_user
        self.max_active_global = max_active_global
        self.max_recovery_attempts = max_recovery_attempts
        self.schedule_grace_seconds = schedule_grace_seconds
        self.max_waiting_seconds = max_waiting_seconds
        # A controllable clock so restart-recovery deadline decisions are
        # deterministic in tests; defaults to the module wall clock in production.
        self._clock = clock or _now

    # -- creation + idempotency --------------------------------------------

    def begin(
        self,
        *,
        user: User,
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
    ) -> LiveRecordingClaim:
        """Create, or return the existing, active recording for (member, source).

        Idempotency and the active ceiling are re-checked inside the write
        transaction: the member-scoped partial-unique index makes a duplicate
        insert fail closed, and a duplicate submission returns the existing
        recording rather than a limit error. A duplicate submission never mutates
        the original intent or schedule, so a re-schedule can never create a
        second waiter or silently change the saved intent.

        ``await_schedule`` (issue #98) marks an UPCOMING broadcast: the recording
        starts durably in ``waiting`` and the waiter wakes near ``scheduled_start_at``
        (a best-available provider time that may be absent — the waiter still probes
        for the source going live). ``start_intent`` / ``fallback_policy`` are the
        from-start member intent, validated here so a raw or unknown value can never
        be persisted.
        """

        if start_intent not in START_INTENTS:
            raise ValueError(f"Unsupported start intent: {start_intent!r}")
        if fallback_policy not in FALLBACK_POLICIES:
            raise ValueError(f"Unsupported fallback policy: {fallback_policy!r}")

        canonical = RemotePlaybackProgressService.canonical_source_identity(source_identity)
        key = RemotePlaybackProgressService.source_identity_key(canonical)

        existing = self._active_for_source(user.id, key)
        if existing is not None:
            return LiveRecordingClaim(recording=existing, created=False)

        recording = LiveRecording(
            id=str(uuid.uuid4()),
            user_id=user.id,
            source_url=source_url,
            source_identity=canonical,
            source_identity_key=key,
            extractor=extractor,
            remote_id=remote_id,
            title=title,
            status="waiting" if (await_schedule or scheduled_start_at is not None) else "queued",
            media_status="pending",
            chat_status="pending",
            format_selection=format_selection or {},
            output_profile=output_profile or {},
            scheduled_start_at=scheduled_start_at,
            start_intent=start_intent,
            fallback_policy=fallback_policy,
            from_start_supported=from_start_supported,
        )
        try:
            with write_transaction(self.db, name="live_recording_begin"):
                self._enforce_active_limits(user.id)
                self.db.add(recording)
                self.db.flush()
        except IntegrityError:
            # A concurrent submission for the same member and source won the
            # active-uniqueness index. Return that recording; never a second one.
            self.db.rollback()
            existing = self._active_for_source(user.id, key)
            if existing is None:
                raise
            return LiveRecordingClaim(recording=existing, created=False)
        return LiveRecordingClaim(recording=recording, created=True)

    def _active_for_source(self, user_id: str, key: str) -> LiveRecording | None:
        return self.db.scalar(
            select(LiveRecording).where(
                LiveRecording.user_id == user_id,
                LiveRecording.source_identity_key == key,
                LiveRecording.status.in_(tuple(NON_TERMINAL_STATUSES)),
            )
        )

    def _enforce_active_limits(self, user_id: str) -> None:
        active = LiveRecording.status.in_(tuple(NON_TERMINAL_STATUSES))
        global_active = self.db.scalar(
            select(func.count()).select_from(LiveRecording).where(active)
        )
        if global_active is not None and global_active >= self.max_active_global:
            raise LiveRecordingLimitError("Lumina is recording as many live streams as it can right now.")
        member_active = self.db.scalar(
            select(func.count()).select_from(LiveRecording).where(active, LiveRecording.user_id == user_id)
        )
        if member_active is not None and member_active >= self.max_active_per_user:
            raise LiveRecordingLimitError("You already have as many live recordings as Lumina allows.")

    # -- reads -------------------------------------------------------------

    def get(self, user: User, recording_id: str) -> LiveRecording | None:
        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.user_id != user.id:
            return None
        return recording

    def get_by_id(self, recording_id: str) -> LiveRecording | None:
        """Unscoped read for the manager's own workers (no member in hand)."""
        return self.db.get(LiveRecording, recording_id)

    PAGE_DEFAULT_LIMIT = 50
    PAGE_MAX_LIMIT = 100

    def list_for_user(
        self,
        user: User,
        *,
        cursor: str | None = None,
        limit: int = PAGE_DEFAULT_LIMIT,
    ) -> tuple[list[LiveRecording], str | None]:
        """Return one bounded page of the member's recordings, newest first.

        ADR 0006 bounded-read pattern (mirrors ``JobManager.list_for_user``):
        keyset cursor over (``created_at`` DESC, ``id`` DESC), never an
        unbounded per-member scan. Raises ``ValueError`` for a malformed cursor.
        """
        bounded_limit = max(1, min(self.PAGE_MAX_LIMIT, limit))
        query = (
            select(LiveRecording)
            .where(LiveRecording.user_id == user.id)
            .order_by(LiveRecording.created_at.desc(), LiveRecording.id.desc())
        )
        if cursor is not None:
            payload = decode_library_cursor(cursor)
            try:
                created_at = datetime.fromisoformat(payload["c"])
                recording_id = payload["i"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid live-recording page cursor") from exc
            if not isinstance(recording_id, str):
                raise ValueError("Invalid live-recording page cursor")
            query = query.where(
                or_(
                    LiveRecording.created_at < created_at,
                    and_(LiveRecording.created_at == created_at, LiveRecording.id < recording_id),
                )
            )
        rows = list(self.db.scalars(query.limit(bounded_limit + 1)))
        recordings = rows[:bounded_limit]
        next_cursor = None
        if len(rows) > bounded_limit:
            last = recordings[-1]
            next_cursor = encode_library_cursor({"c": last.created_at.isoformat(), "i": last.id})
        return recordings, next_cursor

    # -- lifecycle transitions ---------------------------------------------

    def mark_recording_started(self, recording_id: str) -> LiveRecording | None:
        """The media edge connected: move to ``live`` and set the offset base.

        ``recording_started_at`` is the media start the chat capture bases its
        media offsets on, so the completed Library item and its timed chat asset
        stay synchronized. Idempotent and never regresses a terminal recording.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        now = _now()
        with write_transaction(self.db, name="live_recording_started"):
            if recording.status == "queued":
                recording.status = "live"
            if recording.media_status in {"pending"}:
                recording.media_status = "recording"
            if recording.recording_started_at is None:
                recording.recording_started_at = now
            if recording.started_at is None:
                recording.started_at = now
        return recording

    def mark_connecting(
        self, recording_id: str, *, from_start_supported: bool | None = None
    ) -> LiveRecording | None:
        """The waiter observed the source go live: move ``waiting`` -> ``queued``.

        This is the durable transition out of the scheduled/upcoming phase into the
        #97 connect flow. It records the inspected from-start support so the outcome
        never implies from-start was possible when the source did not offer it.
        Idempotent and never regresses a recording past ``queued``.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_connecting"):
            if recording.status == "waiting":
                recording.status = "queued"
            if from_start_supported is not None:
                recording.from_start_supported = from_start_supported
        return recording

    def record_schedule_change(
        self, recording_id: str, scheduled_start_at: datetime
    ) -> LiveRecording | None:
        """Durably update the best-available provider start time for a waiter.

        A broadcast can be rescheduled while Lumina waits; persisting the new time
        means the waiter wakes near the updated time and the change survives a
        restart. Only meaningful while still ``waiting``.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status != "waiting":
            return recording
        with write_transaction(self.db, name="live_recording_reschedule"):
            recording.scheduled_start_at = scheduled_start_at
        return recording

    def fail_scheduled(
        self,
        recording_id: str,
        *,
        reason: str,
        awaiting_fallback_choice: bool = False,
    ) -> LiveRecording | None:
        """Terminate a scheduled acquisition that never connected, honestly.

        A cancelled broadcast, an excessive delay, an expired sign-in, or a
        from-start-unavailable + require-choice source ends the acquisition as
        ``failed`` with a distinct ``waiting_reason`` — never a false completion and
        never a silent edge fallback. ``awaiting_fallback_choice`` marks the case a
        member can resolve by deliberately re-recording from the edge.
        """

        if reason not in WAITING_REASONS:
            raise ValueError(f"Unsupported waiting reason: {reason!r}")
        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_fail_scheduled"):
            recording.status = "failed"
            recording.waiting_reason = reason
            recording.awaiting_fallback_choice = awaiting_fallback_choice
            recording.finished_at = recording.finished_at or _now()
        return recording

    def cancel_scheduled(self, recording_id: str) -> LiveRecording | None:
        """The member abandoned a scheduled acquisition before anything was captured.

        A deliberate stop or cancel during the waiting phase discards a schedule
        that never produced any output: an honest ``cancelled``, distinct from a
        capture failure and from a source-side cancellation.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_cancel_scheduled"):
            recording.status = "cancelled"
            recording.cancel_requested = True
            recording.finished_at = recording.finished_at or _now()
        return recording

    def enter_finalizing(self, recording_id: str) -> LiveRecording | None:
        """Both capture loops have ended; publication is underway."""

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_finalizing"):
            recording.status = "finalizing"
            recording.recording_stopped_at = recording.recording_stopped_at or _now()
        return recording

    def set_media_status(
        self,
        recording_id: str,
        status: str,
        *,
        failure: RedactedFailure | None = None,
        library_item_id: str | None = None,
        capture_origin: str | None = None,
        history: str | None = None,
        from_start_supported: bool | None = None,
        awaiting_fallback_choice: bool | None = None,
        end_reason: str | None = None,
    ) -> LiveRecording | None:
        if status not in MEDIA_STATUSES:
            raise ValueError(f"Unsupported media output status: {status!r}")
        if capture_origin is not None and capture_origin not in CAPTURE_ORIGINS:
            raise ValueError(f"Unsupported capture origin: {capture_origin!r}")
        if history is not None and history not in HISTORY_STATES:
            raise ValueError(f"Unsupported history state: {history!r}")
        recording = self.db.get(LiveRecording, recording_id)
        if recording is None:
            return None
        with write_transaction(self.db, name="live_recording_media"):
            recording.media_status = status
            if library_item_id is not None:
                recording.library_item_id = library_item_id
            if capture_origin is not None:
                recording.capture_origin = capture_origin
            if history is not None:
                recording.history = history
            if from_start_supported is not None:
                recording.from_start_supported = from_start_supported
            if awaiting_fallback_choice is not None:
                recording.awaiting_fallback_choice = awaiting_fallback_choice
            if end_reason is not None:
                recording.media_end_reason = end_reason
            if failure is not None:
                recording.media_failure_category = failure.category
                recording.media_error = failure.message
            elif status != "failed":
                recording.media_failure_category = None
                recording.media_error = None
        return recording

    def note_media_published(self, recording_id: str, library_item_id: str) -> LiveRecording | None:
        """Durably point the recording at its Library item AT publish time (issue #112).

        The media sink calls this INSIDE the same transaction that inserts the
        Library item (``write_transaction`` is reentrant, so this joins the
        publish commit): there is no durable moment where the item exists but the
        recording row does not point at it. A crash between the publish and the
        atomic finalize-outcome commit therefore leaves a ``live``/``stopping``
        row whose ``library_item_id`` proves capture already produced its item,
        so :meth:`plan_recovery` finalizes instead of RESUMEing — a resume would
        re-capture and orphan the first item. The media sub-status is floored to
        ``partial`` (a usable published capture not yet proven source-end
        complete — never a false ``completed``); when the process survives, the
        ordinary finalize-outcome commit overwrites it with the fully resolved
        status.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_media_published"):
            recording.library_item_id = library_item_id
            if recording.media_status not in _MEDIA_TERMINAL:
                recording.media_status = "partial"
        return recording

    def set_chat_status(
        self,
        recording_id: str,
        status: str,
        *,
        failure: RedactedFailure | None = None,
        chat_asset_id: str | None = None,
    ) -> LiveRecording | None:
        if status not in CHAT_STATUSES:
            raise ValueError(f"Unsupported chat output status: {status!r}")
        recording = self.db.get(LiveRecording, recording_id)
        if recording is None:
            return None
        with write_transaction(self.db, name="live_recording_chat"):
            recording.chat_status = status
            if chat_asset_id is not None:
                recording.chat_asset_id = chat_asset_id
            if failure is not None:
                recording.chat_failure_category = failure.category
                recording.chat_error = failure.message
            elif status != "failed":
                recording.chat_failure_category = None
                recording.chat_error = None
        return recording

    def request_stop(self, user: User, recording_id: str) -> LiveRecording:
        """Deliberately stop: finalize usable outputs, distinct from cancel."""

        recording = self._owned_or_raise(user, recording_id)
        if recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_stop"):
            recording.stop_requested = True
            if recording.status in {"queued", "live"}:
                recording.status = "stopping"
        return recording

    def request_cancel(self, user: User, recording_id: str) -> LiveRecording:
        """Abruptly cancel: discard in-flight work, distinct from a deliberate stop."""

        recording = self._owned_or_raise(user, recording_id)
        if recording.status in TERMINAL_STATUSES:
            return recording
        with write_transaction(self.db, name="live_recording_cancel"):
            recording.cancel_requested = True
        return recording

    def finalize(self, recording_id: str) -> LiveRecording | None:
        """Resolve the honest overall terminal state from the sibling outputs."""

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None:
            return None
        if recording.status in TERMINAL_STATUSES:
            return recording
        overall = compute_overall_status(
            recording.media_status,
            recording.chat_status,
            cancel_requested=recording.cancel_requested,
            # An explicit partial-history condition can never resolve to a false
            # ``completed`` (from-start honesty), even when both outputs are
            # individually terminal-complete.
            history_partial=recording.history == "partial",
        )
        with write_transaction(self.db, name="live_recording_finalize"):
            recording.status = overall
            recording.finished_at = recording.finished_at or _now()
        return recording

    # -- restart recovery ---------------------------------------------------

    def plan_recovery(self, recording: LiveRecording) -> RecoveryDecision:
        """Deterministically decide a restart action for a recovered recording.

        A recording already in ``finalizing`` never re-captures: both capture loops
        had ended and publication was underway, so recovery resolves the overall
        HONESTLY from the durable outputs rather than RESUMEing — a
        resume would re-capture from the current edge and overwrite (or orphan) the
        already-published media item, inverting a true completion to a false total
        failure. This holds crash-consistently because a durable ``finalizing`` row
        always carries its committed sibling sub-statuses (see the manager's
        ``_commit_outcome``). A recording the member already asked to stop or cancel
        is likewise finalized rather than resumed (their intent is durable across
        the restart); a recording that has exhausted its bounded recovery attempts
        is finalized with whatever it durably captured; anything else non-terminal
        resumes forward from the current edge.
        """

        if recording.status in TERMINAL_STATUSES:
            return RecoveryDecision.NONE
        if recording.status == "finalizing":
            return RecoveryDecision.FINALIZE
        if recording.stop_requested or recording.cancel_requested:
            return RecoveryDecision.FINALIZE
        if recording.status == "waiting":
            # A waiting re-entry never counts toward the capture-recovery budget
            # (issue I1), so the ONLY restart-durable bound on the waiting phase
            # lives here. The waiter's own bounds are per-process (its probe counter
            # resets each run and its wall-clock grace applies only once a start time
            # is known), so without this a perpetually-upcoming source with no
            # announced start could hold an active slot indefinitely across frequent
            # restarts. Past a durable deadline the wait ends honestly; otherwise it
            # resumes waiting.
            deadline = self._waiting_deadline(recording)
            if deadline is not None and self._clock() > deadline:
                return RecoveryDecision.FINALIZE
            return RecoveryDecision.RESUME
        if recording.library_item_id is not None:
            # The media run already published its Library item — recorded durably
            # at publish time (issue #112) — so capture is over and only the
            # finalize-outcome commit was lost to the crash. RESUME would
            # re-capture from the current edge and publish a SECOND item,
            # orphaning the first; recovery finalizes from the durable outputs
            # instead (the publish-time floor makes the media output a usable
            # ``partial``, so this is an honest partial, never a false failure).
            return RecoveryDecision.FINALIZE
        if recording.attempts >= self.max_recovery_attempts:
            return RecoveryDecision.FINALIZE
        return RecoveryDecision.RESUME

    def _waiting_deadline(self, recording: LiveRecording) -> datetime | None:
        """The restart-durable moment a waiting recording must stop waiting.

        Computed only from persisted columns so it is identical across restarts: a
        known ``scheduled_start_at`` plus the schedule grace (the same tolerance the
        waiter applies to an excessive delay, made durable here), or — when no start
        was ever announced — the durable ``created_at`` plus the no-start cap.
        Returns ``None`` only if there is no durable base to measure from, in which
        case recovery stays permissive (RESUME) rather than terminating blindly.
        """

        if recording.scheduled_start_at is not None:
            return recording.scheduled_start_at + timedelta(seconds=self.schedule_grace_seconds)
        if recording.created_at is not None:
            return recording.created_at + timedelta(seconds=self.max_waiting_seconds)
        return None

    def claim_for_run(self, recording_id: str) -> LiveRecording | None:
        """Count one (re)claim toward the bounded CAPTURE-recovery budget.

        A re-entry while still ``waiting`` (issue I1) does NOT count: a broadcast
        scheduled hours out (the whole point of #98) can span many benign process
        restarts before it ever goes live. Counting those restarts would let a
        handful of ordinary redeploys trip the capture ceiling and fail the schedule
        before capture even begins. The waiting phase is instead bounded durably by
        :meth:`plan_recovery`'s waiting deadline; this budget only guards a poison
        CAPTURE crash-loop, so an attempt is counted once the recording has left
        ``waiting`` for actual capture.
        """

        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.status in TERMINAL_STATUSES:
            return recording
        if recording.status == "waiting":
            return recording
        with write_transaction(self.db, name="live_recording_claim"):
            recording.attempts += 1
        return recording

    # -- helpers ------------------------------------------------------------

    def _owned_or_raise(self, user: User, recording_id: str) -> LiveRecording:
        recording = self.db.get(LiveRecording, recording_id)
        if recording is None or recording.user_id != user.id:
            raise LookupError(recording_id)
        return recording


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sweep_recording_retention(db: Session, now: datetime | None = None) -> int:
    """Quarantine finished recordings past the admin's age or total-size bound.

    Kept recordings are never removed but still count toward the size budget.
    Deletion goes through the artifact quarantine, so it stays recoverable for the
    quarantine window. Returns how many recordings were removed.

    Loads every finished recording's (flags, item) per cycle; a household
    holds hundreds at most. Page it if that ever stops being true.
    """
    app_settings = db.get(AppSettings, 1)
    keep_days = (app_settings.recording_keep_days or 0) if app_settings else 0
    max_bytes = ((app_settings.recording_max_gb or 0) if app_settings else 0) * 1024**3
    if not keep_days and not max_bytes:
        return 0
    cutoff = (now or _now()) - timedelta(days=keep_days) if keep_days else None
    rows = db.execute(
        select(LiveRecording.kept, LiveRecording.finished_at, LibraryItem)
        .join(LibraryItem, LibraryItem.id == LiveRecording.library_item_id)
        .where(LiveRecording.status.in_(tuple(TERMINAL_STATUSES)), LibraryItem.status != "missing")
        .order_by(LiveRecording.finished_at.desc())
    ).all()
    total = 0
    removed = 0
    library = LibraryService(db)
    for kept, finished_at, item in rows:
        size = item.file_size or 0
        total += size
        expired = cutoff is not None and finished_at is not None and finished_at < cutoff
        if kept or not (expired or (max_bytes and total > max_bytes)):
            continue
        try:
            with write_transaction(db, name="recording_retention"):
                library.delete_file(item)
        except (FileNotFoundError, PermissionError):
            continue
        total -= size
        removed += 1
    return removed
