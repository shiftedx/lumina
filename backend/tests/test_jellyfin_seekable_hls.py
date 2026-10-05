"""Jellyfin apps get the whole file's HLS timeline from 0 and ffmpeg runs wherever they seek (26-pp, jellyfin_hls.py)."""
from __future__ import annotations

import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services import jellyfin_hls as jh
from app.services import local_playback_sessions as lps
from app.services.jellyfin_hls import HlsRun, Plan, copy_plan, encode_plan
from app.services.local_playback_sessions import ffmpeg_command
from app.services.media_titles import jellyfin_id
from app.services.playback_decision import Decision
from tests.test_jellyfin_integration import SEASON, Vault, episodes, login, needs_ffmpeg, vault  # noqa: F401 - vault is a fixture
from tests.test_v1_sidecars import scan

# Cuts ffmpeg 8.1's hls muxer made on a source with these keyframes (hls_time 4, one continuous run): each cut is the first
# keyframe after the previous cut that is at least k segment lengths past the first keyframe.
IRREGULAR = [0, 1.5, 3.917, 4.125, 6.208, 7, 13.917, 14.125, 20, 21, 25.5, 29.917, 30, 41, 45.208, 49, 52, 59.5, 60, 67, 77, 80.125, 90]
IRREGULAR_CUTS = [0, 4.125, 13.917, 14.125, 20, 21, 25.5, 29.917, 41, 45.208, 49, 52, 59.5, 60, 67, 77, 80.125, 90]
# Copies H.264 and AC-3, so a 'mkv' only needs a remux; hevc/aac/h264-480 variants below force the other decisions.
HLS_COPY = {
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac,ac3"}],
    "TranscodingProfiles": [{"Type": "Video", "Container": "mp4", "Protocol": "hls", "VideoCodec": "h264", "AudioCodec": "aac,ac3"}],
}
# H.264 only, so an HEVC file's video is encoded.
HLS_ENCODE = {
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    "TranscodingProfiles": [{"Type": "Video", "Container": "mp4", "Protocol": "hls", "VideoCodec": "h264", "AudioCodec": "aac"}],
}


def test_encode_plan_is_the_one_second_then_four_second_grid() -> None:
    plan = encode_plan(20.0)
    assert plan.starts == (0, 1, 5, 9, 13, 17)
    assert plan.durations == (1, 4, 4, 4, 4, 3)
    assert plan.anchors is None and plan.run_for(3).index == 3 and plan.run_for(3).seek == 9 and plan.run_for(3).length is None
    assert plan.index_at(0) == 0 and plan.index_at(4.9) == 1 and plan.index_at(5) == 2 and plan.index_at(999) == 5


def test_copy_plan_cuts_where_ffmpegs_hls_muxer_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jh, "CHUNK_SEGMENTS", 100)  # one run, as in the ffmpeg measurement
    plan = copy_plan(IRREGULAR, 99.84)
    assert list(plan.starts) == IRREGULAR_CUTS
    assert sum(plan.durations) == pytest.approx(99.84) and plan.anchors == (0,)


def test_copy_plan_chunks_start_a_run_at_a_keyframe_and_end_it_at_the_next(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jh, "CHUNK_SEGMENTS", 5)
    keyframes = [float(t) for t in range(0, 100, 2)]  # a keyframe every 2 s: a cut every 4 s
    plan = copy_plan(keyframes, 100)
    assert plan.anchors == (0, 5, 10, 15, 20) and plan.starts[:6] == (0, 4, 8, 12, 16, 20)
    first, second = plan.run_for(3), plan.run_for(7)
    assert (first.index, first.seek, first.length) == (0, 0, 20) and (second.index, second.seek, second.length) == (5, 20, 20)
    assert plan.run_for(24).length is None and plan.run_end(first) == 5 and plan.run_end(plan.run_for(24)) == 25


