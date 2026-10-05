"""Recommendation ranking: pure functions, no database, no I/O.

``rank`` turns one surface's candidates into a served list: vetoes, the hybrid score, the channel discount and cap,
calibration, a windowed DPP, seeded exploration and a one-line reason per item. Everything it knows about the member
arrives in a ``MemberProfile`` (profile.py); every constant comes from ``CONSTANTS`` (reco/__init__.py).
"""
from __future__ import annotations

import hashlib
import math
import random
from array import array
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from types import MappingProxyType

from app.media_schemas import RecoReasonCode, RecoSlot
from app.services.popular_discovery import POPULAR_CATEGORIES
from app.services.reco import (
    CONSTANTS, SOURCE_CHANNEL, SOURCE_FOLLOW, SOURCE_HISTORY, SOURCE_SEED, SURFACE_EXPLORE, Candidate, Constants,
    MemberProfile, Ranked, SatisfiedItem, SurfaceContext, explore_positions,
)

DAY_SECONDS = 86_400.0
ANCHORED = frozenset({"up_next", "title_similar", "home_because"})  # surfaces whose context term C_i uses an anchor
CALIBRATED = frozenset({"home_picked", "home_recommended", "explore_for_you"})
_CATEGORY_LABELS = {category.key: category.label for category in POPULAR_CATEGORIES}
_NO_NAMES: Mapping[str, str] = MappingProxyType({})


# ---- small maths --------------------------------------------------------------------------------------------------

def age_days(at: datetime | None, now: datetime) -> float | None:
    return None if at is None else max(0.0, (now - at).total_seconds() / DAY_SECONDS)


def decay(age: float, half_life_days: float) -> float:
    """0.5^(age / half-life): the weight of evidence ``age`` days old."""
    return 0.5 ** (max(age, 0.0) / half_life_days)


def affinity(total: float, k: Constants = CONSTANTS) -> float:
    """a_c = 1 − exp(−max(A_c, 0) / 3) ∈ [0, 1)."""
    return 1.0 - math.exp(-max(total, 0.0) / k.affinity_scale)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    return (shared := len(left & right)) / (len(left) + len(right) - shared) if left and right else 0.0  # no union set


def cosine(left: array | None, right: array | None) -> float | None:
    """Cosine of two unit vectors; None when either is missing or their lengths differ (another model's vector)."""
    if left is None or right is None or not len(left) or len(left) != len(right):
        return None
    return math.sumprod(left, right)


def similarity(left_vector: array | None, left_tokens: frozenset[str], right_vector: array | None, right_tokens: frozenset[str]) -> float:
    """Cosine when both vectors exist in one space, else token Jaccard."""
    value = cosine(left_vector, right_vector)
    return value if value is not None else jaccard(left_tokens, right_tokens)


def percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an ascending sequence (q in [0, 1])."""
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def normalise_topic(raw: Sequence[float]) -> list[float]:
    """s_i = clamp((c_i − P50) / (P99 − P50), 0, 1) over this list's candidates; all 0 when the spread is 0."""
    if not raw:
        return []
    ordered = sorted(raw)
    p50, p99 = percentile(ordered, 0.5), percentile(ordered, 0.99)
    span = p99 - p50
    return [clamp((value - p50) / span) for value in raw] if span > 0 else [0.0] * len(raw)


# ---- channel identity ---------------------------------------------------------------------------------------------

def channel_of(candidate: Candidate, profile: MemberProfile) -> str | None:
    """The candidate's channel (a title's collection), with a legacy name key merged into its stable key."""
    key = candidate.channel_key
    return profile.channel_aliases.get(key, key) if key else None


def _bare_name(candidate: Candidate) -> str:
    return (candidate.channel_name or "").strip().casefold()


def channel_value(mapping: Mapping[str, object], candidate: Candidate, profile: MemberProfile):  # noqa: ANN201
    """A per-channel value by the stable key, else by the bare casefolded name that pre-1.9.0 rows store."""
    key = channel_of(candidate, profile)
    if key is not None and key in mapping:
        return mapping[key]
    name = _bare_name(candidate)
    return mapping.get(name) if name else None


