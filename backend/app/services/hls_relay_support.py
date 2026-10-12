"""Decide whether an inspected source is the guarded HLS-relay tracer case.

Kept free of heavy imports so the capability seam can consult it cheaply. The
tracer that the relay supports is a completed Twitch VOD exposed as an HLS
(``m3u8``) master. Generic HLS from arbitrary providers is deliberately not
relayable in this slice and stays not-playable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PublicProvider:
    """What Lumina supports for one provider without any sign-in.

    This is the declared product contract. Transport enforcement stays in the
    scoped predicates below; ``test_v1_provider_contract`` keeps the two in
    lockstep. ``playback``/``download`` False blocks every per-source action;
    True still leaves each source to its own transport evidence. ``vod`` alone
    (without ``playback``) means public links resolve to metadata only.
    """

    label: str
    search: bool = False
    channel: bool = False
    live: bool = False
    vod: bool = False
    playback: bool = False
    download: bool = False
    chat_replay: bool = False


PUBLIC_PROVIDERS: dict[str, PublicProvider] = {
    "youtube": PublicProvider(
        "YouTube", search=True, channel=True, live=True, vod=True,
        playback=True, download=True, chat_replay=True,
    ),
    # Twitch search is the public live directory; VOD chat is never backfilled.
    "twitch": PublicProvider(
        "Twitch", search=True, channel=True, live=True, vod=True, playback=True, download=True,
    ),
    "soundcloud": PublicProvider(
        "SoundCloud", search=True, channel=True, vod=True, playback=True, download=True,
    ),
    # Kick VOD/clip/live media flows only through the guarded HLS relay
    # (anonymous extraction; no curl_cffi impersonation, cookies or token).
    # Record-from-now is in _LIVE_RECORD_PROVIDERS (#142). Scheduling and
    # discovery stay off: yt-dlp's Kick extractor exposes no upcoming broadcasts.
    "kick": PublicProvider("Kick", live=True, vod=True, playback=True, download=True),
    "generic": PublicProvider("Web", vod=True, playback=True, download=True),
}


def public_provider(provider: str) -> PublicProvider:
    """Unrecognized extractors are generic public URL support, never first-class."""
    return PUBLIC_PROVIDERS.get(provider, PUBLIC_PROVIDERS["generic"])

# Kick VODs/clips are the same IVS-style HLS as Twitch VODs; the guarded
# relay plays them and GuardedHlsFD acquires them through the same predicate.
_RELAY_PROVIDERS = frozenset({"twitch", "kick"})
_RELAY_LIFECYCLES = frozenset({"vod", "completed_live"})

# The live-HLS relay serves currently-live YouTube (#96), Twitch (#99) and Kick
# at the current edge. All flow through the same guarded relay, fail-closed
# rewriting, and bounded live session; only the provider allowlist widened. Every
# other provider stays not-playable, and broadening this set further is a
# deliberate, separately reviewed change.
_LIVE_RELAY_PROVIDERS = frozenset({"youtube", "twitch", "kick"})
_LIVE_RELAY_LIFECYCLES = frozenset({"live"})
# Live "record from now" records currently-live YouTube (#97), Twitch (#100) and
# Kick (#142, proven against a public broadcast) through the same guarded live-HLS transport the relay already serves. #97
# deliberately scoped this to YouTube only; #100 admits Twitch to the RECORD scope
# following the same tracer discipline, because #99 proved the guarded relay,
# fail-closed rewriting, and bounded live session for Twitch live. Every other
# provider stays out of the record scope, so recording capability never follows
# viewing onto a provider recording does not support; widening this set further is
# a deliberate, separately reviewed change.
_LIVE_RECORD_PROVIDERS = frozenset({"youtube", "twitch", "kick"})
# Scheduling an UPCOMING broadcast + recording it from the beginning (issue #98) is
# scoped to YouTube — a strict SUBSET of the live-record providers. Twitch live
# recording (#100) is strictly forward-only from the moment Lumina connects, so it
# has no schedule-and-wait or from-start affordance; only YouTube exposes the
# experimental from-start capability. From-start never widens the record set.
_SCHEDULE_PROVIDERS = frozenset({"youtube"})
_FROM_START_PROVIDERS = frozenset({"youtube"})
_FROM_START_LIFECYCLES = frozenset({"live", "upcoming"})


_KICK_EXTRACTORS = frozenset({"kick", "kickvod", "kickclip", "kick:live", "kick:vod", "kick:clips"})


def derive_provider(info: Mapping[str, Any]) -> str:
    """Normalize the extractor identity to Lumina's descriptive provider name.

    Kept here, at the lowest transport-independent layer, so the capability seam
    and the download-transport gate agree on the tracer without either importing
    the other.
    """

    identity = " ".join(
        value.lower()
        for value in (info.get("extractor"), info.get("extractor_key"))
        if isinstance(value, str)
    )
    if "youtube" in identity or "youtu.be" in identity:
        return "youtube"
    if "twitch" in identity:
        return "twitch"
    # Exact yt-dlp Kick extractors only: KickStarter/Kicker are lookalikes.
    if identity.split() and set(identity.split()) <= _KICK_EXTRACTORS:
        return "kick"
    if "soundcloud" in identity:
        return "soundcloud"
    if "generic" in identity:
        return "generic"
    return "unknown"


def derive_lifecycle(info: Mapping[str, Any]) -> str:
    """Return the user-facing source lifecycle from sanitized extractor metadata."""

    live_status = info.get("live_status")
    status = live_status.lower() if isinstance(live_status, str) else ""
    if info.get("is_live") is True or status in {"is_live", "live"}:
        return "live"
    if info.get("is_upcoming") is True or status in {"is_upcoming", "upcoming"}:
        return "upcoming"
    if status in {"post_live", "postlive"}:
        return "post_live"
    if info.get("was_live") is True or status in {"was_live", "completed", "completed_live"}:
        return "completed_live"
    return "vod"


def _m3u8_formats(info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    formats = info.get("formats")
    candidates = [item for item in formats if isinstance(item, Mapping)] if isinstance(formats, list) else []
    if isinstance(info.get("url"), str) or isinstance(info.get("manifest_url"), str):
        candidates.append(info)
    hls: list[Mapping[str, Any]] = []
    for item in candidates:
        protocol = item.get("protocol")
        if isinstance(protocol, str) and "m3u8" in protocol.lower():
            hls.append(item)
    return hls


def _master_url_from_formats(formats: list[Mapping[str, Any]]) -> str | None:
    for item in formats:
        manifest_url = item.get("manifest_url")
        if isinstance(manifest_url, str) and manifest_url:
            return manifest_url
    for item in formats:
        url = item.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def extract_master_url(info: Mapping[str, Any]) -> str | None:
    """Return the upstream HLS master address the relay should parse, if any."""

    top_manifest = info.get("manifest_url")
    if isinstance(top_manifest, str) and top_manifest:
        return top_manifest
    return _master_url_from_formats(_m3u8_formats(info))


def extract_request_headers(info: Mapping[str, Any]) -> dict[str, str]:
    """Return the upstream request headers to apply when fetching relay resources.

    For HLS these headers apply to the master, media playlists, and segments alike.
    They are used server-side only and never surface to the browser.
    """

    top_headers = info.get("http_headers")
    if isinstance(top_headers, Mapping) and top_headers:
        return {str(key): str(value) for key, value in top_headers.items()}
    for item in _m3u8_formats(info):
        headers = item.get("http_headers")
        if isinstance(headers, Mapping) and headers:
            return {str(key): str(value) for key, value in headers.items()}
    return {}


def supports_hls_relay(info: Mapping[str, Any], provider: str, lifecycle: str) -> bool:
    """Whether ``info`` is a Twitch VOD whose HLS master the relay can serve."""

    if provider not in _RELAY_PROVIDERS or lifecycle not in _RELAY_LIFECYCLES:
        return False
    return extract_master_url(info) is not None


def supports_hls_acquisition_source(info: Mapping[str, Any]) -> bool:
    """Tracer-acquire predicate that derives provider and lifecycle from ``info``.

    The download-transport gate consults this with only an extractor info dict in
    hand; it derives provider and lifecycle through the same shared helpers the
    capability seam uses, so the gate and the advertised ``can_acquire`` cannot
    drift apart for the tracer.
    """

    return supports_hls_relay(info, derive_provider(info), derive_lifecycle(info))


def supports_live_hls_acquisition(info: Mapping[str, Any], provider: str, lifecycle: str) -> bool:
    """Whether Lumina may RECORD ``info`` through the guarded native-HLS transport.

    Live recording is scoped to a currently-live source the live relay plays back:
    a YouTube (#97), Twitch (#100) or Kick (#142) ``live`` source exposing an HLS master. The
    media capture flows through the same guarded transport (``GuardedHlsFD`` /
    the guarded relay fetch) as the Twitch VOD tracer, so no unguarded fetch
    reaches an upstream address. Every other provider stays out of scope, so the
    generic ``SAFE_NATIVE_PROTOCOLS`` gate — and the #91 capability lockstep — is
    unchanged; this is a scoped tracer flip, never a blanket live-acquire.

    Recording is scoped to its own provider set (``_LIVE_RECORD_PROVIDERS``)
    rather than the live-relay set, so record capability is governed deliberately
    and never silently follows a future relay-provider widening.
    """

    if provider not in _LIVE_RECORD_PROVIDERS or lifecycle not in _LIVE_RELAY_LIFECYCLES:
        return False
    return extract_master_url(info) is not None


def supports_live_hls_acquisition_source(info: Mapping[str, Any]) -> bool:
    """Live-record predicate that derives provider and lifecycle from ``info``.

    The download-transport gate consults this with only an extractor info dict in
    hand, deriving provider and lifecycle through the same shared helpers the
    capability seam uses, so the gate and the advertised ``can_record`` cannot
    drift apart for the live tracer.
    """

    return supports_live_hls_acquisition(info, derive_provider(info), derive_lifecycle(info))


def supports_scheduled_recording(info: Mapping[str, Any], provider: str, lifecycle: str) -> bool:
    """Whether Lumina can schedule + wait for this UPCOMING source (issue #98).

    Scoped to a YouTube ``upcoming`` broadcast — the same provider Lumina can
    record once it goes live. The waiter re-inspects at connect, so a master
    address is not required now (an upcoming source rarely exposes one yet).
    """

    del info  # An upcoming source is identified by provider + lifecycle alone.
    return provider in _SCHEDULE_PROVIDERS and lifecycle == "upcoming"


def supports_live_from_start(info: Mapping[str, Any], provider: str, lifecycle: str) -> bool:
    """Whether the source offers the best-effort from-start (record-from-beginning).

    This is yt-dlp's experimental live-from-start capability, scoped to YouTube live
    and upcoming sources. It advertises only that the INTENT can be offered; the
    honest whole-broadcast-history determination happens at capture time, and an
    incomplete beginning is reported as a partial-history condition (issue #98).
    """

    del info
    return provider in _FROM_START_PROVIDERS and lifecycle in _FROM_START_LIFECYCLES


def supports_live_hls_relay(info: Mapping[str, Any], provider: str, lifecycle: str) -> bool:
    """Whether ``info`` is a currently-live source the live relay serves.

    Same master-address extraction as the VOD relay, but scoped to the live
    lifecycle and the live-relay provider set (YouTube #96 and Twitch #99). A live
    source is played at the current edge and never registered as a seekable VOD
    session.
    """

    if provider not in _LIVE_RELAY_PROVIDERS or lifecycle not in _LIVE_RELAY_LIFECYCLES:
        return False
    return extract_master_url(info) is not None


def has_transport_evidence(info: Mapping[str, Any]) -> bool:
    """Whether the extractor result carries any concrete transport metadata.

    A fully inspected source always exposes transport evidence: a formats
    list (even an empty one — inspection ran and found none), a manifest
    address, or a top-level declared protocol. A flat search/playlist entry
    carries none of these keys at all — only identity fields and a
    watch-page URL — so its transport support is unknown rather than absent.
    An empty ``formats`` list therefore still counts as evidence: it is the
    confirmed-no-formats case (fail-closed), distinct from never having
    looked.
    """
    formats = info.get("formats")
    if isinstance(formats, list):
        return True
    manifest_url = info.get("manifest_url")
    if isinstance(manifest_url, str) and manifest_url:
        return True
    protocol = info.get("protocol")
    if isinstance(protocol, str) and protocol:
        return True
    return False


def is_live_relay_provider(provider: str) -> bool:
    """Whether the guarded live-HLS relay serves this provider at all."""
    return provider in _LIVE_RELAY_PROVIDERS
