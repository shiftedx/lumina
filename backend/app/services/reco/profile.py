"""The member profile and the token and candidate builders.

``build_profile`` reads one member's rows only: progress, saves, follows, the watch queue, interests,
suppressions and their own ``reco_events``. Public metadata comes from ``remote_media`` and visible ``media_titles``.
"""
from __future__ import annotations

import math
import operator
import re
import threading
from array import array
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import (
    LibraryItem, MediaTitle, MemberInterest, MemberRecommendationSuppression, PlaybackProgress, RecoEvent, RemoteMedia,
    RemotePlaybackProgress, SourceAutomation, User, WatchQueueEntry,
)
from app.services.library import LibraryService
from app.services.media_titles import person_name_id
from app.services.reco import CONSTANTS, SOURCE_LIBRARY, Candidate, Constants, MemberProfile, SatisfiedItem, events
from app.services.reco.ranking import age_days, clamp, decay
from app.services.remote_playback import RemotePlaybackProgressService

MAX_TOKENS = 40
HISTORY_LIMIT = 500  # newest progress, save and queue rows read per build, as the legacy policy reads
FATIGUE_WINDOW = timedelta(days=14)  # Fatigue reads 14 days of impressions
DIRECTOR_TYPES = frozenset({"Director", "Creator"})  # a series' creator stands in for a director
# Dropped from remote title tokens, with the words every upload title repeats.
STOPWORDS = frozenset("""
    a about after all also an and any are as at be been but by can could did do does for from had has have her his how
    i if in into is it its just me more most my new no not now of off on one only or our out over she so some than
    that the their them then there these they this those to too up us was we were what when where which who why will
    with you your official video videos full feat episode part
""".split())
_WORD = re.compile(r"[^\W_]+")


# ---- tokens and candidates --------------------------------------------------------------------------

def remote_tokens(title: str | None, channel_key: str | None, category_keys: Sequence[str]) -> frozenset[str]:
    """``ch:{channel}``, ``cat:{key}``, then the casefolded title words (≥ 3 characters, no stopwords) and their bigrams; ≤ 40."""
    words = [word for word in _WORD.findall((title or "").casefold()) if len(word) >= 3 and word not in STOPWORDS]
    ordered = [f"ch:{channel_key}"] if channel_key else []
    ordered += [f"cat:{key}" for key in category_keys]
    ordered += words + [f"{left} {right}" for left, right in zip(words, words[1:])]
    return frozenset(list(dict.fromkeys(ordered))[:MAX_TOKENS])


def _person_id(ref: Mapping) -> str:
    return ref.get("person_id") or person_name_id(ref["name"])


def _metadata(title: MediaTitle) -> dict:
    return _as_dict(title.metadata_json)


def _as_dict(metadata: object) -> dict:
    return metadata if isinstance(metadata, dict) else {}


def _genres(metadata: Mapping) -> list[str]:
    genres = metadata.get("genres")
    return [genre.strip().casefold() for genre in genres if isinstance(genre, str) and genre.strip()] if isinstance(genres, list) else []


def title_tokens(title: MediaTitle) -> frozenset[str]:
    """``genre:``, ``cast:`` (first 5 actors), ``dir:`` (directors and creators), ``col:``, ``cat:`` and ``decade:`` tokens."""
    return tokens_of(title.metadata_json, title.boxset_id, title.category, title.year)


def tokens_of(metadata: object, boxset_id: str | None, category: str | None, year: int | None) -> frozenset[str]:
    """``title_tokens`` from the columns alone, for callers that select them instead of loading the row."""
    metadata = _as_dict(metadata)
    people = metadata.get("people")
    people = [ref for ref in people if isinstance(ref, dict) and isinstance(ref.get("name"), str)] if isinstance(people, list) else []
    tokens = {f"genre:{genre}" for genre in _genres(metadata)}
    tokens |= {f"cast:{_person_id(ref)}" for ref in [ref for ref in people if ref.get("type") == "Actor"][:5]}
    tokens |= {f"dir:{_person_id(ref)}" for ref in people if ref.get("type") in DIRECTOR_TYPES}
    if boxset_id:
        tokens.add(f"col:{boxset_id}")
    if category:
        tokens.add(f"cat:{category}")
    if year:
        tokens.add(f"decade:{year // 10 * 10}")
    return frozenset(tokens)


