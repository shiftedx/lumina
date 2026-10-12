"""Mixed-source collections with explicit watch-vs-save actions.

Reuses the acquisition-batch harness from test_acquisition_collections_api (the
same admission path a "Save to vault" action drives) instead of duplicating it.
"""
from __future__ import annotations

from app.models import LibraryItem
from app.services.acquisition_batch import RedactedDispatchFailure
from tests.test_acquisition_collections_api import phase_three_api  # noqa: F401


def remote(n: int) -> dict:
    return {"provider": "youtube", "remote_id": f"v{n}", "url": f"https://example.com/v{n}", "title": f"Video {n}"}


def test_collection_mixed_ref_visibility(phase_three_api) -> None:  # noqa: ANN001
    client, sessions, _manager, actor, owner, member = phase_three_api
    with sessions.begin() as db:
        db.add_all([
            LibraryItem(id="owner-private", user_id=owner.id, visibility="private", title="Owner private"),
            LibraryItem(id="owner-shared", user_id=owner.id, visibility="shared", title="Owner shared"),
            LibraryItem(id="member-private", user_id=member.id, visibility="private", title="Member secret"),
        ])
    collection_id = client.post("/api/collections", json={"name": "Mixed picks", "visibility": "shared"}).json()["id"]
    assert client.post(f"/api/collections/{collection_id}/items/owner-private").status_code == 200
    assert client.post(f"/api/collections/{collection_id}/items/owner-shared").status_code == 200
    assert client.post(f"/api/collections/{collection_id}/remote-items", json=remote(1)).status_code == 201

    # The owner cannot add another member's private item into their own collection
    # by guessing its id: membership never grants access, so it 404s like a miss.
    assert client.post(f"/api/collections/{collection_id}/items/member-private").status_code == 404
    guessed = client.post("/api/collections/does-not-exist/items/owner-private")
    assert guessed.status_code == 404

    actor["user"] = member
    # And a member cannot view the owner's private item's title through the shared
    # collection: it tombstones (available=false, title=None) instead of leaking.
    detail = client.get(f"/api/collections/{collection_id}").json()
    private_entry = next(entry for entry in detail["entries"] if entry["ref"]["kind"] == "library" and entry["availability"] == "unavailable")
    assert private_entry["title"] is None
    assert "Owner private" not in client.get(f"/api/collections/{collection_id}").text
    shared_entry = next(entry for entry in detail["entries"] if entry["ref"].get("library_item_id") == "owner-shared")
    assert shared_entry["title"] == "Owner shared"
    remote_entry = next(entry for entry in detail["entries"] if entry["ref"]["kind"] == "remote")
    assert remote_entry["title"] == "Video 1"


def test_collection_reorder_persists(phase_three_api) -> None:  # noqa: ANN001
    client, *_ = phase_three_api
    collection_id = client.post("/api/collections", json={"name": "Queue order", "visibility": "private"}).json()["id"]
    for n in (1, 2, 3):
        body = client.post(f"/api/collections/{collection_id}/remote-items", json=remote(n)).json()
    titles = [entry["title"] for entry in body["entries"]]
    assert titles == ["Video 1", "Video 2", "Video 3"]

    first_id, revision = body["entries"][0]["id"], body["revision"]
    moved = client.patch(f"/api/collections/{collection_id}/entries/{first_id}", json={"position": 2, "expected_revision": revision})
    assert moved.status_code == 200
    assert [entry["title"] for entry in moved.json()["entries"]] == ["Video 2", "Video 3", "Video 1"]

    # Reload preserves the new order.
    reloaded = client.get(f"/api/collections/{collection_id}").json()
    assert [entry["title"] for entry in reloaded["entries"]] == ["Video 2", "Video 3", "Video 1"]
    assert [entry["position"] for entry in reloaded["entries"]] == [0, 1, 2]

    # A stale revision conflicts rather than silently overwriting the winner's order.
    stale = client.patch(f"/api/collections/{collection_id}/entries/{first_id}", json={"position": 0, "expected_revision": revision})
    assert stale.status_code == 409
    assert [entry["title"] for entry in client.get(f"/api/collections/{collection_id}").json()["entries"]] == ["Video 2", "Video 3", "Video 1"]


