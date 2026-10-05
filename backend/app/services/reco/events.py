"""Recommendation events and watch depth: channel keys, the play-session rules,
the served-list cache and the event store. Every read and write here is scoped to one member."""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.media_schemas import RecoEventBatch, RecoSurface
from app.models import MediaTitle, RecoEvent, RecoPool, User
from app.persistence import queue_after_commit
from app.services.channel_discovery import canonical_channel_identity, normalize_channel_source_url
from app.services.reco import CONSTANTS, EventKind, Ranked, ServedList, TargetKind

if TYPE_CHECKING:  # routers.profile_export imports this module, so the model is imported inside export()
    from app.routers.profile_export import ExportRecoEvent

ATTRIBUTION_WINDOW = timedelta(minutes=30)
PLAY_SESSION_GAP = timedelta(minutes=30)
RESTART_FRACTION = 0.1
SHORT_RETENTION = timedelta(days=35)  # impression, open
LONG_RETENTION = timedelta(days=400)  # every other kind
DAILY_EVENT_CAP = 2000
SWEEP_BATCH = 2000

CHANNEL_PREFIX = "https://www.youtube.com/channel/"
KEY_MAX = 255  # the width of every channel_key column
NAME_MAX = 200
_UC_ID = re.compile(r"UC[0-9A-Za-z_-]{22}")
_UC_IN_URL = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")
_CHANNEL_PATH = re.compile(r"^/(?:channel/|@|c/|user/)", re.IGNORECASE)


def name_key(extractor: str | None, uploader: str | None) -> str | None:
    """'name:{extractor family}:{casefolded uploader}', the legacy fallback; None without an uploader."""
    name = (uploader or "").strip().casefold()[:NAME_MAX]
    if not name:
        return None
    family = (extractor or "").split(":", 1)[0].strip().casefold() or "web"
    return f"name:{family}:{name}"


def _is_channel_address(address: str) -> bool:
    """A YouTube address names a channel only through /channel/, /@, /c/ or /user/; a watch URL is not a channel."""
    parts = urlsplit(address)
    return not (parts.hostname or "").endswith("youtube.com") or bool(_CHANNEL_PATH.match(parts.path))


def channel_key(extractor: str | None, channel_id: str | None, channel_url: str | None, uploader: str | None) -> str | None:
    """Canonical_channel_identity for a UC id, else the normalized channel address, else the name key."""
    identity = canonical_channel_identity(None, channel_id) if isinstance(channel_id, str) and _UC_ID.fullmatch(channel_id) else None
    if identity:
        return identity
    address = normalize_channel_source_url(channel_url)
    if address is not None and len(address) <= KEY_MAX and _is_channel_address(address):
        return address
    return name_key(extractor, uploader)


def library_channel_key(extractor: str | None, metadata: Mapping[str, object]) -> str | None:
    """The stable key of a saved YouTube item, tried exactly as schema step 9 backfills it (channel_id, uploader_id,
    then the /channel/UC… segment of channel_url, uploader_url); None when none holds a 24-character UC id."""
    if not (extractor or "").casefold().startswith("youtube"):
        return None
    for field in ("channel_id", "uploader_id"):
        value = str(metadata.get(field) or "")
        if _UC_ID.fullmatch(value):
            return CHANNEL_PREFIX + value
    for field in ("channel_url", "uploader_url"):
        found = _UC_IN_URL.search(str(metadata.get(field) or ""))
        if found:
            return CHANNEL_PREFIX + found.group(1)
    return None


def is_skip(*, position_seconds: float, max_fraction: float, completions: int, last_watched_at: datetime, now: datetime) -> bool:
    """A skip is derived, never stored. A checkpoint that stayed shallow, was never completed and has gone quiet."""
    return (
        max_fraction < CONSTANTS.skip_fraction
        and completions == 0
        and position_seconds < CONSTANTS.skip_seconds
        and now - last_watched_at > timedelta(minutes=CONSTANTS.skip_settle_minutes)
    )


@dataclass(frozen=True)
class DepthStep:
    fraction: float  # this checkpoint's position / duration (1.0 when completed)
    max_fraction: float  # the deepest the member has been; never decreases
    play: bool  # a new play session starts at this checkpoint
    complete: bool  # completed turned true at this checkpoint


def _fraction(position_seconds: float, duration_seconds: float | None) -> float:
    if not duration_seconds or duration_seconds <= 0:
        return 0.0
    return min(1.0, max(0.0, position_seconds / duration_seconds))


