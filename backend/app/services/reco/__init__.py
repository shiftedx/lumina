"""Recommendations: the value types every module shares.

Frozen: the other modules (events.py, pool.py, profile.py, ranking.py, policy.py, metrics.py) sit beside this file
and never edit it.
"""
from __future__ import annotations

from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Literal

from sqlalchemy.orm import Session

from app.media_schemas import RecoReasonCode, RecoSlot, RecoSurface

FEATURE_KEY = "personal_recommendations"  # AppSettings.ai_features_disabled kill switch

TargetKind = Literal["remote", "title"]
EventKind = Literal["impression", "open", "play", "complete", "not_interested", "fewer", "hide_channel", "restore"]

# Candidate.sources and reco_pool.sources bits. The first five are stored in reco_pool; the rest are read
# when a request is served and never stored.
SOURCE_FOLLOW = 1
SOURCE_CHANNEL = 2
SOURCE_SEED = 4
SOURCE_INTEREST = 8
SOURCE_HISTORY = 16  # a watched item kept only so it gets a vector; never a candidate
SOURCE_POPULAR = 32
SOURCE_LIBRARY = 64
SOURCE_CURRENT_CHANNEL = 128

# Exploration slots per 12 positions, at the tail of each window.
SURFACE_EXPLORE: Mapping[RecoSurface, int] = MappingProxyType({
    "home_picked": 2, "home_recommended": 2, "explore_for_you": 2,
    "home_because": 1, "up_next": 1, "title_similar": 1,
    "explore_popular": 0,
})
# Served list length per surface; up_next and title_similar take the request's limit, defaulting to this.
SURFACE_K: Mapping[RecoSurface, int] = MappingProxyType({
    "home_picked": 20, "home_recommended": 20, "home_because": 20, "explore_for_you": 24,
    "explore_popular": 24, "up_next": 12, "title_similar": 12,
})


def explore_positions(k: int, per_12: int) -> frozenset[int]:
    """1-based positions reserved for exploration: the last ``per_12`` of every 12 (2 → 11, 12, 23, 24)."""
    return frozenset(p for p in range(1, k + 1) if per_12 and (p - 1) % 12 >= 12 - per_12)


@dataclass(frozen=True)
class Constants:
    """Every ranking constant in one place. A change ships with a replay run (ADR 0015)."""

    # Channel affinity A_c: weight per evidence, half-lives in days; a_c = 1 - exp(-max(A_c, 0) / affinity_scale).
    w_follow: float = 3.0
    w_save: float = 2.0
    save_half_life_days: float = 180.0
    w_completion: float = 1.5
    w_rewatch: float = 1.0
    w_partial: float = 1.0
    partial_floor: float = 0.1  # partial weight = clamp((max_fraction - floor) / span, 0, 1)
    partial_span: float = 0.6
    w_skip: float = -0.5
    w_queue: float = 0.5
    w_dismiss: float = -0.25
    play_half_life_days: float = 30.0
    w_not_interested: float = -1.0
    not_interested_half_life_days: float = 60.0
    affinity_scale: float = 3.0
    # A skip: max_fraction < skip_fraction, no completion, position < skip_seconds, last checkpoint ≥ skip_settle_minutes old.
    skip_fraction: float = 0.1
    skip_seconds: float = 30.0
    skip_settle_minutes: float = 30.0
    # Title affinity: same collection, shared director, ≥ 2 shared cast, 1 shared cast.
    title_collection: float = 1.0
    title_director: float = 0.6
    title_cast_two: float = 0.4
    title_cast_one: float = 0.2
    # Satisfied history and taste.
    satisfied_fraction: float = 0.5
    satisfied_limit: int = 100
    satisfied_half_life_days: float = 60.0
    rewatch_bonus: float = 0.5
    centroids_max: int = 4
    vectors_per_centroid: int = 8
    centroid_min_vectors: int = 3
    kmeans_iterations: int = 10
    jaccard_seeds: int = 50
    mix_history: float = 0.7
    mix_declared: float = 0.3
    # Score: R = 1 + w_a·a + w_s·s + w_m·m.
    w_affinity: float = 2.0
    w_topic: float = 2.0
    w_interest: float = 0.5
    remote_fresh_boost: float = 0.6
    remote_fresh_tau_days: float = 7.0
    title_fresh_boost: float = 0.3
    title_fresh_tau_days: float = 30.0
    remote_quality_base: float = 0.75
    remote_quality_span: float = 0.5
    views_log10_full: float = 7.0
    channel_percentile_min_items: int = 5
    title_quality_base: float = 0.8
    title_quality_span: float = 0.4
    rating_floor: float = 5.0
    rating_span: float = 4.0
    # Fatigue.
    item_fatigue_k: float = 0.5
    item_fatigue_half_life_days: float = 3.0
    channel_fatigue_k: float = 0.05
    channel_fatigue_half_life_days: float = 7.0
    channel_fatigue_floor: float = 0.5
    impression_window_minutes: float = 30.0
    fatigue_exclude_count: int = 4
    fatigue_exclude_days: float = 14.0
    # Feedback: show fewer = min(1, floor + step·floor(days / step_days)); Not interested channel spill.
    fewer_floor: float = 0.2
    fewer_step: float = 0.2
    fewer_step_days: float = 35.0
    spill_floor: float = 0.7
    spill_days: float = 30.0
    # Surface context.
    w_context_current: float = 2.0
    w_context_same_channel: float = 1.0
    w_context_anchor: float = 3.0
    w_context_collection: float = 1.0
    follow_new_days: float = 7.0
    next_part_overlap: float = 0.6
    follows_max_in_picked: int = 6
    # Diversity.
    channel_floor: float = 0.4
    current_channel_floor: float = 0.7
    channel_cap_per_20: int = 4
    rank_pool: int = 100
    calibration_lambda: float = 0.3
    calibration_alpha: float = 0.01
    dpp_alpha: float = 0.9
    dpp_sigma: float = 0.7
    dpp_window: int = 12
    dpp_quality_floor: float = 0.1
    dpp_epsilon: float = 1e-9
    # Exploration.
    explore_top_m: int = 40
    explore_temperature: float = 0.3
    explore_min_topic: float = 0.25
    # Reasons.
    reason_name_chars: int = 32
    reason_chars: int = 64
    reason_weak: float = 0.3
    new_arrival_days: float = 30.0
    # Caches.
    profile_ttl_minutes: float = 10.0
    list_ttl_minutes: float = 30.0
    list_cache_size: int = 512


