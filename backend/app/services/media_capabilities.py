"""Derive Lumina's safe, public Media-source capability contract.

The extractor response is deliberately kept behind this one seam.  Callers use
the compact normalized result and never need to infer provider or transport
support from raw extractor metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from app.schemas import MediaSourceCapabilities, MediaSourceChatCapabilities
from app.services.hls_relay_support import (
    derive_lifecycle,
    derive_provider,
    has_transport_evidence,
    is_live_relay_provider,
    public_provider,
    supports_hls_relay,
    supports_live_from_start,
    supports_live_hls_acquisition,
    supports_live_hls_relay,
    supports_scheduled_recording,
)
from app.services.network_policy import PolicyYoutubeDL


_SEGMENTED_PROTOCOL_MARKERS = ("m3u8", "dash", "ism", "f4m")

# Browser playback flows only through Lumina's progressive-transport relay.
_PLAYABLE_PROTOCOLS = {"http", "https"}
# Acquisition mirrors the worker's download-transport gate exactly, so a source
# only looks acquirable when yt-dlp can actually fetch it under the public-only
# network policy. Single source of truth: PolicyYoutubeDL.validate_download_transport
# (network_policy.py). http_dash_segments DASH is acquirable; HLS/ISM/F4M are not.
_ACQUIRABLE_PROTOCOLS = PolicyYoutubeDL.SAFE_NATIVE_PROTOCOLS


def derive_media_capabilities(info: Mapping[str, Any]) -> MediaSourceCapabilities:
    """Return conservative actions for one sanitized extractor result.

    Playback and acquisition are gated on the transport, never on provider
    identity, which stays descriptive.  Playback needs a progressive http/https
    stream Lumina can relay.  Acquisition mirrors the worker's
    download-transport gate (SAFE_NATIVE_PROTOCOLS): progressive http(s),
    http_dash_segments DASH VOD, and playlist/channel containers.  HLS/ISM/F4M
    VOD, non-http(s) protocols (rtmp/rtsp/mms), and lifecycles Lumina cannot
    handle stay unavailable so no misleading enabled action is offered.
    """

    provider = derive_provider(info)
    lifecycle = derive_lifecycle(info)
    if not public_provider(provider).playback:
        # A recognized provider whose public adapter has not landed: every action
        # is honestly unavailable, whatever transport the extractor happens to list.
        return MediaSourceCapabilities(
            provider=provider, lifecycle=lifecycle,
            can_play=False, play_reason="provider_not_supported",
            can_acquire=False, acquire_reason="provider_not_supported",
            record_reason="provider_not_supported" if lifecycle == "live" else None,
            schedule_reason="provider_not_supported" if lifecycle == "upcoming" else None,
        )
    chat = _chat_capabilities(info, provider, lifecycle)
    # A flat search/discovery entry was never inspected: its actions are a guess
    # the Watch surface re-checks from the fully extracted source at open.
    provisional = not has_transport_evidence(info) and not _is_container(info)
    gate = _access_gate(info)
    if gate is not None:
        # Anonymous extraction cannot open a gated source, whatever transport it
        # lists: every action is honestly unavailable and no sign-in is offered.
        return MediaSourceCapabilities(
            provider=provider, lifecycle=lifecycle,
            can_play=False, play_reason=gate, can_acquire=False, acquire_reason=gate,
            record_reason="live_record_not_supported" if lifecycle == "live" else None,
            chat=chat, provisional=provisional,
        )
    if lifecycle == "live":
        # A currently-live source Lumina can relay plays at the current edge; it
        # is a transient viewing session, never acquired to the vault, and never
        # registered as a seekable VOD (the live relay descriptor is not
        # seekable). A subscriber-gated live source is a distinct, honest
        # not-playable state (handled above); other live sources whose transport
        # Lumina cannot relay keep the generic not-supported reason. A flat
        # search/discovery entry from a live-relay provider carries no transport
        # evidence at all (see below), so it is advertised provisionally playable
        # rather than judged fail-closed on absent evidence.
        play_reason: Any
        if supports_live_hls_relay(info, provider, lifecycle):
            play_reason, can_play = None, True
        elif is_live_relay_provider(provider) and not has_transport_evidence(info):
            # A flat search/discovery entry never carries formats or a manifest
            # address, so relay support cannot be judged from it. For a provider
            # the live relay serves, advertise playable-in-principle: the Watch
            # surface re-inspects the fully extracted source at open and makes
            # the honest final call, and the live relay itself fails closed.
            play_reason, can_play = None, True
        else:
            play_reason, can_play = "live_playback_not_supported", False
        # "Record from now" (issue #97) is offered only when Lumina can capture the
        # source through the same guarded live-HLS transport it plays back. It is a
        # distinct deliberate action, so ordinary can_acquire stays False (the
        # batch flow never records a live source); recording flows through its own
        # durable multi-output acquisition.
        can_record = supports_live_hls_acquisition(info, provider, lifecycle)
        # From-start (issue #98) is offered only alongside a recordable live source:
        # a currently-live YouTube broadcast can be recorded from its beginning
        # (best-effort/experimental), never a source Lumina cannot record at all.
        from_start = can_record and supports_live_from_start(info, provider, lifecycle)
        return MediaSourceCapabilities(
            provider=provider, lifecycle=lifecycle,
            can_play=can_play,
            play_reason=play_reason,
            can_acquire=False,
            acquire_reason="live_acquisition_not_supported",
            can_record=can_record,
            record_reason=None if can_record else "live_record_not_supported",
            from_start_available=from_start,
            chat=chat, provisional=provisional,
        )
    if lifecycle == "upcoming":
        # An upcoming YouTube broadcast can be SCHEDULED: Lumina waits for it and
        # records it from the beginning (best-effort) when it goes live. Ordinary
        # playback/acquisition stay unavailable until it starts.
        can_schedule = supports_scheduled_recording(info, provider, lifecycle)
        from_start = can_schedule and supports_live_from_start(info, provider, lifecycle)
        return MediaSourceCapabilities(
            provider=provider, lifecycle=lifecycle, can_play=False,
            play_reason="upcoming_not_started", can_acquire=False,
            acquire_reason="upcoming_not_started",
            can_schedule=can_schedule,
            schedule_reason=None if can_schedule else "upcoming_schedule_not_supported",
            scheduled_start=_scheduled_start(info) if can_schedule else None,
            from_start_available=from_start,
            chat=chat, provisional=provisional,
        )
    if lifecycle == "post_live":
        return MediaSourceCapabilities(
            provider=provider, lifecycle=lifecycle, can_play=False,
            play_reason="post_live_processing", can_acquire=False,
            acquire_reason="post_live_processing", chat=chat, provisional=provisional,
        )
    segmented = _has_segmented_transport(info)
    fallback_reason = "segmented_transport_not_supported" if segmented else "no_supported_transport"

    # Playback needs either a progressive http(s) stream the relay can pass
    # through directly, or the guarded HLS relay's one supported tracer case (a
    # Twitch VOD master).  Generic segmented transport from other providers
    # stays not-playable with a stable reason.
    if _has_transport_in(info, _PLAYABLE_PROTOCOLS) or supports_hls_relay(info, provider, lifecycle):
        can_play, play_reason = True, None
    else:
        can_play, play_reason = False, fallback_reason

    # Acquisition mirrors the generic SAFE_NATIVE_PROTOCOLS download gate, plus
    # two scoped exceptions: playlist/channel containers acquire as a whole-source
    # job, and the supported Twitch VOD tracer acquires through the guarded
    # native-HLS transport (issue #95). The tracer is a scoped flip, not a change
    # to _ACQUIRABLE_PROTOCOLS, so the #91 generic-capability lockstep still holds.
    if (
        _has_transport_in(info, _ACQUIRABLE_PROTOCOLS)
        or _is_container(info)
        # Issue #95 scopes native-HLS acquisition to the same completed Twitch
        # VOD tracer the relay plays back.
        or supports_hls_relay(info, provider, lifecycle)
    ):
        can_acquire, acquire_reason = True, None
    else:
        can_acquire, acquire_reason = False, fallback_reason

    return MediaSourceCapabilities(
        provider=provider, lifecycle=lifecycle,
        can_play=can_play, play_reason=play_reason,
        can_acquire=can_acquire, acquire_reason=acquire_reason, chat=chat,
        provisional=provisional,
    )


def _chat_capabilities(
    info: Mapping[str, Any], provider: str, lifecycle: str,
) -> MediaSourceChatCapabilities:
    """Expose only the availability state; never carry subtitle URLs or tokens."""

    subtitles = info.get("subtitles")
    live_chat = subtitles.get("live_chat") if isinstance(subtitles, Mapping) else None
    has_json_replay = isinstance(live_chat, list) and any(
        isinstance(track, Mapping) and track.get("ext") == "json" for track in live_chat
    )
    if public_provider(provider).chat_replay and lifecycle == "completed_live" and has_json_replay:
        return MediaSourceChatCapabilities(replay="available")
    # Live chat viewing (2.2.0): YouTube through its chat API when the source exposes a
    # live_chat track, Twitch through anonymous read-only IRC. Kick still needs a sign-in.
    if lifecycle == "live" and ((provider == "youtube" and isinstance(live_chat, list) and live_chat) or provider == "twitch"):
        return MediaSourceChatCapabilities(live="available")
    if provider == "kick" and lifecycle == "live":
        return MediaSourceChatCapabilities(live_reason="authentication_required")
    return MediaSourceChatCapabilities()


def _scheduled_start(info: Mapping[str, Any]) -> datetime | None:
    """The best-available provider start time for an upcoming broadcast, if any.

    yt-dlp records a scheduled broadcast's start as ``release_timestamp`` (epoch
    seconds). It may be absent; the waiter still probes at connect, so scheduling
    is offered even without a precise time.
    """

    timestamp = info.get("release_timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and timestamp > 0:
        return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
    return None


def _access_gate(info: Mapping[str, Any]) -> str | None:
    """The honest reason a source is closed to anonymous viewers, if it is.

    yt-dlp's ``availability`` marks subscriber/membership gating
    (``subscriber_only``/``needs_subscription``) and account gating
    (``needs_auth`` for age/sign-in, ``premium_only``, ``private``). Lumina never
    signs in, so these are distinct not-available states, never a cookie prompt.
    """

    availability = info.get("availability")
    value = availability.lower() if isinstance(availability, str) else ""
    if value in {"subscriber_only", "needs_subscription"}:
        return "subscriber_only"
    if value in {"needs_auth", "premium_only", "private"}:
        return "sign_in_required"
    return None


def _formats(info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    formats = info.get("formats")
    candidates = [item for item in formats if isinstance(item, Mapping)] if isinstance(formats, list) else []
    # A few extractors expose their only format at the top level.
    if isinstance(info.get("url"), str):
        candidates.append(info)
    return candidates


def _is_container(info: Mapping[str, Any]) -> bool:
    return info.get("_type") == "playlist" or bool(info.get("entries"))


def _has_segmented_transport(info: Mapping[str, Any]) -> bool:
    return any(
        isinstance(item.get("protocol"), str)
        and any(marker in item["protocol"].lower() for marker in _SEGMENTED_PROTOCOL_MARKERS)
        for item in _formats(info)
    )


def _has_transport_in(info: Mapping[str, Any], allowed_protocols: set[str]) -> bool:
    """Whether any format declares a protocol in ``allowed_protocols`` with a URL.

    A declared protocol must be in the allowlist, so this rejects rtmp/rtsp/mms
    (which carry a direct URL but delegate networking outside the guarded
    transport) and any segmented protocol the allowlist omits.
    """

    for item in _formats(info):
        protocol = item.get("protocol")
        if isinstance(protocol, str) and protocol.lower() not in allowed_protocols:
            continue
        url = item.get("url")
        if isinstance(url, str) and url:
            return True
        # Sanitized extractor entries can omit an expiring direct URL. An
        # audio-bearing format with no declared protocol remains a native
        # http(s) candidate for both playback and acquisition.
        acodec = item.get("acodec")
        if protocol is None and isinstance(acodec, str) and acodec.lower() != "none":
            return True
    return False
