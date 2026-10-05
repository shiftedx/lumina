from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.media_schemas import RECO_KEY
from app.models import MemberRecommendationSuppression, User
from app.persistence import queue_after_commit
from app.services.member_recommendations import _channel_key, _source_key
from app.services.reco import CONSTANTS


# A suppression is scoped to one stable Media-source identity or one stable
# source-channel identity. These are the SAME identities #87/#88/#89 already use
# (``_source_key`` / the casefolded channel key), so a suppressed candidate is
# filtered by its own key on every surface — no parallel identity is invented.
ITEM_SCOPE = "item"
CHANNEL_SCOPE = "channel"
# A Media title suppressed from library-surface recommendations (ADR 0011); target_key is the title id.
TITLE_SCOPE = "title"
# Show fewer from a channel: x0.2, stepping back to normal every 35 days, gone at 140.
FEWER_SCOPE = "fewer"
FEWER_RECOVERY = timedelta(days=CONSTANTS.fewer_step_days * round((1 - CONSTANTS.fewer_floor) / CONSTANTS.fewer_step))
# The rows a new follow of the channel removes.
CHANNEL_FEEDBACK_SCOPES = (CHANNEL_SCOPE, FEWER_SCOPE)

# The reco_events kind each control writes.
FEEDBACK_EVENTS = {ITEM_SCOPE: "not_interested", TITLE_SCOPE: "not_interested", FEWER_SCOPE: "fewer", CHANNEL_SCOPE: "hide_channel"}
_SERVED_KEY = re.compile(RECO_KEY)


@dataclass(frozen=True)
class SuppressedKeys:
    """The member's active suppression identities, as the policy consumes them."""

    item_keys: frozenset[str]
    channel_keys: frozenset[str]
    title_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SuppressionRecord:
    """A single suppression with only safe metadata for identification."""

    id: str
    scope: str  # ITEM_SCOPE | CHANNEL_SCOPE | TITLE_SCOPE | FEWER_SCOPE
    target_key: str
    title: str | None
    channel_name: str | None
    source: str | None
    created_at: datetime
    channel_key: str | None = None  # the stable channel key; target_key keeps the name for 1.8.0

    @property
    def recovers_at(self) -> datetime | None:
        """When Show fewer is back to normal; None for every other scope."""
        return self.created_at + FEWER_RECOVERY if self.scope == FEWER_SCOPE else None


