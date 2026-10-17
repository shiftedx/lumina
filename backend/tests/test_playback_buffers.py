"""Playback buffers follow media time and retain only regenerable seek history."""
import asyncio
import json
import os
import shutil
import subprocess

import pytest

from app.services.jellyfin_hls import HlsRun, Plan
from app.services.local_playback_sessions import _tree_bytes
from tests.test_transcode_sessions import REMUX, registry, start, stopped  # noqa: F401 - shared fixture


def test_active_response_hard_links_do_not_double_count_cache_bytes(tmp_path):
    source = tmp_path / "seg0.m4s"
    source.write_bytes(b"segment")
    pin = tmp_path / ".response"
    pin.mkdir()
    os.link(source, pin / source.name)
    assert _tree_bytes(tmp_path) == len(b"segment")


def test_long_gop_copy_throttles_at_media_time_not_segment_count(registry, monkeypatch):
    monkeypatch.setenv("FAKE_SEGMENTS", "21")
    session = start(registry, REMUX)
    session.plan = Plan(tuple(float(i * 10) for i in range(100)), (10.0,) * 100)
    session.run = HlsRun(0, 0)
    registry.file(session, "seg0.m4s")
    assert session.throttled and stopped(session.process.pid)
    registry.file(session, "seg16.m4s")
    assert not session.throttled and not stopped(session.process.pid, expect=False)


def test_seekable_history_prunes_muxed_and_roku_segments_together(registry):
    session = start(registry, REMUX)
    session.plan = Plan(tuple(float(i * 10) for i in range(100)), (10.0,) * 100)
    session.run = HlsRun(0, 0)
    for i in range(80):
        for prefix in ("", "audio-", "video-"):
            (session.directory / f"{prefix}seg{i}.m4s").write_bytes(b"segment")
    (session.directory / "audio-init.mp4").write_bytes(b"init")
    session.max_requested = 50
    registry.maintain_buffers()
    for prefix in ("", "audio-", "video-"):
        assert not (session.directory / f"{prefix}seg42.m4s").exists()
        assert (session.directory / f"{prefix}seg44.m4s").exists()
        assert (session.directory / f"{prefix}seg50.m4s").exists()
    assert (session.directory / "audio-init.mp4").exists()


def test_pruned_history_restarts_even_while_the_original_encoder_is_running(registry):
    session = start(registry, REMUX)
    session.plan = Plan(tuple(float(i * 10) for i in range(100)), (10.0,) * 100)
    session.run = HlsRun(0, 0)
    session.max_requested = 8
    assert session.process.poll() is None
    assert registry._needs_run(session, 0)


def test_restart_invalidates_split_derivatives_with_their_source(registry):
    session = start(registry, REMUX)
    session.plan = Plan((0.0, 4.0, 8.0), (4.0,) * 3)
    session.run = HlsRun(0, 0)
    session.respawn = lambda run: subprocess.Popen(["sleep", "60"])
    for i in range(3):
        for prefix in ("", "audio-", "video-"):
            (session.directory / f"{prefix}seg{i}.m4s").write_bytes(b"old")
    with session.seek_lock:
        registry._restart(session, HlsRun(1, 4))
    for prefix in ("", "audio-", "video-"):
        assert (session.directory / f"{prefix}seg0.m4s").exists()
        assert not (session.directory / f"{prefix}seg1.m4s").exists()
        assert not (session.directory / f"{prefix}seg2.m4s").exists()


def test_growing_web_playlist_keeps_its_advertised_history(registry):
    session = start(registry, REMUX)
    (session.directory / "seg0.m4s").write_bytes(b"old")
    session.max_requested = 100
    registry.maintain_buffers()
    assert (session.directory / "seg0.m4s").exists()


def test_buffer_tick_runs_without_a_client_request_and_survives_failure(monkeypatch):
    from app import main

    calls = []

    async def sleep(seconds):
        assert seconds == 1
        if len(calls) == 2:
            raise asyncio.CancelledError

    def maintain():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("temporary failure")

    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    monkeypatch.setattr(main.local_playback_sessions, "maintain_buffers", maintain)
    try:
        asyncio.run(main.playback_buffer_loop())
    except asyncio.CancelledError:
        pass
    assert len(calls) == 2


def test_pruned_segment_is_regenerated_and_decodable_after_backward_seek(tmp_path, monkeypatch):
    from app.services import local_playback_sessions as lps
    from app.services.jellyfin_hls import encode_plan
    from app.services.playback_decision import Decision

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe required")
    source = tmp_path / "synthetic.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24:duration=14", "-c:v", "libx264", "-preset", "ultrafast", str(source)],
                   check=True, timeout=20)
    monkeypatch.setattr(lps, "RETAIN_BEHIND_SECONDS", 4)
    sessions = lps.LocalPlaybackSessions()
    try:
        decision = Decision("transcode", video="encode", video_index=0, height=180, bitrate=200_000)
        session = sessions.start(user_id="u", item_id="i", ffmpeg=ffmpeg, source=source,
                                 facts={"duration": 14, "streams": [{"index": 0, "codec": "h264"}]},
                                 decision=decision, cap=1, hwaccel_mode="off", plan=encode_plan(14))
        assert sessions.segment(session, "seg3.m4s") is not None
        sessions.maintain_buffers()
        assert not (session.directory / "seg0.m4s").exists()
        path = sessions.segment(session, "seg0.m4s")
        assert path is not None and session.run.index == 0, (
            session.process.poll(), lps._tail(session.directory / "ffmpeg.log", 2000),
            sorted(p.name for p in session.directory.iterdir()),
        )
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_name,start_time",
                                 "-of", "json", f"concat:{session.directory / 'init.mp4'}|{path}"],
                                capture_output=True, text=True, check=True, timeout=10)
        stream = json.loads(result.stdout)["streams"][0]
        assert stream["codec_name"] == "h264" and abs(float(stream["start_time"])) < 0.2
    finally:
        sessions.close_all()
