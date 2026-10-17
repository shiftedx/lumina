"""Metadata editor catalogue and history for the music title layer."""
from __future__ import annotations

import pytest

from app.models import MediaTitle, TitleEdit, User
from app.services import library_search, media_titles, metadata_bulk as mb, metadata_editor as me, metadata_history as mh
from metadata_support import ADMIN, t1_seam  # noqa: F401
from support import make_user, seed_app_settings
from title_support import ALBUM, ALICE, ARTIST, seed_gallery, seed_tree


@pytest.fixture
def music_world(db_factory, tmp_path, api_client):  # noqa: ANN001, ANN201
    root = (tmp_path / "media").resolve()
    with db_factory() as db:
        db.add_all([make_user(ADMIN, role="admin", username="admin"), make_user(ALICE, username="alice")])
        seed_tree(db, root)
        seed_gallery(db, root)
        seed_app_settings(db)
        library_search.ensure_search_index(db.get_bind())
        db.get(MediaTitle, ARTIST).images = {
            "Primary": {"path": "Music/Artist A/folder.jpg", "tag": "artist"},
            "Backdrop": {"path": "Music/Artist A/backdrop.jpg", "tag": "backdrop"},
        }
        db.commit()

    def client_for(user_id: str):  # noqa: ANN202
        with db_factory() as db:
            return api_client(user=db.get(User, user_id), base_url="http://localhost")

    return db_factory, client_for


def edit(client, title_id: str, changes: dict, **extra):  # noqa: ANN001, ANN201
    return client.post("/api/metadata/edits", json={"edits": [{"title_id": title_id, "changes": changes, **extra}]})


def test_album_and_artist_documents_expose_music_fields_and_primary_art(music_world) -> None:  # noqa: ANN001
    _factory, client_for = music_world
    album = client_for(ADMIN).get(f"/api/titles/{ALBUM}/metadata").json()
    artist = client_for(ADMIN).get(f"/api/titles/{ARTIST}/metadata").json()

    assert album["type"] == "album" and album["parent"] == {"id": ARTIST, "type": "artist", "name": "Artist A"}
    assert artist["type"] == "artist" and artist["parent"] is None
    assert {"name", "sort_name", "overview", "genres", "tags", "added_at", "provider_ids"} <= album["fields"].keys()
    assert {"release_type", "edition", "label", "catalogue_number", "country", "original_year", "mood", "styles"} <= album["fields"].keys()
    assert {"name", "sort_name", "overview", "genres", "tags", "added_at", "provider_ids", "country", "mood", "styles",
            "origin", "active_since", "active_until"} <= artist["fields"].keys()
    assert not {"release_type", "original_year", "index_number", "runtime_minutes"} & artist["fields"].keys()
    assert [image["type"] for image in album["images"]] == ["Primary"]
    assert [image["type"] for image in artist["images"]] == ["Primary"]


@pytest.mark.parametrize("release_type", me.RELEASE_TYPES)
def test_album_catalogue_accepts_release_types_and_musicbrainz_uuid(release_type: str) -> None:
    musicbrainz = "f9f0c2e0-5e2a-4a4b-9c7d-4f7a3c2e1b00"
    assert me.validate("album", "release_type", release_type) == release_type
    assert me.validate("album", "provider_ids", {"MusicBrainz": musicbrainz, "Tmdb": "603"})["MusicBrainz"] == musicbrainz


@pytest.mark.parametrize(("type_", "field", "value"), [
    ("album", "release_type", "mixtape"),
    ("album", "provider_ids", {"MusicBrainz": "not-a-uuid"}),
    ("artist", "provider_ids", {"MusicBrainz": "f9f0c2e0-5e2a-4a4b-9c7d"}),
    ("artist", "release_type", "album"),
    ("album", "active_since", "1990"),
    ("artist", "year", 1990),
    ("album", "runtime_minutes", 40),
    ("artist", "index_number", 1),
])
def test_music_catalogue_rejects_invalid_values_and_video_only_fields(type_: str, field: str, value) -> None:  # noqa: ANN001
    with pytest.raises(me.FieldError):
        me.validate(type_, field, value)


