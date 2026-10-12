from __future__ import annotations

from app.models import LibraryNote, LibraryItem, LibraryTag, User
from app.services.library import LibraryService
from support import make_user, memory_session_factory


def make_session():
    factory = memory_session_factory()
    return factory(), factory.kw["bind"]


def make_member(member_id: str = "member-1", role: str = "viewer") -> User:
    return make_user(member_id, role=role)


def add_item(session, item_id: str, member_id: str | None, *, visibility: str = "private", title: str = "", uploader: str | None = None, playlist: str | None = None, metadata: dict | None = None) -> LibraryItem:
    item = LibraryItem(
        id=item_id,
        user_id=member_id,
        visibility=visibility,
        title=title or item_id,
        uploader=uploader,
        playlist_name=playlist,
        metadata_json=metadata or {},
        status="available",
    )
    session.add(item)
    return item


def test_indexed_item_matches_on_title() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="Sourdough baking masterclass")
    session.add(member)
    session.commit()

    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    assert search.item_ids_for_query(member, "sourdough", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "masterclass", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "unrelated", limit=10) == []


def test_matches_on_uploader_channel_and_description() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(
        session,
        "v1",
        member.id,
        title="Untitled",
        uploader="Orbital Films",
        metadata={"channel": "Deep Space Channel", "description": "A crewed mission to the outer planets."},
    )
    session.add(member)
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    assert search.item_ids_for_query(member, "orbital", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "deep space", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "crewed", limit=10) == ["v1"]


def test_search_folds_diacritics() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="Beignets at the Café Crème")
    session.add(member)
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    # The unicode61 tokenizer with remove_diacritics folds the accent, so a
    # plain-ASCII query still matches.
    assert search.item_ids_for_query(member, "cafe", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "creme", limit=10) == ["v1"]


def test_prefix_typeahead_matches_partial_leading_token() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="Woodworking joinery basics")
    session.add(member)
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    assert search.item_ids_for_query(member, "wood", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "join", limit=10) == ["v1"]


def test_item_visibility_is_enforced_for_item_text() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_member("owner")
    other = make_member("other")
    admin = make_member("admin", role="admin")
    private_item = add_item(session, "priv", owner.id, visibility="private", title="saffronzeppelin private")
    shared_item = add_item(session, "shar", owner.id, visibility="shared", title="saffronzeppelin shared")
    ownerless_item = add_item(session, "orph", None, visibility="private", title="saffronzeppelin orphan")
    session.add_all([owner, other, admin])
    session.commit()
    search = LibrarySearchService(session)
    for item in (private_item, shared_item, ownerless_item):
        search.sync_item(item)
    session.commit()

    assert set(search.item_ids_for_query(owner, "saffronzeppelin", limit=10)) == {"priv", "shar"}
    assert set(search.item_ids_for_query(other, "saffronzeppelin", limit=10)) == {"shar"}
    # An admin can view shared items and ownerless items, but not another member's private item.
    assert set(search.item_ids_for_query(admin, "saffronzeppelin", limit=10)) == {"shar", "orph"}


def test_member_private_tag_matches_only_its_owner() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_member("owner")
    other = make_member("other")
    item = add_item(session, "v1", owner.id, visibility="shared", title="Untitled family film")
    session.add_all([owner, other, item])
    session.add(LibraryTag(id="t1", item_id="v1", user_id=owner.id, tag="cozynightingale"))
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    search.sync_member_curation("v1", owner.id)
    session.commit()

    assert search.item_ids_for_query(owner, "cozynightingale", limit=10) == ["v1"]
    assert search.item_ids_for_query(other, "cozynightingale", limit=10) == []


def test_shared_comment_matches_every_viewer_but_private_comment_only_its_author() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_member("owner")
    friend = make_member("friend")
    item = add_item(session, "v1", owner.id, visibility="shared", title="Untitled")
    session.add_all([owner, friend, item])
    session.add_all(
        [
            LibraryNote(id="c-priv", item_id="v1", user_id=owner.id, visibility="private", body="secretgenealogy"),
            LibraryNote(id="c-shar", item_id="v1", user_id=owner.id, visibility="household", body="summerreunion"),
        ]
    )
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    search.sync_member_curation("v1", owner.id)
    session.commit()

    assert search.item_ids_for_query(owner, "summerreunion", limit=10) == ["v1"]
    assert search.item_ids_for_query(friend, "summerreunion", limit=10) == ["v1"]
    assert search.item_ids_for_query(owner, "secretgenealogy", limit=10) == ["v1"]
    assert search.item_ids_for_query(friend, "secretgenealogy", limit=10) == []


