"""Non-native local files play through bounded, authorized HLS remux/transcode sessions."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from app.models import LibraryItem
from app.routers import local_playback
from app.services import local_playback_sessions as lps
from app.services.hwaccel import HwStatus, hwaccel
from app.services.yt_dlp_service import YtDlpService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401
from tests.test_v1_probe import make_media, pytestmark  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_sessions():
    hwaccel.__init__()
    yield
    lps.sessions.close_all()
    hwaccel.__init__()


def _start(item_id: str, user: str = "alice"):
    with _client(user) as client:
        return client.post(f"/api/library/{item_id}/playback-sessions")


def _finished(session_id: str) -> Path:
    session = lps.sessions._sessions[session_id]
    assert session.process.wait(timeout=60) == 0
    return session.directory


def _probe_concat(directory: Path, tmp_path: Path) -> dict:
    joined = tmp_path / "joined.mp4"
    segments = sorted(directory.glob("seg*.m4s"), key=lambda p: int(p.stem[3:]))
    joined.write_bytes((directory / "init.mp4").read_bytes() + b"".join(p.read_bytes() for p in segments))
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(joined)], capture_output=True, check=True)
    return json.loads(out.stdout)


def test_remux_preserves_original_hash(household: None, tmp_path: Path) -> None:
    media = make_media(settings.library_root / "alice" / "clip.mkv")
    before = hashlib.sha256(media.read_bytes()).hexdigest()
    item_id = _download("alice", media)

    body = _start(item_id).json()
    assert body["mode"] == "remux" and body["playback_url"].endswith("/index.m3u8")
    directory = _finished(body["session_id"])
    with _client("alice") as client:
        manifest = client.get(body["playback_url"])
        init = client.get(f"/api/playback-sessions/{body['session_id']}/init.mp4")
    assert manifest.status_code == 200 and "#EXT-X-ENDLIST" in manifest.text
    assert manifest.headers["content-type"] == "application/vnd.apple.mpegurl"
    assert init.status_code == 200
    streams = {s["codec_type"]: s["codec_name"] for s in _probe_concat(directory, tmp_path)["streams"]}
    assert streams == {"video": "h264", "audio": "aac"}
    assert hashlib.sha256(media.read_bytes()).hexdigest() == before


def test_transcode_real_browser_av(household: None, tmp_path: Path) -> None:
    media = make_media(settings.library_root / "alice" / "old.avi", vcodec="mpeg4", acodec="ac3", seconds=6)
    item_id = _download("alice", media)
    with _client("alice") as client:
        assert client.get(f"/api/library/{item_id}/playback-options").json()["mode"] == "transcode"

    body = _start(item_id).json()
    assert body["mode"] == "transcode"
    probed = _probe_concat(_finished(body["session_id"]), tmp_path)
    streams = {s["codec_type"]: s for s in probed["streams"]}
    assert (streams["video"]["codec_name"], streams["audio"]["codec_name"]) == ("h264", "aac")
    assert (streams["video"]["width"], streams["video"]["height"]) == (160, 120)
    assert 5 < float(probed["format"]["duration"]) < 7
    # Keyframes at 0, 1, 2, 3, 4 s: four 1 s segments, then independent 4 s ones.
    assert len(list(Path(_finished(body["session_id"])).glob("seg*.m4s"))) == 5


def test_concurrent_and_cancel_bounds(household: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A stand-in ffmpeg that writes a manifest and then keeps running like a long transcode.
    fake = tmp_path / "ffmpeg"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nprintf "#EXTM3U\\n#EXT-X-ENDLIST\\n" > "$last"\nexec sleep 60\n')
    fake.chmod(0o755)
    monkeypatch.setattr(local_playback, "media_tool", lambda db, name: str(fake) if name == "ffmpeg" else None)
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus())  # never probe the stand-in
    with _client("admin") as admin:
        assert admin.put("/api/admin/settings", json={"max_playback_sessions": 1}).json()["max_playback_sessions"] == 1
        assert admin.put("/api/admin/settings", json={"max_playback_sessions": 99}).status_code == 422

    old = {"vcodec": "mpeg4", "acodec": "ac3"}  # browser-foreign video: a real encode
    alice_item = _download("alice", make_media(settings.library_root / "alice" / "a.avi", **old), remote_id="a")
    bob_item = _download("bob", make_media(settings.library_root / "bob" / "b.avi", **old), remote_id="b")
    bob_mkv = _download("bob", make_media(settings.library_root / "bob" / "c.mkv"), remote_id="c")
    alice = _start(alice_item).json()
    assert _start(alice_item).json()["session_id"] == alice["session_id"]  # reused, not a second child
    assert _start(bob_item, "bob").status_code == 429  # one video encode allowed
    assert _start(bob_mkv, "bob").status_code == 201  # a remux is not held to the encode cap

    process = lps.sessions._sessions[alice["session_id"]].process
    directory = lps.sessions._sessions[alice["session_id"]].directory
    with _client("bob") as bob:
        assert bob.get(alice["playback_url"]).status_code == 404  # another member's session
        assert bob.delete(f"/api/playback-sessions/{alice['session_id']}").status_code == 204
    assert process.poll() is None  # bob cannot cancel alice's session
    with _client("alice") as client:
        assert client.get(f"/api/playback-sessions/{alice['session_id']}/..%2Findex.m3u8").status_code == 404
        assert client.delete(f"/api/playback-sessions/{alice['session_id']}").status_code == 204
        assert client.get(alice["playback_url"]).status_code == 404
    assert process.poll() is not None and not directory.exists()  # cancellation killed the child

    bob = _start(bob_item, "bob").json()
    session = lps.sessions._sessions[bob["session_id"]]
    session.last_access = time.monotonic() - lps.IDLE_SECONDS - 1
    lps.sessions.reap()
    assert session.process.poll() is not None and not session.directory.exists()  # idle expiry


def test_restart_at_offset_replaces_the_old_generation(household: None, tmp_path: Path) -> None:
    """#137: a seek (or resume) past the converted part restarts ffmpeg there."""
    media = make_media(settings.library_root / "alice" / "long.mkv", vcodec="libx265", seconds=60)
    item_id = _download("alice", media)
    first = _start(item_id).json()
    old = lps.sessions._sessions[first["session_id"]]

    with _client("alice") as client:
        restarted = client.post(f"/api/library/{item_id}/playback-sessions?start=45").json()
        assert client.post(f"/api/library/{item_id}/playback-sessions?start=-1").status_code == 422
    assert restarted["start"] == 45 and restarted["session_id"] != first["session_id"]
    assert old.process.poll() is not None and not old.directory.exists()  # old ffmpeg is gone
    assert list(lps.sessions._sessions) == [restarted["session_id"]]

    directory = _finished(restarted["session_id"])
    probed = _probe_concat(directory, tmp_path)
    video = next(s for s in probed["streams"] if s["codec_type"] == "video")
    assert 44 <= float(video["start_time"]) <= 46  # first segment starts near 45 s
    with _client("alice") as client:
        manifest = client.get(restarted["playback_url"]).text
    # Only the remaining ~15 s: four 1 s segments, then independent 4 s ones (1+1+1+1+4+4+3).
    assert 14 < sum(float(line[8:].rstrip(",")) for line in manifest.splitlines() if line.startswith("#EXTINF:")) < 16.5
    assert len(list(directory.glob("seg*.m4s"))) == 7


