"""Small, probeable RKMPP building blocks for the Linux Rockchip VPU path.

The Jellyfin FFmpeg package includes the RKMPP and RKRGA userspace libraries on
arm64, but those libraries only become hardware acceleration when the target
Rockchip BSP exposes the required device nodes.  Keep the actual capability
decision in a short encode probe rather than treating a packaged encoder as
proof that a host can use it.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile


PROBE_TIMEOUT_SECONDS = 15
SUPPORTED_CODECS = frozenset({"h264", "hevc"})
ZERO_COPY_INPUT_CODECS = frozenset({"h264", "hevc", "vp9", "av1"})
ZERO_COPY_PROBE_HEIGHT = 144
_ZERO_COPY_SOURCE_ENCODERS = {
    "h264": ["-c:v", "libx264", "-preset", "ultrafast", "-g", "5"],
    "hevc": ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error:keyint=5"],
    "vp9": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-g", "5"],
    "av1": ["-c:v", "libsvtav1", "-preset", "12", "-g", "5"],
}


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


def zero_copy_decoder_args(input_codec: str) -> list[str] | None:
    """Return explicit DRM-PRIME RKMPP decode options for one verified input codec.

    Keeping the decoder name explicit makes the probe prove the requested pair,
    rather than letting FFmpeg silently choose a software decoder.
    """
    if input_codec not in ZERO_COPY_INPUT_CODECS:
        return None
    return [
        "-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime", "-afbc", "rga",
        "-c:v", f"{input_codec}_rkmpp",
    ]


def zero_copy_scale_filter(height: int) -> str:
    """Scale an SDR 8-bit DRM-PRIME frame through RGA without a CPU transfer."""
    if height <= 0 or height % 2:
        raise ValueError("Rockchip zero-copy scale height must be a positive even integer")
    return (
        f"scale_rkrga=w=-2:h={height}:format=nv12:force_yuv=8bit:"
        "force_chroma=420sp:afbc=1"
    )


def _probe_failure(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in (result.stderr + result.stdout).splitlines() if line.strip()]
    return lines[-1] if lines else f"ffmpeg exited {result.returncode}"


def _run_probe(command: list[str]) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            check=False, stdin=subprocess.DEVNULL,
        ), None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__


def _ffprobe_for(ffmpeg: str, ffprobe: str | None) -> str:
    if ffprobe:
        return ffprobe
    sibling = Path(ffmpeg).with_name("ffprobe")
    return str(sibling) if sibling.is_file() else "ffprobe"


def probe_zero_copy(
    ffmpeg: str,
    input_codec: str,
    output_codec: str,
    ffprobe: str | None = None,
) -> tuple[bool, str]:
    """Qualify one exact SDR decoder/RGA/encoder pair with a real output file.

    This deliberately excludes HDR, tone mapping, and subtitle burn-in.  Those
    paths need CPU or OpenCL work that makes a DRM-PRIME/RGA/MPP-only claim
    inaccurate.  A host must pass this probe for each input/output pair before
    playback may call that pair zero-copy.
    """
    decoder = zero_copy_decoder_args(input_codec)
    if decoder is None:
        return False, f"unsupported Rockchip zero-copy input codec: {input_codec}"
    if output_codec not in SUPPORTED_CODECS:
        return False, f"unsupported Rockchip zero-copy output codec: {output_codec}"

    with tempfile.TemporaryDirectory(prefix="lumina-rkmpp-zero-copy-") as temporary:
        root = Path(temporary)
        source = root / "source.mkv"
        output = root / "output.mkv"
        source_result, error = _run_probe([
            ffmpeg, "-hide_banner", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5", "-frames:v", "5",
            "-pix_fmt", "yuv420p", *_ZERO_COPY_SOURCE_ENCODERS[input_codec], "-f", "matroska", str(source),
        ])
        if error:
            return False, f"{input_codec} source synthesis failed: {error}"
        if source_result is None or source_result.returncode:
            return False, f"{input_codec} source synthesis failed: {_probe_failure(source_result)}"
        if not source.is_file() or not source.stat().st_size:
            return False, f"{input_codec} source synthesis created no media input"

        graph_result, error = _run_probe([
            ffmpeg, "-hide_banner", "-y", "-v", "error", *device_args(), *decoder,
            "-i", str(source), "-an", "-frames:v", "5",
            "-vf", zero_copy_scale_filter(ZERO_COPY_PROBE_HEIGHT), *encoder_args(output_codec),
            "-b:v", "400k", "-f", "matroska", str(output),
        ])
        if error:
            return False, f"{input_codec}->{output_codec} zero-copy graph failed: {error}"
        if graph_result is None or graph_result.returncode:
            return False, f"{input_codec}->{output_codec} zero-copy graph failed: {_probe_failure(graph_result)}"
        if not output.is_file() or not output.stat().st_size:
            return False, f"{input_codec}->{output_codec} zero-copy graph created no media output"

        probe_result, error = _run_probe([
            _ffprobe_for(ffmpeg, ffprobe), "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=codec_name,width,height,nb_read_frames", "-of", "json", str(output),
        ])
        if error:
            return False, f"output verification failed: {error}"
        if probe_result is None or probe_result.returncode:
            return False, f"output verification failed: {_probe_failure(probe_result)}"
        try:
            streams = json.loads(probe_result.stdout).get("streams", [])
            stream = streams[0]
            frame_count = int(stream.get("nb_read_frames") or 0)
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            return False, "output verification returned no readable video stream"
        if (
            stream.get("codec_name") != output_codec
            or stream.get("height") != ZERO_COPY_PROBE_HEIGHT
            or stream.get("width") != 256
            or frame_count < 1
        ):
            return False, f"output verification did not match {output_codec} 256x{ZERO_COPY_PROBE_HEIGHT} with frames"

        try:
            decoded = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-v", "error", "-i", str(output), "-map", "0:v:0",
                    "-frames:v", "1", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-",
                ],
                capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"output frame decode failed: {type(exc).__name__}"
        if decoded.returncode or not decoded.stdout:
            return False, "output frame decode produced no pixels"
    return True, f"{input_codec}->{output_codec} decoded through DRM-PRIME, RGA, and MPP with a readable frame"


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
