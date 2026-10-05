"""Local sidecar metadata for imported files: NFO, artwork and grouping.

Kodi/Plex/Jellyfin-style ``.nfo`` XML is read with a bounded, entity-free
parser: any DTD/entity declaration rejects the document (no XXE, no entity
expansion) and URLs inside it are never fetched. Sidecars are read-only and
opened without following symlinks. Grouping is conservative: without a
sidecar or an unambiguous filename convention an item stays ``unclassified``.
Field precedence: sidecar, then filename.
"""
from __future__ import annotations

import os
import re
import stat
import xml.etree.ElementTree as ET
import xml.parsers.expat
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Any

from app.services.audio_tags import AudioTags, clean_text, tag_number

NFO_MAX_BYTES = 256 * 1024
NFO_MAX_DEPTH = 8
NFO_FIELDS = frozenset({
    "title", "originaltitle", "showtitle", "year", "premiered", "aired", "season", "episode",
    "plot", "artist", "albumartist", "album", "track", "genre",
    "sorttitle", "id", "imdbid", "tmdbid", "tvdbid", "mpaa", "certification",
    "airsbefore_season", "airsbefore_episode", "airsafter_season",
})
NFO_MAX_ACTORS = 20  # <actor> credits kept, cast and crew together in document order (Jellyfin people-bloat lesson)
# The <actor><type> values Lumina shows (Jellyfin's PersonKind names, any case); a credit without one is an Actor.
PERSON_KINDS = {kind.casefold(): kind for kind in ("Actor", "GuestStar", "Director", "Writer", "Creator", "Producer", "Composer")}
PEOPLE_MARKER = "/metadata/People/"  # Jellyfin's people folder in any install: /config/data/…, /var/lib/jellyfin/…
PERSON_PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
PERSON_PHOTO_MAX_CHARS = 512
TMDB_PHOTO = re.compile(r"^https?://image\.tmdb\.org/t/p/[a-z0-9]+(/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp))$")
# Top-level crew elements (Jellyfin and Kodi write directors and writers here, not as <actor>): display only.
CREW_TAGS = {"director": "Director", "credits": "Writer", "writer": "Writer"}
NFO_MAX_CREW = 10
# [tmdbid-603] / {imdbid=tt…} on folder names and stems. Written from the naming docs.
PROVIDER_TAG = re.compile(r"\s*[\[{](tmdbid|imdbid|tvdbid)[-=]([^\]}\s]+)[\]}]", re.IGNORECASE)
# Provider ids end up in TMDB request URLs: anything else is dropped at this trust boundary.
PROVIDER_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")
PROVIDERS = {"tmdb": "Tmdb", "imdb": "Imdb", "tvdb": "Tvdb"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".ts", ".wmv", ".mpg", ".mpeg"})

EPISODE_PATTERN = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|(?<![\dA-Za-z])(\d{1,2})x(\d{2,3})(?!\d)")
MOVIE_PATTERN = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)")
SEASON_DIR_PATTERN = re.compile(r"^(?:season|series|staffel|saison)[ ._-]*(\d{1,2})$", re.IGNORECASE)
TRACK_PATTERN = re.compile(r"^(?P<number>\d{1,3})(?:[ ._-]+|\s*-\s*)(?P<title>.+)$")
# S01E01-E02 / S01E01E02 / S01E01-02 continue a match at its end. "- 1080p" and "-720p" never do.
MULTI_EPISODE_PATTERN = re.compile(r"-?[Ee](\d{1,3})(?!\d)|-(\d{1,3})(?![\dA-Za-z])")
EXTRA_DIRS = {
    "trailers": "trailer", "featurettes": "featurette", "behind the scenes": "behindthescenes",
    "deleted scenes": "deletedscene", "interviews": "interview", "scenes": "scene", "shorts": "short",
    "clips": "clip", "extras": "other", "other": "other", "theme-music": "other",
}
EXTRA_SUFFIX_PATTERN = re.compile(
    r"(?:^|-)(trailer|featurette|behindthescenes|deletedscene|deleted|interview|scene|short|clip|other|extra)$", re.IGNORECASE
)
EXTRA_SUFFIX_TYPES = {"deleted": "deletedscene", "extra": "other"}
SUBTITLE_FORMATS = frozenset({"srt", "ass", "ssa", "vtt"})
SUBTITLE_FLAGS = frozenset({"forced", "sdh", "cc", "hi", "default"})
# (Jellyfin image type, folder-level names, own-stem suffix) for movie and series folders.
FOLDER_ART = (
    ("Primary", ("poster", "folder", "cover"), "-poster"),
    ("Backdrop", ("fanart", "backdrop", "background"), "-fanart"),
    ("Logo", ("logo", "clearlogo"), None),
    ("Thumb", ("landscape", "thumb"), None),
    ("Banner", ("banner",), None),
)
# Music.
DISC_DIR_PATTERN = re.compile(r"^(?:cd|disc|disk)\s*(\d+)$", re.IGNORECASE)
YEAR_SUFFIX_PATTERN = re.compile(r"\s*\((\d{4})\)$")
ALBUM_ART, ARTIST_ART = ["cover", "folder", "front", "album"], ["artist", "folder", "poster"]
UNKNOWN_ALBUM, UNKNOWN_ARTIST, VARIOUS_ARTISTS = "Unknown album", "Unknown artist", "Various Artists"
AudioTagReader = Callable[[tuple[str, ...]], AudioTags | None]  # root-relative parts -> the file's tags (the scanner's cache)


