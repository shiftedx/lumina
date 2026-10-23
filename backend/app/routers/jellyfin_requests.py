"""Lumina Requests extensions authenticated by the opt-in Jellyfin Connected app.

This namespace deliberately mirrors the member Requests API without making a
Connected-app credential usable on browser ``/api`` routes.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import requests as member_requests
from app.routers import requests_catalog
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller

router = APIRouter(prefix="/jellyfin/lumina/requests")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]
Kind = Literal["movie", "show", "anime"]
Page = Annotated[int, Query(ge=1, le=500)]
RequestPage = Annotated[int, Query(ge=1, le=10_000)]


def _error(status: int, detail: object) -> JSONResponse:
    """This Lumina extension is JSON by contract; ordinary Jellyfin routes stay empty-bodied."""
    return JSONResponse(status_code=status, content={"detail": detail})


def _catalog(db: Session, caller: JellyfinCaller, build):  # noqa: ANN001, ANN202
    try:
        return requests_catalog._serve(db, caller.user, build)
    except HTTPException as error:
        return _error(error.status_code, error.detail)


# Catalog ----------------------------------------------------------------------------------------------------------

@router.get("/catalog/framing")
def framing(caller: Caller, db: Db, src: str = Query(min_length=1, max_length=512)):  # noqa: ANN201
    from app.routers import hero_framing

    return hero_framing.framing(src, caller.user, db)


@router.get("/catalog/home")
def home(caller: Caller, db: Db):  # noqa: ANN201
    return _catalog(db, caller, requests_catalog.catalog.home)


@router.get("/catalog/list/{kind}")
def list_items(
    kind: Kind, section: str | None = Query(default=None, max_length=32), genre: str | None = Query(default=None, max_length=64),
    page: Page = 1, caller: Caller = None, db: Db = None,  # type: ignore[assignment]
):  # noqa: ANN201
    return _catalog(db, caller, lambda client: requests_catalog.catalog.list_items(client, kind, section, genre, page))


@router.get("/catalog/anime/season")
def anime_season(
    season: str | None = Query(default=None, max_length=8), year: int | None = Query(default=None, ge=1940, le=2100),
    sort: str = Query(default="popularity", max_length=16), page: Page = 1, caller: Caller = None, db: Db = None,  # type: ignore[assignment]
):  # noqa: ANN201
    return _catalog(db, caller, lambda client: requests_catalog.catalog.anime_season(client, season, year, sort, page))


@router.get("/catalog/anime/schedule")
def anime_schedule(caller: Caller, db: Db):  # noqa: ANN201
    return _catalog(db, caller, requests_catalog.catalog.anime_schedule)


@router.get("/catalog/search")
def search(q: str = Query(default="", max_length=200), page: Page = 1, caller: Caller = None, db: Db = None):  # type: ignore[assignment]
    return _catalog(db, caller, lambda client: requests_catalog.catalog.search(client, q, page))


@router.get("/catalog/genres/{kind}")
def genres(kind: Kind, caller: Caller, db: Db):  # noqa: ANN201
    return _catalog(db, caller, lambda client: requests_catalog.catalog.genres(client, kind))


@router.get("/catalog/title/{kind}/{item_id}")
def title(kind: Kind, item_id: Annotated[int, Path(ge=1)], caller: Caller, db: Db):  # noqa: ANN201
    return _catalog(db, caller, lambda client: requests_catalog.catalog.detail(client, kind, item_id))


# Member requests --------------------------------------------------------------------------------------------------

@router.get("/quota")
def quota(caller: Caller, db: Db) -> dict:
    return member_requests.get_quota(caller.user, db)


@router.get("")
@router.get("/")
def mine(
    status: str | None = Query(default=None, max_length=24), page: RequestPage = 1, caller: Caller = None, db: Db = None,  # type: ignore[assignment]
) -> dict:
    return member_requests.list_requests("mine", status, page, caller.user, db)


@router.post("")
@router.post("/")
def create(body: member_requests.CreateBody, request: Request, caller: Caller, db: Db):  # noqa: ANN201
    # ``create_request`` carries the member rate limit and preserves its structured quota/policy refusal envelope.
    return member_requests.create_request(body, request, caller.user, db)
