"""Member watchlist actions for authenticated Jellyfin Connected apps.

This is a narrow detail-action adapter over the existing WatchQueue service.
It deliberately does not expose generic playlist editing or a second watchlist.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id
from app.services.titles import TitleService, preferred_versions
from app.services.watch_queue import WatchQueueFull, WatchQueueService


router = APIRouter(prefix="/jellyfin/lumina/watchlist")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


class WatchlistState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included: StrictBool


def _resolved_item_id(db: Session, caller: JellyfinCaller, value: str) -> str:
    """Resolve the same playable target used by a web detail action.

    A Jellyfin detail may name a vault item, a movie/episode title, a Series
    (whose detail CTA is its current play-next episode), or a Season. The
    stored queue always contains a concrete visible library item.
    """
    item_id = parse_item_id(value)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    library = LibraryService(db)
    if library.get_item(item_id, caller.user) is not None:
        return item_id

    titles = TitleService(db)
    title = titles.get_visible(item_id, caller.user)
    if title is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    if title.type == "series":
        title_id = titles.play_next(caller.user, title)
    elif title.type == "season":
        children = titles.children(caller.user, title)
        title_id = next((child.id for child in children if child.type == "episode"), None)
    elif title.type in {"movie", "episode"}:
        title_id = title.id
    else:
        raise HTTPException(status_code=422, detail="Only movies and episodes can be added to the watchlist")
    if title_id is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    resolved = preferred_versions(db, caller.user, [title_id]).get(title_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return resolved.id


def _entry_id(service: WatchQueueService, caller: JellyfinCaller, item_id: str) -> str | None:
    _revision, entries = service.read(caller.user)
    return next((entry.id for entry, item in entries if item is not None and item.id == item_id), None)


@router.get("/{item_id}")
def get_watchlist_state(item_id: str, caller: Caller, db: Db) -> WatchlistState:
    resolved = _resolved_item_id(db, caller, item_id)
    return WatchlistState(included=_entry_id(WatchQueueService(db), caller, resolved) is not None)


@router.put("/{item_id}")
def put_watchlist_state(item_id: str, body: WatchlistState, caller: Caller, db: Db) -> WatchlistState:
    resolved = _resolved_item_id(db, caller, item_id)
    service = WatchQueueService(db)
    entry_id = _entry_id(service, caller, resolved)
    if body.included and entry_id is None:
        try:
            service.add(caller.user, library_item_id=resolved)
        except WatchQueueFull as exc:
            raise HTTPException(status_code=422, detail="The watchlist is full") from exc
    elif not body.included and entry_id is not None:
        service.remove(caller.user, entry_id)
    return WatchlistState(included=body.included)
