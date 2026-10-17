"""Authenticated REST boundary for the standalone Books surface."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from app.book_schemas import (
    BookCombine, BookOverview, BookPatch, BookResponse, BookSeriesResponse, BookShelfResponse, BookStatePatch,
    BundleMatchesResponse,
    BooksPage, SeriesPage, ShelfCreate, ShelfEntries, ShelfPatch,
)
from app.db import get_db
from app.http_boundaries import BOOK_UPLOAD_TOO_LARGE_MESSAGE
from app.models import User
from app.persistence import write_transaction
from app.security import get_current_user
from app.services import member_access
from app.services.books import BookError, BookForbidden, BookNotFound, BookService, book_root

URL = "/api/books"


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, BookNotFound): return HTTPException(status_code=404, detail="Book not found")
    if isinstance(exc, BookForbidden): return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _cursor(value: str | None) -> int:
    if value is None: return 0
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    if parsed < 0 or str(parsed) != value: raise HTTPException(status_code=422, detail="Invalid cursor")
    return parsed


async def import_book(filename: str, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    if not filename or len(filename) > 255:
        raise HTTPException(status_code=422, detail="A book filename is required.")
    access = member_access.for_user(db, current_user)
    if access is not None and not access.can_download:
        raise HTTPException(status_code=403, detail="downloads_not_allowed")
    root = book_root(); root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=root)
    staged = Path(temporary)
    new_target: Path | None = None
    try:
        with os.fdopen(fd, "wb") as handle:
            try:
                async for block in request.stream():
                    handle.write(block)
            except ClientDisconnect as exc:
                if request.scope.get("state", {}).get("body_too_large"):
                    raise HTTPException(status_code=413, detail=BOOK_UPLOAD_TOO_LARGE_MESSAGE) from exc
                if request.scope.get("state", {}).get("body_timeout"):
                    raise HTTPException(status_code=408, detail="Book upload timed out.") from exc
                raise HTTPException(status_code=400, detail="Book upload was interrupted.") from exc
            handle.flush(); os.fsync(handle.fileno())
        try:
            service = BookService(db)
            format_, manifest = service.inspect_staged(staged, filename)
            with write_transaction(db, name="book_import"):
                book = service.import_staged(staged, filename, current_user, format_=format_, manifest=manifest)
                # ``book`` may now be a child of an existing canonical root. Its storage key is
                # still the newly moved upload; never infer cleanup from the canonical row.
                new_target = book_root() / book.storage_key
                payload = service.serialize(book, current_user)
        except (BookError, IntegrityError) as exc:
            if new_target is not None:
                new_target.unlink(missing_ok=True)
            raise _error(exc) from exc
        except Exception:
            if new_target is not None:
                new_target.unlink(missing_ok=True)
            raise
        return payload
    finally:
        staged.unlink(missing_ok=True)


def overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return BookService(db).overview(current_user)


def list_books(kind: Literal["ebook", "manga"] | None = None, q: str | None = Query(default=None, max_length=200), sort: Literal["added", "title", "author", "custom"] = "added", status: Literal["all", "unread", "reading", "read", "favorites", "reading_list"] = "all", shelf_id: str | None = None, cursor: str | None = None, limit: int = Query(default=40, ge=1, le=100), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db); books, total, next_cursor = service.list_books(current_user, kind=kind, q=q, sort=sort, status=status, shelf_id=shelf_id, offset=_cursor(cursor), limit=limit)
    return {"items": service.serialize_many(books, current_user), "total": total, "next_cursor": next_cursor}


def list_series(q: str | None = Query(default=None, max_length=200), status: Literal["all", "unread", "reading", "read", "favorites", "reading_list"] = "all", sort: Literal["added", "title", "author"] = "title", cursor: str | None = None, limit: int = Query(default=40, ge=1, le=100), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    items = BookService(db).series(current_user, q, status)
    if sort == "title": items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    elif sort == "author": items.sort(key=lambda item: (item["author"].casefold(), item["title"].casefold(), item["id"]))
    else: items.sort(key=lambda item: (-item["created_at"].timestamp(), item["id"]))
    offset = _cursor(cursor); page = items[offset:offset + limit + 1]
    return {"items": page[:limit], "total": len(items), "next_cursor": str(offset + limit) if len(page) > limit else None}


def get_series(series_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db); series = next((item for item in service.series(current_user) if item["id"] == series_id), None)
    if series is None: raise HTTPException(status_code=404, detail="Series not found")
    from app.book_models import BookItem
    books = list(db.scalars(select(BookItem).where(BookItem.series_id == series_id, BookItem.kind == "manga", service.catalog_predicate(current_user)).order_by(BookItem.volume_number.nulls_last(), BookItem.title.collate("NOCASE"), BookItem.id)))
    return {"series": series, "books": service.serialize_many(books, current_user)}


def get_book(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try: return service.serialize(service.get(book_id, current_user), current_user)
    except BookError as exc: raise _error(exc) from exc


def reader(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        book = service.get(book_id, current_user); data = service.reader(book); data["book"] = service.serialize(book, current_user, for_reader=True); return data
    except BookError as exc: raise _error(exc) from exc


def get_chapter(book_id: str, index: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try: return {"nodes": service.chapter_nodes(service.get(book_id, current_user), index)}
    except (BookError, IndexError, ValueError) as exc: raise _error(exc) from exc


def get_page(book_id: str, index: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    service = BookService(db)
    try:
        book = service.get(book_id, current_user); pages = (book.manifest or {}).get("pages") or []
        if index < 0 or index >= len(pages): raise BookNotFound("Page not found")
        return _resource(service, book, pages[index])
    except (BookError, IndexError, ValueError, FileNotFoundError) as exc: raise _error(exc) from exc


def get_resource(book_id: str, member: str = Query(max_length=1024), current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    service = BookService(db)
    try: return _resource(service, service.get(book_id, current_user), member)
    except (BookError, FileNotFoundError) as exc: raise _error(exc) from exc


def get_cover(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    service = BookService(db)
    try:
        book = service.get(book_id, current_user); member = (book.manifest or {}).get("cover_member")
        if not isinstance(member, str): raise BookNotFound("Cover not found")
        return _resource(service, book, member)
    except (BookError, FileNotFoundError) as exc: raise _error(exc) from exc


def _resource(service: BookService, book, member: str) -> Response:  # noqa: ANN001
    body, mime = service.resource(book, member)
    return Response(content=body, media_type=mime, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


def patch_state(book_id: str, payload: BookStatePatch, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_state"):
            book = service.get(book_id, current_user); state = service.update_state(book, current_user, payload.model_dump(exclude_none=True)); return service.serialize(book, current_user, state=state, for_reader=payload.unit_index is not None or payload.offset is not None)
    except BookError as exc: raise _error(exc) from exc


def patch_book(book_id: str, payload: BookPatch, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_edit"):
            book = service.update_book(service.get(book_id, current_user), current_user, payload.model_dump(exclude_unset=True)); return service.serialize(book, current_user)
    except BookError as exc: raise _error(exc) from exc


def list_shelves(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[dict]: return BookService(db).shelves(current_user)

def create_shelf(payload: ShelfCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_shelf_create"): shelf = service.create_shelf(current_user, payload.model_dump())
        return next(item for item in service.shelves(current_user) if item["id"] == shelf.id)
    except (BookError, IntegrityError) as exc: raise _error(exc) from exc

def patch_shelf(shelf_id: str, payload: ShelfPatch, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_shelf_edit"): shelf = service.edit_shelf(shelf_id, current_user, payload.model_dump(exclude_unset=True))
        return next(item for item in service.shelves(current_user) if item["id"] == shelf.id)
    except BookError as exc: raise _error(exc) from exc

def put_shelf_entries(shelf_id: str, payload: ShelfEntries, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_shelf_entries"): shelf = service.replace_shelf_entries(shelf_id, current_user, payload.book_ids)
        return next(item for item in service.shelves(current_user) if item["id"] == shelf.id)
    except BookError as exc: raise _error(exc) from exc

def delete_shelf(shelf_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    try:
        with write_transaction(db, name="book_shelf_delete"): BookService(db).delete_shelf(shelf_id, current_user)
    except BookError as exc: raise _error(exc) from exc
    return Response(status_code=204)


def combine_books(payload: BookCombine, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_combine"):
            book = service.combine(current_user, payload.primary_book_id, payload.book_ids)
            return service.serialize(book, current_user)
    except BookError as exc:
        raise _error(exc) from exc


def bundle_matches(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, int]:
    try:
        with write_transaction(db, name="book_bundle_matches"):
            return BookService(db).bundle_matches(current_user)
    except BookError as exc:
        raise _error(exc) from exc


def delete_book(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    try:
        with write_transaction(db, name="book_trash"):
            BookService(db).trash(book_id, current_user)
    except BookError as exc:
        raise _error(exc) from exc
    return Response(status_code=204)


def list_trash(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[dict]:
    service = BookService(db)
    return [service.serialize(book, current_user, include_deleted_files=True) for book in service.trash_list(current_user)]


def separate_book(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_separate"):
            return service.serialize(service.separate(book_id, current_user), current_user)
    except BookError as exc:
        raise _error(exc) from exc


def restore_book(book_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    service = BookService(db)
    try:
        with write_transaction(db, name="book_restore"):
            book = service.restore(book_id, current_user)
            return service.serialize(book, current_user)
    except BookError as exc:
        raise _error(exc) from exc


def register(app: FastAPI) -> None:
    app.put(URL + "/import", response_model=BookResponse, status_code=201)(import_book)
    app.get(URL + "/overview", response_model=BookOverview)(overview)
    app.get(URL, response_model=BooksPage)(list_books)
    app.get(URL + "/series", response_model=SeriesPage)(list_series)
    app.get(URL + "/series/{series_id}")(get_series)
    # Static management routes must precede the asset-id route below.
    app.post(URL + "/combine", response_model=BookResponse)(combine_books)
    app.post(URL + "/bundle-matches", response_model=BundleMatchesResponse)(bundle_matches)
    app.get(URL + "/trash", response_model=list[BookResponse])(list_trash)
    app.get(URL + "/shelves", response_model=list[BookShelfResponse])(list_shelves)
    app.post(URL + "/shelves", response_model=BookShelfResponse, status_code=201)(create_shelf)
    app.patch(URL + "/shelves/{shelf_id}", response_model=BookShelfResponse)(patch_shelf)
    app.put(URL + "/shelves/{shelf_id}/entries", response_model=BookShelfResponse)(put_shelf_entries)
    app.delete(URL + "/shelves/{shelf_id}", status_code=204)(delete_shelf)
    app.post(URL + "/{book_id}/separate", response_model=BookResponse)(separate_book)
    app.post(URL + "/{book_id}/restore", response_model=BookResponse)(restore_book)
    app.get(URL + "/{book_id}/reader")(reader)
    app.get(URL + "/{book_id}/chapters/{index}")(get_chapter)
    app.get(URL + "/{book_id}/pages/{index}")(get_page)
    app.get(URL + "/{book_id}/resources")(get_resource)
    app.get(URL + "/{book_id}/cover")(get_cover)
    app.patch(URL + "/{book_id}/state", response_model=BookResponse)(patch_state)
    app.patch(URL + "/{book_id}", response_model=BookResponse)(patch_book)
    app.delete(URL + "/{book_id}", status_code=204)(delete_book)
    app.get(URL + "/{book_id}", response_model=BookResponse)(get_book)
