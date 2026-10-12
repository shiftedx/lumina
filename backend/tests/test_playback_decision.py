"""One pure, table-tested playback decision for browsers and Jellyfin clients."""
from __future__ import annotations

import pytest

from app.services.playback_decision import (
    BROWSER_DEFAULT, Limits, caps_from_browser_profiles, caps_from_device_profile, decide,
)


def v(codec="h264", height=1080, width=1920, pix_fmt="yuv420p", transfer=None, dv=None, compat=None, index=0):
    return {"index": index, "type": "video", "codec": codec, "height": height, "width": width, "pix_fmt": pix_fmt,
            "color_transfer": transfer, "dv_profile": dv, "dv_compat": compat, "default": True}


def a(codec="aac", channels=2, index=1, default=True):
    return {"index": index, "type": "audio", "codec": codec, "channels": channels, "default": default}


def s(codec="subrip", index=2):
    return {"index": index, "type": "subtitle", "codec": codec}


def facts(container, *streams, bit_rate=5_000_000, webm=False):
    return {"container": container, "webm_suffix": webm, "bit_rate": bit_rate, "streams": list(streams)}


MKV, MP4, AVI = "matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "avi"
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
HEVC8 = caps_from_browser_profiles(["mp4-avc-aac", "mp4-hevc-aac"])
HEVC10 = caps_from_browser_profiles(["mp4-avc-aac", "mp4-hevc10-aac"])
HEVC10_HDR = caps_from_browser_profiles(["mp4-hevc10-aac", "hdr"])
HDR10_4K = v("hevc", 2160, 3840, "yuv420p10le", "smpte2084")

ROWS = [
    # id, caps, facts, limits, audio_index, subtitle_index -> (mode, video, audio, burn, height, bitrate, reasons)
    ("infuse_mkv_h264_aac_direct", INFUSE, facts(MKV, v(), a()), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
    ("infuse_hevc10_hdr10_truehd_direct", INFUSE, facts(MKV, HDR10_4K, a("truehd", 8)), Limits(), None, None, ("direct", "copy", "copy", None, None, None, ())),
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