def test_shared_comment_on_unviewable_item_does_not_leak() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_member("owner")
    admin = make_member("admin", role="admin")
    item = add_item(session, "priv", owner.id, visibility="private", title="Untitled")
    session.add_all([owner, admin, item])
    session.add(LibraryNote(id="c1", item_id="priv", user_id=owner.id, visibility="household", body="amberquasar"))
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    # The comment is shared, but the private item is not viewable by the admin.
    assert search.item_ids_for_query(admin, "amberquasar", limit=10) == []
    assert search.item_ids_for_query(owner, "amberquasar", limit=10) == ["priv"]


def test_malformed_fts_syntax_in_query_is_safe() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="NEAR the parenthesis")
    session.add(member)
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()

    for hostile in ['"', "*", "NEAR(", "AND OR NOT", "(parenthesis", 'title:"x', "^leading", "col:val", "a-b"]:
        # Must not raise and must not 500 the caller.
        assert isinstance(search.item_ids_for_query(member, hostile, limit=10), list)
    # A query that is only punctuation has no searchable tokens.
    assert search.item_ids_for_query(member, "***", limit=10) == []
    # Ordinary words inside otherwise-hostile input still match.
    assert search.item_ids_for_query(member, "parenthesis)", limit=10) == ["v1"]


def test_deleted_item_disappears_from_results() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="ephemeralcomet")
    session.add(member)
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()
    assert search.item_ids_for_query(member, "ephemeralcomet", limit=10) == ["v1"]

    session.delete(item)
    session.commit()

    # The inner join to library_items drops the row, and the ORM delete event
    # sweeps the index entry itself in the same transaction.
    assert search.item_ids_for_query(member, "ephemeralcomet", limit=10) == []


def test_remove_item_sweeps_item_and_member_rows() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="sweepablemeteor")
    session.add_all([member, item])
    session.add(LibraryTag(id="t1", item_id="v1", user_id=member.id, tag="privatemeteor"))
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    search.sync_member_curation("v1", member.id)
    session.commit()
    assert search.item_ids_for_query(member, "sweepablemeteor", limit=10) == ["v1"]

    search.remove_item("v1")
    session.commit()

    # Both the item row and the member curation row are gone; even without the
    # library_items join the index no longer proposes the item.
    assert search.item_ids_for_query(member, "sweepablemeteor", limit=10) == []
    assert search.item_ids_for_query(member, "privatemeteor", limit=10) == []


def test_visibility_flip_updates_results() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_member("owner")
    other = make_member("other")
    item = add_item(session, "v1", owner.id, visibility="private", title="tidalmarigold")
    session.add_all([owner, other, item])
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    session.commit()
    assert search.item_ids_for_query(other, "tidalmarigold", limit=10) == []

    item.visibility = "shared"
    session.commit()

    assert search.item_ids_for_query(other, "tidalmarigold", limit=10) == ["v1"]


# --- write-path hooks: mutations through the services must feed the index ---


def make_service_user(session, member_id="member-1", role="viewer"):
    user = make_member(member_id, role=role)
    session.add(user)
    session.commit()
    return user


def test_upsert_from_info_indexes_the_item() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    user = make_service_user(session)
    service = LibraryService(session)
    item = service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "Glacier photography guide", "filepath": "/tmp/a.mp4"},
        owner_user_id=user.id,
    )
    session.commit()

    assert LibrarySearchService(session).item_ids_for_query(user, "glacier", limit=10) == [item.id]


def test_upsert_reindexes_changed_title() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    user = make_service_user(session)
    service = LibraryService(session)
    service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "First title everest", "filepath": "/tmp/a.mp4"},
        owner_user_id=user.id,
    )
    session.commit()
    item = service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "Second title kilimanjaro", "filepath": "/tmp/a.mp4"},
        owner_user_id=user.id,
    )
    session.commit()

    search = LibrarySearchService(session)
    assert search.item_ids_for_query(user, "everest", limit=10) == []
    assert search.item_ids_for_query(user, "kilimanjaro", limit=10) == [item.id]


