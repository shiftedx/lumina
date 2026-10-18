"""Authenticated music surfaces. Speakers receive revocable capabilities, never browser credentials."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from sqlalchemy import select, func, and_, delete
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppSettings, LibraryItem, MediaTitle, PlaybackProgress, MusicCastTicket, MusicLookup, MusicTrackOverride, User, utcnow
from app.music_schemas import CastControl, CastRequest, LyricsEdit, MusicArtworkPlan, MusicIdentify, MusicLyrics, MusicPlanApply, MusicPlaylistSave, MusicQueueRequest, MusicReleaseCreate, MusicTrack, MusicTrackEdit, MusicVideos, MusicWriteback
from app.persistence import write_transaction
from app.routers.titles import visible_title_or_404
from app.security import get_admin_user, get_current_user
from app.services import resource_policy, music_audio, music_curation, music_library, music_playlists, music_providers, music_videos, public_address, screen_time
from app.services.library import LibraryService
from app.services.media_response import MediaFileResponse
from app.services.metadata_editor import Batch, can_edit_details, get_detail_editor, write_user_field
from app.services.rate_limit import enforce_rate_limit


class _MusicAudioResponse(MediaFileResponse):
    """Keep a converted cache entry leased until every response exit path."""

    def __init__(self, *args, release, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._release_music_file = release

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release_music_file()


def _music_audio_response(playback, *, headers: dict[str, str]) -> MediaFileResponse:  # noqa: ANN001
    path, content_type = playback
    release = getattr(playback, "release", lambda: None)
    try:
        return _MusicAudioResponse(path, media_type=content_type, headers=headers, release=release)
    except BaseException:
        release()
        raise


def _guard_music_resources(db: Session, request: Request, user: User, item: LibraryItem, playback, device: str) -> None:
    from app.services.media_probe import MediaProbeService
    from app.services.playback_decision import ANY, ClientCaps, DirectProfile
    from app.services.playback_resources import playback_resources
    if not playback_resources.enabled(db):
        return
    try:
        facts = dict(MediaProbeService(db).facts(item))
        path, _content_type = playback
        duration = facts.get("duration")
        # Compatibility WAV/FLAC can be larger than the source. Budget the file actually sent.
        facts["bit_rate"] = int(path.stat().st_size * 8 / duration) if duration and duration > 0 else None
        caps = ClientCaps(direct=(DirectProfile(ANY, ANY, ANY),), video={}, audio=ANY)
        admission = playback_resources.admit_direct(db, user, request, item.id, device, facts, caps)
        if admission is not None and admission.decision.mode != "direct":
            playback_resources.release(admission)
            raise HTTPException(409, "This audio format cannot fit the household playback bandwidth. Choose a smaller format or download it first.")
        request.state.playback_admission = admission
    except BaseException:
        getattr(playback, "release", lambda: None)()
        raise


def queue(body: MusicQueueRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[MusicTrack]:
    screen_time.enforce(db, user, fresh=True)
    return music_library.queue(db, user, body)


def get_track(item_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MusicTrack:
    return music_library.manifests(db, user, music_library.visible_tracks(db, user, [item_id]))[0]


def track_metadata(item_id: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    item = music_library.visible_tracks(db, user, [item_id])[0]
    override = db.get(MusicTrackOverride, item_id)
    fields = music_library.effective_track_fields(db, user, [item])[item.id]
    return {"fields": fields, "overridden": [key for key in (override.fields or {}) if key in music_library.TRACK_FIELDS] if override else []}


def highlights(title_id: str, limit: int = Query(8, ge=1, le=50), user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[MusicTrack]:
    title = visible_title_or_404(db, title_id, user)
    if title.type not in ("album", "artist"):
        raise HTTPException(404, "Music title not found")
    items = list(db.scalars(select(LibraryItem).join(MediaTitle, LibraryItem.title_id == MediaTitle.id)
        .outerjoin(PlaybackProgress, and_(PlaybackProgress.item_id == LibraryItem.id, PlaybackProgress.user_id == user.id))
        .where(MediaTitle.id == title.id if title.type == "album" else MediaTitle.parent_id == title.id,
               LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
        .order_by(func.coalesce(PlaybackProgress.plays, 0).desc(), MediaTitle.year.desc(), LibraryItem.title, LibraryItem.id).limit(limit)))
    return music_library.manifests(db, user, items)


def audio(item_id: str, request: Request, format_: Literal["native", "flac", "wav"] = Query("native", alias="format"),
          download: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MediaFileResponse:
    item = music_library.visible_tracks(db, user, [item_id])[0]
    screen_time.enforce(db, user)
    if download:
        from app.services.streaming_gate import require_download
        require_download(db, user)
    try:
        path = LibraryService(db).resolve_media_path(item)
        playback = music_audio.playback_file(path, format_)
    except (FileNotFoundError, OSError) as error:
        raise HTTPException(404, "Track is unavailable") from error
    if not download and request.method != "HEAD":
        _guard_music_resources(db, request, user, item, playback, resource_policy.playback_device(request))
    return _music_audio_response(playback, headers={"Cache-Control": "private, max-age=600"})


def edit_tracks(body: MusicTrackEdit, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_library.edit_tracks(db, user, body)


def create_release(body: MusicReleaseCreate, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_library.create_release(db, user, body)


def undo(batch_id: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_library.undo_tracks(db, user, batch_id)


def lyrics(item_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    item = music_library.visible_tracks(db, user, [item_id])[0]
    override = db.get(MusicTrackOverride, item_id)
    local = music_library.track_fields(item, override).get("lyrics")
    if isinstance(local, str) and (local or (override and "lyrics" in (override.fields or {}))):
        return {**music_providers.parse_lyrics(local, source="Your library").model_dump(), "refreshing": False}
    track = music_library.manifests(db, user, [item])[0]
    key = music_providers.key_for("lyrics", track.artist, track.title, track.album, str(track.duration))
    row = db.get(MusicLookup, key)
    music_providers.enqueue(key, "lyrics", lambda: music_providers.lyric_lookup(track.title, track.artist, track.album, track.duration))
    return {
        **MusicLyrics().model_dump(),
        **(row.payload if row else {}),
        "refreshing": music_providers.pending(key),
    }


def save_lyrics(item_id: str, body: LyricsEdit, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_library.edit_tracks(db, user, MusicTrackEdit(item_ids=[item_id], changes={"lyrics": body.text}))


def videos(item_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MusicVideos:
    from app.services.streaming_gate import check
    item = music_library.visible_tracks(db, user, [item_id])[0]
    track = music_library.manifests(db, user, [item])[0]
    local = music_videos.saved(db, user, track.artist, track.title, artist_id=track.artist_id if track.artist == track.album_artist else None)
    try:
        check(db, user, "youtube")
    except HTTPException as error:
        if error.status_code != 403:
            raise
        return MusicVideos(items=local)
    enforce_rate_limit("youtube_search", request, user_id=user.id)
    key = music_providers.key_for("videos", track.artist, track.title)
    row = db.get(MusicLookup, key)
    music_providers.enqueue(key, "videos", lambda: music_providers.video_lookup(track.artist, track.title))
    payload = row.payload if row else {}
    merged = {v.id: v for v in local}
    from app.music_schemas import MusicVideo
    for value in payload.get("items", []):
        video = MusicVideo.model_validate(value)
        merged.setdefault(video.id, video)
    return MusicVideos(items=list(merged.values()), error=payload.get("error"), refreshing=music_providers.pending(key))


def title_videos(title_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MusicVideos:
    from app.services.streaming_gate import check
    title = visible_title_or_404(db, title_id, user)
    if title.type not in ("artist", "album"):
        raise HTTPException(404, "Music title not found")
    parent = visible_title_or_404(db, title.parent_id, user) if title.type == "album" and title.parent_id else None
    artist = parent.name if parent else title.name
    local = music_videos.saved(db, user, artist, artist_id=parent.id if parent else title.id if title.type == "artist" else None)
    if title.type == "album":
        local = music_videos.album_performances(db, user, title, local)
    try:
        check(db, user, "youtube")
    except HTTPException as error:
        if error.status_code != 403:
            raise
        return MusicVideos(items=local)
    enforce_rate_limit("youtube_search", request, user_id=user.id)
    key = music_providers.key_for("artist_videos", artist)
    row = db.get(MusicLookup, key)
    music_providers.enqueue(key, "videos", lambda: music_providers.video_lookup(artist))
    payload = row.payload if row else {}
    from app.music_schemas import MusicVideo
    merged = {v.id: v for v in local}
    for value in payload.get("items", []):
        video = MusicVideo.model_validate(value)
        merged.setdefault(video.id, video)
    matches = list(merged.values())
    if title.type == "album":
        matches = music_videos.album_performances(db, user, title, matches)
    return MusicVideos(items=matches, error=payload.get("error"), refreshing=music_providers.pending(key))


def discography(title_id: str, offset: int = Query(0, ge=0, le=10_000), user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    title = visible_title_or_404(db, title_id, user)
    identity = (title.provider_ids or {}).get("MusicBrainz")
    if title.type != "artist" or not identity:
        return {"items": [], "next_offset": None, "refreshing": False, "error": None}
    key = music_providers.key_for("discography", identity, str(offset))
    row = db.get(MusicLookup, key)
    music_providers.enqueue(key, "discography", lambda: music_providers.release_page(identity, offset), ttl_days=30)
    return {"items": (row.payload or {}).get("items", []) if row else [], "next_offset": (row.payload or {}).get("next_offset") if row else None,
            "refreshing": music_providers.pending(key), "error": (row.payload or {}).get("error") if row else None}


def editions(title_id: str, offset: int = Query(0, ge=0, le=10_000), user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    title = visible_title_or_404(db, title_id, user)
    if title.type != "album":
        raise HTTPException(404, "Album not found")
    group = (title.metadata_json or {}).get("release_group_id")
    peers = select(MediaTitle).where(MediaTitle.type == "album", MediaTitle.id != title.id, LibraryService.visible_title_predicate(user))
    peers = peers.where(func.json_extract(MediaTitle.metadata_json, "$.release_group_id") == group) if group else peers.where(MediaTitle.parent_id == title.parent_id, func.lower(MediaTitle.name) == title.name.casefold())
    rows = list(db.scalars(peers.order_by(MediaTitle.year, MediaTitle.name, MediaTitle.id).limit(100)))
    from app.services.titles import TitleService
    service = TitleService(db)
    batch = service.load(user, rows)
    owned = [service.summary(row, batch).model_dump() for row in rows]
    if not group:
        return {"owned": owned, "items": [], "next_offset": None, "refreshing": False, "error": None}
    key = music_providers.key_for("editions", group, str(offset))
    row = db.get(MusicLookup, key)
    music_providers.enqueue(key, "editions", lambda: music_providers.release_editions(group, offset), ttl_days=30)
    return {"owned": owned, "items": (row.payload or {}).get("items", []) if row else [], "next_offset": (row.payload or {}).get("next_offset") if row else None,
            "refreshing": music_providers.pending(key), "error": (row.payload or {}).get("error") if row else None}


def identify(title_id: str, body: MusicIdentify, request: Request, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    enforce_rate_limit("metadata_preview", request, user_id=user.id)
    title = visible_title_or_404(db, title_id, user)
    if title.type not in ("artist", "album"):
        raise HTTPException(422, "Choose an artist or album")
    if title.locked:
        raise HTTPException(409, "title_locked")
    try:
        provider_id = str(uuid.UUID(body.provider_id)) if body.provider_id else None
    except ValueError as error:
        raise HTTPException(422, "Invalid MusicBrainz identifier") from error
    if provider_id:
        with write_transaction(db, name="music_identify"):
            batch = Batch(db, "identify", user.id)
            write_user_field(title, "provider_ids", {**(title.provider_ids or {}), "MusicBrainz": provider_id}, batch)
            title.metadata_due_at = utcnow()
            cached = db.get(MusicLookup, music_providers.key_for("metadata", title.id))
            if cached:
                cached.expires_at = utcnow()
            batch.finish(db)
    queued = music_providers.request_enrichment(title.id, provider_id, force=body.refresh or bool(provider_id))
    return {"queued": queued}


def enrichment(title_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    title = visible_title_or_404(db, title_id, user)
    if title.type not in ("album", "artist"):
        raise HTTPException(404, "Music title not found")
    key = music_providers.key_for("metadata", title.id)
    row = db.get(MusicLookup, key)
    if not title.locked:
        music_providers.request_enrichment(title.id)
    return {"refreshing": music_providers.pending(key), "identified": bool((title.provider_ids or {}).get("MusicBrainz")),
            "candidates": (title.metadata_json or {}).get("music_candidates", []) if can_edit_details(user, db.get(AppSettings, 1)) else [],
            "error": ((row.payload or {}).get("error") or (row.payload or {}).get("artwork_error")) if row else None}


def speakers(request: Request, user: User = Depends(get_current_user)) -> list[dict]:
    from app.services.music_sonos import service, SonosError
    enforce_rate_limit("metadata_preview", request, user_id=user.id)
    try:
        return service.discover()
    except SonosError as error:
        raise HTTPException(502, "Sonos speakers could not be discovered") from error


def cast(body: CastRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    from app.services.music_sonos import service, SonosError, SonosTrack
    from app.services.music_listening_session import MusicListeningSessionConflict, MusicListeningSessionService
    tracks = music_library.manifests(db, user, music_library.visible_tracks(db, user, body.item_ids))
    enforce_rate_limit("music_cast", request, user_id=user.id)
    screen_time.enforce(db, user, fresh=True)
    origin = public_address.local_origin() or public_address.origin()
    if not origin:
        raise HTTPException(409, "Configure a server address reachable by your Sonos speakers")
    sessions = MusicListeningSessionService(db)
    shared = sessions.read(user)
    revision = shared.revision if body.base_revision is None else body.base_revision
    # Older callers have no revision. Seed only the validated, visible candidate IDs,
    # then transfer against that durable revision; modern callers always send a CAS base.
    if body.base_revision is None and shared.revision == 0 and not shared.queue:
        shared = sessions.replace(user, base_revision=0, item_ids=[track.id for track in tracks], current_index=0,
                                  position_seconds=body.position_seconds, repeat="off", shuffle=False)
        revision = shared.revision
    if body.base_revision is not None:
        expected = [track.id for track in shared.queue[body.current_index:body.current_index + len(body.item_ids)]]
        if body.current_index >= len(shared.queue) or expected != body.item_ids:
            raise HTTPException(409, detail={"code": "music_session_conflict", "session": {
                "revision": shared.revision, "queue": [track.model_dump() for track in shared.queue], "current_index": shared.current_index,
                "position_seconds": shared.position_seconds, "repeat": shared.repeat, "shuffle": shared.shuffle,
                "active_output": shared.active_output, "updated_at": shared.updated_at.isoformat() + "Z" if shared.updated_at else None, "removed_count": shared.removed_count,
            }})
    if not tracks:
        raise HTTPException(422, "No requested tracks are currently available for Sonos")
    queue_indices = [body.current_index + index for index, item_id in enumerate(body.item_ids) if any(track.id == item_id for track in tracks)]
    token, ticket_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    with write_transaction(db, name="music_cast_ticket"):
        db.execute(delete(MusicCastTicket).where(MusicCastTicket.expires_at <= utcnow()))
        ticket = MusicCastTicket(id=ticket_id, user_id=user.id, speaker_id=body.speaker_id,
                                 token_hash=hashlib.sha256(token.encode()).hexdigest(), item_ids=[t.id for t in tracks],
                                 expires_at=utcnow() + timedelta(hours=24), output_format="sonos_s1" if body.compatibility else "sonos")
        db.add(ticket)
    queue = [SonosTrack(url=f"{origin}/api/music/cast/{ticket_id}/{t.id}/audio?token={token}", title=t.title, artist=t.artist,
                        album=t.album, duration_seconds=t.duration,
                        artwork_url=f"{origin}/api/music/cast/{ticket_id}/{t.id}/artwork?token={token}" if t.artwork_url else "",
                        content_type="audio/flac") for t in tracks]
    try:
        result = service.play_queue(body.speaker_id, queue)
        if body.position_seconds:
            service.control(body.speaker_id, "seek", int(body.position_seconds))
        acknowledged = service.status(body.speaker_id)
        if not acknowledged.get("playing") or (acknowledged.get("track") or {}).get("track_number") != 1:
            raise SonosError("Sonos did not acknowledge the requested queue")
    except SonosError as error:
        with write_transaction(db, name="music_cast_revoke"):
            ticket.expires_at = utcnow()
        raise HTTPException(502, "Sonos playback could not start") from error
    output = {"kind": "sonos", "ticket_id": ticket_id, "speaker_id": body.speaker_id, "queue_indices": queue_indices}
    try:
        saved = sessions.acknowledge_sonos(user, base_revision=revision, output=output)
    except MusicListeningSessionConflict:
        try: service.control(body.speaker_id, "stop", None)
        except SonosError: pass
        with write_transaction(db, name="music_cast_revoke_conflict"):
            ticket.expires_at = utcnow()
        fresh = sessions.read(user)
        raise HTTPException(409, detail={"code": "music_session_conflict", "session": {
            "revision": fresh.revision, "queue": [track.model_dump() for track in fresh.queue], "current_index": fresh.current_index,
            "position_seconds": fresh.position_seconds, "repeat": fresh.repeat, "shuffle": fresh.shuffle,
            "active_output": fresh.active_output, "updated_at": fresh.updated_at.isoformat() + "Z" if fresh.updated_at else None, "removed_count": fresh.removed_count,
        }}) from None
    old_ticket = shared.active_output.get("ticket_id") if shared.active_output.get("kind") == "sonos" else None
    if isinstance(old_ticket, str) and old_ticket != ticket_id:
        old = db.get(MusicCastTicket, old_ticket)
        if old:
            try: service.control(old.speaker_id, "stop", None)
            except SonosError: pass
            with write_transaction(db, name="music_cast_revoke_source"):
                old.expires_at = utcnow()
    return {"ticket_id": ticket_id, "state": result, "requested_count": len(body.item_ids), "queued_count": len(tracks), "excluded_count": len(body.item_ids) - len(tracks), "queue_indices": queue_indices, "current_index": queue_indices[0], "position_seconds": body.position_seconds, "session": {"revision": saved.revision, "queue": [track.model_dump() for track in saved.queue], "current_index": saved.current_index, "position_seconds": saved.position_seconds, "repeat": saved.repeat, "shuffle": saved.shuffle, "active_output": saved.active_output, "updated_at": saved.updated_at.isoformat() + "Z" if saved.updated_at else None, "removed_count": saved.removed_count}}


def _cast_access(db: Session, ticket_id: str, item_id: str, token: str | None) -> tuple[MusicCastTicket, User, LibraryItem]:
    ticket = db.get(MusicCastTicket, ticket_id)
    if not token or not ticket or ticket.expires_at <= utcnow() or item_id not in ticket.item_ids or not secrets.compare_digest(ticket.token_hash, hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(401, "Expired cast access")
    user = db.get(User, ticket.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Expired cast access")
    item = music_library.visible_tracks(db, user, [item_id])[0]
    return ticket, user, item


def cast_audio(ticket_id: str, item_id: str, request: Request, token: str | None = Query(None, max_length=100), db: Session = Depends(get_db, scope="function")) -> MediaFileResponse:
    ticket, user, item = _cast_access(db, ticket_id, item_id, token)
    request.state.resource_user_id = user.id
    screen_time.enforce(db, user)
    try:
        playback = music_audio.playback_file(LibraryService(db).resolve_media_path(item), ticket.output_format)
    except (OSError, FileNotFoundError) as error:
        raise HTTPException(404, "Track is unavailable") from error
    if request.method != "HEAD":
        _guard_music_resources(db, request, user, item, playback, f"cast:{ticket.id}")
    return _music_audio_response(playback, headers={"Cache-Control": "no-store"})


def control(ticket_id: str, body: CastControl, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    from app.services.music_sonos import service, SonosError
    ticket = db.get(MusicCastTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id or ticket.expires_at <= utcnow():
        raise HTTPException(404, "Cast session not found")
    enforce_rate_limit("music_control", request, user_id=user.id)
    if body.action in ("play", "next", "previous", "seek"):
        screen_time.enforce(db, user, fresh=True)
    if body.action == "stop":
        # Revoke first, including when an unreachable speaker cannot acknowledge Stop.
        with write_transaction(db, name="music_cast_stop"):
            ticket.expires_at = utcnow()
    try:
        result = service.control(ticket.speaker_id, body.action, body.value)
    except SonosError as error:
        raise HTTPException(502, "Sonos control is unavailable") from error
    if body.action in ("pause", "stop"):
        screen_time.heartbeat(db, user, stopped=True)
    return result


def cast_status(ticket_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    from app.services.music_sonos import service, SonosError
    ticket = db.get(MusicCastTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id or ticket.expires_at <= utcnow():
        raise HTTPException(404, "Cast session not found")
    enforce_rate_limit("music_control", request, user_id=user.id)
    try:
        screen_time.enforce(db, user, fresh=True)
        result = service.status(ticket.speaker_id)
        if result.get("playing"):
            screen_time.heartbeat(db, user)
        return result
    except HTTPException as error:
        if error.status_code == 403:
            with write_transaction(db, name="music_cast_limit"):
                ticket.expires_at = utcnow()
            try:
                service.control(ticket.speaker_id, "stop", None)
            except SonosError:
                pass
        raise
    except SonosError as error:
        raise HTTPException(502, "Sonos status is unavailable") from error


def artwork_preview(body: MusicArtworkPlan, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_curation.artwork_plan(db, user, body)


async def artwork_bulk(request: Request, plan_id: str = Query(min_length=64, max_length=64), user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> dict:
    from app.routers.title_images import _body_refused
    enforce_rate_limit("image_upload", request, user_id=user.id)
    # Resolve authorization and the expiring plan before reading a large upload.
    music_curation._load_plan(db, user, plan_id, "artwork_plan")
    try:
        data = await request.body()
    except ClientDisconnect:
        raise _body_refused(request) from None
    return await run_in_threadpool(music_curation.apply_artwork, db, user, plan_id, data)


def writeback_preview(body: MusicWriteback, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_curation.writeback_plan(db, user, body)


def file_backups(user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[dict]:
    from app.models import MusicFileWriteback
    return [{"id": r.id, "item_id": r.item_id, "created_at": r.created_at.isoformat(), "restored": bool(r.restored_at)}
            for r in db.scalars(select(MusicFileWriteback).where(MusicFileWriteback.user_id == user.id).order_by(MusicFileWriteback.created_at.desc()).limit(100))]


def file_restore(record_id: str, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_curation.restore_writeback(db, user, record_id)


def playlists(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[dict]:
    return music_playlists.list_playlists(db, user)


def playlist_create(body: MusicPlaylistSave, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_playlists.save(db, user, body)


def playlist_save(playlist_id: str, body: MusicPlaylistSave, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return music_playlists.save(db, user, body, playlist_id)


def playlist_delete(playlist_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    music_playlists.remove(db, user, playlist_id)
    return {"deleted": True}


def playlist_tracks(playlist_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[MusicTrack]:
    screen_time.enforce(db, user, fresh=True)
    return music_playlists.tracks(db, user, playlist_id)


def mix(kind: Literal["favorites", "unplayed", "rotation", "radio"], seed_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[MusicTrack]:
    screen_time.enforce(db, user, fresh=True)
    return music_playlists.mix(db, user, kind, seed_id)


def register(app: FastAPI, artwork=None) -> None:  # noqa: ANN001
    music_providers.configure_artwork(artwork)
    def file_writeback(body: MusicPlanApply, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return music_curation.apply_writeback(db, user, body.plan_id, artwork)

    def cast_artwork(ticket_id: str, item_id: str, request: Request, token: str | None = Query(None, max_length=100), db: Session = Depends(get_db, scope="function")) -> Response:
        from app.services.titles import title_image_bytes
        _ticket, user, item = _cast_access(db, ticket_id, item_id, token)
        request.state.resource_user_id = user.id
        title = visible_title_or_404(db, item.title_id, user) if item.title_id else None
        if title is None or title.type != "album":
            raise HTTPException(404, "Album artwork not found")
        content_type, data = title_image_bytes(db, title, "Primary", artwork)
        return Response(data, media_type=content_type, headers={"Cache-Control": "no-store"})

    app.add_api_route("/api/music/artwork/preview", artwork_preview, methods=["POST"])
    app.add_api_route("/api/music/artwork/bulk", artwork_bulk, methods=["PUT"])
    app.add_api_route("/api/music/writeback/preview", writeback_preview, methods=["POST"])
    app.add_api_route("/api/music/writeback/apply", file_writeback, methods=["POST"])
    app.add_api_route("/api/music/writeback/backups", file_backups, methods=["GET"])
    app.add_api_route("/api/music/writeback/{record_id}/restore", file_restore, methods=["POST"])
    app.add_api_route("/api/music/playlists", playlists, methods=["GET"])
    app.add_api_route("/api/music/playlists", playlist_create, methods=["POST"])
    app.add_api_route("/api/music/playlists/{playlist_id}", playlist_save, methods=["PUT"])
    app.add_api_route("/api/music/playlists/{playlist_id}", playlist_delete, methods=["DELETE"])
    app.add_api_route("/api/music/playlists/{playlist_id}/tracks", playlist_tracks, methods=["GET"])
    app.add_api_route("/api/music/mixes/{kind}", mix, methods=["GET"])
    app.add_api_route("/api/music/queue", queue, methods=["POST"])
    app.add_api_route("/api/music/albums", create_release, methods=["POST"])
    app.add_api_route("/api/music/tracks/edit", edit_tracks, methods=["POST"])
    app.add_api_route("/api/music/edits/{batch_id}/undo", undo, methods=["POST"])
    app.add_api_route("/api/music/tracks/{item_id}", get_track, methods=["GET"])
    app.add_api_route("/api/music/tracks/{item_id}/metadata", track_metadata, methods=["GET"])
    app.add_api_route("/api/music/tracks/{item_id}/audio", audio, methods=["GET", "HEAD"])
    app.add_api_route("/api/music/tracks/{item_id}/lyrics", lyrics, methods=["GET"])
    app.add_api_route("/api/music/tracks/{item_id}/lyrics", save_lyrics, methods=["PUT"])
    app.add_api_route("/api/music/tracks/{item_id}/videos", videos, methods=["GET"])
    app.add_api_route("/api/music/titles/{title_id}/videos", title_videos, methods=["GET"])
    app.add_api_route("/api/music/titles/{title_id}/discography", discography, methods=["GET"])
    app.add_api_route("/api/music/titles/{title_id}/editions", editions, methods=["GET"])
    app.add_api_route("/api/music/titles/{title_id}/identify", identify, methods=["POST"])
    app.add_api_route("/api/music/titles/{title_id}/enrichment", enrichment, methods=["GET"])
    app.add_api_route("/api/music/titles/{title_id}/tracks", highlights, methods=["GET"])
    app.add_api_route("/api/music/sonos", speakers, methods=["GET"])
    app.add_api_route("/api/music/cast", cast, methods=["POST"])
    app.add_api_route("/api/music/cast/{ticket_id}/{item_id}/audio", cast_audio, methods=["GET", "HEAD"])
    app.add_api_route("/api/music/cast/{ticket_id}/{item_id}/artwork", cast_artwork, methods=["GET", "HEAD"])
    app.add_api_route("/api/music/cast/{ticket_id}/control", control, methods=["POST"])
    app.add_api_route("/api/music/cast/{ticket_id}/status", cast_status, methods=["GET"])
