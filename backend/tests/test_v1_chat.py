"""Source chat is public, read-only and inert; auth-only live chat (Kick) is explained, never invited."""

from __future__ import annotations

import json

from app.services.media_capabilities import derive_media_capabilities
from app.services.timed_chat import TimedChatBudget, normalize_youtube_replay_chat


def _live(extractor: str) -> dict:
    return {
        "id": "chan", "extractor": extractor, "is_live": True, "live_status": "is_live", "webpage_url": "https://example.test/live",
        "formats": [{"format_id": "hls", "protocol": "m3u8_native", "url": "https://cdn.example/360.m3u8",
                     "manifest_url": "https://cdn.example/master.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
    }


def test_live_chat_availability_by_provider() -> None:
    # Kick live chat still needs a signed-in identity: explained, never invited.
    chat = derive_media_capabilities(_live("kick:live")).chat
    assert (chat.live, chat.live_reason) == ("unavailable", "authentication_required")
    # Twitch reads anonymously over IRC (2.2.0); YouTube when the source exposes a live_chat track.
    assert derive_media_capabilities(_live("twitch:stream")).chat.live == "available"
    youtube = {**_live("youtube"), "subtitles": {"live_chat": [{"ext": "json", "url": "https://www.youtube.com/watch?v=x"}]}}
    assert derive_media_capabilities(youtube).chat.live == "available"
    assert derive_media_capabilities(_live("youtube")).chat.live == "unavailable"
    assert derive_media_capabilities(_live("youtube")).chat.live_reason is None


def _message(item_id: str, author: str, text: str, offset_ms: int = 1000) -> str:
    renderer = {"id": item_id, "authorName": {"simpleText": author}, "message": {"runs": [{"text": text}]}}
    return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(offset_ms), "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": renderer}}}]}})


def test_chat_payload_xss_safe() -> None:
    hostile = "<img src=x onerror=alert(1)>\x00\x1b[31m‮evil\nline"
    result = normalize_youtube_replay_chat(
        [_message("m1", "Mallory‮\x07", hostile), _message("m2", "Bob", "x" * 5000)],
        budget=TimedChatBudget(max_text_chars=500),
    )
    first, second = result.events
    # Markup stays literal text (React renders it inert); controls and bidi overrides are gone.
    assert first.text == "<img src=x onerror=alert(1)>[31mevilline"
    assert first.author is not None and first.author.name == "Mallory"
    assert len(second.text) == 500
