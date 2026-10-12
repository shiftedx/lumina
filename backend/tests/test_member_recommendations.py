from __future__ import annotations

import pytest
from sqlalchemy import event

import app.main as main
from app.models import LibraryItem, MemberInterest
from app.services.member_recommendations import MemberInterestService, MemberRecommendationPolicy
from app.services.popular_discovery import PopularCategorySnapshot, PopularSnapshot
from support import make_user, memory_session_factory, popular_item as _item, popular_snapshot as _snapshot


@pytest.fixture(autouse=True)
def legacy_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """These route tests pin ADR 0007's policy, which the routes serve with personalised recommendations off."""
    from app.services import reco

    monkeypatch.setattr(reco, "enabled", lambda _db: False)


def _session():
    return memory_session_factory()()


def test_member_interests_are_canonical_durable_and_isolated() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    interests = MemberInterestService(db)

    assert interests.replace(first, ["music", "cooking", "music"]) == ("music", "cooking")
    assert interests.list_for(first) == ("music", "cooking")
    assert interests.list_for(second) == ()

    try:
        interests.replace(second, ["not-a-category"])
    except ValueError as error:
        assert "Unknown interest category" in str(error)
    else:
        raise AssertionError("unknown interest categories must be rejected")


def test_orphaned_stored_interest_keys_are_dropped_defensively_on_both_read_paths() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add_all([
        MemberInterest(id="known", user_id=member.id, category_key="music"),
        MemberInterest(id="orphaned", user_id=member.id, category_key="retired-category"),
    ])
    db.commit()

    interests = MemberInterestService(db)
    assert interests.list_for(member) == ("music",)

    snapshot = _snapshot(_item("music-one", ("music",)))
    result = MemberRecommendationPolicy(db).home(member, ("music", "retired-category"), snapshot)

    assert [item.id for item in result.items] == ["music-one"]
    assert result.state == "ready"


def test_member_recommendations_balance_categories_and_exclude_visible_saved_or_unavailable_sources() -> None:
    db = _session()
    member = make_user("member")
    other = make_user("other")
    db.add_all([
        member, other,
        LibraryItem(id="saved", user_id=other.id, visibility="shared", extractor="youtube", remote_id="saved", title="Saved", status="available"),
        LibraryItem(id="hidden", user_id=other.id, visibility="private", extractor="youtube", remote_id="hidden", title="Hidden", status="available"),
    ])
    db.commit()
    snapshot = _snapshot(
        _item("music-one", ("music",)), _item("cooking-one", ("cooking",)),
        _item("music-two", ("music",)), _item("cooking-two", ("cooking",)),
        _item("saved", ("music",)), _item("unavailable", ("cooking",), availability="private"),
        _item("hidden", ("music",)),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music", "cooking"), snapshot)

    assert [item.id for item in result.items] == ["music-one", "cooking-one", "music-two", "cooking-two", "hidden"]
    assert result.state == "ready"


def test_member_recommendations_balance_overlapping_interests_without_repeating_items() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    result = MemberRecommendationPolicy(db).home(member, ("music", "cooking"), _snapshot(
        _item("shared", ("music", "cooking")),
        _item("cooking-only", ("cooking",)),
        _item("music-only", ("music",)),
    ))

    assert [item.id for item in result.items] == ["shared", "cooking-only", "music-only"]
    assert len({item.id for item in result.items}) == len(result.items)


def test_member_recommendations_only_select_candidate_library_columns() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add_all(
        LibraryItem(
            id=f"unrelated-{index}", user_id=member.id, visibility="shared", extractor="youtube",
            remote_id=f"unrelated-{index}", title="Unrelated", status="available",
        )
        for index in range(100)
    )
    db.add(LibraryItem(
        id="saved", user_id=member.id, visibility="shared", extractor="youtube", remote_id="saved",
        title="Saved", status="available",
    ))
    db.commit()

    statements: list[str] = []
    listener = lambda _conn, _cursor, statement, _parameters, _context, _executemany: statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        result = MemberRecommendationPolicy(db).home(member, ("music",), _snapshot(
            _item("saved", ("music",)), _item("fresh", ("music",)),
        ))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    assert [item.id for item in result.items] == ["fresh"]
    # The visible-Library exclusion query stays bounded to candidate remote_ids
    # and column-narrow (no id or metadata payload).
    exclusion_statement = next(
        statement for statement in statements if "FROM library_items" in statement and "remote_id IN" in statement
    )
    assert "library_items.extractor" in exclusion_statement
    assert "library_items.remote_id" in exclusion_statement
    assert "library_items.id" not in exclusion_statement
    # The deliberate-save affinity aggregate is likewise bounded to the member's
    # own rows and selects only the channel, never the id or metadata payload.
    saved_statement = next(
        statement for statement in statements if "FROM library_items" in statement and "count(*)" in statement
    )
    assert "GROUP BY library_items.uploader" in saved_statement
    assert "library_items.id" not in saved_statement
    assert "metadata_json" not in saved_statement


def test_member_recommendations_keep_stale_items_and_failure_state_without_candidates() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    policy = MemberRecommendationPolicy(db)
    stale = _snapshot(_item("survivor", ("music",)))
    stale = PopularSnapshot(**{
        **stale.__dict__, "state": "stale", "stale": True, "error": "temporarily unavailable",
        "categories": (PopularCategorySnapshot("music", "Music", "stale", None, None),),
    })
    failed = PopularSnapshot(**{
        **stale.__dict__, "items": (), "state": "failed", "stale": False,
        "categories": (PopularCategorySnapshot("music", "Music", "failed", None, None),),
    })

    assert [item.id for item in policy.home(member, ("music",), stale).items] == ["survivor"]
    assert policy.home(member, ("music",), failed).state == "failed"


def test_member_recommendations_use_selected_category_health_not_global_popular_state() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    source = _snapshot(_item("music-one", ("music",)))
    source = PopularSnapshot(**{
        **source.__dict__,
        "state": "partial",
        "stale": False,
        "categories": (
            PopularCategorySnapshot("music", "Music", "ready", None, None),
            PopularCategorySnapshot("cooking", "Cooking", "failed", None, None),
        ),
    })

    assert MemberRecommendationPolicy(db).home(member, ("music",), source).state == "ready"


def test_authenticated_interest_contract_and_home_feed_are_member_scoped(monkeypatch) -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    first_response = main.update_member_interests(main.MemberInterestsUpdateRequest(keys=["music"]), first, db)
    second_response = main.get_member_interests(second, db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: _snapshot(_item("music-one", ("music",))))

    feed = main.home_recommendations(object(), first, db)

    assert first_response.selected_keys == ["music"]
    assert second_response.selected_keys == []
    assert [item.id for item in feed.items] == ["music-one"]


def test_up_next_endpoint_never_searches_a_provider(monkeypatch) -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: _snapshot(_item("local-news", ("news",))))

    class _Refusing:
        def __init__(self, _db):
            pass

        def youtube_search(self, _query, _limit):
            raise AssertionError("Up Next never searches a provider")

    monkeypatch.setattr(main, "YtDlpService", _Refusing)
    payload = main.UpNextRequest(
        source_url="https://www.youtube.com/watch?v=now", source_id="now",
        uploader="NowPlaying", category_keys=["news"],
    )

    result = main.up_next_recommendations(payload, object(), member, db)

    assert [item.id for item in result.items] == ["local-news"]


