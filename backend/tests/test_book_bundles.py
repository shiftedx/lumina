from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, inspect, text

from app import book_models  # noqa: F401 - registers Books metadata before db_factory creates its schema
from app import db as db_module
from app.book_models import BookItem, BookState
from app.config import settings
from app.db import Base

from test_book_native import pdf, rar
from test_books_api import _cbz, _epub, _user


def _import(client, filename: str, body: bytes) -> dict:
    response = client.put(f"/api/books/import?filename={quote(filename)}", content=body)
    assert response.status_code == 201, response.text
    return response.json()


def _cbr(*, number: int) -> bytes:
    return rar({
        "ComicInfo.xml": f"<ComicInfo><Title>Night</Title><Writer>Ada</Writer><Series>Night</Series><Number>{number}</Number></ComicInfo>".encode(),
        "1.png": b"\x89PNG\r\n\x1a\nsmall",
    })


def test_import_auto_bundles_epub_pdf_by_safe_filename_and_keeps_both_readers(api_client, db_factory) -> None:
    owner = _user(db_factory, "bundle-owner")
    with api_client(user=owner, base_url="http://localhost") as client:
        epub = _import(client, r"incoming\Northbound.epub", _epub())
        pdf_book = _import(client, "/incoming/Northbound.pdf", pdf())

        assert epub["filename"] == "Northbound.epub"
        assert pdf_book["id"] != epub["id"]
        assert pdf_book["canonical_book_id"] == epub["id"]
        assert {file["filename"] for file in pdf_book["files"]} == {"Northbound.epub", "Northbound.pdf"}
        assert client.get(f"/api/books/{epub['id']}/chapters/0").status_code == 200
        assert client.get(f"/api/books/{pdf_book['id']}/pages/1").status_code == 200

        page = client.get("/api/books").json()
        assert page["total"] == 1
        root = page["items"][0]
        assert root["id"] == epub["id"]
        assert root["canonical_book_id"] == epub["id"]
        assert root["format"] == "epub" and root["chapter_count"] == 1 and root["page_count"] == 0
        assert root["reading_file_id"] == epub["id"]

        pdf_state = client.patch(f"/api/books/{pdf_book['id']}/state", json={"unit_index": 1}).json()
        assert pdf_state["format"] == "pdf" and pdf_state["progress"]["percent"] == 50
        logical = client.get(f"/api/books/{epub['id']}").json()
        assert logical["reading_file_id"] == pdf_book["id"] and logical["progress"]["percent"] == 50

        # A root checkpoint stays in the EPUB's coordinate system; it never inherits PDF page 1.
        root_state = client.patch(f"/api/books/{epub['id']}/state", json={"unit_index": 0, "offset": 0.25}).json()
        assert root_state["reading_file_id"] == epub["id"] and root_state["progress"]["percent"] == 25
        assert client.get(f"/api/books/{pdf_book['id']}").json()["progress"]["percent"] == 50


def test_auto_matching_is_strict_about_names_formats_owners_and_volume(api_client, db_factory) -> None:
    owner = _user(db_factory, "strict-owner")
    other = _user(db_factory, "strict-other")
    with api_client(user=owner, base_url="http://localhost") as client:
        first = _import(client, "first.epub", _epub())
        same_format = _import(client, "second.epub", _epub())
        assert first["canonical_book_id"] != same_format["canonical_book_id"]
        assert client.post("/api/books/bundle-matches").json()["combined"] == 0

        volume_cbz = _import(client, "Night v1.cbz", _cbz(title="Night", series="Night", number=1))
        volume_cbr = _import(client, "Night v1.cbr", _cbr(number=2))
        assert volume_cbz["canonical_book_id"] != volume_cbr["canonical_book_id"]

    with api_client(user=other, base_url="http://localhost") as client:
        foreign = _import(client, "first.pdf", pdf())
        assert foreign["canonical_book_id"] == foreign["id"]


def test_manual_combine_preserves_file_states_and_shelf_order(api_client, db_factory) -> None:
    owner = _user(db_factory, "manual-owner")
    with api_client(user=owner, base_url="http://localhost") as client:
        primary = _import(client, "primary.epub", _epub())
        secondary = _import(client, "secondary.epub", _epub())
        client.patch(f"/api/books/{primary['id']}/state", json={"unit_index": 0, "offset": 0.25, "favorite": True})
        client.patch(f"/api/books/{secondary['id']}/state", json={"unit_index": 0, "offset": 0.75, "reading_list": True})
        shelf = client.post("/api/books/shelves", json={"name": "Reading order"}).json()
        client.put(f"/api/books/shelves/{shelf['id']}/entries", json={"book_ids": [secondary["id"], primary["id"]]})

        combined = client.post("/api/books/combine", json={"primary_book_id": primary["id"], "book_ids": [primary["id"], secondary["id"]]}).json()
        assert combined["id"] == primary["id"] and combined["canonical_book_id"] == primary["id"]
        assert {file["id"] for file in combined["files"]} == {primary["id"], secondary["id"]}
        assert client.get("/api/books").json()["total"] == 1
        assert client.get(f"/api/books/{secondary['id']}").json()["progress"]["offset"] == 0.75
        assert client.get("/api/books/shelves").json()[0]["book_ids"] == [primary["id"]]

    with db_factory() as db:
        child = db.get(BookItem, secondary["id"])
        assert child is not None and child.bundle_parent_id == primary["id"]
        states = {state.book_id: state for state in db.query(BookState).filter(BookState.user_id == owner.id).all()}
        assert states[primary["id"]].offset == 0.25 and states[secondary["id"]].offset == 0.75
        assert states[primary["id"]].favorite and states[secondary["id"]].reading_list


