from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence import write_transaction
from app.models import (
    utcnow,
    AcquisitionBatch,
    AcquisitionBatchEntry,
    AcquisitionJobOutput,
    AcquisitionSourceReservation,
    DownloadJob,
    LibraryItem,
)


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "duplicate"}
FAILURE_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class SelectedSourceEntry:
    source_url: str
    extractor: str | None = None
    remote_id: str | None = None
    title: str | None = None
    details: dict | None = None


@dataclass(frozen=True)
class DispatchReceipt:
    download_job_id: str


@dataclass(frozen=True)
class RedactedFailure:
    category: str
    message: str

    def __post_init__(self) -> None:
        if not FAILURE_CATEGORY.fullmatch(self.category):
            raise ValueError("Failure category must be a stable lowercase identifier.")
        clean_message = " ".join(self.message.split())
        if not clean_message or len(clean_message) > 500:
            raise ValueError("Redacted failure message must contain 1-500 characters.")
        object.__setattr__(self, "message", clean_message)


class RedactedDispatchFailure(Exception):
    """A definite dispatcher rejection whose fields are explicitly safe to persist."""

    def __init__(self, category: str, message: str) -> None:
        self.failure = RedactedFailure(category, message)
        super().__init__(self.failure.message)


class InvalidJobOutputError(ValueError):
    pass


class JobDispatcher(Protocol):
    """Create or recover exactly one job for an idempotency key.

    Implementations must return the same job for repeated keys. A
    RedactedDispatchFailure means no job was created; every other exception is
    treated as uncertain and reconciled by calling this method again.
    """

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
    ) -> DispatchReceipt: ...

    def activate_dispatched(self, receipt: DispatchReceipt) -> None: ...


