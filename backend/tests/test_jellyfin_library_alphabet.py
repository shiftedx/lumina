"""Indexed native-library alphabet anchors stay member-scoped and compact."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle, MemberFavorite
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, ROOT, add_file, jellyfin_household, mediabrowser, seed_gallery, uid


PATH = "/Lumina/Library/alphabet"
NAME_WALL = {
    "IncludeItemTypes": "Movie,Series,Video",
    "Recursive": "true",
    "SortBy": "SortName",
    "SortOrder": "Ascending",
}


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    with db_module.SessionLocal.begin() as db:
        seed_gallery(db, tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def alphabet(client: TestClient, token: str, **params):  # noqa: ANN201
    response = client.get(PATH, params=NAME_WALL | params, headers=mediabrowser(token))
    assert response.status_code == 200, response.text
    return response.json()


def items(client: TestClient, token: str, **params):  # noqa: ANN201
    response = client.get("/Items", params=NAME_WALL | {"Limit": 100} | params, headers=mediabrowser(token))
    assert response.status_code == 200, response.text
    return response.json()


def expected_anchors(page: dict) -> dict:
    anchors: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for index, item in enumerate(page["Items"]):
        first = (item.get("SortName") or item["Name"])[0].upper()
        letter = first if "A" <= first <= "Z" else "#"
        if letter not in seen:
            anchors.append({"letter": letter, "index": index})
            seen.add(letter)
    return {"anchors": anchors, "total": page["TotalRecordCount"]}


def test_native_alphabet_uses_compact_member_visible_absolute_indexes(native: TestClient) -> None:
    alice = alphabet(native, ALICE_TOKEN)
    assert alice == {
        "anchors": [{"letter": "F", "index": 0}, {"letter": "M", "index": 1}, {"letter": "S", "index": 2}],
        "total": 4,
    }

    # Bob can see one additional private title; Alice's compact response neither
    # names it nor includes its count.
    bob = alphabet(native, BOB_TOKEN)
    assert bob == {
        "anchors": [{"letter": "F", "index": 0}, {"letter": "M", "index": 1}, {"letter": "S", "index": 2}],
        "total": 5,
    }

    movies = alphabet(native, ALICE_TOKEN, IncludeItemTypes="Movie")
    assert movies == {"anchors": [{"letter": "F", "index": 0}, {"letter": "M", "index": 1}], "total": 2}


def test_native_alphabet_refuses_non_name_search_and_unauthenticated_catalogs(native: TestClient) -> None:
    assert native.get(PATH, params=NAME_WALL).status_code == 401
    assert native.get(PATH, params=NAME_WALL | {"SearchTerm": "Movie"}, headers=mediabrowser(ALICE_TOKEN)).status_code == 400
    assert native.get(PATH, params=NAME_WALL | {"SortOrder": "Descending"}, headers=mediabrowser(ALICE_TOKEN)).status_code == 400


def test_native_alphabet_exactly_matches_items_scopes_visibility_and_absolute_order(tmp_path: Path) -> None:
    """The compact index has the exact title predicate and order of its matching /Items wall."""
    root = tmp_path / "media"
    jellyfin_household(root)
    with db_module.SessionLocal.begin() as db:
        seed_gallery(db, root)

        def movie(number: int, name: str, *, sort_name: str | None = None, owner: str = ALICE,
                  visibility: str = "shared", year: int = 2042, genres: list[str] | None = None) -> str:
            title_id, item_id = uid(number), uid(number + 1000)
            db.add(MediaTitle(
                id=title_id, type="movie", key=f"{ROOT}:alphabet/{number}", root_id=ROOT,
                name=name, sort_name=sort_name, category="movies", year=year,
                metadata_json={"genres": genres or []},
            ))
            add_file(db, root, f"alphabet/{number}.mp4", item_id=item_id, title_id=title_id,
                     owner=owner, visibility=visibility, title=name, kind="movie")
            return title_id

        favorite = movie(401, "Alpha Favorite", genres=["Drama"])
        movie(402, "Zephyr one", sort_name="Zephyr", genres=["Drama"])
        movie(403, "Zephyr two", sort_name="Zephyr", genres=["Drama"])
        movie(404, "9 Private", owner=BOB, visibility="private", genres=["Drama"])
        db.add(MemberFavorite(user_id=ALICE, target_id=favorite))

    client = TestClient(app, base_url="http://localhost")
    try:
        # Genre/year/favourite/unwatched filters are all delegated to the same SQL predicate as /Items.
        filtered = {
            "IncludeItemTypes": "Movie", "Genres": "Drama", "Years": "2042",
            "IsFavorite": "true", "IsPlayed": "false",
        }
        assert alphabet(client, ALICE_TOKEN, **filtered) == expected_anchors(items(client, ALICE_TOKEN, **filtered))

        # Parent view and Anime category retain /Items' filtering and their absolute SortName positions.
        views = client.get("/UserViews", headers=mediabrowser(ALICE_TOKEN)).json()["Items"]
        shows = next(view for view in views if view.get("CollectionType") == "tvshows")
        for scope in (
            {"ParentId": shows["Id"], "IncludeItemTypes": "Series"},
            {"IncludeItemTypes": "Movie,Series", "LuminaCategory": "anime"},
        ):
            assert alphabet(client, ALICE_TOKEN, **scope) == expected_anchors(items(client, ALICE_TOKEN, **scope))

        alice_page = items(client, ALICE_TOKEN, IncludeItemTypes="Movie")
        bob_page = items(client, BOB_TOKEN, IncludeItemTypes="Movie")
        assert alphabet(client, ALICE_TOKEN, IncludeItemTypes="Movie") == expected_anchors(alice_page)
        assert alphabet(client, BOB_TOKEN, IncludeItemTypes="Movie") == expected_anchors(bob_page)
        assert "#" not in {anchor["letter"] for anchor in alphabet(client, ALICE_TOKEN, IncludeItemTypes="Movie")["anchors"]}
        assert alphabet(client, BOB_TOKEN, IncludeItemTypes="Movie")["anchors"][0] == {"letter": "#", "index": 0}

        # A SortName tie remains in the same deterministic id tie-break order and its anchor points at the first tie.
        ties = [item for item in alice_page["Items"] if item.get("SortName") == "Zephyr"]
        assert [item["Name"] for item in ties] == ["Zephyr one", "Zephyr two"]
        zed_index = next(index for index, item in enumerate(alice_page["Items"]) if item.get("SortName") == "Zephyr")
        assert next(anchor for anchor in alphabet(client, ALICE_TOKEN, IncludeItemTypes="Movie")["anchors"] if anchor["letter"] == "Z") == {
            "letter": "Z", "index": zed_index,
        }
    finally:
        client.close()
