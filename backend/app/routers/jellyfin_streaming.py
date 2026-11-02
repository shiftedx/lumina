"""Small native-only bridge for server-mediated remote playback."""
from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.routers.jellyfin_settings import native_streaming_enabled
from app.services import screen_time, streaming_gate
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/jellyfin/lumina/streaming")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]
_NATIVE_DEFAULT_PROFILES = ("mp4-avc-aac", "m4a-aac")


class PlaybackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=8, max_length=2048)
    supported_profiles: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("source_url")
    @classmethod
    def https_source(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("A public HTTPS source is required.")
        return value


class PlaybackResponse(BaseModel):
    status: Literal["ready", "unsupported"]
    session_id: str
    playback_url: str | None = None
    fallback_url: str
    seekable: bool
    live: bool


def _remote_enabled(db: Session, caller: JellyfinCaller) -> None:
    if not native_streaming_enabled(db, caller.user):
        raise HTTPException(status_code=403, detail="native_streaming_disabled")


def _descriptor(caller: JellyfinCaller, descriptor, source_url: str) -> PlaybackResponse:  # noqa: ANN001
    playback_url = None
    if descriptor.status == "ready" and descriptor.transport == "progressive":
        suffix = f"/Lumina/Streaming/playback/{descriptor.stream_id}/content"
        if descriptor.selected_rendition_id:
            suffix = f"/Lumina/Streaming/playback/{descriptor.stream_id}/renditions/{descriptor.selected_rendition_id}/content"
        playback_url = suffix + f"?api_key={quote(caller.token, safe='')}"
    elif descriptor.status == "ready" and descriptor.playback_url and "/relay/" in descriptor.playback_url:
        playback_url = f"/Lumina/Streaming/playback/{descriptor.stream_id}/relay/1/master.m3u8?api_key={quote(caller.token, safe='')}"
    elif descriptor.status == "ready" and descriptor.transport == "hls":
        playback_url = f"/Lumina/Streaming/playback/{descriptor.stream_id}/hls/1/manifest.m3u8?api_key={quote(caller.token, safe='')}"
    return PlaybackResponse(
        status="ready" if playback_url else "unsupported",
        session_id=descriptor.stream_id,
        playback_url=playback_url,
        fallback_url=source_url,
        seekable=bool(playback_url and descriptor.seekable),
        live=descriptor.live,
    )


@router.post("/playback", response_model=PlaybackResponse)
def start_playback(body: PlaybackBody, request: Request, caller: Caller, db: Db) -> PlaybackResponse:
    _remote_enabled(db, caller)
    enforce_rate_limit("remote_stream_register", request, user_id=caller.user.id)
    # A registration is a new playback start, not another fetch from an
    # already-authorized session.  Do not reuse the running-stream grace.
    screen_time.enforce(db, caller.user, fresh=True)
    streaming_gate.check_url(db, caller.user, body.source_url)
    # Import at request time: main owns the resolver/cache lifecycle and imports
    # the Jellyfin router while the application is constructed.
    from app.main import _browser_capabilities, _playback_ceiling, _playback_registrar, _require_provider_policy
    from app.services.yt_dlp_service import YtDlpService

    try:
        preview = YtDlpService(db).preview_for_playback(body.source_url, caller.user.id, True)
    except Exception as exc:  # provider failures must not leak upstream details
        raise HTTPException(status_code=502, detail="The streaming source could not be prepared.") from exc
    source_url = preview.webpage_url or body.source_url
    streaming_gate.check_url(
        db, caller.user, source_url,
        channels=(preview.raw.get("channel_url"), preview.raw.get("uploader_url")),
        live=preview.capabilities is not None and preview.capabilities.lifecycle == "live",
    )
    if preview.kind != "video" or (preview.capabilities is not None and not preview.capabilities.can_play):
        raise HTTPException(status_code=409, detail="This source cannot be played by the server.")
    _require_provider_policy(request, caller.user.id)
    registrar = _playback_registrar(preview)
    descriptor = registrar.register(
        owner_user_id=caller.user.id, source_url=source_url, info=preview.raw,
        browser_capabilities=_browser_capabilities(body.supported_profiles or list(_NATIVE_DEFAULT_PROFILES), _playback_ceiling(caller.user.id)),
    )
    return _descriptor(caller, descriptor, source_url)


def _stream(spec) -> StreamingResponse:  # noqa: ANN001
    from app.main import ClosingStreamingResponse

    return ClosingStreamingResponse(spec.body, status_code=spec.status_code, headers=spec.headers, close=spec.close)


_LOCAL_STREAM_PATH = re.compile(rb"/api/remote-streams/[A-Za-z0-9_-]+/(?:hls|relay)/[0-9]+/(?:r/[A-Za-z0-9_-]+|[A-Za-z0-9._-]+)")


def _native_manifest(spec, caller: JellyfinCaller) -> StreamingResponse:  # noqa: ANN001
    """Replace the browser-only relay routes with same-origin native routes.

    The only bearer here is the connected-app token in a Lumina URL. Rewritten
    manifests never contain the upstream URL, cookies, or provider credentials.
    """
    headers = dict(spec.headers)
    location = headers.get("Location")
    if location and location.startswith("/api/remote-streams/"):
        headers["Location"] = location.replace("/api/remote-streams/", "/Lumina/Streaming/playback/", 1) + f"?api_key={quote(caller.token, safe='')}"
    content_type = headers.get("Content-Type", "")
    if "mpegurl" not in content_type:
        from app.main import ClosingStreamingResponse

        return ClosingStreamingResponse(spec.body, status_code=spec.status_code, headers=headers, close=spec.close)
    try:
        body = b"".join(spec.body)
    finally:
        spec.close()
    capability = quote(caller.token, safe="").encode()
    body = _LOCAL_STREAM_PATH.sub(lambda match: match.group(0).replace(b"/api/remote-streams/", b"/Lumina/Streaming/playback/") + b"?api_key=" + capability, body)
    headers["Content-Length"] = str(len(body))
    return Response(body, status_code=spec.status_code, headers=headers, media_type=content_type)


@router.get("/playback/{stream_id}/content")
def content(stream_id: str, request: Request, caller: Caller, db: Db,
            range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    _remote_enabled(db, caller)
    enforce_rate_limit("remote_stream_content", request, user_id=caller.user.id)
    screen_time.enforce(db, caller.user)
    from app.main import remote_streams

    return _stream(remote_streams.serve_content(caller.user.id, stream_id, range_header))


@router.get("/playback/{stream_id}/renditions/{rendition_id}/content")
def rendition_content(stream_id: str, rendition_id: str, request: Request, caller: Caller, db: Db,
                      range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    _remote_enabled(db, caller)
    enforce_rate_limit("remote_stream_content", request, user_id=caller.user.id)
    screen_time.enforce(db, caller.user)
    from app.main import remote_streams

    return _stream(remote_streams.serve_content(caller.user.id, stream_id, range_header, rendition_id=rendition_id))


@router.get("/playback/{stream_id}/hls/{generation}/{resource}")
def hls_content(stream_id: str, generation: int, resource: str, request: Request, caller: Caller, db: Db,
                range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    _remote_enabled(db, caller)
    screen_time.enforce(db, caller.user)
    from app.main import remote_streams

    if resource == "manifest.m3u8" and not remote_streams.has_presentation(caller.user.id, stream_id, generation):
        enforce_rate_limit("remote_stream_package", request, user_id=caller.user.id)
    else:
        enforce_rate_limit("remote_stream_content", request, user_id=caller.user.id)
    return _native_manifest(remote_streams.serve_hls(caller.user.id, stream_id, generation, resource, range_header), caller)


@router.get("/playback/{stream_id}/relay/{generation}/master.m3u8")
def relay_master(stream_id: str, generation: int, request: Request, caller: Caller, db: Db) -> StreamingResponse:
    _remote_enabled(db, caller)
    screen_time.enforce(db, caller.user)
    enforce_rate_limit("remote_stream_package", request, user_id=caller.user.id)
    from app.main import _relay_service_for

    return _native_manifest(_relay_service_for(caller.user.id, stream_id).serve_master(caller.user.id, stream_id, generation), caller)


@router.get("/playback/{stream_id}/relay/{generation}/r/{resource_id}")
def relay_resource(stream_id: str, generation: int, resource_id: str, request: Request, caller: Caller, db: Db,
                   range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    _remote_enabled(db, caller)
    screen_time.enforce(db, caller.user)
    enforce_rate_limit("remote_stream_content", request, user_id=caller.user.id)
    from app.main import _relay_service_for

    return _native_manifest(_relay_service_for(caller.user.id, stream_id).serve_resource(caller.user.id, stream_id, generation, resource_id, range_header), caller)


@router.delete("/playback/{stream_id}", status_code=204)
def stop_playback(stream_id: str, caller: Caller) -> None:
    from app.main import _owning_stream_service

    try:
        _owning_stream_service(caller.user.id, stream_id).release(caller.user.id, stream_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Playback session not found.") from exc