def read_bounded(path: Path, limit: int) -> bytes:
    """Up to ``limit`` bytes of a regular file, never through a symlink (OSError otherwise)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(limit)
    finally:
        os.close(fd)


class _DtdFound(Exception):
    pass


def _raise_dtd(*_args: Any) -> None:
    raise _DtdFound


def _declares_dtd(data: bytes) -> bool:
    """Whether expat sees a DOCTYPE/ENTITY declaration in any XML encoding (UTF-16 too)."""
    probe = xml.parsers.expat.ParserCreate()
    probe.StartDoctypeDeclHandler = probe.EntityDeclHandler = _raise_dtd
    try:
        probe.Parse(data, True)
    except (_DtdFound, ValueError):  # ValueError: an encoding expat cannot decode; fail closed
        return True
    except xml.parsers.expat.ExpatError:
        pass  # malformed/hybrid NFOs are handled by the real parse below
    return False


def parse_nfo(path: Path) -> tuple[str, dict[str, Any]] | None:
    """(root tag, fields) from a local NFO, or None when absent, oversized, unsafe or corrupt."""
    try:
        data = read_bounded(path, NFO_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > NFO_MAX_BYTES or _declares_dtd(data):
        return None
    parser = ET.XMLPullParser(events=("start", "end"))
    root_tag: str | None = None
    fields: dict[str, Any] = {}
    depth = 0
    closed = False
    try:
        parser.feed(data)
        parser.close()
    except ET.ParseError:
        pass  # hybrid NFOs append a URL after the XML: keep what parsed before it
    for event, element in parser.read_events():
        if event == "start":
            depth += 1
            if depth > NFO_MAX_DEPTH:
                return None
            root_tag = root_tag or element.tag
            continue
        depth -= 1
        if depth == 0:
            closed = True
            break
        if depth != 1:
            continue
        # The pull parser has built the whole element by its end event, so child lookups work here.
        if element.tag == "actor":
            name = (element.findtext("name") or "").strip()
            kind = PERSON_KINDS.get((element.findtext("type") or "Actor").strip().casefold())
            if name and kind and len(fields.get("actor", [])) < NFO_MAX_ACTORS:
                role = (element.findtext("role") or "").strip()[:200] or None
                fields.setdefault("actor", []).append(
                    {"name": name[:200], "role": role, "type": kind, "thumb": person_thumb(element.findtext("thumb"))}
                )
        elif element.tag in CREW_TAGS:
            name = " ".join((element.text or "").split())[:200]
            job = CREW_TAGS[element.tag]
            crew = fields.setdefault("crew", [])
            if name and len(crew) < NFO_MAX_CREW and (name.casefold(), job) not in {(c["name"].casefold(), c["job"]) for c in crew}:
                crew.append({"name": name, "job": job})
        elif element.tag == "set":
            name = (element.findtext("name") or element.text or "").strip()
            if name:
                fields.setdefault("set", name[:200])
        elif element.tag == "uniqueid":
            provider = PROVIDERS.get(str(element.get("type") or "").lower())
            value = (element.text or "").strip()
            if provider and PROVIDER_ID.match(value):
                fields.setdefault("uniqueid", {}).setdefault(provider, value)
        elif element.tag in NFO_FIELDS and element.text and element.text.strip():
            value = element.text.strip()[:2000]
            if element.tag == "genre":
                fields.setdefault("genre", []).append(value)
            else:
                fields.setdefault(element.tag, value)
    return (root_tag, fields) if root_tag and closed else None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _year(fields: dict[str, Any]) -> int | None:
    for key in ("year", "premiered", "aired"):
        match = re.match(r"(\d{4})", str(fields.get(key) or ""))
        if match:
            return int(match.group(1))
    return None


def person_thumb(value: str | None) -> dict[str, str] | None:
    """A credit's ``<thumb>`` as a photo source, or None.

    ``{"path": …}`` relative to Jellyfin's people folder: only what follows ``/metadata/People/``, 1–3 segments with no
    empty, ``.`` or ``..`` one, JPEG/PNG/WebP. ``{"tmdb": "/x.jpg"}`` for an image.tmdb.org URL. Nothing here reads a
    file or fetches a URL; cast_photos.image_bytes confines the read again.
    """
    text = (value or "").strip()
    if match := TMDB_PHOTO.match(text):
        return {"tmdb": match[1]}
    _, marker, rest = text.replace("\\", "/").partition(PEOPLE_MARKER)
    parts = rest.split("/")
    if not marker or len(rest) > PERSON_PHOTO_MAX_CHARS or len(parts) > 3 or any(part in ("", ".", "..") for part in parts):
        return None
    return {"path": rest} if PurePosixPath(rest).suffix.lower() in PERSON_PHOTO_SUFFIXES else None


def people_folder_guess(name: str) -> str | None:
    """Where Jellyfin keeps a person's photo when no ``<thumb>`` names it (crew elements carry none):
    ``<first character, upper case>/<name>/folder.jpg``, validated like a thumb. A wrong guess is just a missing file.

    Jellyfin also strips characters invalid in file names; such names get no guessed photo.
    """
    name = name.strip()
    found = person_thumb(f"{PEOPLE_MARKER}{name[:1].upper()}/{name}/folder.jpg") if name else None
    return found["path"] if found else None


def strip_provider_tags(text: str) -> tuple[str, dict[str, str]]:
    """``Heat (1995) [tmdbid-949]`` -> ("Heat (1995)", {"Tmdb": "949"}). Malformed ids are stripped and dropped."""
    ids: dict[str, str] = {}
    for match in PROVIDER_TAG.finditer(text):
        if PROVIDER_ID.match(match.group(2)):
            ids.setdefault(PROVIDERS[match.group(1).lower()[:4]], match.group(2))
    return PROVIDER_TAG.sub("", text).strip(), ids


def nfo_provider_ids(tag: str | None, fields: dict[str, Any]) -> dict[str, str]:
    """Provider ids from ``<uniqueid type=…>``, ``<tmdbid>``/``<imdbid>``/``<tvdbid>`` and Kodi's legacy ``<id>``."""
    ids = dict(fields.get("uniqueid") or {})
    for key, provider in (("tmdbid", "Tmdb"), ("imdbid", "Imdb"), ("tvdbid", "Tvdb")):
        value = str(fields.get(key) or "")
        if PROVIDER_ID.match(value):
            ids.setdefault(provider, value)
    legacy = str(fields.get("id") or "")
    if PROVIDER_ID.match(legacy) and legacy.startswith("tt"):
        ids.setdefault("Imdb", legacy)
    elif PROVIDER_ID.match(legacy) and legacy.isdigit() and tag == "tvshow":
        ids.setdefault("Tvdb", legacy)
    return ids


