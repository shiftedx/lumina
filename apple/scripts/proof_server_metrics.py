"""Metrics and fault-injection seams for the disposable native proof server."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from proof_server_fixtures import (
    ARTWORK_METRICS_PATH_ENV,
    MAX_ARTWORK_METRICS_BYTES,
    MAX_PERFORMANCE_CATALOGUE_COUNT,
    MAX_PERFORMANCE_POSTER_VARIANTS,
    MAX_PERFORMANCE_REQUEST_LOG_BYTES,
    MAX_PLAYBACK_INFO_DELAY_SECONDS,
    PERFORMANCE_CATALOGUE_COUNT_ENV,
    PERFORMANCE_POSTER_VARIANTS_ENV,
    PERFORMANCE_REQUEST_LOG_ENV,
    PERFORMANCE_VIDEO_SIZES,
    PERFORMANCE_VIDEO_SIZE_ENV,
    PLAYBACK_INFO_DELAY_ENV,
)

def performance_catalogue_count(env: str = PERFORMANCE_CATALOGUE_COUNT_ENV) -> int:
    """Return the opt-in size of the disposable high-density catalog."""
    raw = os.environ.get(env, "0")
    try:
        count = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{env} must be an integer from 0 to {MAX_PERFORMANCE_CATALOGUE_COUNT}.") from error
    if not 0 <= count <= MAX_PERFORMANCE_CATALOGUE_COUNT:
        raise RuntimeError(f"{env} must be from 0 to {MAX_PERFORMANCE_CATALOGUE_COUNT}.")
    return count


def performance_poster_variant_count() -> int:
    """Return the opt-in number of local artwork variants for poster-wall profiling."""
    raw = os.environ.get(PERFORMANCE_POSTER_VARIANTS_ENV, "0")
    try:
        count = int(raw)
    except ValueError as error:
        raise RuntimeError(
            f"{PERFORMANCE_POSTER_VARIANTS_ENV} must be an integer from 0 to {MAX_PERFORMANCE_POSTER_VARIANTS}."
        ) from error
    if not 0 <= count <= MAX_PERFORMANCE_POSTER_VARIANTS:
        raise RuntimeError(f"{PERFORMANCE_POSTER_VARIANTS_ENV} must be from 0 to {MAX_PERFORMANCE_POSTER_VARIANTS}.")
    return count


def performance_video_size() -> str:
    size = os.environ.get(PERFORMANCE_VIDEO_SIZE_ENV, "320x180")
    if size not in PERFORMANCE_VIDEO_SIZES:
        choices = ", ".join(sorted(PERFORMANCE_VIDEO_SIZES))
        raise RuntimeError(f"{PERFORMANCE_VIDEO_SIZE_ENV} must be one of: {choices}.")
    return size


def playback_info_delay_seconds(value: str | None = None) -> float:
    """Read the broker-only delay used by cancellation journeys; unset is off."""
    raw = os.environ.get(PLAYBACK_INFO_DELAY_ENV) if value is None else value
    if raw in (None, ""):
        return 0
    try:
        seconds = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{PLAYBACK_INFO_DELAY_ENV} must be a number from 0 to {MAX_PLAYBACK_INFO_DELAY_SECONDS}.") from error
    if not 0 <= seconds <= MAX_PLAYBACK_INFO_DELAY_SECONDS:
        raise RuntimeError(f"{PLAYBACK_INFO_DELAY_ENV} must be from 0 to {MAX_PLAYBACK_INFO_DELAY_SECONDS}.")
    return seconds


def install_playback_info_delay(app: Any, seconds: float) -> None:
    """Delay only authenticated PlaybackInfo negotiation in the disposable fixture."""
    if seconds == 0:
        return

    @app.middleware("http")
    async def delay_playback_info(request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path.split("/")
        if request.method == "POST" and len(path) == 4 and path[1] == "Items" and path[3] == "PlaybackInfo":
            await asyncio.sleep(seconds)
        return await call_next(request)


def install_audio_format_failure(app: Any) -> None:
    """Make direct audio undecodable while keeping the real compatible conversion available."""
    @app.middleware("http")
    async def unsupported_direct_audio(request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path.lower()
        if request.method == "GET" and path.startswith("/audio/") and path.endswith("/stream") and request.query_params.get("format") != "flac":
            from starlette.responses import Response
            return Response(bytes([0xA5]) * 32_768, media_type="audio/mp4")
        return await call_next(request)


def install_initial_video_format_failure(app: Any) -> Callable[[str], None]:
    """Reject only the verified public item's direct MP4 bytes and record its compatible retry."""
    from starlette.responses import JSONResponse, Response

    lock = threading.Lock()
    active = faulted = False
    direct_source_id: str | None = None
    initial: dict[str, Any] | None = None
    compatible: list[dict[str, Any]] = []
    normalized_item_id: str | None = None
    verified_source_id: str | None = None
    payload = b"not-a-real-mp4\n" * 32_768

    def target_item(path: list[str]) -> bool:
        return (
            normalized_item_id is not None
            and len(path) == 4
            and path[1].lower() == "items"
            and path[3].lower() == "playbackinfo"
            and path[2].replace("-", "").lower() == normalized_item_id
        )

    def option_values(body: dict[str, Any]) -> dict[str, Any]:
        # The direct fixture has video stream 0 and its sole AAC stream 1, with
        # no selected subtitle. Jellyfin accepts those defaults when omitted,
        # while the prepared compatible request writes them explicitly.
        return {
            "StartTimeTicks": body.get("StartTimeTicks"),
            "MaxStreamingBitrate": body.get("MaxStreamingBitrate"),
            "AudioStreamIndex": body.get("AudioStreamIndex", 1),
            "SubtitleStreamIndex": body.get("SubtitleStreamIndex", -1),
        }

    def compatibility_profile(body: dict[str, Any]) -> bool:
        profile = body.get("DeviceProfile")
        if body.get("EnableDirectPlay") is not False or not isinstance(profile, dict):
            return False
        direct = profile.get("DirectPlayProfiles")
        hls = profile.get("TranscodingProfiles")
        codecs = profile.get("CodecProfiles")
        if not isinstance(direct, list) or not isinstance(hls, list) or not isinstance(codecs, list):
            return False
        direct_video = [entry for entry in direct if isinstance(entry, dict) and entry.get("Type") == "Video"]
        hls_video = [entry for entry in hls if isinstance(entry, dict) and entry.get("Type") == "Video" and str(entry.get("Protocol")).lower() == "hls"]
        h264 = next((entry for entry in codecs if isinstance(entry, dict) and entry.get("Type") == "Video" and entry.get("Codec") == "h264"), None)
        aac = next((entry for entry in codecs if isinstance(entry, dict) and entry.get("Type") == "VideoAudio" and entry.get("Codec") == "aac"), None)
        conditions = h264.get("Conditions") if isinstance(h264, dict) else None
        audio_conditions = aac.get("Conditions") if isinstance(aac, dict) else None
        return (
            bool(direct_video) and bool(hls_video)
            and all(entry.get("VideoCodec") == "h264" for entry in [*direct_video, *hls_video])
            and all(entry.get("AudioCodec") == "aac" for entry in [*direct_video, *hls_video])
            and isinstance(conditions, list)
            and {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR"} in conditions
            and audio_conditions == [{"Property": "AudioChannels", "Condition": "LessThanEqual", "Value": "2"}]
            and [entry.get("Codec") for entry in codecs if isinstance(entry, dict) and entry.get("Type") == "Video"] == ["h264"]
        )

    def snapshot() -> dict[str, Any]:
        with lock:
            retry = compatible[-1] if compatible else None
            first_recovery = compatible[0] if compatible else None
            initial_options = option_values(initial) if initial else None
            retry_options = option_values(retry) if retry else None
            expected_source_id = direct_source_id or verified_source_id or (initial or {}).get("MediaSourceId")
            return {
                "faultedDirectStream": faulted,
                "compatibilityRequests": len(compatible),
                "compatibilityProfile": bool(retry and compatibility_profile(retry)),
                "normalRecoveryProfile": bool(first_recovery and not compatibility_profile(first_recovery)),
                "sameMediaSource": bool(retry and expected_source_id and retry.get("MediaSourceId") == expected_source_id),
                "preservedOptions": bool(
                    initial_options and retry_options
                    and retry_options["StartTimeTicks"] >= initial_options["StartTimeTicks"]
                    and all(retry_options[key] == initial_options[key] for key in ("MaxStreamingBitrate", "AudioStreamIndex", "SubtitleStreamIndex"))
                ),
            }

    def activate(verified_public_item_id: str, source_id: str | None = None) -> None:
        nonlocal active, normalized_item_id, verified_source_id
        normalized = verified_public_item_id.replace("-", "").lower()
        if not normalized:
            raise ValueError("verified public item ID is required")
        with lock:
            normalized_item_id = normalized
            verified_source_id = source_id
            active = True

    def reset() -> None:
        nonlocal faulted, direct_source_id, initial
        with lock:
            faulted = False
            direct_source_id = None
            initial = None
            compatible.clear()

    @app.middleware("http")
    async def initial_video_format_failure(request, call_next):  # noqa: ANN001, ANN202
        nonlocal direct_source_id, faulted, initial
        path = request.url.path.split("/")
        if request.method == "GET" and request.url.path == "/Lumina/Proof/initial-video-format-recovery":
            return JSONResponse(snapshot())
        if request.method == "POST" and request.url.path == "/Lumina/Proof/initial-video-format-recovery/reset":
            reset()
            return JSONResponse(snapshot())
        with lock:
            enabled = active
        if not enabled:
            return await call_next(request)
        if request.method == "POST" and target_item(path):
            try:
                body = json.loads(await request.body())
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = None
            if isinstance(body, dict):
                with lock:
                    if body.get("EnableDirectPlay") is False:
                        compatible.append(body)
                    elif initial is None:
                        initial = body
            return await call_next(request)
        if (
            request.method == "GET"
            and normalized_item_id is not None
            and len(path) == 4
            and path[1].lower() == "videos"
            and path[2].replace("-", "").lower() == normalized_item_id
            and path[3].lower() in {"stream", "stream.mp4", "stream.m4v", "stream.mov"}
        ):
            with lock:
                direct_source_id = request.query_params.get("MediaSourceId")
                faulted = True
            requested = request.headers.get("range")
            if requested and requested.startswith("bytes="):
                start_text, _, end_text = requested[6:].partition("-")
                start = int(start_text or 0)
                end = min(int(end_text) if end_text else len(payload) - 1, len(payload) - 1)
                if start <= end:
                    return Response(payload[start:end + 1], status_code=206, media_type="video/mp4", headers={
                        "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{len(payload)}",
                    })
            return Response(payload, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})
        return await call_next(request)

    app.add_api_route("/Lumina/Proof/initial-video-format-recovery", snapshot, methods=["GET"], include_in_schema=False)
    return activate


