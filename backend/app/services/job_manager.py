from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
import weakref
from contextlib import suppress
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session
from yt_dlp.utils import Popen as YtDlpPopen

from app.db import SessionLocal
from app.events import EventBus
from app.persistence import write_transaction
from app.models import DownloadJob, LibraryItem, MediaArtifact, StorageRoot, User, utcnow
from app.schemas import FormatResolutionState, FormatSelection, JobCreateRequest, JobOutput, JobResponse, OutputProfile
from app.services.hls_relay_support import derive_provider
from app.services.library import (
    LibraryService,
    decode_library_cursor,
    encode_library_cursor,
    infer_media_kind,
    resolve_download_output_path,
)
from app.services.storage_routing import (
    RouteDecision,
    StorageRoutingService,
    StorageUnavailable,
    publish_file,
    staging_dir,
)
from app.services.acquisition_batch import RedactedFailure
from app.services.acquisition_job_manager import JobManagerAcquisitionAdapter
from app.services.transcripts import TranscriptService
from app.services.redaction import redact
from app.services.webhooks import WebhookService
from app.services.yt_dlp_service import DownloadCancelled, YtDlpService


RETRYABLE_STATUSES = frozenset({"failed", "cancelled", "interrupted"})
SHUTDOWN_INTERRUPTION = "Interrupted by a server shutdown; retry to download it again."
RESTART_INTERRUPTION = "Interrupted by a server restart; retry to download it again."

# yt-dlp runs FFmpeg and external downloaders through its own Popen subclass
# (``yt_dlp.utils.Popen``, not ``subprocess.Popen``). Children spawned while a
# job's attempt runs are recorded under that job (a ContextVar set in the job's
# worker thread), so cancel and shutdown can end exactly that job's children —
# a long FFmpeg merge never returns to Python for a progress hook to stop it.
# Processes spawned outside a job (previews, live extraction) are not tracked.
_current_job: ContextVar[str | None] = ContextVar("lumina_ytdlp_job", default=None)
_job_children: dict[str, weakref.WeakSet] = {}
_job_children_lock = Lock()

