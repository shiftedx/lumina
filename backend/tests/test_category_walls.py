"""Category and music walls: /api/titles?category= and ?type=album|artist, their totals,
letters, chips and cursors, and what never reaches them."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.models import User
from app.services.titles import TitleFilters, TitleSeek, encode_title_cursor
from discovery_support import add_progress
from support import make_user
from title_support import (
    ALBUM, ALICE, ANIME_FILE, ANIME_MOVIE, ANIME_MOVIE_FILE, ANIME_SERIES, ARTIST, BOB, FILE, MOVIE, S1E1, S1E2,
    SECRET_SERIES, SERIES, TRACKS, seed_gallery, seed_tree,
)


@pytest.fixture
def walls(db_factory, api_client, tmp_path):  # noqa: ANN001, ANN201
    """seed_tree + seed_gallery; ``get`` requests as alice (default) or bob. Shared by T1's Part A API tests."""
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        session.commit()
        members = {"alice": session.get(User, ALICE), "bob": session.get(User, BOB)}
    current = {"user": members["alice"]}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")

    def get(path: str, member: str = "alice", **params):  # noqa: ANN202
        current["user"] = members[member]
        return client.get(path, params=params)

    return SimpleNamespace(get=get, db=db_factory, root=root)


def ids(page: dict) -> list[str]:
    return [item["id"] for item in page["items"]]


def _digest_before_categories(types: tuple[str, ...], sort: str) -> str:
    """TitleFilters.digest as 1.5.1 computed it, before category joined the list identity."""
    canonical = {**dataclasses.asdict(TitleFilters()), "genres": [], "resolutions": [], "types": sorted(types), "sort": sort}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def test_category_walls_list_exactly_their_titles(walls) -> None:  # noqa: ANN001
    """Every sort, with totals; the Anime wall mixes movies and series; another member's private show never counts."""
    expected = {
        "name": [ANIME_MOVIE, ANIME_SERIES], "created": [ANIME_MOVIE, ANIME_SERIES],
        "year": [ANIME_SERIES, ANIME_MOVIE], "rating": [ANIME_MOVIE, ANIME_SERIES],
    }
    for sort, order in expected.items():
        anime = walls.get("/api/titles", category="anime", sort=sort).json()
        assert (ids(anime), anime["total"]) == (order, 2), sort
        assert ids(walls.get("/api/titles", category="movies", sort=sort).json()) == [MOVIE]
        assert ids(walls.get("/api/titles", category="shows", sort=sort).json()) == [SERIES]
    assert walls.get("/api/titles", category="anime").json()["letters"] == [{"letter": "F", "index": 0}, {"letter": "S", "index": 1}]
    bob = walls.get("/api/titles", category="shows", member="bob").json()
    assert (ids(bob), bob["total"]) == ([SECRET_SERIES, SERIES], 2)
    assert walls.get("/api/titles", category="shows").json()["total"] == 1


def test_movies_and_shows_never_show_anime(walls) -> None:  # noqa: ANN001
    """Criterion 5 for lists, totals and letters; without category or type the list spans every category (spotlight)."""
    movies = walls.get("/api/titles", category="movies").json()
    assert (ids(movies), movies["total"], movies["letters"]) == ([MOVIE], 1, [{"letter": "M", "index": 0}])
    shows = walls.get("/api/titles", category="shows", sort="created").json()
    assert (ids(shows), shows["total"]) == ([SERIES], 1)
    everything = walls.get("/api/titles", sort="created").json()
    assert set(ids(everything)) == {MOVIE, SERIES, ANIME_MOVIE, ANIME_SERIES}


def test_a_type_must_belong_to_its_category(walls) -> None:  # noqa: ANN001
    mismatch = walls.get("/api/titles", category="movies", type="series")
    assert (mismatch.status_code, mismatch.json()) == (400, {"detail": "Invalid category"})
    assert walls.get("/api/titles", category="anime", type="episode").status_code == 400
    assert ids(walls.get("/api/titles", category="anime", type="movie").json()) == [ANIME_MOVIE]  # Pinned interpretation 1
    assert ids(walls.get("/api/titles", category="anime", type="series").json()) == [ANIME_SERIES]
    assert ids(walls.get("/api/titles", category="movies", type="movie").json()) == [MOVIE]
    assert walls.get("/api/titles", category="music").status_code == 422


def test_the_anime_wall_applies_each_titles_own_chip_rules(walls) -> None:  # noqa: ANN001
    """The mixed wall uses the movie rules for movies and the series rules for series."""
    with walls.db() as session:
        add_progress(session, ALICE, ANIME_FILE, completed=True)  # the show's only episode: watched
        add_progress(session, ALICE, ANIME_MOVIE_FILE, position=600)  # the film: started
        session.commit()
    assert ids(walls.get("/api/titles", category="anime", unwatched="true").json()) == [ANIME_MOVIE]
    assert ids(walls.get("/api/titles", category="anime", in_progress="true").json()) == [ANIME_MOVIE]
    assert ids(walls.get("/api/titles", category="anime", in_progress="true", member="bob").json()) == []
    assert ids(walls.get("/api/titles", category="anime", year_from=2021).json()) == [ANIME_SERIES]
    assert ids(walls.get("/api/titles", category="movies", unwatched="true").json()) == [MOVIE]


