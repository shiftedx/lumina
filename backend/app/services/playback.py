from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from functools import partial

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, defer

from app.media_schemas import TitleSummary
from app.models import LibraryItem, MediaTitle, PlaybackProgress, User, utcnow
from app.persistence import JUST_AHEAD_QUEUED, queue_after_commit
from app.schemas import PlaybackProgressResponse, PlaybackProgressUpdateRequest
from app.services import media_segments
from app.services.library import LibraryService
from app.services.media_artifacts import MediaArtifactService
from app.services.reco.events import DepthStep, RecoEventService, depth_step, library_channel_key, title_event_key


_UNSET = object()
CONTINUE_WATCHING_LIMIT = 24
# Versions of one Media title collapse to one card, so the shelf query over-fetches.
# 2x; a member with many half-watched versions of the same titles can see a short shelf.
CONTINUE_WATCHING_FETCH = CONTINUE_WATCHING_LIMIT * 2


def _queue_just_ahead(db: Session, user_id: str, item_id: str) -> None:
    """After the commit that finishes a titled item, start the member's just-ahead chain.

    Once per session and member, so "mark watched" on a whole series starts one chain (for its first
    episode), not one thread per episode.
    """
    queued = db.info.setdefault(JUST_AHEAD_QUEUED, set())
    if user_id in queued:
        return
    queued.add(user_id)
    from app.services import just_ahead  # local: just_ahead -> titles -> playback

    queue_after_commit(db, partial(just_ahead.on_completed, user_id, item_id))


