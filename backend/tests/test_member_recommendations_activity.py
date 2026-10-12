"""Activity-adaptive scoring, exploration, and isolation for issue #88.

These cover the deepened member-scoped recommendation policy: a deterministic
scored ranking over Interest categories, followed channels, playback activity,
deliberate saves, freshness, and popularity, plus a configurable exploration
allocation. The invariant/regression coverage for #85 lives in
``test_member_recommendations.py``; this module owns the #88 behaviors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.models import LibraryItem, PlaybackProgress, RemotePlaybackProgress, User
from app.services.member_recommendations import (
    MemberActivitySignals,
    MemberRecommendationPolicy,
    RecommendationScore,
    score_candidate,
)
from support import make_user, memory_session_factory, popular_item as _item, popular_snapshot as _snapshot


REFERENCE = datetime(2026, 7, 19, tzinfo=UTC)


def _session():
    return memory_session_factory()()


def _remote_watch(
    member: User, remote_id: str, uploader: str, *, completed: bool, position: float = 30.0, cleared: bool = False
) -> RemotePlaybackProgress:
    identity = f"youtube:{remote_id}"
    return RemotePlaybackProgress(
        id=f"rpp-{member.id}-{remote_id}", user_id=member.id, source_identity=identity,
        source_identity_key=f"key-{member.id}-{remote_id}", extractor="youtube", remote_id=remote_id,
        source_url=f"https://www.youtube.com/watch?v={remote_id}", uploader=uploader, position_seconds=position,
        duration_seconds=100.0, completed=completed, cleared=cleared, last_watched_at=REFERENCE,
    )


def _saved(member: User, remote_id: str, uploader: str) -> LibraryItem:
    return LibraryItem(
        id=f"lib-{member.id}-{remote_id}", user_id=member.id, visibility="private", extractor="youtube",
        remote_id=remote_id, title=remote_id.title(), uploader=uploader, status="available",
    )


# --- Scoring is inspectable and deterministic -------------------------------


def test_score_candidate_combines_named_signals_deterministically() -> None:
    signals = MemberActivitySignals(
        selected_interests=frozenset({"music"}),
        followed_channels=frozenset({"followedchannel"}),
        played_uploaders=frozenset({"followedchannel"}),
        completed_uploaders=frozenset({"followedchannel"}),
        saved_uploaders=frozenset({"followedchannel"}),
        frequently_saved_uploaders=frozenset({"followedchannel"}),
        continue_watching_keys=frozenset(),
        reference_time=REFERENCE,
    )
    item = _item(
        "hit", ("music",), uploader="FollowedChannel", view_count=20_000_000,
        published_at=REFERENCE - timedelta(days=2),
    )

    score = score_candidate(item, signals)

    assert isinstance(score, RecommendationScore)
    assert score.interest == 0.5  # one of a possible two selected categories matched
    assert score.follow == 1.0
    assert score.playback == 1.0  # completed watch outranks a mere open
    assert score.save == 1.0
    assert score.recency == 1.0
    assert score.popularity == 1.0
    assert score.total == 5.0 + 4.0 + 3.0 + 2.0 * 0.5 + 1.0 + 1.0
    # Identical inputs must yield an identical, equal score.
    assert score_candidate(item, signals) == score


def test_every_selected_category_surfaces_even_when_provider_front_loads_one() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    # The provider order front-loads music and never interleaves cooking, so a
    # score-tie ordering that fell back to provider position would starve cooking
    # within the limit. Equal-score establishment must round-robin across the
    # member's selected categories instead.
    snapshot = _snapshot(
        _item("m0", ("music",)), _item("m1", ("music",)), _item("m2", ("music",)),
        _item("m3", ("music",)), _item("c0", ("cooking",)), _item("c1", ("cooking",)),
    )

    result = MemberRecommendationPolicy(db, limit=4).home(member, ("music", "cooking"), snapshot)

    ids = [item.id for item in result.items]
    assert {item.category_keys[0] for item in result.items} == {"music", "cooking"}
    assert ids == ["m0", "c0", "m1", "c1"]


def test_stronger_signals_still_outrank_the_category_round_robin() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "seen", "MusicStar", completed=True))
    db.commit()

    # A watched music channel outscores an untouched cooking item; the round-robin
    # only shapes equal-score ties, it never overrides a stronger signal.
    snapshot = _snapshot(
        _item("cook-first", ("cooking",), uploader="Stranger"),
        _item("music-watched", ("music",), uploader="MusicStar"),
    )

    result = MemberRecommendationPolicy(db, limit=2).home(member, ("music", "cooking"), snapshot)

    assert [item.id for item in result.items] == ["music-watched", "cook-first"]


def test_equal_scores_break_ties_on_provider_order_then_source_key() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    # Three identically-scored candidates (same signals, views, no dates):
    # ordering must fall to the provider's snapshot position, deterministically.
    snapshot = _snapshot(
        _item("first", ("music",)),
        _item("second", ("music",)),
        _item("third", ("music",)),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)

    assert [item.id for item in result.items] == ["first", "second", "third"]


def test_score_components_bucket_partial_signals() -> None:
    signals = MemberActivitySignals(
        selected_interests=frozenset({"music", "cooking"}),
        followed_channels=frozenset(),
        played_uploaders=frozenset({"opened"}),
        completed_uploaders=frozenset(),
        saved_uploaders=frozenset({"opened"}),
        frequently_saved_uploaders=frozenset(),
        continue_watching_keys=frozenset(),
        reference_time=REFERENCE,
    )
    item = _item(
        "partial", ("music", "cooking"), uploader="Opened", view_count=200_000,
        published_at=REFERENCE - timedelta(days=45),
    )

    score = score_candidate(item, signals)

    assert score.interest == 1.0  # both selected categories matched
    assert score.follow == 0.0
    assert score.playback == 0.5  # opened but never completed
    assert score.save == 0.5  # exactly one save from this channel
    assert score.recency == 0.5  # 45 days old
    assert score.popularity == 0.5  # 200k views


# --- Signal weighting drives established-interest ordering -------------------


def test_followed_and_played_channels_rank_above_unsignaled() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "played-src", "PlayedChannel", completed=True))
    db.commit()

    snapshot = _snapshot(
        _item("plain", ("music",), uploader="Nobody"),
        _item("played", ("music",), uploader="PlayedChannel"),
        _item("followed", ("music",), uploader="FollowedChannel"),
    )

    result = MemberRecommendationPolicy(db).home(
        member, ("music",), snapshot, followed_channel_keys=frozenset({"followedchannel"})
    )

    assert [item.id for item in result.items] == ["followed", "played", "plain"]


def test_save_affinity_boosts_candidates_from_saved_channels() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add_all([_saved(member, "saved-a", "Chef"), _saved(member, "saved-b", "Chef")])
    db.commit()

    snapshot = _snapshot(
        _item("unknown", ("cooking",), uploader="Stranger"),
        _item("fresh-chef", ("cooking",), uploader="Chef"),
    )

    result = MemberRecommendationPolicy(db).home(member, ("cooking",), snapshot)

    assert [item.id for item in result.items] == ["fresh-chef", "unknown"]


def test_recency_uses_snapshot_reference_not_wall_clock() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("stale", ("music",), published_at=REFERENCE - timedelta(days=400)),
        _item("fresh", ("music",), published_at=REFERENCE - timedelta(days=1)),
    )

    policy = MemberRecommendationPolicy(db)
    first = policy.home(member, ("music",), snapshot)
    second = policy.home(member, ("music",), snapshot)

    assert [item.id for item in first.items] == ["fresh", "stale"]
    # Deterministic regardless of when it runs.
    assert [item.id for item in first.items] == [item.id for item in second.items]


# --- Exploration allocation --------------------------------------------------


def test_exploration_reserves_about_a_quarter_of_positions_and_distributes() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    established = [_item(f"music-{index:02d}", ("music",)) for index in range(18)]
    exploration = [_item(f"game-{index:02d}", ("gaming",)) for index in range(18)]
    snapshot = _snapshot(*established, *exploration)

    result = MemberRecommendationPolicy(db, limit=24).home(member, ("music",), snapshot)

    ids = [item.id for item in result.items]
    assert len(ids) == 24
    exploration_positions = [index for index, item_id in enumerate(ids) if item_id.startswith("game-")]
    # Roughly a quarter of the 24 positions, evenly distributed rather than dumped at the end.
    assert exploration_positions == [3, 7, 11, 15, 19, 23]
    # Deterministic and stable.
    assert ids == [item.id for item in MemberRecommendationPolicy(db, limit=24).home(member, ("music",), snapshot).items]


def test_exploration_ratio_is_configurable() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    established = [_item(f"music-{index:02d}", ("music",)) for index in range(12)]
    exploration = [_item(f"game-{index:02d}", ("gaming",)) for index in range(12)]
    snapshot = _snapshot(*established, *exploration)

    none = MemberRecommendationPolicy(db, limit=12, exploration_ratio=0.0).home(member, ("music",), snapshot)
    half = MemberRecommendationPolicy(db, limit=12, exploration_ratio=0.5).home(member, ("music",), snapshot)

    assert all(item.id.startswith("music-") for item in none.items)
    assert sum(item.id.startswith("game-") for item in half.items) == 6


def test_watched_channel_is_established_and_unfamiliar_channels_are_exploration() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "seen-game", "KnownGamer", completed=True))
    db.commit()

    # The member has no interests, but has watched KnownGamer. Its content is a
    # demonstrated preference (established), while the two never-seen channels
    # are the genuinely unfamiliar exploration pool.
    snapshot = _snapshot(
        _item("novel-a", ("gaming",), uploader="FreshFace"),
        _item("familiar", ("gaming",), uploader="KnownGamer"),
        _item("novel-b", ("gaming",), uploader="NewVoice"),
    )

    result = MemberRecommendationPolicy(db, limit=3).home(member, (), snapshot)

    ids = [item.id for item in result.items]
    # The watched channel's item leads as established personalization; the
    # unfamiliar channels fill the remaining (exploration) positions.
    assert ids[0] == "familiar"
    assert set(ids[1:]) == {"novel-a", "novel-b"}


def test_cold_start_with_no_anchor_returns_deterministic_degraded_state() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("low", ("gaming",), view_count=10),
        _item("high", ("gaming",), view_count=5_000_000),
        _item("mid", ("news",), view_count=100_000),
    )

    result = MemberRecommendationPolicy(db).home(member, (), snapshot)

    # No interests, follows, or activity: Home keeps a clear degraded/empty
    # state (onboarding) rather than becoming a feed of entirely unfamiliar
    # media — that is the separate Popular surface's job.
    assert result.items == ()
    assert result.state == "empty"


def test_activity_alone_anchors_a_feed_without_any_interests() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "watched-src", "WatchedChannel", completed=True))
    db.commit()

    snapshot = _snapshot(
        _item("from-watched", ("gaming",), uploader="WatchedChannel"),
        _item("unfamiliar-a", ("gaming",), uploader="Stranger"),
        _item("unfamiliar-b", ("news",), uploader="Newsroom"),
    )

    result = MemberRecommendationPolicy(db, limit=8).home(member, (), snapshot)

    ids = [item.id for item in result.items]
    # Demonstrated activity is a first-class anchor even with zero interests:
    # the watched channel leads, and unfamiliar channels ride along as exploration.
    assert ids[0] == "from-watched"
    assert set(ids) == {"from-watched", "unfamiliar-a", "unfamiliar-b"}


# --- Continue Watching and Library exclusion under scoring -------------------


def test_continue_watching_remote_entry_excluded_but_channel_signal_kept() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "resume-vid", "Vlogger", completed=False, position=42.0))
    db.commit()

    snapshot = _snapshot(
        _item("resume-vid", ("music",), uploader="Vlogger"),
        _item("other-vlogger", ("music",), uploader="Vlogger"),
        _item("stranger", ("music",), uploader="Stranger"),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)

    ids = [item.id for item in result.items]
    assert "resume-vid" not in ids  # the active Continue Watching source is not re-offered
    assert ids == ["other-vlogger", "stranger"]  # its channel still lifts a sibling


def test_visible_saved_and_unavailable_excluded_across_pools() -> None:
    db = _session()
    member = make_user("member")
    other = make_user("other")
    db.add_all([
        member, other,
        LibraryItem(id="shared-save", user_id=other.id, visibility="shared", extractor="youtube", remote_id="shared-save", title="Shared", status="available"),
    ])
    db.commit()

    snapshot = _snapshot(
        _item("music-keep", ("music",)),
        _item("shared-save", ("gaming",)),  # visible save must not resurface even as exploration
        _item("blocked", ("gaming",), availability="private"),
        _item("game-keep", ("gaming",)),
    )

    result = MemberRecommendationPolicy(db, limit=10).home(member, ("music",), snapshot)

    ids = [item.id for item in result.items]
    assert "shared-save" not in ids
    assert "blocked" not in ids
    assert set(ids) == {"music-keep", "game-keep"}


# --- Member isolation --------------------------------------------------------


def test_activity_signals_do_not_leak_between_members() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.add(_remote_watch(first, "a-src", "AChannel", completed=True))
    db.add_all([_saved(second, "b-a", "BChannel"), _saved(second, "b-b", "BChannel")])
    db.commit()

    snapshot = _snapshot(
        _item("from-a", ("music",), uploader="AChannel"),
        _item("from-b", ("music",), uploader="BChannel"),
        _item("neutral", ("music",), uploader="Neutral"),
    )
    policy = MemberRecommendationPolicy(db)

    first_feed = [item.id for item in policy.home(first, ("music",), snapshot).items]
    second_feed = [item.id for item in policy.home(second, ("music",), snapshot).items]

    assert first_feed[0] == "from-a"  # first's playback lifts A, not B
    assert second_feed[0] == "from-b"  # second's saves lift B, not A
    assert first_feed != second_feed


def test_local_playback_of_a_shared_item_boosts_its_channel() -> None:
    db = _session()
    member, other = make_user("member"), make_user("other")
    # A shared item owned by another member: this member watched it but never
    # saved it, so the lift is playback affinity, not a save.
    watched = LibraryItem(
        id="shared-item", user_id=other.id, visibility="shared", extractor="youtube", remote_id="shared-item",
        title="Shared", uploader="SharedChef", status="available",
    )
    db.add_all([member, other, watched])
    db.add(PlaybackProgress(
        id="pp", user_id=member.id, item_id="shared-item", position_seconds=90,
        duration_seconds=100, completed=True, last_watched_at=REFERENCE,
    ))
    db.commit()

    snapshot = _snapshot(
        _item("neutral", ("cooking",), uploader="Stranger"),
        _item("sibling", ("cooking",), uploader="SharedChef"),  # different source, same channel
    )

    result = MemberRecommendationPolicy(db).home(member, ("cooking",), snapshot)

    assert [item.id for item in result.items] == ["sibling", "neutral"]


def test_local_playback_completion_outranks_open() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    # Two remote watches: one completed channel, one merely opened channel.
    db.add_all([
        _remote_watch(member, "done-src", "Finished", completed=True),
        _remote_watch(member, "open-src", "Started", completed=False, position=5.0),
    ])
    db.commit()

    snapshot = _snapshot(
        _item("opened-item", ("music",), uploader="Started"),
        _item("finished-item", ("music",), uploader="Finished"),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)

    assert [item.id for item in result.items] == ["finished-item", "opened-item"]


# --- Negative / pinned behavior ---------------------------------------------


@pytest.mark.parametrize("ratio", [-0.1, 1.0, 1.5])
def test_exploration_ratio_out_of_range_is_rejected(ratio: float) -> None:
    db = _session()
    with pytest.raises(ValueError):
        MemberRecommendationPolicy(db, exploration_ratio=ratio)


def test_cleared_remote_resume_contributes_no_channel_affinity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    # A cleared resume checkpoint is dismissed history: it must not lift its channel.
    db.add(_remote_watch(member, "gone-src", "ClearedChannel", completed=False, cleared=True))
    db.commit()

    # ClearedChannel is placed *after* neutral in provider order; if the cleared
    # record wrongly contributed affinity, its item would jump ahead.
    snapshot = _snapshot(
        _item("neutral", ("music",), uploader="Stranger"),
        _item("cleared-chan", ("music",), uploader="ClearedChannel"),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)

    assert [item.id for item in result.items] == ["neutral", "cleared-chan"]


def test_completed_remote_watch_stays_an_eligible_candidate() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.add(_remote_watch(member, "vid-done", "DoneChannel", completed=True))
    db.commit()

    # A finished remote watch is neither a Library save nor a Continue Watching
    # resume point, so its source remains eligible — and its channel is lifted.
    snapshot = _snapshot(
        _item("neutral", ("music",), uploader="Stranger"),
        _item("vid-done", ("music",), uploader="DoneChannel"),
    )

    result = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)

    ids = [item.id for item in result.items]
    assert "vid-done" in ids
    assert ids == ["vid-done", "neutral"]


def test_local_playback_join_query_is_column_narrow() -> None:
    db = _session()
    member = make_user("member")
    watched = LibraryItem(
        id="own-item", user_id=member.id, visibility="private", extractor="youtube", remote_id="own-item",
        title="Own", uploader="OwnChannel", status="available",
    )
    db.add_all([member, watched])
    db.add(PlaybackProgress(
        id="pp", user_id=member.id, item_id="own-item", position_seconds=10, duration_seconds=100, completed=True,
    ))
    db.commit()

    statements: list[str] = []
    listener = lambda _conn, _cursor, statement, _parameters, _context, _executemany: statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        MemberRecommendationPolicy(db).home(member, ("music",), _snapshot(_item("fresh", ("music",))))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    join_statement = next(
        statement for statement in statements
        if "JOIN library_items" in statement and "playback_progress" in statement
    )
    # Only the two columns the affinity needs, not the wide Library row.
    assert "library_items.uploader" in join_statement
    assert "playback_progress.completed" in join_statement
    assert "library_items.metadata_json" not in join_statement
    assert "library_items.thumbnail_url" not in join_statement
