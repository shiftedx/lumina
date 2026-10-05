"""Tagged audio for the music tests and the realstack Music journey.

``build_music(root)`` writes, under ``root`` (the Music folder), four albums by three album artists:

  Artist A/Album One (2019)/01 - Song One.flac … 03 - Song Three.flac   fully tagged, plus a 300x200 cover.jpg
  Artist A/Album Two/CD1/01 - Opening.flac, CD2/01 - Closing.flac         discs 1 and 2, an embedded cover only
  Compilations/Mix/01 - First.flac, 02 - Second.flac                      compilation=1, artists Singer B and Singer C
  Untagged Artist/Untagged Album/01 Intro.mp3                               no tags

``add_theme_music(show_folder)`` writes a 1 s theme.mp3. Needs ffmpeg with flac, libmp3lame, aac and libopus.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

CODECS = {
    "flac": ["-c:a", "flac"], "mp3": ["-c:a", "libmp3lame", "-q:a", "9"], "m4a": ["-c:a", "aac"], "opus": ["-c:a", "libopus"],
}
SINE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]


def _ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)], check=True)


def cover_image(path: Path, size: str) -> Path:
    """A solid JPEG of ``size`` ("300x200")."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"color=c=0x356258:size={size}", "-frames:v", "1", path)
    return path


def write_track(path: Path, tags: dict[str, str], *, codec: str | None = None, cover: Path | None = None) -> Path:
    """A 1 s sine at ``path`` with ffmpeg ``-metadata`` tags and, optionally, ``cover`` attached as its picture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    art = ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"] if cover else []
    metadata = [part for key, value in tags.items() for part in ("-metadata", f"{key}={value}")]
    _ffmpeg(*SINE, *art, *CODECS[codec or path.suffix.lstrip(".")], *metadata, path)
    return path


def build_music(root: Path) -> None:
    album_one = root / "Artist A" / "Album One (2019)"
    cover_image(album_one / "cover.jpg", "300x200")  # 3:2, so the square crop is proven
    for number, name in enumerate(("Song One", "Song Two", "Song Three"), start=1):
        write_track(album_one / f"{number:02d} - {name}.flac", {
            "title": name, "artist": "Artist A", "album_artist": "Artist A", "album": "Album One",
            "track": f"{number}/3", "date": "2019", "genre": "Folk",
        })
    with tempfile.TemporaryDirectory() as scratch:  # the embedded cover's source stays outside the library
        embedded = cover_image(Path(scratch) / "front.jpg", "200x200")
        for disc, name in ((1, "Opening"), (2, "Closing")):
            write_track(root / "Artist A" / "Album Two" / f"CD{disc}" / f"01 - {name}.flac", {
                "title": name, "artist": "Artist A", "album_artist": "Artist A", "album": "Album Two", "track": "1",
                "disc": f"{disc}/2",
            }, cover=embedded)
    for number, (name, artist) in enumerate((("First", "Singer B"), ("Second", "Singer C")), start=1):
        write_track(root / "Compilations" / "Mix" / f"{number:02d} - {name}.flac", {
            "title": name, "artist": artist, "album": "Mix", "track": str(number), "compilation": "1",
        })
    write_track(root / "Untagged Artist" / "Untagged Album" / "01 Intro.mp3", {})


def add_theme_music(show_folder: Path) -> None:
    write_track(show_folder / "theme.mp3", {})