def test_add_and_delete_tag_through_service_updates_index() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    user = make_service_user(session)
    service = LibraryService(session)
    item = service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "Untitled", "filepath": "/tmp/a.mp4"},
        owner_user_id=user.id,
    )
    session.commit()
    service.add_tag(item.id, "brightpelican", user)
    session.commit()
    search = LibrarySearchService(session)
    assert search.item_ids_for_query(user, "brightpelican", limit=10) == [item.id]

    service.delete_tag(item.id, "brightpelican", user)
    session.commit()
    assert search.item_ids_for_query(user, "brightpelican", limit=10) == []


def test_comment_visibility_change_moves_text_between_scopes() -> None:
    from app.services.library_search import LibrarySearchService
    from app.services.media_notes import MediaNotesService

    session, engine = make_session()
    owner = make_service_user(session, "owner")
    friend = make_member("friend")
    session.add(friend)
    service = LibraryService(session)
    item = service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "Untitled", "filepath": "/tmp/a.mp4"},
        owner_user_id=owner.id,
        visibility="shared",
    )
    session.commit()
    notes = MediaNotesService(session)
    comment = notes.add_note(item.id, "wanderingfjord", "private", owner)
    session.commit()
    search = LibrarySearchService(session)
    assert search.item_ids_for_query(owner, "wanderingfjord", limit=10) == [item.id]
    assert search.item_ids_for_query(friend, "wanderingfjord", limit=10) == []

    notes.update_note(comment.id, "wanderingfjord", "household", owner)
    session.commit()
    # Now that it is shared it must be visible to another household member too.
    assert search.item_ids_for_query(friend, "wanderingfjord", limit=10) == [item.id]

    notes.delete_note(comment.id, owner)
    session.commit()
    assert search.item_ids_for_query(owner, "wanderingfjord", limit=10) == []
    assert search.item_ids_for_query(friend, "wanderingfjord", limit=10) == []


def test_set_visibility_through_service_updates_results() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    owner = make_service_user(session, "owner")
    other = make_member("other")
    session.add(other)
    service = LibraryService(session)
    item = service.upsert_from_info(
        {"id": "yt1", "extractor_key": "YouTube", "title": "quietlantern", "filepath": "/tmp/a.mp4"},
        owner_user_id=owner.id,
        visibility="private",
    )
    session.commit()
    search = LibrarySearchService(session)
    assert search.item_ids_for_query(other, "quietlantern", limit=10) == []

    service.set_visibility(item, "shared", owner)
    session.commit()
    assert search.item_ids_for_query(other, "quietlantern", limit=10) == [item.id]


# --- recovery: rebuild and survive backup/restore ---


def test_ensure_search_index_rebuilds_after_table_dropped() -> None:
    from sqlalchemy import text
    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="rebuildablecomet")
    session.add_all([member, item])
    session.add(LibraryTag(id="t1", item_id="v1", user_id=member.id, tag="myprivatetag"))
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    search.sync_member_curation("v1", member.id)
    session.commit()
    assert search.item_ids_for_query(member, "rebuildablecomet", limit=10) == ["v1"]

    session.execute(text("DROP TABLE library_item_fts"))
    session.execute(text("DROP TABLE library_member_fts"))
    session.commit()

    ensure_search_index(engine)

    fresh = LibrarySearchService(session)
    assert fresh.item_ids_for_query(member, "rebuildablecomet", limit=10) == ["v1"]
    assert fresh.item_ids_for_query(member, "myprivatetag", limit=10) == ["v1"]


def test_ensure_search_index_rebuilds_when_count_out_of_sync() -> None:
    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    # Two items exist in the base table but were never indexed (e.g. seeded
    # before the corpus was built).
    add_item(session, "v1", member.id, title="unindexedalpha")
    add_item(session, "v2", member.id, title="unindexedbeta")
    session.add(member)
    session.commit()
    assert LibrarySearchService(session).item_ids_for_query(member, "unindexedalpha", limit=10) == []

    ensure_search_index(engine)

    search = LibrarySearchService(session)
    assert search.item_ids_for_query(member, "unindexedalpha", limit=10) == ["v1"]
    assert search.item_ids_for_query(member, "unindexedbeta", limit=10) == ["v2"]


