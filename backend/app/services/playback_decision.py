"""Pure playback decision. No I/O.

Given probe facts (media_probe.normalize), what a client says it plays, and member/bitrate
limits, pick the least work: direct > remux > audio-only encode > video encode. Video is
re-encoded only for an unsupported codec, bit depth or HDR range, a size or bitrate over the
limit, or an image subtitle the client cannot render. Text subtitles are always sidecars.
Reasons use Jellyfin's TranscodeReasons names so PlaybackInfo can emit them as they are.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

ANY = frozenset({"*"})
ALL_RANGES = frozenset({"SDR", "HDR10", "HLG", "HDR10Plus", "DOVI", "DOVIWithHDR10", "DOVIWithHLG", "DOVIWithSDR", "DOVIWithEL", "DOVIWithHDR10Plus", "DOVIWithELHDR10Plus"})
# A Dolby Vision stream whose base layer is plain HDR10/HLG/SDR plays as that base layer.
BASE_LAYER = {"DOVIWithHDR10": "HDR10", "DOVIWithEL": "HDR10", "DOVIWithHDR10Plus": "HDR10", "DOVIWithELHDR10Plus": "HDR10", "DOVIWithHLG": "HLG", "DOVIWithSDR": "SDR"}
IMAGE_SUBTITLE_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"})
HLS_VIDEO_COPY = frozenset({"h264", "hevc", "av1", "vp9"})  # carried as-is in fMP4 HLS
HLS_AUDIO_COPY = frozenset({"aac", "mp3", "ac3", "eac3", "flac", "opus"})
RUNGS = {1080: 8_000_000, 720: 4_000_000, 480: 1_500_000}  # H.264 output height -> -maxrate (bps)
MODERN_RUNGS = {2160: 20_000_000, **RUNGS}  # HEVC/AV1; only explicitly negotiated HLS clients see 4K
MAX_ENCODE_HEIGHT = 1080  # H.264 output tops out here; 4K sources play direct or at 1080
# ffprobe format_name tokens -> the container names clients advertise.
CONTAINER_ALIASES = {
    "matroska": {"mkv", "matroska"}, "mov": {"mov", "mp4", "m4v", "m4a"}, "mp4": {"mp4", "m4v", "mov", "m4a"},
    "mpegts": {"ts", "mpegts", "m2ts", "mts"}, "asf": {"asf", "wmv", "wma"}, "mpeg": {"mpeg", "mpg"}, "ogg": {"ogg", "oga"},
}
IMAGE_SUBTITLE_FORMATS = frozenset({"pgssub", "pgs", "dvdsub", "dvbsub", "vobsub", "sub"})


@dataclass(frozen=True)
class VideoCap:
    max_width: int | None = None
    max_height: int | None = None
    max_bit_depth: int = 8
    ranges: frozenset[str] = frozenset({"SDR"})


@dataclass(frozen=True)
class DirectProfile:
    containers: frozenset[str]
    video: frozenset[str]  # ANY = any codec; empty = audio-only profile
    audio: frozenset[str]
    # None preserves the pre-extension behavior for a profile posted by an
    # older client. An empty tuple is an explicit denial for this exact row.
    dolby_vision: tuple["DolbyVisionCap", ...] | None = None
    # A client-side Matroska route which rewrites Profile 7 RPUs as 8.1 and
    # deliberately drops the enhancement layer. This is distinct from the
    # original-stream Dolby Vision declaration above.
    profile7_to_81_max_level: int | None = None
    # A separate-track Profile 7 carriage needs an additional client/engine
    # qualification; ordinary P7 conversion capability does not imply it.
    profile7_separate_track_packaging: bool = False
    # MPEG-TS has a distinct, explicitly validated two-PID carriage.  It is
    # declared outside Jellyfin's normal DirectPlayProfiles so older servers do
    # not mistake a generic HEVC/M2TS row for this lossy conversion route.
    profile7_m2ts_pair_packaging: bool = False


@dataclass(frozen=True)
class DolbyVisionCap:
    profile: int
    compatibility_ids: frozenset[int]
    max_level: int


@dataclass(frozen=True)
class ClientCaps:
    direct: tuple[DirectProfile, ...]
    video: Mapping[str, VideoCap]  # codecs it decodes (direct or in HLS); "*" = any
    audio: frozenset[str]
    max_channels: int = 8  # for codecs without their own limit
    encode_channels: int = 2  # AAC channels when audio must be encoded
    image_subtitles: bool = False  # renders PGS/DVD subtitles itself
    max_bitrate: int | None = None
    selects_audio: bool = False  # can pick a non-default audio track of a direct-played file
    channel_limits: Mapping[str, int] = field(default_factory=dict)  # per-codec AudioChannels limits
    audio_bit_depth_limits: Mapping[str, int] = field(default_factory=dict)  # codec or "*"; missing probe depth fails a limit
    transcode_video: tuple[str, ...] = ("h264",)  # explicit fMP4 HLS encoder codecs, never inferred from direct play
    # Explicit HDR HLS output support. SDR remains governed by transcode_video.
    transcode_ranges: Mapping[str, frozenset[str]] = field(default_factory=dict)
    hls_video_copy: frozenset[str] = HLS_VIDEO_COPY  # Direct AV1 support need not include AV1 fMP4 HLS.
    # A declared fMP4 HLS row may be narrower than direct-play audio support.
    hls_audio_copy: frozenset[str] = HLS_AUDIO_COPY


@dataclass(frozen=True)
class Limits:
    max_height: int | None = None  # member ceiling and/or the chosen quality
    max_bitrate: int | None = None  # Jellyfin MaxStreamingBitrate
    dv5_reshape: bool = False  # OpenCL/CUDA tone-mapping can reshape Dolby Vision profile 5
    require_known_bitrate: bool = False  # a protected remote direct play must not assume an unknown source fits
    force_audio_encode: bool = False  # a constrained HLS rendition reserves a known AAC budget
    encode_codecs: tuple[str, ...] = ("h264",)  # codecs proven by this server's current probe
    # HDR output ranges proven by the selected encoder's real pipeline probe.
    encode_ranges: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    mode: str  # direct | remux | transcode | unavailable
    video: str | None = None  # copy | encode | None (no video)
    audio: str | None = None
    video_index: int | None = None
    audio_index: int | None = None
    burn_subtitle: int | None = None  # probe index of an image subtitle to overlay
    height: int | None = None  # output height when video is encoded
    bitrate: int | None = None  # -maxrate when video is encoded
    audio_channels: int | None = None  # when audio is encoded
    tonemap: bool = False
    video_codec: str = "h264"  # encoded output; copied video retains its probed source codec
    video_range: str = "SDR"  # encoded output range; copied video retains its probed source range
    # Requested local packaging, not an assertion that its packet conversion
    # completed. Prism's runtime conversion statistics provide that verdict.
    client_packaging: str | None = None
    reasons: tuple[str, ...] = field(default=())

    @property
    def kind(self) -> str:
        """remux | audio | video: what the session costs (concurrency caps and diagnostics)."""
        return "video" if self.video == "encode" else "audio" if self.audio == "encode" else "remux"


def video_range(stream: Mapping[str, Any]) -> str:
    base = {"smpte2084": "HDR10", "arib-std-b67": "HLG"}.get(stream.get("color_transfer") or "", "SDR")
    if stream.get("dv_profile") is None:
        return base
    return {1: "DOVIWithHDR10", 6: "DOVIWithEL", 4: "DOVIWithHLG", 2: "DOVIWithSDR"}.get(stream.get("dv_compat"), "DOVI")


def pixel_bit_depth(stream: Mapping[str, Any]) -> int | None:
    pixel_format = stream.get("pix_fmt")
    match = re.search(r"p0?(\d{2})(?:le|be)?(?:$|[^0-9])", pixel_format) if isinstance(pixel_format, str) else None
    return int(match[1]) if match else None


def bit_depth(stream: Mapping[str, Any]) -> int:
    pixel_depth = pixel_bit_depth(stream) or 8
    explicit = stream.get("bit_depth")
    # Normalized ffprobe depth remains useful when a pixel format is absent.
    # Conflicting metadata must never lower a known decoder requirement.
    if isinstance(explicit, int) and not isinstance(explicit, bool) and 1 <= explicit <= 64:
        return max(pixel_depth, explicit)
    return pixel_depth


def has_verified_ten_bit_video(stream: Mapping[str, Any]) -> bool:
    """Strict Dolby Vision paths require the original 10-bit pixel-format proof."""
    return pixel_bit_depth(stream) == 10 and bit_depth(stream) == 10


def _container_tokens(facts: Mapping[str, Any]) -> set[str]:
    names = {name.strip() for name in str(facts.get("container") or "").split(",") if name.strip()}
    if "webm" in names and "matroska" in names:  # ffprobe names both "matroska,webm"; the suffix decides
        return {"webm"} if facts.get("webm_suffix") else {"mkv", "matroska"}
    return set().union(*(CONTAINER_ALIASES.get(name, {name}) for name in names)) if names else set()


def _profile_matches(profile: DirectProfile, tokens: set[str], video: str | None, audio: str | None) -> bool:
    return (
        (profile.containers == ANY or bool(tokens & profile.containers))
        and (video is None or profile.video == ANY or video in profile.video)
        and (audio is None or profile.audio == ANY or audio in profile.audio)
    )


def _dolby_vision_source_is_single_layer(video: Mapping[str, Any]) -> bool:
    """Only a fully probed, 10-bit single-layer HEVC source can use the opt-in path."""
    return (
        video.get("codec") == "hevc"
        and has_verified_ten_bit_video(video)
        and video.get("dv_profile") in {5, 8}
        and isinstance(video.get("dv_compat"), int)
        and not isinstance(video.get("dv_compat"), bool)
        and isinstance(video.get("dv_level"), int)
        and not isinstance(video.get("dv_level"), bool)
        and 1 <= video["dv_level"] <= 9
        and video.get("dv_rpu_present") is True
        and video.get("dv_el_present") is False
        and video.get("dv_bl_present") is True
    )


def _dolby_vision_matches(profile: DirectProfile, video: Mapping[str, Any] | None) -> bool:
    """A present Lumina declaration is a strict direct-play gate for Dolby Vision."""
    if video is None or video.get("dv_profile") is None:
        return True
    if profile.dolby_vision is None:  # Legacy DeviceProfile: retain its base-layer behavior.
        return True
    if not _dolby_vision_source_is_single_layer(video):
        return False
    return any(
        cap.profile == video["dv_profile"]
        and video["dv_compat"] in cap.compatibility_ids
        and video["dv_level"] <= cap.max_level
        for cap in profile.dolby_vision
    )


def _profile7_source_is_convertible(video: Mapping[str, Any]) -> bool:
    """P7->8.1 packaging is a constrained, enhancement-layer-dropping route."""
    # ffprobe's DOVI record does not identify MEL versus FEL. Both are admitted
    # only to the explicitly lossy declaration; neither is described as preserved.
    return (
        video.get("codec") == "hevc"
        and has_verified_ten_bit_video(video)
        and type(video.get("dv_profile")) is int
        and video["dv_profile"] == 7
        and type(video.get("dv_compat")) is int
        and video["dv_compat"] == 6
        and isinstance(video.get("dv_level"), int)
        and not isinstance(video.get("dv_level"), bool)
        and 1 <= video["dv_level"] <= 9
        and video.get("dv_rpu_present") is True
        and video.get("dv_el_present") is True
        and video.get("dv_bl_present") is True
    )


def _profile7_separate_track_source_is_convertible(
    base: Mapping[str, Any] | None, videos: list[Mapping[str, Any]],
) -> bool:
    """Recognize only the qualified legacy BL-first two-track P7 carriage."""
    if base is None or len(videos) != 2 or videos[0] is not base:
        return False
    enhancement = videos[1]
    base_rate, enhancement_rate = base.get("frame_rate"), enhancement.get("frame_rate")
    for rate in (base_rate, enhancement_rate):
        if rate is not None and (
            type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0
        ):
            return False
    if base_rate is not None and enhancement_rate is not None and base_rate != enhancement_rate:
        return False
    return (
        base.get("codec") == enhancement.get("codec") == "hevc"
        and has_verified_ten_bit_video(base)
        and has_verified_ten_bit_video(enhancement)
        and type(base.get("dv_profile")) is int
        and type(enhancement.get("dv_profile")) is int
        and base["dv_profile"] == enhancement["dv_profile"] == 7
        and type(base.get("dv_compat")) is int
        and type(enhancement.get("dv_compat")) is int
        and base["dv_compat"] == enhancement["dv_compat"] == 6
        and type(base.get("dv_level")) is int
        and type(enhancement.get("dv_level")) is int
        and base["dv_level"] == enhancement["dv_level"]
        and 1 <= base["dv_level"] <= 9
        and base.get("dv_rpu_present") is False
        and base.get("dv_el_present") is False
        and base.get("dv_bl_present") is True
        and enhancement.get("dv_rpu_present") is True
        and enhancement.get("dv_el_present") is True
        and enhancement.get("dv_bl_present") is False
    )


def _profile_can_package_profile7(
    profile: DirectProfile, tokens: set[str], video: Mapping[str, Any] | None, audio: Mapping[str, Any] | None,
    videos: list[Mapping[str, Any]],
) -> bool:
    """Whether this exact MKV direct row may request client-side P7 packaging."""
    return (
        profile.profile7_to_81_max_level is not None
        and profile.containers == frozenset({"mkv"})
        and video is not None
        and _profile_matches(profile, tokens, video.get("codec"), audio and audio.get("codec"))
        and (
            _profile7_source_is_convertible(video)
            or (
                profile.profile7_separate_track_packaging
                and _profile7_separate_track_source_is_convertible(video, videos)
            )
        )
        and video["dv_level"] <= profile.profile7_to_81_max_level
    )


def _is_dolby_vision_direct_candidate(
    profiles: Iterable[DirectProfile], tokens: set[str], video: Mapping[str, Any] | None, audio: Mapping[str, Any] | None,
) -> bool:
    """A matching opt-in may supersede only the generic DOVI range condition."""
    if video is None or video.get("dv_profile") is None:
        return False
    return any(
        _profile_matches(profile, tokens, video.get("codec"), audio and audio.get("codec"))
        and _dolby_vision_matches(profile, video)
        and profile.dolby_vision is not None
        for profile in profiles
    )


def _is_profile7_packaging_direct_candidate(
    profiles: Iterable[DirectProfile], tokens: set[str], video: Mapping[str, Any] | None, audio: Mapping[str, Any] | None,
    videos: list[Mapping[str, Any]],
) -> bool:
    return any(_profile_can_package_profile7(profile, tokens, video, audio, videos) for profile in profiles)


_M2TS_PAIR_KEYS = {
    "packet_size", "program_number", "pmt_pid", "base_pid", "enhancement_pid",
    "base_stream_index", "enhancement_stream_index", "dv_profile", "dv_level", "dv_compat",
}


def _m2ts_pair_base(facts: Mapping[str, Any], videos: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return only a complete, probe-validated P7 M2TS base track.

    Facts are persisted/untrusted input, so repeat the small structural check
    here rather than treating a cache marker as an authorization by itself.
    """
    pair = facts.get("dv_transport_pair")
    if (
        facts.get("m2ts_suffix") is not True
        or str(facts.get("container") or "").split(",") != ["mpegts"]
        or not isinstance(pair, dict)
        or set(pair) != _M2TS_PAIR_KEYS
    ):
        return None
    required = {
        "packet_size": 192, "dv_profile": 7, "dv_compat": 6,
    }
    if any(pair.get(key) != value or type(pair.get(key)) is not int for key, value in required.items()):
        return None
    if type(pair.get("dv_level")) is not int or not 1 <= pair["dv_level"] <= 9:
        return None
    for key in ("program_number", "pmt_pid", "base_pid", "enhancement_pid", "base_stream_index", "enhancement_stream_index"):
        if type(pair.get(key)) is not int:
            return None
    if pair["base_pid"] == pair["enhancement_pid"] or pair["base_stream_index"] == pair["enhancement_stream_index"]:
        return None
    if len(videos) != 2:
        return None
    by_index = {stream.get("index"): stream for stream in videos}
    base, enhancement = by_index.get(pair["base_stream_index"]), by_index.get(pair["enhancement_stream_index"])
    if base is None or enhancement is None:
        return None
    if any(stream.get("codec") != "hevc" or not has_verified_ten_bit_video(stream) or stream.get("program_number") != pair["program_number"] for stream in (base, enhancement)):
        return None
    if base.get("ts_pid") != pair["base_pid"] or enhancement.get("ts_pid") != pair["enhancement_pid"]:
        return None
    expected = {
        "dv_profile": 7, "dv_compat": 6, "dv_level": pair["dv_level"],
        "dv_rpu_present": True, "dv_el_present": True, "dv_bl_present": False,
    }
    if any(enhancement.get(key) is not None and enhancement.get(key) != value for key, value in expected.items()):
        return None
    rates = (base.get("frame_rate"), enhancement.get("frame_rate"))
    if any(rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0) for rate in rates):
        return None
    if rates[0] is not None and rates[1] is not None and rates[0] != rates[1]:
        return None
    return base


