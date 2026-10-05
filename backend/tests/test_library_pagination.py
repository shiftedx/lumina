from datetime import datetime

import pytest

from sqlalchemy import event

from app.models import LibraryItem
from app.services.library import LibraryService
from support import make_user


def _item(item_id: str, *, title: str | None = None, user_id: str | None = "member-1", visibility: str = "shared", downloaded_at: datetime | None = None, created_at: datetime = datetime(2026, 1, 1), metadata: dict | None = None, status: str = "available") -> LibraryItem:
    metadata = metadata if metadata is not None else {"id": item_id}
    return LibraryItem(
        id=item_id,
        user_id=user_id,
        visibility=visibility,
        extractor="YouTube",
        remote_id=item_id,
        title=title or f"Item {item_id}",
        duration=60,
        downloaded_at=downloaded_at,
        created_at=created_at,
        updated_at=created_at,
        metadata_json=metadata,
        metadata_summary=LibraryService.summarize_metadata(metadata),
        status=status,
    )


@pytest.fixture
def library_api(db_factory, api_client):
    member = make_user("member-1", username="alice", display_name="Alice")
    friend = make_user("member-2", username="bob", display_name="Bob")
    admin = make_user("admin-1", role="admin", username="root", display_name="Root")
    with db_factory.begin() as session:
        session.add_all([member, friend, admin])

    active_user = {"value": member}
    client = api_client(user=lambda: active_user["value"], base_url="http://localhost")
    return client, db_factory.kw["bind"], db_factory, active_user, member, friend, admin


def collect_all_pages(client, params: dict | None = None) -> list[str]:  # noqa: ANN001
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        query = dict(params or {})
        if cursor is not None:
            query["cursor"] = cursor
        response = client.get("/api/library", params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("Pagination never terminated")


def test_pages_are_deterministically_ordered_across_null_downloads_and_ties(library_api) -> None:
    client, _engine, session_factory, _active, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item("recent", downloaded_at=datetime(2026, 3, 5), created_at=datetime(2026, 1, 5)),
            _item("older", downloaded_at=datetime(2026, 3, 1), created_at=datetime(2026, 1, 4)),
            _item("tie-a", downloaded_at=datetime(2026, 3, 3), created_at=datetime(2026, 1, 3)),
            _item("tie-b", downloaded_at=datetime(2026, 3, 3), created_at=datetime(2026, 1, 3)),
            _item("never-downloaded-new", downloaded_at=None, created_at=datetime(2026, 2, 2)),
            _item("never-downloaded-old", downloaded_at=None, created_at=datetime(2026, 2, 1)),
        ])

    expected = ["recent", "tie-b", "tie-a", "older", "never-downloaded-new", "never-downloaded-old"]
    assert collect_all_pages(client, {"limit": 2}) == expected
    assert collect_all_pages(client, {"limit": 4}) == expected
    single = client.get("/api/library", params={"limit": 100}).json()
    assert [item["id"] for item in single["items"]] == expected
    assert single["next_cursor"] is None


def test_cursor_round_trip_produces_no_gaps_or_overlaps(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"item-{index:03d}", downloaded_at=datetime(2026, 3, 1, index % 5), created_at=datetime(2026, 1, 1, index // 5))
            for index in range(25)
        ])

    first = client.get("/api/library", params={"limit": 10}).json()
    assert len(first["items"]) == 10
    assert first["next_cursor"] is not None
    second = client.get("/api/library", params={"limit": 10, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 10
    third = client.get("/api/library", params={"limit": 10, "cursor": second["next_cursor"]}).json()
    assert len(third["items"]) == 5
    assert third["next_cursor"] is None
    collected = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert len(collected) == len(set(collected)) == 25


def test_invalid_cursor_is_a_bad_request(library_api) -> None:
    client, *_ = library_api
    assert client.get("/api/library", params={"cursor": "not-base64!"}).status_code == 400
    assert client.get("/api/library", params={"cursor": "aGVsbG8"}).status_code == 400
    assert client.get("/api/library", params={"search": "anything", "cursor": "aGVsbG8"}).status_code == 400


def test_page_size_is_clamped_to_the_contract_bounds(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"clamp-{index:03d}", downloaded_at=datetime(2026, 3, 1, 0, index % 60), created_at=datetime(2026, 1, 1))
            for index in range(120)
        ])

    oversized = client.get("/api/library", params={"limit": 500}).json()
    assert len(oversized["items"]) == 100
    undersized = client.get("/api/library", params={"limit": 0}).json()
    assert len(undersized["items"]) == 1
    default = client.get("/api/library").json()
    assert len(default["items"]) == 60


