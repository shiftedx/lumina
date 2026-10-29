#!/usr/bin/env python3
"""Generate synthetic DV 8.1/8.4 metadata fixtures, never display/color certification.

Requires ffmpeg/libx265 and an explicitly supplied dovi_tool (tested with 2.3.4).
RPU generation format: https://github.com/quietvoid/dovi_tool/blob/2.3.4/docs/generator.md
No executable or sample media is downloaded by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile


def run(args: list[str]) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.decode(errors='replace')[-2000:]}")
    return result.stdout


def add_configuration(data: bytes, compatibility: int) -> bytes:
    """Append the generated RPU's config to our small, moov-last fixture only.

    The mdat stays at its original offset, so chunk offsets remain unchanged.
    DV record field layout is the public DOVIDecoderConfigurationRecord format.
    """
    offsets = {b"moov": 8, b"trak": 8, b"mdia": 8, b"minf": 8, b"stbl": 8, b"stsd": 16}
    patched = 0

    def rewrite(block: bytes, *, top: bool = False) -> bytes:
        nonlocal patched
        output = bytearray()
        position = 0
        seen_media = False
        while position < len(block):
            if position + 8 > len(block):
                raise ValueError("Truncated fixture box")
            size, kind = struct.unpack_from(">I4s", block, position)
            if size < 8 or position + size > len(block):
                raise ValueError("Unsupported fixture box size")
            box = block[position:position + size]
            if top and kind == b"mdat":
                seen_media = True
            if top and kind == b"moov" and not seen_media:
                raise ValueError("Fixture must have moov after mdat")
            if kind in offsets:
                prefix = offsets[kind]
                body = box[8:prefix] + rewrite(box[prefix:])
                box = struct.pack(">I4s", len(body) + 8, kind) + body
            elif kind == b"hvc1":
                # Profile 8, level 1, RPU+base present, enhancement layer absent.
                record = bytes([1, 0, 8 << 1, (1 << 3) | 5, compatibility << 4]) + bytes(19)
                child = struct.pack(">I4s", len(record) + 8, b"dvvC") + record
                box = struct.pack(">I4s", len(box) + len(child), kind) + box[8:] + child
                patched += 1
            output.extend(box)
            position += size
        return bytes(output)

    result = rewrite(data, top=True)
    if patched != 1:
        raise ValueError("Expected exactly one generated HEVC sample entry")
    return result


def generate(output: Path, tool: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "purpose": "Synthetic metadata retention; not graded Dolby Vision content",
        "doviTool": run([tool, "--version"]).decode().strip(),
        "ffmpeg": run(["ffmpeg", "-version"]).decode().splitlines()[0],
        "fixtures": [],
    }
    with tempfile.TemporaryDirectory(prefix="lumina-dv-generate-") as scratch:
        temporary = Path(scratch)
        for compatibility, transfer in [(1, "smpte2084"), (4, "arib-std-b67")]:
            profile = f"8.{compatibility}"
            config = {
                "cm_version": "V40", "profile": profile,
                "level6": {"max_display_mastering_luminance": 1000, "min_display_mastering_luminance": 1,
                           "max_content_light_level": 1000, "max_frame_average_light_level": 400},
                "shots": [{"start": second * 24, "duration": 24,
                           "metadata_blocks": [{"Level1": {"min_pq": 0, "max_pq": 2400 + second * 50,
                                                           "avg_pq": 1000 + second * 20}}]}
                          for second in range(8)],
            }
            configuration = temporary / "rpu.json"
            configuration.write_text(json.dumps(config))
            rpu, base, injected = [temporary / name for name in ("RPU.bin", "base.hevc", "injected.hevc")]
            run([tool, "generate", "-j", str(configuration), "-o", str(rpu)])
            run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=8",
                 "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
                 "-x265-params", "pools=1:frame-threads=1:bframes=0:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1",
                 "-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc", "-color_range", "tv", str(base)])
            run([tool, "inject-rpu", "-i", str(base), "--rpu-in", str(rpu), "-o", str(injected)])
            raw = temporary / "raw.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-r", "24", "-i", str(injected), "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-tag:v", "hvc1", str(raw)])
            mp4 = output / f"dv8-{compatibility}.mp4"
            mp4.write_bytes(add_configuration(raw.read_bytes(), compatibility))
            mkv = mp4.with_suffix(".mkv")
            run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-map", "0", "-c", "copy", str(mkv)])
            for media in (mp4, mkv):
                facts = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(media)]))
                video = next(stream for stream in facts["streams"] if stream["codec_type"] == "video")
                dv = next(side for side in video.get("side_data_list", []) if "dv_profile" in side)
                if (dv["dv_profile"], dv["dv_level"], dv["dv_bl_signal_compatibility_id"],
                    dv["rpu_present_flag"], dv["el_present_flag"], dv["bl_present_flag"]) != (8, 1, compatibility, 1, 0, 1):
                    raise ValueError("Generated DV configuration does not match its RPU")
                extracted = temporary / "extracted.hevc"
                recovered = temporary / "recovered.bin"
                run(["ffmpeg", "-v", "error", "-y", "-i", str(media), "-map", "0:v:0", "-c", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", str(extracted)])
                recovered.unlink(missing_ok=True)
                run([tool, "extract-rpu", "-i", str(extracted), "-o", str(recovered)])
                if recovered.read_bytes() != rpu.read_bytes():
                    raise ValueError("Generated fixture did not retain its original RPU sequence")
                manifest["fixtures"].append({"file": media.name, "sha256": hashlib.sha256(media.read_bytes()).hexdigest(), "dolbyVision": dv})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dovi-tool", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.output, arguments.dovi_tool)
