"""Loudness is measured once per file version in the background and becomes a bounded gain."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app import db as db_module
from app.config import settings
from app.models import LibraryItemArtifact, MediaArtifact, StorageRoot
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
    result = measure_loudness(shutil.which("ffmpeg"), _audio(tmp_path / "sine.m4a", 6), 0, stop=threading.Event())
    assert abs(result["i"] - (-21.8)) <= 1  # lavfi sine: amplitude 1/8 at 440 Hz


@needs_ffmpeg
def test_measurement_runs_niced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    niced: list[tuple[int, int, int]] = []
    monkeypatch.setattr(pw.os, "setpriority", lambda which, who, prio: niced.append((which, who, prio)))
    measure_loudness(shutil.which("ffmpeg"), _audio(tmp_path / "sine.m4a", 1), 0, stop=threading.Event())
    assert niced == [(pw.os.PRIO_PROCESS, niced[0][1], 19)] and niced[0][1] > 0


@needs_ffmpeg
def test_video_encode_pauses_measurement_and_shutdown_kills_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = _audio(tmp_path / "long.m4a", 240)  # seconds of work unpaused; it must never finish while paused
    stop = threading.Event()
    monkeypatch.setattr(pw.background_work, "register", lambda process, *, backfill=False: None)
    monkeypatch.setattr(pw.background_work, "sync", lambda process, *, backfill=False: False)
    monkeypatch.setattr(pw.background_work, "unregister", lambda process: None)
    monkeypatch.setattr(pw.sessions, "video_encodes", lambda: 1)
    threading.Timer(1.5, stop.set).start()
    began = time.monotonic()
    assert measure_loudness(shutil.which("ffmpeg"), media, 0, stop=stop) is None
    assert time.monotonic() - began >= 1.4


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> ProbeWarming:
    warming = ProbeWarming()
    warming.measured = []  # type: ignore[attr-defined]
    monkeypatch.setattr(pw.background_work, "busy", lambda: False)
    monkeypatch.setattr(pw.background_work, "backfill_allowed", lambda: True)
    monkeypatch.setattr(pw.sessions, "video_encodes", lambda: 0)

    def fake_measure(ffmpeg, path, stream_index, *, stop, backfill=False):  # noqa: ANN001
        warming.measured.append(path.name)  # type: ignore[attr-defined]
        return MEASURED

    monkeypatch.setattr(pw, "measure_loudness", fake_measure)
    return warming


def _loudness(name: str):
    with db_module.SessionLocal() as db:
        return next((a.probe or {}).get("loudness") for a in db.query(MediaArtifact).all() if a.relative_path.endswith(name))


def _ready_audio(path: Path) -> str:
    item_id = _download("alice", path)
    status = path.stat()
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        artifact.size, artifact.mtime_ns = status.st_size, status.st_mtime_ns
        artifact.probe = {
            "fingerprint": f"{status.st_size}:{status.st_mtime_ns}",
            "streams": [{"type": "audio", "index": 0, "default": True}],
        }
        db.commit()
    return item_id


def test_default_does_not_select_unsolicited_loudness(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready_audio(settings.library_root / "alice" / "clip.mp4")
    monkeypatch.setattr(pw.background_work, "backfill_allowed", lambda: False)
    global_probe_queries = 0

    def count_probe_query(_connection, _cursor, statement, _parameters, _context, _executemany):  # noqa: ANN001
        nonlocal global_probe_queries
        if "ORDER BY media_artifacts.created_at DESC" in statement and "_scan_fingerprint" in repr(_parameters):
            global_probe_queries += 1

    event.listen(db_module.engine, "before_cursor_execute", count_probe_query)
    try:
        assert not worker.run_once() and not worker.run_once()
    finally:
        event.remove(db_module.engine, "before_cursor_execute", count_probe_query)
    assert global_probe_queries == 1
    assert worker.measured == [] and _loudness("clip.mp4") is None


def test_demanded_loudness_waits_for_quiet_without_losing_priority(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_id = _ready_audio(settings.library_root / "alice" / "clip.mp4")
    busy = True
    monkeypatch.setattr(pw.background_work, "busy", lambda: busy)
    monkeypatch.setattr(pw.background_work, "backfill_allowed", lambda: False)
    worker.prioritize(item_id)

    assert not worker.run_once()
    assert list(worker._wanted) == [item_id] and worker.measured == []
    busy = False
    assert worker.run_once()
    assert list(worker._wanted) == [] and worker.measured == ["clip.mp4"]


@pytest.mark.parametrize("backfill", [False, True], ids=["foreground-activity", "backfill-window"])
def test_measurement_syncs_activity_and_window_every_poll(monkeypatch: pytest.MonkeyPatch, backfill: bool) -> None:
    class Process:
        pid = 123
        returncode = 0

        def __init__(self) -> None:
            self.waits: list[float | None] = []
            self.signals: list[int] = []
            self.killed = False

        def poll(self):  # noqa: ANN201
            return 0 if len(self.waits) == 3 else None

        def wait(self, timeout=None):  # noqa: ANN001, ANN201
            self.waits.append(timeout)
            if timeout is not None and len(self.waits) < 3:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return 0

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def kill(self) -> None:
            self.killed = True

    process = Process()
    calls: list[tuple[str, bool]] = []
    commands: list[list[str]] = []
    blocked = iter((False, True, False))

    def popen(command, **_kwargs):  # noqa: ANN001, ANN202
        commands.append(command)
        return process

    monkeypatch.setattr(pw.subprocess, "Popen", popen)
    monkeypatch.setattr(pw.os, "setpriority", lambda *args: None)
    monkeypatch.setattr(pw.sessions, "video_encodes", lambda: 0)
    monkeypatch.setattr(
        pw.background_work, "register", lambda child, *, backfill=False: calls.append(("register", backfill)),
    )
    monkeypatch.setattr(
        pw.background_work, "sync", lambda child, *, backfill=False: calls.append(("sync", backfill)) or next(blocked),
    )
    monkeypatch.setattr(
        pw.background_work, "unregister", lambda child: calls.append(("unregister", False)),
    )

    assert measure_loudness("ffmpeg", Path("clip.mp4"), 0, stop=threading.Event(), backfill=backfill) == {"error": "measure_failed"}
    assert calls == [
        ("register", backfill), ("sync", backfill), ("sync", backfill), ("sync", backfill), ("unregister", False),
    ]
    assert process.waits == [pw.POLL_SECONDS, pw.POLL_SECONDS, pw.POLL_SECONDS]
    assert max(timeout for timeout in process.waits if timeout is not None) <= 0.1
    assert commands[0][commands[0].index("-threads") + 1] == "1"
    assert commands[0][commands[0].index("-filter_threads") + 1] == "1"
    assert commands[0][commands[0].index("-filter_complex_threads") + 1] == "1"


def test_background_probes_drain_before_loudness_starts(household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch) -> None:
    _download("alice", settings.library_root / "alice" / "older.mp4", remote_id="older")
    _download("alice", settings.library_root / "alice" / "newer.mp4", remote_id="newer")
    probed: list[str] = []

    def cached_facts(self, artifact, path):  # noqa: ANN001
        probed.append(path.name)
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": [
            {"type": "audio", "index": 0, "default": True},
        ]}
        self.db.commit()
        return {"streams": artifact.probe["streams"]}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")

    assert worker.run_once() and worker.run_once()
    assert probed == ["newer.mp4", "older.mp4"]
    assert worker.measured == []
    assert worker.run_once() and worker.measured == ["newer.mp4"]
    assert worker.run_once() and worker.measured == ["newer.mp4", "older.mp4"]
    assert not worker.run_once()


@pytest.mark.parametrize(("batch_size", "after_32", "after_33", "before_loudness"), [
    (1, 32, 33, 34),
    (32, 1, 2, 3),
])
def test_probe_ids_are_selected_in_bounded_batches_before_loudness(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
    batch_size: int, after_32: int, after_33: int, before_loudness: int,
) -> None:
    monkeypatch.setattr(pw, "PROBE_BATCH_SIZE", batch_size)
    for index in range(33):
        _download("alice", settings.library_root / "alice" / f"clip-{index:02}.mp4", remote_id=f"clip-{index:02}")
    probed: list[str] = []

    def cached_facts(self, artifact, path):  # noqa: ANN001
        probed.append(path.name)
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": [
            {"type": "audio", "index": 0, "default": True},
        ]}
        self.db.commit()
        return {"streams": artifact.probe["streams"]}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    global_probe_queries = 0

    def count_probe_query(_connection, _cursor, statement, _parameters, _context, _executemany):  # noqa: ANN001
        nonlocal global_probe_queries
        if "ORDER BY media_artifacts.created_at DESC" in statement and "_scan_fingerprint" in repr(_parameters):
            global_probe_queries += 1

    event.listen(db_module.engine, "before_cursor_execute", count_probe_query)
    try:
        assert all(worker.run_once() for _ in range(32))
        assert len(probed) == 32 and global_probe_queries == after_32 and worker.measured == []
        assert worker.run_once()
        assert len(probed) == 33 and global_probe_queries == after_33 and worker.measured == []
        assert worker.run_once()
        assert global_probe_queries == before_loudness and worker.measured == [probed[0]]
    finally:
        event.remove(db_module.engine, "before_cursor_execute", count_probe_query)


def test_prioritized_item_preempts_a_buffered_probe_batch(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    oldest = _download("alice", settings.library_root / "alice" / "oldest.mp4", remote_id="oldest")
    _download("alice", settings.library_root / "alice" / "middle.mp4", remote_id="middle")
    _download("alice", settings.library_root / "alice" / "newest.mp4", remote_id="newest")
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")

    def cached_facts(self, artifact, path):  # noqa: ANN001
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": [
            {"type": "audio", "index": 0, "default": True},
        ]}
        self.db.commit()
        return {"streams": artifact.probe["streams"]}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)

    assert worker.run_once()
    worker.prioritize(oldest)
    assert worker.run_once()

    assert worker.measured == ["oldest.mp4"]


@pytest.mark.parametrize("change", ["disable", "delete"])
def test_buffered_probe_is_revalidated_before_use(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    item_id = _download("alice", settings.library_root / "alice" / "clip.mp4")
    probed: list[str] = []
    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", lambda self, artifact, path: probed.append(path.name))
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        worker._probe_ids.append(artifact.id)
        if change == "disable":
            db.get(StorageRoot, artifact.root_id).enabled = False
        else:
            db.delete(db.get(LibraryItemArtifact, item_id))
            db.delete(artifact)
        db.commit()

    assert not worker.run_once()
    assert probed == []


def test_finished_import_discards_buffer_so_its_newest_probe_wins(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download("alice", settings.library_root / "alice" / "oldest.mp4", remote_id="oldest")
    _download("alice", settings.library_root / "alice" / "middle.mp4", remote_id="middle")
    probed: list[str] = []

    def cached_facts(self, artifact, path):  # noqa: ANN001
        probed.append(path.name)
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": []}
        self.db.commit()
        return {"streams": []}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    assert worker.run_once() and probed == ["middle.mp4"]
    _download("alice", settings.library_root / "alice" / "new-import.mp4", remote_id="new-import")

    worker.after_import("run-1")

    assert worker.run_once() and probed == ["middle.mp4", "new-import.mp4"]


def test_import_finishing_during_batch_select_discards_the_stale_refill(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download("alice", settings.library_root / "alice" / "clip.mp4")
    selected = 0

    def cached_facts(self, artifact, path):  # noqa: ANN001
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": []}
        self.db.commit()
        return {"streams": []}

    def finish_import_during_first_query(_connection, _cursor, statement, _parameters, _context, _executemany):  # noqa: ANN001
        nonlocal selected
        if "ORDER BY media_artifacts.created_at DESC" in statement and "_scan_fingerprint" in repr(_parameters):
            selected += 1
            if selected == 1:
                worker.after_import("run-1")

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    event.listen(db_module.engine, "after_cursor_execute", finish_import_during_first_query)
    try:
        assert worker.run_once()
    finally:
        event.remove(db_module.engine, "after_cursor_execute", finish_import_during_first_query)

    assert selected == 2


def test_import_finishing_during_empty_probe_select_is_rechecked_before_loudness(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = settings.library_root / "alice" / "clip.mp4"
    _download("alice", path)
    status = path.stat()
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        artifact.size, artifact.mtime_ns = status.st_size, status.st_mtime_ns
        artifact.probe = {
            "fingerprint": f"{status.st_size}:{status.st_mtime_ns}",
            "streams": [{"type": "audio", "index": 0, "default": True}],
        }
        db.commit()
    selected = 0

    def finish_import_during_first_query(_connection, _cursor, statement, _parameters, _context, _executemany):  # noqa: ANN001
        nonlocal selected
        if "ORDER BY media_artifacts.created_at DESC" in statement and "_scan_fingerprint" in repr(_parameters):
            selected += 1
            if selected == 1:
                worker.after_import("run-1")

    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    event.listen(db_module.engine, "after_cursor_execute", finish_import_during_first_query)
    try:
        assert worker.run_once()
    finally:
        event.remove(db_module.engine, "after_cursor_execute", finish_import_during_first_query)

    assert selected == 2 and worker.measured == ["clip.mp4"]


def test_legacy_loudness_only_cache_is_reprobed_then_remeasured(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = settings.library_root / "alice" / "legacy.mp4"
    _download("alice", path)
    status = path.stat()
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        artifact.size, artifact.mtime_ns = status.st_size, status.st_mtime_ns
        artifact.probe = {"fingerprint": f"{status.st_size}:{status.st_mtime_ns}", "loudness": MEASURED}
        db.commit()
    probes: list[str] = []

    def probe(self, candidate):  # noqa: ANN001
        probes.append(candidate.name)
        return {"streams": [{"type": "audio", "index": 0, "default": True}]}

    monkeypatch.setattr(pw.MediaProbeService, "_probe", probe)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")

    assert worker.run_once() and probes == ["legacy.mp4"]
    assert _loudness("legacy.mp4") is None  # replacing an incomplete codec cache queues a safe remeasurement
    assert worker.run_once() and worker.measured == ["legacy.mp4"]
    assert _loudness("legacy.mp4") == MEASURED
    assert not worker.run_once()


def test_a_stale_fingerprint_is_reprobed_before_its_loudness_is_remeasured(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = settings.library_root / "alice" / "changed.mp4"
    _download("alice", path)
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        status = path.stat()
        artifact.size, artifact.mtime_ns = status.st_size, status.st_mtime_ns
        artifact.probe = {"fingerprint": "1:1", "streams": [], "loudness": MEASURED}
        db.commit()
    probes: list[str] = []

    def probe(self, candidate):  # noqa: ANN001
        probes.append(candidate.name)
        return {"streams": [{"type": "audio", "index": 0, "default": True}]}

    monkeypatch.setattr(pw.MediaProbeService, "_probe", probe)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")

    assert worker.run_once() and probes == ["changed.mp4"]
    assert _loudness("changed.mp4") is None
    assert worker.run_once() and worker.measured == ["changed.mp4"]
    assert _loudness("changed.mp4") == MEASURED


def test_live_probe_ahead_of_scan_does_not_starve_probe_and_loudness_queues(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = settings.library_root / "alice" / "pending.mp4"
    changed = settings.library_root / "alice" / "changed.mp4"
    _download("alice", pending, remote_id="pending")
    _download("alice", changed, remote_id="changed")
    old = changed.stat()
    with db_module.SessionLocal() as db:
        artifacts = {artifact.relative_path.rsplit("/", 1)[-1]: artifact for artifact in db.query(MediaArtifact)}
        pending_status = pending.stat()
        artifacts["pending.mp4"].size = pending_status.st_size
        artifacts["pending.mp4"].mtime_ns = pending_status.st_mtime_ns
        artifacts["changed.mp4"].size = old.st_size
        artifacts["changed.mp4"].mtime_ns = old.st_mtime_ns
        db.commit()

    changed.write_bytes(b"a newer and differently sized file")
    probed: list[str] = []

    def probe(self, candidate):  # noqa: ANN001
        probed.append(candidate.name)
        return {"streams": [{"type": "audio", "index": 0, "default": True}]}

    monkeypatch.setattr(pw.MediaProbeService, "_probe", probe)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    with db_module.SessionLocal() as db:  # a playback request observes the changed file before the next library scan
        artifact = next(artifact for artifact in db.query(MediaArtifact) if artifact.relative_path.endswith("changed.mp4"))
        pw.MediaProbeService(db).cached_facts(artifact, changed)
    assert probed == ["changed.mp4"]
    probed.clear()

    assert worker.run_once()  # acknowledge that the cache already describes the live file
    with db_module.SessionLocal() as db:
        artifact = next(artifact for artifact in db.query(MediaArtifact) if artifact.relative_path.endswith("changed.mp4"))
        assert (artifact.size, artifact.mtime_ns) == (old.st_size, old.st_mtime_ns)  # the scanner still owns these
        assert artifact.probe[pw.SCAN_FINGERPRINT_KEY] == f"{old.st_size}:{old.st_mtime_ns}"
    assert worker.run_once() and probed == ["pending.mp4"]
    assert worker.run_once() and worker.measured == ["changed.mp4"]

    changed.write_bytes(b"a third version observed by the scanner")
    scanned = changed.stat()
    with db_module.SessionLocal() as db:
        artifact = next(artifact for artifact in db.query(MediaArtifact) if artifact.relative_path.endswith("changed.mp4"))
        artifact.size, artifact.mtime_ns = scanned.st_size, scanned.st_mtime_ns
        db.commit()
    probed.clear()
    assert worker.run_once() and probed == ["changed.mp4"]


def test_a_prioritized_stale_item_with_old_loudness_still_enters_the_probe_phase(
    household: None, worker: ProbeWarming, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = settings.library_root / "alice" / "changed.mp4"
    item_id = _download("alice", path)
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).one()
        status = path.stat()
        artifact.size, artifact.mtime_ns = status.st_size, status.st_mtime_ns
        artifact.probe = {"fingerprint": "1:1", "streams": [], "loudness": MEASURED}
        db.commit()
    probes: list[str] = []

    def probe(self, candidate):  # noqa: ANN001
        probes.append(candidate.name)
        return {"streams": [{"type": "audio", "index": 0, "default": True}]}

    monkeypatch.setattr(pw.MediaProbeService, "_probe", probe)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    worker.prioritize(item_id)

    assert worker.run_once() and probes == ["changed.mp4"]
    assert _loudness("changed.mp4") is None
    assert worker.run_once() and worker.measured == ["changed.mp4"]


@needs_ffmpeg
def test_newest_first_restart_safe_and_opened_items_jump_the_queue(household: None, worker: ProbeWarming) -> None:
    older = _download("alice", make_media(settings.library_root / "alice" / "older.mp4"), remote_id="older")
    _download("alice", make_media(settings.library_root / "alice" / "newer.mp4"), remote_id="newer")
    newest = _download("alice", make_media(settings.library_root / "alice" / "newest.mp4"), remote_id="newest")
    worker.prioritize(older)
    assert all(worker.run_once() for _ in range(5))
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

    def racing_measure(ffmpeg, path, stream_index, *, stop, backfill=False):  # noqa: ANN001
        with db_module.SessionLocal() as db:  # the file changed and was re-probed meanwhile
            artifact = db.query(MediaArtifact).one()
            artifact.probe = {**artifact.probe, "fingerprint": "0:0"}
            db.commit()
        return MEASURED

    monkeypatch.setattr(pw, "measure_loudness", racing_measure)
    monkeypatch.setattr(pw.background_work, "busy", lambda: False)
    monkeypatch.setattr(pw.background_work, "backfill_allowed", lambda: True)
    monkeypatch.setattr(pw.sessions, "video_encodes", lambda: 0)
    warming = ProbeWarming()
    assert warming.run_once()  # probe-ready phase
    assert warming.run_once()  # loudness phase races a changed fingerprint
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
        streams = [{"type": "audio", "index": 0, "default": True}]
        artifact.probe = {"fingerprint": f"{path.stat().st_size}:{path.stat().st_mtime_ns}", "streams": streams}
        self.db.commit()
        return {"streams": streams}

    monkeypatch.setattr(pw.MediaProbeService, "cached_facts", cached_facts)
    monkeypatch.setattr(pw, "media_tool", lambda db, name: "ffmpeg")
    assert worker.run_once()
    assert _loudness("broken.mp4") == {"error": "measure_failed"}
    with db_module.SessionLocal() as db:
        assert db.query(MediaArtifact).filter(MediaArtifact.relative_path.endswith("broken.mp4")).one().probe["error"] == "probe_failed"
    assert worker.run_once() and worker.measured == []
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
