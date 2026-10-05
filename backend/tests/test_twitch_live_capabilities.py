"""Issue #99 capability seam: a currently-live Twitch source plays at the edge.

The guarded live-HLS relay (#96) now supports live Twitch as well as live
YouTube. A live Twitch source plays at the edge but advertises NO current chat:
provider-native chat needs a connected third-party identity, which is
unavailable in 1.0. A subscriber-gated live source is a distinct, honest
not-playable state. Acquisition of live media stays unsupported and no upstream
address is exposed here.
"""

from __future__ import annotations

from app.services.hls_relay_support import supports_live_hls_relay
from app.services.media_capabilities import derive_media_capabilities


def _live_twitch(**extra):
    info = {
        "extractor_key": "Twitch",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    }
    info.update(extra)
    return info


def test_live_twitch_master_is_playable_at_the_current_edge() -> None:
    caps = derive_media_capabilities(_live_twitch())
    assert caps.provider == "twitch"
    assert caps.lifecycle == "live"
    assert caps.can_play is True
    assert caps.play_reason is None
    # Live media is a transient viewing session, never acquired to the vault.
    assert caps.can_acquire is False
    assert caps.acquire_reason == "live_acquisition_not_supported"


def test_live_twitch_advertises_current_chat() -> None:
    caps = derive_media_capabilities(_live_twitch())
    assert caps.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)
    assert caps.chat.replay == "unavailable"


def test_live_twitch_advertises_chat_even_when_media_is_not_relayable() -> None:
    # Chat is read anonymously over IRC, independent of the media transport.
    caps = derive_media_capabilities({"extractor_key": "Twitch", "is_live": True, "formats": []})
    assert caps.can_play is False
    assert caps.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)


def test_subscriber_only_live_twitch_is_a_distinct_not_playable_state() -> None:
    caps = derive_media_capabilities(_live_twitch(availability="subscriber_only"))
    assert caps.can_play is False
    assert caps.play_reason == "subscriber_only"
    # Chat stays readable even when the media is subscriber-only.
    assert caps.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)


def test_live_relay_predicate_now_covers_twitch_and_youtube() -> None:
    assert supports_live_hls_relay(_live_twitch(), "twitch", "live") is True
    youtube_live = {"extractor_key": "Youtube", "is_live": True, "manifest_url": "https://m.example/live.m3u8"}
    assert supports_live_hls_relay(youtube_live, "youtube", "live") is True
    # A non-live Twitch source is not a live-relay case.
    assert supports_live_hls_relay({"extractor_key": "Twitch"}, "twitch", "vod") is False
