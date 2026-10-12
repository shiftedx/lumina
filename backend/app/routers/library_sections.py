"""GET /api/library/sections: what the member can see per Library tab.

The tab row, the All landing's kicker and its chapters read these counts: four grouped counts under the member's
visibility, cached per process for 30 s. categories.recategorise() clears the cache when titles change tab.
"""
from __future__ import annotations

import time

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import LibrarySections
from app.models import LibraryItem, MediaTitle, User
from app.security import get_current_user
from app.services import member_access
from app.services.library import LibraryService
from app.services.titles import MUSIC_TYPES, WALL_TYPES

SECTIONS_TTL_SECONDS = 30.0
SECTION_KINDS = {"audio": "saved_audio", "video": "youtube", "recording": "recordings"}  # item kind -> LibrarySections field
# A per-process 30 s cache per (member, role), like routers/titles._facets; move to an invalidated cache if a
# finished scan must show its new tab at once.
_sections: dict[tuple[str, str, int], tuple[float, LibrarySections]] = {}


def clear() -> None:
    _sections.clear()


def count_sections(db: Session, user: User) -> LibrarySections:
    """Visible wall titles by category, music titles by type, visible non-missing items by kind, and missing items."""
    visible = LibraryService.visible_title_predicate(user)
    walls = dict(db.execute(select(MediaTitle.category, func.count()).where(WALL_TYPES, visible).group_by(MediaTitle.category)).all())
    music = dict(db.execute(select(MediaTitle.type, func.count()).where(MUSIC_TYPES, visible).group_by(MediaTitle.type)).all())
    items = LibraryService.visible_predicate(user)
    kinds = dict(db.execute(
        select(LibraryItem.kind, func.count())
        .where(LibraryItem.kind.in_(list(SECTION_KINDS)), LibraryItem.status != "missing", items)
        .group_by(LibraryItem.kind)
    ).all())
    deleted = db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.status == "missing", items)) or 0
    return LibrarySections(
        movies=walls.get("movies", 0), shows=walls.get("shows", 0), anime=walls.get("anime", 0),
        albums=music.get("album", 0), artists=music.get("artist", 0),
        **{field: kinds.get(kind, 0) for kind, field in SECTION_KINDS.items()}, deleted=deleted,
    )


def get_library_sections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibrarySections:
    key, now = (current_user.id, current_user.role, member_access.generation(current_user.id)), time.monotonic()  # nor an access change
    cached = _sections.get(key)
    if cached is not None and now - cached[0] < SECTIONS_TTL_SECONDS:
        return cached[1]
    sections = count_sections(db, current_user)
    _sections[key] = (now, sections)
    return sections


def register(app: FastAPI) -> None:
    app.get("/api/library/sections", response_model=LibrarySections)(get_library_sections)  # before /api/library/{item_id}
