"""Media title reads shared by Lumina's TV/movie UI and the Jellyfin API.

Every read is visibility-scoped (``visible_title_predicate`` for titles, ``visible_predicate`` for
items), and a page of titles is loaded set-based: a fixed number of queries whatever its size.
``next_up`` is pure and table-tested; the rest is a thin query layer around it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

from sqlalchemy import Float, Integer, and_, case, cast, false, func, literal, literal_column, or_, select
from sqlalchemy.orm import Session, aliased, defer

from app.media_schemas import (
    AlbumTrack, GenreFacet, LibraryUpNext, ResolutionFacet, TitleArt, TitleDetail, TitleExtra, TitleFacets, TitleLetter, TitleMatch, TitleResolution, TitleSummary,
    TitleUserData, TitleVersion, YearRange,
)
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberFavorite, PlaybackProgress, StorageRoot, TitleArtwork, TitleUpload, User
from app.persistence import read_regular_file
from app.schemas import PlaybackProgressUpdateRequest
from app.services.artwork import LOCAL_ARTWORK_TYPES, ArtworkError, ArtworkService
from app.services.audio_tags import embedded_picture
from app.services.library import LibraryService, decode_library_cursor, encode_library_cursor
from app.services.media_artifacts import artifact_file
from app.services.playback import PlaybackProgressService
from app.services.title_metadata import title_people
from app.services import art_urls, member_access, tmdb

UNNUMBERED = 1_000_000  # a season/episode without a number sorts after every numbered one


@dataclass(frozen=True)
class EpisodeProgress:
    """One episode of a series, in (season, episode) order, with the member's latest progress on any of its versions."""

    title_id: str
    season: int
    episode: int
    last_watched_at: datetime | None = None
    completed: bool = False
    position_seconds: int = 0


def resume_anchor(ordered_episodes: Sequence[EpisodeProgress]) -> EpisodeProgress | None:
    """The most recently watched regular (non-special) episode that was started or finished."""
    watched = [
        episode for episode in ordered_episodes
        if episode.season != 0 and episode.last_watched_at is not None and (episode.completed or episode.position_seconds > 0)
    ]
    return max(watched, key=lambda episode: episode.last_watched_at, default=None)


def next_up(ordered_episodes: Sequence[EpisodeProgress], *, dismissed: bool = False) -> EpisodeProgress | None:
    """The episode to watch next, anchored on the most recently watched one.

    Specials (season 0) are excluded. An in-progress anchor belongs to Resume, so this returns None.
    A completed anchor yields its immediate successor even if that was watched long ago, so a
    rewatch moves forward instead of jumping to the first never-played episode (Jellyfin #7534).
    A finished series returns None, and so does one whose anchor the member dismissed.
    """
    anchor = resume_anchor(ordered_episodes)
    if anchor is None or not anchor.completed or dismissed:
        return None
    regular = [episode for episode in ordered_episodes if episode.season != 0]
    position = regular.index(anchor)
    return regular[position + 1] if position + 1 < len(regular) else None


LEAF_TYPES = ("movie", "episode")
FOLDER_TYPES = ("series", "season", "boxset")
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
SORT_NAME = func.coalesce(MediaTitle.sort_name, MediaTitle.name).collate("NOCASE")
# models.WALL_TYPES_SQL as literals: SQLite uses a partial index only when the query repeats its WHERE (a bound type IN (?) does not).
WALL_TYPES = MediaTitle.type.in_([literal_column("'movie'"), literal_column("'series'")])
# models.MUSIC_TYPES_SQL as literals, for the music partial indexes.
MUSIC_TYPES = MediaTitle.type.in_([literal_column("'album'"), literal_column("'artist'")])
# The wall types each category lists: a category wall filters WALL_TYPES and category, never type.
CATEGORY_TYPES: dict[str, tuple[str, ...]] = {"movies": ("movie",), "shows": ("series",), "anime": ("movie", "series")}
# Recently added: when the files arrived, else when a scan met the title (models.MediaTitle.arrived_at, its index verbatim).
ADDED = func.coalesce(MediaTitle.added_at, MediaTitle.created_at)
TITLE_SORTS = {
    "name": (SORT_NAME, MediaTitle.id),
    "created": (ADDED.desc(), MediaTitle.id),
    "year": (MediaTitle.year.desc().nulls_last(), SORT_NAME, MediaTitle.id),
    "rating": (func.json_extract(MediaTitle.metadata_json, "$.community_rating").desc().nulls_last(), SORT_NAME, MediaTitle.id),
}


def user_data_from(progress: PlaybackProgress | None, *, favorite: bool) -> TitleUserData:
    """A playable's UserData from the member's latest progress row (a title uses its most recently watched version)."""
    if progress is None:
        return TitleUserData(is_favorite=favorite)
    return TitleUserData(
        played=progress.completed, is_favorite=favorite,
        position_seconds=0 if progress.completed else progress.position_seconds,
        duration_seconds=progress.duration_seconds, last_watched_at=progress.last_watched_at,
        resume_item_id=progress.item_id,
    )


def version_label(item: LibraryItem) -> str | None:
    """The scanner names versions "{title} · {label}"; reading it back needs no metadata_json load."""
    return item.title.rsplit(" · ", 1)[1] if " · " in item.title else None


EXTRA_BACKDROPS = 4
UP_NEXT_LIMIT = 12


def image_key(image_type: str, index: int = 0) -> str:
    """The images-map key: "Backdrop" for index 0, "Backdrop.1" to "Backdrop.4" for the extras; no other type has an index."""
    if index == 0:
        return image_type
    if image_type != "Backdrop" or not 0 < index <= EXTRA_BACKDROPS:
        raise ValueError("index")
    return f"Backdrop.{index}"


def title_image_source(title: MediaTitle, image_type: str) -> str | None:
    """What identifies a title's stored art: the scanner's tag, else its relative path, embedded track, TMDB path or
    upload sha. A tombstone ({"removed": True, ...}) resolves to nothing."""
    entry = (title.images or {}).get(image_type)
    if not isinstance(entry, dict) or entry.get("removed"):
        return None
    source = entry.get("tag") or entry.get("path") or entry.get("embedded") or entry.get("tmdb") or entry.get("upload")
    return str(source) if source else None


def image_tag(title: MediaTitle, image_type: str) -> str | None:
    """The 16-hex tag inside ``image_url``; it changes with the key and the source."""
    source = title_image_source(title, image_type)
    return hashlib.sha256(f"{title.id}:{image_type}:{source}".encode()).hexdigest()[:16] if source else None


def image_url(title: MediaTitle, image_type: str) -> str | None:
    """Lumina's art URL. The tag only busts browser caches; the route re-checks visibility on every request.
    The tag stays last (routers/titles.py checks it with endswith)."""
    tag = image_tag(title, image_type)
    if tag is None:
        return None
    base, _, index = image_type.partition(".")
    query = f"index={index}&" if index else ""
    return f"/api/titles/{title.id}/images/{base}?{query}tag={tag}"


