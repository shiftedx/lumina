from __future__ import annotations

import hashlib
import itertools
import json
import re
import secrets
import struct
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic, sleep, time
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import quoteattr

from app.services.playback_log import log_playback
from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE, PersistentStreamRangeCache, StreamCacheKey
from app.services.remote_streaming_types import (
    AUTO_RENDITION_ID,
    DEFAULT_BROWSER_PROFILES,
    PLAYBACK_CEILINGS,
    BrowserCapabilities,
    ByteRange,
    DashIndex,
    DashPresentation,
    HlsAsset,
    HlsPackager,
    HlsPresentation,
    MediaTrack,
    PlaybackDescriptor,
    PlaybackMediaKind,
    PlaybackResolver,
    PlaybackStatus,
    PlaybackTransport,
    PreparationSupersededError,
    RangeNotSatisfiableError,
    RenditionDescriptor,
    ResponseSlots,
    StreamNotFoundError,
    StreamResponseSpec,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
    UpstreamReader,
    UpstreamTrackExpiredError,
    _ByteLimitedBody,
    _ClosingBody,
    _DASH_INDEX_CACHE_ENTRIES,
    _DASH_INDEX_JOIN_SECONDS,
    _DASH_INDEX_TTL_SECONDS,
    _DASH_PROBE_BYTES,
    _DASH_PROBE_WORKERS,
    _DASH_RESOURCE,
    _MAX_DASH_SEGMENT_BYTES,
    _PlaybackPlan,
    _Rendition,
    _RenditionKey,
    _StreamRecord,
    acquire_slots,
    release_slots,
    with_close,
)
from app.services.remote_streaming_dash_index import _parse_dash_index, _parse_webm_index
from app.services.remote_streaming_planning import (
    _audio_codec_family,
    _audio_profile,
    _audio_score,
    _auto_ladder,
    _bounded_byte_range,
    _canonical_remote_source_url,
    _content_type,
    _dash_audio_supported,
    _dash_manifest,
    _dash_video_supported,
    _known_video_container,
    _legacy_hls_video,
    _manifest_video_codec,
    _profile,
    _progressive,
    _rendition_frame_rate,
    _rendition_key,
    _rendition_label,
    _rendition_quality_key,
    _split_video_supported,
    _stable_source_identity,
    _track,
    _track_cache_fingerprint,
    _video_codec_family,
    _video_score,
    parse_range_header,
    redact_preview_info,
)


_BARE_URI_ATTRIBUTE = re.compile(rb'URI="([A-Za-z0-9][A-Za-z0-9._-]*)"')
_BARE_URI_LINE = re.compile(rb"(?m)^[A-Za-z0-9][A-Za-z0-9._-]*$")


SAFE_RESPONSE_HEADERS = {
    "accept-ranges": "Accept-Ranges",
    "content-length": "Content-Length",
    "content-range": "Content-Range",
    "etag": "ETag",
    "last-modified": "Last-Modified",
}


