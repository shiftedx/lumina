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


PlaybackStatus = Literal["ready", "unsupported"]
PlaybackTransport = Literal["progressive", "hls", "dash"]
PlaybackMediaKind = Literal["video", "audio"]


DEFAULT_BROWSER_PROFILES = frozenset({
    "mp4-avc-aac",
    "mp4-av1-aac",
    "webm-vp8-vorbis",
    "webm-vp8-opus",
    "webm-vp9-vorbis",
    "webm-vp9-opus",
    "webm-av1-vorbis",
    "webm-av1-opus",
    "m4a-aac",
    "webm-opus",
    "webm-vorbis",
    "mp3-mp3",
})
# A DASH response is one indexed segment. 4K AV1/VP9 segments exceed the
# 16 MiB progressive budget (about 21 MiB for 5 s of 2160p60), so each
# presentation's budget is its own largest segment, never more than this.
_MAX_DASH_SEGMENT_BYTES = 64 * 1024 * 1024
# The one non-opaque rendition id: adaptive playback within the member's ceiling.
AUTO_RENDITION_ID = "auto"
# Member playback ceilings (``ui_prefs.playback_max_height``); absent means Best available.
PLAYBACK_CEILINGS = frozenset({1440, 1080, 720, 480})
_DASH_PROBE_BYTES = 256 * 1024
# A fixed burst per preparation; make it a setting if upstreams start rate-limiting parallel probes.
_DASH_PROBE_WORKERS = 8
# Matches the preview cache: a click within two minutes of its intent prefetch reuses the same signed addresses.
_DASH_INDEX_TTL_SECONDS = 120.0
_DASH_INDEX_CACHE_ENTRIES = 256
_DASH_INDEX_JOIN_SECONDS = 20.0  # a probe's own upstream timeout is 15 s
_DASH_RESOURCE = re.compile(r"manifest\.mpd|audio|video-\d{1,2}")


class StreamNotFoundError(LookupError):
    """The stream does not exist for this household member."""


class RangeNotSatisfiableError(ValueError):
    def __init__(self, length: int | None) -> None:
        super().__init__("The requested media range is not satisfiable.")
        self.length = length


class UnsupportedPlaybackError(RuntimeError):
    """An operation requires a ready playback plan."""


class UpstreamTrackExpiredError(UnsupportedPlaybackError):
    """A private split track expired while its generation was being prepared."""


class PreparationSupersededError(UnsupportedPlaybackError):
    """A newer request from the same member replaced this preparation."""


@dataclass(frozen=True)
class ByteRange:
    start: int | None
    end: int | None = None


@dataclass(frozen=True)
class BrowserCapabilities:
    """Codec/container profiles confirmed playable by the requesting browser, and the member's ceiling."""

    supported_profiles: frozenset[str] = DEFAULT_BROWSER_PROFILES
    max_height: int | None = None


@dataclass(frozen=True)
class RenditionDescriptor:
    rendition_id: str
    width: int | None
    height: int | None
    frame_rate: float | None
    bitrate_kbps: float | None
    video_codec: str
    audio_codec: str
    container: str
    content_type: str
    display_label: str


@dataclass(frozen=True)
class PlaybackDescriptor:
    status: PlaybackStatus
    stream_id: str
    transport: PlaybackTransport | None
    media_kind: PlaybackMediaKind | None
    playback_url: str | None
    content_type: str | None
    has_video: bool
    has_audio: bool
    seekable: bool
    live: bool = False
    fallback_code: str | None = None
    fallback_message: str | None = None
    renditions: tuple[RenditionDescriptor, ...] = ()
    selected_rendition_id: str | None = None
    auto_available: bool = False


@dataclass(frozen=True)
class MediaTrack:
    format_id: str
    url: str = field(repr=False)
    content_type: str
    protocol: str
    ext: str
    vcodec: str
    acodec: str
    height: int | None
    content_length: int | None
    expires_at: float | None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    width: int | None = None
    frame_rate: float | None = None
    bitrate_kbps: float | None = None
    estimated_content_length: int | None = None
    source_container: str | None = None
    # yt-dlp's per-format ``http_chunk_size``: some CDNs (YouTube) throttle
    # unranged reads to ~realtime, so reads are split into ranges of this size.
    chunk_size: int | None = None
    # yt-dlp's language_preference: YouTube's original audio is 10, its auto-dubbed copies -1, others unset (0).
    language_preference: int = 0


@dataclass
class UpstreamMediaResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Iterable[bytes]
    close: Callable[[], None] = lambda: None


