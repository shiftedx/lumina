"""Stable MusicBrainz identity at the local music scanner boundary."""
from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC
from sqlalchemy import func, select

from app import db as db_module
from app.models import LibraryItem, MediaTitle, MusicTrackOverride
from app.services.audio_tags import AudioTags
from app.services.local_metadata import _music
from test_audio_tags import music_fixture, needs_ffmpeg
from test_v1_titles import admin, media, scan  # noqa: F401

ARTIST_A = "12345678-1234-4678-9234-123456789012"
ARTIST_B = "22345678-1234-4678-9234-123456789012"
RELEASE_A = "32345678-1234-4678-9234-123456789012"
RELEASE_B = "42345678-1234-4678-9234-123456789012"
RELEASE_C = "52345678-1234-4678-9234-123456789012"
GROUP = "62345678-1234-4678-9234-123456789012"


def _write(path: Path, *, title: str, album: str, artist: str = "Same Artist", artist_id: str = ARTIST_A,
           release_id: str = RELEASE_A, disc: int = 1, performer_id: str | None = None) -> Path:
    music_fixture().write_track(path, {
        "title": title, "artist": artist, "album_artist": "Same Artist", "album": album,
        "track": "1", "disc": str(disc),
    })
    audio = FLAC(path)
    audio["musicbrainz_albumartistid"] = [artist_id]
    audio["musicbrainz_artistid"] = [performer_id or artist_id]
    audio["musicbrainz_albumid"] = [release_id]
    audio["musicbrainz_releasegroupid"] = [GROUP]
    audio.save()
    return path


def _retag_release(path: Path, release_id: str) -> None:
    audio = FLAC(path)
    audio["musicbrainz_albumid"] = [release_id]
    audio.save()


def _title(db, key: str) -> MediaTitle:  # noqa: ANN001
    title = db.scalar(select(MediaTitle).where(MediaTitle.key == key))
    assert title is not None
    return title


def test_identity_keys_use_release_and_album_artist_ids_but_not_compilation_performers(tmp_path: Path) -> None:
    (tmp_path / "Compilations" / "Mix").mkdir(parents=True)
    performer = AudioTags(title="One", artist="Singer One", album="Mix", compilation=True,
                          musicbrainz_artist_id=ARTIST_A, musicbrainz_album_id=RELEASE_A)
    artist, album = _music(tmp_path, ("Compilations", "Mix"), "01 One", performer, {}, None)[0]
    assert artist["key"] == "artist:various artists"
    assert album["key"] == f"album:musicbrainz:{RELEASE_A}"

    credited = AudioTags(title="One", artist="Singer One", album="Mix", compilation=True,
                         musicbrainz_artist_id=ARTIST_A, musicbrainz_album_artist_id=ARTIST_B,
                         musicbrainz_album_id=RELEASE_A)
    artist, _album = _music(tmp_path, ("Compilations", "Mix"), "01 One", credited, {}, None)[0]
    assert artist["key"] == f"artist:musicbrainz:{ARTIST_B}"


@needs_ffmpeg
def test_same_named_releases_and_provider_key_reconciliation_are_stable(admin, media: Path) -> None:  # noqa: ANN001
    root = media / "Music"
    _write(root / "Edition A" / "CD1" / "01 One.flac", title="One", album="Shared Name", release_id=RELEASE_A)
    _write(root / "Edition A" / "CD2" / "01 Two.flac", title="Two", album="Shared Name", release_id=RELEASE_A, disc=2)
    _write(root / "Edition B" / "01 Three.flac", title="Three", album="Shared Name", release_id=RELEASE_B)
    assert scan(admin)["state"] == "succeeded"

    with db_module.SessionLocal() as db:
        first = _title(db, f"album:musicbrainz:{RELEASE_A}")
        second = _title(db, f"album:musicbrainz:{RELEASE_B}")
        assert first.id != second.id and first.parent_id == second.parent_id
        assert db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.title_id == first.id)) == 2
        assert db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.title_id == second.id)) == 1
        identities = first.id, second.id
        first.key = "album:legacy-provider-key"
        db.commit()

    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        assert (_title(db, f"album:musicbrainz:{RELEASE_A}").id,
                _title(db, f"album:musicbrainz:{RELEASE_B}").id) == identities
        assert db.scalar(select(func.count()).select_from(MediaTitle).where(MediaTitle.type == "album")) == 2


