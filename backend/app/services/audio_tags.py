"""Embedded audio tags for the music library.

A file is opened read-only and never through a symlink, and a broken or hostile file only ever yields None (tags) or
FileNotFoundError (pictures). Only bounded tag metadata is kept: pictures are recorded as ``has_picture``, and
``embedded_picture`` reads one on demand for album art, never storing it. Existing record keys remain stable; the
optional MusicBrainz, lyrics, ReplayGain, credits, edition, label and catalogue keys are documented on ``AudioTags``.
"""
from __future__ import annotations

import base64
import logging
import math
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Cover, MP4Tags

logger = logging.getLogger(__name__)

MAX_TEXT = 300
MAX_LYRICS = 100_000
MAX_GENRES = 3
MAX_CREDITS = 20
# A parser upgrade refreshes unchanged files once, then returns to fingerprint-only scans.
AUDIO_TAG_VERSION = 1
FRONT_COVER = 3  # ID3 APIC and FLAC picture type "Cover (front)"
DISC_SUFFIX = re.compile(r"\s*[\(\[]\s*(cd|disc|disk)\s*(\d+)\s*[\)\]]\s*$", re.IGNORECASE)
LEADING_DIGITS = re.compile(r"^\s*(\d+)")
YEAR_DIGITS = re.compile(r"^\s*(\d{4})")
NUMBER = re.compile(r"^[+\-−]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*d?b)?$", re.IGNORECASE)
PICTURE_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/png": "image/png"}
# The table: (ID3 frame, Vorbis comment keys in order, MP4 atom) for each text field.
TEXT_FIELDS = {
    "title": ("TIT2", ("title",), "©nam"),
    "artist": ("TPE1", ("artist",), "©ART"),
    "album_artist": ("TPE2", ("albumartist", "album artist"), "aART"),
    "album": ("TALB", ("album",), "©alb"),
}
OPTIONAL_FIELDS = (
    "musicbrainz_artist_id", "musicbrainz_album_artist_id", "musicbrainz_album_id", "musicbrainz_release_group_id", "musicbrainz_recording_id",
    "lyrics", "replaygain_track_gain_db", "replaygain_album_gain_db", "replaygain_track_peak", "replaygain_album_peak",
    "composers", "performers", "edition", "disc_subtitle", "label", "catalogue_number",
)


