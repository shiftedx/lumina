"""Focused regressions for Jellyfin browse and search hot paths."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient

from app.models import User
from app.routers import jellyfin, jellyfin_integration
from app.services import jellyfin as jf
from app.services import semantic_discovery
from app.services.semantic_discovery import SemanticDiscovery


def test_unsorted_search_materializes_only_the_requested_page(monkeypatch) -> None:  # noqa: ANN001
    """Ranking may find 200 refs, but a 10-item page must build only those 10 DTOs."""
    refs = [f"title-{index:03d}" for index in range(200)]
    materialized: list[list[str]] = []

    monkeypatch.setattr(jellyfin_integration, "search_refs", lambda *_args, **_kwargs: refs)

    class Mapper:
        def __init__(self, *_args, **_kwargs):
            pass

        def by_ids(self, wanted: list[str]) -> list[dict]:
            materialized.append(wanted)
            return [{"Id": ref, "Name": ref} for ref in wanted]

    monkeypatch.setattr(jf, "JellyfinMapper", Mapper)
    result = jellyfin_integration.search_items(
        None, User(id="member", username="member", display_name="Member", role="viewer", is_active=True),
        jf.ItemsQuery(searchterm="movie", startindex=50, limit=10),
    )

    assert materialized == [refs[50:60]]
    assert [row["Id"] for row in result["Items"]] == refs[50:60]
    assert (result["TotalRecordCount"], result["StartIndex"]) == (200, 50)


def test_sorted_search_materializes_before_sorting_and_keeps_rendered_total(monkeypatch) -> None:  # noqa: ANN001
    """A client sort still applies globally, including the defensive by_ids filtering."""
    refs = ["charlie", "removed", "alpha", "bravo"]
    materialized: list[list[str]] = []
    monkeypatch.setattr(jellyfin_integration, "search_refs", lambda *_args, **_kwargs: refs)

    class Mapper:
        def __init__(self, *_args, **_kwargs):
            pass

        def by_ids(self, wanted: list[str]) -> list[dict]:
            materialized.append(wanted)
            return [{"Id": ref, "Name": ref.title()} for ref in wanted if ref != "removed"]

    monkeypatch.setattr(jf, "JellyfinMapper", Mapper)
    result = jellyfin_integration.search_items(
        None, User(id="member", username="member", display_name="Member", role="viewer", is_active=True),
        jf.ItemsQuery(searchterm="movie", sortby=["SortName"], startindex=1, limit=1),
    )

    assert materialized == [refs]
    assert [row["Id"] for row in result["Items"]] == ["bravo"]
    assert (result["TotalRecordCount"], result["StartIndex"]) == (3, 1)


def test_cached_searches_do_not_serialize_ranking(monkeypatch) -> None:  # noqa: ANN001
    """The shared index lock protects cache mutation, not independent ranking work."""
    discovery = SemanticDiscovery()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    rendezvous = threading.Barrier(2)

    monkeypatch.setattr(semantic_discovery.embeddings, "serving", lambda _db: None)
    monkeypatch.setattr(discovery, "_candidate_documents", lambda *_args, **_kwargs: [])

    def concurrent_rank(*_args, **_kwargs):
        rendezvous.wait(timeout=0.5)
        return []

    monkeypatch.setattr(discovery, "_rank", concurrent_rank)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: discovery.search(None, member, "movie"), range(2)))

    assert all(result.matches == () for result in results)


def test_jellyfin_title_batch_skips_unused_artwork_loading(monkeypatch) -> None:  # noqa: ANN001
    """Jellyfin image tags use title sources directly; TitleArtwork is not read by its DTOs."""
    mapper = object.__new__(jf.JellyfinMapper)
    mapper.sources = False
    mapper.people = False
    mapper.db = None
    calls: list[dict] = []

    class Titles:
        def load(self, _user, _titles, **options):  # noqa: ANN001
            calls.append(options)
            return object()

    mapper.titles = Titles()
    mapper.user = object()
    monkeypatch.setattr(mapper, "title_dto", lambda *_args, **_kwargs: {})
    title = SimpleNamespace(id="title", metadata_json={}, field_sources={}, locked=False)

    assert mapper.title_dtos([title]) == [{"LockData": False, "LockedFields": []}]
    assert calls == [{"with_artifacts": False, "with_metadata": False, "with_artwork": False}]


def test_item_requests_admit_at_most_two_database_workers() -> None:
    """A burst waits before dependencies and the sync worker pool, not inside SQLite."""
    app = FastAPI()
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    @app.get("/jellyfin/items")
    def items() -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active >= 2:
                entered.set()
        try:
            assert release.wait(timeout=2)
            return {"Items": []}
        finally:
            with lock:
                active -= 1

    app.add_middleware(jellyfin.JellyfinPathMiddleware, root_segments=frozenset({"items"}))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(client.get, "/Items") for _ in range(8)]
        try:
            assert entered.wait(timeout=1)
            time.sleep(0.05)
            assert peak == 2
        finally:
            release.set()
        assert [future.result(timeout=2).status_code for future in futures] == [200] * 8


def test_item_gate_releases_slots_after_route_exceptions() -> None:
    """Two failed item requests release both slots for the queued request."""
    app = FastAPI()
    failed = threading.Event()
    release = threading.Event()
    successor = threading.Event()
    lock = threading.Lock()
    calls = 0

    @app.get("/jellyfin/items")
    def items() -> dict:
        nonlocal calls
        with lock:
            calls += 1
            call = calls
            if calls >= 2:
                failed.set()
        if call <= 2:
            assert release.wait(timeout=2)
            raise RuntimeError("expected")
        successor.set()
        return {"Items": []}

    app.add_middleware(jellyfin.JellyfinPathMiddleware, root_segments=frozenset({"items"}))
    with TestClient(app, raise_server_exceptions=False) as client, ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.get, "/Items") for _ in range(3)]
        try:
            assert failed.wait(timeout=1)
            assert not successor.wait(timeout=0.05)
        finally:
            release.set()
        assert sorted(future.result(timeout=2).status_code for future in futures) == [200, 500, 500]


def test_item_burst_does_not_hold_database_slots_needed_by_other_routes() -> None:
    """Current-user, system-info and progress calls complete while 32 item calls are queued."""
    app = FastAPI()
    database_slots = threading.BoundedSemaphore(15)  # SQLAlchemy's 5 + 10 overflow default
    item_release = threading.Event()
    two_items = threading.Event()
    lock = threading.Lock()
    active_items = 0

    def database_session():  # noqa: ANN202
        assert database_slots.acquire(timeout=2)
        try:
            yield
        finally:
            database_slots.release()

    @app.get("/jellyfin/items")
    def items(_db=Depends(database_session)) -> dict:  # noqa: B008, ANN001
        nonlocal active_items
        with lock:
            active_items += 1
            if active_items >= 2:
                two_items.set()
        try:
            assert item_release.wait(timeout=3)
            return {"Items": []}
        finally:
            with lock:
                active_items -= 1

    @app.get("/jellyfin/users/member")
    def current_user(_db=Depends(database_session)) -> dict:  # noqa: B008, ANN001
        return {"Id": "member"}

    @app.get("/jellyfin/system/info/public")
    def system_info(_db=Depends(database_session)) -> dict:  # noqa: B008, ANN001
        return {"ServerName": "Lumina"}

    @app.post("/jellyfin/sessions/playing/progress", status_code=204)
    def progress(_db=Depends(database_session)) -> Response:  # noqa: B008, ANN001
        return Response(status_code=204)

    app.add_middleware(
        jellyfin.JellyfinPathMiddleware,
        root_segments=frozenset({"items", "sessions", "system", "users"}),
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=35) as pool:
        item_futures = [pool.submit(client.get, "/Items") for _ in range(32)]
        try:
            assert two_items.wait(timeout=1)
            controls = [
                pool.submit(client.get, "/Users/member"),
                pool.submit(client.get, "/System/Info/Public"),
                pool.submit(client.post, "/Sessions/Playing/Progress"),
            ]
            assert [future.result(timeout=1).status_code for future in controls] == [200, 200, 204]
        finally:
            item_release.set()
        assert all(future.result(timeout=3).status_code == 200 for future in item_futures)