@needs_ffmpeg
def test_homonymous_album_artists_are_separated_by_album_artist_id(admin, media: Path) -> None:  # noqa: ANN001
    root = media / "Music"
    _write(root / "First" / "01 One.flac", title="One", album="First", artist_id=ARTIST_A,
           release_id=RELEASE_A, performer_id=RELEASE_C)
    _write(root / "Second" / "01 Two.flac", title="Two", album="Second", artist_id=ARTIST_B,
           release_id=RELEASE_B, performer_id=RELEASE_C)
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        first = _title(db, f"artist:musicbrainz:{ARTIST_A}")
        second = _title(db, f"artist:musicbrainz:{ARTIST_B}")
        assert first.id != second.id and first.name == second.name == "Same Artist"
        assert db.scalar(select(MediaTitle).where(MediaTitle.key == "artist:same artist")) is None


@needs_ffmpeg
def test_unambiguous_legacy_titles_are_adopted_without_losing_ids_art_or_user_fields(admin, media: Path) -> None:  # noqa: ANN001
    root = media / "Music" / "Same Artist" / "Legacy Album"
    paths = [music_fixture().write_track(root / f"0{number} Track {number}.flac", {
        "title": f"Track {number}", "artist": "Same Artist", "album_artist": "Same Artist", "album": "Legacy Album",
    }) for number in (1, 2)]
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        album = _title(db, "album:same artist/legacy album")
        artist = _title(db, "artist:same artist")
        album_id, artist_id = album.id, artist.id
        album.name = "Household edition"
        album.field_sources = {**(album.field_sources or {}), "name": "user", "images.Primary": "user"}
        album.images = {"Primary": {"upload": "household-art"}}
        db.commit()

    for path in paths:
        audio = FLAC(path)
        audio["musicbrainz_albumartistid"] = [ARTIST_A]
        audio["musicbrainz_albumid"] = [RELEASE_A]
        audio.save()
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        album = _title(db, f"album:musicbrainz:{RELEASE_A}")
        artist = _title(db, f"artist:musicbrainz:{ARTIST_A}")
        assert (album.id, artist.id) == (album_id, artist_id)
        assert album.name == "Household edition" and album.images["Primary"] == {"upload": "household-art"}
        assert album.provider_ids["MusicBrainz"] == RELEASE_A


@needs_ffmpeg
def test_manual_regroup_stays_put_when_the_source_release_identity_changes(admin, media: Path) -> None:  # noqa: ANN001
    root = media / "Music"
    source_path = _write(root / "Source" / "01 Source.flac", title="Source", album="Source", release_id=RELEASE_A)
    _write(root / "Destination" / "01 Destination.flac", title="Destination", album="Destination", release_id=RELEASE_B)
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Source"))
        source = _title(db, f"album:musicbrainz:{RELEASE_A}")
        destination = _title(db, f"album:musicbrainz:{RELEASE_B}")
        assert item is not None
        item_id, source_id, destination_id = item.id, source.id, destination.id

    response = admin.post("/api/music/tracks/edit", json={"item_ids": [item_id], "changes": {"album_id": destination_id}})
    assert response.status_code == 200, response.text
    _retag_release(source_path, RELEASE_C)
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        item = db.get(LibraryItem, item_id)
        override = db.get(MusicTrackOverride, item_id)
        assert item is not None and override is not None and item.title_id == destination_id
        assert override.fields["_source_album_id"] == source_id
        assert _title(db, f"album:musicbrainz:{RELEASE_B}").id == destination_id
        assert _title(db, f"album:musicbrainz:{RELEASE_C}").id not in (source_id, destination_id)


@needs_ffmpeg
def test_locked_edited_release_is_preserved_when_a_rescan_reports_a_different_release(admin, media: Path) -> None:  # noqa: ANN001
    path = _write(media / "Music" / "Locked" / "01 Track.flac", title="Track", album="Locked", release_id=RELEASE_A)
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        original = _title(db, f"album:musicbrainz:{RELEASE_A}")
        original_id = original.id
        original.name = "My locked edition"
        original.field_sources = {**(original.field_sources or {}), "name": "user"}
        original.locked = True
        db.commit()

    _retag_release(path, RELEASE_B)
    assert scan(admin)["state"] == "succeeded"
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        original = _title(db, f"album:musicbrainz:{RELEASE_A}")
        replacement = _title(db, f"album:musicbrainz:{RELEASE_B}")
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Track"))
        assert original.id == original_id and original.locked and original.name == "My locked edition"
        assert original.provider_ids["MusicBrainz"] == RELEASE_A
        assert replacement.id != original_id and replacement.provider_ids["MusicBrainz"] == RELEASE_B
        assert item is not None and item.title_id == replacement.id
