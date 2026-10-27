"""Revocable, receiver-scoped direct-video capabilities for Chromecast."""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeviceToken, MediaCastTicket, User, utcnow
from app.persistence import write_transaction
from app.routers import visible_item_or_404
from app.routers.local_playback import PlaybackSessionRequest, get_subtitle_vtt, list_subtitle_tracks, open_session, playback_limits
from app.security import get_current_user
from app.services import activity, jellyfin as jf, public_address, screen_time, subtitle_tracks
from app.services.connected_apps import jellyfin_user
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id
from app.services.local_playback_sessions import PlaybackSession, sessions
from app.services.media_probe import MediaProbeService
from app.services.media_response import MediaFileResponse
from app.services.playback_decision import ClientCaps, DirectProfile, IMAGE_SUBTITLE_CODECS, VideoCap, decide
from app.services.playback_resources import playback_resources
from app.services.transcripts import TranscriptService

TTL = timedelta(minutes=15)
NATIVE_TTL_MAX = timedelta(hours=8)
CHROMECAST_CAPS = ClientCaps(
    direct=(DirectProfile(frozenset({"mp4", "mov", "m4v"}), frozenset({"h264"}), frozenset({"aac"})),),
    video={"h264": VideoCap(max_height=1080, max_bit_depth=8, ranges=frozenset({"SDR"}))},
    audio=frozenset({"aac"}), max_channels=2, encode_channels=2, selects_audio=False, channel_limits={"aac": 2},
)
CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}
MPEGURL = "application/vnd.apple.mpegurl"


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


def _stop_conversion(ticket: MediaCastTicket) -> None:
    if ticket.playback_session_id:
        sessions.stop(ticket.playback_session_id, ticket.user_id)


def _discard_ticket(db: Session, ticket: MediaCastTicket, *, name: str) -> None:
    _stop_conversion(ticket)
    with write_transaction(db, name=name):
        db.delete(ticket)


def _access(db: Session, ticket_id: str, token: str | None) -> tuple[MediaCastTicket, User, object]:
    ticket = db.get(MediaCastTicket, ticket_id)
    if not token or ticket is None or ticket.expires_at <= utcnow() or not secrets.compare_digest(ticket.token_hash, _token_hash(token)):
        if ticket is not None and ticket.expires_at <= utcnow():
            _discard_ticket(db, ticket, name="chromecast_ticket_expire")
        raise _expired()
    user = db.get(User, ticket.user_id)
    if user is None or not user.is_active:
        _discard_ticket(db, ticket, name="chromecast_ticket_invalidate")
        raise _expired()
    if ticket.issuer_device_id is not None:
        issuer = db.get(DeviceToken, ticket.issuer_device_id)
        if issuer is None or issuer.user_id != ticket.user_id or issuer.kind != "jellyfin":
            _discard_ticket(db, ticket, name="chromecast_ticket_invalidate")
            raise _expired()
    return ticket, user, visible_item_or_404(db, ticket.item_id, user)


def _subtitle(db: Session, item_id: str, user: User, track_id: str | None):
    if track_id is None:
        return None
    track = next((candidate for candidate in list_subtitle_tracks(item_id, user, db) if candidate.id == track_id), None)
    if track is None:
        raise HTTPException(409, detail={"code": "subtitle_unavailable", "message": "The selected subtitle is no longer available."})
    return track


def _cast_context(db: Session, item, user: User, *, audio_index: int | None, subtitle: str | None) -> tuple[dict, object, object, bool]:  # noqa: ANN001
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Media file is not available") from exc
    if facts.get("error"):
        raise HTTPException(409, detail={"code": "conversion_unavailable", "message": "This file cannot be converted for Chromecast."})
    tracks = _subtitle(db, item.id, user, subtitle)
    # ffprobe reports MP4-family containers as a token list (normally mov,mp4,...).
    # Refuse files that do not identify as MP4 before the broader decision aliases apply.
    is_mp4 = "mp4" in {part.strip().lower() for part in str(facts.get("container") or "").split(",")}
    subtitle_index = int(subtitle[2:]) if subtitle and subtitle.startswith("e:") and subtitle[2:].isdigit() else None
    decision = decide(CHROMECAST_CAPS, facts, playback_limits(db, user.id, facts), audio_index, subtitle_index)
    direct = is_mp4 and decision.mode == "direct" and (tracks is None or tracks.format == "text")
    return facts, tracks, decision, direct


