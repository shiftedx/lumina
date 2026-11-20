"""Music HTTP contracts: visible queues, effective metadata, playback and cast capabilities."""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.models import (
    LibraryItem,
    LibraryItemArtifact,
    MediaArtifact,
    MediaTitle,
    MemberAccess,
    AppSettings,
    MusicCastTicket,
    MusicEditBatch,
    MusicLookup,
    MusicTrackOverride,
    PlaybackProgress,
    TitleEdit,
    User,
    utcnow,
)
from app.services import member_access, music_audio, music_providers, music_sonos, public_address, screen_time
from app.services.library_search import LibrarySearchService
from support import make_user, seed_app_settings
from title_support import ALBUM, ALICE, ARTIST, BOB, ROOT, TRACKS, add_file, seed_gallery, seed_tree, uid

ADMIN = uid(0xAD11)
PRIVATE_TRACK = uid(174)
MISSING_TRACK = uid(175)
SECOND_ALBUM = uid(72)
SECOND_TRACK = uid(176)
RESTRICTED_ALBUM = uid(73)
RESTRICTED_TRACK = uid(177)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def music_world(db_factory, tmp_path: Path, api_client):  # noqa: ANN001, ANN201
    root = (tmp_path / "media").resolve()
    with db_factory() as db:
        db.add_all([
            make_user(ADMIN, role="admin", username="admin"),
            make_user(ALICE, username="alice"),
            make_user(BOB, username="bob"),
        ])
        seed_tree(db, root)
        seed_gallery(db, root)
        db.add(MediaTitle(
            id=SECOND_ALBUM, type="album", key="album:artist a/album two", root_id=ROOT,
            name="Album Two", parent_id=ARTIST, year=2024, metadata_json={"genres": ["Jazz"]},
        ))
        add_file(
            db, root, "Music/Artist A/Album Two (2024)/01 Other.flac", item_id=SECOND_TRACK,
            title_id=SECOND_ALBUM, owner=ALICE, title="Other song", kind="track",
            metadata={"artist": "Artist A", "album_artist": "Artist A", "track_number": 1, "disc_number": 1},
        )
        db.add(MediaTitle(
            id=RESTRICTED_ALBUM, type="album", key="album:artist a/restricted album", root_id=ROOT,
            name="Restricted album", parent_id=ARTIST, year=2024,
        ))
        add_file(
            db, root, "Music/Artist A/Restricted Album/01 Restricted.flac", item_id=RESTRICTED_TRACK,
            title_id=RESTRICTED_ALBUM, owner=BOB, visibility="shared", title="Restricted song", kind="track",
            metadata={"artist": "Artist A", "album_artist": "Artist A", "track_number": 1, "disc_number": 1},
        )
        add_file(
            db, root, "Music/Artist A/Album One (2019)/private.flac", item_id=PRIVATE_TRACK,
            title_id=ALBUM, owner=BOB, visibility="private", title="Private song", kind="track",
            metadata={"artist": "Artist A", "album_artist": "Artist A", "track_number": 4, "disc_number": 1},
        )
        missing = add_file(
            db, root, "Music/Artist A/Album One (2019)/missing.flac", item_id=MISSING_TRACK,
            title_id=ALBUM, owner=ALICE, title="Missing song", kind="track",
        )
        missing.status = "missing"
        db.flush()
        first_artifact = db.scalar(
            select(MediaArtifact)
            .join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
            .where(LibraryItemArtifact.library_item_id == TRACKS[0])
        )
        first_artifact.probe = {**(first_artifact.probe or {}), "loudness": {"i": -20.0, "tp": -3.0}}
        seed_app_settings(db, members_edit_metadata=True)
        db.commit()

    def client_for(user_id: str):  # noqa: ANN202
        with db_factory() as db:
            return api_client(user=db.get(User, user_id), base_url="http://localhost")

    return db_factory, client_for, root


def test_queue_keeps_requested_order_and_hides_private_and_missing_tracks(music_world) -> None:  # noqa: ANN001
    _factory, client_for, _root = music_world
    alice = client_for(ALICE)

    queued = alice.post("/api/music/queue", json={"item_ids": [TRACKS[2], TRACKS[0], TRACKS[2]]})
    assert queued.status_code == 200
    assert [track["id"] for track in queued.json()] == [TRACKS[2], TRACKS[0], TRACKS[2]]
    assert queued.json()[0]["audio_url"] == f"/api/music/tracks/{TRACKS[2]}/audio"

    album = alice.post("/api/music/queue", json={"title_id": ALBUM})
    assert [(track["disc"], track["number"]) for track in album.json()] == [(1, 1), (1, 2), (2, 1)]
    for item_id in (PRIVATE_TRACK, MISSING_TRACK, uid(9999)):
        reply = alice.post("/api/music/queue", json={"item_ids": [item_id]})
        assert (reply.status_code, reply.json()) == (404, {"detail": "Track not found"})


