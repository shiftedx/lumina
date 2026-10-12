"""Diagnostics' recommendations section: household totals over a window, never a member.

Every figure comes from indexed range scans of ``reco_events`` plus the progress rows behind attributed plays. A rate is None
when its denominator is under MIN_DENOMINATOR; counts stay. Nothing here carries a member, title or channel.
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.media_schemas import RecoDiagnostics, RecoPoolHealth, RecoSurfaceStats
from app.models import LibraryItem, MediaTitle, PlaybackProgress, RecoEvent, RecoPool, RemoteMedia, RemotePlaybackProgress, User

MIN_DENOMINATOR = 50
SURFACES = ("home_picked", "home_recommended", "home_because", "explore_for_you", "explore_popular", "up_next", "title_similar")
NEGATIVE = ("not_interested", "fewer", "hide_channel")
KINDS = ("impression", "open", "play", "complete", *NEGATIVE)
CHUNK = 500  # keys per IN (...) so a long window never nears SQLite's variable limit


def _rate(numerator: float, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator >= MIN_DENOMINATOR else None


@dataclass
class _Tally:
    impressions: int = 0
    explore_shown: int = 0
    exploit_shown: int = 0
    opens: set = field(default_factory=set)  # (user_id, list_id, item_key): one click per member, list and item
    plays: int = 0  # attributed: the event carries a list_id
    explore_played: int = 0
    exploit_played: int = 0
    completes: int = 0
    negatives: int = 0
    played: set = field(default_factory=set)  # (user_id, target_kind, item_key) behind the attributed plays


def _in_window(since: datetime, until: datetime) -> tuple:
    """The window's events: ~45k rows at five members, all read from the covering ix_reco_events_kind_surface."""
    return RecoEvent.at >= since, RecoEvent.at <= until, RecoEvent.kind.in_(KINDS)


def _chunks(pairs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    return [pairs[start:start + CHUNK] for start in range(0, len(pairs), CHUNK)]


def _play_through(db: Session, pairs: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], float]:
    """The current max_fraction behind each (member, target kind, item key); a title's is the best of its movie or episodes."""
    found: dict[tuple[str, str, str], float] = {}
    for chunk in _chunks(sorted((user, key) for user, kind, key in pairs if kind == "remote")):
        wanted = set(chunk)
        rows = db.execute(select(RemotePlaybackProgress.user_id, RemotePlaybackProgress.source_identity_key, RemotePlaybackProgress.max_fraction).where(
            RemotePlaybackProgress.user_id.in_({user for user, _ in chunk}), RemotePlaybackProgress.source_identity_key.in_({key for _, key in chunk}),
        ))
        found.update({(user, "remote", key): fraction for user, key, fraction in rows if (user, key) in wanted})
    leaf, season = aliased(MediaTitle), aliased(MediaTitle)
    top = case((leaf.type == "episode", season.parent_id), else_=leaf.id)  # a series for an episode, else the movie itself
    for chunk in _chunks(sorted((user, key) for user, kind, key in pairs if kind == "title")):
        wanted = set(chunk)
        rows = db.execute(
            select(PlaybackProgress.user_id, top, func.max(PlaybackProgress.max_fraction))
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(leaf, leaf.id == LibraryItem.title_id)
            .outerjoin(season, season.id == leaf.parent_id)
            .where(PlaybackProgress.user_id.in_({user for user, _ in chunk}), top.in_({key for _, key in chunk}))
            .group_by(PlaybackProgress.user_id, top)
        )
        found.update({(user, "title", key): fraction for user, key, fraction in rows if (user, key) in wanted})
    return found


