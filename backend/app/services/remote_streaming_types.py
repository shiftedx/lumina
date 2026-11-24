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
    personal_youtube_generation: str | None = field(default=None, repr=False)
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
