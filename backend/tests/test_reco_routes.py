"""Recommendation feedback and routes: the service, the remote routes, the feedback routes."""
from __future__ import annotations

from types import SimpleNamespace

import hashlib
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.models import MemberInterest, MemberRecommendationSuppression, RecoEvent, RecoPool, RemoteMedia, SourceAutomation
from app.persistence import write_transaction
from app.services.member_follows import MemberFollowService, PlannedFollow
from app.services.member_recommendations import MemberRecommendationPolicy
from app.services.member_suppressions import FEWER_RECOVERY, MemberSuppressionService
from app.services.reco import SOURCE_SEED
from app.services.reco.events import generations, served_lists
from app.services.reco.policy import remote_key
from discovery_support import add_movie
from support import make_user, memory_session_factory, popular_item, popular_snapshot, seed_app_settings

NOW = datetime(2026, 10, 1, 12, 0, 0)
UC = "UC" + "h" * 22
CHANNEL = f"https://www.youtube.com/channel/{UC}"


def _session_with(*members):  # noqa: ANN002, ANN202
    session = memory_session_factory()()
    session.add_all(members)
    session.commit()
    return session


# ---- the feedback service ---------------------------------------------------------------------------------

def test_fewer_keeps_the_name_and_the_stable_key_and_recovers_after_140_days() -> None:
    member = make_user("member")
    db = _session_with(member)

    record = MemberSuppressionService(db).suppress_fewer(member, uploader=" Harbor Films ", channel_key=CHANNEL, at=NOW)

    assert (record.scope, record.target_key, record.channel_key, record.channel_name) == ("fewer", "harbor films", CHANNEL, "Harbor Films")
    assert record.created_at == NOW
    assert record.recovers_at == NOW + timedelta(days=140) == NOW + FEWER_RECOVERY


def test_pressing_show_fewer_again_resets_its_date() -> None:
    member = make_user("member")
    db = _session_with(member)
    service = MemberSuppressionService(db)
    first = service.suppress_fewer(member, uploader="Harbor Films", channel_key=CHANNEL, at=NOW)

    again = service.suppress_fewer(member, uploader="Harbor Films", channel_key=CHANNEL, at=NOW + timedelta(days=50))

    assert again.id == first.id
    assert again.created_at == NOW + timedelta(days=50)
    assert db.query(MemberRecommendationSuppression).count() == 1


def test_channel_feedback_keeps_the_name_key_for_rollback() -> None:
    member = make_user("member")
    db = _session_with(member)
    service = MemberSuppressionService(db)

    hidden = service.suppress_channel(member, uploader="Harbor Films", channel_key=CHANNEL)
    service.suppress_fewer(member, uploader="Quiet Lane", channel_key=None, at=NOW)
    service.suppress_title(member, title_id="0b7e5a52-4b1e-4f3c-9d55-3c1f0e7a2b10", name="Arrival")

    assert (hidden.target_key, hidden.channel_key, hidden.recovers_at) == ("harbor films", CHANNEL, None)
    # 1.8.0's filter (and the legacy policy behind the switch) reads active_keys: names for channels, never fewer rows.
    keys = service.active_keys(member)
    assert keys.channel_keys == frozenset({"harbor films"})
    assert keys.title_keys == frozenset({"0b7e5a52-4b1e-4f3c-9d55-3c1f0e7a2b10"})
    assert "quiet lane" not in keys.channel_keys and keys.item_keys == frozenset()


def test_a_channel_known_only_by_its_id_is_keyed_by_the_stable_key() -> None:
    member = make_user("member")
    db = _session_with(member)

    record = MemberSuppressionService(db).suppress_channel(member, uploader=None, channel_key=CHANNEL)

    assert (record.target_key, record.channel_key, record.channel_name) == (CHANNEL, CHANNEL, None)


def test_item_feedback_records_its_channel_for_the_spill() -> None:
    member = make_user("member")
    db = _session_with(member)

    record = MemberSuppressionService(db).suppress_item(
        member, source="youtube", item_id="vid-1", webpage_url=None, title="Harbor walk", uploader="Harbor Films", channel_key=CHANNEL,
    )

    assert (record.scope, record.target_key, record.channel_key) == ("item", "id:youtube:vid-1", CHANNEL)


def test_get_reads_only_the_members_own_row() -> None:
    owner, other = make_user("owner"), make_user("other")
    db = _session_with(owner, other)
    record = MemberSuppressionService(db).suppress_channel(owner, uploader="Harbor Films")

    assert MemberSuppressionService(db).get(owner, record.id) == record
    assert MemberSuppressionService(db).get(other, record.id) is None