def test_visibility_rules_hold_on_every_page_for_each_role(library_api) -> None:
    client, _engine, session_factory, active_user, member, friend, admin = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item("shared-item", user_id=member.id, visibility="shared", downloaded_at=datetime(2026, 3, 4)),
            _item("member-private", user_id=member.id, visibility="private", downloaded_at=datetime(2026, 3, 3)),
            _item("friend-private", user_id=friend.id, visibility="private", downloaded_at=datetime(2026, 3, 2)),
            _item("ownerless-private", user_id=None, visibility="private", downloaded_at=datetime(2026, 3, 1)),
        ])

    assert collect_all_pages(client, {"limit": 1}) == ["shared-item", "member-private"]
    active_user["value"] = friend
    assert collect_all_pages(client, {"limit": 1}) == ["shared-item", "friend-private"]
    active_user["value"] = admin
    assert collect_all_pages(client, {"limit": 1}) == ["shared-item", "ownerless-private"]


def test_list_pages_serve_stored_summaries_and_never_load_full_metadata(library_api) -> None:
    client, engine, session_factory, _active, member, *_ = library_api
    metadata = {
        "id": "summary-item",
        "categories": ["Music"],
        "formats": [{}, {}, {}],
        "subtitles": {"en": [{}]},
        "description": "never in list payloads",
    }
    with session_factory.begin() as session:
        session.add(_item("summary-item", downloaded_at=datetime(2026, 3, 1), metadata=metadata))

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        page = client.get("/api/library").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    item = page["items"][0]
    assert item["metadata_json"]["formats_count"] == 3
    assert item["metadata_json"]["categories"] == ["Music"]
    assert "formats" not in item["metadata_json"]
    assert "description" not in item["metadata_json"]
    library_statements = [statement for statement in statements if "library_items" in statement]
    assert library_statements, "expected the page to query library_items"
    assert all("metadata_json" not in statement for statement in library_statements)

    detail = client.get("/api/library/summary-item").json()
    assert "formats" not in detail["metadata_json"]  # raw formats stay server-side
    assert detail["metadata_json"]["description"] == "never in list payloads"


def test_owner_display_fields_resolve_with_one_users_query_per_page(library_api) -> None:
    client, engine, session_factory, _active, member, friend, _admin = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"owned-{index:02d}", user_id=member.id if index % 2 else friend.id, downloaded_at=datetime(2026, 3, 1, index))
            for index in range(10)
        ])

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        page = client.get("/api/library").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(page["items"]) == 10
    assert {item["owner_username"] for item in page["items"]} == {"alice", "bob"}
    user_queries = [statement for statement in statements if "FROM users" in statement]
    assert len(user_queries) <= 1


