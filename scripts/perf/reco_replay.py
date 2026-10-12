"""Offline recommendation replay: leakage-free, per-instance, aggregate output only.

    python scripts/perf/reco_replay.py DATA_DIR [--policy legacy|new|ablation|compare] [--assert-targets] [--synthetic] [--json OUT]

DATA_DIR is a COPY of app-data (app.db, and popular-discovery.json beside it) under the system temp directory.
Nothing is ever committed: every instance's as-of state is built inside a transaction that is rolled back. No
provider, AI or other network call is made (run it in a `--network none` container on the box). It prints one JSON
object of aggregate metrics: no member, title, channel, id or path.

Seam. A policy is ``recommend(session, member_id, as_of, surface, k) -> list[Candidate]`` over the as-of state in
``session``; surfaces are home_picked, home_recommended, home_because and up_next. The spec's seam returns item
keys; this one returns ``Candidate`` (key + channel + published time) because the hit, diversity and freshness
metrics need the channel and age of whatever a policy returns, including items outside any pool the harness knows.

Test instances. Per active member, the newest 10 satisfied remote watches and the newest 10 satisfied title
watches. Satisfied (pre-v9 approximation): completed, or position / duration >= 0.5. A watch is a
playback_progress row (not an extra) or a remote_playback_progress row (cleared rows included: the watch
happened), timed by last_watched_at. A movie or episode is a title watch (key: the movie, or the episode's
series; channel: its first genre, casefolded); every other row is a remote watch (key: the policy's _source_key;
channel: the casefolded uploader). Production keeps one checkpoint per item, so earlier rewatches are invisible.

State at as_of = last_watched_at - 1 s, inside the transaction (household-wide):
  - the instance's own progress row is deleted outright, even when it was started earlier;
  - progress rows (both tables) first seen at or after as_of are deleted; first seen = the earlier of created_at
    and last_watched_at (imported history is created at import time but watched long before);
  - progress rows first seen before as_of but touched after it revert to "started, not completed" with
    last_watched_at = created_at, plays 1, completions 0 and max_fraction 0: their completion, watch depth and
    later checkpoints are post-as_of evidence;
  - movies and episodes with coalesce(added_at, created_at) >= as_of are deleted (a series is present while one
    of its episodes is, so an ongoing series is not hidden by its newest episode's arrival);
  - library_items whose created_at, downloaded_at and title's added_at are all at or after as_of are deleted;
  - member_favorites, source_automations (follows), member_interests and member_recommendation_suppressions
    created at or after as_of are deleted. member_interests.created_at is the member's last interests save
    (replace() rewrites every row), so this can hide interests held earlier (conservative);
  - a NULL timestamp counts as before as_of; remote `cleared`, and follows or suppressions removed before the copy
    was taken, are as of the copy;
  - reco_events with at >= as_of and reco_pool rows first seen at or after as_of are deleted (the new policy's state; the legacy policy
    never reads them). remote_media is a household cache with no member column and is left whole: a member reaches it only through their pool.

Candidates. home_picked / up_next: the stored Popular snapshot (popular-discovery.json) re-ranked as of as_of,
items published after it dropped (unknown publish time kept), the 24-item household cut applied for the legacy
policy (the new policy and the ablation rank it uncut) and refreshed_at set to as_of. The snapshot is the copy's current one: snapshot history is not stored. Up Next's provider
"<uploader> videos" search is a live call, unavailable offline. home_recommended / home_because: visible,
unstarted movies and series. The legacy policy never sees follow feed_entries.

Metrics (each averaged per member, then across members; hits are 0/1 per instance):
  remote_channel_hit@10 / remote_item_hit@10: a remote instance's channel / item in the top 10 of home_picked
    (item hit is meaningful on the synthetic seed only: production pools at past dates are unknown before v9);
  title_item_hit@10: a title instance in the top 10 of home_recommended;
  anchored_title_hit@10: a title instance in the top 10 of home_because, anchored on the member's newest completed
    title before as_of (instances without one are skipped);
  up_next_channel_hit@10: a remote instance whose previous remote watch is under 2 h earlier: its channel in the
    top 10 of up_next with that watch as the current source;
  coverage@20 per surface: |union of top-20 keys| / |union of eligible candidates| over all instances (household);
  home_picked top 12: distinct channels, max count of one channel (mean, worst, and worst excess over the instance's
    cap max(3, ceil(12 / channels in its eligible pool))), median published age in days. These are scored on paired
    instances (every measured policy returned a list); the "unpaired" figures count every instance.
Trivial baselines run through the same seam over the same candidates and are reported for every hit metric:
newest_first; household_top_channel (the household's most-watched channels first, by watch count as of as_of,
then newest); last_watch_channel (the channel of the member's last watch of that kind first, then newest). For
titles "channel" is the first genre.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTANCES_PER_KIND = 10
UP_NEXT_GAP = timedelta(hours=2)
SURFACES = ("home_picked", "home_recommended", "home_because", "up_next")
HIT_METRICS = ("remote_channel_hit@10", "remote_item_hit@10", "title_item_hit@10", "anchored_title_hit@10", "up_next_channel_hit@10")
LIMITATIONS = (
    "remote pool is the copy's current Popular snapshot re-ranked at each as_of, not the snapshot live then",
    "remote_item_hit@10 is meaningful on the synthetic seed only (past production pools are unknown before v9)",
    "up_next provider-related search (live call) is unavailable offline; up_next ranks the snapshot pool only",
    "progress keeps one checkpoint per item: earlier rewatches are invisible",
    "interest rows carry the last save time, so interests saved after as_of are hidden even if held before",
    "untimestamped state (remote cleared flags, removed follows/suppressions) is taken as of the copy",
)
EXTRA_LIMITATIONS = (
    "the candidate universe is the union of the measured policies' candidate sets: coverage@20 shares one denominator (the legacy pool alone is under 20 items)",
    "pre-deploy box replay: the new policy's pool is its persisted pool plus the Popular snapshot, so hits are a lower bound (seed expansion and channel listings cannot run offline)",
    "synthetic pools are seeded rows with first-seen times, not a refresher run; the refresher is covered by its own tests",
)


@dataclass(frozen=True)
class Candidate:
    """One recommended item: its key, its channel (first genre for a title), and when it was published/arrived."""

    key: str
    channel: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class Watch:
    table: str  # "local" (playback_progress) or "remote" (remote_playback_progress)
    row_id: str
    kind: str  # "title" or "remote"
    key: str
    channel: str
    at: datetime
    satisfied: bool
    completed: bool
    source: str | None = None
    remote_id: str | None = None
    url: str | None = None
    uploader: str | None = None


Policy = Callable[..., list[Candidate]]
UNRESOLVED: Counter = Counter()  # surface -> item keys the new policy returned that no table could resolve (reported, never dropped silently)


def _naive(moment: datetime | None) -> datetime | None:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment is not None and moment.tzinfo else moment


def _first_genre(title) -> str:  # noqa: ANN001
    genres = (title.metadata_json or {}).get("genres") if isinstance(title.metadata_json, dict) else None
    return next((genre.casefold() for genre in genres or [] if isinstance(genre, str)), "")


def watches(session, member_id: str | None) -> list[Watch]:  # noqa: ANN001
    """Every watch of ``member_id`` (the whole household when None), oldest first (ties by row id)."""
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from app.models import LibraryItem, MediaTitle, PlaybackProgress, RemotePlaybackProgress
    from app.services.member_recommendations import TITLE_POOL_TYPES, _channel_key
    from app.services.popular_discovery import _source_key

    def satisfied(completed: bool, position: float | None, duration: float | None) -> bool:
        return bool(completed) or bool(duration and (position or 0) / duration >= 0.5)

    leaf, season = aliased(MediaTitle), aliased(MediaTitle)
    local = (
        select(PlaybackProgress.id.label("row_id"), PlaybackProgress.completed, PlaybackProgress.position_seconds, PlaybackProgress.duration_seconds,
               PlaybackProgress.last_watched_at, LibraryItem.extractor, LibraryItem.remote_id, LibraryItem.webpage_url,
               LibraryItem.title, LibraryItem.uploader, leaf.id.label("leaf_id"), leaf.type.label("leaf_type"), season.parent_id.label("series_id"))
        .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
        .outerjoin(leaf, leaf.id == LibraryItem.title_id).outerjoin(season, season.id == leaf.parent_id)
        .where(LibraryItem.extra_type.is_(None))
    )
    remote = select(RemotePlaybackProgress)
    if member_id is not None:
        local, remote = local.where(PlaybackProgress.user_id == member_id), remote.where(RemotePlaybackProgress.user_id == member_id)
    rows = session.execute(local).all()
    tops = {row.series_id if row.leaf_type == "episode" else row.leaf_id for row in rows if row.leaf_type in (*TITLE_POOL_TYPES, "episode")}
    genres = {title.id: _first_genre(title) for title in session.scalars(select(MediaTitle).where(MediaTitle.id.in_(tops - {None})))}
    found: list[Watch] = []
    for row in rows:
        done, enough = bool(row.completed), satisfied(row.completed, row.position_seconds, row.duration_seconds)
        top = row.series_id if row.leaf_type == "episode" else row.leaf_id
        if row.leaf_type in (*TITLE_POOL_TYPES, "episode") and top:
            found.append(Watch("local", row.row_id, "title", top, genres.get(top, ""), row.last_watched_at, enough, done))
        else:
            found.append(Watch("local", row.row_id, "remote", _source_key(row.extractor or "youtube", row.remote_id, row.webpage_url, row.title, row.uploader),
                               _channel_key(row.uploader), row.last_watched_at, enough, done, row.extractor, row.remote_id, row.webpage_url, row.uploader))
    for row in session.scalars(remote):
        found.append(Watch("remote", row.id, "remote", _source_key(row.extractor or "youtube", row.remote_id, row.source_url, None, row.uploader),
                           _channel_key(row.uploader), row.last_watched_at, satisfied(row.completed, row.position_seconds, row.duration_seconds),
                           bool(row.completed), row.extractor, row.remote_id, row.source_url, row.uploader))
    return sorted(found, key=lambda watch: (watch.at, watch.row_id))


def pick_instances(history: list[Watch]) -> list[Watch]:
    """The newest INSTANCES_PER_KIND satisfied remote and title watches, oldest first."""
    chosen = [watch for kind in ("remote", "title") for watch in [w for w in history if w.kind == kind and w.satisfied][-INSTANCES_PER_KIND:]]
    return sorted(chosen, key=lambda watch: (watch.at, watch.row_id))


def hide_after(session, as_of: datetime, held: list[Watch]) -> dict[str, int]:  # noqa: ANN001
    """Make ``session`` show the household as of ``as_of`` (see the module docstring); never commits. Rows changed per table."""
    from sqlalchemy import and_, delete, func, or_, select, update

    from app.models import (
        LibraryItem, MediaTitle, MemberFavorite, MemberInterest, MemberRecommendationSuppression, PlaybackProgress, RecoEvent, RecoPool,
        RemotePlaybackProgress, SourceAutomation,
    )

    counts: dict[str, int] = Counter()

    def run(name: str, statement) -> None:  # noqa: ANN001
        counts[name] += session.execute(statement, execution_options={"synchronize_session": False}).rowcount or 0

    for name, model, table in (("playback_progress", PlaybackProgress, "local"), ("remote_playback_progress", RemotePlaybackProgress, "remote")):
        held_ids = [watch.row_id for watch in held if watch.table == table]
        run(name, delete(model).where(or_(model.id.in_(held_ids), and_(model.created_at >= as_of, model.last_watched_at >= as_of))))
        # Watch depth is post-cutoff evidence too (review I1): the profile reads completions >= 1 or max_fraction >= 0.9 as completed.
        run(name, update(model).where(model.last_watched_at >= as_of).values(
            completed=False, plays=1, completions=0, max_fraction=0.0, last_watched_at=model.created_at))
    run("media_titles", delete(MediaTitle).where(MediaTitle.type.in_(("movie", "episode")), func.coalesce(MediaTitle.added_at, MediaTitle.created_at) >= as_of))
    arrived_earlier = select(MediaTitle.id).where(MediaTitle.id == LibraryItem.title_id, MediaTitle.added_at < as_of).exists()
    run("library_items", delete(LibraryItem).where(
        LibraryItem.created_at >= as_of, func.coalesce(LibraryItem.downloaded_at, LibraryItem.created_at) >= as_of, ~arrived_earlier,
    ))
    for name, model in (("member_favorites", MemberFavorite), ("source_automations", SourceAutomation),
                        ("member_interests", MemberInterest), ("member_recommendation_suppressions", MemberRecommendationSuppression)):
        run(name, delete(model).where(model.created_at >= as_of))
    if session.get_bind().dialect.has_table(session.connection(), "reco_events"):  # a v8 copy has no reco tables (legacy-only runs)
        run("reco_events", delete(RecoEvent).where(RecoEvent.at >= as_of))
        run("reco_pool", delete(RecoPool).where(RecoPool.first_seen_at >= as_of))  # nominated after the cutoff: the member's pool did not have it
    return counts


def popular_snapshot(as_of: datetime, *, cut: bool = True):  # noqa: ANN201
    """The stored Popular snapshot as the live system would rank it at ``as_of`` (see the module docstring).

    ``cut=False`` keeps every ranked item: the new policy reads ``PopularDiscovery.candidates()``, uncut by the 24-item
    feed limit; the legacy policy keeps the cut."""
    from app.config import settings
    from app.services.popular_discovery import PopularDiscovery

    path = settings.data_dir / "popular-discovery.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    now = as_of.replace(tzinfo=UTC).timestamp()
    categories = store.get("categories") if isinstance(store.get("categories"), dict) else {}
    for record in categories.values():
        if isinstance(record, dict) and isinstance(record.get("items"), list):
            record["items"] = [item for item in record["items"] if not isinstance(item, dict) or (item.get("published_at") or 0) <= now]
    store.update(categories=categories, refreshed_at=now)

    def offline(_query: str, _limit: int) -> object:
        raise RuntimeError("the replay never searches a provider")

    discovery = PopularDiscovery(path, offline, executor=_NoRefresh())
    snapshot = discovery._build_snapshot(store, now, refreshing=False)
    return snapshot if cut else dataclasses.replace(snapshot, items=tuple(discovery._rank_items(categories, now, cut=False)))


class _NoRefresh:
    def submit(self, function):  # noqa: ANN001, ANN201
        return None


def _remote(item) -> Candidate:  # noqa: ANN001
    from app.services.member_recommendations import _channel_key
    from app.services.popular_discovery import _source_key

    return Candidate(_source_key(item.source, item.id, item.webpage_url, item.title, item.uploader), _channel_key(item.uploader), _naive(item.published_at))


def _title(title) -> Candidate:  # noqa: ANN001
    return Candidate(title.id, _first_genre(title), title.arrived_at)


def _resolve(session, keys: list[str], kind: str) -> list[Candidate]:  # noqa: ANN001
    """The new policy's keys as the harness's Candidates: remote_media.key to the legacy ``id:…`` key and casefolded uploader; a title id to its first genre."""
    from sqlalchemy import select

    from app.models import MediaTitle, RemoteMedia
    from app.services.member_recommendations import _channel_key
    from app.services.popular_discovery import _source_key

    if kind == "remote":
        rows = {row.key: row for row in session.scalars(select(RemoteMedia).where(RemoteMedia.key.in_(keys)))}
        return [
            Candidate(_source_key(row.extractor or "youtube", row.remote_id, row.webpage_url, row.title, row.uploader), _channel_key(row.uploader), _naive(row.published_at))
            for row in (rows.get(key) for key in keys) if row is not None
        ]
    titles = {title.id: title for title in session.scalars(select(MediaTitle).where(MediaTitle.id.in_(keys)))}
    return [_title(title) for title in (titles.get(key) for key in keys) if title is not None]


def new_policy(session, member_id: str, as_of: datetime, surface: str, k: int) -> list[Candidate]:  # noqa: ANN001
    """The 1.9.0 policy through the spec's seam (``recommend`` returns keys; no I/O), then into the harness's key and channel space."""
    from app.services.reco import policy  # lazy: R4's module; the tests stand a stub in for it

    # G-R4-4: the seam takes the as-of Popular snapshot (the stored file re-ranked at as_of), so the policy does no file I/O.
    keys = policy.recommend(session, member_id, as_of, surface, k, popular=popular_snapshot(as_of, cut=False).items)
    resolved = _resolve(session, keys, _kind(surface))
    UNRESOLVED[surface] += len(keys) - len(resolved)
    return resolved


def last_watch(session, member_id: str, as_of: datetime, kind: str, *, completed: bool = False) -> Watch | None:  # noqa: ANN001
    """The member's newest ``kind`` watch before ``as_of`` in the as-of state (Up Next's current source, Because's anchor)."""
    return next((watch for watch in reversed(watches(session, member_id))
                 if watch.kind == kind and watch.at < as_of and (watch.completed or not completed)), None)


