"""Bounded normalization of yt-dlp replay-chat actions into timed chat events.

Fixtures mirror the *current* yt-dlp `.live_chat.json` replay shape: one JSON
object per line, each an ``action`` whose ``replayChatItemAction`` carries a
``videoOffsetTimeMsec`` (media offset in milliseconds) and an inner ``actions``
list of innertube renderers. Excerpts are deliberately compact — no giant
blobs — and no continuation tokens, request headers, or cookies are present or
retained.
"""

from __future__ import annotations

import json

from app.services.timed_chat import (
    TimedChatBudget,
    normalize_youtube_replay_chat,
)


def _add_text(offset_ms: int, item_id: str, author: str, text: str, *, badges=None, channel="UC_author") -> dict:
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "id": item_id,
                                "timestampUsec": str((offset_ms + 1_600_000_000_000) * 1000),
                                "authorExternalChannelId": channel,
                                "authorName": {"simpleText": author},
                                "authorPhoto": {"thumbnails": [{"url": "https://yt3.example/photo.jpg"}]},
                                "authorBadges": badges or [],
                                "message": {"runs": [{"text": text}]},
                            }
                        }
                    }
                }
            ],
        }
    }


def _paid(offset_ms: int, item_id: str, author: str, text: str, amount: str) -> dict:
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatPaidMessageRenderer": {
                                "id": item_id,
                                "authorExternalChannelId": "UC_supporter",
                                "authorName": {"simpleText": author},
                                "purchaseAmountText": {"simpleText": amount},
                                "message": {"runs": [{"text": text}]},
                            }
                        }
                    }
                }
            ],
        }
    }


def _membership(offset_ms: int, item_id: str, author: str) -> dict:
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatMembershipItemRenderer": {
                                "id": item_id,
                                "authorExternalChannelId": "UC_member",
                                "authorName": {"simpleText": author},
                                "headerSubtext": {"runs": [{"text": "Member for 6 months"}]},
                            }
                        }
                    }
                }
            ],
        }
    }


def _delete(offset_ms: int, target_id: str) -> dict:
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "markChatItemAsDeletedAction": {
                        "deletedStateMessage": {"runs": [{"text": "Message deleted by author"}]},
                        "targetItemId": target_id,
                    }
                }
            ],
        }
    }


def _delete_by_author(offset_ms: int, channel_id: str) -> dict:
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "markChatItemsByAuthorAsDeletedAction": {
                        "deletedStateMessage": {"runs": [{"text": "Removed by moderator"}]},
                        "externalChannelId": channel_id,
                    }
                }
            ],
        }
    }


def _lines(*actions: dict) -> list[bytes]:
    return [json.dumps(action, ensure_ascii=False).encode("utf-8") for action in actions]


def test_normalizes_ordered_events_with_stable_identity_and_media_offsets() -> None:
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(1200, "msg-a", "Ada", "first!"),
            _add_text(3400, "msg-b", "Grace", "hello everyone"),
            _paid(5000, "msg-c", "Linus", "great stream", "$5.00"),
        ),
        budget=TimedChatBudget(),
    )
    assert result.outcome == "ready"
    assert [event.id for event in result.events] == ["msg-a", "msg-b", "msg-c"]
    assert [event.offset_ms for event in result.events] == [1200, 3400, 5000]
    # Offsets are monotonic non-decreasing so the rail can binary-search playback.
    assert result.events == tuple(sorted(result.events, key=lambda event: event.offset_ms))
    assert result.events[0].kind == "message"
    assert result.events[0].author is not None
    assert result.events[0].author.name == "Ada"
    assert result.events[2].kind == "paid_message"
    assert result.events[2].amount == "$5.00"


