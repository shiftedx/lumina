from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from typing import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import LibraryItem, MediaTitle, MemberFavorite, MemberInterest, PlaybackProgress, RemotePlaybackProgress, User
from app.services.library import LibraryService
from app.services.popular_discovery import POPULAR_CATEGORIES, PopularCategorySnapshot, PopularItem, PopularSnapshot, _source_key


INTEREST_CATEGORY_KEYS = tuple(category.key for category in POPULAR_CATEGORIES)
_CATEGORY_ORDER = {key: index for index, key in enumerate(INTEREST_CATEGORY_KEYS)}
_UNPLAYABLE_AVAILABILITY = {"private", "unavailable", "needs_auth", "premium", "subscriber_only", "login_required"}

# Signal weights. Ordered by ADR 0007's priority: deliberate personal signals
# (an explicit follow, demonstrated watching, a deliberate save) outrank a
# declared interest, which in turn outranks generic freshness and popularity.
# Every component below is normalized to quarter-steps in [0, 1], so the
# weighted total is an exact multiple of 0.25 and comparisons stay deterministic.
WEIGHT_FOLLOW = 5.0
WEIGHT_PLAYBACK = 4.0
WEIGHT_CONTEXT = 3.0
WEIGHT_SAVE = 3.0
WEIGHT_INTEREST = 2.0
WEIGHT_RECENCY = 1.0
WEIGHT_POPULARITY = 1.0

# Current-playback affinity is Up Next's contextual weighting: it enters the one
# shared scorer as another quarter-step component (never a second ranking path).
# It is a strong in-session signal — you deliberately chose to watch this — so it
# ranks alongside a deliberate save, yet below an explicit follow (5) and a
# demonstrated completion (4): context adjusts ranking toward the current source,
# creator, and subject matter without overriding member-scoped signals. It is
# zero on Home (no playback context), so Home ranking is byte-for-byte unchanged.
_CONTEXT_CREATOR = 1.0
_CONTEXT_SUBJECT = 0.5

# How many deliberate saves from one channel count as strong (vs incidental)
# affinity, and how many distinct saved channels we aggregate per member.
_STRONG_SAVE_COUNT = 2
_SAVED_CHANNEL_LIMIT = 200
# Channel affinity reads the most recent plays only; a years-long history is not scanned per Home load.
_PLAYED_HISTORY_LIMIT = 500


def _known_categories(keys: Iterable[str]) -> tuple[str, ...]:
    """Drop stored/requested keys no longer present in the canonical set.

    Interest keys are durable, so a category retired from POPULAR_CATEGORIES
    in a later release can leave orphaned keys in storage. Both read paths
    route through here so an orphaned key is excluded rather than raising,
    and the sort key falls back to a sentinel instead of indexing directly.
    """
    known = (key for key in keys if key in _CATEGORY_ORDER)
    return tuple(sorted(known, key=lambda key: _CATEGORY_ORDER.get(key, len(_CATEGORY_ORDER))))


def _channel_key(uploader: str | None) -> str:
    """Normalize a channel/uploader name into a comparable identity key.

    Channel identity here is a casefolded display name, an approximation: two
    distinct channels that share a display name collide, and a channel that
    renames changes its key. Popular candidates and playback/save records carry
    no stable channel id today, so the name is the only cross-signal handle.
    #87 introduces first-class follows with durable channel identifiers; the
    followed-channel seam is where a stable id replaces this approximation.
    """
    return (uploader or "").strip().casefold()


@dataclass(frozen=True)
class MemberActivitySignals:
    """Durable, member-scoped signals the policy scores against.

    Every field is derived from existing durable records (playback history and
    resume checkpoints, Library saves) or an injected followed-channel set. The
    value object is immutable and never shared across members.
    """

    selected_interests: frozenset[str]
    followed_channels: frozenset[str]
    played_uploaders: frozenset[str]
    completed_uploaders: frozenset[str]
    saved_uploaders: frozenset[str]
    frequently_saved_uploaders: frozenset[str]
    continue_watching_keys: frozenset[str]
    reference_time: datetime | None

    def is_familiar(self, uploader_key: str) -> bool:
        """A channel the member has already watched or saved (not exploration)."""
        return bool(uploader_key) and (uploader_key in self.played_uploaders or uploader_key in self.saved_uploaders)


