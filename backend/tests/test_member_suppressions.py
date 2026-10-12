"""Durable, idempotent, member-isolated Discovery suppressions for issue #90.

These cover the suppression store and its separation invariants: item
suppression is one stable Media-source identity, channel suppression is one
stable source-channel identity, both member-scoped, and neither touches a
Library item, playback history, or a follow (channel Source automation).
"""

from __future__ import annotations

import pytest

from app.models import (
    LibraryItem,
    MemberRecommendationSuppression,
    PlaybackProgress,
    RemotePlaybackProgress,
    SourceAutomation,
)
from app.services.member_recommendations import _channel_key, _source_key
from app.services.member_suppressions import MemberSuppressionService
from support import make_user, memory_session_factory


def _session():
    return memory_session_factory()()


# --- Item + channel suppression create durable, correctly-keyed records ------


def test_suppress_item_stores_the_shared_stable_source_identity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    record = MemberSuppressionService(db).suppress_item(
        member, source="youtube", item_id="abc123", webpage_url=None, title="A Video", uploader="Creator"
    )
    db.commit()

    assert record.scope == "item"
    # The durable key is the exact identity the policy derives everywhere else.
    assert record.target_key == _source_key("youtube", "abc123", None, "A Video", "Creator")
    assert record.title == "A Video"
    stored = db.query(MemberRecommendationSuppression).all()
    assert len(stored) == 1 and stored[0].user_id == member.id


def test_suppress_channel_stores_the_shared_channel_identity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()

    record = MemberSuppressionService(db).suppress_channel(member, uploader="Cooking Channel")
    db.commit()

    assert record.scope == "channel"
    # Channel identity is the casefolded display name #88's ranking seam matches.
    assert record.target_key == _channel_key("Cooking Channel") == "cooking channel"
    assert record.channel_name == "Cooking Channel"


# --- Idempotency (re-checked inside the write) -------------------------------


def test_suppressions_are_idempotent_per_member_scope_and_target() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    service = MemberSuppressionService(db)

    first = service.suppress_item(member, source="youtube", item_id="dup", webpage_url=None, title="Dup", uploader="C")
    db.commit()
    second = service.suppress_item(member, source="youtube", item_id="dup", webpage_url=None, title="Dup", uploader="C")
    db.commit()

    assert first.id == second.id
    assert db.query(MemberRecommendationSuppression).count() == 1


def test_item_and_channel_suppressions_of_one_member_are_separate_rows() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    service = MemberSuppressionService(db)

    service.suppress_item(member, source="youtube", item_id="x", webpage_url=None, title="X", uploader="Chan")
    service.suppress_channel(member, uploader="Chan")
    db.commit()

    assert db.query(MemberRecommendationSuppression).count() == 2


# --- Member isolation --------------------------------------------------------


def test_suppressions_are_isolated_per_household_member() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    service = MemberSuppressionService(db)

    service.suppress_item(first, source="youtube", item_id="only-first", webpage_url=None, title="F", uploader="C")
    service.suppress_channel(first, uploader="First Only")
    db.commit()

    assert service.active_keys(second).item_keys == frozenset()
    assert service.active_keys(second).channel_keys == frozenset()
    assert service.list_for(second) == []
    # And first still sees exactly their own two suppressions.
    assert len(service.list_for(first)) == 2


# --- Separation: item suppression leaves Library + history untouched ---------


def test_item_suppression_does_not_remove_library_or_erase_playback_history() -> None:
    db = _session()
    member = make_user("member")
    saved = LibraryItem(
        id="lib-1", user_id=member.id, visibility="private", extractor="youtube",
        remote_id="keep", title="Keep", uploader="Creator", status="available",
    )
    progress = PlaybackProgress(
        id="pp-1", user_id=member.id, item_id="lib-1", position_seconds=42.0, duration_seconds=100.0, completed=False,
    )
    remote = RemotePlaybackProgress(
        id="rpp-1", user_id=member.id, source_identity="youtube:keep", source_identity_key="key-keep",
        extractor="youtube", remote_id="keep", source_url="https://www.youtube.com/watch?v=keep",
        uploader="Creator", position_seconds=17.0, duration_seconds=100.0, completed=False, cleared=False,
    )
    db.add_all([member, saved, progress, remote])
    db.commit()

    MemberSuppressionService(db).suppress_item(
        member, source="youtube", item_id="keep", webpage_url=None, title="Keep", uploader="Creator"
    )
    db.commit()

    assert db.query(LibraryItem).filter_by(id="lib-1").one().status == "available"
    # Neither local nor remote playback history is touched by item suppression.
    assert db.query(PlaybackProgress).filter_by(id="pp-1").one().position_seconds == 42.0
    assert db.query(RemotePlaybackProgress).filter_by(id="rpp-1").one().position_seconds == 17.0


