"""Small, probeable RKMPP building blocks for the Linux Rockchip VPU path.

The Jellyfin FFmpeg package includes the RKMPP and RKRGA userspace libraries on
arm64, but those libraries only become hardware acceleration when the target
Rockchip BSP exposes the required device nodes.  Keep the actual capability
decision in a short encode probe rather than treating a packaged encoder as
proof that a host can use it.
"""
from __future__ import annotations

import subprocess


PROBE_TIMEOUT_SECONDS = 15
SUPPORTED_CODECS = frozenset({"h264", "hevc"})


def device_args() -> list[str]:
    """Create the RKMPP device used for upload and encode.

    This intentionally does not initialize OpenCL or RKRGA.  Both need
    target-specific driver/kernel qualification before they can be used in a
    playback graph.
    """
    return ["-init_hw_device", "rkmpp=rk", "-filter_hw_device", "rk"]


def encoder_args(codec: str) -> list[str]:
    """Conservative HLS encoder options supported by the bundled RKMPP FFmpeg."""
    if codec not in SUPPORTED_CODECS:
        raise ValueError(f"unsupported Rockchip output codec: {codec}")
    return ["-c:v", f"{codec}_rkmpp", "-rc_mode", "CBR", "-g", "10000"]


def encode_filter() -> str:
    """Upload CPU NV12 frames to the RKMPP device without assuming RGA support."""
    return "format=nv12,hwupload=derive_device=rkmpp"


def probe(ffmpeg: str, codec: str) -> tuple[bool, str]:
    """Run one actual CPU-frame-to-RKMPP encode and retain its diagnostic output."""
    try:
        result = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-v", "error", *device_args(),
                "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=1", "-frames:v", "1",
                "-vf", encode_filter(), *encoder_args(codec), "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    return result.returncode == 0, result.stdout + result.stderr