def decode_vector(blob: bytes | None) -> array | None:
    if not blob:
        return None
    vector = array("f")
    vector.frombytes(blob)
    return vector


def candidate_from_remote(row: RemoteMedia, *, sources: int, seed_ref: str | None = None,
                          channel_view_percentile: float | None = None) -> Candidate:
    """A pooled or listed remote video as a candidate. Its stored tokens win; a row without them is tokenised here."""
    category_keys = tuple(key for key in (row.category_keys or []) if isinstance(key, str))
    stored = frozenset(token for token in (row.tokens or []) if isinstance(token, str))
    return Candidate(
        key=row.key, target_kind="remote", title=row.title or "", channel_key=row.channel_key, channel_name=row.uploader,
        tokens=stored or remote_tokens(row.title, row.channel_key, category_keys), published_at=row.published_at,
        sources=sources, seed_ref=seed_ref, category_keys=category_keys, views=row.view_count,
        channel_view_percentile=channel_view_percentile, vector=decode_vector(row.vector),
    )


def _rating(title: MediaTitle) -> float | None:
    return rating_of(title.metadata_json)


def rating_of(metadata: object) -> float | None:
    rating = _as_dict(metadata).get("community_rating")
    return float(rating) if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None


def candidate_from_title(title: MediaTitle, vector: array | None, *, sources: int = SOURCE_LIBRARY) -> Candidate:
    """A movie or series as a candidate; its "channel" is its collection, its age its arrival."""
    return Candidate(
        key=title.id, target_kind="title", title=title.name, channel_key=title.boxset_id, channel_name=None,
        tokens=title_tokens(title), published_at=title.arrived_at, sources=sources, rating=_rating(title), vector=vector,
    )


# ---- pure profile parts -----------------------------------------------------------------------------

def _unit(values: list[float]) -> list[float] | None:
    norm = math.sqrt(math.sumprod(values, values))
    return [value / norm for value in values] if norm else None


def _assign(points: Sequence[tuple[list[float], float]], centers: Sequence[list[float]]) -> list[list[int]]:
    members: list[list[int]] = [[] for _ in centers]
    for index, (vector, _weight) in enumerate(points):
        best = max(range(len(centers)), key=lambda c: (math.sumprod(vector, centers[c]), -c))
        members[best].append(index)
    return members


def centroids(vectors: Sequence[tuple[array, float]], k: Constants = CONSTANTS) -> tuple[tuple[array, ...], tuple[float, ...]]:
    """Weighted k-means over (unit vector, weight), newest first: k = min(4, ceil(n / 8)); the newest vector seeds the
    first centre, then repeatedly the point farthest (by 1 − cosine) from every chosen centre; 10 iterations; unit
    centres; mass = summed weight. Fewer than 3 vectors (of the newest vector's length): no centroids."""
    points = [(vector.tolist(), float(weight)) for vector, weight in vectors if vector is not None and len(vector)]
    points = [(vector, weight) for vector, weight in points if len(vector) == len(points[0][0])]
    if len(points) < k.centroid_min_vectors:
        return (), ()
    count = min(k.centroids_max, math.ceil(len(points) / k.vectors_per_centroid))
    centers = [points[0][0]]
    while len(centers) < count:
        farthest = max(range(len(points)),
                       key=lambda i: (min(1.0 - math.sumprod(points[i][0], center) for center in centers), -i))
        centers.append(points[farthest][0])
    for _ in range(k.kmeans_iterations):
        moved = []
        for center, members in zip(centers, _assign(points, centers)):
            total = [0.0] * len(center)
            for index in members:
                vector, weight = points[index]
                total = list(map(operator.add, total, map(weight.__mul__, vector)))
            moved.append(_unit(total) or center if members else center)
        if moved == centers:
            break
        centers = moved
    mass = tuple(math.fsum(points[i][1] for i in members) for members in _assign(points, centers))
    return tuple(array("f", center) for center in centers), mass


