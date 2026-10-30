"""Ffprobe facts carry a per-stream list and an explicit cache version."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event

import pytest

from app.services import media_probe
from app.services.media_probe import PROBE_CACHE_VERSION, is_stale, normalize, run_ffprobe
from app.services.mpegts_dolby_vision import parse_m2ts_dolby_vision_pair
from app.services.m2ts_irap_index import M2TSIRAPIndexes, scan, source_fingerprint
from app.config import settings

RAW = {
    "format": {"format_name": "matroska,webm", "duration": "5400.5", "bit_rate": "24000000"},
    "chapters": [
        {"start_time": "100", "end_time": "200", "tags": {"title": " Middle "}},
        {"start_time": "0", "end_time": "100", "tags": {"title": "Opening"}},
        {"start_time": "-1", "end_time": "0", "tags": {"title": "Ignored"}},
        {"start_time": "5400.5", "tags": {"title": "Past end"}},
    ],
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "level": 153, "width": 3840, "height": 2160,
         "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "color_primaries": "bt2020", "color_space": "bt2020nc", "color_range": "tv", "avg_frame_rate": "24000/1001", "disposition": {"default": 1},
         "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_level": 9,
                              "rpu_present_flag": 1, "el_present_flag": 0, "bl_present_flag": 1,
                              "dv_bl_signal_compatibility_id": 1}]},
        {"index": 1, "codec_type": "audio", "codec_name": "truehd", "channels": 8, "channel_layout": "7.1", "sample_rate": "48000",
         "tags": {"language": "eng", "title": "Atmos"}, "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "bit_rate": "192000", "tags": {"language": "und"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}, "disposition": {"forced": 1}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "spa"}, "disposition": {"hearing_impaired": 1}},
        {"index": 5, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        {"index": 6, "codec_type": "attachment", "codec_name": "ttf"},
        {"index": "x", "codec_type": "audio", "codec_name": "ac3"},
        "not-a-dict",
    ],
}


def _mpeg_crc(section: bytes) -> bytes:
    crc = 0xFFFFFFFF
    for byte in section:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc.to_bytes(4, "big")


def _section(table: int, body: bytes) -> bytes:
    payload = bytes([table, 0xB0 | ((len(body) + 4) >> 8), (len(body) + 4) & 0xFF]) + body
    return payload + _mpeg_crc(payload)


def _psi_packet(pid: int, section: bytes, *, continuity: int = 0) -> bytes:
    ts = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10 | continuity, 0]) + section
    return b"\0\0\0\0" + ts.ljust(188, b"\xff")


def _payload_packet(pid: int, payload: bytes, *, continuity: int, starts: bool) -> bytes:
    """One M2TS packet whose adaptation field bounds this exact payload."""
    if len(payload) > 184:
        raise ValueError("payload exceeds one TS packet")
    header = bytes([0x47, (0x40 if starts else 0) | (pid >> 8), pid & 0xFF])
    if len(payload) == 184:
        return b"\0\0\0\0" + header + bytes([0x10 | continuity]) + payload
    adaptation_length = 183 - len(payload)
    return b"\0\0\0\0" + header + bytes([0x30 | continuity, adaptation_length]) + b"\0" * adaptation_length + payload


def _null_packet(*, continuity: int = 0) -> bytes:
    return b"\0\0\0\0" + bytes([0x47, 0x1F, 0xFF, 0x10 | continuity]) + b"\xff" * 184


def _bad_adaptation_packet(pid: int) -> bytes:
    # The 184-byte adaptation field exceeds the TS payload by one byte.
    return b"\0\0\0\0" + bytes([0x47, pid >> 8, pid & 0xFF, 0x30, 184]) + b"\0" * 183


def _pes_pts(value: int) -> bytes:
    return bytes([0x21 | ((value >> 29) & 0x0E), (value >> 22) & 0xFF, 1 | ((value >> 14) & 0xFE), (value >> 7) & 0xFF, 1 | ((value << 1) & 0xFE)])


def _pes(value: int, body: bytes) -> bytes:
    return b"\0\0\1\xe0\0\0\x80\x80\x05" + _pes_pts(value) + body


def _dovi_m2ts(*, corrupt_crc: bool = False, discontinuity: bool = False, bad_dependency: bool = False, null_packets: bool = False) -> bytes:
    pmt_pid, base, enhancement = 0x1000, 0x1011, 0x102f
    pat = _section(0, b"\0\x01\xc1\0\0\0\x01" + bytes([0xE0 | (pmt_pid >> 8), pmt_pid & 0xFF]))
    hevc = b"\x38\x0d" + b"\0" * 13
    dependency = (base + (1 if bad_dependency else 0)) << 3 | 7
    dovi = b"\xb0\x07\x01\0\x0e\x36" + dependency.to_bytes(2, "big") + b"\x6f"
    entries = (
        bytes([0x24, 0xE0 | (base >> 8), base & 0xFF, 0xF0, len(hevc)]) + hevc
        + bytes([0x06, 0xE0 | (enhancement >> 8), enhancement & 0xFF, 0xF0, 6 + len(hevc) + len(dovi)])
        + b"\x05\x04DOVI" + hevc + dovi
    )
    pmt = _section(2, b"\0\x01\xc1\0\0" + bytes([0xE0 | (base >> 8), base & 0xFF, 0xF0, 0]) + entries)
    if corrupt_crc:
        pmt = pmt[:-1] + bytes([pmt[-1] ^ 1])
    packets = _psi_packet(0, pat) + _psi_packet(pmt_pid, pmt)
    if discontinuity:
        packets += _psi_packet(0, pat, continuity=2)
    if null_packets:
        # Null PID continuity is explicitly unconstrained by MPEG-TS.
        packets += _null_packet(continuity=0) + _null_packet(continuity=0)
    return packets


@pytest.mark.parametrize("invalid", ["corrupt_crc", "discontinuity", "bad_dependency"])
def test_m2ts_dolby_vision_pair_requires_crc_continuity_and_explicit_dependency(invalid: str) -> None:
    kwargs = {invalid: True}
    assert parse_m2ts_dolby_vision_pair(_dovi_m2ts(**kwargs)) is None


def test_m2ts_dolby_vision_pair_reads_nonadjacent_explicit_pids() -> None:
    pair = parse_m2ts_dolby_vision_pair(_dovi_m2ts())

    assert pair is not None
    assert (pair.program_number, pair.pmt_pid, pair.base_pid, pair.enhancement_pid, pair.dv_profile, pair.dv_level, pair.dv_compat) == (1, 0x1000, 0x1011, 0x102f, 7, 6, 6)


def test_m2ts_irap_index_requires_real_base_irap_and_exact_el_pts(tmp_path: Path) -> None:
    pair_bytes = _dovi_m2ts()
    pair = parse_m2ts_dolby_vision_pair(pair_bytes)
    assert pair is not None
    # EL first proves the index chooses the earliest exact-PTS PES offset.
    source = pair_bytes + _payload_packet(pair.enhancement_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True)
    source += _payload_packet(pair.base_pid, _pes(90_000, b"\0\0\1\x26\x01"), continuity=0, starts=True)
    path = tmp_path / "pair.m2ts"
    path.write_bytes(source)

    index = scan(path, pair)

    assert index is not None
    assert index.keyframe_pts == [90_000]
    assert index.packet_offsets == [len(pair_bytes)]
    # A non-key base NAL cannot be promoted from a timestamp/PCR-like anchor.
    path.write_bytes(pair_bytes + _payload_packet(pair.enhancement_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True)
                     + _payload_packet(pair.base_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True))
    assert scan(path, pair) is None


def test_m2ts_irap_index_accepts_a_pes_header_split_across_ts_packets(tmp_path: Path) -> None:
    pair_bytes = _dovi_m2ts()
    pair = parse_m2ts_dolby_vision_pair(pair_bytes)
    assert pair is not None
    base = _pes(90_000, b"\0\0\1\x26\x01")
    path = tmp_path / "split-pes.m2ts"
    path.write_bytes(
        pair_bytes
        + _payload_packet(pair.enhancement_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True)
        + _payload_packet(pair.base_pid, base[:8], continuity=0, starts=True)
        + _payload_packet(pair.base_pid, base[8:], continuity=1, starts=False)
    )
    assert (index := scan(path, pair)) is not None
    assert (index.keyframe_pts, index.packet_offsets) == ([90_000], [len(pair_bytes)])


def test_m2ts_irap_index_rejects_a_trailing_incomplete_enhancement_pes(tmp_path: Path) -> None:
    pair_bytes = _dovi_m2ts()
    pair = parse_m2ts_dolby_vision_pair(pair_bytes)
    assert pair is not None
    path = tmp_path / "trailing-el-pes.m2ts"
    path.write_bytes(
        pair_bytes
        + _payload_packet(pair.enhancement_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True)
        + _payload_packet(pair.base_pid, _pes(90_000, b"\0\0\1\x26\x01"), continuity=0, starts=True)
        + _payload_packet(pair.enhancement_pid, b"\0\0\1\xe0\0\0\x80\x80", continuity=1, starts=True)
    )
    assert scan(path, pair) is None


def test_m2ts_irap_index_cache_reuses_only_current_complete_source(tmp_path: Path) -> None:
    pair_bytes = _dovi_m2ts()
    pair = parse_m2ts_dolby_vision_pair(pair_bytes)
    assert pair is not None
    path = tmp_path / "pair.m2ts"
    path.write_bytes(pair_bytes + _payload_packet(pair.enhancement_pid, _pes(90_000, b"\0\0\1\x02\x01"), continuity=0, starts=True)
                     + _payload_packet(pair.base_pid, _pes(90_000, b"\0\0\1\x26\x01"), continuity=0, starts=True))
    index = scan(path, pair)
    assert index is not None
    cache = M2TSIRAPIndexes._file("source")
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(asdict(index)))
    indexes = M2TSIRAPIndexes()
    assert indexes.get(path, "source") == index
    path.write_bytes(path.read_bytes() + b"x")
    assert indexes.get(path, "source") is None
    cache.write_text(json.dumps({"status": "unsupported", "fingerprint": source_fingerprint(path)}))
    assert indexes.request_status(path, pair, "source")[0] == "unsupported"
    path.write_bytes(path.read_bytes() + b"x")
    assert indexes.request_status(path, pair, "source")[0] == "pending"
    # Cancellation cannot return a partial map that a later request might use.
    stopped = Event(); stopped.set()
    assert scan(path, pair, stopped) is None


def test_m2ts_irap_index_persist_failure_becomes_bounded_terminal_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pair.m2ts"
    path.write_bytes(_dovi_m2ts())
    pair = parse_m2ts_dolby_vision_pair(path.read_bytes())
    assert pair is not None
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x")
    monkeypatch.setattr(settings, "data_dir", blocked)
    indexes = M2TSIRAPIndexes()
    indexes.start()
    try:
        assert indexes.request_status(path, pair, "source")[0] == "pending"
        for _ in range(100):
            if indexes.request_status(path, pair, "source")[0] == "unsupported":
                break
            time.sleep(0.01)
        assert indexes.request_status(path, pair, "source")[0] == "unsupported"
        assert not indexes._queue
    finally:
        indexes.stop()


def test_m2ts_irap_index_matches_the_complete_variable_gop_oracle() -> None:
    root = os.environ.get("LUMINA_DV_M2TS_FIXTURES")
    if not root:
        pytest.skip("generated M2TS corpus is not configured")
    fixture_root = Path(root)
    manifest = json.loads((fixture_root / "manifest.json").read_text())
    fixture = fixture_root / manifest["variableGOPColdSeek"]["fixture"]["container"]
    with fixture.open("rb") as source:
        pair = parse_m2ts_dolby_vision_pair(source.read(4 * 1024 * 1024))
    assert pair is not None
    index = scan(fixture, pair)
    assert index is not None
    oracle = manifest["variableGOPColdSeek"]["fixture"]["suppliedSeekIndex"]
    assert (index.keyframe_pts, index.packet_offsets) == (oracle["KeyframePTS"], oracle["PacketOffsets"])


@pytest.mark.parametrize("filename", ["p7-m2ts-mel.m2ts", "p7-m2ts-nonconventional-mel.m2ts"])
def test_m2ts_irap_index_uses_pes_starts_for_bl_and_el_first(filename: str) -> None:
    root = os.environ.get("LUMINA_DV_M2TS_FIXTURES")
    if not root:
        pytest.skip("generated M2TS corpus is not configured")
    fixture = Path(root) / filename
    with fixture.open("rb") as source:
        pair = parse_m2ts_dolby_vision_pair(source.read(4 * 1024 * 1024))
    assert pair is not None
    index = scan(fixture, pair)
    assert index is not None
    assert index.keyframe_pts == [7500, 97500, 187500, 277500, 367500, 457500, 547500, 637500]
    assert index.packet_offsets == [576, 55488, 119808, 184512, 245568, 309888, 375552, 441216]


def test_m2ts_dolby_vision_pair_accepts_legal_null_counters_and_delivers_a_pmt_completed_at_pointer() -> None:
    source = _dovi_m2ts(null_packets=True)
    pat = source[:192]
    pmt_packet = source[192:384]
    section_start = 4 + 4 + 1  # M2TS prefix, TS header, PSI pointer
    pmt_length = 3 + ((pmt_packet[section_start + 1] & 0x0F) << 8 | pmt_packet[section_start + 2])
    pmt = pmt_packet[section_start:section_start + pmt_length]
    cut = 11
    pmt_pid = 0x1000
    split = (
        _payload_packet(pmt_pid, b"\0" + pmt[:cut], continuity=0, starts=True)
        + _payload_packet(pmt_pid, bytes([len(pmt) - cut]) + pmt[cut:], continuity=1, starts=True)
    )

    pair = parse_m2ts_dolby_vision_pair(pat + split + source[384:])

    assert pair is not None
    assert (pair.base_pid, pair.enhancement_pid) == (0x1011, 0x102F)


@pytest.mark.parametrize("mutate", [
    lambda source: source[:192 + 5] + bytes([source[192 + 5] | 0x80]) + source[192 + 6:],
    lambda source: source + _bad_adaptation_packet(0x102F),
])
def test_m2ts_dolby_vision_pair_rejects_transport_error_and_bad_adaptation_outside_psi(mutate) -> None:
    assert parse_m2ts_dolby_vision_pair(mutate(_dovi_m2ts())) is None


def _m2ts_probe_raw(*, el_first: bool = False) -> dict[str, object]:
    base = {"index": 1 if el_first else 0, "id": "0x1011", "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "width": 1920, "height": 1080}
    enhancement = {"index": 0 if el_first else 1, "id": "0x102f", "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "width": 640, "height": 360, "side_data_list": [{
        "side_data_type": "DOVI configuration record", "dv_profile": 7, "dv_level": 6,
        "dv_bl_signal_compatibility_id": 6, "rpu_present_flag": 1, "el_present_flag": 1, "bl_present_flag": 0,
    }]}
    return {
        "format": {"format_name": "mpegts"},
        "programs": [{"program_id": 1, "streams": [{"index": 0}, {"index": 1}, {"index": 2}]}],
        "streams": [enhancement, base, {"index": 2, "id": "0x1100", "codec_type": "audio", "codec_name": "aac", "channels": 2}]
        if el_first else [base, enhancement, {"index": 2, "id": "0x1100", "codec_type": "audio", "codec_name": "aac", "channels": 2}],
    }


def test_m2ts_public_probe_marks_only_complete_signalled_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Database:
        def get(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(media_probe, "media_tool", lambda *_args: "ffprobe")
    def probe_raw(_tool: str, path: Path) -> dict[str, object]:
        raw = _m2ts_probe_raw(el_first=path.stem == "el-first")
        if path.stem == "conflicting-depth":
            for stream in raw["streams"]:
                if stream.get("codec_type") == "video":
                    stream.update(pix_fmt="yuv420p", bits_per_raw_sample="10")
        return raw
    monkeypatch.setattr(media_probe, "run_ffprobe", probe_raw)
    valid = tmp_path / "pair.m2ts"
    valid.write_bytes(_dovi_m2ts(null_packets=True))
    el_first = tmp_path / "el-first.m2ts"
    el_first.write_bytes(valid.read_bytes())
    truncated = tmp_path / "truncated.m2ts"
    truncated.write_bytes(valid.read_bytes()[:-1])
    conflicting = tmp_path / "conflicting-depth.m2ts"
    conflicting.write_bytes(valid.read_bytes())
    service = media_probe.MediaProbeService(Database())  # type: ignore[arg-type]

    facts = service._probe(valid)
    assert facts["dv_transport_pair"] == {
        "packet_size": 192, "program_number": 1, "pmt_pid": 0x1000,
        "base_pid": 0x1011, "enhancement_pid": 0x102F,
        "base_stream_index": 0, "enhancement_stream_index": 1,
        "dv_profile": 7, "dv_level": 6, "dv_compat": 6,
    }
    el_first_facts = service._probe(el_first)
    assert (el_first_facts["dv_transport_pair"]["base_stream_index"], el_first_facts["video_codec"], el_first_facts["width"], el_first_facts["height"]) == (1, "hevc", 1920, 1080)
    assert "dv_transport_pair" not in service._probe(truncated)
    assert "dv_transport_pair" not in service._probe(conflicting)


def test_m2ts_normalization_retains_only_valid_stream_pid_and_program_membership() -> None:
    facts = normalize({
        "format": {"format_name": "mpegts"},
        "programs": [{"program_id": 1, "streams": [{"index": 4}, {"index": 5}]}],
        "streams": [
            {"index": 4, "id": "0x1011", "codec_type": "video", "codec_name": "hevc"},
            {"index": 5, "id": "0x1015", "codec_type": "video", "codec_name": "hevc"},
            {"index": 6, "id": "not-a-pid", "codec_type": "audio", "codec_name": "aac"},
        ],
    }, "pair.m2ts")

    assert facts["m2ts_suffix"] is True
    assert [(stream["index"], stream.get("ts_pid"), stream.get("program_number")) for stream in facts["streams"]] == [
        (4, 0x1011, 1), (5, 0x1015, 1), (6, None, None),
    ]


def test_every_real_stream_is_listed_with_normalized_facts() -> None:
    facts = normalize(RAW, "Movie.mkv")
    assert facts["bit_rate"] == 24_000_000
    assert [s["index"] for s in facts["streams"]] == [0, 1, 2, 3, 4]  # cover art, attachments, bad index dropped
    video, truehd, aac, pgs, srt = facts["streams"]
    assert video == {
        "index": 0, "type": "video", "codec": "hevc", "profile": "Main 10", "level": 153, "language": None, "title": None,
        "default": True, "forced": False, "hearing_impaired": False, "width": 3840, "height": 2160, "channels": None,
        "channel_layout": None, "sample_rate": None, "bit_rate": None, "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084",
        "color_primaries": "bt2020", "color_space": "bt2020nc", "color_range": "tv",
        "frame_rate": 23.976, "dv_profile": 8, "dv_compat": 1, "dv_level": 9,
        "dv_rpu_present": True, "dv_el_present": False, "dv_bl_present": True, "hdr10": False, "hdr10plus": False,
    }
    assert (truehd["language"], truehd["title"], truehd["channels"], truehd["sample_rate"], truehd["default"]) == ("eng", "Atmos", 8, 48000, True)
    assert (aac["language"], aac["bit_rate"], aac["default"]) == (None, 192000, False)  # "und" is no language
    assert (pgs["codec"], pgs["forced"]) == ("hdmv_pgs_subtitle", True)
    assert srt["hearing_impaired"] is True
    # The compact facts the web player already shows are unchanged.
    assert (facts["video_codec"], facts["audio_codec"], facts["height"], facts["audio_tracks"]) == ("hevc", "truehd", 2160, 3)
    assert facts["chapters"] == [
        {"start_time": 0.0, "end_time": 100.0, "title": "Opening"},
        {"start_time": 100.0, "end_time": 200.0, "title": "Middle"},
    ]


@pytest.mark.parametrize("transfer", [None, "smpte2084", "arib-std-b67"])
def test_frame_colors_follow_playable_video_when_cover_art_comes_first(monkeypatch: pytest.MonkeyPatch, transfer: str | None) -> None:
    responses = iter([
        {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                {"index": 2, "codec_type": "video", "codec_name": "hevc", "color_space": "bt2020nc", "color_transfer": transfer, "disposition": "bad"},
            ],
        },
        {"frames": [{"stream_index": 2, "color_space": "bt2020nc", "color_transfer": transfer or "smpte2084", "color_primaries": "bt2020", "color_range": "tv", "pix_fmt": "yuv420p10le"}]},
    ])
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(next(responses)).encode(), b"")

    monkeypatch.setattr(media_probe, "_run_bounded_process", fake_run)
    facts = normalize(run_ffprobe("ffprobe", Path("/media/movie.mkv")), "movie.mkv")

    assert len(commands) == 2
    assert commands[1][commands[1].index("-select_streams") + 1] == "2"
    assert facts["video_codec"] == "hevc"
    stream = facts["streams"][1]
    assert (stream["color_transfer"], stream["color_space"], stream["color_primaries"], stream["color_range"], stream["pix_fmt"]) == (transfer or "smpte2084", "bt2020nc", "bt2020", "tv", "yuv420p10le")


def _fake_probe(tmp_path: Path, body: str) -> Path:
    probe = tmp_path / "ffprobe"
    probe.write_text(f"#!{sys.executable}\n{body}")
    probe.chmod(0o755)
    return probe


def _assert_reaped(pid_file: Path) -> None:
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


class _FakePipe:
    def __init__(self, *, close_error: bool = False) -> None:
        self.close_error = close_error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error:
            raise OSError("close failed")


class _FakeProcess:
    def __init__(self, *, ignore_terminate: bool = False, pipe_close_error: bool = False) -> None:
        self.stdout = _FakePipe(close_error=pipe_close_error)
        self.stderr = _FakePipe()
        self.returncode = 0
        self.running = True
        self.ignore_terminate = ignore_terminate
        self.calls: list[object] = []

    def poll(self) -> int | None:
        self.calls.append("poll")
        return None if self.running else self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if not self.ignore_terminate:
            self.running = False

    def kill(self) -> None:
        self.calls.append("kill")
        self.running = False

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        if self.running:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return self.returncode


def test_selector_construction_failure_still_kills_and_reaps_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(ignore_terminate=True)
    monkeypatch.setattr(media_probe.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        media_probe.selectors, "DefaultSelector",
        lambda: (_ for _ in ()).throw(OSError("selector unavailable")),
    )

    with pytest.raises(media_probe.ProbeError, match="probe_failed"):
        media_probe._run_bounded_process(["ffprobe"])

    assert process.calls == [
        "poll", "terminate", ("wait", media_probe.PROBE_TERMINATE_GRACE_SECONDS), "kill", ("wait", None),
    ]
    assert process.stdout.close_calls == 1
    assert process.stderr.close_calls == 1


def test_cleanup_failures_cannot_skip_probe_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(pipe_close_error=True)
    process.running = False

    class EmptySelector:
        def __init__(self) -> None:
            self.close_calls = 0

        def register(self, *_args: object) -> None:
            pass

        def get_map(self) -> dict[object, object]:
            return {}

        def close(self) -> None:
            self.close_calls += 1
            raise OSError("selector close failed")

    selector = EmptySelector()
    monkeypatch.setattr(media_probe.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(media_probe.selectors, "DefaultSelector", lambda: selector)

    result = media_probe._run_bounded_process(["ffprobe"])

    assert result.returncode == 0
    assert selector.close_calls == 1
    assert process.stdout.close_calls == 1
    assert process.stderr.close_calls == 1
    wait_call = process.calls[0]
    assert isinstance(wait_call, tuple)
    assert wait_call[0] == "wait"
    assert 0 < wait_call[1] <= media_probe.PROBE_TIMEOUT_SECONDS
    assert process.calls[1:] == ["poll", ("wait", None)]


def test_bounded_probe_accepts_a_shorter_call_site_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess()
    process.running = False

    class EmptySelector:
        def register(self, *_args: object) -> None:
            pass

        def get_map(self) -> dict[object, object]:
            return {}

        def close(self) -> None:
            pass

    monkeypatch.setattr(media_probe.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(media_probe.selectors, "DefaultSelector", EmptySelector)
    media_probe._run_bounded_process(["ffprobe"], timeout=0.1)
    wait_call = process.calls[0]
    assert isinstance(wait_call, tuple) and 0 < wait_call[1] <= 0.1


@pytest.mark.parametrize("fd", [1, 2], ids=["stdout", "stderr"])
def test_probe_caps_each_output_pipe_and_reaps_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fd: int,
) -> None:
    limit = 64
    pid_file = tmp_path / "pid"
    term_file = tmp_path / "terminated"
    probe = _fake_probe(tmp_path, """import os, signal, time
