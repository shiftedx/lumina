"""Bounded workload — validated limits, per-member caps and a library free-space reserve."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.events import EventBus
from app.main import app
from app.models import AppSettings, DownloadJob, User
from app.schemas import JobCreateRequest
from app.security import get_current_user
from app.services import job_manager as job_manager_module
from app.services.job_manager import JobAdmissionError, JobManager
from app.services.yt_dlp_service import YtDlpService

MB = 1024 * 1024


@pytest.fixture
def env(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    monkeypatch.setattr("app.main._require_source_acquisition_capability", lambda *args, **kwargs: None)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admission.sqlite3'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(job_manager_module, "SessionLocal", factory)
    admin = User(id="admin-1", username="admin", display_name="Admin", role="admin", is_active=True)
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    other = User(id="member-2", username="other", display_name="Other", role="viewer", is_active=True)
    with factory.begin() as db:
        db.add_all([admin, member, other])
        db.add(YtDlpService(db)._default_app_settings(None))
    actor = {"user": admin}

    def override_db():
        with factory() as db:
            yield db
            db.commit()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: actor["user"]
    yield factory, actor, TestClient(app, base_url="http://localhost"), (admin, member, other)
    app.dependency_overrides.clear()
    engine.dispose()


def _set(factory, **values) -> None:  # noqa: ANN001
    with factory.begin() as db:
        record = db.get(AppSettings, 1)
        for key, value in values.items():
            setattr(record, key, value)


@pytest.mark.parametrize(
    "payload",
    [{"concurrency": 0}, {"concurrency": -3}, {"concurrency": 10_000}, {"max_active_jobs_per_user": 0},
     {"max_active_jobs_per_user": 100_000}, {"min_free_disk_mb": -1}],
)
def test_oversized_concurrency_rejected(env, payload) -> None:  # noqa: ANN001
    factory, _actor, client, _users = env
    assert client.put("/api/admin/settings", json=payload).status_code == 422
    with factory() as db:
        assert db.get(AppSettings, 1).concurrency == 1


def test_accepted_limits_declare_when_they_apply(env) -> None:  # noqa: ANN001
    _factory, actor, client, (_admin, member, _other) = env
    response = client.put("/api/admin/settings", json={"concurrency": 3, "max_active_jobs_per_user": 5, "min_free_disk_mb": 0})
    assert response.status_code == 200
    body = response.json()
    assert (body["concurrency"], body["max_active_jobs_per_user"], body["min_free_disk_mb"]) == (3, 5, 0)
    assert body["setting_effects"] == {"concurrency": "restart", "max_active_jobs_per_user": "new_jobs", "min_free_disk_mb": "new_jobs"}
    actor["user"] = member
    assert client.put("/api/admin/settings", json={"concurrency": 2}).status_code == 403


def test_full_root_blocks_before_write(env, monkeypatch) -> None:  # noqa: ANN001
    factory, _actor, client, (_admin, member, _other) = env
    _set(factory, min_free_disk_mb=100)
    monkeypatch.setattr(job_manager_module, "_free_bytes", lambda root: 50 * MB)
    manager = JobManager(EventBus())
    with factory() as db, pytest.raises(JobAdmissionError) as refused:
        manager.enqueue(db, JobCreateRequest(source_url="https://example.com/a"), member)
    assert (refused.value.reason, refused.value.status_code) == ("storage_low", 409)
    with factory() as db:
        assert db.query(DownloadJob).count() == 0

    # Admitted with room to spare, but the download is larger than what is left
    # above the reserve: it stops at the first progress report, not at a full disk.
    monkeypatch.setattr(job_manager_module, "_free_bytes", lambda root: 110 * MB)
    with factory() as db:
        job = manager.enqueue(db, JobCreateRequest(source_url="https://example.com/b"), member)
    reports: list[int] = []

    def fake_download(self, url, *, progress_hooks, **kwargs):  # noqa: ANN001
        for downloaded in (1 * MB, 2 * MB):
            reports.append(downloaded)
            progress_hooks[0]({"status": "downloading", "downloaded_bytes": downloaded, "total_bytes": 500 * MB})
        raise AssertionError("download continued past the reserve")

    monkeypatch.setattr(YtDlpService, "download", fake_download)
    manager._run_job(job.id)
    assert reports == [1 * MB]
    with factory() as db:
        stopped = db.get(DownloadJob, job.id)
        assert stopped.status == "failed"
        assert "free-space reserve" in stopped.error


def test_queued_jobs_reserve_is_summed_across_admissions(env, monkeypatch) -> None:  # noqa: ANN001
    """A second queued job's known size counts against free space, not just the first's."""
    factory, _actor, client, (_admin, member, _other) = env
    _set(factory, min_free_disk_mb=100, max_active_jobs_per_user=5)
    monkeypatch.setattr(job_manager_module, "_free_bytes", lambda root: 150 * MB)
    manager = JobManager(EventBus())
    with factory() as db:
        first = manager.enqueue(
            db, JobCreateRequest(source_url="https://example.com/big1", preview_snapshot={"filesize": 80 * MB}), member
        )
    assert first.preview_snapshot["filesize"] == 80 * MB
    # 150 MB free - 80 MB already queued leaves 70 MB, below the 100 MB reserve.
    with factory() as db, pytest.raises(JobAdmissionError) as refused:
        manager.enqueue(db, JobCreateRequest(source_url="https://example.com/big2", preview_snapshot={"filesize": 80 * MB}), member)
    assert (refused.value.reason, refused.value.status_code) == ("storage_low", 409)
    with factory() as db:
        assert db.query(DownloadJob).count() == 1


def test_member_cannot_starve_household(env, monkeypatch) -> None:  # noqa: ANN001
    factory, actor, client, (_admin, member, other) = env
    _set(factory, max_active_jobs_per_user=2, min_free_disk_mb=0)
    monkeypatch.setattr("app.main.jobs", JobManager(EventBus()))
    actor["user"] = member
    statuses = [client.post("/api/jobs", json={"source_url": f"https://example.com/{n}"}).status_code for n in range(3)]
    assert statuses == [201, 201, 429]
    refused = client.post("/api/jobs", json={"source_url": "https://example.com/again"})
    assert refused.json()["detail"]["reason"] == "member_queue_full"
    # The household's other member is still admitted.
    actor["user"] = other
    assert client.post("/api/jobs", json={"source_url": "https://example.com/theirs"}).status_code == 201


def test_extra_source_ports_are_validated_saved_and_applied(env, monkeypatch) -> None:  # noqa: ANN001
    from app.services import network_policy

    monkeypatch.setattr(network_policy, "_extra_ports", frozenset())
    _factory, _actor, client, _users = env
    for refused in ([22], [8000, 6379], [0], [65536]):
        assert client.put("/api/admin/settings", json={"extra_source_ports": refused}).status_code == 422, refused
    assert client.get("/api/admin/settings").json()["extra_source_ports"] == []
    saved = client.put("/api/admin/settings", json={"extra_source_ports": [8080, 8000, 8000, 443]}).json()
    assert saved["extra_source_ports"] == [8000, 8080] and network_policy._extra_ports == {8000, 8080}
    assert client.put("/api/admin/settings", json={"extra_source_ports": []}).json()["extra_source_ports"] == []
    assert network_policy._extra_ports == frozenset()
