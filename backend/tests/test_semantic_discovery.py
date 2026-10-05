import pytest
from app.models import LibraryNote, LibraryItem, LibraryTag, SourceAutomation, User
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
