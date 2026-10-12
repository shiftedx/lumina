"""Durable, member-scoped timed chat asset builds: resume/replace + isolation."""

from __future__ import annotations

import json
from datetime import timedelta

from app.models import ChatReplayAsset, User, utcnow
from app.services.timed_chat import TimedChatBudget, normalize_youtube_replay_chat
from app.services.timed_chat_asset import TimedChatAssetService
from app.services.yt_dlp_service import ReplayChatFetch
from support import make_user, memory_session_factory


def _session_factory():
    factory = memory_session_factory()
    with factory.begin() as session:
        session.add_all([make_user("user-a", username="alice", display_name="Alice"), make_user("user-b", username="bob", display_name="Bob")])
    return factory


def _user(session, user_id):
    return session.get(User, user_id)


def _add_line(offset, item_id, text):
    return json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(offset),
                "actions": [
                    {
                        "addChatItemAction": {
                            "item": {
                                "liveChatTextMessageRenderer": {
                                    "id": item_id,
                                    "authorName": {"simpleText": "Ada"},
                                    "message": {"runs": [{"text": text}]},
                                }
                            }
                        }
                    }
                ],
            }
        }
    ).encode("utf-8")


def _build(service, session, user_id, identity, url, fetch, *, refresh=False):
    """Drive the two-phase build the way the endpoint does, in one process."""

    user = _user(session, user_id)
    ticket = service.begin_build(identity, url, user, refresh=refresh)
    session.commit()
    if ticket.build_id is None:
        return ticket.response
    result = normalize_youtube_replay_chat(fetch.lines, budget=service.budget)
    asset = service.store_result(ticket.canonical, url, user, ticket.build_id, fetch, result)
    response = service.serialize(asset)
    session.commit()
    return response


def test_build_persists_terminal_ready_state_with_bounded_events() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        fetch = ReplayChatFetch(status="available", lines=[_add_line(1000, "m1", "hi"), _add_line(2000, "m2", "yo")])
        response = _build(service, session, "user-a", "youtube:vid", "https://youtu.be/vid", fetch)
        assert response.status == "ready"
        assert response.event_count == 2
        assert [event.offset_ms for event in response.events] == [1000, 2000]

        rows = session.query(ChatReplayAsset).all()
        assert len(rows) == 1
        assert rows[0].user_id == "user-a"
        assert rows[0].finished_at is not None


def test_second_load_returns_existing_terminal_asset_without_rebuilding() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        fetch = ReplayChatFetch(status="available", lines=[_add_line(1000, "m1", "hi")])
        _build(service, session, "user-a", "youtube:vid", "https://youtu.be/vid", fetch)

        user = _user(session, "user-a")
        ticket = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        assert ticket.build_id is None  # nothing to rebuild
        assert ticket.response.status == "ready"
        assert ticket.response.event_count == 1


def test_refresh_replaces_the_asset_in_place() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        _build(
            service, session, "user-a", "youtube:vid", "https://youtu.be/vid",
            ReplayChatFetch(status="available", lines=[_add_line(1000, "m1", "old")]),
        )
        response = _build(
            service, session, "user-a", "youtube:vid", "https://youtu.be/vid",
            ReplayChatFetch(status="available", lines=[_add_line(1000, "m1", "new"), _add_line(2000, "m2", "extra")]),
            refresh=True,
        )
        assert response.event_count == 2
        assert session.query(ChatReplayAsset).count() == 1


def test_interrupted_building_row_is_replaced_on_next_load() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        user = _user(session, "user-a")
        # Simulate a crashed build: a stale building row with no finish.
        ticket = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        session.commit()
        stale = session.query(ChatReplayAsset).one()
        stale.started_at = utcnow() - timedelta(hours=1)
        session.commit()

        # A fresh load treats the stale build as replaceable and claims it.
        reclaim = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        assert reclaim.build_id is not None
        assert reclaim.build_id != ticket.build_id


def test_in_flight_build_is_not_duplicated() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        user = _user(session, "user-a")
        service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        session.commit()
        # A second load arriving while the first is still fresh gets the loading
        # placeholder rather than launching a duplicate build.
        second = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        assert second.build_id is None
        assert second.response.status == "building"


def test_stale_finalize_is_discarded_when_a_newer_build_supersedes_it() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        user = _user(session, "user-a")
        first = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=False)
        session.commit()
        # A refresh supersedes the first build with a new build_id.
        second = service.begin_build("youtube:vid", "https://youtu.be/vid", user, refresh=True)
        session.commit()
        assert second.build_id is not None and second.build_id != first.build_id

        # The stale first build finalizes late; its result must be discarded.
        stale_result = normalize_youtube_replay_chat(
            [_add_line(1, "stale", "stale")], budget=service.budget
        )
        asset = service.store_result(
            first.canonical, "https://youtu.be/vid", user, first.build_id,
            ReplayChatFetch(status="available", lines=[]), stale_result,
        )
        assert asset.build_id == second.build_id  # unchanged by the stale writer
        assert asset.status == "building"


def test_unavailable_and_failed_fetches_map_to_terminal_states() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        unavailable = _build(
            service, session, "user-a", "youtube:novid", "https://youtu.be/novid",
            ReplayChatFetch(status="unavailable"),
        )
        assert unavailable.status == "unavailable"
        assert unavailable.event_count == 0

        failed = _build(
            service, session, "user-a", "youtube:err", "https://youtu.be/err",
            ReplayChatFetch(status="failed"),
        )
        assert failed.status == "failed"


