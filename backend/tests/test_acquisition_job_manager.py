from __future__ import annotations

import asyncio
from threading import Event, Thread
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.events import EventBus
from app.models import AcquisitionBatchEntry, AcquisitionJobOutput, DownloadJob, LibraryItem, User
from app.services import job_manager as job_manager_module
from app.services.acquisition_batch import (
    AcquisitionBatchService,
    DispatchReceipt,
    RedactedDispatchFailure,
    SelectedSourceEntry,
)
from app.services.job_manager import JobManager
from app.services.yt_dlp_service import YtDlpService
from support import memory_session_factory


_factory = memory_session_factory


def _entry() -> list[SelectedSourceEntry]:
    return [SelectedSourceEntry("https://example.test/one", "example", "one", "One")]


def test_adapter_stages_one_deterministic_inert_job_and_activates_only_after_link_commit() -> None:
    sessions = _factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    with sessions() as db:
        batch = AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id="member-a", source_url="source", entries=_entry(),
        )
        entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
        output = db.scalar(select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == entry.id))
        job = db.get(DownloadJob, output.download_job_id)
        assert job.status == "queued"
        assert manager._queue.get_nowait() == job.id
        # The job row tells the Downloads UI to retry through its batch entry.
        serialized = JobManager.serialize(job)
        assert (serialized.acquisition_batch_id, serialized.acquisition_entry_id) == (batch.id, entry.id)

        repeated = manager.acquisition.ensure_dispatched(
            idempotency_key=entry.id, batch_id=entry.batch_id, batch_entry_id=entry.id, user_id="member-a", source_url=entry.source_url,
            format_selection={}, output_profile={}, preview_snapshot={},
        )
        assert repeated.download_job_id == job.id
        assert db.scalar(select(DownloadJob.id).where(DownloadJob.id == job.id)) == job.id
        manager.acquisition.activate_dispatched(repeated)
        manager.acquisition.activate_dispatched(repeated)
        assert manager._queue.empty()


def test_adapter_rejects_deterministic_job_collision_without_dispatching() -> None:
    sessions = _factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    receipt = manager.acquisition.ensure_dispatched(
        idempotency_key="entry", batch_id="batch", batch_entry_id="entry", user_id="member-a", source_url="https://example.test/one",
        format_selection={}, output_profile={}, preview_snapshot={},
    )
    with sessions() as db:
        job = db.get(DownloadJob, receipt.download_job_id)
        job.user_id = "member-b"
        db.commit()
    with pytest.raises(RedactedDispatchFailure, match="identity is unavailable"):
        manager.acquisition.ensure_dispatched(
            idempotency_key="entry", batch_id="batch", batch_entry_id="entry", user_id="member-a", source_url="https://example.test/one",
            format_selection={}, output_profile={}, preview_snapshot={},
        )
    assert manager._queue.empty()


def test_concurrent_activation_claims_and_enqueues_linked_job_once(tmp_path) -> None:  # noqa: ANN001
    engine = create_engine(
        f"sqlite:///{tmp_path / 'activation.db'}", future=True, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    manager.acquisition._reconciling = True
    with sessions() as db:
        AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id="member-a", source_url="source", entries=_entry(),
        )
        output = db.scalar(select(AcquisitionJobOutput))
        job = db.get(DownloadJob, output.download_job_id)
        job.status = "staged"
        db.commit()
    manager.acquisition._reconciling = False

    workers = [
        Thread(target=manager.acquisition.activate_dispatched, args=(DispatchReceipt(job.id),))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert manager._queue.qsize() == 1


def test_live_resume_schedules_job_when_first_activation_failed_after_durable_claim(monkeypatch) -> None:  # noqa: ANN001
    sessions = _factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    dispatch = manager.dispatch_staged
    failures = 0

    def fail_before_enqueue(_job: DownloadJob) -> bool:
        nonlocal failures
        failures += 1
        raise RuntimeError("simulated failure after staged-to-queued claim")

    monkeypatch.setattr(manager, "dispatch_staged", fail_before_enqueue)
    with sessions() as db:
        batch = AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id="member-a", source_url="source", entries=_entry(),
        )
        entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
        output = db.scalar(select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == entry.id))
        assert failures == 1
        assert entry.status == "dispatching"
        assert db.get(DownloadJob, output.download_job_id).status == "queued"
        assert manager._queue.empty()

        monkeypatch.setattr(manager, "dispatch_staged", dispatch)
        resumed = AcquisitionBatchService(db, manager.acquisition).resume_batch(
            user_id="member-a", batch_id=batch.id,
        )
        db.refresh(entry)
        assert resumed.status == "queued"
        assert entry.status == "queued"

    manager.acquisition.activate_dispatched(DispatchReceipt(output.download_job_id))
    assert manager._queue.qsize() == 1
    assert manager._queue.get_nowait() == output.download_job_id
    assert manager._queue.empty()


