"""Playback derivatives use the configured bounded temporary storage."""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import pytest
import yaml

from app.config import EnvSettings, settings
from app.services import local_playback_sessions as lps
from app.services.local_playback_sessions import LocalPlaybackSessions, SessionCapError
from app.services.playback_decision import Decision


FACTS = {
    "duration": 60.0,
    "bit_rate": 3_000_000,
    "streams": [
        {"index": 0, "type": "video", "codec": "h264", "height": 480},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
    ],
}
REMUX = Decision("remux", video="copy", audio="copy", video_index=0, audio_index=1)
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def registry(tmp_path: Path):
    fake = tmp_path / "ffmpeg"
    fake.write_text(
        "#!/bin/sh\n"
        "for last; do :; done\n"
        "printf '#EXTM3U\\n#EXT-X-ENDLIST\\n' > \"$last\"\n"
        "exec sleep 60\n"
    )
    fake.chmod(0o755)
    registry = LocalPlaybackSessions()
    registry.ffmpeg = str(fake)  # test convenience
    yield registry
    registry.close_all()


def start(registry: LocalPlaybackSessions, *, min_free_bytes: int) -> None:
    registry.start(
        user_id="member",
        item_id="movie",
        ffmpeg=registry.ffmpeg,
        source=Path("/media/movie.mkv"),
        facts=FACTS,
        decision=REMUX,
        cap=1,
        min_free_bytes=min_free_bytes,
    )


def test_native_playback_storage_defaults_to_the_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUMINA_PLAYBACK_TEMP_DIR", raising=False)
    assert EnvSettings().playback_temp_dir is None
    monkeypatch.setattr(settings, "playback_temp_dir", None)
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    assert lps.session_root() == tmp_path / "playback-sessions"


def test_explicit_playback_storage_uses_a_disposable_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    playback = tmp_path / "ram-playback"
    monkeypatch.setenv("LUMINA_PLAYBACK_TEMP_DIR", str(playback))
    configured = EnvSettings()
    monkeypatch.setattr(settings, "playback_temp_dir", configured.playback_temp_dir)

    assert configured.playback_temp_dir == playback
    assert lps.session_root() == playback / "playback-sessions"


def test_ram_playback_storage_uses_its_own_available_capacity(
    registry: LocalPlaybackSessions,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playback = tmp_path / "ram-playback"
    monkeypatch.setattr(settings, "playback_temp_dir", playback)
    monkeypatch.setattr(lps.shutil, "disk_usage", lambda _path: Usage(1024**3, 512 * 1024**2, 512 * 1024**2))

    start(registry, min_free_bytes=2 * 1024**3)

    assert next(iter(registry.live())).directory.parent == playback / "playback-sessions"


def test_cleanup_preserves_other_files_on_the_temporary_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "playback_temp_dir", tmp_path)
    sibling = tmp_path / "unrelated"
    sibling.write_text("keep")
    lps.session_root().mkdir()
    (lps.session_root() / "old-session").mkdir()

    LocalPlaybackSessions().close_all()

    assert sibling.read_text() == "keep" and not lps.session_root().exists()


def test_switching_to_ram_cleans_legacy_crash_derivatives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "playback_temp_dir", tmp_path / "ram")
    legacy = data / "playback-sessions"
    legacy.mkdir(parents=True)
    (legacy / "old-segment.m4s").write_bytes(b"old")
    original = data / "library" / "original.mp4"
    original.parent.mkdir()
    original.write_bytes(b"original")
    lps.session_root().mkdir(parents=True)

    LocalPlaybackSessions().close_all()

    assert not legacy.exists() and not lps.session_root().exists()
    assert original.read_bytes() == b"original"


def test_full_ram_playback_storage_refuses_a_new_session(
    registry: LocalPlaybackSessions,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "playback_temp_dir", tmp_path / "ram-playback")
    monkeypatch.setattr(lps.shutil, "disk_usage", lambda _path: Usage(1024**3, 1024**3, 0))

    with pytest.raises(SessionCapError, match="low on temporary storage"):
        start(registry, min_free_bytes=2 * 1024**3)


def test_compose_mounts_bounded_playback_tmpfs() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    service = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]["lumina"]

    assert service["environment"]["LUMINA_PLAYBACK_TEMP_DIR"] == "/playback"
    assert "/playback:rw,noexec,nosuid,nodev,size=${LUMINA_PLAYBACK_TMPFS_SIZE:-1g},mode=1777" in service["tmpfs"]