def legacy_policy(session, member_id: str, as_of: datetime, surface: str, k: int, source=None) -> list[Candidate]:  # noqa: ANN001
    """ADR 0007/0011's member_recommendations exactly as the routes call it, on the as-of state in ``session``."""
    from app.models import User
    from app.services.member_follows import MemberFollowService
    from app.services.member_recommendations import MemberInterestService, MemberRecommendationPolicy, PlaybackContext

    member = session.get(User, member_id)
    policy = MemberRecommendationPolicy(session, limit=k)
    if surface == "home_recommended":
        return [_title(title) for title in policy.titles(member)[:k]]
    if surface == "home_because":
        anchor = last_watch(session, member_id, as_of, "title", completed=True)
        return [_title(title) for title in policy.titles(member, anchor=anchor.key)[:k]] if anchor else []
    interests = MemberInterestService(session).list_for(member)
    follows = MemberFollowService(session).followed_channel_keys(member)
    source = source or popular_snapshot(as_of)
    if surface == "home_picked":
        return [_remote(item) for item in policy.home(member, interests, source, followed_channel_keys=follows).items[:k]]
    current = last_watch(session, member_id, as_of, "remote")
    if current is None:
        return []
    subjects = next((item.category_keys for item in source.items if _remote(item).key == current.key), ())
    context = PlaybackContext.for_source(source=current.source or "youtube", item_id=current.remote_id, webpage_url=current.url,
                                         title=None, uploader=current.uploader, subject_keys=subjects)
    result = policy.up_next(member, interests, source, current=context, followed_channel_keys=follows)
    return [_remote(item) for item in result.items[:k]]