@dataclass(frozen=True)
class PlaybackContext:
    """The currently playing Media source, as a scored input for Up Next.

    ``source_key`` is the exact source being watched (excluded from its own Up
    Next, like a Continue Watching entry). ``creator_key`` and ``subject_keys``
    carry the current creator and subject matter so candidates similar to what
    the member is watching are lifted and treated as an in-session anchor. This
    is a policy INPUT, not a surface-owned reranking: Up Next builds it, Home
    leaves it ``None``.
    """

    source_key: str
    creator_key: str
    subject_keys: frozenset[str]

    @classmethod
    def for_source(
        cls,
        *,
        source: str | None,
        item_id: str | None,
        webpage_url: str | None,
        title: str | None,
        uploader: str | None,
        subject_keys: Iterable[str] = (),
    ) -> "PlaybackContext":
        return cls(
            source_key=_source_key(source, item_id, webpage_url, title, uploader),
            creator_key=_channel_key(uploader),
            subject_keys=frozenset(_known_categories(subject_keys)),
        )

    def component(self, item: PopularItem) -> float:
        """Quarter-step contextual affinity for one candidate against the current source."""
        uploader_key = _channel_key(item.uploader)
        if uploader_key and uploader_key == self.creator_key:
            return _CONTEXT_CREATOR
        if self.subject_keys and self.subject_keys.intersection(item.category_keys):
            return _CONTEXT_SUBJECT
        return 0.0

    def is_related(self, item: PopularItem) -> bool:
        """Whether a candidate is anchored to the current playback (creator or subject)."""
        return self.component(item) > 0.0


@dataclass(frozen=True)
class RecommendationScore:
    """Inspectable, per-signal score for one candidate.

    Components are normalized to [0, 1]; ``total`` applies the module weights.
    """

    interest: float
    follow: float
    playback: float
    save: float
    recency: float
    popularity: float
    context: float = 0.0

    @property
    def total(self) -> float:
        return (
            WEIGHT_FOLLOW * self.follow
            + WEIGHT_PLAYBACK * self.playback
            + WEIGHT_CONTEXT * self.context
            + WEIGHT_SAVE * self.save
            + WEIGHT_INTEREST * self.interest
            + WEIGHT_RECENCY * self.recency
            + WEIGHT_POPULARITY * self.popularity
        )


def _as_utc_timestamp(value: datetime | None) -> float | None:
    if not isinstance(value, datetime):
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.timestamp()


def _recency_component(published_at: datetime | None, reference_time: datetime | None) -> float:
    """Freshness bucket derived from snapshot-anchored time, never a wall clock."""
    reference = _as_utc_timestamp(reference_time)
    published = _as_utc_timestamp(published_at)
    if reference is None or published is None:
        return 0.0
    age_days = max(0.0, (reference - published) / 86_400)
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.75
    if age_days <= 90:
        return 0.5
    if age_days <= 365:
        return 0.25
    return 0.0


def _popularity_component(view_count: int | None) -> float:
    if view_count is None or view_count < 0:
        return 0.0
    if view_count >= 10_000_000:
        return 1.0
    if view_count >= 1_000_000:
        return 0.75
    if view_count >= 100_000:
        return 0.5
    if view_count >= 10_000:
        return 0.25
    return 0.0


def score_candidate(
    item: PopularItem,
    signals: MemberActivitySignals,
    context: "PlaybackContext | None" = None,
) -> RecommendationScore:
    """Combine every durable signal into one inspectable, deterministic score.

    Interest strength is bucketed against the two strongest matched categories;
    playback and save affinity are bucketed by demonstrated depth (completed vs
    merely opened, repeated vs single save). Freshness is anchored to the
    snapshot reference time so identical inputs always score identically. When a
    playback ``context`` is supplied (Up Next), current-source affinity enters as
    one more quarter-step component; on Home it is absent and scores zero.
    """
    uploader_key = _channel_key(item.uploader)
    matched = signals.selected_interests.intersection(item.category_keys)
    interest = min(len(matched), 2) / 2

    follow = 1.0 if uploader_key and uploader_key in signals.followed_channels else 0.0

    if uploader_key and uploader_key in signals.completed_uploaders:
        playback = 1.0
    elif uploader_key and uploader_key in signals.played_uploaders:
        playback = 0.5
    else:
        playback = 0.0

    if uploader_key and uploader_key in signals.frequently_saved_uploaders:
        save = 1.0
    elif uploader_key and uploader_key in signals.saved_uploaders:
        save = 0.5
    else:
        save = 0.0

    return RecommendationScore(
        interest=interest,
        follow=follow,
        playback=playback,
        save=save,
        recency=_recency_component(item.published_at, signals.reference_time),
        popularity=_popularity_component(item.view_count),
        context=context.component(item) if context is not None else 0.0,
    )


# ---- Media titles (ADR 0011): the second candidate pool, library surfaces only ---------------------

TITLE_POOL_TYPES = ("movie", "series")
# Title history reads the newest plays only, like channel affinity.
_TITLE_HISTORY_LIMIT = _PLAYED_HISTORY_LIMIT
_CONTEXT_BOXSET = 1.0
_CONTEXT_PERSON = 0.75  # a shared person, or embedding cosine >= _CONTEXT_COSINE
_CONTEXT_GENRES = 0.5  # two or more shared genres
_CONTEXT_GENRE = 0.25
_CONTEXT_COSINE = 0.8