def in_channels(keys: frozenset[str], candidate: Candidate, profile: MemberProfile) -> bool:
    key = channel_of(candidate, profile)
    return (key is not None and key in keys) or (bool(_bare_name(candidate)) and _bare_name(candidate) in keys)


# ---- factors -------------------------------------------------------------------------------------------

_LINK_PREFIXES = ("col:", "dir:", "cast:")


def title_link(candidate_tokens: frozenset[str], seed_tokens: frozenset[str], k: Constants = CONSTANTS) -> float:
    """Franchise or person link of two titles: same collection 1.0, shared director 0.6, ≥ 2 shared cast 0.4, 1 cast 0.2."""
    shared = candidate_tokens & seed_tokens
    if not shared:
        return 0.0  # the common case: skips three generator scans per pair (a_c walks every satisfied title)
    if any(token.startswith("col:") for token in shared):
        return k.title_collection
    if any(token.startswith("dir:") for token in shared):
        return k.title_director
    cast = sum(1 for token in shared if token.startswith("cast:"))
    return k.title_cast_two if cast >= 2 else k.title_cast_one if cast == 1 else 0.0


def title_affinity(candidate: Candidate, profile: MemberProfile, k: Constants = CONSTANTS) -> tuple[float, SatisfiedItem | None]:
    """A title's a_c: the max link over satisfied titles, with the newest seed that gives it."""
    best, seed = 0.0, None
    linking = frozenset(token for token in candidate.tokens if token.startswith(_LINK_PREFIXES))  # genre/cat/decade never link
    if not linking:
        return best, seed
    for item in profile.satisfied:
        if item.target_kind == "title" and (value := title_link(linking, item.tokens, k)) > best:
            best, seed = value, item
    return best, seed


def candidate_affinity(candidate: Candidate, profile: MemberProfile, k: Constants = CONSTANTS) -> float:
    if candidate.target_kind == "title":
        return title_affinity(candidate, profile, k)[0]
    return affinity(channel_value(profile.affinity, candidate, profile) or 0.0, k)


def as_list(vector: array | None) -> list[float] | None:
    """A vector as a Python list: math.sumprod over lists is ~3× faster than over array("f")."""
    return vector.tolist() if vector is not None and len(vector) else None


def _best_cosine(vector: list[float] | None, centroids: Sequence[list[float]]) -> float | None:
    values = [math.sumprod(vector, centroid) for centroid in centroids if vector is not None and len(centroid) == len(vector)]
    return max(values) if values else None


def _topic(vector: list[float] | None, tokens: frozenset[str], centroids: Sequence[list[float]], profile: MemberProfile) -> float:
    if (best := _best_cosine(vector, centroids)) is not None:
        return best
    seeds = profile.satisfied[: CONSTANTS.jaccard_seeds]
    return max((jaccard(tokens, seed.tokens) for seed in seeds), default=0.0)


def topic_raw(candidate: Candidate, profile: MemberProfile) -> float:
    """c_i: the max cosine to the taste centroids, else the max token Jaccard against the newest 50 satisfied items."""
    return _topic(as_list(candidate.vector), candidate.tokens, [centroid.tolist() for centroid in profile.centroids], profile)


def freshness(candidate: Candidate, now: datetime, k: Constants = CONSTANTS) -> float:
    age = age_days(candidate.published_at, now)
    if age is None:
        return 1.0
    if candidate.target_kind == "remote":
        return 1.0 + k.remote_fresh_boost * math.exp(-age / k.remote_fresh_tau_days)
    return 1.0 + k.title_fresh_boost * math.exp(-age / k.title_fresh_tau_days)


