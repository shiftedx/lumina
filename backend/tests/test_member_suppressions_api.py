"""Suppression endpoints: create, list, restore, and cross-surface effect (#90).

These exercise the route wiring end to end — that a POST promptly removes the
candidate from a re-fetched Home and Up Next (immediate replacement), that the
Settings list surfaces safe metadata, that restore makes the candidate eligible
again, and that everything stays scoped to the authenticated member.
"""

from __future__ import annotations

import pytest

import app.main as main
from app.models import MemberRecommendationSuppression, User
from app.services.member_suppressions import MemberSuppressionService
from support import make_user, memory_session_factory, popular_item as _item, popular_snapshot as _snapshot


@pytest.fixture(autouse=True)
def legacy_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """These route tests pin ADR 0007's policy, which the routes serve with personalised recommendations off."""
    from app.services import reco

    monkeypatch.setattr(reco, "enabled", lambda _db: False)


def _session():
    return memory_session_factory()()


def _seed_music_member(db) -> User:
    from app.models import MemberInterest

    member = make_user("member")
    db.add_all([member, MemberInterest(id="i", user_id=member.id, category_key="music")])
    db.commit()
    return member


def test_create_item_suppression_then_home_drops_it_on_refetch(monkeypatch) -> None:
    db = _session()
    member = _seed_music_member(db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main.popular_discovery, "get_snapshot",
        lambda: _snapshot(_item("hidden", ("music",)), _item("shown", ("music",))),
    )

    before = [item.id for item in main.home_recommendations(object(), member, db).items]
    assert "hidden" in before

    payload = main.SuppressRecommendationRequest(
        scope="item", source_id="hidden", source_url="https://www.youtube.com/watch?v=hidden",
        title="Hidden", uploader="Creator",
    )
    created = main.create_suppression(payload, member, db)
    assert created.scope == "item"

    after = [item.id for item in main.home_recommendations(object(), member, db).items]
    assert after == ["shown"]


def test_create_channel_suppression_applies_across_home_and_up_next(monkeypatch) -> None:
    db = _session()
    member = _seed_music_member(db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main.popular_discovery, "get_snapshot",
        lambda: _snapshot(_item("muted", ("music",), uploader="Muted Channel"), _item("kept", ("music",), uploader="Other")),
    )

    main.create_suppression(main.SuppressRecommendationRequest(scope="channel", uploader="Muted Channel"), member, db)

    home = [item.id for item in main.home_recommendations(object(), member, db).items]
    up_next_payload = main.UpNextRequest(
        source_url="https://www.youtube.com/watch?v=now", source_id="now", uploader="Anchor", category_keys=["music"],
    )
    up_next = [item.id for item in main.up_next_recommendations(up_next_payload, object(), member, db).items]

    assert "muted" not in home and "muted" not in up_next
    assert "kept" in up_next


def test_list_suppressions_splits_items_and_channels_with_safe_metadata() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    service = MemberSuppressionService(db)
    service.suppress_item(member, source="youtube", item_id="x", webpage_url=None, title="A Clip", uploader="Creator")
    service.suppress_channel(member, uploader="Muted Channel")
    db.commit()

    listing = main.list_suppressions(member, db)

    assert [entry.title for entry in listing.items] == ["A Clip"]
    assert [entry.channel_name for entry in listing.channels] == ["Muted Channel"]


def test_restore_suppression_makes_the_item_eligible_again(monkeypatch) -> None:
    db = _session()
    member = _seed_music_member(db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: _snapshot(_item("back", ("music",))))
    created = main.create_suppression(
        main.SuppressRecommendationRequest(scope="item", source_id="back", title="Back", uploader="Creator"), member, db
    )
    assert [item.id for item in main.home_recommendations(object(), member, db).items] == []

    main.restore_suppression(created.id, member, db)

    assert [item.id for item in main.home_recommendations(object(), member, db).items] == ["back"]


def test_create_item_suppression_rejects_a_candidate_without_identity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    with pytest.raises(main.HTTPException) as exc:
        main.create_suppression(main.SuppressRecommendationRequest(scope="item"), member, db)
    assert exc.value.status_code == 422


def test_restore_only_touches_the_authenticated_members_suppression() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    other = MemberSuppressionService(db).suppress_channel(second, uploader="Not Yours")
    db.commit()

    main.restore_suppression(other.id, first, db)

    # first cannot delete second's suppression via the endpoint.
    assert db.query(MemberRecommendationSuppression).filter_by(id=other.id).count() == 1


def test_create_suppression_stays_idempotent_when_the_insert_races(monkeypatch) -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    payload = main.SuppressRecommendationRequest(scope="channel", uploader="Race Channel")
    first = main.create_suppression(payload, member, db)

    # A stale pre-read forces the constraint-conflict path inside the write
    # transaction; the endpoint must still return the existing row (200), never
    # surface an IntegrityError as a 500.
    real_existing = MemberSuppressionService._existing
    calls = {"n": 0}

    def flaky_existing(self, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_existing(self, *args, **kwargs)

    monkeypatch.setattr(MemberSuppressionService, "_existing", flaky_existing)

    second = main.create_suppression(payload, member, db)

    assert second.id == first.id
    assert db.query(MemberRecommendationSuppression).count() == 1


def test_legacy_popular_drops_suppressed_items_and_channels_on_refetch(monkeypatch) -> None:
    """Kill switch off: Explore's Popular must not bring back what the member hid (Not interested, Don't recommend)."""
    db = _session()
    member = _seed_music_member(db)
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    snapshot = _snapshot(_item("hidden", ("music",)), _item("blocked", ("music",), uploader="Other"), _item("shown", ("music",), uploader="Third"))
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr(main.popular_discovery, "candidates", lambda: snapshot.items)
    ids = lambda: [item.id for item in main.popular_now(object(), member, db).items]  # noqa: E731
    assert ids() == ["hidden", "blocked", "shown"]
    main.create_suppression(main.SuppressRecommendationRequest(
        scope="item", source_id="hidden", source_url="https://www.youtube.com/watch?v=hidden", title="Hidden", uploader="Creator"), member, db)
    assert ids() == ["blocked", "shown"]
    main.create_suppression(main.SuppressRecommendationRequest(
        scope="channel", uploader="Other", source="youtube", source_id="blocked", source_url="https://www.youtube.com/watch?v=blocked", title="Blocked"), member, db)
    assert ids() == ["shown"]
