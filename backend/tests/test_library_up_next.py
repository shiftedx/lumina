"""The watch page's Up next for a Library item: following episodes in order, a movie's collection, else none."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import LibraryItem, MediaTitle, PlaybackProgress, User
from support import make_user
from title_support import (
    ALICE, BOB, BOXSET, CHANNEL_OLD, FILE, MOVIE, MOVIE_1080, ROOT, S0E1, S1E1, S1E2, S2E1, SECRET_EPISODE, SERIES, T0,
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


def up_next(api_client, factory, user_id: str, item_id: str):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost").get(f"/api/library/{item_id}/up-next")


def test_an_episode_lists_the_rest_of_its_season_then_later_seasons_without_specials(library, api_client) -> None:  # noqa: ANN001
    body = up_next(api_client, library[0], ALICE, FILE[S1E1]).json()
    assert (body["kind"], body["title"], body["title_id"], body["current_id"]) == ("episodes", "Show", SERIES, S1E1)
    assert [(e["id"], e["season_number"], e["index_number"], e["play_item_id"]) for e in body["items"]] == [
        (S1E2, 1, 2, FILE[S1E2]), (S2E1, 2, 1, FILE[S2E1]),
    ]
    assert body["items"][0]["runtime_seconds"] == 1500
    assert [e["id"] for e in up_next(api_client, library[0], ALICE, FILE[S2E1]).json()["items"]] == []


def test_a_special_continues_into_the_regular_seasons(library, api_client) -> None:  # noqa: ANN001
    assert [e["id"] for e in up_next(api_client, library[0], ALICE, FILE[S0E1]).json()["items"]] == [S1E1, S1E2, S2E1]


def test_a_missing_episode_is_skipped(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    with factory() as session:
        session.get(LibraryItem, FILE[S1E2]).status = "missing"
        session.commit()
    assert [e["id"] for e in up_next(api_client, factory, ALICE, FILE[S1E1]).json()["items"]] == [S2E1]


def test_the_version_family_already_playing_continues_unless_another_version_was_started(library, api_client) -> None:  # noqa: ANN001
    factory, root = library
    later = T0 + timedelta(days=5)  # added after the plain files, so the plain file stays the preferred version
    with factory() as session:
        add_file(session, root, "Show/Season 01/Show S01E01 - 4K.mkv", item_id=uid(211), title_id=S1E1, owner=ALICE, title="Show S01E01 · 4K", created_at=later)
        add_file(session, root, "Show/Season 01/Show S01E02 - 4K.mkv", item_id=uid(212), title_id=S1E2, owner=ALICE, title="Show S01E02 · 4K", created_at=later)
        session.commit()
    assert up_next(api_client, factory, ALICE, uid(211)).json()["items"][0]["play_item_id"] == uid(212)
    assert up_next(api_client, factory, ALICE, FILE[S1E1]).json()["items"][0]["play_item_id"] == FILE[S1E2]
    with factory() as session:
        session.add(PlaybackProgress(id=uid(900_001), user_id=ALICE, item_id=FILE[S1E2], position_seconds=300, duration_seconds=1500, completed=False))
        session.commit()
    first = up_next(api_client, factory, ALICE, uid(211)).json()["items"][0]
    assert (first["play_item_id"], first["user_data"]["position_seconds"]) == (FILE[S1E2], 300)


def test_another_members_private_episode_is_never_listed_and_their_file_is_404(library, api_client) -> None:  # noqa: ANN001
    factory, _root = library
    with factory() as session:
        session.get(LibraryItem, FILE[S1E2]).visibility = "private"
        session.commit()
    assert [e["id"] for e in up_next(api_client, factory, BOB, FILE[S1E1]).json()["items"]] == [S2E1]
    assert [e["id"] for e in up_next(api_client, factory, ALICE, FILE[S1E1]).json()["items"]] == [S1E2, S2E1]
    assert up_next(api_client, factory, ALICE, FILE[SECRET_EPISODE]).status_code == 404


def test_a_movie_lists_its_collection_in_order_and_alone_has_none(library, api_client) -> None:  # noqa: ANN001
    factory, root = library
    assert up_next(api_client, factory, ALICE, MOVIE_1080).json() == {"kind": "none", "title": None, "title_id": None, "current_id": None, "items": []}
    with factory() as session:
        for title_id, item_id, name, year in ((uid(21), uid(221), "Sequel", 2022), (uid(22), uid(222), "Prequel", 2018)):
            session.add(MediaTitle(id=title_id, type="movie", key=f"{ROOT}:{name}", root_id=ROOT, name=name, year=year, boxset_id=BOXSET))
            add_file(session, root, f"{name}/{name}.mkv", item_id=item_id, title_id=title_id, owner=ALICE, title=name, kind="movie")
        session.commit()
    body = up_next(api_client, factory, ALICE, MOVIE_1080).json()
    assert (body["kind"], body["title"], body["title_id"], body["current_id"]) == ("collection", "Saga", BOXSET, MOVIE)
    assert [e["name"] for e in body["items"]] == ["Prequel", "Movie", "Sequel"]


def test_an_untitled_video_has_none(library, api_client) -> None:  # noqa: ANN001
    assert up_next(api_client, library[0], ALICE, CHANNEL_OLD).json()["kind"] == "none"
