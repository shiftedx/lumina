"""Atomic, lossless metadata writeback for registered music artifacts.

The caller resolves and authorizes ``path`` before entering this module.  This
module deliberately has no path resolver: the only paths it accepts are the
already-confined artifact path and a caller-owned backup root.  Mutagen writes
only to a sibling copy, and the original is replaced after that copy and its
undo record are durable.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
from typing import Any
import uuid
import zlib

import mutagen
from mutagen.aac import AAC
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TCON, TDRC, TIT2, TALB, TPE1, TPE2, TRCK, TPOS, TXXX, UFID, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis


MAX_ARTWORK_BYTES = 8 * 1024 * 1024
_MAX_TEXT_BYTES = 1 * 1024 * 1024
_FRONT_COVER = 3
_MB_OWNER = "http://musicbrainz.org"
_UUID_REPR_LENGTH = 36
_PATH_LOCKS = tuple(threading.Lock() for _ in range(64))


class WritebackError(RuntimeError):
    """A writeback or restore was refused without changing the artifact."""


__all__ = ("WritebackError", "writeback", "restore")


class _Artwork:
    __slots__ = ("data", "mime", "width", "height")

    def __init__(self, data: bytes, mime: str, width: int, height: int) -> None:
        self.data = data
        self.mime = mime
        self.width = width
        self.height = height


_FIELD_ALIASES = {
    "title": "title",
    "artist": "artist",
    "album_artist": "album_artist",
    "albumartist": "album_artist",
    "album": "album",
    "track_number": "track_number",
    "track": "track_number",
    "disc_number": "disc_number",
    "disc": "disc_number",
    "year": "year",
    "genres": "genres",
    "genre": "genres",
    "lyrics": "lyrics",
    "musicbrainz_recording_id": "musicbrainz_recording_id",
    "musicbrainz_recordingid": "musicbrainz_recording_id",
    "recording_id": "musicbrainz_recording_id",
    "musicbrainz_release_id": "musicbrainz_release_id",
    "musicbrainz_releaseid": "musicbrainz_release_id",
    "release_id": "musicbrainz_release_id",
    "musicbrainz_artist_id": "musicbrainz_artist_id",
    "musicbrainz_artistid": "musicbrainz_artist_id",
    "artist_id": "musicbrainz_artist_id",
    "musicbrainz_album_artist_id": "musicbrainz_album_artist_id",
    "musicbrainz_albumartistid": "musicbrainz_album_artist_id",
    "album_artist_id": "musicbrainz_album_artist_id",
}
_TEXT_FIELDS = {"title", "artist", "album_artist", "album"}
_MB_FIELDS = {"musicbrainz_recording_id", "musicbrainz_release_id", "musicbrainz_artist_id", "musicbrainz_album_artist_id"}


def _error(message: str, cause: BaseException | None = None) -> WritebackError:
    error = WritebackError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _normalise_fields(fields: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise WritebackError("Writeback fields must be an object")

    normalised: dict[str, Any] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or key not in _FIELD_ALIASES:
            raise WritebackError(f"Unsupported writeback field: {key!r}")
        name = _FIELD_ALIASES[key]
        if name in normalised:
            raise WritebackError(f"Writeback field supplied more than once: {name}")
        if name in _TEXT_FIELDS or name in _MB_FIELDS or name == "lyrics":
            if value is not None and (not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_TEXT_BYTES):
                raise WritebackError(f"Invalid value for {name}")
        elif name in {"track_number", "disc_number", "year"}:
            if value is not None:
                if isinstance(value, bool):
                    raise WritebackError(f"Invalid value for {name}")
                if isinstance(value, str) and value.isdecimal():
                    value = int(value)
                if not isinstance(value, int):
                    raise WritebackError(f"Invalid value for {name}")
                limit = 9999 if name == "year" else 2**31 - 1
                lower = 0 if name == "year" else 1
                if not lower <= value <= limit:
                    raise WritebackError(f"Invalid value for {name}")
        elif name == "genres":
            if value is not None:
                if isinstance(value, str):
                    value = [value]
                if not isinstance(value, (list, tuple)):
                    raise WritebackError("Genres must be a list of strings")
                if len(value) > 256 or any(not isinstance(genre, str) or len(genre.encode("utf-8")) > _MAX_TEXT_BYTES for genre in value):
                    raise WritebackError("Invalid genres")
                value = list(value)
        normalised[name] = value
    return normalised


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    position = 2
    frame_dimensions: tuple[int, int] | None = None
    pending_marker: int | None = None
    while position < len(data):
        if pending_marker is None:
            if data[position] != 0xFF:
                return None
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                return None
            marker = data[position]
            position += 1
        else:
            marker = pending_marker
            pending_marker = None
        if marker == 0xD9:
            return frame_dimensions if frame_dimensions is not None and position == len(data) else None
        if marker == 0xDA:
            if position + 2 > len(data):
                return None
            segment_length = int.from_bytes(data[position:position + 2], "big")
            if segment_length < 2 or position + segment_length > len(data):
                return None
            position += segment_length
            while position < len(data):
                if data[position] != 0xFF:
                    position += 1
                    continue
                while position < len(data) and data[position] == 0xFF:
                    position += 1
                if position >= len(data):
                    return None
                marker = data[position]
                position += 1
                if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                    continue
                if marker == 0xD9:
                    return frame_dimensions if frame_dimensions is not None and position == len(data) else None
                # A marker after a scan belongs to the outer segment parser.
                pending_marker = marker
                break
            else:
                return None
            continue
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[position:position + 2], "big")
        if segment_length < 2 or position + segment_length > len(data):
            return None
        if marker in ({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}) and segment_length >= 8:
            height = int.from_bytes(data[position + 3:position + 5], "big")
            width = int.from_bytes(data[position + 5:position + 7], "big")
            if not width or not height or width * height > 100_000_000:
                return None
            frame_dimensions = (width, height)
        position += segment_length
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < len(signature) or data[:8] != signature:
        return None
    position = 8
    saw_header = saw_data = saw_end = False
    width = height = 0
    while position < len(data):
        if position + 12 > len(data):
            return None
        length = int.from_bytes(data[position:position + 4], "big")
        chunk_start = position + 4
        chunk_end = chunk_start + 4 + length
        if length > len(data) or chunk_end + 4 > len(data):
            return None
        chunk_type = data[chunk_start:chunk_start + 4]
        chunk_data = data[chunk_start + 4:chunk_end]
        recorded_crc = int.from_bytes(data[chunk_end:chunk_end + 4], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != recorded_crc:
            return None
        if chunk_type == b"IHDR":
            if saw_header or len(chunk_data) != 13:
                return None
            width, height = struct.unpack(">II", chunk_data[:8])
            if not width or not height or width * height > 100_000_000:
                return None
            saw_header = True
        elif chunk_type == b"IDAT":
            saw_data = True
        elif chunk_type == b"IEND":
            if chunk_data or saw_end or chunk_end + 4 != len(data):
                return None
            saw_end = True
        position = chunk_end + 4
        if saw_end:
            break
    return (width, height) if saw_header and saw_data and saw_end else None


def _validate_artwork(artwork: bytes | None, artwork_type: str) -> _Artwork | None:
    if artwork is None:
        return None
    if not isinstance(artwork, bytes) or not artwork or len(artwork) > MAX_ARTWORK_BYTES:
        raise WritebackError("Artwork must be non-empty bytes no larger than 8 MiB")
    if not isinstance(artwork_type, str):
        raise WritebackError("Artwork type must be image/jpeg or image/png")
    mime = artwork_type.lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in {"image/jpeg", "image/png"}:
        raise WritebackError("Artwork type must be image/jpeg or image/png")
    dimensions = _jpeg_dimensions(artwork) if mime == "image/jpeg" else _png_dimensions(artwork)
    if dimensions is None:
        raise WritebackError("Artwork is not a valid JPEG or PNG")
    width, height = dimensions
    return _Artwork(artwork, mime, width, height)


def _path_text(path: Path) -> str:
    return str(path.absolute())


def _path_hash(path: Path) -> str:
    return hashlib.sha256(_path_text(path).encode("utf-8")).hexdigest()


def _path_lock(path: Path) -> threading.Lock:
    """A bounded per-path critical section; unrelated artifacts usually use different stripes."""
    stripe = int(_path_hash(path)[:8], 16) % len(_PATH_LOCKS)
    return _PATH_LOCKS[stripe]


def _safe_open(path: Path, flags: int, mode: int = 0o600) -> int:
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, mode)


def _lstat_regular(path: Path, *, writable: bool) -> os.stat_result:
    if not isinstance(path, Path):
        raise WritebackError("Artifact path must be a Path")
    try:
        listed = os.lstat(path)
    except OSError as exc:
        raise _error("Artifact cannot be opened", exc)
    if stat.S_ISLNK(listed.st_mode):
        raise WritebackError("Symlinked artifacts are not writable")
    if not stat.S_ISREG(listed.st_mode):
        raise WritebackError("Artifact is not a regular file")
    if writable and not listed.st_mode & 0o222:
        raise WritebackError("Artifact is read-only")
    return listed


def _stat_token(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, stat.S_IMODE(value.st_mode))


def _read_hash(path: Path, *, writable: bool = False) -> tuple[os.stat_result, str]:
    _lstat_regular(path, writable=writable)
    try:
        descriptor = _safe_open(path, os.O_RDONLY)
    except OSError as exc:
        raise _error("Artifact cannot be opened", exc)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WritebackError("Artifact is not a regular file")
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except WritebackError:
        raise
    except OSError as exc:
        raise _error("Artifact cannot be read", exc)
    finally:
        os.close(descriptor)
    if _stat_token(before) != _stat_token(after):
        raise WritebackError("Artifact changed while it was being read")
    try:
        if _stat_token(os.lstat(path)) != _stat_token(after):
            raise WritebackError("Artifact changed while it was being read")
    except OSError as exc:
        raise _error("Artifact cannot be checked", exc)
    return after, digest.hexdigest()


def _make_temp(path: Path, mode: int) -> tuple[Path, int]:
    descriptor = -1
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=f".lumina-writeback{path.suffix}", dir=path.parent)
        os.fchmod(descriptor, mode)
        return Path(name), descriptor
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if "name" in locals():
            with suppress(OSError):
                os.unlink(name)
        raise _error("Could not create a sibling temporary file", exc)


def _copy_source(path: Path, target: Path, descriptor: int, expected: os.stat_result) -> str:
    digest = hashlib.sha256()
    try:
        source_descriptor = _safe_open(path, os.O_RDONLY)
    except OSError as exc:
        os.close(descriptor)
        raise _error("Artifact cannot be opened", exc)
    try:
        source_stat = os.fstat(source_descriptor)
        if _stat_token(source_stat) != _stat_token(expected):
            raise WritebackError("Artifact changed before writeback")
        with os.fdopen(source_descriptor, "rb", closefd=True) as source:
            source_descriptor = -1
            with os.fdopen(descriptor, "wb", closefd=True) as destination:
                descriptor = -1
                while chunk := source.read(1 << 20):
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        final_source_stat = os.stat(path, follow_symlinks=False)
        if _stat_token(final_source_stat) != _stat_token(expected):
            raise WritebackError("Artifact changed while it was copied")
    except WritebackError:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as exc:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        raise _error("Artifact could not be copied", exc)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path, mode: int) -> str:
    descriptor = -1
    digest = hashlib.sha256()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_TRUNC)
        source_descriptor = _safe_open(source, os.O_RDONLY)
        try:
            with os.fdopen(source_descriptor, "rb", closefd=True) as source_handle:
                source_descriptor = -1
                with os.fdopen(descriptor, "wb", closefd=True) as destination:
                    descriptor = -1
                    while chunk := source_handle.read(1 << 20):
                        digest.update(chunk)
                        destination.write(chunk)
                    destination.flush()
                    os.fchmod(destination.fileno(), mode)
                    os.fsync(destination.fileno())
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise _error("Could not create a durable backup copy", exc)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        descriptor = _safe_open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise _error("Could not make the temporary audio durable", exc)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise _error("Could not make the directory durable", exc)


def _load_audio(path: Path) -> tuple[Any, str]:
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # mutagen exposes several format-specific error classes
        raise _error("Audio file is corrupted or unreadable", exc)
    if audio is None:
        raise WritebackError("Audio format is unsupported")
    if isinstance(audio, AAC):
        raise WritebackError("AAC/ADTS tagging is not supported by mutagen")
    if isinstance(audio, MP3):
        return audio, "mp3"
    if isinstance(audio, FLAC):
        return audio, "flac"
    if isinstance(audio, OggVorbis):
        return audio, "vorbis"
    if isinstance(audio, OggOpus):
        return audio, "opus"
    if isinstance(audio, MP4):
        suffix = path.suffix.lower().lstrip(".")
        return audio, suffix if suffix in {"m4a", "m4b", "m4p", "mp4"} else "m4a"
    raise WritebackError(f"Audio format {type(audio).__name__} is unsupported")


def _ensure_tags(audio: Any) -> Any:
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as exc:
            raise _error("This audio format cannot store tags", exc)
    if audio.tags is None:
        raise WritebackError("This audio format cannot store tags")
    return audio.tags


def _remove_id3_frames(tags: ID3, frame_id: str) -> None:
    for frame in list(tags.getall(frame_id)):
        tags.delall(frame.HashKey)


def _set_id3_frame(tags: ID3, frame_id: str, frame_type: Any, value: str | None) -> None:
    tags.delall(frame_id)
    if value is not None:
        tags.add(frame_type(encoding=3, text=[value]))


def _number_with_total(tags: ID3, frame_id: str, value: int) -> str:
    existing = tags.getall(frame_id)
    if existing and existing[0].text:
        current = str(existing[0].text[0])
        if "/" in current:
            total = current.split("/", 1)[1]
            if total.isdecimal():
                return f"{value}/{total}"
    return str(value)


def _remove_txxx(tags: ID3, descriptions: set[str]) -> None:
    folded = {description.casefold() for description in descriptions}
    for frame in list(tags.getall("TXXX")):
        if str(frame.desc).casefold() in folded:
            tags.delall(frame.HashKey)


def _set_txxx(tags: ID3, descriptions: tuple[str, ...], value: str | None) -> None:
    _remove_txxx(tags, set(descriptions))
    if value is not None:
        for description in descriptions:
            tags.add(TXXX(encoding=3, desc=description, text=[value]))


def _set_ufid(tags: ID3, value: str | None) -> None:
    for frame in list(tags.getall("UFID")):
        if str(frame.owner).casefold() == _MB_OWNER.casefold():
            tags.delall(frame.HashKey)
    if value is not None:
        try:
            data = uuid.UUID(value).bytes
        except ValueError:
            data = value.encode("utf-8")
        tags.add(UFID(owner=_MB_OWNER, data=data))


def _set_id3_lyrics(tags: ID3, value: str | None) -> None:
    _remove_id3_frames(tags, "USLT")
    if value is not None:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=value))


def _set_id3_artwork(tags: ID3, artwork: _Artwork) -> None:
    for frame in list(tags.getall("APIC")):
        if int(frame.type) == _FRONT_COVER:
            tags.delall(frame.HashKey)
    tags.add(APIC(encoding=3, mime=artwork.mime, type=_FRONT_COVER, desc="", data=artwork.data))


def _apply_id3(audio: MP3, fields: dict[str, Any], artwork: _Artwork | None) -> None:
    tags = _ensure_tags(audio)
    if "title" in fields:
        _set_id3_frame(tags, "TIT2", TIT2, fields["title"])
    if "artist" in fields:
        _set_id3_frame(tags, "TPE1", TPE1, fields["artist"])
    if "album_artist" in fields:
        _set_id3_frame(tags, "TPE2", TPE2, fields["album_artist"])
    if "album" in fields:
        _set_id3_frame(tags, "TALB", TALB, fields["album"])
    if "track_number" in fields:
        value = fields["track_number"]
        _set_id3_frame(tags, "TRCK", TRCK, None if value is None else _number_with_total(tags, "TRCK", value))
    if "disc_number" in fields:
        value = fields["disc_number"]
        _set_id3_frame(tags, "TPOS", TPOS, None if value is None else _number_with_total(tags, "TPOS", value))
    if "year" in fields:
        _set_id3_frame(tags, "TDRC", TDRC, None if fields["year"] is None else str(fields["year"]))
        tags.delall("TYER")
    if "genres" in fields:
        tags.delall("TCON")
        if fields["genres"]:
            tags.add(TCON(encoding=3, text=fields["genres"]))
    if "lyrics" in fields:
        _set_id3_lyrics(tags, fields["lyrics"])
    if "musicbrainz_recording_id" in fields:
        _set_txxx(tags, ("MusicBrainz Recording Id", "MusicBrainz Track Id"), fields["musicbrainz_recording_id"])
        _set_ufid(tags, fields["musicbrainz_recording_id"])
    if "musicbrainz_release_id" in fields:
        _set_txxx(tags, ("MusicBrainz Album Id", "MusicBrainz Release Id"), fields["musicbrainz_release_id"])
    if "musicbrainz_artist_id" in fields:
        _set_txxx(tags, ("MusicBrainz Artist Id",), fields["musicbrainz_artist_id"])
    if "musicbrainz_album_artist_id" in fields:
        _set_txxx(tags, ("MusicBrainz Album Artist Id",), fields["musicbrainz_album_artist_id"])
    if artwork is not None:
        _set_id3_artwork(tags, artwork)


def _find_vorbis_keys(tags: Any, names: tuple[str, ...]) -> list[str]:
    folded = {name.casefold() for name in names}
    return [key for key in list(tags.keys()) if str(key).casefold() in folded]


def _set_vorbis(tags: Any, names: tuple[str, ...], value: str | list[str] | None, canonical: str | None = None) -> None:
    for key in _find_vorbis_keys(tags, names):
        del tags[key]
    if value is not None:
        tags[canonical or names[0]] = value if isinstance(value, list) else [value]


def _vorbis_number(tags: Any, names: tuple[str, ...], value: int) -> str:
    current = next((str(item) for key in _find_vorbis_keys(tags, names) for item in tags[key]), "")
    if "/" in current:
        total = current.split("/", 1)[1]
        if total.isdecimal():
            return f"{value}/{total}"
    return str(value)


_VORBIS_MB_KEYS = {
    "musicbrainz_recording_id": ("musicbrainz_trackid", "musicbrainz_recordingid"),
    "musicbrainz_release_id": ("musicbrainz_albumid", "musicbrainz_releaseid"),
    "musicbrainz_artist_id": ("musicbrainz_artistid",),
    "musicbrainz_album_artist_id": ("musicbrainz_albumartistid", "musicbrainz_album_artist_id"),
}


def _picture_from_vorbis(value: Any) -> Picture | None:
    if not isinstance(value, str):
        return None
    try:
        return Picture(base64.b64decode(value, validate=True))
    except (ValueError, binascii.Error, TypeError):
        return None


def _new_picture(artwork: _Artwork) -> Picture:
    picture = Picture()
    picture.type = _FRONT_COVER
    picture.mime = artwork.mime
    picture.width = artwork.width
    picture.height = artwork.height
    picture.data = artwork.data
    return picture


def _set_vorbis_artwork(tags: Any, artwork: _Artwork) -> None:
    existing = tags.get("metadata_block_picture", [])
    if isinstance(existing, str):
        existing = [existing]
    kept = []
    for value in existing:
        picture = _picture_from_vorbis(value)
        if picture is None or int(picture.type) != _FRONT_COVER:
            kept.append(value)
    kept.append(base64.b64encode(_new_picture(artwork).write()).decode("ascii"))
    _set_vorbis(tags, ("metadata_block_picture",), kept, "metadata_block_picture")


def _apply_vorbis(audio: OggVorbis | OggOpus, fields: dict[str, Any], artwork: _Artwork | None) -> None:
    tags = _ensure_tags(audio)
    if "title" in fields:
        _set_vorbis(tags, ("title",), None if fields["title"] is None else [fields["title"]], "title")
    if "artist" in fields:
        _set_vorbis(tags, ("artist",), None if fields["artist"] is None else [fields["artist"]], "artist")
    if "album_artist" in fields:
        _set_vorbis(tags, ("albumartist", "album artist"), None if fields["album_artist"] is None else [fields["album_artist"]], "albumartist")
    if "album" in fields:
        _set_vorbis(tags, ("album",), None if fields["album"] is None else [fields["album"]], "album")
    if "track_number" in fields:
        value = fields["track_number"]
        _set_vorbis(tags, ("tracknumber",), None if value is None else [_vorbis_number(tags, ("tracknumber",), value)], "tracknumber")
    if "disc_number" in fields:
        value = fields["disc_number"]
        _set_vorbis(tags, ("discnumber",), None if value is None else [_vorbis_number(tags, ("discnumber",), value)], "discnumber")
    if "year" in fields:
        _set_vorbis(tags, ("date", "year"), None if fields["year"] is None else [str(fields["year"])], "date")
    if "genres" in fields:
        _set_vorbis(tags, ("genre",), fields["genres"], "genre")
    if "lyrics" in fields:
        _set_vorbis(tags, ("lyrics",), None if fields["lyrics"] is None else [fields["lyrics"]], "lyrics")
    for name, names in _VORBIS_MB_KEYS.items():
        if name in fields:
            value = fields[name]
            for key in names:
                _set_vorbis(tags, (key,), None if value is None else [value], key)
    if artwork is not None:
        _set_vorbis_artwork(tags, artwork)


def _set_mp4_text(tags: Any, atom: str, value: str | None) -> None:
    if value is None:
        tags.pop(atom, None)
    else:
        tags[atom] = [value]


def _mp4_number(tags: Any, atom: str, value: int | None) -> None:
    if value is None:
        tags.pop(atom, None)
        return
    current = tags.get(atom, [])
    total = current[0][1] if current and isinstance(current[0], tuple) and len(current[0]) > 1 else 0
    tags[atom] = [(value, total)]


_MP4_MB_ATOMS = {
    "musicbrainz_recording_id": ("MusicBrainz Track Id", "MusicBrainz Recording Id"),
    "musicbrainz_release_id": ("MusicBrainz Album Id", "MusicBrainz Release Id"),
    "musicbrainz_artist_id": ("MusicBrainz Artist Id",),
    "musicbrainz_album_artist_id": ("MusicBrainz Album Artist Id",),
}


def _set_mp4_freeform(tags: Any, names: tuple[str, ...], value: str | None) -> None:
    prefix = "----:com.apple.iTunes:"
    for name in names:
        tags.pop(prefix + name, None)
    if value is not None:
        for name in names:
            tags[prefix + name] = [MP4FreeForm(value.encode("utf-8"))]


def _apply_mp4(audio: MP4, fields: dict[str, Any], artwork: _Artwork | None) -> None:
    tags = _ensure_tags(audio)
    text_atoms = {"title": "©nam", "artist": "©ART", "album_artist": "aART", "album": "©alb"}
    for name, atom in text_atoms.items():
        if name in fields:
            _set_mp4_text(tags, atom, fields[name])
    if "track_number" in fields:
        _mp4_number(tags, "trkn", fields["track_number"])
    if "disc_number" in fields:
        _mp4_number(tags, "disk", fields["disc_number"])
    if "year" in fields:
        _set_mp4_text(tags, "©day", None if fields["year"] is None else str(fields["year"]))
    if "genres" in fields:
        if fields["genres"] is None:
            tags.pop("©gen", None)
        else:
            if fields["genres"]:
                tags["©gen"] = fields["genres"]
            else:
                tags.pop("©gen", None)
    if "lyrics" in fields:
        _set_mp4_text(tags, "©lyr", fields["lyrics"])
    for name, atoms in _MP4_MB_ATOMS.items():
        if name in fields:
            _set_mp4_freeform(tags, atoms, fields[name])
    if artwork is not None:
        image_format = MP4Cover.FORMAT_JPEG if artwork.mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        tags["covr"] = [MP4Cover(artwork.data, imageformat=image_format)]


def _apply_fields(audio: Any, format_name: str, fields: dict[str, Any], artwork: _Artwork | None) -> None:
    if not fields and artwork is None:
        return
    if format_name == "mp3":
        _apply_id3(audio, fields, artwork)
    elif format_name in {"vorbis", "opus"}:
        _apply_vorbis(audio, fields, artwork)
    elif format_name == "flac":
        # FLAC comments share the Vorbis representation; picture blocks live
        # outside the comment dictionary and therefore need their own pass.
        _apply_vorbis(audio, fields, None)
        if artwork is not None:
            existing = list(audio.pictures)
            audio.clear_pictures()
            for picture in existing:
                if int(picture.type) != _FRONT_COVER:
                    audio.add_picture(picture)
            audio.add_picture(_new_picture(artwork))
    elif format_name in {"m4a", "m4b", "m4p", "mp4"}:
        _apply_mp4(audio, fields, artwork)
    else:  # guarded by _load_audio, retained as a defense if formats change
        raise WritebackError(f"Audio format {format_name} is unsupported")


def _save_audio(audio: Any, format_name: str) -> None:
    try:
        if format_name == "mp3" and isinstance(audio.tags, ID3):
            version = getattr(audio.tags, "version", (2, 4, 0))
            audio.save(v2_version=3 if len(version) > 1 and version[1] == 3 else 4)
        else:
            audio.save()
    except Exception as exc:
        raise _error("Audio metadata could not be saved", exc)


def _ensure_writable_current(path: Path, expected: os.stat_result, expected_hash: str) -> None:
    current, digest = _read_hash(path, writable=True)
    if _stat_token(current) != _stat_token(expected) or digest != expected_hash:
        raise WritebackError("Artifact changed during writeback")


def _ensure_backup_root(root: Path, *, create: bool) -> Path:
    if not isinstance(root, Path):
        raise WritebackError("Backup root must be a Path")
    try:
        if create:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        listed = os.lstat(root)
    except OSError as exc:
        raise _error("Backup root is unavailable", exc)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(listed.st_mode):
        raise WritebackError("Backup root is not a directory")
    return root


def _display_format(path: Path, format_name: str) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix if suffix in {"mp3", "flac", "ogg", "oga", "vorbis", "opus", "m4a", "m4b", "m4p", "mp4"} else format_name


def _create_backup(root: Path, backup_id: str, snapshot: Path, original_hash: str, after_hash: str, format_name: str, size: int, mode: int, path: Path) -> None:
    root = _ensure_backup_root(root, create=True)
    backup_dir = root / backup_id
    try:
        backup_dir.mkdir(mode=0o700)
        if stat.S_ISLNK(os.lstat(backup_dir).st_mode):
            raise WritebackError("Backup directory is a symlink")
        backup_file = backup_dir / "original"
        descriptor = os.open(backup_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        copied_hash = _copy_file(snapshot, backup_file, 0o600)
        if copied_hash != original_hash:
            raise WritebackError("Original backup checksum mismatch")
        journal = {
            "backup_id": backup_id,
            "path_hash": _path_hash(path),
            "original_sha256": original_hash,
            "after_sha256": after_hash,
            "format": format_name,
            "bytes": size,
            "mode": mode,
        }
        journal_bytes = (json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        journal_path = backup_dir / "journal.json"
        descriptor = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(journal_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        _fsync_directory(backup_dir)
        _fsync_directory(root)
    except WritebackError:
        raise
    except OSError as exc:
        raise _error("Durable original backup could not be created", exc)


def _strict_backup_id(value: str) -> str:
    if not isinstance(value, str) or len(value) != _UUID_REPR_LENGTH:
        raise WritebackError("Backup id is not a UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise _error("Backup id is not a UUID", exc)
    if str(parsed) != value:
        raise WritebackError("Backup id is not a UUID")
    return value


def _regular_backup_file(path: Path) -> None:
    try:
        listed = os.lstat(path)
    except OSError as exc:
        raise _error("Backup is missing", exc)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
        raise WritebackError("Backup is not a regular file")


def _read_journal(path: Path, backup_id: str) -> dict[str, Any]:
    _regular_backup_file(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            journal = json.load(handle)
    except (OSError, ValueError) as exc:
        raise _error("Backup journal is missing or malformed", exc)
    if not isinstance(journal, dict) or journal.get("backup_id") != backup_id:
        raise WritebackError("Backup journal does not match its id")
    required = {"path_hash", "original_sha256", "after_sha256", "format", "bytes", "mode"}
    if not required <= journal.keys():
        raise WritebackError("Backup journal is incomplete")
    if not all(isinstance(journal[key], str) and len(journal[key]) == 64 for key in ("path_hash", "original_sha256", "after_sha256")):
        raise WritebackError("Backup journal has invalid checksums")
    if not isinstance(journal["format"], str) or journal["format"] not in {"mp3", "flac", "ogg", "oga", "vorbis", "opus", "m4a", "m4b", "m4p", "mp4"} or not isinstance(journal["bytes"], int) or journal["bytes"] < 0 or not isinstance(journal["mode"], int):
        raise WritebackError("Backup journal has invalid metadata")
    return journal


def _cleanup(path: Path | None) -> None:
    if path is not None:
        with suppress(OSError):
            path.unlink()


def _check_expected_fingerprint(current: os.stat_result, expected: tuple[int, int] | None) -> None:
    if expected is None:
        return
    if (
        not isinstance(expected, tuple)
        or len(expected) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in expected)
    ):
        raise WritebackError("Expected artifact fingerprint is invalid")
    if (current.st_size, current.st_mtime_ns) != expected:
        raise WritebackError("Artifact changed since writeback preview")


def writeback(
    path: Path,
    fields: dict[str, Any],
    *,
    backup_root: Path,
    artwork: bytes | None = None,
    artwork_type: str = "image/jpeg",
    expected_fingerprint: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Write tags and optional front-cover artwork to one registered artifact.

    ``path`` is intentionally not resolved or searched here.  The caller must
    provide the already-confined, registered artifact path described by the
    music API.  The returned backup id is the only handle accepted by
    :func:`restore`.
    """
    normalised = _normalise_fields(fields)
    artwork_value = _validate_artwork(artwork, artwork_type)
    with _path_lock(path):
        return _writeback_locked(path, normalised, backup_root, artwork_value, expected_fingerprint)


