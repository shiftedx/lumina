"""TMDB image candidates for one title and image type. One ``/images`` call per request."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import MediaTitle
from app.services import tmdb
from app.services.title_images import ALLOWED_TYPES  # TMDB has no season backdrops: those are upload-only


class NoTmdbId(Exception):
    """The title (or its series ancestor) has no usable TMDB id."""


def _tmdb_id(db: Session, title: MediaTitle) -> str:
    node, hops = title, 0
    while node is not None and hops < 3:
        value = (node.provider_ids or {}).get("Tmdb")
        if node.type in ("movie", "series"):
            if isinstance(value, str) and value.isdigit():
                return value
            break
        node = db.get(MediaTitle, node.parent_id) if node.parent_id else None
        hops += 1
    raise NoTmdbId


def _number(title: MediaTitle) -> int:
    return title.index_number if title.index_number is not None else 0


def candidates(client: tmdb.TmdbClient, db: Session, title: MediaTitle, image_type: str) -> list[dict]:
    if title.type not in ALLOWED_TYPES.get(image_type, ()):
        raise ValueError("That title cannot have this image type.")
    if title.type == "season" and image_type == "Backdrop":
        return []
    tid, kind = _tmdb_id(db, title), image_type
    if title.type == "movie":
        path = f"/movie/{tid}/images"
    elif title.type == "series":
        path = f"/tv/{tid}/images"
    elif title.type == "season":
        path = f"/tv/{tid}/season/{_number(title)}/images"
    else:
        season = db.get(MediaTitle, title.parent_id)
        path, kind = f"/tv/{tid}/season/{_number(season)}/episode/{_number(title)}/images", "Still"
    return tmdb.image_candidates(client.get(path, include_image_language=client.image_languages), kind, client.lang2)
