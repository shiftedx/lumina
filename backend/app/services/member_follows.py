from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from app.models import SourceAutomation, User
from app.services.channel_discovery import channel_display_key, normalize_channel_source_url
from app.services.member_suppressions import MemberSuppressionService, queue_member_changed
from app.services.user_settings import UserSettingsService
from app.services.yt_dlp_service import YtDlpService


# A follow is a channel-type Source automation. This is the one durable
# representation of "the member follows this channel"; #87 does not introduce a
# parallel follow table (follows are Source automations).
CHANNEL_SOURCE_TYPE = "channel"


@dataclass(frozen=True)
class FollowRequest:
    """A caller-supplied intent to follow one channel candidate."""

    source_url: str
    display_name: str


@dataclass(frozen=True)
class PlannedFollow:
    """A validated follow ready to be written inside a durable transaction."""

    channel_key: str
    source_url: str
    display_name: str


@dataclass(frozen=True)
class FollowOutcome:
    channel_key: str
    display_name: str
    status: str  # "created" | "existing" | "invalid"
    automation_id: str | None = None
    reason: str | None = None


class MemberFollowService:
    """Create and read a member's channel follows as Source automations.

    Every follow is an active, member-owned channel automation with automatic
    acquisition disabled, deduped by a stable channel identity so completion
    retries and reloads never create a second automation for one channel.

    The service also exposes the two channel keys #88's Home ranking consumes:
    ``existing_follow_identities`` (the durable, address-based identity used for
    dedup and "already following" recognition) and ``followed_channel_keys``
    (the casefolded display name the Home seam matches against a candidate's
    uploader — see the mapping note on ``followed_channel_keys``).
    """

    def __init__(self, db: Session, *, validate_url: Callable[[str], str] | None = None) -> None:
        self._db = db
        # DNS/network-policy validation is injectable so unit tests stay
        # hermetic; the request path uses the same validator create_automation
        # does, rejecting private or unroutable channel addresses.
        self._validate_url = validate_url or YtDlpService(db).validate_source_url

    def _channel_column(self, member: User, column) -> list[str]:  # noqa: ANN001
        # One column, not whole rows: a follow row carries feed/rules JSON that Home never reads.
        return [
            value
            for (value,) in self._db.query(column)
            .filter(SourceAutomation.user_id == member.id, SourceAutomation.source_type == CHANNEL_SOURCE_TYPE)
            .all()
        ]

    def existing_follow_identities(self, member: User) -> set[str]:
        identities: set[str] = set()
        for source_url in self._channel_column(member, SourceAutomation.source_url):
            identity = normalize_channel_source_url(source_url)
            if identity:
                identities.add(identity)
        return identities

    def followed_channel_keys(self, member: User) -> frozenset[str]:
        """The #88 Home seam keys derived from this member's follows.

        #88 ranks candidates by matching a casefolded *uploader display name*,
        because a durable Popular record carries no channel id. A follow's
        durable identity is its channel address (stable across renames), but the
        only key the Home seam can compare against a candidate is the channel's
        display name. So the seam key is the casefolded follow label, which is
        the channel's display name at follow time. This is honest partial
        matching with two accepted imperfections, both consequences of Popular
        records carrying no channel id (so #88 stays unchanged):
          - Rename lag: after a channel renames, the follow persists
            (address-keyed) while its Home boost lags until the display name
            lines up again.
          - Display-name collision: following channel A also boosts a different
            popular channel that happens to share a followed channel's casefolded display name.
        Both are preferred over inventing a separate, id-based ranking path.
        """
        keys = {channel_display_key(label) for label in self._channel_column(member, SourceAutomation.label)}
        keys.discard("")
        return frozenset(keys)

    def validate_and_plan(
        self, member: User, follows: Iterable[FollowRequest]
    ) -> tuple[list[PlannedFollow], list[FollowOutcome]]:
        """Normalize, dedupe, drop already-followed, and validate — outside any write.

        DNS-resolving validation runs here so it never spans the durable
        follow-creation transaction, matching the source-automation service's
        careful ordering.
        """
        existing = self.existing_follow_identities(member)
        planned: list[PlannedFollow] = []
        outcomes: list[FollowOutcome] = []
        seen: set[str] = set()
        for follow in follows:
            identity = normalize_channel_source_url(follow.source_url)
            display_name = (follow.display_name or "").strip()
            if identity is None or not display_name:
                outcomes.append(
                    FollowOutcome(identity or "", display_name, "invalid", reason="Unusable channel address or name.")
                )
                continue
            if identity in existing:
                outcomes.append(FollowOutcome(identity, display_name, "existing"))
                continue
            if identity in seen:
                continue
            try:
                self._validate_url(identity)
            except Exception as exc:  # noqa: BLE001 - any validation failure is a rejected candidate
                outcomes.append(FollowOutcome(identity, display_name, "invalid", reason=str(exc)))
                continue
            seen.add(identity)
            planned.append(PlannedFollow(channel_key=identity, source_url=identity, display_name=display_name))
        return planned, outcomes

    def create_planned(self, member: User, planned: Iterable[PlannedFollow]) -> list[FollowOutcome]:
        """Write validated follows inside the caller's durable transaction, idempotently.

        Existing identities are re-read here (not just in planning) so a
        concurrent or replayed completion that already created a follow yields
        an ``existing`` outcome instead of a duplicate automation.
        """
        existing = self.existing_follow_identities(member)
        settings = UserSettingsService(self._db)
        defaults = settings.resolve_automation_defaults(settings.snapshot_for_user(member))
        outcomes: list[FollowOutcome] = []
        created: set[str] = set()
        for follow in planned:
            if follow.channel_key in existing or follow.channel_key in created:
                outcomes.append(FollowOutcome(follow.channel_key, follow.display_name, "existing"))
                continue
            automation = self._build_follow(member, follow, defaults)
            self._db.add(automation)
            created.add(follow.channel_key)
            outcomes.append(FollowOutcome(follow.channel_key, follow.display_name, "created", automation_id=automation.id))
        self._db.flush()
        if created:
            # The deliberate positive wins over Show fewer and Don't recommend.
            suppressions = MemberSuppressionService(self._db)
            for follow in planned:
                if follow.channel_key in created:
                    suppressions.clear_channel_feedback(member, channel_key=follow.channel_key, name=follow.display_name)
            queue_member_changed(self._db, member.id)
        return outcomes

    def follow_channels(self, member: User, follows: Iterable[FollowRequest]) -> list[FollowOutcome]:
        """Plan (validate) then create. Convenience for callers not already in a write transaction."""
        planned, outcomes = self.validate_and_plan(member, follows)
        outcomes.extend(self.create_planned(member, planned))
        return outcomes

    def _build_follow(self, member: User, follow: PlannedFollow, defaults) -> SourceAutomation:
        cron_expression = (defaults.cron_expression or "*/30 * * * *").strip()
        return SourceAutomation(
            id=str(uuid.uuid4()),
            user_id=member.id,
            label=follow.display_name[:255] or "Followed channel",
            source_url=follow.source_url,
            source_type=CHANNEL_SOURCE_TYPE,
            cron_expression=cron_expression,
            active=True,
            # Following seeds the household feed; it never acquires media on its
            # own. Automatic acquisition stays disabled on every created follow.
            auto_download=False,
            format_selection=defaults.format_selection.model_dump(),
            output_profile=defaults.output_profile.model_dump(),
            rules=defaults.rules.model_dump(),
            duplicate_policy=defaults.duplicate_policy,
            max_items_per_run=defaults.max_items_per_run,
            max_items_per_day=defaults.max_items_per_day,
            backfill_limit=defaults.backfill_limit,
            # Due now: the scheduler reads the new follow's feed on its next tick.
            next_check_at=None,
            last_error=None,
            last_run_summary={},
        )