class MemberSuppressionService:
    """Create, list, and restore one Household member's Discovery suppressions.

    Every write is idempotent (re-checked inside the caller's durable
    transaction and backstopped by a per-member unique constraint) and strictly
    member-scoped. The service only writes to its own table: it never removes a
    Library item, erases playback history, or touches a channel Source
    automation, keeping suppression separate from ownership and following.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def suppress_item(
        self,
        member: User,
        *,
        source: str | None,
        item_id: str | None,
        webpage_url: str | None,
        title: str | None,
        uploader: str | None,
        channel_key: str | None = None,
    ) -> SuppressionRecord:
        """Suppress one recommended Media source by its stable identity.

        The identity is derived exactly as the policy derives every candidate
        key. A candidate with no id, address, or title carries no usable
        identity and is rejected rather than stored as a degenerate key.
        """
        if not any(value and value.strip() for value in (item_id, webpage_url, title)):
            raise ValueError("An item suppression needs a source id, address, or title.")
        target_key = _source_key(source, item_id, webpage_url, title, uploader)
        return self._upsert(
            member, ITEM_SCOPE, target_key,
            title=_clean(title), channel_name=_clean(uploader), source=_clean(source), channel_key=channel_key,
        )

    def suppress_channel(
        self, member: User, *, uploader: str | None, source: str | None = None, channel_key: str | None = None,
    ) -> SuppressionRecord:
        """Don't recommend a channel: keyed by its casefolded name (1.8.0 still filters it), with the stable key beside it."""
        return self._upsert(
            member, CHANNEL_SCOPE, _channel_target(uploader, channel_key),
            title=None, channel_name=_clean(uploader), source=_clean(source), channel_key=channel_key,
        )

    def suppress_fewer(
        self, member: User, *, uploader: str | None, source: str | None = None, channel_key: str | None = None, at: datetime,
    ) -> SuppressionRecord:
        """Show fewer from a channel. Pressing it again restarts the 140 days from ``at``."""
        return self._upsert(
            member, FEWER_SCOPE, _channel_target(uploader, channel_key),
            title=None, channel_name=_clean(uploader), source=_clean(source), channel_key=channel_key, at=at,
        )

    def suppress_title(self, member: User, *, title_id: str, name: str | None = None) -> SuppressionRecord:
        """Suppress one Media title from More like this, Home title rows and Jellyfin Suggestions."""
        target_key = (title_id or "").strip()
        if not target_key:
            raise ValueError("A title suppression needs a title id.")
        return self._upsert(member, TITLE_SCOPE, target_key, title=_clean(name), channel_name=None, source=None)

    def _upsert(
        self,
        member: User,
        scope: str,
        target_key: str,
        *,
        title: str | None,
        channel_name: str | None,
        source: str | None,
        channel_key: str | None = None,
        at: datetime | None = None,
    ) -> SuppressionRecord:
        # Re-read at write time (not only in a caller's pre-check) so a replayed
        # or concurrent suppression returns the existing row instead of a
        # duplicate — mirroring #87's follow dedup.
        existing = self._existing(member, scope, target_key)
        if existing is not None:
            return self._refresh(existing, channel_key=channel_key, at=at)
        row = MemberRecommendationSuppression(
            id=str(uuid.uuid4()), user_id=member.id, scope=scope, target_key=target_key,
            title=title, channel_name=channel_name, source=source, channel_key=channel_key,
        )
        if at is not None:
            row.created_at = at
        try:
            # A SAVEPOINT contains the insert so a lost race against the per-member
            # unique constraint rolls back only this insert, never the caller's
            # transaction. The writer-admission lock normally serializes writes so
            # the re-read above already dedups; this is the defense-in-depth path
            # that keeps the write idempotent (return the winner) instead of
            # surfacing an IntegrityError as a 500.
            with self._db.begin_nested():
                self._db.add(row)
                self._db.flush()
        except IntegrityError:
            winner = self._existing(member, scope, target_key)
            if winner is None:
                raise
            return self._refresh(winner, channel_key=channel_key, at=at)
        return self._record(row)

    def _refresh(self, row: MemberRecommendationSuppression, *, channel_key: str | None, at: datetime | None) -> SuppressionRecord:
        """A repeated control: Show fewer restarts its clock; a row first written by 1.8.0 learns its stable key."""
        changed = False
        if at is not None and row.created_at != at:
            row.created_at, changed = at, True
        if channel_key and not row.channel_key:
            row.channel_key, changed = channel_key, True
        if changed:
            self._db.flush()
        return self._record(row)

    def _existing(self, member: User, scope: str, target_key: str) -> MemberRecommendationSuppression | None:
        return (
            self._db.query(MemberRecommendationSuppression)
            .filter_by(user_id=member.id, scope=scope, target_key=target_key)
            .one_or_none()
        )

    def list_for(self, member: User) -> list[SuppressionRecord]:
        rows = (
            self._db.query(MemberRecommendationSuppression)
            .filter(MemberRecommendationSuppression.user_id == member.id)
            .order_by(MemberRecommendationSuppression.created_at.desc(), MemberRecommendationSuppression.id)
            .all()
        )
        return [self._record(row) for row in rows]

    def get(self, member: User, suppression_id: str) -> SuppressionRecord | None:
        """One of the member's own rows (the restore event needs it before the delete); never another member's."""
        row = (
            self._db.query(MemberRecommendationSuppression)
            .filter(MemberRecommendationSuppression.user_id == member.id, MemberRecommendationSuppression.id == suppression_id)
            .one_or_none()
        )
        return self._record(row) if row is not None else None

    def restore(self, member: User, suppression_id: str) -> bool:
        """Remove one member-owned suppression, making the target eligible again.

        Returns whether a row was removed. Another member's suppression is never
        touched, and a missing id is a no-op (idempotent restore).
        """
        deleted = (
            self._db.query(MemberRecommendationSuppression)
            .filter(
                MemberRecommendationSuppression.user_id == member.id,
                MemberRecommendationSuppression.id == suppression_id,
            )
            .delete(synchronize_session=False)
        )
        return bool(deleted)

    def clear_channel_feedback(self, member: User, *, channel_key: str | None, name: str | None) -> int:
        """Following a channel removes its Show fewer and Don't recommend rows; returns how many.

        Matched by the stable key, or by the casefolded name for rows that carry none. Two channels with the
        same name share a name key, so following one also clears a name-only row of the other.
        """
        name_key = _channel_key(name)
        matches = [MemberRecommendationSuppression.channel_key == channel_key] if channel_key else []
        if name_key:
            matches.append(MemberRecommendationSuppression.target_key == name_key)
        if not matches:
            return 0
        return (
            self._db.query(MemberRecommendationSuppression)
            .filter(
                MemberRecommendationSuppression.user_id == member.id,
                MemberRecommendationSuppression.scope.in_(CHANNEL_FEEDBACK_SCOPES),
                or_(*matches),
            )
            .delete(synchronize_session=False)
        )

    def active_keys(self, member: User) -> SuppressedKeys:
        """The member's suppression identities, for the shared policy's filter."""
        rows = (
            self._db.query(MemberRecommendationSuppression.scope, MemberRecommendationSuppression.target_key)
            .filter(MemberRecommendationSuppression.user_id == member.id)
            .all()
        )
        by_scope = lambda scope: frozenset(key for row_scope, key in rows if row_scope == scope)  # noqa: E731
        return SuppressedKeys(item_keys=by_scope(ITEM_SCOPE), channel_keys=by_scope(CHANNEL_SCOPE), title_keys=by_scope(TITLE_SCOPE))

    @staticmethod
    def _record(row: MemberRecommendationSuppression) -> SuppressionRecord:
        return SuppressionRecord(
            id=row.id, scope=row.scope, target_key=row.target_key,
            title=row.title, channel_name=row.channel_name, source=row.source, created_at=row.created_at,
            channel_key=row.channel_key,
        )


