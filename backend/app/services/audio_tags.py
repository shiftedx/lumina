"""Embedded audio tags for the music library.

A file is opened read-only and never through a symlink, and a broken or hostile file only ever yields None (tags) or
FileNotFoundError (pictures). Only tag text is kept: pictures are recorded as ``has_picture``, and
``embedded_picture`` reads one on demand for album art, never storing it.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import stat
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Cover, MP4Tags

logger = logging.getLogger(__name__)

MAX_TEXT = 300
MAX_GENRES = 3
FRONT_COVER = 3  # ID3 APIC and FLAC picture type "Cover (front)"
DISC_SUFFIX = re.compile(r"\s*[\(\[]\s*(cd|disc|disk)\s*(\d+)\s*[\)\]]\s*$", re.IGNORECASE)
LEADING_DIGITS = re.compile(r"^\s*(\d+)")
YEAR_DIGITS = re.compile(r"^\s*(\d{4})")
PICTURE_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/png": "image/png"}
# The table: (ID3 frame, Vorbis comment keys in order, MP4 atom) for each text field.
TEXT_FIELDS = {
    "title": ("TIT2", ("title",), "©nam"),
    "artist": ("TPE1", ("artist",), "©ART"),
    "album_artist": ("TPE2", ("albumartist", "album artist"), "aART"),
    "album": ("TALB", ("album",), "©alb"),
}


@dataclass(frozen=True)
class AudioTags:
    """One file's normalised tags: numbers 1–999, the year 1000–2100, at most 3 genres; None or () when absent."""

    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    track: int | None = None
    disc: int | None = None
    year: int | None = None
    genres: tuple[str, ...] = ()
    compilation: bool = False
    has_picture: bool = False

    def record(self, fingerprint: str) -> dict[str, Any]:
        """The item's ``lumina_audio_tags`` (server-side only): these tags under the file's "size:mtime_ns:inode"."""
        return {"fp": fingerprint, **asdict(self), "genres": list(self.genres)}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> AudioTags | None:
        """A stored record back into tags; None for a file whose tags could not be read (a record of only ``fp``)."""
        if set(record) <= {"fp"}:
            return None
        values = {field.name: record.get(field.name) for field in fields(cls)}
        return cls(**{
            **values, "genres": tuple(values["genres"] or ()),
            "compilation": bool(values["compilation"]), "has_picture": bool(values["has_picture"]),
        })


def clean_text(value: Any) -> str | None:
    """Whitespace collapsed and stripped; None when empty or longer than 300 characters."""
    cleaned = " ".join(str(value).split())
    return cleaned if cleaned and len(cleaned) <= MAX_TEXT else None


def tag_number(value: Any) -> int | None:
    """The leading number of a track or disc value ("3", "3/12", 3) when it is 1–999."""
    match = LEADING_DIGITS.match(str(value)) if value is not None else None
    found = int(match.group(1)) if match else None
    return found if found is not None and 1 <= found <= 999 else None


def _tag_year(value: Any) -> int | None:
    match = YEAR_DIGITS.match(str(value)) if value is not None else None
    found = int(match.group(1)) if match else None
    return found if found is not None and 1000 <= found <= 2100 else None


def _genres(values: list[Any]) -> tuple[str, ...]:
    """Split on ; and /, deduped case-insensitively keeping the first spelling, at most 3."""
    kept: dict[str, str] = {}
    for value in values:
        for part in re.split(r"[;/]", str(value)):
            genre = clean_text(part)
            if genre and genre.casefold() not in kept:
                kept[genre.casefold()] = genre
    return tuple(kept.values())[:MAX_GENRES]


