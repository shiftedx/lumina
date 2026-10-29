#!/usr/bin/env python3
"""Create bounded structural Dolby Vision Profile 7 MEL/FEL conversion fixtures.

The tiny RPU vectors come from quietvoid/dovi_tool 2.3.4 and are hash-pinned.
This helper creates eight-second synthetic HEVC base/enhancement layers, muxes a
real enhancement-layer NAL with dovi_tool, then writes a Profile 7/compatibility
6 configuration record. MEL also checks the upstream mode-2 expected vector;
FEL uses the versioned tool's mode-2 output (its upstream vector uses mode 5).
The result exercises metadata and packaging plumbing only:
it does not establish reference colour, FEL residual fidelity, or display output.

Fixtures are deliberately written outside the source tree. No movie bytes are
committed or downloaded; only the upstream project's tiny test RPU vectors are
fetched at their pinned revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen

from generate_dv_fixtures import add_configuration, run


REVISION = "2.3.4"
UPSTREAM = f"https://raw.githubusercontent.com/quietvoid/dovi_tool/{REVISION}/assets/tests"
P7_VECTORS = {
    "mel": {
        "source": "mel_orig.bin",
        "source_sha256": "cabda945d36cd4c3eca9c2df05cf387562e648e3250be28632c9b2991f2ec60b",
        "converted": "mel_to_81.bin",
        "converted_sha256": "2b746d62d5fe9be304a9b598657d4910a403107479df77a54d85d5ebe1e84eba",
    },
    "fel": {
        "source": "fel_orig.bin",
        "source_sha256": "b2b27714b7279c4e24d1a795cb6f95d3ad06745db96d360698ee0932184117a0",
        # Upstream's fel_to_81.bin is mode 5 (mapping preserved), while
        # PrismCore's P7 conversion uses mode 2. Do not compare unlike modes.
    },
}
FRAMES = 192
FRAME_RATE = 24
MAX_VECTOR_BYTES = 64 * 1024


def download(name: str, expected_hash: str) -> bytes:
    with urlopen(f"{UPSTREAM}/{name}", timeout=30) as response:
        data = response.read(MAX_VECTOR_BYTES + 1)
    if len(data) > MAX_VECTOR_BYTES:
        raise ValueError(f"Pinned dovi_tool vector exceeds {MAX_VECTOR_BYTES} bytes: {name}")
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Pinned dovi_tool vector hash mismatch for {name}: {actual_hash}")
    return data


def annexb_nalus(data: bytes) -> list[tuple[int, int, int]]:
    """Return Annex-B NAL start/end/type tuples."""
    starts: list[tuple[int, int]] = []
    index = 0
    while index + 3 < len(data):
        if data[index:index + 3] == b"\0\0\1":
            starts.append((index, 3))
            index += 3
        elif data[index:index + 4] == b"\0\0\0\1":
            starts.append((index, 4))
            index += 4
        else:
            index += 1
    nalus = []
    for index, (start, length) in enumerate(starts):
        if start + length + 1 >= len(data):
            continue
        first = data[start + length]
        end = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        nalus.append((start, end, (first >> 1) & 0x3F))
    return nalus


def annexb_has_enhancement_layer(data: bytes) -> bool:
    """dovi_tool marks a muxed HEVC enhancement layer with NAL type 63."""
    return any(kind == 63 for _, _, kind in annexb_nalus(data))


def corrupt_second_rpu(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Keep frame one valid and make frame two's RPU checksum fail."""
    rpus = [(start, end) for start, end, kind in annexb_nalus(data) if kind == 62]
    if len(rpus) != FRAMES:
        raise ValueError(f"Expected {FRAMES} RPU NALs for negative fixture, found {len(rpus)}")
    start, end = rpus[1]
    if end - start < 8:
        raise ValueError("RPU NAL is unexpectedly short")
    original = data[start:end]
    damaged = bytearray(original)
    damaged[-1] ^= 1
    result = data[:start] + damaged + data[end:]
    return result, original, bytes(damaged)


def dovi_configuration(media: Path) -> dict[str, object]:
    facts = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(media)]))
    video = next(stream for stream in facts["streams"] if stream["codec_type"] == "video")
    return next(side for side in video.get("side_data_list", []) if side.get("side_data_type") == "DOVI configuration record")