def test_search_results_arrive_in_the_same_page_shape(library_api) -> None:
    from app.services.library_search import ensure_search_index

    client, engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"needle-{index}", title=f"Needle result {index}", downloaded_at=datetime(2026, 3, 1, index), created_at=datetime(2026, 1, 1))
            for index in range(5)
        ])
        session.add(_item("haystack", title="Unrelated", downloaded_at=datetime(2026, 3, 2)))
    # Items seeded straight into the table bypass the upsert write hook; build
    # the FTS corpus once, as startup recovery does.
    ensure_search_index(engine)

    first = client.get("/api/library", params={"search": "needle", "limit": 3}).json()
    assert set(first) == {"items", "next_cursor"}
    assert len(first["items"]) == 3
    assert first["next_cursor"] is not None
    second = client.get("/api/library", params={"search": "needle", "limit": 3, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None
    found = {item["id"] for page in (first, second) for item in page["items"]}
    assert found == {f"needle-{index}" for index in range(5)}


def _job(job_id: str, *, user_id: str = "member-1", status: str = "queued", created_at: datetime = datetime(2026, 1, 1)):
    from app.models import DownloadJob

    return DownloadJob(
        id=job_id,
        user_id=user_id,
        source_url=f"https://example.com/{job_id}",
        status=status,
        format_selection={},
        output_profile={},
        created_at=created_at,
    )


def test_job_pages_use_the_same_keyset_contract(library_api) -> None:
    client, _engine, session_factory, _active, member, friend, _admin = library_api
    with session_factory.begin() as session:
        session.add_all([
            _job(f"job-{index}", created_at=datetime(2026, 2, 1, index)) for index in range(7)
        ])
        session.add(_job("job-staged", status="staged", created_at=datetime(2026, 2, 2)))
        session.add(_job("job-foreign", user_id=friend.id, created_at=datetime(2026, 2, 3)))

    collected: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/api/jobs", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert set(page) == {"items", "next_cursor"}
        collected.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert collected == [f"job-{index}" for index in reversed(range(7))]
    assert client.get("/api/jobs", params={"cursor": "broken"}).status_code == 400

    default_page = client.get("/api/jobs").json()
    assert [item["id"] for item in default_page["items"]] == collected
    assert default_page["next_cursor"] is None


def test_acquisition_batch_serialization_resolves_outputs_with_one_query(library_api) -> None:
    from app.models import AcquisitionBatch, AcquisitionBatchEntry, AcquisitionJobOutput

    client, engine, session_factory, _active, member, *_ = library_api
    with session_factory.begin() as session:
        session.add(AcquisitionBatch(
            id="batch-1",
            user_id=member.id,
            source_url="https://example.com/playlist",
            status="queued",
            selected_count=5,
        ))
        for index in range(5):
            session.add(AcquisitionBatchEntry(
                id=f"entry-{index}",
                batch_id="batch-1",
                user_id=member.id,
                selection_index=index,
                source_url=f"https://example.com/media/{index}",
                source_identity=f"identity-{index}",
                status="queued",
            ))
        for index in range(3):
            session.add(AcquisitionJobOutput(
                id=f"output-{index}",
                batch_entry_id=f"entry-{index}",
                user_id=member.id,
                download_job_id=f"job-{index}",
                library_item_id=f"item-{index}" if index == 0 else None,
            ))

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        response = client.get("/api/acquisition-batches/batch-1")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert [entry["download_job_id"] for entry in entries] == ["job-0", "job-1", "job-2", None, None]
    assert entries[0]["library_item_id"] == "item-0"
    output_queries = [statement for statement in statements if "acquisition_job_outputs" in statement]
    assert len(output_queries) == 1, output_queries


def test_household_collection_detail_bounds_items_while_count_stays_accurate(library_api) -> None:
    from app.models import HouseholdCollection, HouseholdCollectionMembership

    client, _engine, session_factory, _active, member, friend, _admin = library_api
    with session_factory.begin() as session:
        session.add(HouseholdCollection(
            id="collection-1",
            owner_user_id=member.id,
            name="Big shelf",
            name_key="big shelf",
            visibility="private",
        ))
        for index in range(105):
            item_id = f"collected-{index:03d}"
            session.add(_item(item_id, downloaded_at=datetime(2026, 3, 1), created_at=datetime(2026, 1, 1)))
            session.add(HouseholdCollectionMembership(
                id=f"membership-{index:03d}",
                collection_id="collection-1",
                library_item_id=item_id,
                added_by_user_id=member.id,
                created_at=datetime(2026, 2, 1, 0, index % 60, index // 60),
            ))
        session.add(_item("invisible-item", user_id=friend.id, visibility="private"))
        session.add(HouseholdCollectionMembership(
            id="membership-invisible",
            collection_id="collection-1",
            library_item_id="invisible-item",
            added_by_user_id=member.id,
            created_at=datetime(2026, 2, 2),
        ))

    detail = client.get("/api/collections/collection-1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item_count"] == 105
    assert len(detail.json()["items"]) == 100
    assert detail.json()["items"][0]["id"] == "collected-000"

    listing = client.get("/api/collections")
    assert listing.status_code == 200
    assert listing.json()[0]["item_count"] == 105
    assert listing.json()[0]["items"] == []


def test_blank_search_delegates_to_the_paged_list_path_without_a_full_load(library_api) -> None:
    from sqlalchemy import event

    from app.models import LibraryItem as _LibraryItem

    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"row-{index}", title=f"Row {index}", downloaded_at=datetime(2026, 3, 1 + index % 27))
            for index in range(80)
        ])

    loaded: list[str] = []

    @event.listens_for(_LibraryItem, "load")
    def _record(target, _context) -> None:  # noqa: ANN001
        loaded.append(target.id)

    try:
        # A whitespace-only search is not a real query: it must take the
        # bounded keyset list path, not fall back to loading the whole table.
        page = client.get("/api/library", params={"search": "   ", "limit": 20}).json()
    finally:
        event.remove(_LibraryItem, "load", _record)

    assert set(page) == {"items", "next_cursor"}
    assert len(page["items"]) == 20
    assert page["next_cursor"] is not None
    assert len(loaded) <= 21, f"blank search loaded {len(loaded)} rows (should be one page)"


