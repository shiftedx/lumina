"""One pure, table-tested playback decision for browsers and Jellyfin clients."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.playback_decision import (
    BROWSER_DEFAULT, Limits, VideoCap, bit_depth, caps_from_browser_profiles, caps_from_device_profile, decide,
)


@pytest.mark.parametrize("source_codec", ["eac3", "dts", "truehd"])
@pytest.mark.parametrize("channels", [6, 8])
def test_roku_audio_conversion_respects_aac_specific_channel_limit(source_codec: str, channels: int) -> None:
    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac,ac3"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "ac3", "MaxAudioChannels": "8"}],
        "CodecProfiles": [{"Type": "VideoAudio", "Codec": "aac", "Conditions": [
            {"Condition": "LessThanEqual", "Property": "AudioChannels", "Value": "2"},
        ]}],
    }
    decision = decide(caps_from_device_profile(profile), facts(MKV, v("hevc", pix_fmt="yuv420p10le"), a(source_codec, channels)))
    assert decision.mode == "transcode" and decision.audio == "encode"
    assert decision.audio_channels == 2


def v(
    codec="h264", height=1080, width=1920, pix_fmt="yuv420p", transfer=None, dv=None, compat=None,
    dv_level=None, rpu=None, el=None, bl=None, index=0,
):
    return {"index": index, "type": "video", "codec": codec, "height": height, "width": width, "pix_fmt": pix_fmt,
            "color_transfer": transfer, "dv_profile": dv, "dv_compat": compat, "dv_level": dv_level,
            "dv_rpu_present": rpu, "dv_el_present": el, "dv_bl_present": bl, "default": True}


def a(codec="aac", channels=2, index=1, default=True, bit_depth=16):
    return {"index": index, "type": "audio", "codec": codec, "channels": channels, "default": default, "bit_depth": bit_depth}


def s(codec="subrip", index=2):
    return {"index": index, "type": "subtitle", "codec": codec}


def facts(container, *streams, bit_rate=5_000_000, webm=False):
    return {"container": container, "webm_suffix": webm, "bit_rate": bit_rate, "streams": list(streams)}


def hdr_v(*, transfer="smpte2084", **kwargs):
    stream = v("hevc", pix_fmt="yuv420p10le", transfer=transfer, **kwargs)
    stream.update(color_space="bt2020nc", color_primaries="bt2020", color_range="tv")
    return stream


MKV, MP4, AVI = "matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "avi"


def dovi_client(entries: object, *, range_: str = "SDR"):
    return caps_from_device_profile({
        "DirectPlayProfiles": [
            {"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "hevc", "AudioCodec": "aac",
             "LuminaDolbyVisionProfiles": entries},
            {"Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "aac",
             "LuminaDolbyVisionProfiles": []},
        ],
        "CodecProfiles": [{"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"},
            {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": range_},
        ]}],
    })


def dovi_source(profile: int, compatibility: int, *, level: int | None = 9, rpu: bool | None = True,
                el: bool | None = False, bl: bool | None = True):
    return v("hevc", pix_fmt="yuv420p10le", dv=profile, compat=compatibility, dv_level=level, rpu=rpu, el=el, bl=bl)


@pytest.mark.parametrize(("source", "entries"), [
    (dovi_source(5, 0), [{"Profile": 5, "CompatibilityIDs": [0], "MaxLevel": 9}]),
    (dovi_source(8, 1), [{"Profile": 8, "CompatibilityIDs": [1], "MaxLevel": 9}]),
    (dovi_source(8, 4), [{"Profile": 8, "CompatibilityIDs": [4], "MaxLevel": 9}]),
])
def test_lumina_dolby_vision_direct_play_is_exact_and_overrides_only_its_range_check(source: dict, entries: list[dict]) -> None:
    decision = decide(dovi_client(entries), facts(MP4, source, a()))

    assert (decision.mode, decision.video, decision.reasons) == ("direct", "copy", ())
    # The qualified client packaging route opts in independently on its MKV
    # row. An MP4 declaration alone must not grant this original-file route.
    mkv_row = {"Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "aac",
               "LuminaDolbyVisionProfiles": entries}
    caps = dovi_client(entries)
    mkv_caps = replace(caps, direct=caps.direct + caps_from_device_profile({"DirectPlayProfiles": [mkv_row]}).direct)
    assert decide(mkv_caps, facts(MKV, source, a())).mode == "direct"
    assert decide(caps, facts(MKV, source, a())).mode != "direct"


@pytest.mark.parametrize("pixel_format", [None, "yuv420p"])
def test_dolby_vision_direct_play_requires_original_ten_bit_pixel_format_proof(pixel_format: str | None) -> None:
    source = dovi_source(5, 0, level=6)
    source.update(pix_fmt=pixel_format, bit_depth=10)

    decision = decide(
        dovi_client([{"Profile": 5, "CompatibilityIDs": [0], "MaxLevel": 6}]),
        facts(MKV, source, a()),
    )

    assert decision.mode != "direct"


def test_dolby_vision_direct_range_exception_never_leaks_to_hls_after_audio_subtitle_or_track_failure() -> None:
    caps = dovi_client([{"Profile": 5, "CompatibilityIDs": [0], "MaxLevel": 9}])
    source = dovi_source(5, 0)

    limited_audio = decide(replace(caps, max_channels=2), facts(MP4, source, a("aac", 8)))
    burned_subtitle = decide(replace(caps, image_subtitles=False), facts(MP4, source, a(), s("hdmv_pgs_subtitle")), subtitle_index=2)
    secondary_audio = decide(
        replace(caps, selects_audio=False), facts(MP4, source, a(), a(index=2, default=False)), audio_index=2,
    )

    for decision in (limited_audio, burned_subtitle, secondary_audio):
        assert decision.mode != "direct"
        assert decision.video != "copy"


@pytest.mark.parametrize("source", [
    dovi_source(7, 1),
    dovi_source(8, 1, level=None),
    dovi_source(8, 1, level=10),
    dovi_source(8, 1, rpu=False),
    dovi_source(8, 1, el=True),
    dovi_source(8, 1, bl=False),
])
def test_lumina_dolby_vision_declaration_fails_closed_for_unqualified_source(source: dict) -> None:
    decision = decide(dovi_client([{"Profile": 8, "CompatibilityIDs": [1, 4], "MaxLevel": 9}]), facts(MP4, source, a()))

    assert decision.mode != "direct"
    assert "VideoRangeTypeNotSupported" in decision.reasons


def test_lumina_dolby_vision_is_scoped_to_the_declaring_direct_profile() -> None:
    caps = dovi_client([{"Profile": 8, "CompatibilityIDs": [1], "MaxLevel": 9}])

    mkv = decide(caps, facts(MKV, dovi_source(8, 1), a()))
    audio_mismatch = decide(caps, facts(MP4, dovi_source(8, 1), a("eac3")))

    assert mkv.mode != "direct"
    assert audio_mismatch.mode != "direct"
    assert "VideoRangeTypeNotSupported" in audio_mismatch.reasons


def test_explicit_dolby_vision_profile_seven_keeps_the_existing_base_layer_hls_fallback() -> None:
    caps = dovi_client([{"Profile": 8, "CompatibilityIDs": [1], "MaxLevel": 9}], range_="HDR10")

    decision = decide(caps, facts(MP4, dovi_source(7, 1), a()))

    assert (decision.mode, decision.video, decision.reasons) == ("remux", "copy", ("ContainerNotSupported",))


P7_TO_81 = [{
    "SourceProfile": 7,
    "SourceCompatibilityIDs": [6],
    "TargetProfile": 8,
    "TargetCompatibilityID": 1,
    "MaxLevel": 9,
    "EnhancementLayerDisposition": "dropped",
}]


def p7_packaging_client(
    conversions: object = P7_TO_81, *, container: str = "mkv", range_: str = "HDR10", separate_tracks: object = False,
):
    return caps_from_device_profile({
        "DirectPlayProfiles": [{
            "Type": "Video", "Container": container, "VideoCodec": "hevc", "AudioCodec": "aac,ac3,eac3",
            "LuminaDolbyVisionProfiles": [], "LuminaDolbyVisionPackagingConversions": conversions,
            "LuminaDolbyVisionSeparateTrackPackaging": separate_tracks,
        }],
        "CodecProfiles": [{"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"},
            {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": range_},
        ]}],
    })


def m2ts_pair_client(*, pairing: object = True, container: str = "m2ts"):
    return caps_from_device_profile({
        "DirectPlayProfiles": [{
            "Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "aac",
            "LuminaDolbyVisionProfiles": [], "LuminaDolbyVisionPackagingConversions": P7_TO_81,
        }],
        "LuminaDolbyVisionTransportProfiles": [{
            "Type": "Video", "Container": container, "VideoCodec": "hevc", "AudioCodec": "aac",
            "LuminaDolbyVisionProfiles": [],
            "LuminaDolbyVisionM2TSPairing": pairing,
            "LuminaDolbyVisionPackagingConversions": P7_TO_81,
        }],
        "CodecProfiles": [{"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"},
            {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "HDR10"},
        ]}],
    })


def m2ts_pair_facts(*, el_first: bool = False, marker: bool = True):
    base = v("hevc", pix_fmt="yuv420p10le", index=8)
    base.update(ts_pid=0x1011, program_number=1, frame_rate=23.976)
    enhancement = dovi_source(7, 6, rpu=True, el=True, bl=False)
    enhancement.update(index=2, ts_pid=0x1015, program_number=1, frame_rate=23.976)
    streams = [enhancement, a(), base] if el_first else [base, a(), enhancement]
    result = facts("mpegts", *streams)
    result["m2ts_suffix"] = True
    if marker:
        result["dv_transport_pair"] = {
            "packet_size": 192, "program_number": 1, "pmt_pid": 0x1000,
            "base_pid": 0x1011, "enhancement_pid": 0x1015,
            "base_stream_index": 8, "enhancement_stream_index": 2,
            "dv_profile": 7, "dv_level": 9, "dv_compat": 6,
        }
    return result


def test_profile_seven_m2ts_pairing_is_explicit_and_selects_the_marked_base_even_when_el_is_first() -> None:
    decision = decide(m2ts_pair_client(), m2ts_pair_facts(el_first=True))

    assert (decision.mode, decision.video, decision.audio, decision.video_index) == ("direct", "copy", "copy", 8)
    assert decision.client_packaging == "p7_m2ts_to_p81_drop_el"


def test_profile_seven_m2ts_pairing_requires_original_ten_bit_pixel_format_proof() -> None:
    source = m2ts_pair_facts()
    for stream in source["streams"]:
        if stream.get("type") == "video":
            stream.update(pix_fmt="yuv420p", bit_depth=10)

    decision = decide(m2ts_pair_client(), source)

    assert decision.mode != "direct"
    assert decision.client_packaging is None


@pytest.mark.parametrize(("client", "source"), [
    (m2ts_pair_client(pairing=False), m2ts_pair_facts()),
    (m2ts_pair_client(container="ts"), m2ts_pair_facts()),
    (m2ts_pair_client(), m2ts_pair_facts(marker=False)),
    (m2ts_pair_client(), {**m2ts_pair_facts(), "m2ts_suffix": False}),
])
def test_profile_seven_m2ts_pairing_fails_closed_without_its_exact_capability_and_marker(client, source) -> None:
    decision = decide(client, source)

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_m2ts_pairing_never_leaks_to_hls_when_audio_rejects_direct_play() -> None:
    source = m2ts_pair_facts()
    source["streams"][1] = a("aac", 8)
    decision = decide(replace(m2ts_pair_client(), max_channels=2), source)

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_mkv_packaging_is_explicit_and_reports_its_lossy_route() -> None:
    decision = decide(p7_packaging_client(), facts(MKV, dovi_source(7, 6, el=True), a()))

    assert (decision.mode, decision.video, decision.audio) == ("direct", "copy", "copy")
    assert decision.client_packaging == "p7_to_p81_drop_el"


def p7_separate_tracks(*, base: dict | None = None, enhancement: dict | None = None, extra: tuple[dict, ...] = ()) -> tuple[dict, ...]:
    base = base or dovi_source(7, 6, rpu=False, el=False, bl=True)
    enhancement = enhancement or dovi_source(7, 6, rpu=True, el=True, bl=False)
    base.update(frame_rate=23.976)
    enhancement.update(index=2, frame_rate=23.976)
    return (base, a(), enhancement, *extra)


def test_profile_seven_separate_track_packaging_is_an_explicit_mkv_direct_route() -> None:
    decision = decide(
        p7_packaging_client(separate_tracks=True),
        facts(MKV, *p7_separate_tracks()),
    )

    assert (decision.mode, decision.video, decision.audio, decision.video_index) == ("direct", "copy", "copy", 0)
    assert decision.client_packaging == "p7_to_p81_drop_el"


@pytest.mark.parametrize("separate_tracks", [False, None, 1, "true"])
def test_profile_seven_separate_track_packaging_requires_literal_client_opt_in(separate_tracks: object) -> None:
    decision = decide(
        p7_packaging_client(separate_tracks=separate_tracks),
        facts(MKV, *p7_separate_tracks()),
    )

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_separate_track_packaging_does_not_grant_older_clients_permission() -> None:
    profile = {
        "Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "aac",
        "LuminaDolbyVisionProfiles": [], "LuminaDolbyVisionPackagingConversions": P7_TO_81,
    }
    caps = caps_from_device_profile({"DirectPlayProfiles": [profile]})

    decision = decide(caps, facts(MKV, *p7_separate_tracks()))

    assert decision.mode != "direct"
    assert decision.client_packaging is None


@pytest.mark.parametrize("source", [
    p7_separate_tracks()[:-1],
    p7_separate_tracks(base=dovi_source(7, 6, rpu=True, el=False, bl=True)),
    p7_separate_tracks(enhancement=dovi_source(7, 6, rpu=False, el=True, bl=False)),
    p7_separate_tracks(enhancement=dovi_source(7, 6, level=8, rpu=True, el=True, bl=False)),
    p7_separate_tracks(enhancement=dovi_source(7, 6, level=True, rpu=True, el=True, bl=False)),
    p7_separate_tracks(enhancement=v("av1", pix_fmt="yuv420p10le", dv=7, compat=6, dv_level=9, rpu=True, el=True, bl=False, index=2)),
    p7_separate_tracks(enhancement=v("hevc", pix_fmt="yuv420p", dv=7, compat=6, dv_level=9, rpu=True, el=True, bl=False, index=2)),
    p7_separate_tracks(extra=(dovi_source(7, 6, rpu=True, el=True, bl=False),)),
])
def test_profile_seven_separate_track_packaging_fails_closed_for_malformed_or_ambiguous_carriage(source: tuple[dict, ...]) -> None:
    decision = decide(p7_packaging_client(separate_tracks=True), facts(MKV, *source))

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_separate_track_packaging_requires_matching_declared_frame_rates_when_known() -> None:
    source = list(p7_separate_tracks())
    source[2]["frame_rate"] = 24.0

    decision = decide(p7_packaging_client(separate_tracks=True), facts(MKV, *source))

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_interleaved_packaging_keeps_its_existing_route_with_an_alternate_video() -> None:
    source = dovi_source(7, 6, el=True)
    alternate = v("h264", index=2)

    decision = decide(p7_packaging_client(), facts(MKV, source, a(), alternate))

    assert decision.mode == "direct"
    assert decision.client_packaging == "p7_to_p81_drop_el"


@pytest.mark.parametrize("source", [
    dovi_source(7, 1, el=True),
    dovi_source(7, 6, level=None, el=True),
    dovi_source(7, 6, level=10, el=True),
    dovi_source(7, 6, rpu=False, el=True),
    dovi_source(7, 6, el=False),
    dovi_source(7, 6, bl=False, el=True),
    v("hevc", pix_fmt="yuv420p", dv=7, compat=6, dv_level=9, rpu=True, el=True, bl=True),
])
def test_profile_seven_packaging_fails_closed_without_all_verified_dual_layer_facts(source: dict) -> None:
    decision = decide(p7_packaging_client(), facts(MKV, source, a()))

    assert decision.mode != "direct"
    assert decision.client_packaging is None


def test_profile_seven_packaging_is_mkv_direct_only_and_does_not_authorize_hls() -> None:
    source = p7_separate_tracks()
    missing = decide(p7_packaging_client([], separate_tracks=True), facts(MKV, *source))
    mp4 = decide(p7_packaging_client(container="mp4", separate_tracks=True), facts(MP4, *source))
    webm = decide(p7_packaging_client(container="mkv", separate_tracks=True), facts("matroska,webm", *source, webm=True))

    for decision in (missing, mp4, webm):
        assert decision.mode != "direct"
        assert decision.client_packaging is None
        assert decision.video == "copy"  # Existing HDR10-base HLS/remux fallback, not converted P8.1.


def test_missing_profile_seven_packaging_declaration_keeps_legacy_direct_play_behavior() -> None:
    legacy = caps_from_device_profile({
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "aac"}],
        "CodecProfiles": [{"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"},
            {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "HDR10"},
        ]}],
    })

    decision = decide(legacy, facts(MKV, dovi_source(7, 6, el=True), a()))

    assert decision.mode == "direct"
    assert decision.client_packaging is None


@pytest.mark.parametrize("conversions", [
    [{**P7_TO_81[0], "MaxLevel": 8}],
    [{**P7_TO_81[0], "TargetCompatibilityID": True}],
    [{**P7_TO_81[0], "SourceCompatibilityIDs": [True]}],
    [{**P7_TO_81[0], "EnhancementLayerDisposition": "preserved"}],
    [{key: value for key, value in P7_TO_81[0].items() if key != "TargetProfile"}],
    [{**P7_TO_81[0], "Unknown": "field"}],
    [P7_TO_81[0], P7_TO_81[0]],
])
def test_profile_seven_packaging_declaration_is_bounded_and_strict(conversions: object) -> None:
    decision = decide(p7_packaging_client(conversions), facts(MKV, dovi_source(7, 6, el=True), a()))

    assert decision.mode != "direct"


def test_profile_seven_packaging_does_not_escape_direct_play_constraints() -> None:
    source = dovi_source(7, 6, el=True)
    caps = p7_packaging_client()
    limited_audio = decide(replace(caps, max_channels=2), facts(MKV, source, a("aac", 8)))
    burned_subtitle = decide(replace(caps, image_subtitles=False), facts(MKV, source, a(), s("hdmv_pgs_subtitle")), subtitle_index=2)
    restricted_quality = decide(caps, facts(MKV, dovi_source(7, 6, el=True), a()), Limits(max_height=720))

    for decision in (limited_audio, burned_subtitle, restricted_quality):
        assert decision.mode != "direct"
        assert decision.client_packaging is None


@pytest.mark.parametrize("entries", [[], [{"Profile": 8, "CompatibilityIDs": [2], "MaxLevel": 9}],
                                      [{"Profile": 8, "CompatibilityIDs": [1], "MaxLevel": 10}],
                                      [{"Profile": True, "CompatibilityIDs": [1], "MaxLevel": 9}],
                                      [{"Profile": 8, "CompatibilityIDs": [1, True], "MaxLevel": 9}]])
def test_lumina_dolby_vision_invalid_or_empty_declaration_denies_only_that_row(entries: object) -> None:
    decision = decide(dovi_client(entries), facts(MP4, dovi_source(8, 1), a()))

    assert decision.mode != "direct"


def test_missing_lumina_dolby_vision_declaration_preserves_legacy_base_layer_behavior() -> None:
    caps = caps_from_device_profile({
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "hevc", "AudioCodec": "aac"}],
    })

    decision = decide(caps, facts(MP4, dovi_source(8, 1), a()))

    assert decision.mode == "direct"


@pytest.mark.parametrize("tonemap", ["cuda", "opencl", "vpp_qsv", "software", "none"])
def test_dolby_vision_5_conversion_requires_a_probed_reshape_path(db_factory, monkeypatch, tonemap: str) -> None:
    from app.routers import local_playback

    source = facts(MKV, v("hevc", pix_fmt="yuv420p10le", dv=5, compat=0), a())
    monkeypatch.setattr(local_playback.hwaccel, "choice", lambda *_: ("nvenc" if tonemap == "cuda" else "qsv", tonemap))
    with db_factory() as db:
        limits = local_playback.playback_limits(db, "member", source)
    decision = decide(BROWSER_DEFAULT, source, limits)
    assert decision.mode == ("transcode" if tonemap in {"cuda", "opencl"} else "unavailable")
    if decision.mode == "transcode":
        assert decision.video == "encode" and decision.tonemap


def test_hdr_output_probe_runs_only_for_an_explicit_eligible_hdr_rendition(db_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import local_playback

    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc"),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_bit_depth=10, ranges=frozenset({"SDR", "HDR10"}))},
    )
    calls: list[tuple] = []
    monkeypatch.setattr(local_playback, "media_tool", lambda _db, name: f"/{name}")
    monkeypatch.setattr(local_playback.hwaccel, "output_codecs", lambda *_args: ("h264", "hevc"))
    monkeypatch.setattr(local_playback.hwaccel, "output_ranges", lambda *args, **kwargs: calls.append((args, kwargs)) or {"hevc": frozenset({"HDR10"})})
    source = facts(MKV, hdr_v(), a())

    with db_factory() as db:
        limits = local_playback.playback_limits(db, "member", source, caps=caps)
        source["streams"][0]["hdr10plus"] = True
        ineligible = local_playback.playback_limits(db, "member", source, caps=caps)
        sdr = local_playback.playback_limits(db, "member", facts(MKV, v("mpeg4"), a()), caps=caps)

    assert limits.encode_ranges == {"hevc": frozenset({"HDR10"})}
    assert calls == [(("/ffmpeg", "auto", "/ffprobe"), {"video_range": "HDR10"})]
    assert ineligible.encode_ranges == sdr.encode_ranges == {}


# An Apple TV client's profile, shaped like the ones Infuse/Swiftfin post. The 10.11 regression row: conditions
# Lumina does not model (VideoLevel, IsAnamorphic, RefFrames, an unknown future property) must be ignored.
INFUSE = caps_from_device_profile({
    "MaxStreamingBitrate": 120_000_000,
    "DirectPlayProfiles": [
        {"Type": "Video", "Container": "mkv,mp4,mov,m4v,ts", "VideoCodec": "h264,hevc,av1,mpeg4", "AudioCodec": "aac,ac3,eac3,truehd,dts,flac,mp3,opus"},
        {"Type": "Audio", "Container": "mp3,aac,flac,m4a"},
    ],
    "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3"}],
    "CodecProfiles": [
        {"Type": "Video", "Codec": "h264", "Conditions": [
            {"Condition": "LessThanEqual", "Property": "VideoLevel", "Value": "52"},
            {"Condition": "NotEquals", "Property": "IsAnamorphic", "Value": "true"},
            {"Condition": "LessThanEqual", "Property": "RefFrames", "Value": "16"},
            {"Condition": "LessThanEqual", "Property": "SomeFutureProperty", "Value": "1"},
            {"Condition": "Frobnicates", "Property": "Width", "Value": "10"},
        ]},
        {"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Condition": "LessThanEqual", "Property": "VideoBitDepth", "Value": "10"},
            {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "SDR|HDR10|HLG|DOVI|DOVIWithHDR10"},
        ]},
        {"Type": "VideoAudio", "Codec": "eac3", "Conditions": [{"Condition": "LessThanEqual", "Property": "AudioChannels", "Value": "6"}]},
    ],
    "SubtitleProfiles": [{"Format": "srt", "Method": "External"}, {"Format": "pgssub", "Method": "Embed"}],
})
TVOS_NO_TRUEHD = caps_from_device_profile({
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mkv,mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3"}],
    "CodecProfiles": [{"Type": "VideoAudio", "Conditions": [{"Condition": "LessThanEqual", "Property": "AudioChannels", "Value": "6"}]}],
    "SubtitleProfiles": [{"Format": "srt", "Method": "External"}],
})
AVCHD = caps_from_device_profile({
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mts", "VideoCodec": "h264", "AudioCodec": "aac"}],
})
# Final review #2/#5: an h264 height cap limits only what Lumina encodes, never a copied hevc stream.
H264_720 = caps_from_device_profile({
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac"}],
    "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac"}],
    "CodecProfiles": [
        {"Type": "Video", "Codec": "h264", "Conditions": [{"Condition": "LessThanEqual", "Property": "Height", "Value": "720"}]},
        {"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Condition": "LessThanEqual", "Property": "VideoBitDepth", "Value": "10"},
            {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "SDR|HDR10"},
        ]},
    ],
})
# Legacy native AVKit profile, retained for clients predating capability-specific negotiation.
NATIVE_AVKIT = caps_from_device_profile({
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,mp3,alac,flac"},
                           {"Type": "Video", "Container": "mkv", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,alac,flac"}],
    "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    "CodecProfiles": [
        {"Type": "Video", "Codec": codec, "Conditions": [{"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": depth}]}
        for codec, depth in (("h264", "8"), ("hevc", "10"))
    ] + [{"Type": "VideoAudio", "Codec": "flac", "Conditions": [{"Property": "AudioBitDepth", "Condition": "LessThanEqual", "Value": "24"}]}],
    "SubtitleProfiles": [{"Format": "vtt", "Method": "External"}],
})
HEVC8 = caps_from_browser_profiles(["mp4-avc-aac", "mp4-hevc-aac"])
HEVC10 = caps_from_browser_profiles(["mp4-avc-aac", "mp4-hevc10-aac"])
HEVC10_HDR = caps_from_browser_profiles(["mp4-hevc10-aac", "hdr"])
HDR10_4K = v("hevc", 2160, 3840, "yuv420p10le", "smpte2084")

ROWS = [
    # id, caps, facts, limits, audio_index, subtitle_index -> (mode, video, audio, burn, height, bitrate, reasons)
    ("native_flac32_audio_only_encode", NATIVE_AVKIT, facts(MP4, v(), a("flac", bit_depth=32)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioBitDepthNotSupported",))),
    ("native_flac_unknown_depth_audio_only_encode", NATIVE_AVKIT, facts(MKV, v(), a("flac", bit_depth=None)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioBitDepthNotSupported",))),
    ("native_mp4_flac_direct", NATIVE_AVKIT, facts(MP4, v(), a("flac", 8, bit_depth=24)), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("native_mkv_mp3_keeps_server_remux", NATIVE_AVKIT, facts(MKV, v(), a("mp3")), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("native_mkv_flac_direct", NATIVE_AVKIT, facts(MKV, HDR10_4K, a("flac", 6)), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("native_flac_channel_limit", NATIVE_AVKIT, facts(MP4, v(), a("flac", 9)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioChannelsNotSupported",))),
    ("native_mp4_mp3_direct", NATIVE_AVKIT, facts(MP4, v(), a("mp3")), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("native_mov_alac_direct", NATIVE_AVKIT, facts("mov", v(), a("alac")), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("native_mkv_hevc10_direct", NATIVE_AVKIT, facts(MKV, HDR10_4K, a()), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("native_mkv_truehd_only_audio_encodes", NATIVE_AVKIT, facts(MKV, HDR10_4K, a("truehd", 8)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioCodecNotSupported",))),
    ("native_h264_high10_encodes", NATIVE_AVKIT, facts(MP4, v(pix_fmt="yuv420p10le"), a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("native_explicit_high10_without_pixfmt_encodes", NATIVE_AVKIT, facts(MP4, {**v(pix_fmt=None), "bit_depth": 10}, a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("native_explicit_depth_cannot_lower_pixfmt", NATIVE_AVKIT, facts(MP4, {**v(pix_fmt="yuv420p10le"), "bit_depth": 8}, a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("native_explicit_hevc12_encodes", NATIVE_AVKIT, facts(MP4, {**v("hevc", pix_fmt=None), "bit_depth": 12}, a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("native_hevc12_encodes", NATIVE_AVKIT, facts(MP4, v("hevc", pix_fmt="yuv420p12le"), a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("infuse_mkv_h264_aac_direct", INFUSE, facts(MKV, v(), a()), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("infuse_hevc10_hdr10_truehd_direct", INFUSE, facts(MKV, HDR10_4K, a("truehd", 8)), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("avchd_mts_direct", AVCHD, facts("mpegts", v(), a()), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("truehd_is_an_audio_only_encode", TVOS_NO_TRUEHD, facts(MKV, v(), a("truehd", 8)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioCodecNotSupported",))),
    ("srt_selected_stays_direct", INFUSE, facts(MKV, v(), a(), s()), Limits(), None, 2, ("direct", "copy", "copy", None, None, None, ())),
    ("pgs_without_embed_support_burns_in", TVOS_NO_TRUEHD, facts(MKV, v(), a(), s("hdmv_pgs_subtitle")), Limits(), None, 2, ("transcode", "encode", "copy", 2, 1080, 8_000_000, ("SubtitleCodecNotSupported",))),
    ("pgs_with_embed_support_stays_direct", INFUSE, facts(MKV, v(), a(), s("hdmv_pgs_subtitle")), Limits(), None, 2, ("direct", "copy", "copy", None, None, None, ())),
    ("browser_mp4_direct", BROWSER_DEFAULT, facts(MP4, v(), a()), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("browser_mkv_h264_remux", BROWSER_DEFAULT, facts(MKV, v(), a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("browser_hevc_encodes", BROWSER_DEFAULT, facts(MKV, v("hevc"), a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoCodecNotSupported",))),
    ("browser_with_hevc_remuxes", HEVC8, facts(MKV, v("hevc"), a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("hevc_10bit_needs_hevc10", HEVC8, facts(MKV, v("hevc", pix_fmt="yuv420p10le"), a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoBitDepthNotSupported",))),
    ("hevc10_remuxes", HEVC10, facts(MKV, v("hevc", pix_fmt="yuv420p10le"), a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("member_ceiling", BROWSER_DEFAULT, facts(MP4, v(height=2160, width=3840), a()), Limits(max_height=720), None, None, ("transcode", "encode", "copy", None, 720, 4_000_000, ("VideoResolutionNotSupported",))),
    ("bitrate_cap_4m", INFUSE, facts(MKV, v(), a(), bit_rate=20_000_000), Limits(max_bitrate=4_000_000), None, None, ("transcode", "encode", "copy", None, 720, 4_000_000, ("ContainerBitrateExceedsLimit",))),
    ("bitrate_cap_1m", INFUSE, facts(MKV, v(), a(), bit_rate=20_000_000), Limits(max_bitrate=1_000_000), None, None, ("transcode", "encode", "copy", None, 480, 1_000_000, ("ContainerBitrateExceedsLimit",))),
    ("hdr_unsupported_tone_maps", HEVC10, facts(MKV, HDR10_4K, a()), Limits(), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoRangeTypeNotSupported",))),
    ("hdr_supported_remuxes", HEVC10_HDR, facts(MKV, HDR10_4K, a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("dv5_without_opencl_unavailable", BROWSER_DEFAULT, facts(MKV, v("hevc", 2160, 3840, "yuv420p10le", None, dv=5, compat=0), a()), Limits(), None, None, ("unavailable", None, None, None, None, None, ("dolby_vision_p5",))),
    ("dv5_with_opencl_tone_maps", BROWSER_DEFAULT, facts(MKV, v("hevc", 2160, 3840, "yuv420p10le", None, dv=5, compat=0), a()), Limits(dv5_reshape=True), None, None, ("transcode", "encode", "copy", None, 1080, 8_000_000, ("VideoCodecNotSupported",))),
    ("dv81_plays_its_hdr10_base_layer", HEVC10_HDR, facts(MKV, v("hevc", 2160, 3840, "yuv420p10le", "smpte2084", dv=8, compat=1), a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("eac3_71_to_51_client", INFUSE, facts(MKV, v(), a("eac3", 8)), Limits(), None, None, ("transcode", "copy", "encode", None, None, None, ("AudioChannelsNotSupported",))),
    ("browser_second_audio_track_remuxes", BROWSER_DEFAULT, facts(MP4, v(), a(), a(index=2, default=False)), Limits(), 2, None, ("remux", "copy", "copy", None, None, None, ("SecondaryAudioNotSupported",))),
    ("audio_index_names_a_video_stream", BROWSER_DEFAULT, facts(MP4, v(), a()), Limits(), 0, None, ("direct", "copy", "copy", None, None, None, ())),
    ("audio_index_missing", BROWSER_DEFAULT, facts(MP4, v(), a()), Limits(), 9, None, ("direct", "copy", "copy", None, None, None, ())),
    ("text_subtitle_selected_is_never_burned", BROWSER_DEFAULT, facts(MKV, v(), a(), s("ass")), Limits(), None, 2, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("subtitle_index_names_audio", BROWSER_DEFAULT, facts(MP4, v(), a()), Limits(), None, 1, ("direct", "copy", "copy", None, None, None, ())),
    ("no_streams", BROWSER_DEFAULT, facts(MKV), Limits(), None, None, ("unavailable", None, None, None, None, None, ("no_playable_streams",))),
    ("mp3_audio_only_direct", BROWSER_DEFAULT, facts("mp3", a("mp3", index=0)), Limits(), None, None, ("direct", None, "copy", None, None, None, ())),
    ("ac3_audio_only_encodes", BROWSER_DEFAULT, facts("ac3", a("ac3", 6, index=0)), Limits(), None, None, ("transcode", None, "encode", None, None, None, ("AudioCodecNotSupported",))),
    ("webm_suffix_direct", BROWSER_DEFAULT, facts(MKV, v("vp9"), a("opus"), webm=True), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("vp9_in_mkv_remuxes", BROWSER_DEFAULT, facts(MKV, v("vp9"), a("opus")), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("h264_height_cap_clamps_the_encode", H264_720, facts(MKV, v(), a()), Limits(), None, None, ("transcode", "encode", "copy", None, 720, 4_000_000, ("VideoResolutionNotSupported",))),
    ("h264_height_cap_never_touches_copied_hevc", H264_720, facts(MKV, HDR10_4K, a()), Limits(), None, None, ("remux", "copy", "copy", None, None, None, ("ContainerNotSupported",))),
    ("mpeg4_avi_encodes_both", BROWSER_DEFAULT, facts(AVI, v("mpeg4", 120, 160), a("ac3")), Limits(), None, None, ("transcode", "encode", "encode", None, 120, 1_500_000, ("VideoCodecNotSupported", "AudioCodecNotSupported"))),
]


@pytest.mark.parametrize(("caps", "probe", "limits", "audio_index", "subtitle_index", "expected"), [row[1:] for row in ROWS], ids=[row[0] for row in ROWS])
def test_decision_table(caps, probe, limits, audio_index, subtitle_index, expected) -> None:
    d = decide(caps, probe, limits, audio_index, subtitle_index)
    assert (d.mode, d.video, d.audio, d.burn_subtitle, d.height, d.bitrate, d.reasons) == expected


def test_encoded_audio_channels_and_kind() -> None:
    to_51 = decide(INFUSE, facts(MKV, v(), a("eac3", 8)))
    assert (to_51.audio_channels, to_51.kind) == (6, "audio")
    stereo = decide(BROWSER_DEFAULT, facts(AVI, v("mpeg4"), a("ac3", 6)))
    assert (stereo.audio_channels, stereo.kind) == (2, "video")
    assert decide(BROWSER_DEFAULT, facts(MKV, v(), a())).kind == "remux"
    assert decide(TVOS_NO_TRUEHD, facts(MKV, v(), a("truehd", 8)), audio_index=None).audio_index == 1


def test_tone_mapping_is_flagged_only_for_hdr_encodes() -> None:
    assert decide(HEVC10, facts(MKV, HDR10_4K, a())).tonemap is True
    assert decide(BROWSER_DEFAULT, facts(MKV, v("hevc"), a())).tonemap is False
    hlg = v("hevc", 1080, 1920, "yuv420p10le", "arib-std-b67")
    assert decide(BROWSER_DEFAULT, facts(MKV, hlg, a())).tonemap is True


@pytest.mark.parametrize("profile", [
    None, [], "junk", {"DirectPlayProfiles": "mkv"}, {"DirectPlayProfiles": ["mkv", None, 3]},
    {"DirectPlayProfiles": [{"Type": "Video"}], "CodecProfiles": [{"Type": "Video", "Conditions": [{"Property": "Width", "Condition": "LessThanEqual", "Value": "wide"}]}]},
    {"CodecProfiles": [{"Type": "Video", "ApplyConditions": [{"Property": "Width"}], "Conditions": [{"Property": "Height", "Condition": "LessThanEqual", "Value": "1"}]}]},
    {"CodecProfiles": [{"Type": "Video", "Conditions": "all"}], "SubtitleProfiles": [None, {"Format": 7}], "MaxStreamingBitrate": "fast"},
])
def test_malformed_device_profiles_never_raise(profile) -> None:
    caps = caps_from_device_profile(profile)
    decide(caps, facts(MKV, v(), a(), s("hdmv_pgs_subtitle")), Limits(), 1, 2)


def test_an_empty_video_codec_list_means_any_codec() -> None:
    anything = caps_from_device_profile({"DirectPlayProfiles": [{"Type": "Video", "Container": "mkv"}]})
    assert decide(anything, facts(MKV, v("vc1"), a("dts", 6))).mode == "direct"


@pytest.mark.parametrize("depth_rules", [(24, 16), (16, 24)])
def test_audio_depth_limits_combine_and_keep_supported_video(depth_rules) -> None:
    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mkv", "VideoCodec": "h264", "AudioCodec": "flac"}],
        "CodecProfiles": [
            {"Type": "VideoAudio", **({"Codec": "flac"} if index else {}), "Conditions": [
                {"Property": "AudioBitDepth", "Condition": "LessThanEqual", "Value": str(depth)},
            ]} for index, depth in enumerate(depth_rules)
        ],
    }
    caps = caps_from_device_profile(profile)
    assert decide(caps, facts(MKV, v(), a("flac", bit_depth=16))).mode == "direct"
    decision = decide(caps, facts(MKV, v(), a("flac", bit_depth=24)))
    assert (decision.video, decision.audio, decision.reasons) == ("copy", "encode", ("AudioBitDepthNotSupported",))


def test_conditional_codec_profiles_are_skipped_not_applied() -> None:
    caps = caps_from_device_profile({
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mkv", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "CodecProfiles": [{"Type": "Video", "Codec": "h264", "ApplyConditions": [{"Condition": "Equals", "Property": "IsInterlaced", "Value": "true"}],
                           "Conditions": [{"Condition": "LessThanEqual", "Property": "Height", "Value": "480"}]}],
    })
    assert decide(caps, facts(MKV, v(), a())).mode == "direct"


def test_browser_profiles_only_add_to_the_default() -> None:
    assert caps_from_browser_profiles([]) is BROWSER_DEFAULT
    assert caps_from_browser_profiles(["unknown-profile"]) is BROWSER_DEFAULT
    with_ac3 = caps_from_browser_profiles(["mp4-ac3"])
    assert decide(with_ac3, facts(MP4, v(), a("ac3", 6))).mode == "direct"
    assert decide(BROWSER_DEFAULT, facts(MP4, v(), a("ac3", 6))).mode == "transcode"
    hdr_hls = caps_from_browser_profiles(["mp4-hevc10-aac", "hls-hevc-aac", "hls-hevc-hdr10-aac"])
    assert hdr_hls.transcode_ranges == {"hevc": frozenset({"HDR10"})}
    assert "HDR10" in hdr_hls.video["hevc"].ranges


def test_modern_output_requires_explicit_hls_profile_and_a_proven_encoder() -> None:
    source = facts(MKV, v("mpeg4", height=2160, width=3840), a())
    explicit = replace(BROWSER_DEFAULT, transcode_video=("h264", "hevc", "av1"))

    av1 = decide(explicit, source, Limits(encode_codecs=("h264", "hevc", "av1")))
    assert (av1.video_codec, av1.height, av1.bitrate) == ("av1", 2160, 20_000_000)

    hevc = decide(explicit, source, Limits(encode_codecs=("h264", "hevc")))
    assert (hevc.video_codec, hevc.height) == ("hevc", 2160)

    baseline = decide(BROWSER_DEFAULT, source, Limits(encode_codecs=("h264", "hevc", "av1")))
    assert (baseline.video_codec, baseline.height) == ("h264", 1080)


@pytest.mark.parametrize("transfer, expected_range", [("smpte2084", "HDR10"), ("arib-std-b67", "HLG")])
def test_explicit_client_and_proven_server_hdr_output_preserves_the_exact_range(transfer: str, expected_range: str) -> None:
    source = facts(MKV, hdr_v(transfer=transfer, height=2160, width=3840), a())
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc"),
        transcode_ranges={"hevc": frozenset({expected_range})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"SDR", expected_range}))},
    )

    decision = decide(caps, source, Limits(encode_codecs=("h264", "hevc"), encode_ranges={"hevc": frozenset({expected_range})}))

    assert (decision.video, decision.video_codec, decision.video_range, decision.tonemap) == ("encode", "hevc", expected_range, False)


def test_hdr_prefers_a_proven_preserving_codec_over_a_higher_ranked_sdr_codec() -> None:
    source = facts(MKV, hdr_v(height=2160, width=3840), a())
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc", "av1"),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={
            **BROWSER_DEFAULT.video,
            "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"SDR", "HDR10"})),
            "av1": VideoCap(max_bit_depth=10, ranges=frozenset({"SDR"})),
        },
    )

    decision = decide(caps, source, Limits(encode_codecs=("h264", "hevc", "av1"), encode_ranges={"hevc": frozenset({"HDR10"})}))

    assert (decision.video_codec, decision.video_range, decision.tonemap) == ("hevc", "HDR10", False)


def test_burned_image_subtitles_force_hdr_to_sdr_or_refuse_when_sdr_is_not_allowed() -> None:
    source = facts(MKV, hdr_v(height=2160, width=3840), a(), s("hdmv_pgs_subtitle"))
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc"),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"SDR", "HDR10"}))},
    )
    limits = Limits(encode_codecs=("h264", "hevc"), encode_ranges={"hevc": frozenset({"HDR10"})})

    sdr = decide(caps, source, limits, subtitle_index=2)
    hdr_only = decide(replace(caps, transcode_video=("hevc",), video={**caps.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"HDR10"}))}), source, limits, subtitle_index=2)

    assert (sdr.burn_subtitle, sdr.video_range, sdr.tonemap) == (2, "SDR", True)
    assert hdr_only.mode == "unavailable"


def test_hdr_client_that_rejects_sdr_is_not_silently_downgraded() -> None:
    source = facts(MKV, hdr_v(height=2160, width=3840), a())
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("hevc",),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"HDR10"}))},
    )

    decision = decide(caps, source, Limits(encode_codecs=("hevc",)))

    assert decision.mode == "unavailable"


@pytest.mark.parametrize("extra", [{"hdr10plus": True}, {"dv_profile": 0}])
def test_dolby_vision_and_hdr10plus_never_become_hdr_output(extra: dict) -> None:
    source_video = hdr_v(height=2160, width=3840)
    source_video.update(extra)
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc"),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"SDR", "HDR10"}))},
    )

    decision = decide(caps, facts(MKV, source_video, a()), Limits(encode_codecs=("h264", "hevc"), encode_ranges={"hevc": frozenset({"HDR10"})}))

    assert (decision.video_range, decision.tonemap) == ("SDR", True)


def test_hdr_preservation_requires_complete_cicp_and_ten_bit_input() -> None:
    source = hdr_v(height=2160, width=3840)
    source["color_range"] = "pc"
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc"),
        transcode_ranges={"hevc": frozenset({"HDR10"})},
        video={**BROWSER_DEFAULT.video, "hevc": VideoCap(max_height=1080, max_bit_depth=10, ranges=frozenset({"SDR", "HDR10"}))},
    )

    decision = decide(caps, facts(MKV, source, a()), Limits(encode_codecs=("h264", "hevc"), encode_ranges={"hevc": frozenset({"HDR10"})}))

    assert (decision.video_range, decision.tonemap) == ("SDR", True)
    assert bit_depth({"pix_fmt": "p010le"}) == 10
    assert bit_depth({"pix_fmt": "p012"}) == 12


def test_device_profiles_scope_positive_hdr_ranges_to_explicit_fmp4_hls_output() -> None:
    profile = {
        "TranscodingProfiles": [
            {"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "hevc", "AudioCodec": "aac"},
            {"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "av1", "AudioCodec": "aac"},
        ],
        "CodecProfiles": [
            {"Type": "Video", "Codec": "hevc", "Conditions": [
                {"Condition": "LessThanEqual", "Property": "VideoBitDepth", "Value": "10"},
                {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "SDR|HDR10|HLG"},
            ]},
            {"Type": "Video", "Codec": "av1", "Conditions": [
                {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "HDR10|HLG"},
            ], "ApplyConditions": [{"Condition": "Equals", "Property": "IsInterlaced", "Value": "true"}]},
        ],
    }

    caps = caps_from_device_profile(profile)

    assert caps.transcode_video == ("hevc", "av1")
    assert caps.transcode_ranges == {"hevc": frozenset({"HDR10", "HLG"})}


def test_negotiated_output_obeys_the_selected_codec_decoder_limits() -> None:
    source = facts(MKV, v("mpeg4", height=2160, width=3840), a())
    caps = replace(
        BROWSER_DEFAULT,
        transcode_video=("h264", "hevc", "av1"),
        video={
            **BROWSER_DEFAULT.video,
            "av1": VideoCap(max_bit_depth=10, ranges=frozenset({"HDR10"})),
            "hevc": VideoCap(max_width=1920, max_bit_depth=8, ranges=frozenset({"SDR"})),
        },
    )

    decision = decide(caps, source, Limits(encode_codecs=("h264", "hevc", "av1")))

    assert (decision.video_codec, decision.height) == ("hevc", 1080)


@pytest.mark.parametrize("codec", ["hevc", "av1"])
def test_modern_hls_output_reencodes_non_aac_audio(codec: str) -> None:
    modern = replace(BROWSER_DEFAULT, transcode_video=("h264", codec))
    decision = decide(modern, facts(MKV, v("mpeg4"), a("opus", 6)), Limits(encode_codecs=("h264", codec)))

    assert (decision.video, decision.video_codec, decision.audio, decision.audio_channels) == ("encode", codec, "encode", 2)


@pytest.mark.parametrize("av1_hls, hls_audio", [(False, "aac"), (True, "aac"), (True, "eac3")])
@pytest.mark.parametrize("container, audio_codec", [(MP4, "aac"), (MKV, "aac"), (MP4, "dts")])
def test_native_av1_direct_support_does_not_authorize_hls_stream_copy(av1_hls: bool, hls_audio: str, container: str, audio_codec: str) -> None:
    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,mov,m4v", "VideoCodec": "h264,hevc,av1", "AudioCodec": "aac"}],
        "TranscodingProfiles": [
            {"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac"},
            {"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "av1" if av1_hls else "h264", "AudioCodec": hls_audio},
        ],
        "CodecProfiles": [
            {"Type": "Video", "Codec": codec, "Conditions": [
                {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"},
                {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR|HDR10|HLG"},
            ]} for codec in ("hevc", "av1")
        ],
    }
    source_video = {**v("av1", 2160, 3840, "yuv420p10le", "smpte2084"),
                    "color_primaries": "bt2020", "color_space": "bt2020nc", "color_range": "tv"}
    caps = caps_from_device_profile(profile)
    decision = decide(caps, facts(container, source_video, a(audio_codec)),
                      Limits(encode_codecs=("h264", "hevc", "av1"), encode_ranges={"hevc": frozenset({"HDR10"})}))

    if container == MP4 and audio_codec == "aac":
        assert decision.mode == "direct" and decision.video == "copy"
    elif av1_hls and hls_audio == "aac":
        assert decision.video == "copy" and decision.audio == ("copy" if audio_codec == "aac" else "encode")
    else:
        assert (decision.video, decision.video_codec, decision.video_range, decision.tonemap) == ("encode", "hevc", "HDR10", False)
        assert "VideoCodecNotSupported" in decision.reasons


@pytest.mark.parametrize("source_codec", ["hevc", "av1"])
@pytest.mark.parametrize("audio_codec", ["aac", "flac", "dts"])
def test_native_compatibility_request_never_reuses_a_modern_hdr_rendition(source_codec: str, audio_codec: str) -> None:
    caps = caps_from_device_profile({
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,mkv", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "CodecProfiles": [
            {"Type": "Video", "Codec": "h264", "Conditions": [
                {"Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "8"},
                {"Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR"},
            ]},
            {"Type": "VideoAudio", "Codec": "aac", "Conditions": [
                {"Property": "AudioChannels", "Condition": "LessThanEqual", "Value": "2"},
            ]},
        ],
    })
    source_video = {**v(source_codec, 2160, 3840, "yuv420p10le", "smpte2084"),
                    "color_primaries": "bt2020", "color_space": "bt2020nc", "color_range": "tv"}
    decision = decide(caps, facts(MP4, source_video, a(audio_codec, 2 if audio_codec == "aac" else 8)), Limits(
        encode_codecs=("h264", "hevc", "av1"),
        encode_ranges={"hevc": frozenset({"HDR10"}), "av1": frozenset({"HDR10"})},
    ))

    assert (decision.video, decision.video_codec, decision.video_range, decision.tonemap) == ("encode", "h264", "SDR", True)
    assert (decision.audio, decision.audio_channels) == (("copy", None) if audio_codec == "aac" else ("encode", 2))


def test_device_profiles_authorize_modern_output_only_for_fmp4_aac_hls() -> None:
    profile = {
        "TranscodingProfiles": [
            {"Type": "Video", "Protocol": "hls", "Container": "ts", "VideoCodec": "h264,hevc,av1", "AudioCodec": "aac"},
            {"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264,hevc,av1", "AudioCodec": "aac"},
        ],
    }
    assert caps_from_device_profile(profile).transcode_video == ("h264", "hevc", "av1")
    ts_only = {"TranscodingProfiles": [profile["TranscodingProfiles"][0]]}
    assert caps_from_device_profile(ts_only).transcode_video == ("h264",)
    explicit_hevc = {"TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "hevc", "AudioCodec": "aac"}]}
    assert caps_from_device_profile(explicit_hevc).transcode_video == ("hevc",)