def nfo_title_fields(tag: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    """Media title fields an NFO provides (source ``nfo``): columns plus ``metadata_json`` keys."""
    premiered = next((str(fields[key])[:10] for key in ("premiered", "aired") if DATE_PATTERN.match(str(fields.get(key) or ""))), None)
    people = [
        {"person_id": None, "name": actor["name"], "role": actor["role"], "type": actor["type"], "thumb": actor["thumb"]}
        for actor in fields.get("actor", [])
    ]
    crew = [{"person_id": None, "name": member["name"], "job": member["job"]} for member in fields.get("crew", [])]
    values: dict[str, Any] = {
        "name": fields.get("title"),
        "sort_name": fields.get("sorttitle"),
        "year": _year(fields) if tag in ("movie", "tvshow") else None,
        "provider_ids": nfo_provider_ids(tag, fields) or None,
        "overview": fields.get("plot"),
        "genres": fields.get("genre"),
        "official_rating": fields.get("mpaa") or fields.get("certification"),  # Kodi writes either; member_access normalizes
        "premiered": premiered,
        "people": people or None,
        "crew": crew or None,  # display only: never search text (title_search_fields reads "people")
    }
    if tag == "episodedetails":
        values.update({key: _int(fields.get(key)) for key in ("airsbefore_season", "airsbefore_episode", "airsafter_season")})
    return {key: value for key, value in values.items() if value is not None}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[._]+", " ", text)).strip(" -")


def _first(names: frozenset[str], candidates: list[str]) -> str | None:
    return next((name for name in candidates if name in names), None)


def _images(stems: list[str]) -> list[str]:
    return [f"{stem}{ext}" for stem in stems for ext in IMAGE_EXTENSIONS]


def _cached_nfo(root: Path, directory: tuple[str, ...], filename: str, cache: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    key = "/".join((*directory, filename))
    if key not in cache:
        cache[key] = parse_nfo(root.joinpath(*directory, filename))
    return cache[key]


def _listing(root: Path, dirs: tuple[str, ...], cache: dict[str, Any]) -> dict[str, tuple[str, bool, str | None]]:
    """Casefolded name -> (name, is_dir, image tag) for one folder, never through symlinks; memoized per batch."""
    key = "/".join(dirs) + "/<listing>"
    if key not in cache:
        found: dict[str, tuple[str, bool, str | None]] = {}
        try:
            with os.scandir(root.joinpath(*dirs)) as listing:
                for entry in listing:
                    if entry.is_dir(follow_symlinks=False):
                        found[entry.name.lower()] = (entry.name, True, None)
                    elif entry.is_file(follow_symlinks=False):
                        tag = None
                        if entry.name.lower().endswith(IMAGE_EXTENSIONS):
                            status = entry.stat(follow_symlinks=False)
                            tag = f"{status.st_size:x}-{status.st_mtime_ns:x}"  # changes when the art changes
                        found[entry.name.lower()] = (entry.name, False, tag)
        except OSError:
            pass
        cache[key] = found
    return cache[key]


def _season_number(name: str) -> int | None:
    if name.lower() == "specials":
        return 0
    match = SEASON_DIR_PATTERN.match(name)
    return int(match.group(1)) if match else None


def _name_year(text: str, *, from_file: bool = False) -> tuple[str, int | None, dict[str, str]]:
    """Folder or file name -> (display name, year, provider ids): tags stripped, then ``Title (Year)``."""
    text, ids = strip_provider_tags(text)
    match = MOVIE_PATTERN.match(text)
    name = match.group("title") if match else text
    return (_clean(name) if from_file else name.strip()), (int(match.group("year")) if match else None), ids


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _present(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "", {}, [])}


def _art(root: Path, dirs: tuple[str, ...], cache: dict[str, Any], candidates: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """``{"images.<Type>": {"path", "tag"}}`` for the first candidate stem (any image extension) present in ``dirs``."""
    listing = _listing(root, dirs, cache)
    art: dict[str, dict[str, str]] = {}
    for kind, stems in candidates.items():
        for candidate in stems:
            hit = next((listing[f"{candidate}{ext}".lower()] for ext in IMAGE_EXTENSIONS if f"{candidate}{ext}".lower() in listing), None)
            if hit and not hit[1]:
                art[f"images.{kind}"] = {"path": "/".join((*dirs, hit[0])), "tag": hit[2]}
                break
    return art


def _folder_art(root: Path, dirs: tuple[str, ...], cache: dict[str, Any], stem: str | None, *, generic: bool) -> dict[str, dict[str, str]]:
    """Movie/series art. Generic names (poster.jpg…) belong to a folder only when the folder is the title's own."""
    return _art(root, dirs, cache, {
        kind: ([stem + own] if stem and own else []) + (list(names) if generic else []) for kind, names, own in FOLDER_ART
    })


def _is_series_folder(root: Path, dirs: tuple[str, ...], cache: dict[str, Any]) -> bool:
    listing = _listing(root, dirs, cache)
    return "tvshow.nfo" in listing or any(is_dir and _season_number(name) is not None for name, is_dir, _ in listing.values())


def _is_movie_folder(root: Path, dirs: tuple[str, ...], cache: dict[str, Any]) -> bool:
    return bool(dirs) and (MOVIE_PATTERN.match(strip_provider_tags(dirs[-1])[0]) is not None or "movie.nfo" in _listing(root, dirs, cache))


def _owns_as_movie(root: Path, dirs: tuple[str, ...], clean_stem: str, cache: dict[str, Any], nfo_title: str | None) -> bool:
    """5: a movie folder that is not a series folder owns a video named after it.

    Named after it: the stem starts with the folder name (``Heat (1995) - 4K``, Radarr's ``Heat (1995) Bluray-1080p``)
    or, with no ``(Year)`` of its own, starts with the folder's title and year (scene ``Up.2009.1080p``). A stem
    ``{stem}.nfo`` that names another movie wins.
    """
    if not _is_movie_folder(root, dirs, cache) or _is_series_folder(root, dirs, cache):
        return False
    if any(not is_dir and EPISODE_PATTERN.search(name) for name, is_dir, _ in _listing(root, dirs, cache).values()):
        return False
    folder = strip_provider_tags(dirs[-1])[0]
    name, year, _ids = _name_year(dirs[-1])
    if nfo_title and _norm(nfo_title) != _norm(name):
        return False
    if clean_stem.casefold().startswith(folder.casefold()) and not clean_stem[len(folder):len(folder) + 1].isalnum():
        return True
    return year is not None and not MOVIE_PATTERN.match(clean_stem) and _norm(clean_stem).startswith(_norm(name) + str(year))


def _series_spec(root: Path, series_dirs: tuple[str, ...], loose_name: str | None, cache: dict[str, Any]) -> dict[str, Any]:
    """The series title: its own folder, or (``loose_name``) one show among several in a flat folder."""
    if loose_name is not None:
        return {"type": "series", "key": "/".join((*series_dirs, loose_name)), "fields": {"path": {"name": loose_name}, "nfo": {}}}
    name, year, ids = _name_year(series_dirs[-1])
    show = _cached_nfo(root, series_dirs, "tvshow.nfo", cache)
    nfo = nfo_title_fields("tvshow", show[1]) if show and show[0] == "tvshow" else {}
    return {"type": "series", "key": "/".join(series_dirs), "fields": {
        "path": _present(name=name, year=year, provider_ids=ids),
        "nfo": {**nfo, **_folder_art(root, series_dirs, cache, None, generic=True)},
    }}


def _movie_chain(root: Path, dirs: tuple[str, ...], clean_stem: str, stem: str, tag: str | None, fields: dict[str, Any],
                 stem_ids: dict[str, str], cache: dict[str, Any], owned: bool) -> tuple[list[dict[str, Any]], str | None]:
    """([boxset?, movie], version label) for a movie file.

    A file its movie folder owns (``_owns_as_movie``) is keyed by the folder; the label is what follows
    the folder name, if the file starts with it.
    """
    folder = strip_provider_tags(dirs[-1])[0] if dirs else ""
    if owned:
        key = "/".join(dirs)
        label = (clean_stem[len(folder):].strip(" -") or None) if clean_stem.casefold().startswith(folder.casefold()) else None
        name, year, ids = _name_year(dirs[-1])
        art = _folder_art(root, dirs, cache, stem, generic=True)
    else:
        match = MOVIE_PATTERN.match(clean_stem)
        base, label = clean_stem, None
        if match and clean_stem[match.end():].startswith(" - "):
            base, label = clean_stem[: match.end()], clean_stem[match.end() + 3:].strip() or None
        key = "/".join((*dirs, base))
        name, year, ids = _name_year(base, from_file=True)
        art = _folder_art(root, dirs, cache, stem, generic=False)
    nfo = nfo_title_fields("movie", fields) if tag == "movie" else {}
    movie = {"type": "movie", "key": key, "fields": {
        "path": _present(name=name, year=year, provider_ids={**ids, **stem_ids}), "nfo": {**nfo, **art},
    }}
    boxset = fields.get("set") if tag == "movie" else None
    if boxset:
        return [{"type": "boxset", "key": f"set:{boxset.casefold()}", "fields": {"path": {}, "nfo": {"name": boxset}}}, movie], label
    return [movie], label


def _extra(root: Path, dirs: tuple[str, ...], clean_stem: str, cache: dict[str, Any], *, theme: bool = False) -> tuple[str, list[dict[str, Any]]] | None:
    """(extra type, owner chain) when the file is an extra of a movie or series, else None.

    ``theme``: a theme song (``theme.*`` or inside ``theme-music``) is an ``other`` extra of its own folder, never music
.
    """
    owner, extra_type, base = dirs, None, None
    for index in range(len(dirs) - 1, 0, -1):
        if dirs[index].lower() in EXTRA_DIRS:
            owner, extra_type = dirs[:index], EXTRA_DIRS[dirs[index].lower()]
            break
    else:
        if theme:
            extra_type = "other"
        else:
            suffix = EXTRA_SUFFIX_PATTERN.search(clean_stem)
            if suffix is None:
                return None
            base = clean_stem[: suffix.start()].strip()
            extra_type = EXTRA_SUFFIX_TYPES.get(suffix.group(1).lower(), suffix.group(1).lower())
    if owner and _is_series_folder(root, owner, cache):
        return extra_type, [_series_spec(root, owner, None, cache)]
    folder = strip_provider_tags(owner[-1])[0] if owner else ""
    # In a movie folder every extra is the folder's, unless its base names a different "Title (Year)".
    if folder and _is_movie_folder(root, owner, cache) and not (base and MOVIE_PATTERN.match(base) and not base.startswith(folder)):
        name, year, ids = _name_year(owner[-1])
        return extra_type, [{"type": "movie", "key": "/".join(owner), "fields": {"path": _present(name=name, year=year, provider_ids=ids), "nfo": {}}}]
    if base and MOVIE_PATTERN.match(base):
        name, year, ids = _name_year(base, from_file=True)
        return extra_type, [{"type": "movie", "key": "/".join((*owner, base)), "fields": {"path": _present(name=name, year=year, provider_ids=ids), "nfo": {}}}]
    return None  # "Movies/Shorts/…", "Home/Clips/…": a category folder, not an extras folder


def music_key(text: str) -> str:
    """Album and artist keys: casefolded, whitespace collapsed. Global, like boxsets'."""
    return " ".join(text.casefold().split())


def _audio_files(root: Path, dirs: tuple[str, ...], cache: dict[str, Any]) -> list[str]:
    """One folder's audio files in the scan's (sorted) order, theme songs left out."""
    return sorted(
        name for name, is_dir, _tag in _listing(root, dirs, cache).values()
        if not is_dir and not name.startswith(".") and os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS
        and os.path.splitext(name)[0].lower() != "theme"
    )


def _album_cover(root: Path, album_dirs: tuple[str, ...], cache: dict[str, Any], audio_tags: AudioTagReader | None) -> dict[str, dict[str, str]]:
    """An album's Primary: a cover/folder/front/album image in the album folder, then in its disc folders in
    name order; else the first track, in the same order, with an embedded picture. Every track of an album computes the
    same cover, so it is memoized per album folder for the batch."""
    key = "/".join(album_dirs) + "/<album cover>"
    if key not in cache:
        discs = sorted(name for name, is_dir, _tag in _listing(root, album_dirs, cache).values() if is_dir and DISC_DIR_PATTERN.match(name))
        folders = [album_dirs, *((*album_dirs, disc) for disc in discs)]
        cover = next((art for folder in folders if (art := _art(root, folder, cache, {"Primary": ALBUM_ART}))), {})
        if not cover and audio_tags is not None:
            track = next((
                (*folder, name) for folder in folders for name in _audio_files(root, folder, cache)
                if (tags := audio_tags((*folder, name))) is not None and tags.has_picture
            ), None)
            if track is not None:
                try:
                    status = os.lstat(root.joinpath(*track))
                    cover = {"images.Primary": {"embedded": "/".join(track), "tag": f"{status.st_size}:{status.st_mtime_ns}"}}
                except OSError:
                    pass
        cache[key] = cover
    return cache[key]


def _music(root: Path, dirs: tuple[str, ...], stem: str, tags: AudioTags, cache: dict[str, Any],
           audio_tags: AudioTagReader | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """([artist, album] specs, the track's item metadata) for an audio file that is a track.

    First non-empty wins. Album: tag, album.nfo title, the album folder without " (YYYY)", "Unknown album". Album
    artist: tag, "Various Artists" for a compilation, album.nfo artist/albumartist, the folder above the album folder
    (inside the root), the track artist, "Unknown artist".
    """
    disc_folder = DISC_DIR_PATTERN.match(dirs[-1]) if len(dirs) >= 2 else None
    album_dirs = dirs[:-1] if disc_folder else dirs
    artist_dirs = album_dirs[:-1] if len(album_dirs) >= 2 else ()
    nfo_entry = _listing(root, album_dirs, cache).get("album.nfo")
    album_nfo = _cached_nfo(root, album_dirs, nfo_entry[0], cache) if nfo_entry and not nfo_entry[1] else None
    nfo = album_nfo[1] if album_nfo else {}
    folder_year = YEAR_SUFFIX_PATTERN.search(album_dirs[-1]) if album_dirs else None
    folder_name = (album_dirs[-1][: folder_year.start()].strip() if folder_year else album_dirs[-1]) if album_dirs else None
    track = TRACK_PATTERN.match(stem)
    album = tags.album or clean_text(nfo.get("title") or "") or folder_name or UNKNOWN_ALBUM
    album_artist = (
        tags.album_artist or (VARIOUS_ARTISTS if tags.compilation else None) or clean_text(nfo.get("artist") or "")
        or clean_text(nfo.get("albumartist") or "") or (artist_dirs[-1] if artist_dirs else None) or tags.artist or UNKNOWN_ARTIST
    )
    year = tags.year or _year(nfo) or (int(folder_year.group(1)) if folder_year else None)
    genres = list(tags.genres)
    artist_spec = {"type": "artist", "key": f"artist:{music_key(album_artist)}", "fields": {
        "path": {"name": album_artist}, "nfo": _art(root, artist_dirs, cache, {"Primary": ARTIST_ART}) if artist_dirs else {},
    }}
    album_spec = {"type": "album", "key": f"album:{music_key(album_artist)}/{music_key(album)}", "fields": {
        "path": _present(name=album, year=year),
        "nfo": {**({"genres": genres} if genres else {}), **_album_cover(root, album_dirs, cache, audio_tags)},
    }}
    metadata = {
        "track": tags.title or (_clean(track.group("title")) if track else stem),
        "artist": tags.artist or album_artist, "album_artist": album_artist, "album": album,
        "track_number": tags.track or (tag_number(track.group("number")) if track else None),
        "disc_number": tags.disc or (tag_number(disc_folder.group(1)) if disc_folder else None),
        "release_year": year, "genre": ", ".join(genres) or None,
    }
    return [artist_spec, album_spec], metadata


def _subtitles(stem: str, siblings: frozenset[str]) -> list[dict[str, Any]]:
    """Sibling ``{stem}.{tokens}.{srt|ass|ssa|vtt}`` files, sorted by filename (stable ``s:{n}`` track order)."""
    found = []
    for name in sorted(siblings):
        if not name.startswith(stem + "."):
            continue
        *tokens, fmt = name[len(stem) + 1:].split(".")
        if fmt.lower() not in SUBTITLE_FORMATS:
            continue
        flags = {token.lower() for token in tokens}
        language = next((token.lower() for token in tokens if token.isalpha() and 2 <= len(token) <= 3 and token.lower() not in SUBTITLE_FLAGS), None)
        found.append({
            "filename": name, "language": language, "forced": "forced" in flags,
            "hearing_impaired": bool(flags & {"sdh", "cc", "hi"}), "default": "default" in flags, "format": fmt.lower(),
        })
    return found


def describe(root: Path, parts: tuple[str, ...], siblings: frozenset[str], cache: dict[str, Any], *,
             audio_tags: AudioTagReader | None = None) -> dict[str, Any]:
    """Title, grouping fields, metadata, sidecar artwork and the Media title chain for one media file.

    ``siblings`` are the regular (non-symlink) file names in the file's directory; ``cache``
    memoizes folder NFOs and listings across one batch. ``titles`` is the outer→leaf chain of
    ``{"type", "key", "fields": {"path": {...}, "nfo": {...}}}`` specs with root-relative keys
    (boxset, album and artist keys are global); ``library_import._link_titles`` writes them through ``apply_field``.
    ``audio_tags`` reads an audio file's embedded tags by root-relative parts (the scanner's cached reader);
    without it, music is classified from folders and ``album.nfo`` alone.
    """
    name = parts[-1]
    stem, ext = os.path.splitext(name)
    clean_stem, stem_ids = strip_provider_tags(stem)
    folder = root.joinpath(*parts[:-1])
    dirs = parts[:-1]
    audio = ext.lower() in AUDIO_EXTENSIONS
    # A theme song is an extra of its folder, never music.
    theme = audio and (clean_stem.lower() == "theme" or any(part.lower() == "theme-music" for part in dirs))
    tags = audio_tags(parts) if audio and not theme and audio_tags is not None else None

    own = parse_nfo(folder / f"{stem}.nfo") if f"{stem}.nfo" in siblings else None
    stem_nfo_title = own[1].get("title") if own and own[0] == "movie" else None
    if own is None and "movie.nfo" in siblings:
        own = _cached_nfo(root, dirs, "movie.nfo", cache)
    tag, fields = own if own else (None, {})
    metadata: dict[str, Any] = {}
    title: str | None = fields.get("title")
    title_source = "sidecar" if title else "filename"
    uploader = playlist = None
    kind = "unclassified"
    artwork_dirs = dirs
    titles: list[dict[str, Any]] = []
    if theme:
        extra = _extra(root, dirs, clean_stem, cache, theme=True)
    else:
        extra = None if audio else _extra(root, dirs, clean_stem, cache)
    owned = not audio and not extra and bool(dirs) and _owns_as_movie(root, dirs, clean_stem, cache, stem_nfo_title)

    episode = EPISODE_PATTERN.search(clean_stem)
    if extra:
        kind, titles = "extra", extra[1]
        title = title or _clean(clean_stem)
    elif tag == "episodedetails" or (tag is None and episode):
        kind = "episode"
        season = _int(fields.get("season")) if tag else None
        number = _int(fields.get("episode")) if tag else None
        if episode and season is None:
            season = int(episode.group(1) or episode.group(3))
        if episode and number is None:
            number = int(episode.group(2) or episode.group(4))
        rest, end = (episode.end() if episode else 0), None
        multi = MULTI_EPISODE_PATTERN.match(clean_stem, episode.end()) if episode else None
        if multi and number is not None and int(multi.group(1) or multi.group(2)) > number:
            rest, end = multi.end(), int(multi.group(1) or multi.group(2))
        in_season_dir = bool(dirs) and _season_number(dirs[-1]) is not None
        series_dirs = dirs[:-1] if in_season_dir else dirs
        show = _cached_nfo(root, series_dirs, "tvshow.nfo", cache)
        prefix = _clean(clean_stem[: episode.start()]) if episode else ""
        series = fields.get("showtitle") or (show[1].get("title") if show else None)
        if not series and prefix:
            series = prefix
        if not series and series_dirs:
            series = strip_provider_tags(series_dirs[-1])[0]
        episode_title = _clean(clean_stem[rest:]) if episode and not title else None
        if not title:
            title = episode_title or (f"{series} S{season:02d}E{number:02d}" if series and season is not None and number is not None else stem)
        metadata.update(series=series, season_number=season, episode_number=number, episode_number_end=end, episode=title)
        uploader = series
        playlist = f"{series} · Season {season}" if series and season is not None else None
        artwork_dirs = series_dirs
        if series and season is not None and number is not None:
            # A real show folder (season dirs or tvshow.nfo) owns every episode in it.
            # A flat folder is the show's own only when the filename names it; a flat folder of many
            # shows keys each show by name. Upgrade: series-level NFO match.
            own_folder = bool(series_dirs) and (
                in_season_dir or show is not None or _is_series_folder(root, series_dirs, cache)
                or not prefix or _norm(prefix).startswith(_norm(_name_year(series_dirs[-1])[0]))
            )
            series_spec = _series_spec(root, series_dirs, None if own_folder else (prefix or series), cache)
            season_art = _art(root, series_dirs, cache, {"Primary": ["season-specials-poster" if season == 0 else f"season{season:02d}-poster"]}) if own_folder else {}
            if in_season_dir and not season_art:
                season_art = _art(root, dirs, cache, {"Primary": ["poster", "folder"]})
            episode_nfo = nfo_title_fields(tag, fields) if tag == "episodedetails" else {}
            titles = [
                series_spec,
                {"type": "season", "key": f"{series_spec['key']}#s{season}", "fields": {
                    "path": {"name": "Specials" if season == 0 else f"Season {season}", "index_number": season}, "nfo": season_art}},
                {"type": "episode", "key": f"{series_spec['key']}#s{season}e{number}", "fields": {
                    "path": _present(name=episode_title or f"Episode {number}", index_number=number, index_number_end=end, provider_ids=stem_ids),
                    "nfo": {**episode_nfo, **_art(root, dirs, cache, {"Primary": [f"{stem}-thumb", stem]})}}},
            ]
    elif tag == "movie" or (tag is None and not audio and (MOVIE_PATTERN.match(clean_stem) or owned)):
        kind = "movie"
        match = MOVIE_PATTERN.match(clean_stem)
        titles, label = _movie_chain(root, dirs, clean_stem, stem, tag, fields, stem_ids, cache, owned)
        path_fields = titles[-1]["fields"]["path"]
        title = title or (_clean(match.group("title")) if match else path_fields.get("name") or clean_stem)
        year = _year(fields) or (int(match.group("year")) if match else path_fields.get("year"))
        metadata["release_year"] = year
        if label:
            title, metadata["version"] = f"{title} · {label}", label
    elif audio and not theme and ((tags is not None and tags.album) or (
        len(dirs) >= 2 and not any(os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS for name in siblings)
    )):
        # 4: an album tag, or a music folder (depth ≥ 2, no video beside it), makes a track.
        kind = "track"
        titles, music = _music(root, dirs, stem, tags or AudioTags(), cache, audio_tags)
        title, title_source = music["track"], "sidecar" if tags is not None and tags.title else "filename"
        metadata.update(music)
        uploader, playlist = music["artist"], music["album"]
    if not title:
        title = stem
    if fields.get("plot"):
        metadata["description"] = fields["plot"]
    if fields.get("genre"):
        metadata["genre"] = ", ".join(fields["genre"])

    poster = _first(siblings, _images([f"{stem}-poster", f"{stem}-thumb", f"{stem}-cover", stem, "poster", "folder", "cover"]))
    fanart = _first(siblings, _images([f"{stem}-fanart", "fanart", "backdrop"]))
    prefix = poster_prefix = "/".join(dirs)
    if poster is None and artwork_dirs != dirs:
        # An episode in a season folder falls back to the show poster beside tvshow.nfo.
        key = "/".join((*artwork_dirs, "<poster>"))
        if key not in cache:
            show_folder = root.joinpath(*artwork_dirs)
            cache[key] = next((name for name in _images(["poster", "folder", "cover"])
                               if (show_folder / name).is_file() and not (show_folder / name).is_symlink()), None)
        poster, poster_prefix = cache[key], "/".join(artwork_dirs)
    metadata.update(
        title=title,
        ext=ext.lstrip(".").lower() or None,
        lumina_import_kind=kind,
        lumina_title_source=title_source,
        lumina_local_artwork=f"{poster_prefix}/{poster}".lstrip("/") if poster else None,
        lumina_local_fanart=f"{prefix}/{fanart}".lstrip("/") if fanart else None,
        lumina_subtitles=(None if audio else _subtitles(stem, siblings)) or None,
    )
    return {
        "title": title,
        "uploader": uploader,
        "playlist_name": playlist,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
        "titles": titles,
        "extra_type": extra[0] if extra else None,
    }
