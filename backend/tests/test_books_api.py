from __future__ import annotations

import io
import struct
import time
import zipfile
import zlib
from html import escape

from app import book_models  # noqa: F401 - registers Books metadata before db_factory creates its schema
from app.book_models import BookItem
from app.http_boundaries import MAX_BOOK_UPLOAD_BODY_BYTES, body_limit
from app.models import MemberAccess, User


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _cover(client, url: str):  # noqa: ANN001,ANN202
    for _attempt in range(100):
        response = client.get(url)
        if response.status_code != 202:
            return response
        time.sleep(0.02)
    return response


def _cbz(*, title: str = "Moon", series: str = "Night", number: int = 1, pages: int = 1, summary: str | None = None) -> bytes:
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        summary_tag = f"<Summary>{summary}</Summary>" if summary is not None else ""
        archive.writestr("ComicInfo.xml", f"<ComicInfo><Title>{title}</Title><Writer>Ada</Writer><Series>{series}</Series><Number>{number}</Number>{summary_tag}</ComicInfo>")
        for page in range(1, pages + 1): archive.writestr(f"{page:03}.png", _png())
    return body.getvalue()


def _epub() -> bytes:
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", """<?xml version='1.0'?><container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>""")
        archive.writestr("OEBPS/content.opf", """<package><metadata><title>Small Book</title><creator>Bea</creator><language>en</language></metadata><manifest><item id='c1' href='chapter.xhtml' media-type='application/xhtml+xml'/><item id='cover' href='cover.png' media-type='image/png' properties='cover-image'/></manifest><spine><itemref idref='c1'/></spine></package>""")
        archive.writestr("OEBPS/chapter.xhtml", "<html><body><h1>Hello</h1><p>Book</p><img src='cover.png' alt='Cover'/></body></html>")
        archive.writestr("OEBPS/cover.png", _png())
    return body.getvalue()


def _plain_cbz() -> bytes:
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("10.png", _png())
        archive.writestr("2.png", _png())
        archive.writestr("1.png", _png())
    return body.getvalue()


def _user(db_factory, user_id: str) -> User:
    with db_factory() as db:
        user = User(id=user_id, username=user_id, display_name=user_id, role="viewer", is_active=True)
        db.add(user); db.commit(); return user


def test_book_synopsis_is_cleaned_stored_and_editable_over_500_characters(api_client, db_factory) -> None:
    owner = _user(db_factory, "synopsis-owner")
    long_synopsis = " ".join(f"Meaningful imported sentence {index} describes the journey." for index in range(40))
    raw_synopsis = f"<p>Imported opening.</p><p>{long_synopsis}</p><script>discarded script</script><p hidden>discarded hidden text</p>"
    expected = f"Imported opening.\n\n{long_synopsis}"

    with api_client(user=owner, base_url="http://localhost") as client:
        imported_response = client.put(
            "/api/books/import?filename=synopsis.cbz",
            content=_cbz(summary=escape(raw_synopsis)),
        )
        assert imported_response.status_code == 201, imported_response.text
        imported = imported_response.json()
        assert imported["description"] == expected
        assert len(imported["description"]) > 500

        edited = ("Edited synopsis with a meaningful sentence. " * 200).strip()
        edited_response = client.patch(f"/api/books/{imported['id']}", json={"description": edited})
        assert edited_response.status_code == 200, edited_response.text
        assert edited_response.json()["description"] == edited

    with db_factory() as db:
        stored = db.get(BookItem, imported["id"])
        assert stored is not None
        assert stored.description == edited
        assert stored.manifest["description"] == edited


def test_existing_raw_html_synopsis_is_cleaned_in_book_response(api_client, db_factory) -> None:
    owner = _user(db_factory, "legacy-synopsis-owner")
    with db_factory() as db:
        db.add(BookItem(
            id="legacy-synopsis-book", storage_key="00000000-0000-0000-0000-000000000001.cbz", format="cbz", kind="manga",
            title="Legacy synopsis", description="<p>Legacy opening.</p><p>Legacy continuation.</p><script>discarded</script><p hidden>secret</p>",
            genres=[], manifest={"pages": []}, visibility="private", owner_user_id=owner.id,
        ))
        db.commit()

    with api_client(user=owner, base_url="http://localhost") as client:
        response = client.get("/api/books/legacy-synopsis-book")

    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Legacy opening.\n\nLegacy continuation."


def test_cbz_books_are_private_then_shareable_with_member_state_and_shelves(api_client, db_factory) -> None:
    owner, other = _user(db_factory, "owner"), _user(db_factory, "other")
    with api_client(user=owner, base_url="http://localhost") as client:
        response = client.put("/api/books/import?filename=moon.cbz", content=_cbz(pages=2))
        assert response.status_code == 201, response.text
        imported = response.json()
        book_id = imported["id"]
        assert imported["title"] == "Moon" and imported["kind"] == "manga" and imported["progress"]["percent"] == 0
        assert client.get(f"/api/books/{book_id}/pages/0").status_code == 200
        assert client.patch(f"/api/books/{book_id}/state", json={"favorite": True, "reading_list": True}).json()["progress"]["updated_at"] is None
        moved = client.patch(f"/api/books/{book_id}/state", json={"unit_index": 0, "offset": 0.5}).json()
        assert moved["progress"]["percent"] == 25 and moved["favorite"]
        shelf = client.post("/api/books/shelves", json={"name": "Night shelf"}).json()
        assert client.put(f"/api/books/shelves/{shelf['id']}/entries", json={"book_ids": [book_id]}).json()["book_ids"] == [book_id]
        assert client.patch(f"/api/books/{book_id}", json={"visibility": "shared"}).status_code == 200
    with api_client(user=other, base_url="http://localhost") as client:
        visible = client.get("/api/books").json()["items"]
        assert [book["id"] for book in visible] == [book_id]
        assert client.patch(f"/api/books/{book_id}", json={"title": "No"}).status_code == 403
        assert client.get("/api/books/shelves").json() == []
    with db_factory() as db:
        db.add(MemberAccess(user_id=other.id, sections=["movies"], streaming={}, can_download=True)); db.commit()
    with api_client(user=other, base_url="http://localhost") as client:
        assert client.get(f"/api/books/{book_id}").status_code == 404
        assert client.get(f"/api/books/{book_id}/pages/0").status_code == 404


def test_epub_reader_resources_listing_series_and_shelves(api_client, db_factory) -> None:
    owner, other = _user(db_factory, "reader"), _user(db_factory, "outsider")
    with api_client(user=owner, base_url="http://localhost") as client:
        epub = client.put("/api/books/import?filename=small.epub", content=_epub()).json()
        first = client.put("/api/books/import?filename=one.cbz", content=_cbz(title="One", number=1)).json()
        second = client.put("/api/books/import?filename=two.cbz", content=_cbz(title="Two", number=2)).json()
        assert client.get(f"/api/books/{epub['id']}/reader").json()["chapters"] == [{"label": "Chapter 1"}]
        nodes = client.get(f"/api/books/{epub['id']}/chapters/0").json()["nodes"]
        assert any(node.get("tag") == "h1" for node in nodes)
        assert _cover(client, f"/api/books/{epub['id']}/cover").headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/books/{epub['id']}/resources?member=OEBPS/cover.png").status_code == 200
        assert client.patch(f"/api/books/{first['id']}/state", json={"completed": True}).status_code == 200
        series = client.get("/api/books/series").json()["items"]
        night = next(item for item in series if item["title"] == "Night")
        assert night["volume_count"] == 2 and night["next_book_id"] == second["id"]
        detail = client.get(f"/api/books/series/{night['id']}").json()
        assert [book["id"] for book in detail["books"]] == [first["id"], second["id"]]
        page1 = client.get("/api/books?kind=manga&sort=title&limit=1").json()
        assert page1["next_cursor"] == "1" and len(page1["items"]) == 1
        assert client.get("/api/books?status=favorites").json()["total"] == 0
        shelf = client.post("/api/books/shelves", json={"name": "Private share", "visibility": "shared"}).json()
        assert client.put(f"/api/books/shelves/{shelf['id']}/entries", json={"book_ids": [second['id'], epub['id']]}).status_code == 200
        # A shared shelf containing only private books leaks neither its name nor members.
        assert client.patch(f"/api/books/{epub['id']}", json={"visibility": "shared"}).status_code == 200
    with api_client(user=other, base_url="http://localhost") as client:
        shelves = client.get("/api/books/shelves").json()
        assert shelves == [{**shelves[0], "book_ids": [epub["id"]], "count": 1, "covers": [f"/api/books/{epub['id']}/cover"]}]
        assert client.put(f"/api/books/shelves/{shelf['id']}/entries", json={"book_ids": [epub['id']]}).status_code == 404
        assert client.delete(f"/api/books/shelves/{shelf['id']}").status_code == 404
        assert client.get(f"/api/books/{first['id']}/cover").status_code == 404


def test_invalid_imports_cleanup_and_metadata_preserves_state(api_client, db_factory) -> None:
    from app.services.books import book_root

    owner = _user(db_factory, "clean")
    with api_client(user=owner, base_url="http://localhost") as client:
        assert client.put("/api/books/import?filename=bad.cbz", content=b"not a zip").status_code == 422
        assert client.put("/api/books/import?filename=bad.epub", content=_cbz()).status_code == 422
        assert list(book_root().glob(".upload-*")) == []
        with db_factory() as db: assert db.query(BookItem).count() == 0
        book = client.put("/api/books/import?filename=keep.cbz", content=_cbz(pages=2)).json()
        client.patch(f"/api/books/{book['id']}/state", json={"unit_index": 1, "offset": 0.25, "favorite": True})
        changed = client.patch(f"/api/books/{book['id']}", json={"title": "Kept", "series_name": "Elsewhere", "volume_number": 4}).json()
        assert changed["id"] == book["id"] and changed["title"] == "Kept" and changed["favorite"]
        assert changed["progress"]["unit_index"] == 1 and changed["progress"]["offset"] == 0.25
        assert client.patch(f"/api/books/{book['id']}/state", json={"unit_index": 2}).status_code == 422
        assert client.patch(f"/api/books/{book['id']}/state", json={"offset": 1.1}).status_code == 422


def test_books_filters_checkpoint_reset_metadata_clears_and_shelf_order(api_client, db_factory) -> None:
    owner = _user(db_factory, "filters")
    with api_client(user=owner, base_url="http://localhost") as client:
        epub = client.put("/api/books/import?filename=novel.epub", content=_epub()).json()
        first = client.put("/api/books/import?filename=Zulu.cbz", content=_cbz(title="Zulu", number=1)).json()
        second = client.put("/api/books/import?filename=Alpha.cbz", content=_cbz(title="Alpha", number=2)).json()
        shelf = client.post("/api/books/shelves", json={"name": "Ordered"}).json()
        assert client.put(f"/api/books/shelves/{shelf['id']}/entries", json={"book_ids": [first['id'], second['id']]}).status_code == 200
        assert [item["id"] for item in client.get(f"/api/books?shelf_id={shelf['id']}&sort=custom").json()["items"]] == [first["id"], second["id"]]
        assert [item["id"] for item in client.get(f"/api/books?shelf_id={shelf['id']}&sort=title").json()["items"]] == [second["id"], first["id"]]
        assert client.get("/api/books?q=pha").json()["items"][0]["id"] == second["id"]
        favorite = client.patch(f"/api/books/{second['id']}/state", json={"favorite": True, "reading_list": True}).json()
        assert favorite["progress"]["updated_at"] is None
        assert [item["id"] for item in client.get("/api/books?status=favorites").json()["items"]] == [second["id"]]
        assert {item["id"] for item in client.get("/api/books?status=unread").json()["items"]} >= {first["id"], second["id"]}
        checkpoint = client.patch(f"/api/books/{second['id']}/state", json={"unit_index": 0, "offset": 0}).json()
        assert checkpoint["progress"]["updated_at"] is not None
        assert [item["id"] for item in client.get("/api/books?status=reading").json()["items"]] == [second["id"]]
        series = client.get("/api/books/series?status=reading&sort=author").json()["items"]
        assert [item["next_book_id"] for item in series] == [second["id"]]
        assert client.get("/api/books/series?q=zulu").json()["items"][0]["volume_count"] == 2
        overview = client.get("/api/books/overview").json()
        assert overview["totals"] == {"ebooks": 1, "manga_series": 1, "volumes": 2}
        assert [item["id"] for item in overview["continue_reading"]] == [second["id"]]
        reset = client.patch(f"/api/books/{second['id']}/state", json={"completed": False, "unit_index": 0, "offset": 0}).json()
        assert reset["progress"]["updated_at"] is None
        assert client.get("/api/books?status=reading").json()["items"] == []
        cleared = client.patch(f"/api/books/{second['id']}", json={"author": None, "series_name": None, "volume_number": None}).json()
        assert cleared["id"] == second["id"] and cleared["author"] == "" and cleared["series_name"] is None and cleared["volume_number"] is None
        assert client.patch(f"/api/books/{second['id']}", json={"title": None}).status_code == 422
        assert client.patch(f"/api/books/{epub['id']}", json={"series_name": "Not manga"}).status_code == 200
        assert all(item["title"] != "Not manga" for item in client.get("/api/books/series").json()["items"])
        page = client.get(f"/api/books/{first['id']}/pages/-1")
        assert page.status_code == 404
        asset = _cover(client, f"/api/books/{first['id']}/cover")
        assert asset.headers["cache-control"] == "private, no-store"


def test_rejected_large_archive_and_download_restriction_leave_no_import(api_client, db_factory, monkeypatch) -> None:
    from app.services.books import book_root
    from app.services import book_archives

    denied = _user(db_factory, "no-download")
    with db_factory() as db:
        db.add(MemberAccess(user_id=denied.id, sections=["books"], streaming={}, can_download=False)); db.commit()
    with api_client(user=denied, base_url="http://localhost") as client:
        assert client.put("/api/books/import?filename=denied.cbz", content=_cbz()).status_code == 403
    with db_factory() as db:
        assert db.query(BookItem).filter(BookItem.owner_user_id == denied.id).count() == 0
    assert list(book_root().glob(".upload-*")) == []

    owner = _user(db_factory, "archive-limit")
    monkeypatch.setattr(book_archives, "MAX_IMAGE_BYTES", 80)
    oversized = io.BytesIO()
    with zipfile.ZipFile(oversized, "w") as archive:
        archive.writestr("001.png", b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    with api_client(user=owner, base_url="http://localhost") as client:
        assert client.put("/api/books/import?filename=oversized.cbz", content=oversized.getvalue()).status_code == 422
    with db_factory() as db:
        assert db.query(BookItem).filter(BookItem.owner_user_id == owner.id).count() == 0
    assert list(book_root().glob(".upload-*")) == []
    assert list(book_root().glob("*.cbz")) == []


def test_plain_cbz_uses_its_filename_and_still_appears_in_manga(api_client, db_factory) -> None:
    owner = _user(db_factory, "plain")
    with api_client(user=owner, base_url="http://localhost") as client:
        book = client.put("/api/books/import?filename=Scanned volume.cbz", content=_plain_cbz()).json()
        assert book["title"] == "Scanned volume" and book["series_name"] is None and book["series_id"] == book["id"]
        series = client.get("/api/books/series?q=scanned").json()["items"]
        assert [(item["title"], item["volume_count"], item["next_book_id"] ) for item in series] == [("Scanned volume", 1, book["id"])]
        reader = client.get(f"/api/books/{book['id']}/reader").json()
        assert reader["page_count"] == 3 and client.get(f"/api/books/{book['id']}/pages/0").status_code == 200


def test_books_upload_cap_is_route_specific() -> None:
    assert body_limit({"method": "PUT", "path": "/api/books/import"}) == MAX_BOOK_UPLOAD_BODY_BYTES
    assert body_limit({"method": "PUT", "path": "/api/books/not-import"}) < MAX_BOOK_UPLOAD_BODY_BYTES
