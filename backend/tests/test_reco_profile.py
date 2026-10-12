"""The member profile and the token and candidate builders: hand-computed expectations."""
from __future__ import annotations

import hashlib
import math
import uuid
from array import array
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models import (
    LibraryItem, MediaTitle, MemberInterest, MemberRecommendationSuppression, PlaybackProgress, RecoEvent, RemoteMedia,
    RemotePlaybackProgress, SourceAutomation, WatchQueueEntry,
)
from app.services.media_titles import person_name_id
from app.services.reco import SOURCE_LIBRARY, SOURCE_SEED, SatisfiedItem
from app.services.reco import profile as reco_profile
from discovery_support import add_movie
from support import make_user, memory_session_factory

NOW = datetime(2026, 10, 1, 12, 0, 0)
UC = "https://www.youtube.com/channel/UC"


def _channel(letter: str) -> str:
    return UC + letter * 22


# ---- tokens and candidates ------------------------------------------------------------------------------

def test_remote_tokens_keep_words_bigrams_channel_and_categories() -> None:
    assert reco_profile.remote_tokens("The Deep Sea: Part 2 — Official Video", _channel("a"), ("science-technology",)) == frozenset({
        f"ch:{_channel('a')}", "cat:science-technology", "deep", "sea", "deep sea"})
    assert reco_profile.remote_tokens(None, None, ()) == frozenset()
    many = reco_profile.remote_tokens(" ".join(f"word{i:02d}" for i in range(50)), _channel("a"), ("music",))
    assert len(many) == 40 and f"ch:{_channel('a')}" in many and "cat:music" in many  # the cap keeps channel and category


def _film(**metadata) -> MediaTitle:  # noqa: ANN003
    return MediaTitle(id="m1", type="movie", key="test:m1", name="The Long Winter", boxset_id="b1", year=1987, category="movies",
                      metadata_json=metadata, created_at=NOW - timedelta(days=40), added_at=NOW - timedelta(days=3))


def test_title_tokens_are_genres_first_five_actors_directors_collection_category_and_decade() -> None:
    actors = [{"person_id": f"p{i}", "name": f"Actor {i}", "type": "Actor"} for i in range(6)]
    crew = [{"person_id": None, "name": "Ada Director", "type": "Director"}, {"person_id": "w1", "name": "W", "type": "Writer"}]
    tokens = reco_profile.title_tokens(_film(genres=["Drama", " War "], people=actors + crew))
    assert tokens == frozenset({
        "genre:drama", "genre:war", "cast:p0", "cast:p1", "cast:p2", "cast:p3", "cast:p4",
        f"dir:{person_name_id('Ada Director')}", "col:b1", "cat:movies", "decade:1980"})
    assert reco_profile.title_tokens(_film(genres="Drama", people=None)) == frozenset({"col:b1", "cat:movies", "decade:1980"})


def test_candidates_carry_their_row_unchanged() -> None:
    vector = array("f", [0.6, 0.8])
    row = RemoteMedia(key="a" * 64, source_identity="youtube:x", webpage_url="https://youtu.be/x", title="Deep Sea", uploader="Tide Pools",
                      channel_key=_channel("t"), view_count=1200, published_at=NOW, category_keys=["science-technology"],
                      tokens=["stored", "tokens"], vector=vector.tobytes(), vector_model="local:test")
    candidate = reco_profile.candidate_from_remote(row, sources=SOURCE_SEED, seed_ref="progress-1", channel_view_percentile=0.5)
    assert (candidate.key, candidate.target_kind, candidate.channel_key, candidate.channel_name, candidate.sources) == (
        "a" * 64, "remote", _channel("t"), "Tide Pools", SOURCE_SEED)
    assert candidate.tokens == frozenset({"stored", "tokens"}) and candidate.vector == vector
    assert (candidate.views, candidate.category_keys, candidate.seed_ref, candidate.channel_view_percentile) == (
        1200, ("science-technology",), "progress-1", 0.5)
    bare = RemoteMedia(key="b" * 64, source_identity="youtube:y", webpage_url="https://youtu.be/y", title="Deep Sea", channel_key=None,
                       category_keys=[], tokens=[])
    assert reco_profile.candidate_from_remote(bare, sources=SOURCE_SEED).tokens == frozenset({"deep", "sea", "deep sea"})
    film = reco_profile.candidate_from_title(_film(community_rating=7.5), None)
    assert (film.key, film.target_kind, film.channel_key, film.published_at, film.rating, film.sources) == (
        "m1", "title", "b1", NOW - timedelta(days=3), 7.5, SOURCE_LIBRARY)