pid_file = os.environ["FAKE_PROBE_PID"]
term_file = os.environ["FAKE_PROBE_TERM"]
def terminated(_signum, _frame):
    open(term_file, "w").write("yes")
signal.signal(signal.SIGTERM, terminated)
open(pid_file, "w").write(str(os.getpid()))
os.write(int(os.environ["FAKE_PROBE_FD"]), b"x" * (int(os.environ["FAKE_PROBE_LIMIT"]) + 1))
while True:
    time.sleep(1)
""")
    monkeypatch.setattr(media_probe, "PROBE_OUTPUT_LIMIT", limit)
    monkeypatch.setattr(media_probe, "PROBE_TERMINATE_GRACE_SECONDS", 0.2)
    monkeypatch.setenv("FAKE_PROBE_PID", str(pid_file))
    monkeypatch.setenv("FAKE_PROBE_TERM", str(term_file))
    monkeypatch.setenv("FAKE_PROBE_FD", str(fd))
    monkeypatch.setenv("FAKE_PROBE_LIMIT", str(limit))
    read_sizes: list[int] = []
    retained_sizes: list[int] = []
    real_read = media_probe._read_pipe
    real_append = media_probe._append_output

    def tracked_read(file_descriptor: int, size: int) -> bytes:
        read_sizes.append(size)
        return real_read(file_descriptor, size)

    def tracked_append(buffer: bytearray, chunk: bytes) -> bool:
        exceeded = real_append(buffer, chunk)
        retained_sizes.append(len(buffer))
        return exceeded

    monkeypatch.setattr(media_probe, "_read_pipe", tracked_read)
    monkeypatch.setattr(media_probe, "_append_output", tracked_append)
    monkeypatch.setattr(media_probe, "media_tool", lambda *_args: str(probe))

    service = media_probe.MediaProbeService(None)  # type: ignore[arg-type]
    assert service._probe(tmp_path / "movie.mkv") == {"error": "probe_failed"}

    assert limit + 1 in read_sizes and max(read_sizes) <= limit + 1
    assert retained_sizes and max(retained_sizes) == limit
    assert term_file.read_text() == "yes"
    _assert_reaped(pid_file)


def test_probe_timeout_terminates_and_reaps_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "pid"
    term_file = tmp_path / "terminated"
    probe = _fake_probe(tmp_path, """import os, signal, time