def install_audio_restore_failure(app: Any, track_id: str) -> Callable[[], None]:
    """Fail one saved-track lookup after verification, then allow an explicit Play retry."""
    enabled = False
    failed = False

    def activate() -> None:
        nonlocal enabled
        enabled = True

    @app.middleware("http")
    async def unavailable_saved_track(request, call_next):  # noqa: ANN001, ANN202
        nonlocal failed
        path = request.url.path.lower()
        if enabled and not failed and request.method == "GET" and path.startswith("/users/") and path.endswith("/items/" + track_id.lower()):
            from starlette.responses import JSONResponse
            failed = True
            return JSONResponse({"detail": "Synthetic saved-track lookup unavailable"}, status_code=503)
        return await call_next(request)

    return activate


def performance_request_log_path() -> Path | None:
    raw = os.environ.get(PERFORMANCE_REQUEST_LOG_ENV)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"{PERFORMANCE_REQUEST_LOG_ENV} must be an absolute path.")
    return path


def artwork_metrics_path() -> Path | None:
    """Return the optional, private evidence file for native artwork requests."""
    raw = os.environ.get(ARTWORK_METRICS_PATH_ENV)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"{ARTWORK_METRICS_PATH_ENV} must be an absolute path.")
    return path


def _metric_phase(value: str | None) -> str:
    phase = (value or "snapshot").strip()
    if not phase or len(phase) > 64 or not all(character.isalnum() or character in "._-" for character in phase):
        raise ValueError("phase must be 1-64 letters, digits, dots, underscores, or hyphens.")
    return phase


