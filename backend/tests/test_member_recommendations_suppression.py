"""Central suppression filtering in the shared recommendation policy (#90).

Suppression is applied once, inside the shared ``_recommend`` core, so Home,
Up Next, and related all drop a suppressed candidate uniformly — a suppressed
candidate cannot disappear from Home while lingering in Up Next. These tests
exercise the policy directly (the surface wiring is covered by the endpoint
tests).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import User
from app.services.member_recommendations import MemberRecommendationPolicy, PlaybackContext
from app.services.member_suppressions import MemberSuppressionService
from app.services.popular_discovery import PopularItem
from support import make_user, memory_session_factory, popular_item as _item, popular_snapshot as _snapshot


REFERENCE = datetime(2026, 7, 19, tzinfo=UTC)


def _session():
    return memory_session_factory()()


def _committed_member(db, member_id: str = "member") -> User:
    member = make_user(member_id)
    db.add(member)
    db.commit()
    return member


# --- Home filters a suppressed item and a suppressed channel -----------------


def test_home_drops_a_suppressed_item_by_its_stable_identity() -> None:
    db = _session()
    member = _committed_member(db)
    MemberSuppressionService(db).suppress_item(
        member, source="youtube", item_id="hidden", webpage_url=None, title="Hidden", uploader="Creator"
    )
    db.commit()

    result = MemberRecommendationPolicy(db).home(
        member, ("music",), _snapshot(_item("hidden", ("music",)), _item("shown", ("music",)))
    )

    assert [item.id for item in result.items] == ["shown"]


def test_home_drops_every_candidate_from_a_suppressed_channel() -> None:
    db = _session()
    member = _committed_member(db)
    MemberSuppressionService(db).suppress_channel(member, uploader="Muted Channel")
    db.commit()

    result = MemberRecommendationPolicy(db).home(member, ("music",), _snapshot(
        _item("muted-a", ("music",), uploader="Muted Channel"),
        _item("muted-b", ("music",), uploader="muted channel"),  # same casefolded identity
        _item("kept", ("music",), uploader="Other Channel"),
    ))

    assert [item.id for item in result.items] == ["kept"]


# --- Cross-surface: one suppression removes the candidate from BOTH surfaces --


def test_a_single_channel_suppression_applies_to_home_and_up_next() -> None:
    db = _session()
    member = _committed_member(db)
    MemberSuppressionService(db).suppress_channel(member, uploader="Muted Channel")
    db.commit()
    snapshot = _snapshot(
        _item("muted", ("music",), uploader="Muted Channel"),
        _item("kept", ("music",), uploader="Other Channel"),
    )

    home = MemberRecommendationPolicy(db).home(member, ("music",), snapshot)
    context = PlaybackContext.for_source(
        source="youtube", item_id="now", webpage_url=None, title="Now", uploader="Anchor", subject_keys=("music",)
    )
    up_next = MemberRecommendationPolicy(db, limit=8).up_next(member, ("music",), snapshot, current=context)

    assert "muted" not in [item.id for item in home.items]
    assert "muted" not in [item.id for item in up_next.items]
    assert "kept" in [item.id for item in up_next.items]


# --- Immediate replacement: a suppressed slot fills with the next candidate ---


def test_suppressing_a_visible_candidate_promotes_a_previously_cut_one() -> None:
    db = _session()
    member = _committed_member(db)
    snapshot = _snapshot(
        _item("first", ("music",)), _item("second", ("music",)), _item("third", ("music",))
    )
    policy = MemberRecommendationPolicy(db, limit=2, exploration_ratio=0.0)

    before = [item.id for item in policy.home(member, ("music",), snapshot).items]
    assert before == ["first", "second"]  # third is cut by the limit

    MemberSuppressionService(db).suppress_item(
        member, source="youtube", item_id="first", webpage_url=None, title="First", uploader="Creator"
    )
    db.commit()

    after = [item.id for item in policy.home(member, ("music",), snapshot).items]
    assert after == ["second", "third"]  # the freed slot is filled, not left empty


# --- Restoration makes the candidate eligible again --------------------------


def test_restoring_a_suppression_makes_the_candidate_eligible_again() -> None:
    db = _session()
    member = _committed_member(db)
    snapshot = _snapshot(_item("restorable", ("music",)), _item("other", ("music",)))
    service = MemberSuppressionService(db)
    record = service.suppress_item(
        member, source="youtube", item_id="restorable", webpage_url=None, title="Restorable", uploader="Creator"
    )
    db.commit()
    assert "restorable" not in [item.id for item in MemberRecommendationPolicy(db).home(member, ("music",), snapshot).items]

    service.restore(member, record.id)
    db.commit()

    assert "restorable" in [item.id for item in MemberRecommendationPolicy(db).home(member, ("music",), snapshot).items]


# --- Suppression overrides a follow boost without unfollowing -----------------


def test_a_suppressed_channel_is_dropped_even_when_it_is_followed() -> None:
    db = _session()
    member = _committed_member(db)
    MemberSuppressionService(db).suppress_channel(member, uploader="Followed But Muted")
    db.commit()

    # The follow seam still supplies the channel key (the follow itself is
    # untouched); central suppression removes it from the ranked feed anyway.
    result = MemberRecommendationPolicy(db).home(
        member, ("music",),
        _snapshot(_item("muted", ("music",), uploader="Followed But Muted"), _item("kept", ("music",), uploader="Other")),
        followed_channel_keys=frozenset({"followed but muted"}),
    )

    assert [item.id for item in result.items] == ["kept"]


# --- Member isolation --------------------------------------------------------


def test_one_members_suppression_does_not_filter_anothers_feed() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    MemberSuppressionService(db).suppress_channel(first, uploader="Muted For First")
    db.commit()
    snapshot = _snapshot(_item("candidate", ("music",), uploader="Muted For First"))

    first_feed = MemberRecommendationPolicy(db).home(first, ("music",), snapshot)
    second_feed = MemberRecommendationPolicy(db).home(second, ("music",), snapshot)

    assert [item.id for item in first_feed.items] == []
    assert [item.id for item in second_feed.items] == ["candidate"]


# --- Restore makes eligible, but does not FORCE into the current visible set ---


def test_restore_makes_eligible_without_pinning_into_the_current_result_set() -> None:
    db = _session()
    member = _committed_member(db)
    # Two equally-scored music candidates: "top" leads by provider order, so
    # "restorable" naturally ranks below a single visible slot.
    snapshot = _snapshot(_item("top", ("music",)), _item("restorable", ("music",)))
    service = MemberSuppressionService(db)
    narrow = MemberRecommendationPolicy(db, limit=1, exploration_ratio=0.0)

    # Baseline: even un-suppressed, "restorable" is below the one visible slot.
    assert [item.id for item in narrow.home(member, ("music",), snapshot).items] == ["top"]

    record = service.suppress_item(
        member, source="youtube", item_id="restorable", webpage_url=None, title="Restorable", uploader="Creator"
    )
    db.commit()
    assert [item.id for item in narrow.home(member, ("music",), snapshot).items] == ["top"]

    service.restore(member, record.id)
    db.commit()
    # Restore does NOT inject or pin the candidate: it still ranks below the limit
    # exactly as before the suppression — it is merely eligible again.
    assert [item.id for item in narrow.home(member, ("music",), snapshot).items] == ["top"]
    # Proof of eligibility: a wider window now surfaces the restored candidate.
    wide = MemberRecommendationPolicy(db, limit=8, exploration_ratio=0.0)
    assert "restorable" in [item.id for item in wide.home(member, ("music",), snapshot).items]


# --- The url:-fallback identity branch (no id) also matches and drops ----------


def test_home_drops_a_url_keyed_candidate_suppressed_by_webpage_url_only() -> None:
    db = _session()
    member = _committed_member(db)
    # A non-YouTube source with no stable id: its identity is the url: branch of
    # _source_key — the case most prone to a store-vs-candidate key mismatch.
    url = "https://vimeo.com/tracks/12345"
    candidate = PopularItem(
        id=None, title="Url Only", uploader="Creator", duration=120, thumbnail=None, artwork_url=None,
        webpage_url=url, view_count=10, availability="public", published_at=None,
        source="vimeo", source_label="Vimeo", capabilities=None, category_keys=("music",),
    )
    MemberSuppressionService(db).suppress_item(
        member, source="vimeo", item_id=None, webpage_url=url, title="Url Only", uploader="Creator"
    )
    db.commit()

    result = MemberRecommendationPolicy(db).home(member, ("music",), _snapshot(candidate, _item("kept", ("music",))))

    assert [item.id for item in result.items] == ["kept"]
    assert "Url Only" not in [item.title for item in result.items]