def test_copy_plan_ends_a_chunk_at_the_last_cut_before_the_resume_position(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jh, "CHUNK_SEGMENTS", 5)
    keyframes = [float(t) for t in range(0, 100, 2)]
    plan = copy_plan(keyframes, 100, pin=47)
    run = plan.run_for(plan.index_at(47))
    assert (run.seek, plan.starts[run.index]) == (44, 44)  # the first run starts on the segment holding 47 s, not 40 s
    assert list(plan.starts) == [float(t) for t in range(0, 100, 4)]  # the cuts themselves do not move
    assert copy_plan([], 100) is None and copy_plan([200.0], 100) is None


def test_playlist_is_a_signed_vod_list_of_every_segment() -> None:
    text = Plan((0, 4), (4, 2.5)).playlist("tok/en")
    assert text.splitlines()[:7] == ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:5", "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS", '#EXT-X-MAP:URI="init.mp4?ApiKey=tok%2Fen"']
    assert text.endswith("#EXTINF:2.500000,\nseg1.m4s?ApiKey=tok%2Fen\n#EXT-X-ENDLIST\n") and "seg0.m4s?ApiKey=tok%2Fen" in text


def segment_start(directory: Path, name: str, init: Path | None = None) -> float:
    """First video timestamp of one fMP4 segment, as ffprobe reads it behind the session's init segment."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "csv=p=0", f"concat:{init or directory / 'init.mp4'}|{directory / name}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return min(float(line.split(",")[1]) for line in out.split() if line.startswith("video"))


def make_clip(path: Path, seconds: int = 60, keyframes: str | None = None, vcodec: str = "libx264") -> Path:
    """H.264 with B-frames (the veryfast default: ffmpeg seeks such a stream earlier than asked) and AC-3 in Matroska."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={seconds}", "-f", "lavfi", "-i", f"sine=duration={seconds}",
           "-c:v", vcodec, "-preset", "veryfast", "-pix_fmt", "yuv420p", "-g", "1000", "-c:a", "ac3", "-shortest"]
    if keyframes:
        cmd += ["-force_key_frames", keyframes]
    subprocess.run([*cmd, str(path)], check=True, timeout=120)
    return path


@needs_ffmpeg
def test_copy_runs_write_exactly_the_planned_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every chunk's run, seeked on a B-frame stream, reproduces the plan's cuts and lands on its keyframe (no segment missing, extra or early)."""
    monkeypatch.setattr(jh, "CHUNK_SEGMENTS", 4)
    source = make_clip(tmp_path / "irregular.mkv", 60, "1.5,3.9,4.1,6.2,7,13.9,14.1,20,21,25.5,29.9,30,41,45.2,49,52,59.5")
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)], capture_output=True, text=True).stdout)
    plan = copy_plan(jh._scan("ffprobe", source, 0), duration, pin=30)
    assert len(plan.anchors) > 3
    decision = Decision(mode="remux", video="copy", audio="copy", video_index=0, audio_index=1)
    out = tmp_path / "out"
    out.mkdir()
    for anchor in plan.anchors:
        subprocess.run(ffmpeg_command("ffmpeg", source, {}, out, decision, run=plan.run_for(anchor)), check=True, timeout=60)
    written = sorted(int(path.name[3:-4]) for path in out.glob("seg*.m4s"))
    assert written == list(range(len(plan.starts)))
    for index, start in enumerate(plan.starts):
        assert segment_start(out, f"seg{index}.m4s") == pytest.approx(start, abs=0.15), index  # <= the B-frame delay, never a whole keyframe


@needs_ffmpeg
def test_runs_of_one_file_share_an_init_segment_and_absolute_times(tmp_path: Path) -> None:
    source = make_clip(tmp_path / "clip.mkv", 30)
    plan = encode_plan(30)
    decision = Decision(mode="transcode", video="encode", audio="encode", video_index=0, audio_index=1, height=180, bitrate=1_000_000, audio_channels=2)
    inits = []
    for index in (0, 3):
        out = tmp_path / f"run{index}"
        out.mkdir()
        run = plan.run_for(index)
        subprocess.run(ffmpeg_command("ffmpeg", source, {"streams": [{"index": 0, "codec": "h264"}]}, out, decision, run=HlsRun(run.index, run.seek, 8)), check=True, timeout=60)
        inits.append((out / "init.mp4").read_bytes())
        assert segment_start(out, f"seg{index}.m4s") == pytest.approx(plan.starts[index], abs=0.15)
    assert inits[0] == inits[1]


def clip_session(vault: Vault, vcodec: str = "libx264") -> tuple:  # noqa: F811
    """A 60 s Matroska with irregular keyframes and AC-3 as the show's first episode, plus a PlaybackInfo-then-master helper."""
    make_clip(vault.media / SEASON / "Vault Show - S01E01.mkv", 60, "1.5,3.9,4.1,6.2,7,13.9,14.1,20,21,25.5,29.9,30,41,45.2,49,52,59.5", vcodec)
    scan(vault.admin, vault.media)
    who = login(vault)
    item = jellyfin_id(episodes(vault)[0]["id"])

    def open_master(profile: dict, resume: float = 0) -> tuple[dict, str, str]:
        info = vault.jf.post(f"/Items/{item}/PlaybackInfo", headers=who.headers, json={"DeviceProfile": profile, "StartTimeTicks": int(resume * 1e7)}).json()
        source = info["MediaSources"][0]
        assert vault.jf.get(source["TranscodingUrl"], headers=who.headers).status_code == 200
        return source, f"{source['TranscodingUrl'].split('/master.m3u8')[0]}/hls/{info['PlaySessionId']}", info["PlaySessionId"]

    return who, open_master


