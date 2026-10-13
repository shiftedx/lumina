"""Visible music manifests, protected track edits and optimistic bulk undo."""
from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MusicEditBatch, MusicTrackOverride, User, utcnow
from app.music_schemas import MusicQueueRequest, MusicReleaseCreate, MusicTrack, MusicTrackEdit
from app.persistence import write_transaction
from app.services import library_search
from app.services.library import LibraryService
from app.services.media_probe import loudness_gain_db
from app.services.titles import TitleService, image_url

TRACK_FIELDS = frozenset({"title", "artist", "album_artist", "album_id", "track_number", "disc_number", "genres", "lyrics", "gain_db"})
MAX_EDIT_HISTORY_BYTES = 2 * 1024 * 1024


def visible_tracks(db: Session, user: User, ids: list[str]) -> list[LibraryItem]:
    unique = list(dict.fromkeys(ids))
    rows = list(db.scalars(select(LibraryItem).where(
        LibraryItem.id.in_(unique), LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing",
        LibraryService.visible_predicate(user),
    ).execution_options(populate_existing=True)))
    mapped = {row.id: row for row in rows}
    if len(mapped) != len(unique):
        raise HTTPException(404, "Track not found")
    return [mapped[item_id] for item_id in ids]


def track_fields(item: LibraryItem, override: MusicTrackOverride | None) -> dict[str, Any]:
    raw = item.metadata_json or {}
    fields = {
        "title": item.title, "artist": raw.get("artist") or item.uploader or "Unknown artist",
        "album_artist": raw.get("album_artist") or raw.get("artist") or item.uploader or "Unknown artist",
        "album_id": item.title_id, "track_number": raw.get("track_number"), "disc_number": raw.get("disc_number"),
        "genres": raw.get("genres") or [], "lyrics": raw.get("lyrics"), "gain_db": raw.get("replaygain_track_gain_db"),
        **({k: v for k, v in (override.fields or {}).items() if k in TRACK_FIELDS} if override else {}),
    }
    fields["track_number"], fields["disc_number"] = _integer(fields["track_number"]), _integer(fields["disc_number"])
    return fields


def _integer(value: Any) -> int | None:
    try:
        result = int(value) if not isinstance(value, bool) else 0
        return result if 0 < result < 1_000_000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _finite(value: Any, low: float, high: float) -> float | None:
    return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None


