"""Member-owned playlists and bounded, visibility-filtered smart music mixes."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, exists, false, func, or_, select, true
from sqlalchemy.orm import Session

from app.models import LibraryItem, MediaTitle, MemberFavorite, MusicPlaylist, MusicTrackOverride, PlaybackProgress, User
from app.music_schemas import MusicMixItem, MusicMixPreview, MusicMixReason, MusicMixSeed, MusicPlaylistRules, MusicPlaylistSave
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


def _genre_expression():
    overridden = func.json_type(MusicTrackOverride.fields, "$.genres").is_not(None)
    item_genres = func.json_extract(LibraryItem.metadata_json, "$.genres")
    return case(
        (overridden, func.json_extract(MusicTrackOverride.fields, "$.genres")),
        (func.coalesce(func.json_array_length(item_genres), 0) > 0, item_genres),
        else_=func.coalesce(func.json_extract(MediaTitle.metadata_json, "$.genres"), "[]"),
    )


def _visible_music_query(user: User):
    return select(LibraryItem).join(MediaTitle, MediaTitle.id == LibraryItem.title_id).outerjoin(
        MusicTrackOverride, MusicTrackOverride.item_id == LibraryItem.id,
    ).where(
        LibraryItem.kind.in_(("track", "audio")),
        LibraryItem.status != "missing",
        LibraryService.visible_predicate(user),
    )


def _visible_genres(db: Session, user: User, predicate) -> list[str]:  # noqa: ANN001
    genres = _genre_expression()
    values = func.json_each(genres).table_valued("key", "value")
    rows = db.execute(
        select(values.c.value)
        .select_from(LibraryItem)
        .join(MediaTitle, MediaTitle.id == LibraryItem.title_id)
        .outerjoin(MusicTrackOverride, MusicTrackOverride.item_id == LibraryItem.id)
        .join(values, true())
        .where(
            predicate,
            values.c.key < 30,
            LibraryItem.kind.in_(("track", "audio")),
            LibraryItem.status != "missing",
            LibraryService.visible_predicate(user),
        )
        .distinct()
        .order_by(func.lower(values.c.value), values.c.value)
        .limit(30)
    )
    return [value[:64] for value in rows.scalars() if isinstance(value, str) and value][:30]


def _genres_for_artist(db: Session, user: User, artist_id: str) -> list[str]:
    return _visible_genres(db, user, MediaTitle.parent_id == artist_id)


def _mix_reason(track, artist_id: str | None, genres: list[str], *, seed_track: bool = False) -> MusicMixReason:  # noqa: ANN001
    if seed_track:
        return MusicMixReason(code="seed_track", item_id=track.id)
    if artist_id and track.artist_id == artist_id:
        return MusicMixReason(code="same_artist", artist_id=artist_id)
    wanted = {genre.casefold() for genre in genres}
    genre = next((value for value in track.genres if value.casefold() in wanted), None)
    if genre:
        return MusicMixReason(code="shared_genre", genre=genre)
    raise AssertionError("contextual mix candidate did not match its seed")


def _ranked_candidates(
    db: Session,
    user: User,
    *,
    artist_id: str | None,
    genres: list[str],
    excluded_ids: set[str],
    limit: int,
) -> list[LibraryItem]:
    genre_values = [genre.casefold() for genre in genres]
    genre_expression = _genre_expression()
    values = func.json_each(genre_expression).table_valued("key", "value")
    genre_match = exists(select(1).select_from(values).where(
        values.c.key < 30,
        func.lower(func.substr(values.c.value, 1, 64)).in_(genre_values),
    )) if genre_values else false()
    artist_match = MediaTitle.parent_id == artist_id if artist_id else false()
    return list(db.scalars(
        _visible_music_query(user)
        .where(or_(artist_match, genre_match), LibraryItem.id.not_in(excluded_ids) if excluded_ids else true())
        .order_by(
            case((artist_match, 1), else_=0).desc(),
            case((genre_match, 1), else_=0).desc(),
            LibraryItem.id,
        )
        .limit(limit)
    ))


def _preview_items(tracks, artist_id: str | None, genres: list[str], *, seed_tracks: bool = False) -> list[MusicMixItem]:  # noqa: ANN001
    return [
        MusicMixItem(**track.model_dump(), reason=_mix_reason(track, artist_id, genres, seed_track=seed_tracks))
        for track in tracks
    ]


def contextual_mix(
    db: Session,
    user: User,
    *,
    seed_kind: str,
    seed_id: str,
    exclude_item_ids: list[str],
) -> MusicMixPreview:
    """Build a bounded, read-only preview. ``exhausted`` means every eligible candidate fit in ``items``."""
    excluded_ids = set(exclude_item_ids)
    if len(excluded_ids) > 100:
        raise HTTPException(422, "Exclude up to 100 unique tracks")
    if seed_kind == "track":
        seed_track = music_library.manifests(db, user, music_library.visible_tracks(db, user, [seed_id]))[0]
        artist_id, genres = seed_track.artist_id, seed_track.genres
        seed = MusicMixSeed(id=seed_track.id, kind="track", name=seed_track.title, artist_id=artist_id, genres=genres)
        excluded_ids.add(seed_track.id)
        candidates = _ranked_candidates(db, user, artist_id=artist_id, genres=genres, excluded_ids=excluded_ids, limit=101)
        tracks = music_library.manifests(db, user, candidates)
        items = _preview_items(tracks[:100], artist_id, genres)
        return MusicMixPreview(seed=seed, items=items, exhausted=len(tracks) <= 100)

    if seed_kind not in ("artist", "album"):
        raise HTTPException(422, "Choose a track, artist or album")
    title = db.scalar(select(MediaTitle).where(
        MediaTitle.id == seed_id,
        MediaTitle.type == seed_kind,
        LibraryService.visible_title_predicate(user),
    ))
    if title is None:
        raise HTTPException(404, "Music seed not found")

    if seed_kind == "artist":
        genres = _genres_for_artist(db, user, title.id)
        seed = MusicMixSeed(id=title.id, kind="artist", name=title.name, artist_id=title.id, genres=genres)
        candidates = _ranked_candidates(db, user, artist_id=title.id, genres=genres, excluded_ids=excluded_ids, limit=101)
        tracks = music_library.manifests(db, user, candidates)
        items = _preview_items(tracks[:100], title.id, genres)
        return MusicMixPreview(seed=seed, items=items, exhausted=len(tracks) <= 100)

    title_genres = [genre for genre in (title.metadata_json or {}).get("genres", []) if isinstance(genre, str)]
    genres = list(dict.fromkeys([*title_genres, *_visible_genres(db, user, MediaTitle.id == title.id)]))[:30]
    seed = MusicMixSeed(id=title.id, kind="album", name=title.name, artist_id=title.parent_id, genres=genres)
    def numbered(name: str, default: int):
        raw = case(
            (func.json_type(MusicTrackOverride.fields, f"$.{name}").is_not(None), func.json_extract(MusicTrackOverride.fields, f"$.{name}")),
            else_=func.json_extract(LibraryItem.metadata_json, f"$.{name}"),
        )
        value = cast(raw, Integer)
        return case((and_(value > 0, value < 1_000_000), value), else_=default)

    disc_number = numbered("disc_number", 1)
    track_number = numbered("track_number", 1_000_000)
    album_items = list(db.scalars(
        _visible_music_query(user)
        .where(MediaTitle.id == title.id, LibraryItem.id.not_in(excluded_ids) if excluded_ids else true())
        .order_by(disc_number, track_number, LibraryItem.title, LibraryItem.id)
        .limit(101)
    ))
    album_tracks = music_library.manifests(db, user, album_items)
    album_tracks.sort(key=lambda track: (track.disc or 1, track.number or 1_000_000, track.title.casefold(), track.id))
    if len(album_tracks) >= 101:
        items = _preview_items(album_tracks[:100], title.parent_id, genres, seed_tracks=True)
        return MusicMixPreview(seed=seed, items=items, exhausted=False)
    remaining = 101 - len(album_tracks)
    outside = _ranked_candidates(
        db, user, artist_id=title.parent_id, genres=genres,
        excluded_ids=excluded_ids | {track.id for track in album_tracks}, limit=remaining,
    )
    tracks = music_library.manifests(db, user, outside)
    items = [
        *_preview_items(album_tracks, title.parent_id, genres, seed_tracks=True),
        *_preview_items(tracks[:100 - len(album_tracks)], title.parent_id, genres),
    ]
    return MusicMixPreview(seed=seed, items=items, exhausted=len(album_tracks) + len(tracks) <= 100)
