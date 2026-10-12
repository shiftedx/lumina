"""Provenance reports one truthful origin, canonical addresses only, and permission-filtered related context."""
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import LibraryItem, LibraryItemArtifact, LibraryNote, MediaArtifact, StorageRoot, User
from app.routers.provenance import public_url
from app.security import get_current_user
from app.services.library import EXTERNAL_LIBRARY_ORIGIN


def _user(identifier: str) -> User:
    return User(id=identifier, username=identifier, display_name=identifier.title(), role="viewer", is_active=True)


def _item(identifier: str, owner: str, visibility: str = "shared", **fields) -> LibraryItem:
    return LibraryItem(id=identifier, user_id=owner, visibility=visibility, title=identifier.title(), metadata_json=fields.pop("meta", {}), status="available", **fields)


def test_provenance_origin_related_and_share() -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        db.add_all([
            _user("owner"), _user("other"),
            StorageRoot(id="lib", label="Library", path="/srv/private/lumina-library", mode="managed"),
            StorageRoot(id="nas", label="Family NAS", path="/mnt/nas/tv", mode="external"),
            _item("film", "owner", uploader="Alpine Films", playlist_name="Valleys", webpage_url="https://www.youtube.com/watch?v=abc", extractor="youtube",
                  meta={"extractor_key": "Youtube", "channel_url": "https://www.youtube.com/@alpine", "upload_date": "20250102"}),
            _item("sibling", "owner", uploader="Alpine Films"),
            _item("hidden", "other", visibility="private", uploader="Alpine Films", playlist_name="Valleys"),
            _item("episode", "owner", extractor=EXTERNAL_LIBRARY_ORIGIN, playlist_name="Valleys", webpage_url=None),
            MediaArtifact(id="a1", root_id="lib", relative_path="youtube/film.mp4", ownership="managed", size=1234,
                          probe={"fingerprint": "1:2", "container": "mov,mp4", "video_codec": "h264", "audio_codec": "aac", "width": 1920, "height": 1080}),
            LibraryItemArtifact(library_item_id="film", artifact_id="a1"),
            MediaArtifact(id="a2", root_id="nas", relative_path="Valleys/S01E01.mkv", ownership="external"),
            LibraryItemArtifact(library_item_id="episode", artifact_id="a2"),
            LibraryNote(id="n1", item_id="film", user_id="owner", visibility="private", body="mine"),
            LibraryNote(id="n2", item_id="film", user_id="other", visibility="private", body="theirs"),
        ])
    app.dependency_overrides[get_current_user] = lambda: _user("owner")
    try:
        http = TestClient(app, base_url="http://localhost")
        body = http.get("/api/library/film/provenance").json()
        # One video, one origin: a single provider and page, no invented multi-source blend.
        assert body["origin"] == "saved" and body["provider"] == "YouTube"  # S25 public label, not the raw extractor key
        assert body["original_url"] == "https://www.youtube.com/watch?v=abc" and body["channel_url"] == "https://www.youtube.com/@alpine"
        assert body["uploaded_on"] == "2025-01-02" and body["storage_label"] == "Library" and body["storage_mode"] == "managed"
        assert body["format"] == {"container": "mov,mp4", "video_codec": "h264", "audio_codec": "aac", "width": 1920, "height": 1080}
        assert body["notes_count"] == 1  # another member's private note is not counted
        # Hidden items never reach counts or titles.
        assert body["related"] == [
            {"reason": "same_channel", "name": "Alpine Films", "count": 1, "items": [{"id": "sibling", "title": "Sibling"}]},
            {"reason": "same_series", "name": "Valleys", "count": 1, "items": [{"id": "episode", "title": "Episode"}]},
        ]
        assert "/srv/" not in str(body) and "fingerprint" not in str(body)

        imported = http.get("/api/library/episode/provenance").json()
        assert imported["origin"] == "imported" and imported["provider"] is None and imported["original_url"] is None
        assert imported["storage_label"] == "Family NAS" and imported["storage_mode"] == "external" and imported["format"] is None
        assert "/mnt/" not in str(imported)

        assert http.get("/api/library/hidden/provenance").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_share_canonical_not_transport() -> None:
    assert public_url("https://example.test/watch?v=1") == "https://example.test/watch?v=1"
    for bad in ("file:///srv/media/a.mp4", "https://user:secret@example.test/a", "/srv/media/a.mp4", None, "javascript:alert(1)"):
        assert public_url(bad) is None
