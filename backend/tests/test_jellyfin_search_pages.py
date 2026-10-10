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
