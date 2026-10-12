"""Per-device sessions, encode-only caps, fallback, throttling, cache, Jellyfin playlists."""
from __future__ import annotations

import subprocess
import threading
import time
from collections import namedtuple
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import local_playback_sessions as lps
from app.services.hwaccel import HwStatus, hwaccel
from app.services.local_playback_sessions import ConversionError, LocalPlaybackSessions, SessionCapError, SupersededError, master_playlist, with_api_key
from app.services.playback_decision import Decision

# A stand-in ffmpeg: optional failure on hardware args, N finished segments, a progress line, a manifest, then it runs on
# as the same shell (no exec after the manifest, or a throttle's SIGSTOP can land mid-execve and be lost).
FAKE = """#!/bin/sh
for last; do :; done
dir=$(dirname "$last")
case " $* " in *" -init_hw_device "*) if [ -n "$FAKE_FAIL_HW" ]; then echo "Device creation failed: -12" >&2; exit 1; fi
  if [ -n "$FAKE_SLOW_HW" ]; then exec sleep 60; fi;; esac
i=0
while [ "$i" -lt "${FAKE_SEGMENTS:-0}" ]; do : > "$dir/seg$i.m4s"; i=$((i + 1)); done
printf 'frame=10\\nspeed=1.52x\\nprogress=continue\\n' > "$dir/progress"
if [ -n "$FAKE_GROW" ]; then printf '#EXTM3U\n#EXTINF:10,\nseg0.m4s\n' > "$last"; sleep 0.4; printf '#EXTM3U\n#EXTINF:10,\nseg0.m4s\n#EXTINF:10,\nseg1.m4s\n#EXTINF:10,\nseg2.m4s\n' > "$last"
else echo "#EXTM3U" > "$last"; echo "#EXT-X-ENDLIST" >> "$last"; fi
n=0; while [ "$n" -lt 60 ]; do sleep 1; n=$((n + 1)); done
"""
FACTS = {"duration": 600.0, "bit_rate": 3_000_000, "streams": [
    {"index": 0, "type": "video", "codec": "mpeg4", "height": 480}, {"index": 1, "type": "audio", "codec": "ac3", "channels": 2, "default": True}]}
VIDEO = Decision("transcode", video="encode", audio="encode", video_index=0, audio_index=1, height=480, bitrate=1_500_000, audio_channels=2)
REMUX = Decision("remux", video="copy", audio="copy", video_index=0, audio_index=1)
HARDWARE = HwStatus(active="qsv", probe_ok=True, tonemap="opencl", software_tonemap="software")


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    hwaccel.__init__()
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus())
    registry = LocalPlaybackSessions()
    registry.ffmpeg = str(fake)  # test convenience
    yield registry
    registry.close_all()
    hwaccel.__init__()


def start(registry, decision=VIDEO, *, user="alice", device="web", item="i1", cap=2, **kwargs):
    return registry.start(user_id=user, item_id=item, ffmpeg=registry.ffmpeg, source=Path("/media/x.avi"), facts=FACTS, decision=decision, cap=cap, device=device, **kwargs)


def stopped(pid: int, expect: bool = True) -> bool:
    """Whether ``pid`` is stopped; polls up to ~5 s for ``expect`` because signal delivery is asynchronous (and slow under load)."""
    deadline = time.monotonic() + 5
    while True:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip().startswith("T")
        if state == expect or time.monotonic() > deadline:
            return state
        time.sleep(0.05)


def test_each_device_of_a_member_gets_its_own_session(registry) -> None:
    web = start(registry)
    tv = start(registry, device="app-1")
    assert web.process.poll() is None and tv.process.poll() is None
    replaced = start(registry, item="i2")  # the same device moves on: its old conversion yields
    assert web.process.poll() is not None and not web.directory.exists()
    assert set(registry._sessions) == {tv.id, replaced.id}
    assert start(registry, device="app-1").id == tv.id  # same item, start and choice: reused


