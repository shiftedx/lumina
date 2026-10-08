"""Editor fields reach Jellyfin clients read-only, and search follows tags."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle
from app.services.jellyfin import title_metadata_fields
from app.services.library_search import title_search_fields
from app.services.media_titles import jellyfin_id
from title_support import ALICE_TOKEN, MOVIE, SERIES, jellyfin_household, mediabrowser


@pytest.fixture
def jf(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def fetch(client: TestClient, title_id: str) -> dict:
    response = client.get(f"/Items/{jellyfin_id(title_id)}", headers=mediabrowser(ALICE_TOKEN))
    assert response.status_code == 200
    return response.json()


def edit(title_id: str, *, meta: dict | None = None, sources: dict | None = None, locked: bool = False) -> None:
    with db_module.SessionLocal() as session:
        title = session.get(MediaTitle, title_id)
        title.metadata_json = {**(title.metadata_json or {}), **(meta or {})}
        title.field_sources = sources or {}
        title.locked = locked
        session.commit()


def test_new_fields_reach_the_dto(jf: TestClient) -> None:
    edit(MOVIE, meta={"original_title": "Dune", "tags": ["kids"], "critic_rating": 88, "custom_rating": "R-ish"})
    edit(SERIES, meta={"air_days": ["Monday"], "air_time": "21:30"})
    movie, series = fetch(jf, MOVIE), fetch(jf, SERIES)
    assert (movie["OriginalTitle"], movie["Tags"], movie["CriticRating"], movie["CustomRating"]) == ("Dune", ["kids"], 88, "R-ish")
    assert (series["AirDays"], series["AirTime"]) == (["Monday"], "21:30")


def test_lock_data_and_locked_fields(jf: TestClient) -> None:
    keys = ("overview", "genres", "people", "studios", "tags", "runtime_minutes", "name", "official_rating", "year")
    edit(MOVIE, sources=dict.fromkeys(keys, "user"))
    dto = fetch(jf, MOVIE)
    assert dto["LockData"] is False
    assert dto["LockedFields"] == ["Cast", "Genres", "Name", "OfficialRating", "Overview", "Runtime", "Studios", "Tags"]
    edit(MOVIE, locked=True)
    assert fetch(jf, MOVIE)["LockData"] is True
    edit(SERIES)
    assert fetch(jf, SERIES)["LockedFields"] == []


def test_cleared_fields_are_omitted(jf: TestClient) -> None:
    edit(MOVIE, meta={"genres": None, "tags": None, "overview": None}, sources={"genres": "user", "tags": "user", "overview": "user"})
    dto = fetch(jf, MOVIE)
    assert not dto.get("Genres") and dto["Tags"] == [] and "Overview" not in dto  # apps dereference Tags unchecked


def test_jellyfin_still_accepts_no_writes(jf: TestClient) -> None:
    headers = mediabrowser(ALICE_TOKEN)
    item = jellyfin_id(MOVIE)
    for call in (jf.post(f"/Items/{item}", json={}, headers=headers), jf.post(f"/Items/{item}/Images/Primary", content=b"x", headers=headers),
                 jf.delete(f"/Items/{item}/Images/Primary", headers=headers)):
        assert call.status_code in (404, 405)


def test_edited_values_show_on_the_next_fetch(jf: TestClient) -> None:
    edit(MOVIE, meta={"overview": "one"})
    assert fetch(jf, MOVIE)["Overview"] == "one"
    edit(MOVIE, meta={"overview": "two"})
    assert fetch(jf, MOVIE)["Overview"] == "two"


def test_tags_are_appended_to_the_genres_column() -> None:
    title = MediaTitle(id="t", type="movie", key="k", root_id="r", name="N", metadata_json={"genres": ["Drama", "Noir"], "tags": ["kids"]})
    assert title_search_fields(title, lambda _id: None)["genres"] == "Drama Noir kids"
    title.metadata_json = {"genres": ["Drama"], "tags": "kids"}
    assert title_search_fields(title, lambda _id: None)["genres"] == "Drama"


def test_title_metadata_fields_ignores_wrong_types() -> None:
    title = MediaTitle(id="t", type="movie", key="k", root_id="r", name="N", metadata_json={"tags": "x", "air_days": [1], "critic_rating": True}, field_sources={})
    out = title_metadata_fields(title)
    assert not {"Tags", "AirDays", "CriticRating"} & set(out)