def test_child_visibility_and_management_are_owner_only(api_client, db_factory) -> None:
    owner, other = _user(db_factory, "visibility-owner"), _user(db_factory, "visibility-other")
    with api_client(user=owner, base_url="http://localhost") as client:
        primary = _import(client, "private-one.epub", _epub())
        child = _import(client, "private-two.pdf", pdf())
        client.post("/api/books/combine", json={"primary_book_id": primary["id"], "book_ids": [primary["id"], child["id"]]})

    with api_client(user=other, base_url="http://localhost") as client:
        assert client.get(f"/api/books/{child['id']}").status_code == 404
        assert client.delete(f"/api/books/{primary['id']}").status_code == 404
        assert client.post(f"/api/books/{primary['id']}/restore").status_code == 403
        assert client.post("/api/books/combine", json={"primary_book_id": primary["id"], "book_ids": [primary["id"], child["id"]]}).status_code == 404

    with api_client(user=owner, base_url="http://localhost") as client:
        assert client.patch(f"/api/books/{primary['id']}", json={"visibility": "shared"}).status_code == 200
    with api_client(user=other, base_url="http://localhost") as client:
        assert client.get(f"/api/books/{child['id']}").status_code == 200


def test_trash_hides_root_and_children_without_removing_files_or_state(api_client, db_factory) -> None:
    owner = _user(db_factory, "trash-owner")
    with api_client(user=owner, base_url="http://localhost") as client:
        root = _import(client, "trash-one.epub", _epub())
        child = _import(client, "trash-two.pdf", pdf())
        client.post("/api/books/combine", json={"primary_book_id": root["id"], "book_ids": [root["id"], child["id"]]})
        client.patch(f"/api/books/{child['id']}/state", json={"unit_index": 1, "favorite": True})
        assert client.delete(f"/api/books/{root['id']}").status_code == 204
        assert client.get("/api/books").json()["total"] == 0
        assert client.get("/api/books/overview").json()["totals"] == {"ebooks": 0, "manga_series": 0, "volumes": 0}
        assert client.get(f"/api/books/{child['id']}").status_code == 404
        trash = client.get("/api/books/trash").json()
        assert len(trash) == 1 and {item["id"] for item in trash[0]["files"]} == {root["id"], child["id"]}

    from app.services.books import stored_path

    with db_factory() as db:
        rows = {book.id: book for book in db.query(BookItem).filter(BookItem.id.in_([root["id"], child["id"]])).all()}
        assert rows[root["id"]].deleted_at is not None and rows[child["id"]].deleted_at is not None
        assert db.query(BookState).filter(BookState.book_id == child["id"], BookState.user_id == owner.id).one().unit_index == 1
        paths = [stored_path(row.storage_key) for row in rows.values()]
        assert all(path.exists() for path in paths)

    with api_client(user=owner, base_url="http://localhost") as client:
        restored = client.post(f"/api/books/{root['id']}/restore")
        assert restored.status_code == 200 and restored.json()["id"] == root["id"]
        assert client.get(f"/api/books/{child['id']}").json()["progress"]["unit_index"] == 1
        assert client.get("/api/books/trash").json() == []


def test_schema_20_to_21_adds_bundle_columns_without_losing_books_or_states(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS ix_book_items_bundle_parent_id"))
        connection.execute(text("DROP INDEX IF EXISTS ix_book_items_deleted_at"))
        connection.execute(text("ALTER TABLE book_items DROP COLUMN filename"))
        connection.execute(text("ALTER TABLE book_items DROP COLUMN bundle_parent_id"))
        connection.execute(text("ALTER TABLE book_items DROP COLUMN deleted_at"))
        connection.execute(text("INSERT INTO book_items (id, storage_key, format, kind, title, genres, reading_direction, manifest, visibility, owner_user_id, available, created_at, updated_at) VALUES ('legacy-book', '00000000-0000-0000-0000-000000000021.epub', 'epub', 'ebook', 'Legacy', '[]', 'ltr', '{\"chapters\": []}', 'private', 'legacy-owner', 1, '2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
        connection.execute(text("INSERT INTO book_states (id, user_id, book_id, unit_index, offset, completed, favorite, reading_list, last_read_at, updated_at, created_at) VALUES ('legacy-state', 'legacy-owner', 'legacy-book', 0, 0.5, 0, 1, 0, '2026-10-13 00:00:00', '2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
        connection.execute(text("PRAGMA user_version = 20"))
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_module.init_db()
    columns = {column["name"] for column in inspect(engine).get_columns("book_items")}
    assert {"filename", "bundle_parent_id", "deleted_at"} <= columns
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == 21
        assert connection.scalar(text("SELECT title FROM book_items WHERE id = 'legacy-book'")) == "Legacy"
        assert connection.scalar(text("SELECT offset FROM book_states WHERE id = 'legacy-state'")) == 0.5


def test_bundling_rollback_sql_is_additive_to_files_and_states() -> None:
    readme = Path(__file__).parents[2] / "docker" / "README.md"
    match = re.search(r"\| Before Books format bundling \(schema 20\) \| `([^`]+)`", readme.read_text(encoding="utf-8"))
    assert match
    statements = match.group(1).split("; ")
    assert statements[0].startswith("UPDATE book_items SET bundle_parent_id = NULL")
    assert "DROP COLUMN filename" in match.group(1)
    assert "PRAGMA user_version = 20" in match.group(1)
