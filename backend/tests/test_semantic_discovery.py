import pytest
from app.models import LibraryNote, LibraryItem, LibraryTag, MediaTitle, SourceAutomation, User
from app.services.library_curation import LibraryCurationService
from app.services.media_notes import MediaNotesService
from app.services.semantic_discovery import SemanticDiscovery
from support import memory_session_factory


def make_session():
    return memory_session_factory()()


def reindex(session) -> None:
    """Build the FTS corpus from rows seeded directly in a test.

    Production keeps the index current through the library/curation write hooks;
    tests that ``session.add`` rows straight into the tables bypass those hooks,
    so they rebuild the corpus once after seeding, exactly as startup recovery
    does.
    """
    from app.services.library_search import ensure_search_index

    ensure_search_index(session.get_bind())


def test_natural_language_intent_ranks_semantically_related_library_item() -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(
                id="mars-documentary",
                user_id=member.id,
                visibility="private",
                title="The Red Frontier",
                uploader="Orbital Films",
                metadata_json={"description": "Astronauts prepare for a crewed mission to Mars."},
                status="available",
            ),
            LibraryItem(
                id="travel-guide",
                user_id=member.id,
                visibility="private",
                title="Weekend travel guide",
                uploader="City Walks",
                metadata_json={"description": "A short tour of neighborhood cafes."},
                status="available",
            ),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery().search(session, member, "space exploration", limit=5)

    assert result.mode == "hybrid"
    assert [match.record_id for match in result.matches] == ["mars-documentary"]
    assert result.matches[0].kind == "library"
    assert result.matches[0].semantic_score > 0


@pytest.mark.parametrize(
    ("query", "title", "description"),
    [
        ("how to bake bread", "Sourdough masterclass", "Knead and proof a rustic loaf."),
        ("manage a household budget", "Debt-free plan", "Track expenses, reduce spending, and grow savings."),
        ("fix a leaking faucet", "Weekend plumbing", "Replace a worn tap washer and seal the pipe."),
        ("take better pictures after sunset", "Low-light camera craft", "Exposure and composition for nighttime portraits."),
        ("grow food in a small yard", "Balcony harvest", "Seedlings, soil, and vegetable containers."),
    ],
)
def test_offline_intent_expansion_handles_cross_domain_paraphrases(query: str, title: str, description: str) -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(
                id="intent-match",
                user_id=member.id,
                visibility="private",
                title=title,
                metadata_json={"description": description},
                status="available",
            ),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery().search(session, member, query, limit=5)

    assert [match.record_id for match in result.matches] == ["intent-match"]
    assert result.matches[0].semantic_score > 0