class AcquisitionBatchService:
    """Durable acquisition outbox with member/source reservation semantics."""

    def __init__(self, db: Session, dispatcher: JobDispatcher) -> None:
        self.db = db
        self.dispatcher = dispatcher

    def stage_selected(
        self,
        *,
        user_id: str,
        source_url: str,
        entries: list[SelectedSourceEntry],
        source_title: str | None = None,
        source_provenance: dict | None = None,
        format_selection: dict | None = None,
        output_profile: dict | None = None,
    ) -> AcquisitionBatch:
        if not entries:
            raise ValueError("At least one source entry must be selected.")
        batch = AcquisitionBatch(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source_url=source_url,
            source_title=source_title,
            source_provenance=source_provenance or {},
            selected_count=len(entries),
            format_selection=format_selection or {},
            output_profile=output_profile or {},
        )
        rows = [
            AcquisitionBatchEntry(
                id=str(uuid.uuid4()),
                batch_id=batch.id,
                user_id=user_id,
                selection_index=index,
                source_url=item.source_url,
                extractor=item.extractor,
                remote_id=item.remote_id,
                source_identity=self._identity(item),
                title=item.title,
                details_json=item.details or {},
            )
            for index, item in enumerate(entries)
        ]
        with write_transaction(self.db, name="acquisition_stage"):
            self.db.add_all([batch, *rows])
        return batch

    def queue_selected(self, **options: object) -> AcquisitionBatch:
        batch = self.stage_selected(**options)  # type: ignore[arg-type]
        return self.resume_batch(user_id=batch.user_id, batch_id=batch.id)

    def list_for_user(self, user_id: str, *, limit: int | None = None) -> list[AcquisitionBatch]:
        return list(
            self.db.scalars(
                select(AcquisitionBatch)
                .where(AcquisitionBatch.user_id == user_id)
                .order_by(AcquisitionBatch.created_at.desc(), AcquisitionBatch.id.desc())
                .limit(limit)
            )
        )

    def get_for_user(self, *, user_id: str, batch_id: str) -> AcquisitionBatch:
        batch = self.db.get(AcquisitionBatch, batch_id)
        if batch is None or batch.user_id != user_id:
            raise LookupError(batch_id)
        return batch

    def resume_batch(self, *, user_id: str, batch_id: str) -> AcquisitionBatch:
        batch = self.db.get(AcquisitionBatch, batch_id)
        if batch is None or batch.user_id != user_id:
            raise LookupError(batch_id)
        entries = list(
            self.db.scalars(
                select(AcquisitionBatchEntry)
                .where(AcquisitionBatchEntry.batch_id == batch.id)
                .order_by(AcquisitionBatchEntry.selection_index)
            )
        )
        for entry in entries:
            if entry.status not in {"pending", "dispatching"}:
                continue
            reservation = self._reserve(entry)
            if reservation is None:
                continue
            try:
                receipt = self.dispatcher.ensure_dispatched(
                    idempotency_key=reservation.idempotency_key,
                    batch_id=batch.id,
                    batch_entry_id=entry.id,
                    user_id=user_id,
                    source_url=entry.source_url,
                    format_selection=batch.format_selection,
                    output_profile=batch.output_profile,
                    preview_snapshot={
                        "title": entry.title,
                        "extractor": entry.extractor,
                        "remote_id": entry.remote_id,
                        **entry.details_json,
                    },
                )
            except RedactedDispatchFailure as error:
                self._mark_definite_dispatch_failure(entry, error.failure)
                continue
            except Exception:
                with write_transaction(self.db, name="acquisition_resume"):
                    entry.failure_category = "dispatch_uncertain"
                    entry.error = "Dispatch outcome is being reconciled."
                continue
            self._persist_dispatch_receipt(entry, receipt)
        with write_transaction(self.db, name="acquisition_resume"):
            self._recompute(batch)
        return batch

    def retry_entry(self, *, user_id: str, entry_id: str) -> AcquisitionBatch:
        entry = self.db.get(AcquisitionBatchEntry, entry_id)
        if entry is None or entry.user_id != user_id:
            raise LookupError(entry_id)
        if entry.status not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled entries can be retried.")
        prior_batch = self.db.get(AcquisitionBatch, entry.batch_id)
        if prior_batch is None:
            raise LookupError(entry.batch_id)
        return self.queue_selected(
            user_id=user_id,
            source_url=prior_batch.source_url,
            source_title=prior_batch.source_title,
            source_provenance=prior_batch.source_provenance,
            format_selection=prior_batch.format_selection,
            output_profile=prior_batch.output_profile,
            entries=[SelectedSourceEntry(entry.source_url, entry.extractor, entry.remote_id, entry.title, entry.details_json)],
        )

    def record_job_outcome(
        self,
        *,
        user_id: str,
        download_job_id: str,
        status: str,
        progress: int | None = None,
        library_item_id: str | None = None,
        failure: RedactedFailure | None = None,
    ) -> AcquisitionBatch:
        if status not in {"queued", "running", "completed", "failed", "cancelled"}:
            raise ValueError("Unsupported acquisition outcome.")
        output = self.db.scalar(
            select(AcquisitionJobOutput).where(
                AcquisitionJobOutput.user_id == user_id,
                AcquisitionJobOutput.download_job_id == download_job_id,
            )
        )
        if output is None:
            raise LookupError(download_job_id)
        entry = self.db.get(AcquisitionBatchEntry, output.batch_entry_id)
        if entry is None or entry.user_id != user_id:
            raise LookupError(output.batch_entry_id)
        batch = self.db.get(AcquisitionBatch, entry.batch_id)
        if batch is None or batch.user_id != user_id:
            raise LookupError(entry.batch_id)
        self._validate_download_job(entry, output.download_job_id, user_id)
        if library_item_id is not None:
            self._validate_job_output(entry, output, user_id, status, library_item_id)

        if entry.status in TERMINAL_STATUSES:
            if entry.status == "completed" and library_item_id is not None and output.library_item_id is None:
                with write_transaction(self.db, name="acquisition_outcome"):
                    output.library_item_id = library_item_id
            return batch

        with write_transaction(self.db, name="acquisition_outcome"):
            requested_progress = max(0, min(100, progress if progress is not None else 0))
            entry.progress = max(entry.progress, requested_progress)
            if library_item_id is not None:
                output.library_item_id = library_item_id
            if status in TERMINAL_STATUSES:
                entry.status = status
                entry.progress = 100
                entry.finished_at = entry.finished_at or utcnow()
                persisted_failure = failure
                if persisted_failure is None and status == "failed":
                    persisted_failure = RedactedFailure("job_failed", "The download job failed.")
                elif persisted_failure is None and status == "cancelled":
                    persisted_failure = RedactedFailure("job_cancelled", "The download job was cancelled.")
                if persisted_failure is not None:
                    entry.failure_category, entry.error = persisted_failure.category, persisted_failure.message
                reservation = self._reservation_for_entry(entry.id)
                if reservation is not None:
                    if status in {"failed", "cancelled"}:
                        self.db.delete(reservation)
                    else:
                        reservation.state = "completed"
            elif status == "running" or entry.status not in {"running"}:
                entry.status = status
            self._recompute(batch)
        return batch

    def _reserve(self, entry: AcquisitionBatchEntry) -> AcquisitionSourceReservation | None:
        existing = self._reservation_for_entry(entry.id)
        if existing is not None:
            with write_transaction(self.db, name="acquisition_reserve"):
                entry.status = "dispatching"
                entry.dispatch_attempts += 1
            return existing
        if self._previously_queued_without_reservation(entry):
            self._mark_duplicate(entry)
            return None
        reservation = AcquisitionSourceReservation(
            id=str(uuid.uuid4()),
            user_id=entry.user_id,
            source_identity=entry.source_identity,
            batch_entry_id=entry.id,
            idempotency_key=entry.id,
        )
        try:
            with write_transaction(self.db, name="acquisition_reserve"):
                entry.status = "dispatching"
                entry.dispatch_attempts += 1
                self.db.add(reservation)
        except IntegrityError:
            entry = self.db.get(AcquisitionBatchEntry, entry.id)
            if entry is None:
                raise
            self._mark_duplicate(entry)
            return None
        return reservation

    def _persist_dispatch_receipt(self, entry: AcquisitionBatchEntry, receipt: DispatchReceipt) -> None:
        try:
            self._validate_download_job(entry, receipt.download_job_id, entry.user_id)
        except InvalidJobOutputError:
            with write_transaction(self.db, name="acquisition_receipt"):
                entry.status = "dispatching"
                entry.failure_category = "dispatch_reconcile"
                entry.error = "Dispatch receipt is being reconciled."
            return
        output = self.db.scalar(
            select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == entry.id)
        )
        with write_transaction(self.db, name="acquisition_receipt"):
            if output is None:
                output = AcquisitionJobOutput(
                    id=str(uuid.uuid4()),
                    batch_entry_id=entry.id,
                    user_id=entry.user_id,
                    download_job_id=receipt.download_job_id,
                )
                self.db.add(output)
            elif output.download_job_id != receipt.download_job_id:
                raise RuntimeError("Dispatcher violated its idempotency-key contract.")
            entry.status = "queued"
            entry.failure_category = None
            entry.error = None
            reservation = self._reservation_for_entry(entry.id)
            if reservation is not None:
                reservation.state = "dispatched"
        try:
            self.dispatcher.activate_dispatched(receipt)
        except Exception:
            with write_transaction(self.db, name="acquisition_receipt"):
                entry.status = "dispatching"
                entry.failure_category = "dispatch_reconcile"
                entry.error = "Dispatch activation is being reconciled."

    def _mark_definite_dispatch_failure(self, entry: AcquisitionBatchEntry, failure: RedactedFailure) -> None:
        with write_transaction(self.db, name="acquisition_dispatch_failed"):
            entry.status = "failed"
            entry.progress = 100
            entry.failure_category = failure.category
            entry.error = failure.message
            entry.finished_at = utcnow()
            reservation = self._reservation_for_entry(entry.id)
            if reservation is not None:
                self.db.delete(reservation)

    def _mark_duplicate(self, entry: AcquisitionBatchEntry) -> None:
        with write_transaction(self.db, name="acquisition_duplicate"):
            entry.status = "duplicate"
            entry.progress = 100
            entry.finished_at = utcnow()

    def _reservation_for_entry(self, entry_id: str) -> AcquisitionSourceReservation | None:
        return self.db.scalar(
            select(AcquisitionSourceReservation).where(AcquisitionSourceReservation.batch_entry_id == entry_id)
        )

    def _previously_queued_without_reservation(self, entry: AcquisitionBatchEntry) -> bool:
        prior = self.db.scalar(
            select(AcquisitionBatchEntry.id)
            .where(
                AcquisitionBatchEntry.user_id == entry.user_id,
                AcquisitionBatchEntry.source_identity == entry.source_identity,
                AcquisitionBatchEntry.batch_id != entry.batch_id,
                AcquisitionBatchEntry.status.in_(("queued", "running", "completed")),
            )
            .limit(1)
        )
        if prior is None:
            return False
        reservation = self.db.scalar(
            select(AcquisitionSourceReservation.id).where(
                AcquisitionSourceReservation.user_id == entry.user_id,
                AcquisitionSourceReservation.source_identity == entry.source_identity,
            )
        )
        return reservation is None

    def _validate_job_output(
        self,
        entry: AcquisitionBatchEntry,
        output: AcquisitionJobOutput,
        user_id: str,
        status: str,
        library_item_id: str,
    ) -> None:
        if status != "completed":
            raise InvalidJobOutputError("A Library item can only be linked to a completed job.")
        self._validate_download_job(entry, output.download_job_id, user_id)
        item = self.db.get(LibraryItem, library_item_id)
        if item is None or item.user_id != user_id:
            raise InvalidJobOutputError("Library item is not owned by this household member.")
        if entry.extractor and item.extractor and entry.extractor.casefold() != item.extractor.casefold():
            raise InvalidJobOutputError("Library item does not match the acquired source.")
        if entry.remote_id and item.remote_id and entry.remote_id != item.remote_id:
            raise InvalidJobOutputError("Library item does not match the acquired source.")

    def _validate_download_job(self, entry: AcquisitionBatchEntry, download_job_id: str, user_id: str) -> DownloadJob:
        job = self.db.get(DownloadJob, download_job_id)
        if job is None or job.user_id != user_id or job.source_url != entry.source_url:
            raise InvalidJobOutputError("Download job does not match this acquisition entry.")
        return job

    def _recompute(self, batch: AcquisitionBatch) -> None:
        entries = list(
            self.db.scalars(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
        )
        batch.queued_count = sum(row.status in {"queued", "running"} for row in entries)
        batch.duplicate_count = sum(row.status == "duplicate" for row in entries)
        batch.completed_count = sum(row.status == "completed" for row in entries)
        batch.failed_count = sum(row.status in {"failed", "cancelled"} for row in entries)
        batch.progress = round(sum(row.progress for row in entries) / len(entries)) if entries else 0
        terminal = sum(row.status in TERMINAL_STATUSES for row in entries)
        if terminal == len(entries):
            batch.finished_at = batch.finished_at or utcnow()
            if batch.failed_count and batch.completed_count + batch.duplicate_count:
                batch.status = "partial"
            elif batch.failed_count:
                batch.status = "failed"
            elif batch.completed_count:
                batch.status = "completed"
            else:
                batch.status = "duplicate"
        elif batch.failed_count:
            batch.status = "partial"
            batch.finished_at = None
        elif any(row.status == "dispatching" for row in entries):
            batch.status = "dispatching"
            batch.finished_at = None
        else:
            batch.status = "queued"
            batch.finished_at = None

    @staticmethod
    def _identity(entry: SelectedSourceEntry) -> str:
        if entry.extractor and entry.remote_id:
            canonical = f"{entry.extractor.casefold()}:{entry.remote_id}"
        else:
            parts = urlsplit(entry.source_url.strip())
            canonical = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, parts.query, ""))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