def _profile_can_package_m2ts_profile7(
    profile: DirectProfile, tokens: set[str], video: Mapping[str, Any] | None, audio: Mapping[str, Any] | None,
    facts: Mapping[str, Any], videos: list[Mapping[str, Any]],
) -> bool:
    base = _m2ts_pair_base(facts, videos)
    pair = facts.get("dv_transport_pair")
    pair_level = pair.get("dv_level") if isinstance(pair, dict) else None
    return (
        profile.profile7_m2ts_pair_packaging
        and profile.profile7_to_81_max_level is not None
        and profile.containers == frozenset({"m2ts"})
        and base is not None and video is base
        and _profile_matches(profile, tokens, base.get("codec"), audio and audio.get("codec"))
        and pair_level <= profile.profile7_to_81_max_level
    ) if type(pair_level) is int else False


def _is_m2ts_profile7_packaging_direct_candidate(
    profiles: Iterable[DirectProfile], tokens: set[str], video: Mapping[str, Any] | None, audio: Mapping[str, Any] | None,
    facts: Mapping[str, Any], videos: list[Mapping[str, Any]],
) -> bool:
    return any(_profile_can_package_m2ts_profile7(profile, tokens, video, audio, facts, videos) for profile in profiles)


def _height_for_bitrate(cap: int | None) -> int | None:
    if not cap:
        return None
    return max((height for height, rate in RUNGS.items() if rate <= cap), default=min(RUNGS))