def eligible(session, member_id: str, as_of: datetime, surface: str) -> list[Candidate]:  # noqa: ANN001
    """The surface's candidate set at ``as_of``: the snapshot cut for remote surfaces, visible unstarted titles otherwise."""
    from sqlalchemy import select

    from app.models import MediaTitle, User
    from app.services.library import LibraryService
    from app.services.member_recommendations import TITLE_POOL_TYPES, MemberRecommendationPolicy

    if surface in ("home_picked", "up_next"):
        return [_remote(item) for item in popular_snapshot(as_of).items]
    member = session.get(User, member_id)
    policy = MemberRecommendationPolicy(session)
    excluded = set(policy._title_history(member)) | policy._suppressed_keys(member).title_keys
    if surface == "home_because":
        anchor = last_watch(session, member_id, as_of, "title", completed=True)
        if anchor is None:
            return []
        excluded.add(anchor.key)
    titles = session.scalars(select(MediaTitle).where(MediaTitle.type.in_(TITLE_POOL_TYPES), LibraryService.visible_title_predicate(member)).order_by(MediaTitle.id))
    return [_title(title) for title in titles if title.id not in excluded]


def _popular_item(row):  # noqa: ANN001, ANN202
    """A pooled remote_media row as the legacy scorer's PopularItem (the ablation feeds the new pool to the old ranking)."""
    from app.services.popular_discovery import PopularItem

    return PopularItem(
        id=row.remote_id, title=row.title, uploader=row.uploader, duration=row.duration, thumbnail=row.thumbnail, artwork_url=None, webpage_url=row.webpage_url,
        view_count=row.view_count, availability=row.availability, published_at=row.published_at, source=(row.extractor or "youtube").split(":")[0],
        source_label="YouTube", capabilities=None, category_keys=tuple(row.category_keys or ()), uploader_url=row.channel_url, uploader_id=None,
    )