def test_mkv_ac3_copies_video_and_encodes_audio(household: None, tmp_path: Path) -> None:
    item_id = _download("alice", make_media(settings.library_root / "alice" / "ac3.mkv", acodec="ac3", seconds=6))
    with _client("alice") as client:
        options = client.get(f"/api/library/{item_id}/playback-options").json()
    assert (options["mode"], options["reason"]) == ("transcode", "AudioCodecNotSupported")
    body = _start(item_id).json()
    session = lps.sessions._sessions[body["session_id"]]
    assert session.kind == "audio"  # the video is copied: not held to the encode cap
    directory = _finished(body["session_id"])
    streams = {s["codec_type"]: s["codec_name"] for s in _probe_concat(directory, tmp_path)["streams"]}
    assert streams == {"video": "h264", "audio": "aac"}
    assert "progress=end" in (directory / "progress").read_text()


def test_quality_choice_encodes_at_that_height(household: None, tmp_path: Path) -> None:
    item_id = _download("alice", make_media(settings.library_root / "alice" / "hd.mp4", size="1280x720", seconds=4))
    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/playback-sessions", json={"max_height": 720}).status_code == 409  # already fits
        body = client.post(f"/api/library/{item_id}/playback-sessions", json={"max_height": 480}).json()
    video = next(s for s in _probe_concat(_finished(body["session_id"]), tmp_path)["streams"] if s["codec_type"] == "video")
    assert (video["codec_name"], video["height"]) == ("h264", 480)


