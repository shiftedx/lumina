"""One Enrichment job pipeline (dispatch by kind, pending pump, restart recovery, bulk)."""
from __future__ import annotations

import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import AsrJob, LibraryItem, MediaTitle, User
from app.security import get_current_user
from app.services import enrichment, local_asr

T0 = datetime(2026, 9, 25, 12, 0, 0)


@pytest.fixture
def items():
    db_module.init_db()
    with db_module.session_scope() as db:
        db.add_all([
            LibraryItem(id=f"i{n}", user_id="owner", visibility="shared", title=f"Item {n}", metadata_json={}, status="available")
            for n in range(3)
        ])
    yield
    with local_asr.jobs.lock:
        local_asr.jobs.running.clear()
        local_asr.jobs.canceled.clear()
        enrichment._bulk.clear()
        enrichment._demanded_captions.clear()
        enrichment._caption_threads.clear()


def _job(job_id: str, item_id: str = "i0", state: str = "pending", error: str | None = None, at: int = 0) -> AsrJob:
    return AsrJob(
        id=job_id, library_item_id=item_id, kind="segments", params={}, model_id="segments-v1",
        state=state, error=error, created_at=T0 + timedelta(seconds=at),
    )


def _caption_job(job_id: str, *, requested_by: str | None = None, item_id: str = "i0", at: int = 0) -> AsrJob:
    return AsrJob(
        id=job_id, library_item_id=item_id, kind="captions", params={}, model_id="captions-v1",
        state="pending", requested_by=requested_by, created_at=T0 + timedelta(seconds=at),
    )


def _states() -> dict[str, tuple[str, str | None]]:
    with db_module.session_scope() as db:
        return {job.id: (job.state, job.error) for job in db.query(AsrJob).all()}


def _wait(predicate, timeout: float = 5) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.02)


def test_restart_repends_orphans_once_then_fails(items) -> None:  # noqa: ANN001
    with db_module.session_scope() as db:
        db.add_all([
            _job("queued", state="queued"), _job("running", state="running"), _job("waiting"),
            _job("twice", state="running", error="restarted"), _job("done", state="succeeded"),
        ])
    assert enrichment.recover_after_restart() == (2, 1)
    states = _states()
    assert states["queued"] == states["running"] == ("pending", "restarted")
    assert states["waiting"] == ("pending", None) and states["done"] == ("succeeded", None)
    assert states["twice"] == ("failed", "Interrupted by two restarts")
    with db_module.session_scope() as db:
        db.get(AsrJob, "queued").state = "running"  # re-admitted, then the server restarts again
    assert enrichment.recover_after_restart() == (0, 1)
    assert _states()["queued"][0] == "failed"