def _artwork_metric_kind(path: str) -> str | None:
    """Classify only first-party Jellyfin artwork routes; query values never enter evidence."""
    segments = urlsplit(path).path.strip("/").split("/")
    if len(segments) < 4 or segments[0] != "Items" or segments[2] != "Images":
        return None
    kind = segments[3].lower()
    return kind if kind in {"primary", "backdrop", "logo"} else None


class _ArtworkRequestMetrics:
    """Opt-in aggregate evidence for artwork requests made by a native proof journey."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(self._descriptor, 0o600)
        self._lock = threading.Lock()
        self._counts = {"primary": 0, "backdrop": 0, "logo": 0}
        self._active = False
        self._stopped = False
        self._bytes_written = 0

    def activate(self) -> None:
        with self._lock:
            self._active = True

    def record(self, *, method: str, path: str, status: int) -> None:  # noqa: ARG002
        # Count every artwork GET after fixture verification. A failed retry is
        # still a real request and must not make the warm-return audit pass.
        if method != "GET":
            return
        kind = _artwork_metric_kind(path)
        if kind is None:
            return
        with self._lock:
            if self._active and not self._stopped:
                self._counts[kind] += 1

    def reset(self, phase: str) -> dict[str, int]:
        with self._lock:
            self._counts = {"primary": 0, "backdrop": 0, "logo": 0}
            snapshot = dict(self._counts)
            self._write_locked(event="reset", phase=phase, counts=snapshot)
            return snapshot

    def snapshot(self, phase: str) -> dict[str, int]:
        with self._lock:
            snapshot = dict(self._counts)
            self._write_locked(event="snapshot", phase=phase, counts=snapshot)
            return snapshot

    def close(self) -> None:
        with self._lock:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1

    def _write_locked(self, *, event: str, phase: str, counts: dict[str, int]) -> None:
        if self._stopped or self._descriptor < 0:
            return
        line = json.dumps({
            "timestamp": round(time.time(), 3),
            "event": event,
            "phase": phase,
            "primary": counts["primary"],
            "backdrop": counts["backdrop"],
            "logo": counts["logo"],
        }, separators=(",", ":")).encode("utf-8") + b"\n"
        if self._bytes_written + len(line) > MAX_ARTWORK_METRICS_BYTES:
            self._stopped = True
            return
        os.write(self._descriptor, line)
        self._bytes_written += len(line)


def install_artwork_request_metrics(app: Any, path: Path) -> _ArtworkRequestMetrics:
    """Add proof-only aggregate counters without exposing artwork URLs or credentials."""
    from fastapi import HTTPException

    recorder = _ArtworkRequestMetrics(path)

    @app.middleware("http")
    async def record_artwork_request(request, call_next):  # noqa: ANN001, ANN202
        recorder.record(method=request.method, path=request.url.path, status=0)
        return await call_next(request)

    async def artwork_metrics(phase: str | None = None):  # noqa: ANN202
        try:
            return recorder.snapshot(_metric_phase(phase))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def reset_artwork_metrics(phase: str | None = None):  # noqa: ANN202
        try:
            return recorder.reset(_metric_phase(phase))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    app.add_api_route("/Lumina/Proof/artwork-metrics", artwork_metrics, methods=["GET"], include_in_schema=False)
    app.add_api_route("/Lumina/Proof/artwork-metrics/reset", reset_artwork_metrics, methods=["POST"], include_in_schema=False)
    return recorder


def _requests_catalog_metric_kind(path: str) -> str | None:
    """Classify only the aggregate native Requests calls used by its efficiency proof."""
    normalized = urlsplit(path).path.rstrip("/")
    prefix = "/Lumina/Requests/catalog/genres/"
    if normalized.startswith(prefix):
        kind = normalized.removeprefix(prefix)
        return f"genres_{kind}" if kind in {"movie", "show", "anime"} else None
    return "details" if normalized.startswith("/Lumina/Requests/catalog/title/") else None


class _RequestsCatalogMetrics:
    """Opt-in bounded in-memory aggregate counters; no request URLs or credentials are retained."""

    _empty_counts = {"genres_movie": 0, "genres_show": 0, "genres_anime": 0, "details": 0}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = dict(self._empty_counts)

    def record(self, *, method: str, path: str) -> None:
        if method != "GET":
            return
        kind = _requests_catalog_metric_kind(path)
        if kind is None:
            return
        with self._lock:
            self._counts[kind] += 1

    def reset(self) -> dict[str, int]:
        with self._lock:
            self._counts = dict(self._empty_counts)
            return dict(self._counts)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


def install_navigation_fixture_controls(app: Any) -> None:
    """Change only disposable server settings and synthetic music availability."""
    from app.db import session_scope
    from app.models import AppSettings, LibraryItem

    async def set_navigation_fixture(mode: str):
        if mode not in {"off", "on", "remove-music"}:
            return {"ok": False}
        with session_scope() as db:
            settings = db.get(AppSettings, 1)
            settings.requests_enabled = mode != "off"
            settings.native_streaming_enabled = mode != "off"
            for item in db.query(LibraryItem).filter(LibraryItem.kind.in_(("track", "audio"))).all():
                item.status = "missing" if mode == "remove-music" else "available"
            db.commit()
        return {"ok": True}

    app.add_api_route("/proof/navigation/{mode}", set_navigation_fixture, methods=["POST"], include_in_schema=False)
    route = app.router.routes.pop()
    frontend_index = next((i for i, candidate in enumerate(app.router.routes) if getattr(candidate, "name", None) == "frontend"), len(app.router.routes))
    app.router.routes.insert(frontend_index, route)



def install_streaming_fixture(app: Any, provisioned: dict[str, Any]) -> None:
    """Keep native streaming qualification offline at the upstream adapter boundary."""
    from types import SimpleNamespace
    from app import main as server
    from app.db import session_scope
    from app.models import AppSettings, LibraryItem
    from app.schemas import PreviewResponse
    from app.services.remote_streaming import RemoteStreamingService, UpstreamMediaResponse
    from app.services.yt_dlp_service import YtDlpService

    with session_scope() as db:
        from app.services.media_artifacts import MediaArtifactService
        source = db.get(LibraryItem, provisioned["directItemID"])
        source_file, _root = MediaArtifactService(db).locate(source)
        if source_file is None:
            raise RuntimeError("Streaming proof requires its generated movie artifact.")
        media = Path(source_file).read_bytes()
    metrics = {"bytes_served": 0, "sessions_released": 0}
    metrics_lock = threading.Lock()

    class Reader:
        def open(self, track, byte_range):  # noqa: ANN001, ANN202
            start = byte_range.start if byte_range else 0
            end = byte_range.end if byte_range and byte_range.end is not None else len(media) - 1
            payload = media[start:end + 1]
            with metrics_lock:
                metrics["bytes_served"] += len(payload)
            headers = {"content-type": "video/mp4", "content-length": str(len(payload)), "accept-ranges": "bytes"}
            if byte_range:
                headers["content-range"] = f"bytes {start}-{end}/{len(media)}"
            return UpstreamMediaResponse(status_code=206 if byte_range else 200, headers=headers, body=[payload])

    class Resolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001, ANN202
            raise AssertionError("The bounded proof source must not refresh upstream.")

    class ProofStreamingService(RemoteStreamingService):
        def release(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            result = super().release(*args, **kwargs)
            with metrics_lock:
                metrics["sessions_released"] += 1
            return result

    server.remote_streams = ProofStreamingService(resolver=Resolver(), reader=Reader())
    original_preview = YtDlpService.preview_for_playback

    def preview(service, source_url, user_id, *args, **kwargs):  # noqa: ANN001, ANN202
        if source_url not in {"https://www.youtube.com/watch?v=proofremote", "https://www.twitch.tv/observatory"}:
            return original_preview(service, source_url, user_id, *args, **kwargs)
        return PreviewResponse(kind="video", title="A new Observatory transmission", extractor="youtube", extractor_key="Youtube",
            webpage_url=source_url, raw={"id": "proofremote", "title": "A new Observatory transmission", "extractor_key": "Youtube",
                "webpage_url": source_url, "duration": 90,
                "formats": [{"format_id": "18", "url": "https://media.example.com/proof.mp4", "protocol": "https", "ext": "mp4",
                             "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 180, "filesize": len(media)}]})
    YtDlpService.preview_for_playback = preview
    server.home_recommendations = lambda *args, **kwargs: SimpleNamespace(items=[{
        "webpage_url": "https://www.youtube.com/watch?v=proofremote", "title": "A new Observatory transmission",
        "uploader": "Observatory Studio", "thumbnail": None,
    }])
    server.live_now = lambda *args, **kwargs: SimpleNamespace(hero=[], items=[{
        "webpage_url": "https://www.twitch.tv/observatory", "title": "Live Observatory Relay",
        "uploader": "Observatory Studio", "kind": "live", "thumbnail": None,
    }])

    async def configure(mode: str):
        if mode not in {"on", "off"}:
            return {"ok": False}
        with session_scope() as db:
            settings = db.get(AppSettings, 1)
            settings.native_streaming_enabled = mode == "on"
            db.commit()
        with metrics_lock:
            metrics["bytes_served"] = 0
            metrics["sessions_released"] = 0
        return {"ok": True}

    async def snapshot():
        with metrics_lock:
            return dict(metrics)

    app.add_api_route("/proof/streaming/{mode}", configure, methods=["POST"], include_in_schema=False)
    app.add_api_route("/proof/streaming-metrics", snapshot, methods=["GET"], include_in_schema=False)
    routes = [route for route in app.router.routes if getattr(route, "path", "").startswith("/proof/streaming")]
    app.router.routes[:] = [route for route in app.router.routes if route not in routes]
    frontend_index = next((i for i, candidate in enumerate(app.router.routes) if getattr(candidate, "name", None) == "frontend"), len(app.router.routes))
    app.router.routes[frontend_index:frontend_index] = routes

def install_requests_catalog_metrics(app: Any) -> _RequestsCatalogMetrics:
    """Add proof-only Requests aggregate counters without exposing request data."""
    recorder = _RequestsCatalogMetrics()

    @app.middleware("http")
    async def record_requests_catalog_request(request, call_next):  # noqa: ANN001, ANN202
        recorder.record(method=request.method, path=request.url.path)
        return await call_next(request)

    async def requests_metrics():  # noqa: ANN202
        return recorder.snapshot()

    async def reset_requests_metrics():  # noqa: ANN202
        return recorder.reset()

    app.add_api_route("/proof/requests-metrics", requests_metrics, methods=["GET"], include_in_schema=False)
    app.add_api_route("/proof/requests-metrics/reset", reset_requests_metrics, methods=["POST"], include_in_schema=False)
    metrics_routes = [
        route for route in app.router.routes
        if getattr(route, "path", None) in {"/proof/requests-metrics", "/proof/requests-metrics/reset"}
    ]
    frontend_index = next(
        (index for index, route in enumerate(app.router.routes) if getattr(route, "name", None) == "frontend"),
        len(app.router.routes),
    )
    for route in metrics_routes:
        app.router.routes.remove(route)
    for offset, route in enumerate(metrics_routes):
        app.router.routes.insert(frontend_index + offset, route)
    return recorder


class _MetadataRefreshMetrics:
    """Opt-in aggregate evidence that metadata saves refresh the Home cycle once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_up = 0

    def record(self, *, method: str, path: str) -> None:
        if method == "GET" and urlsplit(path).path.rstrip("/") == "/Shows/NextUp":
            with self._lock:
                self._next_up += 1

    def reset(self) -> dict[str, int]:
        with self._lock:
            self._next_up = 0
            return {"next_up": self._next_up}

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"next_up": self._next_up}