def test_discovery_never_indexes_another_members_private_items_or_automations() -> None:
    session = make_session()
    owner = User(id="owner", username="owner", display_name="Owner", role="viewer", is_active=True)
    viewer = User(id="viewer", username="viewer", display_name="Viewer", role="viewer", is_active=True)
    session.add_all(
        [
            owner,
            viewer,
            LibraryItem(
                id="private-space",
                user_id=owner.id,
                visibility="private",
                title="Private astronaut journal",
                metadata_json={"description": "A secret mission to Mars."},
                status="available",
            ),
            LibraryItem(
                id="shared-space",
                user_id=owner.id,
                visibility="shared",
                title="Household astronomy",
                metadata_json={"description": "A public tour of the planets."},
                status="available",
            ),
            SourceAutomation(
                id="owners-private-follow",
                user_id=owner.id,
                label="Private Mars missions",
                source_url="https://www.youtube.com/@private-space",
                source_type="channel",
                cron_expression="0 */6 * * *",
            ),
            SourceAutomation(
                id="viewers-follow",
                user_id=viewer.id,
                label="Mars Astronomy",
                source_url="https://www.youtube.com/@mars-astronomy",
                source_type="channel",
                cron_expression="0 */6 * * *",
            ),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery().search(session, viewer, "space exploration", limit=10)

    assert {match.record_id for match in result.matches} == {"viewers-follow", "shared-space"}
    assert next(match for match in result.matches if match.record_id == "viewers-follow").kind == "channel"


def test_next_search_incrementally_reflects_a_new_private_tag() -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    item = LibraryItem(
        id="quiet-film",
        user_id=member.id,
        visibility="private",
        title="A Quiet Film",
        metadata_json={},
        status="available",
    )
    session.add_all([member, item])
    session.commit()
    reindex(session)
    discovery = SemanticDiscovery()

    before = discovery.search(session, member, "family favorite", limit=5)
    # Add the tag through the curation service so its FTS write hook fires,
    # exactly as the tag route does in production.
    LibraryCurationService(session).add_tag(item.id, "family favorite", member)
    session.commit()
    after = discovery.search(session, member, "family favorite", limit=5)

    assert before.matches == ()
    assert [match.record_id for match in after.matches] == [item.id]
    assert after.matches[0].lexical_score > 0
    assert after.index_generation == before.index_generation + 1


def test_discovery_indexes_only_comments_visible_to_the_searching_member() -> None:
    session = make_session()
    owner = User(id="owner", username="owner", display_name="Owner", role="viewer", is_active=True)
    friend = User(id="friend", username="friend", display_name="Friend", role="viewer", is_active=True)
    item = LibraryItem(
        id="shared-film",
        user_id=owner.id,
        visibility="shared",
        title="Untitled family film",
        metadata_json={},
        status="available",
    )
    session.add_all(
        [
            owner,
            friend,
            item,
            LibraryNote(id="private-note", item_id=item.id, user_id=owner.id, visibility="private", body="secret genealogy"),
            LibraryNote(id="shared-note", item_id=item.id, user_id=owner.id, visibility="household", body="summer reunion"),
        ]
    )
    session.commit()
    reindex(session)

    owners_private_result = SemanticDiscovery().search(session, owner, "secret genealogy")
    friends_private_result = SemanticDiscovery().search(session, friend, "secret genealogy")
    friends_shared_result = SemanticDiscovery().search(session, friend, "summer reunion")

    assert [match.record_id for match in owners_private_result.matches] == [item.id]
    assert friends_private_result.matches == ()
    assert [match.record_id for match in friends_shared_result.matches] == [item.id]


def test_vault_owner_search_has_no_bypass_into_member_private_items_or_comments() -> None:
    session = make_session()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    vault_owner = User(id="vault-owner", username="vault-owner", display_name="Vault Owner", role="admin", is_active=True)
    private_item = LibraryItem(id="private-item", user_id=member.id, visibility="private", title="saffronzeppelin", metadata_json={}, status="available")
    shared_item = LibraryItem(id="shared-item", user_id=member.id, visibility="shared", title="Shared title", metadata_json={}, status="available")
    session.add_all(
        [
            member,
            vault_owner,
            private_item,
            shared_item,
            LibraryNote(id="private-item-comment", item_id=private_item.id, user_id=member.id, visibility="household", body="amberquasar"),
            LibraryNote(id="private-comment", item_id=shared_item.id, user_id=member.id, visibility="private", body="cobaltmarzipan"),
            LibraryNote(id="shared-comment", item_id=shared_item.id, user_id=member.id, visibility="household", body="household clue"),
        ]
    )
    session.commit()
    reindex(session)
    discovery = SemanticDiscovery()

    assert discovery.search(session, vault_owner, "saffronzeppelin").matches == ()
    assert discovery.search(session, vault_owner, "amberquasar").matches == ()
    assert discovery.search(session, vault_owner, "cobaltmarzipan").matches == ()
    assert [match.record_id for match in discovery.search(session, vault_owner, "household clue").matches] == [shared_item.id]


def test_comment_add_edit_and_delete_incrementally_refresh_member_search() -> None:
    session = make_session()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    item = LibraryItem(id="private-film", user_id=member.id, visibility="private", title="Quiet film", metadata_json={}, status="available")
    session.add_all([member, item])
    session.commit()
    reindex(session)
    discovery = SemanticDiscovery()
    notes = MediaNotesService(session)

    before = discovery.search(session, member, "zzgardenalpha")
    comment = notes.add_note(item.id, "zzgardenalpha", "private", member)
    session.commit()
    after_add = discovery.search(session, member, "zzgardenalpha")

    notes.update_note(comment.id, "zzwoodbeta", "private", member)
    session.commit()
    old_after_edit = discovery.search(session, member, "zzgardenalpha")
    after_edit = discovery.search(session, member, "zzwoodbeta")

    notes.delete_note(comment.id, member)
    session.commit()
    after_delete = discovery.search(session, member, "zzwoodbeta")

    assert before.matches == ()
    assert [match.record_id for match in after_add.matches] == [item.id]
    assert after_add.index_generation == before.index_generation + 1
    assert old_after_edit.matches == ()
    assert [match.record_id for match in after_edit.matches] == [item.id]
    assert after_edit.index_generation == after_add.index_generation + 1
    assert after_delete.matches == ()
    # Under bounded candidate indexing a deletion drops the item from the
    # candidate set rather than bumping the member generation; the behaviour
    # that matters — the deleted comment no longer matches — is asserted above.
    assert after_delete.index_generation == after_edit.index_generation


def test_typeahead_degrades_to_ranked_lexical_matches_when_semantics_fail() -> None:
    class BrokenEncoder:
        def encode(self, text: str) -> dict[str, float]:  # noqa: ARG002
            raise RuntimeError("semantic adapter is offline")

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(id="exact", user_id=member.id, visibility="private", title="Woodworking basics", metadata_json={}, status="available"),
            LibraryItem(id="description", user_id=member.id, visibility="private", title="Handmade", metadata_json={"description": "woodworking basics"}, status="available"),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery(encoder=BrokenEncoder()).search(session, member, "woodworking", limit=5)

    assert result.mode == "lexical"
    assert [match.record_id for match in result.matches] == ["exact", "description"]
    assert all(match.semantic_score == 0 for match in result.matches)


def test_query_embedding_is_computed_once_inside_the_fallback_boundary() -> None:
    class ThirdCallFailsEncoder:
        def __init__(self) -> None:
            self.calls = 0

        def encode(self, text: str) -> dict[str, float]:
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("flaky semantic adapter")
            return {"shared": 1.0}

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(id="woodworking", user_id=member.id, visibility="private", title="Woodworking basics", metadata_json={}, status="available"),
        ]
    )
    session.commit()
    reindex(session)
    encoder = ThirdCallFailsEncoder()

    result = SemanticDiscovery(encoder=encoder).search(session, member, "woodworking", limit=5)

    assert result.mode == "hybrid"
    assert [match.record_id for match in result.matches] == ["woodworking"]
    assert encoder.calls == 2