def test_paid_membership_and_badges_are_preserved_provider_neutrally() -> None:
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(
                100,
                "mod-msg",
                "Moderator Mo",
                "keep it civil",
                badges=[{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "MODERATOR"}, "tooltip": "Moderator"}}],
            ),
            _add_text(
                200,
                "owner-msg",
                "Channel Owner",
                "welcome",
                badges=[{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "OWNER"}, "tooltip": "Owner"}}],
            ),
            _add_text(
                300,
                "member-msg",
                "Long Timer",
                "hi",
                badges=[{"liveChatAuthorBadgeRenderer": {"customThumbnail": {"thumbnails": []}, "tooltip": "Member (2 years)"}}],
            ),
            _membership(400, "join-msg", "New Member"),
        ),
        budget=TimedChatBudget(),
    )
    by_id = {event.id: event for event in result.events}
    assert by_id["mod-msg"].author.badges == ("moderator",)
    assert by_id["owner-msg"].author.badges == ("owner",)
    assert by_id["member-msg"].author.badges == ("member",)
    assert by_id["join-msg"].kind == "membership"


def test_deleted_and_author_removed_messages_are_represented_not_resurrected() -> None:
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(1000, "keep", "Ada", "this stays"),
            _add_text(2000, "gone", "Troll", "buy my thing", channel="UC_spammer"),
            _add_text(2500, "gone2", "Troll", "again", channel="UC_spammer"),
            _delete(3000, "gone"),
            _delete_by_author(3500, "UC_spammer"),
        ),
        budget=TimedChatBudget(),
    )
    by_id = {event.id: event for event in result.events}
    assert by_id["keep"].moderation == "visible"
    assert by_id["keep"].text == "this stays"
    # A targeted deletion marks the message deleted and never leaks its text.
    assert by_id["gone"].moderation == "deleted"
    assert by_id["gone"].text == ""
    # Author-level removal marks every message from that channel removed.
    assert by_id["gone2"].moderation == "author_removed"
    assert by_id["gone2"].text == ""


def test_author_removal_also_hides_later_messages_from_the_banned_channel() -> None:
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(1000, "before", "Spammer", "x", channel="UC_ban"),
            _delete_by_author(1500, "UC_ban"),
            _add_text(2000, "after", "Spammer", "evading", channel="UC_ban"),
        ),
        budget=TimedChatBudget(),
    )
    by_id = {event.id: event for event in result.events}
    assert by_id["before"].moderation == "author_removed"
    assert by_id["after"].moderation == "author_removed"
    assert by_id["after"].text == ""


def test_event_count_budget_truncates_and_reports_partial() -> None:
    lines = _lines(*[_add_text(index * 10, f"m{index}", "A", "hi") for index in range(50)])
    result = normalize_youtube_replay_chat(lines, budget=TimedChatBudget(max_events=10))
    assert len(result.events) == 10
    assert result.truncated is True
    assert result.outcome == "partial"


def test_moderation_after_the_event_budget_still_applies_to_admitted_messages() -> None:
    # The high-volume case that matters most: the add budget fills, but a
    # deletion/author-removal arriving AFTER the cutoff must still moderate an
    # already-admitted message rather than leaving it visible.
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(1000, "keep", "Ada", "this stays"),
            _add_text(2000, "target", "Troll", "delete me later", channel="UC_spammer"),
            _add_text(3000, "banned", "Troll", "ban me later", channel="UC_spammer"),
            _add_text(4000, "overflow-1", "Loud", "past the cutoff"),  # dropped by budget
            _add_text(5000, "overflow-2", "Loud", "also past the cutoff"),  # dropped by budget
            _delete(6000, "target"),
            _delete_by_author(7000, "UC_spammer"),
        ),
        budget=TimedChatBudget(max_events=3),
    )
    by_id = {event.id: event for event in result.events}
    assert len(result.events) == 3  # add budget held
    assert result.truncated is True
    assert result.outcome == "partial"
    assert "overflow-1" not in by_id and "overflow-2" not in by_id
    # Post-cutoff moderation was still applied to the pre-cutoff messages.
    assert by_id["target"].moderation == "deleted"
    assert by_id["target"].text == ""
    assert by_id["banned"].moderation == "author_removed"
    assert by_id["keep"].moderation == "visible"