def install_metadata_refresh_metrics(app: Any) -> _MetadataRefreshMetrics:
    """Add proof-only metadata refresh counters without retaining request data."""
    recorder = _MetadataRefreshMetrics()

    @app.middleware("http")
    async def record_metadata_refresh_request(request, call_next):  # noqa: ANN001, ANN202
        recorder.record(method=request.method, path=request.url.path)
        return await call_next(request)

    async def metadata_refresh_metrics():  # noqa: ANN202
        return recorder.snapshot()

    async def reset_metadata_refresh_metrics():  # noqa: ANN202
        return recorder.reset()

    paths = {"/proof/metadata-refresh-metrics", "/proof/metadata-refresh-metrics/reset"}
    app.add_api_route("/proof/metadata-refresh-metrics", metadata_refresh_metrics, methods=["GET"], include_in_schema=False)
    app.add_api_route("/proof/metadata-refresh-metrics/reset", reset_metadata_refresh_metrics, methods=["POST"], include_in_schema=False)
    metrics_routes = [route for route in app.router.routes if getattr(route, "path", None) in paths]
    frontend_index = next(
        (index for index, route in enumerate(app.router.routes) if getattr(route, "name", None) == "frontend"),
        len(app.router.routes),
    )
    for route in metrics_routes:
        app.router.routes.remove(route)
    for offset, route in enumerate(metrics_routes):
        app.router.routes.insert(frontend_index + offset, route)
    return recorder


