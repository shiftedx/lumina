"""Hardware probe, software fallback and the ffmpeg arguments per hw × tone-map × burn-in."""
from __future__ import annotations

import threading
import time
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import hwaccel as hw_module
from app.services import local_playback_sessions as lps
from app.services.hwaccel import HwAccel, HwStatus, detect, encoder_args
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
            if not ok and any(name in arg for arg in args):
                return False, f"Error: cannot open {name} device\n"
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
    monkeypatch.setattr(hw_module.platform, "system", lambda: "Linux")
    run, _ = fake_run({"h264_qsv": True, "opencl=ocl": True})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("/usr/bin/ffmpeg", "auto") == HwStatus(active="qsv", probe_ok=True, tonemap="opencl", software_tonemap="software")


def test_auto_falls_to_vaapi_then_software(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module.platform, "system", lambda: "Linux")
    run, _ = fake_run({"h264_qsv": False, "h264_vaapi": True, "opencl=ocl": False})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("ffmpeg", "auto") == HwStatus(active="vaapi", probe_ok=True, tonemap="software", software_tonemap="software")
    run, _ = fake_run({"h264_qsv": False, "h264_vaapi": False}, filters=" ... tonemap V->V x\n")
    monkeypatch.setattr(hw_module, "_run", run)
    status = detect("ffmpeg", "auto")
    assert (status.active, status.probe_ok, status.tonemap) == ("none", False, "none")  # no zscale: no software tone-map
    assert "qsv: Error: cannot open h264_qsv device" in status.probe_error and "vaapi:" in status.probe_error


def test_qsv_without_opencl_uses_vpp_qsv_and_off_never_probes_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module.platform, "system", lambda: "Linux")
    run, calls = fake_run({"h264_qsv": True, "opencl=ocl": False})
    monkeypatch.setattr(hw_module, "_run", run)
    assert detect("ffmpeg", "qsv").tonemap == "vpp_qsv"
    calls.clear()
    assert detect("ffmpeg", "off") == HwStatus(probe_ok=True, tonemap="software", software_tonemap="software")
    assert all("-filters" in args for args in calls)
    assert detect(None, "auto").probe_error == "ffmpeg_unavailable"


def test_modern_codecs_are_advertised_only_after_their_exact_encoder_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module.platform, "system", lambda: "Linux")

    def run(args: list[str]) -> tuple[bool, str]:
        if "-filters" in args:
            return True, FILTERS
        joined = " ".join(args)
        return ("h264_qsv" in joined or "hevc_qsv" in joined), "no av1 qsv" if "av1_qsv" in joined else ""

    monkeypatch.setattr(hw_module, "_run", run)
    status = detect("ffmpeg", "qsv")
    assert status.encode_codecs == ("h264", "hevc")
    assert set(status.encode_errors) == {"av1"}
    accel = HwAccel()
    monkeypatch.setattr(accel, "status", lambda ffmpeg, mode: status)
    assert accel.output_codecs("ffmpeg", "qsv") == ("h264", "hevc")
    assert accel.choice("ffmpeg", "qsv", "hevc") == ("qsv", status.tonemap)
    assert accel.choice("ffmpeg", "qsv", "av1") == ("none", status.software_tonemap)


def test_hdr_ranges_are_exact_demand_probes_and_choice_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []
    verified = {"ffprobe": True, "ffprobe-a": True, "ffprobe-b": False}
    status = HwStatus(active="qsv", probe_ok=True, encode_codecs=("h264", "hevc", "av1"))
    monkeypatch.setattr(hw_module, "detect", lambda ffmpeg, mode: status)
    monkeypatch.setattr(
        hw_module, "_hdr_encode_test",
        lambda ffmpeg, ffprobe, hw, codec, video_range: calls.append((hw, codec, video_range)) or (codec == "hevc" and verified[ffprobe], "unsupported"),
    )
    accel = HwAccel()
    assert accel.output_ranges("ffmpeg", "qsv", "ffprobe", video_range="HDR10") == {"hevc": frozenset({"HDR10"})}
    assert set(calls) == {("qsv", "hevc", "HDR10"), ("qsv", "av1", "HDR10")}
    assert accel.choice("ffmpeg", "qsv", "hevc", "HDR10", "ffprobe") == ("qsv", "none")
    assert accel.choice("ffmpeg", "qsv", "av1", "HDR10", "ffprobe") == ("none", "none")
    # Reusing the exact query is cached; an unsupported range never launches a probe.
    accel.output_ranges("ffmpeg", "qsv", "ffprobe", video_range="HDR10")
    assert len(calls) == 2
    assert accel.output_ranges("ffmpeg", "qsv", "ffprobe", video_range="DOVI") == {}
    cached = accel.peek("ffmpeg", "qsv")
    assert cached is not None and cached.hdr_encode_ranges == {"hevc": ("HDR10",)}
    assert cached.hdr_encode_errors == {"av1/HDR10": "unsupported"}
    assert accel.output_ranges("ffmpeg", "qsv", "ffprobe-b", video_range="HDR10") == {}
    assert accel.output_ranges("ffmpeg", "qsv", None, video_range="HDR10") == {}
    accel.disabled = True
    assert accel.output_ranges("ffmpeg", "qsv", "ffprobe-a", video_range="HDR10") == {}