@dataclass
class StreamResponseSpec:
    status_code: int
    headers: dict[str, str]
    body: Iterable[bytes] = field(repr=False)
    close: Callable[[], None] = field(default=lambda: None, repr=False)


class _ClosingBody:
    def __init__(self, body: Iterable[bytes], close: Callable[[], None]) -> None:
        self._body = body
        self._close_callback = close
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._body
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close_callback()


ResponseSlots = tuple[threading.BoundedSemaphore, threading.BoundedSemaphore]


def acquire_slots(
    global_slot: threading.BoundedSemaphore,
    by_user: dict[str, threading.BoundedSemaphore],
    per_user_limit: int,
    owner_user_id: str,
    lock: threading.Lock,
    *,
    global_busy: str,
    user_busy: str,
) -> ResponseSlots:
    """Take one global and one per-member slot without blocking, or raise with the matching message."""
    if not global_slot.acquire(blocking=False):
        raise UnsupportedPlaybackError(global_busy)
    with lock:
        user_slot = by_user.setdefault(owner_user_id, threading.BoundedSemaphore(per_user_limit))
    if not user_slot.acquire(blocking=False):
        global_slot.release()
        raise UnsupportedPlaybackError(user_busy)
    return global_slot, user_slot


def release_slots(slots: ResponseSlots) -> None:
    global_slot, user_slot = slots
    user_slot.release()
    global_slot.release()


def with_close(spec: StreamResponseSpec, on_close: Callable[[], None]) -> StreamResponseSpec:
    """``spec`` whose body runs ``on_close`` exactly once, after the upstream close, when exhausted or closed."""

    def close() -> None:
        try:
            spec.close()
        finally:
            on_close()

    body = _ClosingBody(spec.body, close)
    return StreamResponseSpec(spec.status_code, spec.headers, body, body.close)


class _ByteLimitedBody:
    def __init__(self, body: Iterable[bytes], close: Callable[[], None], byte_limit: int) -> None:
        self._body = body
        self._close_callback = close
        self._remaining = byte_limit
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._body:
                if self._remaining <= 0:
                    break
                public = chunk[: self._remaining]
                self._remaining -= len(public)
                if public:
                    yield public
                if len(public) != len(chunk):
                    break
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close_callback()


@dataclass(frozen=True)
class HlsAsset:
    content: bytes | Path = field(repr=False)
    content_type: str


@dataclass(frozen=True)
class HlsPresentation:
    # A Path manifest is re-read per request: an incremental package's playlist grows.
    manifest: bytes | Path
    assets: Mapping[str, HlsAsset]
    # Resolves when packaging stops using upstream (its slot is held until then).
    done: Future[None] | None = None


@dataclass(frozen=True)
class DashIndex:
    initialization_end: int
    index_start: int
    index_end: int
    duration_seconds: float
    max_referenced_size: int
    content_length: int
    # The probed bytes through the index (init + index): what dash.js asks for first, served without an upstream read.
    head: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class DashPresentation:
    manifest: bytes
    videos: tuple[MediaTrack, ...]
    audio: MediaTrack
    segment_byte_limit: int
    # resource name ("video-N", "audio") -> its probed init + index bytes and the track's full length.
    heads: Mapping[str, tuple[bytes, int]] = field(default_factory=dict, repr=False)


class PlaybackResolver(Protocol):
    def resolve(self, source_url: str, owner_user_id: str) -> dict[str, Any]: ...


class UpstreamReader(Protocol):
    def open(self, track: MediaTrack, byte_range: ByteRange | None, timeout_seconds: float | None = None) -> UpstreamMediaResponse: ...


class HlsPackager(Protocol):
    def can_package(self, video: MediaTrack, audio: MediaTrack) -> bool: ...

    def prepare(self, stream_id: str, generation: int, video: MediaTrack, audio: MediaTrack) -> HlsPresentation: ...

    def close(self, stream_id: str) -> None: ...

    def close_generation(self, stream_id: str, generation: int) -> None: ...


@dataclass
class _PlaybackPlan:
    transport: PlaybackTransport | None
    media_kind: PlaybackMediaKind | None
    content_type: str | None
    video: MediaTrack | None
    audio: MediaTrack | None
    fallback_code: str | None = None
    renditions: tuple[_Rendition, ...] = field(default_factory=tuple, repr=False)
    selected_rendition_id: str | None = None
    # Every browser-playable DASH video track (the Auto ladder source) and, in Auto, the active ladder.
    dash_videos: tuple[MediaTrack, ...] = field(default_factory=tuple, repr=False)
    auto_videos: tuple[MediaTrack, ...] = field(default_factory=tuple, repr=False)


