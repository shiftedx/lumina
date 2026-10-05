"""Import a member's Jellyfin watch history (ADR 0010 amendment): match, plan, apply.

Matching never looks past the member's visibility: an item they cannot see in Lumina is never
matched, and unmatched entries are named only with what their own Jellyfin server said.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.media_schemas import JellyfinImportSummary
from app.models import (
    LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberFavorite, PlaybackProgress, StorageRoot, User, utcnow,
)
from app.services.jellyfin_history_client import JellyfinEntry, JellyfinHistory
from app.services.library import LibraryService
from app.services.titles import TitleService

PROVIDERS = ("Tmdb", "Imdb", "Tvdb")  # tried in this order; the first unique hit wins
MAX_UNMATCHED_NAMES = 200


def path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.replace("\\", "/").split("/") if part)


def common_tail(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    count = 0
    while count < min(len(a), len(b)) and a[-1 - count] == b[-1 - count]:
        count += 1
    return count


@dataclass(frozen=True)
class Target:
    """Where one Jellyfin entry lands: a Media title, and the exact Library item when a file path matched."""

    title_id: str | None
    item_id: str | None


class Matcher:
    """Indexes over what one member can see, in two queries; ``match`` adds one more for episodes."""

    def __init__(self, db: Session, user: User) -> None:
        self.db, self.user = db, user
        # (last two path components) -> [(all components, item id, title id)]; versions only, never extras or missing files.
        self.tails: dict[tuple[str, ...], list[tuple[tuple[str, ...], str, str | None]]] = defaultdict(list)
        files = db.execute(
            select(LibraryItem.id, LibraryItem.title_id, StorageRoot.path, MediaArtifact.relative_path)
            .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .where(LibraryService.visible_predicate(user), LibraryItem.status != "missing", LibraryItem.extra_type.is_(None))
        )
        for item_id, title_id, root_path, relative in files:
            parts = path_parts(f"{root_path}/{relative}")
            if len(parts) >= 2:
                self.tails[parts[-2:]].append((parts, item_id, title_id))
        self.providers: dict[tuple[str, str, str], set[str]] = defaultdict(set)  # (type, provider, id) -> title ids
        titles = db.execute(
            select(MediaTitle.id, MediaTitle.type, MediaTitle.provider_ids)
            .where(MediaTitle.type.in_(("movie", "series")), LibraryService.visible_title_predicate(user))
        )
        for title_id, kind, ids in titles:
            for provider in PROVIDERS:
                if value := (ids or {}).get(provider):
                    self.providers[(kind, provider, str(value))].add(title_id)

    def match(self, history: JellyfinHistory) -> tuple[list[tuple[JellyfinEntry, Target]], list[JellyfinEntry]]:
        """Each entry's target, in order: file path, then provider ids (episodes: their series' ids plus numbers)."""
        matched: list[tuple[JellyfinEntry, Target]] = []
        unmatched: list[JellyfinEntry] = []
        episodes: list[JellyfinEntry] = []
        for entry in history.entries:
            target = self._by_path(entry) if entry.type != "Series" else None
            if target is None and entry.type != "Episode":
                target = self._by_provider(entry.type.lower(), entry.provider_ids)  # "movie" | "series"
            if target is not None:
                matched.append((entry, target))
            elif entry.type == "Episode":
                episodes.append(entry)
            else:
                unmatched.append(entry)
        series_of = {
            entry.id: self._by_provider("series", history.series_provider_ids.get(entry.series_id or "", {}))
            for entry in episodes
        }
        numbered = self._episodes({target.title_id for target in series_of.values() if target is not None})
        for entry in episodes:
            series = series_of[entry.id]
            numbers_known = entry.season is not None and entry.episode is not None
            found = numbered.get((series.title_id, entry.season, entry.episode), set()) if series and numbers_known else set()
            if len(found) == 1:
                matched.append((entry, Target(next(iter(found)), None)))
            else:
                unmatched.append(entry)
        return matched, unmatched

    def _by_path(self, entry: JellyfinEntry) -> Target | None:
        """The one visible file sharing the longest trailing path (at least two components) with the Jellyfin file."""
        parts = path_parts(entry.path or "")
        candidates = self.tails.get(parts[-2:], []) if len(parts) >= 2 else []
        scored = [(common_tail(parts, other), item_id, title_id) for other, item_id, title_id in candidates]
        best = max((score for score, _, _ in scored), default=0)
        winners = [(item_id, title_id) for score, item_id, title_id in scored if score == best]
        return Target(title_id=winners[0][1], item_id=winners[0][0]) if len(winners) == 1 else None

    def _by_provider(self, kind: str, ids: dict[str, str]) -> Target | None:
        for provider in PROVIDERS:
            found = self.providers.get((kind, provider, ids.get(provider, "")), set())
            if len(found) == 1:
                return Target(title_id=next(iter(found)), item_id=None)
        return None

    def _episodes(self, series_ids: set[str]) -> dict[tuple[str, int | None, int | None], set[str]]:
        """(series id, season number, episode number) -> visible episode title ids, for the matched series."""
        index: dict[tuple[str, int | None, int | None], set[str]] = defaultdict(set)
        if not series_ids:
            return index
        season = aliased(MediaTitle)
        rows = self.db.execute(
            select(MediaTitle.id, season.parent_id, season.index_number, MediaTitle.index_number)
            .join(season, season.id == MediaTitle.parent_id)
            .where(MediaTitle.type == "episode", season.parent_id.in_(series_ids), LibraryService.visible_title_predicate(self.user))
        )
        for episode_id, series_id, season_number, number in rows:
            index[(series_id, season_number, number)].add(episode_id)
        return index


def merge(entry: JellyfinEntry, current: PlaybackProgress | None) -> tuple[int, bool] | None:
    """(position, completed) to write for one entry, or None when Lumina already has it or something newer.

    The one newer-wins rule: completed stays completed, and an existing row is replaced only by a strictly
    newer dated Jellyfin play. An undated resume point is skipped: it has no honest place in Continue watching.
    """
    if current is not None and (current.completed or entry.last_played is None or current.last_watched_at >= entry.last_played):
        return None
    if entry.played:
        return 0, True
    if entry.position_seconds > 0 and entry.last_played is not None:
        return entry.position_seconds, False
    return None


@dataclass(frozen=True)
class ProgressWrite:
    item_id: str
    position_seconds: int
    completed: bool
    last_watched_at: datetime
    duration_seconds: int | None


@dataclass
class ImportPlan:
    writes: dict[str, ProgressWrite] = field(default_factory=dict)  # Lumina title (or untitled item) id -> write
    favorites: set[str] = field(default_factory=set)  # new MemberFavorite target ids
    targets: set[str] = field(default_factory=set)  # every matched Lumina title or untitled item
    unmatched: list[str] = field(default_factory=list)  # Jellyfin's own names

    def summary(self) -> JellyfinImportSummary:
        changed = self.writes.keys() | self.favorites
        return JellyfinImportSummary(
            watched=sum(write.completed for write in self.writes.values()),
            in_progress=sum(not write.completed for write in self.writes.values()),
            favorites=len(self.favorites),
            up_to_date=len(self.targets - changed),
            unmatched=len(self.unmatched),
            unmatched_names=self.unmatched[:MAX_UNMATCHED_NAMES],
        )


def plan(db: Session, user: User, history: JellyfinHistory) -> ImportPlan:
    """What importing ``history`` would change for ``user`` now. Reads only; ``apply`` writes it."""
    matched, unmatched = Matcher(db, user).match(history)
    result = ImportPlan(unmatched=[entry.label for entry in unmatched])
    titles = TitleService(db)
    leaf_ids = [target.title_id for entry, target in matched if entry.type != "Series" and target.title_id]
    batch = titles.load(user, db.scalars(select(MediaTitle).where(MediaTitle.id.in_(leaf_ids))).all())
    untitled = [target.item_id for _, target in matched if target.title_id is None]
    loose = {item.id: item for item in db.scalars(select(LibraryItem).where(LibraryItem.id.in_(untitled)))}
    loose_progress = titles.progress_for(user, untitled)
    existing = titles.favorites_for(user, [target.title_id or target.item_id for _, target in matched])
    # Oldest first, so when two Jellyfin entries land on one Lumina title the newest one's write wins.
    for entry, target in sorted(matched, key=lambda pair: pair[0].last_played or datetime.min):
        key = target.title_id or target.item_id
        item: LibraryItem | None = None
        current: PlaybackProgress | None = None
        duration: float | None = None
        if entry.type != "Series":
            if target.title_id is not None:
                versions = batch.versions.get(target.title_id, [])
                item = next((v for v in versions if v.id == target.item_id), None) or batch.preferred_version(target.title_id)
                current, duration = batch.latest_progress(target.title_id), batch.duration(item)
            else:
                item, current = loose.get(target.item_id), loose_progress.get(target.item_id)
                duration = item.duration if item is not None else None
            if item is None:  # a visible title whose only visible files are extras
                result.unmatched.append(entry.label)
                continue
        result.targets.add(key)
        if entry.favorite and key not in existing:
            result.favorites.add(key)
        change = merge(entry, current) if item is not None else None
        if change is not None:
            result.writes[key] = ProgressWrite(
                item.id, change[0], change[1], entry.last_played or utcnow(), round(duration) if duration else None,
            )
    result.unmatched.sort()
    return result


def apply(db: Session, user: User, result: ImportPlan) -> None:
    """Write one plan. The caller owns the single write transaction; favorites are only ever added."""
    rows = TitleService(db).progress_for(user, [write.item_id for write in result.writes.values()])
    now = utcnow()
    for write in result.writes.values():
        row = rows.get(write.item_id)
        if row is None:
            row = PlaybackProgress(id=str(uuid.uuid4()), user_id=user.id, item_id=write.item_id, created_at=now)
            db.add(row)
        row.position_seconds, row.completed = write.position_seconds, write.completed
        row.duration_seconds = write.duration_seconds or row.duration_seconds
        row.last_watched_at = write.last_watched_at
        if row.dismissed_at is not None and row.dismissed_at < write.last_watched_at:
            row.dismissed_at = None  # like playing it: undoes an older dismiss, never a newer one
    db.add_all(MemberFavorite(user_id=user.id, target_id=target) for target in result.favorites)
    db.flush()