def test_contextual_mix_preview_orders_reasons_and_hides_ineligible_tracks(music_world) -> None:  # noqa: ANN001
    factory, client_for, root = music_world
    genre_artist, genre_album, genre_track = uid(740), uid(741), uid(742)
    lonely_artist, lonely_album, lonely_track = uid(750), uid(751), uid(752)
    hidden_artist, hidden_album, hidden_track = uid(760), uid(761), uid(762)
    with factory() as db:
        db.add_all([
            MediaTitle(id=genre_artist, type="artist", key="artist:genre", root_id=ROOT, name="Genre artist"),
            MediaTitle(id=genre_album, type="album", key="album:genre", root_id=ROOT, parent_id=genre_artist,
                       name="Genre album", metadata_json={"genres": ["FOLK"]}),
            MediaTitle(id=lonely_artist, type="artist", key="artist:lonely", root_id=ROOT, name="Lonely artist"),
            MediaTitle(id=lonely_album, type="album", key="album:lonely", root_id=ROOT, parent_id=lonely_artist,
                       name="Lonely album"),
            MediaTitle(id=hidden_artist, type="artist", key="artist:hidden", root_id=ROOT, name="Hidden artist"),
            MediaTitle(id=hidden_album, type="album", key="album:hidden", root_id=ROOT, parent_id=hidden_artist,
                       name="Hidden album"),
        ])
        add_file(db, root, "Music/Genre/01 Folk.flac", item_id=genre_track, title_id=genre_album, owner=ALICE,
                 title="A Folk Match", kind="track", metadata={"artist": "Genre artist", "track_number": 1})
        add_file(db, root, "Music/Lonely/01 Solo.flac", item_id=lonely_track, title_id=lonely_album, owner=ALICE,
                 title="Solo", kind="track", metadata={"artist": "Lonely artist", "track_number": 1})
        add_file(db, root, "Music/Hidden/01 Secret.flac", item_id=hidden_track, title_id=hidden_album, owner=BOB,
                 visibility="private", title="Secret", kind="track", metadata={"artist": "Hidden artist", "track_number": 1})
        db.commit()

    alice = client_for(ALICE)
    track = alice.get("/api/music/contextual-mix", params={"seed_kind": "track", "seed_id": TRACKS[0]})
    assert track.status_code == 200
    payload = track.json()
    assert payload["seed"] == {"id": TRACKS[0], "kind": "track", "name": "Song 1.1", "artist_id": ARTIST, "genres": ["Folk"]}
    assert [entry["id"] for entry in payload["items"]] == [TRACKS[1], TRACKS[2], SECOND_TRACK, RESTRICTED_TRACK, genre_track]
    assert len({entry["id"] for entry in payload["items"]}) == len(payload["items"])
    assert [entry["reason"]["code"] for entry in payload["items"]] == ["same_artist", "same_artist", "same_artist", "same_artist", "shared_genre"]
    assert payload["items"][-1]["reason"]["genre"] == "FOLK"
    assert payload["exhausted"] and PRIVATE_TRACK not in repr(payload) and MISSING_TRACK not in repr(payload)

    with factory() as db:
        db.add(MusicTrackOverride(item_id=genre_track, fields={"genres": []}))
        db.commit()
    cleared = alice.get("/api/music/contextual-mix", params={"seed_kind": "track", "seed_id": TRACKS[0]})
    assert genre_track not in [entry["id"] for entry in cleared.json()["items"]]

    excluded = alice.get("/api/music/contextual-mix", params=[
        ("seed_kind", "track"), ("seed_id", TRACKS[0]), ("exclude_item_ids", TRACKS[1]), ("exclude_item_ids", TRACKS[1]),
    ])
    assert excluded.status_code == 200 and TRACKS[1] not in [entry["id"] for entry in excluded.json()["items"]]

    album = alice.get("/api/music/contextual-mix", params={"seed_kind": "album", "seed_id": ALBUM})
    assert [(entry["id"], entry["reason"]["code"]) for entry in album.json()["items"][:3]] == [
        (TRACKS[0], "seed_track"), (TRACKS[1], "seed_track"), (TRACKS[2], "seed_track"),
    ]
    assert alice.get("/api/music/contextual-mix", params={"seed_kind": "track", "seed_id": PRIVATE_TRACK}).status_code == 404
    assert alice.get("/api/music/contextual-mix", params={"seed_kind": "track", "seed_id": MISSING_TRACK}).status_code == 404
    assert alice.get("/api/music/contextual-mix", params={"seed_kind": "album", "seed_id": hidden_album}).json() == {"detail": "Music seed not found"}
    empty = alice.get("/api/music/contextual-mix", params={"seed_kind": "track", "seed_id": lonely_track})
    assert empty.json()["items"] == [] and empty.json()["exhausted"]
    assert alice.get("/api/music/mixes/radio", params={"seed_id": TRACKS[0]}).status_code == 200


