"""Title recommendations: one policy, a second pool of unstarted visible titles."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.models import MemberFavorite
from app.services.member_recommendations import MemberRecommendationPolicy, TitleFacts, TitleSignals, score_title
from app.services.member_suppressions import MemberSuppressionService
from discovery_support import BASE, add_movie, add_progress, add_series
from app.services import member_access
from support import make_user, memory_session_factory


@pytest.fixture(autouse=True)
def legacy_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests pin ADR 0011's title policy, which the routes serve with personalised recommendations off."""
    from app.services import reco

    monkeypatch.setattr(reco, "enabled", lambda _db: False)


def facts(title_id: str = "c", *, genres=(), people=(), boxset: str | None = None, days: int = 0, rating: float | None = None) -> TitleFacts:
    return TitleFacts(
        id=title_id, type="movie", boxset_id=boxset, genres=frozenset(genres), people=frozenset(people),
        created_at=BASE + timedelta(days=days), community_rating=rating,
    )


def test_recency_dates_a_title_by_when_its_files_arrived() -> None:
    from app.models import MediaTitle

    scanned = dict(type="movie", key="k", name="N", provider_ids={}, metadata_json={}, created_at=BASE)
    assert TitleFacts.of(MediaTitle(id="old", added_at=BASE - timedelta(days=400), **scanned)).created_at == BASE - timedelta(days=400)
    assert TitleFacts.of(MediaTitle(id="unknown", **scanned)).created_at == BASE  # no file time: when the scan met it


WATCHED = facts("w", genres=("drama", "war"), people=("amy adams",))


@pytest.mark.parametrize(
    ("candidate", "signals", "anchor", "cosine", "expected"),
    [
        # expected = (playback, save, context, recency, popularity)
        (facts(genres=("drama",)), TitleSignals(completed=(WATCHED,), reference_time=BASE), None, None, (1.0, 0.0, 0.0, 1.0, 0.0)),
        (facts(people=("amy adams",)), TitleSignals(started=(WATCHED,), reference_time=BASE), None, None, (0.5, 0.0, 0.0, 1.0, 0.0)),
        (facts(genres=("war",)), TitleSignals(favorites=(WATCHED,), reference_time=BASE), None, None, (0.0, 1.0, 0.0, 1.0, 0.0)),
        (facts(boxset="lotr"), TitleSignals(reference_time=BASE), facts("a", boxset="lotr"), None, (0.0, 0.0, 1.0, 1.0, 0.0)),
        # a boxset anchor (a collection's own page) ranks its member movies as same-boxset
        (facts(boxset="lotr"), TitleSignals(reference_time=BASE), TitleFacts("lotr", "boxset", None, frozenset(), frozenset(), BASE, None), None, (0.0, 0.0, 1.0, 1.0, 0.0)),
        (facts(people=("amy adams",)), TitleSignals(reference_time=BASE), WATCHED, None, (0.0, 0.0, 0.75, 1.0, 0.0)),
        (facts(), TitleSignals(reference_time=BASE), WATCHED, 0.85, (0.0, 0.0, 0.75, 1.0, 0.0)),
        (facts(genres=("drama", "war")), TitleSignals(reference_time=BASE), WATCHED, 0.2, (0.0, 0.0, 0.5, 1.0, 0.0)),
        (facts(genres=("war",)), TitleSignals(reference_time=BASE), WATCHED, None, (0.0, 0.0, 0.25, 1.0, 0.0)),
        (facts(days=-400, rating=8.1), TitleSignals(reference_time=BASE), None, None, (0.0, 0.0, 0.0, 0.0, 1.0)),
        (facts(days=-20, rating=6.5), TitleSignals(reference_time=BASE), None, None, (0.0, 0.0, 0.0, 0.75, 0.5)),
        (facts(rating=4.0), TitleSignals(), None, None, (0.0, 0.0, 0.0, 0.0, 0.0)),
    ],
)
def test_score_title_table(candidate, signals, anchor, cosine, expected) -> None:  # noqa: ANN001
    score = score_title(candidate, signals, anchor, anchor_cosine=cosine)

    assert (score.playback, score.save, score.context, score.recency, score.popularity) == expected
    assert (score.interest, score.follow) == (0.0, 0.0)
    assert (score.total * 4).is_integer()  # quarter-step weights keep totals exact