CONSTANTS = Constants()


@dataclass(frozen=True)
class Candidate:
    """One remote video or library title a list may contain. Built by pool.py from profile.py's builders.

    ``tokens`` are: remote words, bigrams, ``ch:{channel_key}``, ``cat:{key}``; titles ``genre:{name}``,
    ``cast:{person_id}`` (first 5), ``dir:{person_id}``, ``col:{boxset_id}``, ``cat:{category}``, ``decade:{1990}``.
    For a title, ``channel_key`` is its collection or boxset id (the franchise the channel discount groups by).
    """

    key: str  # remote_media.key (64 hex) or media_titles.id
    target_kind: TargetKind
    title: str
    channel_key: str | None
    channel_name: str | None
    tokens: frozenset[str]
    published_at: datetime | None  # remote: upload date; title: added_at
    sources: int  # SOURCE_* bits
    seed_ref: str | None = None
    category_keys: tuple[str, ...] = ()
    views: int | None = None
    channel_view_percentile: float | None = None  # within-channel percentile when ≥ 5 items of the channel are known
    rating: float | None = None  # title community rating
    vector: array | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SatisfiedItem:
    """One row of the member's satisfied history: a seed for reasons, centroids and the Jaccard fallback."""

    key: str
    target_kind: TargetKind
    title: str
    channel_key: str | None
    tokens: frozenset[str]
    weight: float
    at: datetime
    category_keys: tuple[str, ...] = ()
    vector: array | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class MemberProfile:
    """Everything ranking knows about one member, built from that member's rows only. Never shared."""

    user_id: str
    built_at: datetime
    generation: int
    affinity: Mapping[str, float]  # channel key -> A_c (decayed sum, may be negative)
    channel_aliases: Mapping[str, str]  # legacy name key -> stable channel key
    followed: frozenset[str]  # stable or name channel keys of active follows
    interests: frozenset[str]  # selected Interest category keys
    satisfied: tuple[SatisfiedItem, ...]  # newest first, ≤ satisfied_limit
    centroids: tuple[array, ...] = field(compare=False, repr=False)
    centroid_mass: tuple[float, ...]
    taste_mix: Mapping[str, float]  # p(g): g = "centroid:{i}", else a category key or "genre:{name}"
    item_fatigue: Mapping[str, float]  # item key -> n_i
    channel_fatigue: Mapping[str, float]  # channel key -> n_c
    fatigued_out: frozenset[str]  # item keys with ≥ 4 unopened impressions in 14 days
    fewer: Mapping[str, datetime]  # channel key -> newest Show fewer
    spill: Mapping[str, datetime]  # channel key -> newest Not interested on one of its items
    hidden_items: frozenset[str]  # item suppressions (target keys)
    hidden_channels: frozenset[str]  # channel suppressions (stable and name keys)
    hidden_titles: frozenset[str]
    excluded: frozenset[str]  # completed, skipped and Continue watching item keys


@dataclass(frozen=True)
class SurfaceContext:
    """What one list request knows beyond the member."""

    surface: RecoSurface
    k: int
    local_date: date  # the exploration seed's day
    context_key: str = "-"  # anchor title id or current item key; part of the list-cache and seed key
    anchor: Candidate | None = None  # the title anchor, or the current video on up_next
    pinned_key: str | None = None  # next part or next episode (up_next)


@dataclass(frozen=True)
class Ranked:
    """One served position."""

    candidate: Candidate
    position: int  # 0-based
    slot: RecoSlot
    score: float
    p_shown: float | None  # exploration draws only
    reason_code: RecoReasonCode
    reason: str


@dataclass(frozen=True)
class ServedList:
    """A list as served, kept 30 minutes so events and repeat requests read exactly what the member saw."""

    list_id: str  # 16 hex
    user_id: str
    surface: RecoSurface
    context_key: str
    created_at: datetime
    items: tuple[Ranked, ...]


def enabled(db: Session) -> bool:
    """False when the admin switched personalised recommendations off; every surface then serves the legacy policy."""
    from app.services.yt_dlp_service import YtDlpService  # keep this package's import graph light

    return FEATURE_KEY not in (YtDlpService(db).get_app_settings().ai_features_disabled or [])