def test_version_choice_stays_within_the_title(household: None) -> None:
    first = _download("alice", make_media(settings.library_root / "alice" / "v1.mkv"), remote_id="v1")
    second = _download("alice", make_media(settings.library_root / "alice" / "v2.mkv"), remote_id="v2")
    foreign = _download("alice", make_media(settings.library_root / "alice" / "x.mkv"), remote_id="x")
    with db_module.SessionLocal() as db:
        for item_id, title_id in ((first, "title-1"), (second, "title-1"), (foreign, "title-2")):
            db.get(LibraryItem, item_id).title_id = title_id
        db.commit()
    with _client("alice") as client:
        chosen = client.post(f"/api/library/{first}/playback-sessions", json={"version_id": second})
        assert chosen.status_code == 201 and lps.sessions._sessions[chosen.json()["session_id"]].item_id == second
        assert client.post(f"/api/library/{first}/playback-sessions", json={"version_id": foreign}).status_code == 404
        assert client.post(f"/api/library/{first}/playback-sessions", json={"subtitle": "s:../x"}).status_code == 422
        assert client.post(f"/api/library/{first}/playback-sessions", json={"max_height": 100000}).status_code == 422
    with _client("bob") as bob:
        assert bob.post(f"/api/library/{first}/playback-sessions", json={"version_id": second}).status_code == 404


def test_hdr10_tone_maps_in_software(household: None, tmp_path: Path) -> None:
    tools = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    tools += subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if "libx265" not in tools or " zscale " not in tools:
        pytest.skip("needs libx265 and zscale (jellyfin-ffmpeg has both)")
    hdr = settings.library_root / "alice" / "hdr.mkv"
    hdr.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=4", "-f", "lavfi", "-i", "sine=duration=4",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-c:a", "aac", "-shortest", str(hdr),
    ], check=True, timeout=120)
    body = _start(_download("alice", hdr)).json()
    video = next(s for s in _probe_concat(_finished(body["session_id"]), tmp_path)["streams"] if s["codec_type"] == "video")
    assert (video["codec_name"], video["pix_fmt"]) == ("h264", "yuv420p")
    assert video.get("color_transfer") != "smpte2084"


def test_qsv_on_a_host_without_it_plays_in_software(household: None) -> None:
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().hwaccel = "qsv"
        db.commit()
    response = _start(_download("alice", make_media(settings.library_root / "alice" / "old.avi", vcodec="mpeg4", acodec="ac3")))
    assert response.status_code == 201
    (status,) = hwaccel._cache.values()
    if status.probe_ok:
        pytest.skip("this host really has QSV")
    assert status.active == "none" and status.probe_error.startswith("qsv:")
    assert lps.sessions._sessions[response.json()["session_id"]].kind == "video_sw"


def test_a_hardware_crash_retries_in_software(household: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A bogus encoder argument makes the "hardware" child die before its first playlist, as a GPU fault would.
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus(active="qsv", probe_ok=True))
    monkeypatch.setitem(lps.ENCODERS, "qsv", ["-c:v", "no_such_encoder"])
    body = _start(_download("alice", make_media(settings.library_root / "alice" / "crash.avi", vcodec="mpeg4", acodec="ac3", seconds=4))).json()
    assert lps.sessions._sessions[body["session_id"]].kind == "video_sw"
    assert (hwaccel.fallbacks, hwaccel.disabled) == (1, False) and lps.recent_errors[0]
    streams = {s["codec_type"]: s["codec_name"] for s in _probe_concat(_finished(body["session_id"]), tmp_path)["streams"]}
    assert streams == {"video": "h264", "audio": "aac"}


def test_a_playlist_replaced_while_it_is_served_is_answered_whole(household: None) -> None:
    """ffmpeg (temp_file) renames a new playlist over index.m3u8 every segment. A response that took its length from one
    file and its body from the next broke ("Response content longer than Content-Length") and failed playback."""
    media = make_media(settings.library_root / "alice" / "clip.mkv")
    body = _start(_download("alice", media)).json()
    directory = _finished(body["session_id"])
    stop = threading.Event()

    def replace() -> None:
        lines = 1
        while not stop.is_set():
            lines = lines % 50 + 1
            (directory / "next.m3u8").write_text("#EXTM3U\n" + "#EXTINF:1.0,\nseg0.m4s\n" * lines)
            os.replace(directory / "next.m3u8", directory / "index.m3u8")

    writer = threading.Thread(target=replace)
    writer.start()
    try:
        with _client("alice") as client:
            for _ in range(300):
                response = client.get(body["playback_url"])
                assert response.status_code == 200
                assert int(response.headers["content-length"]) == len(response.content)
                assert response.text.startswith("#EXTM3U\n") and response.text.endswith("seg0.m4s\n")
    finally:
        stop.set()
        writer.join()