def listed(vault: Vault, who, base: str) -> tuple[list[float], str]:  # noqa: ANN001
    text = vault.jf.get(f"{base}/index.m3u8", headers=who.headers).text
    durations = [float(value) for value in re.findall(r"EXTINF:([\d.]+)", text)]
    return [sum(durations[:i]) for i in range(len(durations))], text


def fetch(vault: Vault, who, base: str, name: str, tmp: Path) -> Path:  # noqa: ANN001
    response = vault.jf.get(f"{base}/{name}", headers=who.headers)
    assert response.status_code == 200, (name, response.status_code)
    (tmp / name).write_bytes(response.content)
    return tmp / name


@needs_ffmpeg
def test_a_resumed_copy_lists_the_whole_file_and_serves_any_segment_of_it(vault: Vault, tmp_path: Path) -> None:  # noqa: F811
    who, open_master = clip_session(vault)
    source, base, play_session = open_master(HLS_COPY, resume=47)
    assert "StartTimeTicks" not in source["TranscodingUrl"]  # Jellyfin keeps it out of HLS URLs: the client seeks inside the full timeline
    starts, text = listed(vault, who, base)
    assert "#EXT-X-ENDLIST" in text and "#EXT-X-PLAYLIST-TYPE:VOD" in text and f"ApiKey={who.token}" in text
    assert starts[0] == 0 and starts[-1] > 50  # 0 to the end of the 60 s file, not a window that starts at the resume point
    init = fetch(vault, who, base, "init.mp4", tmp_path)
    session = next(s for s in lps.sessions._sessions.values() if s.play_session_id == play_session)
    assert session.plan is not None and session.run.seek == pytest.approx(session.plan.starts[session.plan.index_at(47)])

    landed = max(i for i, start in enumerate(starts) if start <= 47)
    jumps = [landed, landed + 1, 0, 1, len(starts) - 1, landed, 3]  # resume, play on, back to the start, to the end, back, forward
    runs = []
    for index in jumps:
        path = fetch(vault, who, base, f"seg{index}.m4s", tmp_path)
        assert segment_start(tmp_path, path.name, init) == pytest.approx(starts[index], abs=0.15), index
        runs.append(session.process.pid)
    assert runs[0] == runs[1]  # the next segment came from the run that was already going
    assert runs[2] != runs[1]  # the jump back to 0 started a run there


