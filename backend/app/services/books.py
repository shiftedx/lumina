"""Standalone, format-aware Books catalog. It deliberately has no LibraryItem dependency."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.book_models import BookItem, BookShelf, BookShelfMembership, BookState
from app.config import settings
from app.models import User, utcnow
from app.services import member_access

BOOK_EXTENSIONS = {".epub": "epub", ".cbz": "cbz"}
MAX_SHELF_ENTRIES = 500
_STORE_KEY = re.compile(r"^[0-9a-f-]{36}\.(?:epub|cbz)$")


class BookError(ValueError):
    pass


class BookNotFound(BookError):
    pass


class BookForbidden(BookError):
    pass


def book_root() -> Path:
    return settings.data_dir / "books"


def stored_path(storage_key: str) -> Path:
    if not _STORE_KEY.fullmatch(storage_key):
        raise BookError("Invalid book file.")
    root = book_root().resolve()
    path = (root / storage_key).resolve(strict=False)
    if not path.is_relative_to(root):
        raise BookError("Invalid book file.")
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise BookError("Book file is unavailable.") from exc
    if not os.path.isfile(path) or os.path.islink(path):
        raise BookError("Book file is unavailable.")
    return path


def _clean(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:limit]
    return value or None


def _genres(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(clean for raw in value if (clean := _clean(raw, 80))))[:30]


def series_id(name: str, author: str | None) -> str:
    key = f"{name.casefold()}\0{(author or '').casefold()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lumina:book-series:{key}"))


class BookService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def visible_predicate(self, user: User):
        shared = BookItem.visibility == "shared"
        # Restricted members must be explicitly granted Books. Unrestricted members retain household visibility.
        access = member_access.for_user(self.db, user)
        if access is not None and access.sections is not None and "books" not in access.sections:
            shared = False
        visible = or_(shared, BookItem.owner_user_id == user.id)
        return or_(visible, BookItem.owner_user_id.is_(None)) if user.role == "admin" else visible

    def get(self, book_id: str, user: User) -> BookItem:
        book = self.db.scalar(select(BookItem).where(BookItem.id == book_id, self.visible_predicate(user)))
        if book is None:
            raise BookNotFound("Book not found")
        return book

    def can_edit(self, book: BookItem, user: User) -> bool:
        return book.owner_user_id == user.id

    def inspect_staged(self, path: Path, filename: str) -> tuple[str, dict[str, Any]]:
        extension = Path(filename).suffix.lower()
        format_ = BOOK_EXTENSIONS.get(extension)
        if format_ is None:
            raise BookError("Books must be EPUB or CBZ files.")
        from app.services.book_archives import inspect_book
        try:
            manifest = inspect_book(path, format_, filename=filename)
        except ValueError as exc:
            raise BookError(str(exc)) from exc
        return format_, manifest

    def import_staged(self, path: Path, filename: str, user: User, *, format_: str, manifest: dict[str, Any]) -> BookItem:
        title = _clean(manifest.get("title")) or _clean(Path(filename).stem) or "Untitled"
        author = _clean(manifest.get("author"))
        found_series = _clean(manifest.get("series_name"))
        book_id = str(uuid.uuid4())
        # Every CBZ is a Manga series, even a plain image archive without ComicInfo.
        # A standalone volume uses its durable book UUID until its owner assigns a name.
        sid = series_id(found_series, author) if found_series else (book_id if format_ == "cbz" else None)
        key = f"{uuid.uuid4()}.{format_}"
        target = book_root() / key
        book_root().mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, target)
            book = BookItem(
                id=book_id, storage_key=key, format=format_, kind="manga" if format_ == "cbz" else "ebook",
                title=title, author=author, description=_clean(manifest.get("description"), 10_000),
                language=_clean(manifest.get("language"), 32), genres=_genres(manifest.get("genres")),
                series_id=sid, series_name=found_series,
                volume_number=self._volume(manifest.get("volume_number")),
                reading_direction="rtl" if manifest.get("reading_direction") == "rtl" else "ltr",
                manifest=manifest, visibility="private", owner_user_id=user.id,
            )
            self.db.add(book)
            self.db.flush()
            return book
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @staticmethod
    def _volume(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    def state_for(self, user: User, ids: list[str]) -> dict[str, BookState]:
        if not ids:
            return {}
        return {row.book_id: row for row in self.db.scalars(select(BookState).where(BookState.user_id == user.id, BookState.book_id.in_(ids)))}

    def shelf_ids_for(self, user: User, ids: list[str]) -> dict[str, list[str]]:
        if not ids:
            return {}
        rows = self.db.execute(
            select(BookShelfMembership.book_id, BookShelfMembership.shelf_id)
            .join(BookShelf, BookShelf.id == BookShelfMembership.shelf_id)
            .where(BookShelfMembership.book_id.in_(ids), or_(BookShelf.owner_user_id == user.id, BookShelf.visibility == "shared"))
            .order_by(BookShelfMembership.position)
        )
        result: dict[str, list[str]] = {}
        for book_id, shelf_id in rows:
            result.setdefault(book_id, []).append(shelf_id)
        return result

    def serialize(self, book: BookItem, user: User, *, state: BookState | None = None, shelf_ids: list[str] | None = None) -> dict[str, Any]:
        state = state if state is not None else self.state_for(user, [book.id]).get(book.id)
        manifest = book.manifest if isinstance(book.manifest, dict) else {}
        units = len(manifest.get("pages") or []) if book.format == "cbz" else len(manifest.get("chapters") or [])
        percent = 0.0
        if state is not None and units:
            percent = 100.0 if state.completed else min(100.0, max(0.0, ((state.unit_index + state.offset) / units) * 100))
        return {
            "id": book.id, "kind": book.kind, "format": book.format, "title": book.title, "author": book.author or "",
            "description": book.description or "", "language": book.language or "", "genres": book.genres or [],
            "series_id": book.series_id, "series_name": book.series_name, "volume_number": book.volume_number,
            "reading_direction": book.reading_direction, "cover_url": f"/api/books/{book.id}/cover" if manifest.get("cover_member") else None,
            "chapter_count": len(manifest.get("chapters") or []), "page_count": len(manifest.get("pages") or []),
            "created_at": book.created_at, "visibility": book.visibility, "can_edit": self.can_edit(book, user),
            "progress": {"unit_index": state.unit_index if state else 0, "offset": state.offset if state else 0,
                         "percent": percent, "completed": state.completed if state else False,
                         "updated_at": state.last_read_at if state else None},
            "favorite": state.favorite if state else False, "reading_list": state.reading_list if state else False,
            "shelf_ids": shelf_ids if shelf_ids is not None else self.shelf_ids_for(user, [book.id]).get(book.id, []),
            "available": book.available and self._available(book),
        }

    @staticmethod
    def _available(book: BookItem) -> bool:
        try:
            stored_path(book.storage_key)
        except BookError:
            return False
        return True

    def serialize_many(self, books: list[BookItem], user: User) -> list[dict[str, Any]]:
        states, shelves = self.state_for(user, [book.id for book in books]), self.shelf_ids_for(user, [book.id for book in books])
        return [self.serialize(book, user, state=states.get(book.id), shelf_ids=shelves.get(book.id, [])) for book in books]

    def list_books(self, user: User, *, kind: str | None, q: str | None, sort: str, status: str, shelf_id: str | None, offset: int, limit: int) -> tuple[list[BookItem], int, str | None]:
        query = select(BookItem).where(self.visible_predicate(user))
        if kind in {"ebook", "manga"}:
            query = query.where(BookItem.kind == kind)
        if q:
            needle = f"%{q.strip()[:200]}%"
            query = query.where(or_(BookItem.title.ilike(needle), BookItem.author.ilike(needle), BookItem.series_name.ilike(needle)))
        state = BookState
        if status != "all":
            query = query.outerjoin(state, and_(state.book_id == BookItem.id, state.user_id == user.id))
            if status == "unread": query = query.where(or_(state.id.is_(None), and_(state.completed.is_(False), state.last_read_at.is_(None))))
            elif status == "reading": query = query.where(state.completed.is_(False), state.last_read_at.is_not(None))
            elif status == "read": query = query.where(state.completed.is_(True))
            elif status == "favorites": query = query.where(state.favorite.is_(True))
            elif status == "reading_list": query = query.where(state.reading_list.is_(True))
        if shelf_id:
            query = query.join(BookShelfMembership, BookShelfMembership.book_id == BookItem.id).join(BookShelf, BookShelf.id == BookShelfMembership.shelf_id).where(BookShelf.id == shelf_id, or_(BookShelf.owner_user_id == user.id, BookShelf.visibility == "shared"))
        order = ((BookShelfMembership.position, BookItem.id) if sort == "custom" and shelf_id else
                 (BookItem.title.collate("NOCASE"), BookItem.id) if sort == "title" else
                 (func.coalesce(BookItem.author, "").collate("NOCASE"), BookItem.title.collate("NOCASE"), BookItem.id) if sort == "author" else
                 (BookItem.created_at.desc(), BookItem.id))
        base = query.order_by(*order)
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        books = list(self.db.scalars(base.offset(offset).limit(limit + 1)))
        next_cursor = str(offset + limit) if len(books) > limit else None
        return books[:limit], total, next_cursor

    def update_state(self, book: BookItem, user: User, values: dict[str, Any]) -> BookState:
        state = self.state_for(user, [book.id]).get(book.id)
        if state is None:
            state = BookState(id=str(uuid.uuid4()), user_id=user.id, book_id=book.id, unit_index=0, offset=0, completed=False, favorite=False, reading_list=False)
            self.db.add(state)
        reset_checkpoint = values.get("completed") is False and values.get("unit_index") == 0 and values.get("offset") == 0
        read = not reset_checkpoint and (any(key in values and values[key] is not None for key in ("unit_index", "offset")) or values.get("completed") is True)
        for key in ("favorite", "reading_list", "completed", "unit_index", "offset"):
            if key in values and values[key] is not None:
                setattr(state, key, values[key])
        units = len((book.manifest or {}).get("pages") or []) if book.format == "cbz" else len((book.manifest or {}).get("chapters") or [])
        if state.unit_index >= max(1, units):
            raise BookError("Reading position is outside this book.")
        if reset_checkpoint:
            state.last_read_at = None
        elif read:
            state.last_read_at = utcnow()
        self.db.flush()
        return state

    def update_book(self, book: BookItem, user: User, values: dict[str, Any]) -> BookItem:
        if not self.can_edit(book, user):
            raise BookForbidden("Only the book owner may edit it.")
        if "title" in values:
            title = _clean(values["title"])
            if title is None:
                raise BookError("Book title is required.")
            book.title = title
        for field in ("author", "reading_direction", "visibility", "genres"):
            if field in values:
                setattr(book, field, _genres(values[field]) if field == "genres" else (_clean(values[field]) if field == "author" else values[field]))
        if "series_name" in values:
            book.series_name = _clean(values["series_name"])
        if "series_name" in values or "author" in values:
            book.series_id = series_id(book.series_name, book.author) if book.series_name else (book.id if book.kind == "manga" else None)
        if "volume_number" in values:
            book.volume_number = values["volume_number"]
        self.db.flush()
        return book

    def reader(self, book: BookItem) -> dict[str, Any]:
        manifest = book.manifest or {}
        return {"book": book, "chapters": [{"label": _clean(row.get("label")) or f"Chapter {index + 1}"} for index, row in enumerate(manifest.get("chapters") or []) if isinstance(row, dict)], "page_count": len(manifest.get("pages") or []), "reading_direction": book.reading_direction}

    def chapter_nodes(self, book: BookItem, index: int) -> list[dict]:
        if book.format != "epub": raise BookNotFound("Chapter not found")
        from app.services.book_archives import chapter_nodes
        return chapter_nodes(stored_path(book.storage_key), book.manifest or {}, index)

    def resource(self, book: BookItem, member: str) -> tuple[bytes, str]:
        if not isinstance(member, str) or len(member) > 1024: raise BookNotFound("Resource not found")
        from app.services.book_archives import read_resource
        return read_resource(stored_path(book.storage_key), member)

    def shelves(self, user: User) -> list[dict[str, Any]]:
        shelves = list(self.db.scalars(select(BookShelf).where(or_(BookShelf.owner_user_id == user.id, BookShelf.visibility == "shared")).order_by(BookShelf.created_at, BookShelf.id)))
        result = []
        for shelf in shelves:
            entries = list(self.db.scalars(select(BookShelfMembership).join(BookItem, BookItem.id == BookShelfMembership.book_id).where(BookShelfMembership.shelf_id == shelf.id, self.visible_predicate(user)).order_by(BookShelfMembership.position)))
            if shelf.owner_user_id != user.id and not entries:
                continue
            books = {book.id: book for book in self.db.scalars(select(BookItem).where(BookItem.id.in_([entry.book_id for entry in entries])))} if entries else {}
            result.append({"id": shelf.id, "name": shelf.name, "description": shelf.description or "", "visibility": shelf.visibility, "can_edit": shelf.owner_user_id == user.id, "book_ids": [entry.book_id for entry in entries], "count": len(entries), "covers": [f"/api/books/{entry.book_id}/cover" for entry in entries if (books.get(entry.book_id).manifest or {}).get("cover_member")][:4]})
        return result

    def create_shelf(self, user: User, values: dict[str, Any]) -> BookShelf:
        name = " ".join(values["name"].split())
        if not name: raise BookError("Shelf name is required.")
        shelf = BookShelf(id=str(uuid.uuid4()), owner_user_id=user.id, name=name, name_key=name.casefold(), description=values.get("description"), visibility=values.get("visibility", "private"))
        self.db.add(shelf); self.db.flush(); return shelf

    def edit_shelf(self, shelf_id: str, user: User, values: dict[str, Any]) -> BookShelf:
        shelf = self._owned_shelf(shelf_id, user)
        if values.get("name") is not None:
            shelf.name = " ".join(values["name"].split()); shelf.name_key = shelf.name.casefold()
        for key in ("description", "visibility"):
            if key in values: setattr(shelf, key, values[key])
        self.db.flush(); return shelf

    def replace_shelf_entries(self, shelf_id: str, user: User, book_ids: list[str]) -> BookShelf:
        shelf = self._owned_shelf(shelf_id, user)
        if len(book_ids) > MAX_SHELF_ENTRIES or len(set(book_ids)) != len(book_ids): raise BookError("Shelf entries are invalid.")
        visible = {book.id for book in self.db.scalars(select(BookItem).where(BookItem.id.in_(book_ids), self.visible_predicate(user)))}
        if visible != set(book_ids): raise BookNotFound("Book not found")
        for entry in self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == shelf.id)): self.db.delete(entry)
        self.db.add_all(BookShelfMembership(id=str(uuid.uuid4()), shelf_id=shelf.id, book_id=book_id, position=index) for index, book_id in enumerate(book_ids))
        self.db.flush(); return shelf

    def delete_shelf(self, shelf_id: str, user: User) -> None:
        shelf = self._owned_shelf(shelf_id, user)
        for entry in self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == shelf.id)): self.db.delete(entry)
        self.db.delete(shelf); self.db.flush()

    def _owned_shelf(self, shelf_id: str, user: User) -> BookShelf:
        shelf = self.db.get(BookShelf, shelf_id)
        if shelf is None or shelf.owner_user_id != user.id: raise BookNotFound("Shelf not found")
        return shelf

    def series(self, user: User, query: str | None = None, status: str = "all") -> list[dict[str, Any]]:
        books = list(self.db.scalars(select(BookItem).where(self.visible_predicate(user), BookItem.kind == "manga", BookItem.series_id.is_not(None)).order_by(BookItem.series_name.collate("NOCASE"), BookItem.volume_number.nulls_last(), BookItem.title)))
        groups: dict[str, list[BookItem]] = {}
        for book in books:
            groups.setdefault(book.series_id or "", []).append(book)
        states = self.state_for(user, [book.id for book in books])
        result=[]
        for sid, entries in groups.items():
            if query:
                needle = query.casefold()
                if not any(needle in field.casefold() for book in entries for field in (book.title, book.author or "", book.series_name or "")):
                    continue
            first = entries[0]; read = sum(bool(states.get(book.id) and states[book.id].completed) for book in entries)
            entry_states = [states.get(book.id) for book in entries]
            unread = all(state is None or (not state.completed and state.last_read_at is None) for state in entry_states)
            reading = any(state is not None and not state.completed and state.last_read_at is not None for state in entry_states)
            favorite = any(state is not None and state.favorite for state in entry_states)
            reading_list = any(state is not None and state.reading_list for state in entry_states)
            if status == "read" and read != len(entries):
                continue
            if status == "unread" and not unread:
                continue
            if status == "reading" and not reading:
                continue
            if status == "favorites" and not favorite:
                continue
            if status == "reading_list" and not reading_list:
                continue
            next_book = next((book.id for book in entries if (state := states.get(book.id)) is not None and not state.completed and state.last_read_at is not None), None)
            if next_book is None:
                next_book = next((book.id for book in entries if not (states.get(book.id) and states[book.id].completed)), None)
            result.append({"id": sid, "title": first.series_name or first.title, "author": first.author or "", "description": first.description or "", "cover_url": f"/api/books/{first.id}/cover" if (first.manifest or {}).get("cover_member") else None, "volume_count": len(entries), "read_volume_count": read, "next_book_id": next_book, "created_at": max(book.created_at for book in entries)})
        return result

    def overview(self, user: User) -> dict[str, Any]:
        visible = self.visible_predicate(user)
        ebooks = self.db.scalar(select(func.count()).select_from(BookItem).where(visible, BookItem.kind == "ebook")) or 0
        volumes = self.db.scalar(select(func.count()).select_from(BookItem).where(visible, BookItem.kind == "manga")) or 0
        manga_series = self.db.scalar(select(func.count(func.distinct(BookItem.series_id))).where(visible, BookItem.kind == "manga", BookItem.series_id.is_not(None))) or 0
        recent = list(self.db.scalars(select(BookItem).where(visible).order_by(BookItem.created_at.desc(), BookItem.id).limit(12)))
        reading = list(self.db.scalars(
            select(BookItem).join(BookState, and_(BookState.book_id == BookItem.id, BookState.user_id == user.id))
            .where(visible, BookState.last_read_at.is_not(None), BookState.completed.is_(False))
            .order_by(BookState.last_read_at.desc(), BookItem.id).limit(12)
        ))
        favorites = list(self.db.scalars(
            select(BookItem).join(BookState, and_(BookState.book_id == BookItem.id, BookState.user_id == user.id))
            .where(visible, BookState.favorite.is_(True)).limit(100)
        ))
        favorite_authors = {book.author.casefold() for book in favorites if book.author}
        favorite_genres = {genre.casefold() for book in favorites for genre in (book.genres or []) if isinstance(genre, str)}
        state = BookState
        unread = or_(state.id.is_(None), and_(state.completed.is_(False), state.last_read_at.is_(None)))
        candidates = list(self.db.scalars(
            select(BookItem).outerjoin(state, and_(state.book_id == BookItem.id, state.user_id == user.id))
            .where(visible, unread).order_by(BookItem.created_at.desc(), BookItem.id).limit(96)
        ))
        def affinity(book: BookItem) -> int:
            return int(bool(book.author and book.author.casefold() in favorite_authors)) + int(any(isinstance(genre, str) and genre.casefold() in favorite_genres for genre in (book.genres or [])))
        discover = sorted(candidates, key=lambda book: (-affinity(book), -book.created_at.timestamp(), book.id))[:12]
        return {"totals": {"ebooks": ebooks, "manga_series": manga_series, "volumes": volumes}, "continue_reading": self.serialize_many(reading, user), "recently_added": self.serialize_many(recent, user), "shelves": self.shelves(user), "discover": self.serialize_many(discover, user)}
