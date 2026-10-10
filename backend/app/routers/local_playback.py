"""Local library playback options, remux/transcode HLS sessions and their playback decisions."""
from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import AudioTrack, PlaybackSessionRequest, SubtitleTrack
from app.models import AppSettings, LibraryItem, Transcript, User
from app.routers import visible_item_or_404
from app.security import get_current_user
from app.services import activity, profanity, screen_time, subtitle_tracks
from app.services.hwaccel import hwaccel
from app.services.jellyfin_hls import plan_for
from app.services.library import LibraryService
from app.services.local_playback_sessions import ConversionError, PlaybackSession, SessionCapError, SupersededError, sessions
from app.services.media_artifacts import MediaArtifactService
from app.services.media_probe import MediaProbeService, loudness_gain_db, media_tool
from app.services.media_response import MediaFileResponse
from app.services.playback_decision import RUNGS, ClientCaps, Limits, caps_from_browser_profiles, decide
from app.services.playback_log import log_playback
from app.services.probe_warming import probe_warming
from app.services.transcripts import TranscriptService
from app.services.user_settings import playback_ceiling

# Fixed text: an OSError raised mid-probe can name a server path.
MEDIA_UNAVAILABLE = "Media file is not available."
MAX_PROFILES = 32


class MediaFacts(BaseModel):
    container: str
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    audio_tracks: int = 0
    subtitles: list[str] = []


class LocalPlaybackResponse(BaseModel):
    mode: Literal["direct", "remux", "transcode", "unavailable"]
    reason: str | None = None
    facts: MediaFacts | None = None
    audio_tracks: list[AudioTrack] = []
    quality_heights: list[int] = []  # the Quality menu's ladder rungs below the source height
    loudness_gain_db: float | None = None  # applied client-side by audioGraph.ts; None = not analysed
    free_video_slots: int = 0  # max_playback_sessions - video_encodes(), never negative


class PlaybackSessionResponse(BaseModel):
    session_id: str
    mode: Literal["remux", "transcode"]
    playback_url: str
    start: float = 0
    kind: Literal["remux", "audio", "video_sw", "video_hw"] = "remux"  # labels time to first frame


def browser_caps(profiles: str | None) -> ClientCaps:
    return caps_from_browser_profiles((profiles or "").split(",")[:MAX_PROFILES])


def session_cap(record: AppSettings | None) -> int:
    """Concurrent video encodes the admin allows (AppSettings.max_playback_sessions, default 2)."""
    return record.max_playback_sessions if record and record.max_playback_sessions else 2


def audio_tracks(facts: dict[str, Any]) -> list[AudioTrack]:
    audios = [s for s in facts.get("streams") or [] if s.get("type") == "audio"]
    default = next((s["index"] for s in audios if s.get("default")), audios[0]["index"] if audios else None)
    return [
        AudioTrack(
            index=s["index"], language=s.get("language"), codec=s.get("codec"), channels=s.get("channels"), default=s["index"] == default,
            label=" · ".join(filter(None, [s.get("title") or (s.get("language") or "").upper() or f"Track {n}", s.get("channel_layout"), (s.get("codec") or "").upper()])),
        )
        for n, s in enumerate(audios, 1)
    ]


def quality_heights(facts: dict[str, Any], ceiling: int | None) -> list[int]:
    """The Quality menu's rungs below the source (the menu adds Original)."""
    height = facts.get("height") or 0
    return [rung for rung in sorted(RUNGS, reverse=True) if rung < height and (ceiling is None or rung <= ceiling)]


def playback_limits(db: Session, user_id: str, facts: dict[str, Any], max_height: int | None = None, max_bitrate: int | None = None) -> Limits:
    """The member ceiling and chosen quality/bitrate, plus whether Dolby Vision profile 5 can be reshaped here."""
    heights = [h for h in (playback_ceiling(db, user_id), max_height) if h]
    dv5 = any(s.get("dv_profile") == 5 for s in facts.get("streams") or [])
    if dv5:  # only then is the hardware probe worth waiting for
        record = db.get(AppSettings, 1)
        dv5 = hwaccel.choice(media_tool(db, "ffmpeg"), record.hwaccel if record else "auto")[1] == "opencl"
    return Limits(max_height=min(heights) if heights else None, max_bitrate=max_bitrate, dv5_reshape=dv5)


def _version(db: Session, user: User, item: LibraryItem, version_id: str | None) -> LibraryItem:
    """The item itself, or a visible sibling version of the same Media title."""
    if not version_id or version_id == item.id:
        return item
    other = visible_item_or_404(db, version_id, user)
    if item.title_id is None or other.title_id != item.title_id or other.extra_type is not None:
        raise HTTPException(status_code=404, detail="Library item not found")
    return other


