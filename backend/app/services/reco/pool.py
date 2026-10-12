"""Candidate sources for recommendations.

Remote candidates live in two tables. ``remote_media`` is a household cache of public video metadata with no member
column; ``reco_pool`` says which of one member's own sources nominated which video. A member's candidates come only from
their own pool rows, the household Popular snapshot, Library titles visible to them and the listing of the channel they
are watching. No provider call and no embedding call happens on a request thread: ``RecoRefresher`` makes the
provider calls on one background worker, and the embedder fills ``remote_media.vector`` (embeddings.py).
"""
from __future__ import annotations

import functools
import heapq
import logging
import math
import random
import re
import threading
import weakref
from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import batched, islice
from typing import NamedTuple, TypeVar
from urllib.parse import urlsplit

from sqlalchemy import String, delete, func, or_, select, type_coerce, update
from sqlalchemy.orm import Session

from app.models import AppSession, LibraryItem, MediaTitle, PlaybackProgress, RecoPool, RemoteMedia, RemotePlaybackProgress, SourceAutomation, User, utcnow
from app.persistence import write_transaction
from app.schemas import YouTubeSearchResult
from app.services.library import LibraryService
from app.services.member_follows import CHANNEL_SOURCE_TYPE
from app.services.member_recommendations import _UNPLAYABLE_AVAILABILITY, TITLE_POOL_TYPES, MemberRecommendationPolicy
from app.services import provider_budget
from app.services.popular_discovery import POPULAR_CATEGORIES, PopularItem
from app.services.reco import (
    CONSTANTS, SOURCE_CHANNEL, SOURCE_CURRENT_CHANNEL, SOURCE_FOLLOW, SOURCE_HISTORY, SOURCE_INTEREST, SOURCE_LIBRARY, SOURCE_POPULAR, SOURCE_SEED,
    Candidate, MemberProfile, enabled,
)
from app.services.reco import events as reco_events
from app.services.reco import profile as reco_profile
from app.services.remote_annotation import remote_source_identity
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.yt_dlp_service import SearchBusyError
from app.services.youtube_channels import ChannelPages, ChannelTabPage

logger = logging.getLogger(__name__)

# ---- constants ----
# Verbatim.
POOL_CAPS = {SOURCE_FOLLOW: 160, SOURCE_CHANNEL: 100, SOURCE_SEED: 80, SOURCE_INTEREST: 40}
REFRESH_AGE = timedelta(hours=6)
ACTIVE_WINDOW = timedelta(days=3)
TRIGGER_DEBOUNCE = timedelta(minutes=10)
TRIGGER_MIN_GAP = timedelta(hours=1)
PRUNE_AFTER = timedelta(days=21)
REMOTE_MEDIA_SWEEP_AFTER = timedelta(days=14)
HISTORY_ANCHORS = 100
CHANNEL_LISTINGS_PER_REFRESH, SEED_SEARCHES_PER_REFRESH, INTEREST_SEARCHES_PER_REFRESH, SEEDS = 10, 3, 1, 4
SEED_REEXPAND = timedelta(days=7)
PROVIDER_SPACING_SECONDS, PROVIDER_JITTER = 15.0, 0.2
CALLS_PER_HOUR, CALLS_PER_DAY = 40, 300
BUSY_DEFER = timedelta(minutes=5)
BUDGET_DEFER = timedelta(minutes=15)  # the household YouTube budget is spent or paused

# Local to this module.
POOL_SWEEP_BATCH = 2000
FAILURES_BEFORE_BACKOFF = 3  # a dead channel is a skipped call; three in a row look like an outage
BACKOFF_BASE_SECONDS, BACKOFF_MAX_SECONDS, RETRY_AFTER_MAX_SECONDS = 60.0, 3600.0, 6 * 3600.0
LISTING_LIMIT = 60  # the tab limit Up Next peeks
LISTING_KEEP = 10  # newest videos kept from one listing
SEARCH_LIMIT = 20
FOLLOW_ENTRIES = 12
SWEEP_EVERY = timedelta(days=1)
CHANNEL_PREFIX = "https://www.youtube.com/channel/"
_PHRASES = {category.key: category.query for category in POPULAR_CATEGORIES}  # fixed phrases, not member text
MIN_DURATION_SECONDS = 60  # Shorter videos are shorts
TITLE_MAX = 300  # a provider's title is untrusted input: bounded before it is stored
URL_MAX = 2048
TOKENS_MAX = 40
CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


# ---- media facts ----
@dataclass(frozen=True)
class Media:
    """One remote video's public facts. ``eligible`` is False for a short, a live stream or an unplayable video."""

    key: str
    source_identity: str
    extractor: str | None
    remote_id: str | None
    webpage_url: str
    title: str | None
    uploader: str | None
    channel_key: str | None
    channel_url: str | None
    duration: int | None
    view_count: int | None
    published_at: datetime | None
    kind: str | None
    category_keys: tuple[str, ...]
    thumbnail: str | None
    availability: str | None
    eligible: bool