# ---- centroids, taste mix, fatigue, suppression keys ----------------------------------------------------

def _angle(degrees: float) -> array:
    return array("f", [math.cos(math.radians(degrees)), math.sin(math.radians(degrees))])


def _direction(angles: list[float], weights: list[float]) -> float:
    return math.degrees(math.atan2(sum(w * math.sin(math.radians(a)) for a, w in zip(angles, weights)),
                                   sum(w * math.cos(math.radians(a)) for a, w in zip(angles, weights))))


def test_k_means_seeds_on_the_newest_vector_then_the_farthest_and_weights_mass() -> None:
    angles, weights = [0, 10, 20, 30, 90, 80, 70, 5, 85], [1, 1, 1, 1, 2, 1, 1, 1, 1]  # newest first; n = 9 gives k = 2
    centres, mass = reco_profile.centroids([(_angle(a), w) for a, w in zip(angles, weights)])
    found = [math.degrees(math.atan2(c[1], c[0])) for c in centres]
    assert found == pytest.approx([_direction([0, 10, 20, 30, 5], [1] * 5), _direction([90, 80, 70, 85], [2, 1, 1, 1])], abs=1e-3)
    assert found == pytest.approx([12.974, 83.016], abs=1e-3)  # centre 0 grew from the newest (0°), centre 1 from 90°
    assert mass == (5.0, 5.0)
    assert reco_profile.centroids([(_angle(0), 1.0), (_angle(1), 1.0)]) == ((), ())  # under 3 vectors: the Jaccard fallback applies
    assert len(reco_profile.centroids([(_angle(i), 1.0) for i in range(17)])[0]) == 3 and len(reco_profile.centroids([(_angle(i), 1.0) for i in range(40)])[0]) == 4
    assert reco_profile.centroids([(_angle(0), 1.0), (array("f", [1.0, 0.0, 0.0]), 1.0), (_angle(5), 1.0), (_angle(9), 1.0)])[1] == (3.0,)


def _seen(key: str, groups: tuple[str, ...], weight: float = 1.0) -> SatisfiedItem:
    return SatisfiedItem(key=key, target_kind="remote", title=key, channel_key=None, tokens=frozenset(), weight=weight, at=NOW,
                         category_keys=groups)


def test_taste_mix_blends_history_and_declared_interests_70_30() -> None:
    history = [_seen("a", ("music",)), _seen("b", ("music", "news"))]  # music 1.5, news 0.5 -> 0.75 / 0.25
    assert reco_profile.taste_mix(history, frozenset({"news", "travel"}), ()) == pytest.approx({"music": 0.525, "news": 0.325, "travel": 0.15})
    assert reco_profile.taste_mix(history, frozenset(), ()) == pytest.approx({"music": 0.75, "news": 0.25})
    assert reco_profile.taste_mix([], frozenset({"news", "travel"}), ()) == pytest.approx({"news": 0.5, "travel": 0.5})
    assert reco_profile.taste_mix(history, frozenset({"news"}), (3.0, 1.0)) == pytest.approx({"centroid:0": 0.75, "centroid:1": 0.25})


def test_fatigue_counts_unopened_impressions_once_per_surface_and_half_hour() -> None:
    day = timedelta(days=1)
    rows = [
        (NOW - 5 * day, "impression", "x", "ch:x", "home_picked"),
        (NOW - 3 * day, "open", "y", "ch:y", "home_picked"),
        (NOW - 2 * day, "impression", "y", "ch:y", "home_picked"),  # after y's open: counts
        (NOW - 1 * day, "impression", "x", "ch:x", "home_picked"),
        (NOW - 1 * day + timedelta(minutes=10), "impression", "x", "ch:x", "home_picked"),  # same surface within 30 min
        (NOW - 1 * day + timedelta(minutes=10), "impression", "x", "ch:x", "explore_for_you"),  # another surface
        (NOW - 1 * day + timedelta(minutes=40), "impression", "x", "ch:x", "home_picked"),
        (NOW - 1 * day + timedelta(minutes=50), "impression", "z", "ch:z", "home_picked"),
        (NOW - 1 * day + timedelta(minutes=55), "play", "z", "ch:z", None),  # z was opened: no fatigue
    ]
    items, channels, out = reco_profile.fatigue_counts(rows, now=NOW, aliases={"ch:y": "ch:stable-y"})
    x_ages = [5, 1, 1 - 10 / 1440, 1 - 40 / 1440]
    assert items["x"] == pytest.approx(sum(0.5 ** (age / 3) for age in x_ages))
    assert channels["ch:x"] == pytest.approx(sum(0.5 ** (age / 7) for age in x_ages))
    assert items["y"] == pytest.approx(0.5 ** (2 / 3)) and channels["ch:stable-y"] == pytest.approx(0.5 ** (2 / 7))
    assert "z" not in items and out == frozenset({"x"})  # 4 unopened impressions in 14 days


