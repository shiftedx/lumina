"""The operator GPU qualifier exercises the same fMP4 output shape as playback."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess


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
