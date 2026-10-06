"""Loudness is measured once per file version in the background and becomes a bounded gain."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app import db as db_module
from app.config import settings
from app.models import MediaArtifact
from app.services import library_import
from app.services import probe_warming as pw
from app.services.media_probe import loudness_gain_db
from app.services.probe_warming import ProbeWarming, measure_loudness, parse_loudnorm
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401
from tests.test_v1_probe import make_media

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")
MEASURED = {"i": -20.0, "tp": -3.0, "lra": 4.0}


@pytest.mark.parametrize(("loudness", "gain"), [
    ({"i": -18.0, "tp": -1.0, "lra": 5.0}, 0.0),
    ({"i": -30.0, "tp": -20.0, "lra": 5.0}, 12.0),  # quiet: capped at +12
    ({"i": -30.0, "tp": -5.0, "lra": 5.0}, 4.0),  # a boost stops where peaks reach -1 dBTP
    ({"i": -24.0, "tp": 0.5, "lra": 5.0}, 0.0),  # already peaking: no boost at all
    ({"i": -5.0, "tp": 0.0, "lra": 5.0}, -12.0),  # loud: capped at -12
    ({"i": -10.0, "tp": -0.1, "lra": 5.0}, -8.0),
    ({"error": "silent"}, None), (None, None), ({}, None), ({"i": "loud"}, None),
])
def test_gain_is_bounded(loudness, gain) -> None:
    assert loudness_gain_db(loudness) == gain


def test_silence_and_garbage_never_store_infinities() -> None:
    assert parse_loudnorm('[Parsed_loudnorm_0 @ 0x1]\n{\n\t"input_i" : "-inf",\n\t"input_tp" : "-inf",\n\t"input_lra" : "0.00"\n}\n') == {"error": "silent"}
    assert parse_loudnorm("no json here") == {"error": "measure_failed"}
    assert parse_loudnorm('{"input_i" : "loud", "input_tp" : "-1", "input_lra" : "1"}') == {"error": "measure_failed"}
    assert parse_loudnorm('x\n{\n\t"input_i" : "-21.81",\n\t"input_tp" : "-17.69",\n\t"input_lra" : "0.10",\n\t"target_offset" : "1.00"\n}\n') == {"i": -21.81, "tp": -17.69, "lra": 0.1}


def _audio(path: Path, seconds: int) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", str(path)], check=True, timeout=120)
    return path


@needs_ffmpeg
def test_a_known_sine_measures_within_one_lu(tmp_path: Path) -> None:
    result = measure_loudness(shutil.which("ffmpeg"), _audio(tmp_path / "sine.m4a", 6), 0, stop=threading.Event(), busy=lambda: False)
    assert abs(result["i"] - (-21.8)) <= 1  # lavfi sine: amplitude 1/8 at 440 Hz


@needs_ffmpeg
def test_measurement_runs_niced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    niced: list[tuple[int, int, int]] = []
    monkeypatch.setattr(pw.os, "setpriority", lambda which, who, prio: niced.append((which, who, prio)))
    measure_loudness(shutil.which("ffmpeg"), _audio(tmp_path / "sine.m4a", 1), 0, stop=threading.Event(), busy=lambda: False)
    assert niced == [(pw.os.PRIO_PROCESS, niced[0][1], 19)] and niced[0][1] > 0


@needs_ffmpeg
def test_measurement_pauses_while_a_video_encode_runs(tmp_path: Path) -> None:
    media = _audio(tmp_path / "long.m4a", 240)  # seconds of work unpaused; it must never finish while paused
    stop = threading.Event()
    threading.Timer(1.5, stop.set).start()
    began = time.monotonic()
    assert measure_loudness(shutil.which("ffmpeg"), media, 0, stop=stop, busy=lambda: True) is None
    assert time.monotonic() - began >= 1.4


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> ProbeWarming:
    warming = ProbeWarming()
    warming.measured = []  # type: ignore[attr-defined]

    def fake_measure(ffmpeg, path, stream_index, *, stop, busy):  # noqa: ANN001
        warming.measured.append(path.name)  # type: ignore[attr-defined]
        return MEASURED

    monkeypatch.setattr(pw, "measure_loudness", fake_measure)
    return warming


def _loudness(name: str):
    with db_module.SessionLocal() as db:
        return next((a.probe or {}).get("loudness") for a in db.query(MediaArtifact).all() if a.relative_path.endswith(name))


@needs_ffmpeg
def test_newest_first_restart_safe_and_opened_items_jump_the_queue(household: None, worker: ProbeWarming) -> None:
    older = _download("alice", make_media(settings.library_root / "alice" / "older.mp4"), remote_id="older")
    _download("alice", make_media(settings.library_root / "alice" / "newer.mp4"), remote_id="newer")
    newest = _download("alice", make_media(settings.library_root / "alice" / "newest.mp4"), remote_id="newest")
    worker.prioritize(older)
    assert worker.run_once() and worker.run_once() and worker.run_once()
    assert worker.measured == ["older.mp4", "newest.mp4", "newer.mp4"]
    assert not worker.run_once()  # the selection is the database: a restart finds the same empty queue
    assert _loudness("newer.mp4") == MEASURED
    with _client("alice") as client:
        assert client.get(f"/api/library/{newest}/playback-options").json()["loudness_gain_db"] == 2.0


@needs_ffmpeg
def test_opening_an_unmeasured_item_prioritizes_it(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    wanted: list[str] = []
    monkeypatch.setattr(pw.probe_warming, "prioritize", wanted.append)
    item_id = _download("alice", make_media(settings.library_root / "alice" / "clip.mp4"))
    with _client("alice") as client:
        assert client.get(f"/api/library/{item_id}/playback-options").json()["loudness_gain_db"] is None
    assert wanted == [item_id]


@needs_ffmpeg
def test_a_file_changed_while_measuring_is_not_stamped(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _download("alice", make_media(settings.library_root / "alice" / "clip.mp4"))

    def racing_measure(ffmpeg, path, stream_index, *, stop, busy):  # noqa: ANN001
        with db_module.SessionLocal() as db:  # the file changed and was re-probed meanwhile
            artifact = db.query(MediaArtifact).one()
            artifact.probe = {**artifact.probe, "fingerprint": "0:0"}
            db.commit()
        return MEASURED

    monkeypatch.setattr(pw, "measure_loudness", racing_measure)
    assert ProbeWarming().run_once()
    assert _loudness("clip.mp4") is None


def test_a_missing_file_is_marked_not_retried_forever(household: None, worker: ProbeWarming) -> None:
    path = settings.library_root / "alice" / "gone.mp4"
    _download("alice", path)  # writes placeholder bytes
    path.unlink()
    assert worker.run_once()
    assert _loudness("gone.mp4") == {"error": "missing"}
    assert not worker.run_once() and worker.measured == []


def test_an_unexpected_failure_is_stamped_so_older_files_are_not_starved(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download("alice", settings.library_root / "alice" / "older.mp4", remote_id="older")
    _download("alice", settings.library_root / "alice" / "broken.mp4", remote_id="broken")

    def cached_facts(self, artifact, path):  # noqa: ANN001
        if path.name == "broken.mp4":
            raise RuntimeError("unexpected")
        return {"streams": [{"type": "audio", "index": 0, "default": True}]}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    assert worker.run_once()
    assert _loudness("broken.mp4") == {"error": "measure_failed"}
    assert worker.run_once() and worker.measured == ["older.mp4"]
    assert not worker.run_once()


def test_a_busy_database_is_retried_not_stamped(household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch) -> None:
    _download("alice", settings.library_root / "alice" / "clip.mp4")

    def busy(self, artifact, path):  # noqa: ANN001
        raise OperationalError("acquire SQLite writer slot", {}, TimeoutError("SQLite writer slot is busy"))

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", busy)
    with pytest.raises(OperationalError):
        worker.run_once()
    assert _loudness("clip.mp4") is None


def test_start_registers_the_post_import_hook_once(monkeypatch: pytest.MonkeyPatch) -> None:
    hooks: list = []
    monkeypatch.setattr(library_import, "after_import_hooks", hooks)
    warming = ProbeWarming()
    monkeypatch.setattr(warming, "_loop", lambda: None)
    warming.start()
    warming.stop()
    warming.start()
    warming.stop()
    assert hooks == [warming.after_import]
    warming._wake.clear()
    hooks[0]("run-1")
    assert warming._wake.is_set()