@dataclass(frozen=True)
class TitleFacts:
    """What one Media title contributes to scoring, casefolded so overlap ignores case."""

    id: str
    type: str
    boxset_id: str | None
    genres: frozenset[str]
    people: frozenset[str]
    created_at: datetime | None
    community_rating: float | None

    @classmethod
    def of(cls, title: MediaTitle) -> "TitleFacts":
        metadata = title.metadata_json if isinstance(title.metadata_json, dict) else {}
        genres = metadata.get("genres") if isinstance(metadata.get("genres"), list) else []
        people = metadata.get("people") if isinstance(metadata.get("people"), list) else []
        rating = metadata.get("community_rating")
        return cls(
            id=title.id, type=title.type, boxset_id=title.boxset_id,
            genres=frozenset(genre.casefold() for genre in genres if isinstance(genre, str)),
            people=frozenset(p["name"].casefold() for p in people if isinstance(p, dict) and isinstance(p.get("name"), str)),
            created_at=title.arrived_at,  # when its files arrived, else when a scan met it
            community_rating=float(rating) if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None,
        )

    def overlaps(self, other: "TitleFacts") -> bool:
        return bool(self.genres & other.genres or self.people & other.people)


@dataclass(frozen=True)
class TitleSignals:
    """A member's title history: completed and started movies/series, favorites, and the library's newest title."""

    completed: tuple[TitleFacts, ...] = ()
    started: tuple[TitleFacts, ...] = ()
    favorites: tuple[TitleFacts, ...] = ()
    reference_time: datetime | None = None


def _rating_component(rating: float | None) -> float:
    if rating is None:
        return 0.0
    for floor, component in ((8.0, 1.0), (7.0, 0.75), (6.0, 0.5), (5.0, 0.25)):
        if rating >= floor:
            return component
    return 0.0


def _anchor_component(title: TitleFacts, anchor: TitleFacts, cosine: float | None) -> float:
    if (anchor.boxset_id and title.boxset_id == anchor.boxset_id) or (anchor.type == "boxset" and title.boxset_id == anchor.id):
        return _CONTEXT_BOXSET
    if title.people & anchor.people or (cosine is not None and cosine >= _CONTEXT_COSINE):
        return _CONTEXT_PERSON
    shared = len(title.genres & anchor.genres)
    return _CONTEXT_GENRES if shared >= 2 else _CONTEXT_GENRE if shared == 1 else 0.0


def score_title(
    title: TitleFacts, signals: TitleSignals, anchor: TitleFacts | None = None, *, anchor_cosine: float | None = None,
) -> RecommendationScore:
    """The one score shape for a Media title; interest and follow do not apply to titles (0).

    playback: genre/people overlap with a completed title 1.0, else a started one 0.5; save: overlap with
    a favorite; context: anchor similarity (same boxset 1.0; shared person or cosine >= 0.8 0.75; two
    shared genres 0.5; one 0.25); recency: arrival (``MediaTitle.arrived_at``) against the newest title (no wall clock);
    popularity: community rating.
    """
    if any(title.overlaps(other) for other in signals.completed):
        playback = 1.0
    elif any(title.overlaps(other) for other in signals.started):
        playback = 0.5
    else:
        playback = 0.0
    return RecommendationScore(
        interest=0.0,
        follow=0.0,
        playback=playback,
        save=1.0 if any(title.overlaps(other) for other in signals.favorites) else 0.0,
        recency=_recency_component(title.created_at, signals.reference_time),
        popularity=_rating_component(title.community_rating),
        context=_anchor_component(title, anchor, anchor_cosine) if anchor is not None else 0.0,
    )


@dataclass(frozen=True)
class MemberRecommendationSnapshot:
    """Home-facing result from the shared member-scoped recommendation policy."""

    items: tuple[PopularItem, ...]
    categories: tuple[PopularCategorySnapshot, ...]
    state: str
    refreshing: bool
    stale: bool
    error: str | None
    last_success_at: object
    refreshed_at: object
    next_refresh_at: object