def _direct_or_409(db: Session, item, user: User, *, audio_index: int | None, subtitle: str | None) -> tuple[dict, object]:  # noqa: ANN001
    facts, tracks, decision, direct = _cast_context(db, item, user, audio_index=audio_index, subtitle=subtitle)
    if not direct:
        if tracks is not None and tracks.format != "text":
            raise HTTPException(409, detail={"code": "subtitle_unsupported", "message": "Chromecast direct playback supports text subtitles only."})
        secondary = "SecondaryAudioNotSupported" in decision.reasons
        code = "default_audio_override_unsupported" if secondary else "conversion_needed"
        message = "Chromecast direct playback cannot use this audio selection." if secondary else "This file needs conversion and cannot be cast directly."
        raise HTTPException(409, detail={"code": code, "message": message})
    return facts, tracks


def _response(ticket: MediaCastTicket, token: str, origin: str, item, facts: dict, position: float, subtitle) -> dict:  # noqa: ANN001
    base = f"{origin}/api/chromecast/{ticket.id}"
    encoded = quote(token, safe="")
    body = {
        "ticket_id": ticket.id, "media_url": f"{base}/media?token={encoded}",
        "content_type": MPEGURL if ticket.playback_session_id else "video/mp4", "title": item.title,
        "duration": facts.get("duration"), "position_seconds": position, "expires_at": ticket.expires_at.isoformat() + "Z",
        "qualification": "unverified_hardware",
    }
    # Bitmap tracks are already burned into a conversion. Only receiver-renderable
    # text tracks get a separate VTT capability.
    if subtitle is not None and subtitle.format == "text":
        body["subtitle"] = {"url": f"{base}/subtitle.vtt?token={encoded}", "language": subtitle.language, "label": subtitle.label}
    return body


def _expires_at(facts: dict, *, native: bool) -> datetime:
    if not native:
        return utcnow() + TTL
    duration = facts.get("duration")
    seconds = float(duration) if isinstance(duration, (int, float)) and float(duration) == float(duration) else None
    if seconds is None or seconds < 0 or seconds == float("inf") or seconds > NATIVE_TTL_MAX.total_seconds():
        return utcnow() + NATIVE_TTL_MAX
    return utcnow() + min(NATIVE_TTL_MAX, max(TTL, timedelta(seconds=seconds + TTL.total_seconds())))


def _discard_expired_tickets(db: Session) -> None:
    expired = db.query(MediaCastTicket).filter(MediaCastTicket.expires_at <= utcnow()).all()
    for ticket in expired:
        _stop_conversion(ticket)
        db.delete(ticket)


def _open_native_conversion(db: Session, request: Request, user: User, item, body: TicketBody,
                            ticket_id: str) -> PlaybackSession:
    # Use a full VOD timeline. The receiver selects its requested absolute start position;
    # LocalPlaybackSessions can restart a bounded ffmpeg run for a later HLS segment.
    burn_subtitle = body.subtitle if body.subtitle and body.subtitle.startswith("i:") else None
    payload = PlaybackSessionRequest(audio_index=body.audio_index, subtitle=burn_subtitle, max_height=1080)
    return open_session(
        db, user, item, caps=CHROMECAST_CAPS, request=payload, start=body.position_seconds,
        device=f"chromecast:{ticket_id}", play_session_id=ticket_id, seekable=True, http_request=request,
    )