def _output_cap(client: ClientCaps, codec: str) -> VideoCap | None:
    return client.video.get(codec) or client.video.get("*")


def _hdr_source_range(video: Mapping[str, Any], rng: str) -> str | None:
    """The exact HDR range safe to preserve, never a Dolby Vision base layer."""
    if (
        rng not in {"HDR10", "HLG"}
        or video.get("dv_profile") is not None
        or bool(video.get("hdr10plus"))
        or bit_depth(video) < 10
        or video.get("color_space") != "bt2020nc"
        or video.get("color_primaries") != "bt2020"
        or video.get("color_range") != "tv"
    ):
        return None
    return rng


def hdr_preservation_range(client: ClientCaps, facts: Mapping[str, Any]) -> str | None:
    """The source range worth probing for, only when a client explicitly accepts it."""
    video = next((stream for stream in facts.get("streams") or [] if isinstance(stream, dict) and stream.get("type") == "video"), None)
    if video is None:
        return None
    rng = _hdr_source_range(video, video_range(video))
    if rng is None:
        return None
    for codec in client.transcode_video:
        cap = _output_cap(client, codec)
        if cap is not None and cap.max_bit_depth >= 10 and rng in cap.ranges and rng in client.transcode_ranges.get(codec, frozenset()):
            return rng
    return None