class RemoteStreamingService:
    """Backend-owned remote playback behind one small, owner-bound interface.

    Upstream addresses, request headers, refresh state, muxing, and cleanup stay
    private. Routes consume descriptors and response specs.
    """

    def __init__(
        self,
        *,
        resolver: PlaybackResolver,
        reader: UpstreamReader,
        hls_packager: HlsPackager | None = None,
        token_factory: Callable[[], str] | None = None,
        rendition_token_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time,
        idle_ttl_seconds: float = 30 * 60,
        max_lifetime_seconds: float = 6 * 60 * 60,
        refresh_margin_seconds: float = 60,
        max_streams_global: int = 100,
        max_streams_per_user: int = 10,
        max_concurrent_packaging_global: int = 2,
        max_concurrent_packaging_per_user: int = 1,
        max_concurrent_reads_global: int = 16,
        max_concurrent_reads_per_user: int = 4,
        max_bytes_per_response: int = 16 * 1024 * 1024,
        stream_cache: PersistentStreamRangeCache | None = None,
    ) -> None:
        if max_concurrent_reads_global < 1 or max_concurrent_reads_per_user < 1:
            raise ValueError("Remote streaming concurrency limits must be positive.")
        if max_bytes_per_response < 1:
            raise ValueError("The remote streaming byte budget must be positive.")
        self._resolver = resolver
        self._reader = reader
        self._hls_packager = hls_packager
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._rendition_token_factory = rendition_token_factory or (lambda: secrets.token_urlsafe(18))
        self._clock = clock
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_lifetime_seconds = max_lifetime_seconds
        self._refresh_margin_seconds = refresh_margin_seconds
        self._max_streams_global = max_streams_global
        self._max_streams_per_user = max_streams_per_user
        self._packaging_global = threading.BoundedSemaphore(max_concurrent_packaging_global)
        self._packaging_per_user_limit = max_concurrent_packaging_per_user
        self._packaging_by_user: dict[str, threading.BoundedSemaphore] = {}
        self._reads_global = threading.BoundedSemaphore(max_concurrent_reads_global)
        self._reads_per_user_limit = max_concurrent_reads_per_user
        self._reads_by_user: dict[str, threading.BoundedSemaphore] = {}
        # signed track address -> (expires at, its probed index); private, in memory only.
        self._dash_indexes: dict[tuple[str, str], tuple[float, Future[DashIndex]]] = {}
        self._max_bytes_per_response = max_bytes_per_response
        self._stream_cache = stream_cache
        self._records: dict[str, _StreamRecord] = {}
        self._tracked_records: dict[str, _StreamRecord] = {}
        self._records_lock = threading.RLock()
        # One in-flight HLS preparation per member: owner -> (request ticket, stream id, generation).
        self._preparing: dict[str, tuple[int, str, int]] = {}
        self._tickets = itertools.count(1)

    def register(
        self,
        *,
        owner_user_id: str,
        source_url: str,
        info: dict[str, Any],
        browser_capabilities: BrowserCapabilities | None = None,
    ) -> PlaybackDescriptor:
        now = self._clock()
        capabilities = browser_capabilities or BrowserCapabilities()
        plan, rendition_ids = self._select_plan(info, capabilities, {})
        with self._records_lock:
            if len(self._tracked_records) >= self._max_streams_global:
                raise UnsupportedPlaybackError("The remote playback server is at capacity.")
            if sum(record.owner_user_id == owner_user_id for record in self._tracked_records.values()) >= self._max_streams_per_user:
                raise UnsupportedPlaybackError("This household member has too many active playback sessions.")
            stream_id = self._unique_stream_id()
            record = _StreamRecord(
                stream_id=stream_id,
                owner_user_id=owner_user_id,
                source_url=source_url,
                source_identity=_stable_source_identity(info, source_url),
                plan=plan,
                created_at=now,
                last_accessed_at=now,
                resolution_expires_at=self._plan_expiry(plan, now),
                browser_capabilities=capabilities,
                rendition_ids=rendition_ids,
                provider=str(info.get("extractor_key") or info.get("extractor") or "unknown").lower(),
                personal_youtube_generation=info.get("_lumina_youtube_session"),
            )
            self._records[stream_id] = record
            self._tracked_records[stream_id] = record
        return self._descriptor(record)

    def has_presentation(self, owner_user_id: str, stream_id: str, generation: int) -> bool:
        """Whether a manifest request would only re-read an existing package (a playlist reload)."""
        record = self._records.get(stream_id)
        return record is not None and record.owner_user_id == owner_user_id and generation in record.presentations

    def describe(self, owner_user_id: str, stream_id: str) -> PlaybackDescriptor:
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            return self._descriptor(record)
        finally:
            self._release_ref(record)

    def refresh(self, owner_user_id: str, stream_id: str) -> PlaybackDescriptor:
        """Resolve a session again without changing its public opaque identity."""

        self._cancel_preparation(owner_user_id, stream_id)
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            self._refresh_if_generation(record, record.generation)
            return self._descriptor(record)
        finally:
            self._release_ref(record)

    def select_rendition(
        self, owner_user_id: str, stream_id: str, rendition_id: str, *, max_height: int | None = None
    ) -> PlaybackDescriptor:
        """Pin one opaque, currently available rendition without quality fallback, or return to Auto.

        ``max_height`` is the member's ceiling, used only by Auto.
        """

        self._cancel_preparation(owner_user_id, stream_id)
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            self._ensure_fresh(record)
            with record.refresh_lock:
                capabilities, pinned_key = record.browser_capabilities, None
                if rendition_id == AUTO_RENDITION_ID:
                    capabilities = replace(capabilities, max_height=max_height)
                    plan = self._default_plan(record.plan, max_height)
                    if not plan.auto_videos:
                        raise StreamNotFoundError("Auto quality is not available for this source.")
                else:
                    rendition = next(
                        (item for item in record.plan.renditions if item.descriptor.rendition_id == rendition_id),
                        None,
                    )
                    if rendition is None:
                        raise StreamNotFoundError("Remote stream rendition not found.")
                    plan, pinned_key = self._plan_with_rendition(record.plan, rendition), rendition.key
                previous = (record.plan, record.generation, record.pinned_rendition_key, record.browser_capabilities)
                record.pinned_rendition_key = pinned_key
                record.browser_capabilities = capabilities
                replaces_segmented = record.plan.transport in {"hls", "dash"} or plan.transport in {"hls", "dash"}
                record.plan = plan
                if replaces_segmented:
                    record.generation += 1
                    self._evict_presentations(record)
                if plan.transport == "dash":
                    try:
                        record.dash_presentations[record.generation] = self._prepare_dash_generation(
                            record,
                            record.generation,
                        )
                        self._evict_presentations(record)
                    except BaseException:
                        record.plan, record.generation, record.pinned_rendition_key, record.browser_capabilities = previous
                        raise
                record.resolution_expires_at = self._plan_expiry(record.plan, self._clock())
                return self._descriptor(record)
        finally:
            self._release_ref(record)

    def serve_content(
        self,
        owner_user_id: str,
        stream_id: str,
        range_header: str | None = None,
        *,
        rendition_id: str | None = None,
    ) -> StreamResponseSpec:
        record = self._acquire_record(owner_user_id, stream_id)
        read_slots: tuple[threading.BoundedSemaphore, threading.BoundedSemaphore] | None = None
        try:
            self._ensure_fresh(record)
            track = self._active_progressive_track(record, rendition_id)
            byte_range = _bounded_byte_range(
                parse_range_header(range_header, track.content_length),
                track.content_length,
                self._max_bytes_per_response,
            )
            cache_range = byte_range or (
                ByteRange(0, track.content_length - 1) if track.content_length is not None else None
            )
            cache_key = self._stream_cache_key(record, track, cache_range)
            cached = self._cached_response(cache_key, normalize_full_response=byte_range is None)
            if cached is not None:
                return self._leased_response(record, cached)
            generation = record.generation
            read_slots = self._acquire_read_slots(record.owner_user_id)
            upstream = self._reader.open(track, byte_range)
            if upstream.status_code in {401, 403, 404, 410}:
                upstream.close()
                self._release_slots(read_slots)
                read_slots = None
                self._refresh_if_generation(record, generation)
                track = self._active_progressive_track(record, rendition_id)
                byte_range = _bounded_byte_range(
                    parse_range_header(range_header, track.content_length),
                    track.content_length,
                    self._max_bytes_per_response,
                )
                cache_range = byte_range or (
                    ByteRange(0, track.content_length - 1) if track.content_length is not None else None
                )
                cache_key = self._stream_cache_key(record, track, cache_range)
                cached = self._cached_response(cache_key, normalize_full_response=byte_range is None)
                if cached is not None:
                    return self._leased_response(record, cached)
                read_slots = self._acquire_read_slots(record.owner_user_id)
                upstream = self._reader.open(track, byte_range)
            if upstream.status_code == 416:
                length = _unsatisfied_length(_header_value(upstream.headers, "content-range"))
                upstream.close()
                self._release_slots(read_slots)
                read_slots = None
                raise RangeNotSatisfiableError(length)
            if upstream.status_code >= 400:
                upstream.close()
                self._release_slots(read_slots)
                read_slots = None
                raise UnsupportedPlaybackError("The remote source did not provide playable media.")
            try:
                self._validate_response_budget(upstream, byte_range)
            except BaseException:
                upstream.close()
                raise
            if cache_key is not None and byte_range is not None:
                try:
                    self._validate_partial_response(upstream, byte_range, exact_end=True)
                except UnsupportedPlaybackError:
                    cache_key = None
            spec = self._response_spec(
                upstream,
                fallback_content_type=track.content_type,
                byte_limit=self._max_bytes_per_response,
            )
            spec = self._captured_response(cache_key, spec)
            spec = self._read_leased_response(spec, read_slots)
            read_slots = None
            return self._leased_response(record, spec)
        except BaseException:
            if read_slots is not None:
                self._release_slots(read_slots)
            self._release_ref(record)
            raise

    def serve_hls(
        self,
        owner_user_id: str,
        stream_id: str,
        generation: int,
        resource: str,
        range_header: str | None = None,
    ) -> StreamResponseSpec:
        ticket = next(self._tickets)
        record = self._acquire_record(owner_user_id, stream_id)
        leased_generation: int | None = None
        read_slots: tuple[threading.BoundedSemaphore, threading.BoundedSemaphore] | None = None
        try:
            self._ensure_fresh(record)
            if self._hls_packager is None:
                raise UnsupportedPlaybackError("This playback session does not expose HLS content.")
            if not resource or resource.startswith(("/", ".")) or "/" in resource or "\\" in resource:
                raise StreamNotFoundError("Remote stream resource not found.")
            with record.refresh_lock:
                presentation = record.presentations.get(generation)
                if presentation is None:
                    if generation != record.generation or record.plan.transport != "hls" or record.plan.video is None or record.plan.audio is None:
                        raise StreamNotFoundError("Remote stream generation not found.")
                    try:
                        presentation = self._prepare_generation(record, generation, ticket)
                    except UpstreamTrackExpiredError:
                        self._refresh_if_generation(record, generation)
                        if record.plan.transport != "hls" or record.plan.video is None or record.plan.audio is None:
                            raise UnsupportedPlaybackError("The refreshed source no longer supports split playback.")
                        refreshed_generation = record.generation
                        presentation = self._prepare_generation(record, refreshed_generation, ticket)
                        record.presentations[refreshed_generation] = presentation
                        self._evict_presentations(record)
                        location = f"/api/remote-streams/{record.stream_id}/hls/{refreshed_generation}/{resource}"
                        return self._leased_response(record, StreamResponseSpec(307, {"Cache-Control": "private, no-store", "Location": location}, []))
                    record.presentations[generation] = presentation
                    self._evict_presentations(record)
                with record.lifecycle_lock:
                    record.generation_refs[generation] = record.generation_refs.get(generation, 0) + 1
                leased_generation = generation
            if resource == "manifest.m3u8":
                manifest = self._scoped_manifest(record, generation, presentation.manifest)
                if len(manifest) > self._max_bytes_per_response:
                    raise UnsupportedPlaybackError("The HLS manifest exceeds the per-response byte budget.")
                return self._leased_response(record, StreamResponseSpec(
                200,
                {
                    "Cache-Control": "private, no-store",
                    "Content-Length": str(len(manifest)),
                    "Content-Type": "application/vnd.apple.mpegurl",
                    "X-Content-Type-Options": "nosniff",
                },
                [manifest],
                ), generation=leased_generation)
            asset = presentation.assets.get(resource)
            if asset is None:
                raise StreamNotFoundError("Remote stream resource not found.")
            read_slots = self._acquire_read_slots(record.owner_user_id)
            asset_length = _asset_length(asset)
            byte_range = _bounded_byte_range(
                parse_range_header(range_header, asset_length),
                asset_length,
                self._max_bytes_per_response,
            )
            start = byte_range.start if byte_range and byte_range.start is not None else 0
            end = byte_range.end if byte_range and byte_range.end is not None else asset_length - 1
            body, close = _asset_body(asset, start, end)
            headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Length": str(end - start + 1),
            "Content-Type": asset.content_type,
            "X-Content-Type-Options": "nosniff",
            }
            status_code = 200
            if byte_range:
                status_code = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{asset_length}"
            spec = self._read_leased_response(StreamResponseSpec(status_code, headers, body, close), read_slots)
            read_slots = None
            return self._leased_response(record, spec, generation=leased_generation)
        except BaseException:
            if read_slots is not None:
                self._release_slots(read_slots)
            self._release_ref(record, leased_generation)
            raise

    def serve_dash(
        self,
        owner_user_id: str,
        stream_id: str,
        generation: int,
        resource: str,
        range_header: str | None = None,
    ) -> StreamResponseSpec:
        """Serve one static SegmentBase MPD or one owner-bound split track range."""

        record = self._acquire_record(owner_user_id, stream_id)
        read_slots: tuple[threading.BoundedSemaphore, threading.BoundedSemaphore] | None = None
        try:
            self._ensure_fresh(record)
            if not _DASH_RESOURCE.fullmatch(resource):
                raise StreamNotFoundError("Remote stream resource not found.")
            with record.refresh_lock:
                presentation = record.dash_presentations.get(generation)
                if presentation is None:
                    if generation != record.generation or record.plan.transport != "dash":
                        raise StreamNotFoundError("Remote stream generation not found.")
                    try:
                        with self._preparation_log(record):
                            presentation = self._prepare_dash_generation(record, generation)
                    except UpstreamTrackExpiredError:
                        self._refresh_if_generation(record, generation)
                        if record.plan.transport != "dash":
                            raise UnsupportedPlaybackError("The refreshed source no longer supports DASH playback.")
                        refreshed_generation = record.generation
                        presentation = self._prepare_dash_generation(record, refreshed_generation)
                        record.dash_presentations[refreshed_generation] = presentation
                        self._evict_presentations(record)
                        location = f"/api/remote-streams/{record.stream_id}/dash/{refreshed_generation}/{resource}"
                        return self._leased_response(record, StreamResponseSpec(
                            307,
                            {"Cache-Control": "private, no-store", "Location": location},
                            [],
                        ))
                    except UnsupportedPlaybackError:
                        fallback = self._prepare_dash_fallback(record)
                        if fallback is None:
                            raise
                        fallback_generation, presentation = fallback
                        record.dash_presentations[fallback_generation] = presentation
                        self._evict_presentations(record)
                        location = f"/api/remote-streams/{record.stream_id}/dash/{fallback_generation}/{resource}"
                        return self._leased_response(record, StreamResponseSpec(
                            307,
                            {"Cache-Control": "private, no-store", "Location": location},
                            [],
                        ))
                    record.dash_presentations[generation] = presentation
                    self._evict_presentations(record)
            if resource == "manifest.mpd":
                if len(presentation.manifest) > self._max_bytes_per_response:
                    raise UnsupportedPlaybackError("The DASH manifest exceeds the per-response byte budget.")
                return self._leased_response(record, StreamResponseSpec(
                    200,
                    {
                        "Cache-Control": "private, no-store",
                        "Content-Length": str(len(presentation.manifest)),
                        "Content-Type": "application/dash+xml",
                        "X-Content-Type-Options": "nosniff",
                    },
                    [presentation.manifest],
                ))
            byte_range = parse_range_header(range_header, None)
            if byte_range is None or byte_range.start is None or byte_range.end is None:
                raise UnsupportedPlaybackError("DASH media requests require an explicit byte range.")
            if resource == "audio":
                track = presentation.audio
            elif int(resource[6:]) < len(presentation.videos):
                track = presentation.videos[int(resource[6:])]
            else:
                raise StreamNotFoundError("Remote stream resource not found.")
            limit = presentation.segment_byte_limit
            if byte_range.end - byte_range.start + 1 > limit:
                raise RangeNotSatisfiableError(track.content_length)
            head, total_length = presentation.heads.get(resource, (b"", 0))
            if byte_range.end < len(head):
                # Init and index were read while preparing: no upstream read, no member read slot (dash.js asks every
                # rung at once, which overran the member's slots and cost a ~1 s retry before the first frame).
                body = head[byte_range.start : byte_range.end + 1]
                return self._leased_response(record, StreamResponseSpec(206, {
                    "Cache-Control": "private, no-store",
                    "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{total_length}",
                    "Content-Type": track.content_type,
                    "X-Content-Type-Options": "nosniff",
                }, [body]))
            cache_key = self._stream_cache_key(record, track, byte_range)
            cached = self._cached_response(cache_key)
            if cached is not None:
                return self._leased_response(record, cached)
            read_slots = self._acquire_read_slots(record.owner_user_id)
            upstream = self._reader.open(track, byte_range)
            if upstream.status_code in {401, 403, 404, 410}:
                upstream.close()
                self._release_slots(read_slots)
                read_slots = None
                if generation == record.generation:
                    self._refresh_if_generation(record, generation)
                raise UpstreamTrackExpiredError("An upstream DASH track expired during playback.")
            if upstream.status_code != 206:
                upstream.close()
                self._release_slots(read_slots)
                read_slots = None
                raise UnsupportedPlaybackError("The upstream source did not honor the DASH byte range.")
            try:
                self._validate_partial_response(upstream, byte_range, exact_end=True)
                self._validate_response_budget(upstream, byte_range, limit)
            except BaseException:
                upstream.close()
                raise
            spec = self._response_spec(
                upstream,
                fallback_content_type=track.content_type,
                byte_limit=limit,
            )
            spec = self._captured_response(cache_key, spec)
            spec = self._read_leased_response(spec, read_slots)
            read_slots = None
            return self._leased_response(record, spec)
        except BaseException:
            if read_slots is not None:
                self._release_slots(read_slots)
            self._release_ref(record)
            raise

    def release(self, owner_user_id: str, stream_id: str) -> None:
        with self._records_lock:
            record = self._records.get(stream_id)
            if record is None or record.owner_user_id != owner_user_id:
                raise StreamNotFoundError("Remote stream not found.")
            self._records.pop(stream_id)
            should_clean = self._retire_locked(record)
        self._cancel_preparation(owner_user_id, stream_id)
        if should_clean:
            self._cleanup_record(record)

    def revoke_personal_youtube(self, owner_user_id: str) -> None:
        with self._records_lock:
            streams = [r.stream_id for r in self._records.values() if r.owner_user_id == owner_user_id and r.personal_youtube_generation]
            for key in [key for key in self._dash_indexes if key[0] == owner_user_id]:
                self._dash_indexes.pop(key, None)
        for stream_id in streams:
            try:
                self.release(owner_user_id, stream_id)
            except StreamNotFoundError:
                pass

    def expire_idle(self) -> int:
        now = self._clock()
        expired: list[_StreamRecord] = []
        retired_count = 0
        with self._records_lock:
            for stream_id, record in list(self._records.items()):
                if now - record.last_accessed_at >= self._idle_ttl_seconds or now - record.created_at >= self._max_lifetime_seconds:
                    record = self._records.pop(stream_id)
                    retired_count += 1
                    if self._retire_locked(record):
                        expired.append(record)
        for record in expired:
            self._cleanup_record(record)
        return retired_count

    def close_all(self) -> int:
        with self._records_lock:
            active_records = list(self._records.values())
            records = [record for record in active_records if self._retire_locked(record)]
            self._records.clear()
        for record in records:
            self._cleanup_record(record)
        return len(active_records)

    def _unique_stream_id(self) -> str:
        for _attempt in range(10):
            candidate = self._token_factory()
            if candidate and candidate not in self._tracked_records:
                return candidate
        raise RuntimeError("Could not allocate an opaque remote stream identifier.")

    def _acquire_record(self, owner_user_id: str, stream_id: str) -> _StreamRecord:
        from app.services import youtube_session

        with self._records_lock:
            record = self._records.get(stream_id)
            if record is None or record.owner_user_id != owner_user_id:
                raise StreamNotFoundError("Remote stream not found.")
            if record.personal_youtube_generation and not youtube_session.generation_valid(owner_user_id, record.personal_youtube_generation):
                raise StreamNotFoundError("YouTube session changed. Reopen this video or watch it on YouTube.")
            with record.lifecycle_lock:
                if record.retired:
                    raise StreamNotFoundError("Remote stream not found.")
                record.active_refs += 1
                record.last_accessed_at = self._clock()
            return record

    @staticmethod
    def _retire_locked(record: _StreamRecord) -> bool:
        with record.lifecycle_lock:
            record.retired = True
            return record.active_refs == 0 and not record.cleaned

    def _release_ref(self, record: _StreamRecord, generation: int | None = None) -> None:
        should_clean = False
        close_generation = False
        with record.lifecycle_lock:
            if generation is not None:
                remaining = record.generation_refs.get(generation, 0) - 1
                if remaining > 0:
                    record.generation_refs[generation] = remaining
                else:
                    record.generation_refs.pop(generation, None)
                    if generation in record.pending_generation_cleanup:
                        record.pending_generation_cleanup.remove(generation)
                        close_generation = True
            record.active_refs -= 1
            should_clean = record.retired and record.active_refs == 0 and not record.cleaned
        if should_clean:
            self._cleanup_record(record)
        elif close_generation and self._hls_packager is not None:
            self._hls_packager.close_generation(record.stream_id, generation)

    def _cleanup_record(self, record: _StreamRecord) -> None:
        with record.lifecycle_lock:
            if record.cleaned or record.active_refs:
                return
            record.cleaned = True
        with self._records_lock:
            self._tracked_records.pop(record.stream_id, None)
        if self._hls_packager is not None:
            self._hls_packager.close(record.stream_id)

    def _acquire_packaging_slots(self, owner_user_id: str) -> ResponseSlots:
        return acquire_slots(
            self._packaging_global, self._packaging_by_user, self._packaging_per_user_limit, owner_user_id, self._records_lock,
            global_busy="Another remote stream is already preparing; try again shortly.",
            user_busy="This household member already has a remote stream preparing.",
        )

    def _acquire_read_slots(self, owner_user_id: str) -> ResponseSlots:
        return acquire_slots(
            self._reads_global, self._reads_by_user, self._reads_per_user_limit, owner_user_id, self._records_lock,
            global_busy="The remote playback server has too many active media responses.",
            user_busy="This household member already has too many active media responses.",
        )

    _release_slots = staticmethod(release_slots)

    def _prepare_generation(self, record: _StreamRecord, generation: int, ticket: int) -> HlsPresentation:
        """Package one generation; the member's newest request supersedes their in-flight preparation.

        Duplicate requests for the same stream generation never reach here
        concurrently: they queue on the record's refresh lock and reuse the
        prepared presentation (join, not 409).
        """
        with self._preparation_log(record):
            return self._prepare_hls(record, generation, ticket)

    @contextmanager
    def _preparation_log(self, record: _StreamRecord) -> Iterator[None]:
        """Log one preparation outcome with safe fields only: never URLs, headers or tokens."""
        started = monotonic()
        plan = record.plan
        failure: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            failure = exc
            raise
        finally:
            outcome = (
                "ready" if failure is None
                else "superseded" if isinstance(failure, PreparationSupersededError)
                else "expired" if isinstance(failure, UpstreamTrackExpiredError)
                else "gone" if isinstance(failure, StreamNotFoundError)
                else "failed" if isinstance(failure, UnsupportedPlaybackError)
                else "error"
            )
            log_playback(
                "remote_stream.prepare",
                outcome,
                stream=record.stream_id[:8],
                provider=record.provider,
                transport=plan.transport,
                rendition=plan.selected_rendition_id,
                video_format=plan.video.format_id if plan.video else None,
                audio_format=plan.audio.format_id if plan.audio else None,
                duration_ms=round((monotonic() - started) * 1000),
                error=f'"{failure}"' if isinstance(failure, UnsupportedPlaybackError) else type(failure).__name__ if failure else None,
            )

    def _prepare_hls(self, record: _StreamRecord, generation: int, ticket: int) -> HlsPresentation:
        packager = self._hls_packager
        if packager is None or record.plan.video is None or record.plan.audio is None:
            raise UnsupportedPlaybackError("This playback session does not expose HLS content.")
        owner = record.owner_user_id
        entry = (ticket, record.stream_id, generation)
        cancelled: tuple[int, str, int] | None = None
        deadline = monotonic() + 15
        while True:
            with self._records_lock:
                previous = self._preparing.get(owner)
                if previous is not None and previous[0] > ticket:
                    raise PreparationSupersededError("A newer remote stream request replaced this one.")
                try:
                    slots = self._acquire_packaging_slots(owner)
                except UnsupportedPlaybackError:
                    if previous is None or monotonic() >= deadline:
                        raise
                else:
                    self._preparing[owner] = entry
                    break
            if previous != cancelled:
                # Cancelling stops its downloads and FFmpeg, removes its files, and frees its slot.
                packager.close_generation(previous[1], previous[2])
                cancelled = previous
            sleep(0.05)
        def finish(_done: object = None) -> None:
            with self._records_lock:
                if self._preparing.get(owner) == entry:
                    del self._preparing[owner]
            release_slots(slots)

        try:
            with record.lifecycle_lock:
                if record.retired:
                    raise StreamNotFoundError("Remote stream not found.")
            presentation = packager.prepare(record.stream_id, generation, record.plan.video, record.plan.audio)
        except BaseException:
            finish()
            raise
        if presentation.done is None:
            finish()
        else:  # background packaging keeps the member's slot and stays supersedable until upstream work stops
            presentation.done.add_done_callback(finish)
        return presentation

    def _cancel_preparation(self, owner_user_id: str, stream_id: str) -> None:
        """Cancel the member's in-flight preparation of ``stream_id`` so refresh/select/release never wait on it."""
        with self._records_lock:
            previous = self._preparing.get(owner_user_id)
        if previous is not None and previous[1] == stream_id and self._hls_packager is not None:
            self._hls_packager.close_generation(stream_id, previous[2])

    def _prepare_dash_generation(self, record: _StreamRecord, generation: int) -> DashPresentation:
        if generation != record.generation or record.plan.transport != "dash" or record.plan.video is None or record.plan.audio is None:
            raise UnsupportedPlaybackError("This playback session does not expose DASH content.")
        tracks = record.plan.auto_videos or (record.plan.video,)
        *video_results, audio_result = self._probe_dash_indexes(record.owner_user_id, (*tracks, record.plan.audio))
        videos: list[tuple[MediaTrack, DashIndex]] = []
        for track, result in zip(tracks, video_results, strict=True):
            if isinstance(result, UnsupportedPlaybackError) and not isinstance(result, UpstreamTrackExpiredError) and record.plan.auto_videos:
                continue  # Auto drops a rung it cannot index; a pinned rendition fails instead of changing.
            if isinstance(result, BaseException):
                raise result
            videos.append((track, result))
        if not videos:
            raise UnsupportedPlaybackError("No Auto rendition provided a bounded DASH segment index.")
        if isinstance(audio_result, BaseException):
            raise audio_result
        audio_index = audio_result
        return DashPresentation(
            manifest=_dash_manifest(record, generation, videos, audio_index),
            videos=tuple(track for track, _index in videos),
            audio=record.plan.audio,
            heads={
                name: (index.head, index.content_length)
                for name, index in (*((f"video-{number}", index) for number, (_track, index) in enumerate(videos)), ("audio", audio_index))
            },
            segment_byte_limit=max(
                self._max_bytes_per_response,
                audio_index.max_referenced_size,
                *(index.max_referenced_size for _track, index in videos),
            ),
        )

    def _prepare_dash_fallback(self, record: _StreamRecord) -> tuple[int, DashPresentation] | None:
        if record.pinned_rendition_key is not None or record.plan.video is None:
            return None
        current_score = _video_score(record.plan.video)
        candidates = sorted(
            (
                rendition
                for rendition in record.plan.renditions
                if rendition.transport == "dash" and _video_score(rendition.video) < current_score
            ),
            key=lambda rendition: _video_score(rendition.video),
            reverse=True,
        )
        previous_plan = record.plan
        previous_generation = record.generation
        for rendition in candidates:
            record.plan = self._plan_with_rendition(previous_plan, rendition)
            record.generation = previous_generation + 1
            try:
                return record.generation, self._prepare_dash_generation(record, record.generation)
            except UnsupportedPlaybackError:
                record.plan = previous_plan
                record.generation = previous_generation
        return None

    def warm_dash(self, *, owner_user_id: str, info: dict[str, Any], browser_capabilities: BrowserCapabilities) -> None:
        """Read the DASH indexes a likely click would need; its preparation then reuses them."""
        plan, _rendition_ids = self._select_plan(info, browser_capabilities, {})
        if plan.transport == "dash" and plan.video is not None and plan.audio is not None:
            self._probe_dash_indexes(owner_user_id, (*(plan.auto_videos or (plan.video,)), plan.audio))

    def _probe_dash_indexes(self, owner_user_id: str, tracks: tuple[MediaTrack, ...]) -> list[DashIndex | BaseException]:
        """Every track's index at once under one member read slot, each a result or its error.

        One at a time took 7-10 s for a YouTube 4K ladder (0.1-2.6 s per probe), the largest share of time to first
        frame. The slot bounds the member; the pool, the burst.
        """
        slots = self._acquire_read_slots(owner_user_id)
        try:
            with ThreadPoolExecutor(max_workers=min(_DASH_PROBE_WORKERS, len(tracks))) as pool:
                probes = [pool.submit(self._read_dash_index, owner_user_id, track, slots=slots) for track in tracks]
                return [probe.exception() or probe.result() for probe in probes]
        finally:
            self._release_slots(slots)

    def _read_dash_index(self, owner_user_id: str, track: MediaTrack, *, slots: ResponseSlots | None = None) -> DashIndex:
        """Probe one track's segment index, under the caller's read slots when given (else its own).

        An index read (or still being read) in the last ``_DASH_INDEX_TTL_SECONDS`` for the same signed address, by an
        intent prefetch or the previous generation, is joined instead of read again; a failed read is forgotten.
        """
        cache_key = (owner_user_id, track.url)
        with self._records_lock:
            cached = self._dash_indexes.get(cache_key)
            leading = cached is None or cached[0] <= monotonic()
            if leading:
                if len(self._dash_indexes) >= _DASH_INDEX_CACHE_ENTRIES:
                    self._dash_indexes.pop(next(iter(self._dash_indexes)), None)
                probe: Future[DashIndex] = Future()
                self._dash_indexes[cache_key] = (monotonic() + _DASH_INDEX_TTL_SECONDS, probe)
            else:
                probe = cached[1]
        if not leading:
            return probe.result(timeout=_DASH_INDEX_JOIN_SECONDS)
        try:
            index = self._probe_dash_index(owner_user_id, track, slots=slots)
        except BaseException as exc:
            with self._records_lock:
                if self._dash_indexes.get(cache_key, (0, None))[1] is probe:
                    self._dash_indexes.pop(cache_key, None)
            probe.set_exception(exc)
            raise
        probe.set_result(index)
        return index

    def _probe_dash_index(self, owner_user_id: str, track: MediaTrack, *, slots: ResponseSlots | None = None) -> DashIndex:
        probe_size = min(_DASH_PROBE_BYTES, self._max_bytes_per_response)
        probe_range = ByteRange(0, probe_size - 1)
        owned_slots = None if slots is not None else self._acquire_read_slots(owner_user_id)
        response: UpstreamMediaResponse | None = None
        try:
            response = self._reader.open(track, probe_range, timeout_seconds=15)
            if response.status_code in {401, 403, 404, 410}:
                raise UpstreamTrackExpiredError("An upstream track expired while preparing DASH playback.")
            if response.status_code != 206:
                raise UnsupportedPlaybackError("The upstream source did not honor the DASH index probe.")
            content_length = self._validate_partial_response(response, probe_range)
            trusted_content_length = content_length or track.content_length
            if trusted_content_length is None or trusted_content_length <= 0:
                raise UnsupportedPlaybackError("The DASH index probe did not report the media length.")
            parse = _parse_webm_index if track.ext == "webm" else _parse_dash_index
            body = bytearray()
            index = None
            # Stop at the first chunk that completes the index: init + index are a few KB, and a slow edge took up to
            # 2 s for the rest of the range (preparation waits for the slowest rung).
            for chunk in response.body:
                body.extend(chunk)
                if len(body) > probe_size:
                    raise UnsupportedPlaybackError("The DASH index exceeds its probe budget.")
                try:
                    index = parse(bytes(body), content_length=trusted_content_length)
                    break
                except UnsupportedPlaybackError:
                    continue
            if index is None:
                index = parse(bytes(body), content_length=trusted_content_length)
            if index.max_referenced_size > _MAX_DASH_SEGMENT_BYTES:
                raise UnsupportedPlaybackError("A DASH segment exceeds the per-response byte budget.")
            return replace(index, head=bytes(body[: index.index_end + 1])) if index.index_end < len(body) else index
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                if owned_slots is not None:
                    self._release_slots(owned_slots)

    @staticmethod
    def _scoped_manifest(record: _StreamRecord, generation: int, manifest: bytes | Path) -> bytes:
        """Point the package's bare file names at this stream's owner-bound HLS route."""
        if isinstance(manifest, Path):
            try:
                manifest = manifest.read_bytes()
            except OSError as exc:  # the generation was cancelled or cleaned meanwhile
                raise StreamNotFoundError("Remote stream resource not found.") from exc
        prefix = f"/api/remote-streams/{record.stream_id}/hls/{generation}/".encode()
        manifest = _BARE_URI_ATTRIBUTE.sub(lambda match: b'URI="' + prefix + match[1] + b'"', manifest)
        manifest = _BARE_URI_LINE.sub(lambda match: prefix + match[0], manifest)
        if b"#EXT-X-ENDLIST" not in manifest:
            # A still-growing playlist starts at its beginning, not at its "live" edge.
            manifest = manifest.replace(b"#EXTM3U\n", b"#EXTM3U\n#EXT-X-START:TIME-OFFSET=0\n", 1)
        return manifest

    def _evict_presentations(self, record: _StreamRecord) -> None:
        victims = sorted(record.presentations)[:-2]
        for generation in victims:
            record.presentations.pop(generation, None)
            with record.lifecycle_lock:
                if record.generation_refs.get(generation, 0):
                    record.pending_generation_cleanup.add(generation)
                    continue
            if self._hls_packager is not None:
                self._hls_packager.close_generation(record.stream_id, generation)
        for generation in sorted(record.dash_presentations)[:-2]:
            record.dash_presentations.pop(generation, None)

    def _leased_response(self, record: _StreamRecord, spec: StreamResponseSpec, *, generation: int | None = None) -> StreamResponseSpec:
        return with_close(spec, lambda: self._release_ref(record, generation))

    @staticmethod
    def _read_leased_response(spec: StreamResponseSpec, slots: ResponseSlots) -> StreamResponseSpec:
        return with_close(spec, lambda: release_slots(slots))

    def _stream_cache_key(
        self,
        record: _StreamRecord,
        track: MediaTrack,
        byte_range: ByteRange | None,
    ) -> StreamCacheKey | None:
        if (
            self._stream_cache is None
            or record.personal_youtube_generation is not None
            or byte_range is None
            or byte_range.start is None
            or byte_range.end is None
        ):
            return None
        return StreamCacheKey(
            owner_user_id=record.owner_user_id,
            source_identity=record.source_identity,
            track_fingerprint=_track_cache_fingerprint(track),
            range_start=byte_range.start,
            range_end=byte_range.end,
        )

    def _cached_response(
        self,
        key: StreamCacheKey | None,
        *,
        normalize_full_response: bool = False,
    ) -> StreamResponseSpec | None:
        if self._stream_cache is None or key is None:
            return None
        try:
            cached = self._stream_cache.lookup(key)
            if cached is None:
                return None
            body, close = cached.open()
            headers = dict(cached.headers)
            status_code = cached.status_code
            if normalize_full_response and status_code == 206:
                status_code = 200
                headers = {
                    name: value
                    for name, value in headers.items()
                    if name.casefold() != "content-range"
                }
            return StreamResponseSpec(status_code, headers, body, close)
        except Exception:
            return None

    def _captured_response(self, key: StreamCacheKey | None, spec: StreamResponseSpec) -> StreamResponseSpec:
        if self._stream_cache is None or key is None:
            return spec
        try:
            expected_length = int(spec.headers["Content-Length"])
            if expected_length != key.range_end - key.range_start + 1:
                return spec
            body, close = self._stream_cache.capture(
                key,
                status_code=spec.status_code,
                headers=spec.headers,
                expected_length=expected_length,
                body=spec.body,
                close=spec.close,
            )
            return StreamResponseSpec(spec.status_code, spec.headers, body, close)
        except Exception:
            return spec

    @staticmethod
    def _active_progressive_track(record: _StreamRecord, rendition_id: str | None) -> MediaTrack:
        plan = record.plan
        track = plan.video or plan.audio
        if plan.transport != "progressive" or track is None:
            raise UnsupportedPlaybackError("This playback session does not expose progressive content.")
        if plan.video is None:
            if rendition_id is not None:
                raise StreamNotFoundError("Remote stream rendition not found.")
            return track
        if plan.selected_rendition_id is None:
            raise UnsupportedPlaybackError("This playback session has no active rendition.")
        if rendition_id is not None and rendition_id != plan.selected_rendition_id:
            raise StreamNotFoundError("Remote stream rendition not found.")
        return track

    @staticmethod
    def _plan_with_rendition(plan: _PlaybackPlan, rendition: _Rendition) -> _PlaybackPlan:
        content_type = (
            "application/vnd.apple.mpegurl"
            if rendition.transport == "hls"
            else "application/dash+xml"
            if rendition.transport == "dash"
            else rendition.video.content_type
        )
        return _PlaybackPlan(
            rendition.transport,
            "video",
            content_type,
            rendition.video,
            rendition.audio,
            renditions=plan.renditions,
            selected_rendition_id=rendition.descriptor.rendition_id,
            dash_videos=plan.dash_videos,
        )

    def _default_plan(self, plan: _PlaybackPlan, ceiling: int | None) -> _PlaybackPlan:
        """Auto: the best rendition within ``ceiling``, adaptive across its codec's DASH ladder when one exists."""
        if not plan.renditions:
            return plan
        within = [item for item in plan.renditions if ceiling is None or (item.video.height or 0) <= ceiling]
        top = max(within or plan.renditions[:1], key=lambda item: _video_score(item.video))
        selected = self._plan_with_rendition(plan, top)
        ladder = _auto_ladder(plan.dash_videos, top.video) if top.transport == "dash" else ()
        if len(ladder) < 2:
            return selected
        return replace(selected, selected_rendition_id=AUTO_RENDITION_ID, auto_videos=ladder)

    def _ensure_fresh(self, record: _StreamRecord) -> None:
        if self._clock() + self._refresh_margin_seconds < record.resolution_expires_at:
            return
        self._refresh_if_generation(record, record.generation)

    def _refresh_if_generation(self, record: _StreamRecord, expected_generation: int) -> None:
        with record.refresh_lock:
            if record.generation != expected_generation:
                return
            info = self._resolver.resolve(record.source_url, record.owner_user_id)
            plan, rendition_ids = self._select_plan(
                info,
                record.browser_capabilities,
                record.rendition_ids,
                pinned_key=record.pinned_rendition_key,
            )
            record.plan = plan
            record.rendition_ids = rendition_ids
            record.generation += 1
            record.resolution_expires_at = self._plan_expiry(plan, self._clock())

    def _descriptor(self, record: _StreamRecord) -> PlaybackDescriptor:
        plan = record.plan
        ready = plan.transport is not None
        playback_url = None
        if plan.transport == "progressive":
            if (
                plan.media_kind == "video"
                and plan.selected_rendition_id is not None
                and len(plan.renditions) > 1
            ):
                playback_url = (
                    f"/api/remote-streams/{record.stream_id}/renditions/"
                    f"{plan.selected_rendition_id}/content"
                )
            else:
                playback_url = f"/api/remote-streams/{record.stream_id}/content"
        elif plan.transport == "hls":
            playback_url = f"/api/remote-streams/{record.stream_id}/hls/{record.generation}/manifest.m3u8"
        elif plan.transport == "dash":
            playback_url = f"/api/remote-streams/{record.stream_id}/dash/{record.generation}/manifest.mpd"
        fallback_message = None
        if not ready:
            fallback_message = (
                "The selected quality is no longer available. Choose another quality to continue."
                if plan.fallback_code == "selected_rendition_unavailable"
                else "This source cannot be streamed here yet. You can still download it."
            )
        return PlaybackDescriptor(
            status="ready" if ready else "unsupported",
            stream_id=record.stream_id,
            transport=plan.transport,
            media_kind=plan.media_kind,
            playback_url=playback_url,
            content_type=plan.content_type,
            has_video=plan.video is not None,
            has_audio=plan.audio is not None or bool(plan.video and plan.video.acodec != "none"),
            seekable=plan.transport in {"progressive", "hls", "dash"},
            fallback_code=plan.fallback_code,
            fallback_message=fallback_message,
            renditions=tuple(item.descriptor for item in plan.renditions),
            selected_rendition_id=plan.selected_rendition_id,
            auto_available=bool(
                plan.auto_videos or self._default_plan(plan, record.browser_capabilities.max_height).auto_videos
            ),
        )

    def _select_plan(
        self,
        info: dict[str, Any],
        browser_capabilities: BrowserCapabilities,
        previous_ids: Mapping[_RenditionKey, str],
        *,
        pinned_key: _RenditionKey | None = None,
    ) -> tuple[_PlaybackPlan, dict[_RenditionKey, str]]:
        if info.get("is_live"):
            return _PlaybackPlan(None, None, None, None, None, "live_not_supported"), {}
        tracks = [_track(candidate) for candidate in info.get("formats", []) if isinstance(candidate, dict)]
        tracks = [track for track in tracks if track is not None]
        muxed = [
            track for track in tracks
            if track.vcodec != "none"
            and track.acodec != "none"
            and _progressive(track)
            and _profile(track) in browser_capabilities.supported_profiles
        ]
        all_videos = [track for track in tracks if track.vcodec != "none" and track.acodec == "none"]
        legacy_split_videos = [
            track for track in all_videos
            if _legacy_hls_video(track, browser_capabilities)
        ]
        audios = [
            track for track in tracks
            if track.vcodec == "none"
            and track.acodec != "none"
            and _audio_profile(track) in browser_capabilities.supported_profiles
        ]
        legacy_audios = [
            track for track in audios
            if track.ext in {"m4a", "mp4"}
            and _audio_codec_family(track.acodec) == "aac"
            and "mp4-avc-aac" in browser_capabilities.supported_profiles
        ]
        dash_audios = [track for track in audios if _dash_audio_supported(track)]
        hls_audio = max(legacy_audios, key=_audio_score) if legacy_audios and self._hls_packager is not None else None
        dash_enabled = "dash-segment-base" in browser_capabilities.supported_profiles
        dash_audio = max(dash_audios, key=_audio_score) if dash_enabled and dash_audios else None
        dash_split_videos = (
            [track for track in all_videos if _dash_video_supported(track, browser_capabilities)]
            if dash_audio is not None
            else []
        )
        packageable_split_videos = (
            [track for track in legacy_split_videos if self._can_package_hls(track, hls_audio)]
            if hls_audio is not None
            else []
        )
        renditions, rendition_ids = self._build_renditions(
            muxed,
            packageable_split_videos,
            dash_split_videos,
            hls_audio,
            dash_audio,
            previous_ids,
        )
        if renditions:
            base = _PlaybackPlan(None, None, None, None, None, renditions=renditions, dash_videos=tuple(dash_split_videos))
            if pinned_key is None:
                return self._default_plan(base, browser_capabilities.max_height), rendition_ids
            selected = next((item for item in renditions if item.key == pinned_key), None)
            if selected is None:
                return replace(base, fallback_code="selected_rendition_unavailable"), rendition_ids
            return self._plan_with_rendition(base, selected), rendition_ids
        if pinned_key is not None:
            return _PlaybackPlan(None, None, None, None, None, "selected_rendition_unavailable"), {}
        high_resolution_split = any(
            track.height is not None
            and track.height > 1080
            and _split_video_supported(track, browser_capabilities)
            for track in all_videos
        )
        if legacy_split_videos and legacy_audios:
            if self._hls_packager is None:
                return _PlaybackPlan(None, None, None, None, None, "muxer_unavailable"), {}
            return _PlaybackPlan(None, None, None, None, None, "split_package_budget_exceeded"), {}
        if high_resolution_split and audios:
            return _PlaybackPlan(None, None, None, None, None, "incremental_mux_unavailable"), {}
        if audios and not all_videos:
            selected = max(audios, key=_audio_score)
            return _PlaybackPlan("progressive", "audio", selected.content_type, None, selected), {}
        if all_videos and audios:
            return _PlaybackPlan(None, None, None, None, None, "no_browser_compatible_format"), {}
        if all_videos and not audios:
            return _PlaybackPlan(None, None, None, None, None, "no_audio"), {}
        return _PlaybackPlan(None, None, None, None, None, "no_browser_compatible_format"), {}

    def _can_package_hls(self, video: MediaTrack, audio: MediaTrack) -> bool:
        if self._hls_packager is None:
            return False
        checker = getattr(self._hls_packager, "can_package", None)
        return True if checker is None else bool(checker(video, audio))

    def _build_renditions(
        self,
        muxed_tracks: Iterable[MediaTrack],
        hls_split_videos: Iterable[MediaTrack],
        dash_split_videos: Iterable[MediaTrack],
        hls_audio: MediaTrack | None,
        dash_audio: MediaTrack | None,
        previous_ids: Mapping[_RenditionKey, str],
    ) -> tuple[tuple[_Rendition, ...], dict[_RenditionKey, str]]:
        by_quality: dict[str, tuple[PlaybackTransport, MediaTrack, MediaTrack | None]] = {}

        def consider(transport: PlaybackTransport, video: MediaTrack, audio: MediaTrack | None) -> None:
            quality = _rendition_quality_key(video)
            existing = by_quality.get(quality)
            if existing is None:
                by_quality[quality] = (transport, video, audio)
                return
            existing_transport, existing_video, _existing_audio = existing
            transport_priority = {"hls": 0, "dash": 1, "progressive": 2}
            if transport_priority[transport] > transport_priority[existing_transport]:
                by_quality[quality] = (transport, video, audio)
            elif transport == existing_transport and _video_score(video) > _video_score(existing_video):
                by_quality[quality] = (transport, video, audio)

        for track in muxed_tracks:
            consider("progressive", track, None)
        if hls_audio is not None:
            for track in hls_split_videos:
                consider("hls", track, hls_audio)
        if dash_audio is not None:
            for track in dash_split_videos:
                consider("dash", track, dash_audio)
        rendition_ids: dict[_RenditionKey, str] = {}
        renditions: list[_Rendition] = []
        candidates = sorted(by_quality.values(), key=lambda item: _video_score(item[1]))
        for transport, video, audio in candidates:
            key = _rendition_key(video, transport)
            rendition_id = previous_ids.get(key) or self._unique_rendition_id(
                {*previous_ids.values(), *rendition_ids.values()}
            )
            rendition_ids[key] = rendition_id
            descriptor = RenditionDescriptor(
                rendition_id=rendition_id,
                width=video.width,
                height=video.height,
                frame_rate=video.frame_rate,
                bitrate_kbps=video.bitrate_kbps,
                video_codec=_video_codec_family(video.vcodec),
                audio_codec=_audio_codec_family(audio.acodec if audio is not None else video.acodec),
                container=video.ext,
                content_type=(
                    "application/vnd.apple.mpegurl"
                    if transport == "hls"
                    else "application/dash+xml"
                    if transport == "dash"
                    else video.content_type
                ),
                display_label=_rendition_label(video),
            )
            renditions.append(_Rendition(key, descriptor, transport, video, audio))
        return tuple(renditions), rendition_ids

    def _unique_rendition_id(self, used: set[str]) -> str:
        for _attempt in range(10):
            candidate = self._rendition_token_factory()
            if candidate and candidate not in used:
                return candidate
        raise RuntimeError("Could not allocate an opaque rendition identifier.")

    @staticmethod
    def _plan_expiry(plan: _PlaybackPlan, now: float) -> float:
        expiries = [track.expires_at for track in (plan.video, plan.audio) if track and track.expires_at is not None]
        return min(expiries) if expiries else now + 120

    def _validate_response_budget(
        self, upstream: UpstreamMediaResponse, requested_range: ByteRange | None, limit: int | None = None
    ) -> None:
        value = _header_value(upstream.headers, "content-length")
        try:
            content_length = int(value) if value is not None else None
        except ValueError:
            content_length = None
        if content_length is not None and content_length > (limit or self._max_bytes_per_response):
            raise UnsupportedPlaybackError("The upstream response exceeds the per-response byte budget.")
        if requested_range is not None and upstream.status_code == 200 and content_length is None:
            raise UnsupportedPlaybackError("The upstream source did not honor a bounded media request.")

    @staticmethod
    def _validate_partial_response(
        upstream: UpstreamMediaResponse,
        requested_range: ByteRange,
        *,
        exact_end: bool = False,
    ) -> int | None:
        value = _header_value(upstream.headers, "content-range")
        if value is None or not value.lower().startswith("bytes "):
            raise UnsupportedPlaybackError("The upstream source omitted the DASH content range.")
        try:
            interval, total_text = value[6:].split("/", 1)
            start_text, end_text = interval.split("-", 1)
            start, end = int(start_text), int(end_text)
            total = None if total_text == "*" else int(total_text)
        except (TypeError, ValueError):
            raise UnsupportedPlaybackError("The upstream source returned an invalid DASH content range.") from None
        if (
            requested_range.start is None
            or requested_range.end is None
            or start != requested_range.start
            or end > requested_range.end
            or (exact_end and end != requested_range.end)
            or end < start
            or (total is not None and (total <= end or total <= 0))
        ):
            raise UnsupportedPlaybackError("The upstream source returned the wrong DASH content range.")
        length = _header_value(upstream.headers, "content-length")
        if length is not None:
            try:
                if int(length) != end - start + 1:
                    raise UnsupportedPlaybackError("The upstream source returned an inconsistent DASH range length.")
            except ValueError:
                raise UnsupportedPlaybackError("The upstream source returned an invalid DASH range length.") from None
        return total

    @staticmethod
    def _response_spec(
        upstream: UpstreamMediaResponse,
        *,
        fallback_content_type: str,
        byte_limit: int,
    ) -> StreamResponseSpec:
        headers = {
            public_name: value
            for name, value in upstream.headers.items()
            if (public_name := SAFE_RESPONSE_HEADERS.get(name.lower())) and value
        }
        headers["Content-Type"] = fallback_content_type
        headers["Cache-Control"] = "private, no-store"
        headers["X-Content-Type-Options"] = "nosniff"
        body = _ByteLimitedBody(upstream.body, upstream.close, byte_limit)
        return StreamResponseSpec(upstream.status_code, dict(sorted(headers.items())), body, body.close)


class _FileRangeBody:
    def __init__(self, path: Path, start: int, end: int, chunk_size: int = MEDIA_FILE_CHUNK_SIZE) -> None:
        self._file = path.open("rb")
        self._file.seek(start)
        self._remaining = end - start + 1
        self._chunk_size = chunk_size
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        try:
            while self._remaining > 0:
                chunk = self._file.read(min(self._chunk_size, self._remaining))
                if not chunk:
                    break
                self._remaining -= len(chunk)
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._file.close()


def _asset_length(asset: HlsAsset) -> int:
    return len(asset.content) if isinstance(asset.content, bytes) else asset.content.stat().st_size


def _asset_body(asset: HlsAsset, start: int, end: int) -> tuple[Iterable[bytes], Callable[[], None]]:
    if isinstance(asset.content, bytes):
        return [asset.content[start : end + 1]], lambda: None
    body = _FileRangeBody(asset.content, start, end)
    return body, body.close


def _unsatisfied_length(content_range: str | None) -> int | None:
    if not content_range:
        return None
    _unit, _separator, value = content_range.partition("*/")
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    normalized = name.lower()
    return next((value for key, value in headers.items() if key.lower() == normalized), None)
