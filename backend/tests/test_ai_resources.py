"""AI allowances span existing job kinds, with fair execution and cancellable waiting."""
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, session_scope
from app.models import AppSettings, AsrJob, Summary, User
from app.services import ai_resources
from app.services.local_ai import JobRegistry
from app.services.yt_dlp_service import YtDlpService
from support import make_user


@pytest.fixture
def db_factory(tmp_path):
    # Concurrent execution probes need independent connections, as in the supported server.
    engine = create_engine(f"sqlite:///{tmp_path / 'ai.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def summary(job_id, owner="a"):
    return Summary(id=job_id, requested_by=owner, library_item_id=job_id, transcript_id=job_id,
                   transcript_revision=1, model_id="test-model", state="queued")


def seed(db_factory, monkeypatch):
    with db_factory() as db:
        db.add_all([make_user("a", role="admin"), make_user("b")])
        db.add(YtDlpService(db)._default_app_settings(None))
        db.commit()
    monkeypatch.setattr(ai_resources, "session_scope", lambda: session_scope(db_factory))


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "worker did not reach the expected admission boundary"
        time.sleep(0.005)


def test_cross_kind_atomic_queue_cap_and_duplicate_reuse(db_factory, monkeypatch):
    seed(db_factory, monkeypatch)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    registry = JobRegistry(16, RuntimeError)
    with db_factory() as db:
        db.get(AppSettings, 1).resource_policy = {"defaults": {"ai_queued": 2}}
        db.add(AsrJob(id="asr", library_item_id="item", kind="asr", model_id="test", requested_by="a", state="pending"))
        db.commit()
        first = registry.start(db, lambda job_id: summary(job_id), lambda _: None, "test_ai_start")
        # A duplicate at capacity reuses the durable job and its existing charge.
        duplicate = registry.start(db, lambda job_id: Summary(id=job_id, requested_by="a", library_item_id=first.library_item_id,
            transcript_id=first.transcript_id, transcript_revision=1, model_id="test-model", state="queued"), lambda _: None, "test_ai_duplicate")
        assert duplicate.id == first.id and len(registry.running_ids()) == 1
        with pytest.raises(RuntimeError, match="AI queue is full"):
            registry.start(db, lambda job_id: summary(job_id), lambda _: None, "test_ai_full")
        assert db.query(Summary).count() == 1
        # An owner has the same allowance; another member still has their own queue.
        other = registry.start(db, lambda job_id: summary(job_id, "b"), lambda _: None, "test_ai_other")
        assert other.requested_by == "b"


def test_round_robin_fifo_and_cancellation_without_execution(db_factory, monkeypatch):
    seed(db_factory, monkeypatch)
    with db_factory() as db:
        db.add_all([summary("a1"), summary("a2"), summary("a3"), summary("b1", "b")])
        db.commit()
    registry = JobRegistry(16, RuntimeError)
    registry.running.update(("a1", "a2", "a3", "b1"))
    release = threading.Event()
    started = []
    errors = []

    def run(job_id):
        try:
            with ai_resources.execution(Summary, job_id, registry) as allowed:
                if allowed:
                    started.append(job_id)
                    if job_id == "a1":
                        assert release.wait(3)
        except BaseException as error:
            errors.append(error)

    threads = []
    try:
        for job_id in ("a1", "a2", "a3", "b1"):
            thread = threading.Thread(target=run, args=(job_id,))
            thread.start()
            threads.append(thread)
            wait_until(lambda: job_id in started or job_id in ai_resources._waiting)
        # Cancel queued work while both execution/queue limits may be full.
        registry.cancel("a3")
        wait_until(lambda: not threads[2].is_alive())
        release.set()
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert started == ["a1", "b1", "a2"]
    assert not ai_resources._waiting and not ai_resources._active


def test_member_execution_cap_spans_summary_and_asr_pools(db_factory, monkeypatch):
    seed(db_factory, monkeypatch)
    with db_factory() as db:
        db.add(summary("summary"))
        db.add_all([AsrJob(id=job_id, library_item_id=job_id, kind="asr", model_id="test", requested_by=owner, state="queued")
                    for job_id, owner in (("asr-a", "a"), ("asr-b", "b"))])
        db.commit()
    registry = JobRegistry(4, RuntimeError)
    registry.running.update(("asr-a", "asr-b"))
    ran = []
    threads = []
    with ai_resources.execution(Summary, "summary", registry) as allowed:
        assert allowed
        for job_id in ("asr-a", "asr-b"):
            def run(job_id=job_id):
                with ai_resources.execution(AsrJob, job_id, registry) as permitted:
                    if permitted:
                        ran.append(job_id)
            thread = threading.Thread(target=run)
            thread.start()
            threads.append(thread)
        wait_until(lambda: "asr-b" in ran)
        assert "asr-a" not in ran
    for thread in threads:
        thread.join(3)
    assert ran == ["asr-b", "asr-a"]


def test_restart_releases_orphan_charges_and_member_can_cancel_own_jobs(api_client, db_factory, monkeypatch):
    seed(db_factory, monkeypatch)
    with db_factory() as db:
        db.add(summary("orphan"))
        db.add(AsrJob(id="waiting", library_item_id="item", kind="asr", model_id="test", requested_by="a", state="pending"))
        db.commit()
        ai_resources.recover(db)
        assert db.get(Summary, "orphan").state == "interrupted"
        owner, other = db.get(User, "a"), db.get(User, "b")
    client = api_client(user=other, base_url="http://localhost")
    assert client.post("/api/me/resource-jobs/enrichment/waiting/cancel").status_code == 404
    client = api_client(user=owner, base_url="http://localhost")
    assert client.get("/api/me/resource-jobs").json() == [{"id": "waiting", "kind": "enrichment", "state": "pending"}]
    assert client.post("/api/me/resource-jobs/enrichment/waiting/cancel").json() == {"state": "canceled"}
    assert client.get("/api/me/resource-jobs").json() == []


def test_interactive_work_refuses_when_the_same_member_cross_kind_slot_is_full(db_factory, monkeypatch):
    seed(db_factory, monkeypatch)
    with db_factory() as db:
        db.add(summary("summary"))
        db.commit()
    registry = JobRegistry(4, RuntimeError)
    registry.running.add("summary")
    release = threading.Event()
    def queued() -> None:
        with ai_resources.execution(Summary, "summary", registry) as allowed:
            assert allowed
            assert release.wait(3)

    worker = threading.Thread(target=queued)
    worker.start()
    wait_until(lambda: "a" in ai_resources._active)
    with pytest.raises(ai_resources.AiCapacityError, match="capacity is busy"):
        with ai_resources.interactive_execution("a", member_limit=1, pool_limit=4):
            pass
    release.set()
    worker.join(3)
    assert not ai_resources._active