def test_contextual_mix_enforces_preview_and_exclusion_caps(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    extra_ids = [uid(10_000 + index) for index in range(101)]
    with factory() as db:
        db.add_all([
            LibraryItem(id=item_id, user_id=ALICE, visibility="shared", title=f"Extra {index}", kind="track",
                        title_id=ALBUM, status="available", metadata_json={"artist": "Artist A", "track_number": index + 10})
            for index, item_id in enumerate(extra_ids)
        ])
        db.commit()

    alice = client_for(ALICE)
    preview = alice.get("/api/music/contextual-mix", params={"seed_kind": "artist", "seed_id": ARTIST})
    assert preview.status_code == 200
    assert len(preview.json()["items"]) == 100 and not preview.json()["exhausted"]
    too_many = alice.get("/api/music/contextual-mix", params=[
        ("seed_kind", "artist"), ("seed_id", ARTIST), *[("exclude_item_ids", uid(20_000 + index)) for index in range(101)],
    ])
    assert too_many.status_code == 422


def test_recently_played_albums_groups_qualified_member_history_and_hides_ineligible_tracks(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    now = utcnow()
    with factory() as db:
        db.add_all([
            PlaybackProgress(id=uid(801), user_id=ALICE, item_id=TRACKS[0], plays=1, last_watched_at=now - timedelta(hours=2)),
            PlaybackProgress(id=uid(802), user_id=ALICE, item_id=TRACKS[1], plays=2, last_watched_at=now),
            PlaybackProgress(id=uid(803), user_id=ALICE, item_id=SECOND_TRACK, plays=1, last_watched_at=now - timedelta(hours=1)),
            PlaybackProgress(id=uid(807), user_id=ALICE, item_id=RESTRICTED_TRACK, plays=1, last_watched_at=now - timedelta(minutes=30)),
            PlaybackProgress(id=uid(804), user_id=ALICE, item_id=TRACKS[2], plays=0, last_watched_at=now + timedelta(hours=1)),
            PlaybackProgress(id=uid(805), user_id=ALICE, item_id=PRIVATE_TRACK, plays=9, last_watched_at=now + timedelta(hours=2)),
            PlaybackProgress(id=uid(806), user_id=ALICE, item_id=MISSING_TRACK, plays=9, last_watched_at=now + timedelta(hours=3)),
        ])
        db.commit()

    for limit in (0, 51):
        assert client_for(ALICE).get(f"/api/music/albums/recently-played?limit={limit}").status_code == 422
    reply = client_for(ALICE).get("/api/music/albums/recently-played?limit=1")
    assert reply.status_code == 200
    assert [entry["id"] for entry in reply.json()["items"]] == [ALBUM]
    assert reply.json()["items"][0]["artist_name"] == "Artist A"
    all_recent = client_for(ALICE).get("/api/music/albums/recently-played?limit=12")
    assert [entry["id"] for entry in all_recent.json()["items"]] == [ALBUM, RESTRICTED_ALBUM, SECOND_ALBUM]
    # A same-second activity tie remains stable across refreshes and the limit.
    with factory() as db:
        db.get(PlaybackProgress, uid(803)).last_watched_at = now
        db.commit()
    tied = client_for(ALICE).get("/api/music/albums/recently-played?limit=12")
    assert [entry["id"] for entry in tied.json()["items"]] == [*sorted([ALBUM, SECOND_ALBUM]), RESTRICTED_ALBUM]
    with factory() as db:
        db.add(MemberAccess(user_id=ALICE, sections=["movies"]))
        db.commit()
    member_access.invalidate(ALICE)
    restricted = client_for(ALICE).get("/api/music/albums/recently-played").json()["items"]
    assert [entry["id"] for entry in restricted] == sorted([ALBUM, SECOND_ALBUM])
    assert client_for(BOB).get("/api/music/albums/recently-played").json() == {"items": []}


def test_track_metadata_uses_the_same_effective_values_as_the_queue(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    with factory() as db:
        item = db.get(LibraryItem, TRACKS[0])
        item.metadata_json = {**item.metadata_json, "track_number": "1", "disc_number": "1"}
        db.commit()
    alice = client_for(ALICE)
    track = alice.get(f"/api/music/tracks/{TRACKS[0]}").json()
    fields = alice.get(f"/api/music/tracks/{TRACKS[0]}/metadata").json()["fields"]

    assert fields == {
        "title": track["title"], "artist": track["artist"], "album_artist": track["album_artist"],
        "album_id": track["album_id"], "track_number": track["number"], "disc_number": track["disc"],
        "genres": track["genres"], "lyrics": None, "gain_db": track["gain_db"],
    }
    assert track["gain_db"] == 2.0
    edited = alice.post("/api/music/tracks/edit", json={
        "item_ids": [TRACKS[0]], "changes": {"gain_db": -3.5},
        "base": {TRACKS[0]: {"gain_db": 2.0}},
    })
    assert edited.status_code == 200 and edited.json()["tracks"][0]["gain_db"] == -3.5
    assert alice.get(f"/api/music/tracks/{TRACKS[0]}/metadata").json()["fields"]["gain_db"] == -3.5


def test_track_overrides_conflict_revert_and_changed_since_undo(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    initial = alice.get(f"/api/music/tracks/{TRACKS[0]}").json()

    saved = alice.post("/api/music/tracks/edit", json={
        "item_ids": [TRACKS[0]],
        "changes": {"title": "House edit", "genres": ["Ambient"], "album_id": SECOND_ALBUM},
        "base": {TRACKS[0]: {"title": initial["title"], "album_id": ALBUM}},
    })
    assert saved.status_code == 200
    changed = saved.json()["tracks"][0]
    assert (changed["title"], changed["album"], changed["genres"], changed["edited"]) == (
        "House edit", "Album Two", ["Ambient"], True,
    )
    with factory() as db:
        assert LibrarySearchService(db).item_ids_for_query(db.get(User, ALICE), "house edit", limit=10) == [TRACKS[0]]

    conflict = alice.post("/api/music/tracks/edit", json={
        "item_ids": [TRACKS[0]], "changes": {"title": "Lost update"},
        "base": {TRACKS[0]: {"title": initial["title"]}},
    })
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["conflicts"][0]["current"]["title"] == "House edit"

    batch_id = saved.json()["batch_id"]
    newer = alice.post("/api/music/tracks/edit", json={"item_ids": [TRACKS[0]], "changes": {"title": "Newer edit"}})
    assert newer.status_code == 200
    skipped = alice.post(f"/api/music/edits/{batch_id}/undo")
    assert skipped.json() == {"applied": 0, "skipped": [TRACKS[0]]}

    reverted = alice.post("/api/music/tracks/edit", json={
        "item_ids": [TRACKS[0]], "changes": {"title": None, "genres": None, "album_id": None},
    })
    assert reverted.status_code == 200
    track = reverted.json()["tracks"][0]
    assert (track["title"], track["album"], track["genres"]) == (initial["title"], "Album One", ["Folk"])
    with factory() as db:
        assert db.get(LibraryItem, TRACKS[0]).title_id == ALBUM
        assert db.get(MusicTrackOverride, TRACKS[0]).fields == {"_source_album_id": ALBUM}
        assert LibrarySearchService(db).item_ids_for_query(db.get(User, ALICE), "house edit", limit=10) == []


def test_create_release_checks_album_bases_uses_destination_artist_and_undoes_atomically(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    ids = TRACKS[:2]
    with factory() as db:
        before_titles = db.query(MediaTitle).count()
        before_batches = db.query(MusicEditBatch).count()

    stale = alice.post("/api/music/albums", json={
        "item_ids": ids, "name": "Stale release", "artist_name": "New artist",
        "base": {item_id: {"album_id": SECOND_ALBUM} for item_id in ids},
    })
    assert stale.status_code == 409
    with factory() as db:
        assert db.query(MediaTitle).count() == before_titles
        assert db.query(MusicEditBatch).count() == before_batches

    created = alice.post("/api/music/albums", json={
        "item_ids": ids, "name": "Home sessions", "artist_name": "Destination Artist", "year": 2026,
        "edition": "Kitchen edition", "base": {item_id: {"album_id": ALBUM} for item_id in ids},
    })
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["applied"] == 2
    assert {(track["artist"], track["album_artist"], track["album"]) for track in payload["tracks"]} == {
        ("Artist A", "Destination Artist", "Home sessions")
    }
    fields = alice.get(f"/api/music/tracks/{ids[0]}/metadata").json()["fields"]
    assert (fields["artist"], fields["album_artist"], fields["album_id"]) == (
        "Artist A", "Destination Artist", payload["album_id"],
    )
    with factory() as db:
        album = db.get(MediaTitle, payload["album_id"])
        assert album is not None
        artist = db.get(MediaTitle, album.parent_id)
        assert artist is not None
        assert album.field_sources["name"] == artist.field_sources["name"] == "user"
        assert {db.get(LibraryItem, item_id).title_id for item_id in ids} == {album.id}

    undone = alice.post(f"/api/music/edits/{payload['batch_id']}/undo")
    assert undone.json() == {"applied": 2, "skipped": []}
    restored = alice.post("/api/music/queue", json={"item_ids": ids}).json()
    assert {(track["album_id"], track["album_artist"]) for track in restored} == {(ALBUM, "Artist A")}


def test_oversize_bulk_edit_rolls_back_every_track(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    item_ids = [f"bulk-lyrics-{number}" for number in range(22)]
    with factory() as db:
        db.add_all([
            LibraryItem(
                id=item_id, user_id=ALICE, visibility="shared", title=f"Bulk track {number}",
                metadata_json={"artist": "Artist A"}, status="available", kind="track", title_id=ALBUM,
            )
            for number, item_id in enumerate(item_ids)
        ])
        db.commit()
    reply = client_for(ALICE).post("/api/music/tracks/edit", json={
        "item_ids": item_ids, "changes": {"lyrics": "x" * 100_000},
    })
    assert (reply.status_code, reply.json()) == (422, {"detail": "Music edit is too large to undo safely"})
    with factory() as db:
        assert db.query(MusicTrackOverride).filter(MusicTrackOverride.item_id.in_(item_ids)).count() == 0
        assert db.query(MusicEditBatch).count() == 0


def test_native_audio_is_private_and_screen_time_applies_to_music_starts(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    reply = alice.get(f"/api/music/tracks/{TRACKS[0]}/audio?format=native")
    assert reply.status_code == 200
    assert reply.headers["content-type"] in {"audio/flac", "audio/x-flac"}
    assert reply.headers["cache-control"] == "private, max-age=600"

    with factory() as db:
        db.add(MemberAccess(user_id=ALICE, daily_limit_minutes=0))
        db.commit()
    screen_time.forget(ALICE)
    refused = alice.post("/api/music/queue", json={"item_ids": [TRACKS[0]]})
    assert (refused.status_code, refused.json()) == (403, {"detail": "screen_time_up"})


def test_offline_audio_download_requires_download_permission_but_listening_does_not(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    with factory() as db:
        db.add(MemberAccess(user_id=ALICE, can_download=False))
        db.commit()
    alice = client_for(ALICE)

    listening = alice.get(f"/api/music/tracks/{TRACKS[0]}/audio?format=native")
    assert listening.status_code == 200
    download = alice.get(f"/api/music/tracks/{TRACKS[0]}/audio?format=native&download=true")
    assert (download.status_code, download.json()) == (403, {"detail": "downloads_not_allowed"})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("source_format", ["flac", "mp3", "m4a", "opus"])
def test_common_source_formats_have_a_bounded_sonos_flac_preview(tmp_path: Path, source_format: str) -> None:
    spec = importlib.util.spec_from_file_location("music_fixture", REPOSITORY_ROOT / "scripts/e2e/music_fixture.py")
    fixture = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(fixture)
    source = fixture.write_track(tmp_path / f"source.{source_format}", {"title": "Preview"}, codec=source_format)

    result, content_type = music_audio.playback_file(source, "sonos")
    cached, cached_type = music_audio.playback_file(source, "sonos")

    assert result == cached and result.suffix == ".flac" and result.stat().st_size > 0
    assert content_type == cached_type == "audio/flac"


def test_provider_work_is_queued_only_after_track_visibility(music_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    _factory, client_for, _root = music_world
    calls: list[tuple] = []
    monkeypatch.setattr(music_providers, "enqueue", lambda *args: calls.append(args))
    monkeypatch.setattr(music_providers, "pending", lambda _key: True)
    alice = client_for(ALICE)

    reply = alice.get(f"/api/music/tracks/{TRACKS[0]}/lyrics")
    assert reply.status_code == 200 and reply.json()["refreshing"] is True
    assert len(calls) == 1
    hidden = alice.get(f"/api/music/tracks/{PRIVATE_TRACK}/lyrics")
    assert hidden.status_code == 404 and len(calls) == 1


def test_cached_lyrics_error_keeps_the_complete_response_contract(music_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    monkeypatch.setattr(music_providers, "enqueue", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(music_providers, "pending", lambda _key: False)
    key = music_providers.key_for("lyrics", "Artist A", "Song 1.1", "Album One", "180.0")
    with factory() as db:
        db.add(MusicLookup(
            key=key, kind="lyrics", payload={"error": "Music provider is temporarily unavailable"},
            expires_at=utcnow() + timedelta(hours=1),
        ))
        db.commit()

    reply = client_for(ALICE).get(f"/api/music/tracks/{TRACKS[0]}/lyrics")

    assert reply.status_code == 200
    assert reply.json() == {
        "plain": None,
        "cues": [],
        "source": None,
        "instrumental": False,
        "error": "Music provider is temporarily unavailable",
        "refreshing": False,
        "retry_after_seconds": 10,
    }


def test_explicit_identification_is_a_protected_history_edit_and_obeys_member_edit_permission(music_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    queued = []
    monkeypatch.setattr(music_providers, "request_enrichment", lambda *args, **kwargs: queued.append((args, kwargs)) or True)
    provider_id = "22345678-1234-4678-9234-123456789012"
    alice = client_for(ALICE)
    reply = alice.post(f"/api/music/titles/{ALBUM}/identify", json={"provider_id": provider_id})
    assert reply.status_code == 200 and reply.json() == {"queued": True}
    assert queued[0] == ((ALBUM, provider_id), {"force": True})
    with factory() as db:
        title = db.get(MediaTitle, ALBUM)
        assert title.provider_ids["MusicBrainz"] == provider_id and title.field_sources["provider_ids"] == "user"
        edit = db.scalar(select(TitleEdit).where(TitleEdit.title_id == ALBUM, TitleEdit.field == "provider_ids"))
        assert edit.user_id == ALICE and edit.kind == "identify" and edit.after["MusicBrainz"] == provider_id
        db.get(AppSettings, 1).members_edit_metadata = False
        db.commit()
    assert client_for(ALICE).post(f"/api/music/titles/{ALBUM}/identify", json={"provider_id": provider_id}).status_code == 403
    assert len(queued) == 1


def test_cast_ticket_is_owner_bound_expiring_revocable_and_rechecks_visibility(
    music_world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    queued = []
    monkeypatch.setattr(public_address, "local_origin", lambda: "http://media.home.arpa")
    monkeypatch.setattr(public_address, "origin", lambda: None)
    monkeypatch.setattr(music_sonos.service, "play_queue", lambda speaker_id, tracks: queued.extend(tracks) or {"ok": True})
    monkeypatch.setattr(music_sonos.service, "control", lambda _speaker, action, _value: {"ok": True, "action": action})
    monkeypatch.setattr(music_sonos.service, "status", lambda _speaker: {"playing": True, "track": {"track_number": 1}})
    converted = tmp_path / "sonos.flac"
    converted.write_bytes(b"fLaCfixture")
    monkeypatch.setattr(music_audio, "playback_file", lambda _path, format_: (converted, "audio/flac"))

    alice = client_for(ALICE)
    cast = alice.post("/api/music/cast", json={"speaker_id": "speaker-1", "item_ids": [TRACKS[0]]})
    assert cast.status_code == 200 and {"ticket_id", "state", "queue_indices", "session"} <= set(cast.json())
    ticket_id = cast.json()["ticket_id"]
    assert len(queued) == 1 and queued[0].content_type == "audio/flac"
    parsed = urlparse(queued[0].url)
    token = parse_qs(parsed.query)["token"][0]
    assert parsed.path == f"/api/music/cast/{ticket_id}/{TRACKS[0]}/audio"
    art_url = urlparse(queued[0].artwork_url)
    assert art_url.path == f"/api/music/cast/{ticket_id}/{TRACKS[0]}/artwork"
    assert parse_qs(art_url.query)["token"] == [token]
    assert alice.get(f"{art_url.path}?token=wrong").status_code == 401
    art = alice.get(f"{art_url.path}?{art_url.query}")
    assert art.status_code == 200 and art.headers["cache-control"] == "no-store"
    with factory() as db:
        ticket = db.get(MusicCastTicket, ticket_id)
        assert ticket.user_id == ALICE
        assert ticket.token_hash == hashlib.sha256(token.encode()).hexdigest()

    bob = client_for(BOB)
    assert bob.post(f"/api/music/cast/{ticket_id}/control", json={"action": "pause"}).status_code == 404
    alice = client_for(ALICE)
    bad = alice.get(f"{parsed.path}?token=wrong")
    assert (bad.status_code, bad.json()) == (401, {"detail": "Expired cast access"})
    audio = alice.get(f"{parsed.path}?token={token}")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/flac"
    assert audio.headers["cache-control"] == "no-store"

    with factory() as db:
        item = db.get(LibraryItem, TRACKS[0])
        item.visibility, item.user_id = "private", BOB
        db.commit()
    assert alice.get(f"{parsed.path}?token={token}").status_code == 404
    assert alice.get(f"{art_url.path}?{art_url.query}").status_code == 404
    with factory() as db:
        item = db.get(LibraryItem, TRACKS[0])
        item.visibility, item.user_id = "shared", ALICE
        db.commit()

    stopped = alice.post(f"/api/music/cast/{ticket_id}/control", json={"action": "stop"})
    assert stopped.status_code == 200
    assert alice.get(f"{parsed.path}?token={token}").status_code == 401
    assert alice.get(f"{art_url.path}?{art_url.query}").status_code == 401
    with factory() as db:
        db.get(MusicCastTicket, ticket_id).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert alice.get(f"{parsed.path}?token={token}").status_code == 401


def test_cast_status_counts_playing_only_and_revokes_speaker_access_when_member_time_is_denied(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    ticket_id = uid(0xCA57)
    with factory() as db:
        db.add(MusicCastTicket(id=ticket_id, user_id=ALICE, speaker_id="speaker-1", token_hash="hash",
                               item_ids=[TRACKS[0]], expires_at=utcnow() + timedelta(hours=1)))
        db.commit()
    state = {"playing": True}
    beats, stops = [], []
    monkeypatch.setattr(music_sonos.service, "status", lambda _id: state)
    monkeypatch.setattr(music_sonos.service, "control", lambda _id, action, _value: stops.append(action) or {})
    monkeypatch.setattr(screen_time, "heartbeat", lambda _db, _user, **_kwargs: beats.append("playing"))
    alice = client_for(ALICE)
    url = f"/api/music/cast/{ticket_id}/status"
    assert alice.get(url).status_code == 200 and beats == ["playing"]
    state["playing"] = False
    assert alice.get(url).status_code == 200 and beats == ["playing"]
    with factory() as db:
        db.add(MemberAccess(user_id=ALICE, daily_limit_minutes=0))
        db.commit()
    screen_time.forget(ALICE)
    reply = alice.get(url)
    assert (reply.status_code, reply.json()) == (403, {"detail": "screen_time_up"})
    assert stops == ["stop"]
    with factory() as db:
        assert db.get(MusicCastTicket, ticket_id).expires_at <= utcnow()


def test_photo_credit_is_only_returned_for_the_current_image(music_world):
    factory, client_for, _root = music_world
    with factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        artist.images = {'Primary': {'music': 'https://thumb.wikimedia.org/photo.jpg', 'tag': 'photo-tag'}}
        artist.metadata_json = {'artwork_credits': [{'image_type': 'Primary', 'source_url': 'https://commons.wikimedia.org/wiki/File:Photo.jpg', 'author': 'Photographer', 'license': 'CC BY', 'tag': 'photo-tag'}]}
        db.commit()
    client = client_for(ALICE)
    response = client.get(f'/api/titles/{ARTIST}')
    assert response.status_code == 200 and response.json()['music']['artwork_credits'][0]['author'] == 'Photographer'
    with factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        artist.images = {'Primary': {'upload': 'my-photo', 'tag': 'manual-tag'}}
        db.commit()
    assert client.get(f'/api/titles/{ARTIST}').json()['music']['artwork_credits'] == []


def test_lyrics_retry_recovers_a_cached_failure_but_does_not_duplicate_pending_work(music_world, monkeypatch):
    factory, client_for, _root = music_world
    key = music_providers.key_for('lyrics', 'Artist A', 'Song 1.1', 'Album One', '180.0')
    calls = []
    monkeypatch.setattr(music_providers, 'enqueue', lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(music_providers, 'pending', lambda _key: False)
    with factory() as db:
        db.add(MusicLookup(key=key, kind='lyrics', payload={'error': 'Music provider is temporarily unavailable'}, refreshed_at=utcnow() - timedelta(minutes=5), expires_at=utcnow() + timedelta(hours=1)))
        db.commit()
    client = client_for(ALICE)
    assert client.get(f'/api/music/tracks/{TRACKS[0]}/lyrics?retry=true').status_code == 200
    assert calls[-1] == {'force': True}
    monkeypatch.setattr(music_providers, 'pending', lambda _key: True)
    client.get(f'/api/music/tracks/{TRACKS[0]}/lyrics?retry=true')
    assert calls[-1] == {}
    monkeypatch.setattr(music_providers, 'pending', lambda _key: False)
    with factory() as db:
        db.get(MusicLookup, key).refreshed_at = utcnow()
        db.commit()
    reply = client.get(f'/api/music/tracks/{TRACKS[0]}/lyrics?retry=true').json()
    assert calls[-1] == {} and reply['retry_after_seconds'] > 0
    with factory() as db:
        db.get(MusicLookup, key).payload = {'plain': 'Saved words', 'cues': []}
        db.commit()
    client.get(f'/api/music/tracks/{TRACKS[0]}/lyrics?retry=true')
    assert calls[-1] == {}


def test_album_file_quality_is_visible_track_weighted_and_batched(music_world) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    from sqlalchemy import event
    from app.services.titles import TitleService

    facts = [("flac", 24, 96000, 1800000, 100), ("alac", 16, 44100, 900000, 200), ("aac", 32, 44100, 256000, 300)]
    with factory() as db:
        for item_id, (codec, depth, rate, bitrate, duration) in zip(TRACKS, facts, strict=True):
            db.get(LibraryItem, item_id).duration = duration
            artifact = db.scalar(select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id).where(LibraryItemArtifact.library_item_id == item_id))
            artifact.probe = {"audio_codec": codec, "duration": duration, "streams": [
                {"type": "video", "codec": "mjpeg", "bit_rate": 999999999},
                {"type": "audio", "codec": codec, "bit_depth": depth, "sample_rate": rate, "bit_rate": bitrate},
            ]}
        # Neither a private nor a missing track can affect the public album's quality.
        for item_id in (PRIVATE_TRACK, MISSING_TRACK):
            artifact = db.scalar(select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id).where(LibraryItemArtifact.library_item_id == item_id))
            artifact.probe = {"audio_codec": "hidden", "streams": [{"type": "audio", "codec": "hidden", "sample_rate": 192000, "bit_depth": 64, "bit_rate": 99000000}]}
        db.commit()

    client = client_for(ALICE)
    album = client.get(f"/api/titles/{ALBUM}")
    assert album.status_code == 200
    data = album.json()
    quality = data["audio_quality"]
    assert quality == {"track_count": 3, "codecs": ["aac", "alac", "flac"], "bit_depths": [16, 24], "sample_rates": [44100, 96000], "bitrate_kbps": 728, "formats_complete": True}
    assert data["tracks"][0]["audio_quality"] == {"codec": "flac", "bitrate_kbps": 1800, "bit_depth": 24, "sample_rate": 96000}
    assert data["tracks"][2]["audio_quality"]["bit_depth"] is None, "A decoder's float precision is not a lossy file's bit depth"
    artist = client.get(f"/api/titles/{ARTIST}").json()
    assert next(row for row in artist["children"] if row["id"] == ALBUM)["audio_quality"] == quality
    catalog = client.get("/api/titles?type=album").json()
    assert next(row for row in catalog["items"] if row["id"] == ALBUM)["audio_quality"] == quality

    with factory() as db:
        service = TitleService(db)
        user = db.get(User, ALICE)
        albums = [db.get(MediaTitle, ALBUM), db.get(MediaTitle, SECOND_ALBUM)]
        service.load(user, albums, with_artwork=False)
        counts = []
        for page in ([albums[0]], albums):
            queries = []
            def capture(_conn, _cursor, sql, _params, _context, _many):  # noqa: ANN001, ANN202
                if sql.lstrip().upper().startswith("SELECT"):
                    queries.append(sql)
            event.listen(db.bind, "before_cursor_execute", capture)
            try:
                batch = service.load(user, page, with_artwork=False)
            finally:
                event.remove(db.bind, "before_cursor_execute", capture)
            assert batch.audio_quality[ALBUM].bitrate_kbps == 728
            counts.append(len(queries))
        assert counts[0] == counts[1], "Album cards must not fetch quality one album at a time"
        artifact = db.scalar(select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id).where(LibraryItemArtifact.library_item_id == TRACKS[0]))
        artifact.probe = {"audio_codec": "flac", "streams": [{"type": "audio", "codec": "flac", "sample_rate": "invalid", "bit_depth": True, "bit_rate": -1}]}
        artifact.size = None
        db.commit()
    partial = client.get(f"/api/titles/{ALBUM}").json()["audio_quality"]
    assert partial["bitrate_kbps"] is None, "Do not average only the known tracks and imply full album coverage"
    assert partial["formats_complete"] is False
    assert partial["sample_rates"] == [44100]