def _mint(body: TicketBody, request: Request, user: User, db: Session, *, issuer_device_id: str | None = None) -> dict:
    origin = _origin()
    item = visible_item_or_404(db, body.item_id, user)
    screen_time.enforce(db, user, fresh=True)
    activity.guard(user.id, item.id, "chromecast")
    facts, subtitle, decision, direct = _cast_context(db, item, user, audio_index=body.audio_index, subtitle=body.subtitle)
    if not direct and issuer_device_id is None:
        # The browser sender remains a deliberately direct-only preview. Native receivers get
        # the server-owned conversion path below, never a browser credential in an HLS URL.
        _direct_or_409(db, item, user, audio_index=body.audio_index, subtitle=body.subtitle)
    token, ticket_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    ticket = MediaCastTicket(
        id=ticket_id, user_id=user.id, issuer_device_id=issuer_device_id, item_id=item.id, audio_index=body.audio_index,
        token_hash=_token_hash(token), subtitle=body.subtitle,
        expires_at=_expires_at(facts, native=issuer_device_id is not None),
    )
    conversion: PlaybackSession | None = None
    if not direct:
        if decision.mode == "unavailable":
            raise HTTPException(409, detail={"code": "conversion_unavailable", "message": "This file cannot be converted for Chromecast."})
        try:
            conversion = _open_native_conversion(db, request, user, item, body, ticket_id)
            ticket.playback_session_id = conversion.id
        except Exception:
            if conversion is not None:
                sessions.stop(conversion.id, user.id)
            raise
    try:
        with write_transaction(db, name="chromecast_ticket_mint"):
            _discard_expired_tickets(db)
            db.add(ticket)
    except Exception:
        _stop_conversion(ticket)
        raise
    return _response(ticket, token, origin, item, facts, body.position_seconds, subtitle)