def test_up_next_endpoint_excludes_the_current_source(monkeypatch) -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main.popular_discovery, "get_snapshot",
        lambda: _snapshot(_item("now", ("news",)), _item("local-news", ("news",))),
    )

    payload = main.UpNextRequest(
        source_url="https://www.youtube.com/watch?v=now", source_id="now",
        uploader="NowPlaying", category_keys=["news"],
    )
    result = main.up_next_recommendations(payload, object(), member, db)

    assert "now" not in [item.id for item in result.items]


def test_up_next_endpoint_is_metered_at_the_search_ceiling(monkeypatch) -> None:
    from app.services.rate_limit import RATE_LIMIT_RULES

    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    charged: list[str] = []
    monkeypatch.setattr(main, "enforce_rate_limit", lambda bucket, *_args, **_kwargs: charged.append(bucket))
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: _snapshot(_item("local-news", ("news",))))

    payload = main.UpNextRequest(source_url="https://www.youtube.com/watch?v=now", source_id="now", uploader="NowPlaying")
    main.up_next_recommendations(payload, object(), member, db)

    # Up Next stays metered at the search ceiling it had while it searched a provider.
    assert charged == ["up_next"]
    assert RATE_LIMIT_RULES["up_next"].max_requests == RATE_LIMIT_RULES["youtube_search"].max_requests == 30
    assert RATE_LIMIT_RULES["up_next"].max_requests < RATE_LIMIT_RULES["popular"].max_requests


def test_home_endpoint_wires_member_follows_into_the_ranking_seam(monkeypatch) -> None:
    from dataclasses import replace

    from app.models import SourceAutomation
    from app.services.member_follows import MemberFollowService

    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(MemberInterest(id="m", user_id=member.id, category_key="music"))
    # A durable follow whose label is the channel display name #88 matches.
    db.add(
        SourceAutomation(
            id="follow-1", user_id=member.id, label="Followed Band",
            source_url="https://www.youtube.com/@followedband", source_type="channel",
            cron_expression="*/30 * * * *", active=True, auto_download=False,
        )
    )
    db.commit()

    followed = replace(_item("followed", ("music",)), uploader="Followed Band")
    stranger = replace(_item("stranger", ("music",)), uploader="Stranger")
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: _snapshot(stranger, followed))

    # Sanity: the follow keys the endpoint derives are the casefolded label.
    assert MemberFollowService(db).followed_channel_keys(member) == frozenset({"followed band"})

    feed = main.home_recommendations(object(), member, db)
    # The followed channel's item is boosted ahead of the equally-popular stranger.
    assert [item.id for item in feed.items][0] == "followed"


def test_update_member_interests_endpoint_rejects_unknown_category_key(db_factory, api_client) -> None:
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add_all([member, MemberInterest(id="existing", user_id=member.id, category_key="music")])

    client = api_client(user=member, base_url="http://localhost")
    response = client.put("/api/discovery/interests", json={"keys": ["music", "not-a-category"]})
    assert response.status_code == 422

    with factory() as session:
        assert MemberInterestService(session).list_for(member) == ("music",)


def test_update_member_interests_endpoint_rejects_non_string_entry(db_factory, api_client) -> None:
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add_all([member, MemberInterest(id="existing", user_id=member.id, category_key="music")])

    client = api_client(user=member, base_url="http://localhost")
    response = client.put("/api/discovery/interests", json={"keys": ["music", 123]})
    assert response.status_code == 422

    with factory() as session:
        assert MemberInterestService(session).list_for(member) == ("music",)