class PlaybackProgressService:
    """Owns per-user playback state and Continue Watching membership."""

    def __init__(self, db: Session):
        self.db = db
        self.library = LibraryService(db)
        # Strong references keep the JOIN-loaded items alive (the identity map
        # is weak), so serializing a shelf adds no per-row item queries.
        self._loaded_items: dict[str, LibraryItem] = {}

    def get(self, item_id: str, user: User) -> PlaybackProgress | None:
        if self.library.get_item(item_id, user) is None:
            raise ValueError("Library item not found")
        return self._progress_query(user).filter(PlaybackProgress.item_id == item_id).first()

    def update(self, item_id: str, payload: PlaybackProgressUpdateRequest, user: User) -> PlaybackProgress:
        item = self.library.get_item(item_id, user)
        if item is None:
            raise ValueError("Library item not found")

        progress = self._progress_query(user).filter(PlaybackProgress.item_id == item_id).first()
        existing = progress is not None
        was_completed = existing and bool(progress.completed)
        previous_watched = progress.last_watched_at if existing else None
        previous_depth = (progress.max_fraction or 0.0) if existing else 0.0
        now = utcnow()
        if progress is None:
            progress = PlaybackProgress(id=str(uuid.uuid4()), user_id=user.id, item_id=item_id, created_at=now)
            self.db.add(progress)

        duration = payload.duration_seconds if payload.duration_seconds is not None else (item.duration or self._probed_duration(item_id))
        position = max(0, payload.position_seconds)
        progress.position_seconds = position
        progress.duration_seconds = duration
        credits_start = self._credits_start(item_id, position, duration)
        progress.completed = payload.completed or self._position_is_complete(position, duration, credits_start)
        # Watch depth. The same rules the remote service applies (events.depth_step).
        step = depth_step(
            known=existing, last_watched_at=previous_watched, was_completed=was_completed, was_cleared=False, max_fraction=previous_depth,
            completed=progress.completed, position_seconds=position, duration_seconds=duration, now=now,
        )
        progress.max_fraction = step.max_fraction
        progress.plays = (progress.plays or 0) + int(step.play)
        progress.completions = (progress.completions or 0) + int(step.complete)
        progress.last_watched_at = now
        progress.updated_at = now
        progress.dismissed_at = None  # playing again (or marking played) undoes a dismiss
        self.db.flush()
        self._record_reco(item, user, step, now)
        if progress.completed and not was_completed and item.title_id is not None:
            _queue_just_ahead(self.db, user.id, item_id)
        return progress

    def _record_reco(self, item: LibraryItem, user: User, step: DepthStep, now: datetime) -> None:
        """A played YouTube item learns its stable channel key (step 9 only backfilled old items;
        Names are never stored here, only ids), and a titled item writes its play and complete events."""
        if item.channel_key is None:
            item.channel_key = library_channel_key(item.extractor, item.metadata_json or {})
        if item.title_id is None or not (step.play or step.complete):
            return
        key = title_event_key(self.db, item.title_id)
        if key is None:
            return
        events = RecoEventService(self.db)
        if step.play:
            events.record_play(user.id, target_kind="title", item_key=key, channel_key=None, fraction=step.fraction, now=now)
        if step.complete:
            events.record_complete(user.id, target_kind="title", item_key=key, channel_key=None, fraction=1.0, now=now)

    def clear(self, item_id: str, user: User) -> None:
        progress = self.get(item_id, user)
        if progress is not None:
            self.db.delete(progress)
            self.db.flush()

    def dismiss(self, item_id: str, user: User) -> None:
        """Hide an item (every version of its title) from Continue watching and Jellyfin Resume; progress is kept."""
        self._set_dismissed(item_id, user, utcnow())

    def undismiss(self, item_id: str, user: User) -> None:
        self._set_dismissed(item_id, user, None)

    def dismiss_next_up(self, series_id: str, user: User) -> None:
        """Hide a series from Next up by dismissing its anchor: the member's newest checkpoint on a regular-season
        episode version that is completed or has a position (the same row ``resume_anchor`` treats as current)."""
        visible = self.db.scalar(
            select(MediaTitle.id).where(MediaTitle.id == series_id, MediaTitle.type == "series", LibraryService.visible_title_predicate(user))
        )
        if visible is None:
            raise ValueError("Series not found")
        episode, season = aliased(MediaTitle), aliased(MediaTitle)
        anchor = (
            self._progress_query(user)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(episode, episode.id == LibraryItem.title_id)
            .join(season, season.id == episode.parent_id)
            .filter(
                season.parent_id == series_id,
                func.coalesce(season.index_number, 1) != 0,
                or_(PlaybackProgress.completed.is_(True), PlaybackProgress.position_seconds > 0),
                LibraryService.visible_predicate(user),
            )
            .order_by(PlaybackProgress.last_watched_at.desc(), PlaybackProgress.id.desc())
            .first()
        )
        if anchor is None:
            raise ValueError("Nothing in this series to dismiss")
        anchor.dismissed_at = utcnow()
        self.db.flush()

    def _checkpoint(self, item_id: str, user: User) -> PlaybackProgress:
        progress = self.get(item_id, user)  # raises ValueError for an invisible item
        if progress is None:
            raise ValueError("No playback progress for this item")
        return progress

    def _set_dismissed(self, item_id: str, user: User, value: datetime | None) -> None:
        checkpoint = self._checkpoint(item_id, user)
        item = self._loaded_items.get(item_id) or self.library.get_item(item_id, user)
        title_id = item.title_id if item is not None else None
        if title_id is None:
            rows: list[PlaybackProgress] = [checkpoint]
        else:
            # Versions of one Media title share a shelf card, so a dismiss (or its undo) covers all of them.
            rows = (
                self.db.query(PlaybackProgress)
                .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
                .filter(PlaybackProgress.user_id == user.id, LibraryItem.title_id == title_id, LibraryItem.extra_type.is_(None))
                .all()
            )
        for row in rows:
            row.dismissed_at = value
        self.db.flush()

    def list_continue_watching(self, user: User) -> list[PlaybackProgress]:
        # One bounded JOIN; the loaded items stay in the identity map so
        # serialization adds no per-row item queries. Every version of one
        # Media title collapses to its most recent checkpoint.
        rows = (
            self.db.query(PlaybackProgress, LibraryItem)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .options(defer(LibraryItem.metadata_json))
            .filter(
                PlaybackProgress.user_id == user.id,
                PlaybackProgress.completed.is_(False),
                PlaybackProgress.position_seconds > 0,
                PlaybackProgress.dismissed_at.is_(None),
                LibraryItem.status != "missing",
                LibraryService.visible_predicate(user),
            )
            .order_by(
                PlaybackProgress.last_watched_at.desc(),
                PlaybackProgress.updated_at.desc(),
                PlaybackProgress.id.desc(),
            )
            .limit(CONTINUE_WATCHING_FETCH)
            .all()
        )
        self._loaded_items.update({item.id: item for _progress, item in rows})
        self.library.prime_owners(item for _progress, item in rows)
        seen: set[str] = set()
        shelf: list[PlaybackProgress] = []
        for progress, item in rows:
            key = item.title_id or item.id
            if key not in seen:
                seen.add(key)
                shelf.append(progress)
        return shelf[:CONTINUE_WATCHING_LIMIT]

    def serialize(self, progress: PlaybackProgress, user: User, *, title: TitleSummary | None | object = _UNSET) -> PlaybackProgressResponse:
        """``title`` lets a bulk caller (``serialize_continue_watching``) pass its own batch-fetched summary;
        left unset, a titled item's summary is looked up here (one PUT response, one extra query)."""
        item = self._loaded_items.get(progress.item_id)
        if item is None or not self.library.can_view(item, user):
            item = self.library.get_item(progress.item_id, user)
        if item is None:  # The caller only receives visible progress records.
            raise ValueError("Library item not found")
        if title is _UNSET:
            title = None
            if item.title_id is not None:
                from app.services.title_summaries import title_summaries  # local: titles imports playback

                found = title_summaries(self.db, user, [item.title_id])
                title = found[0] if found else None
        return PlaybackProgressResponse(
            id=progress.id,
            user_id=progress.user_id,
            item_id=progress.item_id,
            position_seconds=progress.position_seconds,
            duration_seconds=progress.duration_seconds,
            completed=progress.completed,
            last_watched_at=progress.last_watched_at,
            created_at=progress.created_at,
            updated_at=progress.updated_at,
            item=self.library.serialize(item, user, summary=True),
            title=title,
        )

    def serialize_continue_watching(self, rows: Sequence[PlaybackProgress], user: User) -> list[PlaybackProgressResponse]:
        """The shelf with each row's Media title summary (poster, "S1 · E3"); ``rows`` come from list_continue_watching."""
        from app.services.title_summaries import title_summaries  # local: titles imports playback

        title_of = {row.id: self._loaded_items[row.item_id].title_id for row in rows if row.item_id in self._loaded_items}
        title_ids = list(filter(None, title_of.values()))
        summaries = {summary.id: summary for summary in title_summaries(self.db, user, title_ids)} if title_ids else {}
        return [
            self.serialize(row, user, title=summaries.get(title_of.get(row.id) or ""))
            for row in rows
        ]

    def _progress_query(self, user: User):
        return self.db.query(PlaybackProgress).filter(PlaybackProgress.user_id == user.id)

    def _credits_start(self, item_id: str, position: int, duration: int | None) -> float | None:
        """Credits start, looked up only once playback passes 70% (one row read and one stat)."""
        if not duration or position < duration * media_segments.WATCHED_CHECK_FRACTION:
            return None
        return media_segments.credits_start_seconds(self.db, item_id)

    def _probed_duration(self, item_id: str) -> int | None:
        """Scanned files have no ``item.duration``; their cached ffprobe runtime (the one Jellyfin reports) drives the 95% rule."""
        found = MediaArtifactService(self.db).artifact_for(item_id)
        seconds = (found[0].probe or {}).get("duration") if found else None
        return round(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0 else None

    @staticmethod
    def _position_is_complete(position: int, duration: int | None, credits_start: float | None = None) -> bool:
        if not duration or duration <= 0:
            return False
        if credits_start is not None and credits_start >= duration * media_segments.CREDITS_COMPLETE_MIN_FRACTION and position >= credits_start:
            return True
        return position >= max(1, duration * 0.95)  # no int(): flooring made 3 s of a 4 s file "complete"
