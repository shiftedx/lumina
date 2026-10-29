#!/usr/bin/env python3
"""Qualify one selected FFmpeg hardware backend with synthetic HLS output.

Run it as Lumina's runtime user on the target Linux host or in the Lumina
container.  It never opens media files, credentials, network URLs, or a running
Lumina server: the short input is generated with FFmpeg's ``testsrc2`` and
``sine`` sources.  A passing result proves this particular driver, device
access path, FFmpeg build, encoder and codec can create independently decodable
HLS segments.  It is not throughput or HDR qualification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Final

# The packaged image already sets PYTHONPATH=/app/backend.  A checkout needs
# the same bounded backend path when this public diagnostic is run directly.
REPOSITORY = Path(__file__).resolve().parents[1]
if (REPOSITORY / "backend" / "app").is_dir():
    sys.path.insert(0, str(REPOSITORY / "backend"))

from app.services import rockchip


ENCODERS: Final[dict[str, dict[str, list[str]]]] = {
    "qsv": {
        "h264": ["-c:v", "h264_qsv", "-global_quality", "23", "-look_ahead", "0", "-adaptive_i", "0", "-adaptive_b", "0", "-forced_idr", "1", "-g", "10000"],
        "hevc": ["-c:v", "hevc_qsv", "-global_quality", "23", "-look_ahead", "0", "-adaptive_i", "0", "-adaptive_b", "0", "-forced_idr", "1", "-g", "10000"],
        "av1": ["-c:v", "av1_qsv", "-global_quality", "23", "-look_ahead", "0", "-g", "10000"],
    },
    "vaapi": {
        "h264": ["-c:v", "h264_vaapi", "-rc_mode", "VBR", "-g", "10000"],
        "hevc": ["-c:v", "hevc_vaapi", "-rc_mode", "VBR", "-g", "10000"],
        "av1": ["-c:v", "av1_vaapi", "-rc_mode", "VBR", "-g", "10000"],
    },
    "nvenc": {
        "h264": ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-forced-idr", "1", "-g", "10000"],
        "hevc": ["-c:v", "hevc_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-forced-idr", "1", "-g", "10000"],
        "av1": ["-c:v", "av1_nvenc", "-preset", "p4", "-tune", "hq", "-rc-lookahead", "0", "-no-scenecut", "1", "-g", "10000"],
    },
    "rkmpp": {
        "h264": ["-c:v", "h264_rkmpp", "-rc_mode", "CBR", "-g", "10000"],
        "hevc": ["-c:v", "hevc_rkmpp", "-rc_mode", "CBR", "-g", "10000"],
    },
}


def device_args(mode: str, device: str, gpu_index: int) -> list[str]:
    if mode == "qsv":
        return ["-init_hw_device", f"vaapi=va:{device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
    if mode == "vaapi":
        return ["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"]
    if mode == "nvenc":
        return ["-init_hw_device", f"cuda=cu:{gpu_index}", "-filter_hw_device", "cu"]
    if mode == "rkmpp":
        return ["-init_hw_device", "rkmpp=rk", "-filter_hw_device", "rk"]
    raise ValueError(f"unsupported mode: {mode}")


def encode_filter(mode: str) -> str:
    filters = {
        "qsv": "format=nv12,hwupload=extra_hw_frames=64,scale_qsv=w=-2:h=180:format=nv12",
        "vaapi": "format=nv12,hwupload=extra_hw_frames=64,scale_vaapi=w=-2:h=180:format=nv12",
        "nvenc": "format=nv12,hwupload=extra_hw_frames=64,scale_cuda=w=-2:h=180:format=nv12",
        # CPU-to-RKMPP proves an encoder/device path without pretending the target's RGA/DRM-PRIME stack is qualified.
        "rkmpp": "format=nv12,hwupload=derive_device=rkmpp",
    }
    return filters[mode]


def _run(command: list[str], *, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 1, "", type(exc).__name__)


def _failure(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in (result.stderr + result.stdout).splitlines() if line.strip()]
    return lines[-1] if lines else f"ffmpeg exited {result.returncode}"


def _encode_command(ffmpeg: str, mode: str, codec: str, device: str, gpu_index: int, output: Path, *, hls: bool) -> list[str]:
    tag = {"hevc": "hvc1", "av1": "av01"}.get(codec)
    command = [
        ffmpeg, "-hide_banner", "-y", "-v", "error", *device_args(mode, device, gpu_index),
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
        "-t", "3.2", "-vf", encode_filter(mode), "-map", "0:v:0", "-map", "1:a:0",
        *ENCODERS[mode][codec], *(["-tag:v", tag] if tag else []), "-b:v", "800k", "-maxrate", "800k", "-bufsize", "1600k",
        "-force_key_frames", "expr:gte(t,n_forced*1)", "-c:a", "aac", "-b:a", "96k",
    ]
    if mode == "nvenc":
        command.extend(["-gpu", str(gpu_index)])
    if not hls:
        # AV1 encoders may buffer more than one frame before producing a
        # packet, so run the same short source window as the HLS check.
        return [*command, "-f", "null", "-"]
    return [
        *command, "-hls_time", "1", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(output / "segment-%03d.m4s"), "-f", "hls", str(output / "index.m3u8"),
    ]


def _segments(manifest: Path) -> list[Path]:
    return [manifest.parent / line.strip() for line in manifest.read_text().splitlines() if line and not line.startswith("#")]


def _check_hls(ffmpeg: str, ffprobe: str, directory: Path, codec: str) -> tuple[bool, str]:
    manifest = directory / "index.m3u8"
    if not manifest.is_file():
        return False, "HLS manifest was not created"
    if "#EXT-X-MAP:URI=\"init.mp4\"" not in manifest.read_text():
        return False, "fMP4 HLS manifest has no init.mp4 map"
    init = directory / "init.mp4"
    parts = _segments(manifest)
    if not init.is_file() or not init.stat().st_size or len(parts) < 2 or any(not part.is_file() or not part.stat().st_size for part in parts):
        return False, "fMP4 HLS output needs an init file and at least two non-empty segments"
    for segment in parts:
        # A media segment intentionally has no movie header. Probe/decode the
        # exact init+fragment pair a client receives instead of accidentally
        # validating MPEG-TS-only behavior for HEVC or AV1.
        joined = directory / f"probe-{segment.stem}.mp4"
        with joined.open("wb") as output:
            output.write(init.read_bytes())
            output.write(segment.read_bytes())
        streams = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(joined)])
        if streams.returncode or streams.stdout.strip() != codec:
            return False, f"{segment.name} reports {streams.stdout.strip() or _failure(streams)}, expected {codec}"
        frames = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+0.2", "-show_frames", "-show_entries", "frame=key_frame", "-of", "csv=p=0", str(joined)])
        first_frame = frames.stdout.splitlines()[0].strip().split(",", 1)[0] if frames.stdout.splitlines() else ""
        if frames.returncode or first_frame != "1":
            return False, f"{segment.name} does not begin with a keyframe"
        decoded = _run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(joined), "-f", "null", "-"])
        if decoded.returncode:
            return False, f"{segment.name} does not decode: {_failure(decoded)}"
    return True, f"{len(parts)} HLS segments encode as {codec}, begin on keyframes, and decode"


def qualify(
    ffmpeg: str,
    ffprobe: str,
    mode: str,
    codecs: list[str],
    device: str,
    gpu_index: int,
    *,
    zero_copy_inputs: list[str] | None = None,
) -> dict[str, object]:
    results: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="lumina-gpu-qualification-") as temp:
        root = Path(temp)
        for codec in codecs:
            if codec not in ENCODERS[mode]:
                results[codec] = {"ok": False, "detail": f"{mode} has no supported {codec} encoder"}
                continue
            probe = _run(_encode_command(ffmpeg, mode, codec, device, gpu_index, root, hls=False))
            if probe.returncode:
                results[codec] = {"ok": False, "detail": f"encoder probe failed: {_failure(probe)}"}
                continue
            destination = root / codec
            destination.mkdir()
            encoded = _run(_encode_command(ffmpeg, mode, codec, device, gpu_index, destination, hls=True))
            if encoded.returncode:
                results[codec] = {"ok": False, "detail": f"HLS encode failed: {_failure(encoded)}"}
                continue
            ok, detail = _check_hls(ffmpeg, ffprobe, destination, codec)
            results[codec] = {"ok": ok, "detail": detail}
    report: dict[str, object] = {
        "mode": mode,
        "device": device if mode in {"qsv", "vaapi"} else None,
        "gpu_index": gpu_index if mode == "nvenc" else None,
        "codecs": results,
    }
    if zero_copy_inputs:
        pairs: dict[str, dict[str, object]] = {}
        for input_codec in zero_copy_inputs:
            for output_codec in codecs:
                ok, detail = rockchip.probe_zero_copy(ffmpeg, input_codec, output_codec, ffprobe)
                pairs[f"{input_codec}->{output_codec}"] = {"ok": ok, "detail": detail}
        report["zero_copy_pairs"] = pairs
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(ENCODERS), required=True, help="Backend selected in Lumina settings.")
    parser.add_argument("--codec", action="append", choices=("h264", "hevc", "av1"), default=[], help="Output codec to qualify; repeat for each required codec (default: h264).")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    parser.add_argument("--device", default="/dev/dri/renderD128", help="DRM render node for QSV/VA-API.")
    parser.add_argument("--gpu-index", type=int, default=0, help="Visible CUDA GPU index for NVENC.")
    parser.add_argument(
        "--zero-copy", action="store_true",
        help="For RKMPP only: additionally prove each explicit SDR decoder/RGA/encoder pair.",
    )
    parser.add_argument(
        "--input-codec", action="append", choices=sorted(rockchip.ZERO_COPY_INPUT_CODECS), default=[],
        help="RKMPP source codec for --zero-copy; repeat for every expected SDR input codec.",
    )
    args = parser.parse_args()
    if args.gpu_index < 0:
        parser.error("--gpu-index must be non-negative")
    if args.zero_copy and args.mode != "rkmpp":
        parser.error("--zero-copy is only available for --mode rkmpp")
    if args.zero_copy and not args.input_codec:
        parser.error("--zero-copy requires at least one --input-codec")
    if args.input_codec and not args.zero_copy:
        parser.error("--input-codec requires --zero-copy")
    report = qualify(
        args.ffmpeg, args.ffprobe, args.mode, args.codec or ["h264"], args.device, args.gpu_index,
        zero_copy_inputs=args.input_codec or None,
    )
    print(json.dumps(report, sort_keys=True))
    outcomes = list(report["codecs"].values())
    outcomes.extend((report.get("zero_copy_pairs") or {}).values())
    return 0 if all(result["ok"] for result in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