def terminated(_signum, _frame):
    open(os.environ["FAKE_PROBE_TERM"], "w").write("yes")
signal.signal(signal.SIGTERM, terminated)
open(os.environ["FAKE_PROBE_PID"], "w").write(str(os.getpid()))
while True:
    time.sleep(1)
""")
    monkeypatch.setattr(media_probe, "PROBE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(media_probe, "PROBE_TERMINATE_GRACE_SECONDS", 0.2)
    monkeypatch.setenv("FAKE_PROBE_PID", str(pid_file))
    monkeypatch.setenv("FAKE_PROBE_TERM", str(term_file))
    monkeypatch.setattr(media_probe, "media_tool", lambda *_args: str(probe))

    service = media_probe.MediaProbeService(None)  # type: ignore[arg-type]
    assert service._probe(tmp_path / "movie.mkv") == {"error": "probe_timeout"}

    assert term_file.read_text() == "yes"
    _assert_reaped(pid_file)


def test_bounded_probe_preserves_normal_json(tmp_path: Path) -> None:
    raw = {"format": {"format_name": "matroska,webm"}, "streams": [{"index": 0, "codec_type": "audio", "codec_name": "flac"}]}
    probe = _fake_probe(tmp_path, f"import os\nos.write(2, b'diagnostic')\nos.write(1, {json.dumps(raw).encode()!r})\n")

    assert run_ffprobe(str(probe), tmp_path / "movie.mkv") == raw


def test_zero_rates_and_junk_values_become_none() -> None:
    facts = normalize({"format": {}, "streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac", "avg_frame_rate": "0/0", "channels": "two", "tags": {"language": "x" * 99}}]}, "a.m4a")
    (stream,) = facts["streams"]
    assert (stream["frame_rate"], stream["channels"], stream["language"]) == (None, None, "x" * 16)
    assert facts["bit_rate"] is None


def test_non_list_chapters_are_ignored() -> None:
    facts = normalize({"format": {"duration": "60"}, "chapters": {"start_time": "0"}}, "clip.mp4")
    assert facts["chapters"] == []


def test_audio_precision_uses_actual_bits_when_the_probe_reports_them() -> None:
    facts = normalize({"streams": [
        {"index": 0, "codec_type": "audio", "codec_name": "flac", "bits_per_raw_sample": "24", "bits_per_sample": "32"},
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "bits_per_sample": "16"},
        {"index": 2, "codec_type": "audio", "codec_name": "opus", "bits_per_raw_sample": "0"},
    ]}, "Music.flac")
    assert [stream.get("bit_depth") for stream in facts["streams"]] == [24, 16, None]


def test_legacy_successful_caches_are_stale_but_cached_errors_are_not() -> None:
    assert is_stale(None, "1:2")
    assert is_stale({"fingerprint": "1:2", "container": "mp4"}, "1:2")  # legacy successful cache: no streams
    assert is_stale({"fingerprint": "1:1", "streams": [], "chapters": []}, "1:2")  # the file changed
    assert is_stale({"fingerprint": "1:2", "streams": []}, "1:2")  # cache predates current facts
    assert is_stale({"fingerprint": "1:2", "streams": [], "chapters": []}, "1:2")  # cache predates current version
    assert is_stale({"fingerprint": "1:2", "streams": [], "chapters": [], "_probe_cache_version": PROBE_CACHE_VERSION - 1}, "1:2")
    assert not is_stale({
        "fingerprint": "1:2", "streams": [], "chapters": [], "_probe_cache_version": PROBE_CACHE_VERSION,
    }, "1:2")
    assert not is_stale({"fingerprint": "1:2", "error": "probe_failed"}, "1:2")
