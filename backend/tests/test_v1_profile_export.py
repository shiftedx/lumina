"""GET /api/me/export bundles the signed-in member's own portable data.

Owner-only is structural (every query is scoped to current_user; there is no
id parameter to request someone else's export), so the isolation test proves
it end to end with two members sharing a database. No import path exists
so there is nothing to round-trip here.
"""
from __future__ import annotations

import pytest

from app.models import LibraryItem, LibraryNote, PlaybackProgress, RemotePlaybackProgress, SourceAutomation
from support import make_user

URL = "/api/me/export"


@pytest.fixture
def env(db_factory, api_client):
    alice, bob = make_user("alice"), make_user("bob")
    with db_factory.begin() as db:
        db.add_all([alice, bob])
        db.add(LibraryItem(id="item-1", user_id="alice", visibility="private", title="Alice's video", metadata_json={}, status="available"))
        db.add(SourceAutomation(
            id="follow-1", user_id="alice", label="Veritasium", source_url="https://www.youtube.com/@veritasium",
            source_type="channel", cron_expression="*/30 * * * *", active=True, auto_download=True,
        ))
        db.add(LibraryNote(id="note-1", item_id="item-1", user_id="alice", visibility="private", body="alice's private note"))
        db.add(PlaybackProgress(id="progress-1", user_id="alice", item_id="item-1", position_seconds=120, duration_seconds=600, completed=False))
        db.add(RemotePlaybackProgress(
            id="remote-progress-1", user_id="alice", source_identity="youtube:xyz", source_identity_key="hash-xyz",
            source_url="https://www.youtube.com/watch?v=xyz", title="Remote video", position_seconds=30.0, duration_seconds=90.0,
        ))
        # A cleared checkpoint must not resurface in the export.
        db.add(RemotePlaybackProgress(
            id="remote-progress-2", user_id="alice", source_identity="youtube:cleared", source_identity_key="hash-cleared",
            source_url="https://www.youtube.com/watch?v=cleared", position_seconds=5.0, cleared=True,
        ))

    current = {"user": alice}
    return current, alice, bob, api_client(user=lambda: current["user"], base_url="http://localhost")


def test_profile_export_owner_only(env) -> None:
    current, alice, bob, client = env

    # Seed a search history entry and a queued item through the real endpoints
    # so the export exercises the same services the rest of the app uses.
    client.post("/api/search/history", json={"query": "alice's search"})
    client.post("/api/me/watch-queue/entries", json={"ref": {"kind": "library", "library_item_id": "item-1"}})

    response = client.get(URL)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["user"]["username"] == "alice"
    assert [f["label"] for f in body["follows"]] == ["Veritasium"]
    assert [n["body"] for n in body["notes"]] == ["alice's private note"]
    assert [e["query"] for e in body["search_history"]] == ["alice's search"]
    assert [p["item_id"] for p in body["playback_progress"]] == ["item-1"]
    # Only the non-cleared remote checkpoint surfaces.
    assert [r["source_identity"] for r in body["remote_playback_progress"]] == ["youtube:xyz"]
    assert [q["library_item_id"] for q in body["queue"]] == ["item-1"]

    current["user"] = bob
    bob_body = client.get(URL).json()
    assert bob_body["user"]["username"] == "bob"
    assert bob_body["follows"] == []
    assert bob_body["notes"] == []
    assert bob_body["search_history"] == []
    assert bob_body["playback_progress"] == []
    assert bob_body["remote_playback_progress"] == []
    assert bob_body["queue"] == []


def test_profile_export_filename_never_carries_raw_username_characters(env) -> None:
    current, alice, bob, client = env
    alice.username = 'zoë"; x=1'  # an invited member picks any 1-80 characters

    response = client.get(URL)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="lumina-export-zo____x_1.json"'
