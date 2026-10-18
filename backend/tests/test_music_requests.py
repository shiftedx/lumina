"""Public member/admin contract for explicitly approved Lidarr acquisitions."""
from __future__ import annotations

import uuid

import pytest

from lidarr_fake import API_KEY, FakeLidarr
from support import make_user

from app.models import (LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle,
                        MusicAcquisition, ServiceConnector, StorageRoot, User)

ARTIST = "11111111-1111-4111-8111-111111111111"
ALBUM = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def music(db_factory, api_client):  # noqa: ANN001, ANN201
    fake = FakeLidarr()
    fake.catalog_artists[ARTIST] = {"foreignArtistId": ARTIST, "artistName": "Canonical Artist"}
    fake.catalog_albums[ALBUM] = {"foreignAlbumId": ALBUM, "title": "Canonical Album", "artist": fake.catalog_artists[ARTIST]}
    with db_factory() as db:
        db.add_all([make_user("admin", role="admin"), make_user("alice"), make_user("bob"), ServiceConnector(
            id="lidarr", kind="lidarr", name="Lidarr", base_url=fake.url, api_key=API_KEY, enabled=True,
            settings={"root_folder": "/music", "quality_profile_id": 4, "metadata_profile_id": 8, "path_mappings": []},
        )])
        db.commit()
    current = {"id": "alice"}

    def who() -> User:
        with db_factory() as db:
            return db.get(User, current["id"])

    try:
        yield api_client(user=who, base_url="http://localhost"), fake, current, db_factory
    finally:
        fake.close()


def _body(request_id: str | None = None, **extra) -> dict:  # noqa: ANN001, ANN202
    return {"id": request_id or str(uuid.uuid4()), "connector_id": "lidarr", "foreign_artist_id": ARTIST,
            "foreign_album_id": ALBUM, "artist_name": "Untrusted artist", "album_name": "Untrusted album", **extra}


def test_member_request_is_idempotent_private_and_requires_admin_approval(music) -> None:  # noqa: ANN001
    client, fake, current, _ = music
    body = _body()
    created = client.post("/api/music/requests", json=body)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "pending_approval" and fake.requests == []
    assert client.post("/api/music/requests", json=body).json()["id"] == body["id"]
    assert client.post("/api/music/requests", json={**body, "album_name": "different"}).status_code == 409
    current["id"] = "bob"
    assert client.get("/api/music/requests").json()["items"] == []
    assert client.post(f"/api/admin/music/requests/{body['id']}/approve").status_code == 403
    assert client.get("/api/admin/lidarr/lidarr/inventory").status_code == 403
    assert client.post("/api/music/requests", json={**_body(), "unexpected": True}).status_code == 422


def test_admin_approval_uses_canonical_identity_safe_config_and_one_search(music) -> None:  # noqa: ANN001
    client, fake, current, _ = music
    body = _body()
    assert client.post("/api/music/requests", json=body).status_code == 200
    current["id"] = "admin"
    response = client.post(f"/api/admin/music/requests/{body['id']}/approve")
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["status"] == "awaiting_import", outcome
    assert outcome["artist_name"] == "Canonical Artist" and outcome["album_name"] == "Canonical Album"
    artist_write = next(body for method, path, body in fake.requests if (method, path) == ("POST", "/api/v1/artist"))
    assert artist_write["rootFolderPath"] == "/music" and artist_write["qualityProfileId"] == 4 and artist_write["metadataProfileId"] == 8
    assert fake.commands and fake.commands[next(iter(fake.commands))]["name"] == "AlbumSearch"
    assert client.post(f"/api/admin/music/requests/{body['id']}/approve").json()["status"] == "awaiting_import"
    assert len(fake.commands) == 1


def test_uncertain_write_never_redispatches_and_connection_change_stays_uncertain(music) -> None:  # noqa: ANN001
    client, fake, current, db_factory = music
    body = _body()
    client.post("/api/music/requests", json=body)
    current["id"] = "admin"

    def fail_after_monitor(method, path, query, payload):  # noqa: ANN001, ANN202
        if (method, path) == ("PUT", "album/monitor"):
            for album_id in payload["albumIds"]:
                fake.albums[album_id]["monitored"] = True
            return 503, None
        return None

    fake.route_hook = fail_after_monitor
    first = client.post(f"/api/admin/music/requests/{body['id']}/approve")
    assert first.status_code == 200 and first.json()["status"] == "uncertain"
    assert client.post(f"/api/admin/music/requests/{body['id']}/approve").json()["status"] == "uncertain"
    assert not fake.commands
    with db_factory() as db:
        db.get(ServiceConnector, "lidarr").base_url = "http://127.0.0.1:1"
        db.commit()
    result = client.post(f"/api/admin/music/requests/{body['id']}/reconcile").json()
    assert result["status"] == "uncertain" and result["error"] == "Connection settings changed"


