"""Hardware transcoding: probe each encoder, then retain a software fallback.

The probe deliberately runs the same short H.264 encode shape that a session
uses. Seeing an encoder or a tone-map filter in ``ffmpeg -filters`` is not
enough: container runtimes frequently expose one without the corresponding
driver or usable GPU.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from app.config import settings
from app.services import rockchip
from app.services.media_probe import LOCAL_INPUT_ARGS

PROBE_TIMEOUT_SECONDS = 15
HDR_PROBE_TIMEOUT_SECONDS = 8
MAX_CONSECUTIVE_FAILURES = 3
_ONE_FRAME = ["-f", "lavfi", "-i", "testsrc2=size=256x144:rate=1", "-frames:v", "1"]
# Hardware GOPs stay longer than the HLS interval so only the forced keyframes
# define segment boundaries, including at high frame rates. Probe these same flags.
ENCODERS = {
    "none": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-sc_threshold", "0", "-x264opts", "subme=0:me_range=16:rc_lookahead=10:me=hex:open_gop=0"],
    # QVBR/ICQ with -maxrate is the documented pairing; tune on the target hardware if bitrates overshoot.
    "qsv": ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23", "-look_ahead", "0", "-adaptive_i", "0", "-adaptive_b", "0", "-forced_idr", "1", "-g", "10000"],
    "vaapi": ["-c:v", "h264_vaapi", "-rc_mode", "VBR", "-g", "10000"],
    # Keep these conservative: each probe runs the exact encoder before a session can select it.
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-forced-idr", "1", "-g", "10000"],
    # VideoToolbox otherwise chooses a one-second GOP, which violates the HLS
    # plan after the initial low-latency segments; forced keyframes still cut it.
    "videotoolbox": ["-c:v", "h264_videotoolbox", "-allow_sw", "0", "-realtime", "1", "-g", "10000"],
    "amf": ["-c:v", "h264_amf", "-quality", "speed", "-forced_idr", "1", "-pa_scene_change_detection_enable", "0", "-pa_adaptive_mini_gop", "0", "-g", "10000"],
    "rkmpp": rockchip.encoder_args("h264"),
}
CODECS = ("h264", "hevc", "av1")
HDR_RANGES = ("HDR10", "HLG")
_HDR_TRANSFER = {"HDR10": "smpte2084", "HLG": "arib-std-b67"}
_HDR_CODEC = frozenset({"hevc", "av1"})
_HDR_MASTERING = {
    "red_x": Fraction(34000, 50000), "red_y": Fraction(16000, 50000),
    "green_x": Fraction(13250, 50000), "green_y": Fraction(34500, 50000),
    "blue_x": Fraction(7500, 50000), "blue_y": Fraction(3000, 50000),
    "white_point_x": Fraction(15635, 50000), "white_point_y": Fraction(16450, 50000),
    "min_luminance": Fraction(1, 10000), "max_luminance": Fraction(10000000, 10000),
}
# ``ENCODERS`` remains the H.264 compatibility surface used by existing callers;
# new sessions ask ``encoder_args`` for the negotiated output codec.
_CODEC_ENCODERS = {
    "h264": ENCODERS,
    "hevc": {
        "none": ["-c:v", "libx265", "-preset", "ultrafast", "-crf", "28", "-forced-idr", "1", "-x265-params", "pools=1:frame-threads=1:scenecut=0"],
        "qsv": ["-c:v", "hevc_qsv", "-preset", "veryfast", "-global_quality", "23", "-look_ahead", "0", "-adaptive_i", "0", "-adaptive_b", "0", "-forced_idr", "1", "-g", "10000"],
        "vaapi": ["-c:v", "hevc_vaapi", "-rc_mode", "VBR", "-g", "10000"],
        "nvenc": ["-c:v", "hevc_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-forced-idr", "1", "-g", "10000"],
        "videotoolbox": ["-c:v", "hevc_videotoolbox", "-allow_sw", "0", "-realtime", "1", "-g", "10000"],
        "amf": ["-c:v", "hevc_amf", "-quality", "speed", "-forced_idr", "1", "-pa_scene_change_detection_enable", "0", "-pa_adaptive_mini_gop", "0", "-g", "10000"],
        "rkmpp": rockchip.encoder_args("hevc"),
    },
    "av1": {
        "none": ["-c:v", "libsvtav1", "-preset", "13", "-crf", "38", "-svtav1-params", "lp=1:scd=0"],
        "qsv": ["-c:v", "av1_qsv", "-preset", "veryfast", "-global_quality", "23", "-look_ahead", "0", "-g", "10000"],
        "vaapi": ["-c:v", "av1_vaapi", "-rc_mode", "VBR", "-g", "10000"],
        "nvenc": ["-c:v", "av1_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-g", "10000"],
        "amf": ["-c:v", "av1_amf", "-quality", "speed", "-g", "10000"],
    },
}
HARDWARE_MODES = frozenset(ENCODERS) - {"none"}
_RATE = ["-b:v", "8000000", "-maxrate", "8000000", "-bufsize", "16000000"]
_SOFTWARE_RATE = _RATE[2:]  # CRF encoders retain their quality mode; sessions omit -b:v too.


@dataclass(frozen=True)
class HwStatus:
    active: str = "none"  # HwaccelActive
    probe_ok: bool = False
    probe_error: str | None = None
    tonemap: str = "none"  # TonemapMethod for hardware sessions
    software_tonemap: str = "none"  # TonemapMethod for software sessions
    encode_codecs: tuple[str, ...] = ("h264",)
    encode_errors: dict[str, str] = field(default_factory=dict, compare=False)
    # These are deliberately cached observations.  Reading an Activity page
    # never starts a slow HDR or DRM-PRIME process probe.
    hdr_encode_ranges: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False)
    hdr_encode_errors: dict[str, str] = field(default_factory=dict, compare=False)
    zero_copy_pairs: tuple[str, ...] = ()
    zero_copy_errors: dict[str, str] = field(default_factory=dict, compare=False)


def encoder_args(hw: str, codec: str, video_range: str = "SDR") -> list[str]:
    """Encoder arguments for a codec proven before it is advertised to clients."""
    try:
        args = list(_CODEC_ENCODERS[codec][hw])
    except KeyError as exc:
        raise ValueError(f"unsupported encoder: {hw}/{codec}") from exc
    if video_range == "SDR":
        return args
    if video_range not in HDR_RANGES or codec not in _HDR_CODEC or hw == "rkmpp":
        raise ValueError(f"unsupported HDR encoder: {hw}/{codec}/{video_range}")
    # SVT only writes HDR colour and static metadata when its explicit HDR
    # switch is on.  Hardware encoders retain FFmpeg frame side-data instead.
    if hw == "none" and codec == "av1":
        index = args.index("-svtav1-params") + 1
        args[index] = f"{args[index]}:enable-hdr=1"
    return args


def device_args(hw: str) -> list[str]:
    """Initialize the device used by a session or probe before its input."""
    if hw == "qsv":
        if platform.system() == "Windows":
            # FFmpeg's QSV device selects a DirectX adapter through its D3D11VA
            # child; keep this index aligned with the configured GPU selection.
            return ["-init_hw_device", f"qsv=qs:hw,child_device={settings.hwaccel_gpu_index},child_device_type=d3d11va", "-filter_hw_device", "qs"]
        return ["-init_hw_device", f"vaapi=va:{settings.hwaccel_device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
    if hw == "vaapi":
        return ["-init_hw_device", f"vaapi=va:{settings.hwaccel_device}", "-filter_hw_device", "va"]
    if hw == "nvenc":
        return ["-init_hw_device", f"cuda=cu:{settings.hwaccel_gpu_index}", "-filter_hw_device", "cu"]
    if hw == "rkmpp":
        return rockchip.device_args()
    return []


def _encode_test(hw: str, codec: str = "h264") -> list[str]:
    graph = {
        "qsv": "format=nv12,hwupload=extra_hw_frames=64,scale_qsv=w=-1:h=144:format=nv12",
        "vaapi": "format=nv12,hwupload=extra_hw_frames=64,scale_vaapi=w=-2:h=144:format=nv12",
        "nvenc": "format=nv12,hwupload=extra_hw_frames=64,scale_cuda=w=-2:h=144:format=nv12",
        "videotoolbox": "scale=-2:144,format=nv12",
        "amf": "scale=-2:144,format=nv12",
        "rkmpp": rockchip.encode_filter(),
    }[hw]
    gpu = ["-gpu", str(settings.hwaccel_gpu_index)] if hw == "nvenc" else []
    return [*device_args(hw), *_ONE_FRAME, "-vf", graph, *encoder_args(hw, codec), *gpu, *_RATE]


def _software_encode_test(codec: str) -> list[str]:
    return [*_ONE_FRAME, "-vf", "scale=-2:144,format=yuv420p", *encoder_args("none", codec), "-threads", "1", *_SOFTWARE_RATE]


def _hdr_colour_args(video_range: str) -> list[str]:
    """Explicit CICP for the only two HDR ranges Lumina currently preserves."""
    return [
        "-color_primaries", "bt2020", "-color_trc", _HDR_TRANSFER[video_range],
        "-colorspace", "bt2020nc", "-color_range", "tv",
    ]


def _hdr_source(ffmpeg: str, source: Path, video_range: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Create a bounded, local MP4 source with CICP plus HDR10 static SEI.

    This is intentionally a real local file, rather than an HLS input: runtime
    sessions retain the file-only demuxer and protocol boundary too.
    """
    transfer = _HDR_TRANSFER[video_range]
    x265 = (
        "pools=1:frame-threads=1:repeat-headers=1:scenecut=0:"
        f"colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc:range=limited:"
        "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
        "max-cll=1000,400"
    )
    return _run([
        ffmpeg, "-hide_banner", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=384x216:rate=2",
        "-t", "3", "-vf", "format=yuv420p10le", *_hdr_colour_args(video_range),
        "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", x265,
        "-tag:v", "hvc1", "-movflags", "+write_colr", str(source),
    ], timeout=timeout)