def test_item_suppressions_also_match_the_remote_media_key() -> None:
    assert reco_profile.suppressed_item_keys("id:youtube:abc") == frozenset({"id:youtube:abc", hashlib.sha256(b"youtube:abc").hexdigest()})
    assert reco_profile.suppressed_item_keys("url:https://Example.com/watch?x=1") == frozenset({
        "url:https://Example.com/watch?x=1", hashlib.sha256(b"url:https://example.com/watch?x=1").hexdigest()})
    assert reco_profile.suppressed_item_keys("text:youtube:a song:a band") == frozenset({"text:youtube:a song:a band"})


# ---- build_profile ----------------------------------------------------------------------------------------

from app.services.reco import events  # noqa: E402 - imported late to keep the module order readable

ALICE, BOB = "alice", "bob"


@pytest.fixture
def db():  # noqa: ANN201
    factory = memory_session_factory()
    with factory() as session:
        session.add_all([make_user(ALICE), make_user(BOB)])
        session.commit()
        yield session


def _remote(db, user: str, key: str, channel: str | None, *, fraction: float, completions: int = 0, days: float = 0.0,  # noqa: ANN001
            position: float = 300.0, cleared: bool = False, uploader: str = "Someone", title: str = "A video") -> None:
    db.add(RemotePlaybackProgress(
        id=str(uuid.uuid4()), user_id=user, source_identity=f"youtube:{key}", source_identity_key=key, source_url=f"https://youtu.be/{key}",
        extractor="youtube", uploader=uploader, title=title, position_seconds=position, duration_seconds=600.0,
        completed=completions > 0, cleared=cleared, max_fraction=fraction, plays=1, completions=completions, channel_key=channel,
        last_watched_at=NOW - timedelta(days=days, seconds=1)))


def _follow(db, user: str, url: str, *, days: float = 1.0) -> None:  # noqa: ANN001
    db.add(SourceAutomation(id=str(uuid.uuid4()), user_id=user, label="A channel", source_url=url, source_type="channel",
                            cron_expression="0 * * * *", active=True, created_at=NOW - timedelta(days=days)))


def _suppress(db, user: str, scope: str, target: str, *, channel: str | None = None, name: str | None = None, days: float = 0.0) -> None:  # noqa: ANN001
    db.add(MemberRecommendationSuppression(id=str(uuid.uuid4()), user_id=user, scope=scope, target_key=target, channel_key=channel,
                                           channel_name=name, source="youtube", created_at=NOW - timedelta(days=days, seconds=1)))


def test_channel_affinity_follows_the_evidence_table(db) -> None:  # noqa: ANN001
    _follow(db, ALICE, _channel("a"))  # +3.0, no decay
    _remote(db, ALICE, "b1", _channel("b"), fraction=1.0, completions=1)  # +1.5 now
    _remote(db, ALICE, "c1", _channel("c"), fraction=1.0, completions=2, days=30)  # (1.5 + 1.0) × 0.5
    _remote(db, ALICE, "d1", _channel("d"), fraction=0.4)  # 1.0 × clamp(0.3 / 0.6) = 0.5
    _remote(db, ALICE, "e1", _channel("e"), fraction=0.05, position=10, days=30)  # skip: -0.5 × 0.5
    _remote(db, ALICE, "f1", _channel("f"), fraction=0.3, position=0, cleared=True)  # 1/3 partial - 0.25 dismissed
    db.add(LibraryItem(id="g-save", user_id=ALICE, title="Saved", extractor="youtube", uploader="G", channel_key=_channel("g"),
                       created_at=NOW - timedelta(days=180)))  # +2.0 × 0.5
    db.add(WatchQueueEntry(id="q1", user_id=ALICE, position=0, provider="youtube", uploader="Queue Channel", created_at=NOW - timedelta(seconds=1)))  # +0.5
    _suppress(db, ALICE, "item", "id:youtube:i1", channel=_channel("i"), name="I", days=60)  # -1.0 × 0.5
    db.commit()
    profile = reco_profile.build_profile(db, ALICE, now=NOW)
    assert profile.affinity == pytest.approx({
        _channel("a"): 3.0, _channel("b"): 1.5, _channel("c"): 1.25, _channel("d"): 0.5, _channel("e"): -0.25,
        _channel("f"): 1 / 3 - 0.25, _channel("g"): 1.0, "name:youtube:queue channel": 0.5, _channel("i"): -0.5,
    })
    assert profile.followed == frozenset({_channel("a")})
    assert profile.excluded == frozenset({"b1", "c1", "e1", "d1"})  # completed, skipped, and d1 still in Continue watching
    assert profile.spill == {_channel("i"): NOW - timedelta(days=60, seconds=1)}
    assert [item.key for item in profile.satisfied] == ["b1", "c1"]  # newest first
    assert [item.weight for item in profile.satisfied] == pytest.approx([1.0, 1.5 * 0.5 ** 0.5])  # rewatch × 1.5, 60-day half-life


