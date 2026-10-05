"""Library gallery T3: embedded audio tags and pictures."""
from __future__ import annotations

import base64
import importlib.util
import shutil
from pathlib import Path

import mutagen
import pytest
from mutagen.flac import FLAC, Picture

from app.services.audio_tags import AudioTags, embedded_picture, read_tags

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
REPO = Path(__file__).resolve().parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
TAGS = {
    "title": "Song One", "artist": "Artist A", "album_artist": "Artist A", "album": "Album One (Disc 2)", "track": "1/3",
    "date": "2019-05-01", "genre": "Folk; Rock/folk;Jazz;Blues", "compilation": "1",
}
EXPECTED = AudioTags(
    title="Song One", artist="Artist A", album_artist="Artist A", album="Album One", track=1, disc=2, year=2019,
    genres=("Folk", "Rock", "Jazz"), compilation=True,
)


def music_fixture():  # noqa: ANN201
    """scripts/e2e/music_fixture.py, loaded by path (scripts is not a package)."""
    spec = importlib.util.spec_from_file_location("music_fixture", REPO / "scripts" / "e2e" / "music_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _picture(kind: int, mime: str, data: bytes) -> Picture:
    picture = Picture()
    picture.type, picture.mime, picture.data = kind, mime, data
    return picture


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["flac", "mp3", "m4a", "opus"])
def test_every_format_reads_the_same_fields(tmp_path, codec) -> None:  # noqa: ANN001
    """ID3, Vorbis comments (FLAC, Opus) and MP4 atoms map to the same fields; "(Disc 2)" moves into the disc."""
    path = music_fixture().write_track(tmp_path / f"song.{codec}", TAGS)
    assert read_tags(path) == EXPECTED


def test_a_record_round_trips_and_an_unreadable_file_records_only_its_fingerprint() -> None:
    record = EXPECTED.record("10:20:30")
    assert record["fp"] == "10:20:30" and record["genres"] == ["Folk", "Rock", "Jazz"]
    assert set(record) == {"fp", "title", "artist", "album_artist", "album", "track", "disc", "year", "genres", "compilation", "has_picture"}
    assert AudioTags.from_record(record) == EXPECTED
    assert AudioTags.from_record({"fp": "10:20:30"}) is None


@needs_ffmpeg
def test_values_are_normalised_and_bounded(tmp_path) -> None:  # noqa: ANN001
    path = music_fixture().write_track(tmp_path / "odd.flac", {})
    audio = FLAC(path)
    audio["title"] = ["  Two   Words "]
    audio["artist"] = ["x" * 301, "Fallback Artist"]
    audio["album"] = ["Live [CD 3]"]
    audio["tracknumber"] = ["0"]
    audio["discnumber"] = ["1000"]
    audio["date"] = ["0999-01-01"]
    audio["genre"] = [" ; / "]
    audio.save()
    assert read_tags(path) == AudioTags(title="Two Words", artist="Fallback Artist", album="Live", disc=3)


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["flac", "mp3", "m4a"])
def test_an_attached_cover_is_flagged_and_served_but_never_kept(tmp_path, codec) -> None:  # noqa: ANN001
    fixture = music_fixture()
    cover = fixture.cover_image(tmp_path / "cover.jpg", "300x200")
    path = fixture.write_track(tmp_path / f"song.{codec}", {"album": "Album"}, cover=cover)
    tags = read_tags(path)
    assert tags.has_picture and all(not isinstance(value, bytes) for value in tags.record("1:2:3").values())
    assert embedded_picture(path, 8 * 1024 * 1024) == ("image/jpeg", cover.read_bytes())


@needs_ffmpeg
def test_an_opus_picture_block_counts(tmp_path) -> None:  # noqa: ANN001
    path = music_fixture().write_track(tmp_path / "song.opus", {"album": "Album"})
    audio = mutagen.File(path)
    audio["metadata_block_picture"] = [base64.b64encode(_picture(3, "image/png", PNG).write()).decode("ascii")]
    audio.save()
    assert read_tags(path).has_picture
    assert embedded_picture(path, 1024) == ("image/png", PNG)


@needs_ffmpeg
def test_the_front_cover_wins_and_only_jpeg_or_png_within_the_cap_is_served(tmp_path) -> None:  # noqa: ANN001
    path = music_fixture().write_track(tmp_path / "song.flac", {"album": "Album"})
    audio = FLAC(path)
    audio.add_picture(_picture(0, "image/gif", b"GIF89a" + b"\x00" * 10))
    audio.add_picture(_picture(3, "image/png", PNG))
    audio.save()
    assert embedded_picture(path, 1024) == ("image/png", PNG)
    with pytest.raises(FileNotFoundError):
        embedded_picture(path, len(PNG) - 1)  # over the cap
    audio.clear_pictures()
    audio.add_picture(_picture(3, "image/gif", b"GIF89a" + b"\x00" * 10))
    audio.save()
    with pytest.raises(FileNotFoundError):
        embedded_picture(path, 1024)  # neither JPEG nor PNG


def test_broken_or_foreign_files_give_none(tmp_path) -> None:  # noqa: ANN001
    for name, data in (("junk.mp3", b"\x00fake"), ("truncated.flac", b"fLaC\x00\x00"), ("empty.m4a", b"")):
        (tmp_path / name).write_bytes(data)
        assert read_tags(tmp_path / name) is None, name
    assert read_tags(tmp_path / "absent.flac") is None
    (tmp_path / "folder.flac").mkdir()
    assert read_tags(tmp_path / "folder.flac") is None
    with pytest.raises(FileNotFoundError):
        embedded_picture(tmp_path / "junk.mp3", 1024)
    with pytest.raises(FileNotFoundError):
        embedded_picture(tmp_path / "truncated.flac", 1024)


def test_a_parser_crash_is_file_not_found(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "odd.flac").write_bytes(b"fLaC")

    def crash(handle):  # noqa: ANN001, ANN202
        raise ValueError("mutagen tripped")

    monkeypatch.setattr(mutagen, "File", crash)
    with pytest.raises(FileNotFoundError):
        embedded_picture(tmp_path / "odd.flac", 1024)


def test_a_fifo_named_like_a_track_never_blocks(tmp_path) -> None:  # noqa: ANN001
    import os
    import threading

    os.mkfifo(tmp_path / "pipe.flac")
    outcome: list[object] = []

    def read() -> None:
        outcome.append(read_tags(tmp_path / "pipe.flac"))
        try:
            embedded_picture(tmp_path / "pipe.flac", 1024)
        except FileNotFoundError as exc:
            outcome.append(type(exc))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(5)
    assert outcome == [None, FileNotFoundError]


@needs_ffmpeg
def test_a_symlinked_track_is_never_opened(tmp_path) -> None:  # noqa: ANN001
    fixture = music_fixture()
    real = fixture.write_track(tmp_path / "real.flac", TAGS, cover=fixture.cover_image(tmp_path / "cover.jpg", "200x200"))
    (tmp_path / "link.flac").symlink_to(real)
    assert read_tags(real) is not None
    assert read_tags(tmp_path / "link.flac") is None
    with pytest.raises(FileNotFoundError):
        embedded_picture(tmp_path / "link.flac", 1024 * 1024)