def test_byte_budget_stops_processing_and_reports_oversized() -> None:
    lines = _lines(*[_add_text(index * 10, f"m{index}", "A", "x" * 200) for index in range(200)])
    total_bytes = sum(len(line) for line in lines)
    result = normalize_youtube_replay_chat(lines, budget=TimedChatBudget(max_bytes=total_bytes // 4, max_events=10_000))
    assert result.truncated is True
    assert result.outcome == "oversized"
    assert 0 < len(result.events) < 200


def test_malformed_lines_are_skipped_without_breaking_the_asset() -> None:
    lines = [
        b"not json at all",
        b"{\"replayChatItemAction\": {}}",  # missing offset + actions
        json.dumps(_add_text(1000, "ok", "Ada", "survived")).encode("utf-8"),
        b"\x00\x01\x02 binary garbage",
        json.dumps({"unknownEnvelope": {"foo": "bar"}}).encode("utf-8"),
    ]
    result = normalize_youtube_replay_chat(lines, budget=TimedChatBudget())
    assert [event.id for event in result.events] == ["ok"]
    assert result.dropped_malformed >= 2
    assert result.outcome == "ready"


def test_all_malformed_input_reports_malformed_not_empty() -> None:
    result = normalize_youtube_replay_chat([b"garbage", b"{oops"], budget=TimedChatBudget())
    assert result.events == ()
    assert result.outcome == "malformed"


def test_no_actions_reports_empty() -> None:
    result = normalize_youtube_replay_chat([], budget=TimedChatBudget())
    assert result.events == ()
    assert result.outcome == "empty"


def test_negative_or_nonnumeric_offsets_leave_events_unsynced_but_present() -> None:
    weird = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "-5",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "id": "no-offset",
                                "authorName": {"simpleText": "Ada"},
                                "message": {"runs": [{"text": "hmm"}]},
                            }
                        }
                    }
                }
            ],
        }
    }
    result = normalize_youtube_replay_chat(_lines(weird), budget=TimedChatBudget())
    assert len(result.events) == 1
    assert result.events[0].offset_ms is None


def test_runs_with_emoji_flatten_to_shortcodes_and_text_is_bounded() -> None:
    action = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "10",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "id": "emoji",
                                "authorName": {"simpleText": "Ada"},
                                "message": {
                                    "runs": [
                                        {"text": "love it "},
                                        {"emoji": {"shortcuts": [":heart:"], "isCustomEmoji": False}},
                                        {"text": " " + "y" * 5000},
                                    ]
                                },
                            }
                        }
                    }
                }
            ],
        }
    }
    result = normalize_youtube_replay_chat(_lines(action), budget=TimedChatBudget(max_text_chars=64))
    event = result.events[0]
    assert event.text.startswith("love it :heart:")
    assert len(event.text) <= 64


def test_duplicate_ids_are_deduplicated_keeping_first_occurrence() -> None:
    result = normalize_youtube_replay_chat(
        _lines(
            _add_text(1000, "dup", "Ada", "first"),
            _add_text(1000, "dup", "Ada", "second (continuation overlap)"),
        ),
        budget=TimedChatBudget(),
    )
    assert len(result.events) == 1
    assert result.events[0].text == "first"


def test_normalization_never_retains_continuations_headers_or_photo_urls() -> None:
    action = _add_text(1000, "msg", "Ada", "hi")
    action["replayChatItemAction"]["continuations"] = [{"liveChatReplayContinuationData": {"continuation": "SECRET_TOKEN"}}]
    action["clickTrackingParams"] = "TRACKING"
    result = normalize_youtube_replay_chat(_lines(action), budget=TimedChatBudget())
    blob = repr(result)
    assert "SECRET_TOKEN" not in blob
    assert "TRACKING" not in blob
    assert "yt3.example" not in blob
