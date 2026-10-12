"""A 1 s first HLS segment, a 25 ms manifest poll, free video slots and the session kind on the wire."""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import local_playback_sessions as lps
from app.services.playback_decision import Decision
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401

VIDEO = Decision("transcode", video="encode", audio="encode", video_index=0, audio_index=1, height=120, bitrate=500_000, audio_channels=2)
REMUX = Decision("remux", video="copy", audio="copy", video_index=0, audio_index=1)
FACTS = {"duration": 10.0, "streams": [{"index": 0, "type": "video", "codec": "h264", "height": 120}, {"index": 1, "type": "audio", "codec": "aac", "channels": 2}]}
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")


def test_hls_cuts_four_one_second_segments_then_four_second_ones() -> None:
    encode = lps.ffmpeg_command("ffmpeg", Path("/m/a.mkv"), FACTS, Path("/out"), VIDEO)
    assert encode[encode.index("-hls_init_time") + 1] == "1"
    assert encode[encode.index("-hls_time") + 1] == "4"
    assert encode[encode.index("-force_key_frames") + 1] == "expr:gte(t,if(lt(n_forced,4),n_forced,4*(n_forced-3)))"


def test_encoders_cut_only_at_the_forced_keyframes() -> None:
    """Jellyfin 10.11 EncodingHelper: libx264 gets -sc_threshold 0 so a scene cut adds no keyframe (hlsenc cuts at any
    keyframe). h264_qsv marks a forced I frame key only when it is an IDR (qsvenc.c), so it needs -forced_idr 1."""
    software = lps.ffmpeg_command("ffmpeg", Path("/m/a.mkv"), FACTS, Path("/out"), VIDEO)
    assert software[software.index("-sc_threshold") + 1] == "0"
    qsv = lps.ffmpeg_command("ffmpeg", Path("/m/a.mkv"), FACTS, Path("/out"), VIDEO, hw="qsv")
    assert qsv[qsv.index("-forced_idr") + 1] == "1"


def test_a_copy_video_remux_keeps_four_second_segments() -> None:
    # With an EVENT playlist hlsenc applies hls_init_time to every segment, so copied video would cut ~1 s segments throughout.
    command = lps.ffmpeg_command("ffmpeg", Path("/m/a.mkv"), FACTS, Path("/out"), REMUX)
    assert "-hls_init_time" not in command
    assert command[command.index("-hls_time") + 1] == "4"


@needs_ffmpeg
def test_a_real_encode_writes_one_second_first_segments(tmp_path: Path) -> None:
    from tests.test_v1_probe import make_media

    source = make_media(tmp_path / "clip.mp4", seconds=10)
    out = tmp_path / "hls"
    out.mkdir()
    subprocess.run(lps.ffmpeg_command("ffmpeg", source, FACTS, out, VIDEO), check=True, timeout=120, stdin=subprocess.DEVNULL)
    durations = [float(value) for value in re.findall(r"#EXTINF:([\d.]+),", (out / "index.m3u8").read_text())]
    assert durations[:5] == [pytest.approx(1, abs=0.1)] * 4 + [pytest.approx(4, abs=0.1)]


def test_the_manifest_wait_polls_every_25_ms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "ffmpeg"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nsleep 0.2\nprintf "#EXTM3U\\n#EXT-X-ENDLIST\\n" > "$last"\nexec sleep 60\n')
    fake.chmod(0o755)
    naps: list[float] = []

    def nap(seconds: float) -> None:
        naps.append(seconds)
        time.sleep(seconds)

    monkeypatch.setattr(lps, "time", SimpleNamespace(monotonic=time.monotonic, sleep=nap))
    registry = lps.LocalPlaybackSessions()
    try:
        registry.start(user_id="alice", item_id="i1", ffmpeg=str(fake), source=Path("/media/x.mkv"), facts=FACTS, decision=REMUX, cap=2)
    finally:
        registry.close_all()
    assert naps and set(naps) == {0.025}


@needs_ffmpeg
def test_playback_options_report_free_video_slots(household: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from tests.test_v1_probe import make_media

    item_id = _download("alice", make_media(settings.library_root / "alice" / "clip.mp4"))
    for running, free in ((1, 1), (5, 0)):
        monkeypatch.setattr(lps.sessions, "video_encodes", lambda running=running: running)
        with _client("alice") as client:
            assert client.get(f"/api/library/{item_id}/playback-options").json()["free_video_slots"] == free


@needs_ffmpeg
def test_a_started_session_reports_its_kind(household: None) -> None:  # noqa: F811
    from tests.test_v1_probe import make_media

    item_id = _download("alice", make_media(settings.library_root / "alice" / "clip.mkv", fmt="matroska"))
    try:
        with _client("alice") as client:
            response = client.post(f"/api/library/{item_id}/playback-sessions")
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["kind"] == lps.sessions.get(body["session_id"], "alice").kind
    finally:
        lps.sessions.close_all()


def _boxes(data: bytes, wanted: str) -> list[bytes]:
    """The payloads of every ``wanted`` box, descending through the containers an fMP4 nests it in."""
    found, offset = [], 0
    while offset + 8 <= len(data):
        size, kind = int.from_bytes(data[offset:offset + 4], "big"), data[offset + 4:offset + 8].decode("latin-1")
        body = data[offset + 8:offset + size]
        if kind == wanted:
            found.append(body)
        elif kind in {"moov", "trak", "edts", "moof", "traf"}:
            found += _boxes(body, wanted)
        offset += size
    return found


@needs_ffmpeg
@pytest.mark.parametrize("decision", [REMUX, VIDEO], ids=["copy", "encode"])
def test_a_resumed_session_times_its_fragments_not_its_init_segment(tmp_path: Path, decision: Decision) -> None:
    """Without frag_discont hlsenc times the first fragment 0 and puts the resume offset in an empty edit in init.mp4.
    hls.js shifts the fragment to its playlist time and Firefox applies the empty edit as well, so a resume buffered at
    twice its position and hls.js reloaded the first two fragments forever (2.7.3)."""
    from tests.test_v1_probe import make_media

    source = make_media(tmp_path / "clip.mp4", seconds=10)
    out = tmp_path / "hls"
    out.mkdir()
    subprocess.run(lps.ffmpeg_command("ffmpeg", source, FACTS, out, decision, start=4), check=True, timeout=120, stdin=subprocess.DEVNULL)
    for elst in _boxes((out / "init.mp4").read_bytes(), "elst"):  # v0: 4-byte header, count, then (duration, media_time, rate)
        media_times = [int.from_bytes(elst[12 + 12 * i:16 + 12 * i], "big", signed=True) for i in range(int.from_bytes(elst[4:8], "big"))]
        assert -1 not in media_times
    traf = _boxes((out / "seg0.m4s").read_bytes(), "tfdt")
    assert traf and all(int.from_bytes(tfdt[4:], "big") > 0 for tfdt in traf)
