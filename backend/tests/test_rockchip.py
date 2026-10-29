"""RKMPP arguments are explicit and capability-gated by an actual encode."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from app.services import rockchip


def test_rkmpp_h264_and_hevc_use_the_bundled_encoder_contract() -> None:
    assert rockchip.device_args() == ["-init_hw_device", "rkmpp=rk", "-filter_hw_device", "rk"]
    assert rockchip.encode_filter() == "format=nv12,hwupload=derive_device=rkmpp"
    assert rockchip.encoder_args("h264") == ["-c:v", "h264_rkmpp", "-rc_mode", "CBR", "-g", "10000"]
    assert rockchip.encoder_args("hevc") == ["-c:v", "hevc_rkmpp", "-rc_mode", "CBR", "-g", "10000"]
    with pytest.raises(ValueError, match="unsupported Rockchip output codec: av1"):
        rockchip.encoder_args("av1")


def test_rkmpp_probe_exercises_the_device_upload_and_selected_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    command: list[str] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command.extend(args)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(rockchip.subprocess, "run", run)
    assert rockchip.probe("ffmpeg", "hevc") == (True, "ok")
    assert command[:5] == ["ffmpeg", "-hide_banner", "-v", "error", "-init_hw_device"]
    assert "rkmpp=rk" in command and "format=nv12,hwupload=derive_device=rkmpp" in command
    assert "hevc_rkmpp" in command and command[-3:] == ["-f", "null", "-"]


def test_rkmpp_probe_reports_startup_failure_without_claiming_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rockchip.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    assert rockchip.probe("missing-ffmpeg", "h264") == (False, "FileNotFoundError")


def test_rkmpp_zero_copy_helpers_pin_a_decoder_and_sdr_rga_filter() -> None:
    assert rockchip.zero_copy_decoder_args("h264") == [
        "-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime", "-afbc", "rga", "-c:v", "h264_rkmpp",
    ]
    assert rockchip.zero_copy_decoder_args("av1") == [
        "-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime", "-afbc", "rga", "-c:v", "av1_rkmpp",
    ]
    assert rockchip.zero_copy_decoder_args("mpeg2video") is None
    assert rockchip.zero_copy_scale_filter(144) == (
        "scale_rkrga=w=-2:h=144:format=nv12:force_yuv=8bit:force_chroma=420sp:afbc=1"
    )
    with pytest.raises(ValueError, match="positive even"):
        rockchip.zero_copy_scale_filter(143)


@pytest.mark.parametrize(
    ("input_codec", "output_codec", "source_encoder"),
    [
        ("h264", "h264", "libx264"),
        ("hevc", "hevc", "libx265"),
        ("vp9", "h264", "libvpx-vp9"),
        ("av1", "hevc", "libsvtav1"),
    ],
)
def test_rkmpp_zero_copy_probe_checks_each_exact_decoder_encoder_pair(
    monkeypatch: pytest.MonkeyPatch,
    input_codec: str,
    output_codec: str,
    source_encoder: str,
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
        commands.append(args)
        if args[0] == "probe-tool":
            return subprocess.CompletedProcess(
                args, 0,
                json.dumps({"streams": [{"codec_name": output_codec, "width": 256, "height": 144, "nb_read_frames": "5"}]}), "",
            )
        if args[-1] == "-":
            return subprocess.CompletedProcess(args, 0, b"pixels", b"")
        if args[-1].endswith(("source.mkv", "output.mkv")):
            Path(args[-1]).write_bytes(b"matroska")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(rockchip.subprocess, "run", run)
    assert rockchip.probe_zero_copy("ffmpeg-tool", input_codec, output_codec, "probe-tool") == (
        True, f"{input_codec}->{output_codec} decoded through DRM-PRIME, RGA, and MPP with a readable frame",
    )
    source, graph, metadata, decoded = commands
    assert source[source.index("-c:v") + 1] == source_encoder
    assert graph[graph.index("-c:v") + 1] == f"{input_codec}_rkmpp"
    assert graph[graph.index("-vf") + 1] == rockchip.zero_copy_scale_filter(144)
    assert graph.count("-c:v") == 2 and graph[-3:] == ["-f", "matroska", graph[-1]]
    assert "hwupload" not in graph and "hwdownload" not in graph
    assert metadata[0] == "probe-tool" and "-count_frames" in metadata
    assert decoded[-5:] == ["-pix_fmt", "yuv420p", "-f", "rawvideo", "-"]


def test_rkmpp_zero_copy_probe_rejects_missing_output_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1].endswith(("source.mkv", "output.mkv")):
            Path(args[-1]).write_bytes(b"matroska")
        if args[0] == "probe-tool":
            return subprocess.CompletedProcess(args, 0, json.dumps({"streams": [{"codec_name": "h264", "width": 256, "height": 144, "nb_read_frames": "0"}]}), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(rockchip.subprocess, "run", run)
    assert rockchip.probe_zero_copy("ffmpeg-tool", "h264", "h264", "probe-tool") == (
        False, "output verification did not match h264 256x144 with frames",
    )


def test_rkmpp_zero_copy_probe_fails_closed_when_the_target_has_no_mpp_device(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[-1].endswith("source.mkv"):
            Path(args[-1]).write_bytes(b"synthetic input")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 187, "", "[h264_rkmpp] Failed to init MPP context: -1")

    monkeypatch.setattr(rockchip.subprocess, "run", run)
    assert rockchip.probe_zero_copy("ffmpeg-tool", "h264", "h264", "probe-tool") == (
        False, "h264->h264 zero-copy graph failed: [h264_rkmpp] Failed to init MPP context: -1",
    )
    assert len(commands) == 2
