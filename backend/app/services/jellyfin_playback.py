"""Jellyfin playback glue.

Composition only. The decision is pure ``decide()`` with ``playback_limits``,
sessions are the shared ``local_playback_sessions.sessions`` singleton, subtitle tracks and
segments come from their own services, and DTOs from the Jellyfin mapper.
"""
from __future__ import annotations

import contextlib
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models import LibraryItem, User
from app.routers.local_playback import playback_limits
from app.services import jellyfin as jf
from app.services.jellyfin_hls import keyframes
from app.services.library import LibraryService
from app.services.media_probe import MediaProbeService, media_tool
from app.services.media_titles import from_ticks, jellyfin_id, parse_item_id
from app.services.playback_decision import caps_from_device_profile, decide
from app.services.transcripts import TranscriptService, jellyfin_subtitle_streams

PLAY_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
PENDING_LIMIT = 256


@dataclass(frozen=True)
class PlaybackRequest:
    profile: dict[str, Any] | None
    max_bitrate: int | None
    start_ticks: int | None
    audio_index: int | None
    subtitle_index: int | None
    media_source_id: str | None  # dashed library item id


def _count(value: object) -> int | None:
    """A non-negative int from a JSON number or digit string; anything else (bool, -1 = "off", junk) is absent."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def playback_request(body: Mapping[str, Any] | None, query: Mapping[str, str]) -> PlaybackRequest:
    """PlaybackInfo input from the posted PlaybackInfoDto, falling back to (lowercased) query keys."""
    body = {str(key).lower(): value for key, value in body.items()} if isinstance(body, Mapping) else {}  # Streamyfin posts camelCase

    def pick(name: str) -> object:
        value = body.get(name.lower())
        return value if value is not None else query.get(name.lower())

    profile = body.get("deviceprofile")
    return PlaybackRequest(
        profile=profile if isinstance(profile, dict) else None,
        max_bitrate=_count(pick("MaxStreamingBitrate")),
        start_ticks=_count(pick("StartTimeTicks")),
        audio_index=_count(pick("AudioStreamIndex")),
        subtitle_index=_count(pick("SubtitleStreamIndex")),
        media_source_id=parse_item_id(pick("MediaSourceId")),
    )


def transcoding_fields(
    decision: Any, *, item_id: str, play_session_id: str, api_key: str,
    audio_index: int | None, subtitle_index: int | None,
) -> dict[str, Any]:
    """MediaSourceInfo fields for one decision. ``item_id`` is the Jellyfin hex of the version item."""
    if decision.mode == "direct":
        return {"SupportsDirectPlay": True, "SupportsDirectStream": True, "SupportsTranscoding": False}
    if decision.mode == "unavailable":  # the decision's reasons aren't Jellyfin TranscodeReasons; offer nothing new
        return {"SupportsTranscoding": False}
    params: dict[str, Any] = {"MediaSourceId": item_id, "PlaySessionId": play_session_id}
    if audio_index is not None:
        params["AudioStreamIndex"] = audio_index
    if subtitle_index is not None:
        params["SubtitleStreamIndex"] = subtitle_index
    if decision.height:
        params["MaxHeight"] = decision.height
    # No StartTimeTicks: Jellyfin never puts it in an HLS URL (StreamInfo.cs). The timeline is the whole file from 0 and the
    # client seeks to its resume point; the request's StartTimeTicks only picks where ffmpeg starts (PendingTranscode).
    if decision.reasons:  # Jellyfin keeps reasons out of the JSON ([JsonIgnore]) and in the URL
        params["TranscodeReasons"] = ",".join(decision.reasons)
    params["ApiKey"] = api_key  # AVPlayer drops headers on playlist and segment requests
    return {
        "SupportsDirectPlay": False,
        "SupportsDirectStream": False,
        "SupportsTranscoding": True,
        "TranscodingSubProtocol": "hls",
        "TranscodingContainer": "mp4",
        "TranscodingUrl": f"/videos/{item_id}/master.m3u8?{urlencode(params, safe=',')}",
    }


@dataclass(frozen=True)
class PendingTranscode:
    user_id: str
    device: str  # the Connected app id (device_tokens.id)
    item_id: str
    caps: Any  # ClientCaps from the PlaybackInfo DeviceProfile
    audio_index: int | None
    subtitle_index: int | None  # an embedded stream index (burned in when it is an image codec), else None
    max_bitrate: int | None
    start_seconds: float


class _PendingTranscodes:
    """PlaybackInfo's transcode inputs, keyed by PlaySessionId, so master.m3u8 can't be steered by URL edits.

    In-memory and bounded; after a restart master.m3u8 404s and the client asks
    PlaybackInfo again. Persist if a client turns out not to retry.
    """

    def __init__(self) -> None:
        self._items: OrderedDict[str, PendingTranscode] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, play_session_id: str, pending: PendingTranscode) -> None:
        key = f"{play_session_id}:{pending.item_id}"
        with self._lock:
            self._items[key] = pending
            self._items.move_to_end(key)
            while len(self._items) > PENDING_LIMIT:
                self._items.popitem(last=False)

    def get(self, play_session_id: str, item_id: str, user_id: str, device: str) -> PendingTranscode | None:
        with self._lock:
            pending = self._items.get(f"{play_session_id}:{item_id}")
        if pending is None or (pending.user_id, pending.device) != (user_id, device):
            return None
        return pending


pending_transcodes = _PendingTranscodes()


def external_subtitle_urls(sources: list[dict[str, Any]], profile: Mapping[str, Any]) -> None:
    """Point srt/vtt DeliveryUrls at a format the client's External SubtitleProfiles allow (the route converts)."""
    entries = profile.get("SubtitleProfiles")
    allowed = {
        token.strip().lower() for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict) and entry.get("Method") == "External" for token in str(entry.get("Format") or "").split(",")
    }
    for stream in (stream for source in sources for stream in source.get("MediaStreams") or []):
        stem, _, ext = str(stream.get("DeliveryUrl") or "").rpartition(".")
        other = {"srt": "vtt", "vtt": "srt"}.get(ext)
        if stream.get("DeliveryMethod") == "External" and other and ext not in allowed and other in allowed:
            stream["DeliveryUrl"] = f"{stem}.{other}"


