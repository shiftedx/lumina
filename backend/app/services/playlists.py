"""Watchlist and household collections as Jellyfin Playlists.

Service functions only. The Jellyfin router (integration) maps ids with media_titles.jellyfin_id /
parse_item_id and errors to HTTP: PlaylistNotFound 404, PlaylistForbidden 403, PlaylistBadRequest 400,
PlaylistConflict 409. The watch queue is a synthetic playlist named "Watchlist"; remote (non-vault)
entries are omitted everywhere, and every entry is visibility-filtered for the caller. Playlist creation
(`POST /Playlists`) is skipped until a client needs it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import SmartCollectionRule
from app.models import (
    HouseholdCollection,
    HouseholdCollectionMembership,
    LibraryItem,
    MediaTitle,
    User,
    WatchQueue,
    WatchQueueEntry,
)
from app.services import smart_collections
from app.services.household_collections import (
    CollectionConflictError,
    CollectionEntryNotFoundError,
    CollectionFullError,
    CollectionNotFoundError,
    DuplicateCollectionItemError,
    HouseholdCollectionService,
)
from app.services.library import LibraryService
from app.services.media_titles import synthetic_id
from app.services.titles import preferred_versions  # The one preferred-version rule
from app.services.watch_queue import WatchQueueConflict, WatchQueueFull, WatchQueueNotFound, WatchQueueService

WATCHLIST_NAME = "Watchlist"
PLAYABLE_TITLE_TYPES = ("movie", "episode")


class PlaylistNotFound(LookupError):
    pass


class PlaylistForbidden(PermissionError):
    pass


class PlaylistBadRequest(ValueError):
    pass


class PlaylistConflict(Exception):
    pass


@dataclass(frozen=True)
class PlaylistEntry:
    entry_id: str  # Jellyfin PlaylistItemId: the queue entry or membership id (smart collections: the item id)
    item_id: str  # the Library item (version) to play


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    kind: Literal["watchlist", "collection", "smart"]
    owner_user_id: str
    can_edit: bool
    entries: tuple[PlaylistEntry, ...]


def watchlist_id(user: User) -> str:
    return synthetic_id(f"watchqueue:{user.id}")


def has_playlists(db: Session, user: User) -> bool:
    """Whether the Jellyfin ``playlists`` user view appears: a vault entry in the queue or any visible collection."""
    queued = db.scalar(
        select(WatchQueueEntry.id).where(WatchQueueEntry.user_id == user.id, WatchQueueEntry.library_item_id.is_not(None)).limit(1)
    )
    return queued is not None or bool(_collections(db, user))


def _collections(db: Session, user: User) -> list[HouseholdCollection]:
    """The collections this member may see as playlists; a restricted member's all-hidden shared ones are left out."""
    service = HouseholdCollectionService(db)
    return service.shown_to(user, service.list_visible(user.id), remote=False)


def list_playlists(db: Session, user: User) -> list[Playlist]:
    watchlist = _watchlist(db, user)
    collections = [_collection_playlist(db, user, collection) for collection in _collections(db, user)]
    return ([watchlist] if watchlist.entries else []) + collections


def get_playlist(db: Session, user: User, playlist_id: str) -> Playlist:
    if playlist_id == watchlist_id(user):
        return _watchlist(db, user)
    try:
        collection = HouseholdCollectionService(db).get_visible(member_user_id=user.id, collection_id=playlist_id)
    except CollectionNotFoundError:
        raise PlaylistNotFound(playlist_id) from None
    return _collection_playlist(db, user, collection)


def add_to_playlist(db: Session, user: User, playlist_id: str, ids: list[str]) -> None:
    playlist = _editable(db, user, playlist_id)
    try:
        for item_id in _resolve_items(db, user, ids):
            if playlist.kind == "watchlist":
                WatchQueueService(db).add(user, library_item_id=item_id)
                continue
            try:
                HouseholdCollectionService(db).add_item(member_user_id=user.id, collection_id=playlist.id, library_item_id=item_id)
            except DuplicateCollectionItemError:
                continue
    except (WatchQueueFull, CollectionFullError):
        raise PlaylistBadRequest("The playlist is full.") from None


def remove_from_playlist(db: Session, user: User, playlist_id: str, entry_ids: list[str]) -> None:
    playlist = _editable(db, user, playlist_id)
    for entry_id in entry_ids:
        if playlist.kind == "watchlist":
            WatchQueueService(db).remove(user, entry_id)
        else:
            HouseholdCollectionService(db).remove_entry(member_user_id=user.id, collection_id=playlist.id, entry_id=entry_id)


