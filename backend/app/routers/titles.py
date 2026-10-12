"""Media title routes for Lumina's TV/movie UI.

Every lookup goes through TitleService.get_visible: a title another member cannot see is 404.
"""
from __future__ import annotations

import time
from typing import Annotated, Literal, get_args

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import StringConstraints
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    EpisodeSummaries, ImageType, KeyScenes, LibraryUpNext, TitleDetail, TitleFacets, TitlePage, TitleCategory, TitleResolution, TitleSort, TitleSummary, TitleType, TitleUserData, TitleWatchedRequest,
)
from app.models import MediaTitle, User
from app.persistence import write_transaction
from app.routers import visible_item_or_404
from app.security import get_current_user
from app.services import ai_extras, member_access
from app.services.artwork import ArtworkService
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id
from app.services.titles import CATEGORY_TYPES, TitleFilters, TitleService, decode_title_cursor, encode_title_cursor, image_key, image_url, title_image_bytes

TITLE_NOT_FOUND = "Title not found"
Genre = Annotated[str, StringConstraints(min_length=1, max_length=64)]


def visible_title_or_404(db: Session, title_id: str, user: User) -> MediaTitle:
    title = TitleService(db).get_visible(parse_item_id(title_id), user)
    if title is None:
        raise HTTPException(status_code=404, detail=TITLE_NOT_FOUND)
    return title