def _hdr_graph(hw: str, height: int) -> tuple[list[str], str]:
    """A P010-only encode graph; no tone map or 8-bit transfer is permitted."""
    if hw == "none":
        return [], f"scale=-2:{height},format=yuv420p10le"
    if hw == "qsv":
        return device_args(hw), f"format=p010,hwupload=extra_hw_frames=64,scale_qsv=w=-1:h={height}:format=p010"
    if hw == "vaapi":
        return device_args(hw), f"format=p010,hwupload=extra_hw_frames=64,scale_vaapi=w=-2:h={height}:format=p010"
    if hw == "nvenc":
        return device_args(hw), f"format=p010,hwupload=extra_hw_frames=64,scale_cuda=w=-2:h={height}:format=p010"
    if hw in {"videotoolbox", "amf"}:
        return [], f"scale=-2:{height},format=p010le"
    raise ValueError(f"HDR preservation is unavailable for {hw}")


def _hdr_stream(ffprobe: str, init: Path, segment: Path, timeout: float) -> tuple[dict[str, object] | None, str]:
    ok, output = _run([
        ffprobe, "-v", "error", "-protocol_whitelist", "file,concat", "-select_streams", "v:0",
        "-show_streams", "-of", "json", f"concat:{init}|{segment}",
    ], timeout=timeout)
    if not ok:
        return None, _last_line(output)
    try:
        stream = json.loads(output).get("streams", [])[0]
    except (IndexError, TypeError, json.JSONDecodeError):
        return None, "ffprobe returned no video stream"
    return stream, ""


