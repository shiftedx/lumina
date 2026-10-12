"""Local media is probed with ffprobe and offered the playback path it actually supports."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from app.models import MediaArtifact, User
from app.services import media_probe
from app.services.user_settings import UserSettingsService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")


def make_media(path: Path, vcodec: str = "libx264", acodec: str = "aac", fmt: str | None = None, seconds: int = 2, size: str = "160x120") -> Path:
    """Tiny synthetic clip from lavfi test sources."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", vcodec, "-c:a", acodec, "-shortest"]
    if vcodec in {"libx264", "libx265"}:
        cmd += ["-pix_fmt", "yuv420p", "-g", "15"]
    if fmt:
        cmd += ["-f", fmt]
    subprocess.run([*cmd, str(path)], check=True, timeout=60)
    return path


def _options(item_id: str, user: str = "alice", profiles: str | None = None):
    with _client(user) as client:
        return client.get(f"/api/library/{item_id}/playback-options", params={"profiles": profiles} if profiles else None)


def test_direct_mp4_range_seek(household: None) -> None:
    media = make_media(settings.library_root / "alice" / "clip.mp4")
    item_id = _download("alice", media)

    body = _options(item_id).json()
    assert body["mode"] == "direct"
    assert body["facts"]["video_codec"] == "h264" and body["facts"]["audio_codec"] == "aac"
    assert (body["facts"]["width"], body["facts"]["height"]) == (160, 120)
    assert 1.5 < body["facts"]["duration"] < 2.5

    original = media.read_bytes()
    with _client("alice") as client:
        partial = client.get(f"/api/library/{item_id}/media", headers={"Range": "bytes=100-199"})
        head = client.head(f"/api/library/{item_id}/media")
    assert partial.status_code == 206 and partial.content == original[100:200]
    assert head.status_code == 200 and head.headers["accept-ranges"] == "bytes"
    assert _options(item_id, "bob").status_code == 404  # private item stays private


def test_container_not_codec(household: None) -> None:
    if "libx265" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
        pytest.skip("ffmpeg lacks libx265")
    # HEVC in Matroska named .mp4: the filename must not make it look browser-native.
    hevc = make_media(settings.library_root / "alice" / "hevc.mp4", vcodec="libx265", acodec="ac3", fmt="matroska")
    mkv = make_media(settings.library_root / "alice" / "h264.mkv")
    hevc_id = _download("alice", hevc, remote_id="hevc")
    mkv_id = _download("alice", mkv, remote_id="mkv")

    hevc_plan = _options(hevc_id).json()
    assert hevc_plan["mode"] == "transcode"
    assert hevc_plan["reason"] == "VideoCodecNotSupported,AudioCodecNotSupported"
    assert "matroska" in hevc_plan["facts"]["container"]
    mkv_plan = _options(mkv_id).json()
    assert (mkv_plan["mode"], mkv_plan["reason"]) == ("remux", "ContainerNotSupported")


def test_probe_corrupt_timeout_safe(household: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corrupt = settings.library_root / "alice" / "broken.mp4"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xff" * 4096)
    good = make_media(settings.library_root / "alice" / "good.mp4")
    corrupt_id = _download("alice", corrupt, remote_id="broken")
    good_id = _download("alice", good, remote_id="good")

    body = _options(corrupt_id).json()
    assert (body["mode"], body["reason"], body["facts"]) == ("unavailable", "probe_failed", None)

    # A hanging prober is killed at the timeout and reported, not waited on forever.
    hang = tmp_path / "ffprobe"
    hang.write_text("#!/bin/sh\nsleep 30\n")
    hang.chmod(0o755)
    real_tool = media_probe.media_tool
    monkeypatch.setattr(media_probe, "media_tool", lambda db, name: str(hang))
    monkeypatch.setattr(media_probe, "PROBE_TIMEOUT_SECONDS", 0.5)
    corrupt.write_bytes(b"changed")  # new fingerprint forces a re-probe
    assert _options(corrupt_id).json()["reason"] == "probe_timeout"

    # Other items continue, and a cached probe is reused without spawning ffprobe again.
    monkeypatch.setattr(media_probe, "media_tool", real_tool)
    assert _options(good_id).json()["mode"] == "direct"
    calls: list[Path] = []
    monkeypatch.setattr(media_probe, "run_ffprobe", lambda tool, path: calls.append(path) or {})
    assert _options(good_id).json()["mode"] == "direct" and calls == []
    with db_module.SessionLocal() as db:
        assert all(row.probe and row.probe["fingerprint"] for row in db.query(MediaArtifact).all())


def test_old_caches_without_streams_are_reprobed(household: None) -> None:
    item_id = _download("alice", make_media(settings.library_root / "alice" / "clip.mp4"))
    assert _options(item_id).status_code == 200
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        assert [s["type"] for s in artifact.probe["streams"]] == ["video", "audio"]
        artifact.probe = {key: value for key, value in artifact.probe.items() if key != "streams"}  # a v2-era cache
        db.commit()
    assert _options(item_id).json()["mode"] == "direct"
    with db_module.SessionLocal() as db:
        assert "streams" in db.query(MediaArtifact).one().probe


def test_profiles_ceiling_audio_tracks_and_quality_rungs(household: None) -> None:
    item_id = _download("alice", make_media(settings.library_root / "alice" / "hd.mp4", size="1280x720"), remote_id="hd")
    body = _options(item_id).json()
    assert (body["mode"], body["quality_heights"]) == ("direct", [480])
    assert body["audio_tracks"] == [{"index": 1, "language": None, "label": "Track 1 · mono · AAC", "codec": "aac", "channels": 1, "default": True}]

    with db_module.SessionLocal() as db:  # the member's playback ceiling now applies to library playback
        record = UserSettingsService(db).ensure_for_user(db.get(User, "alice"))
        record.ui_prefs = {"playback_max_height": 480}
        db.commit()
    capped = _options(item_id).json()
    assert (capped["mode"], capped["reason"], capped["quality_heights"]) == ("transcode", "VideoResolutionNotSupported", [480])
    assert _options(item_id, "bob").status_code == 404

    if "libx265" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
        return
    hevc_id = _download("alice", make_media(settings.library_root / "alice" / "hevc.mp4", vcodec="libx265", size="320x240"), remote_id="hevc")
    assert _options(hevc_id).json()["mode"] == "transcode"
    assert _options(hevc_id, profiles="mp4-avc-aac,mp4-hevc-aac").json()["mode"] == "direct"