def quality(candidate: Candidate, k: Constants = CONSTANTS) -> float:
    if candidate.target_kind == "title":
        if candidate.rating is None:
            return 1.0
        return k.title_quality_base + k.title_quality_span * clamp((candidate.rating - k.rating_floor) / k.rating_span)
    if candidate.channel_view_percentile is not None:
        pct = clamp(candidate.channel_view_percentile)
    elif candidate.views is not None:
        pct = min(1.0, math.log10(1 + max(candidate.views, 0)) / k.views_log10_full)
    else:
        return 1.0
    return k.remote_quality_base + k.remote_quality_span * pct


def fatigue(candidate: Candidate, profile: MemberProfile, k: Constants = CONSTANTS) -> float:
    n_item = profile.item_fatigue.get(candidate.key, 0.0)
    n_channel = channel_value(profile.channel_fatigue, candidate, profile) or 0.0
    return 1.0 / (1.0 + k.item_fatigue_k * n_item) * max(k.channel_fatigue_floor, 1.0 / (1.0 + k.channel_fatigue_k * n_channel))


def fewer_factor(days: float, k: Constants = CONSTANTS) -> float:
    """Show fewer: ×0.2 for 35 days, then 0.4, 0.6, 0.8, and 1.0 from day 140."""
    return min(1.0, k.fewer_floor + k.fewer_step * math.floor(days / k.fewer_step_days))


def spill_factor(days: float, k: Constants = CONSTANTS) -> float:
    """Not interested's channel spill: 0.7, recovering linearly to 1.0 over 30 days."""
    return min(1.0, k.spill_floor + (1.0 - k.spill_floor) * days / k.spill_days)


def feedback(candidate: Candidate, profile: MemberProfile, now: datetime, k: Constants = CONSTANTS) -> float:
    if candidate.target_kind != "remote":
        return 1.0
    factor = 1.0
    if (at := channel_value(profile.fewer, candidate, profile)) is not None:
        factor *= fewer_factor(age_days(at, now) or 0.0, k)
    if (at := channel_value(profile.spill, candidate, profile)) is not None:
        factor *= spill_factor(age_days(at, now) or 0.0, k)
    return factor


def context(candidate: Candidate, ctx: SurfaceContext, k: Constants = CONSTANTS) -> float:
    """C_i: up_next 1 + 2·s_cur + 1·[same channel]; title anchors 1 + 3·s_anchor + 1·[same collection]; else 1.

    s_cur and s_anchor are the raw cosine to the anchor's vector, else the token Jaccard.
    """
    anchor = ctx.anchor
    if anchor is None or ctx.surface not in ANCHORED:
        return 1.0
    same = 1.0 if anchor.channel_key and candidate.channel_key == anchor.channel_key else 0.0
    near = max(0.0, similarity(candidate.vector, candidate.tokens, anchor.vector, anchor.tokens))
    if ctx.surface == "up_next":
        return 1.0 + k.w_context_current * near + k.w_context_same_channel * same
    return 1.0 + k.w_context_anchor * near + k.w_context_collection * same


def score(candidate: Candidate, profile: MemberProfile, ctx: SurfaceContext, *, topic: float, now: datetime,
          k: Constants = CONSTANTS) -> tuple[float, dict[str, float]]:
    """score = R × F × Q × G × B × C. ``topic`` is the normalised s_i. Contributions: the weighted R terms
    (channel 2·a_c, topic 2·s_i, interest 0.5·m_i) and context C − 1, which reasons read."""
    interest = 1.0 if candidate.target_kind == "remote" and profile.interests.intersection(candidate.category_keys) else 0.0
    contributions = {
        "channel": k.w_affinity * candidate_affinity(candidate, profile, k),
        "topic": k.w_topic * topic,
        "interest": k.w_interest * interest,
    }
    relevance = 1.0 + contributions["channel"] + contributions["topic"] + contributions["interest"]
    surface_context = context(candidate, ctx, k)
    contributions["context"] = surface_context - 1.0
    value = (relevance * freshness(candidate, now, k) * quality(candidate, k) * fatigue(candidate, profile, k)
             * feedback(candidate, profile, now, k) * surface_context)
    return value, contributions


# ---- filters and the list pipeline ----------------------------------------------------------------------