def _value_text(value: Any) -> str | None:
    """Decode mutagen's text/bytes values without letting arbitrary objects leak into the record."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class AudioTags:
    """One file's normalised tags.

    Existing keys keep their original names. Optional keys are added to
    ``lumina_audio_tags`` only when present: ``musicbrainz_*_id`` values are
    validated UUIDs; ``lyrics`` is plain text or LRC text capped at 100,000
    characters; ``replaygain_{track,album}_gain_db`` is -30..20 dB and the
    corresponding ``*_peak`` is a finite non-negative peak; ``composers`` and
    ``performers`` are bounded lists; and ``edition``, ``disc_subtitle``,
    ``label`` and ``catalogue_number`` are bounded text. The scanner maps these
    names directly into raw track metadata, while MusicBrainz artist/album ids
    also become title ``provider_ids`` through ``apply_field``.
    """

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
    musicbrainz_artist_id: str | None = None
    musicbrainz_album_artist_id: str | None = None
    musicbrainz_album_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    musicbrainz_recording_id: str | None = None
    lyrics: str | None = None
    replaygain_track_gain_db: float | None = None
    replaygain_album_gain_db: float | None = None
    replaygain_track_peak: float | None = None
    replaygain_album_peak: float | None = None
    composers: tuple[str, ...] = ()
    performers: tuple[str, ...] = ()
    edition: str | None = None
    disc_subtitle: str | None = None
    label: str | None = None
    catalogue_number: str | None = None

    def record(self, fingerprint: str) -> dict[str, Any]:
        """The item's ``lumina_audio_tags`` (server-side only): these tags under the file's "size:mtime_ns:inode"."""
        values = asdict(self)
        values["genres"] = list(self.genres)
        for name in OPTIONAL_FIELDS:
            value = values[name]
            if value is None or value == () or value == []:
                values.pop(name)
            elif isinstance(value, tuple):
                values[name] = list(value)
        return {"fp": fingerprint, "tag_version": AUDIO_TAG_VERSION, **values}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> AudioTags | None:
        """A stored record back into tags; None for a file whose tags could not be read (a record of only ``fp``)."""
        if set(record) <= {"fp", "tag_version"}:
            return None
        values = {field.name: record.get(field.name) for field in fields(cls)}
        return cls(**{
            **values, "genres": tuple(values["genres"] or ()),
            "composers": tuple(values["composers"] or ()), "performers": tuple(values["performers"] or ()),
            "compilation": bool(values["compilation"]), "has_picture": bool(values["has_picture"]),
        })


def clean_text(value: Any) -> str | None:
    """Whitespace collapsed and stripped; None when empty or longer than 300 characters."""
    if isinstance(value, bytes):
        value = _value_text(value)
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned if cleaned and len(cleaned) <= MAX_TEXT else None


def _lyrics(value: Any) -> str | None:
    """Keep lyric line breaks, but reject empty or overlarge tag payloads."""
    text = _value_text(value)
    if text is None:
        return None
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text if text and len(text) <= MAX_LYRICS else None


def _uuid(values: list[Any]) -> str | None:
    """Return one unambiguous UUID; malformed and conflicting values are not guessed."""
    found: set[str] = set()
    for value in values:
        text = _value_text(value)
        if not text:
            continue
        try:
            found.add(str(uuid.UUID(text.strip())))
        except (ValueError, AttributeError):
            continue
    return next(iter(found)) if len(found) == 1 else None


def _number(value: Any, *, peak: bool = False) -> float | None:
    text = _value_text(value)
    if text is None:
        return None
    text = text.strip().replace("−", "-")
    if not NUMBER.fullmatch(text):
        return None
    try:
        number = float(re.sub(r"\s*d?b$", "", text, flags=re.IGNORECASE).strip())
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if peak:
        return number if 0 <= number <= 10 else None
    return number if -30 <= number <= 20 else None


def _credits(values: list[Any]) -> tuple[str, ...]:
    """Bound and deduplicate credit names while retaining tag order."""
    found: dict[str, str] = {}
    for value in values:
        text = clean_text(value)
        if text and text.casefold() not in found:
            found[text.casefold()] = text
    return tuple(found.values())[:MAX_CREDITS]


def _id3_text(tags: ID3, *names: str) -> list[Any]:
    values: list[Any] = []
    for name in names:
        for frame in tags.getall(name):
            values.extend(list(frame.text))
    return values


def _id3_txxx(tags: ID3, *descriptions: str) -> list[Any]:
    wanted = {description.casefold() for description in descriptions}
    return [value for frame in tags.getall("TXXX") if str(frame.desc).casefold() in wanted for value in frame.text]


def _id3_ufid(tags: ID3) -> list[Any]:
    values: list[Any] = []
    for frame in tags.getall("UFID"):
        if str(frame.owner).casefold() not in {"http://musicbrainz.org", "https://musicbrainz.org"}:
            continue
        data = frame.data
        if isinstance(data, bytes) and len(data) == 16:
            try:
                values.append(str(uuid.UUID(bytes=data)))
                continue
            except ValueError:
                pass
        values.append(data)
    return values


def _id3_synced(frame: Any) -> str | None:
    lines: list[str] = []
    for pair in getattr(frame, "text", ()):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        text, stamp = pair
        text = _value_text(text)
        if not text:
            continue
        try:
            milliseconds = int(stamp) if int(getattr(frame, "format", 2)) == 2 else None
        except (TypeError, ValueError):
            milliseconds = None
        if milliseconds is None or milliseconds < 0:
            lines.append(text)
            continue
        minutes, remainder = divmod(milliseconds, 60_000)
        seconds, hundredths = divmod(remainder, 1_000)
        lines.append(f"[{minutes:02d}:{seconds:02d}.{hundredths // 10:02d}]{text}")
    return "\n".join(lines) or None


def _mp4_freeform(tags: MP4Tags, *names: str) -> list[Any]:
    wanted = {name.casefold() for name in names}
    values: list[Any] = []
    prefix = "----:com.apple.itunes:"
    for key, raw in tags.items():
        text_key = str(key).casefold()
        if not text_key.startswith(prefix) or text_key.removeprefix(prefix) not in wanted:
            continue
        values.extend(raw if isinstance(raw, (list, tuple)) else [raw])
    return values


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
            musicbrainz_artist_id=_id3_txxx(tags, "MusicBrainz Artist Id"),
            musicbrainz_album_artist_id=_id3_txxx(tags, "MusicBrainz Album Artist Id"),
            musicbrainz_album_id=_id3_txxx(tags, "MusicBrainz Album Id", "MusicBrainz Release Id"),
            musicbrainz_release_group_id=_id3_txxx(tags, "MusicBrainz Release Group Id"),
            musicbrainz_recording_id=_id3_txxx(tags, "MusicBrainz Recording Id", "MusicBrainz Track Id") + _id3_ufid(tags),
            lyrics=[line for lyric in (_id3_synced(frame) for frame in tags.getall("SYLT")) if (line := lyric)]
            + [frame.text for frame in tags.getall("USLT")],
            composers=_id3_text(tags, "TCOM") + _id3_txxx(tags, "Composer", "Composers"),
            performers=_id3_txxx(tags, "Performer", "Performers"),
            edition=frame("TSST") + _id3_txxx(tags, "Edition"),
            disc_subtitle=frame("TSST") + _id3_txxx(tags, "Disc Subtitle", "Disc SubTitle", "Discsubtitle"),
            label=frame("TPUB") + _id3_txxx(tags, "Label", "Publisher"),
            catalogue_number=_id3_txxx(tags, "Catalog Number", "Catalogue Number", "CatalogNumber", "CatalogueNumber"),
            replaygain_track_gain_db=_id3_txxx(tags, "REPLAYGAIN_TRACK_GAIN", "ReplayGain Track Gain"),
            replaygain_album_gain_db=_id3_txxx(tags, "REPLAYGAIN_ALBUM_GAIN", "ReplayGain Album Gain"),
            replaygain_track_peak=_id3_txxx(tags, "REPLAYGAIN_TRACK_PEAK", "ReplayGain Track Peak"),
            replaygain_album_peak=_id3_txxx(tags, "REPLAYGAIN_ALBUM_PEAK", "ReplayGain Album Peak"),
        )
        compilation = any(str(value) == "1" for name in ("TCMP", "TXXX:TCMP") for value in frame(name))
        return values, compilation, bool(tags.getall("APIC"))
    if isinstance(tags, MP4Tags):
        values = {name: list(tags.get(atom, [])) for name, (_id3, _vorbis, atom) in TEXT_FIELDS.items()}
        values.update(
            track=[pair[0] for pair in tags.get("trkn", [])], disc=[pair[0] for pair in tags.get("disk", [])],
            year=list(tags.get("©day", [])), genres=list(tags.get("©gen", [])),
            musicbrainz_artist_id=_mp4_freeform(tags, "MusicBrainz Artist Id"),
            musicbrainz_album_artist_id=_mp4_freeform(tags, "MusicBrainz Album Artist Id"),
            musicbrainz_album_id=_mp4_freeform(tags, "MusicBrainz Album Id", "MusicBrainz Release Id"),
            musicbrainz_release_group_id=_mp4_freeform(tags, "MusicBrainz Release Group Id"),
            musicbrainz_recording_id=_mp4_freeform(tags, "MusicBrainz Recording Id", "MusicBrainz Track Id"),
            lyrics=list(tags.get("©lyr", [])) + _mp4_freeform(tags, "lyrics", "unsyncedlyrics", "syncedlyrics"),
            composers=list(tags.get("©wrt", [])) + _mp4_freeform(tags, "Composer", "Composers"),
            performers=_mp4_freeform(tags, "Performer", "Performers"),
            edition=_mp4_freeform(tags, "Edition"),
            disc_subtitle=_mp4_freeform(tags, "Disc Subtitle", "Disc SubTitle", "Discsubtitle"),
            label=list(tags.get("©pub", [])) + _mp4_freeform(tags, "Label", "Publisher"),
            catalogue_number=_mp4_freeform(tags, "Catalog Number", "Catalogue Number", "CatalogNumber", "CatalogueNumber"),
            replaygain_track_gain_db=_mp4_freeform(tags, "REPLAYGAIN_TRACK_GAIN", "ReplayGain Track Gain"),
            replaygain_album_gain_db=_mp4_freeform(tags, "REPLAYGAIN_ALBUM_GAIN", "ReplayGain Album Gain"),
            replaygain_track_peak=_mp4_freeform(tags, "REPLAYGAIN_TRACK_PEAK", "ReplayGain Track Peak"),
            replaygain_album_peak=_mp4_freeform(tags, "REPLAYGAIN_ALBUM_PEAK", "ReplayGain Album Peak"),
        )
        return values, bool(tags.get("cpil")), bool(tags.get("covr"))
    pictures = bool(getattr(audio, "pictures", None))
    if tags is None:
        return {}, False, pictures

    # Vorbis comments (FLAC, Ogg Vorbis, Opus): case-insensitive keys, each a list of strings.
    def comment(*keys: str) -> list[Any]:
        wanted = {key.casefold() for key in keys}
        values: list[Any] = []
        for key, raw in tags.items():
            if str(key).casefold() in wanted:
                values.extend(raw if isinstance(raw, (list, tuple)) else [raw])
        return values

    values = {name: comment(*vorbis) for name, (_id3, vorbis, _mp4) in TEXT_FIELDS.items()}
    values.update(
        track=comment("tracknumber"), disc=comment("discnumber"), year=comment("date", "year"), genres=comment("genre"),
        musicbrainz_artist_id=comment("musicbrainz_artistid", "musicbrainz_artist_id"),
        musicbrainz_album_artist_id=comment("musicbrainz_albumartistid", "musicbrainz_album_artist_id"),
        musicbrainz_album_id=comment("musicbrainz_albumid", "musicbrainz_releaseid", "musicbrainz_release_id"),
        musicbrainz_release_group_id=comment("musicbrainz_releasegroupid", "musicbrainz_releasegroup_id"),
        musicbrainz_recording_id=comment("musicbrainz_trackid", "musicbrainz_recordingid", "musicbrainz_recording_id"),
        lyrics=comment("syncedlyrics", "lyrics", "unsyncedlyrics", "unsynced_lyrics"),
        composers=comment("composer", "composers"), performers=comment("performer", "performers"),
        edition=comment("edition", "version", "albumversion", "release_version"),
        disc_subtitle=comment("discsubtitle", "disc_subtitle", "disc subtitle"),
        label=comment("label", "labelname", "publisher", "organization"),
        catalogue_number=comment(
            "catalognumber", "cataloguenumber", "catalog_number", "catalogue_number", "releasecatalognumber",
        ),
        replaygain_track_gain_db=comment("replaygain_track_gain"), replaygain_album_gain_db=comment("replaygain_album_gain"),
        replaygain_track_peak=comment("replaygain_track_peak"), replaygain_album_peak=comment("replaygain_album_peak"),
    )
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

    def first_uuid(name: str) -> str | None:
        return _uuid(values.get(name, []))

    def first_number(name: str, *, peak: bool = False) -> float | None:
        return next((parsed for value in values.get(name, []) if (parsed := _number(value, peak=peak)) is not None), None)

    album, disc = first("album"), first("disc", tag_number)
    suffix = DISC_SUFFIX.search(album) if album else None
    if suffix:  # "Album (Disc 2)" is disc 2 of "Album"
        album, disc = album[: suffix.start()].strip() or None, disc or tag_number(suffix.group(2))
    return AudioTags(
        title=first("title"), artist=first("artist"), album_artist=first("album_artist"), album=album,
        track=first("track", tag_number), disc=disc, year=first("year", _tag_year), genres=_genres(values.get("genres", [])),
        compilation=compilation, has_picture=has_picture,
        musicbrainz_artist_id=first_uuid("musicbrainz_artist_id"), musicbrainz_album_id=first_uuid("musicbrainz_album_id"),
        musicbrainz_album_artist_id=first_uuid("musicbrainz_album_artist_id"),
        musicbrainz_release_group_id=first_uuid("musicbrainz_release_group_id"), musicbrainz_recording_id=first_uuid("musicbrainz_recording_id"),
        lyrics=next((parsed for value in values.get("lyrics", []) if (parsed := _lyrics(value)) is not None), None),
        replaygain_track_gain_db=first_number("replaygain_track_gain_db"), replaygain_album_gain_db=first_number("replaygain_album_gain_db"),
        replaygain_track_peak=first_number("replaygain_track_peak", peak=True), replaygain_album_peak=first_number("replaygain_album_peak", peak=True),
        composers=_credits(values.get("composers", [])), performers=_credits(values.get("performers", [])),
        edition=first("edition"), disc_subtitle=first("disc_subtitle"), label=first("label"),
        catalogue_number=first("catalogue_number"),
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