def expect_profile7(media: Path) -> None:
    config = dovi_configuration(media)
    expected = {
        "dv_profile": 7,
        "dv_level": 6,
        "dv_bl_signal_compatibility_id": 6,
        "rpu_present_flag": 1,
        "el_present_flag": 1,
        "bl_present_flag": 1,
    }
    actual = {key: config.get(key) for key in expected}
    if actual != expected:
        raise ValueError(f"Generated Profile 7 configuration mismatch: {actual}")


def encode_layer(destination: Path, pattern: str, frames: int) -> None:
    run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", pattern,
        "-frames:v", str(frames), "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "pools=1:frame-threads=1:bframes=0:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1",
        str(destination),
    ])


def video_packet_count(media: Path) -> int:
    return int(run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "default=nw=1:nk=1", str(media),
    ]).decode().strip())


def generate_variant(output: Path, tool: str, kind: str, details: dict[str, str]) -> dict[str, object]:
    source_rpu = download(details["source"], details["source_sha256"])
    expected_rpu = download(details["converted"], details["converted_sha256"]) if "converted" in details else None
    source_path = output / f"p7-{kind}.rpu"
    expected_path = output / f"p81-{kind}.rpu"
    source_path.write_bytes(source_rpu * FRAMES)
    if expected_rpu is not None:
        expected_path.write_bytes(expected_rpu)
    with tempfile.TemporaryDirectory(prefix=f"lumina-p7-{kind}-") as temporary_name:
        temporary = Path(temporary_name)
        base = temporary / "base.hevc"
        enhancement = temporary / "enhancement.hevc"
        enhancement_with_rpu = temporary / "enhancement-rpu.hevc"
        elementary = output / f"p7-{kind}.hevc"
        encode_layer(base, f"testsrc2=size=128x72:rate={FRAME_RATE}:duration=8", FRAMES)
        encode_layer(enhancement, f"testsrc=size=128x72:rate={FRAME_RATE}:duration=8", FRAMES)
        run([tool, "inject-rpu", "-i", str(enhancement), "--rpu-in", str(source_path), "-o", str(enhancement_with_rpu)])
        run([tool, "mux", "--bl", str(base), "--el", str(enhancement_with_rpu), "-o", str(elementary)])
        if not annexb_has_enhancement_layer(elementary.read_bytes()):
            raise ValueError("dovi_tool mux did not produce an enhancement-layer NAL")

        raw_mp4 = temporary / "raw.mp4"
        configured_mp4 = temporary / "p7.mp4"
        mkv = output / f"p7-{kind}.mkv"
        run([
            "ffmpeg", "-v", "error", "-y", "-r", str(FRAME_RATE), "-i", str(elementary),
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-tag:v", "hvc1", str(raw_mp4),
        ])
        configured_mp4.write_bytes(add_configuration(
            raw_mp4.read_bytes(), compatibility=6, profile=7, level=6, enhancement_layer=True
        ))
        run(["ffmpeg", "-v", "error", "-y", "-i", str(configured_mp4), "-map", "0", "-c", "copy", str(mkv)])
        expect_profile7(configured_mp4)
        expect_profile7(mkv)
        if video_packet_count(mkv) != FRAMES:
            raise ValueError(f"Expected {FRAMES} video packets in the generated Profile 7 MKV")

        converted = temporary / "p81.hevc"
        recovered = temporary / "p81.rpu"
        run([tool, "-m", "2", "convert", "--discard", "-i", str(elementary), "-o", str(converted)])
        run([tool, "extract-rpu", "-i", str(converted), "-o", str(recovered)])
        converted_rpu = recovered.read_bytes()
        if expected_rpu is not None and converted_rpu != expected_rpu * FRAMES:
            raise ValueError(f"Profile 7 {kind.upper()} conversion did not match dovi_tool's pinned P8.1 vector")
        expected_path.write_bytes(converted_rpu)

    return {
        "kind": kind,
        "source": {"url": f"{UPSTREAM}/{details['source']}", "sha256": details["source_sha256"]},
        "expectedP81": {
            "file": expected_path.name,
            "sha256": hashlib.sha256(expected_path.read_bytes()).hexdigest(),
            "upstreamVector": expected_rpu is not None,
        },
        "elementary": elementary.name,
        "container": mkv.name,
        "frames": FRAMES,
        "sha256": hashlib.sha256(mkv.read_bytes()).hexdigest(),
        "structuralOnly": True,
    }