def get_local_playback(item_id: str, profiles: str | None = Query(None, max_length=2048), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LocalPlaybackResponse:
    item = visible_item_or_404(db, item_id, current_user)
    screen_time.enforce(db, current_user, fresh=True)  # the web player asks this before every start, direct play too
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE) from exc
    if facts.get("error"):
        return LocalPlaybackResponse(mode="unavailable", reason=facts["error"])
    if "loudness" not in facts:
        probe_warming.prioritize(item.id)  # someone is about to watch it: measure it next
    limits = playback_limits(db, current_user.id, facts)
    decision = decide(browser_caps(profiles), facts, limits)
    return LocalPlaybackResponse(
        mode=decision.mode, reason=",".join(decision.reasons) or None, facts=MediaFacts(**facts),
        audio_tracks=audio_tracks(facts), quality_heights=quality_heights(facts, limits.max_height),
        loudness_gain_db=loudness_gain_db(facts.get("loudness")),
        free_video_slots=max(0, session_cap(db.get(AppSettings, 1)) - sessions.video_encodes()),
    )


def open_session(
    db: Session, user: User, item: LibraryItem, *, caps: ClientCaps, request: PlaybackSessionRequest | None = None, start: float = 0,
    device: str = "web", play_session_id: str | None = None, max_bitrate: int | None = None, audio_gain_db: float | None = None,
    seekable: bool = False,
) -> PlaybackSession:
    """Start or reuse the conversion of one visible item (web route and Jellyfin master.m3u8). Closes ``db`` before waiting.

    ``seekable`` (Jellyfin clients) lists the whole file's timeline and starts ffmpeg at ``start``, as Jellyfin does; a
    copy whose keyframes are not known in time keeps the growing playlist, from 0."""
    request = request or PlaybackSessionRequest()
    item = _version(db, user, item, request.version_id)
    activity.guard(user.id, item.id, device)  # the owner stopped this: no new conversion for 120 s
    screen_time.enforce(db, user, fresh=True)  # viewing hours and the daily limit (web and Jellyfin master.m3u8)
    try:
        facts = MediaProbeService(db).facts(item)
        source, _root = MediaArtifactService(db).locate(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE) from exc
    if facts.get("error"):
        raise HTTPException(status_code=409, detail="This file has no playable audio or video.")
    subtitle = request.subtitle or ""
    subtitle_index = int(subtitle[2:]) if subtitle.startswith("i:") else None  # text tracks are sidecars, never burned in
    decision = decide(caps, facts, playback_limits(db, user.id, facts, request.max_height, max_bitrate), request.audio_index, subtitle_index)
    if decision.mode == "direct":
        raise HTTPException(status_code=409, detail="This file does not need conversion.")
    if decision.mode == "unavailable":
        raise HTTPException(status_code=409, detail="This file cannot be converted for playback on this server.")
    ffmpeg = media_tool(db, "ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg is not available on this server.")
    ffprobe = media_tool(db, "ffprobe")
    record = db.get(AppSettings, 1)
    cap = session_cap(record)
    hwaccel_mode = record.hwaccel if record else "auto"
    min_free = (record.min_free_disk_mb if record else 0) * 1024 * 1024
    cache_cap = (record.transcode_cache_gb if record else 10) * 1024**3
    user_id, item_id = user.id, item.id
    db.close()  # the manifest wait below must not hold a database session
    started = time.monotonic()
    plan = plan_for(decision.video, ffprobe, source, facts, decision.video_index, start) if seekable else None
    try:
        session = sessions.start(
            user_id=user_id, item_id=item_id, ffmpeg=ffmpeg, source=source, facts=facts, decision=decision, cap=cap,
            start=0 if seekable else start, plan=plan, resume=start if plan else 0,
            device=device, play_session_id=play_session_id, hwaccel_mode=hwaccel_mode, min_free_bytes=min_free,
            cache_cap_bytes=cache_cap, audio_gain_db=audio_gain_db, ffprobe=ffprobe,
        )
    except SupersededError as exc:  # the member's newer request owns the device slot; this caller already moved on
        log_playback("local_playback.start", "superseded", item=item_id, mode=decision.mode, duration_ms=round((time.monotonic() - started) * 1000))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SessionCapError, ConversionError) as exc:
        log_playback("local_playback.start", "busy" if isinstance(exc, SessionCapError) else "failed", item=item_id, mode=decision.mode, duration_ms=round((time.monotonic() - started) * 1000), error=f'"{exc}"')
        raise HTTPException(status_code=429 if isinstance(exc, SessionCapError) else 502, detail=str(exc)) from exc
    log_playback("local_playback.start", "started", playback=session.id[:8], item=item_id, mode=session.mode, kind=session.kind, duration_ms=round((time.monotonic() - started) * 1000))
    return session