def manifests(
    db: Session,
    user: User,
    items: list[LibraryItem],
    *,
    overrides: Mapping[str, MusicTrackOverride] | None = None,
) -> list[MusicTrack]:
    if not items:
        return []
    overrides = overrides if overrides is not None else {
        row.item_id: row
        for row in db.scalars(select(MusicTrackOverride).where(MusicTrackOverride.item_id.in_([i.id for i in items])))
    }
    fields = {item.id: track_fields(item, overrides.get(item.id)) for item in items}
    album_ids = {entry["album_id"] for entry in fields.values() if entry["album_id"]}
    albums = list(db.scalars(select(MediaTitle).where(MediaTitle.id.in_(album_ids), MediaTitle.type == "album")))
    artists = list(db.scalars(select(MediaTitle).where(MediaTitle.id.in_({a.parent_id for a in albums if a.parent_id}), MediaTitle.type == "artist")))
    titles = {title.id: title for title in [*albums, *artists]}
    facts = {item_id: probe or {} for item_id, probe in db.execute(
        select(LibraryItemArtifact.library_item_id, MediaArtifact.probe)
        .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .where(LibraryItemArtifact.library_item_id.in_([i.id for i in items]))
    )}
    tracks = []
    for item in items:
        value, probe = fields[item.id], facts.get(item.id, {})
        album = titles.get(value["album_id"])
        artist = titles.get(album.parent_id) if album else None
        stream = next((s for s in probe.get("streams", []) if s.get("type") == "audio"), {})
        gain = value["gain_db"] if value["gain_db"] is not None else loudness_gain_db(probe.get("loudness"))
        genres = value["genres"] if isinstance(value["genres"], list) else []
        override = overrides.get(item.id)
        if not genres and album and not (override and "genres" in (override.fields or {})):
            genres = (album.metadata_json or {}).get("genres", [])
        if not isinstance(genres, list):
            genres = []
        album_artist = value["album_artist"]
        if override and "album_id" in (override.fields or {}) and "album_artist" not in (override.fields or {}) and artist:
            album_artist = artist.name
        tracks.append(MusicTrack(
            id=item.id, title=str(value["title"]), artist=str(value["artist"]), album_artist=str(album_artist),
            album=album.name if album else item.playlist_name or "Loose tracks", album_id=album.id if album else None,
            artist_id=artist.id if artist else None, number=_integer(value["track_number"]), disc=_integer(value["disc_number"]),
            duration=item.duration or probe.get("duration"), artwork_url=image_url(album, "Primary") if album else None,
            audio_url=f"/api/music/tracks/{item.id}/audio", codec=probe.get("audio_codec") or stream.get("codec"),
            sample_rate=_integer(stream.get("sample_rate")), bit_depth=_integer(stream.get("bits_per_raw_sample") or stream.get("bit_depth")),
            channels=_integer(stream.get("channels")), gain_db=gain if type(gain) in (int, float) and math.isfinite(gain) else None,
            genres=[g[:64] for g in genres if isinstance(g, str)][:30],
            edited=bool(overrides.get(item.id) and any(k in TRACK_FIELDS for k in overrides[item.id].fields)),
            peak=_finite((item.metadata_json or {}).get("replaygain_track_peak"), 0.001, 32),
            album_gain_db=_finite((item.metadata_json or {}).get("replaygain_album_gain_db"), -30, 20),
            album_peak=_finite((item.metadata_json or {}).get("replaygain_album_peak"), 0.001, 32),
            gain_is_manual=bool(override and "gain_db" in (override.fields or {})),
        ))
    return tracks


def effective_track_fields(
    db: Session,
    user: User,
    items: list[LibraryItem],
    overrides: Mapping[str, MusicTrackOverride] | None = None,
) -> dict[str, dict[str, Any]]:
    """Editor values exactly as the queue presents them, plus lyrics and edit field names."""
    loaded = overrides if overrides is not None else {
        row.item_id: row
        for row in db.scalars(select(MusicTrackOverride).where(MusicTrackOverride.item_id.in_([i.id for i in items])))
    }
    presented = {track.id: track for track in manifests(db, user, items, overrides=loaded)}
    result = {}
    for item in items:
        fields = track_fields(item, loaded.get(item.id))
        track = presented[item.id]
        fields.update(
            title=track.title,
            artist=track.artist,
            album_artist=track.album_artist,
            album_id=track.album_id,
            track_number=track.number,
            disc_number=track.disc,
            genres=track.genres,
            gain_db=track.gain_db,
        )
        result[item.id] = fields
    return result


