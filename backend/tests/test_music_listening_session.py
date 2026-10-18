"""Member listening sessions retain intent without leaking revoked music."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.db import get_db
from app.models import (LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MusicAcquisition,
                        StorageRoot, User)
from app.routers import music_listening_session
from app.security import get_current_user
from support import make_user


URL = "/api/me/music-session"
TRACK_ONE, TRACK_TWO = "track-one", "track-two"


@pytest.fixture
def listening_api(db_factory):  # noqa: ANN001, ANN201
    app = FastAPI()
    music_listening_session.register(app)
    current = {"id": "alice"}
    with db_factory.begin() as db:
        db.add_all([
            make_user("alice"), make_user("bob"),
            MediaTitle(id="album", type="album", key="album:one", name="Album"),
            LibraryItem(id=TRACK_ONE, user_id="alice", visibility="shared", title="First", kind="track", status="available", title_id="album", duration=120, metadata_json={"artist": "Artist"}),
            LibraryItem(id=TRACK_TWO, user_id="alice", visibility="shared", title="Second", kind="track", status="available", title_id="album", duration=90, metadata_json={"artist": "Artist"}),
        ])

    def override_db():  # noqa: ANN202
        with db_factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def current_user() -> User:
        with db_factory() as db:
            return db.get(User, current["id"])

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = current_user
    with TestClient(app, base_url="http://localhost") as client:
        yield client, db_factory, current


def body(revision: int, ids: list[str], *, index: int | None = 0, position: float = 0, repeat: str = "off", shuffle: bool = False) -> dict:
    return {
        "base_revision": revision, "item_ids": ids, "current_index": index, "position_seconds": position,
        "repeat": repeat, "shuffle": shuffle, "active_output": {"kind": "browser"},
    }


def test_session_round_trip_keeps_duplicates_and_intentional_rewind(listening_api) -> None:  # noqa: ANN001
    client, _factory, _current = listening_api
    assert client.get(URL).json() == {
        "revision": 0, "queue": [], "current_index": None, "position_seconds": 0,
        "repeat": "off", "shuffle": False, "active_output": {"kind": "browser"}, "updated_at": None, "removed_count": 0,
        "entries": [], "entry_index": None, "unavailable_policy": "skip",
    }

    saved = client.put(URL, json=body(0, [TRACK_ONE, TRACK_TWO, TRACK_ONE], index=2, position=50, repeat="all", shuffle=True))
    assert saved.status_code == 200
    first = saved.json()
    assert first["revision"] == 1
    assert [track["id"] for track in first["queue"]] == [TRACK_ONE, TRACK_TWO, TRACK_ONE]
    assert (first["current_index"], first["position_seconds"], first["repeat"], first["shuffle"]) == (2, 50, "all", True)

    rewound = client.put(URL, json=body(1, [TRACK_ONE, TRACK_TWO, TRACK_ONE], index=2, position=7, repeat="all", shuffle=True))
    assert rewound.status_code == 200
    assert (rewound.json()["revision"], rewound.json()["position_seconds"]) == (2, 7)


def test_two_clients_conflict_exposes_only_current_safe_snapshot_and_owner_isolation(listening_api) -> None:  # noqa: ANN001
    client, _factory, current = listening_api
    assert client.put(URL, json=body(0, [TRACK_ONE], position=12)).status_code == 200
    conflict = client.put(URL, json=body(0, [TRACK_TWO], position=5))
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "music_session_conflict"
    assert detail["session"]["revision"] == 1
    assert [track["id"] for track in detail["session"]["queue"]] == [TRACK_ONE]

    current["id"] = "bob"
    assert client.get(URL).json()["queue"] == []
    assert client.put(URL, json=body(0, [TRACK_TWO], position=3)).status_code == 200
    current["id"] = "alice"
    assert [track["id"] for track in client.get(URL).json()["queue"]] == [TRACK_ONE]


def test_read_prunes_lost_visibility_remaps_cursor_and_never_returns_old_metadata(listening_api) -> None:  # noqa: ANN001
    client, factory, _current = listening_api
    assert client.put(URL, json=body(0, [TRACK_ONE, TRACK_TWO, TRACK_ONE], index=2, position=20)).status_code == 200
    with factory.begin() as db:
        row = db.get(LibraryItem, TRACK_TWO)
        row.visibility, row.user_id = "private", "bob"

    pruned = client.get(URL)
    assert pruned.status_code == 200
    result = pruned.json()
    assert (result["revision"], result["removed_count"], result["current_index"], result["position_seconds"]) == (2, 1, 1, 20)
    assert [track["id"] for track in result["queue"]] == [TRACK_ONE, TRACK_ONE]
    assert "Second" not in pruned.text

    with factory.begin() as db:
        row = db.get(LibraryItem, TRACK_ONE)
        row.visibility, row.user_id = "private", "bob"
    gone = client.get(URL).json()
    assert (gone["queue"], gone["current_index"], gone["position_seconds"], gone["removed_count"]) == ([], None, 0, 2)


def test_put_prunes_mixed_hidden_ids_and_null_cursor_normalizes_without_a_second_revision(listening_api) -> None:  # noqa: ANN001
    client, factory, _current = listening_api
    with factory.begin() as db:
        row = db.get(LibraryItem, TRACK_TWO)
        row.visibility, row.user_id = "private", "bob"

    saved = client.put(URL, json=body(0, [TRACK_ONE, TRACK_TWO, TRACK_ONE], index=2, position=20))
    assert saved.status_code == 200
    result = saved.json()
    assert (result["revision"], result["removed_count"], result["current_index"], result["position_seconds"]) == (1, 1, 1, 20)
    assert [track["id"] for track in result["queue"]] == [TRACK_ONE, TRACK_ONE]
    assert "Second" not in saved.text

    reset = client.put(URL, json=body(1, [TRACK_ONE], index=None, position=45))
    assert reset.status_code == 200
    assert (reset.json()["revision"], reset.json()["current_index"], reset.json()["position_seconds"]) == (2, 0, 0)
    reloaded = client.get(URL).json()
    assert (reloaded["revision"], reloaded["removed_count"], reloaded["current_index"], reloaded["position_seconds"]) == (2, 0, 0, 0)


def test_session_rejects_unbounded_or_remote_output_payloads(listening_api) -> None:  # noqa: ANN001
    client, _factory, _current = listening_api
    malformed = body(0, [TRACK_ONE], index=3)
    assert client.put(URL, json=malformed).status_code == 422
    remote = body(0, [TRACK_ONE])
    remote["active_output"] = {"kind": "chromecast", "url": "http://speaker.invalid"}
    assert client.put(URL, json=remote).status_code == 422
    too_far = body(0, [TRACK_ONE], position=604_801)
    assert client.put(URL, json=too_far).status_code == 422
    invalid_id = body(0, ["track one"])
    assert client.put(URL, json=invalid_id).status_code == 422


def test_logical_acquisition_entries_keep_pending_intent_without_leaking_other_members(listening_api) -> None:  # noqa: ANN001
    import uuid
    from app.models import MusicAcquisition

    client, factory, _current = listening_api
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    with factory.begin() as db:
        db.add_all([
            MusicAcquisition(id=mine, user_id="alice", connector_id="lidarr", foreign_artist_id=str(uuid.uuid4()),
                             foreign_album_id=str(uuid.uuid4()), artist_name="Artist", album_name="Pending album", status="requested"),
            MusicAcquisition(id=other, user_id="bob", connector_id="lidarr", foreign_artist_id=str(uuid.uuid4()),
                             foreign_album_id=str(uuid.uuid4()), artist_name="Other", album_name="Private album", status="requested"),
        ])
    payload = body(0, [TRACK_ONE], index=0)
    payload.update({"entries": [
        {"kind": "track", "item_id": TRACK_ONE},
        {"kind": "acquisition", "request_id": mine},
        {"kind": "acquisition", "request_id": other},
    ], "entry_index": 0, "unavailable_policy": "wait"})
    response = client.put(URL, json=payload)
    assert response.status_code == 200
    result = response.json()
    assert [track["id"] for track in result["queue"]] == [TRACK_ONE]
    assert [(entry["kind"], entry["state"]) for entry in result["entries"]] == [("track", "playable"), ("acquisition", "requested")]
    assert "Private album" not in response.text
    assert result["entry_index"] == 0 and result["unavailable_policy"] == "wait"


def test_acquisition_album_keeps_selected_track_within_resolved_entry(listening_api) -> None:  # noqa: ANN001
    import uuid

    client, factory, _current = listening_api
    request_id, release_group = str(uuid.uuid4()), str(uuid.uuid4())
    with factory.begin() as db:
        db.get(MediaTitle, "album").metadata_json = {"release_group_id": release_group}
        db.get(LibraryItem, TRACK_ONE).metadata_json = {"artist": "Artist", "disc_number": 1, "track_number": 1}
        db.get(LibraryItem, TRACK_TWO).metadata_json = {"artist": "Artist", "disc_number": 1, "track_number": 2}
        db.add_all([
            StorageRoot(id="music-root", label="Music", path="/music", mode="external", enabled=True),
            MediaArtifact(id="one-artifact", root_id="music-root", relative_path="one.flac", ownership="managed", lifecycle="available"),
            MediaArtifact(id="two-artifact", root_id="music-root", relative_path="two.flac", ownership="managed", lifecycle="available"),
            LibraryItemArtifact(library_item_id=TRACK_ONE, artifact_id="one-artifact"),
            LibraryItemArtifact(library_item_id=TRACK_TWO, artifact_id="two-artifact"),
            MusicAcquisition(id=request_id, user_id="alice", connector_id="lidarr", foreign_artist_id=str(uuid.uuid4()),
                             foreign_album_id=release_group, artist_name="Artist", album_name="Album", status="awaiting_import", args={}, result={}),
        ])
    payload = body(0, [TRACK_ONE, TRACK_TWO], index=1, position=20)
    payload.update({"entries": [{"kind": "acquisition", "request_id": request_id}], "entry_index": 0})
    saved = client.put(URL, json=payload)
    assert saved.status_code == 200
    assert (saved.json()["current_index"], saved.json()["position_seconds"]) == (1, 20)
    assert [track["id"] for track in saved.json()["entries"][0]["tracks"]] == [TRACK_ONE, TRACK_TWO]
    restored = client.get(URL).json()
    assert (restored["current_index"], restored["position_seconds"]) == (1, 20)


def test_logical_order_retains_playable_tail_when_flat_queue_cap_is_reached(listening_api, monkeypatch) -> None:  # noqa: ANN001
    import uuid
    from app.services import music_queue_entries

    monkeypatch.setattr(music_queue_entries, "MAX_ENTRIES", 3)
    client, factory, _current = listening_api
    request_id, release_group = str(uuid.uuid4()), str(uuid.uuid4())
    with factory.begin() as db:
        db.get(MediaTitle, "album").metadata_json = {"release_group_id": release_group}
        db.get(LibraryItem, TRACK_ONE).metadata_json = {"artist": "Artist", "disc_number": 1, "track_number": 1}
        db.get(LibraryItem, TRACK_TWO).metadata_json = {"artist": "Artist", "disc_number": 1, "track_number": 2}
        db.add_all([
            StorageRoot(id="queue-root", label="Music", path="/queue", mode="external", enabled=True),
            MediaArtifact(id="queue-one", root_id="queue-root", relative_path="one.flac", ownership="managed", lifecycle="available"),
            MediaArtifact(id="queue-two", root_id="queue-root", relative_path="two.flac", ownership="managed", lifecycle="available"),
            LibraryItemArtifact(library_item_id=TRACK_ONE, artifact_id="queue-one"),
            LibraryItemArtifact(library_item_id=TRACK_TWO, artifact_id="queue-two"),
            MusicAcquisition(id=request_id, user_id="alice", connector_id="lidarr", foreign_artist_id=str(uuid.uuid4()),
                             foreign_album_id=release_group, artist_name="Artist", album_name="Album", status="awaiting_import", args={}, result={}),
        ])
    payload = body(0, [TRACK_ONE, TRACK_TWO, TRACK_ONE, TRACK_TWO], index=0)
    payload.update({"entries": [
        {"kind": "acquisition", "request_id": request_id},
        {"kind": "track", "item_id": TRACK_ONE},
        {"kind": "track", "item_id": TRACK_TWO},
    ], "entry_index": 0})
    saved = client.put(URL, json=payload)
    assert saved.status_code == 200
    result = saved.json()
    assert [track["id"] for track in result["queue"]] == [TRACK_ONE, TRACK_TWO, TRACK_ONE]
    assert [entry["state"] for entry in result["entries"]] == ["playable", "playable", "playable"]
    assert result["entries"][2]["tracks"] == []
