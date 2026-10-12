"""Default movie cards may be reused only while their database and caller scope match."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle, MemberAccess, User
from app.services import jellyfin as jf, member_access
from test_jellyfin_api import get
from title_support import ALICE, MOVIE, jellyfin_household


@pytest.fixture
def client(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    value = TestClient(app, base_url="http://localhost")
    yield value
    value.close()


def test_movie_search_reuses_rendering_and_invalidates_metadata_and_access(client, monkeypatch) -> None:  # noqa: ANN001
    calls = []
    original = jf.JellyfinMapper.by_ids

    def counted(self, refs):  # noqa: ANN001, ANN202
        calls.append(tuple(refs))
        return original(self, refs)

    monkeypatch.setattr(jf.JellyfinMapper, "by_ids", counted)
    query = {"SearchTerm": "Movie", "IncludeItemTypes": "Movie", "Limit": 60}
    bodies = [get(client, "/Items", **query).json() for _ in range(6)]
    assert all(body == bodies[0] for body in bodies)
    assert bodies[0]["Items"] and len(calls) < 6

    with db_module.SessionLocal() as db:
        db.get(MediaTitle, MOVIE).name = "Updated Movie"
        db.commit()
    updated = get(client, "/Items", **query).json()
    assert updated["Items"][0]["Name"] == "Updated Movie"

    with db_module.SessionLocal() as db:
        db.add(MemberAccess(user_id=ALICE, sections=["shows"]))
        db.commit()
    member_access.invalidate(ALICE)
    assert get(client, "/Items", **query).json() == {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
    with db_module.SessionLocal() as db:
        db.get(User, ALICE).is_active = False
        db.commit()
    assert get(client, "/Items", **query).status_code == 401


def test_cached_pages_copy_results_and_bound_encoded_memory() -> None:
    from types import SimpleNamespace
    from app.services import jellyfin_discovery as search
    from support import memory_session_factory, seed_app_settings

    with memory_session_factory()() as db:
        seed_app_settings(db)
        reader = User(id="card-reader", role="viewer")
        reader.__dict__[member_access.ACCESS_ATTR] = None
        mapper = SimpleNamespace(server_id="public-server-id", scope="")
        calls = []
        query = jf.ItemsQuery(searchterm="movie", includeitemtypes=["Movie"], limit=60)

        def render() -> dict:
            calls.append(True)
            return {"Items": [{"Name": "Original"}], "TotalRecordCount": 1, "StartIndex": 0}

        first = search.cached_search_page(db, reader, query, mapper, render)
        first["Items"][0]["Name"] = "Caller mutation"
        assert search.cached_search_page(db, reader, query, mapper, render)["Items"][0]["Name"] == "Original"
        assert len(calls) == 1
        for index in range(10):
            query.startindex = index
            search.cached_search_page(db, reader, query, mapper, render)
        pages = db.connection().info[search._SEARCH_PAGE_CACHE][1]
        assert len(pages) == search.SEARCH_PAGE_CACHE_ENTRIES
        assert sum(len(value) for value in pages.values()) <= search.SEARCH_PAGE_CACHE_ENTRIES * search.SEARCH_PAGE_CACHE_BYTES

        query.searchterm = "oversized"
        def large() -> dict:
            calls.append(True)
            return {"Items": [{"Overview": "x" * search.SEARCH_PAGE_CACHE_BYTES}]}
        before = len(calls)
        search.cached_search_page(db, reader, query, mapper, large)
        search.cached_search_page(db, reader, query, mapper, large)
        assert len(calls) == before + 2

        query.fields = ["MediaSources"]
        before = len(calls)
        search.cached_search_page(db, reader, query, mapper, render)
        search.cached_search_page(db, reader, query, mapper, render)
        assert len(calls) == before + 2


def test_different_search_terms_reuse_overlapping_movie_cards(client, monkeypatch) -> None:  # noqa: ANN001
    from app.routers.jellyfin_integration import search_items
    from app.services.library_search import index_title

    calls = []
    original = jf.JellyfinMapper.by_ids

    def counted(self, refs):  # noqa: ANN001, ANN202
        calls.append(tuple(refs))
        return original(self, refs)

    monkeypatch.setattr(jf.JellyfinMapper, "by_ids", counted)
    with db_module.SessionLocal() as db:
        movie = db.get(MediaTitle, MOVIE)
        movie.name = "Aurora Movie"
        db.flush()
        index_title(db, movie)
        db.commit()
        source = db.get(User, ALICE)
        reader = member_access.carry_access(db, User(id=source.id, role=source.role), source)
        first = search_items(db, reader, jf.ItemsQuery(searchterm="Aurora", includeitemtypes=["Movie"]))
        second = search_items(db, reader, jf.ItemsQuery(searchterm="Movie", includeitemtypes=["Movie"]))
        assert first["Items"] and first["Items"] == second["Items"]
        assert calls == [(MOVIE,)], "overlapping queries should render each unchanged card once"
        first["Items"][0]["Name"] = "Caller mutation"
        third = search_items(db, reader, jf.ItemsQuery(searchterm="Aurora Movie", includeitemtypes=["Movie"]))
        assert third["Items"][0]["Name"] == "Aurora Movie"
        assert calls == [(MOVIE,)]

        movie.name = "Aurora Updated Movie"
        db.flush()
        index_title(db, movie)
        db.commit()
        updated = search_items(db, reader, jf.ItemsQuery(searchterm="Updated", includeitemtypes=["Movie"]))
        assert updated["Items"][0]["Name"] == "Aurora Updated Movie"
        assert calls == [(MOVIE,), (MOVIE,)]


def test_card_reuse_is_bounded_and_bypasses_other_projections() -> None:
    from types import SimpleNamespace
    from app.services import jellyfin_discovery as search
    from support import memory_session_factory, seed_app_settings
    from title_support import uid

    with memory_session_factory()() as db:
        seed_app_settings(db)
        reader = User(id="card-reader", role="viewer")
        reader.__dict__[member_access.ACCESS_ATTR] = None
        calls = []

        def render(refs):  # noqa: ANN001, ANN202
            calls.append(tuple(refs))
            return [{"Id": jf.jid(ref), "Name": ref, "Overview": "é" * 32_768} for ref in refs]

        mapper = SimpleNamespace(server_id="public-server-id", scope="", by_ids=render)
        query = jf.ItemsQuery(searchterm="movie", includeitemtypes=["Movie"])
        first = search.cached_search_cards(db, reader, query, mapper, [uid(1), uid(2)])
        first[0]["Name"] = "Caller mutation"
        second = search.cached_search_cards(db, reader, query, mapper, [uid(2), uid(1), uid(3)])
        assert [dto["Name"] for dto in second] == [uid(2), uid(1), uid(3)]
        assert calls == [(uid(1), uid(2)), (uid(3),)]
        for index in range(4, 20):
            search.cached_search_cards(db, reader, query, mapper, [uid(index)])
        cards = db.connection().info[search._SEARCH_CARD_CACHE][1]
        assert sum(len(value) for value in cards.values()) <= search.SEARCH_CARD_CACHE_BYTES
        assert len(cards) < 20

        mapper.by_ids = lambda refs: [{"Id": jf.jid(ref), "Name": ref} for ref in refs]
        for index in range(20, 20 + search.SEARCH_CARD_CACHE_ENTRIES + 1):
            search.cached_search_cards(db, reader, query, mapper, [uid(index)])
        assert len(cards) == search.SEARCH_CARD_CACHE_ENTRIES
        assert sum(len(value) for value in cards.values()) <= search.SEARCH_CARD_CACHE_BYTES
        mapper.by_ids = render

        for fields, sortby in [(["MediaSources"], []), ([], ["SortName"])]:
            query.fields, query.sortby = fields, sortby
            before = len(calls)
            search.cached_search_cards(db, reader, query, mapper, [uid(19)])
            search.cached_search_cards(db, reader, query, mapper, [uid(19)])
            assert len(calls) == before + 2

        query.fields, query.sortby = [], []
        mapper.by_ids = lambda refs: [{"Id": jf.jid(ref), "Overview": "x" * search.SEARCH_CARD_CACHE_BYTES} for ref in refs]
        search.cached_search_cards(db, reader, query, mapper, [uid(999)])
        assert all(key[1] != jf.jid(uid(999)) for key in cards)


def test_card_reuse_invalidates_scope_writes_and_rollback() -> None:
    from types import SimpleNamespace
    from sqlalchemy import update
    from app.services import jellyfin_discovery as search
    from support import memory_session_factory, seed_app_settings

    with memory_session_factory()() as db:
        seed_app_settings(db)
        movie = MediaTitle(id=MOVIE, type="movie", key="test:card", name="Original", category="movies")
        db.add(movie)
        db.commit()
        reader = User(id="card-reader", role="viewer")
        reader.__dict__[member_access.ACCESS_ATTR] = None
        calls = []

        def render(refs):  # noqa: ANN001, ANN202
            calls.append(tuple(refs))
            name = db.query(MediaTitle.name).filter(MediaTitle.id == MOVIE).scalar()
            return [{"Id": jf.jid(ref), "Name": name} for ref in refs]

        mapper = SimpleNamespace(server_id="public-server-id", scope="", by_ids=render)
        query = jf.ItemsQuery(searchterm="movie", includeitemtypes=["Movie"])
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        assert len(calls) == 1
        for changes in [dict(id="other-reader"), dict(role="admin")]:
            other = User(id=changes.get("id", reader.id), role=changes.get("role", reader.role))
            other.__dict__[member_access.ACCESS_ATTR] = None
            search.cached_search_cards(db, other, query, mapper, [MOVIE])
        reader.__dict__[member_access.ACCESS_ATTR] = member_access.EffectiveAccess(sections=frozenset({"movies"}))
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        assert len(calls) == 4
        mapper.scope = "different-art-scope"
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        mapper.server_id = "different-signing-key-id"
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        assert len(calls) == 6

        db.execute(update(MediaTitle).where(MediaTitle.id == MOVIE).values(name="Uncommitted"))
        assert search.cached_search_cards(db, reader, query, mapper, [MOVIE])[0]["Name"] == "Uncommitted"
        db.rollback()
        assert search.cached_search_cards(db, reader, query, mapper, [MOVIE])[0]["Name"] == "Original"
        assert len(calls) == 8
        movie.name = "Pending edit"
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        search.cached_search_cards(db, reader, query, mapper, [MOVIE])
        assert len(calls) == 10