_RenditionKey = tuple[str, str, str, str, str, int | None, int | None]


@dataclass(frozen=True)
class _Rendition:
    key: _RenditionKey
    descriptor: RenditionDescriptor
    transport: PlaybackTransport
    video: MediaTrack = field(repr=False)
    audio: MediaTrack | None = field(default=None, repr=False)


@dataclass
class _StreamRecord:
    stream_id: str
    owner_user_id: str
    source_url: str = field(repr=False)
    source_identity: str = field(repr=False)
    plan: _PlaybackPlan = field(repr=False)
    created_at: float
    last_accessed_at: float
    resolution_expires_at: float
    browser_capabilities: BrowserCapabilities = field(repr=False)
    rendition_ids: dict[_RenditionKey, str] = field(default_factory=dict, repr=False)
    pinned_rendition_key: _RenditionKey | None = field(default=None, repr=False)
    generation: int = 1
    refresh_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    presentations: dict[int, HlsPresentation] = field(default_factory=dict, repr=False)
    dash_presentations: dict[int, DashPresentation] = field(default_factory=dict, repr=False)
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    active_refs: int = 0
    retired: bool = False
    cleaned: bool = False
    generation_refs: dict[int, int] = field(default_factory=dict, repr=False)
    pending_generation_cleanup: set[int] = field(default_factory=set, repr=False)
    provider: str = "unknown"


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
        self._dash_indexes: dict[str, tuple[float, Future[DashIndex]]] = {}
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
        with self._records_lock:
            record = self._records.get(stream_id)
            if record is None or record.owner_user_id != owner_user_id:
                raise StreamNotFoundError("Remote stream not found.")
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
        with self._records_lock:
            cached = self._dash_indexes.get(track.url)
            leading = cached is None or cached[0] <= monotonic()
            if leading:
                if len(self._dash_indexes) >= _DASH_INDEX_CACHE_ENTRIES:
                    self._dash_indexes.pop(next(iter(self._dash_indexes)), None)
                probe: Future[DashIndex] = Future()
                self._dash_indexes[track.url] = (monotonic() + _DASH_INDEX_TTL_SECONDS, probe)
            else:
                probe = cached[1]
        if not leading:
            return probe.result(timeout=_DASH_INDEX_JOIN_SECONDS)
        try:
            index = self._probe_dash_index(owner_user_id, track, slots=slots)
        except BaseException as exc:
            with self._records_lock:
                if self._dash_indexes.get(track.url, (0, None))[1] is probe:
                    self._dash_indexes.pop(track.url, None)
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


def _parse_dash_index(data: bytes, *, content_length: int | None) -> DashIndex:
    position = 0
    saw_ftyp = False
    saw_moov = False
    while position + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, position)
        header_size = 8
        if size == 1:
            if position + 16 > len(data):
                break
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header_size = 16
        if size < header_size or position + size > len(data):
            break
        if kind == b"ftyp":
            saw_ftyp = True
        elif kind == b"moov":
            saw_moov = True
        if kind != b"sidx":
            position += size
            continue
        cursor = position + header_size
        if cursor + 12 > position + size:
            break
        version = data[cursor]
        cursor += 4
        _reference_id, timescale = struct.unpack_from(">II", data, cursor)
        cursor += 8
        if timescale <= 0:
            break
        if version == 0:
            if cursor + 8 > position + size:
                break
            _earliest, first_offset = struct.unpack_from(">II", data, cursor)
            cursor += 8
        elif version == 1:
            if cursor + 16 > position + size:
                break
            _earliest, first_offset = struct.unpack_from(">QQ", data, cursor)
            cursor += 16
        else:
            break
        if cursor + 4 > position + size:
            break
        _reserved, reference_count = struct.unpack_from(">HH", data, cursor)
        cursor += 4
        if reference_count <= 0 or reference_count > 8_192 or cursor + reference_count * 12 > position + size:
            break
        duration = 0
        referenced_bytes = 0
        max_referenced_size = 0
        for _index in range(reference_count):
            referenced_size, subsegment_duration, _sap = struct.unpack_from(">III", data, cursor)
            cursor += 12
            if referenced_size & 0x80000000:
                raise UnsupportedPlaybackError("Indirect DASH indexes are not supported.")
            referenced_size &= 0x7FFFFFFF
            if referenced_size <= 0:
                raise UnsupportedPlaybackError("The DASH index contains an empty media reference.")
            referenced_bytes += referenced_size
            max_referenced_size = max(max_referenced_size, referenced_size)
            duration += subsegment_duration
        media_start = position + size + first_offset
        media_end = media_start + referenced_bytes
        if (
            position <= 0
            or not saw_ftyp
            or not saw_moov
            or duration <= 0
            or media_start < position + size
            or (content_length is not None and media_end > content_length)
        ):
            break
        return DashIndex(
            initialization_end=position - 1,
            index_start=position,
            index_end=position + size - 1,
            duration_seconds=duration / timescale,
            max_referenced_size=max_referenced_size,
            content_length=content_length or media_end,
        )
    raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.")


