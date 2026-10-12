"""Playback-contextual Up Next / related media through the shared policy (#89).

These cover the shared member-scoped recommendation policy's Up Next surface:
contextual weighting (current creator and subject matter) as a scored INPUT to
the same scorer #88 defines, deterministic blending of provider-related
candidates with the local pool, graceful degraded fallback, exclusions,
preserved exploration, capability-aware deprioritization so autoplay-eligible
positions stay playable, and member isolation. The Home-facing invariants live
in the sibling suites (``test_member_recommendations`` / ``_activity``); this
module owns #89. One policy, no second ranking path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import LibraryItem, RemotePlaybackProgress, User
from app.schemas import MediaSourceCapabilities
from app.services.member_recommendations import (
    MemberActivitySignals,
    MemberRecommendationPolicy,
    PlaybackContext,
    RecommendationScore,
    score_candidate,
)
from support import make_user, memory_session_factory, popular_item as _item, popular_snapshot as _snapshot


REFERENCE = datetime(2026, 7, 19, tzinfo=UTC)


def _session():
    return memory_session_factory()()


def _playable(lifecycle: str = "vod") -> MediaSourceCapabilities:
    return MediaSourceCapabilities(provider="youtube", lifecycle=lifecycle, can_play=True, can_acquire=True)


def _not_playable(lifecycle: str = "live", reason: str = "live_playback_not_supported") -> MediaSourceCapabilities:
    acquire_reason = "live_acquisition_not_supported" if lifecycle == "live" else reason
    return MediaSourceCapabilities(
        provider="youtube", lifecycle=lifecycle, can_play=False, play_reason=reason,
        can_acquire=False, acquire_reason=acquire_reason,
    )


def _context(*, source_id: str = "current", uploader: str = "NowPlaying", subjects: tuple[str, ...] = ()) -> PlaybackContext:
    return PlaybackContext.for_source(
        source="youtube", item_id=source_id, webpage_url=f"https://www.youtube.com/watch?v={source_id}",
        title="Now Playing", uploader=uploader, subject_keys=subjects,
    )


def _remote_watch(member: User, remote_id: str, uploader: str, *, completed: bool) -> RemotePlaybackProgress:
    return RemotePlaybackProgress(
        id=f"rpp-{member.id}-{remote_id}", user_id=member.id, source_identity=f"youtube:{remote_id}",
        source_identity_key=f"key-{member.id}-{remote_id}", extractor="youtube", remote_id=remote_id,
        source_url=f"https://www.youtube.com/watch?v={remote_id}", uploader=uploader, position_seconds=30.0,
        duration_seconds=100.0, completed=completed, cleared=False, last_watched_at=REFERENCE,
    )


def _saved(member: User, remote_id: str, uploader: str) -> LibraryItem:
    return LibraryItem(
        id=f"lib-{member.id}-{remote_id}", user_id=member.id, visibility="private", extractor="youtube",
        remote_id=remote_id, title=remote_id.title(), uploader=uploader, status="available",
    )


# --- Contextual weighting is a scored input to the shared scorer -------------


def test_context_score_component_is_named_quarter_step_and_off_without_context() -> None:
    signals = MemberActivitySignals(
        selected_interests=frozenset(), followed_channels=frozenset(), played_uploaders=frozenset(),
        completed_uploaders=frozenset(), saved_uploaders=frozenset(), frequently_saved_uploaders=frozenset(),
        continue_watching_keys=frozenset(), reference_time=REFERENCE,
    )
    context = PlaybackContext(source_key="id:youtube:current", creator_key="nowplaying", subject_keys=frozenset({"music"}))
    same_creator = _item("a", ("gaming",), uploader="NowPlaying")
    same_subject = _item("b", ("music",), uploader="Other")
    unrelated = _item("c", ("news",), uploader="Other")

    assert score_candidate(same_creator, signals, context).context == 1.0
    assert score_candidate(same_subject, signals, context).context == 0.5
    assert score_candidate(unrelated, signals, context).context == 0.0
    # No context supplied (Home) leaves the component and the total unchanged.
    assert score_candidate(same_creator, signals).context == 0.0
    assert isinstance(score_candidate(same_creator, signals, context), RecommendationScore)
    # The weighted total stays an exact multiple of a quarter step (deterministic).
    total = score_candidate(same_creator, signals, context).total
    assert round(total * 4) == total * 4


def test_up_next_contextual_weighting_prioritizes_creator_then_subject_keeping_exploration() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("unrelated", ("news",), uploader="Stranger", capabilities=_playable()),
        _item("same-subject", ("music",), uploader="OtherChef", capabilities=_playable()),
        _item("same-creator", ("gaming",), uploader="NowPlaying", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))

    result = MemberRecommendationPolicy(db, limit=3).up_next(member, (), snapshot, current=context)

    # Current creator is the strongest anchor, related subject next, and the
    # unfamiliar/unrelated item still rides along as reserved exploration.
    assert [item.id for item in result.items] == ["same-creator", "same-subject", "unrelated"]


def test_up_next_context_never_bypasses_a_stronger_member_signal() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    # A followed + completed channel the member has demonstrated preference for.
    db.add(_remote_watch(member, "seen", "FollowedStar", completed=True))
    db.commit()

    snapshot = _snapshot(
        _item("context-only", ("news",), uploader="NowPlaying", capabilities=_playable()),
        _item("followed", ("news",), uploader="FollowedStar", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying")

    result = MemberRecommendationPolicy(db, limit=2).up_next(
        member, (), snapshot, current=context, followed_channel_keys=frozenset({"followedstar"}),
    )

    # follow(5) + playback(4) outranks context(3): current-context adjusts, it
    # does not override demonstrated member-scoped signal.
    assert [item.id for item in result.items] == ["followed", "context-only"]


# --- Deterministic blending + graceful degraded fallback ---------------------


def test_up_next_blends_provider_related_with_local_pool_deterministically() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    provider = [
        _item("prov-1", uploader="NowPlaying", capabilities=_playable()),
        _item("prov-2", uploader="NowPlaying", capabilities=_playable()),
    ]
    snapshot = _snapshot(
        _item("local-music", ("music",), uploader="LocalChef", capabilities=_playable()),
        _item("local-news", ("news",), uploader="Stranger", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))
    policy = MemberRecommendationPolicy(db, limit=8)

    first = [item.id for item in policy.up_next(member, (), snapshot, current=context, provider_related=provider).items]
    second = [item.id for item in policy.up_next(member, (), snapshot, current=context, provider_related=provider).items]

    assert first == second  # identical inputs, identical output
    assert {"prov-1", "prov-2", "local-music", "local-news"} == set(first)  # both pools blended
    # Same-creator provider candidates and the adjacent-subject local candidate
    # lead the unrelated exploration item.
    assert first.index("prov-1") < first.index("local-news")
    assert first.index("local-music") < first.index("local-news")


def test_up_next_falls_back_to_local_pool_when_provider_related_is_empty() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("local-music", ("music",), uploader="LocalChef", capabilities=_playable()),
        _item("local-news", ("news",), uploader="Stranger", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))

    result = MemberRecommendationPolicy(db, limit=8).up_next(member, (), snapshot, current=context, provider_related=())

    assert set(item.id for item in result.items) == {"local-music", "local-news"}
    assert result.items[0].id == "local-music"  # adjacent subject anchors ahead of the unrelated item


def test_up_next_uses_provider_related_when_local_pool_is_empty() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    provider = [
        _item("prov-1", uploader="NowPlaying", capabilities=_playable()),
        _item("prov-2", uploader="NowPlaying", capabilities=_playable()),
    ]
    context = _context(uploader="NowPlaying")

    result = MemberRecommendationPolicy(db, limit=8).up_next(
        member, (), _snapshot(), current=context, provider_related=provider,
    )

    assert set(item.id for item in result.items) == {"prov-1", "prov-2"}


# --- Exclusions --------------------------------------------------------------


def test_up_next_excludes_current_source_duplicates_and_visible_library() -> None:
    db = _session()
    member = make_user("member")
    other = make_user("other")
    db.add_all([
        member, other,
        LibraryItem(id="saved", user_id=other.id, visibility="shared", extractor="youtube", remote_id="saved-vid", title="Saved", status="available"),
    ])
    db.commit()

    provider = [_item("dup", uploader="NowPlaying", capabilities=_playable())]
    snapshot = _snapshot(
        _item("current", ("music",), uploader="NowPlaying", capabilities=_playable()),  # the source being watched
        _item("dup", ("music",), uploader="NowPlaying", capabilities=_playable()),  # duplicate of a provider candidate
        _item("saved-vid", ("music",), uploader="Chef", capabilities=_playable()),  # a visible Library save
        _item("keep", ("music",), uploader="Fresh", capabilities=_playable()),
    )
    context = _context(source_id="current", uploader="NowPlaying", subjects=("music",))

    result = MemberRecommendationPolicy(db, limit=8).up_next(
        member, ("music",), snapshot, current=context, provider_related=provider,
    )

    ids = [item.id for item in result.items]
    assert "current" not in ids  # the current source is never re-offered
    assert "saved-vid" not in ids  # a remote source already visible in the Library is excluded
    assert ids.count("dup") == 1  # provider/local duplicate collapses to one
    assert "keep" in ids


# --- Capability-aware deprioritization (routed follow-up, one shared layer) ---


def test_up_next_deprioritizes_known_not_playable_out_of_autoplay_positions() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        # Highest raw context score (same creator) but not playable in this snapshot.
        _item("live-same-creator", uploader="NowPlaying", capabilities=_not_playable("live")),
        # Lower score (unrelated) but playable.
        _item("vod-stranger", ("news",), uploader="Stranger", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying")

    result = MemberRecommendationPolicy(db, limit=8).up_next(member, (), snapshot, current=context)

    ids = [item.id for item in result.items]
    # A known not-playable remote candidate must not hold the first,
    # autoplay-eligible position — a playable candidate leads instead.
    assert ids[0] == "vod-stranger"
    # It is still surfaced (honest, downloadable) but deprioritized to the tail.
    assert ids[-1] == "live-same-creator"


def test_up_next_first_position_is_playable_whenever_any_candidate_is_playable() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("live-a", uploader="NowPlaying", capabilities=_not_playable("live")),
        _item("upcoming-b", uploader="NowPlaying", capabilities=_not_playable("upcoming", "upcoming_not_started")),
        _item("vod-c", ("music",), uploader="OtherChef", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))

    result = MemberRecommendationPolicy(db, limit=8).up_next(member, (), snapshot, current=context)

    assert result.items[0].capabilities is not None
    assert result.items[0].capabilities.can_play


def test_up_next_still_surfaces_only_not_playable_candidates_without_promoting_playability() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("live-a", uploader="NowPlaying", capabilities=_not_playable("live")),
        _item("upcoming-b", ("music",), uploader="OtherChef", capabilities=_not_playable("upcoming", "upcoming_not_started")),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))

    result = MemberRecommendationPolicy(db, limit=8).up_next(member, (), snapshot, current=context)

    # They remain visible (a member can still queue them for download), and the
    # policy never fabricates playability — the surface's autoplay guard refuses
    # to auto-advance into any of them.
    assert {item.id for item in result.items} == {"live-a", "upcoming-b"}
    assert all(item.capabilities is not None and not item.capabilities.can_play for item in result.items)


def test_home_keeps_not_playable_capability_items_as_previews() -> None:
    """The chosen Home-vs-Up Next rule: Home does not filter on capability.

    A live item is honest as a Home preview (its card gates the action, per
    #91); only Up Next's autoplay-eligible ordering is capability-aware.
    """
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    snapshot = _snapshot(
        _item("live-music", ("music",), uploader="LiveChannel", capabilities=_not_playable("live")),
        _item("vod-music", ("music",), uploader="VodChannel", capabilities=_playable()),
    )

    result = MemberRecommendationPolicy(db, limit=8).home(member, ("music",), snapshot)

    assert "live-music" in [item.id for item in result.items]


# --- Exploration preserved (not a Home copy) ---------------------------------


def test_up_next_reserves_evenly_spaced_exploration_positions() -> None:
    db = _session()
    member = make_user("m")
    db.add(member)
    db.commit()

    established = [_item(f"creator-{index:02d}", uploader="NowPlaying", capabilities=_playable()) for index in range(8)]
    exploration = [_item(f"novel-{index:02d}", ("news",), uploader=f"Stranger{index}", capabilities=_playable()) for index in range(8)]
    snapshot = _snapshot(*established, *exploration)
    context = _context(uploader="NowPlaying")

    result = MemberRecommendationPolicy(db, limit=8).up_next(member, (), snapshot, current=context)

    ids = [item.id for item in result.items]
    exploration_positions = [index for index, item_id in enumerate(ids) if item_id.startswith("novel-")]
    # ~25% of positions, evenly distributed rather than dumped at the end: Up
    # Next keeps deliberate space for unfamiliar work instead of copying Home.
    assert exploration_positions == [3, 7]


# --- Member isolation --------------------------------------------------------


def test_up_next_signals_and_context_do_not_leak_between_members() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.add(_remote_watch(first, "a-src", "AChannel", completed=True))
    db.add_all([_saved(second, "b-a", "BChannel"), _saved(second, "b-b", "BChannel")])
    db.commit()

    snapshot = _snapshot(
        _item("from-a", ("music",), uploader="AChannel", capabilities=_playable()),
        _item("from-b", ("music",), uploader="BChannel", capabilities=_playable()),
        _item("neutral", ("music",), uploader="Neutral", capabilities=_playable()),
    )
    context = _context(uploader="NowPlaying", subjects=("music",))
    policy = MemberRecommendationPolicy(db, limit=8)

    first_feed = [item.id for item in policy.up_next(first, ("music",), snapshot, current=context).items]
    second_feed = [item.id for item in policy.up_next(second, ("music",), snapshot, current=context).items]

    assert first_feed[0] == "from-a"  # first's playback lifts A, not B
    assert second_feed[0] == "from-b"  # second's saves lift B, not A
    assert first_feed != second_feed