def test_playable_needs_visible_linked_available_track(music) -> None:  # noqa: ANN001
    client, _, current, db_factory = music
    with db_factory() as db:
        db.add_all([MusicAcquisition(id=str(uuid.uuid4()), user_id="alice", connector_id="lidarr", foreign_artist_id=ARTIST,
                                     foreign_album_id=ALBUM, artist_name="Artist", album_name="Album", status="awaiting_import", args={}, result={}),
                    MusicAcquisition(id=str(uuid.uuid4()), user_id="bob", connector_id="lidarr", foreign_artist_id=ARTIST,
                                     foreign_album_id=ALBUM, artist_name="Artist", album_name="Album", status="awaiting_import", args={}, result={}),
                    MediaTitle(id="album-title", type="album", key="album:mb", name="Album", provider_ids={"MusicBrainz": "66666666-6666-4666-8666-666666666666"}, metadata_json={"release_group_id": ALBUM}),
                    StorageRoot(id="root", label="Music", path="/media/music", mode="external", enabled=True),
                    LibraryItem(id="track", user_id="alice", visibility="private", title="Track", kind="track", status="available", title_id="album-title"),
                    MediaArtifact(id="artifact", root_id="root", relative_path="track.flac", ownership="managed", lifecycle="available"),
                    LibraryItemArtifact(library_item_id="track", artifact_id="artifact")])
        db.commit()
    alice = client.get("/api/music/requests").json()["items"][0]
    assert alice["availability"] == {"state": "playable", "library_album_id": "album-title"}
    with db_factory() as db:
        db.get(StorageRoot, "root").enabled = False
        db.commit()
    assert client.get("/api/music/requests").json()["items"][0]["availability"] == {"state": "awaiting_import"}
    with db_factory() as db:
        db.get(StorageRoot, "root").enabled = True
        db.commit()
    current["id"] = "bob"
    bob = client.get("/api/music/requests").json()["items"][0]
    assert bob["availability"] == {"state": "awaiting_import"}


def test_admin_inventory_reads_paged_queue_history_and_redacts_source_details(music) -> None:  # noqa: ANN001
    client, fake, current, _ = music
    fake.artists[10] = {"id": 10, "foreignArtistId": ARTIST, "artistName": "Artist One", "monitored": True}
    fake.artists[11] = {"id": 11, "foreignArtistId": "44444444-4444-4444-8444-444444444444", "artistName": "Artist Two", "monitored": False}
    fake.albums[12] = {"id": 12, "foreignAlbumId": ALBUM, "title": "Album One", "artist": fake.artists[10], "monitored": True}
    fake.albums[13] = {"id": 13, "foreignAlbumId": "55555555-5555-4555-8555-555555555555", "title": "Album Two", "artist": fake.artists[11], "monitored": False}
    fake.queue = [{"id": i, "outputPath": "/private/file", "downloadUrl": "https://private.invalid"} for i in range(3)]
    fake.history = [{"id": i, "path": "/private/file"} for i in range(2)]
    current["id"] = "admin"
    response = client.get("/api/admin/lidarr/lidarr/inventory?page_size=1")
    assert response.status_code == 200, response.text
    assert response.json()["queue_count"] == 3 and response.json()["history_count"] == 2
    assert response.json()["total"] == 2 and len(response.json()["items"]) == 1 and response.json()["total_pages"] == 2
    assert "private.invalid" not in response.text and "/private/file" not in response.text


def test_reconcile_requires_recorded_completed_command(music) -> None:  # noqa: ANN001
    client, fake, current, db_factory = music
    artist = {"id": 42, "foreignArtistId": ARTIST, "artistName": "Canonical Artist"}
    album = {"id": 43, "foreignAlbumId": ALBUM, "title": "Canonical Album", "artist": artist, "monitored": True}
    fake.artists[42], fake.albums[43] = artist, album
    fake.commands[44] = {"id": 44, "status": "completed", "name": "AlbumSearch", "albumIds": [43]}
    with db_factory() as db:
        connector = db.get(ServiceConnector, "lidarr")
        from app.services.lidarr import _connection  # only creates the durable test fixture binding
        db.add(MusicAcquisition(id="33333333-3333-4333-8333-333333333333", user_id="alice", connector_id="lidarr",
                                foreign_artist_id=ARTIST, foreign_album_id=ALBUM, artist_name="Artist", album_name="Album",
                                status="uncertain", args={}, result={"_connection": _connection(connector), "command_id": 44}))
        db.commit()
    current["id"] = "admin"
    response = client.post("/api/admin/music/requests/33333333-3333-4333-8333-333333333333/reconcile")
    assert response.status_code == 200 and response.json()["status"] == "awaiting_import"