def test_a_legacy_name_key_merges_into_the_stable_key(db) -> None:  # noqa: ANN001
    _remote(db, ALICE, "j1", _channel("j"), fraction=1.0, completions=1, uploader="Jay Channel")
    db.add(LibraryItem(id="j-save", user_id=ALICE, title="Saved", extractor="youtube", uploader="Jay Channel", channel_key=None,
                       created_at=NOW - timedelta(seconds=1)))  # a pre-1.9.0 save keyed by name only
    _suppress(db, ALICE, "fewer", "jay channel", channel=None, days=1)  # a fewer row without a stable key: the bare name
    _suppress(db, ALICE, "channel", "old name", channel=None)
    _suppress(db, ALICE, "channel", "jay channel", channel=_channel("j"))
    db.commit()
    profile = reco_profile.build_profile(db, ALICE, now=NOW)
    assert profile.channel_aliases == {"name:youtube:jay channel": _channel("j")}
    assert profile.affinity == pytest.approx({_channel("j"): 3.5})
    assert profile.hidden_channels == frozenset({"old name", _channel("j")})
    assert profile.fewer == {"jay channel": NOW - timedelta(days=1, seconds=1)}


def test_titles_roll_up_to_the_series_and_carry_their_first_genre(db) -> None:  # noqa: ANN001
    add_movie(db, "m1", "The Long Winter", genres=["Drama", "War"], owner=ALICE)
    add_movie(db, "m2", "Hidden Film", genres=["Comedy"], owner=BOB, visibility="private")
    db.add(PlaybackProgress(id="p1", user_id=ALICE, item_id="m1-v", position_seconds=1200, duration_seconds=1200, completed=True,
                            max_fraction=1.0, plays=1, completions=1, last_watched_at=NOW - timedelta(days=1)))
    db.add(PlaybackProgress(id="p2", user_id=ALICE, item_id="m2-v", position_seconds=1200, duration_seconds=1200, completed=True,
                            max_fraction=1.0, plays=1, completions=1, last_watched_at=NOW - timedelta(days=1)))
    db.commit()
    profile = reco_profile.build_profile(db, ALICE, now=NOW)
    assert [(item.key, item.target_kind, item.category_keys) for item in profile.satisfied] == [("m1", "title", ("genre:drama",))]
    assert {"m1", "m2"} <= profile.excluded  # a finished movie is never offered again, visible or not
    assert profile.taste_mix == pytest.approx({"genre:drama": 1.0}) and profile.centroids == ()  # no embedder: no vectors


def test_suppressions_interests_and_events_become_vetoes_and_fatigue(db) -> None:  # noqa: ANN001
    _suppress(db, ALICE, "item", "id:youtube:abc", name="Abc Channel")
    _suppress(db, ALICE, "title", "m9")
    db.add(MemberInterest(id="i1", user_id=ALICE, category_key="travel", created_at=NOW - timedelta(days=1)))
    for minutes in (60, 120, 180, 240):
        db.add(RecoEvent(user_id=ALICE, at=NOW - timedelta(minutes=minutes), kind="impression", surface="home_picked",
                         target_kind="remote", item_key="k" * 64, channel_key=_channel("k")))
    db.commit()
    profile = reco_profile.build_profile(db, ALICE, now=NOW)
    assert profile.hidden_items == frozenset({"id:youtube:abc", hashlib.sha256(b"youtube:abc").hexdigest()})
    assert profile.hidden_titles == frozenset({"m9"}) and profile.interests == frozenset({"travel"})
    assert profile.spill == {"name:youtube:abc channel": NOW - timedelta(seconds=1)} and profile.fatigued_out == frozenset({"k" * 64})
    assert profile.item_fatigue["k" * 64] == pytest.approx(sum(0.5 ** (m / 1440 / 3) for m in (60, 120, 180, 240)))


