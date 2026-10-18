"""Public admin boundary for durable, allowlisted arr controls."""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from arr_fake import API_KEY, FakeArr
from support import make_user

from app.models import ArrOperation, ArrServer, User


@pytest.fixture
def controls(db_factory, api_client):  # noqa: ANN001, ANN201
    fake = FakeArr("sonarr")
    fake.library[101] = {
        "id": 101, "title": "Example", "monitored": False, "qualityProfileId": 1,
        "rootFolderPath": "/tv/Shows", "tags": [9],
        "seasons": [{"seasonNumber": 0, "monitored": False}, {"seasonNumber": 1, "monitored": False}],
    }
    fake.releases[101] = [{
        "guid": "release-guid", "indexerId": 2, "title": "Example S01E01 1080p", "approved": False,
        "quality": {"quality": {"name": "WEBDL-1080p"}}, "languages": [{"name": "English"}],
        "rejections": ["Custom format"], "size": 123456, "downloadUrl": "https://private.example/release",
    }]
    with db_factory() as db:
        db.add_all([make_user("admin", role="admin"), make_user("alice"), ArrServer(
            id="sonarr", kind="sonarr", name="Sonarr", base_url=fake.url, api_key=API_KEY, enabled=True,
        )])
        db.commit()
    current = {"id": "admin"}

    def who() -> User:
        with db_factory() as db:
            return db.get(User, current["id"])

    try:
        yield api_client(user=who, base_url="http://localhost"), fake, current, db_factory
    finally:
        fake.close()


def _item(client):  # noqa: ANN001, ANN202
    response = client.get("/api/admin/arr/servers/sonarr/items/101")
    assert response.status_code == 200, response.text
    return response.json()


def _operation(client, revision: str, action: str, **extra):  # noqa: ANN001, ANN202
    body = {"operation_id": str(uuid.uuid4()), "base_revision": revision, "action": action, **extra}
    response = client.post("/api/admin/arr/servers/sonarr/items/101/operations", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def test_admin_reads_sanitized_releases_and_preserves_upstream_fields_on_monitor_and_profile(controls) -> None:  # noqa: ANN001
    client, fake, _, _ = controls
    before = _item(client)
    assert before["profiles"] == [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}]
    releases = client.get("/api/admin/arr/servers/sonarr/items/101/releases")
    assert releases.status_code == 200
    [release] = releases.json()["items"]
    assert release == {
        "release_id": hashlib.sha256(json.dumps(["release-guid", 2], separators=(",", ":")).encode()).hexdigest(), "title": "Example S01E01 1080p",
        "quality": "WEBDL-1080p", "languages": ["English"], "rejections": ["Custom format"],
        "approved": False, "size": 123456,
    }
    assert "private.example" not in releases.text and "guid" not in releases.text
    _, refused = _operation(client, before["revision"], "grab", release_id=release["release_id"])
    assert refused["status"] == "failed" and fake.grabbed == []

    _, monitored = _operation(client, before["revision"], "monitor", monitored=True)
    assert monitored["status"] == "succeeded" and fake.library[101]["monitored"] is True
    assert fake.library[101]["rootFolderPath"] == "/tv/Shows" and fake.library[101]["tags"] == [9]
    _, profile = _operation(client, monitored["result"]["state"]["revision"], "profile", profile_id=4)
    assert profile["status"] == "succeeded" and fake.library[101]["qualityProfileId"] == 4


def test_operations_enforce_admin_permission_stale_revision_seasons_and_command_allowlist(controls) -> None:  # noqa: ANN001
    client, fake, current, _ = controls
    current["id"] = "alice"
    assert client.get("/api/admin/arr/servers/sonarr/items/101/releases").status_code == 403
    assert client.post("/api/admin/arr/servers/sonarr/items/101/operations", json={
        "operation_id": str(uuid.uuid4()), "base_revision": "0" * 64, "action": "search"}).status_code == 403
    current["id"] = "admin"
    item = _item(client)
    _, stale = _operation(client, "0" * 64, "monitor", monitored=True)
    assert stale["status"] == "conflict" and fake.library[101]["monitored"] is False
    _, season = _operation(client, item["revision"], "monitor", monitored=True, season_number=1)
    assert season["status"] == "succeeded" and fake.library[101]["seasons"][1]["monitored"] is True
    _, search = _operation(client, season["result"]["state"]["revision"], "search", season_number=1)
    _, rescan = _operation(client, search["result"]["state"]["revision"], "rescan")
    assert search["status"] == rescan["status"] == "succeeded"
    assert fake.commands == [
        {"name": "SeasonSearch", "seriesId": 101, "seasonNumber": 1}, {"name": "RescanSeries", "seriesId": 101},
    ]
    invalid = client.post("/api/admin/arr/servers/sonarr/items/101/operations", json={
        "operation_id": str(uuid.uuid4()), "base_revision": item["revision"], "action": "rescan", "profile_id": 4,
    })
    assert invalid.status_code == 422


