from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.persistence import write_transaction
from app.models import AcquisitionBatchEntry, AcquisitionJobOutput, DownloadJob, LibraryItem
from app.services.acquisition_batch import (
    AcquisitionBatchService,
    DispatchReceipt,
    RedactedDispatchFailure,
    RedactedFailure,
    TERMINAL_STATUSES,
)

if TYPE_CHECKING:
    from app.services.job_manager import JobManager


# Running-progress observations are the acquisition write-amplification hot spot:
# yt-dlp emits a callback per fragment, and each one would otherwise open a short
# write transaction. Terminal and activation observes are never coalesced.
PROGRESS_MIN_DELTA = 5
PROGRESS_MIN_INTERVAL_SECONDS = 2.0
_COALESCED_STATUS = "running"


class _ProgressCoalescer:
    """Per-entry tracker that admits a running-progress write only when it matters.

    A running observation reaches the database when its progress has moved at
    least ``PROGRESS_MIN_DELTA`` points from the last durable write for that
    entry, or at least ``PROGRESS_MIN_INTERVAL_SECONDS`` have elapsed since it.
    Everything else is dropped at the adapter, so dropped values are never
    buffered and can never overwrite a later terminal write.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._last_progress: dict[str, int] = {}
        self._last_write: dict[str, float] = {}

    def should_persist(self, job_id: str, progress: int, now: float) -> bool:
        with self._lock:
            last_progress = self._last_progress.get(job_id)
            last_write = self._last_write.get(job_id)
            return (
                last_progress is None
                or last_write is None
                or abs(progress - last_progress) >= PROGRESS_MIN_DELTA
                or now - last_write >= PROGRESS_MIN_INTERVAL_SECONDS
            )

    def record(self, job_id: str, progress: int, now: float) -> None:
        with self._lock:
            self._last_progress[job_id] = progress
            self._last_write[job_id] = now

    def clear(self, job_id: str) -> None:
        with self._lock:
            self._last_progress.pop(job_id, None)
            self._last_write.pop(job_id, None)


class JobManagerAcquisitionAdapter:
    """Deterministic staging, activation, recovery, and lifecycle bridge."""

    def __init__(self, manager: JobManager, session_factory: Callable[[], Session]) -> None:
        self.manager = manager
        self.session_factory = session_factory
        self._reconciling = False
        self._owned_job_ids: set[str] = set()
        self._owned_lock = Lock()
        self._progress = _ProgressCoalescer()
        self._clock: Callable[[], float] = time.monotonic

    def owns(self, job_id: str) -> bool:
        with self._owned_lock:
            return job_id in self._owned_job_ids

    @staticmethod
    def is_linked(db: Session, job_id: str) -> bool:
        return db.scalar(
            select(AcquisitionJobOutput.id).where(AcquisitionJobOutput.download_job_id == job_id).limit(1)
        ) is not None

    def _remember(self, job_id: str) -> None:
        with self._owned_lock:
            self._owned_job_ids.add(job_id)

    def ensure_dispatched(
        self,
        *,
        idempotency_key: str,
        batch_id: str,
        batch_entry_id: str,
        user_id: str,
        source_url: str,
        format_selection: dict,
        output_profile: dict,
        preview_snapshot: dict,
    ) -> DispatchReceipt:
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lumina:acquisition:{idempotency_key}"))
        with self.session_factory() as db:
            job = db.get(DownloadJob, job_id)
            if job is None:
                job = DownloadJob(
                    id=job_id,
                    user_id=user_id,
                    source_url=source_url,
                    status="staged",
                    format_selection=format_selection,
                    output_profile=output_profile,
                    preview_snapshot=self.manager._compact_preview_snapshot(preview_snapshot),
                    error=None,
                    acquisition_batch_id=batch_id,
                    acquisition_entry_id=batch_entry_id,
                )
                with write_transaction(db, name="acquisition_job_stage"):
                    db.add(job)
            elif job.user_id != user_id or job.source_url != source_url:
                raise RedactedDispatchFailure("idempotency_conflict", "The acquisition job identity is unavailable.")
            return DispatchReceipt(download_job_id=job.id)

    def activate_dispatched(self, receipt: DispatchReceipt) -> None:
        with self.session_factory() as db:
            job = db.get(DownloadJob, receipt.download_job_id)
            output = db.scalar(
                select(AcquisitionJobOutput).where(AcquisitionJobOutput.download_job_id == receipt.download_job_id)
            )
            if job is None or output is None or output.user_id != job.user_id:
                return
            self._remember(job.id)
            with write_transaction(db, name="acquisition_job_claim"):
                claimed = db.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job.id, DownloadJob.status == "staged")
                    .values(status="queued", queue_position=self.manager._queue.qsize() + 1)
                )
            db.refresh(job)
            if claimed.rowcount != 1 and job.status != "queued":
                return
            if self._reconciling:
                return
            self.manager.dispatch_staged(job)
            self.observe(job.id, "queued")

    def reconcile_startup(self) -> None:
        self._reconciling = True
        try:
            with self.session_factory() as db:
                linked_ids = list(db.scalars(select(AcquisitionJobOutput.download_job_id)))
                for job_id in linked_ids:
                    self._remember(job_id)
                dispatching = list(
                    db.scalars(
                        select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.status == "dispatching")
                    )
                )
                batch_keys = {(entry.user_id, entry.batch_id) for entry in dispatching}
            for user_id, batch_id in batch_keys:
                with self.session_factory() as db:
                    AcquisitionBatchService(db, self).resume_batch(user_id=user_id, batch_id=batch_id)
            with self.session_factory() as db:
                staged_ids = list(
                    db.scalars(
                        select(DownloadJob.id)
                        .join(AcquisitionJobOutput, AcquisitionJobOutput.download_job_id == DownloadJob.id)
                        .where(DownloadJob.status == "staged")
                    )
                )
            for job_id in staged_ids:
                try:
                    self.activate_dispatched(DispatchReceipt(job_id))
                except Exception:
                    # The committed link and staged job are durable. A later
                    # reconciliation pass can safely claim the same job.
                    continue
        finally:
            self._reconciling = False
        self._reconcile_linked_outcomes()

    def _reconcile_linked_outcomes(self) -> None:
        with self.session_factory() as db:
            linked = list(
                db.execute(
                    select(AcquisitionJobOutput.download_job_id, DownloadJob.status)
                    .join(DownloadJob, DownloadJob.id == AcquisitionJobOutput.download_job_id)
                    .where(DownloadJob.status.in_(("queued", "running", "postprocessing", "completed", "failed", "cancelled", "interrupted")))
                )
            )
        for job_id, status in linked:
            if status == "completed":
                library_item_id = self._completed_library_item_id(job_id)
                self.observe(job_id, "completed", progress=100, library_item_id=library_item_id)
            elif status in {"failed", "cancelled", "interrupted"}:
                self.observe(
                    job_id,
                    "failed" if status == "interrupted" else status,
                    failure=RedactedFailure(
                        f"job_{status}",
                        "The download job failed." if status == "failed" else "The download job was cancelled.",
                    ),
                )
            else:
                self.observe(job_id, "running" if status in {"running", "postprocessing"} else "queued")

    def _completed_library_item_id(self, job_id: str) -> str | None:
        with self.session_factory() as db:
            entry = db.scalar(
                select(AcquisitionBatchEntry)
                .join(AcquisitionJobOutput, AcquisitionJobOutput.batch_entry_id == AcquisitionBatchEntry.id)
                .where(AcquisitionJobOutput.download_job_id == job_id)
            )
            if entry is None:
                return None
            query = select(LibraryItem.id).where(LibraryItem.user_id == entry.user_id)
            if entry.extractor:
                query = query.where(LibraryItem.extractor == entry.extractor)
            if entry.remote_id:
                query = query.where(LibraryItem.remote_id == entry.remote_id)
            else:
                query = query.where(LibraryItem.webpage_url == entry.source_url)
            return db.scalar(query.limit(1))

    def observe(
        self,
        job_id: str,
        status: str,
        *,
        progress: int | None = None,
        library_item_id: str | None = None,
        failure: RedactedFailure | None = None,
    ) -> bool:
        if not self.owns(job_id):
            return False
        coalesced = status == _COALESCED_STATUS and progress is not None
        now = self._clock()
        if coalesced and not self._progress.should_persist(job_id, progress, now):
            # Dropped at the adapter: the SSE stream still carries this update,
            # but the durable running progress is repaired on startup if lost.
            return True
        with self.session_factory() as db:
            output = db.scalar(
                select(AcquisitionJobOutput).where(AcquisitionJobOutput.download_job_id == job_id)
            )
            if output is None:
                return False
            AcquisitionBatchService(db, self).record_job_outcome(
                user_id=output.user_id,
                download_job_id=job_id,
                status=status,
                progress=progress,
                library_item_id=library_item_id,
                failure=failure,
            )
            # A late running observe after a terminal one must not repopulate the
            # cleared tracker (the entry would leak until restart).
            terminal = db.get(AcquisitionBatchEntry, output.batch_entry_id).status in TERMINAL_STATUSES
        if coalesced and not terminal:
            self._progress.record(job_id, progress, now)
        else:
            # Terminal and activation observes flush the tracker so a later stale
            # running observe cannot be dropped against a cleared entry.
            self._progress.clear(job_id)
        return True

    def observe_in_session(
        self,
        db: Session,
        job: DownloadJob,
        status: str,
        *,
        progress: int | None = None,
        library_item: LibraryItem | None = None,
        failure: RedactedFailure | None = None,
    ) -> bool:
        if not self.owns(job.id):
            return False
        output = db.scalar(
            select(AcquisitionJobOutput).where(AcquisitionJobOutput.download_job_id == job.id)
        )
        if output is None:
            return False
        AcquisitionBatchService(db, self).record_job_outcome(
            user_id=output.user_id,
            download_job_id=job.id,
            status=status,
            progress=progress,
            library_item_id=library_item.id if library_item else None,
            failure=failure,
        )
        # In-session observes carry terminal outcomes (e.g. cancellation); flush
        # the coalescer so no stale running progress lingers for this entry.
        self._progress.clear(job.id)
        return True
