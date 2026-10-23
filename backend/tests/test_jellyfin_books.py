"""Native reading retains the Books service's format, visibility and member-state contracts."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.book_models import BookItem
from app.main import app
from app.models import AppSettings, MemberAccess, User
from app.services.books import BookService, book_root
from test_books_api import _cbz, _epub
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, jellyfin_household, mediabrowser


def test_native_books_auth_visibility_resources_and_member_progress(tmp_path):
    jellyfin_household(tmp_path / "media")
    with db_module.SessionLocal.begin() as db:
        owner = db.get(User, ALICE)
        root = book_root(); root.mkdir(parents=True, exist_ok=True)
        ids = []
        for name, body in [("native.cbz", _cbz(pages=4)), ("native.epub", _epub())]:
            staged = tmp_path / name; staged.write_bytes(body)
            service = BookService(db)
            format_, manifest = service.inspect_staged(staged, name)
            book = service.import_staged(staged, name, owner, format_=format_, manifest=manifest)
            ids.append(book.id)
        cbz, epub = ids
        shelf = service.create_shelf(owner, {"name": "Night shelf", "visibility": "private"})
        service.replace_shelf_entries(shelf.id, owner, list(reversed(ids)))
        shelf_id = shelf.id
    base = "/Lumina/Books"
    client = TestClient(app, base_url="http://localhost")
    try:
        client.headers.update(mediabrowser(ALICE_TOKEN))
        catalog = client.get(base)
        assert catalog.status_code == 200, catalog.text
        assert {row["id"] for row in catalog.json()["items"]} == set(ids)
        assert [row["id"] for row in client.get(base, params={"shelf_id": shelf_id}).json()["items"]] == list(reversed(ids))
        overview = client.get(base + "/overview").json()
        assert overview["shelves"][0]["book_ids"] == list(reversed(ids))
        series = client.get(base + "/series").json()["items"]
        assert len(series) == 1 and series[0]["volume_count"] == 1
        assert client.get(f"{base}/series/{series[0]['id']}").json()["books"][0]["id"] == cbz
        assert client.get(base + "/overview", headers=mediabrowser(BOB_TOKEN)).json()["shelves"] == []
        assert client.get(base + "/series", headers=mediabrowser(BOB_TOKEN)).json()["items"] == []
        assert client.get(f"{base}/series/{series[0]['id']}", headers=mediabrowser(BOB_TOKEN)).status_code == 404
        assert client.get(base, params={"shelf_id": shelf_id}, headers=mediabrowser(BOB_TOKEN)).json()["items"] == []
        assert client.get(base, headers=mediabrowser("revoked-token")).status_code == 401
        assert client.get(base, headers=mediabrowser(BOB_TOKEN)).json()["items"] == []
        for path in [f"/{cbz}/reader", f"/{cbz}/pages/0", f"/{epub}/chapters/0", f"/{epub}/resources?member=OEBPS/cover.png"]:
            assert client.get(base + path, headers=mediabrowser(BOB_TOKEN)).status_code == 404
        manifest = client.get(f"{base}/{cbz}/reader").json()
        assert manifest["page_count"] == 4
        page = client.get(f"{base}/{cbz}/pages/2")
        assert page.status_code == 200 and page.headers["content-type"] == "image/png"
        assert page.headers["cache-control"] == "private, no-store"
        assert client.get(f"{base}/{cbz}/pages/4").status_code == 404
        assert client.get(f"{base}/{epub}/chapters/0").json()["nodes"]
        assert client.get(f"{base}/{epub}/resources", params={"member": "OEBPS/cover.png"}).status_code == 200
        assert client.get(f"{base}/{epub}/resources", params={"member": "../secret"}).status_code == 422
        for unit, offset in [(3, 0), (1, 0.2)]:
            saved = client.patch(f"{base}/{cbz}/state", json={"unit_index": unit, "offset": offset})
            assert saved.status_code == 200, saved.text
            assert saved.json()["progress"]["unit_index"] == unit
        filtered = client.get(base, params={"kind": "manga", "status": "reading", "sort": "title"})
        assert filtered.status_code == 200, filtered.text
        assert [row["id"] for row in filtered.json()["items"]] == [cbz]
        assert client.get(f"{base}/{cbz}/reader").json()["book"]["progress"]["offset"] == 0.2
        with db_module.SessionLocal.begin() as db:
            db.get(BookItem, cbz).visibility = "shared"
        assert client.get(f"{base}/{cbz}/reader", headers=mediabrowser(BOB_TOKEN)).json()["book"]["progress"]["unit_index"] == 0
        with db_module.SessionLocal.begin() as db:
            db.add(MemberAccess(user_id=BOB, sections=["movies"], streaming={}, can_download=True))
        assert client.get(f"{base}/{cbz}/reader", headers=mediabrowser(BOB_TOKEN)).status_code == 404
        with db_module.SessionLocal.begin() as db:
            db.scalar(select(AppSettings)).jellyfin_enabled = False
        assert client.get(base).status_code == 404
    finally:
        client.close()
