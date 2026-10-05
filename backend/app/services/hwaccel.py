"""Hardware transcoding: a cached probe and the software fallback.

The probe runs a one-frame test encode per candidate (auto tries qsv, then vaapi) and reads
``ffmpeg -filters`` for the tone-mapping filters. Sessions ask ``choice()`` which encoder to
use. A hardware session that dies before its first playlist calls ``record_failure()`` and
retries in software; after three consecutive failures hardware stays off until restart.
"""
from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass

PROBE_TIMEOUT_SECONDS = 15
MAX_CONSECUTIVE_FAILURES = 3
RENDER_NODE = "/dev/dri/renderD128"  # The first render node; make it a setting if a host ever has two GPUs
_ONE_FRAME = ["-f", "lavfi", "-i", "testsrc2=size=256x144:rate=1", "-frames:v", "1"]
_TESTS = {
    "qsv": ["-init_hw_device", f"vaapi=va:{RENDER_NODE}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs", *_ONE_FRAME, "-vf", "format=nv12,hwupload=extra_hw_frames=16", "-c:v", "h264_qsv"],
    "vaapi": ["-init_hw_device", f"vaapi=va:{RENDER_NODE}", "-filter_hw_device", "va", *_ONE_FRAME, "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"],
    "opencl": ["-init_hw_device", "opencl=ocl", "-filter_hw_device", "ocl", *_ONE_FRAME, "-vf", "format=nv12,hwupload,hwdownload,format=nv12", "-c:v", "rawvideo"],
}


@dataclass(frozen=True)
class HwStatus:
    active: str = "none"  # HwaccelActive
    probe_ok: bool = False
    probe_error: str | None = None
    tonemap: str = "none"  # TonemapMethod for hardware sessions
    software_tonemap: str = "none"  # TonemapMethod for software sessions


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


def detect(ffmpeg: str | None, mode: str) -> HwStatus:
    if not ffmpeg:
        return HwStatus(probe_error="ffmpeg_unavailable")
    _ok, filters = _run([ffmpeg, "-hide_banner", "-filters"])
    software = "software" if _has_filter(filters, "zscale") and _has_filter(filters, "tonemap") else "none"
    if mode == "off":
        return HwStatus(probe_ok=True, tonemap=software, software_tonemap=software)
    errors: list[str] = []
    for hw in ("qsv", "vaapi") if mode == "auto" else (mode,):
        ok, output = _run([ffmpeg, "-hide_banner", "-v", "error", *_TESTS[hw], "-f", "null", "-"])
        if not ok:
            errors.append(f"{hw}: {_last_line(output)}"[:200])
            continue
        if _has_filter(filters, "tonemap_opencl") and _run([ffmpeg, "-hide_banner", "-v", "error", *_TESTS["opencl"], "-f", "null", "-"])[0]:
            tonemap = "opencl"
        elif hw == "qsv" and _has_filter(filters, "vpp_qsv"):
            tonemap = "vpp_qsv"
        else:
            tonemap = software
        return HwStatus(active=hw, probe_ok=True, tonemap=tonemap, software_tonemap=software)
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
