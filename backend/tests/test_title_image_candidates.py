"""TMDB image candidates: pure parsing, the per-type endpoint, and the proxy size override."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import artwork as artwork_module
from app.services import tmdb
from app.services.artwork import ArtworkService
from app.services.title_image_candidates import NoTmdbId, candidates

RAW = {
    "posters": [
        {"file_path": "/a.jpg", "iso_639_1": "en", "vote_average": 5.1, "width": 2000, "height": 3000},
        {"file_path": "/b.png", "iso_639_1": "fr", "vote_average": 7.0, "width": 1000, "height": 1500},
        {"file_path": "/c.svg", "iso_639_1": None, "vote_average": 9.0, "width": 10, "height": 10},
        {"file_path": "/../x.jpg", "iso_639_1": "en", "vote_average": 9.9},
        {"file_path": "/d.jpg", "iso_639_1": None, "vote_average": 5.1, "width": 800, "height": 1200},
        "junk", {"vote_average": 1}, {"file_path": 7},
    ],
    "backdrops": [{"file_path": "/bd.jpg", "vote_average": 1}],
    "logos": [{"file_path": "/lg.png", "vote_average": 1}],
    "stills": [{"file_path": "/st.jpg", "vote_average": 1}],
}


def test_sorted_by_vote_then_language_match_then_width() -> None:
    got = tmdb.image_candidates(RAW, "Primary", "en")
    assert [c["tmdb_path"] for c in got] == ["/b.png", "/a.jpg", "/d.jpg"]
    assert got[0] == {"tmdb_path": "/b.png", "width": 1000, "height": 1500, "language": "fr", "vote": 7.0}


def test_only_valid_tmdb_paths_survive_and_malformed_entries_are_skipped() -> None:
    paths = {c["tmdb_path"] for c in tmdb.image_candidates(RAW, "Primary", "en")}
    assert paths == {"/a.jpg", "/b.png", "/d.jpg"}
    assert tmdb.image_candidates({"posters": "x"}, "Primary", "en") == []
    assert tmdb.image_candidates({}, "Primary", "en") == []


def test_capped_at_60() -> None:
    many = {"posters": [{"file_path": f"/p{i}.jpg", "vote_average": i} for i in range(100)]}
    assert len(tmdb.image_candidates(many, "Primary", "en")) == 60


def test_kind_selects_the_list() -> None:
    pick = lambda kind: tmdb.image_candidates(RAW, kind, "en")[0]["tmdb_path"]  # noqa: E731
    assert (pick("Backdrop"), pick("Logo"), pick("Still")) == ("/bd.jpg", "/lg.png", "/st.jpg")


class Fake:
    image_languages = "en,null"
    lang2 = "en"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, **params: object) -> dict:
        self.calls.append((path, params))
        return RAW


def make(rows: list[SimpleNamespace]):  # noqa: ANN201
    by_id = {r.id: r for r in rows}
    return SimpleNamespace(get=lambda _model, key: by_id.get(key))


SERIES = SimpleNamespace(id="s", type="series", parent_id=None, index_number=None, provider_ids={"Tmdb": "42"})
SEASON = SimpleNamespace(id="se", type="season", parent_id="s", index_number=2, provider_ids={})
EPISODE = SimpleNamespace(id="e", type="episode", parent_id="se", index_number=5, provider_ids={})
MOVIE = SimpleNamespace(id="m", type="movie", parent_id=None, index_number=None, provider_ids={"Tmdb": "603"})
DB = make([SERIES, SEASON, EPISODE, MOVIE])


def test_movie_and_series_call_the_images_endpoint_once() -> None:
    fake = Fake()
    candidates(fake, DB, MOVIE, "Primary")
    candidates(fake, DB, SERIES, "Backdrop")
    assert fake.calls == [("/movie/603/images", {"include_image_language": "en,null"}), ("/tv/42/images", {"include_image_language": "en,null"})]


def test_season_uses_the_season_images_and_episode_the_episode_images() -> None:
    fake = Fake()
    assert candidates(fake, DB, SEASON, "Primary")[0]["tmdb_path"] == "/b.png"
    assert candidates(fake, DB, EPISODE, "Primary")[0]["tmdb_path"] == "/st.jpg"
    assert [p for p, _ in fake.calls] == ["/tv/42/season/2/images", "/tv/42/season/2/episode/5/images"]


def test_season_backdrop_is_empty_and_unsupported_types_are_rejected() -> None:
    fake = Fake()
    assert candidates(fake, DB, SEASON, "Backdrop") == [] and fake.calls == []
    for title, kind in ((EPISODE, "Logo"), (EPISODE, "Backdrop"), (SEASON, "Logo"), (MOVIE, "Thumb"), (EPISODE, "Still")):
        with pytest.raises(ValueError):
            candidates(fake, DB, title, kind)


def test_a_title_without_a_tmdb_id_raises() -> None:
    bare = SimpleNamespace(id="b", type="movie", parent_id=None, index_number=None, provider_ids={"Tmdb": "x"})
    with pytest.raises(NoTmdbId):
        candidates(Fake(), DB, bare, "Primary")
    orphan = SimpleNamespace(id="o", type="episode", parent_id="gone", index_number=1, provider_ids={})
    with pytest.raises(NoTmdbId):
        candidates(Fake(), DB, orphan, "Primary")


def test_load_image_size_override_is_limited_to_allowed_sizes(tmp_path: Path) -> None:
    seen: list[str] = []

    class Fetcher:
        def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201
            seen.append(url)
            raise artwork_module.ArtworkUnavailableError("x")

    service = ArtworkService(remote_fetcher=Fetcher(), public_source_policy=tmdb.policy, pinned_root=tmp_path)
    with pytest.raises(artwork_module.ArtworkNotFoundError):
        tmdb.load_image(service, "/a.jpg", "Primary", size="w9999")
    with pytest.raises(artwork_module.ArtworkUnavailableError):
        tmdb.load_image(service, "/a.jpg", "Primary", size="w300")
    assert seen == [f"{tmdb.IMAGE_BASE}/w300/a.jpg"]
    assert tmdb.image_url("/a.jpg", "w300") and tmdb.CANDIDATE_SIZES == {"Primary": "w185", "Backdrop": "w300", "Logo": "w185"}
    assert tmdb.image_url("/a.jpg", "w300") == f"{tmdb.IMAGE_BASE}/w300/a.jpg"