def _clean(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


def _channel_target(uploader: str | None, channel_key: str | None) -> str:
    """The row's target_key for channel and fewer: the casefolded name (1.8.0 matches on it), else the stable key."""
    target_key = _channel_key(uploader) or (channel_key or "")
    if not target_key:
        raise ValueError("A channel suppression needs a channel identity.")
    return target_key


def feedback_event_key(
    record: SuppressionRecord, *, key: str | None, source: str | None, item_id: str | None, webpage_url: str | None,
) -> str:
    """A feedback event's item key: the served key the card echoed, the title id, the video's served key,
    else a hash of the row's target. Client text is never stored raw."""
    from app.services.reco.policy import remote_key  # policy -> profile -> this module

    if key and _SERVED_KEY.fullmatch(key):
        return key
    if record.scope == TITLE_SCOPE:
        return record.target_key
    if record.scope == ITEM_SCOPE and (served := remote_key(source, item_id, webpage_url)):
        return served
    return hashlib.sha256(record.target_key.encode()).hexdigest()


def queue_member_changed(db: Session, member_id: str, refresher=None) -> None:  # noqa: ANN001 - RecoRefresher | None
    """A member's signals changed (feedback, a follow, interests). After the commit: rebuild their profile, drop every
    cached list of theirs and nudge their pool."""
    from app.services.reco.events import generations, served_lists  # events -> models only; keep the graph light

    queue_after_commit(db, lambda: generations.bump(member_id))
    queue_after_commit(db, lambda: served_lists.drop(member_id))
    if refresher is not None:
        queue_after_commit(db, lambda: refresher.trigger(member_id))
