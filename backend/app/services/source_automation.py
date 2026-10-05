from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp
from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.events import EventBus
from app.models import AutomationDecision, AutomationRun, DownloadJob, LibraryItem, SourceAutomation, User, utcnow
from app.services import streaming_gate
from app.persistence import queue_after_commit, write_transaction
from app.schemas import (
    AutomationDecisionResponse,
    AutomationPreviewResult,
    AutomationRuleSet,
    AutomationRunResponse,
    FormatSelection,
    JobCreateRequest,
    OutputProfile,
    PreviewEntry,
    PreviewResponse,
    SourceAutomationCreateRequest,
    SourceAutomationResponse,
    UserAutomationDefaults,
)
from app.services import provider_budget
from app.services.channel_discovery import channel_feed_url
from app.services.job_manager import JobManager
from app.services.kick_public import is_kick_host
from app.services.redaction import redact
from app.services.user_settings import UserSettingsService
from app.services.webhooks import WebhookService
from app.services.yt_dlp_service import YtDlpService


# One member's automations share a small sweep pool: bound count and cadence.
MAX_AUTOMATIONS_PER_USER = 200
MIN_CHECK_INTERVAL_MINUTES = 15
# Bounded loop: long enough to see a schedule's true firing cadence (daily and
# most weekly patterns), short enough to walk minute-by-minute cheaply.
CADENCE_WINDOW_MINUTES = 48 * 60


# Several members following one channel cost one extraction per cycle; a visit refreshes a follow only when it is stale.
FOLLOW_REFRESH_MIN_AGE = timedelta(minutes=10)
FEED_CACHE_MAX_SECONDS = 30 * 60
BUDGET_RETRY = timedelta(minutes=15)
_FEED_CACHE: dict[str, tuple[float, PreviewResponse]] = {}
_FEED_CACHE_LOCK = threading.Lock()


KICK_AUTOMATION_UNSUPPORTED = (
    "Kick downloads are not supported yet, so automatic saving stays off. Following still shows when this channel is live."
)


def _is_not_live(exc: Exception) -> bool:
    """yt-dlp's provider-confirmed "channel is offline" answer, not an outage."""
    cause = exc.exc_info[1] if isinstance(exc, yt_dlp.utils.DownloadError) and exc.exc_info else exc
    return isinstance(cause, yt_dlp.utils.UserNotLive)


class AutomationAlreadyRunningError(Exception):
    """A run was requested while another runner holds the automation's lease.

    Both scheduled and manual runs claim a short-lived database lease before
    inspecting the source, so only one runner advances an automation at a time.
    The route layer maps this to HTTP 409.
    """


@dataclass
class _PlannedDecision:
    """One in-memory Automation decision, computed before the write transaction opens."""

    entry: PreviewEntry
    action: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    stage_payload: JobCreateRequest | None = None
    normalized_source_url: str | None = None