def test_the_cap_counts_video_encodes_only(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    start(registry, cap=1)
    with pytest.raises(SessionCapError, match="busy"):
        start(registry, user="bob", cap=1)
    start(registry, REMUX, user="bob", cap=1)  # copies are cheap and not held to the encode cap
    monkeypatch.setattr(lps, "OTHER_SESSIONS", 1)
    with pytest.raises(SessionCapError):
        start(registry, REMUX, user="carol", cap=1)


def test_low_disk_refuses_new_sessions(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(lps.shutil, "disk_usage", lambda path: usage(10, 10, 0))
    with pytest.raises(SessionCapError, match="low on disk space"):
        start(registry, min_free_bytes=1)
    assert registry._sessions == {}


def test_throttle_pauses_far_ahead_and_resumes_near_the_edge(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SEGMENTS", "41")  # seg0..seg40 already encoded (160 s)
    session = start(registry)
    registry.file(session, "seg0.m4s")  # 160 s ahead of the player: > 120 s
    assert session.throttled and stopped(session.process.pid)
    registry.file(session, "seg20.m4s")  # 80 s ahead: still above 60 s
    assert session.throttled
    registry.file(session, "seg26.m4s")  # 56 s ahead: resume
    assert not session.throttled and not stopped(session.process.pid, expect=False)
    assert registry.snapshot()["throttled"] == 0


def test_the_stand_in_does_not_exec_after_its_manifest() -> None:
    """macOS drops a SIGSTOP that lands mid-execve, so a throttle right after the manifest must not race an exec."""
    assert "exec" not in FAKE.split('> "$last"', 1)[1]


def test_stopping_a_throttled_child_is_prompt(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SEGMENTS", "41")
    session = start(registry)
    registry.file(session, "index.m3u8")  # manifest polls throttle too (nothing requested yet)
    assert session.throttled
    began = time.monotonic()
    assert registry.stop(session.id)
    assert time.monotonic() - began < 2 and session.process.poll() is not None


def test_over_cap_sessions_are_evicted_least_recent_first(registry) -> None:
    old, older, fresh = (start(registry, REMUX, user=name) for name in "abc")
    for session in (old, older, fresh):
        (session.directory / "seg9.m4s").write_bytes(b"x" * 1000)
    now = time.monotonic()
    old.last_access, older.last_access = now - 100, now - 200
    registry.cache_cap_bytes = 2500
    registry.reap()
    assert set(registry._sessions) == {old.id, fresh.id}  # the least recently read went first
    registry.cache_cap_bytes = 1
    registry.reap()
    assert set(registry._sessions) == {fresh.id}  # a session read in the last 30 s is never evicted


def test_hardware_failure_retries_once_in_software(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HARDWARE)
    assert start(registry).kind == "video_hw" and hwaccel.failures == 0
    monkeypatch.setenv("FAKE_FAIL_HW", "1")
    session = start(registry, item="i2")
    assert session.kind == "video_sw" and session.process.poll() is None
    assert hwaccel.fallbacks == 1 and "Device creation failed" in lps.recent_errors[0]
    start(registry, item="i3")
    start(registry, item="i4")
    assert hwaccel.disabled and hwaccel.fallbacks == 3
    assert start(registry, item="i5").kind == "video_sw" and hwaccel.fallbacks == 3  # no more hardware attempts


def test_a_hardware_session_replaced_before_its_playlist_is_not_a_hardware_failure(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """Final review #1: a far-seek or quality change supersedes a pending hardware session; no failure, no retry."""
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HARDWARE)
    monkeypatch.setenv("FAKE_SLOW_HW", "1")  # the hardware child never writes its first playlist
    outcome: list[object] = []

    def first() -> None:
        try:
            outcome.append(start(registry))
        except Exception as exc:  # noqa: BLE001 - the thread reports what start() raised
            outcome.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    deadline = time.monotonic() + 5
    while not registry._sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    replacement = start(registry, REMUX, item="i2")  # same member and device
    thread.join(10)
    assert isinstance(outcome[0], SupersededError)
    assert hwaccel.failures == 0 and hwaccel.fallbacks == 0
    assert list(registry._sessions) == [replacement.id]


def test_dolby_vision_5_is_not_retried_in_software(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """Final review #3: the software tone-map cannot reshape DV5, so a failed hardware session is refused, not retried."""
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HARDWARE)
    monkeypatch.setenv("FAKE_FAIL_HW", "1")
    facts = {**FACTS, "streams": [{**FACTS["streams"][0], "codec": "hevc", "dv_profile": 5}, FACTS["streams"][1]]}
    with pytest.raises(ConversionError):
        registry.start(user_id="alice", item_id="i1", ffmpeg=registry.ffmpeg, source=Path("/media/x.mkv"), facts=facts,
                       decision=replace(VIDEO, tonemap=True), cap=2)
    assert hwaccel.failures == 1 and not registry._sessions


def test_stop_where_targets_a_device_or_a_play_session(registry) -> None:
    web = start(registry, REMUX, play_session_id="p1")
    tv = start(registry, REMUX, device="app-1", play_session_id="p2")
    bob = start(registry, REMUX, user="bob", device="app-1", play_session_id="p2")
    assert registry.stop_where(user_id="alice", play_session_id="p2") == 1
    assert tv.id not in registry._sessions and web.id in registry._sessions
    assert registry.stop_where(user_id="alice") == 1
    assert list(registry._sessions) == [bob.id]


def test_snapshot_counts_kinds_and_reads_speed(registry) -> None:
    start(registry)
    start(registry, REMUX, user="bob")
    snap = registry.snapshot()
    assert snap["sessions"] == {"video_sw": 1, "remux": 1}
    assert sorted(snap["speeds"].values()) == [1.52, 1.52]
    assert snap["cache_bytes"] > 0
    assert registry.video_encodes() == 1


def test_jellyfin_playlists_carry_the_api_key_on_every_uri() -> None:
    playlist = '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4.0,\nseg0.m4s\n#EXTINF:4.0,\nseg1.m4s?x=1\n'
    signed = with_api_key(playlist, "a b/c&d")
    assert '#EXT-X-MAP:URI="init.mp4?ApiKey=a%20b%2Fc%26d"' in signed
    assert "\nseg0.m4s?ApiKey=a%20b%2Fc%26d\n" in signed and "\nseg1.m4s?x=1&ApiKey=a%20b%2Fc%26d\n" in signed
    assert "#EXTINF:4.0," in signed
    session = lps.PlaybackSession("s", "u", "i", "transcode", Path("/x"), None, bandwidth=4_000_000)  # type: ignore[arg-type]
    assert master_playlist(session, "hls/s/index.m3u8?ApiKey=k") == "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\nhls/s/index.m3u8?ApiKey=k\n"


def test_reap_bounds_the_ffmpeg_log(registry) -> None:
    session = start(registry, REMUX)
    log = session.directory / "ffmpeg.log"
    log.write_bytes(b"x" * (lps.LOG_LIMIT_BYTES + 1))
    registry.reap()
    assert log.stat().st_size == 0 and session.id in registry._sessions  # ≤ 64 KB, session untouched


def test_a_copy_restart_reports_where_its_first_segment_really_starts(registry, tmp_path: Path) -> None:
    """Copied video starts at the keyframe before ``start``; the player's timeline must use that, or it stalls at the first segment's end.

    Encoded audio must start at that keyframe too: a segment whose audio begins seconds after its video misplaces hls.js's fragments.
    """
    source = tmp_path / "gop5.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=25:duration=12", "-f", "lavfi", "-i", "sine=duration=12",
                    "-c:v", "libx264", "-g", "125", "-keyint_min", "125", "-sc_threshold", "0", "-c:a", "ac3", str(source)], check=True, timeout=60)
    facts = {"duration": 12.0, "streams": [{"index": 0, "type": "video", "codec": "h264", "height": 48}, {"index": 1, "type": "audio", "codec": "ac3", "channels": 1}]}
    copy = Decision("transcode", video="copy", audio="encode", video_index=0, audio_index=1, audio_channels=1)
    session = registry.start(user_id="alice", item_id="i1", ffmpeg="ffmpeg", ffprobe="ffprobe", source=source, facts=facts, decision=copy, cap=2, start=7)
    assert session.start == 7  # the request, for reuse
    assert session.media_start == pytest.approx(5, abs=0.1)
    starts = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,concat", "-show_entries", "stream=start_time", "-of", "csv=p=0",
                             f"concat:{session.directory / 'init.mp4'}|{session.directory / 'seg0.m4s'}"], capture_output=True, text=True, check=True).stdout.split()
    assert [float(value) for value in starts] == [pytest.approx(5, abs=0.15)] * 2
    encoded = registry.start(user_id="alice", item_id="i1", ffmpeg="ffmpeg", ffprobe="ffprobe", source=source, facts=facts, decision=replace(VIDEO, height=48), cap=2, start=7)
    assert encoded.media_start == 7  # an encode starts exactly where asked


def test_every_session_answers_once_three_segments_are_listed(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """hls.js first re-polls a live playlist ~2 target durations after loading it. A long-GOP copy's first playlist holds one
    ~10 s segment (a seek inside it stalls); an encode answering with its 1 s first segment stalled ~1 s at its end."""
    monkeypatch.setenv("FAKE_GROW", "1")
    session = start(registry, REMUX)
    assert (session.directory / "index.m3u8").read_text().count("#EXTINF") == 3
    encoded = start(registry, VIDEO, device="tv")
    assert (encoded.directory / "index.m3u8").read_text().count("#EXTINF") == 3
