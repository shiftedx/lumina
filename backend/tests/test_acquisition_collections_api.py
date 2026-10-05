from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import main as main_module
from app.models import LibraryItem
from app.schemas import MediaSourceCapabilities, PreviewResponse
from app.services import job_manager as job_manager_module
from app.services.acquisition_batch import RedactedDispatchFailure
from app.services.job_manager import JobManager
from app.services.yt_dlp_service import YtDlpService
from support import make_user


@pytest.fixture
def phase_three_api(monkeypatch, db_factory, api_client):  # noqa: ANN001
    sessions = db_factory
    owner, member = make_user("owner", role="admin"), make_user("member")
    with sessions.begin() as db:
        db.add_all([owner, member])

    actor = {"user": owner}
    events = Mock()
    manager = JobManager(events)
    manager.acquisition.session_factory = sessions
    monkeypatch.setattr(main_module, "jobs", manager)
    # create_acquisition_batch reinspects the container source exactly once; stub
    # that extraction so these mechanics tests stay offline. Empty container
    # entries leave every selection to the worker backstop, preserving URLs.
    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda _self, source_url, *_args, **_kwargs: PreviewResponse(
            kind="playlist", webpage_url=source_url,
            capabilities=MediaSourceCapabilities(provider="generic", lifecycle="vod", can_play=False, can_acquire=True),
            entries=[], raw={"_type": "playlist"},
        ),
    )
    # The single-entry retry path still reinspects through this gate.
    monkeypatch.setattr(
        main_module,
        "_require_source_acquisition_capability",
        lambda _db, source_url, *_args: source_url,
    )
    monkeypatch.setattr(job_manager_module, "SessionLocal", sessions)
    client = api_client(user=lambda: actor["user"], base_url="http://localhost", raise_server_exceptions=False)
    return client, sessions, manager, actor, owner, member


def _batch_payload() -> dict:
    return {
        "source_url": "https://example.com/playlist",
        "source_title": "Household picks",
        "source_provenance": {
            "extractor": "example",
            "playlist_id": "playlist-one",
            "playlist_title": "Provider playlist title",
            "thumbnail": "https://images.example.com/playlist.jpg",
        },
        "format_selection": {"preset": "best_1080p", "subtitles": False},
        "output_profile": {"subdir": "family/picks", "organize_by": "playlist"},
        "entries": [
            {"source_url": "https://example.com/one", "extractor": "example", "remote_id": "one", "title": "One", "duration": 20},
            {"source_url": "https://example.com/one", "extractor": "example", "remote_id": "one", "title": "One duplicate"},
            {"source_url": "https://example.com/fail", "extractor": "example", "remote_id": "fail", "title": "Fails"},
        ],
    }


def test_acquisition_batch_api_reports_partial_duplicate_progress_retry_and_member_isolation(phase_three_api) -> None:  # noqa: ANN001
    client, sessions, manager, actor, _owner, member = phase_three_api
    original_dispatch = manager.acquisition.ensure_dispatched

    def partial_dispatch(**options):  # noqa: ANN003, ANN202
        if options["source_url"].endswith("/fail"):
            raise RedactedDispatchFailure("source_unavailable", "The source is unavailable.")
        return original_dispatch(**options)

    manager.acquisition.ensure_dispatched = partial_dispatch  # type: ignore[method-assign]
    response = client.post("/api/acquisition-batches", json=_batch_payload())
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "partial"
    assert (batch["queued_count"], batch["duplicate_count"], batch["failed_count"]) == (1, 1, 1)
    assert [entry["status"] for entry in batch["entries"]] == ["queued", "duplicate", "failed"]
    assert {key: value for key, value in batch["source_provenance"].items() if value is not None} == _batch_payload()["source_provenance"]
    assert batch["output_profile"]["subdir"] == "family/picks"
    assert batch["format_selection"]["preset"] == "best_1080p"

    queued = batch["entries"][0]
    assert manager.acquisition.observe(queued["download_job_id"], "running", progress=47)
    detail = client.get(f"/api/acquisition-batches/{batch['id']}")
    assert detail.status_code == 200
    assert detail.json()["entries"][0]["progress"] == 47
    listed = client.get("/api/acquisition-batches")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == batch["id"]
    assert listed.json()[0]["entries"] == []
    with_entries = client.get("/api/acquisition-batches", params={"limit": 1, "include_entries": True}).json()
    assert [entry["id"] for entry in with_entries[0]["entries"]] == [entry["id"] for entry in detail.json()["entries"]]
    assert with_entries[0]["entries"][0]["download_job_id"] == queued["download_job_id"]
    assert client.get("/api/acquisition-batches", params={"limit": 51}).status_code == 422

    manager.acquisition.ensure_dispatched = original_dispatch  # type: ignore[method-assign]
    failed = batch["entries"][2]
    retried = client.post(f"/api/acquisition-batches/{batch['id']}/entries/{failed['id']}/retry")
    assert retried.status_code == 201
    retry_batch = retried.json()
    assert retry_batch["id"] != batch["id"]
    assert retry_batch["entries"][0]["id"] != failed["id"]
    assert retry_batch["entries"][0]["download_job_id"] != failed["download_job_id"]
    assert retry_batch["entries"][0]["status"] == "queued"

    actor["user"] = member
    assert client.get("/api/acquisition-batches").json() == []
    assert client.get(f"/api/acquisition-batches/{batch['id']}").status_code == 404
    assert client.post(f"/api/acquisition-batches/{batch['id']}/entries/{failed['id']}/retry").status_code == 404


