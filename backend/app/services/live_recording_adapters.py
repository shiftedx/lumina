"""Guarded, forward-only adapters for the two sibling live-recording outputs.

These are the real collaborators the :class:`LiveRecordingManager` drives:

- :class:`GuardedLiveChatCapturer` reuses the #96 ``LiveChatFetcher`` seam to
  poll the current live-edge continuation forward from connect, normalizes each
  page through the single #93 normalization authority with a media-relative
  offset, and publishes a durable timed chat asset (the same ``ChatReplayAsset``
  the synchronized chat rail loads). It never backfills chat from before Lumina
  connected and never persists a continuation, header, or cookie.
- :class:`GuardedLiveHlsRunner` records the media by FOLLOWING the live edge:
  it re-fetches the media playlist through the guarded transport, appends newly
  listed segments, and stops on true source-end, a deliberate stop, a
  runtime/disk ceiling, or an abrupt cancel — reporting source-end honestly so a
  bounded capture is never a false complete. :class:`GuardedLiveMediaRecorder`
  maps that run to the sibling media output's terminal state.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import select

from app.models import ChatReplayAsset, utcnow
from app.persistence import write_transaction
from app.services.acquisition_batch import RedactedFailure
from app.services.live_chat_seam import LiveChatFetcher
from app.services.live_recording import MediaRunProvenance, resolve_capture_provenance
from app.services.live_recording_manager import LiveChatResult, LiveMediaResult, RecordingContext
from app.services.media_probe import LOCAL_INPUT_ARGS
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.timed_chat import TimedChatBudget, apply_live_action, new_build_state


# -- chat capture -----------------------------------------------------------


class GuardedLiveChatCapturer:
    """Publish a durable timed chat asset from the current live-edge continuation."""

    def __init__(
        self,
        *,
        fetcher: LiveChatFetcher,
        budget: TimedChatBudget | None = None,
        clock: Callable[[], float] = time.time,
        poll_interval_seconds: float = 2.0,
        max_consecutive_failures: int = 4,
    ) -> None:
        self._fetcher = fetcher
        self._budget = budget or TimedChatBudget()
        self._clock = clock
        self._poll_interval = poll_interval_seconds
        self._max_failures = max(1, max_consecutive_failures)

    def capture(self, ctx: RecordingContext) -> LiveChatResult:
        covered = self._covered_from_start(ctx)
        boot = self._fetcher.bootstrap(ctx.source_url, ctx.user_id)
        if boot.status != "available" or not boot.continuation:
            # No chat to capture: a documented terminal state, not a failure, and
            # not something that consumes a durable asset row. There is no earlier
            # chat to be missing, so this never contributes a partial-history gap.
            return LiveChatResult(status="unavailable", covered_from_start=True)

        state = new_build_state(self._budget, rolling=True)
        base_epoch = self._base_epoch(ctx)
        continuation = boot.continuation
        failures = 0
        ended_cleanly = False
        interrupted = False

        while True:
            if ctx.cancel_requested():
                # Abrupt cancel discards in-flight chat without publishing.
                return LiveChatResult(status="failed", failure=_chat_cancelled())
            try:
                page = self._fetcher.fetch_page(continuation, headers=boot.headers)
            except Exception:
                # Chat capture must tolerate transient interruption. Retry until
                # the bounded ceiling, then stop with whatever was captured.
                failures += 1
                if failures >= self._max_failures:
                    interrupted = True
                    break
                if self._sleep_or_break(ctx):
                    if ctx.cancel_requested():
                        return LiveChatResult(status="failed", failure=_chat_cancelled())
                    interrupted = True
                    break
                continue
            failures = 0
            offset_ms = self._offset_ms(base_epoch)
            for action in page.actions:
                apply_live_action(state, action, offset_ms=offset_ms)
            if page.status == "ended" or page.continuation is None or page.status == "disabled":
                ended_cleanly = True
                break
            continuation = page.continuation
            if ctx.stop_requested():
                interrupted = True
                break
            if self._sleep_or_break(ctx):
                if ctx.cancel_requested():
                    return LiveChatResult(status="failed", failure=_chat_cancelled())
                interrupted = True
                break

        events = list(state.events)
        if not events:
            if ended_cleanly:
                asset_id = self._persist(ctx, "empty", state, events)
                return LiveChatResult(status="completed", chat_asset_id=asset_id, covered_from_start=covered)
            # Interrupted before anything usable was captured.
            return LiveChatResult(status="failed", failure=RedactedFailure("chat_failed", "The live chat capture could not be completed."))
        status = "ready" if ended_cleanly else "partial"
        asset_id = self._persist(ctx, status, state, events)
        return LiveChatResult(status="completed", chat_asset_id=asset_id, covered_from_start=covered)

    def _covered_from_start(self, ctx: RecordingContext) -> bool:
        """Whether this forward-only chat is EVIDENCED to reach the media beginning.

        Coverage back to the beginning must be evidenced, never assumed:

        - ``live_edge`` intent: the chat edge is aligned with the edge-following
          media — there is no earlier point by definition — so it is covered.
        - ``from_start`` intent: the capturer bootstraps at the current live-chat
          edge and the go-live poll interval means connect trails real go-live, so
          chat between the broadcast start and connect is genuinely missed. Nothing
          on the context evidences that the chat connect was at/before the earliest
          captured media point (a ``scheduled_start_at`` is only a planned time, not
          proof of when chat actually connected). With no such evidence, from-start
          chat coverage back to the beginning is UNPROVABLE, so it is reported as an
          explicit partial-history condition (False) rather than a false complete.
        """
        return ctx.start_intent != "from_start"

    def _sleep_or_break(self, ctx: RecordingContext) -> bool:
        """Pace polling; wake immediately if the member stopped or cancelled."""
        return ctx.wait_for_end(self._poll_interval)

    def _base_epoch(self, ctx: RecordingContext) -> float:
        if ctx.offset_base is None:
            return self._clock()
        return ctx.offset_base.replace(tzinfo=UTC).timestamp()

    def _offset_ms(self, base_epoch: float) -> int:
        return int(max(0.0, (self._clock() - base_epoch) * 1000.0))

    def _persist(self, ctx: RecordingContext, status: str, state, events) -> str:
        return persist_captured_chat_asset(ctx, status, state, events, max_events=self._budget.max_events)


def persist_captured_chat_asset(
    ctx: RecordingContext, status: str, state, events, *, max_events: int
) -> str:
    """Publish a forward-only live capture as the member's durable timed chat asset.

    Shared by the live-recording chat capturer
    (:class:`GuardedLiveChatCapturer`) and the transient viewing rail, so a
    single durable-asset writer owns the member-scoped upsert, the resume merge,
    and the truncation honesty regardless of the provider transport.
    The stored events already crossed the single normalization authority, so they
    carry no continuation, session id, token, or upstream address.
    """

    # INVARIANT: unlike TimedChatAssetService.store_result, this upsert has no
    # build_id compare-and-set. It is safe today ONLY because the member-scoped
    # partial-unique active index on LiveRecording guarantees at most one active
    # capturer per (member, source), so there is never a second concurrent writer
    # racing this row. If a second concurrent live capturer for the same
    # (member, source) is ever introduced, add a CAS here before relying on this.
    canonical = RemotePlaybackProgressService.canonical_source_identity(ctx.source_identity)
    key = RemotePlaybackProgressService.source_identity_key(canonical)
    with ctx.session_factory() as db:
        with write_transaction(db, name="live_chat_capture_finalize"):
            asset = db.scalar(
                select(ChatReplayAsset).where(
                    ChatReplayAsset.user_id == ctx.user_id,
                    ChatReplayAsset.source_identity_key == key,
                )
            )
            now = utcnow()
            prior_events = list(asset.events_json or []) if asset is not None else []
            if asset is None:
                asset = ChatReplayAsset(
                    id=str(uuid.uuid4()),
                    user_id=ctx.user_id,
                    source_identity=canonical,
                    source_identity_key=key,
                    created_at=now,
                )
                db.add(asset)
            # A resumed recording connects fresh at the current edge and only
            # sees later messages; merge the pre-crash events so a resume never
            # loses earlier chat (union by stable id, earliest first).
            merged, merge_truncated = _merge_chat_events(prior_events, events, max_events)
            effective_status = status
            if merge_truncated and status == "ready":
                effective_status = "partial"
            asset.source_url = ctx.source_url
            asset.status = effective_status
            asset.build_id = str(uuid.uuid4())
            asset.event_count = len(merged)
            asset.total_seen = state.total_seen
            asset.bytes_processed = state.bytes_processed
            asset.dropped_malformed = state.dropped_malformed
            asset.truncated = bool(state.truncated) or effective_status == "partial" or merge_truncated
            asset.events_json = merged
            asset.error = None
            asset.started_at = asset.started_at or now
            asset.finished_at = now
            asset.updated_at = now
            asset_id = asset.id
    return asset_id


def _chat_cancelled() -> RedactedFailure:
    return RedactedFailure("chat_cancelled", "The live chat capture was cancelled.")


def _merge_chat_events(prior: list, new_events: list, cap: int) -> tuple[list, bool]:
    """Union prior (pre-crash) chat with a resumed capture, preserving order.

    Prior events keep their place (they carry earlier media offsets); newly
    captured events not already present are appended. If the union exceeds the
    bounded cap it keeps the earliest ``cap`` events (the recording from its
    start) and reports truncation, so a resume never silently drops earlier chat.
    """

    merged: list = []
    seen: set = set()
    for entry in prior:
        if not isinstance(entry, dict):
            continue
        event_id = entry.get("id")
        if event_id and event_id not in seen:
            seen.add(event_id)
            merged.append(entry)
    for event in new_events:
        if event.id not in seen:
            seen.add(event.id)
            merged.append(event.to_public_dict())
    truncated = len(merged) > cap
    if truncated:
        merged = merged[:cap]
    return merged, truncated


# -- media recording --------------------------------------------------------


# Runner stop reasons -> the durable ``media_end_reason`` vocabulary.
_END_REASONS = {"ended": "source_ended", "runtime": "time_limit", "disk": "size_limit"}


class LiveMediaCancelled(RuntimeError):
    """The member abruptly cancelled the media recording; discard the partial."""


class LiveMediaUnsupported(RuntimeError):
    """The live media cannot be safely recorded (e.g. encrypted or no master)."""


@dataclass(frozen=True)
class MediaRunOutcome:
    """What a media run produced.

    ``reached_source_end`` is True ONLY when the recorder observed true
    source-end (``#EXT-X-ENDLIST`` / the master no longer resolving) after
    recording. A usable-but-not-source-end capture (a deliberate stop, a
    runtime/disk ceiling, or an interruption) sets it False so the recorder never
    reports a false complete.

    The from-start dimension (issue #98) is symmetric — it describes the BEGINNING
    of the capture. ``began_at_source_start`` is True only when capture actually
    began at the broadcast start; ``history_complete`` is True only when that
    from-start beginning had no gap (best-effort — reported honestly rather than
    assumed); ``from_start_supported`` records what the inspected source offered;
    ``needs_fallback_choice`` marks a from-start-unavailable run that refused to
    silently fall back to the edge because the member's saved intent required an
    explicit choice.
    """

    library_item_id: str | None
    reached_source_end: bool = False
    began_at_source_start: bool = False
    history_complete: bool = False
    from_start_supported: bool = False
    needs_fallback_choice: bool = False
    end_reason: str | None = None


class LiveMediaRunner(Protocol):
    """Records the live media forward from the current edge until it ends.

    Implementations block until the source ends (``reached_source_end=True``), the
    member stops (finalize the usable partial, ``reached_source_end=False``), or a
    ceiling/interruption bounds the capture (also ``reached_source_end=False``); an
    abrupt cancel raises :class:`LiveMediaCancelled`. A fake runner exercises the
    outcome handling in tests.
    """

    def run(self, ctx: RecordingContext) -> MediaRunOutcome: ...


class LiveMediaSink(Protocol):
    """Receives the appended live segments and finalizes a Library item.

    The real sink writes the guarded segment bytes to a private temp file,
    remuxes them into the member's output container, and publishes a Library
    item; ``finalize`` returns its id (or ``None`` when nothing playable was
    produced). ``discard`` drops an abandoned/cancelled capture. A fake sink
    exercises the recorder loop without any media production.
    """

    def append_init(self, data: bytes) -> None: ...

    def append_segment(self, data: bytes, sequence: int) -> None: ...

    def finalize(self) -> str | None: ...

    def discard(self) -> None: ...


class GuardedMediaFetch(Protocol):
    """Guarded fetch of an HLS document or segment through the public-source policy."""

    def fetch_text(self, url: str, *, headers: dict) -> str: ...

    def fetch_bytes(self, url: str, *, headers: dict) -> bytes: ...


class GuardedLiveHlsRunner:
    """Follow the live edge through the guarded transport and record it honestly.

    The runner re-fetches the media playlist through the guarded ``fetch`` on each
    refresh, appends every newly-listed segment (deduped by URL) to the sink, and
    stops on true source-end (``#EXT-X-ENDLIST``), a deliberate stop, a
    runtime/disk ceiling, an abrupt cancel, or a bounded run of fetch failures.
    Every address is parsed by the same fail-closed HLS parser the relay uses, and
    an encrypted playlist (which would need an out-of-guard key/decrypt path) is
    refused rather than recorded as garbage. ``reached_source_end`` is set only on
    ENDLIST, so a stopped or bounded capture is never a false complete.

    INTEGRATION BOUNDARY: whether a real broadcast's segments remux into a playable
    file is validated against a live source (the issue forbids live YouTube in the
    test suite). The guarded fetch, the edge-following loop, the honest source-end
    determination, the stop/cancel semantics, and the runtime/disk ceilings are
    what this seam owns and are covered offline with fakes.
    """

    def __init__(
        self,
        *,
        resolver,
        fetch: GuardedMediaFetch,
        sink_factory: Callable[[RecordingContext], LiveMediaSink],
        clock: Callable[[], float] | None = None,
        max_runtime_seconds: float,
        max_disk_bytes: int,
        poll_interval_seconds: float = 2.0,
        max_consecutive_failures: int = 6,
    ) -> None:
        self._resolver = resolver
        self._fetch = fetch
        self._sink_factory = sink_factory
        self._clock = clock or time.monotonic
        self._max_runtime_seconds = max_runtime_seconds
        self._max_disk_bytes = max_disk_bytes
        self._poll_interval = poll_interval_seconds
        self._max_failures = max(1, max_consecutive_failures)

    def run(self, ctx: RecordingContext) -> MediaRunOutcome:
        from app.services.hls_relay_support import extract_master_url, extract_request_headers

        # From-start (issue #98) is authorized by the capability seam and carried on
        # the context. When the source does not support it and the member's saved
        # intent forbids a silent fallback, refuse BEFORE any capture — never record
        # the edge contrary to intent.
        from_start_supported = bool(ctx.from_start_supported)
        want_from_start = ctx.start_intent == "from_start"
        if want_from_start and not from_start_supported and ctx.fallback_policy == "require_choice":
            return MediaRunOutcome(
                library_item_id=None, needs_fallback_choice=True, from_start_supported=False
            )
        use_from_start = want_from_start and from_start_supported

        info = self._resolver.resolve(ctx.source_url, ctx.user_id)
        master_url = extract_master_url(info)
        if master_url is None:
            return MediaRunOutcome(
                library_item_id=None, reached_source_end=False, from_start_supported=from_start_supported
            )
        headers = extract_request_headers(info)
        sink = self._sink_factory(ctx)
        # A resumed run appends to the bytes the crashed run left, so they
        # count toward the disk ceiling and are never discarded as "nothing captured".
        bytes_written = int(getattr(sink, "resumed_bytes", 0))
        resumed = bytes_written > 0
        # The last media sequence the crashed run captured: a quick restart relists
        # it, and re-appending it would duplicate media and push chat off sync (#145).
        resume_after = getattr(sink, "resumed_sequence", None) if resumed else None
        reason: str | None = None
        # ``began_at_source_start`` is answered HONESTLY from the first media
        # playlist's MEDIA-SEQUENCE: 0 means the earliest-available media point IS
        # the broadcast beginning. From-start is best-effort — when the earliest
        # segment is past 0, the beginning is genuinely missing, so this stays False
        # and the recorder maps the capture to a partial-history outcome rather than
        # a false complete. INTEGRATION BOUNDARY: whether yt-dlp's --live-from-start
        # actually surfaces the whole-broadcast window for a given real source is
        # validated against a live source (the same deploy-smoke boundary #97 uses);
        # the honest reporting here holds regardless of what the window contained.
        began_at_source_start = False
        first_playlist_seen = False
        try:
            media_url, pending_text = self._resolve_media_playlist(master_url, headers)
            seen_segments: set[str] = set()
            seen_map = False
            failures = 0
            start = self._clock()
            while reason is None:
                if ctx.cancel_requested():
                    sink.discard()
                    raise LiveMediaCancelled()
                if pending_text is not None:
                    # Reuse the media playlist already fetched while resolving, so
                    # the connect-time window is never dropped by a redundant fetch.
                    text, pending_text = pending_text, None
                else:
                    try:
                        text = self._fetch.fetch_text(media_url, headers=headers)
                    except Exception:
                        failures += 1
                        if failures >= self._max_failures:
                            # The edge stopped resolving: end the capture (partial).
                            reason = "interrupted"
                            break
                        if self._pace(ctx):
                            reason = self._signal_reason(ctx, sink)
                            break
                        continue
                failures = 0
                addresses, ended = self._parse(text, media_url)
                if not first_playlist_seen:
                    first_playlist_seen = True
                    # From-start is only truly achieved when the earliest available
                    # media point is the broadcast start (sequence 0).
                    began_at_source_start = use_from_start and self._media_sequence(text) == 0
                    # The require-choice gate is enforced against the ACTUAL capture
                    # origin, not only the inspected from-start support. When the
                    # source advertised from-start but this connect actually begins
                    # past the beginning (already-live or a late-detected schedule —
                    # first MEDIA-SEQUENCE != 0), a member who required an explicit
                    # choice must NOT silently get an edge partial: refuse here,
                    # before any segment is appended, and surface the fallback choice.
                    # The sequence is only known once the first playlist is fetched,
                    # so this gate necessarily fires here rather than pre-capture.
                    if want_from_start and ctx.fallback_policy == "require_choice" and not began_at_source_start and not resumed:
                        sink.discard()
                        return MediaRunOutcome(
                            library_item_id=None,
                            needs_fallback_choice=True,
                            from_start_supported=from_start_supported,
                        )
                sequence = self._media_sequence(text) or 0
                if resume_after is not None and sequence + sum(k == "segment" for k, _ in addresses) <= resume_after:
                    resume_after = None  # the sequence went backwards: a new stream, nothing to skip
                for kind, url in addresses:
                    if kind == "map":
                        if seen_map:
                            continue
                        seen_map = True
                        data = self._fetch.fetch_bytes(url, headers=headers)
                        sink.append_init(data)
                    else:
                        seq, sequence = sequence, sequence + 1
                        if url in seen_segments or (resume_after is not None and seq <= resume_after):
                            continue
                        seen_segments.add(url)
                        data = self._fetch.fetch_bytes(url, headers=headers)
                        sink.append_segment(data, seq)
                    bytes_written += len(data)
                    if bytes_written > self._max_disk_bytes:
                        reason = "disk"
                        break
                if reason is not None:
                    break
                if ended:
                    reason = "ended"
                elif ctx.stop_requested():
                    reason = "stopped"
                elif self._clock() - start > self._max_runtime_seconds:
                    reason = "runtime"
                elif self._pace(ctx):
                    reason = self._signal_reason(ctx, sink)
        except LiveMediaCancelled:
            raise
        except Exception:
            # Malformed/encrypted/unsupported or any fetch-path error. With nothing
            # captured this publishes nothing (honest media failure); bytes already
            # captured are valid media and are kept as an interrupted partial.
            reason = "interrupted"
        if bytes_written == 0:
            sink.discard()
            return MediaRunOutcome(
                library_item_id=None, reached_source_end=False, from_start_supported=from_start_supported
            )
        item_id = sink.finalize()
        # A resumed run has a gap where the process was down: never a complete capture.
        reached_end = reason == "ended" and not resumed
        return MediaRunOutcome(
            library_item_id=item_id,
            reached_source_end=reached_end,
            began_at_source_start=began_at_source_start,
            # A complete from-start history means we captured continuously from
            # the broadcast beginning through to source-end.
            history_complete=began_at_source_start and reached_end,
            from_start_supported=from_start_supported,
            end_reason=_END_REASONS.get(reason or "interrupted", reason),
        )

    def salvage(self, ctx: RecordingContext) -> str | None:
        """Publish the bytes a crashed run left on disk; discard them on cancel."""
        sink = self._sink_factory(ctx)
        if ctx.cancel_requested() or not getattr(sink, "resumed_bytes", 0):
            sink.discard()
            return None
        return sink.finalize()

    def _pace(self, ctx: RecordingContext) -> bool:
        # Between refreshes, wait ~one poll interval but wake immediately if the
        # member stops or cancels.
        return ctx.wait_for_end(self._poll_interval)

    def _signal_reason(self, ctx: RecordingContext, sink: LiveMediaSink) -> str:
        if ctx.cancel_requested():
            sink.discard()
            raise LiveMediaCancelled()
        return "stopped"

    @staticmethod
    def _media_sequence(text: str) -> int | None:
        """The playlist's ``#EXT-X-MEDIA-SEQUENCE`` (the index of its first segment).

        Returns ``None`` when the tag is absent or unparseable. On the honesty axis
        an absent tag is NOT evidence the playlist began at sequence 0: asserting
        "began at the beginning" without the tag would be an assume-beginning
        default. The caller compares ``== 0``, so ``None`` correctly yields "not
        confirmed to begin at the source start" (a partial-history condition).
        """
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    return int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None

    def _resolve_media_playlist(self, master_url: str, headers: dict) -> tuple[str, str | None]:
        from app.services.hls_manifest import classify

        text = self._fetch.fetch_text(master_url, headers=headers)
        if classify(text) == "media":
            # The resolved address already IS the media playlist; hand its text to
            # the loop so the connect-time window is not dropped by a re-fetch.
            return master_url, text
        variants = [url for kind, url in _collect_addresses(text, master_url, is_master=True) if kind == "media_playlist"]
        if not variants:
            raise LiveMediaUnsupported("The live master listed no playable variant.")
        # Record the best rendition: masters do not order variants by quality
        # (Kick's IVS master lists 160p first). Stable max keeps the first on ties.
        bandwidths = _variant_bandwidths(text, master_url)
        return max(variants, key=lambda url: bandwidths.get(url, -1)), None

    def _parse(self, text: str, base_url: str) -> tuple[list[tuple[str, str]], bool]:
        from app.services.hls_manifest import MalformedManifestError, classify

        if classify(text) != "media":
            raise MalformedManifestError("A live variant did not resolve to a media playlist.")
        addresses = _collect_addresses(text, base_url, is_master=False)
        if any(kind == "key" for kind, _ in addresses):
            # Encrypted content needs an out-of-guard key fetch + decrypt; refuse.
            raise LiveMediaUnsupported("Encrypted live media is not recordable.")
        ended = "#EXT-X-ENDLIST" in text
        media_addresses = [(kind, url) for kind, url in addresses if kind in {"segment", "map"}]
        return media_addresses, ended


def _variant_bandwidths(text: str, base_url: str) -> dict[str, int]:
    """Map each ``#EXT-X-STREAM-INF`` variant address to its declared BANDWIDTH."""

    from urllib.parse import urljoin

    bandwidths: dict[str, int] = {}
    pending: int | None = None
    for line in (raw.strip() for raw in text.splitlines()):
        if line.startswith("#EXT-X-STREAM-INF"):
            match = re.search(r"[:,]BANDWIDTH=(\d+)", line)
            pending = int(match.group(1)) if match else 0
        elif line and not line.startswith("#") and pending is not None:
            bandwidths[urljoin(base_url, line)] = pending
            pending = None
    return bandwidths


def _collect_addresses(text: str, base_url: str, *, is_master: bool) -> list[tuple[str, str]]:
    """Extract (kind, absolute-url) via the shared fail-closed HLS parser.

    Reusing ``rewrite_master``/``rewrite_media`` means the recorder inherits the
    relay's strict rejection of unknown address-carrying tags and residual
    upstream addresses; the collector just records the addresses instead of
    rewriting them.
    """

    from app.services.hls_manifest import rewrite_master, rewrite_media

    collected: list[tuple[str, str]] = []

    def collect(kind: str, url: str) -> str:
        collected.append((kind, url))
        # Return a Lumina-style root-relative placeholder, never the upstream url:
        # rewrite_master/rewrite_media run a residual-address backstop over the
        # rendered output that rejects any surviving scheme-bearing address.
        return f"/collected/{len(collected)}"

    (rewrite_master if is_master else rewrite_media)(text, base_url, collect)
    return collected


class GuardedLiveMediaRecorder:
    """Map a guarded media run + the member intent to the media output's state.

    This is where the honest-reporting rules live. From #97 (review fix 1b): the
    media output is ``completed`` ONLY when the run reached true source-end; a
    usable capture that did not is ``partial`` — never a false complete. From #98:
    from-start is best-effort, so a from-start capture is ``completed`` only when it
    began at the source start AND the beginning history was complete; an incomplete
    beginning is an honest ``partial``. A from-start-unavailable run that the saved
    intent said must not silently fall back is a ``from_start_unavailable`` failure
    the member can resolve, never a silent edge capture. A cancel discards, and an
    empty/failed run is a media failure.
    """

    def __init__(self, *, runner: LiveMediaRunner) -> None:
        self._runner = runner

    def record(self, ctx: RecordingContext) -> LiveMediaResult:
        try:
            outcome = self._runner.run(ctx)
        except LiveMediaCancelled:
            # Cancel discards the partial recording: nothing usable is published.
            return LiveMediaResult(status="failed", failure=RedactedFailure("media_cancelled", "The recording was cancelled."))
        except Exception:
            # A resumed run that could not reconnect (the broadcast ended while
            # Lumina was down) still publishes what the crashed run captured.
            return (ctx.resume and self.salvage(ctx)) or LiveMediaResult(
                status="failed", failure=RedactedFailure("media_failed", "The live recording could not be completed.")
            )
        if outcome.library_item_id is None and ctx.resume and not outcome.needs_fallback_choice:
            salvaged = self.salvage(ctx)
            if salvaged is not None:
                return salvaged
        if outcome.needs_fallback_choice:
            # From-start was unavailable and the saved intent required an explicit
            # choice: refuse to silently record the edge, and surface the honest
            # condition the member can resolve by re-recording from the edge.
            return LiveMediaResult(
                status="failed",
                failure=RedactedFailure("from_start_unavailable", "Recording from the beginning is not available for this source."),
                from_start_supported=outcome.from_start_supported,
                awaiting_fallback_choice=True,
            )
        if outcome.library_item_id is None:
            return LiveMediaResult(
                status="failed",
                failure=RedactedFailure("media_failed", "The live recording produced no playable media."),
                from_start_supported=outcome.from_start_supported,
            )
        provenance = MediaRunProvenance(
            library_item_id=outcome.library_item_id,
            reached_source_end=outcome.reached_source_end,
            began_at_source_start=outcome.began_at_source_start,
            history_complete=outcome.history_complete,
            from_start_supported=outcome.from_start_supported,
        )
        capture_origin, media_status, media_history_complete = resolve_capture_provenance(
            start_intent=ctx.start_intent,
            fallback_policy=ctx.fallback_policy,
            outcome=provenance,
        )
        return LiveMediaResult(
            status=media_status,
            library_item_id=outcome.library_item_id,
            capture_origin=capture_origin,
            media_history_complete=media_history_complete,
            from_start_supported=outcome.from_start_supported,
            end_reason=outcome.end_reason,
        )

    def salvage(self, ctx: RecordingContext) -> LiveMediaResult | None:
        """Publish a crashed run's leftover bytes as an interrupted partial."""
        salvage = getattr(self._runner, "salvage", None)
        item_id = salvage(ctx) if salvage is not None else None
        if item_id is None:
            return None
        return LiveMediaResult(status="partial", library_item_id=item_id, end_reason="interrupted")


# -- real guarded-fetch + ffmpeg sink adapters ------------------------------
# The runner's control logic is covered offline with fakes; these concrete
# adapters are the live integration boundary (guarded fetch of real addresses,
# and ffmpeg remux of real segments into a playable file).


class PublicGuardedMediaFetch:
    """Guarded fetch of an HLS document/segment, bounded and policy-validated.

    Reuses the same ``PublicRelayFetcher`` the live relay uses, so every fetch
    revalidates the public-source policy and fails closed on private/redirected
    addresses. Bodies are read under explicit byte ceilings so a hostile upstream
    cannot exhaust memory.
    """

    def __init__(self, fetcher, *, max_manifest_bytes: int, max_segment_bytes: int) -> None:
        self._fetcher = fetcher
        self._max_manifest_bytes = max_manifest_bytes
        self._max_segment_bytes = max_segment_bytes

    def fetch_text(self, url: str, *, headers: dict) -> str:
        return self._read(url, headers, self._max_manifest_bytes).decode("utf-8", "ignore")

    def fetch_bytes(self, url: str, *, headers: dict) -> bytes:
        return self._read(url, headers, self._max_segment_bytes)

    def _read(self, url: str, headers: dict, limit: int) -> bytes:
        response = self._fetcher.fetch(url, headers=headers)
        data = bytearray()
        try:
            for chunk in response.body:
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("A live resource exceeded its byte ceiling.")
        finally:
            response.close()
        return bytes(data)


# Recording is scoped to these providers (``_LIVE_RECORD_PROVIDERS``); the map
# gives each its canonical yt-dlp extractor key so the published Library item is
# filed under its true provider identity, not a hardcoded one.
_RECORD_EXTRACTOR_KEYS = {"youtube": "Youtube", "twitch": "Twitch"}


def recording_library_info(source_identity: str, source_url: str, filepath: str) -> dict:
    """Build the Library ``info`` for a recorded live item from its source identity.

    The provider is derived from the recording's canonical source identity
    (``youtube:…`` / ``twitch:…``) so a Twitch recording is filed under the Twitch
    provider and remote id — sharing one source identity with its captured chat
    asset — rather than a hardcoded YouTube one.
    """

    provider, separator, remote_id = source_identity.partition(":")
    provider = provider.strip().casefold()
    if not separator or not remote_id:
        provider, remote_id = provider or "generic", source_identity
    extractor_key = _RECORD_EXTRACTOR_KEYS.get(provider, provider.capitalize() or "Generic")
    return {
        "id": remote_id,
        "extractor": provider or "generic",
        "extractor_key": extractor_key,
        "title": "Live recording",
        "webpage_url": source_url,
        "filepath": filepath,
        "lumina_live_recording": True,
    }


class FfmpegLiveMediaSink:
    """Append guarded live segments to a private file and remux to a Library item.

    INTEGRATION BOUNDARY: whether real broadcast segments remux into a playable
    container is validated against a live source, not offline. On finalize this
    concatenates the captured init+segments, remuxes with ffmpeg (``-c copy``),
    and publishes a private Library item under the same source identity as the
    recording so the completed item's chat rail loads the captured chat asset.
    """

    def __init__(
        self,
        ctx: RecordingContext,
        *,
        session_factory: Callable[[], object],
        events: object | None,
        temp_root: Path,
        ffmpeg_path: str | None,
        remux_timeout_seconds: float = 600,
    ) -> None:
        self._ctx = ctx
        self._session_factory = session_factory
        self._events = events
        self._ffmpeg_path = ffmpeg_path or "ffmpeg"
        self._remux_timeout = remux_timeout_seconds
        temp_dir = Path(temp_root) / "live-recordings"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._raw_path = temp_dir / f"{ctx.recording_id}.raw"
        # Append, never truncate: after a crash the resumed run continues the
        # same file, so the bytes captured before the restart survive.
        self._handle = self._raw_path.open("ab")
        self.resumed_bytes = self._handle.tell()
        # The last captured media sequence survives a crash beside the raw file.
        self._seq_path = self._raw_path.with_suffix(".seq")
        try:
            self.resumed_sequence: int | None = int(self._seq_path.read_text()) if self.resumed_bytes else None
        except (OSError, ValueError):
            self.resumed_sequence = None

    def append_init(self, data: bytes) -> None:
        if self._handle.tell() == 0:
            self._handle.write(data)
            return
        # A resumed file already starts with its init segment. A different one
        # (another variant or codec setup) cannot share the file: stop here so the
        # pre-restart bytes publish as their own interrupted partial, uncorrupted.
        # Exact-bytes compare; a cosmetic init change also ends the resume.
        with self._raw_path.open("rb") as head:
            if head.read(len(data)) != data:
                raise LiveMediaUnsupported("The resumed stream's codec setup changed.")

    def append_segment(self, data: bytes, sequence: int) -> None:
        self._handle.write(data)
        # Flush first so a crash never records a sequence whose bytes were lost.
        self._handle.flush()
        self._seq_path.write_text(str(sequence))

    def finalize(self) -> str | None:
        try:
            self._handle.flush()
            self._handle.close()
        except OSError:
            return None
        container = str((self._ctx.output_profile or {}).get("container") or "mp4")
        out_path = self._raw_path.with_name(f"live-{self._ctx.recording_id}.{container}")
        remuxed = self._remux(out_path)
        self._cleanup_raw()
        try:
            return self._publish(out_path) if remuxed else None
        finally:
            out_path.unlink(missing_ok=True)

    def discard(self) -> None:
        try:
            if not self._handle.closed:
                self._handle.close()
        except OSError:
            pass
        self._cleanup_raw()

    def _remux(self, out_path: Path) -> bool:
        try:
            result = subprocess.run(
                # A restart (or a fall-behind) leaves a forward timestamp jump in the
                # raw capture. ffmpeg collapses MPEG-TS jumps over 10 s by default,
                # which would shift chat (timed by wall clock) off the media (#145);
                # keep forward gaps. Backward jumps (a source reset) stay corrected.
                [
                    self._ffmpeg_path, "-y", "-dts_delta_threshold", "1000000000", *LOCAL_INPUT_ARGS,
                    "-i", str(self._raw_path), "-c", "copy", str(out_path),
                ],
                capture_output=True,
                timeout=self._remux_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0

    def _publish(self, staged: Path) -> str | None:
        """Route the remuxed file like any download (media kind ``recording``) and publish it."""
        # Imported lazily to keep the module free of the heavy library import graph.
        from app.services.library import LibraryService
        from app.services.live_recording import LiveRecordingService
        from app.services.storage_routing import publish_file

        info = recording_library_info(self._ctx.source_identity, self._ctx.source_url, "")

        with self._session_factory() as db:  # type: ignore[union-attr]
            library = LibraryService(db, self._events)

            def record(final: Path, _decision: object) -> str:
                with write_transaction(db, name="live_recording_media_publish"):
                    item = library.upsert_from_info({**info, "filepath": str(final)}, owner_user_id=self._ctx.user_id, visibility="private")
                    # Point the recording at its item in the SAME transaction
                    # (``write_transaction`` is reentrant, so this joins the publish
                    # commit). This closes the mid-capture crash orphan window
                    # (issue #112): the item and the recording's pointer to it are
                    # durable together, so restart recovery can never RESUME and
                    # re-capture over an already-published item.
                    LiveRecordingService(db).note_media_published(self._ctx.recording_id, item.id)
                    return item.id

            # No publication journal for recordings; a crash between the
            # link and this commit leaves one unregistered file in the root.
            return publish_file(
                db, staged, source=info["extractor"], media_kind="recording", height=None,
                relative=f"{self._ctx.user_id or 'shared'}/live/{staged.name}", key=self._ctx.recording_id, record=record,
            )

    def _cleanup_raw(self) -> None:
        for path in (self._raw_path, self._seq_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class YtDlpLiveSourceProbe:
    """Re-inspect a scheduled source through the guarded preview seam (issue #98).

    The scheduled-waiter drives this to decide whether an upcoming broadcast has
    gone live, been cancelled, or is delayed. It reuses the single capability seam
    so the waiter's notion of "live", "upcoming", from-start support, and the best
    provider start time can never drift from the rest of the product. Upstream
    error strings are classified internally and NEVER surfaced.

    INTEGRATION BOUNDARY: the actual yt-dlp inspection of a real upcoming broadcast
    (and the exact error strings a cancelled/geo-blocked/members-only source
    yields) is validated against live sources, the same deploy-smoke boundary #97
    uses; the state normalization is what this seam owns.
    """

    def __init__(self, *, session_factory: Callable[[], object]) -> None:
        self._session_factory = session_factory

    def probe(self, ctx: RecordingContext) -> "SourceProbe":
        from datetime import UTC

        import yt_dlp

        from app.services.live_recording_waiter import SourceProbe
        from app.services.yt_dlp_service import PublicSourcePolicyError, YtDlpService

        with self._session_factory() as db:  # type: ignore[union-attr]
            try:
                preview = YtDlpService(db).preview(ctx.source_url, lazy_playlist=False)
            except PublicSourcePolicyError:
                return SourceProbe(state="error")
            except yt_dlp.utils.DownloadError as exc:
                return self._classify_download_error(exc)
            except Exception:
                return SourceProbe(state="error")
            capabilities = preview.capabilities
        if capabilities is None:
            return SourceProbe(state="error")
        lifecycle = capabilities.lifecycle
        if lifecycle == "live":
            return SourceProbe(state="live", from_start_supported=bool(capabilities.from_start_available))
        if lifecycle == "upcoming":
            epoch = None
            if capabilities.scheduled_start is not None:
                epoch = capabilities.scheduled_start.replace(tzinfo=UTC).timestamp()
            return SourceProbe(state="upcoming", scheduled_start_epoch=epoch)
        if lifecycle in ("completed_live", "post_live"):
            # The broadcast finished before Lumina could connect from now.
            return SourceProbe(state="ended")
        return SourceProbe(state="error")

    @staticmethod
    def _classify_download_error(exc: Exception) -> "SourceProbe":
        from app.services.live_recording_waiter import SourceProbe

        message = str(exc).lower()
        if any(token in message for token in ("cancel", "removed", "deleted", "terminated", "no longer", "not available", "unavailable")):
            return SourceProbe(state="cancelled")
        if any(token in message for token in ("sign in", "log in", "login", "members-only", "member's", "cookies", "authenticat", "private")):
            return SourceProbe(state="auth_expired")
        # A transient inspection failure: the waiter retries within its bounds.
        return SourceProbe(state="error")


def build_guarded_live_media_recorder(
    *,
    session_factory: Callable[[], object],
    events: object | None,
    resolver,
    relay_fetcher,
    temp_root: Path,
    ffmpeg_path: str | None,
    max_runtime_seconds: float,
    max_disk_bytes: int,
    max_manifest_bytes: int,
    max_segment_bytes: int,
    poll_interval_seconds: float,
    max_consecutive_failures: int,
) -> GuardedLiveMediaRecorder:
    """Assemble the real edge-following recorder from the guarded primitives."""

    fetch = PublicGuardedMediaFetch(
        relay_fetcher, max_manifest_bytes=max_manifest_bytes, max_segment_bytes=max_segment_bytes
    )

    def sink_factory(ctx: RecordingContext) -> FfmpegLiveMediaSink:
        return FfmpegLiveMediaSink(
            ctx,
            session_factory=session_factory,
            events=events,
            temp_root=temp_root,
            ffmpeg_path=ffmpeg_path,
        )

    runner = GuardedLiveHlsRunner(
        resolver=resolver,
        fetch=fetch,
        sink_factory=sink_factory,
        max_runtime_seconds=max_runtime_seconds,
        max_disk_bytes=max_disk_bytes,
        poll_interval_seconds=poll_interval_seconds,
        max_consecutive_failures=max_consecutive_failures,
    )
    return GuardedLiveMediaRecorder(runner=runner)