# A route path segment must parse as an id (app.services.media_titles.parse_item_id); every other
# fixture title here is looked up only through the Python API, so a plain word still serves as its id.
WATCHED_ID = "00000000-0000-4000-8000-000000000001"


def _library(session):
    member = make_user("member")
    session.add_all([make_user("owner"), make_user("other"), member])
    add_movie(session, WATCHED_ID, "Watched", genres=["Drama"], people=["Amy Adams"])
    add_movie(session, "started", "Started", genres=["Horror"])
    add_movie(session, "drama2", "Another Drama", genres=["Drama"])
    add_movie(session, "adams", "Adams Film", people=["Amy Adams"])
    add_movie(session, "horror2", "Another Horror", genres=["Horror"])
    add_movie(session, "private", "Private Drama", genres=["Drama"], owner="other", visibility="private")
    add_movie(session, "cooking", "Cooking Show", genres=["Food"])
    add_movie(session, "suppressed", "Suppressed Drama", genres=["Drama"])
    add_progress(session, "member", f"{WATCHED_ID}-v", completed=True)
    add_progress(session, "member", "started-v", position=100)
    session.commit()
    MemberSuppressionService(session).suppress_title(member, title_id="suppressed", name="Suppressed Drama")
    session.commit()
    return member


def test_titles_exclude_started_invisible_and_suppressed_and_reserve_exploration() -> None:
    session = memory_session_factory()()
    member = _library(session)

    ids = [title.id for title in MemberRecommendationPolicy(session, limit=10).titles(member, types=("movie",))]

    # adams/drama2 overlap the completed title (5.0), horror2 the started one (3.0); cooking is exploration.
    assert ids == ["adams", "drama2", "horror2", "cooking"]


def test_exploration_share_and_deterministic_order() -> None:
    session = memory_session_factory()()
    member = make_user("member")
    session.add(member)
    add_movie(session, "seed", "Seed", genres=["Drama"])
    for index in range(8):
        add_movie(session, f"d{index}", f"Drama {index}", genres=["Drama"])
    for index in range(4):
        add_movie(session, f"f{index}", f"Food {index}", genres=["Food"])
    add_progress(session, "member", "seed-v", completed=True)
    session.commit()
    policy = MemberRecommendationPolicy(session, limit=8)

    first = [title.id for title in policy.titles(member, types=("movie",))]

    assert first == [title.id for title in policy.titles(member, types=("movie",))]
    assert [first[3], first[7]] == ["f0", "f1"]  # floor((position + 1) * 0.25) lands exploration at 3 and 7
    assert all(title_id.startswith("d") for index, title_id in enumerate(first) if index not in (3, 7))


def test_cold_member_gets_nothing_without_an_anchor_and_a_ranked_list_with_one() -> None:
    session = memory_session_factory()()
    _library(session)
    cold = make_user("cold")
    session.add(cold)
    session.commit()
    policy = MemberRecommendationPolicy(session, limit=10)

    assert policy.titles(cold, types=("movie",)) == []
    similar = [title.id for title in policy.titles(cold, anchor=WATCHED_ID, types=("movie",))]
    assert similar[0] == "adams"  # shares a person with the anchor (0.75 context)
    assert WATCHED_ID not in similar and "private" not in similar
    assert policy.titles(cold, anchor="private", types=("movie",)) == []  # an invisible anchor answers nothing


def test_episode_progress_takes_the_whole_series_out_of_recommendations() -> None:
    session = memory_session_factory()()
    member = make_user("member")
    session.add(member)
    add_series(session, "show", "Show", seasons={1: 2}, genres=["Drama"])
    add_series(session, "show2", "Other Show", seasons={1: 1}, genres=["Drama"])
    add_progress(session, "member", "show-s1e1-v", completed=True)
    session.commit()

    assert [title.id for title in MemberRecommendationPolicy(session).titles(member, types=("series",))] == ["show2"]