def test_typeahead_is_selective_and_page_bounded() -> None:
    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    session.add_all(
        [
            LibraryItem(
                id=f"clip-{index}",
                user_id=member.id,
                visibility="private",
                title=f"Ordinary household clip number {index}",
                metadata_json={},
                status="available",
            )
            for index in range(25)
        ]
    )
    session.add(
        LibraryItem(
            id="needle",
            user_id=member.id,
            visibility="private",
            title="uniquephoenix aurora borealis",
            metadata_json={},
            status="available",
        )
    )
    session.commit()
    ensure_search_index(engine)
    search = LibrarySearchService(session)

    # A selective typeahead returns just the needle.
    assert search.item_ids_for_query(member, "uniquep", limit=20) == ["needle"]

    # A broad typeahead matching every clip is still bounded by the page limit.
    assert len(search.item_ids_for_query(member, "ordi", limit=20)) == 20


# --- write-path delete must be a rowid seek, never an O(corpus) FTS scan ---


def test_item_update_deletes_the_fts_row_by_rowid_not_by_scan() -> None:
    from sqlalchemy import event, text

    from app.services.library_search import ITEM_FTS_TABLE, LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    for index in range(200):
        add_item(session, f"filler-{index}", member.id, title=f"ordinary clip {index}")
    target = add_item(session, "target", member.id, title="original title")
    session.commit()
    ensure_search_index(engine)

    deletes: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.startswith(f"DELETE FROM {ITEM_FTS_TABLE} WHERE"):
            deletes.append(statement)

    try:
        target.title = "changed title"
        LibrarySearchService(session).sync_item(target)
        session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert deletes, "re-indexing an existing item must delete its prior FTS row"
    assert all("rowid" in statement.lower() for statement in deletes), deletes
    assert all("item_id" not in statement.lower() for statement in deletes), deletes
    # FTS5 always prints "SCAN ... VIRTUAL TABLE INDEX 0" in EQP; the trailing
    # ":=" is a pushed-down rowid equality (an O(1) seek). The UNINDEXED item_id
    # form has no pushed-down constraint (bare "INDEX 0:") — a full scan.
    rowid_plan = session.execute(text(f"EXPLAIN QUERY PLAN DELETE FROM {ITEM_FTS_TABLE} WHERE rowid = 1")).all()[0][3]
    scan_plan = session.execute(text(f"EXPLAIN QUERY PLAN DELETE FROM {ITEM_FTS_TABLE} WHERE item_id = 'x'")).all()[0][3]
    assert rowid_plan.rstrip().endswith(":="), rowid_plan
    assert scan_plan.rstrip().endswith("INDEX 0:"), scan_plan


def test_member_curation_update_deletes_by_rowid_not_by_scan() -> None:
    from sqlalchemy import event

    from app.services.library_search import MEMBER_FTS_TABLE, LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    item = add_item(session, "v1", member.id, title="Untitled")
    session.add(item)
    session.add(LibraryTag(id="t1", item_id="v1", user_id=member.id, tag="firsttag"))
    session.commit()
    ensure_search_index(engine)

    deletes: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.startswith(f"DELETE FROM {MEMBER_FTS_TABLE} WHERE"):
            deletes.append(statement)

    try:
        session.add(LibraryTag(id="t2", item_id="v1", user_id=member.id, tag="secondtag"))
        session.flush()
        LibrarySearchService(session).sync_member_curation("v1", member.id)
        session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert deletes, "re-indexing member curation must delete its prior FTS row"
    assert all("rowid" in statement.lower() for statement in deletes), deletes


def _seed_dominance(session, engine):
    """Alice: 200 private high-bm25 'aurora' items; Bob: 30 own lower-bm25 matches."""
    from app.services.library_search import ensure_search_index

    alice = make_member("alice")
    bob = make_member("bob")
    session.add_all([alice, bob])
    for index in range(200):
        add_item(session, f"alice-{index:03d}", alice.id, visibility="private", title=f"aurora {index}")
    for index in range(30):
        add_item(
            session,
            f"bob-{index:03d}",
            bob.id,
            visibility="private",
            title=f"aurora recording from the northern trip evening session number {index}",
        )
    session.commit()
    ensure_search_index(engine)
    return alice, bob