def test_clear_channel_feedback_removes_only_this_members_channel_and_fewer_rows() -> None:
    owner, other = make_user("owner"), make_user("other")
    db = _session_with(owner, other)
    service = MemberSuppressionService(db)
    service.suppress_channel(owner, uploader="Harbor Films", channel_key=CHANNEL)
    service.suppress_fewer(owner, uploader="Renamed Harbor", channel_key=CHANNEL, at=NOW)  # matched by the stable key
    service.suppress_fewer(owner, uploader="Harbor Films", channel_key=None, at=NOW)  # matched by the name
    kept_item = service.suppress_item(owner, source="youtube", item_id="v", webpage_url=None, title="T", uploader="Harbor Films")
    kept_other_channel = service.suppress_channel(owner, uploader="Quiet Lane")
    kept_other_member = service.suppress_channel(other, uploader="Harbor Films", channel_key=CHANNEL)

    removed = service.clear_channel_feedback(owner, channel_key=CHANNEL, name="Harbor Films")

    assert removed == 3
    left = {row.id for row in db.query(MemberRecommendationSuppression).all()}
    assert left == {kept_item.id, kept_other_channel.id, kept_other_member.id}


def test_clear_channel_feedback_without_any_identity_removes_nothing() -> None:
    member = make_user("member")
    db = _session_with(member)
    MemberSuppressionService(db).suppress_channel(member, uploader="Harbor Films")

    assert MemberSuppressionService(db).clear_channel_feedback(member, channel_key=None, name="  ") == 0


# ---- the remote routes --------------------------------------------------------------------------------------

def _member():  # noqa: ANN202
    return make_user(f"m-{uuid.uuid4().hex[:12]}")  # served_lists and profiles are process-wide


def _watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _pooled(db, member, vid: str, title: str, *, channel: str = CHANNEL) -> str:  # noqa: ANN001
    key = remote_key("youtube", vid, _watch(vid))
    db.add(RemoteMedia(
        key=key, source_identity=f"youtube:{vid}", extractor="youtube", remote_id=vid, webpage_url=_watch(vid), title=title,
        uploader="Harbor Films", channel_key=channel, channel_url=channel, duration=600, view_count=1_000,
        published_at=NOW - timedelta(days=2), kind="video", category_keys=["music"], thumbnail=None, availability="public",
        tokens=[], fetched_at=NOW, last_nominated_at=NOW,
    ))
    db.add(RecoPool(user_id=member.id, item_key=key, sources=SOURCE_SEED, first_seen_at=NOW, last_nominated_at=NOW))
    return key


class _Refresher:
    def __init__(self) -> None:
        self.warmed: list[str] = []
        self.triggered: list[str] = []

    def warm_channel(self, channel_id: str) -> None:
        self.warmed.append(channel_id)

    def trigger(self, member_id: str) -> None:
        self.triggered.append(member_id)


class _Pages:
    def peek_tab(self, _channel_id: str, _tab: str, _limit: int) -> None:
        return None

    def page(self, *_args) -> None:  # noqa: ANN002
        raise AssertionError("a request thread never loads a channel page")


