"""Wall facets: genres, year range and resolution buckets of the visible titles of one type."""
from __future__ import annotations

import pytest

from app.models import LibraryItemArtifact, MediaArtifact, User
from discovery_support import add_movie, add_series, add_version
from support import make_user
from title_support import uid


def _probe(session, item_id: str, width: int, height: int) -> None:  # noqa: ANN001
    artifact = MediaArtifact(id=f"{item_id}-a", root_id="root", relative_path=f"{item_id}.mkv", ownership="external", probe={"width": width, "height": height})
    session.add_all([artifact, LibraryItemArtifact(library_item_id=item_id, artifact_id=artifact.id)])


@pytest.fixture(autouse=True)
def fresh_facets(monkeypatch):  # noqa: ANN001, ANN201
    from app.routers import titles as titles_router

    monkeypatch.setattr(titles_router, "_facets", {})


@pytest.fixture
def catalogue(db_factory):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        add_movie(session, uid(1), "One", genres=["Drama", "War"], year=1999)
        _probe(session, f"{uid(1)}-v", 3840, 2160)
        add_movie(session, uid(2), "Two", genres=["drama"], year=2021)
        _probe(session, f"{uid(2)}-v", 1920, 1080)
        add_version(session, f"{uid(2)}-4k", uid(2))
        _probe(session, f"{uid(2)}-4k", 3840, 2160)
        add_movie(session, uid(3), "Three", genres=["Comedy"], year=None)
        add_movie(session, uid(4), "Secret", owner="owner", visibility="private", genres=["Horror"], year=1950)
        _probe(session, f"{uid(4)}-v", 720, 480)
        add_series(session, uid(5), "Show", seasons={1: 3}, genres=["Drama"])
        for episode in (1, 2):
            _probe(session, f"{uid(5)}-s1e{episode}-v", 1280, 720)
        _probe(session, f"{uid(5)}-s1e3-v", 1920, 1080)
        session.commit()
    return db_factory


def client_for(api_client, factory, user_id: str):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def test_movie_facets_group_genres_bound_years_and_count_buckets_by_title(catalogue, api_client) -> None:  # noqa: ANN001
    facets = client_for(api_client, catalogue, "member").get("/api/titles/facets", params={"type": "movie"}).json()
    assert facets["genres"] == [{"name": "Comedy", "count": 1}, {"name": "Drama", "count": 2}, {"name": "War", "count": 1}]
    assert facets["years"] == {"min": 1999, "max": 2021}
    assert facets["resolutions"] == [{"value": "4k", "count": 2}, {"value": "1080p", "count": 1}]  # 4k → sd, count > 0 only


def test_series_facets_count_each_show_once_per_bucket(catalogue, api_client) -> None:  # noqa: ANN001
    facets = client_for(api_client, catalogue, "member").get("/api/titles/facets", params={"type": "series", "unwatched": "true"}).json()
    assert facets == {"genres": [{"name": "Drama", "count": 1}], "years": None, "resolutions": [{"value": "1080p", "count": 1}, {"value": "720p", "count": 1}]}


def test_facets_count_only_what_the_member_can_see(catalogue, api_client) -> None:  # noqa: ANN001
    member = client_for(api_client, catalogue, "member").get("/api/titles/facets", params={"type": "movie"}).json()
    assert "Horror" not in {genre["name"] for genre in member["genres"]} and member["years"]["min"] == 1999
    assert "sd" not in {bucket["value"] for bucket in member["resolutions"]}
    owner = client_for(api_client, catalogue, "owner").get("/api/titles/facets", params={"type": "movie"}).json()
    assert {"name": "Horror", "count": 1} in owner["genres"] and owner["years"]["min"] == 1950
    assert {"value": "sd", "count": 1} in owner["resolutions"]


def test_facets_need_a_wall_type(catalogue, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, catalogue, "member")
    assert client.get("/api/titles/facets").status_code == 400  # Exactly one of type or category
    assert client.get("/api/titles/facets", params={"type": "episode"}).status_code == 422
