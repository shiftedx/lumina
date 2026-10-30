"""Post-import probe warming and loudness analysis.

One background thread first drains missing or stale codec probes, then measures the loudness
of default audio tracks one file at a time. An item someone just opened jumps both queues. The
database is the durable queue, so a restart resumes either phase where it stopped. Unsolicited
loudness runs only in the configured background-work window; demanded work waits for foreground
activity to become quiet. Results live inside ``artifact.probe``, so a changed file (new
fingerprint) is probed again before its loudness is re-measured.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import MediaArtifact, StorageRoot
from app.persistence import write_transaction
from app.services import library_import
from app.services.background_work import background_work
from app.services.local_playback_sessions import sessions
from app.services.media_artifacts import MediaArtifactService, artifact_file
from app.services.media_probe import LOCAL_INPUT_ARGS, MediaProbeService, media_tool
from app.services.m2ts_irap_index import m2ts_irap_indexes, pair_from_facts

logger = logging.getLogger(__name__)
IDLE_SECONDS = 60
POLL_SECONDS = 0.1
WANTED_RETRY_SECONDS = 1
LOUDNESS_TIMEOUT_SECONDS = 3600  # of running time; time paused for video encodes does not count
LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}")
SCAN_FINGERPRINT_KEY = "_scan_fingerprint"
PROBE_BATCH_SIZE = 32


def parse_loudnorm(text: str) -> dict[str, Any]:
    """loudnorm's JSON block -> {i, tp, lra}; silence and junk become errors, never infinities."""
    blocks = LOUDNORM_JSON.findall(text)
    try:
        data = json.loads(blocks[-1])
        i, tp, lra = (float(data[key]) for key in ("input_i", "input_tp", "input_lra"))
    except (IndexError, KeyError, TypeError, ValueError):
        return {"error": "measure_failed"}
    if not (math.isfinite(i) and math.isfinite(tp)):
        return {"error": "silent"}
    return {"i": round(i, 2), "tp": round(tp, 2), "lra": round(lra, 2) if math.isfinite(lra) else 0.0}


def _signal(process: subprocess.Popen, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.send_signal(sig)


def measure_loudness(
    ffmpeg: str, path: Path, stream_index: int, *, stop: threading.Event, backfill: bool = False,
) -> dict[str, Any] | None:
    """EBU R128 of one audio stream (loudnorm's first pass), or None when asked to stop.

    Runs niced and registered with the shared foreground gate. Paused time does not consume its timeout.
    """
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-threads", "1", "-filter_threads", "1",
        "-filter_complex_threads", "1", *LOCAL_INPUT_ARGS, "-i", str(path),
        "-map", f"0:{stream_index}", "-vn", "-sn", "-dn", "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ]
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        with contextlib.suppress(OSError):  # background work: yield the CPU to playback and the UI
            os.setpriority(os.PRIO_PROCESS, process.pid, 19)
        ran, video_blocked = 0.0, False
        background_work.register(process, backfill=backfill)
        try:
            while process.poll() is None:
                if stop.is_set():
                    return None
                if ran > LOUDNESS_TIMEOUT_SECONDS:
                    return {"error": "timeout"}
                blocked = background_work.sync(process, backfill=backfill)
                video_busy = sessions.video_encodes() > 0
                if video_busy:
                    _signal(process, signal.SIGSTOP)
                elif video_blocked and not blocked:
                    _signal(process, signal.SIGCONT)
                video_blocked, blocked = video_busy, blocked or video_busy
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=POLL_SECONDS)
                if not blocked:
                    ran += POLL_SECONDS
        finally:
            background_work.unregister(process)
            if process.poll() is None:
                _signal(process, signal.SIGCONT)
                process.kill()
                process.wait()
        if process.returncode != 0:
            return {"error": "measure_failed"}
        stderr.seek(0, os.SEEK_END)
        stderr.seek(max(0, stderr.tell() - 8192))
        return parse_loudnorm(stderr.read().decode("utf-8", "replace"))


