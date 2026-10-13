"""Standalone, format-aware Books catalog. It deliberately has no LibraryItem dependency."""
from __future__ import annotations

import os
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.book_models import BookItem, BookShelf, BookShelfMembership, BookState
from app.config import settings
from app.models import User, utcnow
from app.services import member_access
from app.services.book_archives import clean_synopsis

BOOK_EXTENSIONS = {".epub": "epub", ".cbz": "cbz", ".cbr": "cbr", ".pdf": "pdf"}
MAX_SHELF_ENTRIES = 500
_STORE_KEY = re.compile(r"^[0-9a-f-]{36}\.(?:epub|cbz|cbr|pdf)$")
_UNKNOWN_AUTHORS = {"unknown", "unknown author", "n a", "na", "none", "anonymous", "various", "various authors"}


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


def safe_filename(value: object, *, required: bool = True) -> str | None:
    """Return only a caller-supplied basename, accepting either slash convention."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise BookError("A valid book filename is required.")
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise BookError("A valid book filename is required.")
    if len(name) > 255:
        raise BookError("Book filenames must be at most 255 characters.")
    return name


def _filename_stem(filename: str) -> str:
    return PurePosixPath(filename).stem


def _normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in value).split())


def _normalized_language(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().casefold().replace("_", "-")


def _known_author(value: object) -> bool:
    return bool((key := _normalized(value)) and key not in _UNKNOWN_AUTHORS)


def _compatible_language(left: object, right: object) -> bool:
    left_key, right_key = _normalized_language(left), _normalized_language(right)
    if not left_key or not right_key:
        return True
    return left_key == right_key or left_key.split("-", 1)[0] == right_key.split("-", 1)[0]


def _fallback_filename(book: BookItem) -> str:
    stem = _clean(book.title, 240) or "Untitled"
    stem = re.sub(r"[\\/]+", "-", stem).strip(" .") or "Untitled"
    suffix = f".{book.format}"
    return f"{stem[:255 - len(suffix)]}{suffix}"


class BookService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def visible_predicate(self, user: User):
        shared = BookItem.visibility == "shared"
        # Legacy Books grants include both views; new grants can allow either view independently.
        access = member_access.for_user(self.db, user)
        if access is not None and access.sections is not None and "books" not in access.sections:
            kinds = [kind for kind in ("ebook", "manga") if f"books:{kind}" in access.sections]
            shared = and_(shared, BookItem.kind.in_(kinds))
        visible = or_(shared, BookItem.owner_user_id == user.id)
        if user.role == "admin":
            visible = or_(visible, BookItem.owner_user_id.is_(None))
        return and_(visible, BookItem.deleted_at.is_(None))

    def catalog_predicate(self, user: User):
        return and_(self.visible_predicate(user), BookItem.bundle_parent_id.is_(None))

    def get(self, book_id: str, user: User) -> BookItem:
        book = self.db.scalar(select(BookItem).where(BookItem.id == book_id, self.visible_predicate(user)))
        if book is None:
            raise BookNotFound("Book not found")
        if book.bundle_parent_id:
            parent = self.db.get(BookItem, book.bundle_parent_id)
            if parent is None or parent.deleted_at is not None:
                raise BookNotFound("Book not found")
        return book

    def _canonical(self, book: BookItem) -> BookItem:
        if not book.bundle_parent_id:
            return book
        parent = self.db.get(BookItem, book.bundle_parent_id)
        if parent is None:
            raise BookNotFound("Book not found")
        return parent

    def can_edit(self, book: BookItem, user: User) -> bool:
        return self._canonical(book).owner_user_id == user.id

    def _bundle_members(self, root: BookItem, *, include_deleted: bool = False) -> list[BookItem]:
        # The data model is deliberately flattened: every child points directly at the root.
        # Keeping the root first makes the response stable and gives old unbundled rows one natural file.
        query = select(BookItem).where(or_(BookItem.id == root.id, BookItem.bundle_parent_id == root.id))
        if not include_deleted:
            query = query.where(BookItem.deleted_at.is_(None))
        rows = list(self.db.scalars(query))
        rows.sort(key=lambda row: (0 if row.id == root.id else 1, row.created_at or datetime.min, row.id))
        return rows

    def inspect_staged(self, path: Path, filename: str) -> tuple[str, dict[str, Any]]:
        safe_name = safe_filename(filename)
        assert safe_name is not None
        extension = PurePosixPath(safe_name).suffix.lower()
        format_ = BOOK_EXTENSIONS.get(extension)
        if format_ is None:
            raise BookError("Books must be EPUB, PDF, CBZ, or CBR files.")
        from app.services.book_archives import inspect_book
        try:
            manifest = inspect_book(path, format_, filename=safe_name)
        except ValueError as exc:
            raise BookError(str(exc)) from exc
        return format_, manifest

    def import_staged(self, path: Path, filename: str, user: User, *, format_: str, manifest: dict[str, Any]) -> BookItem:
        original_filename = safe_filename(filename)
        assert original_filename is not None
        title = _clean(manifest.get("title")) or _clean(_filename_stem(original_filename)) or "Untitled"
        author = _clean(manifest.get("author"))
        found_series = _clean(manifest.get("series_name"))
        description = clean_synopsis(manifest.get("description"))
        stored_manifest = {**manifest, "description": description}
        book_id = str(uuid.uuid4())
        # Every CBZ/CBR is a Manga series, even a plain image archive without ComicInfo.
        # A standalone volume uses its durable book UUID until its owner assigns a name.
        sid = series_id(found_series, author) if found_series else (book_id if format_ in {"cbz", "cbr"} else None)
        key = f"{uuid.uuid4()}.{format_}"
        target = book_root() / key
        book_root().mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, target)
            book = BookItem(
                id=book_id, storage_key=key, format=format_, kind="manga" if format_ in {"cbz", "cbr"} else "ebook",
                filename=original_filename, title=title, author=author, description=description,
                language=_clean(manifest.get("language"), 32), genres=_genres(manifest.get("genres")),
                series_id=sid, series_name=found_series,
                volume_number=self._volume(manifest.get("volume_number")),
                reading_direction="rtl" if manifest.get("reading_direction") == "rtl" else "ltr",
                manifest=stored_manifest, visibility="private", owner_user_id=user.id,
            )
            self.db.add(book)
            self.db.flush()
            self._auto_bundle_import(book, user)
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

    def _states_for_members(self, user: User, members: list[BookItem]) -> dict[str, BookState]:
        return self.state_for(user, [member.id for member in members])

    def shelf_ids_for(self, user: User, ids: list[str]) -> dict[str, list[str]]:
        if not ids:
            return {}
        roots: set[str] = set()
        for book_id in ids:
            asset = self.db.get(BookItem, book_id)
            if asset is not None:
                roots.add(self._canonical(asset).id)
        if not roots:
            return {}
        rows = self.db.execute(
            select(BookShelfMembership.book_id, BookShelfMembership.shelf_id)
            .join(BookShelf, BookShelf.id == BookShelfMembership.shelf_id)
            .where(BookShelfMembership.book_id.in_(roots), or_(BookShelf.owner_user_id == user.id, BookShelf.visibility == "shared"))
            .order_by(BookShelfMembership.position, BookShelfMembership.id)
        )
        result: dict[str, list[str]] = {}
        for book_id, shelf_id in rows:
            result.setdefault(book_id, []).append(shelf_id)
        return result

    @staticmethod
    def _counts(book: BookItem) -> tuple[int, int]:
        manifest = book.manifest if isinstance(book.manifest, dict) else {}
        return len(manifest.get("chapters") or []), len(manifest.get("pages") or [])

    @staticmethod
    def _units(book: BookItem) -> int:
        chapters, pages = BookService._counts(book)
        return chapters if book.format == "epub" else pages

    def _logical_info(self, root: BookItem, user: User, *, members: list[BookItem] | None = None, states: dict[str, BookState] | None = None) -> dict[str, Any]:
        members = members if members is not None else self._bundle_members(root)
        states = states if states is not None else self._states_for_members(user, members)
        state_rows = [(member, states[member.id]) for member in members if member.id in states]
        checkpoints = [(member, state) for member, state in state_rows if state.last_read_at is not None]
        active = [(member, state) for member, state in checkpoints if not state.completed]
        available_members = [member for member in members if member.available and self._available(member)]
        available_ids = {member.id for member in available_members}
        selectable = [(member, state) for member, state in (active or checkpoints) if member.id in available_ids]
        selected_book: BookItem | None = None
        selected_state: BookState | None = None
        if selectable:
            selected_book, selected_state = max(selectable, key=lambda item: (item[1].last_read_at or datetime.min, item[0].id))
        reading_book = selected_book
        eligible = available_members or members
        if reading_book is None:
            reading_book = next((member for member in eligible if member.format == "epub"), None)
        if reading_book is None:
            reading_book = next((member for member in eligible if member.id == root.id), None) or (eligible[0] if eligible else root)
        return {
            "states": state_rows,
            "selected_book": selected_book,
            "selected_state": selected_state,
            "reading_file_id": reading_book.id,
            "completed": bool(not active and any(state.completed for _member, state in state_rows)),
            "favorite": any(state.favorite for _member, state in state_rows),
            "reading_list": any(state.reading_list for _member, state in state_rows),
            "has_checkpoint": bool(checkpoints),
            "reading": bool(active),
        }

    def _filename(self, book: BookItem) -> str:
        if book.filename:
            try:
                return safe_filename(book.filename) or _fallback_filename(book)
            except BookError:
                pass
        return _fallback_filename(book)

    def _file_response(self, book: BookItem) -> dict[str, Any]:
        chapters, pages = self._counts(book)
        return {
            "id": book.id, "filename": self._filename(book), "format": book.format,
            "chapter_count": chapters, "page_count": pages, "available": book.available and self._available(book),
        }

    def serialize(self, book: BookItem, user: User, *, state: BookState | None = None, shelf_ids: list[str] | None = None, include_deleted_files: bool = False, for_reader: bool = False, members: list[BookItem] | None = None, states: dict[str, BookState] | None = None) -> dict[str, Any]:
        canonical = self._canonical(book)
        members = members if members is not None else self._bundle_members(canonical, include_deleted=include_deleted_files)
        live_members = [member for member in members if member.deleted_at is None]
        state_members = live_members or members
        states = states if states is not None else self._states_for_members(user, state_members)
        logical = self._logical_info(canonical, user, members=state_members, states=states)
        requested_state = state if state is not None and state.book_id == book.id else states.get(book.id)
        if book.id == canonical.id and not for_reader:
            progress_state = logical["selected_state"]
            progress_book = logical["selected_book"] or canonical
            completed = logical["completed"]
            favorite = logical["favorite"]
            reading_list = logical["reading_list"]
        else:
            progress_state = requested_state
            progress_book = book
            completed = bool(requested_state and requested_state.completed)
            favorite = logical["favorite"]
            reading_list = logical["reading_list"]
        units = self._units(progress_book)
        percent = 0.0
        if progress_state is not None and units:
            percent = 100.0 if progress_state.completed else min(100.0, max(0.0, ((progress_state.unit_index + progress_state.offset) / units) * 100))
        shelves = shelf_ids if shelf_ids is not None else self.shelf_ids_for(user, [canonical.id]).get(canonical.id, [])
        manifest = book.manifest if isinstance(book.manifest, dict) else {}
        chapters, pages = self._counts(book)
        return {
            "id": book.id, "filename": self._filename(book), "canonical_book_id": canonical.id,
            "reading_file_id": book.id if for_reader else logical["reading_file_id"], "files": [self._file_response(member) for member in members if include_deleted_files or member.deleted_at is None],
            "kind": book.kind, "format": book.format, "title": canonical.title, "author": canonical.author or "",
            "description": clean_synopsis(canonical.description) or "", "language": canonical.language or "", "genres": canonical.genres or [],
            "series_id": canonical.series_id, "series_name": canonical.series_name, "volume_number": canonical.volume_number,
            "reading_direction": canonical.reading_direction, "cover_url": f"/api/books/{book.id}/cover" if manifest.get("cover_member") else None,
            "chapter_count": chapters, "page_count": pages,
            "created_at": canonical.created_at, "visibility": canonical.visibility, "can_edit": self.can_edit(canonical, user),
            "progress": {"unit_index": progress_state.unit_index if progress_state else 0, "offset": progress_state.offset if progress_state else 0,
                         "percent": percent, "completed": completed,
                         "updated_at": progress_state.last_read_at if progress_state else None},
            "favorite": favorite, "reading_list": reading_list, "shelf_ids": shelves,
            "available": any(member.available and self._available(member) for member in live_members) if book.id == canonical.id and not for_reader else book.available and self._available(book),
        }

    @staticmethod
    def _available(book: BookItem) -> bool:
        try:
            stored_path(book.storage_key)
        except BookError:
            return False
        return True

    def serialize_many(self, books: list[BookItem], user: User) -> list[dict[str, Any]]:
        roots = {self._canonical(book).id: self._canonical(book) for book in books}
        members, states = self._state_map_for_roots(user, list(roots.values()))
        shelves = self.shelf_ids_for(user, list(roots))
        return [self.serialize(book, user, members=members[self._canonical(book).id], states=states,
                               shelf_ids=shelves.get(self._canonical(book).id, [])) for book in books]

    def _catalog_roots(self, user: User) -> list[BookItem]:
        return list(self.db.scalars(select(BookItem).where(self.catalog_predicate(user))))

    def _state_map_for_roots(self, user: User, roots: list[BookItem]) -> tuple[dict[str, list[BookItem]], dict[str, BookState]]:
        if not roots:
            return {}, {}
        root_ids = [root.id for root in roots]
        members_by_root: dict[str, list[BookItem]] = {root.id: [] for root in roots}
        rows = list(self.db.scalars(select(BookItem).where(
            or_(BookItem.id.in_(root_ids), BookItem.bundle_parent_id.in_(root_ids)), BookItem.deleted_at.is_(None))))
        for member in rows:
            members_by_root[member.bundle_parent_id or member.id].append(member)
        for root_id, members in members_by_root.items():
            members.sort(key=lambda member: (0 if member.id == root_id else 1, member.created_at or datetime.min, member.id))
        return members_by_root, self.state_for(user, [member.id for member in rows])

    def list_books(self, user: User, *, kind: str | None, q: str | None, sort: str, status: str, shelf_id: str | None, offset: int, limit: int) -> tuple[list[BookItem], int, str | None]:
        roots = self._catalog_roots(user)
        if kind in {"ebook", "manga"}:
            roots = [root for root in roots if root.kind == kind]
        if q:
            needle = _normalized(q[:200])
            roots = [root for root in roots if any(needle in _normalized(value) for value in (root.title, root.author, root.series_name))]
        shelf_positions: dict[str, int] = {}
        if shelf_id:
            shelf = self.db.get(BookShelf, shelf_id)
            if shelf is None or (shelf.owner_user_id != user.id and shelf.visibility != "shared"):
                return [], 0, None
            entries = list(self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == shelf_id).order_by(BookShelfMembership.position, BookShelfMembership.id)))
            for entry in entries:
                asset = self.db.get(BookItem, entry.book_id)
                if asset is None:
                    continue
                root_id = self._canonical(asset).id
                shelf_positions.setdefault(root_id, entry.position)
            roots = [root for root in roots if root.id in shelf_positions]
        members_by_root, states = self._state_map_for_roots(user, roots)
        filtered: list[BookItem] = []
        for root in roots:
            info = self._logical_info(root, user, members=members_by_root[root.id], states=states)
            matches = {
                "unread": not info["completed"] and not info["has_checkpoint"],
                "reading": info["reading"],
                "read": info["completed"],
                "favorites": info["favorite"],
                "reading_list": info["reading_list"],
            }
            if status == "all" or matches.get(status, False):
                filtered.append(root)
        if sort == "custom" and shelf_id:
            filtered.sort(key=lambda root: (shelf_positions.get(root.id, MAX_SHELF_ENTRIES + 1), root.id))
        elif sort == "title":
            filtered.sort(key=lambda root: (_normalized(root.title), root.id))
        elif sort == "author":
            filtered.sort(key=lambda root: (_normalized(root.author), _normalized(root.title), root.id))
        else:
            filtered.sort(key=lambda root: (-(root.created_at or datetime.min).timestamp(), root.id))
        total = len(filtered)
        page = filtered[offset:offset + limit + 1]
        return page[:limit], total, str(offset + limit) if len(page) > limit else None

    def _new_state(self, user: User, book_id: str) -> BookState:
        state = BookState(id=str(uuid.uuid4()), user_id=user.id, book_id=book_id, unit_index=0, offset=0, completed=False, favorite=False, reading_list=False)
        self.db.add(state)
        return state

    def update_state(self, book: BookItem, user: User, values: dict[str, Any]) -> BookState:
        canonical = self._canonical(book)
        members = self._bundle_members(canonical)
        states = self._states_for_members(user, members)
        target = states.get(book.id)
        if target is None:
            target = self._new_state(user, book.id)
            states[book.id] = target
        reset_checkpoint = values.get("completed") is False and values.get("unit_index") == 0 and values.get("offset") == 0
        if reset_checkpoint:
            # Resetting the logical book is the one operation that deliberately touches every format's checkpoint.
            for member in members:
                state = states.get(member.id)
                if state is None and member.id in {canonical.id, book.id}:
                    state = self._new_state(user, member.id)
                    states[member.id] = state
                if state is not None:
                    state.completed = False
                    state.unit_index = 0
                    state.offset = 0
                    state.last_read_at = None
        for flag in ("favorite", "reading_list"):
            if flag not in values or values[flag] is None:
                continue
            if values[flag] is True:
                # Keep both the requested asset and the logical root visible to later OR aggregation.
                for member_id in {canonical.id, book.id}:
                    if member_id not in states:
                        states[member_id] = self._new_state(user, member_id)
            for state in states.values():
                setattr(state, flag, values[flag])
        if values.get("completed") is True and "unit_index" not in values and "offset" not in values:
            for state in states.values():
                state.completed = True
        for key in ("completed", "unit_index", "offset"):
            if key in values and values[key] is not None:
                setattr(target, key, values[key])
        units = self._units(book)
        if target.unit_index >= max(1, units):
            raise BookError("Reading position is outside this book.")
        if not reset_checkpoint and (any(key in values and values[key] is not None for key in ("unit_index", "offset")) or values.get("completed") is True):
            target.last_read_at = utcnow()
        self.db.flush()
        return target

    def update_book(self, book: BookItem, user: User, values: dict[str, Any]) -> BookItem:
        canonical = self._canonical(book)
        if canonical.owner_user_id != user.id:
            raise BookForbidden("Only the book owner may edit it.")
        if "filename" in values:
            book.filename = safe_filename(values["filename"], required=False)
        if "title" in values:
            title = _clean(values["title"])
            if title is None:
                raise BookError("Book title is required.")
            canonical.title = title
        for field in ("author", "reading_direction", "genres"):
            if field in values:
                setattr(canonical, field, _genres(values[field]) if field == "genres" else (_clean(values[field]) if field == "author" else values[field]))
        if "visibility" in values:
            for member in self._bundle_members(canonical, include_deleted=True):
                member.visibility = values["visibility"]
        if "series_name" in values:
            canonical.series_name = _clean(values["series_name"])
        if "description" in values:
            canonical.description = clean_synopsis(values["description"])
            canonical.manifest = {**(canonical.manifest if isinstance(canonical.manifest, dict) else {}), "description": canonical.description}
        if "series_name" in values or "author" in values:
            canonical.series_id = series_id(canonical.series_name, canonical.author) if canonical.series_name else (canonical.id if canonical.kind == "manga" else None)
        if "volume_number" in values:
            canonical.volume_number = values["volume_number"]
        self.db.flush()
        return book

    def reader(self, book: BookItem) -> dict[str, Any]:
        manifest = book.manifest or {}
        return {"book": book, "chapters": [{"label": _clean(row.get("label")) or f"Chapter {index + 1}"} for index, row in enumerate(manifest.get("chapters") or []) if isinstance(row, dict)], "page_count": len(manifest.get("pages") or []), "reading_direction": self._canonical(book).reading_direction}

    def chapter_nodes(self, book: BookItem, index: int) -> list[dict]:
        if book.format != "epub":
            raise BookNotFound("Chapter not found")
        from app.services.book_archives import chapter_nodes
        return chapter_nodes(stored_path(book.storage_key), book.manifest or {}, index)

    def resource(self, book: BookItem, member: str) -> tuple[bytes, str]:
        if not isinstance(member, str) or len(member) > 1024:
            raise BookNotFound("Resource not found")
        from app.services.book_archives import read_resource
        return read_resource(stored_path(book.storage_key), member)

    def _auto_names(self, root: BookItem) -> set[str]:
        return {_normalized(_filename_stem(member.filename)) for member in self._bundle_members(root) if member.filename}

    def _auto_compatible(self, left: BookItem, right: BookItem) -> bool:
        if left.owner_user_id != right.owner_user_id or left.visibility != right.visibility or left.kind != right.kind:
            return False
        if left.format == right.format or not _compatible_language(left.language, right.language):
            return False
        # A logical entry may already contain the other format. Never turn a later
        # import into a duplicate same-format member of that bundle.
        if any(member.format == left.format for member in self._bundle_members(right, include_deleted=True)):
            return False
        if any(member.format == right.format for member in self._bundle_members(left, include_deleted=True)):
            return False
        if left.volume_number is not None and right.volume_number is not None and left.volume_number != right.volume_number:
            return False
        return True

    def _auto_match(self, left: BookItem, right: BookItem) -> bool:
        if not left.filename or not right.filename or not self._auto_compatible(left, right):
            return False
        if self._auto_names(left) & self._auto_names(right):
            return True
        if not _known_author(left.author) or not _known_author(right.author):
            return False
        left_key = (_normalized(left.title), _normalized(left.author))
        right_key = (_normalized(right.title), _normalized(right.author))
        return bool(left_key[0] and left_key[1] and left_key == right_key)

    @staticmethod
    def _auto_primary(left: BookItem, right: BookItem) -> BookItem:
        if {left.format, right.format} == {"epub", "pdf"}:
            return left if left.format == "epub" else right
        left_key = (left.created_at or datetime.min, left.id)
        right_key = (right.created_at or datetime.min, right.id)
        return left if left_key <= right_key else right

    def _find_auto_pair(self, roots: list[BookItem]) -> tuple[BookItem, BookItem] | None:
        for left in roots:
            candidates = [right for right in roots if right.id != left.id and self._auto_match(left, right)]
            if len(candidates) == 1:
                right = candidates[0]
                if sum(self._auto_match(right, other) for other in roots if other.id != right.id) == 1:
                    return left, right
        return None

    def _merge_shelves(self, asset_ids: set[str], canonical_id: str) -> None:
        affected = list(self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.book_id.in_(asset_ids))))
        if not affected:
            return
        by_shelf: dict[str, list[BookShelfMembership]] = defaultdict(list)
        for entry in affected:
            by_shelf[entry.shelf_id].append(entry)
        all_entries = list(self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id.in_(list(by_shelf)))))
        by_shelf_all: dict[str, list[BookShelfMembership]] = defaultdict(list)
        for entry in all_entries:
            by_shelf_all[entry.shelf_id].append(entry)
        replacements: dict[str, list[str]] = {}
        for shelf_id, entries in by_shelf_all.items():
            entries.sort(key=lambda entry: (entry.position, entry.id))
            output: list[str] = []
            inserted = False
            for entry in entries:
                if entry.book_id in asset_ids:
                    if not inserted:
                        output.append(canonical_id)
                        inserted = True
                else:
                    output.append(entry.book_id)
            replacements[shelf_id] = output
        for entry in all_entries:
            self.db.delete(entry)
        self.db.flush()
        for shelf_id, book_ids in replacements.items():
            self.db.add_all(BookShelfMembership(id=str(uuid.uuid4()), shelf_id=shelf_id, book_id=book_id, position=position) for position, book_id in enumerate(book_ids))
        self.db.flush()

    def _combine_roots(self, primary: BookItem, roots: list[BookItem]) -> BookItem:
        assets: list[BookItem] = []
        seen: set[str] = set()
        for root in roots:
            for member in self._bundle_members(root, include_deleted=True):
                if member.id not in seen:
                    seen.add(member.id)
                    assets.append(member)
        asset_ids = {member.id for member in assets}
        for member in assets:
            member.bundle_parent_id = None if member.id == primary.id else primary.id
        self._merge_shelves(asset_ids, primary.id)
        self.db.flush()
        return primary

    def combine(self, user: User, primary_book_id: str, book_ids: list[str]) -> BookItem:
        if len(book_ids) > 100 or len(book_ids) < 2:
            raise BookError("Choose at least two and at most 100 entries.")
        requested_ids = list(dict.fromkeys([primary_book_id, *book_ids]))
        if len(requested_ids) < 2:
            raise BookError("Choose at least two distinct entries.")
        requested = [self.get(book_id, user) for book_id in requested_ids]
        primary = self._canonical(requested[0])
        if primary.owner_user_id != user.id:
            raise BookForbidden("Only the book owner may combine entries.")
        roots: list[BookItem] = []
        for book in requested:
            root = self._canonical(book)
            if root.id not in {entry.id for entry in roots}:
                roots.append(root)
            if root.owner_user_id != primary.owner_user_id:
                raise BookForbidden("Entries must have the same owner.")
            if root.visibility != primary.visibility:
                raise BookError("Entries must have the same visibility.")
            if root.kind != primary.kind:
                raise BookError("Entries must have the same kind.")
            if not _compatible_language(root.language, primary.language):
                raise BookError("Entries must have compatible languages.")
            if root.volume_number is not None and primary.volume_number is not None and root.volume_number != primary.volume_number:
                raise BookError("Different volumes must stay separate.")
        if len(roots) < 2:
            raise BookError("Choose at least two distinct entries.")
        if len({root.volume_number for root in roots if root.volume_number is not None}) > 1:
            raise BookError("Different volumes must stay separate.")
        if any(not _compatible_language(left.language, right.language) for left in roots for right in roots):
            raise BookError("Entries must have compatible languages.")
        return self._combine_roots(primary, roots)

    def _auto_bundle_import(self, book: BookItem, user: User) -> None:
        roots = list(self.db.scalars(select(BookItem).where(BookItem.owner_user_id == user.id, BookItem.deleted_at.is_(None), BookItem.bundle_parent_id.is_(None))))
        candidates = [root for root in roots if root.id != book.id and self._auto_match(book, root)]
        if len(candidates) != 1:
            return
        other = candidates[0]
        if sum(self._auto_match(other, root) for root in roots if root.id != other.id) != 1:
            return
        primary = self._auto_primary(book, other)
        self._combine_roots(primary, [book, other])

    def bundle_matches(self, user: User) -> dict[str, int]:
        combined = 0
        while True:
            roots = list(self.db.scalars(select(BookItem).where(BookItem.owner_user_id == user.id, BookItem.deleted_at.is_(None), BookItem.bundle_parent_id.is_(None)).order_by(BookItem.created_at, BookItem.id)))
            pair = self._find_auto_pair(roots)
            if pair is None:
                break
            left, right = pair
            primary = self._auto_primary(left, right)
            self._combine_roots(primary, [primary, right if primary.id == left.id else left])
            combined += 1
        remaining = self.db.scalar(select(func.count()).select_from(BookItem).where(BookItem.owner_user_id == user.id, BookItem.deleted_at.is_(None), BookItem.bundle_parent_id.is_(None))) or 0
        return {"combined": combined, "remaining": remaining}

    def separate(self, book_id: str, user: User) -> BookItem:
        book = self.get(book_id, user)
        root = self._canonical(book)
        if root.owner_user_id != user.id:
            raise BookForbidden("Only the book owner may separate files.")
        if book.id == root.id:
            raise BookError("Choose an alternate file to separate.")
        book.bundle_parent_id = None
        entries = list(self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.book_id == root.id)))
        for entry in entries:
            siblings = list(self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == entry.shelf_id).order_by(BookShelfMembership.position, BookShelfMembership.id)))
            for sibling in siblings:
                if sibling.position > entry.position:
                    sibling.position += 1
            self.db.add(BookShelfMembership(id=str(uuid.uuid4()), shelf_id=entry.shelf_id, book_id=book.id, position=entry.position + 1))
        self.db.flush()
        return book

    def trash(self, book_id: str, user: User) -> None:
        book = self.get(book_id, user)
        root = self._canonical(book)
        if root.owner_user_id != user.id:
            raise BookForbidden("Only the book owner may move it to Trash.")
        if book.id != root.id:
            raise BookError("Separate this file into an entry before moving it to Trash.")
        removed_at = utcnow()
        members = self._bundle_members(root, include_deleted=True)
        for member in members:
            member.deleted_at = removed_at
        self.db.flush()

    def restore(self, book_id: str, user: User) -> BookItem:
        book = self.db.get(BookItem, book_id)
        if book is None:
            raise BookNotFound("Book not found")
        root = self._canonical(book)
        if root.owner_user_id != user.id:
            raise BookForbidden("Only the book owner may restore it.")
        if root.deleted_at is not None or book.id == root.id:
            for member in self._bundle_members(root, include_deleted=True):
                member.deleted_at = None
        else:
            book.deleted_at = None
        self.db.flush()
        return book

    def trash_list(self, user: User) -> list[BookItem]:
        return list(self.db.scalars(select(BookItem).where(BookItem.owner_user_id == user.id, BookItem.bundle_parent_id.is_(None), BookItem.deleted_at.is_not(None)).order_by(BookItem.deleted_at.desc(), BookItem.id)))

    def shelves(self, user: User) -> list[dict[str, Any]]:
        shelves = list(self.db.scalars(select(BookShelf).where(or_(BookShelf.owner_user_id == user.id, BookShelf.visibility == "shared")).order_by(BookShelf.created_at, BookShelf.id)))
        result = []
        for shelf in shelves:
            entries = list(self.db.scalars(select(BookShelfMembership).join(BookItem, BookItem.id == BookShelfMembership.book_id).where(BookShelfMembership.shelf_id == shelf.id, self.catalog_predicate(user)).order_by(BookShelfMembership.position, BookShelfMembership.id)))
            if shelf.owner_user_id != user.id and not entries:
                continue
            books = {book.id: book for book in self.db.scalars(select(BookItem).where(BookItem.id.in_([entry.book_id for entry in entries])))} if entries else {}
            result.append({"id": shelf.id, "name": shelf.name, "description": shelf.description or "", "visibility": shelf.visibility, "can_edit": shelf.owner_user_id == user.id, "book_ids": [entry.book_id for entry in entries], "count": len(entries), "covers": [f"/api/books/{entry.book_id}/cover" for entry in entries if (books.get(entry.book_id).manifest or {}).get("cover_member")][:4]})
        return result

    def create_shelf(self, user: User, values: dict[str, Any]) -> BookShelf:
        name = " ".join(values["name"].split())
        if not name:
            raise BookError("Shelf name is required.")
        shelf = BookShelf(id=str(uuid.uuid4()), owner_user_id=user.id, name=name, name_key=name.casefold(), description=values.get("description"), visibility=values.get("visibility", "private"))
        self.db.add(shelf)
        self.db.flush()
        return shelf

    def edit_shelf(self, shelf_id: str, user: User, values: dict[str, Any]) -> BookShelf:
        shelf = self._owned_shelf(shelf_id, user)
        if values.get("name") is not None:
            shelf.name = " ".join(values["name"].split())
            shelf.name_key = shelf.name.casefold()
        for key in ("description", "visibility"):
            if key in values:
                setattr(shelf, key, values[key])
        self.db.flush()
        return shelf

    def replace_shelf_entries(self, shelf_id: str, user: User, book_ids: list[str]) -> BookShelf:
        shelf = self._owned_shelf(shelf_id, user)
        if len(book_ids) > MAX_SHELF_ENTRIES:
            raise BookError("Shelf entries are invalid.")
        canonical_ids: list[str] = []
        for book_id in book_ids:
            root = self._canonical(self.get(book_id, user))
            if root.id in canonical_ids:
                raise BookError("Shelf entries are invalid.")
            canonical_ids.append(root.id)
        for entry in self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == shelf.id)):
            self.db.delete(entry)
        self.db.flush()
        self.db.add_all(BookShelfMembership(id=str(uuid.uuid4()), shelf_id=shelf.id, book_id=book_id, position=index) for index, book_id in enumerate(canonical_ids))
        self.db.flush()
        return shelf

    def delete_shelf(self, shelf_id: str, user: User) -> None:
        shelf = self._owned_shelf(shelf_id, user)
        for entry in self.db.scalars(select(BookShelfMembership).where(BookShelfMembership.shelf_id == shelf.id)):
            self.db.delete(entry)
        self.db.delete(shelf)
        self.db.flush()

    def _owned_shelf(self, shelf_id: str, user: User) -> BookShelf:
        shelf = self.db.get(BookShelf, shelf_id)
        if shelf is None or shelf.owner_user_id != user.id:
            raise BookNotFound("Shelf not found")
        return shelf

    def series(self, user: User, query: str | None = None, status: str = "all") -> list[dict[str, Any]]:
        books = list(self.db.scalars(select(BookItem).where(self.catalog_predicate(user), BookItem.kind == "manga").order_by(BookItem.series_name.collate("NOCASE"), BookItem.volume_number.nulls_last(), BookItem.title, BookItem.id)))
        groups: dict[str, list[BookItem]] = {}
        for book in books:
            if book.series_id:
                groups.setdefault(book.series_id, []).append(book)
        members_by_root, states = self._state_map_for_roots(user, books)
        result = []
        for sid, entries in groups.items():
            if query:
                needle = _normalized(query)
                if not any(needle in _normalized(field) for book in entries for field in (book.title, book.author, book.series_name)):
                    continue
            info_by_id = {book.id: self._logical_info(book, user, members=members_by_root[book.id], states=states) for book in entries}
            read = sum(bool(info_by_id[book.id]["completed"]) for book in entries)
            unread = all(not info_by_id[book.id]["completed"] and not info_by_id[book.id]["has_checkpoint"] for book in entries)
            reading = any(info_by_id[book.id]["reading"] for book in entries)
            favorite = any(info_by_id[book.id]["favorite"] for book in entries)
            reading_list = any(info_by_id[book.id]["reading_list"] for book in entries)
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
            next_book = next((book.id for book in entries if info_by_id[book.id]["reading"]), None)
            if next_book is None:
                next_book = next((book.id for book in entries if not info_by_id[book.id]["completed"]), None)
            first = entries[0]
            result.append({"id": sid, "title": first.series_name or first.title, "author": first.author or "", "description": clean_synopsis(first.description) or "", "cover_url": f"/api/books/{first.id}/cover" if (first.manifest or {}).get("cover_member") else None, "volume_count": len(entries), "read_volume_count": read, "next_book_id": next_book, "created_at": max(book.created_at for book in entries)})
        return result

    def overview(self, user: User) -> dict[str, Any]:
        books = self._catalog_roots(user)
        members_by_root, states = self._state_map_for_roots(user, books)
        info_by_id = {book.id: self._logical_info(book, user, members=members_by_root[book.id], states=states) for book in books}
        ebooks = sum(book.kind == "ebook" for book in books)
        volumes = sum(book.kind == "manga" for book in books)
        manga_series = len({book.series_id for book in books if book.kind == "manga" and book.series_id is not None})
        recent = sorted(books, key=lambda book: (-(book.created_at or datetime.min).timestamp(), book.id))[:12]
        reading = [book for book in books if info_by_id[book.id]["reading"]]
        reading.sort(key=lambda book: (-(info_by_id[book.id]["selected_state"].last_read_at or datetime.min).timestamp() if info_by_id[book.id]["selected_state"] else 0, book.id))
        favorites = [book for book in books if info_by_id[book.id]["favorite"]]
        favorite_authors = {_normalized(book.author) for book in favorites if _known_author(book.author)}
        favorite_genres = {_normalized(genre) for book in favorites for genre in (book.genres or []) if isinstance(genre, str)}
        candidates = [book for book in books if not info_by_id[book.id]["completed"] and not info_by_id[book.id]["has_checkpoint"]]

        def affinity(book: BookItem) -> int:
            return int(bool(book.author and _normalized(book.author) in favorite_authors)) + int(any(isinstance(genre, str) and _normalized(genre) in favorite_genres for genre in (book.genres or [])))

        discover = sorted(candidates, key=lambda book: (-affinity(book), -(book.created_at or datetime.min).timestamp(), book.id))[:12]
        return {"totals": {"ebooks": ebooks, "manga_series": manga_series, "volumes": volumes}, "continue_reading": self.serialize_many(reading[:12], user), "recently_added": self.serialize_many(recent, user), "shelves": self.shelves(user), "discover": self.serialize_many(discover, user)}
