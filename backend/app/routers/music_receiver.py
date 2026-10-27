"""Device-bound, bounded Chromecast music receiver queues."""
from __future__ import annotations

import hashlib
import math
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeviceToken, MusicReceiverTicket, User, utcnow
from app.persistence import write_transaction
from app.services import music_audio, music_library, public_address, screen_time
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id

TTL = timedelta(minutes=15)
TTL_MAX = timedelta(hours=8)
QUEUE_LIMIT = 100
CONTENT_TYPE = "audio/mp4"
CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


class MusicReceiverTicketBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    item_ids: list[str] = Field(min_length=1, max_length=QUEUE_LIMIT)
    current_index: int = Field(default=0, ge=0, le=QUEUE_LIMIT - 1)
    position_seconds: float = Field(ge=0, le=604800)


def _origin() -> str:
    origin = public_address.origin() or public_address.local_origin()
    if origin is None:
        raise HTTPException(409, detail={"code": "cast_address_required", "message": "Configure a reachable public or local Lumina address before casting."})
    return origin


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _expiry(tracks: list) -> datetime:  # noqa: ANN401
    durations = [getattr(track, "duration", None) for track in tracks]
    if any(type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0 for duration in durations):
        return utcnow() + TTL_MAX
    return utcnow() + min(TTL_MAX, max(TTL, timedelta(seconds=sum(durations) + TTL.total_seconds())))


def _tracks(db: Session, user: User, item_ids: list[str]):  # noqa: ANN201
    items = music_library.visible_tracks(db, user, item_ids)
    if len(items) != len(item_ids) or [item.id for item in items] != item_ids:
        raise HTTPException(404, "Music track not found")
    return items, music_library.manifests(db, user, items)


def _discard(db: Session, ticket: MusicReceiverTicket, *, name: str) -> None:
    with write_transaction(db, name=name):
        db.delete(ticket)


def _access(db: Session, ticket_id: str, item_id: str, token: str | None):  # noqa: ANN201
    ticket = db.get(MusicReceiverTicket, ticket_id)
    if ticket is None or not token or not secrets.compare_digest(ticket.token_hash, _token_hash(token)):
        raise HTTPException(401, "Expired music receiver access")
    if ticket.expires_at <= utcnow():
        _discard(db, ticket, name="music_receiver_expire")
        raise HTTPException(401, "Expired music receiver access")
    user = db.get(User, ticket.user_id)
    issuer = db.get(DeviceToken, ticket.issuer_device_id)
    if user is None or not user.is_active or issuer is None or issuer.user_id != ticket.user_id or issuer.kind != "jellyfin":
        _discard(db, ticket, name="music_receiver_invalidate")
        raise HTTPException(401, "Expired music receiver access")
    item_ids = ticket.item_ids if isinstance(ticket.item_ids, list) and all(isinstance(value, str) for value in ticket.item_ids) else []
    if item_id not in item_ids:
        raise HTTPException(401, "Expired music receiver access")
    items, tracks = _tracks(db, user, item_ids)
    index = item_ids.index(item_id)
    item, track = items[index], tracks[index]
    return ticket, user, item, track


def _response(ticket: MusicReceiverTicket, token: str, origin: str, tracks: list, current_index: int, position: float) -> dict:  # noqa: ANN001
    encoded = quote(token, safe="")
    queue = [
        {"item_id": track.id, "media_url": f"{origin}/api/music/receivers/{ticket.id}/{track.id}/audio?token={encoded}",
         "content_type": CONTENT_TYPE, "title": track.title, "duration": track.duration}
        for track in tracks
    ]
    current = queue[current_index]
    return {
        "ticket_id": ticket.id,
        "media_url": current["media_url"],
        "content_type": CONTENT_TYPE,
        "title": current["title"],
        "duration": current["duration"],
        "position_seconds": position,
        "expires_at": ticket.expires_at.isoformat() + "Z",
        "qualification": "unverified_hardware",
        "queue": queue,
        "queue_limit": QUEUE_LIMIT,
    }


def mint_native(body: MusicReceiverTicketBody, user: User, db: Session, *, issuer_device_id: str) -> dict:
    parsed_ids = [parse_item_id(item_id) for item_id in body.item_ids]
    if any(item_id is None for item_id in parsed_ids) or body.current_index >= len(parsed_ids):
        raise HTTPException(404, "Music track not found")
    item_ids = [item_id for item_id in parsed_ids if item_id is not None]
    origin = _origin()
    screen_time.enforce(db, user, fresh=True)
    _items, tracks = _tracks(db, user, item_ids)
    token, ticket_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    ticket = MusicReceiverTicket(
        id=ticket_id, user_id=user.id, issuer_device_id=issuer_device_id, item_ids=item_ids,
        token_hash=_token_hash(token), expires_at=_expiry(tracks),
    )
    with write_transaction(db, name="music_receiver_mint"):
        db.query(MusicReceiverTicket).filter(MusicReceiverTicket.expires_at <= utcnow()).delete(synchronize_session=False)
        db.add(ticket)
    return _response(ticket, token, origin, tracks, body.current_index, body.position_seconds)


def renew_native(ticket_id: str, user: User, db: Session, *, issuer_device_id: str) -> dict:
    ticket = db.get(MusicReceiverTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id or ticket.issuer_device_id != issuer_device_id or ticket.expires_at <= utcnow():
        raise HTTPException(404, "Music receiver ticket not found")
    item_ids = ticket.item_ids if isinstance(ticket.item_ids, list) and all(isinstance(value, str) for value in ticket.item_ids) else []
    _items, tracks = _tracks(db, user, item_ids)
    screen_time.enforce(db, user, fresh=True)
    with write_transaction(db, name="music_receiver_renew"):
        ticket.expires_at = _expiry(tracks)
    return {"ticket_id": ticket.id, "expires_at": ticket.expires_at.isoformat() + "Z"}


def revoke_native(ticket_id: str, user: User, db: Session, *, issuer_device_id: str) -> Response:
    with write_transaction(db, name="music_receiver_revoke"):
        ticket = db.get(MusicReceiverTicket, ticket_id)
        if ticket is None or ticket.user_id != user.id or ticket.issuer_device_id != issuer_device_id:
            raise HTTPException(404, "Music receiver ticket not found")
        db.delete(ticket)
    return Response(status_code=204)


def audio(ticket_id: str, item_id: str, request: Request, token: str | None = Query(default=None, max_length=128),
          db: Session = Depends(get_db, scope="function")) -> Response:
    ticket, user, item, _track = _access(db, ticket_id, item_id, token)
    request.state.resource_user_id = user.id
    screen_time.enforce(db, user)
    try:
        playback = music_audio.playback_file(LibraryService(db).resolve_media_path(item), "chromecast")
    except FileNotFoundError as exc:
        raise HTTPException(404, "Track is unavailable") from exc
    if request.method != "HEAD":
        music_audio.guard_resources(db, request, user, item, playback, f"music-receiver:{ticket.id}")
    return music_audio.audio_response(playback, headers=CORS)


def register(app: FastAPI) -> None:
    app.get("/api/music/receivers/{ticket_id}/{item_id}/audio")(audio)
    app.head("/api/music/receivers/{ticket_id}/{item_id}/audio")(audio)