def test_hdr_probe_stops_before_launching_an_encode_after_its_total_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0.0, 0.0, hw_module.HDR_PROBE_TIMEOUT_SECONDS + 0.1))
    calls: list[list[str]] = []
    monkeypatch.setattr(hw_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(hw_module, "_hdr_source", lambda ffmpeg, source, video_range, timeout: (True, ""))
    monkeypatch.setattr(hw_module, "_run", lambda args, **kwargs: calls.append(args) or (True, ""))
    assert hw_module._hdr_encode_test("ffmpeg", "ffprobe", "none", "hevc", "HDR10") == (False, "HDR probe deadline exceeded")
    assert calls == []


def test_static_hdr_metadata_requires_every_mastering_coordinate_and_light_level() -> None:
    mastering = {key: f"{value.numerator}/{value.denominator}" for key, value in hw_module._HDR_MASTERING.items()}
    stream = {"side_data_list": [
        {"side_data_type": "Mastering display metadata", **mastering},
        {"side_data_type": "Content light level metadata", "max_content": 1000, "max_average": 400},
    ]}
    assert hw_module._has_static_hdr_metadata(stream)
    stream["side_data_list"][0]["red_x"] = "1/2"  # type: ignore[index]
    assert not hw_module._has_static_hdr_metadata(stream)


def test_rkmpp_zero_copy_is_pair_proven_and_cached_for_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module, "detect", lambda ffmpeg, mode: HwStatus(active="rkmpp", probe_ok=True, encode_codecs=("h264", "hevc")))
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(hw_module.rockchip, "probe_zero_copy", lambda ffmpeg, source, output, ffprobe: calls.append((source, output)) or (True, "ok"))
    accel = HwAccel()
    assert accel.status("ffmpeg", "rkmpp").active == "rkmpp"
    assert accel.zero_copy_supported("ffmpeg", "hevc", "h264", "ffprobe")
    assert accel.zero_copy_supported("ffmpeg", "hevc", "h264", "ffprobe")
    assert calls == [("hevc", "h264")]
    status = accel.peek("ffmpeg", "rkmpp")
    assert status is not None and status.zero_copy_pairs == ("hevc->h264",) and not status.zero_copy_errors


def test_windows_qsv_uses_the_selected_d3d11_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hw_module.settings, "hwaccel_gpu_index", 2)
    assert hw_module.device_args("qsv") == ["-init_hw_device", "qsv=qs:hw,child_device=2,child_device_type=d3d11va", "-filter_hw_device", "qs"]


def test_rkmpp_probe_advertises_only_h264_and_hevc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw_module, "_run", lambda args: (True, FILTERS) if "-filters" in args else (False, "not reached"))
    monkeypatch.setattr(hw_module.rockchip, "probe", lambda ffmpeg, codec: (codec != "av1", "no av1"))
    status = detect("ffmpeg", "rkmpp")
    assert status.active == "rkmpp" and status.encode_codecs == ("h264", "hevc")
    assert status.encode_errors == {"av1": "unsupported_encoder"}


@pytest.mark.parametrize(("hw", "codec", "encoder", "tag"), [("qsv", "hevc", "hevc_qsv", "hvc1"), ("qsv", "av1", "av1_qsv", "av01"), ("rkmpp", "hevc", "hevc_rkmpp", "hvc1")])
def test_modern_encoder_arguments_and_fmp4_tags(hw: str, codec: str, encoder: str, tag: str) -> None:
    decision = Decision("transcode", video="encode", audio="encode", video_index=0, audio_index=1, height=720, bitrate=4_000_000, video_codec=codec)
    cmd = lps.ffmpeg_command("ffmpeg", Path("/m/input.mkv"), HDR, OUT, decision, hw=hw)
    assert cmd[cmd.index("-c:v") + 1] == encoder
    assert cmd[cmd.index("-tag:v") + 1] == tag
    if codec == "av1":
        assert "-forced_idr" not in encoder_args("qsv", "av1")