def _load(path: Path) -> Any:
    """mutagen's view of a regular file opened without following a symlink (None: an audio format it does not know)."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))  # a FIFO never blocks
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        return mutagen.File(handle)


def _raw(audio: Any) -> tuple[dict[str, list[Any]], bool, bool]:
    """(field -> raw values, compilation, has_picture) from whichever tag format the file carries."""
    tags = audio.tags
    if isinstance(tags, ID3):
        def frame(name: str) -> list[Any]:
            return list(tags[name].text) if name in tags else []

        values = {name: frame(id3) for name, (id3, _vorbis, _mp4) in TEXT_FIELDS.items()}
        values.update(
            track=frame("TRCK"), disc=frame("TPOS"), year=[str(stamp) for stamp in frame("TDRC")] or frame("TYER"),
            genres=list(tags["TCON"].genres) if "TCON" in tags else [],
        )
        compilation = any(str(value) == "1" for name in ("TCMP", "TXXX:TCMP") for value in frame(name))
        return values, compilation, bool(tags.getall("APIC"))
    if isinstance(tags, MP4Tags):
        values = {name: list(tags.get(atom, [])) for name, (_id3, _vorbis, atom) in TEXT_FIELDS.items()}
        values.update(
            track=[pair[0] for pair in tags.get("trkn", [])], disc=[pair[0] for pair in tags.get("disk", [])],
            year=list(tags.get("©day", [])), genres=list(tags.get("©gen", [])),
        )
        return values, bool(tags.get("cpil")), bool(tags.get("covr"))
    pictures = bool(getattr(audio, "pictures", None))
    if tags is None:
        return {}, False, pictures

    # Vorbis comments (FLAC, Ogg Vorbis, Opus): case-insensitive keys, each a list of strings.
    def comment(*keys: str) -> list[Any]:
        return next((list(tags[key]) for key in keys if key in tags), [])

    values = {name: comment(*vorbis) for name, (_id3, vorbis, _mp4) in TEXT_FIELDS.items()}
    values.update(track=comment("tracknumber"), disc=comment("discnumber"), year=comment("date", "year"), genres=comment("genre"))
    compilation = any(str(value) == "1" for value in comment("compilation"))
    return values, compilation, pictures or "metadata_block_picture" in tags


def read_tags(path: Path) -> AudioTags | None:
    """The file's normalised tags, or None when it is not readable audio. Never raises."""
    try:
        audio = _load(path)
        if audio is None:
            return None
        values, compilation, has_picture = _raw(audio)
    except (mutagen.MutagenError, OSError):
        return None
    except Exception as exc:  # noqa: BLE001 - a hostile file never fails a scan
        logger.warning("Skipped unreadable audio tags: %s", type(exc).__name__)
        return None

    def first(name: str, parse=clean_text):  # noqa: ANN001, ANN202
        return next((parsed for value in values.get(name, []) if (parsed := parse(value)) is not None), None)

    album, disc = first("album"), first("disc", tag_number)
    suffix = DISC_SUFFIX.search(album) if album else None
    if suffix:  # "Album (Disc 2)" is disc 2 of "Album"
        album, disc = album[: suffix.start()].strip() or None, disc or tag_number(suffix.group(2))
    return AudioTags(
        title=first("title"), artist=first("artist"), album_artist=first("album_artist"), album=album,
        track=first("track", tag_number), disc=disc, year=first("year", _tag_year), genres=_genres(values.get("genres", [])),
        compilation=compilation, has_picture=has_picture,
    )


def _pictures(audio: Any) -> list[tuple[int, str, bytes]]:
    """(picture type, MIME type, bytes) of every embedded picture in file order; an MP4 cover counts as a front cover."""
    tags = audio.tags
    if isinstance(tags, ID3):
        return [(int(frame.type), frame.mime, frame.data) for frame in tags.getall("APIC")]
    if isinstance(tags, MP4Tags):
        mime = {MP4Cover.FORMAT_JPEG: "image/jpeg", MP4Cover.FORMAT_PNG: "image/png"}
        return [(FRONT_COVER, mime.get(cover.imageformat, ""), bytes(cover)) for cover in tags.get("covr", [])]
    found = [(int(picture.type), picture.mime, picture.data) for picture in getattr(audio, "pictures", None) or []]
    for value in (tags.get("metadata_block_picture") or []) if tags is not None else []:
        try:
            picture = Picture(base64.b64decode(value))
        except Exception:  # noqa: BLE001 - a malformed picture block is skipped, like a missing one
            continue
        found.append((int(picture.type), picture.mime, picture.data))
    return found


def embedded_picture(path: Path, max_bytes: int) -> tuple[str, bytes]:
    """(content type, bytes) of a track's front cover, else its first picture.

    Only JPEG and PNG within ``max_bytes`` are served; anything else is FileNotFoundError, like a missing file.
    ``path`` must already be confined to its storage root (``media_artifacts.artifact_file``).
    """
    try:
        audio = _load(path)
        pictures = _pictures(audio) if audio is not None else []
    except Exception as exc:  # noqa: BLE001 - mutagen raises more than MutagenError on hostile files
        raise FileNotFoundError("The track is unreadable") from exc
    if not pictures:
        raise FileNotFoundError("The track has no picture")
    _kind, mime, data = next((picture for picture in pictures if picture[0] == FRONT_COVER), pictures[0])
    content_type = PICTURE_TYPES.get(str(mime).lower())
    if content_type is None or not data or len(data) > max_bytes:
        raise FileNotFoundError("Unsupported embedded picture")
    return content_type, data