def test_collection_save_partial(phase_three_api) -> None:  # noqa: ANN001
    client, _sessions, manager, *_ = phase_three_api
    collection_id = client.post("/api/collections", json={"name": "To save", "visibility": "private"}).json()["id"]
    client.post(f"/api/collections/{collection_id}/remote-items", json={"provider": "example", "remote_id": "one", "url": "https://example.com/one", "title": "Keeper"})
    client.post(f"/api/collections/{collection_id}/remote-items", json={"provider": "example", "remote_id": "fail", "url": "https://example.com/fail", "title": "Fails"})
    entries = client.get(f"/api/collections/{collection_id}").json()["entries"]
    assert {entry["title"] for entry in entries} == {"Keeper", "Fails"}

    original_dispatch = manager.acquisition.ensure_dispatched

    def partial_dispatch(**options):  # noqa: ANN003, ANN202
        if options["source_url"].endswith("/fail"):
            raise RedactedDispatchFailure("source_unavailable", "The source is unavailable.")
        return original_dispatch(**options)

    manager.acquisition.ensure_dispatched = partial_dispatch  # type: ignore[method-assign]
    # "Save to vault" builds a batch from the collection's remote refs (S19 admission).
    payload = {
        "source_url": "https://example.com/one",
        "entries": [
            {"source_url": entry["ref"]["url"], "extractor": entry["ref"]["provider"], "remote_id": entry["ref"]["remote_id"], "title": entry["title"]}
            for entry in entries
        ],
    }
    response = client.post("/api/acquisition-batches", json=payload)
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "partial"
    assert (batch["queued_count"], batch["failed_count"]) == (1, 1)
    kept, failed = (batch["entries"][0], batch["entries"][1]) if batch["entries"][0]["status"] == "queued" else (batch["entries"][1], batch["entries"][0])
    assert kept["status"] == "queued"
    assert failed["status"] == "failed"

    # Retrying only the failed source leaves the successful one untouched, and the
    # collection's own membership is unaffected by Save to vault either way.
    manager.acquisition.ensure_dispatched = original_dispatch  # type: ignore[method-assign]
    retried = client.post(f"/api/acquisition-batches/{batch['id']}/entries/{failed['id']}/retry")
    assert retried.status_code == 201
    assert retried.json()["entries"][0]["status"] == "queued"
    assert client.get(f"/api/acquisition-batches/{batch['id']}").json()["entries"][0]["id"] == kept["id"]
    assert {entry["title"] for entry in client.get(f"/api/collections/{collection_id}").json()["entries"]} == {"Keeper", "Fails"}


def test_collection_remove_not_delete_file(phase_three_api) -> None:  # noqa: ANN001
    client, sessions, *_, owner, _member = phase_three_api
    with sessions.begin() as db:
        db.add(LibraryItem(id="saved", user_id=owner.id, visibility="private", title="Saved video", file_path="/vault/saved.mp4", file_size=1234))
    collection_id = client.post("/api/collections", json={"name": "Vault picks", "visibility": "private"}).json()["id"]
    added = client.post(f"/api/collections/{collection_id}/items/saved")
    assert added.status_code == 200
    entry_id = added.json()["entries"][0]["id"]

    removed = client.delete(f"/api/collections/{collection_id}/entries/{entry_id}")
    assert removed.status_code == 200
    assert removed.json()["entries"] == []
    # The artifact itself is untouched: file_path/file_size survive the membership removal.
    with sessions() as db:
        item = db.get(LibraryItem, "saved")
        assert item is not None
        assert item.file_path == "/vault/saved.mp4"
        assert item.file_size == 1234

    # Removing an already-removed entry is idempotent, not an error.
    assert client.delete(f"/api/collections/{collection_id}/entries/{entry_id}").status_code == 200
