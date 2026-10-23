"""Small native-library extensions that do not alter generic Jellyfin DTOs."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.services import jellyfin as jf


router = APIRouter(prefix="/jellyfin/lumina/library")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


@router.get("/alphabet")
def alphabet(caller: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    """Return compact absolute A–Z anchors for the caller's current video wall."""
    kinds = query.types()
    requested = query.csv("includeitemtypes")
    sorts = [value.casefold() for value in query.csv("sortby")]
    orders = [value.casefold() for value in query.csv("sortorder")]
    supported = (
        query.recursive
        and (not requested or bool(kinds) and kinds <= {"movie", "series"})
        and not query.csv("excludeitemtypes")
        and not query.csv("ids")
        and not query.searchterm
        and not query.sourceprovider
        and not query.seriesid
        and (not sorts or sorts == ["sortname"])
        and (not orders or orders == ["ascending"])
    )
    if not supported:
        raise HTTPException(status_code=400, detail="Alphabet indexing is available only for name-sorted video library walls.")
    try:
        return jf.JellyfinMapper(db, caller.user, query.csv("fields")).alphabet(query)
    except LookupError:
        return {"anchors": [], "total": 0}
