"""Member-owned playlists and bounded, visibility-filtered smart music mixes."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select, true
from sqlalchemy.orm import Session

from app.models import LibraryItem, MediaTitle, MemberFavorite, MusicPlaylist, MusicTrackOverride, PlaybackProgress, User
from app.music_schemas import MusicPlaylistRules, MusicPlaylistSave
from app.persistence import write_transaction
from app.services import music_library
from app.services.library import LibraryService


def visible(db: Session, user: User, playlist_id: str) -> MusicPlaylist:
    row = db.scalar(select(MusicPlaylist).where(MusicPlaylist.id == playlist_id, or_(MusicPlaylist.user_id == user.id, MusicPlaylist.shared)))
    if row is None:
        raise HTTPException(404, "Playlist not found")
    return row


def document(row: MusicPlaylist, user: User) -> dict:
    return {"id": row.id, "name": row.name, "shared": row.shared, "rules": row.rules, "revision": row.revision,
            "count": len(row.item_ids or []) if row.rules is None else None, "can_edit": row.user_id == user.id}


def list_playlists(db: Session, user: User) -> list[dict]:
    return [document(row, user) for row in db.scalars(select(MusicPlaylist).where(or_(MusicPlaylist.user_id == user.id, MusicPlaylist.shared))
                                                      .order_by(MusicPlaylist.name, MusicPlaylist.id).limit(500))]


def save(db: Session, user: User, body: MusicPlaylistSave, playlist_id: str | None = None) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Give the playlist a name")
    if body.rules and body.item_ids:
        raise HTTPException(422, "Choose playlist tracks or smart rules")
    if body.rules and body.rules.year_from and body.rules.year_to and body.rules.year_from > body.rules.year_to:
        raise HTTPException(422, "The end year must follow the start year")
    ids = list(dict.fromkeys(body.item_ids))
    if ids:
        music_library.visible_tracks(db, user, ids)
    with write_transaction(db, name="music_playlist_save"):
        if playlist_id:
            row = visible(db, user, playlist_id)
            if row.user_id != user.id:
                raise HTTPException(403, "Only the playlist owner can edit it")
            if body.revision is None or body.revision != row.revision:
                raise HTTPException(409, "Playlist changed. Reopen it before saving.")
        else:
            if db.scalar(select(func.count()).select_from(MusicPlaylist).where(MusicPlaylist.user_id == user.id)) >= 100:
                raise HTTPException(422, "You can keep up to 100 playlists")
            row = MusicPlaylist(id=str(uuid.uuid4()), user_id=user.id, revision=0)
            db.add(row)
        row.name, row.shared, row.item_ids, row.rules = name, body.shared, ids, body.rules.model_dump() if body.rules else None
        row.revision += 1
    return document(row, user)


def remove(db: Session, user: User, playlist_id: str) -> None:
    with write_transaction(db, name="music_playlist_delete"):
        row = visible(db, user, playlist_id)
        if row.user_id != user.id:
            raise HTTPException(403, "Only the playlist owner can delete it")
        db.delete(row)


def smart_query(user: User, rules: MusicPlaylistRules):
    query = select(LibraryItem).outerjoin(MediaTitle, MediaTitle.id == LibraryItem.title_id).outerjoin(
        MusicTrackOverride, MusicTrackOverride.item_id == LibraryItem.id).outerjoin(
        PlaybackProgress, and_(PlaybackProgress.user_id == user.id, PlaybackProgress.item_id == LibraryItem.id)).where(
        LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
    if rules.artist_id:
        query = query.where(MediaTitle.parent_id == rules.artist_id)
    if rules.year_from:
        query = query.where(MediaTitle.year >= rules.year_from)
    if rules.year_to:
        query = query.where(MediaTitle.year <= rules.year_to)
    if rules.favorites:
        query = query.where(exists(select(MemberFavorite.user_id).where(MemberFavorite.user_id == user.id,
            MemberFavorite.target_id.in_((LibraryItem.id, MediaTitle.id, MediaTitle.parent_id)))))
    if rules.unplayed:
        query = query.where(func.coalesce(PlaybackProgress.plays, 0) == 0)
    if rules.genres:
        # Explicit [] is a clear; coalesce falls through only when an override is absent.
        genres = func.coalesce(func.json_extract(MusicTrackOverride.fields, "$.genres"),
                               func.json_extract(LibraryItem.metadata_json, "$.genres"), func.json_extract(MediaTitle.metadata_json, "$.genres"), "[]")
        values = func.json_each(genres).table_valued("value")
        query = query.where(exists(select(1).select_from(values).where(func.lower(values.c.value).in_([g.casefold() for g in rules.genres]))))
    return query


def tracks(db: Session, user: User, playlist_id: str) -> list:
    row = visible(db, user, playlist_id)
    if row.rules is not None:
        items = list(db.scalars(smart_query(user, MusicPlaylistRules.model_validate(row.rules)).order_by(MediaTitle.year.desc(), LibraryItem.title, LibraryItem.id).limit(5000)))
    else:
        # Shared playlists never reveal now-private, removed or missing entries.
        items_by_id = {i.id: i for i in db.scalars(select(LibraryItem).where(LibraryItem.id.in_(row.item_ids or []),
                       LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing", LibraryService.visible_predicate(user)))}
        items = [items_by_id[i] for i in (row.item_ids or []) if i in items_by_id]
    return music_library.manifests(db, user, items)


def mix(db: Session, user: User, kind: str, seed_id: str | None = None) -> list:
    rules = MusicPlaylistRules(favorites=kind == "favorites", unplayed=kind == "unplayed")
    if kind == "radio":
        if not seed_id:
            raise HTTPException(422, "Choose a track to start radio")
        seed = music_library.manifests(db, user, music_library.visible_tracks(db, user, [seed_id]))[0]
        rules = MusicPlaylistRules(genres=seed.genres[:20], artist_id=seed.artist_id if not seed.genres else None)
    if kind == "rotation":
        query = smart_query(user, rules).order_by(func.coalesce(PlaybackProgress.plays, 0).desc(), func.coalesce(PlaybackProgress.last_watched_at, LibraryItem.created_at).desc(), LibraryItem.id)
    else:
        query = smart_query(user, rules).order_by(func.random())
    items = list(db.scalars(query.limit(100)))
    return music_library.manifests(db, user, items)
