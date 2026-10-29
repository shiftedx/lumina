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
SEPARATE_SCENE_REFRESH_FRAMES = tuple(range(0, FRAMES, 24))


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


def corrupt_rpu(data: bytes, index: int, frames: int) -> tuple[bytes, bytes, bytes]:
    """Damage exactly one RPU while preserving the dual-layer HEVC carriage."""
    rpus = [(start, end) for start, end, kind in annexb_nalus(data) if kind == 62]
    if len(rpus) != frames:
        raise ValueError(f"Expected {frames} RPU NALs for negative fixture, found {len(rpus)}")
    if not 0 <= index < frames:
        raise ValueError(f"RPU index {index} is outside the {frames}-frame fixture")
    start, end = rpus[index]
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


def encode_timed_layer_mp4(destination: Path, pattern: str, frames: int, *, bframes: int) -> None:
    """Encode directly into MP4 so its distinct decode/presentation order survives."""
    run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", pattern,
        "-frames:v", str(frames), "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params",
        f"pools=1:frame-threads=1:bframes={bframes}:b-adapt=0:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1",
        "-tag:v", "hvc1", str(destination),
    ])


def packet_facts(media: Path, ordinal: int) -> list[dict[str, object]]:
    args = ["ffprobe", "-v", "error", "-select_streams", f"v:{ordinal}", "-show_packets"]
    args.extend(["-of", "json", str(media)])
    return json.loads(run(args))["packets"]


def video_packet_count(media: Path, ordinal: int = 0) -> int:
    return int(run([
        "ffprobe", "-v", "error", "-select_streams", f"v:{ordinal}", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "default=nw=1:nk=1", str(media),
    ]).decode().strip())


def video_packet_timing(media: Path, ordinal: int) -> list[tuple[str | None, str | None, str | None]]:
    return [(packet.get("pts_time"), packet.get("dts_time"), packet.get("duration_time"))
            for packet in packet_facts(media, ordinal)]


def annexb_units(data: bytes) -> list[bytes]:
    return [data[start:end] for start, end, _ in annexb_nalus(data)]


def hevc_stream_bytes(media: Path, ordinal: int, destination: Path) -> bytes:
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(media), "-map", f"0:v:{ordinal}", "-c", "copy",
        "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", str(destination),
    ])
    return destination.read_bytes()


def vcl_nals(data: bytes) -> list[bytes]:
    return [
        data[start + (4 if data[start:start + 4] == b"\0\0\0\1" else 3):end]
        for start, end, kind in annexb_nalus(data) if kind <= 31
    ]


def track_configuration(stream: dict[str, object]) -> dict[str, object]:
    side_data = stream.get("side_data_list", [])
    configuration = next((side for side in side_data if side.get("side_data_type") == "DOVI configuration record"), None)
    if configuration is None:
        raise ValueError("Separate-track fixture lost its DOVI configuration")
    return configuration


def expect_separate_profile7(media: Path, frames: list[int], *, enhancement_offset_seconds: float = 0,
                             mismatched_enhancement: bool = False, reordered_base: bool = False) -> None:
    facts = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(media)]))
    tracks = [stream for stream in facts["streams"] if stream["codec_type"] == "video"]
    if len(tracks) != len(frames):
        raise ValueError(f"Expected {len(frames)} separate HEVC tracks, found {len(tracks)}")
    expected_flags = [(0, 0, 1)] + [(1, 1, 0)] * (len(frames) - 1)
    for ordinal, (stream, packet_count, flags) in enumerate(zip(tracks, frames, expected_flags)):
        configuration = track_configuration(stream)
        actual = (
            configuration.get("dv_profile"), configuration.get("dv_level"),
            configuration.get("dv_bl_signal_compatibility_id"), configuration.get("rpu_present_flag"),
            configuration.get("el_present_flag"), configuration.get("bl_present_flag"),
        )
        expected = (7, 6, 6, *flags)
        if actual != expected:
            raise ValueError(f"Separate HEVC track {ordinal} DOVI configuration mismatch: {actual}")
        if video_packet_count(media, ordinal) != packet_count:
            raise ValueError(f"Separate HEVC track {ordinal} packet count changed")
    base_timing = video_packet_timing(media, 0)
    for ordinal, packet_count in enumerate(frames[1:], start=1):
        timing = video_packet_timing(media, ordinal)
        if len(timing) != packet_count:
            raise ValueError(f"Separate HEVC track {ordinal} timing count changed")
        if (enhancement_offset_seconds or mismatched_enhancement) and ordinal == 1:
            if timing == base_timing:
                raise ValueError("Mismatched enhancement track retained base timestamps")
            if enhancement_offset_seconds:
                observed_offset = float(timing[0][0] or 0) - float(base_timing[0][0] or 0)
                if abs(observed_offset - enhancement_offset_seconds) > 0.002:
                    raise ValueError(f"Enhancement timestamp offset changed: {observed_offset}")
        elif reordered_base and ordinal == 1:
            base_pts = sorted(item[0] for item in base_timing)
            enhancement_pts = [item[0] for item in timing]
            if base_pts != enhancement_pts:
                raise ValueError("Reordered separate HEVC tracks do not retain the same PTS set")
        elif timing != base_timing:
            raise ValueError(f"Separate HEVC track {ordinal} is not timestamp-aligned with the base layer")