class MemberInterestService:
    """Persist only canonical Interest-category keys for the authenticated member."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_for(self, member: User) -> tuple[str, ...]:
        rows = self._db.query(MemberInterest.category_key).filter(MemberInterest.user_id == member.id).all()
        return _known_categories(key for (key,) in rows)

    def replace(self, member: User, keys: Iterable[str]) -> tuple[str, ...]:
        normalized = self.normalize(keys)
        self._db.query(MemberInterest).filter(MemberInterest.user_id == member.id).delete(synchronize_session=False)
        self._db.add_all(MemberInterest(id=str(uuid.uuid4()), user_id=member.id, category_key=key) for key in normalized)
        self._db.flush()
        return normalized

    @staticmethod
    def normalize(keys: Iterable[str]) -> tuple[str, ...]:
        requested = list(keys)
        if any(not isinstance(key, str) for key in requested):
            raise ValueError("Interest categories must be canonical category keys.")
        unknown = sorted({key for key in requested if key not in _CATEGORY_ORDER})
        if unknown:
            raise ValueError(f"Unknown interest category: {unknown[0]}")
        return tuple(sorted(set(requested), key=_CATEGORY_ORDER.__getitem__))


@dataclass(frozen=True)
class _Candidate:
    item: PopularItem
    key: str
    provider_position: int
    score: RecommendationScore


class MemberRecommendationPolicy:
    """Score and balance durable discovery candidates for one Household member.

    This is the shared policy seam for Home and future discovery surfaces
    (ADR 0007). It ranks a durable Popular snapshot against the member's
    Interest categories, followed channels, playback activity, deliberate
    saves, freshness, and popularity, and reserves a configurable share of
    positions for exploration. It never searches a provider or persists search
    text, and never mixes one member's signals into another's feed.

    "Followed channels" is deliberately a passed-in ``followed_channel_keys``
    seam rather than a durable lookup: a first-class follow feature is #87's
    scope and no follow store exists yet, so Home supplies an empty set today.
    #87/#89 plug real, member-scoped follow keys into the same parameter without
    any reranking change.
    """

    def __init__(self, db: Session, *, limit: int = 24, exploration_ratio: float = 0.25) -> None:
        if not 0.0 <= exploration_ratio < 1.0:
            raise ValueError("exploration_ratio must be at least zero and less than one.")
        self._db = db
        self._limit = limit
        self._exploration_ratio = exploration_ratio

    def home(
        self,
        member: User,
        interests: tuple[str, ...],
        source: PopularSnapshot,
        *,
        followed_channel_keys: frozenset[str] | Iterable[str] = frozenset(),
    ) -> MemberRecommendationSnapshot:
        return self._recommend(
            member, interests, source, candidates=source.items, context=None,
            followed_channel_keys=followed_channel_keys,
        )

    def up_next(
        self,
        member: User,
        interests: tuple[str, ...],
        source: PopularSnapshot,
        *,
        current: PlaybackContext,
        provider_related: Iterable[PopularItem] = (),
        followed_channel_keys: frozenset[str] | Iterable[str] = frozenset(),
    ) -> MemberRecommendationSnapshot:
        """Rank Up Next / related media through the one shared policy.

        Provider-related candidates are blended ahead of the local Popular pool,
        then the shared dedup/exclusion/scoring/exploration machinery ranks the
        combined stream with ``current`` playback affinity as a scored input.
        Either pool may be empty (degraded fallback); the other still ranks.
        """
        blended = tuple(provider_related) + tuple(source.items)
        return self._recommend(
            member, interests, source, candidates=blended, context=current,
            followed_channel_keys=followed_channel_keys,
        )

    def titles(
        self, member: User, *, anchor: str | None = None, types: Iterable[str] = TITLE_POOL_TYPES,
    ) -> list[MediaTitle]:
        """Rank unstarted, visible Media titles for library surfaces (ADR 0011).

        Same score shape, weights, suppression filter, ``_rank_key`` and exploration share as Home.
        Started or completed titles belong to Continue watching and Next up and are never offered.
        With an ``anchor`` (More like this, Jellyfin Similar) the anchor is the context, so the list
        fills even for a cold member; without one, a member with no established signal gets [].
        """
        anchor_title = self._visible_top_title(member, anchor) if anchor else None
        if anchor and anchor_title is None:
            return []
        history = self._title_history(member)
        favorites = set(self._db.scalars(select(MemberFavorite.target_id).where(MemberFavorite.user_id == member.id)))
        excluded = set(history) | self._suppressed_keys(member).title_keys | ({anchor_title.id} if anchor_title else set())
        candidates = [
            title for title in self._db.scalars(
                select(MediaTitle)
                .where(MediaTitle.type.in_(tuple(types)), LibraryService.visible_title_predicate(member))
                .order_by(func.lower(func.coalesce(MediaTitle.sort_name, MediaTitle.name)), MediaTitle.id)
            )
            if title.id not in excluded
        ]
        signal_ids = set(history) | favorites
        known = {t.id: TitleFacts.of(t) for t in self._db.scalars(
            select(MediaTitle).where(MediaTitle.id.in_(signal_ids), MediaTitle.type.not_in(("album", "artist")))  # music never steers video
        )} if signal_ids else {}
        facts = [TitleFacts.of(title) for title in candidates]
        created = [fact.created_at for fact in (*facts, *known.values()) if fact.created_at is not None]
        signals = TitleSignals(
            completed=tuple(known[i] for i, done in history.items() if done and i in known),
            started=tuple(known[i] for i, done in history.items() if not done and i in known),
            favorites=tuple(known[i] for i in sorted(favorites) if i in known),
            reference_time=max(created) if created else None,
        )
        anchor_facts = TitleFacts.of(anchor_title) if anchor_title is not None else None
        cosines = self._anchor_cosines(anchor_title) if anchor_title is not None else {}
        established: list[_Candidate] = []
        exploration: list[_Candidate] = []
        for position, (title, fact) in enumerate(zip(candidates, facts)):
            score = score_title(fact, signals, anchor_facts, anchor_cosine=cosines.get(title.id))
            candidate = _Candidate(item=title, key=title.id, provider_position=position, score=score)
            # Exploration = no genre/people overlap with anything the member watched, saved or is looking at.
            related = score.playback > 0 or score.save > 0 or score.context > 0
            (established if related else exploration).append(candidate)
        if not established and anchor_title is None:
            return []
        established.sort(key=self._rank_key)
        exploration.sort(key=self._rank_key)
        return [candidate.item for candidate in self._allocate(established, exploration)]

    def recently_completed(self, member: User, *, since: datetime | None = None, until: datetime | None = None,
                           limit: int = 3) -> list[MediaTitle]:
        """The member's newest ``limit`` distinct visible movies/series with a completion at or after ``since`` and before ``until``."""
        episode, season = aliased(MediaTitle), aliased(MediaTitle)
        rows = self._db.execute(
            select(episode.id, episode.type, season.parent_id)
            .select_from(PlaybackProgress)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(episode, episode.id == LibraryItem.title_id)
            .outerjoin(season, season.id == episode.parent_id)
            .where(
                PlaybackProgress.user_id == member.id, PlaybackProgress.completed.is_(True),
                LibraryItem.extra_type.is_(None),
                episode.type.in_(("movie", "episode")),  # a finished track is not "Because you watched" its album
                *(() if since is None else (PlaybackProgress.last_watched_at >= since,)),
                *(() if until is None else (PlaybackProgress.last_watched_at < until,)),
            )
            .order_by(PlaybackProgress.last_watched_at.desc(), PlaybackProgress.id)
            .limit(_TITLE_HISTORY_LIMIT)
        ).all()
        ordered = list(dict.fromkeys(series_id if kind == "episode" and series_id else leaf for leaf, kind, series_id in rows))
        if not ordered:
            return []
        visible = {t.id: t for t in self._db.scalars(
            select(MediaTitle).where(MediaTitle.id.in_(ordered), LibraryService.visible_title_predicate(member))
        )}
        return [visible[title_id] for title_id in ordered if title_id in visible][:limit]

    def _title_history(self, member: User, until: datetime | None = None) -> dict[str, bool]:
        """Top-level titles (movie, or the series of an episode) the member started -> whether any part was completed."""
        episode, season = aliased(MediaTitle), aliased(MediaTitle)
        rows = self._db.execute(
            select(episode.id, episode.type, season.parent_id, PlaybackProgress.completed)
            .select_from(PlaybackProgress)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(episode, episode.id == LibraryItem.title_id)
            .outerjoin(season, season.id == episode.parent_id)
            .where(PlaybackProgress.user_id == member.id, LibraryItem.extra_type.is_(None), episode.type.in_(("movie", "episode")),
                   *(() if until is None else (PlaybackProgress.last_watched_at < until,)))
            .order_by(PlaybackProgress.last_watched_at.desc(), PlaybackProgress.id)
            .limit(_TITLE_HISTORY_LIMIT)
        ).all()
        history: dict[str, bool] = {}
        for leaf, kind, series_id, completed in rows:
            top = series_id if kind == "episode" and series_id else leaf
            history[top] = history.get(top, False) or bool(completed)
        return history

    def _visible_top_title(self, member: User, title_id: str) -> MediaTitle | None:
        """The anchor as a movie/series/boxset: an episode or season anchors on its series."""
        title = self._db.scalar(select(MediaTitle).where(MediaTitle.id == title_id, LibraryService.visible_title_predicate(member)))
        while title is not None and title.type in ("episode", "season") and title.parent_id:
            title = self._db.get(MediaTitle, title.parent_id)
        return title

    def _anchor_cosines(self, anchor: MediaTitle) -> dict[str, float]:
        """Embedding cosine of every embedded title to the anchor; {} without an embedding model or anchor vector."""
        from app.services import embeddings  # embeddings -> yt_dlp_service; keep this module's import graph light

        choice = embeddings.serving(self._db)
        if choice is None:
            return {}
        vectors = embeddings.vector_map(self._db, choice.model_id)
        if anchor.id not in vectors:
            return {}
        anchor_vector = vectors[anchor.id][1]
        return {target: embeddings.cosine(anchor_vector, vector) for target, (kind, vector) in vectors.items() if kind == "title"}

    def _recommend(
        self,
        member: User,
        interests: tuple[str, ...],
        source: PopularSnapshot,
        *,
        candidates: Iterable[PopularItem],
        context: PlaybackContext | None,
        followed_channel_keys: frozenset[str] | Iterable[str],
    ) -> MemberRecommendationSnapshot:
        selected_categories = _known_categories(set(interests))
        selected = frozenset(selected_categories)
        followed = frozenset(_channel_key(key) for key in followed_channel_keys if _channel_key(key))

        raw: list[tuple[str, PopularItem, int]] = []
        seen: set[str] = set()
        for position, item in enumerate(candidates):
            key = _source_key(item.source, item.id, item.webpage_url, item.title, item.uploader)
            if key in seen or not self._playable_when_known(item.availability):
                continue
            seen.add(key)
            raw.append((key, item, position))

        signals = self._gather_signals(member, selected, followed, source)
        excluded = self._visible_source_keys(member, [(key, item) for key, item, _pos in raw])
        excluded |= signals.continue_watching_keys
        if context is not None:
            # Never re-offer the source the member is currently watching.
            excluded.add(context.source_key)

        # Central suppression: applied once here so every consumer (Home, Up
        # Next, related) drops a suppressed candidate uniformly. An item
        # suppression is an excluded source key; a channel suppression removes
        # every candidate from that channel, overriding even a follow boost.
        # Suppression only filters recommendations — it never removes a Library
        # item, erases playback history, or touches the follow automation.
        suppressed = self._suppressed_keys(member)
        excluded |= suppressed.item_keys

        established: list[_Candidate] = []
        exploration: list[_Candidate] = []
        for key, item, position in raw:
            if key in excluded or _channel_key(item.uploader) in suppressed.channel_keys:
                continue
            score = score_candidate(item, signals, context)
            uploader_key = _channel_key(item.uploader)
            # Established = connected to a demonstrated preference: a selected
            # interest, an explicit follow, a channel already watched or saved,
            # or (Up Next only) the current playback's creator/subject. Exploration
            # is what is left: genuinely unfamiliar channels and adjacent,
            # non-anchored material.
            is_established = (
                bool(selected.intersection(item.category_keys))
                or (bool(uploader_key) and uploader_key in followed)
                or signals.is_familiar(uploader_key)
                or (context is not None and context.is_related(item))
            )
            candidate = _Candidate(item=item, key=key, provider_position=position, score=score)
            (established if is_established else exploration).append(candidate)

        established = self._balance_established(established, selected_categories)
        exploration.sort(key=self._rank_key)
        if context is None:
            # Home: exploration is a reserved slice of an anchored feed, not a feed
            # on its own. With no established anchor in this snapshot keep the
            # deterministic degraded/empty state rather than filling Home with
            # entirely unfamiliar media; that is the separate Popular surface's job.
            ordered = self._allocate(established, exploration) if established else []
        else:
            # Up Next: the current playback is itself the anchor, so the surface
            # fills even for a cold member — moving them into adjacent/unfamiliar
            # work — and known not-playable candidates are held off the
            # autoplay-eligible front.
            ordered = self._allocate_playable_first(established, exploration)

        items = tuple(candidate.item for candidate in ordered)
        selected_category_snapshots = tuple(category for category in source.categories if category.key in selected)
        state = self._state_for_selected_categories(selected_category_snapshots, source.state)
        if not items and state == "ready":
            state = "empty"
        return MemberRecommendationSnapshot(
            items=items, categories=selected_category_snapshots, state=state, refreshing=source.refreshing,
            stale=any(category.state == "stale" for category in selected_category_snapshots),
            error=source.error, last_success_at=source.last_success_at, refreshed_at=source.refreshed_at,
            next_refresh_at=source.next_refresh_at,
        )

    @staticmethod
    def _rank_key(candidate: _Candidate) -> tuple[int, int, str]:
        # Highest combined score first; ties fall to the provider's own popularity
        # ordering, then a stable source key. The score total is an exact multiple
        # of 0.25, so scaling to an int keeps the comparison free of float noise.
        return (-round(candidate.score.total * 1000), candidate.provider_position, candidate.key)

    def _balance_established(self, candidates: list[_Candidate], selected_categories: tuple[str, ...]) -> list[_Candidate]:
        """Order established candidates by score, round-robining ties by category.

        Stronger combined scores still lead, so demonstrated signal always wins:
        candidates are emitted strictly by descending score. But *within* a group
        of equal-scored candidates the order round-robins across the member's
        selected categories, so a multi-interest member sees every interest
        represented instead of whatever order the provider returned (#85's
        guarantee). A candidate's category is the first selected one it matches in
        canonical order; anchor-only items with no selected category sort last.

        The rotation counter is global across score tiers, not reset per tie
        group: an item that already represented a category (including a
        higher-scored multi-category item) pushes that category to the back of
        the next tie's rotation. It only reorders *within* a tie, never across
        score boundaries.
        """
        category_index = {key: index for index, key in enumerate(selected_categories)}
        no_category = len(selected_categories)

        def primary_category(candidate: _Candidate) -> int:
            matched = [category_index[key] for key in candidate.item.category_keys if key in category_index]
            return min(matched) if matched else no_category

        emitted_per_category: dict[int, int] = defaultdict(int)
        ordered: list[_Candidate] = []
        by_score = sorted(candidates, key=lambda candidate: -round(candidate.score.total * 1000))
        for _score, tie_group in groupby(by_score, key=lambda candidate: -round(candidate.score.total * 1000)):
            buckets: dict[int, deque[_Candidate]] = defaultdict(deque)
            for candidate in sorted(tie_group, key=lambda candidate: (candidate.provider_position, candidate.key)):
                buckets[primary_category(candidate)].append(candidate)
            while buckets:
                category = min(buckets, key=lambda cat: (emitted_per_category[cat], cat))
                ordered.append(buckets[category].popleft())
                emitted_per_category[category] += 1
                if not buckets[category]:
                    del buckets[category]
        return ordered

    def _allocate(self, established: list[_Candidate], exploration: list[_Candidate]) -> list[_Candidate]:
        """Interleave established and exploration candidates deterministically.

        Exploration lands on an even cadence (about ``exploration_ratio`` of the
        feed) rather than being appended, and either pool backfills the other
        when one runs short so the feed still fills up to ``limit``.
        """
        total = min(self._limit, len(established) + len(exploration))
        result: list[_Candidate] = []
        established_index = 0
        exploration_index = 0
        emitted_exploration = 0
        for position in range(total):
            target_exploration = math.floor((position + 1) * self._exploration_ratio + 1e-9)
            exploration_available = exploration_index < len(exploration)
            established_available = established_index < len(established)
            prefer_exploration = target_exploration > emitted_exploration
            take_exploration = exploration_available and (prefer_exploration or not established_available)
            if take_exploration:
                result.append(exploration[exploration_index])
                exploration_index += 1
                emitted_exploration += 1
            else:
                result.append(established[established_index])
                established_index += 1
        return result

    def _allocate_playable_first(self, established: list[_Candidate], exploration: list[_Candidate]) -> list[_Candidate]:
        """Up Next allocation that keeps known not-playable candidates off the front.

        Capability-aware filtering lives here at the shared policy layer (not in a
        surface's UI): playable-or-unknown candidates are interleaved exactly as
        Home does, then any *known* not-playable candidate (a live/upcoming source
        in this snapshot) backfills the tail up to ``limit``. Autoplay advances to
        the first result, so a not-playable source can never occupy an
        autoplay-eligible position, yet it stays visible for the member to queue
        for download. When every candidate is not-playable they are still
        surfaced — the policy never fabricates playability.
        """
        playable_established = [candidate for candidate in established if self._candidate_playable(candidate.item)]
        playable_exploration = [candidate for candidate in exploration if self._candidate_playable(candidate.item)]
        ordered = self._allocate(playable_established, playable_exploration)
        if len(ordered) >= self._limit:
            return ordered
        deprioritized = sorted(
            (candidate for candidate in (*established, *exploration) if not self._candidate_playable(candidate.item)),
            key=self._rank_key,
        )
        return ordered + deprioritized[: self._limit - len(ordered)]

    @staticmethod
    def _candidate_playable(item: PopularItem) -> bool:
        """Whether a candidate is eligible for an autoplay-eligible position.

        Unknown capabilities stay eligible (the preview/player still gates a true
        failure); only a capability that positively reports ``can_play`` False —
        a live or upcoming source in this snapshot — is deprioritized.
        """
        capabilities = item.capabilities
        return capabilities is None or capabilities.can_play

    def _suppressed_keys(self, member: User):
        """Read the member's active suppressions for the central filter.

        Imported lazily: the suppression service depends on this module's stable
        identity helpers, so a module-level import would be circular.
        """
        from app.services.member_suppressions import MemberSuppressionService

        return MemberSuppressionService(self._db).active_keys(member)

    def _gather_signals(
        self,
        member: User,
        selected: frozenset[str],
        followed: frozenset[str],
        source: PopularSnapshot,
    ) -> MemberActivitySignals:
        played: set[str] = set()
        completed: set[str] = set()
        continue_watching: set[str] = set()

        remote_rows = (
            self._db.query(
                RemotePlaybackProgress.uploader,
                RemotePlaybackProgress.completed,
                RemotePlaybackProgress.cleared,
                RemotePlaybackProgress.position_seconds,
                RemotePlaybackProgress.extractor,
                RemotePlaybackProgress.remote_id,
                RemotePlaybackProgress.source_url,
            )
            .filter(RemotePlaybackProgress.user_id == member.id, RemotePlaybackProgress.cleared.is_(False))
            .all()
        )
        for uploader, is_completed, cleared, position, extractor, remote_id, source_url in remote_rows:
            uploader_key = _channel_key(uploader)
            if uploader_key:
                played.add(uploader_key)
                if is_completed:
                    completed.add(uploader_key)
            # An active resume point is a Continue Watching entry: exclude the
            # exact source, but keep its channel affinity above.
            if not cleared and not is_completed and (position or 0) > 0:
                continue_watching.add(_source_key(extractor or "youtube", remote_id, source_url, None, uploader))

        local_rows = (
            self._db.query(LibraryItem.uploader, PlaybackProgress.completed)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .filter(PlaybackProgress.user_id == member.id)
            .order_by(PlaybackProgress.last_watched_at.desc())
            .limit(_PLAYED_HISTORY_LIMIT)
            .all()
        )
        for uploader, is_completed in local_rows:
            uploader_key = _channel_key(uploader)
            if uploader_key:
                played.add(uploader_key)
                if is_completed:
                    completed.add(uploader_key)

        saved: set[str] = set()
        frequently_saved: set[str] = set()
        saved_rows = (
            self._db.query(LibraryItem.uploader, func.count().label("saves"))
            .filter(LibraryItem.user_id == member.id, LibraryItem.uploader.isnot(None))
            .group_by(LibraryItem.uploader)
            .order_by(func.count().desc(), LibraryItem.uploader)
            .limit(_SAVED_CHANNEL_LIMIT)
            .all()
        )
        for uploader, saves in saved_rows:
            uploader_key = _channel_key(uploader)
            if not uploader_key:
                continue
            saved.add(uploader_key)
            if saves >= _STRONG_SAVE_COUNT:
                frequently_saved.add(uploader_key)

        reference_time = source.refreshed_at if isinstance(source.refreshed_at, datetime) else source.last_success_at
        return MemberActivitySignals(
            selected_interests=selected,
            followed_channels=followed,
            played_uploaders=frozenset(played),
            completed_uploaders=frozenset(completed),
            saved_uploaders=frozenset(saved),
            frequently_saved_uploaders=frozenset(frequently_saved),
            continue_watching_keys=frozenset(continue_watching),
            reference_time=reference_time if isinstance(reference_time, datetime) else None,
        )

    def _visible_source_keys(self, member: User, candidates: Iterable[tuple[str, PopularItem]]) -> set[str]:
        """Fetch only visible Library records which can match this bounded feed."""
        candidate_keys = {key for key, _item in candidates}
        if not candidate_keys:
            return set()

        remote_ids: set[str] = set()
        webpage_urls: set[str] = set()
        text_titles: set[str] = set()
        text_uploaders: set[str] = set()
        for _key, item in candidates:
            if item.id and item.id.strip():
                remote_ids.add(item.id.strip())
            if item.webpage_url and item.webpage_url.strip():
                webpage_urls.add(item.webpage_url.strip())
            if not item.id and not item.webpage_url:
                text_titles.add(item.title)
                text_uploaders.add(item.uploader or "")

        candidate_predicates = []
        if remote_ids:
            candidate_predicates.append(LibraryItem.remote_id.in_(remote_ids))
        if webpage_urls:
            candidate_predicates.append(LibraryItem.webpage_url.in_(webpage_urls))
        if text_titles:
            candidate_predicates.append(and_(LibraryItem.title.in_(text_titles), LibraryItem.uploader.in_(text_uploaders)))
        if not candidate_predicates:
            return set()

        items = self._db.query(
            LibraryItem.extractor,
            LibraryItem.remote_id,
            LibraryItem.webpage_url,
            LibraryItem.title,
            LibraryItem.uploader,
        ).filter(
            LibraryService.visible_predicate(member),
            or_(*candidate_predicates),
        ).all()
        return {
            key
            for extractor, remote_id, webpage_url, title, uploader in items
            if (key := _source_key(extractor or "youtube", remote_id, webpage_url, title, uploader)) in candidate_keys
        }

    @staticmethod
    def _playable_when_known(availability: str | None) -> bool:
        return availability is None or availability.strip().casefold() not in _UNPLAYABLE_AVAILABILITY

    @staticmethod
    def _state_for_selected_categories(categories: tuple[PopularCategorySnapshot, ...], fallback: str) -> str:
        """Keep unrelated Popular categories from degrading a member's Home shelf."""
        if not categories:
            return fallback
        states = {category.state for category in categories}
        if states == {"failed"}:
            return "failed"
        if states == {"pending"}:
            return "loading"
        if "failed" in states or "pending" in states:
            return "partial"
        if "stale" in states:
            return "stale"
        if states == {"empty"}:
            return "empty"
        return "ready"
