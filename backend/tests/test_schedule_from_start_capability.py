"""Scheduling + from-start capability derivation (issue #98).

The capability seam is the single authority on whether Lumina offers to schedule
an upcoming broadcast and record it from the beginning. Recording capability
never follows viewing, and from-start is offered only alongside a recordable
source, labelled as the best-effort/experimental INTENT.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.media_capabilities import derive_media_capabilities


def test_youtube_upcoming_can_be_scheduled_with_a_provider_start_time() -> None:
    caps = derive_media_capabilities(
        {
            "extractor_key": "Youtube",
            "live_status": "is_upcoming",
            "release_timestamp": int(datetime(2026, 7, 20, 18, 0, 0, tzinfo=UTC).timestamp()),
        }
    )
    assert caps.lifecycle == "upcoming"
    assert caps.can_schedule is True
    assert caps.schedule_reason is None
    assert caps.scheduled_start == datetime(2026, 7, 20, 18, 0, 0)
    assert caps.from_start_available is True
    # Ordinary playback/acquisition stay unavailable until it starts.
    assert caps.can_play is False
    assert caps.can_acquire is False


def test_youtube_upcoming_without_a_start_time_still_schedulable() -> None:
    caps = derive_media_capabilities({"extractor_key": "Youtube", "live_status": "is_upcoming"})
    assert caps.can_schedule is True
    assert caps.scheduled_start is None
    assert caps.from_start_available is True


def test_non_youtube_upcoming_is_not_schedulable() -> None:
    caps = derive_media_capabilities({"extractor_key": "Twitch", "live_status": "is_upcoming"})
    assert caps.lifecycle == "upcoming"
    assert caps.can_schedule is False
    assert caps.schedule_reason == "upcoming_schedule_not_supported"
    assert caps.from_start_available is False


def test_recordable_youtube_live_advertises_from_start() -> None:
    caps = derive_media_capabilities(
        {
            "extractor_key": "Youtube",
            "is_live": True,
            "formats": [{"protocol": "m3u8_native", "manifest_url": "https://cdn.example/live.m3u8", "vcodec": "avc1", "acodec": "mp4a"}],
        }
    )
    assert caps.lifecycle == "live"
    assert caps.can_record is True
    assert caps.from_start_available is True


def test_non_recordable_live_source_never_advertises_from_start() -> None:
    # A live source with no recordable master must not offer from-start (recording
    # capability, and from-start with it, never follows mere viewing).
    caps = derive_media_capabilities({"extractor_key": "Youtube", "is_live": True})
    assert caps.can_record is False
    assert caps.from_start_available is False