if not getattr(YtDlpPopen.__init__, "_lumina_tracked", False):  # idempotent across reloads
    _ytdlp_popen_init = YtDlpPopen.__init__

    def _tracked_popen_init(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN001
        _ytdlp_popen_init(self, *args, **kwargs)
        job_id = _current_job.get()
        if job_id is not None:
            with _job_children_lock:
                _job_children.setdefault(job_id, weakref.WeakSet()).add(self)

    _tracked_popen_init._lumina_tracked = True  # type: ignore[attr-defined]
    YtDlpPopen.__init__ = _tracked_popen_init  # type: ignore[method-assign]


def _terminate_job_children(job_ids: list[str], grace_seconds: float | None) -> None:
    """SIGTERM the live children of these jobs; with a grace, wait then SIGKILL."""
    with _job_children_lock:
        children = [child for job_id in job_ids for child in list(_job_children.get(job_id, ())) if child.poll() is None]
    for child in children:
        child.terminate()
    if grace_seconds is None:
        return
    for child in children:
        try:
            child.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            child.kill()


class JobConflictError(ValueError):
    """The job exists but its current state does not allow the requested action."""


class JobAdmissionError(ValueError):
    """Workload limits refuse new work; `reason` is a stable machine-readable code."""

    def __init__(self, reason: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class StorageReserveExceeded(Exception):
    pass


ACTIVE_STATUSES = ("queued", "running", "postprocessing")
# job id -> (progress %, bytes/s) of the latest yt-dlp hook, for the admin Activity page; readers drop ids that are no longer running.
LIVE_PROGRESS: dict[str, tuple[float | None, float | None]] = {}
DISK_CHECK_INTERVAL_SECONDS = 2.0


def _free_bytes(root: str) -> int | None:
    # The root may not exist until its first download; measure the volume it will live on.
    path = Path(root).expanduser()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None  # unknown; admission treats it as full rather than writing blind


def _reserved_bytes(db: Session, user_id: str) -> int:
    """Known/estimated sizes of this member's other active jobs, not yet written to disk.

    Sums across all the member's active jobs regardless of which root
    they route to; scope per-root if multi-root household routing makes that matter.
    """
    total = 0
    rows = db.query(DownloadJob.preview_snapshot).filter(DownloadJob.user_id == user_id, DownloadJob.status.in_(ACTIVE_STATUSES))
    for (preview,) in rows:
        size = (preview or {}).get("filesize") or (preview or {}).get("filesize_approx")
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            total += int(size)
    return total


def _requested_route(db: Session, preview_snapshot: dict[str, Any] | None, format_selection: dict[str, Any] | None) -> RouteDecision:
    """Where the request would land before its output is known (height unknown, never guessed)."""
    selection = format_selection or {}
    audio = selection.get("extract_audio") or selection.get("preset") == "audio_only"
    return StorageRoutingService(db).decide(derive_provider(preview_snapshot or {}), "audio" if audio else "video", None)


def _actual_height(entry: dict[str, Any]) -> int | None:
    downloads = entry.get("requested_downloads") if isinstance(entry.get("requested_downloads"), list) else []
    for candidate in (entry, *downloads):
        height = candidate.get("height") if isinstance(candidate, dict) else None
        if isinstance(height, int) and not isinstance(height, bool) and height > 0:
            return height
    return None


def _discard_staging(db: Session, job: DownloadJob) -> None:
    """Drop an attempt's staging and any publication its Library commit never confirmed.

    A journaled file is removed only while unregistered AND still hard-linked to
    this job's staging bytes, so unrelated media at that path is never touched.
    """
    routing = job.routing or {}
    pending = routing.get("publishing") or {}
    roots = [root for root_id in {routing.get("staging_root_id"), pending.get("root_id")} if root_id and (root := db.get(StorageRoot, root_id))]
    staged = set()
    for root in roots:
        for path in staging_dir(root, job.id).rglob("*"):
            with suppress(OSError):
                status = path.lstat()
                staged.add((status.st_dev, status.st_ino))
    pending_root = db.get(StorageRoot, pending["root_id"]) if pending else None
    if pending_root is not None and not db.query(MediaArtifact.id).filter_by(root_id=pending_root.id, relative_path=pending["path"]).first():
        published = Path(pending_root.path) / pending["path"]
        with suppress(OSError):
            status = published.lstat()
            if (status.st_dev, status.st_ino) in staged:
                published.unlink()
    for root in roots:
        shutil.rmtree(staging_dir(root, job.id), ignore_errors=True)


def _admit(db: Session, user_id: str, preview_snapshot: dict[str, Any] | None, format_selection: dict[str, Any] | None) -> None:
    """Refuse work beyond the member's active-job cap or into an offline or full target root.

    The reserve check subtracts other already-queued jobs' known/estimated
    sizes from free space, so several admissions in a row can't each see the
    same untouched free space; the midstream check in the progress hook bounds
    the rest once a download is actually writing bytes.
    """
    app_settings = YtDlpService(db).get_app_settings()
    active = (
        db.query(DownloadJob.id)
        .filter(DownloadJob.user_id == user_id, DownloadJob.status.in_(ACTIVE_STATUSES))
        .count()
    )
    if active >= app_settings.max_active_jobs_per_user:
        raise JobAdmissionError(
            "member_queue_full",
            f"You already have {active} downloads queued or running; wait for some to finish.",
            429,
        )
    root = StorageRoutingService(db).writable_root(_requested_route(db, preview_snapshot, format_selection))
    if root is None:
        raise JobAdmissionError(
            "storage_unavailable",
            "The storage location for this download is offline; an administrator needs to check it.",
            503,
        )
    if (_free_bytes(root.path) or 0) - _reserved_bytes(db, user_id) < app_settings.min_free_disk_mb * 1024 * 1024:
        raise JobAdmissionError(
            "storage_low",
            "The library is nearly out of free space; an administrator needs to free some before new downloads.",
            409,
        )


class JobManager:
    def __init__(self, events: EventBus):
        self.events = events
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._worker_tasks: list[asyncio.Task] = []
        self._cancel_flags: dict[str, Event] = {}
        self._scheduled_job_ids: set[str] = set()
        self._scheduled_lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        # job id -> set when its blocking worker thread has fully exited.
        self._running: dict[str, Event] = {}
        self.acquisition = JobManagerAcquisitionAdapter(self, lambda: SessionLocal())

    async def start(self, *, concurrency: int = 1) -> None:
        if not self._worker_tasks:
            self._loop = asyncio.get_running_loop()
            self._stopping = False
            self._reconcile_interrupted_jobs()
            self.acquisition.reconcile_startup()
            self._enqueue_queued_jobs()
            self._worker_tasks = [
                asyncio.create_task(self._worker_loop()) for _ in range(max(1, concurrency))
            ]
            self._worker_task = self._worker_tasks[0]

    async def stop(self, *, grace_seconds: float = 20.0) -> bool:
        """Stop admission, cancel in-flight work and join its blocking threads.

        Returns False when some work outlived the grace period; that work is
        recorded as interrupted so the next start never re-runs it blindly.
        """
        self._stopping = True
        for job_id in list(self._running):
            self._cancel_flags.setdefault(job_id, Event()).set()
        for worker_task in self._worker_tasks:
            worker_task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []
        self._worker_task = None
        # Cancelling the asyncio wrappers does not stop to_thread work: join it.
        return await asyncio.to_thread(self._join_running, grace_seconds)

    def _join_running(self, grace_seconds: float) -> bool:
        half = grace_seconds / 2
        for done in list(self._running.values()):
            done.wait(timeout=half)
        if self._running:
            _terminate_job_children(list(self._running), half / 2)
            for done in list(self._running.values()):
                done.wait(timeout=half / 2)
        stuck = list(self._running)
        if stuck:
            with SessionLocal() as db, write_transaction(db, name="jobs_shutdown_incomplete"):
                for job in db.query(DownloadJob).filter(DownloadJob.id.in_(stuck)):
                    job.status = "interrupted"
                    job.finished_at = utcnow()
                    job.error = f"{SHUTDOWN_INTERRUPTION} Cleanup did not finish; partial files may remain."
        return not stuck

    def enqueue(self, db: Session, payload: JobCreateRequest, user: User) -> DownloadJob:
        # URL validation resolves DNS, so it must finish before the writer is held.
        normalized_source_url = YtDlpService(db).validate_source_url(payload.source_url)
        with write_transaction(db, name="job_enqueue"):
            job = self.stage_enqueue(db, payload, user, normalized_source_url=normalized_source_url)
        db.refresh(job)
        self.dispatch_staged(job)
        return job

    def stage_enqueue(
        self, db: Session, payload: JobCreateRequest, user: User, *, normalized_source_url: str | None = None
    ) -> DownloadJob:
        """Persist a queued job in the caller's write transaction without committing or dispatching it.

        Callers holding a write transaction must pass a pre-validated
        `normalized_source_url`: validating here resolves DNS, which must never
        run while the writer is held.
        """
        if normalized_source_url is None:
            normalized_source_url = YtDlpService(db).validate_source_url(payload.source_url)
        _admit(db, user.id, payload.preview_snapshot, payload.format_selection.model_dump())
        job = DownloadJob(
            id=str(uuid.uuid4()),
            user_id=user.id,
            source_url=normalized_source_url,
            status="queued",
            queue_position=self._queue.qsize() + 1,
            format_selection=payload.format_selection.model_dump(),
            output_profile=payload.output_profile.model_dump(),
            preview_snapshot=self._compact_preview_snapshot(payload.preview_snapshot),
            error=None,
        )
        db.add(job)
        db.flush()
        return job

    def dispatch_staged(self, job: DownloadJob) -> bool:
        """Make an already-committed staged job visible to the worker and observers."""
        self._cancel_flags.setdefault(job.id, Event())
        if not self._enqueue_job_id(job.id):
            return False
        self.events.publish("job_queued", self._job_event_payload(job))
        return True

    JOB_PAGE_DEFAULT_LIMIT = 50
    JOB_PAGE_MAX_LIMIT = 100

    @staticmethod
    def list_for_user(
        db: Session,
        user: User,
        *,
        cursor: str | None = None,
        limit: int = JOB_PAGE_DEFAULT_LIMIT,
    ) -> tuple[list[DownloadJob], str | None]:
        """Return one bounded page of jobs whose status belongs to the public job contract."""
        bounded_limit = max(1, min(JobManager.JOB_PAGE_MAX_LIMIT, limit))
        query = JobManager.page_query(db, user, cursor=cursor).limit(bounded_limit + 1)
        rows = query.all()
        jobs = rows[:bounded_limit]
        next_cursor = None
        if len(rows) > bounded_limit:
            last = jobs[-1]
            next_cursor = encode_library_cursor({"c": last.created_at.isoformat(), "i": last.id})
        return jobs, next_cursor

    @staticmethod
    def page_query(db: Session, user: User, *, cursor: str | None = None):
        query = (
            db.query(DownloadJob)
            .filter(DownloadJob.user_id == user.id, DownloadJob.status != "staged")
            .order_by(DownloadJob.created_at.desc(), DownloadJob.id.desc())
        )
        if cursor is not None:
            payload = decode_library_cursor(cursor)
            try:
                created_at = datetime.fromisoformat(payload["c"])
                job_id = payload["i"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid job page cursor") from exc
            if not isinstance(job_id, str):
                raise ValueError("Invalid job page cursor")
            query = query.filter(
                or_(
                    DownloadJob.created_at < created_at,
                    and_(DownloadJob.created_at == created_at, DownloadJob.id < job_id),
                )
            )
        return query

    @staticmethod
    def get_for_user(db: Session, job_id: str, user: User) -> DownloadJob | None:
        job = db.get(DownloadJob, job_id)
        if job is None or job.user_id != user.id or job.status == "staged":
            return None
        return job

    def cancel(self, db: Session, job_id: str, user: User) -> DownloadJob:
        job = self.get_for_user(db, job_id, user)
        if job is None:
            raise ValueError("Job not found")
        flag = self._cancel_flags.setdefault(job_id, Event())
        flag.set()
        _terminate_job_children([job_id], None)  # e.g. an FFmpeg merge no hook can interrupt
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = utcnow()
            job.error = "Cancelled before start"
            self.events.publish("job_failed", self._job_event_payload(job))
        with write_transaction(db, name="job_cancel"):
            db.flush()
        if job.status == "cancelled" and self.acquisition.is_linked(db, job.id):
            try:
                self.acquisition.observe_in_session(
                    db,
                    job,
                    "cancelled",
                    failure=RedactedFailure("job_cancelled", "The download job was cancelled."),
                )
            except Exception:  # Acquisition history must not change ordinary cancellation semantics.
                pass
        return job

    def retry(self, db: Session, job_id: str, user: User) -> DownloadJob:
        job = self.get_for_user(db, job_id, user)
        if job is None:
            raise ValueError("Job not found")
        if self.acquisition.is_linked(db, job.id):
            raise JobConflictError("Acquisition jobs must be retried from their acquisition batch.")
        self._require_retryable(job.status)
        YtDlpService(db).validate_source_url(job.source_url)
        with write_transaction(db, name="job_retry"):
            # Re-read under the writer slot: the pre-check above is only a fast path.
            db.refresh(job)
            observed = job.status
            self._require_retryable(observed)
            _admit(db, user.id, job.preview_snapshot, job.format_selection)
            attempt = {
                "status": observed,
                "error": job.error,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
            # The status predicate makes admission atomic: a concurrent retry
            # that already claimed this job leaves nothing for this one to update.
            claimed = db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job.id, DownloadJob.status == observed)
                .values(
                    status="queued",
                    started_at=None,
                    finished_at=None,
                    error=None,
                    attempts=[*(job.attempts or []), attempt],
                    queue_position=self._queue.qsize() + 1,
                )
            )
            if claimed.rowcount != 1:
                raise JobConflictError("This download was already retried.")
        db.refresh(job)
        self._cancel_flags[job.id] = Event()
        self._enqueue_job_id(job.id)
        self.events.publish("job_queued", self._job_event_payload(job))
        return job

    @staticmethod
    def _require_retryable(status: str) -> None:
        if status not in RETRYABLE_STATUSES:
            raise JobConflictError(
                f"Only {' or '.join(sorted(RETRYABLE_STATUSES))} downloads can be retried; this one is {status}."
            )

    def clear_completed(self, db: Session, user: User) -> int:
        completed_jobs = (
            db.query(DownloadJob)
            .filter(DownloadJob.user_id == user.id, DownloadJob.status == "completed")
            .all()
        )
        if not completed_jobs:
            return 0
        deleted = len(completed_jobs)
        with write_transaction(db, name="jobs_clear_completed"):
            for job in completed_jobs:
                db.delete(job)
        return deleted

    @staticmethod
    def _compact_preview_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        if not snapshot:
            return snapshot
        compact: dict[str, Any] = {}
        for key in [
            "id",
            "title",
            "thumbnail",
            "webpage_url",
            "uploader",
            "duration",
            "availability",
            "extractor",
            "extractor_key",
            "playlist",
            "playlist_title",
            "playlist_count",
            "_type",
            "format_resolution",
            "filesize",
            "filesize_approx",
        ]:
            value = snapshot.get(key)
            if value is not None:
                if key == "format_resolution":
                    try:
                        compact[key] = FormatResolutionState.model_validate(value).model_dump(mode="json")
                    except (TypeError, ValueError):
                        continue
                else:
                    compact[key] = value
        entries = snapshot.get("entries")
        if isinstance(entries, list):
            compact_entries: list[dict[str, Any]] = []
            for entry in entries[:200]:
                if not isinstance(entry, dict):
                    continue
                compact_entry = {
                    key: entry.get(key)
                    for key in ["id", "title", "thumbnail", "webpage_url", "uploader", "duration", "availability"]
                    if entry.get(key) is not None
                }
                if compact_entry:
                    compact_entries.append(compact_entry)
            if compact_entries:
                compact["entries"] = compact_entries
        return compact

    def _enqueue_job_id(self, job_id: str) -> bool:
        if self._stopping:
            # Admission is closed; the durable queued row is picked up next start.
            return False
        with self._scheduled_lock:
            if job_id in self._scheduled_job_ids:
                return False
            self._scheduled_job_ids.add(job_id)
            try:
                if self._loop is None:
                    self._queue.put_nowait(job_id)
                    return True
                try:
                    on_worker_loop = asyncio.get_running_loop() is self._loop
                except RuntimeError:
                    on_worker_loop = False
                if on_worker_loop:
                    self._queue.put_nowait(job_id)
                else:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, job_id)
            except Exception:
                self._scheduled_job_ids.discard(job_id)
                raise
        return True

    def _reconcile_interrupted_jobs(self) -> None:
        """Settle attempts a crash left mid-flight instead of blindly re-running them.

        Work whose media already reached the Library is adopted as completed;
        anything else is left explicitly interrupted for the member to retry.
        """
        with SessionLocal() as db:
            stranded = db.query(DownloadJob).filter(DownloadJob.status.in_(["running", "postprocessing"])).all()
            if not stranded:
                return
            library = LibraryService(db, self.events)
            published = {job.id for job in stranded if self._published_media_exists(db, library, job)}
            now = utcnow()
            with write_transaction(db, name="jobs_recover"):
                for job in stranded:
                    job.finished_at = now
                    if job.id in published:
                        job.status = "completed"
                        job.error = None
                    else:
                        job.status = "interrupted"
                        job.error = RESTART_INTERRUPTION
            for job in stranded:
                _discard_staging(db, job)

    @staticmethod
    def _published_media_exists(db: Session, library: LibraryService, job: DownloadJob) -> bool:
        if (job.routing or {}).get("published"):
            return True  # a Library row committed with its published file
        remote_id = (job.preview_snapshot or {}).get("id")
        if not remote_id or not job.user_id:
            return False
        items = db.query(LibraryItem).filter(LibraryItem.user_id == job.user_id, LibraryItem.remote_id == remote_id)
        for item in items:
            try:
                library.resolve_media_path(item)
                return True
            except (FileNotFoundError, ValueError):
                continue
        return False

    def _enqueue_queued_jobs(self) -> None:
        with SessionLocal() as db:
            queued_ids = [
                job_id
                for (job_id,) in db.query(DownloadJob.id)
                .filter(DownloadJob.status == "queued")
                .order_by(DownloadJob.created_at.asc())
            ]
        for job_id in queued_ids:
            self._cancel_flags[job_id] = Event()
            self._enqueue_job_id(job_id)

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            with self._scheduled_lock:
                self._scheduled_job_ids.discard(job_id)
            try:
                try:
                    await asyncio.to_thread(self._run_job, job_id)
                except Exception as exc:  # noqa: BLE001
                    with SessionLocal() as db:
                        job = db.get(DownloadJob, job_id)
                        if job is not None:
                            with write_transaction(db, name="job_status"):
                                job.status = "failed"
                                job.finished_at = utcnow()
                                job.error = redact(str(exc), paths=True)
                            WebhookService(db).notify_job_failed(self._event_job_payload(job))
                            self.events.publish("job_failed", self._job_event_payload(job))
                            if self.acquisition.owns(job.id):
                                self._observe_acquisition(
                                    job.id,
                                    "failed",
                                    failure=RedactedFailure("job_failed", "The download job failed."),
                                )
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        # Register before checking _stopping: stop() sets the flag first and then
        # signals every registered job, so no attempt can slip past both.
        done = self._running[job_id] = Event()
        token = _current_job.set(job_id)
        try:
            if not self._stopping:
                self._run_attempt(job_id)
        finally:
            _current_job.reset(token)
            with _job_children_lock:
                _job_children.pop(job_id, None)
            try:
                with SessionLocal() as db:
                    job = db.get(DownloadJob, job_id)
                    if job is not None:
                        _discard_staging(db, job)
            finally:
                del self._running[job_id]
                done.set()

    def _run_attempt(self, job_id: str) -> None:
        with SessionLocal() as db:
            job = db.get(DownloadJob, job_id)
            if job is None or job.status != "queued":
                return
            owner = db.get(User, job.user_id) if job.user_id else None
            if owner is not None and not owner.is_active:
                # A deactivated member's queued work never starts.
                with write_transaction(db, name="job_status"):
                    job.status = "cancelled"
                    job.finished_at = utcnow()
                    job.error = "Cancelled: the owner's account is deactivated."
                self.events.publish("job_failed", self._job_event_payload(job))
                return
            acquisition_linked = self.acquisition.owns(job_id)
            ytdlp = YtDlpService(db)
            library = LibraryService(db, self.events)
            cancel_flag = self._cancel_flags.setdefault(job_id, Event())
            progress_state = {"phase": "download"}
            app_settings = ytdlp.get_app_settings()
            routing = StorageRoutingService(db)
            reserve_bytes = app_settings.min_free_disk_mb * 1024 * 1024
            disk_checked_at = [0.0]

            def ensure_storage(remaining_bytes: int = 0) -> None:
                if (_free_bytes(staging_root.path) or 0) - remaining_bytes < reserve_bytes:
                    raise StorageReserveExceeded(
                        "Stopped: the library would drop below its free-space reserve. Free space and retry."
                    )

            def progress_hook(data: dict[str, Any]) -> None:
                if cancel_flag.is_set():
                    raise DownloadCancelled(f"Job {job_id} cancelled")
                now = time.monotonic()
                if now - disk_checked_at[0] >= DISK_CHECK_INTERVAL_SECONDS:
                    disk_checked_at[0] = now
                    expected = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    ensure_storage(max(0, int(expected) - int(data.get("downloaded_bytes") or 0)))
                if progress_state["phase"] != "download":
                    progress_state["phase"] = "download"
                    job.status = "running"
                total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded_bytes = data.get("downloaded_bytes")
                progress = None
                if total_bytes and downloaded_bytes:
                    progress = round((downloaded_bytes / total_bytes) * 100, 1)
                LIVE_PROGRESS[job_id] = (progress, data.get("speed"))
                self.events.publish(
                    "job_progress",
                    self._job_event_payload(
                        job,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        eta=data.get("eta"),
                        speed=data.get("speed"),
                        progress=progress,
                        progress_status=data.get("status"),
                        fragment_index=data.get("fragment_index"),
                        fragment_count=data.get("fragment_count"),
                    ),
                )
                if acquisition_linked and progress is not None:
                    self._observe_acquisition(job.id, "running", progress=round(progress))

            def postprocessor_hook(data: dict[str, Any]) -> None:
                if cancel_flag.is_set():
                    raise DownloadCancelled(f"Job {job_id} cancelled")
                progress_state["phase"] = "postprocess"
                job.status = "postprocessing"
                self.events.publish(
                    "job_postprocess",
                    self._job_event_payload(
                        job,
                        progress_status=data.get("status"),
                        postprocessor=data.get("postprocessor"),
                    ),
                )
                if acquisition_linked:
                    self._observe_acquisition(job.id, "running")

            with write_transaction(db, name="job_status"):
                job.status = "running"
                job.started_at = utcnow()
                job.error = None
                # Stage on the root the request would route to; the actual output decides the final root.
                provisional = _requested_route(db, job.preview_snapshot, job.format_selection)
                job.routing = {"staging_root_id": provisional.root_id, "published": []}
            staging_root = routing.writable_root(provisional)
            self.events.publish("job_started", self._job_event_payload(job))
            if acquisition_linked:
                self._observe_acquisition(job.id, "running")
            try:
                if not job.user_id:
                    raise ValueError("Download job has no Library owner.")
                if staging_root is None:
                    raise StorageUnavailable("Stopped: the storage location for this download is offline. Ask an administrator to check it, then retry.")
                ensure_storage()
                staging = staging_dir(staging_root, job.id)
                acquisition = ytdlp.download(
                    job.source_url,
                    format_selection=FormatSelection(**job.format_selection),
                    output_profile=OutputProfile(**job.output_profile),
                    owner_user_id=job.user_id,
                    output_root=staging,
                    progress_hooks=[progress_hook],
                    postprocessor_hooks=[postprocessor_hook],
                    previous_resolution=(job.preview_snapshot or {}).get("format_resolution"),
                )
                sanitized = acquisition.info
                job.preview_snapshot = {
                    **(job.preview_snapshot or {}),
                    "format_resolution": acquisition.format_resolution.model_dump(mode="json"),
                }
                preview_snapshot = job.preview_snapshot or {}
                saved_items = []

                def journal(root_id: str, path: str) -> None:
                    with write_transaction(db, name="job_publication"):
                        job.routing = {**job.routing, "publishing": {"root_id": root_id, "path": path}}

                def record(final: Path, decision: RouteDecision, entry: dict[str, Any], kind: str, height: int | None) -> LibraryItem:
                    with write_transaction(db, name="library_item_upsert"):
                        item = library.upsert_from_info({**entry, "filepath": str(final)}, owner_user_id=job.user_id, visibility="private")
                        library.resolve_media_path(item)  # registered, or roll back
                        root = db.get(StorageRoot, decision.root_id)
                        applied = {
                            **decision.snapshot(), "media_kind": kind, "actual_height": height,
                            "library_item_id": item.id, "root_label": root.label if root else None,
                        }
                        job.routing = {**job.routing, "publishing": None, "published": [*job.routing["published"], applied]}
                    return item

                caption_sources: list[dict[str, Any]] = []
                for entry in self._flatten_entries(sanitized):
                    filepath = resolve_download_output_path(entry)
                    staged = Path(filepath) if filepath else None
                    if staged is None or not staged.is_file() or not staged.resolve().is_relative_to(staging.resolve()):
                        continue
                    merged_entry = entry
                    if preview_snapshot and preview_snapshot.get("id") == entry.get("id"):
                        merged_entry = {**preview_snapshot, **entry}
                    kind, height = infer_media_kind(entry, str(staged)), _actual_height(entry)
                    caption_sources.append(entry)
                    saved_items.append(publish_file(
                        db, staged, source=derive_provider(merged_entry), media_kind=kind, height=height,
                        relative=staged.relative_to(staging).as_posix(), key=job.id, journal=journal,
                        record=lambda final, decision, entry=merged_entry, kind=kind, height=height: record(final, decision, entry, kind, height),
                    ))
                if not saved_items:
                    raise FileNotFoundError("Download finished but no local media file was found.")
                # Captions are read from staging (still present until the attempt's discard).
                TranscriptService(db).ingest_download_captions(zip(caption_sources, saved_items))
                with write_transaction(db, name="job_status"):
                    job.status = "completed"
                    job.finished_at = utcnow()
                self.events.publish("job_completed", self._job_event_payload(job))
                if acquisition_linked:
                    self._observe_acquisition(
                        job.id,
                        "completed",
                        progress=100,
                        library_item_id=saved_items[0].id if saved_items else None,
                    )
            except Exception as exc:  # noqa: BLE001
                if self._stopping:
                    # Shutdown (a cancel flag or a terminated child) ended this
                    # attempt; record it as retryable rather than user-cancelled.
                    with write_transaction(db, name="job_status"):
                        job.status = "interrupted"
                        job.finished_at = utcnow()
                        job.error = SHUTDOWN_INTERRUPTION
                    if acquisition_linked:
                        self._observe_acquisition(
                            job.id,
                            "failed",
                            failure=RedactedFailure("job_interrupted", "The download was interrupted by a shutdown."),
                        )
                    return
                if isinstance(exc, DownloadCancelled) or cancel_flag.is_set():
                    with write_transaction(db, name="job_status"):
                        job.status = "cancelled"
                        job.finished_at = utcnow()
                        job.error = str(exc)
                    self.events.publish("job_failed", self._job_event_payload(job))
                    if acquisition_linked:
                        self._observe_acquisition(
                            job.id,
                            "cancelled",
                            failure=RedactedFailure("job_cancelled", "The download job was cancelled."),
                        )
                    return
                with write_transaction(db, name="job_status"):
                    job.status = "failed"
                    job.finished_at = utcnow()
                    job.error = str(exc) if isinstance(exc, (StorageReserveExceeded, StorageUnavailable)) else redact(YtDlpService.explain_download_error(str(exc)), paths=True)
                WebhookService(db).notify_job_failed(self._event_job_payload(job))
                self.events.publish("job_failed", self._job_event_payload(job))
                if acquisition_linked:
                    self._observe_acquisition(
                        job.id,
                        "failed",
                        failure=RedactedFailure("job_failed", "The download job failed."),
                    )

    def _observe_acquisition(self, job_id: str, status: str, **options: Any) -> None:
        try:
            self.acquisition.observe(job_id, status, **options)
        except Exception:
            # The durable job state remains authoritative; startup reconciliation
            # repairs acquisition history without changing the download outcome.
            pass

    @staticmethod
    def _flatten_entries(info: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not info:
            return []
        if info.get("_type") == "playlist":
            entries = []
            for entry in info.get("entries", []):
                if entry:
                    entries.extend(JobManager._flatten_entries(entry))
            return entries
        return [info]

    @staticmethod
    def serialize(job: DownloadJob) -> JobResponse:
        return JobResponse(
            id=job.id,
            user_id=job.user_id,
            source_url=job.source_url,
            status=job.status,
            queue_position=job.queue_position,
            title=(job.preview_snapshot or {}).get("title"),
            extractor=(job.preview_snapshot or {}).get("extractor") or (job.preview_snapshot or {}).get("extractor_key"),
            format_selection=job.format_selection,
            format_resolution=JobManager._format_resolution_state(job.preview_snapshot),
            output_profile=job.output_profile,
            preview_snapshot=job.preview_snapshot,
            error=job.error,
            attempts=job.attempts or [],
            outputs=[
                JobOutput(library_item_id=applied.get("library_item_id"), root_label=applied.get("root_label"), folder=applied.get("folder") or "")
                for applied in (job.routing or {}).get("published") or []
            ],
            acquisition_batch_id=job.acquisition_batch_id,
            acquisition_entry_id=job.acquisition_entry_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    @staticmethod
    def _format_resolution_state(snapshot: dict[str, Any] | None) -> FormatResolutionState | None:
        value = (snapshot or {}).get("format_resolution")
        if value is None:
            return None
        try:
            return FormatResolutionState.model_validate(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _event_job_payload(job: DownloadJob, **extra: Any) -> dict[str, Any]:
        payload = JobManager.serialize(job).model_dump(mode="json")
        payload.pop("artwork_url", None)
        preview = job.preview_snapshot or {}
        if preview:
            payload.setdefault("title", preview.get("title"))
            payload.setdefault("extractor", preview.get("extractor") or preview.get("extractor_key"))
        payload.update(extra)
        payload["id"] = job.id
        return payload

    @staticmethod
    def _job_event_payload(job: DownloadJob, **extra: Any) -> dict[str, Any]:
        return {
            "user_id": job.user_id,
            "job": JobManager._event_job_payload(job, **extra),
        }
