"""Previewed artwork batches and explicit, reversible audio-file writeback."""
from __future__ import annotations

import secrets
import shutil
import threading
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import LibraryItemArtifact, MediaArtifact, MediaTitle, MusicFileWriteback, MusicLookup, MusicTrackOverride, User, utcnow
from app.music_schemas import MusicArtworkPlan, MusicWriteback
from app.persistence import write_transaction
from app.services import metadata_editor, music_library, music_writeback, title_images
from app.services.library import LibraryService
from app.services.media_probe import media_tool
from app.services.titles import title_image_bytes, title_image_source

PLAN_TTL = timedelta(minutes=20)
MAX_BACKUP_BYTES = 10 * 1024**3
_writeback_slot = threading.Lock()


def _save_plan(db: Session, user: User, kind: str, payload: dict) -> str:
    key = secrets.token_hex(32)
    with write_transaction(db, name="music_plan"):
        db.add(MusicLookup(key=key, kind=kind, payload={"user_id": user.id, **payload}, expires_at=utcnow() + PLAN_TTL))
    return key


def _load_plan(db: Session, user: User, key: str, kind: str) -> MusicLookup:
    plan = db.get(MusicLookup, key)
    if plan is None or plan.kind != kind or plan.expires_at <= utcnow() or plan.payload.get("user_id") != user.id:
        raise HTTPException(409, "This preview has expired. Preview the changes again.")
    return plan


def artwork_plan(db: Session, user: User, body: MusicArtworkPlan) -> dict:
    ids = list(dict.fromkeys(body.title_ids))
    if set(ids) != set(body.base):
        raise HTTPException(422, "Each selected title needs its current artwork tag")
    key = _save_plan(db, user, "artwork_plan", {**body.model_dump(), "title_ids": ids})
    return {"plan_id": key}


def apply_artwork(db: Session, user: User, plan_id: str, data: bytes) -> dict:
    plan = _load_plan(db, user, plan_id, "artwork_plan")
    payload = dict(plan.payload)
    try:
        encoded = title_images.encode(data, payload["image_type"], ffmpeg=media_tool(db, "ffmpeg"))
        applied, skipped = 0, []
        with write_transaction(db, name="music_artwork_bulk"):
            plan = _load_plan(db, user, plan_id, "artwork_plan")
            titles = metadata_editor.load_titles(db, user, payload["title_ids"])
            batch = metadata_editor.Batch(db, "image", user.id)
            for title_id in payload["title_ids"]:
                title = titles.get(title_id)
                reason = "not_found" if title is None else "wrong_type" if title.type not in ("album", "artist") else "locked_item" if title.locked else None
                if reason is None:
                    try:
                        key = title_images.check_write(title, payload["image_type"], 0, payload["base"][title_id], encoded.sha256)
                    except title_images.UploadError as error:
                        reason = "changed_since" if error.detail == "conflict" else error.detail
                if reason:
                    skipped.append({"title_id": title_id, "reason": reason})
                    continue
                if applied == 0:
                    title_images.save(db, encoded, user.id)
                applied += title_images.write_image(title, key, {"upload": encoded.sha256, "tag": encoded.sha256}, batch)
            batch.finish(db)
            db.delete(plan)
        return {"batch_id": batch.id if applied else None, "applied": applied, "skipped": skipped}
    except title_images.UploadError as error:
        raise HTTPException(error.status, error.detail) from None


def _write_fields(db: Session, user: User, items: list) -> list[dict]:
    tracks = music_library.manifests(db, user, items)
    albums = {t.id: t for t in db.scalars(select(MediaTitle).where(MediaTitle.id.in_({t.album_id for t in tracks if t.album_id})))}
    artists = {t.id: t for t in db.scalars(select(MediaTitle).where(MediaTitle.id.in_({t.parent_id for t in albums.values() if t.parent_id})))}
    overrides = {o.item_id: o for o in db.scalars(select(MusicTrackOverride).where(MusicTrackOverride.item_id.in_([i.id for i in items])))}
    entries = []
    for item, track in zip(items, tracks, strict=True):
        path = LibraryService(db).resolve_media_path(item)
        stat = path.stat()
        album = albums.get(track.album_id)
        source = music_library.track_fields(item, overrides.get(item.id))
        fields = {"title": track.title, "artist": track.artist, "album_artist": track.album_artist, "album": track.album,
                  "track_number": track.number, "disc_number": track.disc, "genres": track.genres}
        if album and album.year:
            fields["year"] = album.year
        override = overrides.get(item.id)
        if override and "lyrics" in (override.fields or {}):
            fields["lyrics"] = source.get("lyrics") or None
        elif source.get("lyrics"):
            fields["lyrics"] = source["lyrics"]
        if album and (album.provider_ids or {}).get("MusicBrainz"):
            fields["musicbrainz_release_id"] = album.provider_ids["MusicBrainz"]
        artist = artists.get(album.parent_id) if album else None
        if artist and (artist.provider_ids or {}).get("MusicBrainz"):
            fields["musicbrainz_album_artist_id"] = artist.provider_ids["MusicBrainz"]
        entries.append({"item_id": item.id, "title": track.title, "format": path.suffix.lower().lstrip("."),
                        "fields": fields, "fingerprint": [stat.st_size, stat.st_mtime_ns], "bytes": stat.st_size,
                        "album_id": track.album_id, "art_tag": title_image_source(album, "Primary") if album else None})
    return entries


