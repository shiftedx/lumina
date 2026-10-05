"""Public sign-in showcase: public catalog only, Lumina-only image URLs, empty (not an error) when unavailable."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.main import app
from app.models import MediaRequest, MediaTitle
from app.services import showcase
from support import make_user, seed_app_settings
from test_requests_catalog import fake, fx  # noqa: F401 - the shared upstream fake

URL = "/api/public/showcase"
SOON = (date.today() + timedelta(days=3)).isoformat()
LATER = (date.today() + timedelta(days=40)).isoformat()


@pytest.fixture(autouse=True)
def _fresh():  # noqa: ANN202
    showcase.clear()
    yield
    showcase.clear()


@pytest.fixture
def upstream(fake):  # noqa: ANN001, ANN201, F811
    movie = fx("tmdb_movie_603")
    fake.tmdb.update({
        "/3/movie/upcoming": {"results": [
            {"id": 700, "title": "Dune: Part Three", "release_date": SOON, "backdrop_path": "/dune-bd.jpg"},
            {"id": 701, "title": "Next Spring", "release_date": LATER, "backdrop_path": "/spring-bd.jpg"},
            {"id": 702, "title": "Adult Title", "release_date": SOON, "adult": True, "backdrop_path": "/x.jpg"},
            {"id": 603, "title": "The Matrix", "release_date": "1999-03-30"},  # already out: not "upcoming"
        ]},
        "/3/movie/now_playing": fx("tmdb_movie_list"),
        "/3/tv/on_the_air": {"results": [{"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17"}]},
        "/3/movie/700": {**movie, "id": 700, "title": "Dune: Part Three", "backdrop_path": "/dune-bd.jpg"},
        "/3/movie/701": {**movie, "id": 701, "title": "Next Spring", "backdrop_path": None, "images": {"logos": []}},
        "/3/tv/209867": {**fx("tmdb_tv_1399"), "id": 209867, "name": "Frieren", "backdrop_path": "/frieren-bd.jpg"},
    })
    return fake


@pytest.fixture
def anon(db_factory, api_client):  # noqa: ANN001, ANN201
    seeded: list[bool] = []

    def build(**settings):  # noqa: ANN003, ANN202
        if not seeded:
            with db_factory() as session:
                seed_app_settings(session, **{"requests_enabled": True, **settings})
            seeded.append(True)
        return api_client(base_url="http://localhost")
    return build


def test_slides_mix_public_releases_with_only_lumina_image_urls(anon, upstream) -> None:  # noqa: ANN001
    response = anon().get(URL)
    assert response.status_code == 200
    slides = response.json()["slides"]
    assert {(s["title"], s["caption"], s["kind"]) for s in slides} == {
        ("Dune: Part Three", f"In cinemas {date.fromisoformat(SOON):%A}", "movie"),  # Next Spring has no backdrop
        ("The Matrix", "In cinemas now", "movie"),
        ("Game of Thrones", "New episodes this week", "show"),
        ("Frieren: Beyond Journey's End", "New this season", "anime"),
    }
    assert "Adult Title" not in response.text and "tmdb.org" not in response.text and "anilist.co" not in response.text
    for slide in slides:
        assert slide["backdrop_url"].startswith(showcase.ART_PREFIX)
        assert set(slide) <= {"title", "caption", "kind", "backdrop_url", "logo_url"}
    dune = next(s for s in slides if s["kind"] == "movie" and s["title"].startswith("Dune"))
    assert showcase.source(dune["logo_url"].removeprefix(showcase.ART_PREFIX)) == "https://image.tmdb.org/t/p/w500/logo-en.png"
    # cached: the same answer without touching upstream again
    calls = len(upstream.calls)
    assert anon().get(URL).json()["slides"] == slides and len(upstream.calls) == calls


def test_no_household_data_leaks(db_factory, anon, upstream) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add(make_user("secret-member", username="hidden-household-name"))
        session.add(MediaTitle(id="11111111-1111-1111-1111-111111111111", type="movie", key="movie:secret", name="Household Secret Film"))
        session.add(MediaRequest(id="r1", kind="movie", media_type="movie", tmdb_id=603, title="The Matrix", status="available", requested_by="secret-member"))
        session.commit()
    text = anon().get(URL).text
    for private in ("hidden-household-name", "secret-member", "Household Secret Film", "11111111", "available", "status", "request"):
        assert private not in text


@pytest.mark.parametrize("settings", [{"requests_enabled": False}, {"tmdb_api_key": None}])
def test_empty_when_requests_or_tmdb_are_not_configured(anon, upstream, monkeypatch, settings) -> None:  # noqa: ANN001
    if "tmdb_api_key" in settings:
        monkeypatch.setattr("app.config.settings.tmdb_api_key", None)
        settings = {}
    response = anon(**settings).get(URL)
    assert response.status_code == 200 and response.json() == {"slides": []}


def test_empty_when_upstream_fails(anon, upstream) -> None:  # noqa: ANN001
    upstream.tmdb.clear()
    upstream.anilist.clear()
    assert anon().get(URL).json() == {"slides": []}


def test_art_serves_only_showcase_images(anon, upstream, monkeypatch) -> None:  # noqa: ANN001
    fetched: list[str] = []
    fake_artwork = SimpleNamespace(
        register_remote_artwork=lambda url: fetched.append(url) or "id",
        load_remote_artwork=lambda artwork_id: SimpleNamespace(content_type="image/jpeg", content=b"jpeg"),
    )
    monkeypatch.setattr(app.state, "artwork", fake_artwork)
    client = anon()
    assert client.get(f"{showcase.ART_PREFIX}AAAAAAAAAAAAAAAAAAAAAA").status_code == 404  # never signed
    slide = client.get(URL).json()["slides"][0]
    image = client.get(slide["backdrop_url"])
    assert image.status_code == 200 and image.content == b"jpeg" and image.headers["cache-control"] == "public, max-age=86400"
    assert fetched == [showcase.source(slide["backdrop_url"].removeprefix(showcase.ART_PREFIX))]
    assert fetched[0].startswith("https://image.tmdb.org/")


def test_rate_limited_per_client(anon, upstream) -> None:  # noqa: ANN001
    client = anon()
    assert all(client.get(URL).status_code == 200 for _ in range(60))
    assert client.get(URL).status_code == 429
