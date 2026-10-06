"""Cast photos : NFO-credited people, their photo files and image URLs.

A person's photo is a Jellyfin ``metadata/People`` file under the operator's read-only LUMINA_PEOPLE_DIR,
else an image.tmdb.org headshot while TMDB is on. Files are read only through ``artifact_file`` (inside the folder,
no symlink anywhere below it, a regular file) and ``read_regular_file`` (O_NOFOLLOW, capped). The rendition pipeline
sees a person as a title of type ``person`` (``subject``).
"""
from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSettings, MediaTitle, NfoPerson, PersonOverride, TitleUpload
from app.persistence import read_regular_file
from app.services import art_urls, tmdb
from app.services.artwork import ArtworkError, ArtworkService
from app.services.media_artifacts import artifact_file
from app.services.local_metadata import people_folder_guess
from app.services.media_titles import person_key, person_name_id

PHOTO_MAX_BYTES = 2 * 1024 * 1024  # a Jellyfin folder.jpg is ~100 KB; also renditions' on-demand original fallback cap
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def people_dir() -> Path | None:
    """The configured folder, resolved (artifact_file compares real paths); None when unset."""
    value = settings.people_dir.strip()
    return Path(os.path.realpath(value)) if value else None


def people_dir_online() -> bool:
    """Configured and not empty: an unmounted bind mount shows as an empty folder, and must not fail every photo."""
    base = people_dir()
    if base is None:
        return False
    try:
        with os.scandir(base) as entries:
            return next(entries, None) is not None
    except OSError:
        return False


def tmdb_enabled(db: Session) -> bool:
    return tmdb.effective_api_key(db.scalar(select(AppSettings).limit(1)) or AppSettings()) is not None


def photo_entry(image_path: str | None, tmdb_path: str | None, tmdb_on: Callable[[], bool]) -> dict[str, str] | None:
    """The stored-image entry a photo reads from: the Jellyfin file while the folder is configured, else the TMDB
    headshot while TMDB is on (``tmdb_on`` is called only then). Never fetches."""
    if image_path and people_dir() is not None:
        return {"path": image_path}
    if tmdb_path and tmdb_on():
        return {"tmdb": tmdb_path}
    return None


def subject(db: Session, person_id: str) -> SimpleNamespace | None:
    """An NFO person, or any person with a household photo (#164), as the rendition pipeline sees a title: type
    "person", one Primary image, no storage root. A household photo wins over the Jellyfin file and TMDB."""
    override = db.get(PersonOverride, person_id)
    if override is not None and override.photo:
        entry: dict | None = {"upload": override.photo}
        return SimpleNamespace(id=person_id, type="person", images={"Primary": entry}, root_id=None, created_at=None)
    person = db.get(NfoPerson, person_id)
    if person is None:
        return None
    entry = photo_entry(person.image_path, person.tmdb_path, lambda: tmdb_enabled(db))
    return SimpleNamespace(id=person.id, type="person", images={"Primary": entry} if entry else {}, root_id=None, created_at=None)


def image_bytes(person: SimpleNamespace, artwork: ArtworkService, db: Session | None = None) -> tuple[str, bytes]:
    """(content type, bytes) of a person's photo; FileNotFoundError when there is none, or it is unsafe or unreadable."""
    entry = person.images.get("Primary") or {}
    if "upload" in entry:
        row = db.execute(select(TitleUpload.content_type, TitleUpload.data).where(TitleUpload.sha256 == entry["upload"])).first() if db else None
        if row is None:
            raise FileNotFoundError("Upload is missing")
        return row.content_type, bytes(row.data)
    if "tmdb" in entry:
        try:
            resolved = tmdb.load_image(artwork, entry["tmdb"], "Person")
        except ArtworkError as exc:
            raise FileNotFoundError("TMDB photo is unavailable") from exc
        return resolved.content_type, resolved.content
    base = people_dir()
    if base is None or "path" not in entry:
        raise FileNotFoundError("No photo")
    path = artifact_file(base, entry["path"])  # inside the folder, no symlink anywhere below it, a regular file
    content_type = PHOTO_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise FileNotFoundError("Unsupported photo type")
    try:
        return content_type, read_regular_file(path, PHOTO_MAX_BYTES)
    except OSError as exc:
        raise FileNotFoundError("Photo is unreadable") from exc


def split_people(people: list[dict], photos: dict[str, dict]) -> list[dict]:
    """A title's NFO people refs with their ``person_id`` and without the thumb. Each thumb goes to ``photos`` (person id
    -> nfo_people values; a credit with a thumb wins over one without) for save_people, so refs stay small."""
    refs = []
    for person in people:
        person_id = person_name_id(person["name"])
        refs.append({**{key: value for key, value in person.items() if key != "thumb"}, "person_id": person_id})
        if person.get("thumb") or person_id not in photos:
            photos[person_id] = {"name": person["name"], **(person.get("thumb") or {})}
    return refs


def split_crew(crew: list[dict], photos: dict[str, dict]) -> list[dict]:
    """A title's NFO crew refs (directors, writers) with their ``person_id``. With no thumb to go on, each gets its
    guessed Jellyfin folder as a ``guess``, which any real thumb (this batch or stored) beats."""
    refs = []
    for member in crew:
        person_id = person_name_id(member["name"])
        refs.append({**member, "person_id": person_id})
        entry = photos.setdefault(person_id, {"name": member["name"]})
        if "path" not in entry and "tmdb" not in entry and (guess := people_folder_guess(member["name"])):
            entry.setdefault("guess", guess)
    return refs


