"""Background caption extraction yields promptly and always reaps its ffmpeg child."""
from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import subtitle_tracks


class FakeProcess:
    def __init__(self, command: list[str], *, finish_after: int = 2) -> None:
        self.command = command
        self.finish_after = finish_after
        self.returncode: int | None = None
        self.waits = 0
        self.signals: list[int] = []
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        if self.returncode is not None:
            return self.returncode
        if self.waits < self.finish_after:
            raise subprocess.TimeoutExpired(self.command, timeout)
        Path(self.command[-1]).write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n")
        self.returncode = 0
        return 0

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _extract_vtt(source: Path, **kwargs) -> str:  # noqa: ANN003
    source.write_bytes(b"media")
    return subtitle_tracks.vtt_body(
        "e:2", facts={"streams": [{"index": 2, "type": "subtitle", "codec": "subrip"}]},
        sidecar_entries=[], source=source, root=source.parent, artifact_id="artifact", fingerprint="version",
        ffmpeg="ffmpeg", **kwargs,
    )


def test_background_caption_extraction_registers_and_checks_the_activity_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess([], finish_after=3)
    registrations: list[tuple[FakeProcess, bool]] = []
    syncs: list[tuple[FakeProcess, bool]] = []
    unregistered: list[FakeProcess] = []

    def popen(command: list[str], **_kwargs) -> FakeProcess:
        process.command = command
        return process

    monkeypatch.setattr(subtitle_tracks.subprocess, "Popen", popen)
    monkeypatch.setattr(subtitle_tracks.background_work, "register", lambda child, *, backfill=False: registrations.append((child, backfill)))
    monkeypatch.setattr(subtitle_tracks.background_work, "sync", lambda child, *, backfill=False: syncs.append((child, backfill)) or False)
    monkeypatch.setattr(subtitle_tracks.background_work, "unregister", lambda child: unregistered.append(child))

    assert "Hello" in _extract_vtt(tmp_path / "source.mkv", background=True, backfill=True)
    assert registrations == [(process, True)]
    assert syncs and set(syncs) == {(process, True)}
    assert unregistered == [process]


def test_interactive_subtitle_extraction_bypasses_the_background_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_kwargs) -> SimpleNamespace:
        Path(command[-1]).write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nNow\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subtitle_tracks.subprocess, "run", run)
    monkeypatch.setattr(
        subtitle_tracks.background_work, "register",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("interactive extraction entered the background gate")),
    )

    assert "Now" in _extract_vtt(tmp_path / "source.mkv")


def test_a_closing_gate_stops_ffmpeg_before_the_worker_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess([], finish_after=2)
    checked: list[bool] = []

    def popen(command: list[str], **_kwargs) -> FakeProcess:
        process.command = command
        return process

    def sync(child: FakeProcess, *, backfill: bool = False) -> bool:
        child.send_signal(signal.SIGSTOP)
        return True

    def check_active() -> None:
        checked.append(True)
        assert process.signals[-1] == signal.SIGSTOP

    monkeypatch.setattr(subtitle_tracks.subprocess, "Popen", popen)
    monkeypatch.setattr(subtitle_tracks.background_work, "register", lambda child, *, backfill=False: None)
    monkeypatch.setattr(subtitle_tracks.background_work, "sync", sync)
    monkeypatch.setattr(subtitle_tracks.background_work, "unregister", lambda child: None)

    assert "Hello" in _extract_vtt(
        tmp_path / "source.mkv", background=True, backfill=True, check_active=check_active,
    )
    assert checked


def test_interactive_promotion_keeps_an_admitted_backfill_registered_until_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess([], finish_after=2)
    promoted = [False]
    promotion_calls: list[FakeProcess] = []
    unregistered: list[FakeProcess] = []

    def popen(command: list[str], **_kwargs) -> FakeProcess:
        process.command = command
        return process

    def sync(child: FakeProcess, *, backfill: bool = False) -> bool:
        child.send_signal(signal.SIGSTOP)
        return True

    def check_active() -> None:
        promoted[0] = True

    monkeypatch.setattr(subtitle_tracks.subprocess, "Popen", popen)
    monkeypatch.setattr(subtitle_tracks.background_work, "register", lambda child, *, backfill=False: None)
    monkeypatch.setattr(subtitle_tracks.background_work, "sync", sync)
    monkeypatch.setattr(
        subtitle_tracks.background_work, "promote",
        lambda child: (promotion_calls.append(child), child.send_signal(signal.SIGCONT)),
    )
    monkeypatch.setattr(subtitle_tracks.background_work, "unregister", lambda child: unregistered.append(child))

    assert "Hello" in _extract_vtt(
        tmp_path / "source.mkv", background=True, backfill=True,
        check_active=check_active, interactive=lambda: promoted[0],
    )
    assert process.signals[:2] == [signal.SIGSTOP, signal.SIGCONT]
    assert promotion_calls == [process]
    assert unregistered == [process]


def test_canceling_a_paused_caption_extraction_resumes_terminates_and_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess([], finish_after=100)

    def popen(command: list[str], **_kwargs) -> FakeProcess:
        process.command = command
        Path(command[-1]).write_bytes(b"partial")
        return process

    def register(child: FakeProcess, *, backfill: bool = False) -> None:
        child.send_signal(signal.SIGSTOP)

    monkeypatch.setattr(subtitle_tracks.subprocess, "Popen", popen)
    monkeypatch.setattr(subtitle_tracks.background_work, "register", register)
    monkeypatch.setattr(subtitle_tracks.background_work, "sync", lambda child, *, backfill=False: True)
    monkeypatch.setattr(subtitle_tracks.background_work, "unregister", lambda child: None)

    with pytest.raises(RuntimeError, match="canceled"):
        _extract_vtt(
            tmp_path / "source.mkv", background=True, backfill=True,
            check_active=lambda: (_ for _ in ()).throw(RuntimeError("canceled")),
        )

    assert signal.SIGCONT in process.signals
    assert process.terminated
    assert not list(subtitle_tracks.subtitle_cache_root().glob("*.part"))


def test_paused_time_does_not_consume_the_extraction_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class TimedProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is not None:
                return self.returncode
            self.waits += 1
            assert timeout is not None and timeout <= 0.1
            clock[0] += timeout
            raise subprocess.TimeoutExpired(self.command, timeout)

    process = TimedProcess([], finish_after=1000)

    def popen(command: list[str], **_kwargs) -> TimedProcess:
        process.command = command
        return process

    syncs = [0]

    def sync(child: TimedProcess, *, backfill: bool = False) -> bool:
        syncs[0] += 1
        return syncs[0] <= 20  # two seconds stopped, then the active timeout begins

    monkeypatch.setattr(subtitle_tracks.subprocess, "Popen", popen)
    monkeypatch.setattr(subtitle_tracks.background_work, "register", lambda child, *, backfill=False: None)
    monkeypatch.setattr(subtitle_tracks.background_work, "sync", sync)
    monkeypatch.setattr(subtitle_tracks.background_work, "unregister", lambda child: None)
    monkeypatch.setattr(subtitle_tracks.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(subtitle_tracks, "EXTRACT_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(subtitle_tracks.SubtitleError, match="subtitle_timeout"):
        _extract_vtt(tmp_path / "source.mkv", background=True, backfill=True)

    assert process.waits >= 22
    assert process.terminated
