"""Music override behavior at the scanner boundary."""
from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from sqlalchemy import event, select

from app import db as db_module
from app.models import LibraryItem, MediaTitle, MusicTrackOverride, User
from app.services.library_search import LibrarySearchService
from app.services.music_library import track_fields
from test_audio_tags import music_fixture, needs_ffmpeg
from test_music_audio_tags import UUIDS
from test_v1_titles import admin, media, scan  # noqa: F401


def _item(item_id: str) -> tuple[LibraryItem, MusicTrackOverride | None]:
    with db_module.SessionLocal() as db:
        item = db.get(LibraryItem, item_id)
        override = db.get(MusicTrackOverride, item_id)
        assert item is not None
        db.expunge_all()
        return item, override


def _album(db, key: str) -> MediaTitle:  # noqa: ANN001
    return db.scalar(select(MediaTitle).where(MediaTitle.type == "album", MediaTitle.key == key))


@needs_ffmpeg
def test_manual_regroup_and_track_edits_survive_source_rescan_without_rekeying_destination(admin, media: Path) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.build_music(media / "Music")
    assert scan(admin)["state"] == "succeeded"

    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Song One"))
        source = _album(db, "album:artist a/album one")
        destination = _album(db, "album:artist a/album two")
        assert item is not None and source is not None and destination is not None
        item_id, source_id, destination_id = item.id, source.id, destination.id
        destination_key, destination_name = destination.key, destination.name
        source_path = media / "Music" / "Artist A" / "Album One (2019)" / "01 - Song One.flac"

    edited = admin.post("/api/music/tracks/edit", json={
        "item_ids": [item_id],
        "changes": {"album_id": destination_id, "title": "Household title", "track_number": 9, "disc_number": 4},
    })
    assert edited.status_code == 200, edited.text
    with db_module.SessionLocal() as db:
        other_ids = list(db.scalars(select(LibraryItem.id).where(
            LibraryItem.title_id == destination_id, LibraryItem.id != item_id,
        )))
    moved = admin.post("/api/music/tracks/edit", json={"item_ids": other_ids, "changes": {"album_id": source_id}})
    assert moved.status_code == 200, moved.text

    audio = FLAC(source_path)
    audio["title"] = ["Freshsourcetitle"]
    audio["artist"] = ["Fresh source artist"]
    audio["albumartist"] = ["Fresh source artist"]
    audio["album"] = ["Fresh source album"]
    audio["tracknumber"] = ["2/2"]
    audio["discnumber"] = ["1/1"]
    audio.save()
    statements: list[str] = []

    def capture(_connection, _cursor, statement, *_args):  # noqa: ANN001
        statements.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", capture)
    try:
        assert scan(admin)["state"] == "succeeded"
    finally:
        event.remove(db_module.engine, "before_cursor_execute", capture)
    assert len([statement for statement in statements if "FROM music_track_overrides" in statement]) == 1

    item, override = _item(item_id)
    assert override is not None
    assert item.title_id == destination_id
    assert item.title == "Freshsourcetitle"
    assert item.metadata_json["artist"] == "Fresh source artist"
    assert item.metadata_json["album"] == "Fresh source album"
    assert (item.metadata_json["track_number"], item.metadata_json["disc_number"]) == (2, 1)
    assert track_fields(item, override) == {
        "title": "Household title", "artist": "Fresh source artist", "album_artist": "Fresh source artist",
        "album_id": destination_id, "track_number": 9, "disc_number": 4, "genres": ["Folk"], "lyrics": None, "gain_db": None,
    }
    assert override.fields["_source_album_id"] == source_id
    with db_module.SessionLocal() as db:
        search = LibrarySearchService(db)
        user = db.get(User, "admin")
        assert search.item_ids_for_query(user, "household title", limit=10) == [item_id]
        assert search.item_ids_for_query(user, "freshsourcetitle", limit=10) == []

    with db_module.SessionLocal() as db:
        destination = db.get(MediaTitle, destination_id)
        assert destination is not None and (destination.key, destination.name) == (destination_key, destination_name)
        assert db.scalar(select(MediaTitle).where(MediaTitle.key == "album:fresh source artist/fresh source album")) is not None


