"""Library gallery contract: category defaults and SQL, and the shared gallery fixture."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import bindparam, select, text

from app.db import ANIME_DEFAULT_MATCH, CATEGORY_SCOPES, CATEGORY_SQL
from app.media_schemas import CLIENT_METRIC_LABELS, MediaServerSettingsUpdate
from app.models import LibraryItem, MediaTitle, User
from app.services.client_metrics import BUDGETS
from support import make_user
from title_support import (
    ALBUM, ALICE, ANIME_EPISODE, ANIME_MOVIE, ANIME_SEASON, ANIME_SERIES, ARTIST, BOB, BOXSET, MOVIE, SERIES, TRACKS,
    seed_gallery, seed_tree,
)


@pytest.fixture
def gallery(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        session.commit()
    return db_factory


def test_a_new_title_takes_its_types_category_until_the_sql_decides(gallery) -> None:  # noqa: ANN001
    with gallery() as session:
        category = dict(session.execute(select(MediaTitle.id, MediaTitle.category)).all())
    assert (category[MOVIE], category[SERIES]) == ("movies", "shows")
    assert (category[BOXSET], category[ALBUM], category[ARTIST]) == (None, None, None)
    assert {category[i] for i in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, ANIME_MOVIE)} == {"anime"}  # seed_gallery sets it


def test_the_gallery_fixture_holds_an_album_of_three_tracks(gallery) -> None:  # noqa: ANN001
    with gallery() as session:
        album = session.get(MediaTitle, ALBUM)
        tracks = session.scalars(select(LibraryItem).where(LibraryItem.title_id == ALBUM).order_by(LibraryItem.id)).all()
        assert (album.type, album.parent_id, album.year, album.key) == ("album", ARTIST, 2019, "album:artist a/album one")
        assert [item.id for item in tracks] == sorted(TRACKS)
        assert {item.kind for item in tracks} == {"track"}
        assert [(item.metadata_json["disc_number"], item.metadata_json["track_number"]) for item in tracks] == [(1, 1), (1, 2), (2, 1)]


def test_scoped_category_statements_touch_only_the_given_titles(gallery) -> None:  # noqa: ANN001
    """The shape categories.refresh_category uses: CATEGORY_SQL with a match and CATEGORY_SCOPES' expanding ids."""
    with gallery() as session:
        session.execute(text("UPDATE media_titles SET category = 'shows' WHERE id IN (:a, :b, :c)"), {"a": ANIME_SERIES, "b": ANIME_SEASON, "c": ANIME_EPISODE})
        session.execute(text("UPDATE media_titles SET category = 'movies' WHERE id = :m"), {"m": ANIME_MOVIE})
        session.execute(text("UPDATE media_titles SET category = 'anime' WHERE id = :s"), {"s": SERIES})  # wrong, but outside the scope
        ids = {"movie_ids": [ANIME_MOVIE], "series_ids": [ANIME_SERIES]}
        for statement, scope in zip(CATEGORY_SQL, CATEGORY_SCOPES, strict=True):
            names = [name for name in ids if f":{name}" in scope]
            clause = text(statement.format(match=ANIME_DEFAULT_MATCH, scope=scope)).bindparams(*(bindparam(name, expanding=True) for name in names))
            session.execute(clause, {name: ids[name] for name in names})
        session.commit()
        category = dict(session.execute(select(MediaTitle.id, MediaTitle.category)).all())
    assert {category[i] for i in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, ANIME_MOVIE)} == {"anime"}
    assert category[SERIES] == "anime"  # untouched
    assert category[MOVIE] == "movies"


@pytest.mark.parametrize("folders", [[""], ["   "], ["TV/Anime"], ["TV\\Anime"], ["x" * 65], ["."], [".."], [f"folder {n}" for n in range(21)]])
def test_anime_folders_refuse_what_the_settings_row_refuses(folders) -> None:  # noqa: ANN001
    """Server side: empty, a path, over 64 characters, . or .., a 21st name."""
    with pytest.raises(ValidationError):
        MediaServerSettingsUpdate.model_validate({"anime_folders": folders})


def test_anime_folders_are_trimmed_and_deduped_keeping_the_first_spelling() -> None:
    assert MediaServerSettingsUpdate.model_validate({"anime_folders": [" Anime ", "ANIME", "Donghua"]}).anime_folders == ["Anime", "Donghua"]
    assert MediaServerSettingsUpdate.model_validate({"anime_folders": []}).anime_folders == []  # nothing is anime


def test_every_wall_metric_label_has_a_budget() -> None:
    """A label the server does not know rejects a whole metrics batch (422); every wall is known and budgeted."""
    for metric in ("wall_first_screen_ms", "wall_sharp_ms"):
        assert {"movies", "shows", "anime", "albums", "artists"} <= CLIENT_METRIC_LABELS[metric]
        assert {(metric, label) for label in CLIENT_METRIC_LABELS[metric]} <= set(BUDGETS)
    assert {"all", "youtube", "recordings"} <= CLIENT_METRIC_LABELS["wall_first_screen_ms"]
    assert {"square:hit", "square:net"} <= CLIENT_METRIC_LABELS["image_load_ms"]


def test_title_summaries_carry_their_category(gallery, api_client) -> None:  # noqa: ANN001
    with gallery() as session:
        alice = session.get(User, ALICE)
    client = api_client(user=alice, base_url="http://localhost")
    categories = {title_id: client.get(f"/api/titles/{title_id}").json()["category"] for title_id in (MOVIE, SERIES, ANIME_MOVIE, ANIME_EPISODE)}
    assert categories == {MOVIE: "movies", SERIES: "shows", ANIME_MOVIE: "anime", ANIME_EPISODE: "anime"}