def _ebml_vint(data: bytes, position: int, *, element_id: bool = False) -> tuple[int, int]:
    """Read one EBML variable-length integer; IDs keep their length marker, sizes drop it."""
    first = data[position]
    length = 9 - first.bit_length()
    if first == 0 or position + length > len(data):
        raise IndexError("truncated EBML integer")
    value = first if element_id else first & (0xFF >> length)
    value = int.from_bytes(bytes([value]) + data[position + 1 : position + length], "big")
    if not element_id and value == (1 << (7 * length)) - 1:
        raise IndexError("unknown-size EBML element")
    return value, position + length


def _parse_webm_index(data: bytes, *, content_length: int | None) -> DashIndex:
    """Find a WebM (YouTube ``webm_dash``) init range and its Cues, the SegmentBase index dash.js reads."""
    try:
        element, position = _ebml_vint(data, 0, element_id=True)
        size, position = _ebml_vint(data, position)
        if element != 0x1A45DFA3:
            raise IndexError("not EBML")
        element, position = _ebml_vint(data, position + size, element_id=True)
        size, position = _ebml_vint(data, position)
        if element != 0x18538067:
            raise IndexError("no Segment")
        segment_start, segment_end = position, position + size
        timecode_scale, duration, cues = 1_000_000, None, None
        while cues is None:
            element_start = position
            element, position = _ebml_vint(data, position, element_id=True)
            size, position = _ebml_vint(data, position)
            end = position + size
            if element == 0x1F43B675:
                raise IndexError("Cues after media")
            if element == 0x1C53BB6B:
                cues = (element_start, position, end)
            elif element == 0x1549A966:
                cursor = position
                while cursor < end:
                    child, cursor = _ebml_vint(data, cursor, element_id=True)
                    child_size, cursor = _ebml_vint(data, cursor)
                    value = data[cursor : cursor + child_size]
                    if child == 0x2AD7B1:
                        timecode_scale = int.from_bytes(value, "big")
                    elif child == 0x4489:
                        duration = struct.unpack(">f" if child_size == 4 else ">d", value)[0]
                    cursor += child_size
            position = end
        cues_start, cursor, cues_end = cues
        if cues_end > len(data):
            raise IndexError("Cues exceed the probe")
        positions: list[int] = []
        while cursor < cues_end:
            element, cursor = _ebml_vint(data, cursor, element_id=True)
            size, cursor = _ebml_vint(data, cursor)
            point_end = cursor + size
            if element == 0xBB:
                child_cursor, cluster = cursor, None
                while child_cursor < point_end and cluster is None:
                    child, child_cursor = _ebml_vint(data, child_cursor, element_id=True)
                    child_size, child_cursor = _ebml_vint(data, child_cursor)
                    if child == 0xB7:
                        track_cursor = child_cursor
                        while track_cursor < child_cursor + child_size:
                            leaf, track_cursor = _ebml_vint(data, track_cursor, element_id=True)
                            leaf_size, track_cursor = _ebml_vint(data, track_cursor)
                            if leaf == 0xF1:
                                cluster = int.from_bytes(data[track_cursor : track_cursor + leaf_size], "big")
                                break
                            track_cursor += leaf_size
                    child_cursor += child_size
                if cluster is not None:
                    positions.append(segment_start + cluster)
            cursor = point_end
    except (IndexError, struct.error):
        raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.") from None
    media_end = min(segment_end, content_length) if content_length is not None else segment_end
    bounds = [*positions, media_end]
    sizes = [end - start for start, end in zip(bounds, bounds[1:])]
    if not positions or not duration or positions[0] < cues_end or any(size <= 0 for size in sizes):
        raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.")
    return DashIndex(
        initialization_end=cues_start - 1,
        index_start=cues_start,
        index_end=cues_end - 1,
        duration_seconds=duration * timecode_scale / 1_000_000_000,
        max_referenced_size=max(sizes),
        content_length=content_length or media_end,
    )