@needs_ffmpeg
def test_a_resumed_encode_lists_the_whole_file_on_the_encoders_grid(vault: Vault, tmp_path: Path) -> None:  # noqa: F811
    if "libx265" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
        pytest.skip("ffmpeg lacks libx265")
    who, open_master = clip_session(vault, "libx265")
    source, base, play_session = open_master(HLS_ENCODE, resume=33)
    assert "VideoCodecNotSupported" in source["TranscodingUrl"] and "StartTimeTicks" not in source["TranscodingUrl"]
    starts, text = listed(vault, who, base)
    plan = encode_plan(60)
    assert starts == pytest.approx(list(plan.starts), abs=0.01) and "#EXT-X-ENDLIST" in text
    init = fetch(vault, who, base, "init.mp4", tmp_path)
    for index in (plan.index_at(33), plan.index_at(33) + 1, 0, len(starts) - 1):
        path = fetch(vault, who, base, f"seg{index}.m4s", tmp_path)
        assert segment_start(tmp_path, path.name, init) == pytest.approx(starts[index], abs=0.2), index


@needs_ffmpeg
def test_segments_requested_together_do_not_pile_up_ffmpeg_runs(vault: Vault, tmp_path: Path) -> None:  # noqa: F811
    who, open_master = clip_session(vault)
    _, base, play_session = open_master(HLS_COPY)
    starts, _ = listed(vault, who, base)
    session = next(s for s in lps.sessions._sessions.values() if s.play_session_id == play_session)
    session.process.wait(timeout=30)  # the copy finished; forget its tail, as if the run had stopped short of it
    for path in session.directory.glob("seg*.m4s"):
        if int(path.name[3:-4]) > 2:
            path.unlink()
    spawned, original = [], session.respawn
    session.respawn = lambda run: spawned.append(run.index) or original(run)
    last = len(starts) - 1
    with ThreadPoolExecutor(6) as pool:  # a player prefetching the tail of the file while the first run is far behind
        codes = list(pool.map(lambda index: vault.jf.get(f"{base}/seg{index}.m4s", headers=who.headers).status_code, [last, last - 1, last, last - 2, last - 1, last]))
    assert codes == [200] * 6 and len(spawned) == 1 and threading.active_count() < 50


@needs_ffmpeg
def test_a_copy_without_keyframes_keeps_the_growing_playlist_from_zero(vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: F811
    monkeypatch.setattr(jh, "keyframes", lambda *args, **kwargs: None)  # the scan has not finished (a big file on a slow disk)
    who, open_master = clip_session(vault)
    _, base, play_session = open_master(HLS_COPY, resume=47)
    session = next(s for s in lps.sessions._sessions.values() if s.play_session_id == play_session)
    assert session.plan is None
    starts, text = listed(vault, who, base)
    assert "PLAYLIST-TYPE:EVENT" in text and starts[0] == 0  # the timeline starts at the file's start, whatever the resume position
    init = fetch(vault, who, base, "init.mp4", tmp_path)
    path = fetch(vault, who, base, "seg0.m4s", tmp_path)
    assert segment_start(tmp_path, path.name, init) < 1


def test_keyframes_are_scanned_once_per_file_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jh._keyframes.clear()
    calls = []
    monkeypatch.setattr(jh, "_scan", lambda ffprobe, path, index: calls.append(path) or [0.0, 4.0])
    media = tmp_path / "a.mkv"
    media.write_bytes(b"x")
    assert jh.keyframes("ffprobe", media, 0, wait=5) == [0.0, 4.0] and jh.keyframes("ffprobe", media, 0) == [0.0, 4.0]
    media.write_bytes(b"xy")  # a changed file is another version
    assert jh.keyframes("ffprobe", media, 0, wait=5) == [0.0, 4.0] and len(calls) == 2
    monkeypatch.setattr(jh, "_scan", lambda *args: [])
    other = tmp_path / "b.mkv"
    other.write_bytes(b"x")
    assert jh.keyframes("ffprobe", other, 0, wait=5) is None  # a failed scan is remembered, not retried per request
    assert jh.plan_for("copy", "ffprobe", other, {"duration": 60}, 0) is None and jh.plan_for("encode", None, other, {"duration": 60}, 0).starts[:3] == (0, 1, 5)
    assert jh.plan_for("encode", None, other, {"duration": 0.5}, 0) is None and jh.plan_for(None, "ffprobe", other, {"duration": 60}, 0) is None
    jh._keyframes.clear()