def save_people(db: Session, photos: dict[str, dict]) -> None:
    """Upsert one scan batch's credited people with one SELECT. A credit without a thumb never erases a known photo, and
    a guessed crew folder fills only a person with no photo at all (a real thumb, local or TMDB, always beats a guess).

    One IN list per batch (≤ 500 files × 30 credits, under SQLite's 32,766 variables); chunk it if batches grow.
    """
    if not photos:
        return
    known = {person.id: person for person in db.scalars(select(NfoPerson).where(NfoPerson.id.in_(photos)))}
    for person_id, values in photos.items():
        person = known.get(person_id)
        if person is None:
            db.add(NfoPerson(id=person_id, name=values["name"], image_path=values.get("path") or values.get("guess"),
                             tmdb_path=values.get("tmdb")))  # split_crew never guesses beside a tmdb thumb
            continue
        unphotographed = person.image_path is None and person.tmdb_path is None and "tmdb" not in values
        path = values.get("path") or (values.get("guess") if unphotographed else None)
        for column, value in (("image_path", path), ("tmdb_path", values.get("tmdb"))):
            if value is not None and getattr(person, column) != value:
                setattr(person, column, value)


# person id -> photo facts for a page of credits, one statement: TMDB people with a headshot, then NFO people with their
# Primary artwork row (a current row that failed or is unsupported means no photo). The ids travel as one JSON parameter,
# so a page of any size stays one SQLite variable (a Jellyfin page can credit more people than the 32,766-variable limit).
PHOTO_SQL = text(
    "WITH ids(id) AS (SELECT value FROM json_each(:ids))"
    " SELECT id, 'tmdb', NULL, profile_path, NULL, NULL FROM people WHERE id IN ids AND profile_path IS NOT NULL"
    " UNION ALL SELECT n.id, 'nfo', n.image_path, n.tmdb_path, a.source_key, a.state FROM nfo_people n"
    " LEFT JOIN title_artwork a ON a.title_id = n.id AND a.image_type = 'Primary' WHERE n.id IN ids"
    # #164 household edits: a renamed person's name, an uploaded photo's sha (each wins over the sources)
    " UNION ALL SELECT id, 'override', name, photo, NULL, NULL FROM person_overrides WHERE id IN ids"
)


def photo_url(person_id: str, source: str) -> str:
    """The signed 240 px portrait (/api/art); the 120 px one differs only in its width."""
    key = art_urls.source_key(person_id, "Primary", source)
    return art_urls.rendition_template(person_id, "Primary", key).replace("{w}", str(max(art_urls.PERSON_WIDTHS)))


def image_urls(db: Session, ids: set[str]) -> dict[str, str]:
    return photos_and_names(db, ids)[0]


def photos_and_names(db: Session, ids: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """(person id -> image URL, person id -> household name) in one statement. A URL is a household photo's signed
    portrait, /api/people/{id}/image for a TMDB headshot, else an NFO photo's signed portrait. None for a photo whose
    current rendition failed, or whose People folder is unmounted, so the page shows the initial. Whether TMDB is on
    costs one more query, only when a credited person's only photo is a TMDB URL."""
    if not ids:
        return {}, {}
    tmdb_on = functools.cache(lambda: tmdb_enabled(db))
    online = functools.cache(people_dir_online)  # unmounted: no URL, so no 404 per credit
    urls: dict[str, str] = {}
    uploads: dict[str, str] = {}
    names: dict[str, str] = {}
    for person_id, kind, image_path, tmdb_path, key, state in db.execute(PHOTO_SQL, {"ids": json.dumps(sorted(ids))}):
        if kind == "override":
            if image_path:
                names[person_id] = image_path
            if tmdb_path:
                uploads[person_id] = tmdb_path
        elif kind == "tmdb":
            urls[person_id] = f"/api/people/{person_id}/image"
        else:
            entry = photo_entry(image_path, tmdb_path, tmdb_on)
            source = (entry.get("path") or entry.get("tmdb")) if entry else None
            if entry and "path" in entry and not online():
                continue
            if source and (key != art_urls.source_key(person_id, "Primary", source) or state == "ready"):
                urls[person_id] = photo_url(person_id, source)
    return urls | {person_id: photo_url(person_id, sha) for person_id, sha in uploads.items()}, names


def top_up(db: Session, title: MediaTitle, tmdb_people: list[dict]) -> None:
    """TMDB top-up: each NFO-credited person of ``title`` takes the headshot TMDB lists under the same name for this
    same title; a namesake elsewhere never lends a photo. Shown only when no Jellyfin photo is configured (photo_entry)."""
    headshots = {person_key(row["name"]): row["profile_path"] for row in tmdb_people if row.get("profile_path")}
    wanted = {
        ref["person_id"]: headshots[key]
        for ref in (title.metadata_json or {}).get("people") or []
        if isinstance(ref, dict) and isinstance(ref.get("person_id"), str) and isinstance(ref.get("name"), str)
        and (key := person_key(ref["name"])) in headshots
    }
    for person in db.scalars(select(NfoPerson).where(NfoPerson.id.in_(wanted))) if wanted else ():
        person.tmdb_path = wanted[person.id]