class ProbeWarming:
    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._wanted: deque[str] = deque(maxlen=100)
        self._probe_ids: deque[str] = deque()
        self._probe_generation = 0
        self._probes_drained_generation: int | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.after_import not in library_import.after_import_hooks:
            library_import.after_import_hooks.append(self.after_import)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="probe-warming", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def after_import(self, run_id: str) -> None:
        """Queue changed artifacts directly; only a restarted driver needs the broad fallback."""
        changes = library_import.import_changes(run_id)
        if changes is not None:
            if changes.artifact_ids:
                self._probe_ids.extend(changes.artifact_ids)
                self._wake.set()
            return
        self._probe_generation += 1
        self._probe_ids.clear()
        self._wake.set()

    def prioritize(self, item_id: str) -> None:
        """Someone opened this item: measure it next."""
        if item_id not in self._wanted:
            self._wanted.append(item_id)
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception as exc:  # noqa: BLE001 - a bad file must not stop the worker
                logger.warning("Probe warming failed; retrying later: %.300s", repr(exc))
                worked = False
            if not worked:
                self._wake.wait(WANTED_RETRY_SECONDS if self._wanted else IDLE_SECONDS)
                self._wake.clear()

    @staticmethod
    def _needs_probe() -> Any:
        """SQL predicate for a new, changed or pre-stream-cache artifact."""
        fingerprint = func.json_extract(MediaArtifact.probe, "$.fingerprint")
        expected = func.printf("%d:%d", MediaArtifact.size, MediaArtifact.mtime_ns)
        acknowledged = func.json_extract(MediaArtifact.probe, f"$.{SCAN_FINGERPRINT_KEY}")
        stale = and_(
            MediaArtifact.size.is_not(None), MediaArtifact.mtime_ns.is_not(None),
            fingerprint.is_not(None), fingerprint != expected,
            or_(acknowledged.is_(None), acknowledged != expected),
        )
        incomplete = and_(
            func.json_type(MediaArtifact.probe, "$.streams").is_(None),
            func.json_extract(MediaArtifact.probe, "$.error").is_(None),
        )
        return or_(MediaArtifact.probe.is_(None), stale, incomplete)

    def _next(self, db) -> tuple[MediaArtifact, bool, bool] | None:  # noqa: ANN001
        """Return (artifact, probe_only, backfill), prioritizing opened items then codec readiness."""
        if background_work.busy() or sessions.video_encodes() > 0:
            return None
        artifacts = MediaArtifactService(db)
        while self._wanted:
            found = artifacts.artifact_for(self._wanted[0])
            if found is None or found[0].lifecycle != "available":
                self._wanted.popleft()
                continue
            artifact = found[0]
            probe = artifact.probe or {}
            expected = (
                f"{artifact.size}:{artifact.mtime_ns}"
                if artifact.size is not None and artifact.mtime_ns is not None else None
            )
            incomplete = "streams" not in probe and "error" not in probe
            stale = (
                expected is not None and probe.get("fingerprint") not in (None, expected)
                and probe.get(SCAN_FINGERPRINT_KEY) != expected
            )
            if "loudness" in probe and (stale or incomplete):
                return artifact, True, False
            self._wanted.popleft()
            if "loudness" not in probe:
                return artifact, False, False
        while True:
            while self._probe_ids:
                try:
                    artifact_id = self._probe_ids.popleft()
                except IndexError:  # after_import cleared it between the check and pop
                    continue
                unprobed = (
                    db.query(MediaArtifact)
                    .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
                    .filter(
                        MediaArtifact.id == artifact_id, MediaArtifact.lifecycle == "available",
                        StorageRoot.enabled.is_(True), self._needs_probe(),
                    )
                    .one_or_none()
                )
                if unprobed is not None:
                    return unprobed, True, False
            generation = self._probe_generation
            # A disabled backfill has no reason to rescan the large JSON queue until an import
            # invalidates it. During its window, keep the safety scan that catches direct metadata
            # changes from callers other than the import hook.
            if self._probes_drained_generation == generation and not background_work.backfill_allowed():
                break
            ids = (
                db.query(MediaArtifact.id)
                .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
                .filter(MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True), self._needs_probe())
                .order_by(MediaArtifact.created_at.desc())
                .limit(PROBE_BATCH_SIZE)
                .all()
            )
            if not ids:
                if generation != self._probe_generation:
                    continue  # an import finished after this empty snapshot; query its files before loudness
                self._probes_drained_generation = generation
                break
            self._probe_ids.extend(artifact_id for artifact_id, in ids)
            if generation != self._probe_generation:  # an import finished while the query ran; prefer its newest files
                self._probe_ids.clear()
        if not background_work.backfill_allowed():
            return None
        unmeasured = (
            db.query(MediaArtifact)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .filter(MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True), func.json_extract(MediaArtifact.probe, "$.loudness").is_(None))
            .order_by(MediaArtifact.created_at.desc())
            .first()
        )
        return (unmeasured, False, True) if unmeasured is not None else None

    def run_once(self) -> bool:
        """Warm one file. False when nothing is left (or when stopping)."""
        with SessionLocal() as db:
            selected = self._next(db)
            if selected is None:
                return False
            artifact, probe_only, backfill = selected
            artifact_id = artifact.id
        try:
            return self._probe(artifact_id) if probe_only else self._warm(artifact_id, backfill=backfill)
        except OperationalError:
            raise  # a busy database or writer slot is transient: _loop retries it later
        except Exception as exc:  # noqa: BLE001 - stamp it so it leaves the head of the newest-first queue
            logger.warning("Probe warming failed for one file; skipping it: %.300s", repr(exc))
            with SessionLocal() as db:
                artifact = db.get(MediaArtifact, artifact_id)
                if artifact is not None:
                    with write_transaction(db, name="probe_warming"):
                        if probe_only:
                            fingerprint = (
                                f"{artifact.size}:{artifact.mtime_ns}"
                                if artifact.size is not None and artifact.mtime_ns is not None else None
                            )
                            artifact.probe = {
                                "fingerprint": fingerprint, "error": "probe_failed",
                                "loudness": {"error": "measure_failed"},
                            }
                        else:
                            artifact.probe = {**(artifact.probe or {}), "loudness": {"error": "measure_failed"}}
            return True

    def _probe(self, artifact_id: str) -> bool:
        """Cache codec facts only, so full-file loudness work cannot block library readiness."""
        with SessionLocal() as db:
            artifact = db.get(MediaArtifact, artifact_id)
            root = db.get(StorageRoot, artifact.root_id)
            try:
                path = artifact_file(root.path, artifact.relative_path)
            except FileNotFoundError:
                with write_transaction(db, name="probe_warming"):
                    artifact.probe = {"fingerprint": None, "error": "missing", "loudness": {"error": "missing"}}
                return True
            facts = MediaProbeService(db).cached_facts(artifact, path)
            if (pair := pair_from_facts(facts)) is not None:
                m2ts_irap_indexes.request(path, pair, artifact.id, explicit=False)
            probe = dict(artifact.probe or {})
            expected = (
                f"{artifact.size}:{artifact.mtime_ns}"
                if artifact.size is not None and artifact.mtime_ns is not None else None
            )
            # The live file may have been probed by playback before the scanner records its new
            # stat. Remember only which scanner fingerprint this cache was checked against: the
            # scanner-owned columns stay old so its next pass still detects and processes the change.
            if expected is not None and probe.get("fingerprint") != expected and probe.get(SCAN_FINGERPRINT_KEY) != expected:
                with write_transaction(db, name="probe_warming"):
                    db.refresh(artifact)
                    probe = dict(artifact.probe or {})
                    expected = (
                        f"{artifact.size}:{artifact.mtime_ns}"
                        if artifact.size is not None and artifact.mtime_ns is not None else None
                    )
                    if expected is not None and probe.get("fingerprint") != expected:
                        artifact.probe = {**probe, SCAN_FINGERPRINT_KEY: expected}
        return True

    def _warm(self, artifact_id: str, *, backfill: bool) -> bool:
        with SessionLocal() as db:
            artifact = db.get(MediaArtifact, artifact_id)
            root = db.get(StorageRoot, artifact.root_id)
            try:
                path = artifact_file(root.path, artifact.relative_path)
            except FileNotFoundError:
                # Marked so it is not picked again; a returning file gets a new fingerprint and is probed afresh.
                with write_transaction(db, name="probe_warming"):
                    artifact.probe = {"fingerprint": None, "error": "missing", "loudness": {"error": "missing"}}
                return True
            facts = MediaProbeService(db).cached_facts(artifact, path)  # one ffprobe when stale
            fingerprint = (artifact.probe or {}).get("fingerprint")
            ffmpeg = media_tool(db, "ffmpeg")
        audios = [s for s in facts.get("streams") or [] if s.get("type") == "audio"]
        audio = next((s for s in audios if s.get("default")), audios[0] if audios else None)
        if facts.get("error") or audio is None:
            loudness: dict[str, Any] | None = {"error": facts.get("error") or "no_audio"}
        elif not ffmpeg:
            loudness = {"error": "ffmpeg_unavailable"}
        else:
            loudness = measure_loudness(ffmpeg, path, audio["index"], stop=self._stop, backfill=backfill)
            if loudness is None:
                return False  # stopping: measured again after the restart
        with SessionLocal() as db:
            artifact = db.get(MediaArtifact, artifact_id)
            probe = dict(artifact.probe or {}) if artifact else {}
            if artifact is None or probe.get("fingerprint") != fingerprint:
                return True  # the file changed while measuring; its new version is measured next
            with write_transaction(db, name="probe_warming"):
                artifact.probe = {**probe, "loudness": loudness}
        return True


probe_warming = ProbeWarming()