def new_candidates(session, member_id: str, as_of: datetime, surface: str) -> list[Candidate]:  # noqa: ANN001
    """The new policy's candidate set at ``as_of``: the member's pool plus the Popular snapshot (remote), or the visible titles."""
    if surface not in ("home_picked", "up_next"):
        return eligible(session, member_id, as_of, surface)
    from app.services.reco.pool import remote_candidates

    pooled = _resolve(session, [candidate.key for candidate in remote_candidates(session, member_id, as_of=as_of)], "remote")
    seen = {candidate.key for candidate in pooled}
    return pooled + [candidate for candidate in map(_remote, popular_snapshot(as_of, cut=False).items) if candidate.key not in seen]


def ablation_policy(session, member_id: str, as_of: datetime, surface: str, k: int) -> list[Candidate]:  # noqa: ANN001
    """The legacy scorer on the new candidate set. Separates the gain from the pool from the gain from ranking."""
    from sqlalchemy import select

    from app.models import RemoteMedia
    from app.services.reco.pool import remote_candidates

    if surface not in ("home_picked", "up_next"):
        return legacy_policy(session, member_id, as_of, surface, k)  # titles: the legacy scorer already ranks every visible title
    snapshot = popular_snapshot(as_of, cut=False)
    have = {_remote(item).key for item in snapshot.items}
    keys = [candidate.key for candidate in remote_candidates(session, member_id, as_of=as_of)]
    pooled = [_popular_item(row) for row in session.scalars(select(RemoteMedia).where(RemoteMedia.key.in_(keys))) if _remote(_popular_item(row)).key not in have]
    return legacy_policy(session, member_id, as_of, surface, k, source=dataclasses.replace(snapshot, items=(*snapshot.items, *pooled)))


