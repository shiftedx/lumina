"""Roku's cached audio/video fMP4 rendition behavior."""
from __future__ import annotations

import signal
from pathlib import Path

import pytest

from app.services import jellyfin_audio
from app.services.jellyfin_audio import rendition_file
from app.services.jellyfin_hls import HlsRun
from app.services.local_playback_sessions import PlaybackSession


class Process:
    def __init__(self) -> None:
        self.signals: list[int] = []

    def poll(self) -> None:
        return None

    def send_signal(self, value: int) -> None:
        self.signals.append(value)


@pytest.mark.parametrize(
    ("name", "run", "source_name", "requested"),
    [
        ("audio-seg26.m4s", None, "seg26.m4s", 26),
        ("video-seg26.m4s", None, "seg26.m4s", 26),
        ("audio-init.mp4", HlsRun(8, 29.9), "seg8.m4s", 8),
        ("video-init.mp4", None, "seg0.m4s", 0),
    ],
)
def test_cached_renditions_still_advance_the_player_and_resume_the_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, run: HlsRun | None, source_name: str, requested: int,
) -> None:
    """A derivative disk hit is still a player request; init follows the run that the session opened around."""
    process = Process()
    session = PlaybackSession("s", "u", "i", "transcode", tmp_path, process, throttled=True, run=run)  # type: ignore[arg-type]
    target = tmp_path / name
    target.write_bytes(b"cached")
    (tmp_path / source_name).write_bytes(b"muxed")
    monkeypatch.setattr(jellyfin_audio.subprocess, "run", lambda *args, **kwargs: pytest.fail("cached rendition was remuxed"))

    assert rendition_file(session, name, "ffmpeg") == target
    assert session.max_requested == requested
    assert not session.throttled and process.signals == [signal.SIGCONT]


def test_a_cache_entry_removed_during_source_lookup_is_not_returned_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seek restart may invalidate split derivatives while rendition_file waits for the requested muxed segment."""
    process = Process()
    session = PlaybackSession("s", "u", "i", "transcode", tmp_path, process)  # type: ignore[arg-type]
    target = tmp_path / "audio-seg7.m4s"
    target.write_bytes(b"stale")

    def invalidate(_session: PlaybackSession, _name: str) -> None:
        target.unlink()
        return None

    monkeypatch.setattr(jellyfin_audio.sessions, "segment", invalidate)
    assert rendition_file(session, target.name, "ffmpeg") is None
    assert not target.exists()