def playable_exists(user: User, leaf=MediaTitle):  # noqa: ANN001, ANN201
    """EXISTS a visible, non-missing version (not an extra) of ``leaf``."""
    return select(LibraryItem.id).where(
        LibraryItem.title_id == leaf.id, LibraryItem.extra_type.is_(None),
        LibraryItem.status != "missing", LibraryService.visible_predicate(user),
    ).exists()


def latest_progress(user: User, column, leaf=MediaTitle):  # noqa: ANN001, ANN201
    """The member's most recently watched version's ``column`` for ``leaf`` (NULL when never watched), correlated."""
    return (
        select(column).join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
        .where(func.coalesce(PlaybackProgress.user_id, "") == user.id, LibraryItem.title_id == leaf.id)
        .order_by(PlaybackProgress.last_watched_at.desc()).limit(1).scalar_subquery()
    )




# ---- wall listing ---------------------------------------------

RATING = func.json_extract(MediaTitle.metadata_json, "$.community_rating")
# The values a keyset cursor stores for each sort: the last row's, in TITLE_SORTS order, before its id.
SORT_KEYS = {"name": (SORT_NAME,), "created": (ADDED,), "year": (MediaTitle.year, SORT_NAME), "rating": (RATING, SORT_NAME)}
# 3: created keys are ADDED. A version-2 created cursor held created_at and restarts at the first page; others still hold.
CURSOR_VERSION = 3
MAX_CURSOR_INDEX = 10**9


@dataclass(frozen=True)
class TitleFilters:
    """The wall's chips and drawer. The default matches everything, as before the gallery."""

    unwatched: bool = False
    in_progress: bool = False
    favorites: bool = False
    genres: tuple[str, ...] = ()  # OR, ASCII case-insensitive (NOCASE)
    year_from: int | None = None
    year_to: int | None = None
    resolutions: tuple[str, ...] = ()  # OR over TitleResolution buckets

    def digest(self, types: Sequence[str], sort: str, category: str | None = None) -> str:
        """12 hex of the canonical list identity (types, category, sort, filters): a cursor is valid only for the list it
        came from. Adding category retired every earlier cursor once; the wall restarts."""
        canonical = {
            **dataclasses.asdict(self), "genres": sorted(self.genres), "resolutions": sorted(self.resolutions),
            "types": sorted(types), "sort": sort, "category": category,
        }
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class TitleSeek:
    """Where the next page starts: after the row with these sort ``keys`` and ``last_id``, at list position ``index``."""

    keys: tuple[Any, ...]
    last_id: str
    index: int


def resolution_bucket():  # noqa: ANN201
    """A version's resolution bucket from its cached probe: "4k", "1080p", "720p", "sd", or NULL when the height is unknown."""
    height = cast(func.json_extract(MediaArtifact.probe, "$.height"), Integer)
    width = cast(func.json_extract(MediaArtifact.probe, "$.width"), Integer)
    return case(
        (or_(height >= 2000, width >= 3200), "4k"),
        (or_(height >= 1000, width >= 1800), "1080p"),
        (height >= 700, "720p"),
        (height.is_not(None), "sd"),
        else_=None,
    )


def resolution_exists(user: User, buckets: Sequence[str], leaf=MediaTitle):  # noqa: ANN001, ANN201
    """EXISTS a visible, non-missing version (not an extra) of ``leaf`` in one of ``buckets``: seeks ix_library_items_title_id."""
    return (
        select(LibraryItem.id)
        .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
        .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .where(LibraryItem.title_id == leaf.id, LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
               LibraryService.visible_predicate(user), resolution_bucket().in_(list(buckets)))
        .exists()
    )


def _episode_exists(user: User, condition: Callable[[Any], Any]):  # noqa: ANN202
    """EXISTS a playable visible episode of the enclosing series (``MediaTitle``) for which ``condition(episode)`` holds."""
    episode, season = aliased(MediaTitle), aliased(MediaTitle)
    return (
        select(episode.id).join(season, season.id == episode.parent_id)
        .where(season.parent_id == MediaTitle.id, episode.type == "episode", playable_exists(user, episode), condition(episode))
        .exists()
    )


def filter_predicates(user: User, types: Sequence[str], filters: TitleFilters) -> list:
    """SQL predicates for ``filters``, correlated with the enclosing query's ``MediaTitle``.

    Watched-state and resolution rules apply to movies and episodes directly and to a series through its visible
    episodes. Seasons and boxsets match none of them (no wall lists them). Every EXISTS seeks by title or parent id,
    never the member's progress history.
    """

    def per_type(leaf_rule: Callable[[], Any], series_rule: Callable[[], Any]):  # noqa: ANN202
        rules = [
            and_(MediaTitle.type == kind, leaf_rule() if kind in LEAF_TYPES else series_rule())
            for kind in types if kind in LEAF_TYPES or kind == "series"
        ]
        return or_(*rules) if rules else false()

    def unwatched(leaf=MediaTitle):  # noqa: ANN001, ANN202
        return latest_progress(user, PlaybackProgress.completed, leaf).is_not(True)

    def started(leaf) -> Any:  # noqa: ANN001
        return latest_progress(user, PlaybackProgress.id, leaf).is_not(None)

    def in_resolution(leaf=MediaTitle):  # noqa: ANN001, ANN202
        return resolution_exists(user, filters.resolutions, leaf)

    predicates: list = []
    if filters.unwatched:
        predicates.append(per_type(unwatched, lambda: _episode_exists(user, unwatched)))
    if filters.in_progress:
        predicates.append(per_type(
            lambda: and_(unwatched(), latest_progress(user, PlaybackProgress.position_seconds) > 0),
            lambda: and_(_episode_exists(user, started), _episode_exists(user, unwatched)),
        ))
    if filters.favorites:
        predicates.append(select(MemberFavorite.target_id).where(MemberFavorite.user_id == user.id, MemberFavorite.target_id == MediaTitle.id).exists())
    if filters.genres:
        genres = func.json_each(MediaTitle.metadata_json, "$.genres").table_valued("value")
        predicates.append(select(literal(1)).select_from(genres).where(genres.c.value.collate("NOCASE").in_(list(filters.genres))).exists())
    if filters.year_from is not None:
        predicates.append(MediaTitle.year >= filters.year_from)
    if filters.year_to is not None:
        predicates.append(MediaTitle.year <= filters.year_to)
    if filters.resolutions:
        predicates.append(per_type(in_resolution, lambda: _episode_exists(user, in_resolution)))
    return predicates


def seek_after(sort: str, seek: TitleSeek):  # noqa: ANN201
    """Rows after ``seek`` in TITLE_SORTS[sort] order, written as a range plus a tie-break so SQLite seeks the index."""

    def after_name(name: str):  # noqa: ANN202
        return and_(SORT_NAME >= name, or_(SORT_NAME > name, MediaTitle.id > seek.last_id))

    if sort == "name":
        return after_name(seek.keys[0])
    if sort == "created":
        created = seek.keys[0]
        return and_(ADDED <= created, or_(ADDED < created, MediaTitle.id > seek.last_id))
    column = MediaTitle.year if sort == "year" else RATING
    value, name = seek.keys
    if value is None:  # NULLS LAST: only the rest of the null tail remains
        return and_(column.is_(None), after_name(name))
    return or_(column < value, column.is_(None), and_(column == value, after_name(name)))


