"""Post-import probe warming and loudness analysis.

One background thread probes files whose cached facts are missing or stale and measures the
loudness of their default audio track, one file at a time. The next file is the one someone
just opened, else the newest with ``json_extract(probe, '$.loudness') IS NULL``, so a restart
resumes where it stopped. The loudness pass pauses while any video encode runs. Results live
inside ``artifact.probe``, so a changed file (new fingerprint) is re-measured for free.
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
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import MediaArtifact, StorageRoot
from app.persistence import write_transaction
from app.services import library_import
from app.services.local_playback_sessions import sessions
from app.services.media_artifacts import MediaArtifactService, artifact_file
from app.services.media_probe import LOCAL_INPUT_ARGS, MediaProbeService, media_tool

logger = logging.getLogger(__name__)
IDLE_SECONDS = 60
POLL_SECONDS = 1.0
LOUDNESS_TIMEOUT_SECONDS = 3600  # of running time; time paused for video encodes does not count
LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}")


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


def measure_loudness(ffmpeg: str, path: Path, stream_index: int, *, stop: threading.Event, busy: Callable[[], bool]) -> dict[str, Any] | None:
    """EBU R128 of one audio stream (loudnorm's first pass), or None when asked to stop.

    Runs on one thread and is paused with SIGSTOP whenever ``busy()`` says a video encode runs.
    """
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-threads", "1", *LOCAL_INPUT_ARGS, "-i", str(path),
        "-map", f"0:{stream_index}", "-vn", "-sn", "-dn", "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ]
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        paused, ran = False, 0.0
        try:
            while process.poll() is None:
                if stop.is_set():
                    return None
                if ran > LOUDNESS_TIMEOUT_SECONDS:
                    return {"error": "timeout"}
                if busy() != paused:
                    paused = not paused
                    _signal(process, signal.SIGSTOP if paused else signal.SIGCONT)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=POLL_SECONDS)
                if not paused:
                    ran += POLL_SECONDS
        finally:
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

    def after_import(self, _run_id: str) -> None:
        """Post-import hook: returns at once; the worker finds the new files itself."""
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
                self._wake.wait(IDLE_SECONDS)
                self._wake.clear()

    def _next(self, db) -> MediaArtifact | None:  # noqa: ANN001
        artifacts = MediaArtifactService(db)
        while self._wanted:
            found = artifacts.artifact_for(self._wanted.popleft())
            if found and found[0].lifecycle == "available" and "loudness" not in (found[0].probe or {}):
                return found[0]
        return (
            db.query(MediaArtifact)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .filter(MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True), func.json_extract(MediaArtifact.probe, "$.loudness").is_(None))
            .order_by(MediaArtifact.created_at.desc())
            .first()
        )

    def run_once(self) -> bool:
        """Warm one file. False when nothing is left (or when stopping)."""
        with SessionLocal() as db:
            artifact = self._next(db)
            if artifact is None:
                return False
            artifact_id = artifact.id
        try:
            return self._warm(artifact_id)
        except OperationalError:
            raise  # a busy database or writer slot is transient: _loop retries it later
        except Exception as exc:  # noqa: BLE001 - stamp it so it leaves the head of the newest-first queue
            logger.warning("Probe warming failed for one file; skipping it: %.300s", repr(exc))
            with SessionLocal() as db:
                artifact = db.get(MediaArtifact, artifact_id)
                if artifact is not None:
                    with write_transaction(db, name="probe_warming"):
                        artifact.probe = {**(artifact.probe or {}), "loudness": {"error": "measure_failed"}}
            return True

    def _warm(self, artifact_id: str) -> bool:
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
            loudness = measure_loudness(ffmpeg, path, audio["index"], stop=self._stop, busy=lambda: sessions.video_encodes() > 0)
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