def _has_static_hdr_metadata(stream: Mapping[str, object]) -> bool:
    side_data = stream.get("side_data_list")
    if not isinstance(side_data, list):
        return False
    kinds = {str(item.get("side_data_type")) for item in side_data if isinstance(item, dict)}
    if not {"Mastering display metadata", "Content light level metadata"} <= kinds:
        return False
    mastering = next((item for item in side_data if isinstance(item, dict) and item.get("side_data_type") == "Mastering display metadata"), None)
    if not isinstance(mastering, dict):
        return False
    try:
        if any(Fraction(str(mastering.get(key))) != expected for key, expected in _HDR_MASTERING.items()):
            return False
    except (ValueError, ZeroDivisionError):
        return False
    light = next((item for item in side_data if isinstance(item, dict) and item.get("side_data_type") == "Content light level metadata"), None)
    if not isinstance(light, dict):
        return False
    return str(light.get("max_content")) == "1000" and str(light.get("max_average")) == "400"


def _verify_hdr_hls(ffprobe: str, directory: Path, codec: str, video_range: str, deadline: float | None = None) -> tuple[bool, str]:
    init = directory / "init.mp4"
    segments = sorted(directory.glob("seg*.m4s"))
    if not init.is_file() or len(segments) < 2:
        return False, "HDR HLS output did not create multiple fMP4 fragments"
    expected_transfer = _HDR_TRANSFER[video_range]
    for segment in segments:
        timeout = PROBE_TIMEOUT_SECONDS if deadline is None else deadline - time.monotonic()
        if timeout <= 0:
            return False, "HDR probe deadline exceeded"
        stream, error = _hdr_stream(ffprobe, init, segment, timeout)
        if stream is None:
            return False, f"{segment.name}: {error}"
        if (
            stream.get("codec_name") != codec
            or stream.get("pix_fmt") not in {"yuv420p10le", "p010le"}
            or stream.get("color_primaries") != "bt2020"
            or stream.get("color_transfer") != expected_transfer
            or stream.get("color_space") != "bt2020nc"
            or stream.get("color_range") != "tv"
            or not _has_static_hdr_metadata(stream)
        ):
            return False, f"{segment.name}: HDR CICP or static metadata did not survive"
    return True, ""


