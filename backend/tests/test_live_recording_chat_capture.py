"""Forward-only live chat capture into a durable timed chat asset (issue #97).

The capturer reuses the #96 LiveChatFetcher seam (bootstrap + fetch_page) but,
unlike the transient viewing rail, it publishes a durable timed chat asset — the
same ChatReplayAsset the synchronized chat rail loads — with media offsets based
on the recording start so the completed Library item and its chat stay
synchronized. A fake fetcher stands in so no live YouTube is touched.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import ChatReplayAsset, User
from app.services.live_chat_seam import LiveChatBootstrap, LiveChatPage
from app.services.live_recording_adapters import GuardedLiveChatCapturer
from app.services.live_recording_manager import RecordingContext
from support import file_backed_session_factory


def _factory():
    return file_backed_session_factory("user-a")


def _message(item_id, text):
    return {
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


class _FakeFetcher:
    """Scriptable live-chat fetcher: bootstrap result + a queue of pages."""

    def __init__(self, bootstrap: LiveChatBootstrap, pages: list) -> None:
        self._bootstrap = bootstrap
        self._pages = list(pages)
        self.bootstrap_calls: list[tuple] = []

    def bootstrap(self, source_url, owner_user_id):  # noqa: ANN001
        self.bootstrap_calls.append((source_url, owner_user_id))
        return self._bootstrap

    def fetch_page(self, continuation, *, headers):  # noqa: ANN001
        if not self._pages:
            return LiveChatPage(actions=[], continuation=None, status="ended")
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _ctx(
    factory,
    *,
    offset_base=None,
    stop=None,
    cancel=None,
    either=None,
    clock=None,
    start_intent="live_edge",
    scheduled_start_at=None,
):
    stop = stop or threading.Event()
    cancel = cancel or threading.Event()
    either = either or threading.Event()
    return RecordingContext(
        recording_id="rec-1",
        user_id="user-a",
        source_url="https://youtube.com/watch?v=LIVE1",
        source_identity="youtube:LIVE1",
        format_selection={},
        output_profile={},
        offset_base=offset_base or datetime.now(UTC).replace(tzinfo=None),
        resume=False,
        session_factory=factory,
        _stop=stop,
        _cancel=cancel,
        _either=either,
        start_intent=start_intent,
        scheduled_start_at=scheduled_start_at,
    )


def _asset(factory):
    with factory() as session:
        return session.scalar(select(ChatReplayAsset).where(ChatReplayAsset.user_id == "user-a"))


def test_captures_forward_only_and_publishes_a_durable_asset() -> None:
    factory = _factory()
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge-token", headers={"x": "y"}, status="available"),
        [
            LiveChatPage(actions=[_message("m1", "hello")], continuation="c2", status="active"),
            LiveChatPage(actions=[_message("m2", "world")], continuation=None, status="ended"),
        ],
    )
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0)
    result = capturer.capture(_ctx(factory))
    assert result.status == "completed"
    asset = _asset(factory)
    assert asset is not None
    assert result.chat_asset_id == asset.id
    assert asset.status == "ready"
    assert asset.event_count == 2
    assert [e["text"] for e in asset.events_json] == ["hello", "world"]
    # Forward-only: the continuation used is the live edge from bootstrap, never a
    # from-the-beginning token, and it is never persisted.
    assert fetcher.bootstrap_calls == [("https://youtube.com/watch?v=LIVE1", "user-a")]
    assert "edge-token" not in (asset.events_json and str(asset.events_json))


def test_offsets_are_media_relative_for_synchronization() -> None:
    factory = _factory()
    base = datetime.now(UTC).replace(tzinfo=None)
    # A controllable clock: the first page arrives 5s into the recording, the
    # second 12s in, so the asset offsets track media time from the start.
    ticks = iter([base.replace(tzinfo=UTC).timestamp() + 5, base.replace(tzinfo=UTC).timestamp() + 12])
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge", status="available"),
        [
            LiveChatPage(actions=[_message("m1", "first")], continuation="c2", status="active"),
            LiveChatPage(actions=[_message("m2", "second")], continuation=None, status="ended"),
        ],
    )
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0, clock=lambda: next(ticks))
    result = capturer.capture(_ctx(factory, offset_base=base))
    assert result.status == "completed"
    offsets = [e["offset_ms"] for e in _asset(factory).events_json]
    assert offsets == [5000, 12000]


def test_scheduled_from_start_chat_is_not_covered_from_the_beginning() -> None:
    # A scheduled from-start recording's forward-only chat connects at the
    # current live-chat edge — up to a full poll interval AFTER real go-live — so
    # chat between the broadcast start and connect is genuinely missed. Having a
    # scheduled_start_at is NOT evidence the chat reached back to the beginning, so
    # covered_from_start must be False (an explicit partial-history condition),
    # never an assumed True that would let the acquisition report a false complete.
    factory = _factory()
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge-token", status="available"),
        [
            LiveChatPage(actions=[_message("m1", "mid-stream")], continuation="c2", status="active"),
            LiveChatPage(actions=[_message("m2", "still going")], continuation=None, status="ended"),
        ],
    )
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0)
    result = capturer.capture(
        _ctx(
            factory,
            start_intent="from_start",
            scheduled_start_at=datetime(2026, 7, 20, 18, 0, 0).replace(tzinfo=None),
        )
    )
    assert result.status == "completed"
    assert result.covered_from_start is False


def test_unavailable_chat_reports_unavailable_without_an_asset() -> None:
    factory = _factory()
    fetcher = _FakeFetcher(LiveChatBootstrap(continuation=None, status="unavailable"), [])
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0)
    result = capturer.capture(_ctx(factory))
    assert result.status == "unavailable"
    assert result.chat_asset_id is None
    assert _asset(factory) is None


def test_deliberate_stop_finalizes_captured_chat_as_usable() -> None:
    factory = _factory()
    stop = threading.Event()
    either = threading.Event()
    # Endless active pages; the stop breaks the loop and finalizes what we have.
    pages = [LiveChatPage(actions=[_message(f"m{i}", f"t{i}")], continuation=f"c{i}", status="active") for i in range(100)]
    fetcher = _FakeFetcher(LiveChatBootstrap(continuation="edge", status="available"), pages)
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0)
    ctx = _ctx(factory, stop=stop, either=either)

    def stop_soon():
        # Let a couple of polls happen, then request the deliberate stop.
        import time

        time.sleep(0.05)
        stop.set()
        either.set()

    t = threading.Thread(target=stop_soon)
    t.start()
    result = capturer.capture(ctx)
    t.join()
    assert result.status == "completed"
    asset = _asset(factory)
    assert asset is not None and asset.event_count >= 1


def test_abrupt_cancel_discards_chat_without_publishing() -> None:
    factory = _factory()
    cancel = threading.Event()
    either = threading.Event()
    cancel.set()
    either.set()
    pages = [LiveChatPage(actions=[_message("m1", "t")], continuation="c1", status="active")]
    fetcher = _FakeFetcher(LiveChatBootstrap(continuation="edge", status="available"), pages)
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0)
    result = capturer.capture(_ctx(factory, cancel=cancel, either=either))
    assert result.status == "failed"
    assert _asset(factory) is None


def test_persistent_fetch_failure_with_no_events_reports_failed() -> None:
    factory = _factory()
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge", status="available"),
        [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")],
    )
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0, max_consecutive_failures=3)
    result = capturer.capture(_ctx(factory))
    assert result.status == "failed"
    assert _asset(factory) is None


def test_fetch_failure_after_capturing_keeps_the_partial_asset() -> None:
    factory = _factory()
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge", status="available"),
        [
            LiveChatPage(actions=[_message("m1", "kept")], continuation="c2", status="active"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
        ],
    )
    capturer = GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0, max_consecutive_failures=3)
    result = capturer.capture(_ctx(factory))
    # A usable captured prefix survives a later connection failure: partial asset,
    # reported as completed (usable), not a total chat failure.
    assert result.status == "completed"
    asset = _asset(factory)
    assert asset is not None
    assert asset.status == "partial"
    assert [e["text"] for e in asset.events_json] == ["kept"]


def test_resume_merges_and_never_loses_pre_crash_chat() -> None:
    factory = _factory()
    # First (pre-crash) capture publishes m1, m2.
    first = _FakeFetcher(
        LiveChatBootstrap(continuation="edge", status="available"),
        [
            LiveChatPage(actions=[_message("m1", "one"), _message("m2", "two")], continuation="c2", status="active"),
            RuntimeError("boom"),
            RuntimeError("boom"),
        ],
    )
    GuardedLiveChatCapturer(fetcher=first, poll_interval_seconds=0, max_consecutive_failures=2).capture(_ctx(factory))
    # The resumed capture connects fresh and only sees later messages m3, m4.
    second = _FakeFetcher(
        LiveChatBootstrap(continuation="edge2", status="available"),
        [
            LiveChatPage(actions=[_message("m3", "three")], continuation="c3", status="active"),
            LiveChatPage(actions=[_message("m4", "four")], continuation=None, status="ended"),
        ],
    )
    GuardedLiveChatCapturer(fetcher=second, poll_interval_seconds=0).capture(_ctx(factory))
    asset = _asset(factory)
    # The pre-crash chat is preserved and the resumed chat is appended (union by id).
    assert [e["text"] for e in asset.events_json] == ["one", "two", "three", "four"]


def test_live_captured_asset_takes_precedence_over_a_later_replay_load() -> None:
    from app.services.timed_chat_asset import TimedChatAssetService

    factory = _factory()
    fetcher = _FakeFetcher(
        LiveChatBootstrap(continuation="edge", status="available"),
        [LiveChatPage(actions=[_message("m1", "live-captured")], continuation=None, status="ended")],
    )
    GuardedLiveChatCapturer(fetcher=fetcher, poll_interval_seconds=0).capture(_ctx(factory))
    # A later, non-refresh replay-chat load for the SAME source identity returns
    # the live-captured asset unchanged rather than rebuilding over it.
    with factory() as session:
        user = session.get(User, "user-a")
        ticket = TimedChatAssetService(session).begin_build(
            "youtube:LIVE1", "https://youtube.com/watch?v=LIVE1", user, refresh=False
        )
        assert ticket.build_id is None  # no rebuild launched
        assert [e.text for e in ticket.response.events] == ["live-captured"]