def generate_invalid_fixture(output: Path, tool: str) -> dict[str, object]:
    """Create an 8-second P7 MEL stream whose second RPU is malformed.

    Prism publishes conversion statistics on a normal HLS segment cut, not an
    EOF flush. The later valid frames and regular keyframes ensure the first
    six-second cut records both earlier successful rewrites and this refusal.
    """
    details = P7_VECTORS["mel"]
    source_rpu = download(details["source"], details["source_sha256"])
    with tempfile.TemporaryDirectory(prefix="lumina-p7-invalid-") as temporary_name:
        temporary = Path(temporary_name)
        rpus = temporary / "rpus.bin"
        rpus.write_bytes(source_rpu * FRAMES)
        base = temporary / "base.hevc"
        enhancement = temporary / "enhancement.hevc"
        enhancement_with_rpu = temporary / "enhancement-rpu.hevc"
        elementary = temporary / "p7.hevc"
        run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=128x72:rate={FRAME_RATE}:duration={FRAMES / FRAME_RATE}",
            "-frames:v", str(FRAMES), "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "pools=1:frame-threads=1:bframes=0:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1", str(base),
        ])
        run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=128x72:rate={FRAME_RATE}:duration={FRAMES / FRAME_RATE}",
            "-frames:v", str(FRAMES), "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "pools=1:frame-threads=1:bframes=0:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1", str(enhancement),
        ])
        run([tool, "inject-rpu", "-i", str(enhancement), "--rpu-in", str(rpus), "-o", str(enhancement_with_rpu)])
        run([tool, "mux", "--bl", str(base), "--el", str(enhancement_with_rpu), "-o", str(elementary)])
        damaged, _, _ = corrupt_second_rpu(elementary.read_bytes())
        invalid_elementary = temporary / "p7-invalid.hevc"
        invalid_elementary.write_bytes(damaged)
        failed = subprocess.run(
            [tool, "-m", "2", "convert", "--discard", "-i", str(invalid_elementary), "-o", str(temporary / "p81.hevc")],
            capture_output=True, timeout=90,
        )
        if failed.returncode == 0:
            raise ValueError("Corrupted second RPU unexpectedly converted successfully")

        raw_mp4 = temporary / "raw.mp4"
        configured_mp4 = temporary / "p7.mp4"
        invalid_mkv = output / "p7-invalid-rpu.mkv"
        run([
            "ffmpeg", "-v", "error", "-y", "-r", "24", "-i", str(invalid_elementary),
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={FRAMES / FRAME_RATE}",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-tag:v", "hvc1", str(raw_mp4),
        ])
        configured_mp4.write_bytes(add_configuration(
            raw_mp4.read_bytes(), compatibility=6, profile=7, level=6, enhancement_layer=True
        ))
        run(["ffmpeg", "-v", "error", "-y", "-i", str(configured_mp4), "-map", "0", "-c", "copy", str(invalid_mkv)])
        expect_profile7(invalid_mkv)
        invalid_container = subprocess.run(
            [tool, "-m", "2", "convert", "--discard", "-i", str(invalid_mkv), "-o", str(temporary / "container-p81.hevc")],
            capture_output=True, timeout=90,
        )
        if invalid_container.returncode == 0:
            raise ValueError("Container remux unexpectedly repaired the malformed second RPU")
    return {
        "container": invalid_mkv.name,
        "firstRpuValid": True,
        "secondRpuMalformed": True,
        "frames": FRAMES,
        "sha256": hashlib.sha256(invalid_mkv.read_bytes()).hexdigest(),
        "structuralOnly": True,
    }


def generate(output: Path, tool: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "purpose": "Structural Profile 7 MEL/FEL metadata and conversion testing; not reference colour or FEL residual qualification",
        "doviTool": run([tool, "--version"]).decode().strip(),
        "ffmpeg": run(["ffmpeg", "-version"]).decode().splitlines()[0],
        "fixtures": [generate_variant(output, tool, kind, details) for kind, details in P7_VECTORS.items()],
        "invalidRpu": generate_invalid_fixture(output, tool),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dovi-tool", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.output, arguments.dovi_tool)