def test_search_recall_survives_another_members_dominant_private_items() -> None:
    from app.services.library_search import LibrarySearchService

    session, engine = make_session()
    _alice, bob = _seed_dominance(session, engine)

    hits = LibrarySearchService(session).search(bob, "aurora", limit=10)

    assert len(hits) == 10, [h.item_id for h in hits]
    assert all(h.item_id.startswith("bob-") for h in hits)


def test_search_result_count_has_no_private_corpus_side_channel() -> None:
    from app.services.library_search import LibrarySearchService

    shared_session, shared_engine = make_session()
    _alice, bob = _seed_dominance(shared_session, shared_engine)
    with_alice = LibrarySearchService(shared_session).search(bob, "aurora", limit=10)

    alone_session, alone_engine = make_session()
    from app.services.library_search import ensure_search_index

    bob_only = make_member("bob")
    alone_session.add(bob_only)
    for index in range(30):
        add_item(
            alone_session,
            f"bob-{index:03d}",
            bob_only.id,
            visibility="private",
            title=f"aurora recording from the northern trip evening session number {index}",
        )
    alone_session.commit()
    ensure_search_index(alone_engine)
    without_alice = LibrarySearchService(alone_session).search(bob_only, "aurora", limit=10)

    # Bob's own result count must not depend on another member's private corpus.
    assert len(with_alice) == len(without_alice) == 10


# --- per-table top-K pool membership is deterministic at exact-tie boundaries ---


def test_tied_rank_pool_membership_is_deterministic_at_fetch_boundary() -> None:
    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    # Identical text -> identical bm25 rank for every row; a fetch bound smaller
    # than the tie group must still admit a formally deterministic subset.
    for index in range(20):
        add_item(session, f"tie-{index:02d}", member.id, title="glacier meltwater survey")
    session.commit()
    ensure_search_index(engine)

    search = LibrarySearchService(session)
    first = search.item_ids_for_query(member, "glacier", limit=3)
    assert first == [f"tie-{index:02d}" for index in range(3)]
    for _ in range(5):
        assert search.item_ids_for_query(member, "glacier", limit=3) == first


def test_sparse_query_exits_after_one_ranked_pass() -> None:
    from sqlalchemy import event as sa_event

    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    for index in range(3):
        add_item(session, f"sparse-{index}", member.id, title=f"heliotrope field note {index}")
    session.commit()
    ensure_search_index(engine)

    search = LibrarySearchService(session)
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _capture)
    try:
        hits = search.search(member, "heliotrope", limit=50)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _capture)

    assert [hit.item_id for hit in hits] == [f"sparse-{index}" for index in range(3)]
    ranked_passes = [s for s in statements if "item_hits" in s]
    # Three raw matches against a 50-wide window: one ranked pass plus one cheap
    # unranked pool count — never the full escalating-K ladder.
    assert len(ranked_passes) == 1, statements
    assert len([s for s in statements if "MATCH" in s]) <= 2, statements


def test_minority_invisible_hits_still_fill_the_window_in_one_ranked_pass() -> None:
    """Other members' private hits above the window cost no count or refill pass."""
    from sqlalchemy import event as sa_event

    from app.services.library_search import LibrarySearchService, ensure_search_index

    session, engine = make_session()
    alice, bob = make_member("alice"), make_member("bob")
    session.add_all([alice, bob])
    for index in range(5):  # shorter titles rank higher under bm25
        add_item(session, f"alice-{index}", alice.id, title=f"quartz {index}")
    for index in range(20):
        add_item(session, f"bob-{index:02d}", bob.id, title=f"quartz field recording from the long valley walk {index}")
    session.commit()
    ensure_search_index(engine)
    statements: list[str] = []
    sa_event.listen(engine, "before_cursor_execute", lambda _c, _cur, statement, *_: statements.append(statement))

    hits = LibrarySearchService(session).search(bob, "quartz", limit=10)

    assert len(hits) == 10 and all(hit.item_id.startswith("bob-") for hit in hits)
    assert len([s for s in statements if "MATCH" in s]) == 1, statements


