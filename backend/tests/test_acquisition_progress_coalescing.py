from __future__ import annotations

from sqlalchemy import select

from app.events import EventBus
from app.models import AcquisitionBatchEntry, AcquisitionJobOutput
from app.persistence import persistence_metrics_snapshot, reset_persistence_metrics
from app.services.acquisition_batch import AcquisitionBatchService, RedactedFailure, SelectedSourceEntry
from app.services.job_manager import JobManager
from support import memory_session_factory


def _linked_job(manager: JobManager, sessions, *, member: str, url: str) -> str:
    """Queue one entry through the adapter and return its owned download job id."""
    with sessions() as db:
        batch = AcquisitionBatchService(db, manager.acquisition).queue_selected(
            user_id=member,
            source_url=f"source-{member}",
            entries=[SelectedSourceEntry(url, "example", url.rsplit("/", 1)[-1], "One")],
        )
        output = db.scalar(
            select(AcquisitionJobOutput).join(
                AcquisitionBatchEntry, AcquisitionBatchEntry.id == AcquisitionJobOutput.batch_entry_id
            ).where(AcquisitionBatchEntry.batch_id == batch.id)
        )
        return output.download_job_id


def _outcome_writes() -> int:
    return int(persistence_metrics_snapshot().get("acquisition_outcome", {}).get("count", 0))


def _entry_for(sessions, job_id: str) -> AcquisitionBatchEntry:
    with sessions() as db:
        output = db.scalar(select(AcquisitionJobOutput).where(AcquisitionJobOutput.download_job_id == job_id))
        return db.get(AcquisitionBatchEntry, output.batch_entry_id)


def test_rapid_running_progress_coalesces_to_a_couple_writes_and_correct_final() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_id = _linked_job(manager, sessions, member="member-a", url="https://example.test/one")

    clock = [0.0]
    manager.acquisition._clock = lambda: clock[0]  # noqa: SLF001
    reset_persistence_metrics()

    for index in range(100):
        clock[0] += 0.005  # 0.5 s total elapsed, never reaching the 2 s interval
        progress = 40 + (index % 5)  # stays inside a 5-point band
        assert manager.acquisition.observe(job_id, "running", progress=progress)

    assert _outcome_writes() <= 2

    # A terminal observe is never coalesced, so the final durable progress is exact.
    assert manager.acquisition.observe(job_id, "completed", progress=100)
    entry = _entry_for(sessions, job_id)
    assert (entry.status, entry.progress) == ("completed", 100)


def test_progress_jump_of_five_or_more_persists_immediately() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_id = _linked_job(manager, sessions, member="member-a", url="https://example.test/one")

    manager.acquisition._clock = lambda: 0.0  # frozen: only the delta rule can fire  # noqa: SLF001
    reset_persistence_metrics()

    assert manager.acquisition.observe(job_id, "running", progress=10)  # first, persisted
    assert manager.acquisition.observe(job_id, "running", progress=14)  # delta 4, dropped
    assert manager.acquisition.observe(job_id, "running", progress=15)  # delta 5, persisted

    assert _outcome_writes() == 2
    assert _entry_for(sessions, job_id).progress == 15


def test_two_second_gap_persists_even_for_a_small_delta() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_id = _linked_job(manager, sessions, member="member-a", url="https://example.test/one")

    clock = [0.0]
    manager.acquisition._clock = lambda: clock[0]  # noqa: SLF001
    reset_persistence_metrics()

    assert manager.acquisition.observe(job_id, "running", progress=10)  # persisted at t=0
    clock[0] = 1.5
    assert manager.acquisition.observe(job_id, "running", progress=11)  # delta 1, gap 1.5 s, dropped
    clock[0] = 2.01
    assert manager.acquisition.observe(job_id, "running", progress=12)  # gap 2.01 s from last write, persisted

    assert _outcome_writes() == 2
    assert _entry_for(sessions, job_id).progress == 12


def test_terminal_state_is_never_coalesced_and_hits_the_database_synchronously() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_id = _linked_job(manager, sessions, member="member-a", url="https://example.test/one")

    manager.acquisition._clock = lambda: 0.0  # frozen: the 2 s rule can never fire  # noqa: SLF001
    reset_persistence_metrics()

    assert manager.acquisition.observe(job_id, "running", progress=50)  # persisted
    assert manager.acquisition.observe(job_id, "running", progress=51)  # delta 1, dropped
    assert manager.acquisition.observe(
        job_id, "failed", failure=RedactedFailure("job_failed", "The download job failed.")
    )

    assert _outcome_writes() == 2  # the dropped running observe never reached the writer
    entry = _entry_for(sessions, job_id)
    assert (entry.status, entry.progress) == ("failed", 100)


def test_late_running_observe_never_overwrites_terminal_progress() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_id = _linked_job(manager, sessions, member="member-a", url="https://example.test/one")

    manager.acquisition._clock = lambda: 0.0  # noqa: SLF001

    assert manager.acquisition.observe(job_id, "running", progress=70)
    assert manager.acquisition.observe(job_id, "completed", progress=100)
    # A stale, out-of-order running observe arrives after the terminal write.
    assert manager.acquisition.observe(job_id, "running", progress=10)

    entry = _entry_for(sessions, job_id)
    assert (entry.status, entry.progress) == ("completed", 100)
    # ...nor repopulates the per-job tracker the terminal observe cleared (#105 item 7).
    assert job_id not in manager.acquisition._progress._last_progress  # noqa: SLF001


def test_progress_coalescing_is_independent_per_entry() -> None:
    sessions = memory_session_factory()
    manager = JobManager(EventBus())
    manager.acquisition.session_factory = sessions
    job_a = _linked_job(manager, sessions, member="member-a", url="https://example.test/a")
    job_b = _linked_job(manager, sessions, member="member-b", url="https://example.test/b")

    manager.acquisition._clock = lambda: 0.0  # frozen  # noqa: SLF001
    reset_persistence_metrics()

    assert manager.acquisition.observe(job_a, "running", progress=50)  # persisted for A
    assert manager.acquisition.observe(job_a, "running", progress=51)  # dropped for A
    assert manager.acquisition.observe(job_b, "running", progress=60)  # one job's tracker must not gate another

    assert _outcome_writes() == 2
    assert _entry_for(sessions, job_a).progress == 50
    assert _entry_for(sessions, job_b).progress == 60