def _newest(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: (c.published_at is None, -(c.published_at.timestamp() if c.published_at else 0), c.key))


def _kind(surface: str) -> str:
    return "remote" if surface in ("home_picked", "up_next") else "title"


def newest_first(session, member_id: str, as_of: datetime, surface: str, k: int, pool=eligible) -> list[Candidate]:  # noqa: ANN001
    return _newest(pool(session, member_id, as_of, surface))[:k]


def household_top_channel(session, member_id: str, as_of: datetime, surface: str, k: int, pool=eligible) -> list[Candidate]:  # noqa: ANN001
    counts = Counter(watch.channel for watch in watches(session, None) if watch.kind == _kind(surface) and watch.at < as_of and watch.channel)
    return sorted(_newest(pool(session, member_id, as_of, surface)), key=lambda c: -counts[c.channel])[:k]


def last_watch_channel(session, member_id: str, as_of: datetime, surface: str, k: int, pool=eligible) -> list[Candidate]:  # noqa: ANN001
    last = last_watch(session, member_id, as_of, _kind(surface))
    channel = last.channel if last else None
    return sorted(_newest(pool(session, member_id, as_of, surface)), key=lambda c: not (channel and c.channel == channel))[:k]


POLICIES: dict[str, Policy] = {"legacy": legacy_policy, "new": new_policy, "ablation": ablation_policy}
BASELINES: dict[str, Policy] = {"newest_first": newest_first, "household_top_channel": household_top_channel, "last_watch_channel": last_watch_channel}


