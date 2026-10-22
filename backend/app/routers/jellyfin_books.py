"""Native Books uses the opt-in Connected-app credential and the existing Books visibility rules."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.book_schemas import BookStatePatch
from app.db import get_db
from app.routers import books
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller

router = APIRouter(prefix="/jellyfin/lumina/books")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


@router.get("")
def catalog(caller: Caller, db: Db, q: str | None = Query(None, max_length=200), cursor: str | None = None,
            kind: Literal["ebook", "manga"] | None = None, shelf_id: str | None = None,
            limit: int = Query(40, ge=1, le=100)) -> dict:
    return books.list_books(kind=kind, q=q, cursor=cursor, limit=limit, shelf_id=shelf_id,
                            sort="custom" if shelf_id else "added", current_user=caller.user, db=db)


@router.get("/overview")
def overview(caller: Caller, db: Db) -> dict:
    return books.overview(caller.user, db)


@router.get("/series")
def series(caller: Caller, db: Db, cursor: str | None = None, limit: int = Query(40, ge=1, le=100)) -> dict:
    return books.list_series(q=None, cursor=cursor, limit=limit, current_user=caller.user, db=db)


@router.get("/series/{series_id}")
def series_detail(series_id: str, caller: Caller, db: Db) -> dict:
    return books.get_series(series_id, caller.user, db)


@router.get("/{book_id}/reader")
def reader(book_id: str, caller: Caller, db: Db) -> dict:
    return books.reader(book_id, caller.user, db)


@router.get("/{book_id}/chapters/{index}")
def chapter(book_id: str, index: int, caller: Caller, db: Db) -> dict:
    return books.get_chapter(book_id, index, caller.user, db)


@router.get("/{book_id}/pages/{index}")
def page(book_id: str, index: int, caller: Caller, db: Db):  # noqa: ANN201
    return books.get_page(book_id, index, caller.user, db)


@router.get("/{book_id}/resources")
def resource(book_id: str, caller: Caller, db: Db, member: str = Query(max_length=1024)):  # noqa: ANN201
    return books.get_resource(book_id, member, caller.user, db)


@router.get("/{book_id}/cover")
def cover(book_id: str, caller: Caller, db: Db):  # noqa: ANN201
    return books.get_cover(book_id, caller.user, db)


@router.patch("/{book_id}/state")
def state(book_id: str, payload: BookStatePatch, caller: Caller, db: Db) -> dict:
    return books.patch_state(book_id, payload, caller.user, db)