def _add_edit_batch(db: Session, batch_id: str, user_id: str, history: list[dict]) -> None:
    size = len(json.dumps(history, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_EDIT_HISTORY_BYTES:
        raise HTTPException(422, "Music edit is too large to undo safely")
    db.add(MusicEditBatch(id=batch_id, user_id=user_id, changes=history))


def queue(db: Session, user: User, request: MusicQueueRequest) -> list[MusicTrack]:
    if request.item_ids and request.title_id:
        raise HTTPException(422, "Choose tracks or a title")
    if request.item_ids:
        items = visible_tracks(db, user, request.item_ids)
    elif request.title_id:
        title = TitleService(db).get_visible(request.title_id, user)
        if title is None or title.type not in ("album", "artist"):
            raise HTTPException(404, "Music title not found")
        query = select(LibraryItem).join(MediaTitle, LibraryItem.title_id == MediaTitle.id).where(
            LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing", LibraryService.visible_predicate(user),
            MediaTitle.id == title.id if title.type == "album" else MediaTitle.parent_id == title.id,
        ).order_by(MediaTitle.year.desc(), MediaTitle.id, LibraryItem.title)
        items = list(db.scalars(query.limit(5001)))
        if len(items) > 5000:
            raise HTTPException(422, "Choose albums or up to 5,000 tracks for one queue")
        tracks = manifests(db, user, items)
        album_order = {}
        for item in items:
            album_order.setdefault(item.title_id, len(album_order))
        tracks.sort(key=lambda t: (album_order.get(t.album_id, 0), t.disc or 1, t.number or 1000, t.title.casefold(), t.id))
        return tracks
    else:
        raise HTTPException(422, "Choose tracks or an album")
    return manifests(db, user, items)


def validate_changes(db: Session, user: User, changes: dict[str, Any]) -> dict[str, Any]:
    if not changes or set(changes) - TRACK_FIELDS:
        raise HTTPException(422, "Unknown music field")
    result = {}
    for key, value in changes.items():
        if value is None:
            result[key] = None
            continue
        valid = True
        if key in ("title", "artist", "album_artist", "lyrics"):
            valid = isinstance(value, str) and (0 if key == "lyrics" else 1) <= len(value.strip()) <= (100_000 if key == "lyrics" else 300)
            value = value.strip() if isinstance(value, str) else value
        elif key in ("track_number", "disc_number"):
            valid = type(value) is int and 1 <= value <= 999
        elif key == "genres":
            valid = isinstance(value, list) and len(value) <= 30 and all(isinstance(v, str) and 0 < len(v.strip()) <= 64 for v in value)
            if valid:
                value = list(dict.fromkeys(v.strip() for v in value))
        elif key == "gain_db":
            valid = type(value) in (int, float) and math.isfinite(value) and -30 <= value <= 20
        elif key == "album_id":
            title = TitleService(db).get_visible(value, user) if isinstance(value, str) else None
            valid = title is not None and title.type == "album"
        if not valid:
            raise HTTPException(422, f"Invalid {key}")
        result[key] = value
    return result


def edit_tracks(db: Session, user: User, request: MusicTrackEdit) -> dict:
    changes = validate_changes(db, user, request.changes)
    items = visible_tracks(db, user, list(dict.fromkeys(request.item_ids)))
    batch_id = str(uuid.uuid4())
    with write_transaction(db, name="music_edit"):
        items = visible_tracks(db, user, list(dict.fromkeys(request.item_ids)))
        overrides = {row.item_id: row for row in db.scalars(select(MusicTrackOverride).where(MusicTrackOverride.item_id.in_(request.item_ids)).execution_options(populate_existing=True))}
        presented = effective_track_fields(db, user, items, overrides)
        conflicts = []
        for item in items:
            effective = presented[item.id]
            base = request.base.get(item.id, {})
            if any(key in base and base[key] != effective[key] for key in changes):
                conflicts.append({"item_id": item.id, "current": effective})
        if conflicts:
            raise HTTPException(409, {"message": "Tracks changed while you were editing", "conflicts": conflicts})
        history = []
        for item in items:
            row = overrides.get(item.id)
            before = dict(row.fields or {}) if row else {}
            after = {**before, **changes}
            for key, value in changes.items():
                if value is None:
                    after.pop(key, None)
            if "album_id" in changes:
                after.setdefault("_source_album_id", before.get("_source_album_id", item.title_id))
                item.title_id = changes["album_id"] or after.get("_source_album_id")
            if after == before:
                continue
            if row is None:
                row = MusicTrackOverride(item_id=item.id)
                db.add(row)
                overrides[item.id] = row
            row.fields = after
            history.append({"item_id": item.id, "before": before, "after": after})
        if history:
            _add_edit_batch(db, batch_id, user.id, history)
            db.flush()
            changed_ids = {entry["item_id"] for entry in history}
            changed = [item for item in items if item.id in changed_ids]
            library_search.LibrarySearchService(db).sync_music_items(
                changed, {item.id: overrides[item.id].fields or {} for item in changed}
            )
    return {"batch_id": batch_id if history else None, "applied": len(history), "tracks": manifests(db, user, items)}


def undo_tracks(db: Session, user: User, batch_id: str) -> dict:
    batch = db.get(MusicEditBatch, batch_id)
    if batch is None or (batch.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Edit batch not found")
    if batch.undone_at:
        raise HTTPException(409, "Edit already undone")
    applied, skipped, restored = 0, [], []
    with write_transaction(db, name="music_undo"):
        for change in batch.changes:
            item = LibraryService(db).get_item(change["item_id"], user)
            row = db.get(MusicTrackOverride, change["item_id"])
            if item is None or row is None or row.fields != change["after"]:
                skipped.append(change["item_id"])
                continue
            before, after = change["before"], change["after"]
            row.fields = before
            if before.get("album_id") != after.get("album_id"):
                item.title_id = before.get("album_id") or after.get("_source_album_id")
            applied += 1
            restored.append((item, row))
        batch.undone_at = utcnow()
        if restored:
            db.flush()
            library_search.LibrarySearchService(db).sync_music_items(
                [item for item, _row in restored], {item.id: row.fields or {} for item, row in restored}
            )
    return {"applied": applied, "skipped": skipped}


def create_release(db: Session, user: User, body: MusicReleaseCreate) -> dict:
    from app.services import metadata_editor
    name = body.name.strip()
    if not name or bool(body.artist_id) == bool(body.artist_name and body.artist_name.strip()):
        raise HTTPException(422, "Choose one artist and give the release a name")
    batch_id, album_id = str(uuid.uuid4()), str(uuid.uuid4())
    with write_transaction(db, name="music_release_create"):
        items = visible_tracks(db, user, list(dict.fromkeys(body.item_ids)))
        overrides = {o.item_id: o for o in db.scalars(
            select(MusicTrackOverride).where(MusicTrackOverride.item_id.in_([i.id for i in items]))
        )}
        conflicts = []
        for item in items:
            base = body.base.get(item.id, {})
            current_album = track_fields(item, overrides.get(item.id))["album_id"]
            if "album_id" in base and base["album_id"] != current_album:
                conflicts.append({"item_id": item.id, "current": {"album_id": current_album}})
        if conflicts:
            raise HTTPException(409, {"message": "Tracks changed while you were editing", "conflicts": conflicts})
        artist = TitleService(db).get_visible(body.artist_id, user) if body.artist_id else None
        if body.artist_id and (artist is None or artist.type != "artist"):
            raise HTTPException(404, "Artist not found")
        metadata_batch = metadata_editor.Batch(db, "edit", user.id)
        if artist is None:
            artist = MediaTitle(id=str(uuid.uuid4()), type="artist", key=f"music-artist:{uuid.uuid4()}", name=body.artist_name.strip())
            db.add(artist)
            metadata_editor.write_user_field(artist, "name", artist.name, metadata_batch)
        album = MediaTitle(id=album_id, type="album", key=f"music-release:{album_id}", name=name, parent_id=artist.id)
        db.add(album)
        for key, value in (("name", name), ("year", body.year), ("edition", body.edition), ("release_type", "album")):
            if value is not None:
                metadata_editor.write_user_field(album, key, metadata_editor.validate("album", key, value), metadata_batch)
        history = []
        for item in items:
            row = overrides.get(item.id)
            before = dict(row.fields or {}) if row else {}
            after = {**before, "album_id": album.id, "_source_album_id": before.get("_source_album_id", item.title_id)}
            if row is None:
                row = MusicTrackOverride(item_id=item.id)
                db.add(row)
                overrides[item.id] = row
            row.fields, item.title_id = after, album.id
            history.append({"item_id": item.id, "before": before, "after": after})
        _add_edit_batch(db, batch_id, user.id, history)
        db.flush()
        library_search.index_title(db, artist)
        library_search.index_title(db, album)
        library_search.LibrarySearchService(db).sync_music_items(
            items, {item.id: overrides[item.id].fields or {} for item in items}
        )
        metadata_batch.finish(db)
    return {"album_id": album_id, "batch_id": batch_id, "applied": len(items), "tracks": manifests(db, user, items)}