def _auto_ladder(videos: Iterable[MediaTrack], top: MediaTrack) -> tuple[MediaTrack, ...]:
    """The DASH rungs Auto may move among: ``top``'s container and codec, one per quality, up to ``top``.

    One codec keeps every switch inside one SourceBuffer without a codec change.
    """
    # HDR and SDR tracks of one codec are not told apart; YouTube does not mix them within one codec.
    family = (top.ext, _video_codec_family(top.vcodec))
    best: dict[str, MediaTrack] = {}
    for track in videos:
        if (track.ext, _video_codec_family(track.vcodec)) != family or (track.height or 0) > (top.height or 0):
            continue
        label = _rendition_label(track)
        if label not in best or (track.bitrate_kbps or 0) > (best[label].bitrate_kbps or 0):
            best[label] = track
    return tuple(sorted(best.values(), key=lambda track: (_video_score(track), track.bitrate_kbps or 0)))


# (level, max picture size, max luma sample rate) from the VP9 bitstream spec, annex A.
_VP9_LEVELS = (
    (10, 36_864, 829_440), (11, 73_728, 2_764_800), (20, 122_880, 4_608_000), (21, 245_760, 9_216_000),
    (30, 552_960, 20_736_000), (31, 983_040, 36_864_000), (40, 2_228_224, 83_558_400),
    (41, 2_228_224, 160_432_128), (50, 8_912_896, 311_951_360), (51, 8_912_896, 588_251_136),
    (52, 8_912_896, 1_176_502_272), (60, 35_651_584, 1_176_502_272), (61, 35_651_584, 2_353_004_544),
)


def _manifest_video_codec(track: MediaTrack) -> str:
    """Providers label WebM VP9 plainly "vp9"; MediaCapabilities (and so dash.js) needs the full RFC 6381 form."""
    if track.vcodec.lower() != "vp9":
        return track.vcodec
    picture = (track.width or 0) * (track.height or 0)
    rate = picture * (track.frame_rate or 30)
    level = next((level for level, max_picture, max_rate in _VP9_LEVELS if picture <= max_picture and rate <= max_rate), 62)
    return f"vp09.00.{level}.08"  # profile 0, 8-bit: the only VP9 the DASH ladder accepts


def _dash_manifest(
    record: _StreamRecord,
    generation: int,
    videos: list[tuple[MediaTrack, DashIndex]],
    audio_index: DashIndex,
) -> bytes:
    audio = record.plan.audio
    if not videos or audio is None or record.plan.selected_rendition_id is None:
        raise UnsupportedPlaybackError("This playback session does not expose DASH content.")
    duration = max(audio_index.duration_seconds, *(index.duration_seconds for _video, index in videos))
    duration_text = f"PT{duration:.3f}S"
    audio_bandwidth = max(1, round((audio.bitrate_kbps or 128) * 1_000))
    base = f"/api/remote-streams/{record.stream_id}/dash/{generation}"

    def representation(position: int, video: MediaTrack, index: DashIndex) -> str:
        frame_rate = f' frameRate={quoteattr(_rendition_frame_rate(video))}' if video.frame_rate else ""
        return (
            f'      <Representation id="video-{position}" bandwidth="{max(1, round((video.bitrate_kbps or 1) * 1_000))}" '
            f'codecs={quoteattr(_manifest_video_codec(video))} width="{video.width or 1}" height="{video.height or 1}"{frame_rate}>\n'
            f'        <BaseURL>{base}/video-{position}</BaseURL>\n'
            f'        <SegmentBase indexRange="{index.index_start}-{index.index_end}" indexRangeExact="true">\n'
            f'          <Initialization range="0-{index.initialization_end}"/>\n'
            '        </SegmentBase>\n'
            '      </Representation>\n'
        )

    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        f'type="static" mediaPresentationDuration={quoteattr(duration_text)} minBufferTime="PT1.5S">\n'
        f'  <Period id="period" duration={quoteattr(duration_text)}>\n'
        f'    <AdaptationSet id="video" contentType="video" mimeType={quoteattr(videos[0][0].content_type)} segmentAlignment="true" startWithSAP="1">\n'
        + "".join(representation(position, video, index) for position, (video, index) in enumerate(videos))
        + '    </AdaptationSet>\n'
        '    <AdaptationSet id="audio" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" startWithSAP="1">\n'
        f'      <Representation id="audio" bandwidth="{audio_bandwidth}" codecs={quoteattr(audio.acodec)}>\n'
        f'        <BaseURL>{base}/audio</BaseURL>\n'
        f'        <SegmentBase indexRange="{audio_index.index_start}-{audio_index.index_end}" indexRangeExact="true">\n'
        f'          <Initialization range="0-{audio_index.initialization_end}"/>\n'
        '        </SegmentBase>\n'
        '      </Representation>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    return manifest.encode()