def test_household_collection_api_enforces_visibility_membership_and_owner_mutation(phase_three_api) -> None:  # noqa: ANN001
    client, sessions, _manager, actor, owner, member = phase_three_api
    with sessions.begin() as db:
        db.add_all([
            LibraryItem(id="owner-private", user_id=owner.id, visibility="private", title="Owner private"),
            LibraryItem(id="owner-shared", user_id=owner.id, visibility="shared", title="Owner shared"),
            LibraryItem(id="member-private", user_id=member.id, visibility="private", title="Member private"),
        ])

    private = client.post("/api/collections", json={"name": "Private picks", "visibility": "private"})
    shared = client.post("/api/collections", json={"name": "Household picks", "description": "Mixed sources", "visibility": "shared"})
    assert (private.status_code, shared.status_code) == (201, 201)
    private_id, shared_id = private.json()["id"], shared.json()["id"]

    assert client.post(f"/api/collections/{shared_id}/items/owner-private").status_code == 200
    added_shared = client.post(f"/api/collections/{shared_id}/items/owner-shared")
    assert added_shared.status_code == 200
    assert {item["id"] for item in added_shared.json()["items"]} == {"owner-private", "owner-shared"}
    entry_ids = {entry["ref"]["library_item_id"]: entry["id"] for entry in added_shared.json()["entries"]}
    assert client.post(f"/api/collections/{shared_id}/items/owner-shared").status_code == 409
    assert client.post(f"/api/collections/{shared_id}/items/member-private").status_code == 404

    renamed = client.put(f"/api/collections/{shared_id}/name", json={"name": "Family favorites"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Family favorites"
    assert client.post("/api/collections", json={"name": " family FAVORITES "}).status_code == 409

    actor["user"] = member
    visible = client.get("/api/collections")
    assert visible.status_code == 200
    assert [collection["id"] for collection in visible.json()] == [shared_id]
    assert visible.json()[0]["item_count"] == 1
    shared_detail = client.get(f"/api/collections/{shared_id}")
    assert shared_detail.status_code == 200
    assert [item["id"] for item in shared_detail.json()["items"]] == ["owner-shared"]
    assert shared_detail.json()["item_count"] == 1
    assert client.get(f"/api/collections/{private_id}").status_code == 404
    assert client.put(f"/api/collections/{shared_id}/name", json={"name": "Not mine"}).status_code == 403
    assert client.put(f"/api/collections/{shared_id}/visibility", json={"visibility": "private"}).status_code == 403
    assert client.post(f"/api/collections/{shared_id}/items/owner-shared").status_code == 403
    assert client.delete(f"/api/collections/{shared_id}/entries/{entry_ids['owner-shared']}").status_code == 403
    assert client.delete(f"/api/collections/{shared_id}").status_code == 403

    own_same_name = client.post("/api/collections", json={"name": "Family favorites", "visibility": "private"})
    assert own_same_name.status_code == 201

    actor["user"] = owner
    removed = client.delete(f"/api/collections/{shared_id}/entries/{entry_ids['owner-private']}")
    assert removed.status_code == 200
    assert [item["id"] for item in removed.json()["items"]] == ["owner-shared"]
    hidden = client.put(f"/api/collections/{shared_id}/visibility", json={"visibility": "private"})
    assert hidden.status_code == 200
    assert hidden.json()["visibility"] == "private"
    assert client.delete(f"/api/collections/{shared_id}").status_code == 204
    assert client.get(f"/api/collections/{shared_id}").status_code == 404