def _mean(values: list) -> float | None:
    present = [value for value in values if value is not None]
    return round(statistics.fmean(present), 4) if present else None


def _member_mean(per_member: list[dict[str, list[float]]], name: str) -> float | None:
    """Equal weight per member: each member's mean over their instances, then the mean across members."""
    return _mean([_mean(member[name]) for member in per_member if member.get(name)])


def replay(session_factory, policy_name: str = "legacy", *, also: tuple[str, ...] = ()) -> dict:  # noqa: ANN001
    """One pass over every instance. ``policy_name`` is the primary (the report's top-level keys); ``also`` are measured beside it.

    Every measured policy and the trivial baselines draw from one candidate universe (the union of the policies' candidate sets), so coverage has
    one denominator for everyone.
    """
    from functools import partial

    from sqlalchemy import select

    from app.models import User

    measured = (policy_name, *also)
    universe = new_candidates if {"new", "ablation"} & set(measured) else eligible
    rankers = {**{name: POLICIES[name] for name in measured}, **{name: partial(fn, pool=universe) for name, fn in BASELINES.items()}}
    UNRESOLVED.clear()
    with session_factory() as session:
        member_ids = list(session.scalars(select(User.id).where(User.is_active).order_by(User.id)))
    per_member: list[dict[str, list[float]]] = []
    counts: Counter = Counter()
    hidden: Counter = Counter()
    top20: dict[tuple[str, str], set[str]] = defaultdict(set)
    pools: dict[str, set[str]] = defaultdict(set)
    pool_sizes: dict[str, list[float]] = defaultdict(list)
    worst_share: dict[str, int] = defaultdict(int)
    over_cap: dict[str, int] = {}
    for member_id in member_ids:
        with session_factory() as session:
            instances = pick_instances(watches(session, member_id))
        values: dict[str, list[float]] = defaultdict(list)
        for watch in instances:
            as_of = watch.at - timedelta(seconds=1)
            with session_factory() as session:
                # The new policy's candidate loaders commit a household-cache upsert (write_transaction); that commit must not make
                # hide_after's deletions permanent, or the next instance would run on a copy that lost its later rows.
                session.commit = session.flush  # type: ignore[method-assign]
                hidden.update(hide_after(session, as_of, [watch]))
                if watch.kind == "remote":
                    surfaces = {"home_picked": ("remote_channel_hit@10", "remote_item_hit@10")}
                    previous = last_watch(session, member_id, as_of, "remote")
                    if previous is not None and watch.at - previous.at < UP_NEXT_GAP:
                        surfaces["up_next"] = ("up_next_channel_hit@10",)
                else:
                    surfaces = {"home_recommended": ("title_item_hit@10",)}
                    if last_watch(session, member_id, as_of, "title", completed=True) is not None:
                        surfaces["home_because"] = ("anchored_title_hit@10",)
                for surface, metrics in surfaces.items():
                    counts[surface] += 1
                    pool = universe(session, member_id, as_of, surface)
                    pools[surface].update(c.key for c in pool)
                    values[f"pool:{surface}"].append(len(pool))
                    if surface == "home_picked" and "new" in measured:
                        values["pool_recall"].append(float(bool(watch.channel) and watch.channel in {c.channel for c in pool}))
                    lists: dict[str, list[Candidate]] = {}
                    for name, ranker in rankers.items():
                        recs = ranker(session, member_id, as_of, surface, 20)
                        top10 = recs[:10]
                        for metric in metrics:
                            hit = watch.key in {c.key for c in top10} if "item" in metric else bool(watch.channel) and watch.channel in {c.channel for c in top10}
                            values[f"{metric}:{name}"].append(float(hit))
                        if name not in measured:
                            continue
                        top20[(name, surface)].update(c.key for c in recs)
                        lists[name] = recs
                    if surface == "home_picked":
                        # Spread and freshness are scored on paired instances (every measured policy returned a list);
                        # the unpaired figures (an empty list counts 0 channels and has no age) are reported beside them.
                        # One channel may hold max(3, ceil(12 / channels in the eligible pool)) of the top 12.
                        paired = all(lists[name] for name in measured)
                        cap = max(3, math.ceil(12 / max(1, len({c.channel for c in pool if c.channel}))))
                        for name in measured:
                            top12 = Counter(c.channel for c in lists[name][:12] if c.channel)
                            ages = [(as_of - c.published_at).total_seconds() / 86_400 for c in lists[name][:12] if c.published_at]
                            for suffix in ("_unpaired", "") if paired else ("_unpaired",):
                                values[f"distinct_channels{suffix}:{name}"].append(len(top12))
                                values[f"max_one_channel{suffix}:{name}"].append(max(top12.values(), default=0))
                                worst_share[name + suffix] = max(worst_share[name + suffix], max(top12.values(), default=0))
                                if ages:
                                    values[f"freshness_median_days{suffix}:{name}"].append(statistics.median(ages))
                            if paired:
                                over_cap[name] = max(over_cap.get(name, -12), max(top12.values(), default=0) - cap)
                session.rollback()
        if instances:
            per_member.append(values)
    for surface in SURFACES:
        pool_sizes[surface] = [_mean(member.get(f"pool:{surface}", [])) for member in per_member]

    def coverage(name: str) -> dict[str, float | None]:
        return {surface: round(len(top20[(name, surface)]) / len(pools[surface]), 4) if pools[surface] else None for surface in SURFACES}

    def top12(name: str, suffix: str = "") -> dict:
        spread = {
            "distinct_channels": _member_mean(per_member, f"distinct_channels{suffix}:{name}"),
            "max_one_channel": _member_mean(per_member, f"max_one_channel{suffix}:{name}"),
            "max_one_channel_worst": worst_share[name + suffix],
            "freshness_median_days": _member_mean(per_member, f"freshness_median_days{suffix}:{name}"),
            "instances": sum(len(member.get(f"max_one_channel{suffix}:{name}", [])) for member in per_member),
        }
        if not suffix:
            spread["max_one_channel_over_cap"] = over_cap.get(name)  # worst (top-12 count of one channel - that instance's cap)
            spread["unpaired"] = top12(name, "_unpaired")
        return spread

    return {
        "policy": policy_name,
        "policies": list(measured),
        "members_total": len(member_ids),
        "members_evaluated": len(per_member),
        "instances": {surface: counts[surface] for surface in SURFACES},
        "hit_at_10": {metric: {name: _member_mean(per_member, f"{metric}:{name}") for name in rankers} for metric in HIT_METRICS},
        "coverage_at_20": coverage(policy_name),
        "home_picked_top12": top12(policy_name),
        "by_policy": {name: {"coverage_at_20": coverage(name), "home_picked_top12": top12(name)} for name in measured},
        "pool_recall": _member_mean(per_member, "pool_recall") if "new" in measured else None,
        "unresolved_keys": sum(UNRESOLVED.values()),
        "pool_size_mean": {surface: _mean(pool_sizes[surface]) for surface in SURFACES},
        "hidden_rows_summed_over_instances": dict(sorted(hidden.items())),
        "limitations": [*LIMITATIONS, *EXTRA_LIMITATIONS],
    }