def test_search_pages_load_only_the_requested_page_not_the_whole_result_set(library_api) -> None:
    from sqlalchemy import event

    from app.models import LibraryItem as _LibraryItem
    from app.services.library_search import ensure_search_index

    client, engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _item(f"needle-{index:03d}", title=f"Needle clip {index}", downloaded_at=datetime(2026, 3, 1 + index % 27))
            for index in range(300)
        ])
    ensure_search_index(engine)

    loaded: list[str] = []

    @event.listens_for(_LibraryItem, "load")
    def _record(target, _context) -> None:  # noqa: ANN001
        loaded.append(target.id)

    try:
        page = client.get("/api/library", params={"search": "needle", "limit": 60}).json()
    finally:
        event.remove(_LibraryItem, "load", _record)

    assert len(page["items"]) == 60
    assert page["next_cursor"] is not None
    # A search page must materialise one page of rows, not the entire
    # (bounded) match set.
    assert len(loaded) <= 65, f"a single search page loaded {len(loaded)} rows"


def test_search_paging_reaches_every_visible_match_under_invisible_dominance(library_api) -> None:
    from app.services.library_search import ensure_search_index

    client, engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        # Another member's 250 PRIVATE items have short, term-dense titles ->
        # they dominate bm25 and are invisible to the searcher (member-1).
        session.add_all([
            _item(f"other-{index:03d}", user_id="member-2", visibility="private", title=f"widget {index}")
            for index in range(250)
        ])
        # The searcher's own 150 matches have longer titles -> worse bm25, so
        # a top-K-before-visibility search would never reach them.
        session.add_all([
            _item(
                f"widget-{index:03d}",
                user_id="member-1",
                visibility="private",
                title=f"widget clip from the long descriptive northern session number {index}",
                downloaded_at=datetime(2026, 3, 1 + index % 27),
            )
            for index in range(150)
        ])
    ensure_search_index(engine)

    seen = collect_all_pages(client, {"search": "widget", "limit": 40})
    assert set(seen) == {f"widget-{index:03d}" for index in range(150)}
