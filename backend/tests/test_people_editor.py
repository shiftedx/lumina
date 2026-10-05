"""#164 (c): the household people editor. A rename or photo applies on every title that credits the person, survives
refreshes (read-time overlay), is found by search, and is one undoable history batch."""
from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.models import AppSettings, MediaTitle, PersonOverride, User, utcnow
from app.services import cast_photos, library_search, title_images, title_metadata
from app.services.media_titles import person_name_id
from app.services.title_images import Encoded
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, MOVIE, S1E1, SECRET_EPISODE, seed_tree

ADMIN = "admin-1"
KEANU = person_name_id("Keanu Reevs")
GHOST = person_name_id("Ghost")
PHOTO = b"\x89PNG\r\n\x1a\nkeanu"


@pytest.fixture
def env(db_factory, tmp_path, api_client, monkeypatch):  # noqa: ANN001, ANN201
    with db_factory() as s:
        s.add_all([make_user(ADMIN, role="admin", username="admin"), make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(s, tmp_path.resolve() / "media")
        seed_app_settings(s)
        for title_id, person_id, name in ((MOVIE, KEANU, "Keanu Reevs"), (S1E1, KEANU, "Keanu Reevs"), (SECRET_EPISODE, GHOST, "Ghost")):
            title = s.get(MediaTitle, title_id)
            title.metadata_json = {**title.metadata_json, "people": [{"person_id": person_id, "name": name, "role": None, "type": "Actor"}]}
        s.commit()
        library_search.ensure_search_index(s.get_bind())
        for title_id in (MOVIE, S1E1):
            library_search.index_title(s, s.get(MediaTitle, title_id))
        s.commit()
    monkeypatch.setattr(title_images, "encode", lambda data, image_type, *, ffmpeg: Encoded(
        hashlib.sha256(data).hexdigest(), "image/jpeg", 200, 300, data))

    def client(user_id: str = ADMIN):  # noqa: ANN202
        with db_factory() as s:
            return api_client(user=s.get(User, user_id), base_url="http://localhost")

    return client, db_factory


def credited_names(factory) -> list[str]:  # noqa: ANN001
    with factory() as s:
        return [p.name for title_id in (MOVIE, S1E1) for p in title_metadata.title_people(s, s.get(MediaTitle, title_id))]


def test_rename_everywhere_search_and_undo(env) -> None:  # noqa: ANN001
    client, factory = env
    admin = client()
    doc = admin.get(f"/api/metadata/people/{KEANU}").json()
    assert (doc["name"], doc["source_name"], doc["name_edited"], doc["title_count"]) == ("Keanu Reevs", "Keanu Reevs", False, 2)
    r = admin.put(f"/api/metadata/people/{KEANU}", json={"name": "  Keanu Reeves "})
    assert r.status_code == 200 and r.json()["person"]["name"] == "Keanu Reeves" and r.json()["person"]["name_edited"]
    batch = r.json()["batch_id"]
    assert credited_names(factory) == ["Keanu Reeves", "Keanu Reeves"]
    with factory() as s:
        hits = s.execute(text(f"SELECT title_id FROM {library_search.TITLE_FTS_TABLE} WHERE {library_search.TITLE_FTS_TABLE} MATCH 'Reeves'")).scalars()
        assert set(hits) == {MOVIE, S1E1}
    assert admin.get("/api/metadata/people", params={"q": "keanu"}).json()[0]["name"] == "Keanu Reeves"
    assert admin.put(f"/api/metadata/people/{KEANU}", json={"name": "Keanu Reeves"}).json()["batch_id"] is None
    assert admin.post(f"/api/metadata/batches/{batch}/undo").json()["restored"] == 1
    assert credited_names(factory) == ["Keanu Reevs", "Keanu Reevs"]
    with factory() as s:
        assert s.get(PersonOverride, KEANU) is None
    assert admin.put(f"/api/metadata/people/{KEANU}", json={"name": ""}).status_code == 422


def test_photo_upload_serves_everywhere_survives_gc_and_reverts(env) -> None:  # noqa: ANN001
    client, factory = env
    admin = client()
    r = admin.put(f"/api/metadata/people/{KEANU}/photo", content=PHOTO)
    assert r.status_code == 200 and r.json()["person"]["photo_edited"]
    sha = hashlib.sha256(PHOTO).hexdigest()
    with factory() as s:
        assert cast_photos.image_urls(s, {KEANU})[KEANU] == cast_photos.photo_url(KEANU, sha)
        person = cast_photos.subject(s, KEANU)
        assert person.images == {"Primary": {"upload": sha}}
        assert cast_photos.image_bytes(person, None, s) == ("image/jpeg", PHOTO)
        title_images.gc_uploads(s, utcnow() + timedelta(days=1))
        s.commit()
    removed = admin.delete(f"/api/metadata/people/{KEANU}/photo")
    assert removed.status_code == 200 and not removed.json()["person"]["photo_edited"]
    assert admin.post(f"/api/metadata/batches/{removed.json()['batch_id']}/undo").json()["restored"] == 1
    with factory() as s:
        title_images.gc_uploads(s, utcnow() + timedelta(days=1))  # the history row still holds it
        s.commit()
        assert cast_photos.image_bytes(cast_photos.subject(s, KEANU), None, s) == ("image/jpeg", PHOTO)


def test_permissions_and_visibility(env) -> None:  # noqa: ANN001
    client, factory = env
    assert client(ALICE).get(f"/api/metadata/people/{KEANU}").json() == {"detail": "editing_not_allowed"}
    with factory() as s:
        s.get(AppSettings, 1).members_edit_metadata = True
        s.commit()
    alice = client(ALICE)
    assert alice.get(f"/api/metadata/people/{KEANU}").status_code == 200
    for request in (alice.get(f"/api/metadata/people/{GHOST}"), alice.put(f"/api/metadata/people/{GHOST}", json={"name": "x"}),
                    alice.put(f"/api/metadata/people/{GHOST}/photo", content=PHOTO), alice.get("/api/metadata/people/nope")):
        assert request.status_code == 404
    assert client(BOB).get(f"/api/metadata/people/{GHOST}").status_code == 200


def test_jellyfin_people_carry_the_household_name_and_photo(tmp_path) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import TitleUpload
    from app.services.media_titles import jellyfin_id, synthetic_id
    from title_support import ALICE_TOKEN, jellyfin_household, mediabrowser

    jellyfin_household(tmp_path.resolve() / "media")
    tmdb_person = synthetic_id("tmdb-person:6384")
    sha = hashlib.sha256(PHOTO).hexdigest()
    with SessionLocal() as s:
        movie = s.get(MediaTitle, MOVIE)
        movie.metadata_json = {**movie.metadata_json, "people": [{"person_id": tmdb_person, "name": "Keanu", "role": "Neo", "type": "Actor"}]}
        s.add_all([PersonOverride(id=tmdb_person, name="Keanu Reeves", photo=sha),
                   TitleUpload(sha256=sha, content_type="image/jpeg", width=200, height=300, data=PHOTO)])
        s.commit()
    jf = TestClient(app, base_url="http://localhost")  # no lifespan: conftest's database is already seeded
    dto = jf.get(f"/Items/{jellyfin_id(MOVIE)}", headers=mediabrowser(ALICE_TOKEN)).json()
    [person] = dto["People"]
    assert (person["Name"], person["Id"]) == ("Keanu Reeves", jellyfin_id(tmdb_person)) and person["PrimaryImageTag"]
    image = jf.get(f"/Items/{person['Id']}/Images/Primary", params={"tag": person["PrimaryImageTag"]})
    assert image.status_code == 200 and image.content