@pytest.fixture
def household(monkeypatch: pytest.MonkeyPatch) -> _Refresher:
    """The routes' singletons without I/O: Popular from memory, a recording refresher, a channel cache that only peeks."""
    items = (popular_item("pop1", ("music",)), popular_item("pop2", ("cooking",)))
    refresher = _Refresher()
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: popular_snapshot(*items))
    monkeypatch.setattr(main.popular_discovery, "candidates", lambda: items)
    monkeypatch.setattr(main, "reco_refresher", refresher)
    monkeypatch.setattr(main, "channel_pages", _Pages())

    def refuse(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        raise AssertionError("no provider search on a request thread")

    monkeypatch.setattr(main.YtDlpService, "youtube_search", refuse)
    return refresher


def test_home_route_serves_annotated_items(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    pooled = _pooled(db, member, "p1", "Harbor walk")
    db.commit()

    response = main.home_recommendations(object(), member, db)

    assert pooled in {item.reco.key for item in response.items}
    assert all(item.reco is not None and item.reco.list_id == response.items[0].reco.list_id for item in response.items)
    assert [item.reco.position for item in response.items] == sorted(item.reco.position for item in response.items)
    assert all(item.artwork_url is None or item.artwork_url.startswith("/api/artwork/remote/") for item in response.items)
    assert response.state == "ready" and response.for_you == []


def test_up_next_route_never_searches_and_has_no_provider_seam(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    _pooled(db, member, "p1", "Tidal pools")
    db.commit()
    payload = main.UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC, limit=6)

    response = main.up_next_recommendations(payload, object(), member, db)

    assert not hasattr(main, "_related_provider_search") and not hasattr(main, "_candidate_from_search_result")
    assert household.warmed == [UC]
    assert response.items and len(response.items) <= 6 and all(item.reco for item in response.items)
    assert "now" not in {item.id for item in response.items}


def test_up_next_rejects_a_malformed_channel_id(db_factory, api_client) -> None:  # noqa: ANN001
    member = _member()
    with db_factory.begin() as session:
        session.add(member)
    client: TestClient = api_client(user=member, base_url="http://localhost")

    response = client.post("/api/discovery/up-next", json={"source_url": _watch("now"), "channel_id": "UC123"})

    assert response.status_code == 422


def test_popular_route_adds_for_you_and_the_category_order(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    pooled = _pooled(db, member, "p1", "Harbor walk")
    db.add(MemberInterest(id=f"i-{member.id}", user_id=member.id, category_key="cooking"))
    MemberSuppressionService(db).suppress_item(member, source="youtube", item_id="pop1", webpage_url=_watch("pop1"),
                                               title="Pop1", uploader="Creator")
    db.commit()

    response = main.popular_now(object(), member, db)

    assert pooled in {item.reco.key for item in response.for_you}
    assert all(item.reco and item.reco.list_id == response.for_you[0].reco.list_id for item in response.for_you)
    assert response.category_order[0] == "cooking"
    assert [item.id for item in response.items] == ["pop2"]  # the hidden item left the rail
    assert all(item.reco is None for item in response.items)


def test_popular_rails_apply_channel_feedback_to_items_no_loader_nominates(household, monkeypatch) -> None:  # noqa: ANN001
    """Feedback applies: a Short or live item skips rank(), so its channel's Not interested and its own
    Not interested are applied to the rail directly (by stable key and by the 1.8.0 name key)."""
    items = (
        popular_item("short1", ("music",), duration=30, uploader="Muted Channel"),  # by name (1.8.0 row)
        popular_item("short2", ("music",), duration=30, uploader="Other", uploader_id=UC),  # by stable key
        popular_item("short3", ("music",), duration=30, uploader="Creator"),  # hidden item
        popular_item("short4", ("music",), duration=30, uploader="Creator"),
        popular_item("pop2", ("cooking",)),
    )
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: popular_snapshot(*items))
    monkeypatch.setattr(main.popular_discovery, "candidates", lambda: items)
    member = _member()
    db = _session_with(member)
    service = MemberSuppressionService(db)
    service.suppress_channel(member, uploader="Muted Channel")
    service.suppress_channel(member, uploader="Renamed", channel_key=CHANNEL)
    service.suppress_item(member, source="youtube", item_id="short3", webpage_url=_watch("short3"), title="Short3",
                          uploader="Creator")
    db.commit()

    response = main.popular_now(object(), member, db)

    assert [item.id for item in response.items] == ["pop2", "short4"]


def test_switched_off_routes_answer_with_the_legacy_policy(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    _pooled(db, member, "p1", "Harbor walk")
    db.commit()
    seed_app_settings(db, ai_features_disabled=["personal_recommendations"])
    legacy = MemberRecommendationPolicy(db).home(member, (), main.popular_discovery.get_snapshot(), followed_channel_keys=frozenset())

    home = main.home_recommendations(object(), member, db)
    popular = main.popular_now(object(), member, db)
    up_next = main.up_next_recommendations(
        main.UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC), object(), member, db,
    )

    assert [item.id for item in home.items] == [item.id for item in legacy.items]
    assert all(item.reco is None for item in [*home.items, *popular.items, *up_next.items])
    assert (popular.for_you, popular.category_order) == ([], [])
    assert household.warmed == []
    assert served_lists.find(member.id, "home_picked", "-", main.utcnow()) is None


# ---- the feedback routes ------------------------------------------------------------------------------------

TITLE_ID = "0b7e5a52-4b1e-4f3c-9d55-3c1f0e7a2b10"
SERVED_KEY = "f" * 64


def _events(db, member) -> list[tuple[str, str, str]]:  # noqa: ANN001
    rows = db.query(RecoEvent).filter(RecoEvent.user_id == member.id).order_by(RecoEvent.id).all()
    return [(row.kind, row.target_kind, row.item_key) for row in rows]


def test_each_control_writes_its_row_and_one_feedback_event(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    add_movie(db, TITLE_ID, "Arrival")
    db.commit()
    request = main.SuppressRecommendationRequest

    item = main.create_suppression(request(scope="item", source_id="v1", source_url=_watch("v1"), title="Harbor walk",
                                           uploader="Harbor Films", channel_id=UC, key=SERVED_KEY, list_id="0123456789abcdef"), member, db)
    fewer = main.create_suppression(request(scope="fewer", uploader="Harbor Films", channel_id=UC), member, db)
    hidden = main.create_suppression(request(scope="channel", uploader="Quiet Lane"), member, db)
    title = main.create_suppression(request(scope="title", title_id=TITLE_ID), member, db)

    stored = {row.id: row.channel_key for row in db.query(MemberRecommendationSuppression)}  # channel_key is not on the wire
    assert [(r.scope, stored[r.id]) for r in (item, fewer, hidden)] == [("item", CHANNEL), ("fewer", CHANNEL), ("channel", None)]
    assert (title.scope, title.target_key, title.title) == ("title", TITLE_ID, "Arrival")
    assert fewer.recovers_at is not None and item.recovers_at is None
    assert [(kind, target) for kind, target, _key in _events(db, member)] == [
        ("not_interested", "remote"), ("fewer", "remote"), ("hide_channel", "remote"), ("not_interested", "title"),
    ]
    assert _events(db, member)[0][2] == SERVED_KEY and _events(db, member)[3][2] == TITLE_ID
    assert household.triggered == [member.id] * 4


def test_the_list_returns_the_four_lists(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    add_movie(db, TITLE_ID, "Arrival")
    db.commit()
    request = main.SuppressRecommendationRequest
    for payload in (request(scope="item", source_id="v1", title="Harbor walk", uploader="Harbor Films"),
                    request(scope="channel", uploader="Quiet Lane"), request(scope="fewer", uploader="Harbor Films"),
                    request(scope="title", title_id=TITLE_ID)):
        main.create_suppression(payload, member, db)

    listing = main.list_suppressions(member, db)

    assert [entry.title for entry in listing.items] == ["Harbor walk"]
    assert [entry.channel_name for entry in listing.channels] == ["Quiet Lane"]
    assert [entry.channel_name for entry in listing.fewer] == ["Harbor Films"]
    assert listing.fewer[0].recovers_at == listing.fewer[0].created_at + timedelta(days=140)
    assert [entry.target_key for entry in listing.titles] == [TITLE_ID]


def test_restore_writes_a_restore_event_only_for_the_members_own_row(household) -> None:  # noqa: ANN001
    owner, other = _member(), _member()
    db = _session_with(owner, other)
    created = main.create_suppression(main.SuppressRecommendationRequest(scope="channel", uploader="Quiet Lane"), owner, db)

    main.restore_suppression(created.id, other, db)  # not theirs: a no-op
    assert db.query(MemberRecommendationSuppression).count() == 1 and _events(db, other) == []

    main.restore_suppression(created.id, owner, db)
    assert db.query(MemberRecommendationSuppression).count() == 0
    assert _events(db, owner)[-1] == ("restore", "remote", created.id)


def test_feedback_drops_every_cached_list_of_the_member(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    _pooled(db, member, "x", "Harbor walk")
    _pooled(db, member, "y", "Tidal pools", channel=f"{CHANNEL}2")
    db.commit()
    up_next = main.UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Someone")
    x = remote_key("youtube", "x", _watch("x"))

    def served() -> dict[str, set[str]]:
        return {
            "home": {item.reco.key for item in main.home_recommendations(object(), member, db).items},
            "explore": {item.reco.key for item in main.popular_now(object(), member, db).for_you},
            "up_next": {item.reco.key for item in main.up_next_recommendations(up_next, object(), member, db).items},
        }

    assert all(x in keys for keys in served().values())
    before = generations.get(member.id)

    created = main.create_suppression(main.SuppressRecommendationRequest(
        scope="item", source_id="x", source_url=_watch("x"), title="Harbor walk", uploader="Harbor Films"), member, db)

    assert generations.get(member.id) > before
    assert all(x not in keys for keys in served().values())  # a hard veto on every surface, inside the 30 minutes
    main.restore_suppression(created.id, member, db)
    assert x in served()["home"]


def test_title_feedback_needs_a_visible_title(db_factory, api_client) -> None:  # noqa: ANN001
    member, owner = _member(), _member()
    with db_factory.begin() as session:
        session.add_all([member, owner])
        add_movie(session, TITLE_ID, "Private", owner=owner.id, visibility="private")
    client: TestClient = api_client(user=member, base_url="http://localhost")

    missing = client.post("/api/discovery/suppressions", json={"scope": "title"})
    hidden = client.post("/api/discovery/suppressions", json={"scope": "title", "title_id": TITLE_ID})
    unknown = client.post("/api/discovery/suppressions", json={"scope": "title", "title_id": "nope"})

    assert (missing.status_code, missing.json()["detail"]) == (422, "A title suppression needs a title id.")
    assert hidden.status_code == 404 and unknown.status_code == 404
    with db_factory() as session:
        assert session.query(MemberRecommendationSuppression).count() == 0
        assert session.query(RecoEvent).count() == 0


def test_a_long_channel_url_never_becomes_an_oversized_key(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    long_url = "https://www.youtube.com/@" + "x" * 2_000

    record = main.create_suppression(main.SuppressRecommendationRequest(scope="fewer", uploader="Harbor Films", channel_url=long_url),
                                     member, db)

    stored = db.query(MemberRecommendationSuppression).filter_by(id=record.id).one().channel_key
    assert stored is None or len(stored) <= 255
    event = db.query(RecoEvent).filter(RecoEvent.user_id == member.id).one()
    assert event.channel_key is None or len(event.channel_key) <= 255


def test_feedback_event_keys_are_served_keys_or_hashes(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)

    main.create_suppression(main.SuppressRecommendationRequest(scope="channel", uploader="Quiet Lane", key="https://evil.example/x"),
                            member, db)
    main.create_suppression(main.SuppressRecommendationRequest(scope="item", source_id="v1", source_url=_watch("v1"), title="T",
                                                                uploader="Harbor Films", key="not-a-served-key"), member, db)

    keys = [key for _kind, _target, key in _events(db, member)]
    assert keys == [hashlib.sha256(b"quiet lane").hexdigest(), remote_key("youtube", "v1", _watch("v1"))]


def test_following_a_channel_clears_its_feedback() -> None:
    member = _member()
    db = _session_with(member)
    service = MemberSuppressionService(db)
    service.suppress_channel(member, uploader="Harbor Films", channel_key=CHANNEL)
    service.suppress_fewer(member, uploader="Harbor Films", channel_key=CHANNEL, at=NOW)
    db.commit()
    before = generations.get(member.id)

    with write_transaction(db, name="test_follow"):
        MemberFollowService(db, validate_url=lambda url: url).create_planned(
            member, [PlannedFollow(channel_key=CHANNEL, source_url=CHANNEL, display_name="Harbor Films")])

    assert db.query(MemberRecommendationSuppression).count() == 0
    assert generations.get(member.id) > before


def test_the_follow_button_clears_channel_feedback(household, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    MemberSuppressionService(db).suppress_fewer(member, uploader="Harbor Films", channel_key=CHANNEL, at=NOW)
    db.commit()
    followed = SourceAutomation(id=f"a-{member.id}", user_id=member.id, label="Harbor Films", source_url=CHANNEL,
                                source_type="channel", cron_expression="*/30 * * * *", active=True, auto_download=False)

    class _Automations:
        def create_automation(self, _payload, _user):  # noqa: ANN001, ANN202
            return followed

        def serialize(self, automation):  # noqa: ANN001, ANN202
            return automation

    monkeypatch.setattr(main, "_source_automation_service", lambda _db: _Automations())

    main.create_automation(SimpleNamespace(source_url=CHANNEL, auto_download=False), member, db)  # the gates read source_url and auto_download

    assert db.query(MemberRecommendationSuppression).count() == 0
    assert household.triggered == [member.id]


def test_changing_interests_rebuilds_the_profile(household) -> None:  # noqa: ANN001
    member = _member()
    db = _session_with(member)
    before = generations.get(member.id)

    main.update_member_interests(main.MemberInterestsUpdateRequest(keys=["music"]), member, db)

    assert generations.get(member.id) > before
    assert household.triggered == [member.id]


def test_an_aware_popular_publish_time_does_not_break_home_explore_or_up_next(household, monkeypatch) -> None:  # noqa: ANN001
    """Popular publish times are tz-aware; a cold remote_media write must not leave aware values to rank against naive now."""
    from datetime import UTC

    items = (popular_item("aw1", ("music",), published_at=datetime(2026, 9, 1, tzinfo=UTC)),)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: popular_snapshot(*items))
    monkeypatch.setattr(main.popular_discovery, "candidates", lambda: items)
    member = _member()
    db = _session_with(member)

    home = main.home_recommendations(object(), member, db)
    explore = main.popular_now(object(), member, db)
    payload = main.UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC, limit=6)
    up_next = main.up_next_recommendations(payload, object(), member, db)

    assert home is not None and explore is not None and up_next is not None