# --- Separation: channel suppression never touches a follow ------------------


def test_channel_suppression_does_not_pause_delete_or_modify_a_follow() -> None:
    db = _session()
    member = make_user("member")
    follow = SourceAutomation(
        id="follow-1", user_id=member.id, label="Cooking Channel",
        source_url="https://www.youtube.com/@cooking", source_type="channel",
        cron_expression="*/30 * * * *", active=True, auto_download=False,
    )
    db.add_all([member, follow])
    db.commit()

    MemberSuppressionService(db).suppress_channel(member, uploader="Cooking Channel")
    db.commit()

    kept = db.query(SourceAutomation).filter_by(id="follow-1").one()
    assert kept.active is True and kept.source_type == "channel"
    assert kept.source_url == "https://www.youtube.com/@cooking"
    # Still exactly one channel automation — suppression created no parallel row.
    assert db.query(SourceAutomation).count() == 1


# --- Restore -----------------------------------------------------------------


def test_restore_removes_only_the_targeted_member_owned_suppression() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.commit()
    service = MemberSuppressionService(db)

    keep = service.suppress_channel(second, uploader="Other Member Channel")
    remove = service.suppress_item(first, source="youtube", item_id="gone", webpage_url=None, title="Gone", uploader="C")
    db.commit()

    # A member cannot restore another member's suppression.
    assert service.restore(first, keep.id) is False
    assert service.restore(first, remove.id) is True
    db.commit()

    assert service.active_keys(first).item_keys == frozenset()
    assert db.query(MemberRecommendationSuppression).filter_by(id=keep.id).count() == 1


# --- active_keys is what the policy consumes ---------------------------------


def test_active_keys_expose_item_and_channel_identities_for_the_policy() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    service = MemberSuppressionService(db)

    service.suppress_item(member, source="youtube", item_id="hidden", webpage_url=None, title="Hidden", uploader="C")
    service.suppress_channel(member, uploader="Muted Channel")
    db.commit()

    keys = service.active_keys(member)
    assert _source_key("youtube", "hidden", None, "Hidden", "C") in keys.item_keys
    assert "muted channel" in keys.channel_keys


# --- Validation: no degenerate or raw-text-only suppressions -----------------


def test_suppress_item_rejects_a_candidate_with_no_usable_identity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    with pytest.raises(ValueError):
        MemberSuppressionService(db).suppress_item(
            member, source="youtube", item_id=None, webpage_url=None, title=None, uploader=None
        )


def test_suppress_channel_rejects_an_empty_channel_identity() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    with pytest.raises(ValueError):
        MemberSuppressionService(db).suppress_channel(member, uploader="   ")


# --- Idempotent even when a concurrent insert wins the unique constraint ------


def test_upsert_returns_the_winner_when_a_concurrent_insert_wins_the_constraint(monkeypatch) -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.commit()
    service = MemberSuppressionService(db)
    first = service.suppress_channel(member, uploader="Race Channel")
    db.commit()

    # Force the write path the writer-admission lock normally serializes away:
    # a stale pre-read that misses the committed row, so the insert reaches the
    # per-member unique constraint. The savepoint must contain the IntegrityError
    # and the post-conflict re-read must return the winner idempotently.
    real_existing = service._existing
    calls = {"n": 0}

    def flaky_existing(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_existing(*args, **kwargs)

    monkeypatch.setattr(service, "_existing", flaky_existing)

    second = service.suppress_channel(member, uploader="Race Channel")
    db.commit()

    assert second.id == first.id
    assert db.query(MemberRecommendationSuppression).count() == 1
