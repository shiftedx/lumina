import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import DownloadJob, LibraryItem, WatchQueueEntry
from app.services import watch_queue
from support import make_user

URL = "/api/me/watch-queue"


def remote(n: int, title: str | None = None) -> dict:
    return {"kind": "remote", "provider": "youtube", "remote_id": f"v{n}", "url": f"https://www.youtube.com/watch?v=v{n}", "title": title or f"Video {n}"}


@pytest.fixture
def env(db_factory, api_client):
    alice, bob = make_user("alice"), make_user("bob")
    with db_factory.begin() as db:
        db.add_all([alice, bob, LibraryItem(id="secret", user_id="bob", visibility="shared", title="Bob private title", metadata_json={}, status="available")])
    current = {"user": alice}
    api_client(user=lambda: current["user"])  # installs the db/user overrides for the TestClients below
    return db_factory, current, alice, bob


def titles(body: dict) -> list:
    return [entry["title"] for entry in body["entries"]]


def test_queue_order_reload_multiuser(env) -> None:
    _, current, alice, bob = env
    phone, laptop = TestClient(app, base_url="http://localhost"), TestClient(app, base_url="http://localhost")
    for n in (1, 2, 3):
        phone.post(f"{URL}/entries", json={"ref": remote(n)})
    body = phone.post(f"{URL}/entries", json={"ref": remote(4), "position": "next"}).json()
    assert titles(body) == ["Video 4", "Video 1", "Video 2", "Video 3"]
    # Re-adding is idempotent; play-next of an existing entry moves it to the front.
    assert titles(phone.post(f"{URL}/entries", json={"ref": remote(2)}).json()) == titles(body)
    body = phone.post(f"{URL}/entries", json={"ref": remote(3), "position": "next"}).json()
    moved = laptop.patch(f"{URL}/entries/{body['entries'][0]['id']}", json={"position": 3, "expected_revision": body["revision"]}).json()
    assert titles(moved) == ["Video 4", "Video 1", "Video 2", "Video 3"]
    assert [entry["position"] for entry in moved["entries"]] == [0, 1, 2, 3]

    current["user"] = bob
    assert laptop.get(URL).json()["entries"] == []
    laptop.post(f"{URL}/entries", json={"ref": {"kind": "library", "library_item_id": "secret"}})
    current["user"] = alice
    assert titles(TestClient(app, base_url="http://localhost").get(URL).json()) == ["Video 4", "Video 1", "Video 2", "Video 3"]
    removed = phone.delete(f"{URL}/entries/{moved['entries'][1]['id']}").json()
    assert titles(removed) == ["Video 4", "Video 2", "Video 3"]
    assert phone.delete(URL).status_code == 422  # clear needs explicit confirmation
    assert phone.delete(URL, params={"confirm": "true"}).json()["entries"] == []
    current["user"] = bob
    assert titles(laptop.get(URL).json()) == ["Bob private title"]


def test_queue_append_not_download(env) -> None:
    factory, *_ = env
    client = TestClient(app, base_url="http://localhost")
    assert client.post(f"{URL}/entries", json={"ref": remote(1)}).status_code == 200
    assert client.post(f"{URL}/entries", json={"ref": {**remote(2), "url": "javascript:alert(1)"}}).status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DownloadJob)) == 0


def test_queue_revision_conflict(env) -> None:
    client = TestClient(app, base_url="http://localhost")
    client.post(f"{URL}/entries", json={"ref": remote(1)})
    body = client.post(f"{URL}/entries", json={"ref": remote(2)}).json()
    first, second = body["entries"]
    assert client.patch(f"{URL}/entries/{second['id']}", json={"position": 0, "expected_revision": body["revision"]}).status_code == 200
    stale = client.patch(f"{URL}/entries/{first['id']}", json={"position": 1, "expected_revision": body["revision"]})
    assert stale.status_code == 409
    # The losing reorder did not apply; the refetch shows only the winner's order.
    assert titles(client.get(URL).json()) == ["Video 2", "Video 1"]


def test_queue_revoked_item(env) -> None:
    factory, current, alice, bob = env
    client = TestClient(app, base_url="http://localhost")
    assert client.post(f"{URL}/entries", json={"ref": {"kind": "library", "library_item_id": "secret"}}).status_code == 200
    with factory.begin() as db:
        db.get(LibraryItem, "secret").visibility = "private"
    entry = client.get(URL).json()["entries"][0]
    assert entry["availability"] == "unavailable"
    assert entry["title"] is None and entry["ref"]["library_item_id"] is None
    assert "Bob private title" not in client.get(URL).text
    # A hidden item cannot be (re)queued, and the tombstone can still be removed.
    assert client.post(f"{URL}/entries", json={"ref": {"kind": "library", "library_item_id": "secret"}}).status_code == 404
    assert client.delete(f"{URL}/entries/{entry['id']}").json()["entries"] == []


def test_queue_is_bounded(env, monkeypatch) -> None:
    factory, *_ = env
    monkeypatch.setattr(watch_queue, "MAX_ENTRIES", 2)
    client = TestClient(app, base_url="http://localhost")
    client.post(f"{URL}/entries", json={"ref": remote(1)})
    client.post(f"{URL}/entries", json={"ref": remote(2)})
    assert client.post(f"{URL}/entries", json={"ref": remote(3)}).status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WatchQueueEntry)) == 2
