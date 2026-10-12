"""Pure playback decision. No I/O.

Given probe facts (media_probe.normalize), what a client says it plays, and member/bitrate
limits, pick the least work: direct > remux > audio-only encode > video encode. Video is
re-encoded only for an unsupported codec, bit depth or HDR range, a size or bitrate over the
limit, or an image subtitle the client cannot render. Text subtitles are always sidecars.
Reasons use Jellyfin's TranscodeReasons names so PlaybackInfo can emit them as they are.
"""
from __future__ import annotations

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
RUNGS = {1080: 8_000_000, 720: 4_000_000, 480: 1_500_000}  # output height -> -maxrate (bps)
MAX_ENCODE_HEIGHT = 1080  # H.264 output tops out here; 4K sources play direct or at 1080
# ffprobe format_name tokens -> the container names clients advertise.
CONTAINER_ALIASES = {
    "matroska": {"mkv", "matroska"}, "mov": {"mov", "mp4", "m4v", "m4a"}, "mp4": {"mp4", "m4v", "mov", "m4a"},
    "mpegts": {"ts", "mpegts", "m2ts"}, "asf": {"asf", "wmv", "wma"}, "mpeg": {"mpeg", "mpg"}, "ogg": {"ogg", "oga"},
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


@dataclass(frozen=True)
class Limits:
    max_height: int | None = None  # member ceiling and/or the chosen quality
    max_bitrate: int | None = None  # Jellyfin MaxStreamingBitrate
    dv5_reshape: bool = False  # OpenCL tone-mapping can reshape Dolby Vision profile 5


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


def bit_depth(stream: Mapping[str, Any]) -> int:
    match = re.search(r"p(\d{2})", stream.get("pix_fmt") or "")
    return int(match[1]) if match else 8


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


def _height_for_bitrate(cap: int | None) -> int | None:
    if not cap:
        return None
    return max((height for height, rate in RUNGS.items() if rate <= cap), default=min(RUNGS))


def decide(client: ClientCaps, facts: Mapping[str, Any], limits: Limits = Limits(), audio_index: int | None = None, subtitle_index: int | None = None) -> Decision:
    streams = [s for s in facts.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("type") == "video"), None)
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
            (bool(bitrate_cap) and (facts.get("bit_rate") or 0) > (bitrate_cap or 0), "ContainerBitrateExceedsLimit"),
        )
        video_reasons = list(dict.fromkeys(reason for bad, reason in checks if bad))
        sub = next((x for x in streams if x.get("type") == "subtitle" and x.get("index") == subtitle_index), None) if subtitle_index is not None else None
        if sub is not None and sub.get("codec") in IMAGE_SUBTITLE_CODECS and not client.image_subtitles:
            burn = sub["index"]
            video_reasons.append("SubtitleCodecNotSupported")

    codec_reasons: list[str] = []
    if audio is not None:
        if audio.get("codec") not in client.audio and "*" not in client.audio:
            codec_reasons.append("AudioCodecNotSupported")
        elif (audio.get("channels") or 2) > client.channel_limits.get(audio.get("codec") or "", client.max_channels):
            codec_reasons.append("AudioChannelsNotSupported")
    secondary = audio is not None and audio is not default_audio and not client.selects_audio
    reasons = video_reasons + codec_reasons + (["SecondaryAudioNotSupported"] if secondary else [])
    tokens = _container_tokens(facts)
    playable = any(_profile_matches(p, tokens, video and video.get("codec"), audio and audio.get("codec")) for p in client.direct)
    indexes = {"video_index": video.get("index") if video else None, "audio_index": audio.get("index") if audio else None}
    if not reasons and playable:
        return Decision("direct", video="copy" if video else None, audio="copy" if audio else None, **indexes)
    if not reasons:
        reasons.append("ContainerNotSupported")

    copy_video = video is not None and not video_reasons and video.get("codec") in HLS_VIDEO_COPY
    copy_audio = audio is not None and not codec_reasons and audio.get("codec") in HLS_AUDIO_COPY
    height = bitrate = channels = None
    tonemap = False
    if video is not None and not copy_video:
        if rng == "DOVI" and video.get("dv_profile") == 5 and not limits.dv5_reshape:
            return Decision("unavailable", reasons=("dolby_vision_p5",))
        output_cap = (client.video.get("h264") or client.video.get("*") or VideoCap()).max_height  # the encode is always H.264
        output_cap = max(output_cap, 144) if output_cap else None  # a garbage profile never asks for a 2-pixel picture
        height = min(h for h in (video.get("height") or MAX_ENCODE_HEIGHT, MAX_ENCODE_HEIGHT, limits.max_height, output_cap, _height_for_bitrate(bitrate_cap)) if h)
        height -= height % 2
        rung = min((r for r in RUNGS if r >= height), default=MAX_ENCODE_HEIGHT)
        bitrate = min(RUNGS[rung], bitrate_cap) if bitrate_cap else RUNGS[rung]
        tonemap = rng not in ("SDR", "DOVIWithSDR")
    if audio is not None and not copy_audio:
        channels = min(audio.get("channels") or 2, client.encode_channels)
    both_copied = (video is None or copy_video) and (audio is None or copy_audio)
    return Decision(
        "remux" if both_copied else "transcode",
        video=None if video is None else "copy" if copy_video else "encode",
        audio=None if audio is None else "copy" if copy_audio else "encode",
        burn_subtitle=burn, height=height, bitrate=bitrate, audio_channels=channels, tonemap=tonemap,
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
)