# Measured ceilings of the synthetic seed, on the seed of 2026-10-01:
# - remote: a policy that knows the seed's generator (the member's 3 channels, the newest-unwatched habit), held to the
#   diversity rows (one upload per own channel, then the newest of every other channel), over the same candidates: item 0.575, channel 0.80;
# - title: the seed draws a member's next film uniformly among their unwatched in-genre films, so a top 10 of in-genre films
#   hits mean(min(1, 10 / N)) per instance = 0.82, whatever the ranking.
# Re-measure them if seed_reco.py's generator changes.
ORACLE_REMOTE_ITEM = 0.575
ORACLE_REMOTE_CHANNEL = 0.80
ORACLE_TITLE = 0.82


def evaluate_targets(report: dict, *, synthetic: bool, policy: str = "new", baseline: str = "legacy") -> list[dict]:
    """The target table over a compare report. ``baseline`` is B. A missing value
    never passes; a tie with a trivial baseline fails, except remote item against newest_first on the synthetic seed."""
    rows: list[dict] = []

    def add(metric: str, value, required, ok: bool) -> None:  # noqa: ANN001
        rows.append({"metric": metric, "value": value, "required": required, "ok": bool(ok)})

    def at_least(metric: str, value, *needs) -> None:  # noqa: ANN001, ANN002
        if value is None or any(need is None for need in needs):
            return add(metric, value, None, False)
        need = round(max(needs), 4)
        add(metric, value, need, value >= need)

    def at_most(metric: str, value, *caps) -> None:  # noqa: ANN001, ANN002
        if value is None or any(cap is None for cap in caps):
            return add(metric, value, None, False)
        cap = round(min(caps), 4)
        add(metric, value, cap, value <= cap)

    hits = report["hit_at_10"]

    def hit(metric: str, relative: Callable[[float], float], floor: float = 0.0) -> None:
        b = hits[metric][baseline]
        at_least(metric, hits[metric][policy], None if b is None else relative(b), floor)

    if synthetic:  # the synthetic seed's hit targets are capped by its measured ceilings
        hit("remote_channel_hit@10", lambda b: max(b + 0.10, 0.9 * ORACLE_REMOTE_CHANNEL), 0.30)
        hit("remote_item_hit@10", lambda b: max(1.2 * b, 0.8 * ORACLE_REMOTE_ITEM), 0.10)
        hit("title_item_hit@10", lambda b: max(1.5 * b, 0.8 * ORACLE_TITLE))  # 
    else:
        hit("remote_channel_hit@10", lambda b: max(1.5 * b, b + 0.10), 0.30)
        hit("title_item_hit@10", lambda b: max(1.2 * b, b + 0.02))
    hit("anchored_title_hit@10", lambda b: 0.95 * b)
    hit("up_next_channel_hit@10", lambda b: 1.2 * b, 0.40)
    b = report["by_policy"][baseline]["coverage_at_20"]["home_picked"]
    at_least("coverage@20", report["by_policy"][policy]["coverage_at_20"]["home_picked"], None if b is None else 1.5 * b, 0.25)
    mine, theirs = report["by_policy"][policy]["home_picked_top12"], report["by_policy"][baseline]["home_picked_top12"]
    b = theirs["distinct_channels"]
    # Synthetic: B + 2 on paired instances asks for ~12 distinct of 12, so the bar is capped at 0.9 x 12.
    # Reference host: the same 0.9 x 12 cap, floor 7 (box legacy B = 12 is already the ceiling).
    need = None if b is None else max(b, min(b + 2, 0.9 * 12)) if synthetic else min(b + 2, 0.9 * 12)
    at_least("distinct_channels_top12", mine["distinct_channels"], need, 7)
    if synthetic:
        at_most("max_one_channel_top12_over_cap", mine["max_one_channel_over_cap"], 0)  # , per instance
    else:
        at_most("max_one_channel_top12", mine["max_one_channel_worst"], 3)  # 
    at_most("freshness_median_days", mine["freshness_median_days"], theirs["freshness_median_days"], 21)
    for metric in HIT_METRICS:
        if metric == "remote_item_hit@10" and not synthetic:
            continue
        value = hits[metric][policy]
        for name in BASELINES:
            rival = hits[metric][name]
            # The seed's next watch is drawn from the newest uploads, so newest_first is near its oracle there; a tie passes.
            tie_ok = synthetic and metric == "remote_item_hit@10" and name == "newest_first"
            ok = value is not None and rival is not None and (value >= rival if tie_ok else value > rival)
            add(f"{metric} {'>=' if tie_ok else '>'} {name}", value, rival, ok)
    return rows


