"""RKMPP arguments are explicit and capability-gated by an actual encode."""
from __future__ import annotations

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