def encode_title_cursor(sort: str, digest: str, seek: TitleSeek) -> str:
    keys = [key.isoformat() if isinstance(key, datetime) else key for key in seek.keys]
    return encode_library_cursor({"v": CURSOR_VERSION, "s": sort, "f": digest, "k": keys, "id": seek.last_id, "i": seek.index})


def decode_title_cursor(cursor: str, sort: str, digest: str) -> TitleSeek | None:
    """The seek in a cursor this list issued; None for the old ``{"o": n}`` offsets and version-2 created cursors (a tab
    open across the upgrade restarts at the first page). ValueError for anything else: another sort or filter set, or keys of the wrong type
    (a forged cursor is a 400, never a SQL type error)."""
    payload = decode_library_cursor(cursor)
    if set(payload) == {"o"}:
        return None
    keys, last_id, index = payload.get("k"), payload.get("id"), payload.get("i")
    version = payload.get("v")
    if version not in (2, CURSOR_VERSION) or (payload.get("s"), payload.get("f")) != (sort, digest):
        raise ValueError("Invalid cursor")
    if not isinstance(keys, list) or len(keys) != len(SORT_KEYS[sort]) or not isinstance(last_id, str) or not 0 < len(last_id) <= 36:
        raise ValueError("Invalid cursor")
    if type(index) is not int or not 0 < index <= MAX_CURSOR_INDEX:
        raise ValueError("Invalid cursor")
    seek = TitleSeek(tuple(_cursor_key(sort, position, key) for position, key in enumerate(keys)), last_id, index)
    return None if version == 2 and sort == "created" else seek


def _cursor_key(sort: str, position: int, key: Any) -> Any:
    if sort == "created":
        if not isinstance(key, str):
            raise ValueError("Invalid cursor")
        return datetime.fromisoformat(key)
    if position == len(SORT_KEYS[sort]) - 1:  # name, and the name tie-break of year and rating
        valid = isinstance(key, str)
    elif sort == "year":  # any stored year (NFOs hold 1860s films and typos), but within SQLite's INTEGER: 2**70 is a 500
        valid = key is None or (type(key) is int and -(2**63) <= key < 2**63)
    else:  # rating: whatever json_extract returned; the bound also rejects NaN and infinities
        valid = key is None or isinstance(key, str) or (type(key) in (int, float) and -1e9 <= key <= 1e9)
    if not valid:
        raise ValueError("Invalid cursor")
    return key


# Music: album and artist titles are counted like folders but never played like them.
MUSIC_TITLE_TYPES = ("album", "artist")
TRACK_DISC = cast(func.json_extract(LibraryItem.metadata_json, "$.disc_number"), Integer)
TRACK_NUMBER = cast(func.json_extract(LibraryItem.metadata_json, "$.track_number"), Integer)
TRACK_ARTIST = func.json_extract(LibraryItem.metadata_json, "$.artist")


@dataclass
class TitleBatch:
    """Everything a page of titles needs, loaded in a fixed number of queries."""

    titles: dict[str, MediaTitle] = field(default_factory=dict)  # the page plus season/series ancestors
    versions: dict[str, list[LibraryItem]] = field(default_factory=dict)  # leaf -> visible playable items, oldest first
    artifacts: dict[str, MediaArtifact] = field(default_factory=dict)  # item id -> artifact (with_artifacts only)
    probe_durations: dict[str, float] = field(default_factory=dict)  # item id -> cached probe duration (always loaded)
    progress: dict[str, PlaybackProgress] = field(default_factory=dict)  # item id -> the member's row
    favorites: set[str] = field(default_factory=set)
    folder_counts: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # folder -> (children, leaves, unplayed)
    folder_watched: dict[str, datetime] = field(default_factory=dict)  # series/season -> latest episode last_watched_at
    artwork: dict[tuple[str, str], TitleArtwork] = field(default_factory=dict)  # (title id, image type) -> row, page titles only
    art_scope: str = ""  # member_access.art_scope of the member the page is for: binds their /api/art signatures

    def latest_progress(self, title_id: str) -> PlaybackProgress | None:
        rows = [self.progress[item.id] for item in self.versions.get(title_id, []) if item.id in self.progress]
        return max(rows, key=lambda row: row.last_watched_at, default=None)

    def preferred_version(self, title_id: str) -> LibraryItem | None:
        """The one "preferred version" rule (Tracks B and G): the most recently watched visible version, else the
        largest file (ties: the first added)."""
        versions = self.versions.get(title_id) or []
        latest = self.latest_progress(title_id)
        watched = next((item for item in versions if latest is not None and item.id == latest.item_id), None)
        return watched or max(versions, key=lambda item: item.file_size or 0, default=None)

    def duration(self, item: LibraryItem | None) -> float | None:
        """An item's runtime: its own duration, else the cached probe's (imports only fill the probe)."""
        return (item.duration or self.probe_durations.get(item.id)) if item else None

    def runtime(self, title: MediaTitle) -> float | None:
        """A leaf's runtime in seconds: the preferred version's, else TMDB's runtime_minutes."""
        meta = title.metadata_json or {}
        return self.duration(self.preferred_version(title.id)) or (meta.get("runtime_minutes") or 0) * 60 or None

    def user_data(self, title: MediaTitle) -> TitleUserData:
        favorite = title.id in self.favorites
        if title.type in FOLDER_TYPES:
            _children, leaves, unplayed = self.folder_counts.get(title.id, (0, 0, 0))
            return TitleUserData(
                played=leaves > 0 and unplayed == 0, is_favorite=favorite, unplayed_count=unplayed,
                last_watched_at=self.folder_watched.get(title.id),  # Series CTA reads "Continue" from this
            )
        return user_data_from(self.latest_progress(title.id), favorite=favorite)

    def ancestors(self, title: MediaTitle) -> tuple[MediaTitle | None, MediaTitle | None]:
        """(season, series) of an episode or season, taken from the batch."""
        if title.type == "season":
            return None, self.titles.get(title.parent_id)
        season = self.titles.get(title.parent_id) if title.type == "episode" else None
        return season, (self.titles.get(season.parent_id) if season else None)

    def art(self, title: MediaTitle, image_type: str, *, preview: bool) -> TitleArt | None:
        """The gallery TitleArt of one of ``title``'s images, from the batch's title_artwork rows."""
        return art_urls.title_art(
            title, image_type, source=title_image_source(title, image_type), url=image_url(title, image_type),
            row=self.artwork.get((title.id, image_type)), preview=preview, scope=self.art_scope,
        )


def _probe_seconds(artifact: MediaArtifact | None) -> int | None:
    try:
        return round(float(((artifact.probe if artifact else None) or {}).get("duration") or 0)) or None
    except (TypeError, ValueError):
        return None