def _encoded_video_choice(client: ClientCaps, limits: Limits, video: Mapping[str, Any], rng: str, *, preserve_hdr: bool) -> tuple[str, str] | None:
    """The best explicit fMP4 HLS output shared by client and server.

    Direct-play claims and wildcard device profiles are intentionally absent:
    they say nothing about a browser/client accepting a newly encoded modern
    rendition.
    """
    available = set(client.transcode_video) & set(limits.encode_codecs)
    preserve = _hdr_source_range(video, rng) if preserve_hdr else None
    if preserve is not None:
        for codec in ("av1", "hevc"):
            cap = _output_cap(client, codec)
            if (
                codec in available
                and cap is not None
                and cap.max_bit_depth >= 10
                and preserve in cap.ranges
                and preserve in client.transcode_ranges.get(codec, frozenset())
                and preserve in limits.encode_ranges.get(codec, frozenset())
            ):
                return codec, preserve
    for codec in ("av1", "hevc", "h264"):
        if codec not in available:
            continue
        cap = _output_cap(client, codec)
        # SDR fallback remains 8-bit. A device profile's decoder limits still
        # apply to an HLS codec it explicitly asks us to create.
        if cap is None or (cap.max_bit_depth >= 8 and "SDR" in cap.ranges):
            return codec, "SDR"
    return None