def parse_range_header(value: str | None, content_length: int | None) -> ByteRange | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized.startswith("bytes=") or "," in normalized:
        raise RangeNotSatisfiableError(content_length)
    start_text, separator, end_text = normalized[6:].partition("-")
    if not separator:
        raise RangeNotSatisfiableError(content_length)
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else None
            if start < 0 or (end is not None and end < start) or (content_length is not None and start >= content_length):
                raise RangeNotSatisfiableError(content_length)
            if content_length is not None and end is not None:
                end = min(end, content_length - 1)
            return ByteRange(start, end)
        suffix = int(end_text)
        if suffix <= 0:
            raise RangeNotSatisfiableError(content_length)
        if content_length is None:
            return ByteRange(None, suffix)
        return ByteRange(max(0, content_length - suffix), content_length - 1)
    except ValueError as exc:
        raise RangeNotSatisfiableError(content_length) from exc


def _bounded_byte_range(
    byte_range: ByteRange | None,
    content_length: int | None,
    byte_limit: int,
) -> ByteRange | None:
    if byte_range is None:
        if content_length is not None and content_length <= byte_limit:
            return None
        end = byte_limit - 1
        if content_length is not None:
            end = min(end, content_length - 1)
        return ByteRange(0, end)
    if byte_range.start is None:
        return ByteRange(None, min(byte_range.end or byte_limit, byte_limit))
    budget_end = byte_range.start + byte_limit - 1
    end = budget_end if byte_range.end is None else min(byte_range.end, budget_end)
    if content_length is not None:
        end = min(end, content_length - 1)
    return ByteRange(byte_range.start, end)


def redact_preview_info(info: Mapping[str, Any]) -> dict[str, Any]:
    """Return public metadata without upstream addresses, headers, or fragments."""

    public_keys = {
        "_type", "id", "title", "description", "uploader", "channel", "duration",
        "webpage_url", "channel_url", "uploader_url", "availability", "extractor", "extractor_key", "upload_date", "view_count",
        "channel_follower_count", "format_resolution",
    }
    format_keys = {
        "format_id", "format_note", "ext", "vcodec", "acodec", "width", "height", "fps", "tbr", "abr",
        "filesize", "filesize_approx", "protocol",
    }
    result = {key: info[key] for key in public_keys if key in info}
    formats = info.get("formats")
    if isinstance(formats, list):
        result["formats"] = [
            {key: candidate[key] for key in format_keys if key in candidate}
            for candidate in formats
            if isinstance(candidate, Mapping)
        ]
    entries = info.get("entries")
    if isinstance(entries, list):
        result["entries"] = [redact_preview_info(entry) for entry in entries[:100] if isinstance(entry, Mapping)]
    return result


