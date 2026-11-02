"""The browser receives current, member-scoped destination availability."""
from app.book_models import BookItem
from app.models import AppSettings, MemberAccess, RequestPolicy, StorageRoot
from app.photo_models import PhotoItem
from app.photo_root_models import PhotoImportSource
from test_category_walls import walls  # noqa: F401
from title_support import ALICE, BOB, ROOT, add_file, uid


def test_navigation_reflects_sources_features_and_member_access(walls):  # noqa: ANN001, F811
    first = walls.get("/api/navigation")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "categories": ["movies", "tv", "anime", "music", "youtube"],
        "features": {"streaming": False, "requests": False},
    }
    with walls.db() as db:
        db.add(AppSettings(id=1, temp_root="/tmp", archive_path="/tmp/archive", native_streaming_enabled=True, requests_enabled=True))
        db.add(MemberAccess(user_id=BOB, sections=["shows"], streaming={"youtube": False, "twitch": False, "kick": False}))
        db.add_all([RequestPolicy(id=f"deny-{kind}", user_id=BOB, kind=kind, can_request=False) for kind in ("movie", "show", "anime")])
        db.add(BookItem(id="book", storage_key="book.epub", format="epub", kind="ebook", title="Book", owner_user_id=ALICE, visibility="private"))
        db.add(PhotoImportSource(id="photo-source", root_id=ROOT, owner_user_id=ALICE, relative_folder=""))
        db.add(PhotoItem(id="external-photo", owner_user_id=ALICE, visibility="private", storage_kind="external", root_id=ROOT, source_id="photo-source", storage_key="external-photo", original_filename="external.jpg", content_type="image/jpeg", sha256="b" * 64, bytes=1, width=1, height=1))
        db.add(PhotoItem(id="photo", owner_user_id=ALICE, visibility="private", storage_kind="managed", storage_key="photo", original_filename="photo.jpg", content_type="image/jpeg", sha256="a" * 64, bytes=1, width=1, height=1))
        db.commit()
    alice = walls.get("/api/navigation").json()
    bob = walls.get("/api/navigation", member="bob").json()
    assert alice == {"categories": ["movies", "tv", "anime", "music", "books", "youtube", "photos"], "features": {"streaming": True, "requests": True}}
    assert bob == {"categories": ["tv", "youtube"], "features": {"streaming": False, "requests": False}}
    with walls.db() as db:
        db.get(StorageRoot, ROOT).observation = {"state": "offline"}
        db.commit()
    assert walls.get("/api/navigation").json() == alice
    with walls.db() as db:
        db.get(StorageRoot, ROOT).enabled = False
        db.get(PhotoImportSource, "photo-source").enabled = False
        db.get(BookItem, "book").available = False
        db.get(PhotoItem, "photo").missing_at = db.get(PhotoItem, "photo").created_at
        db.commit()
    assert walls.get("/api/navigation").json() == {"categories": [], "features": {"streaming": True, "requests": True}}


def test_navigation_requires_authentication(api_client):  # noqa: ANN001
    assert api_client(base_url="http://localhost").get("/api/navigation").status_code == 401


def test_saved_recordings_do_not_require_streaming(walls):  # noqa: ANN001, F811
    with walls.db() as db:
        add_file(db, walls.root, "recordings/saved.mkv", item_id=uid(998), title_id=None,
                 owner=ALICE, visibility="private", title="Recording", kind="recording")
        db.commit()
    assert "recordings" in walls.get("/api/navigation").json()["categories"]
    assert "recordings" not in walls.get("/api/navigation", member="bob").json()["categories"]
    assert walls.get("/api/navigation").json()["features"]["streaming"] is False