class SourceAutomationService:
    # A run holds the lease across the source inspection, which happens outside
    # any write transaction; 15 minutes covers a slow preview and is reclaimed
    # by expiry if a runner crashes without clearing it.
    RUN_LEASE_TTL = timedelta(minutes=15)
    # Newest entries kept per follow for the mixed-source Subscriptions feed.
    FEED_ENTRY_LIMIT = 12

    def __init__(
        self,
        db: Session,
        jobs: JobManager,
        events: EventBus | None = None,
        *,
        artwork_url_resolver: Callable[[str | None], str | None] | None = None,
    ):
        self.db = db
        self.jobs = jobs
        self.events = events
        # Injected at the serve boundary (see app.main._source_automation_service)
        # so a stored raw provider URL is mapped to a frontend-consumable
        # /api/artwork/... URL wherever an automation is serialized, including
        # the automation_checked / automation_failed SSE payloads built inside
        # this service. With no resolver configured, serialize() degrades to
        # null rather than ever echoing the raw upstream URL.
        self._artwork_url_resolver = artwork_url_resolver

    def list_automations(self, user: User) -> list[SourceAutomation]:
        return (
            self.db.query(SourceAutomation)
            .filter(SourceAutomation.user_id == user.id)
            .order_by(SourceAutomation.created_at.desc())
            .all()
        )

    def get_automation(self, automation_id: str, user: User | None = None) -> SourceAutomation | None:
        automation = self.db.get(SourceAutomation, automation_id)
        if automation is None:
            return None
        if user is not None and automation.user_id != user.id:
            return None
        return automation

    def create_automation(self, payload: SourceAutomationCreateRequest, user: User) -> SourceAutomation:
        # The URL check resolves DNS; it runs before building the automation,
        # which may flush a first-time settings row and open a write
        # transaction that would otherwise span the lookup.
        YtDlpService(self.db).validate_source_url(payload.source_url)
        owned = self.db.query(SourceAutomation.id).filter(SourceAutomation.user_id == user.id).count()
        if owned >= MAX_AUTOMATIONS_PER_USER:
            raise ValueError(f"You can have at most {MAX_AUTOMATIONS_PER_USER} automations.")
        automation = self._build_automation(payload, user)
        self._validate_automation(automation, user, validate_source=False)
        self.db.add(automation)
        self.db.flush()
        return automation

    def _build_automation(self, payload: SourceAutomationCreateRequest, user: User) -> SourceAutomation:
        defaults = self._user_automation_defaults(user)
        return SourceAutomation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            label=payload.label.strip() or "Source automation",
            source_url=YtDlpService.normalize_source_url(payload.source_url.strip()),
            source_type=payload.source_type or self.infer_source_type(payload.source_url),
            cron_expression=(payload.cron_expression or defaults.cron_expression).strip(),
            active=payload.active,
            auto_download=payload.auto_download,
            format_selection=(payload.format_selection or defaults.format_selection).model_dump(),
            output_profile=(payload.output_profile or defaults.output_profile).model_dump(),
            rules=payload.rules.model_dump(),
            duplicate_policy=payload.duplicate_policy,
            max_items_per_run=payload.max_items_per_run,
            max_items_per_day=payload.max_items_per_day,
            backfill_limit=payload.backfill_limit,
            next_check_at=(
                None if not payload.active or payload.source_type == "channel"
                else self._next_run(payload.cron_expression or defaults.cron_expression)
            ),
            last_error=None,
            last_run_summary={},
        )

    def set_auto_download(self, automation_id: str, enabled: bool, user: User) -> SourceAutomation:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        if enabled and is_kick_host(automation.source_url):
            raise ValueError(KICK_AUTOMATION_UNSUPPORTED)
        automation.auto_download = enabled
        self.db.flush()
        return automation

    def pause_automation(self, automation_id: str, user: User) -> SourceAutomation:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        automation.active = False
        automation.next_check_at = None
        self.db.flush()
        return automation

    def resume_automation(self, automation_id: str, user: User) -> SourceAutomation:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        self._validate_automation(automation, user)
        automation.active = True
        automation.next_check_at = self._next_run(automation.cron_expression)
        self.db.flush()
        return automation

    def delete_automation(self, automation_id: str, user: User) -> None:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        self.db.delete(automation)
        self.db.flush()

    def due_ids(self, now: datetime) -> list[str]:
        return [
            automation_id
            for (automation_id,) in self.db.query(SourceAutomation.id)
            .join(User, User.id == SourceAutomation.user_id)
            .filter(SourceAutomation.active.is_(True), User.is_active.is_(True))
            .filter((SourceAutomation.next_check_at.is_(None)) | (SourceAutomation.next_check_at <= now))
            .order_by(SourceAutomation.next_check_at.asc().nullsfirst(), SourceAutomation.created_at.asc())
            .all()
        ]

    def process_one(self, automation_id: str, now: datetime) -> tuple[SourceAutomation, int] | None:
        """Refresh one due follow/automation; a failure stays on that source only."""
        try:
            automation = self.db.get(SourceAutomation, automation_id)
            if automation is None:
                return None
            user = self.db.get(User, automation.user_id)
            if user is None or not user.is_active:
                return None
            if not streaming_gate.allows_url(self.db, user, automation.source_url):
                # The member's access now blocks this kind: no run is recorded; it waits for its next slot.
                with write_transaction(self.db, name="automation_access_skip"):
                    automation.next_check_at = self._next_run(automation.cron_expression, now)
                return None
            run =self.run_automation(automation.id, user, now=now)
            return automation, run.queued_count
        except Exception:
            self.db.rollback()
            try:
                automation = self.db.get(SourceAutomation, automation_id)
            except Exception:
                self.db.rollback()
                return None
            return (automation, 0) if automation is not None else None

    def process_due(self, now: datetime | None = None) -> list[tuple[SourceAutomation, int]]:
        now = now or utcnow()
        results = (self.process_one(automation_id, now) for automation_id in self.due_ids(now))
        return [result for result in results if result is not None]

    def request_follow_refresh(self, user: User) -> list[SourceAutomation]:
        """Mark the member's active follows due; the bounded scheduler does the provider work.

        Repeated or concurrent requests (several viewers, several tabs) only set
        the same due marker, and the run lease keeps it to one check per follow.
        """
        follows = [
            automation for automation in self.list_automations(user)
            if automation.source_type == "channel" and automation.active
        ]
        stale_before = utcnow() - FOLLOW_REFRESH_MIN_AGE
        for automation in follows:
            if automation.last_checked_at is None or automation.last_checked_at < stale_before:
                automation.next_check_at = None
        self.db.flush()
        return follows

    def run_automation(self, automation_id: str, user: User, now: datetime | None = None) -> AutomationRun:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        return self._execute(automation, user, dry_run=False, now=now)

    def preview_automation(self, automation_id: str, user: User, *, now: datetime | None = None) -> AutomationPreviewResult:
        automation = self.get_automation(automation_id, user)
        if automation is None:
            raise ValueError("Automation not found")
        self._validate_automation(automation, user)
        run_response = self.serialize_run(self._execute(automation, user, dry_run=True, now=now), include_decisions=True)
        return AutomationPreviewResult(automation=self.serialize(automation), run=run_response)

    def _claim_run_lease(self, automation: SourceAutomation) -> str:
        """Claim the automation's run lease with a conditional UPDATE, or raise.

        The claim wins only when no live lease exists, so a concurrent runner
        cannot advance the same automation. Wall-clock time bounds the lease so
        it never couples to a caller-supplied scheduling ``now``. Returns the
        claimed lease id so release can verify it still owns the lease.
        """
        lease_now = utcnow()
        lease_id = str(uuid.uuid4())
        with write_transaction(self.db, name="automation_lease_claim"):
            claimed = self.db.execute(
                update(SourceAutomation)
                .where(
                    SourceAutomation.id == automation.id,
                    or_(
                        SourceAutomation.run_lease_id.is_(None),
                        SourceAutomation.run_lease_expires_at < lease_now,
                    ),
                )
                .values(run_lease_id=lease_id, run_lease_expires_at=lease_now + self.RUN_LEASE_TTL)
            )
        self.db.refresh(automation)
        if claimed.rowcount != 1:
            raise AutomationAlreadyRunningError("This Source automation is already running.")
        return lease_id

    def _release_run_lease(self, automation: SourceAutomation, lease_id: str | None) -> None:
        """Clear the lease inside the run's finalizing write transaction, only if still ours.

        A run that outlives its TTL may have had the lease reclaimed by another
        runner; the identity-guarded UPDATE reads the durable lease id (not the
        stale in-session copy), so finalizing never clears a lease this run no
        longer holds (a rowcount of 0 is expected in that case). The lease
        columns are expired so a later read of this automation reflects the
        durable outcome rather than the pre-release cache.
        """
        if lease_id is None:
            return
        self.db.execute(
            update(SourceAutomation)
            .where(SourceAutomation.id == automation.id, SourceAutomation.run_lease_id == lease_id)
            .values(run_lease_id=None, run_lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        self.db.expire(automation, ["run_lease_id", "run_lease_expires_at"])

    def _release_run_lease_out_of_band(self, automation_id: str, lease_id: str | None) -> None:
        """Best-effort identity-guarded lease release on a FRESH session.

        Used when a finalizing write could not commit (e.g. writer-slot
        contention, which raises OperationalError — a SQLAlchemyError). The
        run's own session may be unusable, so a separate short transaction
        clears the lease promptly instead of leaving it held for the full TTL;
        expiry remains the backstop if even this cannot commit.
        """
        if lease_id is None:
            return
        bind = self.db.get_bind()
        try:
            with Session(bind=bind) as release_db:
                with write_transaction(release_db, name="automation_lease_release"):
                    release_db.execute(
                        update(SourceAutomation)
                        .where(
                            SourceAutomation.id == automation_id,
                            SourceAutomation.run_lease_id == lease_id,
                        )
                        .values(run_lease_id=None, run_lease_expires_at=None)
                    )
        except Exception:
            # Expiry-based reclaim is the backstop; a failed release must not
            # mask the original error the caller is about to re-raise.
            pass

    def _execute(self, automation: SourceAutomation, user: User, *, dry_run: bool, now: datetime | None = None) -> AutomationRun:
        now = now or utcnow()
        lease_id: str | None = None
        if not dry_run:
            # Claim before the external inspection so a concurrent scheduled or
            # manual run yields; dry runs write nothing durable and never claim.
            lease_id = self._claim_run_lease(automation)
        run = AutomationRun(
            id=str(uuid.uuid4()),
            automation_id=automation.id,
            user_id=user.id,
            status="preview" if dry_run else "running",
            started_at=now,
            discovered_count=0,
            matched_count=0,
            queued_count=0,
            manual_count=0,
            skipped_count=0,
            failed_count=0,
            summary_json={},
        )

        decisions: list[AutomationDecision | AutomationDecisionResponse] = []
        try:
            # The external inspection runs first, with reads only and no run
            # row, so a slow or stalled source can never hold the writer slot
            # or an open SQLite write transaction.
            self._validate_automation(automation, user)
            try:
                preview: PreviewResponse | None = self._feed_preview(automation, now)
            except provider_budget.BudgetExhausted:
                if dry_run:
                    raise
                # Not a failure: keep the last feed, check again soon, tell nobody.
                with write_transaction(self.db, name="automation_run"):
                    automation.next_check_at = now + BUDGET_RETRY
                    self._release_run_lease(automation, lease_id)
                run.status, run.finished_at = "deferred", utcnow()
                return run
            except yt_dlp.utils.DownloadError as exc:
                # An offline Twitch/Kick channel is a successful check with nothing new.
                if not _is_not_live(exc):
                    raise
                preview = None
            entries = self._candidate_entries(preview, automation) if preview else []
            run.discovered_count = len(entries)
            # Decisions are computed in memory before the write transaction so
            # per-entry URL validation (a DNS lookup) never runs with the
            # writer held.
            planned = self._plan_decisions(automation, user, preview, entries, now=now, dry_run=dry_run)
            if dry_run:
                for item in planned:
                    decisions.append(self._record_decision(run, automation, user, item.entry, item.action, item.reason, item.details, persist=False))
                self._finalize_run(run, decisions, dry_run=True)
                run.decisions_for_preview = decisions  # type: ignore[attr-defined]
                return run
            with write_transaction(self.db, name="automation_run"):
                self.db.add(run)
                self._stage_planned(run, automation, user, planned, decisions)
                automation.last_checked_at = now
                automation.next_check_at = self._next_run(automation.cron_expression, now) if automation.active else None
                automation.last_error = None
                automation.last_run_summary = run.summary_json
                automation.feed_entries = [
                    entry.model_dump(mode="json", exclude={"artwork_url"}) for entry in entries[: self.FEED_ENTRY_LIMIT]
                ]
                if preview:
                    self._persist_channel_artwork(automation, preview)
                self.db.flush()
                self._release_run_lease(automation, lease_id)
                self._publish_after_commit(
                    "automation_checked",
                    {
                        "user_id": user.id,
                        "automation": self.serialize(automation).model_dump(mode="json"),
                        "run": self.serialize_run(run).model_dump(mode="json"),
                    },
                )
            return run
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, SQLAlchemyError):
                # A finalizing write failed (e.g. writer-slot contention, an
                # OperationalError which is a SQLAlchemyError). The run's own
                # session may be unusable, so release the lease out of band
                # instead of holding it for the full TTL.
                self._release_run_lease_out_of_band(automation.id, lease_id)
                raise
            safe_error = redact(str(exc), paths=True)
            run.status = "failed"
            run.error = safe_error
            run.finished_at = utcnow()
            run.summary_json = self._summary(run)
            automation.last_error = safe_error
            if not dry_run:
                automation.last_checked_at = now
                automation.next_check_at = self._next_run(automation.cron_expression, now) if automation.active else None
                automation.last_run_summary = run.summary_json
                try:
                    with write_transaction(self.db, name="automation_run"):
                        self.db.add(run)
                        self.db.flush()
                        self._release_run_lease(automation, lease_id)
                        self._notify_failure_after_commit(automation, safe_error)
                        self._publish_after_commit(
                            "automation_failed",
                            {"user_id": user.id, "automation": self.serialize(automation).model_dump(mode="json"), "error": safe_error},
                        )
                except SQLAlchemyError:
                    # The failure-record write itself failed (e.g. writer-slot
                    # contention). The run's own session may be unusable, so
                    # release the lease out of band instead of holding it for
                    # the full TTL.
                    self._release_run_lease_out_of_band(automation.id, lease_id)
                    raise
                return run
            raise

    def _feed_preview(self, automation: SourceAutomation, now: datetime) -> PreviewResponse:
        """Extract the follow's feed in the background lane; a channel feed is shared household-wide for
        min(the follow's interval, 30 min) so every member following it reads one extraction per cycle."""
        is_channel = automation.source_type == "channel"
        url = channel_feed_url(automation.source_url) if is_channel else automation.source_url
        ttl = min((self._next_run(automation.cron_expression, now) - now).total_seconds(), FEED_CACHE_MAX_SECONDS) if is_channel else 0
        with _FEED_CACHE_LOCK:  # One global lock serializes feed extractions; per-url locks if sweeps go parallel. Held across the extraction so a second member waits for it instead of repeating it
            hit = _FEED_CACHE.get(url)
            if is_channel and hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            with provider_budget.priority("background"):
                preview = YtDlpService(self.db).preview(
                    url,
                    lazy_playlist=True,
                    format_selection=FormatSelection(**(automation.format_selection or {})),
                )
            if is_channel:
                _FEED_CACHE[url] = (time.monotonic(), preview)
            return preview

    def _plan_decisions(
        self,
        automation: SourceAutomation,
        user: User,
        preview: PreviewResponse,
        entries: list[PreviewEntry],
        *,
        now: datetime,
        dry_run: bool,
    ) -> list[_PlannedDecision]:
        """Decide every entry in memory, resolving DNS for queue candidates before any write transaction."""
        existing_ids = self._existing_remote_ids([entry.id for entry in entries if entry.id], automation.user_id)
        pending_ids = self._pending_remote_ids(automation.user_id)
        # Stable admission uniqueness: an entry this automation already queued is never
        # queued again (a failed download is retried from Downloads, not re-admitted here).
        pending_ids |= self._attempted_remote_ids(automation, [entry.id for entry in entries if entry.id])
        daily_remaining = self._daily_remaining(automation, now)
        matched_this_run = 0
        limit = automation.max_items_per_run or len(entries)
        backfill_limit = automation.backfill_limit or len(entries)
        planned: list[_PlannedDecision] = []
        # Member access: an owner who may not download only gets matches to review, never queued downloads.
        auto_download = automation.auto_download and streaming_gate.can_download(self.db, self.db.get(User, automation.user_id))

        for entry in entries[:backfill_limit]:
            if dry_run:
                if auto_download:
                    decision_action = "would_queue"
                    reason = "Would queue if this automation runs."
                else:
                    decision_action = "manual"
                    reason = "Would match for manual review without queueing."
            elif auto_download:
                decision_action = "queued"
                reason = "Queued by automation."
            else:
                decision_action = "manual"
                reason = "Matches rules; manual queue mode left it unqueued."
            source_url = entry.webpage_url or automation.source_url

            skip_action, skip_reason = self._skip_reason(entry, automation, existing_ids, pending_ids, now=now)
            if skip_action:
                decision_action, reason = skip_action, skip_reason
            elif matched_this_run >= limit:
                decision_action, reason = "skipped_limit", "Run limit reached."
            elif decision_action != "manual" and daily_remaining is not None and daily_remaining <= 0:
                decision_action, reason = "skipped_limit", "Daily queue limit reached."

            details = {
                "duration": entry.duration,
                "uploader": entry.uploader,
                "thumbnail": entry.thumbnail,
                "published_at": entry.published_at.isoformat() if entry.published_at else None,
                "media_kind": entry.media_kind,
                "source_type": automation.source_type,
                "dry_run": dry_run,
            }
            stage_payload: JobCreateRequest | None = None
            normalized_source_url: str | None = None
            if decision_action in {"queued", "would_queue", "manual"}:
                matched_this_run += 1
                if decision_action != "manual" and daily_remaining is not None:
                    daily_remaining -= 1
                if decision_action == "queued":
                    try:
                        # The DNS-resolving URL check happens here, outside the
                        # write transaction that later stages the job.
                        normalized_source_url = YtDlpService(self.db).validate_source_url(source_url)
                        stage_payload = JobCreateRequest(
                            source_url=source_url,
                            format_selection=FormatSelection(**(automation.format_selection or {})),
                            output_profile=OutputProfile(**(automation.output_profile or {})),
                            preview_snapshot=self._preview_snapshot(preview, entry),
                        )
                        pending_ids.add(entry.id or source_url)
                    except Exception as exc:  # noqa: BLE001
                        decision_action = "failed"
                        reason = str(exc)

            planned.append(
                _PlannedDecision(
                    entry=entry,
                    action=decision_action,
                    reason=reason,
                    details=details,
                    stage_payload=stage_payload,
                    normalized_source_url=normalized_source_url,
                )
            )
        return planned

    def _stage_planned(
        self,
        run: AutomationRun,
        automation: SourceAutomation,
        user: User,
        planned: list[_PlannedDecision],
        decisions: list[AutomationDecision | AutomationDecisionResponse],
    ) -> None:
        """Stage queue candidates and persist decisions inside the caller's write transaction."""
        for item in planned:
            decision_action, reason = item.action, item.reason
            if decision_action == "queued" and item.stage_payload is not None:
                try:
                    job = self.jobs.stage_enqueue(self.db, item.stage_payload, user, normalized_source_url=item.normalized_source_url)
                    self._after_commit(lambda job=job: self.jobs.dispatch_staged(job))
                except Exception as exc:  # noqa: BLE001
                    decision_action = "failed"
                    reason = str(exc)
            decisions.append(self._record_decision(run, automation, user, item.entry, decision_action, reason, item.details, persist=True))
        self._finalize_run(run, decisions, dry_run=False)

    def _finalize_run(
        self,
        run: AutomationRun,
        decisions: list[AutomationDecision | AutomationDecisionResponse],
        *,
        dry_run: bool,
    ) -> None:
        run.matched_count = len([decision for decision in decisions if decision.action in {"queued", "would_queue", "manual"}])
        run.queued_count = len([decision for decision in decisions if decision.action == "queued"])
        run.manual_count = len([decision for decision in decisions if decision.action == "manual"])
        run.failed_count = len([decision for decision in decisions if decision.action == "failed"])
        run.skipped_count = len(decisions) - run.matched_count - run.failed_count
        run.status = "preview" if dry_run else "completed"
        run.finished_at = utcnow()
        run.summary_json = self._summary(run)

    def _persist_channel_artwork(self, automation: SourceAutomation, preview: PreviewResponse) -> None:
        """Cache the resolved channel avatar so Subscriptions can render it without a live check.

        Only called on a successful sweep inspection (a raised exception skips
        straight to the failure branch below, so a failed inspection never
        reaches here and never clears a stored avatar). Writes only when the
        inspection yields a resolvable avatar that differs from what is
        already stored.
        """
        if preview.kind != "playlist":
            return
        resolved_avatar = YtDlpService.resolve_channel_avatar(preview.raw)
        if resolved_avatar and resolved_avatar != automation.artwork_url:
            automation.artwork_url = resolved_avatar

    def _resolve_artwork_url(self, stored_artwork_url: str | None) -> str | None:
        """Map a stored raw provider artwork URL to a servable proxy URL.

        Mirrors app.main._remote_artwork_url / _job_response_with_artwork: the
        raw provider URL stays on the model (artwork registry ids are
        ephemeral and re-registered on serve), and only the serve boundary
        maps it through the registrar. No stored URL, or no resolver
        configured, both mean null rather than leaking the raw URL to a
        frontend that only renders an /api/-prefixed src.
        """
        if not stored_artwork_url or self._artwork_url_resolver is None:
            return None
        return self._artwork_url_resolver(stored_artwork_url)

    def serialize(self, automation: SourceAutomation) -> SourceAutomationResponse:
        return SourceAutomationResponse(
            id=automation.id,
            user_id=automation.user_id,
            label=automation.label,
            source_url=automation.source_url,
            source_type=automation.source_type if automation.source_type in {"playlist", "channel", "search", "generic_url"} else "generic_url",
            artwork_url=self._resolve_artwork_url(automation.artwork_url),
            cron_expression=automation.cron_expression,
            active=automation.active,
            auto_download=automation.auto_download,
            format_selection=automation.format_selection or {},
            output_profile=automation.output_profile or {},
            rules=AutomationRuleSet.model_validate(automation.rules or {}),
            duplicate_policy=automation.duplicate_policy if automation.duplicate_policy in {"skip_same_source", "allow_media_variants"} else "skip_same_source",
            max_items_per_run=automation.max_items_per_run,
            max_items_per_day=automation.max_items_per_day,
            backfill_limit=automation.backfill_limit,
            last_checked_at=automation.last_checked_at,
            next_check_at=automation.next_check_at,
            last_error=automation.last_error,
            last_run_summary=automation.last_run_summary or {},
            feed_entries=[
                PreviewEntry.model_validate({**entry, "artwork_url": self._resolve_artwork_url(entry.get("thumbnail"))})
                for entry in automation.feed_entries or []
            ],
            created_at=automation.created_at,
            updated_at=automation.updated_at,
        )

    def serialize_run(self, run: AutomationRun, *, include_decisions: bool = False) -> AutomationRunResponse:
        decisions: list[AutomationDecisionResponse] = []
        preview_decisions = getattr(run, "decisions_for_preview", None)
        if preview_decisions is not None:
            decisions = [self.serialize_decision(decision) for decision in preview_decisions]
        elif include_decisions:
            decisions = [
                self.serialize_decision(decision)
                for decision in self.db.query(AutomationDecision).filter(AutomationDecision.run_id == run.id).order_by(AutomationDecision.created_at.asc()).all()
            ]
        return AutomationRunResponse(
            id=run.id,
            automation_id=run.automation_id,
            user_id=run.user_id,
            status=run.status if run.status in {"running", "completed", "failed", "preview"} else "failed",
            started_at=run.started_at,
            finished_at=run.finished_at,
            discovered_count=run.discovered_count,
            matched_count=run.matched_count,
            queued_count=run.queued_count,
            manual_count=run.manual_count,
            skipped_count=run.skipped_count,
            failed_count=run.failed_count,
            error=run.error,
            summary_json=run.summary_json or {},
            decisions=decisions,
        )

    @staticmethod
    def serialize_decision(decision: AutomationDecision | AutomationDecisionResponse) -> AutomationDecisionResponse:
        if isinstance(decision, AutomationDecisionResponse):
            return decision
        return AutomationDecisionResponse(
            id=decision.id,
            run_id=decision.run_id,
            automation_id=decision.automation_id,
            remote_id=decision.remote_id,
            source_url=decision.source_url,
            title=decision.title,
            action=decision.action if decision.action in {"queued", "would_queue", "manual", "skipped_duplicate", "skipped_rule", "skipped_limit", "failed"} else "failed",
            reason=decision.reason,
            details=decision.details_json or {},
            created_at=decision.created_at,
        )

    @staticmethod
    def infer_source_type(source_url: str) -> str:
        value = source_url.strip()
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        lowered = value.lower()
        if "ytsearch" in lowered or lowered.startswith("search:"):
            return "search"
        if "youtube.com" in parsed.netloc and parsed.path.startswith("/@"):
            return "channel"
        if query.get("list") or "playlist" in parsed.path:
            return "playlist"
        return "generic_url"

    @staticmethod
    def _next_run(expression: str, base: datetime | None = None) -> datetime:
        SourceAutomationService._validate_cron(expression)
        cursor = (base or utcnow()).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if SourceAutomationService._matches_cron(expression, cursor):
                return cursor
            cursor += timedelta(minutes=1)
        raise ValueError("Cron expression does not yield a future run")

    def _user_automation_defaults(self, user: User) -> UserAutomationDefaults:
        # Read-only on purpose: building an automation (create or preview)
        # must never flush a first-time settings row, whose open write
        # transaction would then span DNS validation or the source preview.
        service = UserSettingsService(self.db)
        return service.resolve_automation_defaults(service.snapshot_for_user(user))

    def _validate_automation(self, automation: SourceAutomation, user: User, *, validate_source: bool = True) -> None:
        self._validate_cron(automation.cron_expression)
        if automation.auto_download and is_kick_host(automation.source_url):
            raise ValueError(KICK_AUTOMATION_UNSUPPORTED)
        if validate_source:
            # Callers that already validated the URL (and may hold pending
            # writes) pass validate_source=False to avoid re-resolving DNS.
            YtDlpService(self.db).validate_source_url(automation.source_url)

    def _candidate_entries(self, preview: PreviewResponse, automation: SourceAutomation) -> list[PreviewEntry]:
        entries = preview.entries if preview.kind == "playlist" else []
        if not entries:
            entries = [
                PreviewEntry(
                    id=str(preview.raw.get("id") or ""),
                    title=preview.title,
                    duration=preview.raw.get("duration") if isinstance(preview.raw.get("duration"), int) else None,
                    thumbnail=preview.raw.get("thumbnail") if isinstance(preview.raw.get("thumbnail"), str) else None,
                    webpage_url=preview.webpage_url,
                    uploader=preview.raw.get("uploader") or preview.raw.get("channel"),
                    availability=preview.availability,
                    published_at=preview.published_at,
                    media_kind=preview.media_kind,
                )
            ]
        return [entry for entry in entries if entry.webpage_url or entry.id][: automation.backfill_limit or 100]

    def _skip_reason(
        self,
        entry: PreviewEntry,
        automation: SourceAutomation,
        existing_ids: set[str],
        pending_ids: set[str],
        *,
        now: datetime,
    ) -> tuple[str | None, str]:
        rules = AutomationRuleSet.model_validate(automation.rules or {})
        remote_key = entry.id or entry.webpage_url or ""
        if automation.duplicate_policy == "skip_same_source" and remote_key and remote_key in existing_ids:
            return "skipped_duplicate", "Already saved in the library."
        if remote_key and remote_key in pending_ids:
            return "skipped_duplicate", "Already pending in the queue."
        hay_title = (entry.title or "").lower()
        hay_uploader = (entry.uploader or "").lower()
        hay_source = " ".join([entry.webpage_url or "", entry.availability or "", automation.source_url]).lower()
        checks = [
            (rules.include_title, hay_title, True, "Title does not match include rule."),
            (rules.exclude_title, hay_title, False, "Title matches exclude rule."),
            (rules.include_uploader, hay_uploader, True, "Uploader does not match include rule."),
            (rules.exclude_uploader, hay_uploader, False, "Uploader matches exclude rule."),
            (rules.include_source, hay_source, True, "Source does not match include rule."),
            (rules.exclude_source, hay_source, False, "Source matches exclude rule."),
        ]
        for terms, haystack, require_match, reason in checks:
            normalized_terms = [term.strip().lower() for term in terms if term.strip()]
            if not normalized_terms:
                continue
            matched = any(term in haystack for term in normalized_terms)
            if (require_match and not matched) or (not require_match and matched):
                return "skipped_rule", reason
        if rules.min_duration is not None and (entry.duration is None or entry.duration < rules.min_duration):
            return "skipped_rule", "Duration is shorter than the minimum."
        if rules.max_duration is not None and entry.duration is not None and entry.duration > rules.max_duration:
            return "skipped_rule", "Duration is longer than the maximum."
        if rules.max_age_days is not None and entry.published_at is not None:
            published_at = entry.published_at
            if published_at.tzinfo is not None:
                published_at = published_at.astimezone(UTC).replace(tzinfo=None)
            run_time = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo is not None else now
            if published_at < run_time - timedelta(days=rules.max_age_days):
                return "skipped_rule", "Published before the automation's maximum age window."
        if rules.media_kind != "any" and entry.media_kind is None:
            return "skipped_rule", "Media kind is unavailable for this entry."
        if rules.media_kind == "audio" and entry.media_kind != "audio":
            return "skipped_rule", "Automation is limited to audio media."
        if rules.media_kind == "video" and entry.media_kind != "video":
            return "skipped_rule", "Automation is limited to video media."
        return None, ""

    def _daily_remaining(self, automation: SourceAutomation, now: datetime) -> int | None:
        if not automation.max_items_per_day:
            return None
        since = now - timedelta(days=1)
        used = (
            self.db.query(AutomationDecision)
            .filter(AutomationDecision.automation_id == automation.id)
            .filter(AutomationDecision.action == "queued")
            .filter(AutomationDecision.created_at >= since)
            .count()
        )
        return max(0, automation.max_items_per_day - used)

    def _existing_remote_ids(self, remote_ids: list[str], user_id: str | None) -> set[str]:
        if not remote_ids:
            return set()
        query = self.db.query(LibraryItem.remote_id).filter(LibraryItem.remote_id.in_(remote_ids))
        if user_id:
            query = query.filter((LibraryItem.visibility == "shared") | (LibraryItem.user_id == user_id) | (LibraryItem.user_id.is_(None)))
        rows = query.all()
        return {remote_id for (remote_id,) in rows if remote_id}

    def _attempted_remote_ids(self, automation: SourceAutomation, remote_ids: list[str]) -> set[str]:
        if not remote_ids:
            return set()
        rows = (
            self.db.query(AutomationDecision.remote_id)
            .filter(
                AutomationDecision.automation_id == automation.id,
                AutomationDecision.action == "queued",
                AutomationDecision.remote_id.in_(remote_ids),
            )
            .all()
        )
        return {remote_id for (remote_id,) in rows if remote_id}

    def _pending_remote_ids(self, user_id: str | None) -> set[str]:
        query = self.db.query(DownloadJob).filter(DownloadJob.status.in_(["queued", "running", "postprocessing"]))
        if user_id:
            query = query.filter(DownloadJob.user_id == user_id)
        pending: set[str] = set()
        for row in query.all():
            snapshot = row.preview_snapshot or {}
            if isinstance(snapshot.get("id"), str):
                pending.add(snapshot["id"])
            if isinstance(snapshot.get("webpage_url"), str):
                pending.add(snapshot["webpage_url"])
        return pending

    @staticmethod
    def _preview_snapshot(preview: PreviewResponse, entry: PreviewEntry) -> dict[str, Any]:
        snapshot = {
            "id": entry.id,
            "title": entry.title,
            "duration": entry.duration,
            "thumbnail": entry.thumbnail,
            "webpage_url": entry.webpage_url,
            "uploader": entry.uploader,
            "availability": entry.availability,
            "published_at": entry.published_at.isoformat() if entry.published_at else None,
            "media_kind": entry.media_kind,
            "playlist_title": preview.title,
            "playlist": preview.title,
            "extractor": preview.extractor,
            "extractor_key": preview.extractor_key,
        }
        if preview.format_resolution is not None:
            snapshot["format_resolution"] = preview.format_resolution.model_dump(mode="json")
        return snapshot

    def _record_decision(
        self,
        run: AutomationRun,
        automation: SourceAutomation,
        user: User,
        entry: PreviewEntry,
        action: str,
        reason: str,
        details: dict[str, Any],
        *,
        persist: bool,
    ) -> AutomationDecision | AutomationDecisionResponse:
        if not persist:
            return AutomationDecisionResponse(
                automation_id=automation.id,
                remote_id=entry.id,
                source_url=entry.webpage_url,
                title=entry.title,
                action=action,  # type: ignore[arg-type]
                reason=reason,
                details=details,
                created_at=utcnow(),
            )
        decision = AutomationDecision(
            id=str(uuid.uuid4()),
            run_id=run.id,
            automation_id=automation.id,
            user_id=user.id,
            remote_id=entry.id,
            source_url=entry.webpage_url,
            title=entry.title,
            action=action,
            reason=reason,
            details_json=details,
        )
        self.db.add(decision)
        self._publish_after_commit(
            "automation_item_decided",
            {"user_id": user.id, "decision": self.serialize_decision(decision).model_dump(mode="json")},
        )
        return decision

    @staticmethod
    def _summary(run: AutomationRun) -> dict[str, Any]:
        return {
            "status": run.status,
            "discovered": run.discovered_count,
            "matched": run.matched_count,
            "queued": run.queued_count,
            "manual": run.manual_count,
            "skipped": run.skipped_count,
            "failed": run.failed_count,
            "error": run.error,
        }

    def _publish_after_commit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.events:
            events = self.events
            self._after_commit(lambda: events.publish(event_type, payload))

    def _notify_failure_after_commit(self, automation: SourceAutomation, error: str) -> None:
        bind = self.db.get_bind()
        notification = SourceAutomation(
            id=automation.id,
            user_id=automation.user_id,
            label=automation.label,
            source_url=automation.source_url,
            source_type=automation.source_type,
            cron_expression=automation.cron_expression,
        )

        def notify() -> None:
            with Session(bind=bind) as notification_db:
                WebhookService(notification_db).notify_automation_error(notification, error)

        self._after_commit(notify)

    def _after_commit(self, action: Callable[[], None]) -> None:
        queue_after_commit(self.db, action)

    @staticmethod
    def _validate_cron(expression: str) -> None:
        fields = expression.strip().split()
        if len(fields) != 5:
            raise ValueError("Cron expression is invalid")
        validators = (
            (fields[0], 0, 59),
            (fields[1], 0, 23),
            (fields[2], 1, 31),
            (fields[3], 1, 12),
            (fields[4], 0, 7),
        )
        for field, minimum, maximum in validators:
            try:
                SourceAutomationService._expand_cron_field(field, minimum, maximum)
            except (TypeError, ValueError):
                raise ValueError("Cron expression is invalid") from None
        # Walk the cron's actual firing minutes over a bounded window instead of
        # judging the minute field alone as if every hour fires: that cyclic
        # shortcut refused rare-but-valid schedules like "0,50 3 * * *" (only
        # fires twice a day, 50 minutes apart) for looking like a 10-minute gap.
        anchor = datetime(2024, 1, 1)  # arbitrary fixed start; only offsets from it matter
        fires = [
            offset for offset in range(CADENCE_WINDOW_MINUTES)
            if SourceAutomationService._matches_cron(expression, anchor + timedelta(minutes=offset))
        ]
        gaps = [later - earlier for earlier, later in zip(fires, fires[1:])]
        if len(fires) > 1 and min(gaps) < MIN_CHECK_INTERVAL_MINUTES:
            raise ValueError(f"Automations can check at most every {MIN_CHECK_INTERVAL_MINUTES} minutes")

    @staticmethod
    def _matches_cron(expression: str, value: datetime) -> bool:
        minute, hour, day, month, weekday = expression.strip().split()
        cron_weekday = (value.weekday() + 1) % 7
        return (
            value.minute in SourceAutomationService._expand_cron_field(minute, 0, 59)
            and value.hour in SourceAutomationService._expand_cron_field(hour, 0, 23)
            and value.day in SourceAutomationService._expand_cron_field(day, 1, 31)
            and value.month in SourceAutomationService._expand_cron_field(month, 1, 12)
            and cron_weekday in SourceAutomationService._expand_cron_field(weekday, 0, 7)
        )

    @staticmethod
    def _expand_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
        values: set[int] = set()
        for part in field.split(","):
            token = part.strip()
            if not token:
                raise ValueError("Empty cron field")
            if token == "*":
                values.update(range(minimum, maximum + 1))
                continue
            if "/" in token:
                base, step_text = token.split("/", 1)
                step = int(step_text)
                if step <= 0:
                    raise ValueError("Invalid step")
                if base == "*":
                    start, end = minimum, maximum
                elif "-" in base:
                    start_text, end_text = base.split("-", 1)
                    start, end = int(start_text), int(end_text)
                else:
                    start, end = int(base), maximum
                if start < minimum or end > maximum or start > end:
                    raise ValueError("Invalid range")
                values.update(range(start, end + 1, step))
                continue
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start < minimum or end > maximum or start > end:
                    raise ValueError("Invalid range")
                values.update(range(start, end + 1))
                continue
            value = int(token)
            if minimum <= value <= maximum:
                values.add(value)
                continue
            raise ValueError("Invalid value")
        if maximum == 7 and 7 in values:
            values.discard(7)
            values.add(0)
        return values
