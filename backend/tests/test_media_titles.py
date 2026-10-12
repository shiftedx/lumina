from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import LibraryItem, MediaTitle, User
from app.services.library import LibraryService
from app.services.media_titles import (
    apply_field,
    from_ticks,
    jellyfin_id,
    parse_item_id,
    synthetic_id,
    to_ticks,
)
from support import make_user

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def test_ids_round_trip_and_reject_garbage() -> None:
    assert jellyfin_id(ID) == "0f8fad5bd9cb469fa16570867728950e"
    for client_form in (ID, jellyfin_id(ID), jellyfin_id(ID).upper(), "{" + ID + "}"):
        assert parse_item_id(client_form) == ID
    for garbage in ("", "../etc/passwd", "0f8fad5b", ID + "0", None, 42):
        assert parse_item_id(garbage) is None
    assert synthetic_id("view:movies") == synthetic_id("view:movies") != synthetic_id("view:tvshows")
    assert uuid.UUID(synthetic_id("tmdb-person:31")).version == 5


def test_ticks() -> None:
    assert to_ticks(1.5) == 15_000_000
    assert from_ticks(15_000_000) == 1.5
    assert to_ticks(None) is None and from_ticks(None) is None


def _title(**fields) -> MediaTitle:  # noqa: ANN003
    return MediaTitle(id=ID, type="movie", key="r:Movie", name="movie", field_sources={}, images={}, metadata_json={}, **fields)


@pytest.mark.parametrize(
    ("first", "second", "expected_name", "expected_source"),
    [
        ("path", "nfo", "second", "nfo"),     # higher rank wins
        ("nfo", "path", "first", "nfo"),      # lower rank never overwrites
        ("path", "tmdb", "second", "tmdb"),   # TMDB may replace a path-derived name
        ("nfo", "tmdb", "first", "nfo"),      # ...but never an NFO one
        ("user", "nfo", "first", "user"),     # a user edit survives every scan
        ("user", "tmdb", "first", "user"),
        ("nfo", "nfo", "second", "nfo"),      # same rank rewrites (NFO edited on disk)
        ("user", "user", "second", "user"),   # a user can change their own edit
    ],
)
def test_apply_field_precedence(first, second, expected_name, expected_source) -> None:  # noqa: ANN001
    title = _title()
    apply_field(title, "name", "first", first)
    apply_field(title, "name", "second", second)
    assert (title.name, title.field_sources["name"]) == (expected_name, expected_source)


def test_apply_field_targets_and_guards() -> None:
    title = _title()
    assert apply_field(title, "name", None, "user") is False  # None never overwrites
    assert apply_field(title, "overview", "A heist.", "nfo") is True
    assert apply_field(title, "overview", "A heist.", "nfo") is False  # unchanged
    assert apply_field(title, "images.Primary", {"path": "poster.jpg"}, "nfo") is True
    assert apply_field(title, "images.Primary", {"tmdb": "/x.jpg"}, "tmdb") is False  # local art outranks TMDB
    assert apply_field(title, "provider_ids", {}, "user") is True  # unmatch locks an empty set
    assert title.metadata_json == {"overview": "A heist."}
    assert title.images == {"Primary": {"path": "poster.jpg"}}
    assert title.field_sources == {"overview": "nfo", "images.Primary": "nfo", "provider_ids": "user"}
    with pytest.raises(ValueError):
        apply_field(title, "key", "r:Other", "user")  # structural columns are the scanner's, not a field
    with pytest.raises(KeyError):
        apply_field(title, "name", "x", "imdb")


def test_apply_field_reassigns_json_so_writes_persist(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add(_title())
        session.commit()
    with db_factory() as session:
        apply_field(session.get(MediaTitle, ID), "overview", "Persisted.", "tmdb")
        session.commit()
    with db_factory() as session:
        title = session.get(MediaTitle, ID)
        assert title.metadata_json == {"overview": "Persisted."} and title.field_sources == {"overview": "tmdb"}


def _seed(session) -> None:  # noqa: ANN001
    session.add_all([make_user("alice"), make_user("bob"), make_user("root", role="admin")])
    session.add_all([
        MediaTitle(id="series", type="series", key="r:Secret Show", name="Secret Show"),
        MediaTitle(id="season", type="season", parent_id="series", key="r:Secret Show#s1", name="Season 1", index_number=1),
        MediaTitle(id="episode", type="episode", parent_id="season", key="r:Secret Show#s1e1", name="Pilot", index_number=1),
        MediaTitle(id="boxset", type="boxset", key="set:saga", name="Saga"),
        MediaTitle(id="movie", type="movie", boxset_id="boxset", key="r:Movie (2020)", name="Movie"),
        MediaTitle(id="orphan", type="movie", key="r:Gone", name="Gone"),
        LibraryItem(id="ep-file", title="Pilot", user_id="alice", visibility="private", title_id="episode", kind="episode"),
        LibraryItem(id="movie-file", title="Movie", user_id="alice", visibility="shared", title_id="movie", kind="movie"),
    ])
    session.commit()


def _visible(session, user_id: str) -> set[str]:  # noqa: ANN001
    user = session.get(User, user_id)
    return set(session.scalars(select(MediaTitle.id).where(LibraryService.visible_title_predicate(user))))


def test_title_visibility_flows_up_from_items(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _seed(session)
        assert _visible(session, "alice") == {"series", "season", "episode", "movie", "boxset"}
        # A private member's show name never leaks: not to members, not to admins.
        assert _visible(session, "bob") == {"movie", "boxset"}
        assert _visible(session, "root") == {"movie", "boxset"}
        # A tombstoned file makes its titles invisible; titles themselves are never deleted.
        session.get(LibraryItem, "movie-file").status = "missing"
        session.commit()
        assert _visible(session, "bob") == set()
        # An extra linked to the series makes only the series visible (the extra itself is shared).
        session.add(LibraryItem(id="trailer", title="Secret Show trailer", user_id="alice", visibility="shared",
                                title_id="series", extra_type="trailer", kind="video"))
        session.commit()
        assert _visible(session, "bob") == {"series"}
