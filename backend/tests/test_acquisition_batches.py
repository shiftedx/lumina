from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from typing import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import AcquisitionBatchEntry, AcquisitionJobOutput, DownloadJob, LibraryItem
from app.services.acquisition_batch import (
    AcquisitionBatchService,
    DispatchReceipt,
    InvalidJobOutputError,
    RedactedDispatchFailure,
    SelectedSourceEntry,
)
from support import memory_session_factory


class IdempotentDispatcher:
    def __init__(
        self,
        failures: dict[str, Exception] | None = None,
        persist_job: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.jobs: dict[str, str] = {}
        self.failures = failures or {}
        self.persist_job = persist_job

    def ensure_dispatched(self, *, idempotency_key: str, source_url: str, **_options: object) -> DispatchReceipt:
        self.calls.append(idempotency_key)
        failure = self.failures.get(source_url)
        if failure:
            raise failure
        job_id = self.jobs.setdefault(idempotency_key, f"job-{len(self.jobs) + 1}")
        if self.persist_job:
            self.persist_job(job_id, str(_options["user_id"]), source_url)
        return DispatchReceipt(download_job_id=job_id)

    def activate_dispatched(self, _receipt: DispatchReceipt) -> None:
        pass


def _session() -> Session:
    return memory_session_factory()()


def _dispatcher(db: Session, failures: dict[str, Exception] | None = None) -> IdempotentDispatcher:
    def persist_job(job_id: str, user_id: str, source_url: str) -> None:
        if db.get(DownloadJob, job_id) is None:
            db.add(DownloadJob(id=job_id, user_id=user_id, source_url=source_url, status="queued"))
            db.commit()

    return IdempotentDispatcher(failures, persist_job)


def _selected(remote_id: str = "one") -> list[SelectedSourceEntry]:
    return [SelectedSourceEntry(f"https://example.test/{remote_id}", "example", remote_id, remote_id.title())]


def test_batch_persists_redacted_partial_dispatch_outcomes_and_job_associations() -> None:
    db = _session()
    dispatcher = _dispatcher(db, {
        "https://example.test/safe": RedactedDispatchFailure("source_unavailable", "The source is unavailable."),
        "https://example.test/raw": RuntimeError("secret-token-must-not-be-persisted"),
    })
    batch = AcquisitionBatchService(db, dispatcher).queue_selected(
        user_id="member-a",
        source_url="https://example.test/playlist",
        source_provenance={"extractor": "example", "playlist_id": "p1"},
        entries=[*_selected(), *_selected("safe"), *_selected("raw")],
    )

    assert batch.status == "partial"
    entries = list(db.scalars(select(AcquisitionBatchEntry).order_by(AcquisitionBatchEntry.selection_index)))
    assert [entry.status for entry in entries] == ["queued", "failed", "dispatching"]
    assert (entries[1].failure_category, entries[1].error) == ("source_unavailable", "The source is unavailable.")
    assert (entries[2].failure_category, entries[2].error) == ("dispatch_uncertain", "Dispatch outcome is being reconciled.")
    assert "secret-token" not in " ".join(entry.error or "" for entry in entries)
    assert db.scalar(select(AcquisitionJobOutput)) is not None


def test_resume_after_external_creation_reuses_entry_idempotency_key_without_a_second_job() -> None:
    db = _session()
    dispatcher = _dispatcher(db)
    batch = AcquisitionBatchService(db, dispatcher).stage_selected(user_id="member-a", source_url="source", entries=_selected())

    class CrashBeforeLink(AcquisitionBatchService):
        def _persist_dispatch_receipt(self, entry: AcquisitionBatchEntry, receipt: DispatchReceipt) -> None:
            raise SystemExit("simulated process interruption")

    with pytest.raises(SystemExit, match="simulated process interruption"):
        CrashBeforeLink(db, dispatcher).resume_batch(user_id="member-a", batch_id=batch.id)

    db.expire_all()
    entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
    assert entry is not None and entry.status == "dispatching"
    assert db.scalar(select(AcquisitionJobOutput)) is None

    resumed = AcquisitionBatchService(db, dispatcher).resume_batch(user_id="member-a", batch_id=batch.id)
    assert resumed.queued_count == 1
    assert len(dispatcher.jobs) == 1
    assert dispatcher.calls == [entry.id, entry.id]


def test_post_link_activation_failure_remains_reconcilable_and_resumes() -> None:
    db = _session()

    def persist_staged_job(job_id: str, user_id: str, source_url: str) -> None:
        if db.get(DownloadJob, job_id) is None:
            db.add(DownloadJob(id=job_id, user_id=user_id, source_url=source_url, status="staged"))
            db.commit()

    class FlakyActivationDispatcher(IdempotentDispatcher):
        def __init__(self) -> None:
            super().__init__(persist_job=persist_staged_job)
            self.activations = 0

        def activate_dispatched(self, receipt: DispatchReceipt) -> None:
            self.activations += 1
            if self.activations == 1:
                raise RuntimeError("simulated activation lock")
            job = db.get(DownloadJob, receipt.download_job_id)
            job.status = "queued"
            db.commit()

    dispatcher = FlakyActivationDispatcher()
    batch = AcquisitionBatchService(db, dispatcher).queue_selected(
        user_id="member-a", source_url="source", entries=_selected(),
    )
    entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
    output = db.scalar(select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == entry.id))
    assert output is not None
    assert (entry.status, entry.failure_category, entry.error) == (
        "dispatching", "dispatch_reconcile", "Dispatch activation is being reconciled.",
    )
    assert db.get(DownloadJob, output.download_job_id).status == "staged"

    resumed = AcquisitionBatchService(db, dispatcher).resume_batch(user_id="member-a", batch_id=batch.id)
    db.refresh(entry)
    assert resumed.status == "queued"
    assert entry.status == "queued"
    assert db.get(DownloadJob, output.download_job_id).status == "queued"
    assert len(dispatcher.jobs) == 1