def depth_step(
    *, known: bool, last_watched_at: datetime | None, was_completed: bool, was_cleared: bool, max_fraction: float,
    completed: bool, position_seconds: float, duration_seconds: float | None, now: datetime,
) -> DepthStep:
    """Shared by the local and the remote progress service. ``known`` is whether a row existed before this
    checkpoint. A play starts on the first checkpoint, after a gap over 30 minutes, or when a completed or cleared row
    restarts below 10%. A completion is the checkpoint where ``completed`` turns true."""
    fraction = 1.0 if completed else _fraction(position_seconds, duration_seconds)
    restarted = (was_completed or was_cleared) and fraction < RESTART_FRACTION
    gap = last_watched_at is not None and now - last_watched_at > PLAY_SESSION_GAP
    return DepthStep(
        fraction=fraction, max_fraction=max(max_fraction, fraction), play=(not known) or gap or restarted,
        complete=completed and not was_completed,
    )


def title_event_key(db: Session, title_id: str) -> str | None:
    """Title targets: the top-level title id, the movie, or the series of an episode (or season).
    Albums, artists and boxsets are not recommended and give None."""
    title = db.get(MediaTitle, title_id)
    for _ in range(3):  # episode -> season -> series
        if title is None:
            return None
        if title.type in ("movie", "series"):
            return title.id
        title = db.get(MediaTitle, title.parent_id) if title.type in ("episode", "season") and title.parent_id else None
    return None