def mint(body: TicketBody, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    if body.subtitle and body.subtitle.startswith("j:"):
        raise HTTPException(422, "Jellyfin stream selections are available only to paired native clients")
    return _mint(body, request, user, db)


def _renew(ticket_id: str, user: User, db: Session, *, issuer_device_id: str | None = None) -> dict:
    ticket = db.get(MediaCastTicket, ticket_id)
    if (
        ticket is None or ticket.user_id != user.id or ticket.expires_at <= utcnow()
        or (issuer_device_id is not None and ticket.issuer_device_id != issuer_device_id)
    ):
        raise HTTPException(404, "Chromecast ticket not found")
    item = visible_item_or_404(db, ticket.item_id, user)
    screen_time.enforce(db, user, fresh=True)
    activity.guard(user.id, item.id, "chromecast")
    if ticket.playback_session_id:
        facts, _subtitle_track, decision, direct = _cast_context(db, item, user, audio_index=ticket.audio_index, subtitle=ticket.subtitle)
        if decision.mode == "unavailable" or direct:
            raise HTTPException(409, detail={"code": "conversion_unavailable", "message": "This file can no longer be converted for Chromecast."})
    else:
        facts, _subtitle_track = _direct_or_409(db, item, user, audio_index=None, subtitle=ticket.subtitle)
    with write_transaction(db, name="chromecast_ticket_renew"):
        ticket.expires_at = _expires_at(facts, native=ticket.issuer_device_id is not None)
    return {"ticket_id": ticket.id, "expires_at": ticket.expires_at.isoformat() + "Z"}


def renew(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return _renew(ticket_id, user, db)


def _recreate_conversion(db: Session, request: Request, ticket: MediaCastTicket, user: User, item) -> PlaybackSession:  # noqa: ANN001
    if ticket.issuer_device_id is None:
        raise HTTPException(404, "Chromecast conversion not found")
    _facts, _subtitle_track, decision, direct = _cast_context(
        db, item, user, audio_index=ticket.audio_index, subtitle=ticket.subtitle,
    )
    if decision.mode == "unavailable" or direct:
        raise HTTPException(409, detail={"code": "conversion_unavailable", "message": "This file can no longer be converted for Chromecast."})
    body = TicketBody(item_id=item.id, position_seconds=0, audio_index=ticket.audio_index, subtitle=ticket.subtitle)
    conversion: PlaybackSession | None = None
    try:
        conversion = _open_native_conversion(db, request, user, item, body, ticket.id)
        with write_transaction(db, name="chromecast_ticket_recreate_conversion"):
            # open_session closes its caller's SQLAlchemy session before it waits for ffmpeg.
            # Re-fetch rather than mutating the now-detached ticket object.
            current = db.get(MediaCastTicket, ticket.id, populate_existing=True)
            if current is None or current.user_id != user.id or current.issuer_device_id != ticket.issuer_device_id:
                raise _expired()
            if current.expires_at <= utcnow():
                db.delete(current)
                raise _expired()
            current.playback_session_id = conversion.id
        ticket.playback_session_id = conversion.id
    except Exception:
        if conversion is not None:
            sessions.stop(conversion.id, user.id)
        raise
    return conversion


def _conversion_session(db: Session, request: Request, ticket: MediaCastTicket, user: User, item) -> PlaybackSession:  # noqa: ANN001
    if not ticket.playback_session_id:
        raise HTTPException(404, "Chromecast conversion not found")
    session = sessions.get(ticket.playback_session_id, user.id)
    if session is None:
        session = _recreate_conversion(db, request, ticket, user, item)
    if session.item_id != item.id:
        raise HTTPException(404, "Chromecast conversion not found")
    playback_resources.touch(session.resource_admission)
    if LibraryService(db).get_item(item.id, user) is None:
        sessions.stop(session.id, user.id)
        raise HTTPException(404, "Media file is not available")
    return session


def _ticketed_uri(uri: str, token: str) -> str:
    return f"{uri}{'&' if '?' in uri else '?'}token={quote(token, safe='')}"


def _ticketed_playlist(playlist: str, token: str) -> str:
    """Attach the receiver capability to every HLS child URI, including EXT-X-MAP."""
    lines = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            line = re.sub(r'URI="([^"]*)"', lambda match: f'URI="{_ticketed_uri(match[1], token)}"', line)
        elif line and not line.startswith("#"):
            line = _ticketed_uri(line, token)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _full_timeline_playlist(session: PlaybackSession, token: str) -> str:
    plan = session.plan
    if plan is None:
        path = sessions.file(session, "index.m3u8")
        if path is None:
            raise HTTPException(404, "Playback segment not found")
        return _ticketed_playlist(path.read_text(encoding="utf-8"), token)
    encoded = quote(token, safe="")
    lines = [
        "#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{max(1, int(max(plan.durations)) + 1)}",
        "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
        f'#EXT-X-MAP:URI="init.mp4?token={encoded}"',
    ]
    for index, duration in enumerate(plan.durations):
        lines += [f"#EXTINF:{duration:.6f},", f"seg{index}.m4s?token={encoded}"]
    return "\n".join([*lines, "#EXT-X-ENDLIST", ""])


def _conversion_master(ticket: MediaCastTicket, session: PlaybackSession, token: str) -> Response:
    return Response(
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n"
        # ``media`` lives one path component above the HLS files. Keep this relative
        # so a reverse-proxied receiver stays on its scoped capability origin.
        f"#EXT-X-STREAM-INF:BANDWIDTH={session.bandwidth}\nhls/index.m3u8?token={quote(token, safe='')}\n",
        media_type=MPEGURL, headers=CORS,
    )


def media(ticket_id: str, request: Request, token: str | None = Query(default=None, max_length=128), db: Session = Depends(get_db, scope="function")) -> Response:
    ticket, user, item = _access(db, ticket_id, token)
    assert token is not None
    screen_time.enforce(db, user)
    device = f"chromecast:{ticket.id}"
    activity.guard(user.id, item.id, device)
    if ticket.playback_session_id:
        return _conversion_master(ticket, _conversion_session(db, request, ticket, user, item), token)
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


def hls(ticket_id: str, name: str, request: Request, token: str | None = Query(default=None, max_length=128),
        db: Session = Depends(get_db, scope="function")) -> Response:
    ticket, user, item = _access(db, ticket_id, token)
    assert token is not None
    screen_time.enforce(db, user)
    activity.guard(user.id, item.id, f"chromecast:{ticket.id}")
    session = _conversion_session(db, request, ticket, user, item)
    if name == "index.m3u8":
        return Response(_full_timeline_playlist(session, token), media_type=MPEGURL, headers=CORS)
    path = sessions.segment(session, name)
    if path is None:
        raise HTTPException(404, "Playback segment not found")
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


def _revoke(ticket_id: str, user: User, db: Session, *, issuer_device_id: str | None = None) -> Response:
    with write_transaction(db, name="chromecast_ticket_revoke"):
        ticket = db.get(MediaCastTicket, ticket_id)
        if ticket is None or ticket.user_id != user.id or (issuer_device_id is not None and ticket.issuer_device_id != issuer_device_id):
            raise HTTPException(404, "Chromecast ticket not found")
        _stop_conversion(ticket)
        db.delete(ticket)
    return Response(status_code=204)


def revoke(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    return _revoke(ticket_id, user, db)


# This is mounted on the private Jellyfin surface. JellyfinPathMiddleware exposes
# it to paired native clients as /Lumina/Cast/... and applies the established
# MediaBrowser device-token authentication before these handlers run.
def _jellyfin_issuer(request: Request) -> str:
    issuer_device_id = getattr(request.state, "connected_app_id", None)
    if not isinstance(issuer_device_id, str):
        raise HTTPException(401, "Authentication required")
    return issuer_device_id


def _jellyfin_subtitle(body: TicketBody, user: User, db: Session) -> TicketBody:
    """Map a Jellyfin MediaStream index to Lumina's internal subtitle-track identity."""
    selected = body.subtitle
    if selected is None:
        return body
    match = re.fullmatch(r"j:(\d+)", selected)
    if match is None:
        raise HTTPException(422, "Invalid native subtitle selection")
    item = visible_item_or_404(db, body.item_id, user)
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Media file is not available") from exc
    index = int(match.group(1))
    embedded = next((stream for stream in facts.get("streams") or [] if stream.get("type") == "subtitle" and stream.get("index") == index), None)
    if embedded is not None:
        codec = str(embedded.get("codec") or "").lower()
        if codec in IMAGE_SUBTITLE_CODECS:
            translated = f"i:{index}"
        elif codec in subtitle_tracks.TEXT_CODECS:
            translated = f"e:{index}"
        else:
            raise HTTPException(409, detail={"code": "subtitle_unsupported", "message": "This subtitle cannot be cast."})
    else:
        sidecar_offset = index - jf.sidecar_base(facts)
        sidecars = subtitle_tracks.sidecars(item)
        if 0 <= sidecar_offset < len(sidecars) and sidecars[sidecar_offset] is not None:
            translated = f"s:{sidecar_offset}"
        else:
            transcript_offset = sidecar_offset - len(sidecars)
            transcripts = TranscriptService(db).subtitle_tracks(item.id)
            if not 0 <= transcript_offset < len(transcripts):
                raise HTTPException(409, detail={"code": "subtitle_unavailable", "message": "The selected subtitle is no longer available."})
            translated = transcripts[transcript_offset].id
    # Keep the one source of truth for sidecar/transcript availability and text/image semantics.
    _subtitle(db, item.id, user, translated)
    return body.model_copy(update={"subtitle": translated})


def jellyfin_mint(body: TicketBody, request: Request, user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> dict:
    # PlaybackInfo's MediaSourceId is the concrete version returned to native clients,
    # in Jellyfin's 32-hex spelling. Do not cast the title/entity id: it could select a
    # different preferred version than the one the local AVPlayer is handing off.
    item_id = parse_item_id(body.item_id)
    if item_id is None:
        raise HTTPException(404, "Media source not found")
    native_body = body.model_copy(update={"item_id": item_id})
    return _mint(_jellyfin_subtitle(native_body, user, db), request, user, db, issuer_device_id=_jellyfin_issuer(request))


def jellyfin_renew(ticket_id: str, request: Request, user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return _renew(ticket_id, user, db, issuer_device_id=_jellyfin_issuer(request))


def jellyfin_revoke(ticket_id: str, request: Request, user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> Response:
    return _revoke(ticket_id, user, db, issuer_device_id=_jellyfin_issuer(request))


def register(app: FastAPI) -> None:
    app.post("/api/chromecast/tickets")(mint)
    app.post("/api/chromecast/tickets/{ticket_id}/renew")(renew)
    app.get("/api/chromecast/{ticket_id}/media")(media)
    app.head("/api/chromecast/{ticket_id}/media")(media)
    app.get("/api/chromecast/{ticket_id}/hls/{name}")(hls)
    app.head("/api/chromecast/{ticket_id}/hls/{name}")(hls)
    app.get("/api/chromecast/{ticket_id}/subtitle.vtt")(subtitle)
    app.delete("/api/chromecast/tickets/{ticket_id}")(revoke)
    app.post("/jellyfin/lumina/cast/tickets")(jellyfin_mint)
    app.post("/jellyfin/lumina/cast/tickets/{ticket_id}/renew")(jellyfin_renew)
    app.delete("/jellyfin/lumina/cast/tickets/{ticket_id}")(jellyfin_revoke)