def configured_track(temporary: Path, tool: str, name: str, elementary: Path, *, rpu_present: bool,
                     enhancement_layer: bool, base_layer_present: bool) -> Path:
    raw = elementary if elementary.suffix == ".mp4" else temporary / f"{name}-raw.mp4"
    configured = temporary / f"{name}-dv.mp4"
    if raw != elementary:
        run(["ffmpeg", "-v", "error", "-y", "-r", str(FRAME_RATE), "-i", str(elementary),
             "-map", "0:v:0", "-c", "copy", "-tag:v", "hvc1", str(raw)])
    configured.write_bytes(add_configuration(
        raw.read_bytes(), compatibility=6, profile=7, level=6, rpu_present=rpu_present,
        enhancement_layer=enhancement_layer, base_layer_present=base_layer_present,
    ))
    return configured


def mux_separate_tracks(output: Path, temporary: Path, base: Path, enhancements: list[Path], *,
                        enhancement_offset_seconds: float = 0) -> None:
    inputs = ["ffmpeg", "-v", "error", "-y", "-i", str(base)]
    for ordinal, enhancement in enumerate(enhancements):
        if ordinal == 0 and enhancement_offset_seconds:
            inputs.extend(["-itsoffset", str(enhancement_offset_seconds)])
        inputs.extend(["-i", str(enhancement)])
    inputs.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={FRAMES / FRAME_RATE}"])
    audio_input = len(enhancements) + 1
    inputs.extend(["-map", "0:v:0"])
    inputs.extend([argument for ordinal in range(len(enhancements)) for argument in ("-map", f"{ordinal + 1}:v:0")])
    inputs.extend(["-map", f"{audio_input}:a:0", "-c:v", "copy", "-c:a", "aac", "-disposition:v:0", "default"])
    inputs.extend([argument for ordinal in range(len(enhancements)) for argument in (f"-disposition:v:{ordinal + 1}", "0")])
    inputs.append(str(output))
    run(inputs)


def expect_separate_payloads(media: Path, temporary: Path, base: Path, enhancement_frames: list[int]) -> list[Path]:
    delivered_base = hevc_stream_bytes(media, 0, temporary / "delivered-base.hevc")
    if vcl_nals(delivered_base) != vcl_nals(base.read_bytes()):
        raise ValueError("Separate-track fixture changed base-layer picture NALs")
    if any(kind in {62, 63} for _, _, kind in annexb_nalus(delivered_base)):
        raise ValueError("Separate-track base layer unexpectedly carries RPU or EL NALs")
    delivered_enhancements = []
    for ordinal, frames in enumerate(enhancement_frames, start=1):
        destination = temporary / f"delivered-el-{ordinal}.hevc"
        delivered_enhancement = hevc_stream_bytes(media, ordinal, destination)
        delivered_enhancements.append(destination)
        types = [kind for _, _, kind in annexb_nalus(delivered_enhancement)]
        if types.count(62) != frames or 63 in types:
            raise ValueError(f"Separate enhancement track {ordinal} lost RPU NALs or retained muxed EL NALs")
    return delivered_enhancements