def test_query_encoder_failure_still_returns_stable_lexical_results() -> None:
    class QueryFailsEncoder:
        def __init__(self) -> None:
            self.calls = 0

        def encode(self, text: str) -> dict[str, float]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("query encoding failed")
            return {"document": 1.0}

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(id="woodworking", user_id=member.id, visibility="private", title="Woodworking basics", metadata_json={}, status="available"),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery(encoder=QueryFailsEncoder()).search(session, member, "woodworking", limit=5)

    assert result.mode == "lexical"
    assert [match.record_id for match in result.matches] == ["woodworking"]
    assert result.matches[0].semantic_score == 0


def test_search_route_exposes_stable_rank_metadata_and_compatibility_items() -> None:
    import app.main as main

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    item = LibraryItem(
        id="mars-documentary",
        user_id=member.id,
        visibility="private",
        title="The Red Frontier",
        uploader="Orbital Films",
        metadata_json={"description": "Astronauts prepare for a crewed mission to Mars."},
        status="available",
    )
    session.add_all([member, item])
    session.commit()
    reindex(session)

    response = main.search_library(q="space exploration", limit=5, current_user=member, db=session)

    assert response.query == "space exploration"
    assert response.mode == "hybrid"
    assert response.items[0].id == item.id
    assert response.matches[0].item.id == item.id
    assert response.matches[0].match_mode == "semantic"


def test_search_loads_only_candidate_items_not_the_whole_library() -> None:
    from sqlalchemy import event

    from app.models import LibraryItem as _LibraryItem
    from app.services.library_search import ensure_search_index

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add(member)
    for index in range(60):
        session.add(
            LibraryItem(
                id=f"filler-{index}",
                user_id=member.id,
                visibility="private",
                title=f"Ordinary filler clip {index}",
                metadata_json={"description": "nothing notable here"},
                status="available",
            )
        )
    session.add(
        LibraryItem(
            id="needle",
            user_id=member.id,
            visibility="private",
            title="uniquephoenix aurora borealis",
            metadata_json={"description": "a rare sighting"},
            status="available",
        )
    )
    session.commit()
    ensure_search_index(session.get_bind())

    loaded_ids: list[str] = []

    @event.listens_for(_LibraryItem, "load")
    def _record(target, _context) -> None:  # noqa: ANN001
        loaded_ids.append(target.id)

    try:
        result = SemanticDiscovery().search(session, member, "uniquephoenix", limit=5)
    finally:
        event.remove(_LibraryItem, "load", _record)

    assert [match.record_id for match in result.matches] == ["needle"]
    # The whole 61-item library must never be materialised: only the FTS
    # candidate(s) are loaded.
    assert "filler-0" not in loaded_ids
    assert len(loaded_ids) <= 10