def _writeback_locked(
    path: Path,
    normalised: dict[str, Any],
    backup_root: Path,
    artwork: _Artwork | None,
    expected_fingerprint: tuple[int, int] | None,
) -> dict[str, Any]:
    original_stat = _lstat_regular(path, writable=True)
    _check_expected_fingerprint(original_stat, expected_fingerprint)
    working: Path | None = None
    snapshot: Path | None = None
    try:
        working, descriptor = _make_temp(path, stat.S_IMODE(original_stat.st_mode))
        original_hash = _copy_source(path, working, descriptor, original_stat)
        audio, format_name = _load_audio(working)
        snapshot, snapshot_descriptor = _make_temp(path, stat.S_IMODE(original_stat.st_mode))
        _copy_source(working, snapshot, snapshot_descriptor, os.stat(working, follow_symlinks=False))
        _apply_fields(audio, format_name, normalised, artwork)
        _save_audio(audio, format_name)
        os.chmod(working, stat.S_IMODE(original_stat.st_mode), follow_symlinks=False)
        _fsync_file(working)
        _load_audio(working)  # validate the complete output before making a backup or replacing the original
        after_stat = os.stat(working, follow_symlinks=False)
        after_hash = _read_hash(working)[1]
        _ensure_writable_current(path, original_stat, original_hash)
        backup_id = str(uuid.uuid4())
        _create_backup(
            backup_root,
            backup_id,
            snapshot,
            original_hash,
            after_hash,
            _display_format(path, format_name),
            after_stat.st_size,
            stat.S_IMODE(original_stat.st_mode),
            path,
        )
        _ensure_writable_current(path, original_stat, original_hash)
        try:
            os.replace(working, path)
        except OSError as exc:
            raise _error("Audio artifact could not be replaced", exc)
        working = None
        _fsync_file(path)
        _fsync_directory(path.parent)
        return {"backup_id": backup_id, "format": _display_format(path, format_name), "bytes": after_stat.st_size}
    except WritebackError:
        raise
    except Exception as exc:
        raise _error("Audio writeback failed", exc)
    finally:
        _cleanup(working)
        _cleanup(snapshot)


