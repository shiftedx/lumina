"""Guarded relay for upstream HLS (the first supported segmented-transport path).

The relay parses an upstream HLS master and its media playlists, rewrites every
address the browser could see into an authenticated, owner-scoped Lumina address,
and proxies each variant playlist, segment, initialization resource, and
encryption key through the public-source policy at fetch time. No upstream URL,
host, cookie, request header, or signed token ever reaches the frontend.

This is a distinct seam from the local VOD HLS packager (which muxes bounded
split tracks into Lumina-authored fMP4). The relay never materializes upstream
media to disk; it holds only an in-memory, bounded, per-generation map of opaque
resource identifiers to the upstream addresses they stand for.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from time import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, urlsplit

from app.services.hls_manifest import (
    HlsResourceKind,
    MalformedManifestError,
    classify,
    rewrite_master,
    rewrite_media,
)
from app.services.hls_relay_support import extract_master_url, extract_request_headers
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError
from app.services.remote_streaming import (
    ByteRange,
    PlaybackDescriptor,
    StreamNotFoundError,
    StreamResponseSpec,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
    _ByteLimitedBody,
    ResponseSlots,
    acquire_slots,
    release_slots,
    with_close,
    parse_range_header,
)

# Re-exported so the support predicate is reachable from one relay module.
__all__ = ["HlsRelayService", "RelayFetcher", "UpstreamRelayExpiredError", "extract_master_url"]

_MASTER_CONTENT_TYPE = "application/vnd.apple.mpegurl"
_AUTH_EXPIRY_STATUSES = frozenset({401, 403, 404, 410})


class UpstreamRelayExpiredError(UnsupportedPlaybackError):
    """An upstream HLS resource expired; the session was re-resolved server-side."""


class RelayFetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        byte_range: ByteRange | None = None,
        timeout_seconds: float | None = None,
    ) -> UpstreamMediaResponse: ...


class RelayResolver(Protocol):
    def resolve(self, source_url: str, owner_user_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _RelayResource:
    kind: HlsResourceKind
    url: str = field(repr=False)


@dataclass
class _RelayGeneration:
    master_url: str = field(repr=False)
    request_headers: dict[str, str] = field(default_factory=dict, repr=False)
    resources_by_id: dict[str, _RelayResource] = field(default_factory=dict, repr=False)
    resources_by_url: dict[str, str] = field(default_factory=dict, repr=False)
    rendered_master: bytes | None = field(default=None, repr=False)


@dataclass
class _RelayRecord:
    stream_id: str
    owner_user_id: str
    source_url: str = field(repr=False)
    created_at: float = 0.0
    last_accessed_at: float = 0.0
    resolution_expires_at: float = 0.0
    generation: int = 1
    generations: dict[int, _RelayGeneration] = field(default_factory=dict, repr=False)
    refresh_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    active_refs: int = 0
    retired: bool = False
    cleaned: bool = False


class HlsRelayService:
    """Owner-bound HLS relay sessions with bounded resources and fail-closed fetches."""

    LIVE = False  # the live subclass flips this: not seekable, presented as live
    # At the member's cap, a session silent this long gives way to a new one (None: refuse until the idle TTL).
    ABANDONED_AFTER_SECONDS: float | None = None

    def __init__(
        self,
        *,
        resolver: RelayResolver,
        fetcher: RelayFetcher,
        policy: PublicSourcePolicy | None = None,
        token_factory: Callable[[], str] | None = None,
        resource_token_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time,
        idle_ttl_seconds: float = 30 * 60,
        max_lifetime_seconds: float = 6 * 60 * 60,
        refresh_margin_seconds: float = 60,
        default_resolution_ttl_seconds: float = 30 * 60,
        max_streams_global: int = 24,
        max_streams_per_user: int = 4,
        max_concurrent_reads_global: int = 16,
        max_concurrent_reads_per_user: int = 4,
        max_manifest_bytes: int = 4 * 1024 * 1024,
        max_bytes_per_response: int = 16 * 1024 * 1024,
        max_key_bytes: int = 4096,
        max_resources_per_generation: int = 8192,
        request_timeout_seconds: float = 30,
    ) -> None:
        if max_concurrent_reads_global < 1 or max_concurrent_reads_per_user < 1:
            raise ValueError("Relay concurrency limits must be positive.")
        if min(max_manifest_bytes, max_bytes_per_response, max_key_bytes, max_resources_per_generation) < 1:
            raise ValueError("Relay byte and resource budgets must be positive.")
        self._resolver = resolver
        self._fetcher = fetcher
        self._policy = policy or PublicSourcePolicy()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._resource_token_factory = resource_token_factory or (lambda: secrets.token_urlsafe(18))
        self._clock = clock
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_lifetime_seconds = max_lifetime_seconds
        self._refresh_margin_seconds = refresh_margin_seconds
        self._default_resolution_ttl_seconds = default_resolution_ttl_seconds
        self._max_streams_global = max_streams_global
        self._max_streams_per_user = max_streams_per_user
        self._reads_global = threading.BoundedSemaphore(max_concurrent_reads_global)
        self._reads_per_user_limit = max_concurrent_reads_per_user
        self._reads_by_user: dict[str, threading.BoundedSemaphore] = {}
        self._max_manifest_bytes = max_manifest_bytes
        self._max_bytes_per_response = max_bytes_per_response
        self._max_key_bytes = max_key_bytes
        self._max_resources_per_generation = max_resources_per_generation
        self._request_timeout_seconds = request_timeout_seconds
        self._records: dict[str, _RelayRecord] = {}
        self.on_end = None  # callable(info dict): the admin Activity history hook
        self._records_lock = threading.RLock()

    # -- registration & lifecycle ------------------------------------------

    def register(
        self,
        *,
        owner_user_id: str,
        source_url: str,
        info: Mapping[str, Any],
        browser_capabilities: Any = None,
    ) -> PlaybackDescriptor:
        del browser_capabilities  # hls.js performs client-side rendition selection.
        master_url = extract_master_url(info)
        if master_url is None:
            return self._unsupported_descriptor()
        # A hard navigation never releases its session: reap idle ones first so an abandoned tab never holds the cap.
        self.expire_idle()
        now = self._clock()
        abandoned: _RelayRecord | None = None
        with self._records_lock:
            owned = [record for record in self._records.values() if record.owner_user_id == owner_user_id]
            if len(owned) >= self._max_streams_per_user and self.ABANDONED_AFTER_SECONDS is not None:
                oldest = min(owned, key=lambda record: record.last_accessed_at)
                if oldest.active_refs == 0 and now - oldest.last_accessed_at >= self.ABANDONED_AFTER_SECONDS:
                    abandoned = self._records.pop(oldest.stream_id)
                    self._retire_locked(abandoned)
                    owned.remove(oldest)
            if len(self._records) >= self._max_streams_global:
                raise UnsupportedPlaybackError("The relay is at capacity.")
            if len(owned) >= self._max_streams_per_user:
                raise UnsupportedPlaybackError("This household member has too many active relay sessions.")
            stream_id = self._unique_stream_id()
            record = _RelayRecord(
                stream_id=stream_id,
                owner_user_id=owner_user_id,
                source_url=source_url,
                created_at=now,
                last_accessed_at=now,
                resolution_expires_at=self._resolution_expiry(master_url, now),
                generation=1,
                generations={1: _RelayGeneration(master_url=master_url, request_headers=extract_request_headers(info))},
            )
            self._records[stream_id] = record
        if abandoned is not None:
            self._ended(abandoned)
        return self._descriptor(record)

    def describe(self, owner_user_id: str, stream_id: str) -> PlaybackDescriptor:
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            return self._descriptor(record)
        finally:
            self._release_ref(record)

    def refresh(self, owner_user_id: str, stream_id: str) -> PlaybackDescriptor:
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            self._refresh_if_generation(record, record.generation)
            return self._descriptor(record)
        finally:
            self._release_ref(record)

    def release(self, owner_user_id: str, stream_id: str) -> None:
        with self._records_lock:
            record = self._records.get(stream_id)
            if record is None or record.owner_user_id != owner_user_id:
                raise StreamNotFoundError("Relay stream not found.")
            self._records.pop(stream_id)
            self._retire_locked(record)
        self._ended(record)

    def list_streams(self) -> list[dict[str, Any]]:
        """Live relays for the admin Activity page: no tokens, only the source address and timings."""
        with self._records_lock:
            return [self._info(record) for record in self._records.values()]

    def stop_stream(self, stream_id: str, *, by_admin: bool = False) -> bool:
        with self._records_lock:
            record = self._records.pop(stream_id, None)
            if record is None:
                return False
            self._retire_locked(record)
        self._ended(record, by_admin=by_admin)
        return True

    def _info(self, record: _RelayRecord) -> dict[str, Any]:
        lag = self._clock() - record.created_at
        idle = self._clock() - record.last_accessed_at
        now = time()
        return {
            "stream_id": record.stream_id, "user_id": record.owner_user_id, "source_url": record.source_url, "live": self.LIVE,
            "started": now - lag, "last_seen": now - idle,
        }

    def _ended(self, record: _RelayRecord, by_admin: bool = False) -> None:
        if self.on_end is not None:
            try:
                self.on_end({**self._info(record), "by_admin": by_admin})
            except Exception:  # history must never break a relay's teardown
                logging.getLogger(__name__).exception("activity history write failed")

    def expire_idle(self) -> int:
        now = self._clock()
        gone: list[_RelayRecord] = []
        with self._records_lock:
            for stream_id, record in list(self._records.items()):
                if now - record.last_accessed_at >= self._idle_ttl_seconds or now - record.created_at >= self._max_lifetime_seconds:
                    self._records.pop(stream_id)
                    self._retire_locked(record)
                    gone.append(record)
        for record in gone:
            self._ended(record)
        return len(gone)

    def close_all(self) -> int:
        with self._records_lock:
            records = list(self._records.values())
            for record in records:
                self._retire_locked(record)
            self._records.clear()
        return len(records)

    def has_owned_stream(self, owner_user_id: str, stream_id: str) -> bool:
        with self._records_lock:
            record = self._records.get(stream_id)
            return record is not None and record.owner_user_id == owner_user_id

    # -- serving -----------------------------------------------------------

    def serve_master(self, owner_user_id: str, stream_id: str, generation: int) -> StreamResponseSpec:
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            self._ensure_fresh(record)
            with record.refresh_lock:
                if generation != record.generation:
                    return self._redirect(record, record.generation, "master.m3u8")
                state = record.generations[record.generation]
                if state.rendered_master is None:
                    try:
                        rendered = self._render_master(record, record.generation, state)
                    except UpstreamRelayExpiredError:
                        self._refresh_if_generation(record, generation)
                        return self._redirect(record, record.generation, "master.m3u8")
                    state.rendered_master = rendered
                body = state.rendered_master
            return self._leased_response(record, self._manifest_spec(body, _MASTER_CONTENT_TYPE))
        except BaseException:
            self._release_ref(record)
            raise

    def serve_resource(
        self,
        owner_user_id: str,
        stream_id: str,
        generation: int,
        resource_id: str,
        range_header: str | None = None,
    ) -> StreamResponseSpec:
        record = self._acquire_record(owner_user_id, stream_id)
        read_slots: tuple[threading.BoundedSemaphore, threading.BoundedSemaphore] | None = None
        try:
            self._ensure_fresh(record)
            with record.refresh_lock:
                state = record.generations.get(generation)
                if state is None:
                    raise StreamNotFoundError("Relay generation not found.")
                resource = state.resources_by_id.get(resource_id)
                if resource is None:
                    raise StreamNotFoundError("Relay resource not found.")
            if resource.kind == "media_playlist":
                return self._serve_media_playlist(record, generation, state, resource)
            read_slots = self._acquire_read_slots(record.owner_user_id)
            spec = self._serve_media_bytes(state, resource, range_header)
            spec = self._read_leased_response(spec, read_slots)
            read_slots = None
            return self._leased_response(record, spec)
        except BaseException:
            if read_slots is not None:
                self._release_slots(read_slots)
            self._release_ref(record)
            raise

    def _serve_media_playlist(
        self,
        record: _RelayRecord,
        generation: int,
        state: _RelayGeneration,
        resource: _RelayResource,
    ) -> StreamResponseSpec:
        text = self._fetch_document(record, resource.url, state.request_headers, self._max_manifest_bytes)
        try:
            if classify(text) != "media":
                raise MalformedManifestError("A variant address did not resolve to a media playlist.")
            rewritten = rewrite_media(text, resource.url, self._make_rewriter(record, generation, state)).encode()
        except MalformedManifestError as exc:
            raise UnsupportedPlaybackError("The relay could not parse an upstream media playlist.") from exc
        if len(rewritten) > self._max_manifest_bytes:
            raise UnsupportedPlaybackError("The rewritten HLS media playlist exceeds the manifest byte budget.")
        return self._leased_response(record, self._manifest_spec(rewritten, _MASTER_CONTENT_TYPE))

    def _serve_media_bytes(
        self,
        state: _RelayGeneration,
        resource: _RelayResource,
        range_header: str | None,
    ) -> StreamResponseSpec:
        self._validate_relay_url(resource.url)
        budget = self._max_key_bytes if resource.kind == "key" else self._max_bytes_per_response
        byte_range = parse_range_header(range_header, None) if range_header else None
        upstream = self._safe_fetch(resource.url, dict(state.request_headers), byte_range)
        if upstream.status_code in _AUTH_EXPIRY_STATUSES:
            upstream.close()
            # Recovery is driven by a single client-issued refresh so one expiry
            # event triggers exactly one server-side re-resolution, never two.
            raise UpstreamRelayExpiredError("An upstream HLS resource expired during playback.")
        if upstream.status_code not in {200, 206}:
            upstream.close()
            raise UnsupportedPlaybackError("The upstream source did not provide the requested HLS resource.")
        try:
            self._enforce_content_length(upstream, budget)
        except BaseException:
            upstream.close()
            raise
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Type": _resource_content_type(resource),
            "X-Content-Type-Options": "nosniff",
        }
        content_length = _header_value(upstream.headers, "content-length")
        if content_length is not None:
            headers["Content-Length"] = content_length
        if upstream.status_code == 206:
            content_range = _header_value(upstream.headers, "content-range")
            if content_range is not None:
                headers["Content-Range"] = content_range
        body = _ByteLimitedBody(upstream.body, upstream.close, budget)
        return StreamResponseSpec(upstream.status_code, dict(sorted(headers.items())), body, body.close)

    # -- upstream helpers --------------------------------------------------

    def _render_master(self, record: _RelayRecord, generation: int, state: _RelayGeneration) -> bytes:
        text = self._fetch_document(record, state.master_url, state.request_headers, self._max_manifest_bytes)
        try:
            if classify(text) != "master":
                raise MalformedManifestError("The upstream master address did not resolve to a master playlist.")
            rendered = rewrite_master(text, state.master_url, self._make_rewriter(record, generation, state)).encode()
        except MalformedManifestError as exc:
            raise UnsupportedPlaybackError("The relay could not parse the upstream HLS master.") from exc
        if len(rendered) > self._max_manifest_bytes:
            raise UnsupportedPlaybackError("The rewritten HLS master exceeds the manifest byte budget.")
        return rendered

    def _fetch_document(self, record: _RelayRecord, url: str, headers: dict[str, str], byte_limit: int) -> str:
        self._validate_relay_url(url)
        slots = self._acquire_read_slots(record.owner_user_id)
        response: UpstreamMediaResponse | None = None
        try:
            response = self._safe_fetch(url, dict(headers), None)
            if response.status_code in _AUTH_EXPIRY_STATUSES:
                raise UpstreamRelayExpiredError("An upstream HLS playlist expired during playback.")
            if response.status_code != 200:
                raise UnsupportedPlaybackError("The upstream source did not provide an HLS playlist.")
            collected = bytearray()
            for chunk in response.body:
                collected.extend(chunk)
                if len(collected) > byte_limit:
                    raise UnsupportedPlaybackError("An upstream HLS playlist exceeds the manifest byte budget.")
            return collected.decode("utf-8", errors="replace")
        finally:
            if response is not None:
                response.close()
            self._release_slots(slots)

    def _make_rewriter(self, record: _RelayRecord, generation: int, state: _RelayGeneration):
        prefix = f"/api/remote-streams/{record.stream_id}/relay/{generation}/r/"

        resolved: set[tuple[str, int]] = set()  # one DNS check per host per rewrite, not per segment

        def rewrite(kind: HlsResourceKind, url: str) -> str:
            self._validate_relay_url(url, resolved)
            resource_id = state.resources_by_url.get(url)
            if resource_id is None:
                if len(state.resources_by_id) >= self._max_resources_per_generation:
                    raise MalformedManifestError("The playlist exceeded the relay resource budget.")
                resource_id = self._unique_resource_id(state.resources_by_id)
                state.resources_by_id[resource_id] = _RelayResource(kind, url)
                state.resources_by_url[url] = resource_id
            return f"{prefix}{resource_id}"

        return rewrite

    def _safe_fetch(
        self,
        url: str,
        headers: dict[str, str],
        byte_range: ByteRange | None,
    ) -> UpstreamMediaResponse:
        """Fetch through the policy-guarded transport, failing closed on any error.

        A redirect into private space is revalidated inside the transport and
        surfaces as a wrapped policy denial; treat it, and any transport error,
        as fail-closed so no partial or private response ever reaches the caller.
        """

        try:
            return self._fetcher.fetch(
                url,
                headers=headers,
                byte_range=byte_range,
                timeout_seconds=self._request_timeout_seconds,
            )
        except PublicSourcePolicyError:
            raise
        except Exception as exc:
            if isinstance(exc.__cause__, PublicSourcePolicyError):
                raise PublicSourcePolicyError() from exc
            raise UnsupportedPlaybackError("The relay could not fetch an upstream HLS resource.") from exc

    def _validate_relay_url(self, url: str, resolved: set[tuple[str, int]] | None = None) -> None:
        try:
            self._policy.validate_url(url, resolved)
        except PublicSourcePolicyError:
            raise
        except ValueError as exc:
            raise PublicSourcePolicyError() from exc

    def _enforce_content_length(self, upstream: UpstreamMediaResponse, budget: int) -> None:
        value = _header_value(upstream.headers, "content-length")
        if value is None:
            return
        try:
            length = int(value)
        except ValueError:
            raise UnsupportedPlaybackError("The upstream source reported an invalid content length.") from None
        if length > budget:
            raise UnsupportedPlaybackError("The upstream HLS resource exceeds the per-response byte budget.")

    # -- freshness & refresh ----------------------------------------------

    def _ensure_fresh(self, record: _RelayRecord) -> None:
        if self._clock() + self._refresh_margin_seconds < record.resolution_expires_at:
            return
        self._refresh_if_generation(record, record.generation)

    def _refresh_if_generation(self, record: _RelayRecord, expected_generation: int) -> None:
        with record.refresh_lock:
            if record.generation != expected_generation:
                return
            info = self._resolver.resolve(record.source_url, record.owner_user_id)
            master_url = extract_master_url(info)
            if master_url is None:
                raise UnsupportedPlaybackError("The source no longer exposes an HLS master to relay.")
            record.generation += 1
            record.generations[record.generation] = _RelayGeneration(
                master_url=master_url,
                request_headers=extract_request_headers(info),
            )
            record.resolution_expires_at = self._resolution_expiry(master_url, self._clock())
            self._evict_generations(record)

    def _evict_generations(self, record: _RelayRecord) -> None:
        for generation in sorted(record.generations)[:-2]:
            record.generations.pop(generation, None)

    def _resolution_expiry(self, master_url: str, now: float) -> float:
        expiry = _url_expiry(master_url)
        if expiry is not None and expiry > now:
            return expiry
        return now + self._default_resolution_ttl_seconds

    # -- descriptors & specs ----------------------------------------------

    def _descriptor(self, record: _RelayRecord) -> PlaybackDescriptor:
        playback_url = f"/api/remote-streams/{record.stream_id}/relay/{record.generation}/master.m3u8"
        return PlaybackDescriptor(
            status="ready",
            stream_id=record.stream_id,
            transport="hls",
            media_kind="video",
            playback_url=playback_url,
            content_type=_MASTER_CONTENT_TYPE,
            has_video=True,
            has_audio=True,
            # A live edge is not a seekable VOD: no duration, no scrubbing.
            seekable=not self.LIVE,
            live=self.LIVE,
        )

    def _unsupported_descriptor(self) -> PlaybackDescriptor:
        return PlaybackDescriptor(
            status="unsupported",
            stream_id="",
            transport=None,
            media_kind=None,
            playback_url=None,
            content_type=None,
            has_video=False,
            has_audio=False,
            seekable=False,
            fallback_code="relay_source_unavailable",
            fallback_message="This source cannot be streamed here yet. You can still download it.",
        )

    def _manifest_spec(self, body: bytes, content_type: str) -> StreamResponseSpec:
        return StreamResponseSpec(
            200,
            {
                "Cache-Control": "private, no-store",
                "Content-Length": str(len(body)),
                "Content-Type": content_type,
                "X-Content-Type-Options": "nosniff",
            },
            [body],
        )

    def _redirect(self, record: _RelayRecord, generation: int, resource: str) -> StreamResponseSpec:
        location = f"/api/remote-streams/{record.stream_id}/relay/{generation}/{resource}"
        return self._leased_response(
            record,
            StreamResponseSpec(307, {"Cache-Control": "private, no-store", "Location": location}, []),
        )

    # -- reference counting, cleanup, concurrency -------------------------

    def _unique_stream_id(self) -> str:
        for _attempt in range(10):
            candidate = self._token_factory()
            if candidate and candidate not in self._records:
                return candidate
        raise RuntimeError("Could not allocate an opaque relay stream identifier.")

    def _unique_resource_id(self, used: Mapping[str, Any]) -> str:
        for _attempt in range(10):
            candidate = self._resource_token_factory()
            if candidate and candidate not in used:
                return candidate
        raise RuntimeError("Could not allocate an opaque relay resource identifier.")

    def _acquire_record(self, owner_user_id: str, stream_id: str) -> _RelayRecord:
        with self._records_lock:
            record = self._records.get(stream_id)
            if record is None or record.owner_user_id != owner_user_id:
                raise StreamNotFoundError("Relay stream not found.")
            with record.lifecycle_lock:
                if record.retired:
                    raise StreamNotFoundError("Relay stream not found.")
                record.active_refs += 1
                record.last_accessed_at = self._clock()
            return record

    @staticmethod
    def _retire_locked(record: _RelayRecord) -> None:
        with record.lifecycle_lock:
            record.retired = True

    def _release_ref(self, record: _RelayRecord) -> None:
        with record.lifecycle_lock:
            record.active_refs -= 1

    def _acquire_read_slots(self, owner_user_id: str) -> ResponseSlots:
        return acquire_slots(
            self._reads_global, self._reads_by_user, self._reads_per_user_limit, owner_user_id, self._records_lock,
            global_busy="The relay has too many active media responses.",
            user_busy="This household member already has too many active relay responses.",
        )

    _release_slots = staticmethod(release_slots)

    def _leased_response(self, record: _RelayRecord, spec: StreamResponseSpec) -> StreamResponseSpec:
        return with_close(spec, lambda: self._release_ref(record))

    @staticmethod
    def _read_leased_response(spec: StreamResponseSpec, slots: ResponseSlots) -> StreamResponseSpec:
        return with_close(spec, lambda: release_slots(slots))


def _resource_content_type(resource: _RelayResource) -> str:
    if resource.kind == "key":
        return "application/octet-stream"
    path = urlsplit(resource.url).path.lower()
    if path.endswith(".ts"):
        return "video/mp2t"
    if path.endswith((".m4s", ".mp4", ".m4v", ".m4a", ".cmfv", ".cmfa")):
        return "video/iso.segment"
    if path.endswith(".aac"):
        return "audio/aac"
    if path.endswith(".vtt"):
        return "text/vtt"
    return "application/octet-stream"


def _url_expiry(url: str) -> float | None:
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return None
    for key in ("expire", "expires", "Expires", "exp"):
        raw = query.get(key, [None])[0]
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    normalized = name.lower()
    return next((value for key, value in headers.items() if key.lower() == normalized), None)