@needs_ffmpeg
def test_invalid_manual_destination_falls_back_without_losing_revert_metadata(admin, media: Path) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.build_music(media / "Music")
    assert scan(admin)["state"] == "succeeded"

    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Song One"))
        source = _album(db, "album:artist a/album one")
        destination = _album(db, "album:artist a/album two")
        assert item is not None and source is not None and destination is not None
        item_id, source_id, destination_id = item.id, source.id, destination.id

    edited = admin.post("/api/music/tracks/edit", json={"item_ids": [item_id], "changes": {"album_id": destination_id}})
    assert edited.status_code == 200, edited.text
    with db_module.SessionLocal() as db:
        destination = db.get(MediaTitle, destination_id)
        assert destination is not None
        destination.type = "artist"  # a stale/non-album target must not be treated as a music destination
        db.commit()

    assert scan(admin)["state"] == "succeeded"
    item, override = _item(item_id)
    assert override is not None
    assert item.title_id == source_id
    assert override.fields["album_id"] == destination_id
    assert override.fields["_source_album_id"] == source_id


@needs_ffmpeg
def test_clearing_manual_album_uses_the_original_source_before_the_next_source_link(admin, media: Path) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.build_music(media / "Music")
    assert scan(admin)["state"] == "succeeded"

    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Song One"))
        source = _album(db, "album:artist a/album one")
        destination = _album(db, "album:artist a/album two")
        assert item is not None and source is not None and destination is not None
        item_id, source_id, destination_id = item.id, source.id, destination.id

    assert admin.post("/api/music/tracks/edit", json={"item_ids": [item_id], "changes": {"album_id": destination_id}}).status_code == 200
    source_path = media / "Music" / "Artist A" / "Album One (2019)" / "01 - Song One.flac"
    audio = mutagen.File(source_path)
    assert audio is not None
    audio["album"] = ["Changed source album"]
    audio.save()
    assert scan(admin)["state"] == "succeeded"

    cleared = admin.post("/api/music/tracks/edit", json={"item_ids": [item_id], "changes": {"album_id": None}})
    assert cleared.status_code == 200, cleared.text
    item, override = _item(item_id)
    assert override is not None and "album_id" not in override.fields
    assert item.title_id == source_id
    assert override.fields["_source_album_id"] == source_id


@needs_ffmpeg
def test_source_audio_fields_refresh_and_musicbrainz_ids_use_title_provenance(admin, media: Path) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.build_music(media / "Music")
    album_paths = sorted((media / "Music" / "Artist A" / "Album One (2019)").glob("*.flac"))
    source_path = album_paths[0]
    for path in album_paths:
        audio = FLAC(path)
        audio["musicbrainz_artistid"] = [UUIDS[0]]
        audio["musicbrainz_albumid"] = [UUIDS[1]]
        audio["musicbrainz_releasegroupid"] = [UUIDS[2]]
        audio["musicbrainz_trackid"] = [UUIDS[3]]
        audio["replaygain_track_gain"] = ["-7.25 dB"]
        audio["replaygain_album_gain"] = ["-8.5 dB"]
        audio["replaygain_track_peak"] = ["1.02"]
        audio["lyrics"] = ["Source lyric"]
        audio.save()
    assert scan(admin)["state"] == "succeeded"

    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Song One"))
        source = _album(db, f"album:musicbrainz:{UUIDS[1]}")
        assert item is not None and source is not None and source.parent_id is not None
        artist = db.get(MediaTitle, source.parent_id)
        assert artist is not None
        assert item.metadata_json["musicbrainz_recording_id"] == UUIDS[3]
        assert item.metadata_json["replaygain_track_gain_db"] == -7.25
        assert item.metadata_json["replaygain_album_gain_db"] == -8.5
        assert item.metadata_json["replaygain_track_peak"] == 1.02
        assert item.metadata_json["lyrics"] == "Source lyric"
        assert source.provider_ids["MusicBrainz"] == UUIDS[1]
        assert source.field_sources["provider_ids"] == "musicbrainz"
        assert artist.provider_ids["MusicBrainz"] == UUIDS[0]

        source.locked = True
        db.commit()
        for path in album_paths:
            audio = FLAC(path)
            audio["musicbrainz_albumid"] = ["52345678-1234-4678-9234-123456789012"]
            audio.save()
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        source = _album(db, f"album:musicbrainz:{UUIDS[1]}")
        assert source is not None and source.provider_ids["MusicBrainz"] == UUIDS[1]
