"""Hardware transcoding: probe each encoder, then retain a software fallback.

The probe deliberately runs the same short H.264 encode shape that a session
uses. Seeing an encoder or a tone-map filter in ``ffmpeg -filters`` is not
enough: container runtimes frequently expose one without the corresponding
driver or usable GPU.
"""
from __future__ import annotations

import platform
import re
import subprocess
import threading
from dataclasses import dataclass

from app.config import settings

PROBE_TIMEOUT_SECONDS = 15
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
}
HARDWARE_MODES = frozenset(ENCODERS) - {"none"}
_RATE = ["-b:v", "8000000", "-maxrate", "8000000", "-bufsize", "16000000"]


@dataclass(frozen=True)
class HwStatus:
    active: str = "none"  # HwaccelActive
    probe_ok: bool = False
    probe_error: str | None = None
    tonemap: str = "none"  # TonemapMethod for hardware sessions
    software_tonemap: str = "none"  # TonemapMethod for software sessions


def device_args(hw: str) -> list[str]:
    """Initialize the device used by a session or probe before its input."""
    if hw == "qsv":
        return ["-init_hw_device", f"vaapi=va:{settings.hwaccel_device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
    if hw == "vaapi":
        return ["-init_hw_device", f"vaapi=va:{settings.hwaccel_device}", "-filter_hw_device", "va"]
    if hw == "nvenc":
        return ["-init_hw_device", f"cuda=cu:{settings.hwaccel_gpu_index}", "-filter_hw_device", "cu"]
    return []


def _encode_test(hw: str) -> list[str]:
    graph = {
        "qsv": "format=nv12,hwupload=extra_hw_frames=64,scale_qsv=w=-1:h=144:format=nv12",
        "vaapi": "format=nv12,hwupload=extra_hw_frames=64,scale_vaapi=w=-2:h=144:format=nv12",
        "nvenc": "format=nv12,hwupload=extra_hw_frames=64,scale_cuda=w=-2:h=144:format=nv12",
        "videotoolbox": "scale=-2:144,format=nv12",
        "amf": "scale=-2:144,format=nv12",
    }[hw]
    gpu = ["-gpu", str(settings.hwaccel_gpu_index)] if hw == "nvenc" else []
    return [*device_args(hw), *_ONE_FRAME, "-vf", graph, *ENCODERS[hw], *gpu, *_RATE]


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
    return [*pre, *source, "-vf", graph, *ENCODERS[hw], *gpu, *_RATE]


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL)
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
    return ("qsv", "nvenc", "vaapi")


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
        ok, output = _run([ffmpeg, "-hide_banner", "-v", "error", *_encode_test(hw), "-f", "null", "-"])
        if not ok:
            errors.append(f"{hw}: {_last_line(output)}"[:200])
            continue
        return HwStatus(active=hw, probe_ok=True, tonemap=_tonemap(ffmpeg, hw, filters, software), software_tonemap=software)
    return HwStatus(probe_error="; ".join(errors), tonemap=software, software_tonemap=software)


class HwAccel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str | None, str], HwStatus] = {}
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

    def choice(self, ffmpeg: str | None, mode: str) -> tuple[str, str]:
        """(hw, tonemap) for a new video encode."""
        status = self.status(ffmpeg, mode)
        if self.disabled or status.active == "none":
            return "none", status.software_tonemap
        return status.active, status.tonemap

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


hwaccel = HwAccel()