def _track(candidate: Mapping[str, Any]) -> MediaTrack | None:
    url = candidate.get("url")
    format_id = candidate.get("format_id")
    protocol = str(candidate.get("protocol") or "").lower()
    if not isinstance(url, str) or not url or not format_id or protocol not in {"http", "https"}:
        return None
    ext = str(candidate.get("ext") or "").lower()
    raw_vcodec = str(candidate.get("vcodec") or "none").strip()
    raw_acodec = str(candidate.get("acodec") or "none").strip()
    # ISO BMFF codec strings are not uniformly case-insensitive. In particular,
    # Chromium accepts the AV1 tier marker in ``av01.0.12M.08`` but rejects the
    # lowercased ``m`` form. Preserve provider spelling for manifests while the
    # codec-family helpers normalize only for comparisons.
    vcodec = "none" if raw_vcodec.lower() == "none" else raw_vcodec
    acodec = "none" if raw_acodec.lower() == "none" else raw_acodec
    content_type = _content_type(ext, vcodec)
    expires_at = candidate.get("url_expiry")
    if not isinstance(expires_at, (int, float)):
        try:
            query_expiry = parse_qs(urlsplit(url).query).get("expire", [None])[0]
            expires_at = float(query_expiry) if query_expiry else None
        except (TypeError, ValueError):
            expires_at = None
    headers = candidate.get("http_headers") if isinstance(candidate.get("http_headers"), Mapping) else {}
    content_length = candidate.get("filesize")
    estimated_content_length = candidate.get("filesize_approx")
    downloader_options = candidate.get("downloader_options")
    chunk_size = downloader_options.get("http_chunk_size") if isinstance(downloader_options, Mapping) else None
    return MediaTrack(
        format_id=str(format_id),
        url=url,
        content_type=content_type,
        protocol=protocol,
        ext=ext,
        vcodec=vcodec,
        acodec=acodec,
        height=candidate.get("height") if isinstance(candidate.get("height"), int) else None,
        content_length=content_length if isinstance(content_length, int) and content_length >= 0 else None,
        expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
        headers={str(key): str(value) for key, value in headers.items()},
        width=candidate.get("width") if isinstance(candidate.get("width"), int) else None,
        frame_rate=float(candidate["fps"]) if isinstance(candidate.get("fps"), (int, float)) else None,
        bitrate_kbps=float(candidate["tbr"]) if isinstance(candidate.get("tbr"), (int, float)) else None,
        estimated_content_length=(
            estimated_content_length
            if isinstance(estimated_content_length, int) and estimated_content_length >= 0
            else None
        ),
        source_container=str(candidate.get("container") or "").lower() or None,
        chunk_size=chunk_size if isinstance(chunk_size, int) and not isinstance(chunk_size, bool) and chunk_size > 0 else None,
        language_preference=preference if isinstance((preference := candidate.get("language_preference")), int) and not isinstance(preference, bool) else 0,
    )


def _progressive(track: MediaTrack) -> bool:
    return track.protocol in {"http", "https"}


