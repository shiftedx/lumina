"""Member playlist ownership, current visibility and bounded smart-rule evaluation."""
from __future__ import annotations

from sqlalchemy import event

from app.models import LibraryItem, MemberFavorite, MusicPlaylist, MusicTrackOverride, PlaybackProgress, User
from app.music_schemas import MusicPlaylistSave
from app.services import music_playlists
from test_music_api import ALBUM, ALICE, BOB, PRIVATE_TRACK, TRACKS, music_world  # noqa: F401
from title_support import uid


def test_own_and_shared_playlist_listing_and_optimistic_revisions(music_world) -> None:  # noqa: ANN001
    _factory, client_for, _root = music_world
    alice = client_for(ALICE)
    private = alice.post("/api/music/playlists", json={"name": " Mine ", "item_ids": [TRACKS[1], TRACKS[0]]})
    shared = alice.post("/api/music/playlists", json={"name": "House mix", "shared": True, "item_ids": [TRACKS[0]]})
    assert private.status_code == shared.status_code == 200
    assert (private.json()["name"], private.json()["revision"], private.json()["count"]) == ("Mine", 1, 2)

    bob = client_for(BOB)
    assert [row["name"] for row in bob.get("/api/music/playlists").json()] == ["House mix"]
    assert bob.put(f"/api/music/playlists/{shared.json()['id']}", json={
        "name": "Takeover", "revision": shared.json()["revision"],
    }).status_code == 403

    alice = client_for(ALICE)
    updated = alice.put(f"/api/music/playlists/{private.json()['id']}", json={
        "name": "Mine now", "revision": private.json()["revision"], "item_ids": [TRACKS[2]],
    })
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    stale = alice.put(f"/api/music/playlists/{private.json()['id']}", json={
        "name": "Stale", "revision": 1, "item_ids": [TRACKS[0]],
    })
    assert (stale.status_code, stale.json()["detail"]) == (409, "Playlist changed. Reopen it before saving.")


def test_shared_manual_playlist_filters_entries_that_became_private_or_missing(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    created = alice.post("/api/music/playlists", json={
        "name": "Shared", "shared": True, "item_ids": [TRACKS[0], TRACKS[1], TRACKS[2]],
    }).json()
    with factory() as db:
        db.get(LibraryItem, TRACKS[1]).visibility = "private"
        db.get(LibraryItem, TRACKS[2]).status = "missing"
        db.commit()

    bob = client_for(BOB)
    tracks = bob.get(f"/api/music/playlists/{created['id']}/tracks")
    assert tracks.status_code == 200
    assert [track["id"] for track in tracks.json()] == [TRACKS[0]]
    assert PRIVATE_TRACK not in repr(tracks.json())


def test_smart_rules_use_effective_genres_favorites_and_member_play_state(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    with factory() as db:
        db.add_all([
            MusicTrackOverride(item_id=TRACKS[0], fields={"genres": ["Ambient"]}),
            MusicTrackOverride(item_id=TRACKS[2], fields={"genres": []}),
            MemberFavorite(user_id=ALICE, target_id=ALBUM),
            PlaybackProgress(
                id=uid(9000), user_id=ALICE, item_id=TRACKS[0], position_seconds=20,
                duration_seconds=180, plays=1,
            ),
        ])
        db.commit()

    alice = client_for(ALICE)
    ambient = alice.post("/api/music/playlists", json={"name": "Ambient", "rules": {"genres": ["ambient"]}}).json()
    folk = alice.post("/api/music/playlists", json={"name": "Folk", "rules": {"genres": ["FOLK"]}}).json()
    favorite_unplayed = alice.post("/api/music/playlists", json={
        "name": "Fresh favorites", "rules": {"favorites": True, "unplayed": True},
    }).json()

    assert [row["id"] for row in alice.get(f"/api/music/playlists/{ambient['id']}/tracks").json()] == [TRACKS[0]]
    assert {row["id"] for row in alice.get(f"/api/music/playlists/{folk['id']}/tracks").json()} == {TRACKS[1]}
    assert {row["id"] for row in alice.get(f"/api/music/playlists/{favorite_unplayed['id']}/tracks").json()} == {
        TRACKS[1], TRACKS[2],
    }


def test_manual_playlist_track_loading_has_a_bounded_query_count(music_world) -> None:  # noqa: ANN001
    factory, _client_for, _root = music_world
    extra_ids = [uid(4000 + index) for index in range(40)]
    with factory() as db:
        for index, item_id in enumerate(extra_ids):
            db.add(LibraryItem(
                id=item_id, user_id=ALICE, visibility="shared", title=f"Track {index}", kind="track",
                title_id=ALBUM, status="available", metadata_json={"track_number": index + 10},
            ))
        playlist = MusicPlaylist(
            id=uid(8000), user_id=ALICE, name="Large", shared=False,
            item_ids=[*TRACKS, *extra_ids], revision=1,
        )
        db.add(playlist)
        db.commit()
        user = db.get(User, ALICE)
        statements: list[str] = []
        listener = lambda *_args: statements.append(_args[2])  # noqa: E731
        event.listen(db.bind, "before_cursor_execute", listener)
        try:
            result = music_playlists.tracks(db, user, playlist.id)
        finally:
            event.remove(db.bind, "before_cursor_execute", listener)

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(result) == 43
    assert len(selects) <= 7, selects


def test_playlist_validation_rejects_mixed_sources_and_inverted_years(music_world) -> None:  # noqa: ANN001
    factory, _client_for, _root = music_world
    with factory() as db:
        user = db.get(User, ALICE)
        for body in (
            MusicPlaylistSave(name="Mixed", item_ids=[TRACKS[0]], rules={"favorites": True}),
            MusicPlaylistSave(name="Years", rules={"year_from": 2025, "year_to": 2020}),
        ):
            try:
                music_playlists.save(db, user, body)
            except Exception as error:  # HTTPException is the public service contract here.
                assert getattr(error, "status_code", None) == 422
            else:
                raise AssertionError("invalid playlist was accepted")