def list_titles(
    type_: TitleType | None = Query(None, alias="type"),
    category: TitleCategory | None = None,
    sort: TitleSort = "name",
    cursor: str | None = Query(None, max_length=2048),
    limit: int = Query(60, ge=1, le=200),
    letter: str | None = Query(None, pattern=r"^(#|[A-Z])$"),
    unwatched: bool = False,
    in_progress: bool = False,
    favorites: bool = False,
    genre: Annotated[list[Genre], Query(max_length=10)] = [],  # noqa: B006 - FastAPI copies query defaults
    year_from: int | None = Query(None, ge=1870, le=2100),
    year_to: int | None = Query(None, ge=1870, le=2100),
    resolution: Annotated[list[TitleResolution], Query(max_length=4)] = [],  # noqa: B006
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> TitlePage:
    """A keyset page of the wall: total and letters only on a first page (no cursor, no letter).

    ``category`` picks a Library tab's wall; a ``type`` beside it must be one of its types.
    Neither lists every movie and series (the All landing's spotlight).
    """
    if category is not None and type_ is not None and type_ not in CATEGORY_TYPES[category]:
        raise HTTPException(status_code=400, detail="Invalid category")
    types = (type_,) if type_ else CATEGORY_TYPES[category] if category else ("movie", "series")
    filters = TitleFilters(unwatched, in_progress, favorites, tuple(genre), year_from, year_to, tuple(dict.fromkeys(resolution)))
    digest = filters.digest(types, sort, category)
    if letter is not None and (sort != "name" or cursor):
        raise HTTPException(status_code=400, detail="Invalid letter")
    try:
        after = decode_title_cursor(cursor, sort, digest) if cursor else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    service = TitleService(db)
    titles, start, following = service.page(
        current_user, types=types, sort=sort, limit=limit, filters=filters, after=after, letter=letter, category=category,
    )
    total = letters = None
    if after is None and letter is None:
        if sort == "name":
            letters, total = service.letters(current_user, types=types, filters=filters, category=category)
        else:
            total = service.count(current_user, types=types, filters=filters, category=category)
    return TitlePage(
        items=service.summaries(current_user, titles), start_index=start, total=total, letters=letters,
        next_cursor=encode_title_cursor(sort, digest, following) if following else None,
    )


FACETS_TTL_SECONDS = 60.0
# A per-process 60 s cache per (member, role, type, category). Facets only feed the filter drawer's counts;
# categories.recategorise() clears it. Move to an invalidated cache if a scan-time change must show at once.
_facets: dict[tuple[str, str, int, str, str], tuple[float, TitleFacets]] = {}


def title_facets(
    type_: Literal["movie", "series", "album"] | None = Query(None, alias="type"),
    category: TitleCategory | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> TitleFacets:
    """One wall's facets: exactly one of ``type`` or ``category``."""
    if (type_ is None) == (category is None):
        raise HTTPException(status_code=400, detail="Give exactly one of type or category")
    # A role or member access change never reuses counts.
    key, now = (current_user.id, current_user.role, member_access.generation(current_user.id), type_ or "", category or ""), time.monotonic()
    cached = _facets.get(key)
    if cached is not None and now - cached[0] < FACETS_TTL_SECONDS:
        return cached[1]
    facets = TitleService(db).facets(current_user, type_, category=category)
    _facets[key] = (now, facets)
    return facets


def get_title(title_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> TitleDetail:
    return TitleService(db).detail(current_user, visible_title_or_404(db, title_id, current_user))


def list_episodes(
    title_id: str,
    season: int | None = Query(None, ge=0, le=100_000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[TitleSummary]:
    series = visible_title_or_404(db, title_id, current_user)
    if series.type != "series":
        raise HTTPException(status_code=404, detail=TITLE_NOT_FOUND)
    service = TitleService(db)
    return service.summaries(current_user, service.episodes(current_user, series, season))


def list_episode_summaries(
    title_id: str,
    season: int = Query(ge=0, le=100_000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EpisodeSummaries:
    series = visible_title_or_404(db, title_id, current_user)
    if series.type != "series":
        raise HTTPException(status_code=404, detail=TITLE_NOT_FOUND)
    return ai_extras.episode_summaries(db, current_user, series, season)


def get_key_scenes(title_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> KeyScenes:
    return ai_extras.key_scenes(db, current_user, visible_title_or_404(db, title_id, current_user))


def list_next_up(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[TitleSummary]:
    service = TitleService(db)
    return service.summaries(current_user, service.next_up_titles(current_user, limit=limit))


def library_up_next(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryUpNext:
    return TitleService(db).up_next(current_user, visible_item_or_404(db, item_id, current_user))


def set_title_watched(
    title_id: str, payload: TitleWatchedRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> TitleUserData:
    title = visible_title_or_404(db, title_id, current_user)
    service = TitleService(db)
    with write_transaction(db, name="title_watched"):
        service.set_watched(current_user, title, payload.watched)
    return service.load(current_user, [title]).user_data(title)


def title_image(
    title_id: str, image_type: str, request: Request, tag: str | None = Query(None, max_length=64),
    index: int = Query(0, ge=0, le=4), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    if image_type not in get_args(ImageType):
        raise HTTPException(status_code=404, detail="Image not found")
    title = visible_title_or_404(db, title_id, current_user)
    try:
        key = image_key(image_type, index)
        content_type, content = title_image_bytes(db, title, key, request.app.state.artwork)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    current = image_url(title, key)
    fresh = tag is not None and current is not None and current.endswith(f"tag={tag}")
    return Response(content=content, media_type=content_type, headers={
        "Cache-Control": "private, max-age=31536000, immutable" if fresh else "private, no-cache",
        "X-Content-Type-Options": "nosniff",
    })


def _favorite_target_or_404(db: Session, target_id: str, user: User) -> str:
    target = parse_item_id(target_id)
    item = LibraryService(db).get_item(target, user) if target else None
    if target and (TitleService(db).get_visible(target, user) is not None or (item is not None and item.status != "missing")):
        return target
    raise HTTPException(status_code=404, detail="Not found")


def add_favorite(target_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    target = _favorite_target_or_404(db, target_id, current_user)
    with write_transaction(db, name="favorite_add"):
        TitleService(db).set_favorite(current_user, target, True)
    return Response(status_code=204)


def remove_favorite(target_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    target = _favorite_target_or_404(db, target_id, current_user)
    with write_transaction(db, name="favorite_remove"):
        TitleService(db).set_favorite(current_user, target, False)
    return Response(status_code=204)


def register(app: FastAPI, artwork: ArtworkService) -> None:
    app.state.artwork = artwork  # the process-wide ArtworkService from main.py (also read by routers/jellyfin.py)
    app.get("/api/titles", response_model=TitlePage)(list_titles)
    app.get("/api/library/{item_id}/up-next", response_model=LibraryUpNext)(library_up_next)
    app.get("/api/titles/next-up", response_model=list[TitleSummary])(list_next_up)  # above /{title_id}
    app.get("/api/titles/facets", response_model=TitleFacets)(title_facets)  # above /{title_id}
    app.get("/api/titles/{title_id}", response_model=TitleDetail)(get_title)
    app.get("/api/titles/{title_id}/episodes", response_model=list[TitleSummary])(list_episodes)
    app.get("/api/titles/{title_id}/episode-summaries", response_model=EpisodeSummaries)(list_episode_summaries)
    app.get("/api/titles/{title_id}/key-scenes", response_model=KeyScenes)(get_key_scenes)
    app.put("/api/titles/{title_id}/watched", response_model=TitleUserData)(set_title_watched)
    app.api_route("/api/titles/{title_id}/images/{image_type}", methods=["GET", "HEAD"])(title_image)
    app.put("/api/me/favorites/{target_id}", status_code=204)(add_favorite)
    app.delete("/api/me/favorites/{target_id}", status_code=204)(remove_favorite)
