"""Model downloads.

One background thread per model fetches its catalog files into ``models/<id>/<name>.part``, resuming
with HTTP Range after an interruption or a restart (a ``.wanted`` marker survives restarts). Every
file's sha256 is verified before any file is renamed into place and the ``.ready`` marker is written,
so no process ever loads unverified bytes. Only catalog URLs are fetched: every hop, redirects
included, must be https to a catalog host or CDN host (plain http only to 127.0.0.1, the test fake),
at most MAX_REDIRECTS hops, and a body larger than the catalog size plus 1 % is aborted. Failure
reasons are fixed strings; URLs, paths and bodies are never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from app.persistence import atomic_write
from app.services import model_catalog
from app.services.model_catalog import CatalogModel, ModelFile

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1 << 20
MAX_REDIRECTS = 5
MAX_ATTEMPTS = 3  # consecutive attempts without progress before the model is failed (Retry)
RETRY_DELAYS = (1.0, 4.0, 15.0)
SPACE_MARGIN = 1.10
SIZE_TOLERANCE = 1.01
PROGRESS_INTERVAL_SECONDS = 1.0
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0

CORRUPTED = "The download was corrupted; Retry"
UNREACHABLE = "The download kept failing; check this server's internet connection and Retry"
OVERSIZE = "The download was larger than expected"
REDIRECT = "The download was redirected to an unexpected host"
GONE = "The model file is no longer available from its source"
UNSAVED = "The download could not be saved; check free space and Retry"

State = Literal["absent", "downloading", "verifying", "ready", "failed"]


@dataclass(frozen=True)
class DownloadStatus:
    state: State
    bytes_done: int | None = None
    bytes_total: int | None = None
    reason: str | None = None


class _Stopped(Exception):
    """Cancel, remove or shutdown asked the worker to stop."""


class _Retryable(Exception):
    """A network failure worth another attempt."""


class _Failed(Exception):
    """Terminal; ``str()`` is the admin-facing reason."""


class _Job:
    def __init__(self, model: CatalogModel, bytes_done: int) -> None:
        self.model = model
        self.stop = threading.Event()
        self.discard = False  # cancel/remove: delete the partial files once the thread stops
        self.state: State = "downloading"
        self.bytes_done = bytes_done
        self.last_notified = 0.0
        self.thread: threading.Thread | None = None


def disk_free(path: Path) -> int | None:
    """Free bytes on the volume holding ``path`` (created before this is called); None when unknown = refuse."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def space_reason(needed: int, free: int) -> str:
    return f"Not enough free space: needs {math.ceil(needed / 1_000_000)} MB, {free // 1_000_000} MB free"


