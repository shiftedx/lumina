"""The 'record from now' capability + its guarded download transport (issue #97).

Recording a currently-live YouTube broadcast is a distinct deliberate action
from ordinary acquisition. It is offered only when Lumina can capture the source
through the same guarded live-HLS transport it plays back, and the media capture
flows through the guarded native-HLS download path — never an unguarded fetch.
"""

from __future__ import annotations

from app.services.hls_relay_support import (
    supports_live_hls_acquisition,
    supports_live_hls_acquisition_source,
)
from app.services.media_capabilities import derive_media_capabilities
from app.services.network_policy import PolicyYoutubeDL


_LIVE_YOUTUBE = {
    "extractor_key": "Youtube",
    "is_live": True,
    "manifest_url": "https://manifest.googlevideo.example/live.m3u8",
    "formats": [
        {"protocol": "m3u8_native", "url": "https://manifest.googlevideo.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"},
    ],
    "subtitles": {"live_chat": [{"ext": "json", "url": "https://youtube.example/live_chat"}]},
}


def test_relayable_live_youtube_offers_record_from_now() -> None:
    caps = derive_media_capabilities(_LIVE_YOUTUBE)
    assert caps.provider == "youtube"
    assert caps.lifecycle == "live"
    assert caps.can_record is True
    assert caps.record_reason is None
    # Recording never masquerades as ordinary acquisition: can_acquire stays the
    # honest live-not-acquirable answer so the batch flow is not offered.
    assert caps.can_acquire is False
    # The live_chat track advertises live chat viewing (2.2.0).
    assert caps.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)


def test_live_youtube_without_relayable_transport_cannot_record() -> None:
    caps = derive_media_capabilities({
        "extractor_key": "Youtube",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert caps.can_record is False
    assert caps.record_reason == "live_record_not_supported"


def test_non_live_sources_do_not_offer_recording() -> None:
    vod = derive_media_capabilities({
        "extractor_key": "Youtube",
        "formats": [{"protocol": "https", "url": "https://media.example/v.webm", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert vod.can_record is False
    assert vod.record_reason is None


_LIVE_TWITCH = {
    "extractor_key": "Twitch",
    "is_live": True,
    "manifest_url": "https://usher.ttvnw.example/live.m3u8",
    "formats": [{"protocol": "m3u8_native", "url": "https://usher.ttvnw.example/live.m3u8"}],
}


def test_live_twitch_is_in_scope_for_recording() -> None:
    # Issue #100 widens the RECORD scope to a currently-live Twitch broadcast: its
    # media records through the same guarded live-HLS transport the #99 relay
    # already serves for Twitch live. Recording is a distinct deliberate action,
    # so ordinary can_acquire stays the honest live-not-acquirable answer.
    caps = derive_media_capabilities(_LIVE_TWITCH)
    assert caps.provider == "twitch"
    assert caps.lifecycle == "live"
    assert caps.can_record is True
    assert caps.record_reason is None
    assert caps.can_acquire is False
    # Provider-native chat needs a connected third-party identity, which is
    # unavailable in 1.0, so no current chat is advertised for the recording.
    assert caps.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)


def test_live_twitch_without_a_relayable_master_cannot_record() -> None:
    # Recording still requires a resolvable HLS master; a live Twitch source that
    # exposes none is an honest not-recordable state, never a false affordance.
    caps = derive_media_capabilities({
        "extractor_key": "Twitch",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native"}],
    })
    assert caps.can_record is False
    assert caps.record_reason == "live_record_not_supported"


def test_live_record_predicate_and_transport_gate_admit_live_twitch() -> None:
    # The record predicate mirrors the live-relay predicate for Twitch too, and
    # the guarded download-transport gate admits native HLS for the live Twitch
    # tracer exactly as it does for the live YouTube tracer.
    assert supports_live_hls_acquisition(_LIVE_TWITCH, "twitch", "live") is True
    assert supports_live_hls_acquisition_source(_LIVE_TWITCH) is True
    policy = PolicyYoutubeDL(params={})
    assert "m3u8_native" in policy._allowed_download_protocols(_LIVE_TWITCH)
    # A completed Twitch VOD is never admitted by the live-record predicate: it is
    # the #95 completed-VOD tracer, not a live recording.
    assert supports_live_hls_acquisition_source({
        "extractor_key": "Twitch",
        "was_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://usher.ttvnw.example/vod.m3u8"}],
    }) is False


def test_live_record_download_transport_admits_guarded_native_hls() -> None:
    # The record predicate mirrors the live-relay predicate, so the media
    # capture is admitted only for the same currently-live YouTube tracer.
    assert supports_live_hls_acquisition(_LIVE_YOUTUBE, "youtube", "live") is True
    assert supports_live_hls_acquisition_source(_LIVE_YOUTUBE) is True
    # A completed VOD is never admitted by the live-record predicate.
    assert supports_live_hls_acquisition_source({
        "extractor_key": "Youtube",
        "formats": [{"protocol": "https", "url": "https://media.example/v.webm"}],
    }) is False


def test_transport_gate_allows_native_hls_only_for_the_live_tracer() -> None:
    policy = PolicyYoutubeDL(params={})
    allowed = policy._allowed_download_protocols(_LIVE_YOUTUBE)
    assert "m3u8_native" in allowed
    # A generic live HLS source (not YouTube) stays on the generic gate.
    generic_allowed = policy._allowed_download_protocols({
        "extractor_key": "Generic",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://other.example/live.m3u8"}],
    })
    assert "m3u8_native" not in generic_allowed