def expect_enhancement_rpus(paths: list[Path], temporary: Path, tool: str, expected_rpus: bytes | None, *,
                            malformed_final_rpu: bool) -> None:
    for ordinal, path in enumerate(paths, start=1):
        recovered = temporary / f"delivered-el-{ordinal}.rpu"
        if malformed_final_rpu:
            failed = subprocess.run(
                [tool, "-m", "2", "convert", "--discard", "-i", str(path), "-o", str(temporary / f"bad-{ordinal}.hevc")],
                capture_output=True, timeout=90,
            )
            if failed.returncode == 0:
                raise ValueError("Container remux repaired a malformed separate-track enhancement RPU")
        else:
            if expected_rpus is None:
                raise ValueError("Expected separate-track RPU payloads are required")
            run([tool, "extract-rpu", "-i", str(path), "-o", str(recovered)])
            if recovered.read_bytes() != expected_rpus:
                raise ValueError("Separate enhancement track changed its ordered RPU payloads")


def expect_reordered_pair(media: Path, temporary: Path, tool: str, base_elementary: Path,
                          expected_p81: Path) -> None:
    """Prove PTS association rather than accidentally accepting EL decode order."""
    base_packets = packet_facts(media, 0)
    enhancement_packets = packet_facts(media, 1)
    base_pts = [int(packet["pts"]) for packet in base_packets]
    enhancement_pts = [int(packet["pts"]) for packet in enhancement_packets]
    if (set(base_pts) != set(enhancement_pts) or len(base_pts) != FRAMES
            or len(set(base_pts)) != len(base_pts) or len(set(enhancement_pts)) != len(enhancement_pts)):
        raise ValueError("Reordered separate tracks do not retain identical PTS sets")
    if base_pts == sorted(base_pts) or enhancement_pts != sorted(enhancement_pts):
        raise ValueError("Reordered fixture did not retain BL decode order distinct from EL PTS order")
    if not any(
        packet.get("dts") not in {None, "N/A"} and packet.get("pts") != packet.get("dts")
        for packet in base_packets
    ):
        raise ValueError("Reordered base track has no distinct DTS")

    source_base = hevc_stream_bytes(media, 0, temporary / "reordered-delivered-base.hevc")
    if vcl_nals(source_base) != vcl_nals(base_elementary.read_bytes()):
        raise ValueError("Reordered fixture changed base-layer picture NALs")
    if any(kind in {62, 63} for _, _, kind in annexb_nalus(source_base)):
        raise ValueError("Reordered fixture base layer carries RPU or EL NALs")

    enhancement = temporary / "reordered-delivered-el.hevc"
    hevc_stream_bytes(media, 1, enhancement)
    converted = mode2_rpus(temporary, tool, enhancement)
    # `extract-rpu` writes standalone Dolby RPU NALs whose first header byte
    # is not the HEVC type-62 framing used in a full access unit. Preserve its
    # Annex-B units verbatim: this is the public expected-vector file format.
    converted_units = annexb_units(converted)
    if len(converted_units) != len(enhancement_packets):
        raise ValueError("Converted reordered EL does not contain one RPU per packet")
    expected = b"".join(dict(zip(enhancement_pts, converted_units))[pts] for pts in base_pts)
    expected_p81.write_bytes(expected)
    if expected == b"".join(converted_units):
        raise ValueError("Reordered expected sequence accidentally follows EL decode order")

    audio = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets", "-of", "json", str(media),
    ]))["packets"]
    audio_dts = [int(packet["dts"]) for packet in audio if "dts" in packet]
    if len(audio_dts) < 2 or audio_dts != sorted(audio_dts):
        raise ValueError("Reordered fixture AAC DTS is not monotonic")


def scene_refreshed_rpus(temporary: Path, tool: str, source_rpu: bytes, frames: int) -> bytes:
    """Use dovi_tool's pinned editor grammar to make frame association observable."""
    original = temporary / "rpus-original.bin"
    configuration = temporary / "scene-cuts.json"
    edited = temporary / "rpus-varied.bin"
    scenes = temporary / "scene-cuts.txt"
    original.write_bytes(source_rpu * frames)
    scene_frames = [frame for frame in SEPARATE_SCENE_REFRESH_FRAMES if frame < frames]
    configuration.write_text(json.dumps({
        "scene_cuts": {"all": False, **{f"{frame}-{frame}": True for frame in scene_frames}},
    }))
    run([tool, "editor", "-i", str(original), "-j", str(configuration), "-o", str(edited)])
    run([tool, "export", "-i", str(edited), "-d", f"scenes={scenes}"])
    if scenes.read_text().splitlines() != [str(frame) for frame in scene_frames] or edited.read_bytes() == original.read_bytes():
        raise ValueError("dovi_tool scene-cut edit did not make the RPU sequence observable")
    return edited.read_bytes()


