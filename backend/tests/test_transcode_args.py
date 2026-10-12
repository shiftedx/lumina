"""Hardware probe, software fallback and the ffmpeg arguments per hw × tone-map × burn-in."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.services import hwaccel as hw_module
from app.services import local_playback_sessions as lps
from app.services.hwaccel import HwAccel, HwStatus, detect
from app.services.media_probe import LOCAL_INPUT_ARGS
from app.services.playback_decision import BROWSER_DEFAULT, Decision, decide

FILTERS = " T.. zscale   V->V  Apply resizing\n ..C tonemap  V->V  tone map\n ... tonemap_opencl V->V opencl\n ... vpp_qsv V->V qsv\n"


def fake_run(results: dict[str, bool], filters: str = FILTERS):
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[bool, str]:
        calls.append(args)
        if "-filters" in args:
            return True, filters
        for name, ok in results.items():
            if any(name in arg for arg in args):
                return ok, "" if ok else f"Error: cannot open {name} device\n"
        return False, "no match\n"

    return run, calls


@pytest.fixture(autouse=True)
def _fresh_hwaccel():
    hw_module.hwaccel.__init__()
    yield
    hw_module.hwaccel.__init__()


def test_auto_prefers_qsv_with_opencl_tone_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = fake_run({"h264_qsv": True, "opencl=ocl": True})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("/usr/bin/ffmpeg", "auto") == HwStatus(active="qsv", probe_ok=True, tonemap="opencl", software_tonemap="software")


def test_auto_falls_to_vaapi_then_software(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = fake_run({"h264_qsv": False, "h264_vaapi": True, "opencl=ocl": False})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("ffmpeg", "auto") == HwStatus(active="vaapi", probe_ok=True, tonemap="software", software_tonemap="software")
    run, _ = fake_run({"h264_qsv": False, "h264_vaapi": False}, filters=" ... tonemap V->V x\n")
    monkeypatch.setattr(hw_module, "_run", run)
    status = detect("ffmpeg", "auto")
    assert (status.active, status.probe_ok, status.tonemap) == ("none", False, "none")  # no zscale: no software tone-map
    assert "qsv: Error: cannot open h264_qsv device" in status.probe_error and "vaapi:" in status.probe_error


def test_qsv_without_opencl_uses_vpp_qsv_and_off_never_probes_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    run, calls = fake_run({"h264_qsv": True, "opencl=ocl": False})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("ffmpeg", "qsv").tonemap == "vpp_qsv"
    calls.clear()
    assert detect("ffmpeg", "off") == HwStatus(probe_ok=True, tonemap="software", software_tonemap="software")
    assert all("-filters" in args for args in calls)
    assert detect(None, "auto").probe_error == "ffmpeg_unavailable"


def test_status_is_cached_per_setting_and_reprobed_on_demand(monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[str] = []
    monkeypatch.setattr(hw_module, "detect", lambda ffmpeg, mode: probes.append(mode) or HwStatus(active="qsv", probe_ok=True, tonemap="opencl"))
    accel = HwAccel()
    accel.status("ffmpeg", "auto")
    accel.status("ffmpeg", "auto")
    accel.status("ffmpeg", "vaapi")  # a changed setting probes again
    accel.reprobe()
    accel.status("ffmpeg", "auto")
    assert probes == ["auto", "vaapi", "auto"]


def test_three_consecutive_hardware_failures_stay_software_until_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module, "detect", lambda ffmpeg, mode: HwStatus(active="qsv", probe_ok=True, tonemap="opencl", software_tonemap="software"))
    accel = HwAccel()
    assert accel.choice("ffmpeg", "auto") == ("qsv", "opencl")
    accel.record_failure()
    accel.record_failure()
    accel.record_success()  # a success resets the consecutive count
    accel.record_failure()
    accel.record_failure()
    assert accel.choice("ffmpeg", "auto") == ("qsv", "opencl") and not accel.disabled
    accel.record_failure()
    assert accel.disabled and accel.fallbacks == 5
    assert accel.choice("ffmpeg", "auto") == ("none", "software")
    accel.reprobe()
    assert accel.disabled  # re-probing does not re-enable hardware; a restart does


HDR = {
    "container": "matroska,webm", "duration": 60.0, "bit_rate": 30_000_000,
    "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "default": True},
        {"index": 1, "type": "audio", "codec": "truehd", "channels": 8, "default": True},
        {"index": 3, "type": "subtitle", "codec": "hdmv_pgs_subtitle"},
    ],
}
OUT = Path("/tmp/session")


def command(hw: str, tonemap: str, burn: bool, **kwargs) -> list[str]:
    decision = decide(BROWSER_DEFAULT, HDR, subtitle_index=3 if burn else None)
    return lps.ffmpeg_command("ffmpeg", Path("/media/movie.mkv"), HDR, OUT, decision, hw=hw, tonemap=tonemap, **kwargs)


def graph(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


@pytest.mark.parametrize(("hw", "tonemap", "present", "absent"), [
    ("none", "software", ["libx264", "zscale=t=linear", "scale=-2:1080"], ["-init_hw_device", "-hwaccel"]),
    ("none", "none", ["libx264", "format=yuv420p"], ["zscale", "-hwaccel"]),
    ("qsv", "opencl", ["h264_qsv", "qsv=qs@va", "opencl=ocl@va", "-hwaccel", "scale_qsv=w=-1:h=1080:format=p010", "tonemap_opencl", "hwmap=derive_device=qsv:reverse=1"], ["zscale", "libx264"]),
    ("qsv", "vpp_qsv", ["h264_qsv", "vpp_qsv=w=-1:h=1080:tonemap=1:format=nv12"], ["tonemap_opencl", "opencl=ocl@va"]),
    ("qsv", "software", ["h264_qsv", "zscale=t=linear", "hwupload=extra_hw_frames=64"], ["-hwaccel"]),
    ("vaapi", "opencl", ["h264_vaapi", "scale_vaapi=w=-2:h=1080:format=p010", "hwmap=derive_device=opencl", "hwmap=derive_device=vaapi:reverse=1"], ["qsv", "libx264"]),
])
@pytest.mark.parametrize("burn", [False, True])
def test_tokens_per_hw_tonemap_and_burn(hw: str, tonemap: str, present: list[str], absent: list[str], burn: bool) -> None:
    cmd = command(hw, tonemap, burn)
    joined = " ".join(cmd)
    for token in present:
        assert token in joined, token
    for token in absent:
        assert token not in joined, token
    assert cmd[-1] == str(OUT / "index.m3u8")
    assert cmd[cmd.index("-maxrate") + 1] == "8000000" and cmd[cmd.index("-bufsize") + 1] == "16000000"
    assert cmd[cmd.index("-map") + 1] == "[v]"
    assert " ".join(LOCAL_INPUT_ARGS) in joined
    assert "-progress" in cmd and cmd[cmd.index("-progress") + 1] == str(OUT / "progress")
    assert ("[0:3]scale=-2:1080[sub]" in graph(cmd)) is burn
    if burn and hw != "none":
        base, _, overlay = graph(cmd).partition("[base]")
        assert base.endswith("hwdownload,format=nv12") and "overlay=eof_action=pass,hwupload=extra_hw_frames=64[v]" in overlay


def test_hw_decode_only_for_supported_codecs() -> None:
    mpeg4 = {"container": "avi", "streams": [{"index": 0, "type": "video", "codec": "mpeg4", "width": 640, "height": 480, "pix_fmt": "yuv420p"}, {"index": 1, "type": "audio", "codec": "mp3", "channels": 2, "default": True}]}
    cmd = lps.ffmpeg_command("ffmpeg", Path("/m/a.avi"), mpeg4, OUT, decide(BROWSER_DEFAULT, mpeg4), hw="qsv", tonemap="opencl")
    assert "-hwaccel" not in cmd
    assert graph(cmd).startswith("[0:0]format=nv12,hwupload=extra_hw_frames=64,scale_qsv=w=-1:h=480:format=nv12")
    assert "tonemap_opencl" not in graph(cmd)  # SDR source: no tone-mapping even when OpenCL exists


def test_copies_audio_encodes_start_offsets_and_gain() -> None:
    copy = Decision("transcode", video="copy", audio="encode", video_index=0, audio_index=1, audio_channels=6)
    cmd = lps.ffmpeg_command("ffmpeg", Path("/m/x.mkv"), HDR, OUT, copy, start=45.5, audio_gain_db=-3.46)
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert "-filter_complex" not in cmd
    assert cmd[cmd.index("-b:a") + 1] == "384k" and cmd[cmd.index("-ac") + 1] == "6"
    assert cmd[cmd.index("-af") + 1] == "volume=-3.5dB"
    assert cmd.index("-ss") < cmd.index("-i") < cmd.index("-output_ts_offset")
    stereo = Decision("remux", video="copy", audio="copy", video_index=0, audio_index=1)
    assert "-af" not in lps.ffmpeg_command("ffmpeg", Path("/m/x.mkv"), HDR, OUT, stereo, audio_gain_db=-3.0)  # gain needs an encode


def test_warm_probes_in_the_background_so_the_first_encode_does_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    probing, done = threading.Event(), threading.Event()

    def slow_detect(ffmpeg: str | None, mode: str) -> HwStatus:
        probing.set()
        done.wait(5)
        return HwStatus(active="qsv", probe_ok=True)

    monkeypatch.setattr(hw_module, "detect", slow_detect)
    began = time.monotonic()
    hw_module.hwaccel.warm("ffmpeg", "auto")
    assert time.monotonic() - began < 1  # app startup does not wait for the test encodes
    assert probing.wait(5)
    done.set()
    assert hw_module.hwaccel.choice("ffmpeg", "auto") == ("qsv", "none")  # the first encode reads the warmed probe