def _normalise_request_path(path: str) -> str:
    path = urlsplit(path).path
    segments = path.split("/")
    if segments[1:5] == ["api", "public", "showcase", "art"] and len(segments) > 5:
        segments[5] = ":token"
    normalised = []
    for segment in segments:
        try:
            uuid.UUID(segment)
        except ValueError:
            normalised.append(segment)
        else:
            normalised.append(":id")
    return "/".join(normalised)


class _PerformanceRequestLog:
    """A bounded, credential-free recorder for the opt-in simulator profiling tour."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(self._descriptor, 0o600)
        self._lock = threading.Lock()
        self._bytes_written = 0
        self._active = False
        self._stopped = False

    def activate(self) -> None:
        self._active = True

    def write(self, *, method: str, path: str, status: int, duration_ms: int) -> None:
        if not self._active or self._stopped:
            return
        line = json.dumps({
            "timestamp": round(time.time(), 3),
            "method": method,
            "path": _normalise_request_path(path),
            "status": status,
            "duration_ms": duration_ms,
        }, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._lock:
            if self._stopped or self._bytes_written + len(line) > MAX_PERFORMANCE_REQUEST_LOG_BYTES:
                self._stopped = True
                return
            os.write(self._descriptor, line)
            self._bytes_written += len(line)

    def close(self) -> None:
        with self._lock:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1


def install_performance_request_log(app: Any, path: Path) -> _PerformanceRequestLog:
    recorder = _PerformanceRequestLog(path)

    @app.middleware("http")
    async def record_performance_request(request, call_next):  # noqa: ANN001, ANN202
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            recorder.write(
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=round((time.perf_counter() - started) * 1_000),
            )
            raise
        recorder.write(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1_000),
        )
        return response

    return recorder