def mode2_rpus(temporary: Path, tool: str, enhancement: Path) -> bytes:
    converted = temporary / "el-p81.hevc"
    recovered = temporary / "el-p81.rpu"
    run([tool, "-m", "2", "convert", "--discard", "-i", str(enhancement), "-o", str(converted)])
    run([tool, "extract-rpu", "-i", str(converted), "-o", str(recovered)])
    return recovered.read_bytes()


def separate_layers(temporary: Path, tool: str, source_rpu: bytes, *, enhancement_frames: int,
                    varied_rpus: bool = False, malformed_final_rpu: bool = False) -> tuple[Path, Path, bytes]:
    base = temporary / "bl.hevc"
    enhancement = temporary / "el.hevc"
    injected = temporary / "el-rpu.hevc"
    rpus = temporary / "rpus.bin"
    encode_layer(base, f"testsrc2=size=128x72:rate={FRAME_RATE}:duration={FRAMES / FRAME_RATE}", FRAMES)
    encode_layer(enhancement, f"testsrc=size=128x72:rate={FRAME_RATE}:duration={enhancement_frames / FRAME_RATE}", enhancement_frames)
    sequence = scene_refreshed_rpus(temporary, tool, source_rpu, enhancement_frames) if varied_rpus else source_rpu * enhancement_frames
    rpus.write_bytes(sequence)
    run([tool, "inject-rpu", "-i", str(enhancement), "--rpu-in", str(rpus), "-o", str(injected)])
    if malformed_final_rpu:
        damaged, _, _ = corrupt_rpu(injected.read_bytes(), enhancement_frames - 1, enhancement_frames)
        injected.write_bytes(damaged)
        failed = subprocess.run(
            [tool, "-m", "2", "convert", "--discard", "-i", str(injected), "-o", str(temporary / "bad-p81.hevc")],
            capture_output=True, timeout=90,
        )
        if failed.returncode == 0:
            raise ValueError("Separate enhancement track accepted its malformed final RPU")
    return base, injected, sequence


def generate_separate_track_fixture(
    output: Path,
    tool: str,
    *,
    kind: str,
    source_rpu: bytes,
    container_name: str,
    enhancement_frames: int = FRAMES,
    enhancement_offset_seconds: float = 0,
    extra_enhancement: bool = False,
    varied_rpus: bool = False,
    expected_p81_name: str | None = None,
    malformed_final_rpu: bool = False,
) -> dict[str, object]:
    """Create a structural two-track MKV; it deliberately claims no Matroska DV association."""
    with tempfile.TemporaryDirectory(prefix=f"lumina-p7-separate-{kind}-") as temporary_name:
        temporary = Path(temporary_name)
        base, enhancement, sequence = separate_layers(
            temporary, tool, source_rpu, enhancement_frames=enhancement_frames,
            varied_rpus=varied_rpus,
            malformed_final_rpu=malformed_final_rpu,
        )
        base_track = configured_track(
            temporary, tool, "bl", base, rpu_present=False, enhancement_layer=False, base_layer_present=True,
        )
        enhancement_track = configured_track(
            temporary, tool, "el", enhancement, rpu_present=True, enhancement_layer=True, base_layer_present=False,
        )
        enhancements = [enhancement_track]
        if extra_enhancement:
            duplicate = temporary / "el-duplicate-dv.mp4"
            duplicate.write_bytes(enhancement_track.read_bytes())
            enhancements.append(duplicate)
        mkv = output / container_name
        mux_separate_tracks(mkv, temporary, base_track, enhancements, enhancement_offset_seconds=enhancement_offset_seconds)
        frames = [FRAMES] + [enhancement_frames] * len(enhancements)
        expect_separate_profile7(
            mkv, frames, enhancement_offset_seconds=enhancement_offset_seconds,
            mismatched_enhancement=enhancement_frames != FRAMES,
        )
        delivered_enhancements = expect_separate_payloads(mkv, temporary, base, [enhancement_frames] * len(enhancements))
        expected_rpus = None if malformed_final_rpu else mode2_rpus(temporary, tool, enhancement)
        expect_enhancement_rpus(
            delivered_enhancements, temporary, tool, None if malformed_final_rpu else sequence,
            malformed_final_rpu=malformed_final_rpu,
        )
        expected_p81 = None
        if expected_p81_name:
            if expected_rpus is None:
                raise ValueError("Malformed separate-track fixture cannot have an expected P8.1 payload")
            expected_path = output / expected_p81_name
            expected_path.write_bytes(expected_rpus)
            expected_p81 = {"file": expected_path.name, "sha256": hashlib.sha256(expected_rpus).hexdigest()}
    return {
        "kind": kind,
        "container": mkv.name,
        "frames": {"base": FRAMES, "enhancement": [enhancement_frames] * len(enhancements)},
        "dolbyVisionTracks": {
            "base": {"profile": 7, "level": 6, "compatibilityID": 6, "rpuPresent": False, "elPresent": False, "blPresent": True},
            "enhancement": {"profile": 7, "level": 6, "compatibilityID": 6, "rpuPresent": True, "elPresent": True, "blPresent": False},
        },
        "enhancementOffsetSeconds": enhancement_offset_seconds,
        "extraEnhancementTracks": len(enhancements) - 1,
        "sceneRefreshFrames": list(SEPARATE_SCENE_REFRESH_FRAMES) if varied_rpus else [],
        "malformedFinalEnhancementRPU": malformed_final_rpu,
        "structuralOnly": True,
        "noMatroskaAssociationClaim": True,
        "sha256": hashlib.sha256(mkv.read_bytes()).hexdigest(),
        "expectedP81": expected_p81,
    }