def test_hdr_command_keeps_p010_bt2020_and_svt_hdr_metadata() -> None:
    facts = {"streams": [{"index": 0, "type": "video", "codec": "hevc", "pix_fmt": "yuv420p10le"}]}
    av1 = Decision("transcode", video="encode", video_index=0, height=144, bitrate=800_000, video_codec="av1", video_range="HLG")
    command = lps.ffmpeg_command("ffmpeg", Path("/m/hdr.mp4"), facts, OUT, av1)
    joined = " ".join(command)
    assert "scale=-2:144,format=yuv420p10le" in joined
    assert "lp=1:scd=0:enable-hdr=1" in joined
    assert "-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc -color_range tv" in joined
    vt = lps.ffmpeg_command("ffmpeg", Path("/m/hdr.mp4"), facts, OUT, replace(av1, video_codec="hevc", video_range="HDR10"), hw="videotoolbox")
    assert "scale=-2:144,format=p010le" in " ".join(vt)
    qsv = lps.ffmpeg_command("ffmpeg", Path("/m/hdr.mp4"), facts, OUT, replace(av1, video_codec="hevc"), hw="qsv")
    assert "-hwaccel" not in qsv and "format=p010,hwupload=extra_hw_frames=64" in graph(qsv)
    with pytest.raises(lps.ConversionError, match="H.264 HDR"):
        lps.ffmpeg_command("ffmpeg", Path("/m/hdr.mp4"), facts, OUT, replace(av1, video_codec="h264", video_range="HDR10"))
    with pytest.raises(lps.ConversionError, match="burned subtitles"):
        lps.ffmpeg_command("ffmpeg", Path("/m/hdr.mp4"), facts, OUT, replace(av1, burn_subtitle=2))


