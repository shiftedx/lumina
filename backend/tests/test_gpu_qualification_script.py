"""The operator GPU qualifier exercises the same fMP4 output shape as playback."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("qualify_gpu_transcoding", ROOT / "scripts" / "qualify_gpu_transcoding.py")
assert SPEC and SPEC.loader
qualifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualifier)


def test_modern_qualifier_uses_fmp4_tags_and_checks_init_plus_every_fragment(tmp_path: Path, monkeypatch) -> None:
    command = qualifier._encode_command("ffmpeg", "nvenc", "av1", "/dev/dri/renderD128", 0, tmp_path, hls=True)
    assert command[command.index("-tag:v") + 1] == "av01"
    assert command[command.index("-hls_segment_type") + 1] == "fmp4"
    assert command[command.index("-hls_fmp4_init_filename") + 1] == "init.mp4"
    assert command[command.index("-hls_segment_filename") + 1].endswith("segment-%03d.m4s")
    assert "-forced-idr" not in command  # matches the production AV1 NVENC profile

    (tmp_path / "init.mp4").write_bytes(b"init")
    (tmp_path / "segment-000.m4s").write_bytes(b"first")
    (tmp_path / "segment-001.m4s").write_bytes(b"second")
    (tmp_path / "index.m3u8").write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\nsegment-000.m4s\n#EXTINF:1,\nsegment-001.m4s\n')
    seen: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        if "-show_frames" in args:
            return subprocess.CompletedProcess(args, 0, "1\n", "")
        if "-show_entries" in args:
            return subprocess.CompletedProcess(args, 0, "av1\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(qualifier, "_run", run)
    assert qualifier._check_hls("ffmpeg", "ffprobe", tmp_path, "av1") == (True, "2 HLS segments encode as av1, begin on keyframes, and decode")
    joined = sorted(tmp_path.glob("probe-*.mp4"))
    assert [path.read_bytes() for path in joined] == [b"initfirst", b"initsecond"]
    checked = [Path(args[-1] if args[0] == "ffprobe" else args[args.index("-i") + 1]) for args in seen]
    assert set(checked) == set(joined)


def test_rockchip_zero_copy_qualifier_reports_each_explicit_input_output_pair(monkeypatch) -> None:
    monkeypatch.setattr(qualifier, "_run", lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(qualifier, "_check_hls", lambda *_args: (True, "HLS passed"))
    pairs: list[tuple[str, str, str | None]] = []

    def zero_copy(ffmpeg: str, input_codec: str, output_codec: str, ffprobe: str | None = None) -> tuple[bool, str]:
        pairs.append((input_codec, output_codec, ffprobe))
        return True, f"{input_codec}->{output_codec} passed"

    monkeypatch.setattr(qualifier.rockchip, "probe_zero_copy", zero_copy)
    report = qualifier.qualify(
        "ffmpeg", "configured-ffprobe", "rkmpp", ["h264", "hevc"], "/dev/dri/renderD128", 0,
        zero_copy_inputs=["h264", "av1"],
    )
    assert report["zero_copy_pairs"] == {
        "h264->h264": {"ok": True, "detail": "h264->h264 passed"},
        "h264->hevc": {"ok": True, "detail": "h264->hevc passed"},
        "av1->h264": {"ok": True, "detail": "av1->h264 passed"},
        "av1->hevc": {"ok": True, "detail": "av1->hevc passed"},
    }
    assert pairs == [
        ("h264", "h264", "configured-ffprobe"),
        ("h264", "hevc", "configured-ffprobe"),
        ("av1", "h264", "configured-ffprobe"),
        ("av1", "hevc", "configured-ffprobe"),
    ]


def test_zero_copy_cli_requires_rockchip_and_an_explicit_input_codec(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["qualifier", "--mode", "rkmpp", "--zero-copy"])
    with pytest.raises(SystemExit, match="2"):
        qualifier.main()


def test_hdr_qualifier_uses_the_public_production_probe_and_reports_its_pair(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class Acceleration:
        def hdr_decode_supported(self, *args: object) -> bool:
            calls.append(args)
            return True

        def status(self, _ffmpeg: str, _mode: str) -> object:
            return SimpleNamespace(hdr_decode_pairs=("hevc10->hevc/HDR10",), hdr_decode_errors={})

    monkeypatch.setattr(qualifier.hwaccel, "HwAccel", Acceleration)
    result = qualifier._qualify_hdr_decode("ffmpeg", "ffprobe", "qsv", "hevc", "hevc", "HDR10")
    assert calls == [("ffmpeg", "qsv", "hevc", "yuv420p10le", "hevc", "HDR10", "ffprobe")]
    assert result == {
        "ok": True,
        "detail": "production hardware-decode/P010/fMP4 metadata proof passed",
        "input": {"codec": "hevc", "pix_fmt": "yuv420p10le"},
        "output": {"codec": "hevc", "range": "HDR10"},
    }


def test_hdr_qualifier_keeps_hdr_and_rockchip_zero_copy_modes_separate(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", [
        "qualifier", "--mode", "rkmpp", "--codec", "hevc", "--input-codec", "hevc", "--zero-copy", "--hdr-range", "HDR10",
    ])
    with pytest.raises(SystemExit, match="2"):
        qualifier.main()


def test_hdr_qualifier_rejects_unqualified_h264_high10_input(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", [
        "qualifier", "--mode", "qsv", "--codec", "hevc", "--input-codec", "h264", "--hdr-range", "HDR10",
    ])
    with pytest.raises(SystemExit, match="2"):
        qualifier.main()


def test_qualifier_standalone_help_bootstraps_the_checkout_backend() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "qualify_gpu_transcoding.py"), "--help"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--zero-copy" in result.stdout
    assert "--input-codec {av1,h264,hevc,vp9}" in result.stdout
    assert "--hdr-range {HDR10,HLG}" in result.stdout
