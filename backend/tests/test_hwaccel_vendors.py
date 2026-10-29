"""Vendor probe contracts: an exposed encoder must survive its runtime command."""
from __future__ import annotations

from app.services import hwaccel

FILTERS = """ ... tonemap_cuda V->V CUDA tone map
 ... tonemap_opencl V->V OpenCL tone map
 ... vpp_qsv V->V QSV tone map
 T.. zscale V->V zscale
 ... tonemap V->V tone map
"""


def test_linux_auto_prefers_intel_then_nvidia_then_vaapi(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[bool, str]:
        calls.append(args)
        if "-filters" in args:
            return True, FILTERS
        if "h264_qsv" in args:
            return False, "qsv unavailable"
        if "h264_nvenc" in args:
            return True, ""
        return False, "unexpected"

    monkeypatch.setattr(hwaccel.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hwaccel, "_run", run)
    status = hwaccel.detect("ffmpeg", "auto")
    assert status.active == "nvenc" and status.probe_ok and status.tonemap == "cuda"
    assert any("h264_qsv" in command for command in calls)
    assert any("h264_nvenc" in command for command in calls)
    assert not any("h264_vaapi" in command for command in calls)


def test_nvenc_probe_and_cuda_tonemap_keep_selected_gpu(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(args: list[str]) -> tuple[bool, str]:
        commands.append(args)
        return (True, FILTERS) if "-filters" in args else (True, "")

    monkeypatch.setattr(hwaccel, "_run", run)
    monkeypatch.setattr(hwaccel.settings, "hwaccel_gpu_index", 2)
    status = hwaccel.detect("ffmpeg", "nvenc")
    assert status == hwaccel.HwStatus(active="nvenc", probe_ok=True, tonemap="cuda", software_tonemap="software", encode_codecs=("h264", "hevc", "av1"))
    encodes = [command for command in commands if "-c:v" in command]
    assert {command[command.index("-c:v") + 1] for command in encodes} >= {"h264_nvenc", "hevc_nvenc", "av1_nvenc"}
    for encode in encodes:
        assert "cuda=cu:2" in encode and encode[encode.index("-gpu") + 1] == "2"
    tone = next(command for command in commands if "-vf" in command and "tonemap_cuda" in command[command.index("-vf") + 1])
    tone_graph = tone[tone.index("-vf") + 1]
    assert "tonemap_cuda=tonemap=bt2390" in tone_graph and "format=nv12" in tone_graph
    assert "cuda=cu:2" in tone and tone[tone.index("-gpu") + 1] == "2"


def test_native_modes_and_invalid_mode_are_safe(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(args: list[str]) -> tuple[bool, str]:
        commands.append(args)
        return (True, FILTERS) if "-filters" in args else (True, "")

    monkeypatch.setattr(hwaccel, "_run", run)
    videotoolbox = hwaccel.detect("ffmpeg", "videotoolbox")
    assert videotoolbox.active == "videotoolbox" and videotoolbox.encode_codecs == ("h264", "hevc")
    vt_encodes = [command for command in commands if "-c:v" in command]
    assert {command[command.index("-c:v") + 1] for command in vt_encodes} == {"h264_videotoolbox", "hevc_videotoolbox"}
    assert all("-allow_sw" in command for command in vt_encodes)
    commands.clear()
    amf = hwaccel.detect("ffmpeg", "amf")
    assert amf.active == "amf" and amf.encode_codecs == ("h264", "hevc", "av1")
    assert {command[command.index("-c:v") + 1] for command in commands if "-c:v" in command} == {"h264_amf", "hevc_amf", "av1_amf"}
    invalid = hwaccel.detect("ffmpeg", "misconfigured")
    assert invalid.active == "none" and invalid.probe_error == "unsupported_hwaccel_mode: misconfigured"


def test_opencl_probe_uses_configured_drm_node_and_pq_frame(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(args: list[str]) -> tuple[bool, str]:
        commands.append(args)
        return (True, FILTERS) if "-filters" in args else (True, "")

    monkeypatch.setattr(hwaccel, "_run", run)
    monkeypatch.setattr(hwaccel.settings, "hwaccel_device", "/dev/dri/renderD129")
    assert hwaccel.detect("ffmpeg", "vaapi").tonemap == "opencl"
    tone = commands[-1]
    assert "vaapi=va:/dev/dri/renderD129" in tone and "opencl=ocl@va" in tone
    assert "color_trc=smpte2084" in tone[tone.index("-i") + 1]
    joined = " ".join(tone)
    assert "hwmap=derive_device=opencl" in joined and "hwmap=derive_device=vaapi:reverse=1" in joined