def vetoed(candidate: Candidate, profile: MemberProfile, ctx: SurfaceContext) -> bool:
    """1: hard suppressions, completed/skipped/Continue watching, fatigue exclusion, the current item."""
    if not candidate.sources & ~SOURCE_HISTORY:
        return True  # a history anchor only exists to carry a vector
    if candidate.key in profile.excluded or candidate.key in profile.fatigued_out:
        return True
    if candidate.key == ctx.context_key or (ctx.anchor is not None and candidate.key == ctx.anchor.key):
        return True
    if candidate.target_kind == "title":
        return candidate.key in profile.hidden_titles
    return candidate.key in profile.hidden_items or in_channels(profile.hidden_channels, candidate, profile)


def _by_score(pair: tuple[Candidate, float]) -> tuple[float, str]:
    return -pair[1], pair[0].key


def channel_discount(scored: Sequence[tuple[Candidate, float]], profile: MemberProfile, *, current_channel: str | None = None,
                     k: Constants = CONSTANTS) -> list[tuple[Candidate, float]]:
    """2: within a channel, by score, the j-th item × ((1 − f)·0.5^j + f), f = 0.4 (the current channel on
    up_next 0.7). Items with no channel are untouched. Returns score order (ties by key)."""
    seen: dict[str, int] = {}
    discounted = []
    for candidate, value in sorted(scored, key=_by_score):
        group = channel_of(candidate, profile)
        if group is None:
            discounted.append((candidate, value))
            continue
        j = seen.get(group, 0)
        seen[group] = j + 1
        floor = k.current_channel_floor if group == current_channel else k.channel_floor
        discounted.append((candidate, value * ((1.0 - floor) * 0.5 ** j + floor)))
    return sorted(discounted, key=_by_score)