def test_database_reservation_prevents_two_sessions_from_dispatching_same_member_source(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions() as db:
        first = AcquisitionBatchService(db, _dispatcher(db)).stage_selected(user_id="member-a", source_url="source", entries=_selected())
        second = AcquisitionBatchService(db, _dispatcher(db)).stage_selected(user_id="member-a", source_url="source", entries=_selected())

    entered_dispatch = Event()
    release_dispatch = Event()
    def persist_race_job(job_id: str, user_id: str, source_url: str) -> None:
        with sessions() as job_db:
            if job_db.get(DownloadJob, job_id) is None:
                job_db.add(DownloadJob(id=job_id, user_id=user_id, source_url=source_url, status="queued"))
                job_db.commit()

    dispatcher = IdempotentDispatcher(persist_job=persist_race_job)
    original_dispatch = dispatcher.ensure_dispatched

    def blocking_dispatch(**kwargs: object) -> DispatchReceipt:
        entered_dispatch.set()
        assert release_dispatch.wait(5)
        return original_dispatch(**kwargs)  # type: ignore[arg-type]

    dispatcher.ensure_dispatched = blocking_dispatch  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def resume(batch_id: str) -> None:
        try:
            with sessions() as db:
                AcquisitionBatchService(db, dispatcher).resume_batch(user_id="member-a", batch_id=batch_id)
        except BaseException as error:
            errors.append(error)

    first_thread = Thread(target=resume, args=(first.id,))
    first_thread.start()
    assert entered_dispatch.wait(5)
    second_thread = Thread(target=resume, args=(second.id,))
    second_thread.start()
    second_thread.join(5)
    release_dispatch.set()
    first_thread.join(5)
    assert not errors
    assert len(dispatcher.jobs) == 1
    with sessions() as db:
        statuses = list(db.scalars(select(AcquisitionBatchEntry.status).order_by(AcquisitionBatchEntry.batch_id)))
        assert sorted(statuses) == ["duplicate", "queued"]


def test_failed_and_cancelled_entries_release_reservation_for_explicit_retry() -> None:
    db = _session()
    dispatcher = _dispatcher(db, {"https://example.test/one": RedactedDispatchFailure("temporary", "Try again later.")})
    failed_batch = AcquisitionBatchService(db, dispatcher).queue_selected(user_id="member-a", source_url="source", entries=_selected())
    failed_entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == failed_batch.id))
    assert failed_entry is not None and failed_entry.status == "failed"

    dispatcher.failures.clear()
    retried = AcquisitionBatchService(db, dispatcher).retry_entry(user_id="member-a", entry_id=failed_entry.id)
    assert retried.queued_count == 1

    retried_entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == retried.id))
    assert retried_entry is not None
    output = db.scalar(select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == retried_entry.id))
    assert output is not None
    AcquisitionBatchService(db, dispatcher).record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="cancelled")
    cancelled_entry = db.get(AcquisitionBatchEntry, output.batch_entry_id)
    assert (cancelled_entry.failure_category, cancelled_entry.error) == (
        "job_cancelled", "The download job was cancelled.",
    )
    retry_batch = AcquisitionBatchService(db, dispatcher).queue_selected(user_id="member-a", source_url="source", entries=_selected())
    assert retry_batch.queued_count == 1