def taste_mix(satisfied: Sequence[SatisfiedItem], interests: frozenset[str], centroid_mass: Sequence[float],
              k: Constants = CONSTANTS) -> dict[str, float]:
    """p(g): centroid masses when centroids exist; else 0.7·history + 0.3·declared interests over category
    keys (remote) and first genres (titles), each item's weight split over its groups; either alone when the other is empty."""
    mass_total = math.fsum(centroid_mass)
    if mass_total > 0:
        return {f"centroid:{index}": mass / mass_total for index, mass in enumerate(centroid_mass)}
    history: dict[str, float] = defaultdict(float)
    for item in satisfied:
        for group in item.category_keys:
            history[group] += item.weight / len(item.category_keys)
    total = math.fsum(history.values())
    p_history = {group: value / total for group, value in history.items()} if total > 0 else {}
    p_declared = {group: 1.0 / len(interests) for group in interests}
    if p_history and p_declared:
        return {group: k.mix_history * p_history.get(group, 0.0) + k.mix_declared * p_declared.get(group, 0.0)
                for group in p_history.keys() | p_declared.keys()}
    return p_history or p_declared


def fatigue_counts(rows: Sequence[tuple[datetime, str, str, str | None, str | None]], *, now: datetime,
                   aliases: Mapping[str, str], k: Constants = CONSTANTS) -> tuple[dict[str, float], dict[str, float], frozenset[str]]:
    """(n_i, n_c, fatigued-out keys) from (at, kind, item_key, channel_key, surface) rows of the last 14 days, oldest first.

    An impression counts once per (item, surface, 30 min) and only while no ``open`` or ``play`` of the item follows
    it. n_i decays with a 3-day half-life, n_c with 7 days; 4 such impressions put the item out."""
    last_open: dict[str, datetime] = {}
    for at, kind, item_key, _channel, _surface in rows:
        if kind in ("open", "play"):
            last_open[item_key] = max(at, last_open.get(item_key, at))
    counted_at: dict[tuple[str, str | None], datetime] = {}
    item_fatigue: dict[str, float] = defaultdict(float)
    channel_fatigue: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    window = timedelta(minutes=k.impression_window_minutes)
    for at, kind, item_key, channel_key, surface in rows:
        if kind != "impression" or (item_key in last_open and last_open[item_key] >= at):
            continue
        previous = counted_at.get((item_key, surface))
        if previous is not None and at - previous < window:
            continue
        counted_at[(item_key, surface)] = at
        age = age_days(at, now) or 0.0
        counts[item_key] += 1
        item_fatigue[item_key] += decay(age, k.item_fatigue_half_life_days)
        if channel_key:
            channel_fatigue[aliases.get(channel_key, channel_key)] += decay(age, k.channel_fatigue_half_life_days)
    return dict(item_fatigue), dict(channel_fatigue), frozenset(key for key, count in counts.items() if count >= k.fatigue_exclude_count)


def suppressed_item_keys(target_key: str) -> frozenset[str]:
    """An item suppression's ``_source_key`` and, for an ``id:`` or ``url:`` key, the ``remote_media.key`` it names (gap G-R3c)."""
    keys = {target_key}
    prefix, _, rest = target_key.partition(":")
    identity = rest if prefix == "id" else target_key if prefix == "url" else None
    if identity:
        try:
            keys.add(RemotePlaybackProgressService.source_identity_key(RemotePlaybackProgressService.canonical_source_identity(identity)))
        except ValueError:
            pass
    return frozenset(keys)


# ---- the profile -----------------------------------------------------------------------------------------------

def _vectors(db: Session) -> tuple[str | None, Mapping[str, tuple[str, array]]]:
    """The serving embedding model and its resident vectors; (None, {}) when the embedder is off or absent."""
    from app.services import embeddings  # embeddings -> yt_dlp_service; keep this module's import graph light

    choice = embeddings.serving(db)
    return (choice.model_id, embeddings.vector_map(db, choice.model_id)) if choice is not None else (None, {})