def test_lidarr_observation_drives_request_status_and_mapped_import_without_fake_play(music, monkeypatch) -> None:  # noqa: ANN001
    client, fake, current, factory = music
    from app.services.requests import sync
    scans = []
    monkeypatch.setattr(sync, "rescan_folder", scans.append)
    fake.artists[10] = {"id": 10, "foreignArtistId": ARTIST, "artistName": "Artist", "path": "/music/Artist", "statistics": {"trackFileCount": 2}}
    fake.albums[12] = {"id": 12, "foreignAlbumId": ALBUM, "artistId": 10, "title": "Album", "artist": fake.artists[10]}
    fake.queue = [{"id": 1, "albumId": 12, "status": "downloading", "downloadUrl": "https://private.invalid", "outputPath": "/private"}]
    with factory.begin() as db:
        db.get(ServiceConnector, "lidarr").settings = {"path_mappings": [{"remote": "/music", "local": "/media/music"}]}
        db.add(MusicAcquisition(id="77777777-7777-4777-8777-777777777777", user_id="alice", connector_id="lidarr", foreign_artist_id=ARTIST,
                                foreign_album_id=ALBUM, artist_name="Artist", album_name="Album", artist_id=10, album_id=12, status="awaiting_import", args={}, result={}))
    assert client.get("/api/music/request-targets").json() == {"items": [{"id": "lidarr", "name": "Lidarr"}]}
    current["id"] = "admin"
    response = client.post("/api/admin/connectors/lidarr/refresh")
    assert response.status_code == 200 and response.json()["stale"] is False, response.text
    assert scans == ["/media/music/Artist"]
    observed = client.get("/api/admin/connectors/lidarr?dataset=queue")
    assert observed.json()["items"][0]["album_id"] == 12
    assert "/private" not in observed.text and "private.invalid" not in observed.text
    current["id"] = "alice"
    request = client.get("/api/music/requests").json()["items"][0]
    assert request["result"]["observation"]["state"] == "downloading"
    assert request["availability"] == {"state": "awaiting_import"}
    current["id"] = "admin"
    fake.down = True
    assert client.post("/api/admin/connectors/lidarr/refresh").json()["stale"] is True
    assert client.get("/api/admin/connectors/lidarr?dataset=queue").json()["total"] == 1


def test_uncertain_same_connector_release_group_blocks_second_request_before_dispatch(music) -> None:  # noqa: ANN001
    client, fake, current, factory = music
    first, second = _body(), _body()
    assert client.post("/api/music/requests", json=first).status_code == 200
    assert client.post("/api/music/requests", json=second).status_code == 200
    with factory.begin() as db:
        db.get(MusicAcquisition, first["id"]).status = "uncertain"
    current["id"] = "admin"
    blocked = client.post(f"/api/admin/music/requests/{second['id']}/approve")
    assert blocked.status_code == 409 and blocked.json()["detail"]["detail"] == "album_operation_pending"
    assert fake.requests == [] and fake.commands == {}


def test_awaiting_import_same_connection_reuses_admission_without_second_search(music) -> None:  # noqa: ANN001
    client, fake, current, _factory = music
    first, second = _body(), _body()
    assert client.post("/api/music/requests", json=first).status_code == 200
    current["id"] = "admin"
    admitted = client.post(f"/api/admin/music/requests/{first['id']}/approve")
    assert admitted.status_code == 200 and admitted.json()["status"] == "awaiting_import"
    assert len(fake.commands) == 1

    current["id"] = "alice"
    assert client.post("/api/music/requests", json=second).status_code == 200
    current["id"] = "admin"
    reused = client.post(f"/api/admin/music/requests/{second['id']}/approve")
    assert reused.status_code == 200 and reused.json()["status"] == "awaiting_import"
    assert len(fake.commands) == 1


def test_second_enabled_connector_cannot_dispatch_same_release_group(music) -> None:  # noqa: ANN001
    client, fake, current, factory = music
    with factory.begin() as db:
        db.add(ServiceConnector(id="lidarr-two", kind="lidarr", name="Lidarr two", base_url=fake.url, api_key=API_KEY, enabled=True,
                                settings={"root_folder": "/music", "quality_profile_id": 4, "metadata_profile_id": 8, "path_mappings": []}))
    first, second = _body(), _body(connector_id="lidarr-two")
    assert client.post("/api/music/requests", json=first).status_code == 200
    current["id"] = "admin"
    assert client.post(f"/api/admin/music/requests/{first['id']}/approve").json()["status"] == "awaiting_import"
    assert len(fake.commands) == 1
    current["id"] = "alice"
    assert client.post("/api/music/requests", json=second).status_code == 200
    current["id"] = "admin"
    blocked = client.post(f"/api/admin/music/requests/{second['id']}/approve")
    assert blocked.status_code == 409 and blocked.json()["detail"]["detail"] == "album_operation_pending"
    assert len(fake.commands) == 1