def test_one_members_rows_never_reach_another_profile(db) -> None:  # noqa: ANN001
    _follow(db, BOB, _channel("a"))
    _remote(db, BOB, "b1", _channel("b"), fraction=1.0, completions=1)
    _suppress(db, BOB, "channel", "bob hides this", channel=_channel("h"))
    db.add(MemberInterest(id="i1", user_id=BOB, category_key="news"))
    db.add(RecoEvent(user_id=BOB, at=NOW - timedelta(hours=1), kind="impression", surface="home_picked", target_kind="remote",
                     item_key="k" * 64, channel_key=_channel("k")))
    db.commit()
    alice = reco_profile.build_profile(db, ALICE, now=NOW)
    assert (alice.affinity, alice.followed, alice.satisfied, alice.hidden_channels, alice.interests, alice.item_fatigue,
            alice.excluded) == ({}, frozenset(), (), frozenset(), frozenset(), {}, frozenset())


def test_as_of_reads_only_what_existed_before_it(db) -> None:  # noqa: ANN001
    _follow(db, ALICE, _channel("a"), days=10)
    _follow(db, ALICE, _channel("z"), days=1)
    _remote(db, ALICE, "b1", _channel("b"), fraction=1.0, completions=1, days=1)
    db.commit()
    past = reco_profile.build_profile(db, ALICE, now=NOW, as_of=NOW - timedelta(days=5))
    assert past.affinity == {_channel("a"): 3.0} and past.satisfied == () and past.built_at == NOW


# ---- the cache and the query plans ------------------------------------------------------------------------

def test_the_cache_rebuilds_after_ten_minutes_or_a_generation_bump(db) -> None:  # noqa: ANN001
    cache = reco_profile.ProfileCache()
    first = cache.get(db, ALICE, now=NOW)
    assert cache.get(db, ALICE, now=NOW + timedelta(minutes=9, seconds=59)) is first
    _follow(db, ALICE, _channel("a"))
    db.commit()
    assert cache.get(db, ALICE, now=NOW + timedelta(minutes=9)) is first  # a write alone does not invalidate
    events.generations.bump(ALICE)
    bumped = cache.get(db, ALICE, now=NOW + timedelta(minutes=9))
    assert bumped is not first and bumped.affinity == {_channel("a"): 3.0}
    assert cache.get(db, ALICE, now=NOW + timedelta(minutes=19, seconds=1)) is not bumped  # older than 10 minutes
    assert cache.get(db, BOB, now=NOW).user_id == BOB  # one entry per member


def test_profile_queries_on_the_new_tables_seek_their_indexes(db) -> None:  # noqa: ANN001
    for index in range(300):
        db.add(RecoEvent(user_id=BOB if index % 2 else ALICE, at=NOW - timedelta(hours=index), kind="impression",
                         surface="home_picked", target_kind="remote", item_key=f"{index:064x}"))
        db.add(RemoteMedia(key=f"{index:064x}", source_identity=f"youtube:{index}", webpage_url=f"https://youtu.be/{index}", title="t"))
    _remote(db, ALICE, f"{1:064x}", _channel("b"), fraction=1.0, completions=1)
    db.commit()
    statements: list[tuple[str, object]] = []
    engine = db.get_bind()

    def capture(_conn, _cursor, statement, parameters, _context, _many) -> None:  # noqa: ANN001
        if "reco_events" in statement or "remote_media" in statement:
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        reco_profile.build_profile(db, ALICE, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert {("reco_events" in s, "remote_media" in s) for s, _ in statements} == {(True, False), (False, True)}
    with engine.connect() as connection:
        for statement, parameters in statements:
            plan = "\n".join(str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))
            assert "SCAN reco_events" not in plan and "SCAN remote_media" not in plan, plan
            assert "ix_reco_events_user_at" in plan or "remote_media USING INDEX sqlite_autoindex_remote_media_1" in plan, plan