def restore(path: Path, backup_id: str, *, backup_root: Path) -> dict[str, Any]:
    """Restore one writeback only when the artifact is still that writeback's output."""
    backup_id = _strict_backup_id(backup_id)
    with _path_lock(path):
        return _restore_locked(path, backup_id, backup_root)


def _restore_locked(path: Path, backup_id: str, backup_root: Path) -> dict[str, Any]:
    _lstat_regular(path, writable=False)
    root = _ensure_backup_root(backup_root, create=False)
    backup_dir = root / backup_id
    try:
        directory_stat = os.lstat(backup_dir)
    except OSError as exc:
        raise _error("Backup is missing", exc)
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        raise WritebackError("Backup is not a directory")
    journal = _read_journal(backup_dir / "journal.json", backup_id)
    if journal["path_hash"] != _path_hash(path):
        raise WritebackError("Backup belongs to a different artifact")
    _current_stat, current_hash = _read_hash(path)
    if current_hash != journal["after_sha256"]:
        raise WritebackError("Artifact changed since this writeback")
    backup_file = backup_dir / "original"
    _regular_backup_file(backup_file)
    backup_hash = _read_hash(backup_file)[1]
    if backup_hash != journal["original_sha256"]:
        raise WritebackError("Backup checksum does not match its journal")
    working: Path | None = None
    try:
        original_mode = journal["mode"]
        if original_mode < 0 or original_mode > 0o7777 or not original_mode & 0o222:
            raise WritebackError("Backup journal has an invalid file mode")
        working, descriptor = _make_temp(path, original_mode)
        os.close(descriptor)
        _copy_file(backup_file, working, original_mode)
        _load_audio(working)
        _fsync_file(working)
        _current_stat, current_hash = _read_hash(path)
        if current_hash != journal["after_sha256"]:
            raise WritebackError("Artifact changed during restore")
        try:
            os.replace(working, path)
        except OSError as exc:
            raise _error("Audio artifact could not be restored", exc)
        working = None
        _fsync_file(path)
        _fsync_directory(path.parent)
        return {"backup_id": backup_id, "format": journal["format"], "bytes": backup_file.stat().st_size}
    except WritebackError:
        raise
    except Exception as exc:
        raise _error("Audio restore failed", exc)
    finally:
        _cleanup(working)