def channel_cap(ordered: Sequence[tuple[Candidate, float]], profile: MemberProfile, size: int,
                k: Constants = CONSTANTS) -> list[tuple[Candidate, float]]:
    """The safety cap: at most 4 items of one channel per 20 served (4 for any list of ≤ 20 + 19)."""
    cap = max(k.channel_cap_per_20, k.channel_cap_per_20 * size // 20)
    counts: dict[str, int] = {}
    kept = []
    for candidate, value in ordered:
        group = channel_of(candidate, profile)
        if group is not None:
            if counts.get(group, 0) >= cap:
                continue
            counts[group] = counts.get(group, 0) + 1
        kept.append((candidate, value))
    return kept


def group_of(candidate: Candidate, profile: MemberProfile) -> str | None:
    """The candidate's taste group: its nearest centroid, else its category key (remote) or genre (title) with the highest
    p(g) in the member's taste mix, ties by name. None when it matches no group of the mix."""
    if profile.centroids and candidate.vector is not None:
        nearest = [(value, -index) for index, centroid in enumerate(profile.centroids)
                   if (value := cosine(candidate.vector, centroid)) is not None]
        if nearest:
            return f"centroid:{-max(nearest)[1]}"
    options = candidate.category_keys if candidate.target_kind == "remote" else (t for t in candidate.tokens if t.startswith("genre:"))
    known = sorted(group for group in options if group in profile.taste_mix)
    return max(known, key=lambda group: profile.taste_mix[group]) if known else None


def kl_divergence(p: Mapping[str, float], counts: Mapping[str, int], n: int, alpha: float) -> float:
    """KL(p ‖ q̃) with q̃ = (1 − α)·q + α·p, q(g) = the chosen items' share in g."""
    total = 0.0
    for group, share in p.items():
        if share > 0:
            q = counts.get(group, 0) / n if n else 0.0
            total += share * math.log(share / ((1.0 - alpha) * q + alpha * share))
    return total


def calibrate(pool: Sequence[tuple[Candidate, float]], groups: Sequence[str | None], p: Mapping[str, float], size: int,
              k: Constants = CONSTANTS) -> list[int]:
    """Steck's greedy calibration: indexes of ``pool`` (score order), picked one at a time to maximise
    (1 − λ)·Σ score/max − λ·KL(p ‖ q̃). Ties go to the higher-scored item. Without a taste mix: the top ``size``."""
    if not p:
        return list(range(min(size, len(pool))))
    top = max((value for _candidate, value in pool), default=0.0) or 1.0
    chosen: list[int] = []
    counts: dict[str, int] = {}
    total = 0.0
    remaining = list(range(len(pool)))
    while remaining and len(chosen) < size:
        best_index, best_value = remaining[0], -math.inf
        for index in remaining:
            group = groups[index]
            trial = dict(counts)
            if group is not None:
                trial[group] = trial.get(group, 0) + 1
            value = ((1.0 - k.calibration_lambda) * (total + pool[index][1] / top)
                     - k.calibration_lambda * kl_divergence(p, trial, len(chosen) + 1, k.calibration_alpha))
            if value > best_value:
                best_index, best_value = index, value
        chosen.append(best_index)
        remaining.remove(best_index)
        total += pool[best_index][1] / top
        if groups[best_index] is not None:
            counts[groups[best_index]] = counts.get(groups[best_index], 0) + 1
    return chosen


def _kernel(left: Candidate, right: Candidate, q_left: float, q_right: float, k: Constants) -> float:
    distance = 1.0 - jaccard(left.tokens, right.tokens)
    return k.dpp_alpha * q_left * q_right * math.exp(-distance / (2.0 * k.dpp_sigma ** 2))


def dpp_select(items: Sequence[tuple[Candidate, float]], size: int, k: Constants = CONSTANTS) -> list[int]:
    """Windowed greedy MAP over ``items`` (score order), returning ``size`` indexes in serving order.

    L_ii = q_i², L_ij = α_d·q_i·q_j·exp(−(1 − Jaccard) / 2σ²), q_i = 0.1 + 0.9·score/max. Each window of 12 starts afresh;
    inside it every pick maximises the residual gain d_i² of an incremental Cholesky (Chen et al. 2018), ties to the
    higher score. Once the best gain is ≤ ε the rest of the window is filled in score order.
    """
    top = max((value for _candidate, value in items), default=0.0) or 1.0
    q = [k.dpp_quality_floor + (1.0 - k.dpp_quality_floor) * value / top for _candidate, value in items]
    remaining = list(range(len(items)))
    picked: list[int] = []
    while remaining and len(picked) < size:
        window = min(k.dpp_window, size - len(picked))
        rows: dict[int, list[float]] = {index: [] for index in remaining}
        gains = {index: q[index] ** 2 for index in remaining}
        chosen: list[int] = []
        while gains and len(chosen) < window:
            best = min(gains, key=lambda index: (-gains[index], index))
            if gains[best] <= k.dpp_epsilon:
                chosen += sorted(gains)[: window - len(chosen)]
                break
            depth = math.sqrt(gains.pop(best))
            best_row = rows.pop(best)
            chosen.append(best)
            for index in gains:
                e = (_kernel(items[best][0], items[index][0], q[best], q[index], k)
                     - math.fsum(a * b for a, b in zip(best_row, rows[index]))) / depth
                rows[index].append(e)
                gains[index] -= e * e
        picked += chosen
        taken = set(chosen)
        remaining = [index for index in remaining if index not in taken]
    return picked


# ---- reasons ----------------------------------------------------------------------------------------

def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def prepared_seeds(profile: MemberProfile) -> list[tuple[SatisfiedItem, list[float] | None]]:
    return [(item, as_list(item.vector)) for item in profile.satisfied]


def nearest_seed(candidate: Candidate, profile: MemberProfile, *,
                 seeds: Sequence[tuple[SatisfiedItem, list[float] | None]] | None = None) -> SatisfiedItem | None:
    """The satisfied item most like the candidate (cosine, else Jaccard); None when nothing is alike at all."""
    vector = as_list(candidate.vector)
    best, seed = 0.0, None
    for item, item_vector in seeds if seeds is not None else prepared_seeds(profile):
        value = _best_cosine(vector, [item_vector] if item_vector is not None else [])
        value = value if value is not None else jaccard(candidate.tokens, item.tokens)
        if value > best:
            best, seed = value, item
    return seed


def _category_label(candidate: Candidate, keys: frozenset[str] | None = None) -> str | None:
    return next((_CATEGORY_LABELS[key] for key in candidate.category_keys
                 if key in _CATEGORY_LABELS and (keys is None or key in keys)), None)


def choose_reason(candidate: Candidate, contributions: Mapping[str, float], profile: MemberProfile, ctx: SurfaceContext, *,
                  slot: RecoSlot, now: datetime, names: Mapping[str, str] = _NO_NAMES, k: Constants = CONSTANTS,
                  seeds: Sequence[tuple[SatisfiedItem, list[float] | None]] | None = None) -> tuple[RecoReasonCode, str]:
    """The reason code and its one line, from the top contributing term and a concrete seed.

    ``seeds`` is ``prepared_seeds(profile)``, passed by ``rank`` so a list converts each seed vector once."""
    def name(value: str) -> str:
        return clip(value, k.reason_name_chars)

    def line(code: RecoReasonCode, text: str) -> tuple[RecoReasonCode, str]:
        return code, clip(text, k.reason_chars)

    remote = candidate.target_kind == "remote"
    if slot == "pinned":
        return line("next_part", "Next part") if remote else line("next_episode", "Next episode")
    if slot == "explore":
        seed = nearest_seed(candidate, profile, seeds=seeds)
        return line("explore", f"Something different · like {name(seed.title)}" if seed else "Something different")
    if ctx.surface in ANCHORED and ctx.anchor is not None and contributions["context"] >= 1.0:
        if ctx.surface != "up_next":
            return line("like_anchor", f"Like {name(ctx.anchor.title)}")
        if candidate.channel_key and candidate.channel_key == ctx.anchor.channel_key and candidate.channel_name:
            return line("same_channel", f"More from {name(candidate.channel_name)}")
        return line("like_current", "Like what you're watching")
    age = age_days(candidate.published_at, now)
    if remote and candidate.sources & SOURCE_FOLLOW and age is not None and age <= k.follow_new_days and candidate.channel_name:
        return line("follow_new", f"New from {name(candidate.channel_name)}, which you follow")
    terms = (("channel", contributions["channel"]), ("finished", contributions["topic"]), ("interest", contributions["interest"]))
    code, value = max(terms, key=lambda term: term[1])  # the first maximum wins: channel, then finished, then interest
    if value >= k.reason_weak:
        if code == "channel" and remote and candidate.channel_name:
            return line("channel", f"Because you watch {name(candidate.channel_name)}")
        if code == "channel" and not remote:
            _link, seed = title_affinity(candidate, profile, k)
            shared = candidate.tokens & seed.tokens if seed else frozenset()
            collections = sorted(token[4:] for token in shared if token.startswith("col:"))
            people = sorted(token[4:] for token in shared if token.startswith("dir:")) + sorted(
                token[5:] for token in shared if token.startswith("cast:"))
            if collections and collections[0] in names:
                return line("channel", f"More {name(names[collections[0]])}")
            if not collections and (label := next((names[p] for p in people if p in names), None)):
                return line("channel", f"With {name(label)}")
            if seed is not None:
                return line("finished", f"Because you finished {name(seed.title)}")
        if code in ("channel", "finished") and (seed := nearest_seed(candidate, profile, seeds=seeds)) is not None:
            return line("finished", f"Because you finished {name(seed.title)}")
        if code == "interest" and (label := _category_label(candidate, profile.interests)):
            return line("interest", f"Popular in {label}, which you picked")
    if remote:
        label = _category_label(candidate)
        return line("popular", f"Popular in {label}" if label else "Popular now")
    if age is not None and age <= k.new_arrival_days:
        return line("new_arrival", "New in your library")
    return line("well_rated", "Well rated")


# ---- exploration ---------------------------------------------------------------------------------------

def explore_rng(user_id: str, local_date: date, surface: str, context_key: str) -> random.Random:
    """random.Random seeded on sha256(user | local date | surface | context): one list a day, reproducible in replay."""
    digest = hashlib.sha256(f"{user_id}|{local_date.isoformat()}|{surface}|{context_key}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def boltzmann(scores: Sequence[float], temperature: float) -> list[float]:
    """p_i ∝ score_i^(1/T), computed on score/max so large scores cannot overflow."""
    top = max(scores, default=0.0)
    if top <= 0:
        return [1.0 / len(scores)] * len(scores) if scores else []
    weights = [(max(value, 0.0) / top) ** (1.0 / temperature) for value in scores]
    total = math.fsum(weights)
    return [weight / total for weight in weights]


def explore_draw(pool: Sequence[tuple[Candidate, float]], count: int, rng: random.Random,
                 k: Constants = CONSTANTS) -> list[tuple[Candidate, float, float]]:
    """``count`` Boltzmann draws without replacement over the top M of ``pool`` (score order): (candidate, score, p_shown),
    p_shown being the drawn item's probability at the moment of its draw."""
    remaining = list(pool[: k.explore_top_m])
    drawn = []
    while remaining and len(drawn) < count:
        probabilities = boltzmann([value for _candidate, value in remaining], k.explore_temperature)
        roll, cumulative, index = rng.random(), 0.0, len(remaining) - 1
        for position, probability in enumerate(probabilities):
            cumulative += probability
            if roll < cumulative:
                index = position
                break
        candidate, value = remaining.pop(index)
        drawn.append((candidate, value, probabilities[index]))
    return drawn


def title_taste(profile: MemberProfile) -> frozenset[str]:
    """Every genre and person token of the satisfied titles: build once per list, not once per candidate."""
    return frozenset(token for item in profile.satisfied if item.target_kind == "title"
                     for token in item.tokens if token.startswith(("genre:", "cast:", "dir:")))


def unfamiliar(candidate: Candidate, profile: MemberProfile, k: Constants = CONSTANTS, *,
               taste: frozenset[str] | None = None, linked: bool | None = None) -> bool:
    """Remote: A_c ≤ 0 and not followed. Title: a_c = 0 and no genre or person shared with a satisfied title.
    ``taste`` (from title_taste) and ``linked`` (a_c > 0, already known from the score) skip recomputing them."""
    if candidate.target_kind == "remote":
        total = channel_value(profile.affinity, candidate, profile)
        return (total is None or total <= 0) and not in_channels(profile.followed, candidate, profile)
    if linked if linked is not None else title_affinity(candidate, profile, k)[0] > 0:
        return False
    return not candidate.tokens & (title_taste(profile) if taste is None else taste)


def explorable(candidate: Candidate, topic: float, profile: MemberProfile, k: Constants = CONSTANTS, *,
               taste: frozenset[str] | None = None, linked: bool | None = None) -> bool:
    """Unfamiliar, and past the social-proof gate: s_i ≥ 0.25, or nominated by seed expansion or a channel listing."""
    return unfamiliar(candidate, profile, k, taste=taste, linked=linked) and (
        topic >= k.explore_min_topic or bool(candidate.sources & (SOURCE_SEED | SOURCE_CHANNEL)))


# ---- the list ------------------------------------------------------------------------------------------------------

def rank(candidates: Sequence[Candidate], profile: MemberProfile, ctx: SurfaceContext, *, now: datetime,
         k: Constants = CONSTANTS, names: Mapping[str, str] = _NO_NAMES) -> list[Ranked]:
    """At most ``ctx.k`` served positions with slot, score, p_shown and reason.

    ``names`` maps person ids and collection ids to display names for the title reasons "With {person}" and
    "More {collection}" (gap G-R3a); without a name those reasons fall back to "Because you finished {seed}".
    """
    # Pure-Python cosine (math.sumprod over lists) costs ≈ 95 ms at the 3,000-candidate ceiling and
    # ≈ 25 ms at a real list's ≈ 650; precompute candidate-to-centroid cosines in the pool if a budget is missed.
    unique = list({candidate.key: candidate for candidate in reversed(candidates)}.values())[::-1]  # first occurrence wins
    eligible = [candidate for candidate in unique if not vetoed(candidate, profile, ctx)]
    if not eligible or ctx.k <= 0:
        return []
    centroids = [centroid.tolist() for centroid in profile.centroids]
    topics = normalise_topic([_topic(as_list(c.vector), c.tokens, centroids, profile) for c in eligible])
    facts: dict[str, tuple[float, dict[str, float]]] = {}
    scored = []
    for candidate, topic in zip(eligible, topics):
        value, contributions = score(candidate, profile, ctx, topic=topic, now=now, k=k)
        facts[candidate.key] = (topic, contributions)
        scored.append((candidate, value))
    current = channel_of(ctx.anchor, profile) if ctx.surface == "up_next" and ctx.anchor is not None else None
    ordered = channel_cap(channel_discount(scored, profile, current_channel=current, k=k), profile, ctx.k, k)

    pinned = next((pair for pair in ordered if pair[0].key == ctx.pinned_key), None) if ctx.pinned_key else None
    if pinned is not None:
        ordered.remove(pinned)
    explore_slots = explore_positions(ctx.k, SURFACE_EXPLORE[ctx.surface])
    exploit_size = max(0, ctx.k - len(explore_slots) - (1 if pinned else 0))
    pool = ordered[: k.rank_pool]
    if ctx.surface in CALIBRATED:
        groups = [group_of(candidate, profile) for candidate, _value in pool]
        subset = [pool[index] for index in sorted(calibrate(pool, groups, profile.taste_mix, exploit_size, k))]
        exploit = [subset[index] for index in dpp_select(subset, len(subset), k)]
    else:
        exploit = [pool[index] for index in dpp_select(pool, exploit_size, k)]

    taken = {candidate.key for candidate, _value in exploit}
    taste, open_pool = title_taste(profile), []
    for pair in ordered:  # the draw only sees the top M explorable items, so the scan stops there
        if len(open_pool) >= k.explore_top_m:
            break
        topic, contributions = facts[pair[0].key]
        linked = contributions["channel"] > 0 if k.w_affinity > 0 else None  # score's a_c, when its weight keeps the sign
        if pair[0].key not in taken and explorable(pair[0], topic, profile, k, taste=taste, linked=linked):
            open_pool.append(pair)
    draws = explore_draw(open_pool, len(explore_slots), explore_rng(profile.user_id, ctx.local_date, ctx.surface, ctx.context_key), k)
    taken |= {candidate.key for candidate, _value, _p in draws}
    shortfall = len(explore_slots) - len(draws)  # too few explorable items: exploit items backfill
    exploit += [pair for pair in ordered if pair[0].key not in taken][: max(0, shortfall)]

    served: list[tuple[Candidate, float, RecoSlot, float | None]] = []
    if pinned is not None:
        served.append((pinned[0], pinned[1], "pinned", None))
    exploit_iter, draw_iter = iter(exploit), iter(draws)
    while len(served) < ctx.k:
        position = len(served) + 1
        draw = next(draw_iter, None) if position in explore_slots else None
        if draw is not None:
            served.append((draw[0], draw[1], "explore", draw[2]))
            continue
        pair = next(exploit_iter, None)
        if pair is None:
            draw = next(draw_iter, None)  # exploit ran out: remaining draws backfill
            if draw is None:
                break
            served.append((draw[0], draw[1], "explore", draw[2]))
            continue
        served.append((pair[0], pair[1], "exploit", None))

    seeds = prepared_seeds(profile)
    ranked = []
    for position, (candidate, value, slot, p_shown) in enumerate(served):
        code, reason = choose_reason(candidate, facts[candidate.key][1], profile, ctx, slot=slot, now=now, names=names, k=k,
                                     seeds=seeds)
        ranked.append(Ranked(candidate=candidate, position=position, slot=slot, score=value, p_shown=p_shown,
                             reason_code=code, reason=reason))
    return ranked