def generate_reordered_separate_track_fixture(output: Path, tool: str, source_rpu: bytes) -> dict[str, object]:
    """Make a P7 pair whose BL decode order is intentionally unlike its EL."""
    with tempfile.TemporaryDirectory(prefix="lumina-p7-separate-reordered-") as temporary_name:
        temporary = Path(temporary_name)
        base_mp4 = temporary / "bl-timed.mp4"
        base_elementary = temporary / "bl.hevc"
        enhancement = temporary / "el.hevc"
        injected = temporary / "el-rpu.hevc"
        rpus = temporary / "rpus-varied.bin"

        # BL is encoded directly into MP4 so its B-frame DTS/PTS relation is
        # not reconstructed from a raw elementary stream. EL remains no-B-frame
        # so its packets are presentation ordered; both retain the same PTS set.
        encode_timed_layer_mp4(
            base_mp4, f"testsrc2=size=128x72:rate={FRAME_RATE}:duration={FRAMES / FRAME_RATE}", FRAMES, bframes=2,
        )
        base_elementary.write_bytes(hevc_stream_bytes(base_mp4, 0, temporary / "bl-source.hevc"))
        encode_layer(enhancement, f"testsrc=size=128x72:rate={FRAME_RATE}:duration={FRAMES / FRAME_RATE}", FRAMES)
        rpus.write_bytes(scene_refreshed_rpus(temporary, tool, source_rpu, FRAMES))
        run([tool, "inject-rpu", "-i", str(enhancement), "--rpu-in", str(rpus), "-o", str(injected)])

        base_track = configured_track(
            temporary, tool, "bl", base_mp4, rpu_present=False, enhancement_layer=False, base_layer_present=True,
        )
        enhancement_track = configured_track(
            temporary, tool, "el", injected, rpu_present=True, enhancement_layer=True, base_layer_present=False,
        )
        mkv = output / "p7-separate-reordered-mel.mkv"
        mux_separate_tracks(mkv, temporary, base_track, [enhancement_track])
        expect_separate_profile7(mkv, [FRAMES, FRAMES], reordered_base=True)
        delivered_enhancement = expect_separate_payloads(mkv, temporary, base_elementary, [FRAMES])[0]
        expect_enhancement_rpus([delivered_enhancement], temporary, tool, rpus.read_bytes(), malformed_final_rpu=False)
        expected_p81 = output / "p81-separate-reordered-mel.rpu"
        expect_reordered_pair(mkv, temporary, tool, base_elementary, expected_p81)

    return {
        "kind": "reordered-mel",
        "container": mkv.name,
        "frames": {"base": FRAMES, "enhancement": [FRAMES]},
        "dolbyVisionTracks": {
            "base": {"profile": 7, "level": 6, "compatibilityID": 6, "rpuPresent": False, "elPresent": False, "blPresent": True},
            "enhancement": {"profile": 7, "level": 6, "compatibilityID": 6, "rpuPresent": True, "elPresent": True, "blPresent": False},
        },
        "sceneRefreshFrames": list(SEPARATE_SCENE_REFRESH_FRAMES),
        "baseBFrames": 2,
        "expectedP81": {"file": expected_p81.name, "sha256": hashlib.sha256(expected_p81.read_bytes()).hexdigest()},
        "structuralOnly": True,
        "noMatroskaAssociationClaim": True,
        "sha256": hashlib.sha256(mkv.read_bytes()).hexdigest(),
    }


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