class TitleService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def visible(user: User):  # noqa: ANN205
        return LibraryService.visible_title_predicate(user)

    def get_visible(self, title_id: str | None, user: User) -> MediaTitle | None:
        if not title_id:
            return None
        return self.db.scalar(select(MediaTitle).where(MediaTitle.id == title_id, self.visible(user)))

    # ---- set-based page loading ------------------------------------------------

    def load(self, user: User, titles: Iterable[MediaTitle], *, with_artifacts: bool = False, with_metadata: bool = False) -> TitleBatch:
        """Ancestors, versions (+artifacts), progress, favorites, folder counts and art rows for ``titles``: ≤ 7 queries."""
        batch = TitleBatch(titles={title.id: title for title in titles}, art_scope=member_access.art_scope(self.db, user))
        page = list(batch.titles.values())
        if not page:
            return batch
        self._load_ancestors(batch, page)
        leaf_ids = [title.id for title in page if title.type in LEAF_TYPES]
        if leaf_ids:
            self._load_versions(batch, user, leaf_ids, with_artifacts=with_artifacts, with_metadata=with_metadata)
        batch.progress = self.progress_for(user, [item.id for items in batch.versions.values() for item in items])
        batch.favorites = self.favorites_for(user, [title.id for title in page])
        self._load_folder_counts(batch, user, [title for title in page if title.type in FOLDER_TYPES or title.type in MUSIC_TITLE_TYPES])
        self._load_artwork(batch, page)
        return batch

    def _load_artwork(self, batch: TitleBatch, page: list[MediaTitle]) -> None:
        """The page's title_artwork rows (one primary-key query); stale or missing ones go to the rendition pass."""
        rows = self.db.scalars(select(TitleArtwork).where(TitleArtwork.title_id.in_([title.id for title in page])))
        batch.artwork = {(row.title_id, row.image_type): row for row in rows}
        art_urls.enqueue(
            (title.id, image_type) for title in page for image_type in art_urls.ART_TYPES
            if art_urls.needs_preparation(title, image_type, title_image_source(title, image_type), batch.artwork.get((title.id, image_type)))
        )

    def progress_for(self, user: User, item_ids: list[str]) -> dict[str, PlaybackProgress]:
        if not item_ids:
            return {}
        rows = self.db.scalars(select(PlaybackProgress).where(PlaybackProgress.user_id == user.id, PlaybackProgress.item_id.in_(item_ids)))
        return {row.item_id: row for row in rows}

    def favorites_for(self, user: User, target_ids: list[str]) -> set[str]:
        if not target_ids:
            return set()
        return set(self.db.scalars(select(MemberFavorite.target_id).where(MemberFavorite.user_id == user.id, MemberFavorite.target_id.in_(target_ids))))

    def artifacts_for(self, item_ids: list[str]) -> dict[str, MediaArtifact]:
        if not item_ids:
            return {}
        rows = self.db.execute(
            select(LibraryItemArtifact.library_item_id, MediaArtifact)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .where(LibraryItemArtifact.library_item_id.in_(item_ids))
        ).all()
        return {item_id: artifact for item_id, artifact in rows}

    def _load_ancestors(self, batch: TitleBatch, page: list[MediaTitle]) -> None:
        # Ancestors of visible titles are visible (visibility flows up), so no predicate here.
        wanted = {title.parent_id for title in page if title.type in ("season", "episode", "album") and title.parent_id}
        wanted |= {batch.titles[i].parent_id for i in wanted if i in batch.titles and batch.titles[i].type == "season" and batch.titles[i].parent_id}
        wanted -= batch.titles.keys()
        if not wanted:
            return
        grandparents = select(MediaTitle.parent_id).where(MediaTitle.id.in_(wanted), MediaTitle.type == "season")
        for title in self.db.scalars(select(MediaTitle).where(or_(MediaTitle.id.in_(wanted), MediaTitle.id.in_(grandparents)))):
            batch.titles.setdefault(title.id, title)

    def _load_versions(self, batch: TitleBatch, user: User, leaf_ids: list[str], *, with_artifacts: bool, with_metadata: bool) -> None:
        query = select(LibraryItem).where(
            LibraryItem.title_id.in_(leaf_ids), LibraryItem.extra_type.is_(None),
            LibraryItem.status != "missing", LibraryService.visible_predicate(user),
        ).order_by(LibraryItem.created_at, LibraryItem.id)
        if not with_metadata:
            query = query.options(defer(LibraryItem.metadata_json))
        query = (
            query.add_columns(cast(func.json_extract(MediaArtifact.probe, "$.duration"), Float))
            .outerjoin(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        )
        if with_artifacts:
            query = query.add_columns(MediaArtifact)
        for item, probe_duration, *artifact in self.db.execute(query).all():
            batch.versions.setdefault(item.title_id, []).append(item)
            if probe_duration and probe_duration > 0:
                batch.probe_durations[item.id] = probe_duration
            if artifact and artifact[0] is not None:
                batch.artifacts[item.id] = artifact[0]

    def _load_folder_counts(self, batch: TitleBatch, user: User, folders: list[MediaTitle]) -> None:
        """(children, leaves, unplayed) per folder: one grouped query each for shows, boxsets and music.

        An album counts (tracks, tracks, 0) and an artist (albums, tracks, 0): its visible, non-missing tracks, grouped by
        album, seeking albums by id or artist and tracks by title id.
        """
        shows = [title.id for title in folders if title.type in ("series", "season")]
        boxsets = [title.id for title in folders if title.type == "boxset"]
        music = [title.id for title in folders if title.type in MUSIC_TITLE_TYPES]
        leaf = aliased(MediaTitle)
        unplayed = func.sum(case((latest_progress(user, PlaybackProgress.completed, leaf).is_(True), 0), else_=1))
        if shows:
            season = aliased(MediaTitle)
            rows = self.db.execute(
                select(season.id, season.parent_id, func.count(), unplayed, func.max(latest_progress(user, PlaybackProgress.last_watched_at, leaf)))
                .select_from(leaf).join(season, season.id == leaf.parent_id)
                .where(leaf.type == "episode", or_(season.id.in_(shows), season.parent_id.in_(shows)), playable_exists(user, leaf))
                .group_by(season.id, season.parent_id)
            ).all()
            for season_id, series_id, episodes, left, watched_at in rows:
                batch.folder_counts[season_id] = (episodes, episodes, left)
                if watched_at is not None:
                    batch.folder_watched[season_id] = watched_at
                    if series_id not in batch.folder_watched or watched_at > batch.folder_watched[series_id]:
                        batch.folder_watched[series_id] = watched_at
                seasons, total, remaining = batch.folder_counts.get(series_id, (0, 0, 0))
                batch.folder_counts[series_id] = (seasons + 1, total + episodes, remaining + left)
        if boxsets:
            rows = self.db.execute(
                select(leaf.boxset_id, func.count(), unplayed)
                .where(leaf.type == "movie", leaf.boxset_id.in_(boxsets), playable_exists(user, leaf))
                .group_by(leaf.boxset_id)
            ).all()
            for boxset_id, movies, left in rows:
                batch.folder_counts[boxset_id] = (movies, movies, left)
        if music:
            album = aliased(MediaTitle)
            rows = self.db.execute(
                select(album.id, album.parent_id, func.count(LibraryItem.id))
                .select_from(LibraryItem).join(album, album.id == LibraryItem.title_id)
                .where(or_(album.id.in_(music), album.parent_id.in_(music)), LibraryItem.extra_type.is_(None),
                       LibraryItem.status != "missing", LibraryService.visible_predicate(user))
                .group_by(album.id, album.parent_id)
            ).all()
            for album_id, artist_id, tracks in rows:
                batch.folder_counts[album_id] = (tracks, tracks, 0)
                albums, total, _unplayed = batch.folder_counts.get(artist_id, (0, 0, 0))
                batch.folder_counts[artist_id] = (albums + 1, total + tracks, 0)

    # ---- lists -------------------------------------------------------------------

    def list_predicates(self, user: User, types: Sequence[str], filters: TitleFilters, category: str | None = None) -> list:
        """The wall's WHERE, with the partial indexes' WHERE repeated as literals so the planner can use them.

        A category wall filters WALL_TYPES and category, never type, so the category indexes
        drive it; a type narrower than its category (anime movies) is the one exception. Music walls repeat MUSIC_TYPES.
        """
        rest = [self.visible(user), *filter_predicates(user, types, filters)]
        if category is not None:
            narrowed = [] if tuple(types) == CATEGORY_TYPES[category] else [MediaTitle.type.in_(types)]
            return [WALL_TYPES, MediaTitle.category == category, *narrowed, *rest]
        if set(types) <= {"album", "artist"}:
            return [MediaTitle.type.in_(types), MUSIC_TYPES, *rest]
        wall = [WALL_TYPES] if set(types) <= {"movie", "series"} else []
        return [MediaTitle.type.in_(types), *wall, *rest]

    def page(
        self, user: User, *, types: Sequence[str], sort: str, limit: int, filters: TitleFilters = TitleFilters(),
        after: TitleSeek | None = None, letter: str | None = None, category: str | None = None,
    ) -> tuple[list[MediaTitle], int, TitleSeek | None]:
        """One keyset page: (titles, position of the first in the full list, the next page's seek or None).

        ``after`` continues a cursor. ``letter`` ("#" or "A".."Z", name sort only) starts at the first title sorting at
        or after it; "#" is the start of the list.
        """
        start = after.index if after is not None else 0
        query = select(MediaTitle, *SORT_KEYS[sort]).where(*self.list_predicates(user, types, filters, category))
        if after is not None:
            query = query.where(seek_after(sort, after))
        elif letter is not None and letter != "#":
            query = query.where(SORT_NAME >= letter)
            start = self.count(user, types=types, filters=filters, before=letter, category=category)
        rows = self.db.execute(query.order_by(*TITLE_SORTS[sort]).limit(limit + 1)).all()
        titles = [row[0] for row in rows[:limit]]
        if len(rows) <= limit:
            return titles, start, None
        last = rows[limit - 1]
        return titles, start, TitleSeek(tuple(last[1:]), last[0].id, start + limit)

    def count(self, user: User, *, types: Sequence[str], filters: TitleFilters, before: str | None = None, category: str | None = None) -> int:
        """Titles in the filtered list (``before``: only those sorting before that name, for a letter's start index)."""
        query = select(func.count()).select_from(MediaTitle).where(*self.list_predicates(user, types, filters, category))
        if before is not None:
            query = query.where(SORT_NAME < before)
        return self.db.scalar(query) or 0

    def letters(self, user: User, *, types: Sequence[str], filters: TitleFilters, category: str | None = None) -> tuple[list[TitleLetter], int]:
        """The A–Z rail anchors in list order, and the list's total, from one grouped index scan.

        Initials outside ASCII A–Z (digits, punctuation, "É" after "Z") share one "#" anchor at the earliest of them.
        """
        initial = func.upper(func.substr(func.coalesce(MediaTitle.sort_name, MediaTitle.name), 1, 1))
        anchors: dict[str, int] = {}
        total = 0
        # Return one row per actual initial instead of materialising every visible
        # title in Python. Keep non-ASCII initials separate in SQL because their
        # groups can straddle A–Z; folding them all to "#" before ordering would
        # shift the ASCII anchors after an early digit by the count of a trailing É.
        rows = self.db.execute(
            select(initial, func.count())
            .where(*self.list_predicates(user, types, filters, category))
            .group_by(initial)
            .order_by(func.min(SORT_NAME))
        )
        for first, count in rows:
            anchors.setdefault(first if len(first) == 1 and "A" <= first <= "Z" else "#", total)
            total += count
        return [TitleLetter(letter=letter, index=index) for letter, index in anchors.items()], total

    def facets(self, user: User, title_type: str | None = None, *, category: str | None = None) -> TitleFacets:
        """Genres, year range and resolution buckets of one wall's visible titles:
        a type (movie, series, album) or a category (its movies and series). Albums have no resolutions."""
        if category is not None:
            visible, kinds = (WALL_TYPES, MediaTitle.category == category, self.visible(user)), CATEGORY_TYPES[category]
        elif title_type == "album":
            visible, kinds = (MediaTitle.type == title_type, MUSIC_TYPES, self.visible(user)), ()
        else:
            visible, kinds = (MediaTitle.type == title_type, WALL_TYPES, self.visible(user)), (title_type,)
        genres = func.json_each(MediaTitle.metadata_json, "$.genres").table_valued("value", "type")
        genre_rows = self.db.execute(
            select(func.min(genres.c.value), func.count(func.distinct(MediaTitle.id)))
            .select_from(MediaTitle).join(genres, literal(True))
            .where(*visible, genres.c.type == "text")
            .group_by(genres.c.value.collate("NOCASE")).order_by(func.min(genres.c.value).collate("NOCASE"))
        ).all()
        low, high = self.db.execute(select(func.min(MediaTitle.year), func.max(MediaTitle.year)).where(*visible)).one()
        counts: Counter[str] = Counter()
        for kind in kinds:  # a category's movies and series are disjoint title sets, so their counts add
            counts.update(dict(self.db.execute(self._resolution_counts(user, kind, category)).all()))
        return TitleFacets(
            genres=[GenreFacet(name=name, count=count) for name, count in genre_rows],
            years=YearRange(min=low, max=high) if low is not None else None,
            resolutions=[ResolutionFacet(value=value, count=counts[value]) for value in get_args(TitleResolution) if counts.get(value)],
        )

    @staticmethod
    def _resolution_counts(user: User, title_type: str, category: str | None = None):  # noqa: ANN205
        """(bucket, titles): a movie counts in every bucket one of its versions is in, a series in every bucket an episode
        is in; ``category`` keeps that category's movies, or the episodes whose season (hence series) is in it.

        A visible version implies a visible title (visibility flows up), so no title predicate is needed here.
        """
        bucket = resolution_bucket()
        leaf = aliased(MediaTitle)
        query = (
            select(bucket).select_from(LibraryItem)
            .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .join(leaf, leaf.id == LibraryItem.title_id)
            .where(LibraryItem.extra_type.is_(None), LibraryItem.status != "missing", LibraryService.visible_predicate(user), bucket.is_not(None))
        )
        if title_type == "series":
            season = aliased(MediaTitle)
            query = query.join(season, season.id == leaf.parent_id).where(leaf.type == "episode")
            if category is not None:
                query = query.where(season.category == category)
            owner = season.parent_id
        else:
            query = query.where(leaf.type == title_type)
            if category is not None:
                query = query.where(leaf.category == category)
            owner = leaf.id
        return query.add_columns(func.count(func.distinct(owner))).group_by(bucket)

    def children(self, user: User, title: MediaTitle) -> list[MediaTitle]:
        """Visible seasons of a series, episodes of a season, movies of a boxset, albums of an artist (newest first)."""
        if title.type == "artist":
            query = select(MediaTitle).where(MediaTitle.parent_id == title.id).order_by(MediaTitle.year.desc().nulls_last(), SORT_NAME, MediaTitle.id)
        elif title.type == "boxset":
            query = select(MediaTitle).where(MediaTitle.type == "movie", MediaTitle.boxset_id == title.id).order_by(MediaTitle.year.nulls_last(), SORT_NAME, MediaTitle.id)
        elif title.type in ("series", "season"):
            query = select(MediaTitle).where(MediaTitle.parent_id == title.id).order_by(func.coalesce(MediaTitle.index_number, UNNUMBERED), SORT_NAME, MediaTitle.id)
        else:
            return []
        return list(self.db.scalars(query.where(self.visible(user))))

    def episodes(self, user: User, series: MediaTitle, season_number: int | None = None) -> list[MediaTitle]:
        season = aliased(MediaTitle)
        query = (
            select(MediaTitle).join(season, season.id == MediaTitle.parent_id)
            .where(MediaTitle.type == "episode", season.parent_id == series.id, self.visible(user))
            .order_by(func.coalesce(season.index_number, UNNUMBERED), func.coalesce(MediaTitle.index_number, UNNUMBERED), SORT_NAME, MediaTitle.id)
        )
        if season_number is not None:
            query = query.where(season.index_number == season_number)
        return list(self.db.scalars(query))

    def extras(self, user: User, title: MediaTitle) -> list[LibraryItem]:
        return list(self.db.scalars(
            select(LibraryItem).options(defer(LibraryItem.metadata_json))
            .where(LibraryItem.title_id == title.id, LibraryItem.extra_type.is_not(None),
                   LibraryItem.status != "missing", LibraryService.visible_predicate(user))
            .order_by(LibraryItem.extra_type, LibraryItem.title, LibraryItem.id)
        ))

    def series_progress(self, user: User, series_ids: list[str]) -> dict[str, list[EpisodeProgress]]:
        """Each series' visible episodes in (season, episode) order with the member's latest progress: one query."""
        if not series_ids:
            return {}
        season, episode = aliased(MediaTitle), aliased(MediaTitle)
        rows = self.db.execute(
            select(
                season.parent_id, episode.id, season.index_number, episode.index_number,
                latest_progress(user, PlaybackProgress.last_watched_at, episode),
                latest_progress(user, PlaybackProgress.completed, episode),
                latest_progress(user, PlaybackProgress.position_seconds, episode),
            )
            .select_from(episode).join(season, season.id == episode.parent_id)
            .where(episode.type == "episode", season.parent_id.in_(series_ids), playable_exists(user, episode))
            .order_by(season.parent_id, func.coalesce(season.index_number, UNNUMBERED), func.coalesce(episode.index_number, UNNUMBERED), episode.id)
        ).all()
        result: dict[str, list[EpisodeProgress]] = {}
        for series_id, episode_id, season_no, episode_no, at, done, position in rows:
            result.setdefault(series_id, []).append(EpisodeProgress(
                episode_id, UNNUMBERED if season_no is None else season_no, UNNUMBERED if episode_no is None else episode_no,
                at, bool(done), position or 0,
            ))
        return result

    def play_next(self, user: User, series: MediaTitle) -> str | None:
        """Series CTA: the in-progress episode, else Next Up, else the first regular episode."""
        episodes = self.series_progress(user, [series.id]).get(series.id, [])
        anchor = resume_anchor(episodes)
        if anchor is not None and not anchor.completed:
            return anchor.title_id
        pick = next_up(episodes) or next((episode for episode in episodes if episode.season != 0), None)
        return pick.title_id if pick else None

    def up_next(self, user: User, item: LibraryItem) -> LibraryUpNext:
        """The watch page's Up next for a visible item (spec: watch Up next): an episode's following episodes in order,
        the rest of its season then later seasons, Specials only from within Specials, at most UP_NEXT_LIMIT; a movie's
        collection in collection order with the current movie kept in place; anything else none. Visibility throughout."""
        title = self.get_visible(item.title_id, user)
        if title is None:
            return LibraryUpNext()
        if title.type == "movie":
            boxset = self.get_visible(title.boxset_id, user)
            films = self.children(user, boxset) if boxset else []
            if len(films) < 2:
                return LibraryUpNext()
            return LibraryUpNext(kind="collection", title=boxset.name, title_id=boxset.id, current_id=title.id, items=self.summaries(user, films))
        season = self.db.get(MediaTitle, title.parent_id) if title.type == "episode" and title.parent_id else None
        series = self.db.get(MediaTitle, season.parent_id) if season and season.parent_id else None
        if series is None:
            return LibraryUpNext()
        episodes = self.episodes(user, series)
        # Specials (season 0) sort first, so only a special is ever followed by them.
        at = next((index for index, episode in enumerate(episodes) if episode.id == title.id), len(episodes))
        following = episodes[at + 1:at + 1 + UP_NEXT_LIMIT]
        batch = self.load(user, following)
        label = version_label(item)
        items = []
        for episode in following:
            summary = self.summary(episode, batch)
            # The version family already playing (a 4K run stays 4K) unless the member already started another version.
            same = next((v for v in batch.versions.get(episode.id, []) if label and version_label(v) == label), None)
            if same is not None and batch.latest_progress(episode.id) is None:
                summary.play_item_id = same.id
            items.append(summary)
        return LibraryUpNext(kind="episodes", title=series.name, title_id=series.id, current_id=title.id, items=items)

    # ---- serialization ---------------------------------------------------------

    def summaries(self, user: User, titles: Sequence[MediaTitle], batch: TitleBatch | None = None) -> list[TitleSummary]:
        batch = batch or self.load(user, titles)
        return [self.summary(title, batch) for title in titles]

    @staticmethod
    def summary(title: MediaTitle, batch: TitleBatch) -> TitleSummary:
        season, series = batch.ancestors(title)
        artist = batch.titles.get(title.parent_id) if title.type == "album" else None
        meta = title.metadata_json or {}
        preferred = batch.preferred_version(title.id)
        runtime = round(batch.runtime(title) or 0) or None  # whole seconds
        return TitleSummary(
            id=title.id, type=title.type, category=title.category, name=title.name, sort_name=title.sort_name, year=title.year,
            index_number=title.index_number, index_number_end=title.index_number_end, parent_id=title.parent_id,
            series_id=series.id if series else None, series_name=series.name if series else None,
            season_number=season.index_number if season else (title.index_number if title.type == "season" else None),
            overview=meta.get("overview"), genres=list(meta.get("genres") or []), official_rating=meta.get("official_rating"),
            community_rating=meta.get("community_rating"), runtime_seconds=runtime,
            poster_url=image_url(title, "Primary"), backdrop_url=image_url(title, "Backdrop"),
            play_item_id=preferred.id if preferred else None, added_at=title.arrived_at, user_data=batch.user_data(title),
            poster=batch.art(title, "Primary", preview=True), backdrop=batch.art(title, "Backdrop", preview=False),
            artist_name=artist.name if artist else None,
            child_count=batch.folder_counts.get(title.id, (0, 0, 0))[0] if title.type in MUSIC_TITLE_TYPES else None,
        )

    def detail(self, user: User, title: MediaTitle) -> TitleDetail:
        if title.type in MUSIC_TITLE_TYPES:
            return self.music_detail(user, title)
        children = self.children(user, title)
        if title.type == "series":
            children.sort(key=lambda child: child.index_number == 0)  # Specials last (stable)
        boxset = self.get_visible(title.boxset_id, user)
        play_next = self.get_visible(self.play_next(user, title), user) if title.type == "series" else None
        extras = self.extras(user, title)
        batch = self.load(user, [title, *children, *(t for t in (boxset, play_next) if t is not None)], with_artifacts=True)
        meta = title.metadata_json or {}
        library = LibraryService(self.db)
        library.prime_media_states(batch.versions.get(title.id, []))
        extra_artifacts = self.artifacts_for([item.id for item in extras])
        people = title_people(self.db, title)  # Shares the metadata builder so cast get image_url, not just the local copy
        match = meta.get("match") if user.role == "admin" else None
        return TitleDetail(
            **self.summary(title, batch).model_dump(exclude={"backdrop"}),
            backdrop=batch.art(title, "Backdrop", preview=True), logo=batch.art(title, "Logo", preview=False),
            episode_count=batch.folder_counts.get(title.id, (0, 0, 0))[1] if title.type in ("series", "season") else None,
            best_height=self.best_height(user, title),
            tagline=meta.get("tagline"), studios=list(meta.get("studios") or []), premiered=meta.get("premiered"),
            end_date=meta.get("end_date"), status=meta.get("status"), logo_url=image_url(title, "Logo"),
            provider_ids=dict(title.provider_ids or {}), people=people,
            versions=[self._version(item, batch.artifacts.get(item.id), library, round(batch.duration(item) or 0) or None) for item in batch.versions.get(title.id, [])],
            extras=[
                TitleExtra(item_id=item.id, extra_type=item.extra_type, name=item.title,
                           duration_seconds=item.duration or _probe_seconds(extra_artifacts.get(item.id)),
                           artwork_url=f"/api/library/{item.id}/artwork")
                for item in extras
            ],
            children=[self.summary(child, batch) for child in children],
            boxset=self.summary(boxset, batch) if boxset else None,
            aired_episode_count=meta.get("aired_episode_count"),
            play_next=self.summary(play_next, batch) if play_next else None,
            match=TitleMatch.model_validate(match) if isinstance(match, dict) else None,
        )

    def best_height(self, user: User, title: MediaTitle) -> int | None:
        """Tallest cached probe height across the visible versions of a movie or episode, or of every episode below a
        series or season: one aggregate that seeks items by title id. None for boxsets and unprobed files."""
        if title.type in LEAF_TYPES:
            leaves = [title.id]
        elif title.type == "season":
            leaves = select(MediaTitle.id).where(MediaTitle.parent_id == title.id, MediaTitle.type == "episode")
        elif title.type == "series":
            season = aliased(MediaTitle)
            leaves = (
                select(MediaTitle.id).join(season, season.id == MediaTitle.parent_id)
                .where(season.parent_id == title.id, MediaTitle.type == "episode")
            )
        else:
            return None
        return self.db.scalar(
            select(func.max(cast(func.json_extract(MediaArtifact.probe, "$.height"), Integer))).select_from(LibraryItem)
            .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .where(LibraryItem.title_id.in_(leaves), LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
                   LibraryService.visible_predicate(user))
        )

    @staticmethod
    def _version(item: LibraryItem, artifact: MediaArtifact | None, library: LibraryService, duration: int | None) -> TitleVersion:
        probe = (artifact.probe if artifact else None) or {}
        video = next((s for s in probe.get("streams") or [] if isinstance(s, dict) and s.get("type") == "video"), {})
        return TitleVersion(
            item_id=item.id, label=version_label(item),
            container=(Path(artifact.relative_path).suffix.lstrip(".").lower() or None) if artifact else None,
            video_codec=probe.get("video_codec"), audio_codec=probe.get("audio_codec"),
            width=probe.get("width"), height=probe.get("height"), hdr=video.get("color_transfer") in HDR_TRANSFERS,
            file_size=item.file_size, duration_seconds=duration,
            media_state=library.serialize(item, summary=True).media_state,
        )

    # ---- Next Up and member state ---------------------------------------------

    def next_up_titles(
        self, user: User, *, series_id: str | None = None, limit: int | None = None,
        pick: Callable[[Sequence[EpisodeProgress], EpisodeProgress | None], EpisodeProgress | None] | None = None,
    ) -> list[MediaTitle]:
        """Next Up across the member's series, most recently watched series first: 4 queries.

        Walks every watched series' episode index in Python; move to SQL window functions if slow.
        """
        season, episode = aliased(MediaTitle), aliased(MediaTitle)
        last_watched = func.max(PlaybackProgress.last_watched_at)
        watched = (
            select(season.parent_id).select_from(PlaybackProgress)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(episode, episode.id == LibraryItem.title_id)
            .join(season, season.id == episode.parent_id)
            .where(PlaybackProgress.user_id == user.id, episode.type == "episode")
            .group_by(season.parent_id).order_by(last_watched.desc())
        )
        if series_id is not None:
            watched = watched.where(season.parent_id == series_id)
        order = list(self.db.scalars(watched))
        progress = self.series_progress(user, order)  # visible episodes only
        dismissed = self._dismissed_anchors(user, order)
        picks = []
        for series in order:
            ordered = progress.get(series, [])
            candidate = next_up(ordered, dismissed=series in dismissed)  # keep the dismissed= argument here
            if pick is not None:
                candidate = pick(ordered, candidate)
            if candidate is not None:
                picks.append(candidate.title_id)
        picks = picks[:limit]
        if not picks:
            return []
        titles = {title.id: title for title in self.db.scalars(select(MediaTitle).where(MediaTitle.id.in_(picks)))}
        return [titles[title_id] for title_id in picks if title_id in titles]

    def _dismissed_anchors(self, user: User, series_ids: list[str]) -> set[str]:
        """Series whose anchor row (the same row ``dismiss_next_up`` marks) the member dismissed: one query."""
        if not series_ids:
            return set()
        season, episode = aliased(MediaTitle), aliased(MediaTitle)
        rows = self.db.execute(
            select(season.parent_id, PlaybackProgress.dismissed_at).select_from(PlaybackProgress)
            .join(LibraryItem, LibraryItem.id == PlaybackProgress.item_id)
            .join(episode, episode.id == LibraryItem.title_id)
            .join(season, season.id == episode.parent_id)
            .where(
                PlaybackProgress.user_id == user.id, season.parent_id.in_(series_ids),
                func.coalesce(season.index_number, 1) != 0,
                or_(PlaybackProgress.completed.is_(True), PlaybackProgress.position_seconds > 0),
                LibraryService.visible_predicate(user),
            )
            .order_by(season.parent_id, PlaybackProgress.last_watched_at.desc(), PlaybackProgress.id.desc())
        ).all()
        anchors: dict[str, object] = {}
        for series_id, dismissed_at in rows:
            anchors.setdefault(series_id, dismissed_at)  # the first row per series is its newest checkpoint
        return {series_id for series_id, dismissed_at in anchors.items() if dismissed_at is not None}

    def leaf_titles(self, user: User, title: MediaTitle) -> list[MediaTitle]:
        """The visible movies/episodes a title stands for (itself, or everything below it)."""
        if title.type in LEAF_TYPES:
            return [title]
        if title.type == "series":
            return self.episodes(user, title)
        return self.children(user, title)  # season -> episodes, boxset -> movies

    def set_watched(self, user: User, title: MediaTitle, watched: bool) -> None:
        """Played: the preferred version of each leaf completes at position 0. Unplayed: every version's progress is cleared."""
        leaves = self.leaf_titles(user, title)
        batch = self.load(user, leaves)
        playback = PlaybackProgressService(self.db)
        for leaf in leaves:
            if watched:
                version = batch.preferred_version(leaf.id)
                if version is not None:
                    playback.update(version.id, PlaybackProgressUpdateRequest(position_seconds=0, completed=True), user)
            else:
                for item in batch.versions.get(leaf.id, []):
                    if item.id in batch.progress:
                        playback.clear(item.id, user)

    def set_favorite(self, user: User, target_id: str, favorite: bool) -> None:
        row = self.db.get(MemberFavorite, (user.id, target_id))
        if favorite and row is None:
            self.db.add(MemberFavorite(user_id=user.id, target_id=target_id))
        elif not favorite and row is not None:
            self.db.delete(row)
        self.db.flush()

    def tracks(self, user: User, album: MediaTitle, album_artist: str | None) -> list[AlbumTrack]:
        """An album's visible, non-missing tracks with the member's progress, ordered (disc, number, name, id) with
        unknown discs and numbers last: one query that seeks items by title id."""
        progress = aliased(PlaybackProgress)
        rows = self.db.execute(
            select(LibraryItem, progress, TRACK_DISC, TRACK_NUMBER, TRACK_ARTIST, cast(func.json_extract(MediaArtifact.probe, "$.duration"), Float))
            .options(defer(LibraryItem.metadata_json))
            .outerjoin(progress, and_(progress.item_id == LibraryItem.id, progress.user_id == user.id))
            .outerjoin(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .where(LibraryItem.title_id == album.id, LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
                   LibraryService.visible_predicate(user))
            .order_by(TRACK_DISC.nulls_last(), TRACK_NUMBER.nulls_last(), LibraryItem.title.collate("NOCASE"), LibraryItem.id)
        ).all()
        same = (album_artist or "").casefold()
        return [
            AlbumTrack(
                item_id=item.id, disc=disc, number=number, name=item.title,
                artist=artist if isinstance(artist, str) and artist.casefold() != same else None,
                duration_seconds=round(item.duration or probe or 0) or None,
                user_data=user_data_from(row, favorite=False),
            )
            for item, row, disc, number, artist, probe in rows
        ]

    def music_detail(self, user: User, title: MediaTitle) -> TitleDetail:
        """An album with its tracks, or an artist with its albums. Music titles have
        no versions, extras, people, boxset or match. With the route's title lookup: album 6 SELECTs, artist 5."""
        albums = self.children(user, title)  # an artist's; [] for an album (no query)
        batch = self.load(user, [title, *albums])
        artist = batch.titles.get(title.parent_id) if title.type == "album" else None
        return TitleDetail(
            **self.summary(title, batch).model_dump(exclude={"backdrop"}),
            backdrop=batch.art(title, "Backdrop", preview=True),
            children=[self.summary(album, batch) for album in albums],
            tracks=self.tracks(user, title, artist.name if artist else None) if title.type == "album" else None,
        )


def preferred_versions(db: Session, user: User, title_ids: Iterable[str]) -> dict[str, LibraryItem]:
    """Each movie/episode's preferred version (TitleBatch.preferred_version), set-based. Shared with discovery."""
    titles = list(db.scalars(select(MediaTitle).where(MediaTitle.id.in_(list(title_ids)), MediaTitle.type.in_(LEAF_TYPES))))
    batch = TitleService(db).load(user, titles)
    return {title.id: version for title in titles if (version := batch.preferred_version(title.id)) is not None}


def title_image_bytes(db: Session, title: MediaTitle, image_type: str, artwork: ArtworkService) -> tuple[str, bytes]:
    """(content type, bytes) of a title's stored art; FileNotFoundError when there is none.

    Stored forms:
    ``{"upload": sha}`` reads the title_uploads row; ``{"removed": True}`` has no art; local ``{"path": root-relative, "tag": …}``,
    ``{"embedded": root-relative track, "tag": …}`` (an album cover inside a track: confined to the root, never read
    through a symlink, JPEG or PNG within MAX_IMAGE_BYTES) and ``{"tmdb": "/x.jpg"}`` (fetched lazily through
    ``tmdb.load_image``). The path comes only from the scanner's stored map, never from the request.
    """
    entry = (title.images or {}).get(image_type)
    if isinstance(entry, dict) and entry.get("removed"):
        raise FileNotFoundError("Removed")
    if isinstance(entry, dict) and isinstance(entry.get("upload"), str) and not entry.get("path"):
        row = db.execute(select(TitleUpload.content_type, TitleUpload.data).where(TitleUpload.sha256 == entry["upload"])).first()
        if row is None:
            raise FileNotFoundError("Upload is missing")
        return row.content_type, bytes(row.data)
    if isinstance(entry, dict) and isinstance(entry.get("tmdb"), str) and not entry.get("path"):
        try:
            resolved = tmdb.load_image(artwork, entry["tmdb"], image_type.partition(".")[0])
        except ArtworkError as exc:
            raise FileNotFoundError("TMDB art is unavailable") from exc
        return resolved.content_type, resolved.content
    relative = entry.get("path") if isinstance(entry, dict) else None
    embedded = entry.get("embedded") if isinstance(entry, dict) and relative is None else None
    root = db.get(StorageRoot, title.root_id) if title.root_id else None
    if not isinstance(relative if relative is not None else embedded, str) or root is None or not root.enabled:
        raise FileNotFoundError("No local art")
    if embedded is not None:
        return embedded_picture(artifact_file(root.path, embedded), MAX_IMAGE_BYTES)
    path = artifact_file(root.path, relative)
    content_type = dict(LOCAL_ARTWORK_TYPES).get(path.suffix.lower())
    if content_type is None:
        raise FileNotFoundError("Unsupported art type")
    try:
        return content_type, read_regular_file(path, MAX_IMAGE_BYTES)
    except OSError as exc:
        raise FileNotFoundError("Art is unreadable") from exc