def writeback_plan(db: Session, user: User, body: MusicWriteback) -> dict:
    items = music_library.visible_tracks(db, user, list(dict.fromkeys(body.item_ids)))
    try:
        entries = _write_fields(db, user, items)
    except (OSError, FileNotFoundError) as error:
        raise HTTPException(409, "A selected audio file is unavailable") from error
    key = _save_plan(db, user, "writeback_plan", {"entries": entries, "include_artwork": body.include_artwork})
    supported = {"mp3", "flac", "ogg", "oga", "opus", "m4a", "m4b", "mp4"}
    return {"plan_id": key, "backup_bytes": sum(e["bytes"] for e in entries), "include_artwork": body.include_artwork,
            "entries": [{k: e[k] for k in ("item_id", "title", "format", "fields")} | {"supported": e["format"] in supported} for e in entries]}


def apply_writeback(db: Session, user: User, plan_id: str, artwork) -> dict:  # noqa: ANN001
    if not _writeback_slot.acquire(blocking=False):
        raise HTTPException(409, "Another music file writeback is already running. Try again shortly.")
    try:
        return _apply_writeback(db, user, plan_id, artwork)
    finally:
        _writeback_slot.release()


def _apply_writeback(db: Session, user: User, plan_id: str, artwork) -> dict:  # noqa: ANN001
    plan = _load_plan(db, user, plan_id, "writeback_plan")
    payload = dict(plan.payload)
    # Claim once before filesystem work. Retrying requires a fresh preview.
    with write_transaction(db, name="music_writeback_claim"):
        plan = _load_plan(db, user, plan_id, "writeback_plan")
        db.delete(plan)
    root = settings.data_dir / "music-file-backups"
    root.mkdir(parents=True, exist_ok=True)
    needed = sum(e["bytes"] for e in payload["entries"])
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    if used + needed > MAX_BACKUP_BYTES or shutil.disk_usage(root).free < needed * 3:
        raise HTTPException(507, "Not enough backup space. Remove old file backups or select fewer tracks.")
    applied, skipped = [], []
    for entry in payload["entries"]:
        try:
            item = music_library.visible_tracks(db, user, [entry["item_id"]])[0]
            fresh = _write_fields(db, user, [item])[0]
            if any(fresh[k] != entry[k] for k in ("fingerprint", "fields", "art_tag", "album_id")):
                raise music_writeback.WritebackError("Track changed after the preview")
            image, content_type = None, "image/jpeg"
            if payload["include_artwork"] and entry["art_tag"]:
                content_type, image = title_image_bytes(db, db.get(MediaTitle, entry["album_id"]), "Primary", artwork)
            result = music_writeback.writeback(LibraryService(db).resolve_media_path(item), entry["fields"], backup_root=root,
                                              artwork=image, artwork_type=content_type, expected_fingerprint=tuple(entry["fingerprint"]))
            record_id = secrets.token_hex(16)
            with write_transaction(db, name="music_file_written"):
                db.add(MusicFileWriteback(id=record_id, user_id=user.id, item_id=item.id, backup_id=result["backup_id"]))
                _invalidate_artifact(db, item)
            applied.append({"id": record_id, "item_id": item.id, "title": entry["title"], "format": result["format"]})
        except (music_writeback.WritebackError, OSError, HTTPException) as error:
            reason = error.detail if isinstance(error, HTTPException) else str(error) if isinstance(error, music_writeback.WritebackError) else "Audio file is unavailable"
            skipped.append({"item_id": entry["item_id"], "reason": reason})
    return {"applied": applied, "skipped": skipped}


def _invalidate_artifact(db: Session, item) -> None:  # noqa: ANN001
    path = LibraryService(db).resolve_media_path(item)
    stat = path.stat()
    item.file_size = stat.st_size
    for artifact in db.scalars(select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
                              .where(LibraryItemArtifact.library_item_id == item.id)):
        artifact.probe, artifact.size, artifact.mtime_ns = None, stat.st_size, stat.st_mtime_ns


def restore_writeback(db: Session, user: User, record_id: str) -> dict:
    record = db.get(MusicFileWriteback, record_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(404, "File backup not found")
    if record.restored_at:
        raise HTTPException(409, "File backup already restored")
    item = music_library.visible_tracks(db, user, [record.item_id])[0]
    try:
        music_writeback.restore(LibraryService(db).resolve_media_path(item), record.backup_id, backup_root=settings.data_dir / "music-file-backups")
    except music_writeback.WritebackError as error:
        raise HTTPException(409, str(error)) from None
    with write_transaction(db, name="music_file_restored"):
        record.restored_at = utcnow()
        _invalidate_artifact(db, item)
    return {"restored": True}
