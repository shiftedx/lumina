"""Guarded relay for a currently-live YouTube HLS stream at the current edge.

This extends the VOD HLS relay (``hls_relay.HlsRelayService``) to live media. It
reuses the same fail-closed manifest rewriting, residual-address backstop,
fetch-time policy revalidation, owner binding, and byte/session budgets, and
differs only where "live" and "VOD" genuinely diverge:

- The session is presented as a live stream: not seekable, ``live=True``. It must
  never be registered as a seekable VOD session (#91).
- A live media playlist is fetched fresh on every request so playback stays at
  the current edge and the playlist advances; the per-generation resource map is
  bounded with a rolling window so a long broadcast can never grow it without
  limit while the current-edge segments stay resolvable.
- End of the broadcast is surfaced explicitly: when the source stops resolving an
  HLS master, a refresh returns an ``unsupported`` descriptor carrying
  ``live_stream_ended`` rather than failing opaquely.

Everything else — the guarded fetch, the residual-address scan, discontinuity
pass-through, redirect-into-private fail-closed, and the concurrency/lifetime
bounds — is inherited unchanged from the VOD relay.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable

from app.services.hls_manifest import HlsResourceKind, MalformedManifestError, classify, rewrite_media
from app.services.hls_relay import (
    _MASTER_CONTENT_TYPE,
    HlsRelayService,
    _RelayGeneration,
    _RelayRecord,
    _RelayResource,
)
from app.services.hls_relay_support import derive_lifecycle, extract_master_url, extract_request_headers
from app.services.remote_streaming import (
    PlaybackDescriptor,
    StreamNotFoundError,
    StreamResponseSpec,
    UnsupportedPlaybackError,
)


_TARGET_DURATION = re.compile(r"^#EXT-X-TARGETDURATION:\s*(\d+)\s*$", re.MULTILINE)
_SEGMENT_DURATION = re.compile(r"^#EXTINF:\s*([0-9]+(?:\.[0-9]*)?)", re.MULTILINE)


def _tighten_target_duration(text: str) -> str:
    """Lower EXT-X-TARGETDURATION to the longest listed segment (RFC 8216 rounding), never raise it.

    hls.js holds a live stream 3 target durations behind the edge and never closer than one; Twitch and Kick (IVS)
    advertise 6 over 2 s segments, so their streams sat 18 s behind the edge instead of 6.
    """
    longest = max((float(value) for value in _SEGMENT_DURATION.findall(text)), default=0.0)
    if not longest:
        return text
    fitted = max(1, int(longest + 0.5))
    return _TARGET_DURATION.sub(lambda match: f"#EXT-X-TARGETDURATION:{min(int(match[1]), fitted)}", text)


class LiveStreamEndedError(UnsupportedPlaybackError):
    """The live broadcast ended: the source no longer resolves an HLS master."""


class LiveHlsRelayService(HlsRelayService):
    """Owner-bound live-HLS relay: current-edge playback, bounded and fail-closed."""

    LIVE = True
    # A playing (or paused) hls.js reloads a live playlist every segment, 2-6 s: 15 s of silence is a reload or a
    # hard navigation that never released, and must not lock the member out of the next stream for a minute.
    ABANDONED_AFTER_SECONDS = 15.0

    def __init__(self, *, background: Callable[[Callable[[], None]], None] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._background = background or (lambda job: threading.Thread(target=job, name="lumina-live-prewarm", daemon=True).start())

    def register(self, *, owner_user_id: str, source_url: str, info: Any, browser_capabilities: Any = None) -> PlaybackDescriptor:
        descriptor = super().register(owner_user_id=owner_user_id, source_url=source_url, info=info, browser_capabilities=browser_capabilities)
        if descriptor.status == "ready":
            self._background(lambda: self._prewarm(owner_user_id, descriptor.stream_id))
        return descriptor

    def _prewarm(self, owner_user_id: str, stream_id: str) -> None:
        """Render the master and open the variant host's connection while the browser loads the player: its first
        two requests then skip an upstream round trip and a TLS handshake each (~300 ms of a cold start)."""
        try:
            record = self._acquire_record(owner_user_id, stream_id)
            try:
                self.serve_master(owner_user_id, stream_id, record.generation).close()
                state = record.generations.get(record.generation)
                variant = next((res.url for res in state.resources_by_id.values() if res.kind == "media_playlist"), None) if state else None
            finally:
                self._release_ref(record)
            preconnect = getattr(self._fetcher, "preconnect", None)
            if variant and preconnect is not None:
                preconnect(variant)
        except Exception:  # noqa: BLE001 - a warm-up never fails anything; the player's own requests report errors
            logging.getLogger(__name__).debug("Live relay prewarm failed", exc_info=True)

    # -- live descriptor ---------------------------------------------------

    def _ended_descriptor(self, record: _RelayRecord) -> PlaybackDescriptor:
        return PlaybackDescriptor(
            status="unsupported",
            stream_id=record.stream_id,
            transport=None,
            media_kind=None,
            playback_url=None,
            content_type=None,
            has_video=False,
            has_audio=False,
            seekable=False,
            live=True,
            fallback_code="live_stream_ended",
            fallback_message="This live stream has ended.",
        )

    # -- live refresh & end-of-stream -------------------------------------

    def refresh(self, owner_user_id: str, stream_id: str) -> PlaybackDescriptor:
        record = self._acquire_record(owner_user_id, stream_id)
        try:
            try:
                self._refresh_if_generation(record, record.generation)
            except LiveStreamEndedError:
                return self._ended_descriptor(record)
            return self._descriptor(record)
        finally:
            self._release_ref(record)

    def _refresh_if_generation(self, record: _RelayRecord, expected_generation: int) -> None:
        with record.refresh_lock:
            if record.generation != expected_generation:
                return
            try:
                info = self._resolver.resolve(record.source_url, record.owner_user_id)
            except Exception as exc:
                # yt-dlp reports an offline channel (Twitch UserNotLive) as an error.
                if "not currently live" in str(exc).lower():
                    raise LiveStreamEndedError("The live broadcast has ended.") from exc
                raise
            master_url = extract_master_url(info)
            if master_url is None or derive_lifecycle(info) != "live":
                # For a live source this is the honest end-of-broadcast signal,
                # not an opaque failure: the edge no longer exists to relay, and a
                # finished broadcast (was_live/post_live) is never relayed as live.
                raise LiveStreamEndedError("The live broadcast has ended.")
            record.generation += 1
            record.generations[record.generation] = _RelayGeneration(
                master_url=master_url,
                request_headers=extract_request_headers(info),
            )
            record.resolution_expires_at = self._resolution_expiry(master_url, self._clock())
            self._evict_generations(record)

    # -- live media playlist: fetched fresh, bounded, lock-guarded --------

    def _serve_media_playlist(
        self,
        record: _RelayRecord,
        generation: int,
        state: _RelayGeneration,
        resource: _RelayResource,
    ) -> StreamResponseSpec:
        # Fetch the live edge fresh every time (network happens outside the lock);
        # the rewrite that mutates the rolling resource map is serialized so a
        # concurrent segment lookup never observes a half-updated map.
        text = self._fetch_document(record, resource.url, state.request_headers, self._max_manifest_bytes)
        with record.refresh_lock:
            if record.generations.get(generation) is not state:
                raise StreamNotFoundError("Relay generation not found.")
            try:
                if classify(text) != "media":
                    raise MalformedManifestError("A variant address did not resolve to a media playlist.")
                rewritten = rewrite_media(_tighten_target_duration(text), resource.url, self._make_rewriter(record, generation, state)).encode()
            except MalformedManifestError as exc:
                raise UnsupportedPlaybackError("The relay could not parse an upstream live media playlist.") from exc
        if len(rewritten) > self._max_manifest_bytes:
            raise UnsupportedPlaybackError("The rewritten live media playlist exceeds the manifest byte budget.")
        return self._leased_response(record, self._manifest_spec(rewritten, _MASTER_CONTENT_TYPE))

    def _make_rewriter(self, record: _RelayRecord, generation: int, state: _RelayGeneration):
        prefix = f"/api/remote-streams/{record.stream_id}/relay/{generation}/r/"

        resolved: set[tuple[str, int]] = set()  # one DNS check per host per rewrite, not per segment

        def rewrite(kind: HlsResourceKind, url: str) -> str:
            self._validate_relay_url(url, resolved)
            resource_id = state.resources_by_url.get(url)
            if resource_id is not None:
                # Mark this still-referenced address most-recently-used so the
                # rolling window never evicts a segment the current edge lists.
                state.resources_by_id[resource_id] = state.resources_by_id.pop(resource_id)
                state.resources_by_url[url] = state.resources_by_url.pop(url)
                return f"{prefix}{resource_id}"
            resource_id = self._unique_resource_id(state.resources_by_id)
            state.resources_by_id[resource_id] = _RelayResource(kind, url)
            state.resources_by_url[url] = resource_id
            # Evict the least-recently-used *segment* addresses beyond the
            # per-generation budget. Segments are the only unbounded-growth kind;
            # everything the current playlist referenced was touched above (moved
            # to the tail), so only stale, rolled-off segments are dropped. The
            # structural media-playlist, map, and key pointers the browser
            # re-fetches for the whole session are never evicted.
            while len(state.resources_by_id) > self._max_resources_per_generation:
                victim_id = next(
                    (rid for rid, res in state.resources_by_id.items() if res.kind == "segment"),
                    None,
                )
                if victim_id is None:
                    break
                victim = state.resources_by_id.pop(victim_id)
                state.resources_by_url.pop(victim.url, None)
            return f"{prefix}{resource_id}"

        return rewrite