def test_recently_completed_is_newest_first_visible_and_windowed() -> None:
    session = memory_session_factory()()
    member = _library(session)
    add_movie(session, "old", "Old", genres=["Drama"])
    add_progress(session, "member", "old-v", completed=True, at=BASE - timedelta(days=40))
    add_progress(session, "member", "adams-v", completed=True, at=BASE + timedelta(minutes=5))
    session.commit()

    recent = MemberRecommendationPolicy(session).recently_completed(member, since=BASE - timedelta(days=30))

    assert [title.id for title in recent] == ["adams", WATCHED_ID]


def test_similar_and_suggestions_query_budget_is_flat() -> None:
    session = memory_session_factory()()
    member = make_user("member")
    session.add(member)
    for index in range(60):
        add_movie(session, f"m{index:02d}", f"Movie {index:02d}", genres=["Drama" if index % 2 else "Comedy"], people=[f"Actor {index % 7}"])
    for index in range(10):
        add_series(session, f"s{index}", f"Series {index}", seasons={1: 3}, genres=["Drama"])
    for index in range(0, 20, 3):
        add_progress(session, "member", f"m{index:02d}-v", completed=True)
    add_progress(session, "member", "s0-s1e1-v")
    session.add(MemberFavorite(user_id="member", target_id="m05"))
    session.commit()
    member_access.for_user(session, member)  # the session's one access lookup (ADR 0019) is not part of the budget

    counts: dict[tuple[int, str | None], int] = {}
    for limit in (20, 200):
        for anchor in (None, "m01"):
            statements: list[str] = []
            listener = lambda *args: statements.append(args[2])  # noqa: E731
            event.listen(session.bind, "before_cursor_execute", listener)
            try:
                MemberRecommendationPolicy(session, limit=limit).titles(member, anchor=anchor, types=("movie", "series"))
            finally:
                event.remove(session.bind, "before_cursor_execute", listener)
            counts[(limit, anchor)] = len(statements)

    assert max(counts.values()) <= 12, counts
    assert counts[(20, None)] == counts[(200, None)] and counts[(20, "m01")] == counts[(200, "m01")]


def test_suppress_title_is_idempotent_and_member_scoped() -> None:
    session = memory_session_factory()()
    member, other = make_user("member"), make_user("other")
    session.add_all([member, other])
    service = MemberSuppressionService(session)

    first = service.suppress_title(member, title_id="t1", name="Title")
    assert service.suppress_title(member, title_id="t1").id == first.id
    assert service.active_keys(member).title_keys == frozenset({"t1"})
    assert service.active_keys(other).title_keys == frozenset()
    with pytest.raises(ValueError):
        service.suppress_title(member, title_id=" ")


# ---- Lumina routes ----------------------------------------------------------------------------------

from app.models import utcnow  # noqa: E402


def test_similar_route_ranks_visible_titles_and_hides_private_anchors(db_factory, api_client) -> None:
    with db_factory() as session:
        member = _library(session)
    client = api_client(user=member, base_url="http://localhost")

    similar = client.get(f"/api/titles/{WATCHED_ID}/similar?limit=3")
    assert similar.status_code == 200
    assert [title["id"] for title in similar.json()][:1] == ["adams"]
    assert "private" not in {title["id"] for title in similar.json()}
    assert client.get("/api/titles/private/similar").status_code == 404
    assert client.get("/api/titles/nope/similar").status_code == 404
    assert client.get(f"/api/titles/{WATCHED_ID}/similar?limit=51").status_code == 422


def test_home_title_rows_anchor_on_recent_completions(db_factory, api_client) -> None:
    with db_factory() as session:
        member = _library(session)
        # _library completed WATCHED_ID at BASE; make it recent relative to the real clock.
        from app.models import PlaybackProgress

        session.query(PlaybackProgress).filter(PlaybackProgress.item_id == f"{WATCHED_ID}-v").update({"last_watched_at": utcnow()})
        session.commit()
    client = api_client(user=member, base_url="http://localhost")

    rows = client.get("/api/home/title-rows").json()["rows"]

    assert [(row["id"], row["kind"], row["title"], row["anchor_title_id"]) for row in rows] == [
        (f"because:{WATCHED_ID}", "because_you_watched", "Because you watched Watched", WATCHED_ID),
        ("recommended", "recommended", "Recommended for you", None),
    ]
    assert rows[0]["items"][0]["id"] == "adams"
    assert not {WATCHED_ID, "started", "private", "suppressed"} & {item["id"] for row in rows for item in row["items"]}