def test_pump_runs_one_bulk_job_at_a_time(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    release = threading.Event()
    ran: list[str] = []
    peak = [0]

    def worker(spec: enrichment.JobSpec) -> dict:
        ran.append(spec.id)
        peak[0] = max(peak[0], len(enrichment._bulk))
        release.wait(5)
        return {}

    monkeypatch.setitem(enrichment.WORKERS, "segments", worker)
    with db_module.session_scope() as db:
        db.add_all([_job(f"b{n}", item_id=f"i{n}", at=n) for n in range(3)])
    racers = [threading.Thread(target=enrichment.pump_pending) for _ in range(4)]  # tick and job-end pumps racing
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()
    _wait(lambda: ran == ["b0"])
    assert [state for state, _ in _states().values()].count("pending") == 2
    release.set()
    _wait(lambda: all(state == "succeeded" for state, _ in _states().values()))
    assert ran == ["b0", "b1", "b2"] and peak[0] == 1  # each ending bulk job admitted the next


def test_automatic_captions_stay_pending_until_the_backfill_window(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ran: list[str] = []
    monkeypatch.setitem(enrichment.WORKERS, "captions", lambda spec: ran.append(spec.id) or {})
    with db_module.session_scope() as db:
        db.add(_caption_job("automatic"))

    assert enrichment.pump_pending() == 0
    time.sleep(0.1)
    assert ran == [] and _states()["automatic"] == ("pending", None)


def test_caption_bulk_admission_respects_demand_and_activity(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ran: list[tuple[str, bool, bool]] = []
    busy, window = [True], [False]
    monkeypatch.setattr(enrichment.background_work, "busy", lambda: busy[0])
    monkeypatch.setattr(enrichment.background_work, "backfill_allowed", lambda: not busy[0] and window[0])
    monkeypatch.setitem(enrichment.WORKERS, "captions", lambda spec: ran.append((spec.id, spec.background, spec.backfill)) or {})
    monkeypatch.setitem(enrichment.WORKERS, "segments", lambda spec: {})
    with db_module.session_scope() as db:
        db.add_all([_caption_job("demanded", requested_by="admin"), _job("segments", item_id="i1", at=1)])

    assert enrichment.pump_pending() == 1  # unrelated enrichment keeps its existing admission behavior
    _wait(lambda: _states()["segments"][0] == "succeeded")
    _wait(lambda: not enrichment._bulk and not enrichment._pump_lock.locked())
    assert ran == [] and _states()["demanded"] == ("pending", None)

    busy[0] = False
    assert enrichment.pump_pending() == 1  # an owner-requested bulk caption job does not need a schedule
    _wait(lambda: _states()["demanded"][0] == "succeeded")
    assert ran == [("demanded", True, False)]

    with db_module.session_scope() as db:
        db.add(_caption_job("automatic", item_id="i2", at=2))
    assert enrichment.pump_pending() == 0
    window[0] = True
    assert enrichment.pump_pending() == 1
    _wait(lambda: _states()["automatic"][0] == "succeeded")
    assert ran == [("demanded", True, False), ("automatic", True, True)]


def test_explicit_request_promotes_a_matching_automatic_caption(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ran: list[str] = []
    monkeypatch.setitem(enrichment.WORKERS, "captions", lambda spec: ran.append(spec.id) or {})
    with db_module.session_scope() as db:
        db.add(_caption_job("automatic"))

    with db_module.session_scope() as db:
        job, created = enrichment.request_job(db, "i0", "captions", {}, "owner")

    assert (job.id, created) == ("automatic", False)
    _wait(lambda: _states()["automatic"][0] == "succeeded")
    assert ran == ["automatic"]


def test_explicit_request_promotes_a_caption_racing_pump_admission(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    admitted = threading.Event()
    resume_pump = threading.Event()
    ran: list[tuple[bool, bool]] = []
    pump_thread: list[threading.Thread] = []
    real_admit = local_asr.jobs.admit
    monkeypatch.setattr(enrichment.background_work, "busy", lambda: False)
    monkeypatch.setattr(enrichment.background_work, "backfill_allowed", lambda: True)

    def admit(job_id: str) -> bool:
        result = real_admit(job_id)
        if threading.current_thread() is pump_thread[0]:
            admitted.set()
            assert resume_pump.wait(2)
        return result

    def worker(spec: enrichment.JobSpec) -> dict:
        ran.append((spec.backfill, enrichment._caption_interactive(spec)))
        return {}

    monkeypatch.setattr(local_asr.jobs, "admit", admit)
    monkeypatch.setitem(enrichment.WORKERS, "captions", worker)
    with db_module.session_scope() as db:
        db.add(_caption_job("automatic"))

    pump = threading.Thread(target=enrichment.pump_pending)
    pump_thread.append(pump)
    pump.start()
    assert admitted.wait(2)
    with db_module.session_scope() as db:
        job, created = enrichment.request_job(db, "i0", "captions", {}, "owner")
    assert (job.id, created) == ("automatic", False)
    resume_pump.set()
    pump.join(2)
    assert not pump.is_alive()
    _wait(lambda: _states()["automatic"][0] == "succeeded")

    with db_module.session_scope() as db:
        jobs = db.query(AsrJob).filter_by(library_item_id="i0", kind="captions").all()
        assert [(row.id, row.requested_by) for row in jobs] == [("automatic", "owner")]
    assert ran == [(True, True)]  # pump captured backfill, but the already-admitted worker observed interactive demand


def test_a_failed_promotion_commit_releases_the_interactive_slot(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from contextlib import contextmanager

    from sqlalchemy.exc import OperationalError

    with db_module.session_scope() as db:
        db.add(_caption_job("automatic"))
    real = enrichment.write_transaction

    @contextmanager
    def failing_promotion(db, *, name: str):  # noqa: ANN001, ANN202
        with real(db, name=name):
            yield
            if name == "enrichment_job_promote":
                raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(enrichment, "write_transaction", failing_promotion)
    with db_module.session_scope() as db, pytest.raises(OperationalError):
        enrichment.request_job(db, "i0", "captions", {}, "owner")

    assert not local_asr.jobs.is_active("automatic")
    assert _states()["automatic"] == ("pending", None)


def test_a_failed_racing_demand_commit_does_not_promote_the_admitted_worker(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    from contextlib import contextmanager

    from sqlalchemy.exc import OperationalError

    with db_module.session_scope() as db:
        db.add(_caption_job("automatic"))
    assert local_asr.jobs.admit("automatic")
    real = enrichment.write_transaction

    @contextmanager
    def failing_demand(db, *, name: str):  # noqa: ANN001, ANN202
        with real(db, name=name):
            yield
            if name == "enrichment_caption_demand":
                raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(enrichment, "write_transaction", failing_demand)
    with db_module.session_scope() as db, pytest.raises(OperationalError):
        enrichment.request_job(db, "i0", "captions", {}, "owner")

    with db_module.session_scope() as db:
        assert db.get(AsrJob, "automatic").requested_by is None
    with local_asr.jobs.lock:
        assert "automatic" not in enrichment._demanded_captions


def test_request_job_dispatches_by_kind_and_reuses(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    calls: list[enrichment.JobSpec] = []
    monkeypatch.setattr(enrichment.background_work, "busy", lambda: True)
    monkeypatch.setattr(enrichment.background_work, "backfill_allowed", lambda: False)
    monkeypatch.setitem(enrichment.WORKERS, "captions", lambda spec: calls.append(spec) or {})
    with db_module.session_scope() as db:
        job, created = enrichment.request_job(db, "i0", "captions", {}, "owner")
    assert created and job.kind == "captions" and job.model_id == "captions-v1"
    _wait(lambda: _states()[job.id][0] == "succeeded")
    assert calls == [enrichment.JobSpec(job.id, "i0", "captions", "captions-v1", {})]
    assert not calls[0].background and not calls[0].backfill
    with db_module.session_scope() as db:
        again, created_again = enrichment.request_job(db, "i0", "captions", {}, "owner")
    assert (again.id, created_again) == (job.id, False)


def test_background_caption_worker_checkpoints_before_opening_an_extraction_stage(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    busy = [True]
    extracted: list[tuple[bool, bool]] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(enrichment.background_work, "busy", lambda: busy[0])
    monkeypatch.setattr(enrichment.MediaProbeService, "facts", lambda self, item: {})
    monkeypatch.setattr(enrichment, "text_streams", lambda db, item: [{"index": 2, "language": "eng"}])
    monkeypatch.setattr(enrichment, "_stream_extraction", lambda db, item: object())

    def read_stream(  # noqa: ANN001, ANN202
        extraction, stream_index, *, background=False, backfill=False, check_active=None, interactive=None,
    ):
        extracted.append((background, backfill))
        return b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n"

    monkeypatch.setattr(enrichment, "_read_stream", read_stream)
    spec = enrichment.JobSpec("job", "i0", "captions", "captions-v1", {}, background=True)

    def work() -> None:
        try:
            enrichment._work_captions(spec)
        except BaseException as exc:  # pragma: no cover - reported below with the original exception
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.15)
    assert extracted == []
    busy[0] = False
    thread.join(2)

    assert not thread.is_alive() and not errors
    assert extracted == [(True, False)]


def test_canceling_a_caption_waiting_at_an_activity_gate_finishes_canceled(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(enrichment.background_work, "busy", lambda: True)
    with db_module.session_scope() as db:
        row = _caption_job("caption", requested_by="admin")
        row.state = "queued"
        db.add(row)
    assert local_asr.jobs.admit("caption")
    with local_asr.jobs.lock:
        enrichment._bulk.add("caption")

    thread = threading.Thread(target=enrichment.run_job, args=("caption",), kwargs={"background": True})
    thread.start()
    _wait(lambda: _states()["caption"][0] == "running")
    assert local_asr.cancel("caption")
    thread.join(2)

    assert not thread.is_alive()
    assert _states()["caption"][0] == "canceled"
    assert not local_asr.jobs.is_active("caption")


def test_shutdown_leaves_a_scheduled_caption_durable_for_restart(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    from app.services import model_supervisor

    supervisor = model_supervisor.Supervisor()
    monkeypatch.setattr(model_supervisor, "supervisor", supervisor)
    monkeypatch.setattr(enrichment.background_work, "backfill_allowed", lambda: False)
    with db_module.session_scope() as db:
        row = _caption_job("caption")
        row.state = "queued"
        db.add(row)
    assert local_asr.jobs.admit("caption")
    with local_asr.jobs.lock:
        enrichment._bulk.add("caption")

    thread = threading.Thread(
        target=enrichment.run_job, args=("caption",), kwargs={"background": True, "backfill": True},
    )
    thread.start()
    _wait(lambda: _states()["caption"][0] == "running")
    supervisor.close()
    thread.join(2)

    assert not thread.is_alive()
    assert _states()["caption"] == ("running", None)
    assert enrichment.recover_after_restart() == (1, 0)
    assert _states()["caption"] == ("pending", "restarted")


def test_native_shutdown_reaps_a_paused_caption_child_and_joins_its_worker(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    from app.services import model_supervisor

    class HungProcess:
        def __init__(self, command: list[str]) -> None:
            self.command = command
            self.returncode: int | None = None
            self.signals: list[int] = []
            self.terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is not None:
                return self.returncode
            time.sleep(min(timeout or 0, 0.01))
            raise subprocess.TimeoutExpired(self.command, timeout)

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    supervisor = model_supervisor.Supervisor()
    process: list[HungProcess] = []
    monkeypatch.setattr(model_supervisor, "supervisor", supervisor)
    monkeypatch.setattr(enrichment.background_work, "_in_window", lambda: True)
    monkeypatch.setattr(
        enrichment.subtitle_tracks.subprocess, "Popen",
        lambda command, **kwargs: process.append(HungProcess(command)) or process[-1],
    )
    scope = {"type": "http", "state": {}}
    enrichment.background_work.start()
    try:
        def extracting(spec: enrichment.JobSpec) -> dict:
            returncode = enrichment.subtitle_tracks._run_background_extract(
                ["ffmpeg"], backfill=True, check_active=lambda: enrichment._caption_active(spec),
                interactive=lambda: enrichment._caption_interactive(spec),
            )
            if returncode:
                raise enrichment.EnrichmentError("Caption extraction stopped")
            return {}

        monkeypatch.setitem(enrichment.WORKERS, "captions", extracting)
        with db_module.session_scope() as db:
            row = _caption_job("caption")
            row.state = "queued"
            db.add(row)
        assert local_asr.jobs.admit("caption")
        with local_asr.jobs.lock:
            enrichment._bulk.add("caption")
        threading.Thread(
            target=enrichment.run_job, args=("caption",), kwargs={"background": True, "backfill": True}, daemon=True,
        ).start()
        _wait(lambda: _states()["caption"][0] == "running" and bool(process))

        enrichment.background_work.begin_request(scope)
        _wait(lambda: signal.SIGSTOP in process[0].signals)
        supervisor.close()
        enrichment.background_work.close()
        enrichment.join_background_captions()  # the lifecycle uses this bounded join; the test never joins the worker directly

        assert process[0].terminated and signal.SIGCONT in process[0].signals
        assert _states()["caption"] == ("running", None)
        with local_asr.jobs.lock:
            assert not enrichment._caption_threads
    finally:
        enrichment.background_work.end_request(scope)
        enrichment.background_work.start()


def test_background_caption_start_after_shutdown_leaves_the_queued_row_durable(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    from app.services import model_supervisor

    supervisor = model_supervisor.Supervisor()
    monkeypatch.setattr(model_supervisor, "supervisor", supervisor)
    with db_module.session_scope() as db:
        row = _caption_job("caption")
        row.state = "queued"
        db.add(row)
    assert local_asr.jobs.admit("caption")
    with local_asr.jobs.lock:
        enrichment._bulk.add("caption")
    supervisor.close()

    enrichment.run_job("caption", background=True, backfill=True)

    assert _states()["caption"] == ("queued", None)
    assert not local_asr.jobs.is_active("caption")
    with local_asr.jobs.lock:
        assert not enrichment._caption_threads and "caption" not in enrichment._bulk


def test_background_caption_join_reports_a_worker_that_misses_the_shutdown_deadline(
    items, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    clock = [100.0]

    class StuckWorker:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float) -> None:
            clock[0] += timeout

    worker = StuckWorker()
    monkeypatch.setattr(enrichment.time, "monotonic", lambda: clock[0])
    with local_asr.jobs.lock:
        enrichment._caption_threads.add(worker)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="Background caption workers did not stop during shutdown"):
        enrichment.join_background_captions()


def test_worker_failures_are_content_free(items, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN001
    def boom(spec: enrichment.JobSpec) -> dict:
        raise KeyError("secret transcript text")

    monkeypatch.setitem(enrichment.WORKERS, "segments", boom)
    with db_module.session_scope() as db:
        job, _ = enrichment.request_job(db, "i0", "segments", {}, "owner")
    _wait(lambda: _states()[job.id][0] == "failed")
    assert _states()[job.id][1] == "Enrichment job failed"
    assert "secret transcript text" not in caplog.text


def test_unconfigured_ai_kinds_are_unavailable(items) -> None:  # noqa: ANN001
    with db_module.session_scope() as db:
        with pytest.raises(enrichment.EnrichmentUnavailable, match="ai_not_configured"):
            enrichment.model_for(db, "translate")
        with pytest.raises(enrichment.EnrichmentUnavailable, match="asr_not_configured"):
            enrichment.model_for(db, "asr")
        assert [enrichment.model_for(db, kind) for kind in ("sync", "segments", "captions")] == ["sync-v1", "segments-v1", "captions-v1"]


def test_cancel_pending(items) -> None:  # noqa: ANN001
    with db_module.session_scope() as db:
        db.add_all([_job("p"), _job("r", state="running")])
    assert enrichment.cancel_pending("p") is True and enrichment.cancel_pending("r") is False
    assert _states()["p"][0] == "canceled"


def test_bulk_by_title_skips_extras_existing_rows_and_caps(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    with db_module.session_scope() as db:
        db.add_all([
            MediaTitle(id="series", type="series", key="r:Show", name="Show"),
            MediaTitle(id="season", type="season", parent_id="series", key="r:Show#s1", name="Season 1", index_number=1),
            MediaTitle(id="e1", type="episode", parent_id="season", key="r:Show#s1e1", name="One", index_number=1),
            MediaTitle(id="e2", type="episode", parent_id="season", key="r:Show#s1e2", name="Two", index_number=2),
        ])
        db.get(LibraryItem, "i0").title_id = "e1"
        db.get(LibraryItem, "i1").title_id = "e2"
        trailer = db.get(LibraryItem, "i2")
        trailer.title_id, trailer.extra_type = "series", "trailer"
    with db_module.session_scope() as db:
        ids = enrichment.bulk_item_ids(db, title_id="series")
        assert sorted(ids) == ["i0", "i1"]
        assert enrichment.queue_bulk(db, ids, ["segments", "captions"], "admin") == (4, 0)
        assert enrichment.queue_bulk(db, ids, ["segments", "captions"], "admin") == (0, 4)
        with pytest.raises(ValueError):
            enrichment.queue_bulk(db, ids, ["sync"], "admin")
        monkeypatch.setattr(enrichment, "MAX_BULK_ROWS", 3)
        with pytest.raises(enrichment.EnrichmentError, match="too_many_rows"):
            enrichment.queue_bulk(db, ids, ["segments", "captions"], "admin")
    assert {state for state, _ in _states().values()} == {"pending"}


def test_admin_tasks_show_and_cancel_pending(items) -> None:  # noqa: ANN001
    admin = User(id="admin", username="admin", display_name="Admin", role="admin", is_active=True)
    with db_module.session_scope() as db:
        db.add_all([User(id="admin", username="admin", display_name="Admin", role="admin", is_active=True), _job("p1")])
    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        client = TestClient(app, base_url="http://localhost")
        listed = client.get("/api/admin/tasks", params={"kind": "asr", "status": "active"}).json()
        (task,) = listed["items"]
        assert (task["id"], task["status"], task["can_cancel"], task["detail"]) == ("p1", "pending", True, "segments · segments-v1")
        assert client.post("/api/admin/tasks/asr/p1/cancel").json() == {"status": "canceled"}
    finally:
        app.dependency_overrides.clear()
    assert _states()["p1"][0] == "canceled"


def test_disabled_ai_features_are_unavailable(items) -> None:  # noqa: ANN001
    """Owner-approved kill switch: a key in ai_features_disabled blocks its enrichment kind."""
    from app.services.yt_dlp_service import YtDlpService

    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["sync", "translate", "subtitles_from_speech"]
    with db_module.session_scope() as db:
        for kind in ("sync", "translate", "asr"):
            with pytest.raises(enrichment.EnrichmentUnavailable, match="ai_feature_disabled"):
                enrichment.model_for(db, kind)
        assert enrichment.model_for(db, "segments") == "segments-v1"


def test_kill_switch_cancels_pending_backlog_and_blocks_run(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Review 7: turning a feature off stops its pending backlog (bulk or re-pended) and a queued job at start."""
    from app.services.yt_dlp_service import YtDlpService

    ran: list[str] = []
    monkeypatch.setitem(enrichment.WORKERS, "asr", lambda spec: ran.append(spec.id) or {})
    with db_module.session_scope() as db:
        db.add_all([
            AsrJob(id=f"a{n}", library_item_id=f"i{n}", kind="asr", model_id="whisper", state="pending", created_at=T0)
            for n in range(2)
        ])
        db.add(AsrJob(id="q", library_item_id="i2", kind="asr", model_id="whisper", state="queued", created_at=T0))
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["subtitles_from_speech"]
    assert enrichment.pump_pending() == 0
    enrichment.run_job("q")
    states = _states()
    assert ran == []
    assert states["a0"] == states["a1"] == states["q"] == ("canceled", "ai_feature_disabled")


def test_pump_never_admits_a_row_canceled_under_it(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Review 10: a cancel that lands between the pump's read and its write wins."""
    ran: list[str] = []
    monkeypatch.setitem(enrichment.WORKERS, "segments", lambda spec: ran.append(spec.id) or {})
    with db_module.session_scope() as db:
        db.add(_job("p"))
    real_admit = local_asr.jobs.admit

    def admit_after_cancel(job_id: str) -> bool:
        assert enrichment.cancel_pending(job_id)
        return real_admit(job_id)

    monkeypatch.setattr(local_asr.jobs, "admit", admit_after_cancel)
    assert enrichment.pump_pending() == 0
    time.sleep(0.2)
    assert ran == [] and _states()["p"][0] == "canceled"
    assert not enrichment._bulk and not local_asr.jobs.is_active("p")


def test_segments_redetect_after_the_file_changes(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Review 8: a succeeded segments job counts as done only while its analysis matches the current file."""
    from app.services import media_segments

    analysis: dict[str, dict] = {"i0": {"fingerprint": "1:1", "segments": []}}
    monkeypatch.setattr(
        media_segments, "current_analysis", lambda db, item_id: (None, "1:1", analysis.get(item_id, {})),
    )
    monkeypatch.setitem(enrichment.WORKERS, "segments", lambda spec: {})
    with db_module.session_scope() as db:
        db.add_all([_job("s0", state="succeeded"), _job("s1", item_id="i1", state="succeeded")])
    with db_module.session_scope() as db:
        again, created = enrichment.request_job(db, "i0", "segments", {}, "owner")
        assert (again.id, created) == ("s0", False)
        assert enrichment.queue_bulk(db, ["i0", "i1"], ["segments"], "admin") == (1, 1)  # i1's file changed
        fresh, created = enrichment.request_job(db, "i1", "segments", {}, "owner")
        assert fresh.id != "s1" and fresh.state == "pending"  # the re-queued row, not the stale success


def test_a_failed_admit_commit_releases_the_pump_slot(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """The admitted flag is set only after the write commits."""
    from contextlib import contextmanager

    from sqlalchemy.exc import OperationalError

    with db_module.session_scope() as db:
        db.add(_job("p"))
    real = enrichment.write_transaction

    @contextmanager
    def failing_admit(db, *, name: str):  # noqa: ANN001, ANN202
        with real(db, name=name):
            yield
            if name == "enrichment_job_admit":
                raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(enrichment, "write_transaction", failing_admit)
    with pytest.raises(OperationalError):
        enrichment.pump_pending()
    assert not enrichment._bulk and not local_asr.jobs.is_active("p")
    assert _states()["p"][0] == "pending"


def test_shutdown_mid_job_leaves_the_row_for_restart_recovery(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.services import model_supervisor
    from app.services.local_ai import LocalAiError

    sup = model_supervisor.Supervisor()
    monkeypatch.setattr(model_supervisor, "supervisor", sup)
    ran: list[str] = []

    def transcribing(spec: enrichment.JobSpec) -> dict:
        ran.append(spec.id)
        sup.close()  # graceful restart kills the speech server under the leased transcribe
        raise LocalAiError("The speech model stopped")

    monkeypatch.setitem(enrichment.WORKERS, "segments", transcribing)
    with db_module.session_scope() as db:
        db.add_all([_job("b0"), _job("b1", item_id="i1", at=1)])
    enrichment.pump_pending()
    _wait(lambda: not enrichment._bulk)
    assert ran == ["b0"]  # the pump is a no-op once shutdown began
    assert _states() == {"b0": ("running", None), "b1": ("pending", None)}
    assert enrichment.recover_after_restart() == (1, 0)
    assert _states()["b0"] == ("pending", "restarted")


def test_a_job_finishing_while_its_row_is_read_is_not_interrupted(items) -> None:  # noqa: ANN001
    """The worker commits its outcome, then leaves the registry: a row read just before both is not an orphan."""
    with db_module.session_scope() as db:
        db.add(_job("r", state="running"))
    assert local_asr.jobs.admit("r")
    with db_module.session_scope() as reader:
        row = reader.get(AsrJob, "r")  # the poll has read the running row...
        with db_module.session_scope() as db:
            db.get(AsrJob, "r").state = "failed"  # ...then the worker finishes...
        local_asr.jobs.release("r")  # ...and releases its slot before the poll serializes
        assert local_asr.state_of(row) == "failed"
    with db_module.session_scope() as db:
        db.get(AsrJob, "r").state = "running"
    with db_module.session_scope() as db:
        assert local_asr.state_of(db.get(AsrJob, "r")) == "interrupted"  # a restart's orphan still is one


def test_a_job_row_deleted_while_it_is_read_is_interrupted_not_an_error(items) -> None:  # noqa: ANN001
    with db_module.session_scope() as db:
        db.add(_job("gone", state="running"))
    with db_module.session_scope() as reader:
        row = reader.get(AsrJob, "gone")
        with db_module.session_scope() as db:
            db.delete(db.get(AsrJob, "gone"))
        assert local_asr.state_of(row) == "interrupted"


def test_a_pump_already_admitting_stops_at_shutdown(items, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.services import model_supervisor

    sup = model_supervisor.Supervisor()
    monkeypatch.setattr(model_supervisor, "supervisor", sup)
    monkeypatch.setattr(enrichment, "BULK_ACTIVE", 2)
    monkeypatch.setitem(enrichment.WORKERS, "segments", lambda spec: {})
    admit = local_asr.jobs.admit

    def admit_then_shut_down(job_id: str) -> bool:
        admitted = admit(job_id)
        sup.close()  # shutdown begins while this pump is mid-pass
        return admitted

    monkeypatch.setattr(local_asr.jobs, "admit", admit_then_shut_down)
    with db_module.session_scope() as db:
        db.add_all([_job("b0"), _job("b1", item_id="i1", at=1)])
    assert enrichment.pump_pending() == 1
    _wait(lambda: not enrichment._bulk)
    assert _states()["b1"] == ("pending", None)