def annotate_media_sources(
    db: Session, user: User, device: str, api_key: str, sources: list[dict[str, Any]],
    request: PlaybackRequest, play_session_id: str,
) -> None:
    """Fill each MediaSource's direct/transcode fields from ``decide()`` and remember the transcodes."""
    if request.profile is None:
        return  # GET PlaybackInfo or no DeviceProfile: direct-play defaults stand
    external_subtitle_urls(sources, request.profile)
    caps = caps_from_device_profile(request.profile)  # D: never raises; unknown conditions are ignored
    start_seconds = from_ticks(request.start_ticks) or 0.0
    library, probe = LibraryService(db), MediaProbeService(db)
    for source in sources:
        item_id = parse_item_id(source.get("Id"))
        item = library.get_item(item_id, user) if item_id else None
        if item is None:
            continue
        try:
            facts = probe.facts(item)
        except OSError:
            continue
        streams = facts.get("streams") or []
        if facts.get("error") or not streams:
            continue  # unprobed or unreadable: keep the direct-play defaults
        chosen = request.media_source_id in (None, item.id)
        audio = request.audio_index if chosen else None
        # External subtitles (sidecars, transcripts) index from sidecar_base up and are always delivered as sidecars.
        subtitle = request.subtitle_index if chosen and request.subtitle_index is not None and request.subtitle_index < jf.sidecar_base(facts) else None
        decision = decide(caps, facts, playback_limits(db, user.id, facts, max_bitrate=request.max_bitrate), audio, subtitle)
        source.update(transcoding_fields(
            decision, item_id=jellyfin_id(item.id), play_session_id=play_session_id, api_key=api_key,
            audio_index=audio, subtitle_index=subtitle,
        ))
        if decision.mode in ("remux", "transcode"):
            if decision.video == "copy" and (ffprobe := media_tool(db, "ffprobe")):  # the master playlist needs the cut points
                with contextlib.suppress(FileNotFoundError, ValueError):
                    keyframes(ffprobe, library.resolve_media_path(item), decision.video_index)
            pending_transcodes.put(play_session_id, PendingTranscode(
                user.id, device, item.id, caps, audio, subtitle, request.max_bitrate, start_seconds,
            ))


_FORMATS: dict[str, Literal["srt", "vtt"]] = {"srt": "srt", "subrip": "srt", "vtt": "vtt", "webvtt": "vtt"}


def transcript_base(probe: dict[str, Any], item: LibraryItem) -> int:
    """Index of the first transcript stream: after every embedded stream and every sidecar slot (append-only)."""
    return jf.sidecar_base(probe) + len(jf.sidecars(item))


def append_transcript_streams(db: Session, source: dict[str, Any], item: LibraryItem, probe: dict[str, Any]) -> None:
    """t: tracks as external subrip MediaStreams after the embedded and sidecar streams."""
    hex_id = jellyfin_id(item.id)
    source["MediaStreams"] = [*source.get("MediaStreams", []), *jellyfin_subtitle_streams(
        TranscriptService(db).subtitle_tracks(item.id), transcript_base(probe, item),
        lambda index: f"/Videos/{hex_id}/{hex_id}/Subtitles/{index}/0/Stream.srt",
    )]


def subtitle_format(fmt: str) -> Literal["srt", "vtt"] | None:
    """The route's ``stream.{fmt}`` suffix, from a closed set: never used to build a path."""
    return _FORMATS.get(fmt.lower())
