"""Indexed native-library alphabet anchors stay member-scoped and compact."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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


def test_native_technical_filters_keep_one_visible_source_and_match_alphabet(tmp_path: Path) -> None:
    """Technical predicates never combine facts from separate versions or classify unknown probe data as SDR."""
    root = tmp_path / "media"
    jellyfin_household(root)

    def probe(*, height: int | None = 2160, transfer: str | None = "bt709", audio: str | None = "aac",
              profile: str | None = None, title: str | None = None, hdr10: bool = False, hdr10plus: bool = False) -> dict:
        width = 3840 if height and height >= 2000 else 1920 if height and height >= 1000 else 640 if height and height > 0 else None
        video = {"index": 0, "type": "video", "codec": "hevc", "width": width,
                 "height": height, "color_transfer": transfer, "hdr10": hdr10, "hdr10plus": hdr10plus}
        streams = [video]
        if audio:
            streams.append({"index": 1, "type": "audio", "codec": audio, "profile": profile, "title": title})
        return {"width": video["width"], "height": height, "streams": streams}

    with db_module.SessionLocal.begin() as db:
        def movie(number: int, name: str, path: str, facts: dict, **metadata: object) -> str:
            title_id, item_id = uid(number), uid(number + 1000)
            db.add(MediaTitle(id=title_id, type="movie", key=f"{ROOT}:technical/{number}", root_id=ROOT, name=name,
                              category="movies", metadata_json={}))
            add_file(db, root, path, item_id=item_id, title_id=title_id, owner=ALICE, visibility="shared", title=name,
                     kind="movie", probe=facts, metadata=metadata)
            return title_id

        movie(501, "Alpha IMAX", "releases/Alpha IMAX HDR10+.mkv", probe(transfer="smpte2084", audio="truehd", title="Dolby Atmos", hdr10plus=True))
        split = movie(502, "Bravo Split", "releases/Bravo 4K.mkv", probe(transfer="smpte2084", audio="aac", hdr10=True))
        add_file(db, root, "releases/Bravo Atmos 1080p.mkv", item_id=uid(2502), title_id=split, owner=ALICE, visibility="shared",
                 title="Bravo Split", kind="movie", probe=probe(height=1080, audio="eac3", title="Atmos"))
        movie(503, "Charlie SDR", "releases/Charlie.mkv", probe(height=480))
        movie(504, "Delta Unknown", "releases/Delta.mkv", probe(transfer=None))
        movie(505, "Echo Parent", "IMAX/Echo.mkv", probe(height=480))
        movie(506, "Foxtrot Climax", "releases/CLIMAX.mkv", probe(height=480))
        movie(507, "Golf Zero", "releases/Golf.mkv", probe(height=0))
        movie(508, "Hotel HLG", "releases/Hotel.mkv", probe(height=480, transfer="arib-std-b67", audio="dts", profile="DTS-HD MA"))
        movie(509, "India Prefix", "releases/IMAX.Enhanced.mkv", probe(transfer=None))
        movie(510, "Juliet Marker", "releases/Juliet_IMAX_Remaster.mkv", probe(transfer=None))
        movie(511, "Kilo Jockey", "releases/Kilo.mkv", probe(transfer=None, audio="truehd", title="Jockey commentary"))
        movie(512, "Lima AAC", "releases/Lima.mkv", probe(transfer=None, audio="aac", title="Atmos"))

    client = TestClient(app, base_url="http://localhost")
    try:
        technical = {
            "IncludeItemTypes": "Movie", "LuminaResolution": "4k", "LuminaHDR": "hdr10plus",
            "LuminaAudio": "atmos", "LuminaIMAX": "true",
        }
        page = items(client, ALICE_TOKEN, **technical)
        assert [item["Name"] for item in page["Items"]] == ["Alpha IMAX"]
        assert alphabet(client, ALICE_TOKEN, **technical) == expected_anchors(page)

        assert [item["Name"] for item in items(client, ALICE_TOKEN, IncludeItemTypes="Movie", LuminaIMAX="true")["Items"]] == [
            "Alpha IMAX", "India Prefix", "Juliet Marker",
        ]
        assert [item["Name"] for item in items(client, ALICE_TOKEN, IncludeItemTypes="Movie", LuminaAudio="atmos")["Items"]] == [
            "Alpha IMAX", "Bravo Split",
        ]

        assert [item["Name"] for item in items(client, ALICE_TOKEN, IncludeItemTypes="Movie", LuminaHDR="sdr")["Items"]] == [
            "Bravo Split", "Charlie SDR", "Echo Parent", "Foxtrot Climax", "Golf Zero",
        ]
        assert [item["Name"] for item in items(client, ALICE_TOKEN, IncludeItemTypes="Movie", LuminaResolution="sd")["Items"]] == [
            "Charlie SDR", "Echo Parent", "Foxtrot Climax", "Hotel HLG",
        ]
        assert [item["Name"] for item in items(client, ALICE_TOKEN, IncludeItemTypes="Movie", LuminaAudio="dtshd")["Items"]] == ["Hotel HLG"]
        assert client.get("/Items/Filters2", params={"Fields": "LuminaTechnicalFilters", "ParentId": "not-an-id"},
                          headers=mediabrowser(ALICE_TOKEN)).json() == {"LuminaTechnicalFilters": True}
    finally:
        client.close()


def test_native_catalog_pages_keep_wall_state_and_visibility_without_detail_payload(native: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import jellyfin as jf

    with db_module.SessionLocal.begin() as db:
        for title in db.scalars(select(MediaTitle)):
            title.metadata_json = {**(title.metadata_json or {}), "overview": "Long detail description. " * 1000,
                                   "imdb_rating": 8.2, "rotten_tomatoes_rating": 91.5, "metacritic_rating": 73}

    full = items(native, ALICE_TOKEN)
    bob = items(native, BOB_TOKEN)
    warmed = []
    monkeypatch.setattr(jf.hero_framing.hero_framing, "warm_library", lambda *args: warmed.append(args))
    for token, expected in [(ALICE_TOKEN, full), (BOB_TOKEN, bob)]:
        collected = []
        for start in range(0, expected["TotalRecordCount"], 2):
            page = items(native, token, Fields="LuminaCatalog", Limit=2, StartIndex=start)
            assert page["StartIndex"] == start
            assert page["TotalRecordCount"] == expected["TotalRecordCount"]
            collected.extend(page["Items"])
        assert [row["Id"] for row in collected] == [row["Id"] for row in expected["Items"]]
        for compact, original in zip(collected, expected["Items"], strict=True):
            for field in ("Id", "Name", "Type", "ProductionYear", "ImageTags", "UserData", "RunTimeTicks", "ChildCount"):
                assert compact.get(field) == original.get(field)
            assert not ({"Overview", "People", "MediaSources", "BackdropImageTags", "ProviderIds",
                         "LuminaIMDbRating", "LuminaRottenTomatoesRating", "LuminaMetacriticRating"} & compact.keys())
    assert not warmed, "Enumerating a wall must not schedule hero framing for every title"
    detail = native.get(f"/Items/{full['Items'][0]['Id']}", headers=mediabrowser(ALICE_TOKEN))
    assert detail.status_code == 200
    assert detail.json()["Overview"].startswith("Long detail description.")
    assert (detail.json()["LuminaIMDbRating"], detail.json()["LuminaRottenTomatoesRating"], detail.json()["LuminaMetacriticRating"]) == (8.2, 91.5, 73)
    assert warmed, "The normal detail path must retain hero preparation"