def decide(client: ClientCaps, facts: Mapping[str, Any], limits: Limits = Limits(), audio_index: int | None = None, subtitle_index: int | None = None) -> Decision:
    streams = [s for s in facts.get("streams") or [] if isinstance(s, dict)]
    videos = [s for s in streams if s.get("type") == "video"]
    # A validated M2TS marker identifies the base PID; never infer it from
    # ffprobe stream ordering (the EL may be listed first).
    video = _m2ts_pair_base(facts, videos) or next(iter(videos), None)
    audios = [s for s in streams if s.get("type") == "audio"]
    default_audio = next((s for s in audios if s.get("default")), audios[0] if audios else None)
    audio = next((s for s in audios if s.get("index") == audio_index), default_audio)
    if video is None and audio is None:
        return Decision("unavailable", reasons=("no_playable_streams",))
    caps = [x for x in (limits.max_bitrate, client.max_bitrate) if x]
    bitrate_cap = min(caps) if caps else None

    video_reasons: list[str] = []
    burn = None
    rng = "SDR"
    if video is not None:
        rng = video_range(video)
        cap = client.video.get(video.get("codec") or "") or client.video.get("*")
        height, width = video.get("height") or 0, video.get("width") or 0
        checks = (
            (cap is None, "VideoCodecNotSupported"),
            (cap is not None and ((cap.max_height or height) < height or (cap.max_width or width) < width), "VideoResolutionNotSupported"),
            (cap is not None and bit_depth(video) > cap.max_bit_depth, "VideoBitDepthNotSupported"),
            (cap is not None and rng not in cap.ranges and BASE_LAYER.get(rng) not in cap.ranges, "VideoRangeTypeNotSupported"),
            (bool(limits.max_height) and height > (limits.max_height or 0), "VideoResolutionNotSupported"),
            (bool(bitrate_cap) and ((facts.get("bit_rate") or 0) > (bitrate_cap or 0) or (limits.require_known_bitrate and not facts.get("bit_rate"))), "ContainerBitrateExceedsLimit"),
        )
        video_reasons = list(dict.fromkeys(reason for bad, reason in checks if bad))
        sub = next((x for x in streams if x.get("type") == "subtitle" and x.get("index") == subtitle_index), None) if subtitle_index is not None else None
        if sub is not None and sub.get("codec") in IMAGE_SUBTITLE_CODECS and not client.image_subtitles:
            burn = sub["index"]
            video_reasons.append("SubtitleCodecNotSupported")

    codec_reasons: list[str] = []
    if audio is not None:
        depth_limit = min((limit for codec, limit in client.audio_bit_depth_limits.items() if codec in ("*", audio.get("codec"))), default=None)
        if audio.get("codec") not in client.audio and "*" not in client.audio:
            codec_reasons.append("AudioCodecNotSupported")
        elif (audio.get("channels") or 2) > client.channel_limits.get(audio.get("codec") or "", client.max_channels):
            codec_reasons.append("AudioChannelsNotSupported")
        elif depth_limit is not None and (audio.get("bit_depth") is None or audio["bit_depth"] > depth_limit):
            codec_reasons.append("AudioBitDepthNotSupported")
        elif limits.force_audio_encode:
            codec_reasons.append("AudioBitrateExceedsLimit")
    secondary = audio is not None and audio is not default_audio and not client.selects_audio
    reasons = video_reasons + codec_reasons + (["SecondaryAudioNotSupported"] if secondary else [])
    tokens = _container_tokens(facts)
    direct_dovi = _is_dolby_vision_direct_candidate(client.direct, tokens, video, audio)
    profile7_packaging = _is_profile7_packaging_direct_candidate(client.direct, tokens, video, audio, videos)
    m2ts_profile7_packaging = _is_m2ts_profile7_packaging_direct_candidate(client.direct, tokens, video, audio, facts, videos)
    playable = any(
        _profile_can_package_m2ts_profile7(profile, tokens, video, audio, facts, videos)
        if profile.profile7_m2ts_pair_packaging else (
            _profile_matches(profile, tokens, video and video.get("codec"), audio and audio.get("codec"))
            and (_dolby_vision_matches(profile, video) or _profile_can_package_profile7(profile, tokens, video, audio, videos))
        )
        for profile in client.direct
    )
    indexes = {"video_index": video.get("index") if video else None, "audio_index": audio.get("index") if audio else None}
    if video is None and bitrate_cap and ((facts.get("bit_rate") or 0) > bitrate_cap or (limits.require_known_bitrate and not facts.get("bit_rate"))):
        return Decision("unavailable", reasons=("ContainerBitrateExceedsLimit",))
    # An explicit Dolby Vision row authorizes only its direct candidate. Its
    # range exception must not escape into an HLS/remux fallback after another
    # constraint (audio, subtitle, or selected track) rejects direct play.
    direct_reasons = [
        reason for reason in reasons
        if not ((direct_dovi or m2ts_profile7_packaging) and reason == "VideoRangeTypeNotSupported")
    ]
    if not direct_reasons and playable:
        return Decision(
            "direct", video="copy" if video else None, audio="copy" if audio else None,
            client_packaging=(
                "p7_m2ts_to_p81_drop_el" if m2ts_profile7_packaging
                else "p7_to_p81_drop_el" if profile7_packaging else None
            ), **indexes,
        )
    if not reasons:
        reasons.append("ContainerNotSupported")

    copy_video = video is not None and not video_reasons and video.get("codec") in HLS_VIDEO_COPY & client.hls_video_copy
    if video is not None and not video_reasons and video.get("codec") in HLS_VIDEO_COPY - client.hls_video_copy:
        reasons.append("VideoCodecNotSupported")
    copy_audio = audio is not None and not codec_reasons and audio.get("codec") in HLS_AUDIO_COPY & client.hls_audio_copy
    height = bitrate = channels = None
    tonemap = False
    output_codec = "h264"
    output_range = "SDR"
    modern_output = False
    if video is not None and not copy_video:
        if rng == "DOVI" and video.get("dv_profile") == 5 and not limits.dv5_reshape:
            return Decision("unavailable", reasons=("dolby_vision_p5",))
        choice = _encoded_video_choice(client, limits, video, rng, preserve_hdr=burn is None)
        if choice is None:
            return Decision("unavailable", reasons=tuple(reasons + ["VideoCodecNotSupported"]))
        output_codec, output_range = choice
        modern_output = output_codec in {"hevc", "av1"}
        output = client.video.get(output_codec) or client.video.get("*") or VideoCap()
        output_cap = output.max_height
        max_height = 2160 if modern_output else MAX_ENCODE_HEIGHT
        rungs = MODERN_RUNGS if modern_output else RUNGS
        bitrate_height = max((height for height, rate in rungs.items() if not bitrate_cap or rate <= bitrate_cap), default=min(rungs))
        source_height, source_width = video.get("height") or max_height, video.get("width") or 0
        width_height = (output.max_width * source_height // source_width) if output.max_width and source_width else None
        height = min(h for h in (source_height, max_height, limits.max_height, output_cap, width_height, bitrate_height) if h)
        height -= height % 2
        if height < 2:
            return Decision("unavailable", reasons=tuple(reasons + ["VideoResolutionNotSupported"]))
        rung = min((r for r in rungs if r >= height), default=max_height)
        bitrate = min(rungs[rung], bitrate_cap) if bitrate_cap else rungs[rung]
        tonemap = output_range == "SDR" and rng not in ("SDR", "DOVIWithSDR")
    if modern_output and audio is not None and audio.get("codec") != "aac":
        # Modern output is admitted only through an explicit fMP4 HLS/AAC
        # profile. Direct-play support for Opus/AC3 must not leak into it.
        copy_audio = False
    if audio is not None and not copy_audio:
        channels = min(audio.get("channels") or 2, client.encode_channels)
    both_copied = (video is None or copy_video) and (audio is None or copy_audio)
    return Decision(
        "remux" if both_copied else "transcode",
        video=None if video is None else "copy" if copy_video else "encode",
        audio=None if audio is None else "copy" if copy_audio else "encode",
        burn_subtitle=burn, height=height, bitrate=bitrate, audio_channels=channels, tonemap=tonemap, video_codec=output_codec, video_range=output_range,
        reasons=tuple(reasons), **indexes,
    )


_MP4 = frozenset({"mp4", "mov", "m4v", "m4a"})
_MP4_VIDEO = frozenset({"h264", "av1", "vp9"})
_MP4_AUDIO = frozenset({"aac", "mp3", "opus", "flac"})
# What every mainstream browser plays; hls.js/MSE plays the same set from fMP4.
BROWSER_DEFAULT = ClientCaps(
    direct=(
        DirectProfile(_MP4, _MP4_VIDEO, _MP4_AUDIO),
        DirectProfile(frozenset({"webm"}), frozenset({"vp8", "vp9", "av1"}), frozenset({"opus", "vorbis"})),
        DirectProfile(frozenset({"mp3"}), frozenset(), frozenset({"mp3"})),
        DirectProfile(frozenset({"flac"}), frozenset(), frozenset({"flac"})),
        DirectProfile(frozenset({"ogg", "oga"}), frozenset(), frozenset({"opus", "vorbis", "flac"})),
        DirectProfile(frozenset({"wav"}), frozenset(), frozenset({"pcm_s16le", "pcm_f32le"})),
    ),
    video={"h264": VideoCap(), "vp8": VideoCap(), "vp9": VideoCap(max_bit_depth=10), "av1": VideoCap(max_bit_depth=10)},
    audio=frozenset({"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_f32le"}),
    transcode_video=("h264",),
)


def caps_from_browser_profiles(profiles: Iterable[str]) -> ClientCaps:
    """BROWSER_DEFAULT plus what this browser reported (frontend browserSupportedPlaybackProfiles)."""
    names = {str(p).strip().lower() for p in profiles}
    video, audio = dict(BROWSER_DEFAULT.video), set()
    if "av1-failed" in names:
        video.pop("av1", None)
    if "mp4-hevc10-aac" in names:
        video["hevc"] = VideoCap(max_bit_depth=10)
    elif "mp4-hevc-aac" in names:
        video["hevc"] = VideoCap()
    audio |= {codec for name, codec in (("mp4-ac3", "ac3"), ("mp4-eac3", "eac3")) if name in names}
    if "hdr" in names:
        video = {c: replace(cap, ranges=frozenset({"SDR", "HDR10", "HLG"})) if c in {"hevc", "vp9", "av1"} else cap for c, cap in video.items()}
    transcode = ["h264"]
    if "hls-hevc-aac" in names:
        transcode.append("hevc")
    if "hls-av1-aac" in names and "av1-failed" not in names:
        transcode.append("av1")
    transcode_ranges: dict[str, frozenset[str]] = {}
    for codec in ("hevc", "av1"):
        if codec not in transcode:
            continue
        ranges = frozenset(
            rng for rng, profile in (("HDR10", f"hls-{codec}-hdr10-aac"), ("HLG", f"hls-{codec}-hlg-aac"))
            if profile in names
        )
        if ranges:
            transcode_ranges[codec] = ranges
            cap = video.get(codec)
            if cap is not None:
                video[codec] = replace(cap, ranges=cap.ranges | ranges)
    if video == BROWSER_DEFAULT.video and not audio and transcode == ["h264"] and not transcode_ranges:
        return BROWSER_DEFAULT
    direct = list(BROWSER_DEFAULT.direct)
    browser_video = _MP4_VIDEO - ({"av1"} if "av1-failed" in names else set())
    direct[0] = DirectProfile(_MP4, browser_video | ({"hevc"} if "hevc" in video else set()), _MP4_AUDIO | audio)
    if "av1-failed" in names:
        direct[1] = DirectProfile(frozenset({"webm"}), frozenset({"vp8", "vp9"}), frozenset({"opus", "vorbis"}))
    return replace(BROWSER_DEFAULT, direct=tuple(direct), video=video, audio=BROWSER_DEFAULT.audio | audio, transcode_video=tuple(transcode), transcode_ranges=transcode_ranges)


def _tokens(value: Any) -> frozenset[str]:
    return frozenset(t.strip().lower() for t in value.split(",") if t.strip()) if isinstance(value, str) else frozenset()


def _number(value: Any) -> int | None:
    try:
        return int(float(str(value).split("|")[0]))
    except (TypeError, ValueError):
        return None


def _strict_int(value: Any, *, lower: int, upper: int) -> int | None:
    return value if type(value) is int and lower <= value <= upper else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _dolby_vision_caps(value: Any) -> tuple[DolbyVisionCap, ...]:
    """Parse Lumina's bounded per-DirectPlayProfile declaration, failing closed."""
    caps: list[DolbyVisionCap] = []
    for entry in _dicts(value):
        profile = _strict_int(entry.get("Profile"), lower=1, upper=9)
        max_level = _strict_int(entry.get("MaxLevel"), lower=1, upper=9)
        ids = entry.get("CompatibilityIDs")
        compatibility_ids = (
            frozenset(ids)
            if isinstance(ids, list) and all(type(item) is int and 0 <= item <= 9 for item in ids)
            else frozenset()
        )
        if profile == 5 and compatibility_ids == frozenset({0}) and max_level is not None:
            caps.append(DolbyVisionCap(profile, compatibility_ids, max_level))
        elif profile == 8 and compatibility_ids and compatibility_ids <= frozenset({1, 4}) and max_level is not None:
            caps.append(DolbyVisionCap(profile, compatibility_ids, max_level))
    return tuple(caps)


def _profile7_to_81_max_level(value: Any) -> int | None:
    """Parse Lumina's one exact, intentionally lossy P7 client-packaging declaration."""
    required = {
        "SourceProfile", "SourceCompatibilityIDs", "TargetProfile", "TargetCompatibilityID",
        "MaxLevel", "EnhancementLayerDisposition",
    }
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return None
    entry = value[0]
    if set(entry) != required:
        return None
    source_profile = _strict_int(entry.get("SourceProfile"), lower=1, upper=9)
    target_profile = _strict_int(entry.get("TargetProfile"), lower=1, upper=9)
    target_compatibility = _strict_int(entry.get("TargetCompatibilityID"), lower=0, upper=9)
    max_level = _strict_int(entry.get("MaxLevel"), lower=1, upper=9)
    source_ids = entry.get("SourceCompatibilityIDs")
    return max_level if (
        source_profile == 7
        and isinstance(source_ids, list)
        and all(type(item) is int for item in source_ids)
        and source_ids == [6]
        and target_profile == 8
        and target_compatibility == 1
        and entry.get("EnhancementLayerDisposition") == "dropped"
        and max_level is not None
    ) else None


def _m2ts_transport_profile(entry: dict[str, Any]) -> DirectProfile | None:
    """Read one custom transport row; ordinary direct rows can never opt in."""
    required = {
        "Type", "Container", "VideoCodec", "AudioCodec", "LuminaDolbyVisionProfiles", "LuminaDolbyVisionM2TSPairing",
        "LuminaDolbyVisionPackagingConversions",
    }
    if (
        set(entry) != required or entry.get("Type") != "Video"
        or entry.get("LuminaDolbyVisionProfiles") != []
        or entry.get("LuminaDolbyVisionM2TSPairing") is not True
    ):
        return None
    containers, video, audio = _tokens(entry.get("Container")), _tokens(entry.get("VideoCodec")), _tokens(entry.get("AudioCodec"))
    level = _profile7_to_81_max_level(entry.get("LuminaDolbyVisionPackagingConversions"))
    if containers != frozenset({"m2ts"}) or video != frozenset({"hevc"}) or not audio or level is None:
        return None
    return DirectProfile(containers, video, audio, profile7_to_81_max_level=level, profile7_m2ts_pair_packaging=True)


_CAP_FIELDS = {"Width": "max_width", "Height": "max_height", "VideoBitDepth": "max_bit_depth"}


def caps_from_device_profile(profile: Any) -> ClientCaps:
    """ClientCaps from a Jellyfin DeviceProfile (PlaybackInfo body).

    Only Width, Height, VideoBitDepth, VideoRangeType, AudioChannels, AudioBitDepth and VideoBitrate conditions
    are read. Every other property or condition is ignored on purpose: Jellyfin 10.11 treated
    unknown conditions as failures and transcoded everything.
    """
    profile = profile if isinstance(profile, dict) else {}
    direct: list[DirectProfile] = []
    video_codecs: set[str] = set()
    audio: set[str] = set()
    for entry in _dicts(profile.get("DirectPlayProfiles")):
        if entry.get("Type") not in ("Video", "Audio"):
            continue
        vids = (_tokens(entry.get("VideoCodec")) or ANY) if entry.get("Type") == "Video" else frozenset()
        auds = _tokens(entry.get("AudioCodec")) or ANY
        dovi = None if "LuminaDolbyVisionProfiles" not in entry else _dolby_vision_caps(entry.get("LuminaDolbyVisionProfiles"))
        containers = _tokens(entry.get("Container")) or ANY
        profile7_to_81 = (
            _profile7_to_81_max_level(entry.get("LuminaDolbyVisionPackagingConversions"))
            if entry.get("Type") == "Video" and containers == frozenset({"mkv"}) and "hevc" in vids
            else None
        )
        separate_track_p7 = bool(
            profile7_to_81 is not None
            and entry.get("LuminaDolbyVisionSeparateTrackPackaging") is True
        )
        direct.append(DirectProfile(containers, vids, auds, dovi, profile7_to_81, separate_track_p7))
        video_codecs |= vids
        audio |= auds
    transport_rows = [_m2ts_transport_profile(entry) for entry in _dicts(profile.get("LuminaDolbyVisionTransportProfiles"))]
    valid_transport_rows = [entry for entry in transport_rows if entry is not None]
    # A malformed or ambiguous custom list must not result in a best-effort
    # direct route. The new row is separate so legacy servers ignore it wholly.
    if len(transport_rows) == 1 and len(valid_transport_rows) == 1:
        direct.append(valid_transport_rows[0])
    transcode_video: set[str] = set()
    fmp4_hls_video: set[str] = set()
    fmp4_hls_audio: set[str] = set()
    has_hls_video_profile = False
    has_fmp4_hls_audio_profile = False
    hls_video_copy = set(HLS_VIDEO_COPY - {"av1"})
    for entry in _dicts(profile.get("TranscodingProfiles")):
        if entry.get("Type") != "Video" or str(entry.get("Protocol") or "").lower() != "hls":
            continue
        has_hls_video_profile = True
        # Lumina emits fragmented MP4 HLS with AAC. A profile for TS/WebM or
        # another audio codec is not an authorization to use this rendition.
        containers, vids, auds = _tokens(entry.get("Container")), _tokens(entry.get("VideoCodec")), _tokens(entry.get("AudioCodec"))
        if containers & {"mp4", "fmp4", "m4v"}:
            has_fmp4_hls_audio_profile = True
            fmp4_hls_audio |= HLS_AUDIO_COPY if "*" in auds else auds & HLS_AUDIO_COPY
        if containers & {"mp4", "fmp4", "m4v"} and "av1" in vids and "aac" in auds:
            hls_video_copy.add("av1")
        video_codecs |= vids
        audio |= auds
        # H.264 is the longstanding HLS compatibility contract. Older Jellyfin
        # clients commonly advertise AC3 or TS here even though their actual HLS
        # fallback works with our AAC fMP4 rendition. Modern output is stricter:
        # it needs an explicit fMP4/AAC profile, never a direct-play claim.
        if "h264" in vids or "*" in vids:
            transcode_video.add("h264")
        if not containers & {"mp4", "fmp4", "m4v"} or "aac" not in auds:
            continue
        modern = vids & {"hevc", "av1"}
        transcode_video |= modern
        fmp4_hls_video |= modern
        if "*" in vids:
            transcode_video.add("h264")  # broad historical profiles retain only the established baseline
    caps: dict[str, dict[str, Any]] = {codec: {"max_bit_depth": 16, "ranges": ALL_RANGES} for codec in video_codecs}
    explicit_ranges: dict[str, frozenset[str]] = {}
    max_channels, max_bitrate = 8, _number(profile.get("MaxStreamingBitrate"))
    channel_limits: dict[str, int] = {}
    audio_bit_depth_limits: dict[str, int] = {}
    for entry in _dicts(profile.get("CodecProfiles")):
        if entry.get("ApplyConditions"):
            continue  # Conditional profiles are skipped (more permissive); evaluate them if a client needs one
        codecs = _tokens(entry.get("Codec"))
        for cond in _dicts(entry.get("Conditions")):
            prop, op, value = cond.get("Property"), cond.get("Condition"), cond.get("Value")
            number = _number(value)
            if entry.get("Type") == "Video":
                for codec in [c for c in caps if not codecs or c in codecs]:
                    if prop in _CAP_FIELDS and op == "LessThanEqual" and number is not None:
                        key = _CAP_FIELDS[prop]
                        caps[codec][key] = min(caps[codec].get(key) or number, number)
                    elif prop == "VideoRangeType" and op in ("Equals", "EqualsAny") and isinstance(value, str):
                        ranges = frozenset(t.strip() for t in value.split("|") if t.strip())
                        caps[codec]["ranges"] = ranges
                        containers = _tokens(entry.get("Container"))
                        if not containers or containers & {"mp4", "fmp4", "m4v"}:
                            prior = explicit_ranges.get(codec)
                            explicit_ranges[codec] = ranges if prior is None else prior & ranges
                if prop == "VideoBitrate" and op == "LessThanEqual" and number:
                    max_bitrate = min(max_bitrate or number, number)
            elif entry.get("Type") in ("VideoAudio", "Audio") and prop == "AudioChannels" and op == "LessThanEqual" and number:
                for codec in codecs:
                    channel_limits[codec] = min(channel_limits.get(codec, number), number)
                if not codecs:
                    max_channels = min(max_channels, number)
            elif entry.get("Type") in ("VideoAudio", "Audio") and prop == "AudioBitDepth" and op == "LessThanEqual" and number:
                for codec in codecs or {"*"}:
                    audio_bit_depth_limits[codec] = min(audio_bit_depth_limits.get(codec, number), number)
    subtitles = _dicts(profile.get("SubtitleProfiles"))
    image = any(_tokens(e.get("Format")) & IMAGE_SUBTITLE_FORMATS and e.get("Method") == "Embed" for e in subtitles)
    final_transcode_video = (tuple(codec for codec in ("h264", "hevc", "av1") if codec in transcode_video) or ("h264",)) if not has_hls_video_profile else tuple(codec for codec in ("h264", "hevc", "av1") if codec in transcode_video)
    transcode_ranges = {
        codec: ranges & frozenset({"HDR10", "HLG"})
        for codec, ranges in explicit_ranges.items()
        if codec in fmp4_hls_video and ranges & {"HDR10", "HLG"}
    }
    return ClientCaps(
        direct=tuple(direct),
        video={codec: VideoCap(**limits) for codec, limits in caps.items()},
        audio=frozenset(audio),
        max_channels=max_channels,
        encode_channels=min(6, max_channels, channel_limits.get("aac", max_channels)),
        image_subtitles=image,
        max_bitrate=max_bitrate,
        selects_audio=True,
        channel_limits=channel_limits,
        audio_bit_depth_limits=audio_bit_depth_limits,
        # Device profiles predating TranscodingProfiles still rely on Lumina's
        # established H.264 fallback. A present HLS profile, by contrast, is an
        # explicit restriction: never manufacture a codec it did not advertise.
        transcode_video=final_transcode_video,
        transcode_ranges=transcode_ranges,
        hls_video_copy=frozenset(hls_video_copy),
        hls_audio_copy=frozenset(fmp4_hls_audio) if has_fmp4_hls_audio_profile else HLS_AUDIO_COPY,
    )