def _track_cache_fingerprint(track: MediaTrack) -> str:
    """Identify media bytes without retaining an expiring URL or private headers."""

    stable_fields = (
        "track-v1",
        track.format_id,
        track.content_type,
        track.protocol,
        track.ext,
        track.vcodec,
        track.acodec,
        track.width,
        track.height,
        track.frame_rate,
        track.bitrate_kbps,
        track.content_length,
        track.estimated_content_length,
        track.source_container,
    )
    payload = json.dumps(stable_fields, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _stable_source_identity(info: Mapping[str, Any], source_url: str) -> str:
    """Use provider media identity for cache reuse without retaining signed URLs."""

    remote_id = str(info.get("id") or "").strip()
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").strip().lower()
    if remote_id and extractor == "youtube":
        return f"youtube:{remote_id}"
    return f"url:{_canonical_remote_source_url(source_url)}"


def _canonical_remote_source_url(value: str) -> str:
    """Mirror the frontend's generic source identity normalization."""

    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        if not parsed.scheme or not parsed.hostname:
            return normalized
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parsed.port
        except ValueError:
            return normalized
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        port_suffix = f":{port}" if port is not None and not default_port else ""
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
        query = urlencode(
            sorted(
                (
                    (name, item)
                    for name, item in parse_qsl(parsed.query, keep_blank_values=True)
                    if not name.casefold().startswith("utm_")
                ),
                key=lambda pair: pair[0],
            ),
            doseq=True,
        )
        return urlunsplit((
            parsed.scheme.lower(),
            f"{userinfo}{hostname}{port_suffix}",
            parsed.path or "/",
            query,
            "",
        ))
    except (UnicodeError, ValueError):
        return normalized


def _video_score(track: MediaTrack) -> tuple[int, int, int]:
    compatible = int(_video_codec_family(track.vcodec) != "unknown")
    container = 2 if track.ext == "mp4" else 1 if track.ext == "webm" else 0
    height = track.height or 0
    return height, compatible, container


def _audio_score(track: MediaTrack) -> tuple[int, int, int, float]:
    """The original language first (never an auto-dub), then codec, container and bitrate."""
    compatible = int(_audio_codec_family(track.acodec) != "unknown")
    container = 2 if track.ext in {"m4a", "mp4"} else 1 if track.ext in {"webm", "mp3"} else 0
    return track.language_preference, compatible, container, track.bitrate_kbps or 0


def _content_type(ext: str, vcodec: str) -> str:
    if vcodec == "none":
        return {"m4a": "audio/mp4", "mp3": "audio/mpeg", "webm": "audio/webm"}.get(ext, "application/octet-stream")
    return {"mp4": "video/mp4", "webm": "video/webm"}.get(ext, "application/octet-stream")


def _video_codec_family(codec: str) -> str:
    normalized = codec.lower()
    if normalized.startswith(("avc", "h264")):
        return "avc"
    if normalized.startswith(("hev", "hvc", "h265")):
        return "hevc"
    if normalized.startswith(("vp09", "vp9")):
        return "vp9"
    if normalized.startswith(("vp08", "vp8")):
        return "vp8"
    if normalized.startswith(("av01", "av1")):
        return "av1"
    return "unknown"


def _audio_codec_family(codec: str) -> str:
    normalized = codec.lower()
    if normalized.startswith(("mp4a", "aac")):
        return "aac"
    if normalized.startswith("opus"):
        return "opus"
    if normalized.startswith("vorbis"):
        return "vorbis"
    if normalized.startswith("mp3"):
        return "mp3"
    return "unknown"


def _profile(track: MediaTrack) -> str | None:
    video = _video_codec_family(track.vcodec)
    audio = _audio_codec_family(track.acodec)
    if track.ext in {"mp4", "webm"} and video != "unknown" and audio != "unknown":
        return f"{track.ext}-{video}-{audio}"
    return None


def _audio_profile(track: MediaTrack) -> str | None:
    audio = _audio_codec_family(track.acodec)
    if audio == "unknown":
        return None
    if track.ext in {"m4a", "mp4"} and audio == "aac":
        return "m4a-aac"
    if track.ext == "webm" and audio in {"opus", "vorbis"}:
        return f"webm-{audio}"
    if track.ext == "mp3" and audio == "mp3":
        return "mp3-mp3"
    return None


def _known_video_container(track: MediaTrack) -> bool:
    return track.ext in {"mp4", "webm"} and _video_codec_family(track.vcodec) != "unknown"


def _legacy_hls_video(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    return (
        track.ext == "mp4"
        and _video_codec_family(track.vcodec) == "avc"
        and track.height is not None
        and 0 < track.height <= 1080
        and "mp4-avc-aac" in capabilities.supported_profiles
    )


def _split_video_supported(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    prefix = f"{track.ext}-{_video_codec_family(track.vcodec)}-"
    return _known_video_container(track) and any(
        profile.startswith(prefix) for profile in capabilities.supported_profiles
    )


def _dash_video_supported(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    if track.ext == "webm":
        # Exactly "vp9": YouTube's 10-bit "vp9.2" is not an MSE codec string, and
        # every such source also carries the 8-bit track.
        return (
            track.source_container == "webm_dash"
            and track.vcodec.lower() == "vp9"
            and "dash-webm-vp9" in capabilities.supported_profiles
        )
    return (
        track.ext == "mp4"
        and track.source_container == "mp4_dash"
        and _video_codec_family(track.vcodec) in {"avc", "av1"}
        and _split_video_supported(track, capabilities)
    )


def _dash_audio_supported(track: MediaTrack) -> bool:
    return (
        track.ext in {"m4a", "mp4"}
        and track.source_container in {"m4a_dash", "mp4_dash"}
        and _audio_codec_family(track.acodec) == "aac"
    )


def _rendition_key(track: MediaTrack, transport: PlaybackTransport) -> _RenditionKey:
    frame_rate = _rendition_frame_rate(track)
    return (
        transport,
        track.ext,
        _video_codec_family(track.vcodec),
        _audio_codec_family(track.acodec),
        frame_rate,
        track.width,
        track.height,
    )


def _rendition_quality_key(track: MediaTrack) -> str:
    # The selector must not expose two visually identical choices just because
    # providers report a cropped raster (338px) beside a nominal one (360px).
    return _rendition_label(track)


def _rendition_frame_rate(track: MediaTrack) -> str:
    return str(int(round(track.frame_rate or 0)))


def _rendition_label(track: MediaTrack) -> str:
    height = track.height
    if height is None:
        label = "Auto"
    else:
        standard_heights = (144, 240, 360, 480, 720, 1080, 1440, 2160)
        nominal_height = min(standard_heights, key=lambda candidate: abs(candidate - height))
        # Providers often report the encoded 16:9 raster (for example 1012) rather
        # than the conventional quality name (1080p). Keep unfamiliar formats exact.
        display_height = nominal_height if abs(nominal_height - height) / nominal_height <= 0.08 else height
        label = "4K" if display_height >= 2160 else f"{display_height}p"
    if track.frame_rate is not None and track.frame_rate >= 50:
        label += f" · {round(track.frame_rate):d} fps"
    return label


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