def _hdr_encode_test(ffmpeg: str, ffprobe: str, hw: str, codec: str, video_range: str) -> tuple[bool, str]:
    """Prove exact output codec/range through local MP4 -> fMP4 HLS, including seek.

    The small bounded input still uses a realistic 384x216 -> 256x144 shape;
    several real GPU drivers reject tiny test frames despite handling playback.
    """
    if codec not in _HDR_CODEC or video_range not in HDR_RANGES or hw == "rkmpp":
        return False, "unsupported HDR encoder/range"
    try:
        pre, graph = _hdr_graph(hw, 144)
    except ValueError as exc:
        return False, str(exc)
    gpu = ["-gpu", str(settings.hwaccel_gpu_index)] if hw == "nvenc" else []
    deadline = time.monotonic() + HDR_PROBE_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="lumina-hdr-probe-") as temporary:
        root = Path(temporary)
        source = root / "source.mp4"
        source_timeout = deadline - time.monotonic()
        if source_timeout <= 0:
            return False, "HDR probe deadline exceeded"
        generated, output = _hdr_source(ffmpeg, source, video_range, source_timeout)
        if not generated:
            return False, f"HDR source synthesis failed: {_last_line(output)}"
        for seek in (0, 1):
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                return False, "HDR probe deadline exceeded"
            directory = root / ("seek" if seek else "start")
            directory.mkdir()
            command = [
                ffmpeg, "-hide_banner", "-y", "-v", "error", *pre, *LOCAL_INPUT_ARGS,
                *(["-ss", str(seek)] if seek else []), "-i", str(source), "-an", "-vf", graph,
                *encoder_args(hw, codec, video_range), *gpu, *(_SOFTWARE_RATE if hw == "none" else _RATE), *_hdr_colour_args(video_range),
                "-force_key_frames", "expr:gte(t,n_forced*1)", "-f", "hls", "-hls_time", "1",
                "-hls_playlist_type", "event", "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                "-hls_segment_filename", str(directory / "seg%d.m4s"),
                "-hls_flags", "independent_segments", str(directory / "index.m3u8"),
            ]
            ok, probe_output = _run(command, timeout=timeout)
            if not ok:
                return False, f"HDR {'seek ' if seek else ''}encode failed: {_last_line(probe_output)}"
            verified, error = _verify_hdr_hls(ffprobe, directory, codec, video_range, deadline)
            if not verified:
                return False, f"HDR {'seek ' if seek else ''}verification failed: {error}"
    return True, ""


def _tone_test(hw: str, tone_map: str) -> list[str]:
    """A PQ frame through the device-local tone mapper and the playback encoder."""
    source = ["-f", "lavfi", "-i", "testsrc2=size=256x144:rate=1,format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc", "-frames:v", "1"]
    pre = device_args(hw)
    if tone_map == "opencl":
        pre += ["-init_hw_device", "opencl=ocl@va"]
        scale = "scale_qsv=w=-1:h=144:format=p010" if hw == "qsv" else "scale_vaapi=w=-2:h=144:format=p010"
        graph = f"format=p010,hwupload=extra_hw_frames=64,{scale},hwmap=derive_device=opencl,tonemap_opencl=tonemap=bt2390:desat=0:peak=100:t=bt709:m=bt709:p=bt709:format=nv12,hwmap=derive_device={hw}:reverse=1:extra_hw_frames=16"
    elif tone_map == "vpp_qsv":
        graph = "format=p010,hwupload=extra_hw_frames=64,vpp_qsv=w=-1:h=144:tonemap=1:format=nv12"
    elif tone_map == "cuda":
        graph = "format=p010,hwupload=extra_hw_frames=64,scale_cuda=w=-2:h=144:format=p010,tonemap_cuda=tonemap=bt2390:desat=0:peak=100:t=bt709:m=bt709:p=bt709:format=nv12:apply_dovi=1"
    else:
        raise ValueError(f"unsupported tone map: {tone_map}")
    gpu = ["-gpu", str(settings.hwaccel_gpu_index)] if hw == "nvenc" else []
    return [*pre, *source, "-vf", graph, *encoder_args(hw, "h264"), *gpu, *_RATE]