def test_orm_delete_of_library_item_sweeps_fts_rows_structurally() -> None:
    from sqlalchemy import text as sa_text

    from app.services.library_search import (
        ITEM_FTS_MAP_TABLE,
        ITEM_FTS_TABLE,
        MEMBER_FTS_MAP_TABLE,
        MEMBER_FTS_TABLE,
        LibrarySearchService,
    )

    session, engine = make_session()
    member = make_member()
    item = add_item(session, "v1", member.id, title="obsidianfjord")
    session.add_all([member, item])
    session.add(LibraryTag(id="t1", item_id="v1", user_id=member.id, tag="privatefjord"))
    session.commit()
    search = LibrarySearchService(session)
    search.sync_item(item)
    search.sync_member_curation("v1", member.id)
    session.commit()

    # A plain ORM delete — no explicit remove_item call — must sweep every
    # index row in the same transaction (the delete-path hard precondition).
    session.delete(item)
    session.commit()

    for table in (ITEM_FTS_TABLE, ITEM_FTS_MAP_TABLE, MEMBER_FTS_TABLE, MEMBER_FTS_MAP_TABLE):
        assert session.execute(sa_text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
    assert search.item_ids_for_query(member, "obsidianfjord", limit=10) == []
    assert search.item_ids_for_query(member, "privatefjord", limit=10) == []


def test_ensure_search_index_recovers_from_malformed_fts_shadow_table() -> None:
    from sqlalchemy import text as sa_text

    from app.services.library_search import ITEM_FTS_TABLE, LibrarySearchService, ensure_search_index

    session, engine = make_session()
    member = make_member()
    session.add(member)
    add_item(session, "v1", member.id, title="cinnabar lighthouse")
    session.commit()
    ensure_search_index(engine)

    # Simulate on-disk corruption: the FTS5 virtual table's content shadow
    # table vanishes, so any query against it raises instead of miscounting.
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TABLE {ITEM_FTS_TABLE}_data")

    ensure_search_index(engine)

    search = LibrarySearchService(session)
    assert search.item_ids_for_query(member, "cinnabar", limit=10) == ["v1"]
    assert session.execute(sa_text(f"SELECT count(*) FROM {ITEM_FTS_TABLE}")).scalar() == 1


# ---- Media titles and transcript moments ------------------------------------

from discovery_support import add_movie, add_series, add_summary  # noqa: E402


def _index_titles(session) -> None:
    from app.models import MediaTitle
    from app.services.library_search import index_title

    for title in session.query(MediaTitle).all():
        index_title(session, title)
    session.commit()


def test_title_index_matches_name_series_genre_and_people_and_hides_private_series() -> None:
    from app.services.library_search import LibrarySearchService

    session, _engine = make_session()
    owner, member = make_member("owner"), make_member("member")
    session.add_all([owner, member])
    add_series(session, "sev", "Severance", seasons={1: 2}, owner="owner", visibility="private", genres=["Thriller"])
    add_movie(session, "arr", "Arrival", genres=["Science Fiction"], people=["Amy Adams"], overview="Linguist decodes aliens")
    session.commit()
    _index_titles(session)
    search = LibrarySearchService(session)

    assert search.title_ids_for_query(member, "arrival", limit=10) == ["arr"]
    assert search.title_ids_for_query(member, "amy adams", limit=10) == ["arr"]
    assert search.title_ids_for_query(member, "linguist", limit=10) == ["arr"]
    assert search.title_ids_for_query(member, "severance", limit=10) == []
    assert search.title_ids_for_query(member, "thriller", limit=10) == []
    # Episodes carry their series name, so "severance" finds the series and its episodes for the owner.
    assert set(search.title_ids_for_query(owner, "severance", limit=10)) == {"sev", "sev-s1", "sev-s1e1", "sev-s1e2"}


def test_series_rename_reindexes_its_episodes() -> None:
    from app.models import MediaTitle
    from app.services.library_search import LibrarySearchService, index_title

    session, _engine = make_session()
    member = make_member()
    session.add(member)
    add_series(session, "show", "Old Name", seasons={1: 1})
    session.commit()
    _index_titles(session)
    series = session.get(MediaTitle, "show")
    series.name = "Andor"
    index_title(session, series)
    session.commit()

    assert set(LibrarySearchService(session).title_ids_for_query(member, "andor", limit=10)) == {"show", "show-s1", "show-s1e1"}


def test_transcript_windows_group_cues_by_minute() -> None:
    from app.services.library_search import transcript_windows

    assert transcript_windows([]) == []
    assert transcript_windows([(0, "a"), (30_000, "b"), (60_000, "c"), (61_000, "d"), (125_000, "e")]) == [
        (0, "a b"), (60_000, "c d"), (125_000, "e"),
    ]


def test_transcript_store_and_summary_index_moments_with_visibility() -> None:
    from app.services.library_search import LibrarySearchService, index_item_transcript
    from app.services.transcripts import TranscriptService

    session, _engine = make_session()
    owner, member = make_member("owner"), make_member("member")
    session.add_all([owner, member])
    add_item(session, "shared", "owner", visibility="shared", title="Pilot")
    add_item(session, "secret", "owner", visibility="private", title="Private tape")
    session.commit()
    TranscriptService(session).store("shared", language="en", source_kind="source_caption",
                                     cues=[(0, 900, "hello there"), (61_000, 61_900, "the red stapler appears")])
    TranscriptService(session).store("secret", language="en", source_kind="source_caption",
                                     cues=[(5_000, 5_900, "the red stapler again")])
    session.commit()
    search = LibrarySearchService(session)

    assert [(hit.item_id, hit.start_ms) for hit in search.moment_hits(member, "stapler", limit=10)] == [("shared", 61_000)]
    assert {hit.item_id for hit in search.moment_hits(owner, "stapler", limit=10)} == {"shared", "secret"}

    add_summary(session, "shared", [{"text": "A heist is planned", "cue_ordinals": [1], "start_ms": 61_000}], overview="Crew plans a heist")
    session.commit()
    index_item_transcript(session, "shared")
    session.commit()
    summary_hits = search.moment_hits(member, "heist", limit=10)
    assert [(hit.item_id, hit.start_ms) for hit in summary_hits] == [("shared", None)]


def test_missing_item_hides_its_moments_and_bad_syntax_is_inert() -> None:
    from app.models import LibraryItem
    from app.services.library_search import LibrarySearchService
    from app.services.transcripts import TranscriptService

    session, _engine = make_session()
    member = make_member()
    session.add(member)
    add_item(session, "gone", member.id, title="Gone")
    session.commit()
    TranscriptService(session).store("gone", language="en", source_kind="source_caption", cues=[(0, 900, "lighthouse keeper")])
    session.get(LibraryItem, "gone").status = "missing"
    session.commit()
    search = LibrarySearchService(session)

    assert search.moment_hits(member, "lighthouse", limit=5) == []
    for query in ['"', "NEAR(", "*", ":", "title:x OR", "a" * 5000]:
        assert search.title_ids_for_query(member, query, limit=5) == []
        assert search.moment_hits(member, query, limit=5) == []


def test_ensure_search_index_rebuilds_title_and_transcript_tables() -> None:
    from app.services.library_search import (
        TITLE_FTS_TABLE,
        TRANSCRIPT_FTS_TABLE,
        LibrarySearchService,
        ensure_search_index,
    )
    from app.services.transcripts import TranscriptService

    session, engine = make_session()
    member = make_member()
    session.add(member)
    add_movie(session, "arr", "Arrival")
    add_item(session, "clip", member.id, title="Clip")
    session.commit()
    TranscriptService(session).store("clip", language="en", source_kind="source_caption", cues=[(0, 900, "heptapod ink")])
    session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TABLE {TITLE_FTS_TABLE}")
        connection.exec_driver_sql(f"DELETE FROM {TRANSCRIPT_FTS_TABLE}")

    ensure_search_index(engine)

    search = LibrarySearchService(session)
    assert search.title_ids_for_query(member, "arrival", limit=5) == ["arr"]
    assert [hit.item_id for hit in search.moment_hits(member, "heptapod", limit=5)] == ["clip"]


def test_orm_delete_of_library_item_sweeps_its_transcript_rows() -> None:
    from app.models import LibraryItem
    from app.services.library_search import TRANSCRIPT_FTS_TABLE
    from app.services.transcripts import TranscriptService

    session, _engine = make_session()
    member = make_member()
    session.add(member)
    add_item(session, "clip", member.id, title="Clip")
    session.commit()
    TranscriptService(session).store("clip", language="en", source_kind="source_caption", cues=[(0, 900, "words")])
    session.commit()
    session.delete(session.get(LibraryItem, "clip"))
    session.commit()

    assert session.execute(text_sql(f"SELECT count(*) FROM {TRANSCRIPT_FTS_TABLE}")).scalar() == 0


def text_sql(statement: str):
    from sqlalchemy import text

    return text(statement)