def test_live_resume_recovers_when_loop_scheduling_fails_without_leaking_reservation() -> None:
    class FailingLoop:
        def call_soon_threadsafe(self, *_args) -> None:  # noqa: ANN002
            raise RuntimeError("event loop is closed")

    sessions = _factory()
    events = Mock()
    manager = JobManager(events)
    manager.acquisition.session_factory = sessions
    manager._loop = FailingLoop()  # type: ignore[assignment]
    with sessions() as db:
        batch = AcquisitionBatchService(db, manager.acquisition).stage_selected(
            user_id="member-a", source_url="source", entries=_entry(),
        )
        entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
        expected_job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lumina:acquisition:{entry.id}"))
        cancel_flag = Event()
        cancel_flag.set()
        manager._cancel_flags[expected_job_id] = cancel_flag

        first = AcquisitionBatchService(db, manager.acquisition).resume_batch(
            user_id="member-a", batch_id=batch.id,
        )
        db.refresh(entry)
        assert first.status == "dispatching"
        assert entry.status == "dispatching"
        assert db.get(DownloadJob, expected_job_id).status == "queued"
        assert expected_job_id not in manager._scheduled_job_ids
        assert manager._queue.empty()
        events.publish.assert_not_called()
        assert manager._cancel_flags[expected_job_id] is cancel_flag
        assert cancel_flag.is_set()

        manager._loop = None
        resumed = AcquisitionBatchService(db, manager.acquisition).resume_batch(
            user_id="member-a", batch_id=batch.id,
        )
        db.refresh(entry)
        assert resumed.status == "queued"
        assert entry.status == "queued"

    manager.acquisition.activate_dispatched(DispatchReceipt(expected_job_id))
    assert manager._queue.qsize() == 1
    assert manager._queue.get_nowait() == expected_job_id
    events.publish.assert_called_once()
    assert events.publish.call_args.args[0] == "job_queued"
    assert manager._cancel_flags[expected_job_id] is cancel_flag
    assert cancel_flag.is_set()


def test_startup_reconciles_linked_staged_job_before_pending_recovery_without_duplicate_queueing() -> None:
    async def scenario() -> None:
        sessions = _factory()
        job_manager_module.SessionLocal = sessions
        manager = JobManager(EventBus())
        manager.acquisition.session_factory = sessions
        with sessions() as db:
            batch = AcquisitionBatchService(db, manager.acquisition).stage_selected(
                user_id="member-a", source_url="source", entries=_entry(),
            )

            class CrashBeforeLink(AcquisitionBatchService):
                def _persist_dispatch_receipt(self, entry, receipt) -> None:  # noqa: ANN001
                    raise SystemExit("crash before link")

            with pytest.raises(SystemExit, match="crash before link"):
                CrashBeforeLink(db, manager.acquisition).resume_batch(user_id="member-a", batch_id=batch.id)
            entry = db.scalar(select(AcquisitionBatchEntry))
            assert entry.status == "dispatching"
            assert db.scalar(select(AcquisitionJobOutput)) is None
            job = db.scalar(select(DownloadJob))
            assert job.status == "staged"

        async def dormant_worker() -> None:
            await asyncio.Event().wait()

        manager._worker_loop = dormant_worker  # type: ignore[method-assign]
        await manager.start()
        assert manager._queue.qsize() == 1
        with sessions() as db:
            assert db.scalar(select(AcquisitionJobOutput)) is not None
            assert db.get(DownloadJob, job.id).status == "queued"
        await manager.stop()

    asyncio.run(scenario())


def test_job_lifecycle_bridge_is_monotonic_and_links_completed_library_item() -> None:
    sessions = _factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    with sessions() as db:
        batch = AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id="member-a", source_url="source", entries=_entry(),
        )
        output = db.scalar(select(AcquisitionJobOutput))
        db.add(LibraryItem(
            id="library-one", user_id="member-a", visibility="private", extractor="example",
            remote_id="one", title="One", webpage_url="https://example.test/one",
        ))
        db.commit()
    assert manager.acquisition.observe(output.download_job_id, "running", progress=70)
    assert manager.acquisition.observe(output.download_job_id, "running", progress=20)
    assert manager.acquisition.observe(
        output.download_job_id, "completed", progress=100, library_item_id="library-one",
    )
    assert manager.acquisition.observe(output.download_job_id, "running", progress=10)
    with sessions() as db:
        entry = db.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == batch.id))
        linked = db.scalar(select(AcquisitionJobOutput))
        assert (entry.status, entry.progress) == ("completed", 100)
        assert linked.library_item_id == "library-one"