def test_job_outcomes_are_monotonic_terminal_and_validate_output_association() -> None:
    db = _session()
    dispatcher = _dispatcher(db)
    batch = AcquisitionBatchService(db, dispatcher).queue_selected(user_id="member-a", source_url="source", entries=_selected())
    output = db.scalar(select(AcquisitionJobOutput))
    assert output is not None
    db.add_all([
        LibraryItem(id="owned", user_id="member-a", visibility="private", extractor="example", remote_id="one", title="Owned"),
        LibraryItem(id="foreign", user_id="member-b", visibility="shared", extractor="example", remote_id="one", title="Foreign"),
        LibraryItem(id="wrong-source", user_id="member-a", visibility="private", extractor="example", remote_id="two", title="Wrong"),
    ])
    db.commit()
    service = AcquisitionBatchService(db, dispatcher)
    service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="running", progress=70)
    service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="running", progress=20)
    entry = db.get(AcquisitionBatchEntry, output.batch_entry_id)
    assert entry is not None and entry.progress == 70

    with pytest.raises(InvalidJobOutputError):
        service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="completed", library_item_id="foreign")
    with pytest.raises(InvalidJobOutputError):
        service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="completed", library_item_id="wrong-source")

    completed = service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="completed", progress=100, library_item_id="owned")
    assert db.get(AcquisitionJobOutput, output.id).library_item_id == "owned"
    finished_at = db.get(AcquisitionBatchEntry, output.batch_entry_id).finished_at
    service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="running", progress=10)
    service.record_job_outcome(user_id="member-a", download_job_id=output.download_job_id, status="failed", progress=100)
    entry = db.get(AcquisitionBatchEntry, output.batch_entry_id)
    assert entry is not None
    assert (entry.status, entry.progress, entry.finished_at) == ("completed", 100, finished_at)
    assert completed.status == "completed"


def test_dedup_is_member_scoped() -> None:
    db = _session()
    dispatcher = _dispatcher(db)
    service = AcquisitionBatchService(db, dispatcher)
    first = service.queue_selected(user_id="member-a", source_url="source", entries=_selected())
    duplicate = service.queue_selected(user_id="member-a", source_url="source", entries=_selected())
    other_member = service.queue_selected(user_id="member-b", source_url="source", entries=_selected())
    assert (first.queued_count, duplicate.duplicate_count, other_member.queued_count) == (1, 1, 1)


@pytest.mark.parametrize("job_problem", ["missing", "foreign", "wrong-source"])
def test_invalid_dispatch_receipt_stays_reserved_for_safe_reconciliation(job_problem: str) -> None:
    db = _session()

    def persist_invalid_job(job_id: str, _user_id: str, _source_url: str) -> None:
        if job_problem == "missing":
            return
        db.add(DownloadJob(
            id=job_id,
            user_id="member-b" if job_problem == "foreign" else "member-a",
            source_url="https://example.test/other" if job_problem == "wrong-source" else "https://example.test/one",
            status="queued",
        ))
        db.commit()

    dispatcher = IdempotentDispatcher(persist_job=persist_invalid_job)
    batch = AcquisitionBatchService(db, dispatcher).queue_selected(
        user_id="member-a", source_url="source", entries=_selected(),
    )
    entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
    assert entry is not None
    assert (entry.status, entry.failure_category, entry.error) == (
        "dispatching", "dispatch_reconcile", "Dispatch receipt is being reconciled.",
    )
    assert db.scalar(select(AcquisitionJobOutput)) is None


@pytest.mark.parametrize("job_problem", ["missing", "foreign", "wrong-source"])
def test_invalid_terminal_outcome_is_rejected_without_mutating_entry(job_problem: str) -> None:
    db = _session()
    dispatcher = _dispatcher(db)
    batch = AcquisitionBatchService(db, dispatcher).queue_selected(
        user_id="member-a", source_url="source", entries=_selected(),
    )
    output = db.scalar(select(AcquisitionJobOutput))
    assert output is not None
    job = db.get(DownloadJob, output.download_job_id)
    assert job is not None
    if job_problem == "missing":
        db.delete(job)
    elif job_problem == "foreign":
        job.user_id = "member-b"
    else:
        job.source_url = "https://example.test/other"
    db.commit()

    with pytest.raises(InvalidJobOutputError, match="does not match"):
        AcquisitionBatchService(db, dispatcher).record_job_outcome(
            user_id="member-a", download_job_id=output.download_job_id, status="completed", progress=100,
        )

    entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
    assert entry is not None and (entry.status, entry.progress, entry.finished_at) == ("queued", 0, None)