def _run(args: list[str], *, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    if timeout <= 0:
        return False, "probe_timeout"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    return result.returncode == 0, result.stdout + result.stderr


def _has_filter(listing: str, name: str) -> bool:
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", listing, re.M) is not None


def _last_line(output: str) -> str:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "failed"


def _candidates(mode: str) -> tuple[str, ...]:
    if mode in HARDWARE_MODES:
        return (mode,)
    if mode != "auto":
        return ()
    system = platform.system()
    if system == "Darwin":
        return ("videotoolbox",)
    if system == "Windows":
        return ("qsv", "nvenc", "amf")
    return ("qsv", "nvenc", "vaapi", "rkmpp")


def _tonemap(ffmpeg: str, hw: str, filters: str, software: str) -> str:
    # A listed filter is merely a candidate; the actual short H.264 encode is
    # the capability check, including driver access and the selected device.
    if hw in {"qsv", "vaapi"} and _has_filter(filters, "tonemap_opencl"):
        if _run([ffmpeg, "-hide_banner", "-v", "error", *_tone_test(hw, "opencl"), "-f", "null", "-"])[0]:
            return "opencl"
    if hw == "qsv" and _has_filter(filters, "vpp_qsv"):
        if _run([ffmpeg, "-hide_banner", "-v", "error", *_tone_test(hw, "vpp_qsv"), "-f", "null", "-"])[0]:
            return "vpp_qsv"
    if hw == "nvenc" and _has_filter(filters, "tonemap_cuda"):
        if _run([ffmpeg, "-hide_banner", "-v", "error", *_tone_test(hw, "cuda"), "-f", "null", "-"])[0]:
            return "cuda"
    return software


def detect(ffmpeg: str | None, mode: str) -> HwStatus:
    if not ffmpeg:
        return HwStatus(probe_error="ffmpeg_unavailable")
    _ok, filters = _run([ffmpeg, "-hide_banner", "-filters"])
    software = "software" if _has_filter(filters, "zscale") and _has_filter(filters, "tonemap") else "none"
    if mode == "off":
        return HwStatus(probe_ok=True, tonemap=software, software_tonemap=software)
    candidates = _candidates(mode)
    if not candidates:
        return HwStatus(probe_error=f"unsupported_hwaccel_mode: {mode}"[:200], tonemap=software, software_tonemap=software)
    errors: list[str] = []
    for hw in candidates:
        ok, output = rockchip.probe(ffmpeg, "h264") if hw == "rkmpp" else _run([ffmpeg, "-hide_banner", "-v", "error", *_encode_test(hw, "h264"), "-f", "null", "-"])
        if not ok:
            errors.append(f"{hw}: {_last_line(output)}"[:200])
            continue
        codecs, codec_errors = ["h264"], {}
        for codec in ("hevc", "av1"):
            if hw not in _CODEC_ENCODERS[codec]:
                codec_errors[codec] = "unsupported_encoder"
                continue
            modern_ok, modern_output = rockchip.probe(ffmpeg, codec) if hw == "rkmpp" else _run([ffmpeg, "-hide_banner", "-v", "error", *_encode_test(hw, codec), "-f", "null", "-"])
            if modern_ok:
                codecs.append(codec)
            else:
                codec_errors[codec] = _last_line(modern_output)[:200]
        return HwStatus(active=hw, probe_ok=True, tonemap=_tonemap(ffmpeg, hw, filters, software), software_tonemap=software, encode_codecs=tuple(codecs), encode_errors=codec_errors)
    return HwStatus(probe_error="; ".join(errors), tonemap=software, software_tonemap=software)


class HwAccel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str | None, str], HwStatus] = {}
        self._software_cache: dict[tuple[str | None, str, str, str | None], bool] = {}
        self._hdr_cache: dict[tuple[str | None, str, str | None, str, int, str, str], tuple[bool, str]] = {}
        self._zero_copy_cache: dict[tuple[str | None, str | None, str, int, str, str], tuple[bool, str]] = {}
        self._hdr_context: dict[tuple[str | None, str], tuple[str, str, int]] = {}
        self._zero_copy_context: dict[str | None, tuple[str, str, int]] = {}
        self.failures = 0
        self.fallbacks = 0
        self.disabled = False

    def status(self, ffmpeg: str | None, mode: str) -> HwStatus:
        with self._lock:
            key = (ffmpeg, mode)
            if key not in self._cache:
                self._cache[key] = detect(ffmpeg, mode)
            return self._cache[key]

    def warm(self, ffmpeg: str | None, mode: str) -> None:
        """Probe in the background at startup: the first encode would otherwise wait for the test encodes."""
        threading.Thread(target=self.status, args=(ffmpeg, mode), name="hwaccel-probe", daemon=True).start()

    def peek(self, ffmpeg: str | None, mode: str) -> HwStatus | None:
        """The cached probe, never starting one (the Activity page polls every few seconds)."""
        with self._lock:
            return self._cache.get((ffmpeg, mode))

    def output_codecs(self, ffmpeg: str | None, mode: str) -> tuple[str, ...]:
        """Codecs a client may negotiate: modern formats require a passing hardware probe."""
        status = self.status(ffmpeg, mode)
        return ("h264",) if self.disabled or status.active == "none" else status.encode_codecs

    def _store_hdr_result(self, ffmpeg: str | None, mode: str, codec: str, video_range: str, result: tuple[bool, str]) -> None:
        """Record a completed on-demand probe without making status() probe HDR."""
        with self._lock:
            key = (ffmpeg, mode)
            status = self._cache.get(key)
            if status is None:
                return
            ranges = dict(status.hdr_encode_ranges)
            errors = dict(status.hdr_encode_errors)
            existing = set(ranges.get(codec, ()))
            name = f"{codec}/{video_range}"
            if result[0]:
                existing.add(video_range)
                ranges[codec] = tuple(sorted(existing))
                errors.pop(name, None)
            else:
                existing.discard(video_range)
                if existing:
                    ranges[codec] = tuple(sorted(existing))
                else:
                    ranges.pop(codec, None)
                errors[name] = result[1][:200]
            self._cache[key] = replace(status, hdr_encode_ranges=ranges, hdr_encode_errors=errors)

    def output_ranges(
        self, ffmpeg: str | None, mode: str, ffprobe: str | None = None, *, video_range: str | None = None,
    ) -> dict[str, frozenset[str]]:
        """Return output HDR ranges proven for this exact encoder and verifier.

        Playback calls this with one negotiated range, so ordinary SDR library
        browsing never performs an HDR process probe.  The no-range form is for
        an explicit diagnostics action and may qualify both HDR10 and HLG.
        """
        if video_range is not None and video_range not in HDR_RANGES:
            return {}
        status = self.status(ffmpeg, mode)
        if self.disabled or status.active == "none" or not ffmpeg or not ffprobe:
            return {}
        context = (ffprobe, str(settings.hwaccel_device), settings.hwaccel_gpu_index)
        with self._lock:
            status_key = (ffmpeg, mode)
            if self._hdr_context.get(status_key) != context:
                current = self._cache.get(status_key)
                if current is not None:
                    self._cache[status_key] = replace(current, hdr_encode_ranges={}, hdr_encode_errors={})
                self._hdr_context[status_key] = context
            status = self._cache.get(status_key, status)
        ranges_to_probe = (video_range,) if video_range else HDR_RANGES
        # The status record is diagnostic history only.  The value returned to
        # a planner is reconstructed from this verifier/device cache context so
        # a changed ffprobe or GPU can never inherit an old passing result.
        proven: dict[str, set[str]] = {}
        for codec in status.encode_codecs:
            if codec not in _HDR_CODEC or status.active == "rkmpp":
                continue
            for candidate_range in ranges_to_probe:
                cache_key = (ffmpeg, mode, ffprobe, str(settings.hwaccel_device), settings.hwaccel_gpu_index, codec, candidate_range)
                with self._lock:
                    result = self._hdr_cache.get(cache_key)
                if result is None:
                    result = _hdr_encode_test(ffmpeg, ffprobe, status.active, codec, candidate_range)
                    with self._lock:
                        self._hdr_cache[cache_key] = result
                self._store_hdr_result(ffmpeg, mode, codec, candidate_range, result)
                if result[0]:
                    proven.setdefault(codec, set()).add(candidate_range)
        return {codec: frozenset(ranges) for codec, ranges in proven.items() if ranges}

    def choice(
        self, ffmpeg: str | None, mode: str, codec: str = "h264", video_range: str = "SDR", ffprobe: str | None = None,
    ) -> tuple[str, str]:
        """(hw, tonemap) for the exact negotiated output codec."""
        status = self.status(ffmpeg, mode)
        if self.disabled or status.active == "none" or codec not in status.encode_codecs:
            return "none", status.software_tonemap
        eligible = video_range == "SDR" or video_range in self.output_ranges(ffmpeg, mode, ffprobe, video_range=video_range).get(codec, frozenset())
        if not eligible:
            return "none", status.software_tonemap
        return status.active, status.tonemap

    def software_codec_supported(
        self, ffmpeg: str | None, codec: str, video_range: str = "SDR", ffprobe: str | None = None,
    ) -> bool:
        """Verify a modern CPU fallback before using it after a GPU session fails."""
        if codec == "h264" and video_range == "SDR":
            return True
        if video_range != "SDR" and (codec not in _HDR_CODEC or not ffmpeg or not ffprobe):
            return False
        with self._lock:
            key = (ffmpeg, codec, video_range, ffprobe)
            cached = self._software_cache.get(key)
        if cached is not None:
            return cached
        if video_range == "SDR":
            result = bool(ffmpeg) and _run([ffmpeg, "-hide_banner", "-v", "error", *_software_encode_test(codec), "-f", "null", "-"])[0]
        else:
            result = bool(ffmpeg) and bool(ffprobe) and _hdr_encode_test(ffmpeg, ffprobe, "none", codec, video_range)[0]
        with self._lock:
            self._software_cache[key] = result
        return result

    def zero_copy_supported(
        self, ffmpeg: str | None, input_codec: str | None, output_codec: str, ffprobe: str | None = None,
    ) -> bool:
        """Probe one explicit RKMPP decoder/RGA/encoder pair, never a generic claim."""
        if not ffmpeg or not ffprobe or input_codec is None:
            return False
        key = (ffmpeg, ffprobe, str(settings.hwaccel_device), settings.hwaccel_gpu_index, input_codec, output_codec)
        with self._lock:
            context = (ffprobe, str(settings.hwaccel_device), settings.hwaccel_gpu_index)
            if self._zero_copy_context.get(ffmpeg) != context:
                for status_key, status in tuple(self._cache.items()):
                    if status_key[0] == ffmpeg and status.active == "rkmpp":
                        self._cache[status_key] = replace(status, zero_copy_pairs=(), zero_copy_errors={})
                self._zero_copy_context[ffmpeg] = context
            result = self._zero_copy_cache.get(key)
        if result is None:
            result = rockchip.probe_zero_copy(ffmpeg, input_codec, output_codec, ffprobe)
            with self._lock:
                self._zero_copy_cache[key] = result
                for status_key, status in tuple(self._cache.items()):
                    if status_key[0] != ffmpeg or status.active != "rkmpp":
                        continue
                    pair = f"{input_codec}->{output_codec}"
                    pairs = set(status.zero_copy_pairs)
                    errors = dict(status.zero_copy_errors)
                    if result[0]:
                        pairs.add(pair)
                        errors.pop(pair, None)
                    else:
                        errors[pair] = result[1][:200]
                    self._cache[status_key] = replace(status, zero_copy_pairs=tuple(sorted(pairs)), zero_copy_errors=errors)
        return result[0]

    def software_tonemap(self, ffmpeg: str | None, mode: str) -> str:
        return self.status(ffmpeg, mode).software_tonemap

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.fallbacks += 1
            if self.failures >= MAX_CONSECUTIVE_FAILURES:
                self.disabled = True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0

    def reprobe(self) -> None:
        """Forget cached probes (admin diagnostics). A disabled fallback stays until restart."""
        with self._lock:
            self._cache.clear()
            self._software_cache.clear()
            self._hdr_cache.clear()
            self._zero_copy_cache.clear()
            self._hdr_context.clear()
            self._zero_copy_context.clear()


hwaccel = HwAccel()