def test_begin_build_rejects_a_source_url_that_does_not_match_the_identity() -> None:
    import pytest

    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        user = _user(session, "user-a")
        # Identity says video A but the URL points at video B.
        with pytest.raises(ValueError, match="does not match"):
            service.begin_build("youtube:AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB", user)
        # A matching youtube URL (with tracking params) is accepted.
        ticket = service.begin_build(
            "youtube:AAAAAAAAAAA", "https://www.youtube.com/watch?v=AAAAAAAAAAA&si=share", user
        )
        assert ticket.build_id is not None


def test_begin_build_accepts_non_canonical_youtube_url_forms() -> None:
    # /live/, /embed/, and /shorts/ are exactly the completed-live URLs this
    # feature loads chat for. The client keys them youtube:<id> via its
    # extractor-id fallback, but the URL alone yields url:<...>; a cross-scheme
    # derivation must stay permissive rather than false-reject a real load.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        user = _user(session, "user-a")
        cases = {
            "youtube:LIVEID00001": "https://www.youtube.com/live/LIVEID00001",
            "youtube:EMBEDID0002": "https://www.youtube.com/embed/EMBEDID0002",
            "youtube:SHORTID0003": "https://www.youtube.com/shorts/SHORTID0003",
        }
        for identity, url in cases.items():
            ticket = service.begin_build(identity, url, user)
            assert ticket.build_id is not None, url


def test_assets_are_member_scoped_and_never_cross_members() -> None:
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session)
        _build(
            service, session, "user-a", "youtube:vid", "https://youtu.be/vid",
            ReplayChatFetch(status="available", lines=[_add_line(1000, "m1", "alice-only")]),
        )
        # Bob has no asset for the same source identity.
        assert service.get("youtube:vid", _user(session, "user-b")) is None
        # Alice still sees hers.
        assert service.get("youtube:vid", _user(session, "user-a")).event_count == 1


# --- forward-only: no Twitch VOD replay-chat backfill (issue #100) ----------

def test_twitch_identity_never_claims_a_replay_build() -> None:
    # The FORWARD-ONLY hard invariant: a Twitch source is never backfilled from a
    # provider replay track (yt-dlp can fetch Twitch VOD rechat). begin_build must
    # NEVER claim a build for a Twitch identity, so no historical import can run.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session, budget=TimedChatBudget(max_events=50))
        ticket = service.begin_build(
            "twitch:12345", "https://twitch.tv/coolstreamer", _user(session, "user-a"), refresh=False
        )
        # No build claimed, and the honest state is "unavailable" — there is no
        # forward-only captured asset for this member, and history is never fetched.
        assert ticket.build_id is None
        assert ticket.response.status == "unavailable"
        assert ticket.response.events == []


def test_twitch_identity_refresh_still_never_backfills() -> None:
    # Even an explicit refresh cannot force a Twitch backfill build.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session, budget=TimedChatBudget(max_events=50))
        ticket = service.begin_build(
            "twitch:12345", "https://twitch.tv/coolstreamer", _user(session, "user-a"), refresh=True
        )
        assert ticket.build_id is None


def test_twitch_captured_asset_is_served_without_any_rebuild() -> None:
    # A durably-captured forward-only Twitch chat asset (from a live recording) is
    # returned as-is by a later load for the same identity — never rebuilt, never
    # supplemented by a provider import.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session, budget=TimedChatBudget(max_events=50))
        canonical = "twitch:12345"
        session.add(
            ChatReplayAsset(
                id="asset-1",
                user_id="user-a",
                source_identity=canonical,
                source_identity_key=service._key(canonical),
                source_url="https://twitch.tv/coolstreamer",
                status="ready",
                build_id="b1",
                event_count=1,
                events_json=[{"id": "m1", "offset_ms": 1000, "kind": "message", "text": "captured", "author": None, "moderation": "visible", "amount": None}],
                created_at=utcnow(),
            )
        )
        session.commit()
        ticket = service.begin_build(
            canonical, "https://twitch.tv/coolstreamer", _user(session, "user-a"), refresh=False
        )
        assert ticket.build_id is None
        assert ticket.response.status == "ready"
        assert [e.text for e in ticket.response.events] == ["captured"]


def test_youtube_identity_with_a_twitch_url_never_claims_a_replay_build() -> None:
    # Booby-trap for the crafted CROSS-HOST request. A caller keys a build-eligible
    # youtube:<id> PATH identity but points source_url at a Twitch VOD. The gate must
    # fail closed on the URL HOST (issue #100) — not the caller-controlled identity
    # prefix — because _identity_from_source_url derives url:<...> for a Twitch URL,
    # so _require_matching_source stays permissive on the prefix mismatch. Without
    # the host closure this claims a build and reaches the yt-dlp replay download.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session, budget=TimedChatBudget(max_events=50))
        ticket = service.begin_build(
            "youtube:FAKEVIDEO01", "https://www.twitch.tv/videos/123456789", _user(session, "user-a"), refresh=False
        )
        # No build claimed; honest "unavailable" (no captured asset for this member),
        # and no historical Twitch replay import can run.
        assert ticket.build_id is None
        assert ticket.response.status == "unavailable"
        assert ticket.response.events == []


def test_youtube_identity_with_a_twitch_url_refresh_still_never_backfills() -> None:
    # Even an explicit refresh cannot force the crafted cross-host backfill build.
    factory = _session_factory()
    with factory() as session:
        service = TimedChatAssetService(session, budget=TimedChatBudget(max_events=50))
        ticket = service.begin_build(
            "youtube:FAKEVIDEO01", "https://www.twitch.tv/videos/123456789", _user(session, "user-a"), refresh=True
        )
        assert ticket.build_id is None