def move_playlist_entry(
    db: Session, user: User, playlist_id: str, entry_id: str, new_index: int, *, expected_revision: int | None = None,
) -> None:
    """Move one entry; ``new_index`` counts only the entries this member sees (Jellyfin's view of the list)."""
    playlist = _editable(db, user, playlist_id)
    visible = [entry.entry_id for entry in playlist.entries]
    if entry_id not in visible:
        raise PlaylistNotFound(entry_id)
    target = visible[max(0, min(new_index, len(visible) - 1))]
    try:
        if playlist.kind == "watchlist":
            revision = expected_revision if expected_revision is not None else (
                db.scalar(select(WatchQueue.revision).where(WatchQueue.user_id == user.id)) or 0
            )
            position = db.scalar(select(WatchQueueEntry.position).where(WatchQueueEntry.id == target))
            WatchQueueService(db).move(user, entry_id, position, revision)
        else:
            revision = expected_revision if expected_revision is not None else db.get(HouseholdCollection, playlist.id).revision
            position = db.scalar(select(HouseholdCollectionMembership.position).where(HouseholdCollectionMembership.id == target))
            HouseholdCollectionService(db).move(
                member_user_id=user.id, collection_id=playlist.id, entry_id=entry_id, index=position, expected_revision=revision,
            )
    except (WatchQueueConflict, CollectionConflictError):
        raise PlaylistConflict(playlist_id) from None
    except (WatchQueueNotFound, CollectionEntryNotFoundError):
        raise PlaylistNotFound(entry_id) from None


def _watchlist(db: Session, user: User) -> Playlist:
    _revision, rows = WatchQueueService(db).read(user)
    entries = tuple(PlaylistEntry(entry.id, item.id) for entry, item in rows if item is not None and item.status != "missing")
    return Playlist(id=watchlist_id(user), name=WATCHLIST_NAME, kind="watchlist", owner_user_id=user.id, can_edit=True, entries=entries)


def _collection_playlist(db: Session, user: User, collection: HouseholdCollection) -> Playlist:
    if collection.rules is not None:
        rule = SmartCollectionRule.model_validate(collection.rules)
        rows = smart_collections.evaluate(db, user, rule)
        if rule.type == "channel_video":
            item_ids = [row.id for row in rows]
        else:  # series results cannot be playlist items and are omitted
            preferred = preferred_versions(db, user, [row.id for row in rows if row.type in PLAYABLE_TITLE_TYPES])
            item_ids = [preferred[row.id].id for row in rows if row.id in preferred]
        entries = tuple(PlaylistEntry(item_id, item_id) for item_id in item_ids)
        return Playlist(collection.id, collection.name, "smart", collection.owner_user_id, False, entries)
    _collection, memberships = HouseholdCollectionService(db).list_entries(member_user_id=user.id, collection_id=collection.id)
    ids = [membership.library_item_id for membership in memberships if membership.library_item_id]
    visible = set(db.scalars(
        select(LibraryItem.id).where(LibraryItem.id.in_(ids), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
    )) if ids else set()
    entries = tuple(PlaylistEntry(m.id, m.library_item_id) for m in memberships if m.library_item_id in visible)
    return Playlist(collection.id, collection.name, "collection", collection.owner_user_id, collection.owner_user_id == user.id, entries)


def _editable(db: Session, user: User, playlist_id: str) -> Playlist:
    playlist = get_playlist(db, user, playlist_id)
    if not playlist.can_edit:
        raise PlaylistForbidden(playlist_id)
    return playlist


def _resolve_items(db: Session, user: User, ids: list[str]) -> list[str]:
    """Item ids stay; Movie/Episode titles become their preferred version; anything else visible is a 400, invisible a 404."""
    wanted = [value for value in ids if value]
    items = set(db.scalars(
        select(LibraryItem.id).where(LibraryItem.id.in_(wanted), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
    )) if wanted else set()
    others = [value for value in wanted if value not in items]
    titles = {t.id: t for t in db.scalars(
        select(MediaTitle).where(MediaTitle.id.in_(others), LibraryService.visible_title_predicate(user))
    )} if others else {}
    playable = preferred_versions(db, user, [t.id for t in titles.values() if t.type in PLAYABLE_TITLE_TYPES])
    resolved = []
    for value in wanted:
        if value in items:
            resolved.append(value)
        elif value in titles and titles[value].type not in PLAYABLE_TITLE_TYPES:
            raise PlaylistBadRequest("Only movies, episodes and videos can be added to a playlist.")
        elif value in playable:
            resolved.append(playable[value].id)
        else:
            raise PlaylistNotFound(value)
    return resolved