def test_artist_active_years_are_bounded_and_ordered(music_world) -> None:  # noqa: ANN001
    assert me.validate("artist", "active_since", "1984-05") == "1984-05"
    with pytest.raises(me.FieldError):
        me.validate("artist", "active_until", "2201")
    for field in ("mood", "styles"):
        with pytest.raises(me.FieldError):
            me.validate("artist", field, [f"value-{i}" for i in range(31)])

    factory, _client_for = music_world
    with factory() as db:
        with pytest.raises(me.FieldError) as raised:
            me.save_edits(db, db.get(User, ADMIN), [me.EditEntry(
                title_id=ARTIST,
                changes={"active_since": me.Change("2000"), "active_until": me.Change("1999")},
            )], owner=True)
        assert (raised.value.field, raised.value.reason) == ("active_until", "end_before_start")


def test_music_field_edit_keeps_source_for_revert_and_locks_the_field(music_world) -> None:  # noqa: ANN001
    factory, client_for = music_world
    with factory() as db:
        media_titles.apply_field(db.get(MediaTitle, ALBUM), "overview", "NFO album", "nfo")
        db.commit()

    admin = client_for(ADMIN)
    saved = edit(admin, ALBUM, {"overview": {"value": "Household album", "base": "NFO album"}})
    assert saved.status_code == 200
    assert saved.json()["titles"][0]["fields"]["overview"] == {
        "value": "Household album", "source": "user", "locked": True, "kept": {"source": "nfo", "value": "NFO album"},
    }

    with factory() as db:
        title = db.get(MediaTitle, ALBUM)
        assert media_titles.apply_field(title, "overview", "TMDB album", "tmdb") is False
        db.commit()
    reverted = admin.post(f"/api/titles/{ALBUM}/metadata/revert", json={"fields": ["overview"]})
    assert reverted.status_code == 200
    assert reverted.json()["title"]["fields"]["overview"]["value"] == "NFO album"
    assert reverted.json()["title"]["fields"]["overview"]["source"] == "nfo"


def test_music_image_pin_allows_primary_but_rejects_video_art_roles(music_world) -> None:  # noqa: ANN001
    _factory, client_for = music_world
    admin = client_for(ADMIN)
    saved = edit(admin, ARTIST, {}, pin=["images.Primary"])
    assert saved.status_code == 200 and saved.json()["titles"][0]["images"][0]["locked"] is True
    rejected = edit(admin, ARTIST, {}, pin=["images.Backdrop"])
    assert rejected.status_code == 422 and rejected.json()["field"] == "images.Backdrop"


def test_music_conflicts_are_optimistic_and_partial(music_world) -> None:  # noqa: ANN001
    _factory, client_for = music_world
    admin = client_for(ADMIN)
    result = admin.post("/api/metadata/edits", json={"edits": [
        {"title_id": ALBUM, "changes": {"name": {"value": "New album", "base": "stale"}}},
        {"title_id": ARTIST, "changes": {"origin": {"value": "Canada"}}},
    ]})
    assert result.status_code == 200
    assert [title["title_id"] for title in result.json()["titles"]] == [ARTIST]
    assert result.json()["conflicts"][0]["title_id"] == ALBUM

    again = edit(admin, ALBUM, {"name": {"value": "Another album", "base": "stale"}})
    assert again.status_code == 409 and again.json()["titles"] == []


def test_bulk_genre_and_style_edits_and_music_history_undo(music_world) -> None:  # noqa: ANN001
    factory, _client_for = music_world
    with factory() as db:
        user = db.get(User, ADMIN)
        result = mb.bulk_edit(db, user, [ALBUM, ARTIST], [
            {"op": "add", "field": "genres", "values": ["Electronic"]},
            {"op": "add", "field": "styles", "values": ["Dream pop"]},
        ], owner=True)
        assert result["applied"] == 2 and result["skipped"] == []
        assert db.get(MediaTitle, ALBUM).metadata_json["styles"] == ["Dream pop"]
        assert db.get(MediaTitle, ARTIST).metadata_json["styles"] == ["Dream pop"]
        rows = db.query(TitleEdit).filter(TitleEdit.batch_id == result["batch_id"]).all()
        assert {row.field for row in rows} == {"genres", "styles"}

        undone = mh.undo(db, user, result["batch_id"], owner=True)
        assert undone["restored"] == 4 and undone["skipped"] == []
        assert db.get(MediaTitle, ALBUM).metadata_json.get("styles") is None
        assert db.get(MediaTitle, ARTIST).metadata_json.get("styles") is None