@pytest.mark.parametrize(("codec", "encoder"), [("hevc", "libx265"), ("av1", "libsvtav1")])
def test_installed_software_modern_encoder_writes_its_negotiated_fmp4_codec(tmp_path: Path, codec: str, encoder: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or encoder not in subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout:
        return  # Optional host encoder: real proof where it exists, not a skipped contract.
    assert HwAccel().software_codec_supported(ffmpeg, codec)
    source, output = tmp_path / "source.mp4", tmp_path / "hls"
    output.mkdir()
    subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=2", "-t", "5", "-c:v", "libx264", str(source)], check=True)
    facts = {"streams": [{"index": 0, "type": "video", "codec": "h264", "width": 64, "height": 64, "pix_fmt": "yuv420p"}]}
    decision = Decision("transcode", video="encode", video_index=0, height=64, bitrate=1_500_000, video_codec=codec)
    subprocess.run(lps.ffmpeg_command(ffmpeg, source, facts, output, decision), check=True, timeout=60)
    ffprobe = str(Path(ffmpeg).with_name("ffprobe"))
    segments = sorted(output.glob("seg*.m4s"))
    assert len(segments) > 1
    probe = subprocess.run([ffprobe, "-v", "error", "-protocol_whitelist", "file,concat", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", f"concat:{output / 'init.mp4'}|{segments[0]}"], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == codec
    for segment in segments:
        frames = subprocess.run([ffprobe, "-v", "error", "-protocol_whitelist", "file,concat", "-show_entries", "frame=key_frame", "-select_streams", "v:0", "-of", "default=nw=1:nk=1", f"concat:{output / 'init.mp4'}|{segment}"], capture_output=True, text=True, check=True)
        assert frames.stdout.splitlines()[0] == "1"
    subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-protocol_whitelist", "file,crypto,data", "-i", str(output / "index.m3u8"), "-f", "null", "-"], check=True, timeout=60)


def test_installed_hdr_encoders_preserve_cicp_static_metadata_and_seek(tmp_path: Path) -> None:
    """The production command reads a local MP4 and every generated fragment is HDR-complete."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        return
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
    variants = [("none", "hevc", "HDR10", "libx265"), ("none", "av1", "HLG", "libsvtav1")]
    if "hevc_videotoolbox" in encoders:
        variants.append(("videotoolbox", "hevc", "HDR10", "hevc_videotoolbox"))
    for hw, codec, video_range, required in variants:
        if required not in encoders:
            continue
        source = tmp_path / f"{codec}-{video_range}.mp4"
        generated, error = hw_module._hdr_source(ffmpeg, source, video_range)
        if not generated:  # Optional host encoder/source capability; the runtime fails closed too.
            continue
        facts = {"streams": [{
            "index": 0, "type": "video", "codec": "hevc", "width": 384, "height": 216,
            "pix_fmt": "yuv420p10le", "color_primaries": "bt2020", "color_transfer": "smpte2084" if video_range == "HDR10" else "arib-std-b67",
            "color_space": "bt2020nc", "color_range": "tv",
        }]}
        decision = Decision("transcode", video="encode", video_index=0, height=144, bitrate=800_000, video_codec=codec, video_range=video_range)
        for start, label in ((0, "start"), (1, "seek")):
            output = tmp_path / f"{hw}-{codec}-{video_range}-{label}"
            output.mkdir()
            subprocess.run(lps.ffmpeg_command(ffmpeg, source, facts, output, decision, hw=hw, start=start), check=True, timeout=60)
            verified, verify_error = hw_module._verify_hdr_hls(ffprobe, output, codec, video_range)
            assert verified, verify_error


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
    ("nvenc", "cuda", ["h264_nvenc", "cuda=cu:0", "-hwaccel cuda", "scale_cuda=w=-2:h=1080:format=p010", "tonemap_cuda=tonemap=bt2390", "t=bt709:m=bt709:p=bt709"], ["libx264", "scale_vaapi"]),
    ("videotoolbox", "none", ["h264_videotoolbox", "-hwaccel videotoolbox", "-allow_sw 0", "-g 10000", "scale=-2:1080,format=nv12"], ["libx264", "-init_hw_device"]),
    ("amf", "none", ["h264_amf", "-quality speed", "scale=-2:1080,format=nv12"], ["libx264", "-init_hw_device", "-hwaccel"]),
    ("rkmpp", "none", ["h264_rkmpp", "rkmpp=rk", "hwupload=derive_device=rkmpp"], ["libx264", "-hwaccel"]),
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
    if hw != "none":
        assert cmd[cmd.index("-b:v") + 1] == "8000000"
        assert cmd[cmd.index("-g") + 1] == "10000"  # no automatic GOP cuts between forced HLS boundaries
    assert cmd[cmd.index("-map") + 1] == "[v]"
    assert " ".join(LOCAL_INPUT_ARGS) in joined
    assert "-progress" in cmd and cmd[cmd.index("-progress") + 1] == str(OUT / "progress")
    assert ("[0:3]scale=-2:1080[sub]" in graph(cmd)) is burn
    if burn and hw in {"qsv", "vaapi", "nvenc"}:
        base, _, overlay = graph(cmd).partition("[base]")
        assert base.endswith("hwdownload,format=nv12") and "overlay=eof_action=pass,hwupload=extra_hw_frames=64[v]" in overlay


def test_hw_decode_only_for_supported_codecs() -> None:
    mpeg4 = {"container": "avi", "streams": [{"index": 0, "type": "video", "codec": "mpeg4", "width": 640, "height": 480, "pix_fmt": "yuv420p"}, {"index": 1, "type": "audio", "codec": "mp3", "channels": 2, "default": True}]}
    cmd = lps.ffmpeg_command("ffmpeg", Path("/m/a.avi"), mpeg4, OUT, decide(BROWSER_DEFAULT, mpeg4), hw="qsv", tonemap="opencl")
    assert "-hwaccel" not in cmd
    assert graph(cmd).startswith("[0:0]format=nv12,hwupload=extra_hw_frames=64,scale_qsv=w=-1:h=480:format=nv12")
    assert "tonemap_opencl" not in graph(cmd)  # SDR source: no tone-mapping even when OpenCL exists
    decision = Decision("transcode", video="encode", video_index=0, height=480)
    for codec, decode in (("h264", False), ("hevc", True)):
        facts = {"streams": [{"index": 0, "codec": codec, "pix_fmt": "yuv420p10le"}]}
        cmd = lps.ffmpeg_command("ffmpeg", Path("/m/ten-bit.mkv"), facts, OUT, decision, hw="nvenc")
        assert ("-hwaccel" in cmd) is decode
        assert "h264_nvenc" in cmd  # unsupported H.264 Hi10 decode still retains GPU encoding


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
