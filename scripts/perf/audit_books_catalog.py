#!/usr/bin/env python3
"""Measure Books catalog hydration, filesystem probes, and shelf query growth."""
from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import book_models  # noqa: E402,F401
from app.book_models import BookItem, BookShelf, BookShelfMembership, BookState  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import User  # noqa: E402


def service_class(ref: str | None):  # noqa: ANN201
    if ref is None:
        from app.services.books import BookService

        return BookService
    source = subprocess.run(
        ["git", "show", f"{ref}:backend/app/services/books.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = ModuleType("books_catalog_audit_ref")
    module.__file__ = f"{ref}:backend/app/services/books.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - trusted local Git revision
    return module.BookService


def key(index: int, extension: str) -> str:
    return f"{index:08x}-0000-4000-8000-{index:012x}.{extension}"


def seed(factory, count: int, shelf_count: int, target_series_size: int) -> User:  # noqa: ANN001
    now = datetime(2026, 1, 1, tzinfo=UTC)
    user = User(id="audit-user", username="audit-user", display_name="Audit", role="viewer", is_active=True)
    books: list[BookItem] = []
    states: list[BookState] = []
    for index in range(count):
        target = index < target_series_size
        manga = target or index % 2 == 1
        extension = "cbz" if manga else "epub"
        book_id = key(index, extension).rsplit(".", 1)[0]
        books.append(BookItem(
            id=book_id,
            storage_key=key(index, extension),
            format=extension,
            kind="manga" if manga else "ebook",
            filename=f"book-{index}.{extension}",
            title=f"Book {index:06d}",
            author=f"Author {index % 30}",
            description="",
            genres=[f"Genre {index % 8}"],
            series_id="target-series" if target else (f"series-{index:08d}" if manga else None),
            series_name="Target series" if target else (f"Series {index:08d}" if manga else None),
            volume_number=float(index) if manga else None,
            manifest={"pages": ["001.png"]} if manga else {"chapters": [{"label": "One"}]},
            visibility="private",
            owner_user_id=user.id,
            created_at=now + timedelta(seconds=index),
        ))
        if index % 3 == 0:
            states.append(BookState(
                id=f"state-{index:08d}", user_id=user.id, book_id=book_id,
                unit_index=0, offset=0.5, completed=index % 6 == 0,
                favorite=index % 9 == 0, reading_list=index % 12 == 0,
                last_read_at=now + timedelta(seconds=index),
            ))

    shelves: list[BookShelf] = []
    memberships: list[BookShelfMembership] = []
    entries_per_shelf = min(20, count)
    for shelf_index in range(shelf_count):
        shelf_id = f"shelf-{shelf_index:08d}"
        shelves.append(BookShelf(
            id=shelf_id, owner_user_id=user.id, name=f"Shelf {shelf_index}",
            name_key=f"shelf {shelf_index}", visibility="private", created_at=now,
        ))
        start = (shelf_index * entries_per_shelf) % count
        for position in range(entries_per_shelf):
            book = books[(start + position) % count]
            memberships.append(BookShelfMembership(
                id=f"membership-{shelf_index:04d}-{position:04d}", shelf_id=shelf_id,
                book_id=book.id, position=position,
            ))

    with factory.begin() as db:
        db.add(user)
        db.add_all(books)
        db.add_all(states)
        db.add_all(shelves)
        db.add_all(memberships)
    return user


@contextmanager
def measured(engine, book_service) -> Iterator[Callable[[], dict[str, Any]]]:  # noqa: ANN001
    statements: list[str] = []
    availability_probes = 0
    hydrated_books = 0
    original_available = book_service.__dict__["_available"]

    def record_query(_connection, _cursor, statement, _parameters, _context, _many):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(" ".join(statement.split()))

    def available(_book):  # noqa: ANN001
        nonlocal availability_probes
        availability_probes += 1
        return True

    def hydrated(_book, _context):  # noqa: ANN001
        nonlocal hydrated_books
        hydrated_books += 1

    event.listen(engine, "before_cursor_execute", record_query)
    event.listen(BookItem, "load", hydrated)
    book_service._available = staticmethod(available)
    started = time.perf_counter()
    try:
        yield lambda: {
            "select_queries": len(statements),
            "availability_probes": availability_probes,
            "book_items_hydrated": hydrated_books,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "repeated_selects": sorted(Counter(statements).values(), reverse=True)[:5],
        }
    finally:
        book_service._available = original_available
        event.remove(BookItem, "load", hydrated)
        event.remove(engine, "before_cursor_execute", record_query)


def run_scale(book_service, count: int, shelf_count: int, target_series_size: int) -> dict[str, Any]:  # noqa: ANN001
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    user = seed(factory, count, shelf_count, target_series_size)

    def measure(name: str, operation: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        with factory() as db, measured(engine, book_service) as stats:
            result = operation(book_service(db))
            return {"name": name, **result, **stats()}

    def catalog(service) -> dict[str, Any]:  # noqa: ANN001
        books, total, _cursor = service.list_books(
            user, kind=None, q=None, sort="added", status="all", shelf_id=None, offset=0, limit=40,
        )
        payload = service.serialize_many(books, user)
        return {"returned": len(payload), "total": total}

    def series_list(service) -> dict[str, Any]:  # noqa: ANN001
        if hasattr(service, "series_page"):
            payload, total, _cursor = service.series_page(user, None, "all", "title", 0, 40)
        else:
            all_items = service.series(user)
            all_items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
            payload, total = all_items[:40], len(all_items)
        return {"returned": len(payload), "total": total}

    def catalog_status(service) -> dict[str, Any]:  # noqa: ANN001
        books, total, _cursor = service.list_books(
            user, kind=None, q=None, sort="added", status="favorites", shelf_id=None, offset=0, limit=40,
        )
        payload = service.serialize_many(books, user)
        return {"returned": len(payload), "total": total}

    def catalog_search_sort(service) -> dict[str, Any]:  # noqa: ANN001
        books, total, _cursor = service.list_books(
            user, kind=None, q="Book", sort="author", status="all", shelf_id=None, offset=0, limit=40,
        )
        payload = service.serialize_many(books, user)
        return {"returned": len(payload), "total": total}

    def series_filtered(service) -> dict[str, Any]:  # noqa: ANN001
        if hasattr(service, "series_page"):
            payload, total, _cursor = service.series_page(user, "Series", "favorites", "author", 0, 40)
        else:
            all_items = service.series(user, "Series", "favorites")
            all_items.sort(key=lambda item: (item["author"].casefold(), item["title"].casefold(), item["id"]))
            payload, total = all_items[:40], len(all_items)
        return {"returned": len(payload), "total": total}

    def series_detail(service) -> dict[str, Any]:  # noqa: ANN001
        if hasattr(service, "series_detail"):
            summary, volumes = service.series_detail(user, "target-series")
        else:
            parameters = inspect.signature(service.series).parameters
            summaries = service.series(user, series_id="target-series") if "series_id" in parameters else service.series(user)
            summary = next(item for item in summaries if item["id"] == "target-series")
            volumes = list(service.db.scalars(
                select(BookItem).where(
                    BookItem.series_id == "target-series", BookItem.kind == "manga", service.catalog_predicate(user),
                ).order_by(BookItem.volume_number.nulls_last(), BookItem.title.collate("NOCASE"), BookItem.id)
            ))
        payload = service.serialize_many(volumes, user)
        return {"returned": len(payload), "volume_count": summary["volume_count"]}

    def overview(service) -> dict[str, Any]:  # noqa: ANN001
        payload = service.overview(user)
        return {"returned_shelves": len(payload["shelves"]), "totals": payload["totals"]}

    try:
        return {
            "books": count,
            "shelves": shelf_count,
            "entries_per_shelf": min(20, count),
            "target_series_volumes": target_series_size,
            "measurements": [
                measure("catalog_page", catalog),
                measure("catalog_status_page", catalog_status),
                measure("catalog_search_sort_page", catalog_search_sort),
                measure("series_list", series_list),
                measure("series_filtered_page", series_filtered),
                measure("series_detail", series_detail),
                measure("overview", overview),
            ],
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", help="Load backend/app/services/books.py from this Git revision")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000])
    parser.add_argument("--shelves", type=int, default=100)
    parser.add_argument("--target-series", type=int, default=200)
    args = parser.parse_args()

    book_service = service_class(args.ref)
    result = {
        "service_source": args.ref or "working-tree",
        "scales": [
            run_scale(book_service, size, args.shelves, min(args.target_series, size))
            for size in args.sizes
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