def test_a_cursor_belongs_to_its_category(walls) -> None:  # noqa: ANN001
    """The digest holds the category: a cursor from another wall, or from before the upgrade, is a 400."""
    first = walls.get("/api/titles", category="anime", sort="name", limit=1).json()
    assert ids(first) == [ANIME_MOVIE] and first["next_cursor"]
    following = walls.get("/api/titles", category="anime", sort="name", limit=1, cursor=first["next_cursor"]).json()
    assert (ids(following), following["start_index"], following["next_cursor"]) == ([ANIME_SERIES], 1, None)
    invalid = (400, {"detail": "Invalid cursor"})
    for params in ({"category": "shows"}, {"type": "movie"}, {}):
        response = walls.get("/api/titles", sort="name", limit=1, cursor=first["next_cursor"], **params)
        assert (response.status_code, response.json()) == invalid, params
    old = encode_title_cursor("name", _digest_before_categories(("movie", "series"), "name"), TitleSeek(("Film",), ANIME_MOVIE, 1))
    for params in ({"category": "anime"}, {}):
        response = walls.get("/api/titles", sort="name", limit=1, cursor=old, **params)
        assert (response.status_code, response.json()) == invalid, params


def test_music_lists_by_type_and_stays_off_the_other_walls(walls) -> None:  # noqa: ANN001
    """type=album|artist; album and artist never reach /api/titles without a type, nor next up."""
    albums = walls.get("/api/titles", type="album").json()
    assert (ids(albums), albums["total"], albums["letters"]) == ([ALBUM], 1, [{"letter": "A", "index": 0}])
    assert ids(walls.get("/api/titles", type="artist", sort="created").json()) == [ARTIST]
    assert ids(walls.get("/api/titles", type="album", genre="Folk").json()) == [ALBUM]
    assert ids(walls.get("/api/titles", type="album", unwatched="true").json()) == []  # chips match nothing for music
    assert not {ALBUM, ARTIST} & set(ids(walls.get("/api/titles", limit=200).json()))
    with walls.db() as session:
        add_progress(session, ALICE, TRACKS[0], completed=True)
        add_progress(session, ALICE, FILE[S1E1], completed=True)
        session.commit()
    assert [title["id"] for title in walls.get("/api/titles/next-up").json()] == [S1E2]


# ---- Facets -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_facets(monkeypatch):  # noqa: ANN001, ANN201
    from app.routers import titles as titles_router

    monkeypatch.setattr(titles_router, "_facets", {})


def _probe(session, item_id: str, width: int, height: int) -> None:  # noqa: ANN001
    from sqlalchemy import select

    from app.models import LibraryItemArtifact, MediaArtifact

    artifact = session.scalar(
        select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
        .where(LibraryItemArtifact.library_item_id == item_id)
    )
    artifact.probe = {"width": width, "height": height}


@pytest.fixture
def faceted(walls):  # noqa: ANN001, ANN201
    """The anime titles gain genres and probed files: a 1080p film and a 4K episode."""
    from app.models import MediaTitle

    with walls.db() as session:
        session.get(MediaTitle, ANIME_MOVIE).metadata_json = {"genres": ["Animation"]}
        session.get(MediaTitle, ANIME_SERIES).metadata_json = {"genres": ["Animation", "Drama"]}
        _probe(session, ANIME_MOVIE_FILE, 1920, 1080)
        _probe(session, ANIME_FILE, 3840, 2160)
        session.commit()
    return walls


def test_category_facets_count_only_that_categorys_titles(faceted) -> None:  # noqa: ANN001
    def facets(**params) -> dict:
        return faceted.get("/api/titles/facets", **params).json()

    assert facets(category="anime") == {
        "genres": [{"name": "Animation", "count": 2}, {"name": "Drama", "count": 1}], "years": {"min": 2020, "max": 2023},
        "resolutions": [{"value": "4k", "count": 1}, {"value": "1080p", "count": 1}],
    }
    assert facets(category="movies") == {
        "genres": [{"name": "Action", "count": 1}], "years": {"min": 2020, "max": 2020}, "resolutions": [{"value": "4k", "count": 1}],
    }
    assert facets(category="shows") == {"genres": [{"name": "Drama", "count": 1}], "years": {"min": 2019, "max": 2019}, "resolutions": []}
    assert facets(type="album") == {"genres": [{"name": "Folk", "count": 1}], "years": {"min": 2019, "max": 2019}, "resolutions": []}
    # The per-type facets still span every category, under their own cache key.
    assert facets(type="series")["genres"] == [{"name": "Animation", "count": 1}, {"name": "Drama", "count": 2}]


def test_facets_take_exactly_one_of_type_or_category(faceted) -> None:  # noqa: ANN001
    for params in ({}, {"type": "movie", "category": "movies"}):
        response = faceted.get("/api/titles/facets", **params)
        assert (response.status_code, response.json()) == (400, {"detail": "Give exactly one of type or category"}), params
    assert faceted.get("/api/titles/facets", type="artist").status_code == 422
    assert faceted.get("/api/titles/facets", category="music").status_code == 422