class MemberGenerations:
    """One counter per member, bumped by every write that changes what ranking knows about them.
    ``profile.py`` rebuilds a member's profile when the counter has moved. In-process; the household is a handful of members."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def get(self, user_id: str) -> int:
        with self._lock:
            return self._counts.get(user_id, 0)

    def bump(self, user_id: str) -> None:
        with self._lock:
            self._counts[user_id] = self._counts.get(user_id, 0) + 1


generations = MemberGenerations()


class ServedListCache:
    """The lists the server actually served, kept so events and repeat requests read exactly what the member saw.

    TTL 30 minutes, LRU 512, thread-safe. ``get`` and ``find`` answer only for the member that owns the list: that is
    what stops a client attributing events to somebody else's list id.

    ``find`` scans the whole cache (512 entries at most); index by member if the cache ever grows.
    """

    def __init__(self, ttl: timedelta | None = None, size: int = CONSTANTS.list_cache_size) -> None:
        self._ttl = ttl if ttl is not None else timedelta(minutes=CONSTANTS.list_ttl_minutes)
        self._size = size
        self._lock = threading.Lock()
        self._lists: OrderedDict[str, ServedList] = OrderedDict()

    def put(self, served: ServedList) -> None:
        with self._lock:
            self._lists[served.list_id] = served
            self._lists.move_to_end(served.list_id)
            while len(self._lists) > self._size:
                self._lists.popitem(last=False)

    def get(self, user_id: str, list_id: str, now: datetime) -> ServedList | None:
        with self._lock:
            served = self._lists.get(list_id)
            if served is None or served.user_id != user_id:
                return None
            if now - served.created_at >= self._ttl:
                del self._lists[list_id]
                return None
            self._lists.move_to_end(list_id)
            return served

    def find(self, user_id: str, surface: RecoSurface, context_key: str, now: datetime) -> ServedList | None:
        """The newest unexpired list by ``created_at``; LRU order is not creation order once ``get`` touches a list."""
        with self._lock:
            newest: ServedList | None = None
            for served in list(self._lists.values()):
                if served.user_id != user_id or served.surface != surface or served.context_key != context_key:
                    continue
                if now - served.created_at >= self._ttl:
                    del self._lists[served.list_id]
                elif newest is None or served.created_at > newest.created_at:
                    newest = served
            if newest is not None:
                self._lists.move_to_end(newest.list_id)
            return newest

    def drop(self, user_id: str, surface: RecoSurface | None = None) -> None:
        with self._lock:
            for list_id in [key for key, served in self._lists.items() if served.user_id == user_id and surface in (None, served.surface)]:
                del self._lists[list_id]

    def clear(self) -> None:
        with self._lock:
            self._lists.clear()


served_lists = ServedListCache()


_EPOCH = datetime(1970, 1, 1)
SHORT_KINDS = ("impression", "open")
FEEDBACK_KINDS = ("not_interested", "fewer", "hide_channel", "restore")


class _DropCounter:
    """Dropped events (unknown list, over the cap) in hourly buckets, so a flood of random list ids costs 25 integers.

    In-process, so a restart zeroes Diagnostics' ``dropped_events_24h``; persist it only if that ever matters.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hours: dict[int, int] = {}

    @staticmethod
    def _hour(at: datetime) -> int:
        return int((at - _EPOCH).total_seconds() // 3600)

    def add(self, now: datetime, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            hour = self._hour(now)
            self._hours[hour] = self._hours.get(hour, 0) + count
            for stale in [key for key in self._hours if key <= hour - 24]:
                del self._hours[stale]

    def last_24h(self, now: datetime) -> int:
        with self._lock:
            hour = self._hour(now)
            return sum(count for key, count in self._hours.items() if hour - 24 < key <= hour)

    def reset(self) -> None:
        with self._lock:
            self._hours.clear()


_drops = _DropCounter()


def _context(served: ServedList | None, key: str) -> Ranked | None:
    return next((ranked for ranked in served.items if ranked.candidate.key == key), None) if served is not None else None


def _row(member_id: str, kind: str, at: datetime, *, target_kind: str, item_key: str, channel_key: str | None, fraction: float | None = None,
         served: ServedList | None = None, ranked: Ranked | None = None) -> RecoEvent:
    """One event row; the context columns come from what was served, never from the client."""
    return RecoEvent(
        user_id=member_id, at=at, kind=kind, target_kind=target_kind, item_key=item_key, channel_key=channel_key, fraction=fraction,
        surface=served.surface if served is not None else None, list_id=served.list_id if served is not None else None,
        position=ranked.position if ranked is not None else None, slot=ranked.slot if ranked is not None else None,
        p_shown=ranked.p_shown if ranked is not None else None, reason_code=ranked.reason_code if ranked is not None else None,
    )


class RecoEventService:
    """The only writer of ``reco_events``. Every method is scoped to one member and runs inside the caller's write transaction."""

    def __init__(self, db: Session, lists: ServedListCache = served_lists) -> None:
        self.db = db
        self.lists = lists

    # -- client events ------------------------------------------------------------------------------------

    def record_client(self, member: User, batch: RecoEventBatch, *, now: datetime) -> int:
        """Store the client's impressions and opens against the lists this member was actually served. Returns rows stored."""
        resolved: list[tuple[object, ServedList, Ranked]] = []
        dropped = 0
        for event in batch.events:
            served = self.lists.get(member.id, event.list_id, now)
            ranked = _context(served, event.key)
            if served is None or ranked is None:
                dropped += 1  # unknown, expired, another member's list, or a key that list never held
            else:
                resolved.append((event, served, ranked))
        seen = self._stored_impressions(member.id, [(event.list_id, event.key) for event, _, _ in resolved if event.kind == "impression"])
        fresh = []
        for event, served, ranked in resolved:
            if event.kind == "impression":
                if (event.list_id, event.key) in seen:
                    continue  # a retried beacon, or the same card seen twice
                seen.add((event.list_id, event.key))
            fresh.append((event, served, ranked))
        room = max(0, DAILY_EVENT_CAP - self._stored_today(member.id, now))
        dropped += max(0, len(fresh) - room)
        rows = [
            _row(member.id, event.kind, now - timedelta(milliseconds=event.age_ms), target_kind=ranked.candidate.target_kind,
                 item_key=ranked.candidate.key, channel_key=ranked.candidate.channel_key, served=served, ranked=ranked)
            for event, served, ranked in fresh[:room]
        ]
        self.db.add_all(rows)
        self.db.flush()
        _drops.add(now, dropped)
        return len(rows)

    def _stored_impressions(self, member_id: str, pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
        if not pairs:
            return set()
        found = self.db.execute(
            select(RecoEvent.list_id, RecoEvent.item_key).where(
                RecoEvent.user_id == member_id, RecoEvent.kind == "impression",
                RecoEvent.item_key.in_({key for _, key in pairs}), RecoEvent.list_id.in_({list_id for list_id, _ in pairs}),
            )
        ).all()
        return {(list_id, key) for list_id, key in found}

    def _stored_today(self, member_id: str, now: datetime) -> int:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.db.scalar(select(func.count()).select_from(RecoEvent).where(RecoEvent.user_id == member_id, RecoEvent.at >= day)) or 0

    # -- server events -------------------------------------------------------------------------------

    def record_play(self, member_id: str, *, target_kind: TargetKind, item_key: str, channel_key: str | None, fraction: float, now: datetime) -> None:
        self._session_event("play", member_id, target_kind, item_key, channel_key, fraction, now)

    def record_complete(self, member_id: str, *, target_kind: TargetKind, item_key: str, channel_key: str | None, fraction: float, now: datetime) -> None:
        self._session_event("complete", member_id, target_kind, item_key, channel_key, fraction, now)
        # After the commit: a profile rebuilt before it would read the old rows and cache them under the new generation.
        queue_after_commit(self.db, partial(generations.bump, member_id))

    def _session_event(self, kind: str, member_id: str, target_kind: str, item_key: str, channel_key: str | None, fraction: float, now: datetime) -> None:
        """One event per member, kind and key per 30 minutes: a series marked watched is one pair, not three hundred.
        The progress rows keep the exact counts either way."""
        recent = self.db.scalar(
            select(func.count()).select_from(RecoEvent).where(
                RecoEvent.user_id == member_id, RecoEvent.item_key == item_key, RecoEvent.kind == kind, RecoEvent.at > now - PLAY_SESSION_GAP,
            )
        )
        if recent:
            return
        row = _row(member_id, kind, now, target_kind=target_kind, item_key=item_key, channel_key=channel_key, fraction=fraction)
        opened = self.db.scalars(
            select(RecoEvent).where(
                RecoEvent.user_id == member_id, RecoEvent.item_key == item_key, RecoEvent.kind == "open", RecoEvent.at >= now - ATTRIBUTION_WINDOW,
            ).order_by(RecoEvent.at.desc()).limit(1)
        ).first()
        if opened is not None:
            row.surface, row.list_id, row.position, row.slot, row.p_shown, row.reason_code = (
                opened.surface, opened.list_id, opened.position, opened.slot, opened.p_shown, opened.reason_code,
            )
        self.db.add(row)
        self.db.flush()

    def record_feedback(
        self, member_id: str, kind: EventKind, *, target_kind: TargetKind, item_key: str, channel_key: str | None, list_id: str | None, now: datetime,
    ) -> None:
        """A suppression control was used. The list id is the client's claim: only the member's own cached list is read."""
        if kind not in FEEDBACK_KINDS:
            raise ValueError(f"{kind} is not a feedback event")
        served = self.lists.get(member_id, list_id, now) if list_id else None
        self.db.add(_row(member_id, kind, now, target_kind=target_kind, item_key=item_key, channel_key=channel_key,
                         served=served, ranked=_context(served, item_key)))
        self.db.flush()
        # Feedback invalidates what the member was shown: that surface, or every list when the list is unknown.
        queue_after_commit(self.db, partial(self.lists.drop, member_id, served.surface if served is not None else None))

    # -- retention, erase, export, diagnostics -----------------------------------------------------------------------

    def sweep(self, now: datetime, *, batch: int = SWEEP_BATCH) -> int:
        """Retention: impressions and opens after 35 days, everything else after 400. At most ``batch`` rows per call."""
        deleted = self._delete_older(now - SHORT_RETENTION, batch, RecoEvent.kind.in_(SHORT_KINDS))
        if deleted < batch:
            deleted += self._delete_older(now - LONG_RETENTION, batch - deleted, None)
        return deleted

    def _delete_older(self, cutoff: datetime, limit: int, kinds) -> int:  # noqa: ANN001
        query = select(RecoEvent.id).where(RecoEvent.at < cutoff)
        if kinds is not None:
            query = query.where(kinds)
        ids = self.db.scalars(query.order_by(RecoEvent.at).limit(limit)).all()
        if ids:
            self.db.execute(delete(RecoEvent).where(RecoEvent.id.in_(ids)))
        return len(ids)

    def clear(self, member_id: str) -> None:
        """Settings -> Clear recommendation history: the member's events and pool, their cached lists, their profile."""
        self.db.execute(delete(RecoEvent).where(RecoEvent.user_id == member_id))
        self.db.execute(delete(RecoPool).where(RecoPool.user_id == member_id))
        queue_after_commit(self.db, partial(self.lists.drop, member_id))
        queue_after_commit(self.db, partial(generations.bump, member_id))

    def export(self, member_id: str, *, limit: int = 5000) -> "list[ExportRecoEvent]":
        from app.routers.profile_export import ExportRecoEvent  # local: profile_export imports this module

        found = self.db.scalars(
            select(RecoEvent).where(RecoEvent.user_id == member_id).order_by(RecoEvent.at.desc(), RecoEvent.id.desc()).limit(limit)
        )
        return [
            ExportRecoEvent(
                at=row.at, kind=row.kind, surface=row.surface, target_kind=row.target_kind, item_key=row.item_key, position=row.position,
                slot=row.slot, reason_code=row.reason_code, fraction=row.fraction,
            )
            for row in found
        ]

    def dropped_24h(self, now: datetime) -> int:
        """Events dropped for an unknown list or over the daily cap in the last 24 hours."""
        return _drops.last_24h(now)