def generate_invalid_fixture(
    output: Path,
    tool: str,
    *,
    container_name: str,
    frames: int,
    malformed_rpu_index: int,
    phase: str,
) -> dict[str, object]:
    """Create a P7 MEL stream with one refused RPU at a named lifecycle phase."""
    details = P7_VECTORS["mel"]
    source_rpu = download(details["source"], details["source_sha256"])
    with tempfile.TemporaryDirectory(prefix="lumina-p7-invalid-") as temporary_name:
        temporary = Path(temporary_name)
        rpus = temporary / "rpus.bin"
        rpus.write_bytes(source_rpu * frames)
        base = temporary / "base.hevc"
        enhancement = temporary / "enhancement.hevc"
        enhancement_with_rpu = temporary / "enhancement-rpu.hevc"
        elementary = temporary / "p7.hevc"
        duration = frames / FRAME_RATE
        encode_layer(base, f"testsrc2=size=128x72:rate={FRAME_RATE}:duration={duration}", frames)
        encode_layer(enhancement, f"testsrc=size=128x72:rate={FRAME_RATE}:duration={duration}", frames)
        run([tool, "inject-rpu", "-i", str(enhancement), "--rpu-in", str(rpus), "-o", str(enhancement_with_rpu)])
        run([tool, "mux", "--bl", str(base), "--el", str(enhancement_with_rpu), "-o", str(elementary)])
        source_elementary = elementary.read_bytes()
        damaged, _, _ = corrupt_rpu(source_elementary, malformed_rpu_index, frames)
        source_rpus = [(start, end) for start, end, kind in annexb_nalus(source_elementary) if kind == 62]
        damaged_rpus = [(start, end) for start, end, kind in annexb_nalus(damaged) if kind == 62]
        changed_rpus = [
            index for index, ((source_start, source_end), (damaged_start, damaged_end)) in enumerate(zip(source_rpus, damaged_rpus))
            if source_elementary[source_start:source_end] != damaged[damaged_start:damaged_end]
        ]
        if len(damaged_rpus) != frames or changed_rpus != [malformed_rpu_index]:
            raise ValueError(f"Expected only RPU {malformed_rpu_index + 1} to change, found {changed_rpus}")
        invalid_elementary = temporary / "p7-invalid.hevc"
        invalid_elementary.write_bytes(damaged)
        failed = subprocess.run(
            [tool, "-m", "2", "convert", "--discard", "-i", str(invalid_elementary), "-o", str(temporary / "p81.hevc")],
            capture_output=True, timeout=90,
        )
        if failed.returncode == 0:
            raise ValueError(f"Corrupted {phase} RPU unexpectedly converted successfully")

        raw_mp4 = temporary / "raw.mp4"
        configured_mp4 = temporary / "p7.mp4"
        invalid_mkv = output / container_name
        run([
            "ffmpeg", "-v", "error", "-y", "-r", "24", "-i", str(invalid_elementary),
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-tag:v", "hvc1", str(raw_mp4),
        ])
        configured_mp4.write_bytes(add_configuration(
            raw_mp4.read_bytes(), compatibility=6, profile=7, level=6, enhancement_layer=True
        ))
        run(["ffmpeg", "-v", "error", "-y", "-i", str(configured_mp4), "-map", "0", "-c", "copy", str(invalid_mkv)])
        expect_profile7(configured_mp4)
        expect_profile7(invalid_mkv)
        if video_packet_count(invalid_mkv) != frames:
            raise ValueError(f"Expected {frames} video packets in {container_name}")
        invalid_container = subprocess.run(
            [tool, "-m", "2", "convert", "--discard", "-i", str(invalid_mkv), "-o", str(temporary / "container-p81.hevc")],
            capture_output=True, timeout=90,
        )
        if invalid_container.returncode == 0:
            raise ValueError(f"Container remux unexpectedly repaired the malformed {phase} RPU")
    return {
        "container": invalid_mkv.name,
        "phase": phase,
        "firstRpuValid": True,
        "malformedRpuIndex": malformed_rpu_index + 1,
        "unchangedRPUsBeforeMalformed": malformed_rpu_index,
        "secondRpuMalformed": malformed_rpu_index == 1,
        "lastRpuMalformed": malformed_rpu_index == frames - 1,
        "frames": frames,
        "sha256": hashlib.sha256(invalid_mkv.read_bytes()).hexdigest(),
        "structuralOnly": True,
    }