def caps_from_browser_profiles(profiles: Iterable[str]) -> ClientCaps:
    """BROWSER_DEFAULT plus what this browser reported (frontend browserSupportedPlaybackProfiles)."""
    names = {str(p).strip().lower() for p in profiles}
    video, audio = dict(BROWSER_DEFAULT.video), set()
    if "mp4-hevc10-aac" in names:
        video["hevc"] = VideoCap(max_bit_depth=10)
    elif "mp4-hevc-aac" in names:
        video["hevc"] = VideoCap()
    audio |= {codec for name, codec in (("mp4-ac3", "ac3"), ("mp4-eac3", "eac3")) if name in names}
    if "hdr" in names:
        video = {c: replace(cap, ranges=frozenset({"SDR", "HDR10", "HLG"})) if c in {"hevc", "vp9", "av1"} else cap for c, cap in video.items()}
    if video == BROWSER_DEFAULT.video and not audio:
        return BROWSER_DEFAULT
    mp4 = DirectProfile(_MP4, _MP4_VIDEO | ({"hevc"} if "hevc" in video else set()), _MP4_AUDIO | audio)
    return replace(BROWSER_DEFAULT, direct=(mp4, *BROWSER_DEFAULT.direct[1:]), video=video, audio=BROWSER_DEFAULT.audio | audio)


def _tokens(value: Any) -> frozenset[str]:
    return frozenset(t.strip().lower() for t in value.split(",") if t.strip()) if isinstance(value, str) else frozenset()


def _number(value: Any) -> int | None:
    try:
        return int(float(str(value).split("|")[0]))
    except (TypeError, ValueError):
        return None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


_CAP_FIELDS = {"Width": "max_width", "Height": "max_height", "VideoBitDepth": "max_bit_depth"}


def caps_from_device_profile(profile: Any) -> ClientCaps:
    """ClientCaps from a Jellyfin DeviceProfile (PlaybackInfo body).

    Only Width, Height, VideoBitDepth, VideoRangeType, AudioChannels and VideoBitrate conditions
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
        direct.append(DirectProfile(_tokens(entry.get("Container")) or ANY, vids, auds))
        video_codecs |= vids
        audio |= auds
    for entry in _dicts(profile.get("TranscodingProfiles")):
        if entry.get("Type") == "Video" and str(entry.get("Protocol") or "").lower() == "hls":
            video_codecs |= _tokens(entry.get("VideoCodec"))
            audio |= _tokens(entry.get("AudioCodec"))
    caps: dict[str, dict[str, Any]] = {codec: {"max_bit_depth": 16, "ranges": ALL_RANGES} for codec in video_codecs}
    max_channels, max_bitrate = 8, _number(profile.get("MaxStreamingBitrate"))
    channel_limits: dict[str, int] = {}
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
                        caps[codec]["ranges"] = frozenset(t.strip() for t in value.split("|") if t.strip())
                if prop == "VideoBitrate" and op == "LessThanEqual" and number:
                    max_bitrate = min(max_bitrate or number, number)
            elif entry.get("Type") in ("VideoAudio", "Audio") and prop == "AudioChannels" and op == "LessThanEqual" and number:
                for codec in codecs:
                    channel_limits[codec] = min(channel_limits.get(codec, number), number)
                if not codecs:
                    max_channels = min(max_channels, number)
    subtitles = _dicts(profile.get("SubtitleProfiles"))
    image = any(_tokens(e.get("Format")) & IMAGE_SUBTITLE_FORMATS and e.get("Method") == "Embed" for e in subtitles)
    return ClientCaps(
        direct=tuple(direct),
        video={codec: VideoCap(**limits) for codec, limits in caps.items()},
        audio=frozenset(audio),
        max_channels=max_channels,
        encode_channels=min(6, max_channels),
        image_subtitles=image,
        max_bitrate=max_bitrate,
        selects_audio=True,
        channel_limits=channel_limits,
    )