def test_title_only_search_does_not_hydrate_item_or_automation_records() -> None:
    """A typed title search needs item link fields for scoring, not full item/channel records."""
    from sqlalchemy import event

    from app.services.semantic_discovery import match_title_id

    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            MediaTitle(
                id="aurora-movie", type="movie", key="test:aurora", name="Aurora Benchmark", year=2025,
                provider_ids={"Tmdb": "1"}, field_sources={"name": "path"}, images={"Primary": {"tag": "unused"}},
                metadata_json={"overview": "A polar expedition", "genres": ["Documentary"]},
            ),
            LibraryItem(
                id="aurora-version", user_id=member.id, visibility="private", title="Aurora Benchmark 4K",
                title_id="aurora-movie", metadata_json={"formats": [{"large": "unused"}]}, status="available",
            ),
            SourceAutomation(
                id="aurora-channel", user_id=member.id, label="Aurora Benchmark Channel",
                source_url="https://example.test/aurora", source_type="channel", cron_expression="0 * * * *",
            ),
        ]
    )
    session.commit()
    reindex(session)
    session.expunge_all()
    member = session.get(User, member.id)
    assert member is not None

    loaded_items: list[str] = []
    statements: list[str] = []

    @event.listens_for(LibraryItem, "load")
    def _record_item(target, _context) -> None:  # noqa: ANN001
        loaded_items.append(target.id)

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record_sql(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        result = SemanticDiscovery().search(session, member, "aurora benchmark", limit=10, types={"movie"})
        statement_count = len(statements)
        assert [(match.kind, match.record_id, match_title_id(match)) for match in result.matches] == [
            ("title", "aurora-movie", "aurora-movie")
        ]
        title = result.matches[0].media_title
        assert title is not None
        assert (title.name, title.type, title.year, title.metadata_json["genres"]) == (
            "Aurora Benchmark", "movie", 2025, ["Documentary"],
        )
        assert len(statements) == statement_count, "accessing projected search fields must not trigger lazy SQL"
    finally:
        event.remove(LibraryItem, "load", _record_item)
        event.remove(session.get_bind(), "before_cursor_execute", _record_sql)

    assert loaded_items == []
    assert not any("source_automations" in statement.casefold() for statement in statements)
    title_loads = [
        statement.casefold() for statement in statements
        if "from media_titles" in statement.casefold() and "where media_titles.id in" in statement.casefold()
    ]
    assert title_loads
    assert all("media_titles.provider_ids" not in statement and "media_titles.images" not in statement for statement in title_loads)
    assert sum("exists" in statement for statement in title_loads) == 1, "check and project title candidates in one query"


def test_untyped_search_keeps_full_item_and_automation_records() -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add_all(
        [
            member,
            LibraryItem(
                id="aurora-item", user_id=member.id, visibility="private", title="Aurora Field Notes",
                uploader="Expedition", metadata_json={"description": "aurora benchmark"}, status="available",
            ),
            SourceAutomation(
                id="aurora-channel", user_id=member.id, label="Aurora Benchmark Channel",
                source_url="https://example.test/aurora", source_type="channel", cron_expression="0 * * * *",
            ),
        ]
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery().search(session, member, "aurora benchmark", limit=10)
    by_id = {match.record_id: match for match in result.matches}

    assert by_id["aurora-item"].library_item is not None
    assert by_id["aurora-item"].library_item.metadata_json == {"description": "aurora benchmark"}
    assert by_id["aurora-channel"].automation is not None
    assert by_id["aurora-channel"].automation.source_url == "https://example.test/aurora"


def test_dense_search_passes_requested_types_to_candidate_materialization(monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    from app.services import semantic_discovery

    discovery = SemanticDiscovery()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    seen: list[object] = []
    monkeypatch.setattr(semantic_discovery.embeddings, "serving", lambda _db: SimpleNamespace(model_id="model"))
    monkeypatch.setattr(semantic_discovery.embeddings, "query_vector", lambda *_args: [1.0])
    monkeypatch.setattr(semantic_discovery.embeddings, "vector_map", lambda *_args: {})
    monkeypatch.setattr(semantic_discovery.embeddings, "nearest", lambda *_args: [])

    def candidates(_db, _member, _query, _vector_hits=(), *, types=None):  # noqa: ANN001
        seen.append(types)
        return []

    monkeypatch.setattr(discovery, "_candidate_documents", candidates)

    result = discovery.search(None, member, "aurora", types={"movie"})

    assert result.matches == ()
    assert seen == [{"movie"}]


def test_episode_search_keeps_full_item_payload_for_moment_hits() -> None:
    from app.services.semantic_discovery import match_title_id
    from app.services.transcripts import TranscriptService

    session = make_session()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    series = MediaTitle(id="series", type="series", key="test:series", name="Workshop", metadata_json={})
    season = MediaTitle(id="season", type="season", key="test:season", name="Season 1", parent_id=series.id, metadata_json={})
    episode = MediaTitle(id="episode", type="episode", key="test:episode", name="The Missing Tool", parent_id=season.id, metadata_json={})
    item = LibraryItem(
        id="episode-version", user_id=member.id, visibility="private", title="Workshop S01E01",
        title_id=episode.id, uploader="Studio", metadata_json={"description": "full payload"}, status="available",
    )
    session.add_all([member, series, season, episode, item])
    session.commit()
    TranscriptService(session).store(
        item.id, language="en", source_kind="source_caption", cues=[(61_000, 62_000, "the vermilion stapler appears")],
    )
    session.commit()
    reindex(session)

    result = SemanticDiscovery().search(
        session, member, "vermilion stapler", limit=10, types={"episode", "moment", "library"},
    )
    moment = next(match for match in result.matches if match.kind == "moment")

    assert (moment.record_id, moment.start_ms, match_title_id(moment)) == ("episode-version@61000", 61_000, "episode")
    assert moment.library_item is not None
    assert (moment.library_item.uploader, moment.library_item.metadata_json) == ("Studio", {"description": "full payload"})


def test_partial_moment_scope_loads_item_candidates_once() -> None:
    """Moment-capable scopes should not project links and then hydrate the same items again."""
    from sqlalchemy import event

    from app.services.semantic_discovery import match_title_id
    from app.services.transcripts import TranscriptService

    session = make_session()
    member = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    episode = MediaTitle(id="episode", type="episode", key="test:episode", name="Workshop", metadata_json={})
    item = LibraryItem(
        id="episode-version", user_id=member.id, visibility="private", title="Workshop recording",
        title_id=episode.id, uploader="Studio", metadata_json={"description": "full payload"}, status="available",
    )
    session.add_all([member, episode, item])
    session.commit()
    TranscriptService(session).store(
        item.id, language="en", source_kind="source_caption", cues=[(61_000, 62_000, "the vermilion stapler appears")],
    )
    session.commit()
    reindex(session)

    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record_sql(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        if "from library_items" in statement.casefold():
            statements.append(statement)

    try:
        result = SemanticDiscovery().search(
            session, member, "vermilion stapler", limit=10, types={"episode", "moment"},
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _record_sql)

    assert [(match.kind, match_title_id(match)) for match in result.matches] == [("moment", "episode")]
    moment = result.matches[0]
    assert moment.library_item is not None
    assert moment.library_item.metadata_json == {"description": "full payload"}
    assert len(statements) == 1


def test_member_index_is_capacity_bounded_across_many_queries() -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add(member)
    session.add_all(
        [
            LibraryItem(
                id=f"alpha{index:02d}",
                user_id=member.id,
                visibility="private",
                title=f"alpha{index:02d}",
                metadata_json={},
                status="available",
            )
            for index in range(30)
        ]
    )
    session.commit()
    reindex(session)

    discovery = SemanticDiscovery(member_index_capacity=10)
    for index in range(30):
        result = discovery.search(session, member, f"alpha{index:02d}", limit=5)
        assert [match.record_id for match in result.matches] == [f"alpha{index:02d}"]

    resident = discovery._member_indexes[member.id].documents
    assert len(resident) <= 10, f"member index held {len(resident)} docs, expected <= 10"


def test_capacity_below_one_querys_candidate_count_stays_hybrid() -> None:
    session = make_session()
    member = User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True)
    session.add(member)
    session.add_all(
        [
            LibraryItem(
                id=f"granite-{index}",
                user_id=member.id,
                visibility="private",
                title=f"granite quarry visit {index}",
                metadata_json={},
                status="available",
            )
            for index in range(6)
        ]
    )
    session.commit()
    reindex(session)

    # A capacity below the per-query candidate count must never evict this
    # query's own candidates mid-synchronize (which would KeyError into the
    # lexical fallback): the ranking stays hybrid and complete.
    discovery = SemanticDiscovery(member_index_capacity=2)
    result = discovery.search(session, member, "granite", limit=10)

    assert result.mode == "hybrid"
    assert len(result.matches) == 6