def generate(output: Path, tool: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    variants = [generate_variant(output, tool, kind, details) for kind, details in P7_VECTORS.items()]
    sources = {kind: download(details["source"], details["source_sha256"]) for kind, details in P7_VECTORS.items()}
    separate_positives = [
        generate_separate_track_fixture(
            output, tool, kind=kind, source_rpu=source, container_name=f"p7-separate-{kind}.mkv",
            varied_rpus=True, expected_p81_name=f"p81-separate-{kind}.rpu",
        )
        for kind, source in sources.items()
    ]
    separate_reordered = generate_reordered_separate_track_fixture(output, tool, sources["mel"])
    separate_negatives = [
        generate_separate_track_fixture(
            output, tool, kind="malformed-final-rpu", source_rpu=sources["mel"],
            container_name="p7-separate-invalid-final-rpu.mkv", malformed_final_rpu=True,
        ),
        generate_separate_track_fixture(
            output, tool, kind="missing-el-au", source_rpu=sources["mel"],
            container_name="p7-separate-missing-el-au.mkv", enhancement_frames=FRAMES - 1,
        ),
        generate_separate_track_fixture(
            output, tool, kind="shifted-el", source_rpu=sources["mel"],
            container_name="p7-separate-shifted-el.mkv", enhancement_offset_seconds=1 / FRAME_RATE,
        ),
        generate_separate_track_fixture(
            output, tool, kind="ambiguous-el", source_rpu=sources["mel"],
            container_name="p7-separate-ambiguous-el.mkv", extra_enhancement=True,
        ),
    ]
    early_invalid = generate_invalid_fixture(
        output, tool,
        container_name="p7-invalid-rpu.mkv",
        frames=FRAMES,
        malformed_rpu_index=1,
        phase="early",
    )
    final_invalid = generate_invalid_fixture(
        output, tool,
        container_name="p7-invalid-final-rpu.mkv",
        frames=FRAMES,
        malformed_rpu_index=FRAMES - 1,
        phase="final-fragment",
    )
    eof_invalid = generate_invalid_fixture(
        output, tool,
        container_name="p7-invalid-eof-rpu.mkv",
        frames=2,
        malformed_rpu_index=1,
        phase="short-eof",
    )
    manifest = {
        "purpose": "Structural Profile 7 MEL/FEL metadata and conversion testing; not reference colour or FEL residual qualification",
        "doviTool": run([tool, "--version"]).decode().strip(),
        "ffmpeg": run(["ffmpeg", "-version"]).decode().splitlines()[0],
        "fixtures": variants,
        "separateTrackFixtures": separate_positives,
        "separateTrackReordered": separate_reordered,
        "separateTrackNegatives": separate_negatives,
        # Retained name for existing early-failure consumers.
        "invalidRpu": early_invalid,
        "invalidFinalRpu": final_invalid,
        "invalidEOFRpu": eof_invalid,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def generate_reordered_only(output: Path, tool: str) -> None:
    """Build just the B-frame qualification corpus without rewriting other fixtures."""
    output.mkdir(parents=True, exist_ok=True)
    fixture = generate_reordered_separate_track_fixture(output, tool, download(
        P7_VECTORS["mel"]["source"], P7_VECTORS["mel"]["source_sha256"]
    ))
    (output / "manifest.json").write_text(json.dumps({
        "purpose": "Structural separate-track P7 PTS association with a B-frame BL; not reference colour",
        "doviTool": run([tool, "--version"]).decode().strip(),
        "ffmpeg": run(["ffmpeg", "-version"]).decode().splitlines()[0],
        "separateTrackReordered": fixture,
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dovi-tool", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reordered-only", action="store_true",
                        help="Generate only the separate-track B-frame ordering corpus")
    arguments = parser.parse_args()
    (generate_reordered_only if arguments.reordered_only else generate)(arguments.output, arguments.dovi_tool)
