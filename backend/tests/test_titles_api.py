"""Lumina's title endpoints: visibility, summaries, detail, episodes."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import LibraryItem, MediaTitle, PlaybackProgress, User
from app.services.library import encode_library_cursor
from support import make_user
from title_support import (
    ALICE, BOB, FILE, MOVIE, MOVIE_1080, MOVIE_4K, S1E1, SEASON1, SEASON2, SECRET_SERIES, SERIES, T0, S2E1, ROOT,
    add_file, seed_tree, uid,
)


@pytest.fixture
def library(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        session.commit()
    return db_factory, root


def member(api_client, db_factory, user_id: str):  # noqa: ANN001, ANN201
    with db_factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def test_grid_lists_only_visible_top_level_titles(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    names = lambda client: [t["name"] for t in client.get("/api/titles").json()["items"]]  # noqa: E731
    assert names(member(api_client, factory, ALICE)) == ["Movie", "Show"]
    assert names(member(api_client, factory, BOB)) == ["Movie", "Secret Show", "Show"]


def test_type_filter_and_keyset_cursor(library, api_client) -> None:  # noqa: ANN001
    client = member(api_client, library[0], ALICE)
    first = client.get("/api/titles", params={"type": "episode", "limit": 2}).json()
    assert [t["name"] for t in first["items"]] == ["Making Of", "Pilot"]
    assert (first["start_index"], first["total"]) == (0, 4)
    second = client.get("/api/titles", params={"type": "episode", "limit": 2, "cursor": first["next_cursor"]}).json()
    assert [t["name"] for t in second["items"]] == ["Return", "Second"] and second["next_cursor"] is None
    assert (second["start_index"], second["total"], second["letters"]) == (2, None, None)
    assert client.get("/api/titles", params={"cursor": "not-a-cursor"}).status_code == 400
    old = encode_library_cursor({"o": 2})  # the offset cursors before the gallery release
    assert client.get("/api/titles", params={"type": "episode", "limit": 2, "cursor": old}).json() == first  # a pre-upgrade tab restarts
    assert client.get("/api/titles", params={"type": "person"}).status_code == 422
    assert client.get("/api/titles", params={"limit": 201}).status_code == 422


def test_summaries_carry_ancestry_art_and_preferred_version(library, api_client) -> None:  # noqa: ANN001
    client = member(api_client, library[0], ALICE)
    pilot = next(t for t in client.get("/api/titles", params={"type": "episode"}).json()["items"] if t["id"] == S1E1)
    assert (pilot["series_id"], pilot["series_name"], pilot["season_number"], pilot["parent_id"]) == (SERIES, "Show", 1, SEASON1)
    assert pilot["play_item_id"] == FILE[S1E1] and pilot["poster_url"] is None
    show = client.get(f"/api/titles/{SERIES}").json()
    assert show["poster_url"].startswith(f"/api/titles/{SERIES}/images/Primary?tag=")


def test_movie_detail_lists_versions_extras_and_boxset(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    client = member(api_client, factory, ALICE)
    movie = client.get(f"/api/titles/{MOVIE}").json()
    assert [v["label"] for v in movie["versions"]] == ["1080p", "4K"]
    four_k = movie["versions"][1]
    assert (four_k["container"], four_k["video_codec"], four_k["hdr"], four_k["height"]) == ("mkv", "hevc", True, 2160)
    assert [e["extra_type"] for e in movie["extras"]] == ["trailer"]
    assert movie["boxset"]["name"] == "Saga" and movie["play_item_id"] == MOVIE_1080
    assert movie["provider_ids"] == {"Tmdb": "603", "Imdb": "tt0133093"} and movie["match"] is None
    with factory() as session:  # unwatched: the largest file is preferred
        session.get(LibraryItem, MOVIE_4K).file_size = 10_000
        session.commit()
    assert client.get(f"/api/titles/{MOVIE}").json()["play_item_id"] == MOVIE_4K
    with factory() as session:  # watched: the most recently watched version wins over size
        session.get(LibraryItem, MOVIE_4K).file_size = 4096
        session.add(PlaybackProgress(id=uid(700), user_id=ALICE, item_id=MOVIE_4K, position_seconds=60, duration_seconds=7200, last_watched_at=T0))
        session.commit()
    movie = client.get(f"/api/titles/{MOVIE}").json()
    assert movie["play_item_id"] == MOVIE_4K and movie["user_data"]["resume_item_id"] == MOVIE_4K
    assert movie["user_data"]["position_seconds"] == 60


def test_series_detail_puts_specials_last_and_offers_first_episode(library, api_client) -> None:  # noqa: ANN001
    show = member(api_client, library[0], ALICE).get(f"/api/titles/{SERIES}").json()
    assert [c["name"] for c in show["children"]] == ["Season 1", "Season 2", "Specials"]
    assert show["user_data"]["unplayed_count"] == 4 and show["user_data"]["played"] is False
    assert show["play_next"]["id"] == S1E1


def test_private_titles_are_404_for_other_members(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    paths = (f"/api/titles/{SECRET_SERIES}", f"/api/titles/{SECRET_SERIES}/episodes")
    alice = member(api_client, factory, ALICE)  # api_client's user override is global: finish with one member first
    assert [alice.get(path).status_code for path in paths] == [404, 404]
    assert alice.get("/api/titles/not-a-uuid").status_code == 404
    bob = member(api_client, factory, BOB)
    assert [bob.get(path).status_code for path in paths] == [200, 200]


def test_episodes_by_season(library, api_client) -> None:  # noqa: ANN001
    client = member(api_client, library[0], ALICE)
    assert [e["name"] for e in client.get(f"/api/titles/{SERIES}/episodes", params={"season": 1}).json()] == ["Pilot", "Second"]
    assert [e["name"] for e in client.get(f"/api/titles/{SERIES}/episodes").json()] == ["Making Of", "Pilot", "Second", "Return"]
    assert client.get(f"/api/titles/{MOVIE}/episodes").status_code == 404


def test_unnumbered_episode_sorts_after_numbered(library, api_client) -> None:  # noqa: ANN001
    factory, root = library
    with factory() as session:
        session.add(MediaTitle(id=uid(14), type="episode", parent_id=SEASON2, key=f"{ROOT}:Show#s2bonus", root_id=ROOT, name="Bonus"))
        add_file(session, root, "Show/Season 02/Bonus.mkv", item_id=uid(114), title_id=uid(14), owner=ALICE, title="Bonus",
                 created_at=T0 - timedelta(days=1))
        session.commit()
    names = [e["name"] for e in member(api_client, factory, ALICE).get(f"/api/titles/{SERIES}/episodes", params={"season": 2}).json()]
    assert names == ["Return", "Bonus"]


def test_tombstoned_file_hides_episode_and_empty_season(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    with factory() as session:
        session.get(LibraryItem, FILE[S2E1]).status = "missing"
        session.commit()
    client = member(api_client, factory, ALICE)
    assert client.get(f"/api/titles/{SERIES}/episodes", params={"season": 2}).json() == []
    assert [c["name"] for c in client.get(f"/api/titles/{SERIES}").json()["children"]] == ["Season 1", "Specials"]
    assert client.get(f"/api/titles/{S2E1}").status_code == 404


from title_support import POSTER, S1E2, SECRET_EPISODE, S0E1  # noqa: E402


def test_next_up_follows_the_last_watched_episode(library, api_client) -> None:  # noqa: ANN001
    client = member(api_client, library[0], ALICE)
    assert client.get("/api/titles/next-up").json() == []
    assert client.put(f"/api/titles/{S1E1}/watched", json={"watched": True}).json()["played"] is True
    up_next = client.get("/api/titles/next-up").json()
    assert [(t["id"], t["series_name"], t["season_number"]) for t in up_next] == [(S1E2, "Show", 1)]
    assert client.get(f"/api/titles/{SERIES}").json()["user_data"]["last_watched_at"] is not None  # series CTA
    assert client.get("/api/titles/next-up", params={"limit": 0}).status_code == 422


def test_watched_fans_out_over_a_series_and_back(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    client = member(api_client, factory, ALICE)
    data = client.put(f"/api/titles/{SERIES}/watched", json={"watched": True}).json()
    assert (data["played"], data["unplayed_count"]) == (True, 0)
    with factory() as session:
        rows = session.query(PlaybackProgress).filter_by(user_id=ALICE).all()
        assert {row.item_id for row in rows} == {FILE[S0E1], FILE[S1E1], FILE[S1E2], FILE[S2E1]}
        assert all(row.completed and row.position_seconds == 0 for row in rows)
    data = client.put(f"/api/titles/{SERIES}/watched", json={"watched": False}).json()
    assert (data["played"], data["unplayed_count"]) == (False, 4)
    with factory() as session:
        assert session.query(PlaybackProgress).filter_by(user_id=ALICE).count() == 0
    assert client.put(f"/api/titles/{SERIES}/watched", json={"watched": True, "extra": 1}).status_code == 422


def test_watching_a_movie_marks_only_its_preferred_version(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    member(api_client, factory, ALICE).put(f"/api/titles/{MOVIE}/watched", json={"watched": True})
    with factory() as session:
        assert [row.item_id for row in session.query(PlaybackProgress).filter_by(user_id=ALICE)] == [MOVIE_1080]


def test_favorites_toggle_and_respect_visibility(library, api_client) -> None:  # noqa: ANN001
    client = member(api_client, library[0], ALICE)
    assert client.put(f"/api/me/favorites/{SERIES}").status_code == 204
    assert client.put(f"/api/me/favorites/{SERIES}").status_code == 204  # idempotent
    assert client.get(f"/api/titles/{SERIES}").json()["user_data"]["is_favorite"] is True
    assert client.delete(f"/api/me/favorites/{SERIES}").status_code == 204
    assert client.get(f"/api/titles/{SERIES}").json()["user_data"]["is_favorite"] is False
    assert client.put(f"/api/me/favorites/{MOVIE_4K}").status_code == 204  # a Library item may be a favorite too
    for target in (SECRET_SERIES, FILE[SECRET_EPISODE], "not-a-uuid", uid(999)):
        assert client.put(f"/api/me/favorites/{target}").status_code == 404


def test_title_art_is_proxied_with_visibility_and_cache_policy(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    client = member(api_client, factory, ALICE)
    url = client.get(f"/api/titles/{SERIES}").json()["poster_url"]
    response = client.get(url)
    assert response.status_code == 200 and response.content == POSTER
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert client.get(f"/api/titles/{SERIES}/images/Primary?tag=stale").headers["cache-control"] == "private, no-cache"
    assert client.head(url).status_code == 200
    assert client.get(f"/api/titles/{SERIES}/images/Logo").status_code == 404  # none stored
    assert client.get(f"/api/titles/{SERIES}/images/Nope").status_code == 404
    assert client.get(f"/api/titles/{SECRET_SERIES}/images/Primary").status_code == 404


def test_tmdb_art_goes_through_track_e_loader(library, api_client, monkeypatch) -> None:  # noqa: ANN001
    from app.services import tmdb
    from app.services.artwork import ArtworkResponse

    factory, _root = library
    with factory() as session:
        movie = session.get(MediaTitle, MOVIE)
        movie.images = {**movie.images, "Backdrop": {"tmdb": "/backdrop.jpg"}}
        session.commit()
    calls = []

    def load_image(artwork, path, kind):  # noqa: ANN001, ANN202
        calls.append((path, kind))
        return ArtworkResponse(content_type="image/jpeg", content=b"tmdb-bytes")

    monkeypatch.setattr(tmdb, "load_image", load_image)
    response = member(api_client, factory, ALICE).get(f"/api/titles/{MOVIE}/images/Backdrop")
    assert (response.status_code, response.content, calls) == (200, b"tmdb-bytes", [("/backdrop.jpg", "Backdrop")])


def test_title_summaries_and_preferred_versions_for_other_tracks(library) -> None:  # noqa: ANN001
    from app.services.title_summaries import title_summaries
    from app.services.titles import preferred_versions

    factory, _root = library
    with factory() as session:
        alice = session.get(User, ALICE)
        summaries = title_summaries(session, alice, [SERIES, SECRET_SERIES, MOVIE, SERIES])
        assert [s.id for s in summaries] == [SERIES, MOVIE]  # invisible dropped, input order, deduplicated
        assert {k: v.id for k, v in preferred_versions(session, alice, [MOVIE, S1E1, SECRET_EPISODE]).items()} == {
            MOVIE: MOVIE_1080, S1E1: FILE[S1E1]}
