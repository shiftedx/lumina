"""Source audio-tag contract for the music scanner."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import mutagen
import pytest
from mutagen.id3 import ID3, SYLT, TXXX, USLT
from mutagen.mp4 import MP4FreeForm

from app.services.audio_tags import read_tags

REPO = Path(__file__).resolve().parents[2]
UUIDS = (
    "12345678-1234-4678-9234-123456789012",
    "22345678-1234-4678-9234-123456789012",
    "32345678-1234-4678-9234-123456789012",
    "42345678-1234-4678-9234-123456789012",
)
ALBUM_ARTIST_ID = "52345678-1234-4678-9234-123456789012"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def music_fixture():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("music_fixture", REPO / "scripts" / "e2e" / "music_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _freeform(audio, name: str, value: str) -> None:  # noqa: ANN001
    audio.tags[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(value.encode("utf-8"))]


def _add_source_tags(path: Path, codec: str, *, oversized_lyrics: bool = False) -> None:
    audio = mutagen.File(path)
    assert audio is not None
    if codec == "mp3":
        assert isinstance(audio.tags, ID3)
        for name, value in (
            ("MusicBrainz Artist Id", UUIDS[0]), ("MusicBrainz Album Artist Id", ALBUM_ARTIST_ID), ("MusicBrainz Album Id", UUIDS[1]),
            ("MusicBrainz Release Group Id", UUIDS[2]), ("MusicBrainz Recording Id", UUIDS[3]),
            ("REPLAYGAIN_TRACK_GAIN", "-7.25 dB"), ("REPLAYGAIN_ALBUM_GAIN", "-8.50 dB"),
            ("REPLAYGAIN_TRACK_PEAK", "1.02"), ("REPLAYGAIN_ALBUM_PEAK", "0.98"),
            ("Edition", "Deluxe"), ("Disc Subtitle", "Disc One"), ("Label", "Example Label"),
            ("Catalog Number", "CAT-001"), ("Composer", "Composer One"), ("Performer", "Performer One"),
        ):
            audio.tags.add(TXXX(encoding=3, desc=name, text=[value]))
        if oversized_lyrics:
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text="x" * 100_001))
        else:
            audio.tags.add(SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=[("Synced line", 1_230)]))
        audio.save()
        return
    if codec == "m4a":
        for name, value in (
            ("MusicBrainz Artist Id", UUIDS[0]), ("MusicBrainz Album Artist Id", ALBUM_ARTIST_ID), ("MusicBrainz Album Id", UUIDS[1]),
            ("MusicBrainz Release Group Id", UUIDS[2]), ("MusicBrainz Recording Id", UUIDS[3]),
            ("REPLAYGAIN_TRACK_GAIN", "-7.25 dB"), ("REPLAYGAIN_ALBUM_GAIN", "-8.50 dB"),
            ("REPLAYGAIN_TRACK_PEAK", "1.02"), ("REPLAYGAIN_ALBUM_PEAK", "0.98"),
            ("Edition", "Deluxe"), ("Disc Subtitle", "Disc One"), ("Label", "Example Label"),
            ("Catalog Number", "CAT-001"),
        ):
            _freeform(audio, name, value)
        audio.tags["©wrt"] = ["Composer One"]
        audio.tags["©lyr"] = ["x" * 100_001 if oversized_lyrics else "Plain lyric"]
        audio.save()
        return
    values = {
        "musicbrainz_artistid": UUIDS[0], "musicbrainz_albumartistid": ALBUM_ARTIST_ID, "musicbrainz_albumid": UUIDS[1],
        "musicbrainz_releasegroupid": UUIDS[2], "musicbrainz_trackid": UUIDS[3],
        "replaygain_track_gain": "-7.25 dB", "replaygain_album_gain": "-8.50 dB",
        "replaygain_track_peak": "1.02", "replaygain_album_peak": "0.98",
        "edition": "Deluxe", "discsubtitle": "Disc One", "label": "Example Label", "catalognumber": "CAT-001",
        "composer": "Composer One", "performer": "Performer One",
        "lyrics": "x" * 100_001 if oversized_lyrics else "Plain lyric",
    }
    for name, value in values.items():
        audio[name] = [value]
    audio.save()


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["flac", "mp3", "ogg", "opus", "m4a"])
def test_musicbrainz_gain_lyrics_and_credit_fields_are_native_format_independent(tmp_path: Path, codec: str) -> None:
    fixture = music_fixture()
    path = tmp_path / f"track.{codec}"
    if codec == "ogg":
        path.parent.mkdir(parents=True, exist_ok=True)
        fixture._ffmpeg(*fixture.SINE, "-ac", "2", "-strict", "-2", "-c:a", "vorbis", path)
    else:
        fixture.write_track(path, {"title": "Song", "artist": "Artist", "album": "Album"}, codec=codec)
    _add_source_tags(path, codec)

    tags = read_tags(path)
    assert tags is not None
    assert (tags.musicbrainz_artist_id, tags.musicbrainz_album_id, tags.musicbrainz_release_group_id, tags.musicbrainz_recording_id) == UUIDS
    assert tags.musicbrainz_album_artist_id == ALBUM_ARTIST_ID
    assert (tags.replaygain_track_gain_db, tags.replaygain_album_gain_db, tags.replaygain_track_peak, tags.replaygain_album_peak) == (
        -7.25, -8.5, 1.02, 0.98,
    )
    assert tags.lyrics == ("[00:01.23]Synced line" if codec == "mp3" else "Plain lyric")
    assert tags.composers == ("Composer One",)
    assert tags.performers == (("Performer One",) if codec in {"flac", "ogg", "mp3", "opus"} else ())
    assert (tags.edition, tags.disc_subtitle, tags.label, tags.catalogue_number) == ("Deluxe", "Disc One", "Example Label", "CAT-001")

    record = tags.record("1:2:3")
    assert record["musicbrainz_artist_id"] == UUIDS[0]
    assert record["replaygain_track_gain_db"] == -7.25
    assert record["lyrics"] == tags.lyrics
    assert type(tags).from_record(record) == tags


@needs_ffmpeg
def test_audio_tag_identity_and_loudness_values_fail_closed_at_their_bounds(tmp_path: Path) -> None:
    path = music_fixture().write_track(tmp_path / "bad.flac", {"title": "Song"}, codec="flac")
    audio = mutagen.File(path)
    assert audio is not None
    audio["musicbrainz_artistid"] = [UUIDS[0], UUIDS[1]]
    audio["musicbrainz_albumid"] = ["not-a-uuid"]
    audio["replaygain_track_gain"] = ["-30.01 dB"]
    audio["replaygain_album_gain"] = ["20.01 dB"]
    audio["replaygain_track_peak"] = ["-0.1"]
    audio["lyrics"] = ["x" * 100_001]
    audio.save()

    tags = read_tags(path)
    assert tags is not None
    assert tags.musicbrainz_artist_id is None and tags.musicbrainz_album_id is None
    assert tags.replaygain_track_gain_db is None and tags.replaygain_album_gain_db is None and tags.replaygain_track_peak is None
    assert tags.lyrics is None
