from app.services.media_capabilities import derive_media_capabilities


def test_derives_safe_actions_for_provider_lifecycles_and_transport() -> None:
    youtube_live = derive_media_capabilities({
        "extractor_key": "Youtube",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert youtube_live.provider == "youtube"
    assert youtube_live.lifecycle == "live"
    assert youtube_live.can_play is False
    assert youtube_live.play_reason == "live_playback_not_supported"
    assert youtube_live.can_acquire is False
    assert youtube_live.acquire_reason == "live_acquisition_not_supported"
    assert youtube_live.chat.live == "unavailable"

    twitch_vod = derive_media_capabilities({
        "extractor_key": "Twitch",
        "live_status": "was_live",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/vod.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_vod.provider == "twitch"
    assert twitch_vod.lifecycle == "completed_live"
    # The guarded HLS relay's tracer case: a Twitch VOD master plays through
    # Lumina even though its transport is segmented.
    assert twitch_vod.can_play is True
    assert twitch_vod.play_reason is None
    # Issue #95: the same Twitch VOD tracer is now acquirable through the guarded
    # native-HLS download transport. The generic SAFE_NATIVE_PROTOCOLS gate is
    # unchanged; only this scoped Twitch-VOD case flips acquire true.
    assert twitch_vod.can_acquire is True
    assert twitch_vod.acquire_reason is None

    generic_audio = derive_media_capabilities({
        "extractor_key": "Generic",
        "formats": [{"protocol": "https", "url": "https://media.example/audio.webm", "vcodec": "none", "acodec": "opus"}],
    })
    assert generic_audio.provider == "generic"
    assert generic_audio.lifecycle == "vod"
    assert generic_audio.can_play is True
    assert generic_audio.can_acquire is True


def test_handles_incomplete_and_unknown_metadata_without_provider_guesses() -> None:
    upcoming = derive_media_capabilities({"extractor": "youtube", "live_status": "is_upcoming"})
    assert upcoming.lifecycle == "upcoming"
    assert upcoming.play_reason == "upcoming_not_started"
    assert upcoming.acquire_reason == "upcoming_not_started"

    post_live = derive_media_capabilities({"extractor_key": "Youtube", "live_status": "post_live"})
    assert post_live.lifecycle == "post_live"
    assert post_live.play_reason == "post_live_processing"

    unknown = derive_media_capabilities({})
    assert unknown.provider == "unknown"
    assert unknown.lifecycle == "vod"
    assert unknown.can_play is False
    # With no http(s) transport evidence at all there is nothing to act on, but
    # provider identity alone no longer blocks a source.
    assert unknown.play_reason == "no_supported_transport"
    assert unknown.can_acquire is False
    assert unknown.acquire_reason == "no_supported_transport"
    assert unknown.chat.replay == "unavailable"


def test_keeps_progressive_vod_and_audio_actions_when_segmented_alternatives_are_also_listed() -> None:
    mixed = derive_media_capabilities({
        "extractor_key": "Youtube",
        "formats": [
            {"protocol": "m3u8_native", "url": "https://media.example/playlist.m3u8", "vcodec": "avc1", "acodec": "mp4a"},
            {"protocol": "https", "url": "https://media.example/fallback.mp4", "vcodec": "avc1", "acodec": "mp4a"},
        ],
    })
    assert mixed.lifecycle == "vod"
    assert mixed.can_play is True
    assert mixed.can_acquire is True


def test_non_http_transports_are_never_playable_or_acquirable() -> None:
    # rtmp/rtsp/mms delegate networking outside Lumina's guarded transport, so a
    # declared non-http(s) protocol must never look playable or acquirable, even
    # when it carries a direct URL.
    for protocol in ("rtmp", "rtsp", "mms"):
        caps = derive_media_capabilities({
            "extractor_key": "Generic",
            "formats": [{"protocol": protocol, "url": f"{protocol}://media.example/stream", "vcodec": "avc1", "acodec": "mp4a"}],
        })
        assert caps.can_play is False, protocol
        assert caps.play_reason == "no_supported_transport", protocol
        assert caps.can_acquire is False, protocol
        assert caps.acquire_reason == "no_supported_transport", protocol


def test_unknown_long_tail_provider_with_progressive_http_stays_actionable() -> None:
    # Provider identity is descriptive only: a provider outside the recognized set
    # ("unknown") with a progressive http(s) transport stays playable and acquirable.
    caps = derive_media_capabilities({
        "extractor_key": "Vimeo",
        "formats": [{"protocol": "https", "url": "https://media.example/clip.mp4", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert caps.provider == "unknown"
    assert caps.can_play is True
    assert caps.play_reason is None
    assert caps.can_acquire is True
    assert caps.acquire_reason is None


def test_playlist_container_without_direct_formats_stays_acquirable_as_a_whole_source() -> None:
    caps = derive_media_capabilities({
        "extractor_key": "Youtube",
        "_type": "playlist",
        "entries": [{"id": "a"}, {"id": "b"}],
    })
    assert caps.lifecycle == "vod"
    assert caps.can_play is False
    assert caps.can_acquire is True
    assert caps.acquire_reason is None


def test_realistic_youtube_adaptive_only_vod_is_acquirable() -> None:
    # A real YouTube VOD often exposes only adaptive (video-only + audio-only)
    # https formats with no progressive muxed stream.
    caps = derive_media_capabilities({
        "extractor_key": "Youtube",
        "formats": [
            {"protocol": "https", "url": "https://media.example/video.mp4", "vcodec": "avc1.640028", "acodec": "none"},
            {"protocol": "https", "url": "https://media.example/audio.m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ],
    })
    assert caps.provider == "youtube"
    assert caps.can_play is True
    assert caps.can_acquire is True


def test_twitch_vod_hls_is_acquirable_but_other_segmented_transports_are_not() -> None:
    # Issue #95: a Twitch VOD exposed as HLS is acquirable through the guarded
    # native-HLS download transport (the same tracer the relay plays back).
    # ISM/F4M stay outside the supported native downloader and never acquirable.
    for protocol in ("m3u8", "m3u8_native"):
        caps = derive_media_capabilities({
            "extractor_key": "Twitch",
            "live_status": "was_live",
            "formats": [{"protocol": protocol, "url": f"https://media.example/vod-{protocol}", "vcodec": "avc1", "acodec": "mp4a"}],
        })
        assert caps.can_acquire is True, protocol
        assert caps.acquire_reason is None, protocol
        assert caps.can_play is True, protocol
        assert caps.play_reason is None, protocol
    for protocol in ("ism", "f4m"):
        caps = derive_media_capabilities({
            "extractor_key": "Twitch",
            "live_status": "was_live",
            "formats": [{"protocol": protocol, "url": f"https://media.example/vod-{protocol}", "vcodec": "avc1", "acodec": "mp4a"}],
        })
        assert caps.can_acquire is False, protocol
        assert caps.acquire_reason == "segmented_transport_not_supported", protocol
        assert caps.can_play is False, protocol
        assert caps.play_reason == "segmented_transport_not_supported", protocol


def test_acquisition_is_scoped_to_the_twitch_vod_tracer_not_blanket_hls() -> None:
    # Generic HLS from another provider is NOT acquirable: the guarded native-HLS
    # acquire path is scoped to the Twitch VOD tracer, not a blanket HLS-acquire.
    generic_hls = derive_media_capabilities({
        "extractor_key": "Generic",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/stream.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert generic_hls.can_acquire is False
    assert generic_hls.acquire_reason == "segmented_transport_not_supported"

    # A live Twitch stream is never acquirable through the tracer (live media is a
    # transient viewing session); playability is covered separately (#99).
    twitch_live = derive_media_capabilities({
        "extractor_key": "Twitch",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_live.can_acquire is False
    assert twitch_live.acquire_reason == "live_acquisition_not_supported"

    # An ordinary Twitch VOD (was never a live broadcast) is also acquirable.
    twitch_plain_vod = derive_media_capabilities({
        "extractor_key": "Twitch",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/vod.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_plain_vod.lifecycle == "vod"
    assert twitch_plain_vod.can_acquire is True
    assert twitch_plain_vod.acquire_reason is None


def test_relay_playback_is_scoped_to_the_twitch_vod_tracer_case() -> None:
    # A completed Twitch VOD master is the one supported relay case.
    twitch_vod = derive_media_capabilities({
        "extractor_key": "Twitch",
        "live_status": "was_live",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/vod.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_vod.can_play is True

    # Generic HLS from another provider stays not-playable: the relay is not a
    # general-purpose segmented-transport gateway.
    generic_hls = derive_media_capabilities({
        "extractor_key": "Generic",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/stream.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert generic_hls.can_play is False
    assert generic_hls.play_reason == "segmented_transport_not_supported"

    # A live Twitch stream now plays at the current edge through the live relay
    # (#99), the same guarded transport as live YouTube (#96). The VOD relay
    # scoping asserted above is unchanged; only the live-relay provider set widened.
    twitch_live = derive_media_capabilities({
        "extractor_key": "Twitch",
        "is_live": True,
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_live.can_play is True
    assert twitch_live.play_reason is None

    # DASH over http_dash_segments IS a safe native download protocol: acquirable
    # server-side while still not playable through the progressive transport.
    dash = derive_media_capabilities({
        "extractor_key": "Youtube",
        "formats": [
            {"protocol": "http_dash_segments", "url": "https://media.example/video.mp4", "vcodec": "avc1.640028", "acodec": "none"},
            {"protocol": "http_dash_segments", "url": "https://media.example/audio.m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ],
    })
    assert dash.can_play is False
    assert dash.play_reason == "segmented_transport_not_supported"
    assert dash.can_acquire is True
    assert dash.acquire_reason is None


def test_acquire_allowlist_stays_in_lockstep_with_the_download_transport_policy() -> None:
    from app.services.media_capabilities import _ACQUIRABLE_PROTOCOLS
    from app.services.network_policy import PolicyYoutubeDL

    assert _ACQUIRABLE_PROTOCOLS == PolicyYoutubeDL.SAFE_NATIVE_PROTOCOLS


def test_youtube_live_with_an_hls_master_plays_at_the_edge_with_live_chat() -> None:
    # A currently-live YouTube source whose HLS master the live relay can serve
    # becomes playable at the current edge; acquisition stays closed. The
    # source exposes a live_chat track, so live chat is advertised (2.2.0).
    live = derive_media_capabilities({
        "extractor_key": "Youtube",
        "is_live": True,
        "manifest_url": "https://manifest.example/live.m3u8?token=private",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
        "subtitles": {"live_chat": [{"ext": "json", "url": "https://secret.example/chat?token=private"}]},
    })
    assert live.provider == "youtube"
    assert live.lifecycle == "live"
    assert live.can_play is True
    assert live.play_reason is None
    # Live media is transient and never acquired to the vault.
    assert live.can_acquire is False
    assert live.acquire_reason == "live_acquisition_not_supported"
    # 2.2.0: the live_chat track advertises live chat again; it will
    # surface as replay chat only once the broadcast completes.
    assert live.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)
    assert live.chat.replay == "unavailable"
    # No upstream address, token, or continuation ever leaks through capabilities.
    assert "secret.example" not in repr(live)
    assert "manifest.example" not in repr(live)


def test_youtube_live_without_a_relayable_master_stays_not_playable() -> None:
    # A live YouTube source with no HLS master to relay keeps the honest
    # not-supported reason rather than pretending to be playable.
    live = derive_media_capabilities({
        "extractor_key": "Youtube",
        "is_live": True,
        "formats": [{"protocol": "http_dash_segments", "url": "https://media.example/live.mpd", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert live.lifecycle == "live"
    assert live.can_play is False
    assert live.play_reason == "live_playback_not_supported"
    # Current chat is still advertised so a member can follow chat even when the
    # media transport is not one Lumina can relay.
    assert live.chat.live == "unavailable"


def test_twitch_live_with_an_hls_master_is_relayed_at_the_current_edge() -> None:
    # #99 widened the live relay from YouTube to Twitch: a live Twitch source with
    # an HLS master plays at the current edge, while VOD relay scoping is
    # unchanged. Current chat is not advertised: provider-native chat needs a
    # connected third-party identity, which is unavailable in 1.0.
    twitch_live = derive_media_capabilities({
        "extractor_key": "Twitch",
        "is_live": True,
        "manifest_url": "https://media.example/live.m3u8",
        "formats": [{"protocol": "m3u8_native", "url": "https://media.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    })
    assert twitch_live.can_play is True
    assert twitch_live.play_reason is None
    assert twitch_live.chat.live == "available"  # 2.2.0: live chat viewing (YouTube API, Twitch anonymous IRC)


def test_flat_youtube_live_search_entry_is_provisionally_playable() -> None:
    # A flat search/discovery entry carries only identity fields and a watch-page
    # URL -- no formats, no manifest, no protocol -- so relay support cannot be
    # judged from it. For a live-relay provider, advertise playable-in-principle;
    # the Watch surface re-inspects the fully extracted source and makes the
    # honest final call, and the live relay itself still fails closed.
    flat_live = derive_media_capabilities({
        "_type": "url",
        "ie_key": "Youtube",
        "extractor_key": "youtube",
        "url": "https://www.youtube.com/watch?v=abc123",
        "title": "t",
        "live_status": "is_live",
    })
    assert flat_live.can_play is True
    assert flat_live.play_reason is None
    assert flat_live.can_acquire is False
    assert flat_live.acquire_reason == "live_acquisition_not_supported"
    assert flat_live.can_record is False


def test_flat_twitch_live_entry_is_provisionally_playable() -> None:
    flat_live = derive_media_capabilities({
        "_type": "url",
        "ie_key": "Twitch",
        "extractor_key": "Twitch",
        "url": "https://www.twitch.tv/somechannel",
        "title": "t",
        "live_status": "is_live",
    })
    assert flat_live.can_play is True
    assert flat_live.play_reason is None


def test_flat_live_entry_from_a_non_relay_provider_stays_fail_closed() -> None:
    # The provisional case is scoped to live-relay providers only; a flat live
    # entry from a provider the live relay does not serve keeps the honest
    # not-supported reason rather than guessing playability.
    flat_live = derive_media_capabilities({
        "_type": "url",
        "extractor_key": "Generic",
        "url": "https://example.com/watch?v=abc123",
        "title": "t",
        "live_status": "is_live",
    })
    assert flat_live.can_play is False
    assert flat_live.play_reason == "live_playback_not_supported"


def test_empty_formats_live_source_is_fail_closed() -> None:
    # An inspected live source with an empty formats list carries evidence of NO
    # relayable transport — it must never fall into the provisional flat-entry case.
    live = derive_media_capabilities({
        "extractor_key": "Youtube",
        "live_status": "is_live",
        "formats": [],
    })
    assert live.can_play is False
    assert live.play_reason == "live_playback_not_supported"


def test_subscriber_gated_flat_youtube_live_entry_stays_subscriber_only() -> None:
    # Subscriber gating is checked first and wins over the provisional case: a
    # flat entry the member cannot watch at all must never be advertised as
    # playable-in-principle.
    flat_live = derive_media_capabilities({
        "_type": "url",
        "ie_key": "Youtube",
        "extractor_key": "youtube",
        "url": "https://www.youtube.com/watch?v=abc123",
        "title": "t",
        "live_status": "is_live",
        "availability": "subscriber_only",
    })
    assert flat_live.can_play is False
    assert flat_live.play_reason == "subscriber_only"


def test_exposes_youtube_completed_live_chat_replay_only_from_safe_subtitle_evidence() -> None:
    completed = derive_media_capabilities({
        "extractor_key": "Youtube",
        "live_status": "was_live",
        "formats": [{"protocol": "https", "url": "https://media.example/archive.mp4", "vcodec": "avc1", "acodec": "mp4a"}],
        "subtitles": {"live_chat": [{"ext": "json", "url": "https://secret.example/chat?token=private"}]},
    })
    assert completed.lifecycle == "completed_live"
    assert completed.chat.live == "unavailable"
    assert completed.chat.replay == "available"
    assert "secret.example" not in repr(completed)
