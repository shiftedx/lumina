"""The Admin Tasks `import` kind (list, counts, cancel, retry). S2's service is FakeAutomation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import ImportRun, StorageRoot, User
from app.security import hash_password
from test_admin_library_automation_api import FakeAutomation, fake, hdr  # noqa: F401  (fixtures)
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

T0 = datetime(2026, 1, 1)
URL = "/api/admin/tasks"
SCOPE2 = [{"dir": "A", "deep": True}, {"dir": "B", "deep": False}]


@pytest.fixture
def seeded(factory) -> None:
    runs = [
        dict(id="i-full", state="succeeded"),
        dict(id="i-scoped", state="succeeded", trigger="watch", scope=SCOPE2),
        dict(id="i-sched", state="running", trigger="scheduled"),
        dict(id="i-held", state="needs_confirmation"),
        dict(id="i-fail", state="failed", error="offline"),
        dict(id="i-int", state="interrupted", scope=[{"dir": "A", "deep": True}]),
        dict(id="i-cancel", state="cancelled"),
    ]
    with factory.begin() as db:
        db.add(StorageRoot(id="r-ext", label="TV", path="/mnt/nas/tv", mode="external"))
        db.add(StorageRoot(id="r-other", label="Movies", path="/mnt/nas/mov", mode="external"))
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
        for n, run in enumerate(runs):
            db.add(ImportRun(root_id="r-other" if run["id"] == "i-cancel" else "r-ext", user_id="u1", created_at=T0 + timedelta(seconds=n), **run))


def ids(response) -> set[str]:
    return {item["id"] for item in response.json()["items"]}


def test_import_kind_rows(client: TestClient, seeded) -> None:
    login(client)
    body = client.get(URL, params={"kind": "import"}).json()
    assert [i["id"] for i in body["items"]] == ["i-cancel", "i-int", "i-fail", "i-held", "i-sched", "i-scoped", "i-full"]
    by = {i["id"]: i for i in body["items"]}
    scoped = by["i-scoped"]
    assert (scoped["title"], scoped["detail"], scoped["status"], scoped["attempts"], scoped["library_item_id"]) == ("TV · 2 folders", "Watched folder", "succeeded", 0, None)
    assert scoped["can_cancel"] is False and scoped["can_retry"] is False
    assert (by["i-full"]["title"], by["i-full"]["detail"]) == ("TV · full scan", "Manual")
    assert by["i-sched"]["detail"] == "Scheduled" and by["i-sched"]["can_cancel"] is True
    assert by["i-fail"]["error"] == "offline" and by["i-fail"]["can_retry"] is True
    assert by["i-held"]["can_cancel"] is False and by["i-held"]["can_retry"] is False
    assert by["i-int"]["title"] == "TV · 1 folder"


def test_import_status_groups_and_counts(client: TestClient, seeded) -> None:
    login(client)
    get = lambda status: client.get(URL, params={"kind": "import", "status": status})  # noqa: E731
    assert ids(get("active")) == {"i-sched", "i-held"}
    assert ids(get("failed")) == {"i-fail", "i-int", "i-cancel"}
    assert ids(get("finished")) == {"i-full", "i-scoped"}
    counts = get("all").json()["counts"]
    assert counts["import"] == {"succeeded": 2, "running": 1, "needs_confirmation": 1, "failed": 1, "interrupted": 1, "cancelled": 1}
    assert {"download", "asr", "summary"} <= set(counts)


def test_import_paging(client: TestClient, seeded) -> None:
    login(client)
    seen, cursor = [], None
    while True:
        params = {"kind": "import", "limit": 3, **({"cursor": cursor} if cursor else {})}
        page = client.get(URL, params=params).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 7 and len(set(seen)) == 7


def test_cancel_import(client: TestClient, factory, seeded) -> None:
    csrf = login(client)
    assert client.post(f"{URL}/import/i-sched/cancel", headers=hdr(csrf)).status_code == 200
    with factory() as db:
        assert db.get(ImportRun, "i-sched").state == "cancel_requested"
    assert client.post(f"{URL}/import/i-full/cancel", headers=hdr(csrf)).status_code == 409
    assert client.post(f"{URL}/import/nope/cancel", headers=hdr(csrf)).status_code == 404


def test_retry_import_resumes_and_submits(client: TestClient, factory, seeded, fake: FakeAutomation) -> None:
    csrf = login(client)
    with factory.begin() as db:  # the seeded running and held runs would block any resume of r-ext
        for run_id in ("i-sched", "i-held"):
            db.get(ImportRun, run_id).state = "succeeded"
    # I1: i-int is newer than i-fail on r-ext, so i-fail's stamps are stale and only i-int may resume.
    stale = client.post(f"{URL}/import/i-fail/retry", headers=hdr(csrf))
    assert stale.status_code == 409 and stale.json()["detail"] == "A newer import ran for this folder. Rescan instead."
    response = client.post(f"{URL}/import/i-int/retry", headers=hdr(csrf))
    assert response.status_code == 200, response.text
    with factory() as db:
        run = db.get(ImportRun, "i-int")
        assert (run.state, run.error) == ("running", None)
        assert db.get(ImportRun, "i-fail").state == "failed"
    assert ("submit", "i-int") in fake.calls
    assert client.post(f"{URL}/import/i-full/retry", headers=hdr(csrf)).status_code == 409
    assert client.post(f"{URL}/import/nope/retry", headers=hdr(csrf)).status_code == 404
    # i-cancel is the newest run of r-other, so it resumes: one active run per root, not per process.
    assert client.post(f"{URL}/import/i-cancel/retry", headers=hdr(csrf)).status_code == 200


def test_old_download_retry_path_unchanged(client: TestClient, seeded) -> None:
    csrf = login(client)
    assert client.post(f"{URL}/download/d-missing/retry", headers=hdr(csrf)).status_code == 404
    assert client.post(f"{URL}/asr/x/retry", headers=hdr(csrf)).status_code == 422


def test_import_tasks_admin_only(client: TestClient, seeded) -> None:
    csrf = login(client, "member")
    assert client.get(URL, params={"kind": "import"}).status_code == 403
    assert client.post(f"{URL}/import/i-sched/cancel", headers=hdr(csrf)).status_code == 403
    assert client.post(f"{URL}/import/i-fail/retry", headers=hdr(csrf)).status_code == 403