def _part(model: CatalogModel, file: ModelFile) -> Path:
    return model_catalog.model_dir(model) / f"{file.name}.part"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _partial_bytes(model: CatalogModel) -> int:
    return sum(min(_size(_part(model, file)), file.size) for file in model.files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_hop(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    secure = parts.scheme == "https" or (parts.scheme == "http" and host == "127.0.0.1")
    if not secure or parts.username or parts.password or not model_catalog.catalog().allows_host(host):
        raise _Failed(REDIRECT)


def _range_start(content_range: str | None) -> int | None:
    """The first byte of ``bytes <start>-<end>/<total>``; None when absent or malformed."""
    try:
        return int((content_range or "").split()[1].split("-")[0])
    except (IndexError, ValueError):
        return None


class DownloadManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._failures: dict[str, str] = {}
        self.on_change: Callable[[str], None] = lambda model_id: None

    def status(self, model: CatalogModel) -> DownloadStatus:
        with self._lock:
            job = self._jobs.get(model.id)
            if job is not None:
                return DownloadStatus(job.state, job.bytes_done, model.size_bytes)
            failure = self._failures.get(model.id)
        if model_catalog.is_installed(model):
            return DownloadStatus("ready", model.size_bytes, model.size_bytes)
        done = _partial_bytes(model)
        if failure is not None:
            return DownloadStatus("failed", done, model.size_bytes, failure)
        return DownloadStatus("absent", done or None, model.size_bytes if done else None)

    def start(self, model: CatalogModel) -> DownloadStatus:
        """Start the model's download (Download / Retry); a no-op while it runs or once it is installed."""
        if model_catalog.is_installed(model):
            return self.status(model)
        directory = model_catalog.model_dir(model)
        with self._lock:
            if model.id not in self._jobs:
                directory.mkdir(parents=True, exist_ok=True)
                remaining = sum(max(file.size - _size(_part(model, file)), 0) for file in model.files)
                needed = math.ceil(remaining * SPACE_MARGIN)
                free = disk_free(directory)
                if free is None or free < needed:
                    self._failures[model.id] = space_reason(needed, free or 0)
                else:
                    self._failures.pop(model.id, None)
                    (directory / model_catalog.WANTED_MARKER).touch()
                    job = _Job(model, model.size_bytes - remaining)
                    job.thread = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"model-download-{model.id}")
                    self._jobs[model.id] = job
                    job.thread.start()
        self._emit(model.id)
        return self.status(model)

    def cancel(self, model: CatalogModel) -> None:
        """Stop the download and delete its partial files (Cancel)."""
        with self._lock:
            job = self._jobs.get(model.id)
            if job is not None:
                job.discard = True
                job.stop.set()
            self._failures.pop(model.id, None)
        if job is not None and job.thread is not None:
            job.thread.join(timeout=READ_TIMEOUT_SECONDS + 5)  # the thread deletes the partials itself
        else:
            self._discard_partials(model)
        self._emit(model.id)

    def remove(self, model: CatalogModel) -> None:
        """Cancel any download and delete the model's directory (Remove)."""
        self.cancel(model)
        shutil.rmtree(model_catalog.model_dir(model), ignore_errors=True)
        self._emit(model.id)

    def resume_wanted(self) -> list[str]:
        """Startup: resume every download a restart interrupted (a ``.wanted`` marker and not installed)."""
        resumed = []
        for model in model_catalog.catalog().models:
            if (model_catalog.model_dir(model) / model_catalog.WANTED_MARKER).exists() and not model_catalog.is_installed(model):
                self.start(model)
                resumed.append(model.id)
        return resumed

    def shutdown(self) -> None:
        """Stop every download, keeping partial files and ``.wanted`` markers for the next start."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.stop.set()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(timeout=5)

    def _emit(self, model_id: str) -> None:
        try:
            self.on_change(model_id)
        except Exception as exc:  # noqa: BLE001 - a listener never breaks a download
            logger.warning("Model change listener failed: %s", type(exc).__name__)

    def _notify(self, job: _Job) -> None:
        now = time.monotonic()
        if now - job.last_notified >= PROGRESS_INTERVAL_SECONDS:
            job.last_notified = now
            self._emit(job.model.id)

    def _discard_partials(self, model: CatalogModel) -> None:
        for file in model.files:
            _part(model, file).unlink(missing_ok=True)
        (model_catalog.model_dir(model) / model_catalog.WANTED_MARKER).unlink(missing_ok=True)

    def _run(self, job: _Job) -> None:
        model = job.model
        directory = model_catalog.model_dir(model)
        failure: str | None = None
        try:
            for file in model.files:
                self._fetch_with_retries(job, file)
            job.state = "verifying"
            self._emit(model.id)
            for file in model.files:
                if job.stop.is_set():
                    raise _Stopped()
                if _sha256(_part(model, file)) != file.sha256:
                    _part(model, file).unlink(missing_ok=True)
                    raise _Failed(CORRUPTED)
            for file in model.files:
                os.replace(_part(model, file), directory / file.name)
            atomic_write(directory / model_catalog.READY_MARKER, json.dumps({file.name: file.sha256 for file in model.files}, sort_keys=True))
            (directory / model_catalog.WANTED_MARKER).unlink(missing_ok=True)
        except _Stopped:
            pass
        except _Failed as exc:
            failure = str(exc)
        except OSError as exc:  # disk full while writing, or the directory was removed under us
            logger.warning("Model download %s could not be saved: %s", model.id, type(exc).__name__)
            failure = UNSAVED
        except Exception as exc:  # noqa: BLE001 - a download thread never dies silently with a job left behind
            logger.error("Model download %s failed: %s", model.id, type(exc).__name__)
            failure = UNREACHABLE
        finally:
            with self._lock:
                self._jobs.pop(model.id, None)
                if failure is not None and not job.discard:
                    self._failures[model.id] = failure
            if failure is not None:
                (directory / model_catalog.WANTED_MARKER).unlink(missing_ok=True)  # a restart does not loop on a failure
            if job.discard:
                self._discard_partials(model)
            self._emit(model.id)

    def _fetch_with_retries(self, job: _Job, file: ModelFile) -> None:
        failures = 0
        while True:
            before = _size(_part(job.model, file))
            try:
                self._fetch(job, file)
                return
            except _Retryable:
                failures = 0 if _size(_part(job.model, file)) > before else failures + 1
                if failures >= MAX_ATTEMPTS:
                    raise _Failed(UNREACHABLE) from None
                if job.stop.wait(RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)]):
                    raise _Stopped() from None

    def _fetch(self, job: _Job, file: ModelFile) -> None:
        """One attempt at one file: resume the ``.part`` from its current size, following allowed redirects."""
        part = _part(job.model, file)
        have = _size(part)
        if have > file.size:
            part.unlink()
            have = 0
        if have == file.size:
            return
        url = file.url
        headers = {"Range": f"bytes={have}-"} if have else {}
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    _check_hop(url)
                    with client.stream("GET", url, headers=headers) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        self._write_body(job, file, response, have)
                        return
                raise _Failed(REDIRECT)
        except httpx.HTTPError as exc:
            raise _Retryable() from exc

    def _write_body(self, job: _Job, file: ModelFile, response: httpx.Response, have: int) -> None:
        part = _part(job.model, file)
        cap = math.floor(file.size * SIZE_TOLERANCE)
        status = response.status_code
        if status == 206 and have and _range_start(response.headers.get("content-range")) == have:
            mode = "ab"
        elif status == 200:
            have, mode = 0, "wb"  # a server that ignores Range sends the whole file: rewrite it
        elif status == 416:
            part.unlink(missing_ok=True)  # our partial no longer fits the file: start over
            raise _Retryable()
        elif status in (401, 403, 404, 410):
            raise _Failed(GONE)
        else:
            raise _Retryable()
        length = response.headers.get("content-length", "")
        if length.isdigit() and have + int(length) > cap:
            raise _Failed(OVERSIZE)
        others = sum(min(_size(_part(job.model, other)), other.size) for other in job.model.files if other is not file)
        with part.open(mode) as out:
            for block in response.iter_bytes(CHUNK_BYTES):
                if job.stop.is_set():
                    raise _Stopped()
                have += len(block)
                if have > cap:
                    out.close()
                    part.unlink(missing_ok=True)
                    raise _Failed(OVERSIZE)
                out.write(block)
                job.bytes_done = others + min(have, file.size)
                self._notify(job)
        if have < file.size:
            raise _Retryable()  # the body ended early


manager = DownloadManager()