def _surfaces(db: Session, now: datetime, window_days: int) -> tuple[list[RecoSurfaceStats], float | None]:
    tallies = {name: _Tally() for name in SURFACES}
    remote_plays = attributed_remote = 0
    window = _in_window(now - timedelta(days=window_days), now)
    attributed = RecoEvent.list_id.is_not(None)
    # Counted and de-duplicated in SQL: reading ~45k ORM-less rows into Python was most of the section's 200 ms. Grouped by the
    # index's own column order so SQLite streams the groups; count(list_id) is the attributed share of each group.
    for kind, surface, slot, target_kind, n, n_attributed in db.execute(
        select(RecoEvent.kind, RecoEvent.surface, RecoEvent.slot, RecoEvent.target_kind, func.count(), func.count(RecoEvent.list_id)).where(*window)
        .group_by(RecoEvent.kind, RecoEvent.surface, RecoEvent.slot, RecoEvent.target_kind)
    ):
        if kind == "play" and target_kind == "remote":
            remote_plays += n
            attributed_remote += n_attributed
        tally = tallies.get(surface)
        if tally is None:
            continue
        if kind == "impression":
            tally.impressions += n
            tally.explore_shown += n * (slot == "explore")
            tally.exploit_shown += n * (slot == "exploit")
        elif kind == "play":
            tally.plays += n_attributed
            tally.explore_played += n_attributed * (slot == "explore")
            tally.exploit_played += n_attributed * (slot == "exploit")
        elif kind == "complete":
            tally.completes += n_attributed
        elif kind in NEGATIVE:
            tally.negatives += n
    for surface, *key in db.execute(select(RecoEvent.surface, RecoEvent.user_id, RecoEvent.list_id, RecoEvent.item_key).where(*window, RecoEvent.kind == "open").distinct()):
        if surface in tallies:
            tallies[surface].opens.add(tuple(key))
    for surface, *key in db.execute(select(RecoEvent.surface, RecoEvent.user_id, RecoEvent.target_kind, RecoEvent.item_key).where(*window, RecoEvent.kind == "play", attributed).distinct()):
        if surface in tallies:
            tallies[surface].played.add(tuple(key))
    fractions = _play_through(db, set().union(*(tally.played for tally in tallies.values())))
    stats = []
    for name in SURFACES:
        tally = tallies[name]
        behind = [fractions[pair] for pair in tally.played if pair in fractions]
        stats.append(RecoSurfaceStats(
            surface=name, impressions=tally.impressions, opens=len(tally.opens), ctr=_rate(len(tally.opens), tally.impressions),
            plays=tally.plays,
            play_through_median=round(statistics.median(behind), 4) if behind and tally.plays >= MIN_DENOMINATOR else None,
            completion_rate=_rate(tally.completes, tally.plays), negative_rate=_rate(tally.negatives, tally.impressions),
            explore_play_rate=_rate(tally.explore_played, tally.explore_shown), exploit_play_rate=_rate(tally.exploit_played, tally.exploit_shown),
        ))
    return stats, _rate(attributed_remote, remote_plays)


def _pool(db: Session, now: datetime, refresher: Mapping[str, int], dropped_events_24h: int) -> RecoPoolHealth:
    """Per-member sizes and ages, summarised to household figures. A deactivated member's pool is not a member with a pool."""
    members = db.execute(
        select(func.count(), func.max(RecoPool.last_nominated_at)).select_from(RecoPool)
        .join(User, User.id == RecoPool.user_id).where(User.is_active).group_by(RecoPool.user_id)
    ).all()
    # "has a vector" rather than "has a vector under the current model"; tighten when R2 exposes the signature check.
    pooled_items = select(RecoPool.item_key).distinct()
    total, with_vector = db.execute(select(func.count(), func.count(RemoteMedia.vector)).where(RemoteMedia.key.in_(pooled_items))).one()
    newest = [at for _count, at in members if at is not None]
    return RecoPoolHealth(
        members_with_pool=len(members),
        median_pool_size=int(statistics.median(count for count, _at in members)) if members else 0,
        oldest_refresh_age_minutes=int((now - min(newest)).total_seconds() // 60) if newest else None,
        provider_calls_24h=int(refresher.get("provider_calls_24h", 0)), budget_hits_24h=int(refresher.get("budget_hits_24h", 0)),
        remote_vector_coverage=round(with_vector / total, 4) if total else None, dropped_events_24h=dropped_events_24h,
    )


def report(db: Session, *, now: datetime, enabled: bool, refresher: Mapping[str, int], dropped_events_24h: int, window_days: int = 28) -> RecoDiagnostics:
    surfaces, share = _surfaces(db, now, window_days)
    return RecoDiagnostics(enabled=enabled, window_days=window_days, surfaces=surfaces, reco_share_of_remote_plays=share,
                           pool=_pool(db, now, refresher, dropped_events_24h))
