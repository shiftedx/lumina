"""Revocable, receiver-scoped direct-video capabilities for Chromecast."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MediaCastTicket, User, utcnow
from app.persistence import write_transaction
from app.routers import visible_item_or_404
from app.routers.local_playback import get_subtitle_vtt, list_subtitle_tracks, playback_limits
from app.security import get_current_user
from app.services import activity, public_address, screen_time
from app.services.library import LibraryService
from app.services.media_probe import MediaProbeService
from app.services.media_response import MediaFileResponse
from app.services.playback_decision import ClientCaps, DirectProfile, VideoCap, decide
from app.services.playback_resources import playback_resources

TTL = timedelta(minutes=15)
CHROMECAST_CAPS = ClientCaps(
    direct=(DirectProfile(frozenset({"mp4", "mov", "m4v"}), frozenset({"h264"}), frozenset({"aac"})),),
    video={"h264": VideoCap(max_height=1080, max_bit_depth=8, ranges=frozenset({"SDR"}))},
    audio=frozenset({"aac"}), max_channels=2, selects_audio=False, channel_limits={"aac": 2},
)
CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


class TicketBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    item_id: str = Field(min_length=1, max_length=64)
    position_seconds: float = Field(ge=0, le=604800)
    audio_index: int | None = Field(default=None, ge=0)
    subtitle: str | None = Field(default=None, min_length=1, max_length=128)


def _expired() -> HTTPException:
    return HTTPException(401, "Expired Chromecast access")


def _origin() -> str:
    origin = public_address.origin() or public_address.local_origin()
    if origin is None:
        raise HTTPException(409, detail={"code": "cast_address_required", "message": "Configure a reachable public or local Lumina address before casting."})
    return origin


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _access(db: Session, ticket_id: str, token: str | None) -> tuple[MediaCastTicket, User, object]:
    ticket = db.get(MediaCastTicket, ticket_id)
    if not token or ticket is None or ticket.expires_at <= utcnow() or not secrets.compare_digest(ticket.token_hash, _token_hash(token)):
        raise _expired()
    user = db.get(User, ticket.user_id)
    if user is None or not user.is_active:
        raise _expired()
    return ticket, user, visible_item_or_404(db, ticket.item_id, user)


def _subtitle(db: Session, item_id: str, user: User, track_id: str | None):
    if track_id is None:
        return None
    track = next((candidate for candidate in list_subtitle_tracks(item_id, user, db) if candidate.id == track_id), None)
    if track is None:
        raise HTTPException(409, detail={"code": "subtitle_unavailable", "message": "The selected subtitle is no longer available."})
    if track.format != "text":
        raise HTTPException(409, detail={"code": "subtitle_unsupported", "message": "Chromecast direct playback supports text subtitles only."})
    return track


def _direct_or_409(db: Session, item, user: User, *, audio_index: int | None, subtitle: str | None) -> tuple[dict, object]:  # noqa: ANN001
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Media file is not available") from exc
    if facts.get("error"):
        raise HTTPException(409, detail={"code": "conversion_needed", "message": "This file needs conversion and cannot be cast directly."})
    tracks = _subtitle(db, item.id, user, subtitle)
    # ffprobe reports MP4-family containers as a token list (normally mov,mp4,...).
    # Refuse files that do not identify as MP4 before the broader decision aliases apply.
    is_mp4 = "mp4" in {part.strip().lower() for part in str(facts.get("container") or "").split(",")}
    subtitle_index = int(subtitle[2:]) if subtitle and subtitle.startswith("e:") and subtitle[2:].isdigit() else None
    decision = decide(CHROMECAST_CAPS, facts, playback_limits(db, user.id, facts), audio_index, subtitle_index)
    if not is_mp4 or decision.mode != "direct":
        secondary = "SecondaryAudioNotSupported" in decision.reasons
        code = "default_audio_override_unsupported" if secondary else "conversion_needed"
        message = "Chromecast direct playback cannot use this audio selection." if secondary else "This file needs conversion and cannot be cast directly."
        raise HTTPException(409, detail={"code": code, "message": message})
    return facts, tracks


def _response(ticket: MediaCastTicket, token: str, origin: str, item, facts: dict, position: float, subtitle) -> dict:  # noqa: ANN001
    base = f"{origin}/api/chromecast/{ticket.id}"
    encoded = quote(token, safe="")
    body = {
        "ticket_id": ticket.id, "media_url": f"{base}/media?token={encoded}", "content_type": "video/mp4", "title": item.title,
        "duration": facts.get("duration"), "position_seconds": position, "expires_at": ticket.expires_at.isoformat() + "Z",
        "qualification": "unverified_hardware",
    }
    if subtitle is not None:
        body["subtitle"] = {"url": f"{base}/subtitle.vtt?token={encoded}", "language": subtitle.language, "label": subtitle.label}
    return body


def mint(body: TicketBody, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    origin = _origin()
    item = visible_item_or_404(db, body.item_id, user)
    screen_time.enforce(db, user, fresh=True)
    activity.guard(user.id, item.id, "chromecast")
    facts, subtitle = _direct_or_409(db, item, user, audio_index=body.audio_index, subtitle=body.subtitle)
    token, ticket_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    ticket = MediaCastTicket(id=ticket_id, user_id=user.id, item_id=item.id, token_hash=_token_hash(token), subtitle=body.subtitle,
                             expires_at=utcnow() + TTL)
    with write_transaction(db, name="chromecast_ticket_mint"):
        db.execute(delete(MediaCastTicket).where(MediaCastTicket.expires_at <= utcnow()))
        db.add(ticket)
    return _response(ticket, token, origin, item, facts, body.position_seconds, subtitle)


def renew(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    ticket = db.get(MediaCastTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id or ticket.expires_at <= utcnow():
        raise HTTPException(404, "Chromecast ticket not found")
    item = visible_item_or_404(db, ticket.item_id, user)
    screen_time.enforce(db, user, fresh=True)
    activity.guard(user.id, item.id, "chromecast")
    _direct_or_409(db, item, user, audio_index=None, subtitle=ticket.subtitle)
    with write_transaction(db, name="chromecast_ticket_renew"):
        ticket.expires_at = utcnow() + TTL
    return {"ticket_id": ticket.id, "expires_at": ticket.expires_at.isoformat() + "Z"}


def media(ticket_id: str, request: Request, token: str | None = Query(default=None, max_length=128), db: Session = Depends(get_db, scope="function")) -> MediaFileResponse:
    ticket, user, item = _access(db, ticket_id, token)
    screen_time.enforce(db, user)
    device = f"chromecast:{ticket.id}"
    activity.guard(user.id, item.id, device)
    try:
        facts = MediaProbeService(db).facts(item)
        path = LibraryService(db).resolve_media_path(item)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Media file is not available") from exc
    is_mp4 = "mp4" in {part.strip().lower() for part in str(facts.get("container") or "").split(",")}
    decision = decide(CHROMECAST_CAPS, facts, playback_limits(db, user.id, facts))
    if not is_mp4 or decision.mode != "direct":
        raise HTTPException(409, detail={"code": "conversion_needed", "message": "This file can no longer be cast directly."})
    admission = playback_resources.admit_direct(db, user, request, item.id, device, facts, CHROMECAST_CAPS)
    if admission is not None and admission.decision.mode != "direct":
        playback_resources.release(admission)
        raise HTTPException(409, detail={"code": "conversion_needed", "message": "This file cannot fit the current household playback policy."})
    request.state.playback_admission = admission
    if request.method == "GET":
        activity.touch(user.id, item.id, device, duration=facts.get("duration"))
    return MediaFileResponse(path, media_type="video/mp4", headers=CORS)


def subtitle(ticket_id: str, token: str | None = Query(default=None, max_length=128), db: Session = Depends(get_db, scope="function")) -> Response:
    ticket, user, item = _access(db, ticket_id, token)
    if ticket.subtitle is None:
        raise HTTPException(404, "Subtitle track not found")
    _subtitle(db, item.id, user, ticket.subtitle)
    screen_time.enforce(db, user)
    response = get_subtitle_vtt(item.id, ticket.subtitle, user, db)
    response.headers.update(CORS)
    return response


def revoke(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    with write_transaction(db, name="chromecast_ticket_revoke"):
        ticket = db.get(MediaCastTicket, ticket_id)
        if ticket is None or ticket.user_id != user.id:
            raise HTTPException(404, "Chromecast ticket not found")
        db.delete(ticket)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.post("/api/chromecast/tickets")(mint)
    app.post("/api/chromecast/tickets/{ticket_id}/renew")(renew)
    app.get("/api/chromecast/{ticket_id}/media")(media)
    app.head("/api/chromecast/{ticket_id}/media")(media)
    app.get("/api/chromecast/{ticket_id}/subtitle.vtt")(subtitle)
    app.delete("/api/chromecast/tickets/{ticket_id}")(revoke)