def test_grab_refetches_opaque_release_and_uncertain_uuid_never_redispatches(controls, monkeypatch) -> None:  # noqa: ANN001
    client, fake, _, db_factory = controls
    item = _item(client)
    release_id = hashlib.sha256(json.dumps(["release-guid", 2], separators=(",", ":")).encode()).hexdigest()
    fake.releases[101][0]["approved"] = True
    fake.releases[101][0]["rejections"] = []
    _, grabbed = _operation(client, item["revision"], "grab", release_id=release_id)
    assert grabbed["status"] == "succeeded" and fake.grabbed[0]["guid"] == "release-guid"
    assert "rejections" not in fake.grabbed[0]  # only documented, allowlisted fields crossed the boundary
    assert "downloadUrl" not in grabbed["result"]

    revision = grabbed["result"]["state"]["revision"]
    body = {"operation_id": str(uuid.uuid4()), "base_revision": revision, "action": "monitor", "monitored": True}
    route, wrote = fake.route, {"value": False}

    def fail_reread(method, path, query, payload):  # noqa: ANN001, ANN202
        if wrote["value"] and (method, path) == ("GET", "series/101"):
            return 503, None
        status, reply = route(method, path, query, payload)
        if (method, path) == ("PUT", "series/101"):
            wrote["value"] = True
        return status, reply

    monkeypatch.setattr(fake, "route", fail_reread)
    first = client.post("/api/admin/arr/servers/sonarr/items/101/operations", json=body)
    assert first.status_code == 200 and first.json()["status"] == "uncertain"
    second = client.post("/api/admin/arr/servers/sonarr/items/101/operations", json=body)
    assert second.json()["id"] == first.json()["id"] and second.json()["status"] == "uncertain"
    assert fake.library[101]["monitored"] is True
    assert len([request for request in fake.requests if request[:2] == ("PUT", "/api/v3/series/101")]) == 1
    other = client.post("/api/admin/arr/servers/sonarr/items/101/operations", json={**body, "operation_id": str(uuid.uuid4())})
    assert other.status_code == 409
    with db_factory() as db:
        row = db.get(ArrOperation, body["operation_id"])
        assert row and row.status == "uncertain"
        db.get(ArrServer, "sonarr").base_url = "http://127.0.0.1:1"
        db.commit()
    reconciled = client.post(f"/api/admin/arr/operations/{first.json()['id']}/reconcile")
    assert reconciled.json()["status"] == "uncertain" and reconciled.json()["error"] == "Connection settings changed"


def test_operation_listing_and_read_only_reconcile(controls) -> None:  # noqa: ANN001
    client, fake, _, _ = controls
    item = _item(client)
    body, outcome = _operation(client, item["revision"], "search")
    listed = client.get("/api/admin/arr/operations?page_size=1")
    assert listed.status_code == 200 and listed.json()["total"] == 1 and listed.json()["items"][0]["id"] == outcome["id"]
    reconciled = client.post(f"/api/admin/arr/operations/{outcome['id']}/reconcile")
    assert reconciled.status_code == 200 and reconciled.json()["status"] == "succeeded"
    assert len(fake.commands) == 1 and body["operation_id"] == outcome["id"]


def test_radarr_movie_monitor_search_and_rescan_use_movie_commands(db_factory, api_client) -> None:  # noqa: ANN001
    fake = FakeArr("radarr")
    fake.library[7] = {"id": 7, "title": "Movie", "monitored": True, "qualityProfileId": 1, "rootFolderPath": "/movies"}
    with db_factory() as db:
        db.add_all([make_user("admin", role="admin"), ArrServer(
            id="radarr", kind="radarr", name="Radarr", base_url=fake.url, api_key=API_KEY, enabled=True,
        )])
        db.commit()
    try:
        def who() -> User:
            with db_factory() as db:
                return db.get(User, "admin")

        client = api_client(user=who, base_url="http://localhost")
        before = client.get("/api/admin/arr/servers/radarr/items/7").json()
        first = client.post("/api/admin/arr/servers/radarr/items/7/operations", json={
            "operation_id": str(uuid.uuid4()), "base_revision": before["revision"], "action": "monitor", "monitored": False,
        }).json()
        assert first["status"] == "succeeded" and fake.library[7]["monitored"] is False
        for action in ("search", "rescan"):
            response = client.post("/api/admin/arr/servers/radarr/items/7/operations", json={
                "operation_id": str(uuid.uuid4()), "base_revision": first["result"]["state"]["revision"], "action": action,
            })
            assert response.status_code == 200 and response.json()["status"] == "succeeded"
        assert fake.commands == [{"name": "MoviesSearch", "movieIds": [7]}, {"name": "RescanMovie", "movieId": 7}]
    finally:
        fake.close()


def test_release_selection_preserves_distinct_guid_indexer_pairs(controls) -> None:  # noqa: ANN001
    client, fake, _, _ = controls
    fake.releases[101] = [
        {"guid": "a", "indexerId": 12, "title": "First", "approved": True, "rejections": []},
        {"guid": "a1", "indexerId": 2, "title": "Second", "approved": True, "rejections": []},
    ]
    releases = client.get("/api/admin/arr/servers/sonarr/items/101/releases").json()["items"]
    assert releases[0]["release_id"] != releases[1]["release_id"]
    _, operation = _operation(client, _item(client)["revision"], "grab", release_id=releases[1]["release_id"])
    assert operation["status"] == "succeeded"
    assert fake.grabbed[0]["guid"] == "a1" and fake.grabbed[0]["indexerId"] == 2