def start_playback_session(
    item_id: str, request: Request, payload: PlaybackSessionRequest | None = Body(None), start: float = Query(0, ge=0, le=86400 * 7),
    profiles: str | None = Query(None, max_length=2048), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> PlaybackSessionResponse:
    item = visible_item_or_404(db, item_id, current_user)
    device = getattr(request.state, "connected_app_id", None) or "web"  # Set for connected-app tokens
    session = open_session(db, current_user, item, caps=browser_caps(profiles), request=payload, start=start, device=device)
    return PlaybackSessionResponse(session_id=session.id, mode=session.mode, playback_url=f"/api/playback-sessions/{session.id}/index.m3u8", start=session.media_start, kind=session.kind)


def get_playback_session_file(session_id: str, name: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MediaFileResponse:
    session = sessions.get(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Playback session not found")
    # Every file (not just the manifest) re-checks access: one indexed lookup.
    if LibraryService(db).get_item(session.item_id, current_user) is None:
        sessions.stop(session_id)  # access was revoked since the session started
        raise HTTPException(status_code=404, detail="Playback session not found")
    screen_time.enforce(db, current_user)
    path = sessions.file(session, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Playback segment not found")
    if name.endswith(".m3u8"):  # read whole: ffmpeg renames a new one over it, so a stat then open could mix two files
        return Response(path.read_bytes(), media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
    if name == "init.mp4":  # small, and a restart may rewrite it: read it whole, never stat-then-open
        return Response(path.read_bytes(), media_type="video/mp4", headers={"Cache-Control": "no-store"})
    return MediaFileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=600"})


def stop_playback_session(session_id: str, current_user: User = Depends(get_current_user)) -> None:
    if sessions.stop(session_id, current_user.id):
        log_playback("local_playback.stop", "stopped", playback=session_id[:8])


def list_subtitle_tracks(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[SubtitleTrack]:
    item = visible_item_or_404(db, item_id, current_user)
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE) from exc
    return subtitle_tracks.list_tracks(facts, item) + TranscriptService(db).subtitle_tracks(item.id)  # t: tracks append


def get_subtitle_vtt(item_id: str, track_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    item = visible_item_or_404(db, item_id, current_user)
    word_filter = profanity.member_filter(db, current_user)  # read before db.close(); driven by the pref, never the URL
    if track_id.startswith("t:"):
        transcript = db.get(Transcript, track_id[2:])
        if transcript is None or transcript.library_item_id != item.id:
            raise HTTPException(status_code=404, detail="Subtitle track not found")
        body = TranscriptService(db).render_transcript(transcript.id, "vtt")
        body = profanity.mask_vtt(body, word_filter) if word_filter is not None else body
        return Response(body, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, no-cache"})
    entries = subtitle_tracks.sidecars(item)  # read before a probe commit expires the row
    artifacts = MediaArtifactService(db)
    try:
        facts = MediaProbeService(db).facts(item)
        source, root = artifacts.locate(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE) from exc
    artifact, _ = artifacts.artifact_for(item.id)
    artifact_id, fingerprint = artifact.id, (artifact.probe or {}).get("fingerprint") or "unknown"
    ffmpeg = media_tool(db, "ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg is not available on this server.")
    db.close()  # extracting an embedded track can read the whole file
    try:
        body = subtitle_tracks.vtt_body(track_id, facts=facts, sidecar_entries=entries, source=source, root=root, artifact_id=artifact_id, fingerprint=fingerprint, ffmpeg=ffmpeg)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Subtitle track not found") from exc
    except subtitle_tracks.SubtitleError as exc:
        raise HTTPException(status_code=502, detail="This subtitle track could not be converted.") from exc
    if word_filter is not None:
        body = profanity.mask_vtt(body, word_filter)
    return Response(body, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, no-cache"})


def register(app: FastAPI) -> None:
    app.get("/api/library/{item_id}/playback-options", response_model=LocalPlaybackResponse)(get_local_playback)
    app.post("/api/library/{item_id}/playback-sessions", response_model=PlaybackSessionResponse, status_code=201)(start_playback_session)
    app.get("/api/playback-sessions/{session_id}/{name}")(get_playback_session_file)
    app.delete("/api/playback-sessions/{session_id}", status_code=204)(stop_playback_session)
    app.get("/api/library/{item_id}/subtitle-tracks", response_model=list[SubtitleTrack])(list_subtitle_tracks)
    app.get("/api/library/{item_id}/subtitle-tracks/{track_id}.vtt")(get_subtitle_vtt)
