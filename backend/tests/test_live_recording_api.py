"""Public API contract for durable live recordings (issue #97).

The routes are exercised directly (like the other live API tests) with a real
shared SQLite engine behind both the request session and the manager's workers,
fake recorder/capturer collaborators (no live YouTube), and a stubbed preview.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app import main as main_module
from app.models import LiveRecording, User
from app.schemas import (
    LiveRecordingCreateRequest,
    MediaSourceCapabilities,
    MediaSourceChatCapabilities,
    PreviewResponse,
)
from app.services.acquisition_batch import RedactedFailure
from app.services.live_recording_manager import LiveChatResult, LiveMediaResult, LiveRecordingManager
from app.services.yt_dlp_service import YtDlpService
from support import file_backed_session_factory


def _engine_factory():
    return file_backed_session_factory("owner-1", "intruder")


class _FakeRecorder:
    def __init__(self, mode="complete"):
        self.mode = mode

    def record(self, ctx):
        if self.mode == "fail":
            return LiveMediaResult(status="failed", failure=RedactedFailure("media_failed", "media failed"))
        if self.mode == "await_stop":
            ctx.wait_for_end(timeout=5)
            if ctx.cancel_requested():
                return LiveMediaResult(status="failed", failure=RedactedFailure("media_cancelled", "cancelled"))
            return LiveMediaResult(status="completed", library_item_id=f"item-{ctx.recording_id}")
        return LiveMediaResult(status="completed", library_item_id=f"item-{ctx.recording_id}")


class _FakeCapturer:
    def __init__(self, mode="complete"):
        self.mode = mode

    def capture(self, ctx):
        if self.mode == "await_stop":
            ctx.wait_for_end(timeout=5)
            if ctx.cancel_requested():
                return LiveChatResult(status="failed", failure=RedactedFailure("chat_cancelled", "cancelled"))
            return LiveChatResult(status="completed", chat_asset_id=f"chat-{ctx.recording_id}")
        return LiveChatResult(status="completed", chat_asset_id=f"chat-{ctx.recording_id}")


def _install(monkeypatch, factory, *, recorder=None, capturer=None, can_record=True, manager_kwargs=None):
    manager = LiveRecordingManager(
        session_factory=factory,
        recorder=recorder or _FakeRecorder(),
        chat_capturer=capturer or _FakeCapturer(),
        **(manager_kwargs or {}),
    )
    monkeypatch.setattr(main_module, "live_recording_manager", manager)
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *a, **k: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        video_id = parse_qs(urlsplit(source_url).query).get("v", ["LIVE1"])[0]
        caps = MediaSourceCapabilities(
            provider="youtube", lifecycle="live", can_play=True, can_acquire=False,
            acquire_reason="live_acquisition_not_supported",
            can_record=can_record, record_reason=None if can_record else "live_record_not_supported",
            chat=MediaSourceChatCapabilities(live="available"),
        )
        return PreviewResponse(
            kind="video", title="Live now", extractor="youtube", webpage_url=source_url,
            capabilities=caps, raw={"id": video_id, "extractor": "youtube"},
        )

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    return manager


def _create(factory, url="https://www.youtube.com/watch?v=LIVE1"):
    with factory() as db:
        user = db.get(User, "owner-1")
        response = main_module.create_live_recording(
            LiveRecordingCreateRequest(source_url=url), object(), user, db
        )
    return response


def _fresh_status(factory, recording_id):
    with factory() as db:
        recording = db.get(LiveRecording, recording_id)
        return (recording.status, recording.media_status, recording.chat_status)


def test_create_records_from_now_and_completes(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory)
    created = _create(factory)
    assert created.status in {"queued", "live", "finalizing", "completed"}
    manager.wait_all(timeout=5)
    assert _fresh_status(factory, created.id) == ("completed", "completed", "completed")
    with factory() as db:
        final = main_module.get_live_recording(created.id, db.get(User, "owner-1"), db)
    assert final.media.library_item_id == f"item-{created.id}"
    assert final.chat.chat_asset_id == f"chat-{created.id}"


def test_create_is_idempotent(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory, recorder=_FakeRecorder("await_stop"), capturer=_FakeCapturer("await_stop"))
    first = _create(factory)
    second = _create(factory)
    assert first.id == second.id
    manager.stop("owner-1", first.id)
    manager.wait_all(timeout=5)


def test_capability_gate_blocks_non_recordable_source(monkeypatch) -> None:
    factory = _engine_factory()
    _install(monkeypatch, factory, can_record=False)
    with pytest.raises(HTTPException) as excinfo:
        _create(factory)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["reason"] == "live_record_not_supported"


def test_stop_route_finalizes(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory, recorder=_FakeRecorder("await_stop"), capturer=_FakeCapturer("await_stop"))
    created = _create(factory)
    _wait_live(factory, created.id)
    with factory() as db:
        stopped = main_module.stop_live_recording(created.id, db.get(User, "owner-1"), db)
    assert stopped.stop_requested is True
    manager.wait_all(timeout=5)
    assert _fresh_status(factory, created.id)[0] == "completed"


def test_cancel_route_is_distinct(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory, recorder=_FakeRecorder("await_stop"), capturer=_FakeCapturer("await_stop"))
    created = _create(factory)
    _wait_live(factory, created.id)
    with factory() as db:
        main_module.cancel_live_recording(created.id, db.get(User, "owner-1"), db)
    manager.wait_all(timeout=5)
    assert _fresh_status(factory, created.id)[0] == "cancelled"


def test_member_isolation_over_the_api(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(monkeypatch, factory, recorder=_FakeRecorder("await_stop"), capturer=_FakeCapturer("await_stop"))
    created = _create(factory)
    with factory() as db:
        intruder = db.get(User, "intruder")
        with pytest.raises(HTTPException) as get_exc:
            main_module.get_live_recording(created.id, intruder, db)
        assert get_exc.value.status_code == 404
        with pytest.raises(HTTPException) as stop_exc:
            main_module.stop_live_recording(created.id, intruder, db)
        assert stop_exc.value.status_code == 404
        # Direct route call: pass the query params explicitly (their declared
        # defaults are FastAPI Query markers outside the request path).
        intruder_page = main_module.list_live_recordings(cursor=None, limit=50, current_user=intruder, db=db)
        assert intruder_page.items == []
        assert intruder_page.next_cursor is None
    manager.stop("owner-1", created.id)
    manager.wait_all(timeout=5)


def test_list_rejects_a_malformed_cursor_with_400() -> None:
    factory = _engine_factory()
    with factory() as db:
        owner = db.get(User, "owner-1")
        with pytest.raises(HTTPException) as excinfo:
            main_module.list_live_recordings(cursor="not-a-cursor", limit=50, current_user=owner, db=db)
    assert excinfo.value.status_code == 400


def test_per_member_limit_returns_429(monkeypatch) -> None:
    factory = _engine_factory()
    manager = _install(
        monkeypatch, factory,
        recorder=_FakeRecorder("await_stop"), capturer=_FakeCapturer("await_stop"),
        manager_kwargs={"max_active_per_user": 1, "max_active_global": 5},
    )
    first = _create(factory, url="https://www.youtube.com/watch?v=LIVE1")
    with pytest.raises(HTTPException) as excinfo:
        _create(factory, url="https://www.youtube.com/watch?v=LIVE2")
    assert excinfo.value.status_code == 429
    manager.stop("owner-1", first.id)
    manager.wait_all(timeout=5)


def _wait_live(factory, recording_id, timeout=5.0):
    import time

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with factory() as db:
            recording = db.get(LiveRecording, recording_id)
            if recording is not None and recording.recording_started_at is not None:
                return
        time.sleep(0.01)
    raise AssertionError("recording never reached the live edge")
