"""Focused regressions for Jellyfin browse and search hot paths."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.models import User
from app.routers import jellyfin_integration
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