def build_profile(db: Session, member_id: str, *, now: datetime, as_of: datetime | None = None) -> MemberProfile:
    """Everything ranking knows about one member, from that member's rows only.

    ``as_of`` (the replay) reads only rows from before it and measures every age from it; live calls leave it None.
    """
    k = CONSTANTS
    at = as_of or now
    generation = events.generations.get(member_id)
    member = db.get(User, member_id)
    aliases: dict[str, str] = {}
    totals: dict[str, float] = defaultdict(float)
    excluded: set[str] = set()
    satisfied: list[SatisfiedItem] = []
    model_id, title_vectors = _vectors(db)

    def add(channel: str | None, weight: float, when: datetime | None, half_life: float | None) -> None:
        if channel:
            totals[channel] += weight * (decay(age_days(when, at) or 0.0, half_life) if half_life else 1.0)

    def alias(extractor: str | None, uploader: str | None, stable: str | None) -> str | None:
        legacy = events.name_key(extractor, uploader)
        if stable and legacy and legacy != stable:
            aliases[legacy] = stable
        return stable or legacy

    def watched(channel: str | None, when: datetime, *, completed: bool, completions: int, fraction: float,
                position: float, dismissed: bool) -> bool:
        """The play evidence for one progress row; returns whether the row was skipped."""
        skipped = False
        if completed:
            add(channel, k.w_completion, when, k.play_half_life_days)
            if completions >= 2:
                add(channel, k.w_rewatch, when, k.play_half_life_days)
        elif events.is_skip(position_seconds=position, max_fraction=fraction, completions=completions, last_watched_at=when, now=at):
            add(channel, k.w_skip, when, k.play_half_life_days)
            skipped = True
        else:
            add(channel, k.w_partial * clamp((fraction - k.partial_floor) / k.partial_span), when, k.play_half_life_days)
        if dismissed and not completed:
            add(channel, k.w_dismiss, when, k.play_half_life_days)
        return skipped

    def weight(when: datetime, completions: int) -> float:
        return (1.0 + k.rewatch_bonus * (completions >= 2)) * decay(age_days(when, at) or 0.0, k.satisfied_half_life_days)

    # Remote progress: affinity, exclusions and satisfied remote history.
    remote_satisfied: list[tuple[RemotePlaybackProgress, str | None]] = []
    for row in db.scalars(
        select(RemotePlaybackProgress)
        .where(RemotePlaybackProgress.user_id == member_id, RemotePlaybackProgress.last_watched_at < at)
        .order_by(RemotePlaybackProgress.last_watched_at.desc(), RemotePlaybackProgress.id).limit(HISTORY_LIMIT)
    ):
        channel = alias(row.extractor, row.uploader, row.channel_key)
        completed = bool(row.completed) or row.completions >= 1 or row.max_fraction >= 0.9
        skipped = watched(channel, row.last_watched_at, completed=completed, completions=row.completions, fraction=row.max_fraction,
                          position=row.position_seconds or 0.0, dismissed=bool(row.cleared))
        if completed or skipped or (not row.cleared and (row.position_seconds or 0) > 0):
            excluded.add(row.source_identity_key)  # completed, skipped, or in Continue watching
        if completed or row.max_fraction >= k.satisfied_fraction:
            remote_satisfied.append((row, channel))
    media = {row.key: row for row in db.scalars(
        select(RemoteMedia).where(RemoteMedia.key.in_([row.source_identity_key for row, _ in remote_satisfied])))} if remote_satisfied else {}
    for row, channel in remote_satisfied:
        cached = media.get(row.source_identity_key)
        category_keys = tuple(cached.category_keys or ()) if cached is not None else ()
        tokens = frozenset(cached.tokens or ()) if cached is not None and cached.tokens else remote_tokens(row.title, channel, category_keys)
        vector = decode_vector(cached.vector) if cached is not None and model_id and cached.vector_model == model_id else None
        satisfied.append(SatisfiedItem(key=row.source_identity_key, target_kind="remote", title=row.title or "", channel_key=channel,
                                       tokens=tokens, weight=weight(row.last_watched_at, row.completions), at=row.last_watched_at,
                                       category_keys=category_keys, vector=vector))

    # Local progress: saved web videos count like remote ones; Library titles roll up to the movie or series.
    episode, season = aliased(MediaTitle), aliased(MediaTitle)
    title_rows: dict[str, list] = {}
    for progress, item_id, item_title, extractor, uploader, item_channel, title_id, leaf_type, series_id in db.execute(
        select(PlaybackProgress, LibraryItem.id, LibraryItem.title, LibraryItem.extractor, LibraryItem.uploader,
               LibraryItem.channel_key, LibraryItem.title_id, episode.type, season.parent_id)
        .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
        .outerjoin(episode, episode.id == LibraryItem.title_id)
        .outerjoin(season, season.id == episode.parent_id)
        .where(PlaybackProgress.user_id == member_id, PlaybackProgress.last_watched_at < at, LibraryItem.extra_type.is_(None))
        .order_by(PlaybackProgress.last_watched_at.desc(), PlaybackProgress.id).limit(HISTORY_LIMIT)
    ):
        completed = bool(progress.completed) or progress.completions >= 1 or progress.max_fraction >= 0.9
        if title_id is None:
            channel = alias(extractor, uploader, item_channel)
            watched(channel, progress.last_watched_at, completed=completed, completions=progress.completions,
                    fraction=progress.max_fraction, position=progress.position_seconds or 0, dismissed=progress.dismissed_at is not None)
            if completed or progress.max_fraction >= k.satisfied_fraction:
                vector = title_vectors.get(item_id)
                satisfied.append(SatisfiedItem(
                    key=item_id, target_kind="remote", title=item_title, channel_key=channel, tokens=remote_tokens(item_title, channel, ()),
                    weight=weight(progress.last_watched_at, progress.completions), at=progress.last_watched_at,
                    vector=vector[1] if vector is not None else None))
            continue
        top = series_id if leaf_type == "episode" and series_id else title_id
        entry = title_rows.setdefault(top, [progress.last_watched_at, False, 0.0, 0, leaf_type])
        entry[1] = entry[1] or completed
        entry[2] = max(entry[2], progress.max_fraction)
        entry[3] = max(entry[3], progress.completions)
    if title_rows and member is not None:
        visible = {title.id: title for title in db.scalars(
            select(MediaTitle).where(MediaTitle.id.in_(list(title_rows)), LibraryService.visible_title_predicate(member)))}
        for top, (when, completed, fraction, completions, leaf_type) in title_rows.items():
            if completed and leaf_type == "movie":
                excluded.add(top)
            title = visible.get(top)
            if title is None or not (completed or fraction >= k.satisfied_fraction):
                continue
            genres = _genres(_metadata(title))
            vector = title_vectors.get(top)
            satisfied.append(SatisfiedItem(key=top, target_kind="title", title=title.name, channel_key=title.boxset_id,
                                           tokens=title_tokens(title), weight=weight(when, completions), at=when,
                                           category_keys=(f"genre:{genres[0]}",) if genres else (),
                                           vector=vector[1] if vector is not None else None))

    # Saves, follows and the watch queue.
    for extractor, uploader, stable, created_at in db.execute(
        select(LibraryItem.extractor, LibraryItem.uploader, LibraryItem.channel_key, LibraryItem.created_at)
        .where(LibraryItem.user_id == member_id, LibraryItem.title_id.is_(None), LibraryItem.created_at < at)
        .order_by(LibraryItem.created_at.desc()).limit(HISTORY_LIMIT)
    ):
        add(alias(extractor, uploader, stable), k.w_save, created_at, k.save_half_life_days)
    followed: set[str] = set()
    for source_url, label in db.execute(
        select(SourceAutomation.source_url, SourceAutomation.label).where(
            SourceAutomation.user_id == member_id, SourceAutomation.source_type == "channel", SourceAutomation.active.is_(True),
            SourceAutomation.created_at < at)
    ):
        if channel := events.channel_key(None, None, source_url, label):
            followed.add(channel)
            add(channel, k.w_follow, None, None)
    for provider, uploader, created_at in db.execute(
        select(WatchQueueEntry.provider, WatchQueueEntry.uploader, WatchQueueEntry.created_at).where(
            WatchQueueEntry.user_id == member_id, WatchQueueEntry.library_item_id.is_(None), WatchQueueEntry.created_at < at)
        .limit(HISTORY_LIMIT)
    ):
        add(events.name_key(provider, uploader), k.w_queue, created_at, k.play_half_life_days)

    # Interests and explicit feedback.
    interests = frozenset(db.scalars(select(MemberInterest.category_key).where(
        MemberInterest.user_id == member_id, MemberInterest.created_at < at)))
    hidden_items: set[str] = set()
    hidden_channels: set[str] = set()
    hidden_titles: set[str] = set()
    fewer: dict[str, datetime] = {}
    spill: dict[str, datetime] = {}
    for scope, target_key, stable, channel_name, source, created_at in db.execute(
        select(MemberRecommendationSuppression.scope, MemberRecommendationSuppression.target_key,
               MemberRecommendationSuppression.channel_key, MemberRecommendationSuppression.channel_name,
               MemberRecommendationSuppression.source, MemberRecommendationSuppression.created_at)
        .where(MemberRecommendationSuppression.user_id == member_id, MemberRecommendationSuppression.created_at < at)
    ):
        if scope == "item":
            hidden_items |= suppressed_item_keys(target_key)
            if channel := alias(source, channel_name, stable):
                spill[channel] = max(created_at, spill.get(channel, created_at))
                add(channel, k.w_not_interested, created_at, k.not_interested_half_life_days)
        elif scope == "channel":
            hidden_channels.add(stable or target_key)
        elif scope == "fewer":
            key = stable or target_key
            fewer[key] = max(created_at, fewer.get(key, created_at))
        elif scope == "title":
            hidden_titles.add(target_key)

    # Impression fatigue from the member's own events (ix_reco_events_user_at).
    item_fatigue, channel_fatigue, fatigued_out = fatigue_counts(db.execute(
        select(RecoEvent.at, RecoEvent.kind, RecoEvent.item_key, RecoEvent.channel_key, RecoEvent.surface)
        .where(RecoEvent.user_id == member_id, RecoEvent.at >= at - FATIGUE_WINDOW, RecoEvent.at < at,
               RecoEvent.kind.in_(("impression", "open", "play")))
        .order_by(RecoEvent.at, RecoEvent.id)
    ).all(), now=at, aliases=aliases, k=k)

    affinity: dict[str, float] = defaultdict(float)
    for key, total in totals.items():
        affinity[aliases.get(key, key)] += total  # a legacy name key's history counts for its stable key

    def latest(dates: Mapping[str, datetime]) -> dict[str, datetime]:
        merged: dict[str, datetime] = {}
        for key, when in dates.items():
            key = aliases.get(key, key)
            merged[key] = max(when, merged.get(key, when))
        return merged

    satisfied.sort(key=lambda item: item.at, reverse=True)
    history = tuple(satisfied[: k.satisfied_limit])
    centres, mass = centroids([(item.vector, item.weight) for item in history if item.vector is not None], k)
    return MemberProfile(
        user_id=member_id, built_at=now, generation=generation, affinity=dict(affinity), channel_aliases=aliases,
        followed=frozenset(aliases.get(key, key) for key in followed), interests=interests, satisfied=history,
        centroids=centres, centroid_mass=mass, taste_mix=taste_mix(history, interests, mass, k),
        item_fatigue=item_fatigue, channel_fatigue=channel_fatigue, fatigued_out=fatigued_out,
        fewer=latest(fewer), spill=latest(spill),
        hidden_items=frozenset(hidden_items), hidden_channels=frozenset(aliases.get(key, key) for key in hidden_channels),
        hidden_titles=frozenset(hidden_titles), excluded=frozenset(excluded),
    )


class ProfileCache:
    """``{user_id: MemberProfile}`` rebuilt when older than 10 minutes or when ``events.generations`` moved.

    A stale profile is rebuilt on the calling thread (≤ 80 ms), not served while the refresh worker
    rebuilds it; move the rebuild onto ``RecoRefresher`` if Home's cold budget is missed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profiles: dict[str, MemberProfile] = {}

    def get(self, db: Session, member_id: str, *, now: datetime) -> MemberProfile:
        with self._lock:
            cached = self._profiles.get(member_id)
        if (cached is not None and cached.generation == events.generations.get(member_id)
                and now - cached.built_at < timedelta(minutes=CONSTANTS.profile_ttl_minutes)):
            return cached
        profile = build_profile(db, member_id, now=now)
        with self._lock:
            current = self._profiles.get(member_id)
            if current is None or current.built_at <= profile.built_at:
                self._profiles[member_id] = profile
        return profile


profiles = ProfileCache()