@dataclass(frozen=True)
class Nomination:
    media: Media
    source: int  # one SOURCE_* bit
    seed_ref: str | None = None  # the member's own row id or interest key; never text


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _naive_utc(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _identity(provider: str | None, entry_id: str | None, webpage_url: str | None) -> tuple[str, str] | None:
    """(remote_media key, canonical identity), keyed exactly as RemotePlaybackProgressService keys progress rows."""
    identity = remote_source_identity(provider, entry_id, webpage_url)
    if not identity:
        return None
    try:
        canonical = RemotePlaybackProgressService.canonical_source_identity(identity)
    except ValueError:  # credentials in the address, a control character, an unsupported scheme
        return None
    return RemotePlaybackProgressService.source_identity_key(canonical), canonical


def _media(
    provider: str, entry_id: str | None, webpage_url: str | None, *, title: str | None, uploader: str | None, uploader_id: str | None,
    uploader_url: str | None, duration: int | None, view_count: int | None, published_at: datetime | None, kind: str | None,
    lifecycle: str | None, availability: str | None, thumbnail: str | None, category_keys: Sequence[str] = (),
) -> Media | None:
    found = _identity(provider, entry_id, webpage_url)
    if found is None or not webpage_url or len(webpage_url) > URL_MAX:
        return None
    parts = urlsplit(webpage_url)
    if parts.username or parts.password:  # remote_source_identity strips credentials from the identity; the stored address must not keep them
        return None
    key, canonical = found
    short = kind == "short" or "/shorts/" in webpage_url or (duration is not None and duration < MIN_DURATION_SECONDS)
    live = kind == "live" or lifecycle in ("live", "upcoming")
    unplayable = (availability or "").strip().casefold() in _UNPLAYABLE_AVAILABILITY
    extractor, remote_id = (None, None) if canonical.startswith("url:") else canonical.split(":", 1)
    channel_id = uploader_id if uploader_id and CHANNEL_ID.fullmatch(uploader_id) else None  # a handle is not a stable id
    return Media(
        key=key, source_identity=canonical, extractor=extractor, remote_id=remote_id, webpage_url=webpage_url,
        title=title[:TITLE_MAX] if title else None, uploader=uploader[:TITLE_MAX] if uploader else None,
        channel_key=reco_events.channel_key(provider, channel_id, uploader_url, uploader), channel_url=uploader_url,
        duration=duration, view_count=view_count, published_at=_naive_utc(published_at),
        kind="short" if short else "live" if live else "video", category_keys=tuple(category_keys), thumbnail=thumbnail,
        availability=availability, eligible=not (short or live or unplayable),
    )


def media_from_result(result: YouTubeSearchResult, category_keys: Sequence[str] = ()) -> Media | None:
    return _media(
        result.source or "youtube", result.id, result.webpage_url, title=result.title, uploader=result.uploader,
        uploader_id=result.uploader_id, uploader_url=result.uploader_url, duration=result.duration, view_count=result.view_count,
        published_at=result.published_at, kind=result.kind, lifecycle=result.capabilities.lifecycle if result.capabilities else None,
        availability=result.availability, thumbnail=result.thumbnail, category_keys=category_keys,
    )


def media_from_popular(item: PopularItem) -> Media | None:
    return _media(
        item.source or "youtube", item.id, item.webpage_url, title=item.title, uploader=item.uploader, uploader_id=item.uploader_id,
        uploader_url=item.uploader_url, duration=item.duration, view_count=item.view_count, published_at=item.published_at, kind=None,
        lifecycle=item.capabilities.lifecycle if item.capabilities else None, availability=item.availability, thumbnail=item.thumbnail,
        category_keys=item.category_keys,
    )


def media_from_feed(entry: Mapping) -> Media | None:
    """One ``source_automations.feed_entries`` item (a PreviewEntry dump): a follow's newest uploads, already fetched by the sweep."""
    capabilities = entry.get("capabilities") if isinstance(entry.get("capabilities"), Mapping) else {}
    return _media(
        _text(capabilities.get("provider")) or "youtube", _text(entry.get("id")), _text(entry.get("webpage_url")), title=_text(entry.get("title")),
        uploader=_text(entry.get("uploader")), uploader_id=_text(entry.get("channel_id")), uploader_url=_text(entry.get("channel_url")),
        duration=_count(entry.get("duration")), view_count=_count(entry.get("view_count")), published_at=_naive_utc(entry.get("published_at")),
        kind=None, lifecycle=_text(capabilities.get("lifecycle")), availability=_text(entry.get("availability")),
        thumbnail=_text(entry.get("thumbnail")),
    )


def media_from_progress(row: RemotePlaybackProgress) -> Media:
    """A history anchor: a watched item kept so it gets a vector for the centroids. Never a candidate (SOURCE_HISTORY)."""
    return Media(
        key=row.source_identity_key, source_identity=row.source_identity, extractor=row.extractor, remote_id=row.remote_id,
        webpage_url=row.source_url, title=row.title[:TITLE_MAX] if row.title else None,
        uploader=row.uploader[:TITLE_MAX] if row.uploader else None, channel_key=row.channel_key, channel_url=None,
        duration=int(row.duration_seconds) if row.duration_seconds else None, view_count=None, published_at=None, kind=None,
        category_keys=(), thumbnail=None, availability=None, eligible=True,
    )


# ---- pool writes ----
def _chunks(values: Sequence, size: int = 500):  # noqa: ANN202
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _fill(row: RemoteMedia, media: Media, now: datetime) -> None:
    """Copy ``media`` onto ``row``: a known field is never blanked, and a changed title or uploader drops the stale vector."""
    for column in ("extractor", "remote_id", "channel_key", "channel_url", "duration", "view_count", "published_at", "kind", "thumbnail", "availability"):
        value = getattr(media, column)
        if value is not None:
            setattr(row, column, value)
    changed = False
    for column in ("title", "uploader"):
        value = getattr(media, column)
        if value is not None and value != getattr(row, column):
            setattr(row, column, value)
            changed = True
    row.category_keys = sorted({*(row.category_keys or []), *media.category_keys})
    row.tokens = sorted(reco_profile.remote_tokens(row.title, row.channel_key, row.category_keys))[:TOKENS_MAX]
    if changed:
        row.vector_model = row.vector_signature = row.vector = None
    row.fetched_at = row.last_nominated_at = now


def upsert_media(db: Session, medias: Iterable[Media], *, now: datetime) -> dict[str, RemoteMedia]:
    """Insert or refresh ``remote_media`` rows: public metadata only, no member column. Returns every row by key."""
    medias = list(medias)
    existing: dict[str, RemoteMedia] = {}
    for keys in _chunks(sorted({media.key for media in medias})):
        existing.update({row.key: row for row in db.scalars(select(RemoteMedia).where(RemoteMedia.key.in_(keys)))})
    for media in medias:
        row = existing.get(media.key)
        if row is None:
            row = RemoteMedia(key=media.key, source_identity=media.source_identity, webpage_url=media.webpage_url, tokens=[], category_keys=[])
            db.add(row)
            existing[media.key] = row
        _fill(row, media, now)
    db.flush()
    return existing


def nominate(db: Session, member_id: str, nominations: Iterable[Nomination], *, now: datetime) -> int:
    """Write one member's pool: media rows, ``reco_pool`` rows (``sources |= bit``), per-source caps, prune. Returns rows touched.

    The caller owns the transaction (one ``write_transaction`` per member refresh). An ineligible entry is never pooled; a
    history anchor is exempt, because a watched short still needs a vector.
    """
    chosen = [n for n in nominations if n.source == SOURCE_HISTORY or n.media.eligible]
    upsert_media(db, [n.media for n in chosen], now=now)
    existing: dict[str, RecoPool] = {}
    for keys in _chunks(sorted({n.media.key for n in chosen})):
        existing.update({
            row.item_key: row for row in db.scalars(select(RecoPool).where(RecoPool.user_id == member_id, RecoPool.item_key.in_(keys)))
        })
    for nomination in chosen:
        row = existing.get(nomination.media.key)
        if row is None:
            row = RecoPool(user_id=member_id, item_key=nomination.media.key, sources=0, first_seen_at=now)
            db.add(row)
            existing[nomination.media.key] = row
        row.sources |= nomination.source
        row.last_nominated_at = now
        if nomination.seed_ref:
            row.seed_ref = nomination.seed_ref[:80]
    db.flush()
    _enforce_caps(db, member_id)
    prune_pool(db, member_id, now=now)
    return len(existing)


def _enforce_caps(db: Session, member_id: str) -> None:
    """Each source keeps its newest ``POOL_CAPS`` videos; an older one loses that source's bit, and the row goes at zero."""
    for bit, cap in POOL_CAPS.items():
        keys = db.scalars(
            select(RecoPool.item_key).join(RemoteMedia, RemoteMedia.key == RecoPool.item_key)
            .where(RecoPool.user_id == member_id, RecoPool.sources.op("&")(bit) != 0)
            .order_by(RemoteMedia.published_at.desc().nulls_last(), RecoPool.item_key)
        ).all()
        for overflow in _chunks(keys[cap:]):
            db.execute(update(RecoPool).where(RecoPool.user_id == member_id, RecoPool.item_key.in_(overflow)).values(sources=RecoPool.sources - bit))
    db.execute(delete(RecoPool).where(RecoPool.user_id == member_id, RecoPool.sources == 0))


def prune_pool(db: Session, member_id: str, *, now: datetime) -> int:
    """Drop rows not nominated for 21 days, and every source of a completed item except its history anchor. Returns rows deleted."""
    removed = db.execute(delete(RecoPool).where(RecoPool.user_id == member_id, RecoPool.last_nominated_at < now - PRUNE_AFTER)).rowcount
    completed = select(RemotePlaybackProgress.source_identity_key).where(
        RemotePlaybackProgress.user_id == member_id, RemotePlaybackProgress.completed.is_(True)
    )
    db.execute(
        update(RecoPool).where(RecoPool.user_id == member_id, RecoPool.item_key.in_(completed))
        .values(sources=RecoPool.sources.op("&")(SOURCE_HISTORY))
    )
    return removed + db.execute(delete(RecoPool).where(RecoPool.user_id == member_id, RecoPool.sources == 0)).rowcount


def sweep(db: Session, *, now: datetime, batch: int = POOL_SWEEP_BATCH) -> int:
    """The daily sweep: every member's prune, then ``remote_media`` that no pool row references and nothing nominated for 14 days."""
    removed = sum(prune_pool(db, member_id, now=now) for member_id in db.scalars(select(RecoPool.user_id).distinct()).all())
    orphans = db.scalars(
        select(RemoteMedia.key)
        .where(RemoteMedia.last_nominated_at < now - REMOTE_MEDIA_SWEEP_AFTER, RemoteMedia.key.not_in(select(RecoPool.item_key)))
        .limit(batch)
    ).all()
    for keys in _chunks(orphans):
        removed += db.execute(delete(RemoteMedia).where(RemoteMedia.key.in_(keys))).rowcount
    return removed


# ---- provider gate ----
T = TypeVar("T")


class _Stop(Exception):
    """End this refresh with what it has: a ceiling, a busy provider or a backoff. Never an error."""


class _BudgetHit(_Stop):
    """The hourly or the daily household ceiling is reached."""


class _Skip(Exception):
    """This one provider call failed; the refresh goes on with the next."""


def _retry_after_seconds(error: BaseException, now: datetime) -> float | None:
    """A provider's Retry-After (seconds or an HTTP date) on the error, its response or an attribute; None without a usable one."""
    headers = getattr(error, "headers", None) or getattr(getattr(error, "response", None), "headers", None)
    value = headers.get("Retry-After") or headers.get("retry-after") if isinstance(headers, Mapping) else None
    if value is None:
        value = getattr(error, "retry_after", None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return max(0.0, ((when.astimezone(UTC).replace(tzinfo=None) if when.tzinfo else when) - now).total_seconds())


class _ProviderGate:
    """Household pacing, ceilings, busy deferral and backoff for recommendation provider calls.

    One worker makes the calls; ``counters`` may be read from a request thread, so the state sits behind a lock. The
    clock, the sleep and the rng are injected, so tests run it with no real waiting.
    """

    def __init__(
        self, clock: Callable[[], datetime], sleep: Callable[[float], None], rng, stop: threading.Event | None = None,  # noqa: ANN001
    ) -> None:
        self._clock, self._sleep, self._rng = clock, sleep, rng
        self._stop = stop or threading.Event()  # set on shutdown: no call goes out after it
        self._lock = threading.Lock()
        self._calls: deque[datetime] = deque()
        self._hits: deque[datetime] = deque()
        self._last: datetime | None = None
        self._failures = 0
        self._backoffs = 0
        self._blocked_until: datetime | None = None
        self.total = 0

    def blocked_until(self) -> datetime | None:
        with self._lock:
            return self._blocked_until

    def counters(self) -> dict[str, int]:
        with self._lock:
            self._trim(self._clock())
            return {"provider_calls_24h": len(self._calls), "budget_hits_24h": len(self._hits)}

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(days=1)
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
        while self._hits and self._hits[0] <= cutoff:
            self._hits.popleft()

    def call(self, function: Callable[[], T]) -> T:
        """Run one provider call, or raise ``_Stop`` (the refresh ends) or ``_Skip`` (this call failed)."""
        if self._stop.is_set():
            raise _Stop
        now = self._clock()
        with self._lock:
            self._trim(now)
            if self._blocked_until is not None and now < self._blocked_until:
                raise _Stop
            in_last_hour = sum(1 for at in self._calls if at > now - timedelta(hours=1))
            if in_last_hour >= CALLS_PER_HOUR or len(self._calls) >= CALLS_PER_DAY:
                self._hits.append(now)
                raise _BudgetHit
            wait = 0.0
            if self._last is not None:
                spacing = PROVIDER_SPACING_SECONDS * (1 + self._rng.uniform(-PROVIDER_JITTER, PROVIDER_JITTER))
                wait = spacing - (now - self._last).total_seconds()
        if wait > 0:
            self._sleep(wait)
            if self._stop.is_set():
                raise _Stop
        with self._lock:
            self._last = self._clock()
            self._calls.append(self._last)
            self.total += 1
        try:
            with provider_budget.priority("background"):
                value = function()
        except provider_budget.BudgetExhausted:  # same "try later" as a busy provider, for longer
            with self._lock:
                self._blocked_until = self._clock() + BUDGET_DEFER
            raise _Stop from None
        except SearchBusyError:  # interactive search wins: stand aside
            with self._lock:
                self._blocked_until = self._clock() + BUSY_DEFER
            raise _Stop from None
        except Exception as error:  # noqa: BLE001 - any provider failure is a skipped call; only its type is ever logged
            self._failed(error)
            raise _Skip from None
        with self._lock:
            self._failures = self._backoffs = 0
        return value

    def _failed(self, error: BaseException) -> None:
        now = self._clock()
        retry_after = _retry_after_seconds(error, now)
        with self._lock:
            self._failures += 1
            if retry_after is None and self._failures < FAILURES_BEFORE_BACKOFF:
                return
            self._backoffs += 1
            exponential = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(self._backoffs - 1, 10))
            hinted = min(retry_after or 0.0, RETRY_AFTER_MAX_SECONDS)
            delay = max(hinted, exponential) if self._failures >= FAILURES_BEFORE_BACKOFF else hinted
            self._blocked_until = now + timedelta(seconds=delay)


# ---- refresher ----
@dataclass(frozen=True)
class RefreshResult:
    member_id: str
    nominated: int
    provider_calls: int
    budget_hit: bool
    error: str | None = None


@dataclass(frozen=True)
class _Seed:
    progress_id: str  # the member's own progress row: the only thing persisted about a seed
    words: tuple[str, ...]  # in memory only


@dataclass(frozen=True)
class _InterestQuery:
    category_key: str
    query: str  # in memory only: never stored, never logged


@dataclass(frozen=True)
class _Plan:
    follows: tuple[Nomination, ...]
    anchors: tuple[Nomination, ...]
    channels: tuple[str, ...]
    seeds: tuple[_Seed, ...]
    interest: _InterestQuery | None


def _is_word(token: str) -> bool:
    """One alphabetic word: not a bigram, a ``ch:``/``cat:`` token or a number (remote_tokens carries all of those)."""
    return len(token) >= 3 and token.isalpha()


def _idf(documents: Mapping[str, Iterable[str]]) -> Callable[[str], float]:
    frequency = Counter(token for tokens in documents.values() for token in set(tokens))
    total = len(documents)
    return lambda token: math.log((total + 1) / (frequency.get(token, 0) + 1)) + 1


def _words_of(row: RemotePlaybackProgress) -> list[str]:
    return [token for token in reco_profile.remote_tokens(row.title, row.channel_key, ()) if _is_word(token)]


def _newest(page: object, keep: int = LISTING_KEEP) -> list[YouTubeSearchResult]:
    """The newest ``keep`` entries of a listing, by upload date; entries without a date keep their listed order after the dated ones."""
    entries = list(page.entries)  # type: ignore[attr-defined]
    return sorted(entries, key=lambda entry: (entry.published_at is None, -entry.published_at.timestamp() if entry.published_at else 0.0))[:keep]


class RecoRefresher:
    """Fills members' Candidate pools on one background worker, never on a request thread."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        search: Callable[[str, int], Sequence[YouTubeSearchResult]],
        channel_pages: ChannelPages | None,
        *,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._search = search
        self._channel_pages = channel_pages
        self._clock = clock
        self._stop = threading.Event()
        # the default pacing sleep wakes on close(), so shutdown never waits out the 15 s spacing
        self._gate = _ProviderGate(clock, sleep or self._stop.wait, rng or random.Random(), self._stop)
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._closed = False
        self._scheduled = False  # a due run is queued or running
        self._triggers: dict[str, datetime] = {}
        self._last_refresh: dict[str, datetime] = {}
        self._retry: dict[str, datetime] = {}  # a refresh that stopped early: when to try again
        self._expanded: dict[str, datetime] = {}  # progress row id -> when its seed was last expanded (in memory: a restart re-expands)
        self._interest_turn: dict[str, int] = {}
        self._warming: set[str] = set()
        self._last_sweep: datetime | None = None

    # ---- scheduling ----
    def _submit(self, function: Callable, *args) -> bool:  # noqa: ANN002
        with self._lock:
            if self._closed:
                return False
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reco-refresh")
            self._executor.submit(self._guarded, function, *args)
        return True

    @staticmethod
    def _guarded(function: Callable, *args) -> None:  # noqa: ANN002
        try:
            function(*args)
        except Exception as error:  # noqa: BLE001 - the worker never dies; the type is all that is logged
            logger.warning("Recommendation refresher task failed: %s", type(error).__name__)

    def schedule_due(self) -> None:
        """Queue one run over the due members. Non-blocking, and a run already queued or running makes this a no-op."""
        with self._lock:
            if self._scheduled:
                return
            self._scheduled = True
        if not self._submit(self._scheduled_run):
            with self._lock:
                self._scheduled = False

    def _scheduled_run(self) -> None:
        try:
            self._run_due()
        finally:
            with self._lock:
                self._scheduled = False

    def _run_due(self) -> None:
        now = self._clock()
        with self._session_factory() as db:
            if not enabled(db):
                return
            due = self._due_members(db, now)
        if self._last_sweep is None or now - self._last_sweep >= SWEEP_EVERY:
            with self._session_factory() as db, write_transaction(db, name="reco_pool_sweep"):
                sweep(db, now=now)
            self._last_sweep = now
        for member_id in due:
            if self._stop.is_set():
                return
            self.refresh(member_id)

    def trigger(self, member_id: str) -> None:
        """A follow, feedback or an interest change: refresh this member soon (debounced 10 minutes, then at most once an hour)."""
        with self._lock:
            self._triggers.setdefault(member_id, self._clock())

    def _due_members(self, db: Session, now: datetime) -> list[str]:
        cutoff = now - ACTIVE_WINDOW
        active = db.scalars(
            select(User.id).where(User.is_active.is_(True), or_(
                User.id.in_(select(AppSession.user_id).where(AppSession.last_seen_at >= cutoff)),
                User.id.in_(select(RemotePlaybackProgress.user_id).where(RemotePlaybackProgress.last_watched_at >= cutoff)),
                User.id.in_(select(PlaybackProgress.user_id).where(PlaybackProgress.last_watched_at >= cutoff)),
            ))
        ).all()
        newest = dict(db.execute(select(RecoPool.user_id, func.max(RecoPool.last_nominated_at)).group_by(RecoPool.user_id)).all())
        with self._lock:
            triggers, last, retry = dict(self._triggers), dict(self._last_refresh), dict(self._retry)
        due: list[tuple[datetime, str]] = []
        for member_id in active:
            pooled = newest.get(member_id)
            since = now - last.get(member_id, datetime.min)
            aged = (pooled is None or now - pooled >= REFRESH_AGE) and since >= REFRESH_AGE
            triggered = member_id in triggers and now - triggers[member_id] >= TRIGGER_DEBOUNCE and since >= TRIGGER_MIN_GAP
            retrying = member_id in retry and now >= retry[member_id]
            finished = db.scalar(select(func.max(RemotePlaybackProgress.updated_at)).where(
                RemotePlaybackProgress.user_id == member_id, RemotePlaybackProgress.completed.is_(True)
            ))
            completed = (
                pooled is not None and finished is not None and finished > pooled
                and now - finished >= TRIGGER_DEBOUNCE and since >= TRIGGER_MIN_GAP
            )
            if aged or triggered or retrying or completed:
                due.append((pooled or datetime.min, member_id))
        return [member_id for _pooled, member_id in sorted(due)]

    def warm_channel(self, channel_id: str) -> None:
        """An Up Next miss on the watched channel's tab: load it on the worker, within the budget. Returns at once."""
        if self._channel_pages is None or not CHANNEL_ID.fullmatch(channel_id):
            return
        with self._lock:
            if channel_id in self._warming:
                return
            self._warming.add(channel_id)
        if not self._submit(self._warm, channel_id):
            with self._lock:
                self._warming.discard(channel_id)

    def _warm(self, channel_id: str) -> None:
        try:
            with self._session_factory() as db:
                if not enabled(db):
                    return
            if self._channel_pages.peek_tab(channel_id, "videos", LISTING_LIMIT) is None:
                try:
                    self._gate.call(lambda: self._channel_pages.page(channel_id, "videos", LISTING_LIMIT))
                except (_Stop, _Skip):
                    pass
        finally:
            with self._lock:
                self._warming.discard(channel_id)

    def counters(self) -> dict[str, int]:
        return self._gate.counters()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
        self._stop.set()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # ---- one refresh ----
    def refresh(self, member_id: str) -> RefreshResult:
        started = self._clock()
        calls_before = self._gate.total
        nominations: list[Nomination] = []
        stopped: _Stop | None = None
        try:
            with self._session_factory() as db:
                if not enabled(db):
                    return RefreshResult(member_id, 0, 0, False)
                plan = self._read_plan(db, member_id, started)
            nominations.extend(plan.follows)
            try:
                self._list_channels(plan.channels, nominations)
                self._expand_seeds(plan.seeds, nominations)
                self._search_interest(plan.interest, nominations)
            except _Stop as stop:
                stopped = stop
            with self._session_factory() as db, write_transaction(db, name="reco_refresh"):
                nominated = nominate(db, member_id, [*nominations, *plan.anchors], now=self._clock())
        except Exception as error:  # noqa: BLE001 - a refresh never raises; the type is all that is logged
            logger.warning("Recommendation refresh failed: %s", type(error).__name__)
            return RefreshResult(member_id, 0, self._gate.total - calls_before, False, error=type(error).__name__)
        self._finished(member_id, started, stopped)
        calls = self._gate.total - calls_before
        logger.info("Recommendation refresh: nominated=%d provider_calls=%d stopped=%s", nominated, calls, type(stopped).__name__ if stopped else "no")
        return RefreshResult(member_id, nominated, calls, isinstance(stopped, _BudgetHit))

    def _finished(self, member_id: str, started: datetime, stopped: _Stop | None) -> None:
        now = self._clock()
        with self._lock:
            self._last_refresh[member_id] = started
            self._triggers.pop(member_id, None)
            if stopped is None:
                self._retry.pop(member_id, None)
            elif isinstance(stopped, _BudgetHit):
                self._retry[member_id] = now + TRIGGER_MIN_GAP
            else:
                self._retry[member_id] = max(self._gate.blocked_until() or now, now + BUSY_DEFER)
            self._expanded = {key: at for key, at in self._expanded.items() if now - at < SEED_REEXPAND}

    def _read_plan(self, db: Session, member_id: str, now: datetime) -> _Plan:
        """Everything a refresh needs from the database, before any provider call. Reads this member's rows only."""
        profile = reco_profile.profiles.get(db, member_id, now=now)
        follows = _follow_nominations(db, member_id)
        progress = db.scalars(
            select(RemotePlaybackProgress).where(RemotePlaybackProgress.user_id == member_id)
            .order_by(RemotePlaybackProgress.last_watched_at.desc(), RemotePlaybackProgress.id).limit(HISTORY_ANCHORS)
        ).all()
        documents = {
            key: frozenset(tokens or ())
            for key, tokens in db.execute(
                select(RemoteMedia.key, RemoteMedia.tokens).join(RecoPool, RecoPool.item_key == RemoteMedia.key).where(RecoPool.user_id == member_id)
            )
        }
        for row in progress:
            documents.setdefault(row.source_identity_key, reco_profile.remote_tokens(row.title, row.channel_key, ()))
        idf = _idf(documents)
        satisfied = [row for row in progress if row.completed or row.max_fraction >= CONSTANTS.satisfied_fraction]
        fresh = [row for row in satisfied if now - self._expanded.get(row.id, datetime.min) >= SEED_REEXPAND][:SEEDS]
        seeds = tuple(
            _Seed(row.id, tuple(sorted(words, key=lambda word: (-idf(word), word))[:3])) for row in fresh if (words := _words_of(row))
        )
        return _Plan(
            follows=follows,
            anchors=tuple(Nomination(media_from_progress(row), SOURCE_HISTORY, row.id) for row in progress),
            channels=_channels_to_list(profile),
            seeds=seeds,
            interest=self._interest_query(db, member_id, profile, satisfied, idf),
        )

    def _interest_query(self, db: Session, member_id: str, profile: MemberProfile, satisfied: Sequence[RemotePlaybackProgress], idf: Callable[[str], float]) -> _InterestQuery | None:
        """One selected Interest with a satisfied watch in its category, rotating: "{category phrase} {highest-IDF token}"."""
        keys = sorted(key for key in profile.interests if key in _PHRASES)
        if not keys or not satisfied:
            return None
        categories = dict(db.execute(select(RemoteMedia.key, RemoteMedia.category_keys).where(RemoteMedia.key.in_([row.source_identity_key for row in satisfied]))).all())
        turn = self._interest_turn.get(member_id, 0)
        for offset in range(len(keys)):
            key = keys[(turn + offset) % len(keys)]
            words = {word for row in satisfied if key in (categories.get(row.source_identity_key) or []) for word in _words_of(row)}
            if words:
                self._interest_turn[member_id] = turn + offset + 1
                return _InterestQuery(key, f"{_PHRASES[key]} {max(sorted(words), key=idf)}")
        return None

    def _list_channels(self, channels: Sequence[str], into: list[Nomination]) -> None:
        if self._channel_pages is None:
            return
        for channel_id in channels[:CHANNEL_LISTINGS_PER_REFRESH]:
            page = self._channel_pages.peek_tab(channel_id, "videos", LISTING_LIMIT)  # a cached tab costs no provider call
            if page is None:
                try:
                    _header, page, _fetched, _stale = self._gate.call(
                        lambda channel_id=channel_id: self._channel_pages.page(channel_id, "videos", LISTING_LIMIT)
                    )
                except _Skip:
                    continue
            into.extend(Nomination(media, SOURCE_CHANNEL) for entry in _newest(page) if (media := media_from_result(entry)))

    def _expand_seeds(self, seeds: Sequence[_Seed], into: list[Nomination]) -> None:
        for seed in seeds[:SEED_SEARCHES_PER_REFRESH]:
            try:
                found = self._gate.call(lambda query=" ".join(seed.words): self._search(query, SEARCH_LIMIT))
            except _Skip:
                continue
            self._expanded[seed.progress_id] = self._clock()
            into.extend(Nomination(media, SOURCE_SEED, seed.progress_id) for entry in found if (media := media_from_result(entry)))

    def _search_interest(self, interest: _InterestQuery | None, into: list[Nomination]) -> None:
        if interest is None or INTEREST_SEARCHES_PER_REFRESH < 1:
            return
        try:
            found = self._gate.call(lambda: self._search(interest.query, SEARCH_LIMIT))
        except _Skip:
            return
        into.extend(
            Nomination(media, SOURCE_INTEREST, interest.category_key)
            for entry in found if (media := media_from_result(entry, (interest.category_key,)))
        )


def _follow_nominations(db: Session, member_id: str) -> tuple[Nomination, ...]:
    """Source (a): the newest entries the automation sweep already fetched for each active channel follow. No provider cost."""
    automations = db.scalars(
        select(SourceAutomation).where(
            SourceAutomation.user_id == member_id, SourceAutomation.source_type == CHANNEL_SOURCE_TYPE, SourceAutomation.active.is_(True)
        )
    ).all()
    return tuple(
        Nomination(media, SOURCE_FOLLOW, automation.id)
        for automation in automations
        for entry in (automation.feed_entries or [])[:FOLLOW_ENTRIES]
        if isinstance(entry, Mapping) and (media := media_from_feed(entry))
    )


def _channels_to_list(profile: MemberProfile) -> tuple[str, ...]:
    """Source (b): the YouTube channels the member likes most that they do not follow (a follow is covered by source a)."""
    liked = sorted(
        ((affinity, key) for key, affinity in profile.affinity.items()
         if affinity > 0 and key.startswith(CHANNEL_PREFIX) and key not in profile.followed and key not in profile.hidden_channels
         and CHANNEL_ID.fullmatch(key.removeprefix(CHANNEL_PREFIX))),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return tuple(key.removeprefix(CHANNEL_PREFIX) for _affinity, key in liked[:CHANNEL_LISTINGS_PER_REFRESH])


# ---- remote candidates ----
CANDIDATE_BITS = SOURCE_FOLLOW | SOURCE_CHANNEL | SOURCE_SEED | SOURCE_INTEREST  # the stored bits that nominate; SOURCE_HISTORY never does
TOUCH_AFTER = timedelta(hours=6)  # a Popular or listing row nominated this recently is not written again


def _playable(row: RemoteMedia) -> bool:
    return (row.availability or "").strip().casefold() not in _UNPLAYABLE_AVAILABILITY and (row.duration is None or row.duration >= MIN_DURATION_SECONDS)


def _view_percentiles(rows: Iterable[RemoteMedia]) -> dict[str, float]:
    """Within-channel view percentile (0 to 1) for a channel with at least ``channel_percentile_min_items`` rows that have views.

    It is computed over the rows passed in, which are the member's own candidates (or the household Popular rows): never over
    rows another member's seeds fetched.
    """
    groups: dict[str, list[RemoteMedia]] = {}
    for row in rows:
        if row.channel_key and row.view_count is not None:
            groups.setdefault(row.channel_key, []).append(row)
    percentiles: dict[str, float] = {}
    for members in groups.values():
        if len(members) >= CONSTANTS.channel_percentile_min_items:
            views = sorted(row.view_count for row in members)
            for row in members:
                percentiles[row.key] = bisect_left(views, row.view_count) / (len(views) - 1)
    return percentiles


def saved_remote_keys(db: Session, member: User, rows: Sequence[RemoteMedia]) -> set[str]:
    """The keys of ``rows`` the member can already open from the Library."""
    by_id = {(row.extractor.casefold(), row.remote_id): row.key for row in rows if row.extractor and row.remote_id}
    saved: set[str] = set()
    for remote_ids in _chunks(sorted({remote_id for _extractor, remote_id in by_id})):
        for extractor, remote_id in db.execute(
            select(LibraryItem.extractor, LibraryItem.remote_id)
            .where(LibraryItem.remote_id.in_(remote_ids), LibraryItem.status != "missing", LibraryService.visible_predicate(member))
        ):
            key = by_id.get(((extractor or "").split(":", 1)[0].casefold(), remote_id))
            if key:
                saved.add(key)
    return saved


def remote_candidates(db: Session, member_id: str, *, as_of: datetime | None = None) -> list[Candidate]:
    """The member's own pool as Candidates: every read filters by ``user_id``."""
    member = db.get(User, member_id)
    if member is None:
        return []
    query = (
        select(RecoPool, RemoteMedia).join(RemoteMedia, RemoteMedia.key == RecoPool.item_key)
        .where(
            RecoPool.user_id == member_id, RecoPool.sources.op("&")(CANDIDATE_BITS) != 0,
            or_(RemoteMedia.kind.is_(None), RemoteMedia.kind == "video"),
        )
        .order_by(RecoPool.last_nominated_at.desc(), RecoPool.item_key)
    )
    if as_of is not None:
        query = query.where(RecoPool.first_seen_at <= as_of, or_(RemoteMedia.published_at.is_(None), RemoteMedia.published_at <= as_of))
    rows = [(entry, media) for entry, media in db.execute(query) if _playable(media)]
    saved = saved_remote_keys(db, member, [media for _entry, media in rows])
    percentiles = _view_percentiles(media for _entry, media in rows)
    return [
        reco_profile.candidate_from_remote(
            media, sources=entry.sources & CANDIDATE_BITS, seed_ref=entry.seed_ref, channel_view_percentile=percentiles.get(media.key)
        )
        for entry, media in rows if media.key not in saved
    ]


def _rows_for(db: Session, medias: Iterable[Media]) -> dict[str, RemoteMedia]:
    """``remote_media`` rows for Popular or listing entries. Written only when a row is new, newly categorised or untouched for 6 h.

    The only write a candidate loader makes, and it carries no member: it lets the vector backfill reach these rows.
    A busy writer raises like any other write route; degrade to unsaved rows if the perf run shows contention.
    """
    medias = list(medias)
    now = utcnow()
    existing: dict[str, RemoteMedia] = {}
    for keys in _chunks(sorted({media.key for media in medias})):
        existing.update({row.key: row for row in db.scalars(select(RemoteMedia).where(RemoteMedia.key.in_(keys)))})
    stale = [
        media for media in medias
        if (row := existing.get(media.key)) is None or now - row.last_nominated_at >= TOUCH_AFTER or not set(media.category_keys) <= set(row.category_keys or [])
    ]
    if stale:
        with write_transaction(db, name="reco_remote_media"):
            existing.update(upsert_media(db, stale, now=now))
    return existing


def popular_candidates(db: Session, items: Sequence[PopularItem]) -> list[Candidate]:
    """Every item of the household Popular snapshot (``PopularDiscovery.candidates()``), not the 24-item feed."""
    medias = {media.key: media for item in items if (media := media_from_popular(item)) and media.eligible}
    rows = _rows_for(db, medias.values())
    percentiles = _view_percentiles(rows[key] for key in medias if key in rows)
    return [
        reco_profile.candidate_from_remote(rows[key], sources=SOURCE_POPULAR, channel_view_percentile=percentiles.get(key))
        for key in medias if key in rows
    ]


def listing_candidates(db: Session, page: ChannelTabPage | None) -> list[Candidate]:
    """Up Next: the peeked listing of the channel being watched. ``None`` (a cache miss) gives no candidates."""
    if page is None:
        return []
    medias = {media.key: media for entry in page.entries if (media := media_from_result(entry)) and media.eligible}
    rows = _rows_for(db, medias.values())
    percentiles = _view_percentiles(rows[key] for key in medias if key in rows)
    return [
        reco_profile.candidate_from_remote(rows[key], sources=SOURCE_CURRENT_CHANNEL, channel_view_percentile=percentiles.get(key))
        for key in medias if key in rows
    ]


# ---- title candidates ----
TITLE_BY_TASTE = 400  # nearest to the member's taste centroids
TITLE_BY_OVERLAP = 200  # best weighted genre, cast, director and collection overlap with what they finished
TITLE_NEWEST = 50
TITLE_NEIGHBOURS = 100  # an anchor's nearest titles, by vector and by shared tokens
_OVERLAP_PREFIXES = ("genre:", "cast:", "dir:", "col:")


class PoolTitle(NamedTuple):
    """A visible movie or series as the title lists read it: selected columns, never a loaded row (reco I2)."""
    id: str
    type: str
    name: str
    boxset_id: str | None
    arrived_at: datetime
    tokens: frozenset[str]
    rating: float | None


@dataclass(frozen=True)
class TitleBase:
    """The member's visible, unstarted movies and series as of a moment: built once per request, read by each of its lists."""
    titles: tuple[PoolTitle, ...]
    _overlaps: dict[int, tuple[MemberProfile, list[tuple[float, str]]]] = field(default_factory=dict, compare=False, repr=False)

    def kinds(self) -> dict[str, str]:
        return {title.id: title.type for title in self.titles}

    @functools.cached_property
    def newest(self) -> list[PoolTitle]:
        return sorted(self.titles, key=lambda title: (title.arrived_at, title.id), reverse=True)

    def by_overlap(self, profile: MemberProfile) -> list[tuple[float, str]]:
        """(weighted genre, cast, director and collection overlap with what the member finished, id), best first: once per
        profile, then each list skips its own exclusions."""
        hit = self._overlaps.get(id(profile))
        if hit is not None and hit[0] is profile:
            return hit[1]
        liked: Counter[str] = Counter()
        for item in profile.satisfied:
            if item.target_kind == "title":
                for token in item.tokens:
                    if token.startswith(_OVERLAP_PREFIXES):
                        liked[token] += item.weight
        ranked = sorted(((sum(liked[token] for token in title.tokens if token in liked), title.id) for title in self.titles), reverse=True)
        self._overlaps[id(profile)] = (profile, ranked)
        return ranked


# Per database: title id -> (its stamp row, the PoolTitle built from it). The stamp is updated_at, which every ORM write
# bumps, plus the columns raw SQL rewrites (category, added_at: db.py) and the other token columns, all as stored.
# Cleared wholesale past TITLE_FACTS_MAX entries, an LRU if a library outgrows it.
_TITLE_FACTS: weakref.WeakKeyDictionary[object, dict[str, tuple[tuple, PoolTitle]]] = weakref.WeakKeyDictionary()
TITLE_FACTS_MAX = 50_000
_FACTS_CHUNK = 500
_STAMP = (
    MediaTitle.id, MediaTitle.boxset_id, MediaTitle.category, MediaTitle.year,
    type_coerce(MediaTitle.added_at, String), type_coerce(MediaTitle.updated_at, String),  # unparsed: only compared
)


def title_base(db: Session, member: User, *, as_of: datetime | None = None) -> TitleBase:
    """The population: visible, unstarted movies and series that had arrived by ``as_of``. Titles come from a
    per-process cache checked against their stored columns, so a warm request parses no metadata, dates or names."""
    started = MemberRecommendationPolicy(db)._title_history(member, until=as_of)  # noqa: SLF001 - the one definition of "started"
    query = select(*_STAMP).where(MediaTitle.type.in_(TITLE_POOL_TYPES), LibraryService.visible_title_predicate(member))
    facts = _TITLE_FACTS.setdefault(db.get_bind(), {})
    titles: list[PoolTitle] = []
    stale: list[tuple] = []
    for row in db.execute(query).tuples():  # one pass, keeping no Row: 10,000 survivors a request made every 5th one a full GC
        if row[0] in started:
            continue
        hit = facts.get(row[0])
        if hit is not None and hit[0] == row:
            titles.append(hit[1])
        else:
            stale.append(tuple(row))
    if len(facts) + len(stale) > TITLE_FACTS_MAX:
        facts.clear()
    for chunk in batched(stale, _FACTS_CHUNK):
        loaded = {row[0]: row[1:] for row in db.execute(
            select(MediaTitle.id, MediaTitle.type, MediaTitle.name, MediaTitle.metadata_json, MediaTitle.added_at, MediaTitle.created_at)
            .where(MediaTitle.id.in_([row[0] for row in chunk]))
        ).tuples()}
        for row in chunk:
            title_id, boxset_id, category, year = row[:4]
            kind, name, metadata, added_at, created_at = loaded[title_id]
            tokens = reco_profile.tokens_of(metadata, boxset_id, category, year)
            title = PoolTitle(title_id, kind, name, boxset_id, added_at or created_at, tokens, reco_profile.rating_of(metadata))
            facts[title_id] = (row, title)  # another request may clear facts meanwhile: never read it back
            titles.append(title)
    return TitleBase(tuple(title for title in titles if as_of is None or title.arrived_at <= as_of))


def title_candidates(
    db: Session, member: User, profile: MemberProfile, *, anchor: MediaTitle | None = None, as_of: datetime | None = None,
    base: TitleBase | None = None,
) -> list[Candidate]:
    """Visible, unstarted movies and series, narrowed to what a surface could plausibly rank. ``base`` is
    ``title_base(db, member, as_of=as_of)``, passed in when a request ranks several lists.

    Brute-force cosine over the resident title vectors;
    ``embeddings.nearest`` and sqlite-vec are the upgrade path.
    """
    from app.services import embeddings  # embeddings -> yt_dlp_service; keep this module's import graph light

    if base is None:
        base = title_base(db, member, as_of=as_of)
    skip = profile.hidden_titles | ({anchor.id} if anchor is not None else set())
    titles = [title for title in base.titles if title.id not in skip]
    tokens = {title.id: title.tokens for title in titles}
    choice = embeddings.serving(db)
    stored = embeddings.vector_map(db, choice.model_id) if choice is not None else {}
    vectors = {title_id: stored[title_id][1] for title_id in tokens if title_id in stored and stored[title_id][0] == "title"}

    picked: set[str] = set()
    if profile.centroids:
        taste = ((max(embeddings.cosine(centroid, vector) for centroid in profile.centroids), title_id) for title_id, vector in vectors.items())
        picked.update(title_id for _score, title_id in heapq.nlargest(TITLE_BY_TASTE, taste))
    overlap = islice(((score, title_id) for score, title_id in base.by_overlap(profile) if title_id not in skip), TITLE_BY_OVERLAP)
    picked.update(title_id for score, title_id in overlap if score > 0)
    picked.update(islice((title.id for title in base.newest if title.id not in skip), TITLE_NEWEST))
    if anchor is not None:
        anchor_tokens = frozenset(token for token in reco_profile.title_tokens(anchor) if token.startswith(_OVERLAP_PREFIXES))  # cat:/decade: alone are not alikeness
        if anchor.id in stored and stored[anchor.id][0] == "title":
            nearest = ((embeddings.cosine(stored[anchor.id][1], vector), title_id) for title_id, vector in vectors.items())
            picked.update(title_id for _score, title_id in heapq.nlargest(TITLE_NEIGHBOURS, nearest))
        shared = ((len(found & anchor_tokens), title_id) for title_id, found in tokens.items())
        picked.update(title_id for count, title_id in heapq.nlargest(TITLE_NEIGHBOURS, shared) if count > 0)
        if anchor.boxset_id:
            picked.update(title.id for title in titles if title.boxset_id == anchor.boxset_id)
    return [  # reco_profile.candidate_from_title, from the selected columns
        Candidate(key=title.id, target_kind="title", title=title.name, channel_key=title.boxset_id, channel_name=None,
                  tokens=title.tokens, published_at=title.arrived_at, sources=SOURCE_LIBRARY, rating=title.rating,
                  vector=vectors.get(title.id))
        for title in sorted(titles, key=lambda title: title.id) if title.id in picked
    ]
