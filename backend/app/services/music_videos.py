"""Associate saved performances with visible artists and recordings, without changing ownership."""
from __future__ import annotations

import re
from types import SimpleNamespace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import LibraryItem, MediaTitle, MusicTrackOverride, User
from app.music_schemas import MusicVideo
from app.services.library import LibraryService
from app.services.music_providers import correlate_video, normalized


def is_music_video(item: LibraryItem) -> bool:
    raw = item.metadata_json or {}
    categories = raw.get("categories") or []
    categories = categories if isinstance(categories, list) else []
    return item.kind == "video" and (any(isinstance(c, str) and normalized(c) == "music" for c in categories)
        or bool(raw.get("artist") and (raw.get("track") or raw.get("album")))
        or bool(re.search(r"\b(official (music )?video|live performance|music video|cover|remix)\b", normalized(item.title))))


def saved(db: Session, user: User, artist: str, track: str | None = None, *, artist_id: str | None = None) -> list:
    # A bounded candidate query: precise matching runs below, never on the whole library.
    words = normalized(artist).split()
    if not words:
        return []
    needle = max(words, key=len).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.scalars(select(LibraryItem).where(LibraryItem.kind == "video", LibraryItem.status != "missing",
        LibraryService.visible_predicate(user), or_(func.lower(LibraryItem.title).like(f"%{needle}%", escape="\\"),
            func.lower(LibraryItem.uploader).like(f"%{needle}%", escape="\\"),
            func.lower(func.json_extract(LibraryItem.metadata_json, "$.artist")) == artist.casefold(),
            func.lower(func.json_extract(LibraryItem.metadata_json, "$.artists")).like(f"%{needle}%", escape="\\"),
            func.json_extract(LibraryItem.metadata_json, "$.music_artist_id") == artist_id if artist_id else False))
        .order_by(LibraryItem.downloaded_at.desc(), LibraryItem.id).limit(200))
    result = []
    for item in rows:
        if not is_music_video(item):
            continue
        raw = item.metadata_json or {}
        names = raw.get("artists") if isinstance(raw.get("artists"), list) else [raw.get("artist")]
        structured = (bool(artist_id) and raw.get("music_artist_id") == artist_id) or any(isinstance(name, str) and normalized(name) == normalized(artist) for name in names[:10])
        matched_title = f"{artist} {raw.get('track') or ''} {item.title}" if structured else item.title
        youtube_id = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", item.remote_id or ""))
        entry = SimpleNamespace(id=item.remote_id if youtube_id else "00000000000", title=matched_title, uploader=item.uploader, webpage_url=item.webpage_url,
                                thumbnail_url=item.thumbnail_url, duration=item.duration)
        video = correlate_video(entry, artist, track)
        if video:
            video.title = item.title
            if not youtube_id:
                video.id, video.url = f"local:{item.id}", f"/watch/{item.id}"
            video.item_id = item.id
            result.append(video)
    return result[:30]


def album_performances(db: Session, user: User, album: MediaTitle, videos: list[MusicVideo]) -> list[MusicVideo]:
    """Album pages show videos for that release or its visible, edited track names."""
    names = list(db.scalars(select(func.coalesce(
        func.json_extract(MusicTrackOverride.fields, "$.title"), LibraryItem.title,
    )).outerjoin(MusicTrackOverride, MusicTrackOverride.item_id == LibraryItem.id).where(
        LibraryItem.title_id == album.id, LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing",
        LibraryService.visible_predicate(user),
    ).limit(5000)))
    identities = [set(normalized(name).split()) for name in [album.name, *names] if isinstance(name, str) and normalized(name)]
    return [video for video in videos if any(words <= set(normalized(video.title).split()) for words in identities)]


def associate_import(db: Session, item: LibraryItem) -> None:
    """Structured artist tags provide an unambiguous link immediately on import.

    Untagged saved clips are matched when an artist/track page is read, so later-added musicians also find them.
    """
    if not is_music_video(item):
        return
    raw = item.metadata_json or {}
    names = raw.get("artists") if isinstance(raw.get("artists"), list) else [raw.get("artist")]
    names = [name.strip().casefold() for name in names[:10] if isinstance(name, str) and name.strip()]
    if not names:
        return
    candidates = list(db.scalars(select(MediaTitle).where(MediaTitle.type == "artist", func.lower(MediaTitle.name).in_(names)).limit(11)))
    if len(candidates) == 1:
        item.metadata_json = {**raw, "music_artist_id": candidates[0].id}
