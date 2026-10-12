"""One public provider contract, with separate truthful per-action states."""

from app.services import hls_relay_support as support
from app.services.hls_relay_support import PUBLIC_PROVIDERS, derive_provider
from app.services.media_capabilities import derive_media_capabilities
from app.services.yt_dlp_service import YtDlpService


def test_kick_not_unknown() -> None:
    for identity in (
        {"extractor_key": "Kick", "extractor": "kick:live"},
        {"extractor_key": "KickVOD", "extractor": "kick:vod"},
        {"extractor_key": "KickClip", "extractor": "kick:clips"},
    ):
        assert derive_provider(identity) == "kick", identity
    # Lookalike extractors and a lookalike host resolved by the generic extractor
    # never become Kick.
    assert derive_provider({"extractor_key": "KickStarter", "extractor": "KickStarter"}) == "unknown"
    assert derive_provider({"extractor_key": "Kicker"}) == "unknown"
    assert derive_provider({"extractor_key": "Generic", "webpage_url": "https://kick.com.evil.example/x"}) == "generic"

    # Kick actions follow per-source transport evidence. Record-from-now needs
    # a live HLS master (#142; none here); scheduling is never offered for Kick.
    expected = {"vod": (True, True), "live": (False, False), "upcoming": (False, False)}
    for lifecycle, extra in (("vod", {}), ("live", {"is_live": True}), ("upcoming", {"live_status": "is_upcoming"})):
        caps = derive_media_capabilities({
            "extractor_key": "KickClip", **extra,
            "formats": [{"protocol": "https", "url": "https://clips.kick.example/c.mp4", "acodec": "mp4a"}],
        })
        assert (caps.provider, caps.lifecycle) == ("kick", lifecycle)
        assert (caps.can_play, caps.can_acquire) == expected[lifecycle]
        assert (caps.can_record, caps.can_schedule) == (False, False)
    assert caps.schedule_reason == "upcoming_schedule_not_supported"


def test_capability_does_not_imply_all_actions() -> None:
    playable_twitch_live = derive_media_capabilities({
        "extractor_key": "Twitch", "is_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://video.twitch.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert playable_twitch_live.can_play is True
    assert playable_twitch_live.can_acquire is False
    assert playable_twitch_live.acquire_reason == "live_acquisition_not_supported"
    assert playable_twitch_live.can_schedule is False
    assert playable_twitch_live.from_start_available is False
    assert (playable_twitch_live.chat.live, playable_twitch_live.chat.replay) == ("available", "unavailable")


def test_source_metadata_no_tokens() -> None:
    signed = "https://edge.example/v.m3u8?sig=SECRET-SIG&token=SECRET-TOKEN"
    caps = derive_media_capabilities({
        "extractor_key": "Youtube", "was_live": True, "manifest_url": signed,
        "formats": [{"protocol": "https", "url": signed, "acodec": "mp4a", "http_headers": {"Cookie": "SECRET-COOKIE"}}],
        "subtitles": {"live_chat": [{"ext": "json", "url": signed}]},
    })
    assert caps.chat.replay == "available"
    dumped = caps.model_dump_json()
    for secret in ("SECRET", "http", "sig=", "token"):
        assert secret not in dumped


def test_unknown_provisional_truth() -> None:
    flat = YtDlpService.normalize_search_result(
        {"id": "abc", "title": "Flat", "url": "https://www.youtube.com/watch?v=abc", "ie_key": "Youtube"}, "youtube",
    )
    assert flat.capabilities is not None and flat.capabilities.provisional is True
    # Inspection that found no usable transport replaces the guess: not playable
    # and no longer provisional.
    inspected = derive_media_capabilities({"extractor_key": "Youtube", "formats": []})
    assert inspected.provisional is False
    assert inspected.can_play is False and inspected.can_acquire is False


def test_provider_table_matches_enforcement() -> None:
    assert set(PUBLIC_PROVIDERS) == {"youtube", "twitch", "kick", "soundcloud", "generic"}
    for name, provider in PUBLIC_PROVIDERS.items():
        assert provider.live == support.is_live_relay_provider(name), name
        assert (name in support._LIVE_RECORD_PROVIDERS) <= provider.live, name
        assert provider.chat_replay == (name == "youtube"), name
        if not provider.playback:
            # Resolution-only (vod) is allowed; no action may outrun playback.
            assert not (provider.search or provider.channel or provider.live or provider.download), name
    assert YtDlpService.normalize_search_result({"id": "x"}, "soundcloud").source_label == "SoundCloud"