def test_ordinary_job_progress_never_polls_acquisition_storage(monkeypatch) -> None:  # noqa: ANN001
    sessions = _factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    observe = Mock(side_effect=AssertionError("ordinary job must not be observed"))
    manager.acquisition.observe = observe  # type: ignore[method-assign]
    with sessions() as db:
        db.add(DownloadJob(
            id="ordinary", user_id="member-a", source_url="https://example.test/ordinary",
            status="queued", format_selection={}, output_profile={},
        ))
        db.commit()

    def fail_after_progress(_self, *_args, **kwargs):  # noqa: ANN001, ANN202
        kwargs["progress_hooks"][0]({"downloaded_bytes": 5, "total_bytes": 10})
        raise RuntimeError("ordinary failure")

    monkeypatch.setattr(YtDlpService, "download", fail_after_progress)
    manager._run_job("ordinary")
    observe.assert_not_called()


def test_cancelled_acquisition_job_rejects_generic_retry_and_explicit_retry_uses_new_job() -> None:
    sessions = _factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    user = User(id="member-a", username="member-a", display_name="Member A", role="viewer", is_active=True)
    with sessions() as db:
        db.add(user)
        db.commit()
        first_batch = AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id=user.id, source_url="source", entries=_entry(),
        )
        first_entry = db.scalar(
            select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == first_batch.id)
        )
        first_output = db.scalar(
            select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == first_entry.id)
        )

        manager.cancel(db, first_output.download_job_id, user)
        db.commit()
        db.refresh(first_entry)
        assert first_entry.status == "cancelled"
        with pytest.raises(ValueError, match="must be retried from their acquisition batch"):
            manager.retry(db, first_output.download_job_id, user)

        retry_batch = AcquisitionBatchService(db, manager.acquisition).retry_entry(
            user_id=user.id, entry_id=first_entry.id,
        )
        retry_entry = db.scalar(
            select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == retry_batch.id)
        )
        retry_output = db.scalar(
            select(AcquisitionJobOutput).where(AcquisitionJobOutput.batch_entry_id == retry_entry.id)
        )
        assert retry_entry.id != first_entry.id
        assert retry_output.download_job_id != first_output.download_job_id

    queued_ids = [manager._queue.get_nowait(), manager._queue.get_nowait()]
    assert queued_ids == [first_output.download_job_id, retry_output.download_job_id]
    assert manager._queue.empty()


@pytest.mark.parametrize("terminal_status", ["cancelled", "completed"])
def test_stale_queue_id_cannot_rerun_terminal_job(terminal_status: str, monkeypatch) -> None:  # noqa: ANN001
    sessions = _factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    with sessions() as db:
        db.add(DownloadJob(
            id="stale", user_id="member-a", source_url="https://example.test/stale",
            status=terminal_status, format_selection={}, output_profile={},
        ))
        db.commit()

    download = Mock(side_effect=AssertionError("terminal jobs must not execute"))
    monkeypatch.setattr(YtDlpService, "download", download)
    assert manager._enqueue_job_id("stale") is True
    assert manager._enqueue_job_id("stale") is False
    assert list(manager._queue._queue) == ["stale"]  # noqa: SLF001
    manager._run_job("stale")
    download.assert_not_called()


def test_job_progress_events_never_carry_server_paths(monkeypatch) -> None:  # noqa: ANN001
    sessions = _factory()
    job_manager_module.SessionLocal = sessions
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(manager.events, "publish", lambda name, payload: published.append((name, payload)))
    with sessions() as db:
        db.add(DownloadJob(
            id="pathy", user_id="member-a", source_url="https://example.test/pathy",
            status="queued", format_selection={}, output_profile={},
        ))
        db.commit()

    def progress_then_fail(_self, *_args, **kwargs):  # noqa: ANN001, ANN202
        kwargs["progress_hooks"][0]({
            "downloaded_bytes": 5, "total_bytes": 10, "status": "downloading",
            "filename": "/srv/library/.lumina-staging/pathy/member-a/x.mp4", "tmpfilename": "/srv/library/x.mp4.part",
        })
        raise RuntimeError("stop")

    monkeypatch.setattr(YtDlpService, "download", progress_then_fail)
    manager._run_job("pathy")
    progress = [payload for name, payload in published if name == "job_progress"]
    assert progress and all("/srv/library" not in str(payload) for payload in progress)
