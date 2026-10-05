"""The admin library-automation routes. S2's service is replaced by FakeAutomation."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.models import AppSettings, ImportRun, StorageRoot, User
from app.routers import admin_library_automation
from app.security import hash_password
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)


class FakeAutomation:
    """Stands in for S2's service: records calls, returns canned shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.scan_result = {"started": [], "queued": [], "skipped": []}
        self.diag = {"poller": {"heartbeat_at": None, "stalled": False, "last_error": None}, "driver": {"active_run_id": None, "queued": 0}, "roots": []}

    def _entry(self, root_id: str) -> dict:
        return {"root_id": root_id, "label": "TV", "schedule": "off", "watch": False, "watch_interval_s": 300, "state": "idle",
                "state_detail": {"pending_files": 0, "dirs_listed": 0, "dirs_total": 0, "since": None}, "active_run": None, "last_run": None,
                "last_full_run_at": None, "next_scan_at": None, "last_skip": None}

    def snapshot(self) -> dict:
        return {"server_timezone": "UTC", "night_hour": 3, "poller": {"heartbeat_at": None, "stalled": False}, "roots": [self._entry("r-ext")]}

    def root_entry(self, root_id: str) -> dict | None:
        return self._entry(root_id) if root_id in ("r-ext", "r-managed", "r-new") else None

    def roots_changed(self) -> None:
        self.calls.append(("roots_changed",))

    def scan_now(self, admin_id: str, root_id: str | None = None) -> dict:
        self.calls.append(("scan_now", admin_id, root_id))
        return self.scan_result

    def submit(self, run_id: str) -> None:
        self.calls.append(("submit", run_id))

    def diagnostics(self) -> dict:
        return self.diag


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAutomation:
    service = FakeAutomation()
    monkeypatch.setattr(admin_library_automation, "automation_service", lambda: service)
    return service


@pytest.fixture
def seeded(factory) -> None:
    with factory.begin() as db:
        db.add(StorageRoot(id="r-ext", label="TV", path="/mnt/nas/tv", mode="external"))
        db.add(StorageRoot(id="r-new", label="New", path="/mnt/nas/new", mode="external"))
        db.add(StorageRoot(id="r-managed", label="Managed", path="/srv/managed", mode="managed"))
        db.add(ImportRun(id="run1", root_id="r-ext", user_id="u1", state="succeeded"))


URL = "/api/admin/library/automation"


def hdr(csrf: str) -> dict[str, str]:
    return {"Origin": settings.allowed_origins_list[0], "X-CSRF-Token": csrf}


def test_every_route_is_admin_only(client: TestClient, factory, seeded) -> None:
    calls = [("get", URL, None), ("patch", URL, {"night_hour": 4}), ("patch", URL + "/roots/r-ext", {"watch": True}), ("post", URL + "/scan", {})]

    def send(method: str, path: str, body, headers=None):
        return client.request(method, path, json=body, headers=headers or {})

    for method, path, body in calls:
        assert send(method, path, body).status_code == 401, (method, path)
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    csrf = login(client, "member")
    for method, path, body in calls:
        assert send(method, path, body, hdr(csrf)).status_code == 403, (method, path)


def test_get_returns_the_snapshot_without_paths(client: TestClient, factory, seeded, fake: FakeAutomation) -> None:
    login(client)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == fake.snapshot()
    assert "/mnt/nas" not in response.text


def test_patch_root_saves_and_notifies(client: TestClient, factory, seeded, fake: FakeAutomation) -> None:
    csrf = login(client)
    response = client.patch(URL + "/roots/r-ext", json={"schedule": "nightly", "watch": True, "watch_interval_s": 900}, headers=hdr(csrf))
    assert response.status_code == 200, response.text
    with factory() as db:
        root = db.get(StorageRoot, "r-ext")
        assert (root.scan_schedule, root.watch_enabled, root.watch_interval_s) == ("nightly", True, 900)
    assert fake.calls == [("roots_changed",)]
    assert client.patch(URL + "/roots/r-ext", json={"watch": False}, headers=hdr(csrf)).status_code == 200
    with factory() as db:
        root = db.get(StorageRoot, "r-ext")
        assert (root.scan_schedule, root.watch_enabled, root.watch_interval_s) == ("nightly", False, 900)


def test_patch_root_validation(client: TestClient, factory, seeded, fake: FakeAutomation) -> None:
    csrf = login(client)
    patch = lambda root, body: client.patch(f"{URL}/roots/{root}", json=body, headers=hdr(csrf))  # noqa: E731
    assert patch("r-ext", {"schedule": "weekly"}).status_code == 422
    assert patch("r-ext", {"watch_interval_s": 120}).status_code == 422
    assert patch("nope", {"watch": True}).status_code == 404
    assert patch("r-managed", {"watch": True}).status_code == 409
    never = patch("r-new", {"watch": True})
    assert never.status_code == 409 and never.json()["detail"] == "Import this folder once before scheduling it"
    assert fake.calls == []


def test_patch_night_hour(client: TestClient, factory, seeded) -> None:
    csrf = login(client)
    assert client.patch(URL, json={"night_hour": 4}, headers=hdr(csrf)).status_code == 200
    with factory() as db:
        assert db.get(AppSettings, 1).library_scan_night_hour == 4
    for bad in (24, -1):
        assert client.patch(URL, json={"night_hour": bad}, headers=hdr(csrf)).status_code == 422


def test_scan_all_and_one(client: TestClient, factory, seeded, fake: FakeAutomation) -> None:
    csrf = login(client)
    fake.scan_result = {"started": [{"root_id": "r-ext", "run_id": "run2"}], "queued": ["r-new"], "skipped": [{"root_id": "r-managed", "reason": "managed"}]}
    for kwargs in ({}, {"json": {}}):
        response = client.post(URL + "/scan", headers=hdr(csrf), **kwargs)
        assert response.status_code == 202 and response.json() == fake.scan_result
        assert fake.calls[-1] == ("scan_now", "u1", None)
    assert client.post(URL + "/scan", json={"root_id": "r-ext"}, headers=hdr(csrf)).status_code == 202
    assert fake.calls[-1] == ("scan_now", "u1", "r-ext")
    count = len(fake.calls)
    assert client.post(URL + "/scan", json={"root_id": "nope"}, headers=hdr(csrf)).status_code == 404
    assert len(fake.calls) == count


def test_patch_root_deleted_before_the_read_is_a_404(client: TestClient, seeded, fake: FakeAutomation, monkeypatch) -> None:
    csrf = login(client)
    monkeypatch.setattr(fake, "root_entry", lambda root_id: None)
    assert client.patch(URL + "/roots/r-ext", json={"watch": True}, headers=hdr(csrf)).status_code == 404