def check_copy(data_dir: Path) -> Path:
    resolved = data_dir.resolve()
    if Path(tempfile.gettempdir()).resolve() not in resolved.parents:
        raise SystemExit("DATA_DIR must be a copy under the temporary directory, never the live app-data")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline recommendation replay on a copy of app-data.")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--policy", choices=[*sorted(POLICIES), "compare"], default="legacy", help="compare: the new policy against legacy and the ablation")
    parser.add_argument("--assert-targets", action="store_true", help="judge the target table (implies --policy compare); exit 1 if any row fails")
    parser.add_argument("--synthetic", action="store_true", help="the synthetic seed: also judge the remote item row (never on a production copy)")
    parser.add_argument("--json", type=Path, default=None, help="also write the report here")
    args = parser.parse_args()
    data_dir = check_copy(args.data_dir)
    os.environ.update({"LUMINA_DATA_DIR": str(data_dir), "LUMINA_AI_BASE_URL": "", "LUMINA_ASR_BASE_URL": "", "LUMINA_TMDB_API_KEY": ""})
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.db import SessionLocal, init_db

    compare = args.policy == "compare" or args.assert_targets
    if compare or args.policy in ("new", "ablation"):
        init_db()  # the temp copy upgrades to the current schema (a v8 copy gains the reco tables), as the app's own start does
    primary, also = ("new", ("legacy", "ablation")) if compare else (args.policy, ())
    report = replay(SessionLocal, primary, also=also)
    failed: list[dict] = []
    if args.assert_targets:
        report["targets"] = evaluate_targets(report, synthetic=args.synthetic)
        failed = [row for row in report["targets"] if not row["ok"]]
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    for row in failed:
        print(f"TARGET MISSED {row['metric']}: {row['value']} needs {row['required']}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
