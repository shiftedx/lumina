"""Recommendations privacy and threads, end to end."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, String, Text, select

import app.main as main
from app.db import Base
from app.media_schemas import RecoEventBatch, RecoEventIn
from app.models import MemberInterest, RecoPool, RemoteMedia, RemotePlaybackProgress, utcnow
from app.routers import discovery
from app.schemas import YouTubeSearchResult
from app.services import embeddings, jellyfin_discovery, local_ai
from app.services.member_recommendations import MemberRecommendationPolicy
from app.services.reco import FEATURE_KEY, SOURCE_SEED
from app.services.reco.events import RecoEventService, served_lists
from app.services.reco.policy import recommend, remote_key
from app.services.reco.pool import RecoRefresher
from discovery_support import add_movie, add_progress
from support import make_user, memory_session_factory, popular_item, popular_snapshot, seed_app_settings

UC = "UC" + "h" * 22
CHANNEL = f"https://www.youtube.com/channel/{UC}"


def _tid(n: int) -> str:
    return str(uuid.UUID(int=n))


def _member(name: str):  # noqa: ANN202
    return make_user(f"{name}-{uuid.uuid4().hex[:8]}")  # served_lists and profiles are process-wide


def _watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _pooled(db, member, vid: str, title: str) -> str:  # noqa: ANN001
    key = remote_key("youtube", vid, _watch(vid))
    now = utcnow()
    db.add(RemoteMedia(
        key=key, source_identity=f"youtube:{vid}", extractor="youtube", remote_id=vid, webpage_url=_watch(vid), title=title,
        uploader="Harbor Films", channel_key=CHANNEL, channel_url=CHANNEL, duration=600, view_count=1_000,
        published_at=now - timedelta(days=1), kind="video", category_keys=["music"], thumbnail=None, availability="public",
        tokens=[], fetched_at=now, last_nominated_at=now,
    ))
    db.add(RecoPool(user_id=member.id, item_key=key, sources=SOURCE_SEED, first_seen_at=now, last_nominated_at=now))
    return key


class _Refresher:
    def __init__(self) -> None:
        self.warmed: list[str] = []

    def warm_channel(self, channel_id: str) -> None:
        self.warmed.append(channel_id)

    def trigger(self, _member_id: str) -> None:
        return None


class _Pages:
    def peek_tab(self, *_args) -> None:  # noqa: ANN002
        return None

    def page(self, *_args) -> None:  # noqa: ANN002
        raise AssertionError("a request thread never loads a channel page")


def _refuse(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
    raise AssertionError("no provider or embedding call on a request thread")


@pytest.fixture
def household(monkeypatch: pytest.MonkeyPatch) -> None:
    items = (popular_item("pop1", ("music",)),)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: popular_snapshot(*items))
    monkeypatch.setattr(main.popular_discovery, "candidates", lambda: items)
    monkeypatch.setattr(main, "reco_refresher", _Refresher())
    monkeypatch.setattr(main, "channel_pages", _Pages())
    monkeypatch.setattr(main.YtDlpService, "youtube_search", _refuse)
    monkeypatch.setattr(embeddings, "embed", _refuse)
    monkeypatch.setattr(local_ai, "embed", _refuse)


def _session(*members):  # noqa: ANN002, ANN202
    session = memory_session_factory()()
    session.add_all(members)
    session.commit()
    return session


def test_member_b_never_receives_an_item_only_in_member_as_pool(household) -> None:  # noqa: ANN001
    alice, bob = _member("alice"), _member("bob")
    db = _session(alice, bob)
    secret = _pooled(db, alice, "secret", "Harbor walk at dawn")
    db.commit()
    # Bob watches a video of the same channel: Up Next must not read that channel's remote_media rows.
    up_next = main.UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC)

    served = {
        "home": [item.reco.key for item in main.home_recommendations(object(), bob, db).items],
        "for_you": [item.reco.key for item in main.popular_now(object(), bob, db).for_you],
        "up_next": [item.reco.key for item in main.up_next_recommendations(up_next, object(), bob, db).items],
        "replay": recommend(db, bob.id, utcnow(), "home_picked", 20, popular=[popular_item("pop1", ("music",))]),
    }

    assert all(secret not in keys for keys in served.values()), served
    assert secret in [item.reco.key for item in main.home_recommendations(object(), alice, db).items]


def test_titles_and_cached_lists_never_cross_members(household) -> None:  # noqa: ANN001
    alice, bob = _member("alice"), _member("bob")
    db = _session(alice, bob)
    add_movie(db, _tid(1), "Shared Drama", genres=["Drama"])
    add_movie(db, _tid(2), "Shared Sequel", genres=["Drama"])
    add_movie(db, _tid(3), "Alice Private", genres=["Drama"], owner=alice.id, visibility="private")
    db.commit()
    for member in (alice, bob):
        add_progress(db, member.id, f"{_tid(1)}-v", completed=True, at=utcnow() - timedelta(days=1))
    db.commit()

    alice_home = main.home_recommendations(object(), alice, db)
    bob_rows = discovery.get_home_title_rows(bob, db).rows
    bob_similar = discovery.list_similar_titles(_tid(1), 12, bob, db)
    bob_jellyfin = jellyfin_discovery.suggestion_refs(db, bob, {"movie"}, 20)

    bob_titles = {item.id for row in bob_rows for item in row.items} | {item.id for item in bob_similar} | set(bob_jellyfin)
    assert _tid(3) not in bob_titles
    assert alice_home.items  # the Popular item: Alice now has a cached Picked for you
    alice_list = alice_home.items[0].reco.list_id
    assert served_lists.get(bob.id, alice_list, utcnow()) is None
    bob_home = main.home_recommendations(object(), bob, db)
    assert alice_list not in {item.reco.list_id for item in bob_home.items}


def test_no_request_thread_calls_a_provider_or_the_embedder(household, db_factory, api_client) -> None:  # noqa: ANN001
    member = _member("member")
    with db_factory.begin() as session:
        session.add(member)
        session.add(MemberInterest(id=f"i-{member.id}", user_id=member.id, category_key="music"))
        add_movie(session, _tid(1), "Shared Drama", genres=["Drama"])
        add_movie(session, _tid(2), "Shared Sequel", genres=["Drama"])
    with db_factory.begin() as session:
        _pooled(session, member, "p1", "Harbor walk")
        add_progress(session, member.id, f"{_tid(1)}-v", completed=True, at=utcnow() - timedelta(days=1))
    client: TestClient = api_client(user=member, base_url="http://localhost")

    responses = [
        client.get("/api/discovery/home"),
        client.post("/api/discovery/up-next", json={"source_url": _watch("now"), "source_id": "now", "channel_id": UC}),
        client.get("/api/discovery/popular"),
        client.get("/api/home/title-rows"),
        client.get(f"/api/titles/{_tid(1)}/similar"),
        client.post("/api/discovery/suppressions", json={"scope": "fewer", "uploader": "Harbor Films", "channel_id": UC}),
        client.get("/api/discovery/suppressions"),
        client.get("/api/discovery/home"),  # feedback dropped every cached list (G-R4-5): serve Home again
    ]

    assert [response.status_code for response in responses] == [200] * len(responses)
    # POST /api/reco/events needs a real session cookie for its CSRF check; its service is what the route runs.
    served = responses[-1].json()["items"][0]["reco"]
    with db_factory() as session:
        stored = RecoEventService(session).record_client(member, RecoEventBatch(events=[
            RecoEventIn(kind="impression", list_id=served["list_id"], key=served["key"], age_ms=0)]), now=utcnow())
        session.commit()
    assert stored == 1


def test_no_seed_query_or_interest_phrase_is_persisted_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    factory = memory_session_factory()
    member = _member("member")
    now = utcnow()
    with factory.begin() as session:
        session.add(member)
        session.add(MemberInterest(id=f"i-{member.id}", user_id=member.id, category_key="cooking"))
        # Satisfied remote watches whose high-IDF words are never adjacent, so no query can equal a stored title's substring.
        for n, title in enumerate(("Lighthouse at the keeper of brass in a lantern", "Quarry by an otter on the marsh at dusk")):
            identity = f"youtube:watched{n}"
            session.add(RemotePlaybackProgress(
                id=f"rp-{n}", user_id=member.id, source_identity=identity,
                source_identity_key=remote_key("youtube", f"watched{n}", _watch(f"watched{n}")), source_url=_watch(f"watched{n}"),
                extractor="youtube", remote_id=f"watched{n}", title=title, uploader="Harbor Films", position_seconds=600,
                duration_seconds=600, completed=True, max_fraction=1.0, plays=1, completions=1, channel_key=CHANNEL,
                last_watched_at=now - timedelta(hours=n + 1),
            ))

    def snapshot_text() -> set[tuple[str, str, str]]:
        values: set[tuple[str, str, str]] = set()
        with factory() as session:
            for table in Base.metadata.sorted_tables:
                columns = [column for column in table.columns if isinstance(column.type, (String, Text, JSON))]
                if columns:
                    for row in session.execute(select(*columns)):
                        values.update((table.name, column.name, str(value)) for column, value in zip(columns, row, strict=True) if value is not None)
        return values

    queries: list[str] = []

    def search(query: str, limit: int) -> list[YouTubeSearchResult]:
        queries.append(query)
        return [YouTubeSearchResult(id=f"r{len(queries)}{n}", title=f"Result {n}", uploader="Somebody Else",
                                    webpage_url=_watch(f"r{len(queries)}{n}"), duration=600, source="youtube") for n in range(3)][:limit]

    before = snapshot_text()
    caplog.set_level(logging.DEBUG)
    RecoRefresher(factory, search, None, clock=lambda: now, sleep=lambda _seconds: None).refresh(member.id)
    added = snapshot_text() - before

    assert queries, "the refresh made no seed or interest search; the test proves nothing"
    for query in queries:
        needle = query.casefold()
        assert not [where for where in added if needle in where[2].casefold()], f"a provider query was stored: {query!r}"
        assert not [record for record in caplog.records if needle in record.getMessage().casefold()], f"a provider query was logged: {query!r}"


def test_switch_off_serves_the_legacy_policy_on_every_surface(household) -> None:  # noqa: ANN001
    member = _member("member")
    db = _session(member)
    _pooled(db, member, "p1", "Harbor walk")
    add_movie(db, _tid(1), "Shared Drama", genres=["Drama"])
    add_movie(db, _tid(2), "Shared Sequel", genres=["Drama"])
    db.commit()
    add_progress(db, member.id, f"{_tid(1)}-v", completed=True, at=utcnow() - timedelta(days=1))
    db.commit()
    on = main.home_recommendations(object(), member, db)
    assert on.items and all(item.reco for item in on.items)  # a list is now cached for 30 minutes

    switch = seed_app_settings(db, ai_features_disabled=[FEATURE_KEY])
    snapshot = main.popular_discovery.get_snapshot()
    legacy = MemberRecommendationPolicy(db)

    home = main.home_recommendations(object(), member, db)
    popular = main.popular_now(object(), member, db)
    rows = discovery.get_home_title_rows(member, db).rows
    similar = discovery.list_similar_titles(_tid(1), 12, member, db)
    suggestions = jellyfin_discovery.suggestion_refs(db, member, {"movie"}, 20)

    assert [item.id for item in home.items] == [item.id for item in legacy.home(member, (), snapshot).items]
    assert all(item.reco is None for item in [*home.items, *popular.items])
    assert (popular.for_you, popular.category_order) == ([], [])
    assert all(item.reco is None for row in rows for item in row.items) and all(item.reco is None for item in similar)
    assert suggestions == [title.id for title in legacy.titles(member, types={"movie"})][:20]
    assert served_lists.find(member.id, "title_similar", f"{_tid(1)}:12", utcnow()) is None  # nothing cached while off

    switch.ai_features_disabled = []  # back on: the new policy serves again
    db.commit()
    assert all(item.reco for item in main.home_recommendations(object(), member, db).items)
