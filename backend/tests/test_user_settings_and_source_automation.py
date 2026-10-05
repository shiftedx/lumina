from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

import app.persistence as persistence

from app.models import AutomationDecision, AutomationRun, LibraryItem, User, UserSettings
from app.schemas import (
    AutomationRuleSet,
    FormatResolutionState,
    FormatSelection,
    PreviewResponse,
    RemotePlaybackCacheSettings,
    SourceAutomationCreateRequest,
    UserSettingsUpdateRequest,
)
from app.security import hash_password
from app.services.source_automation import SourceAutomationService
from app.services.user_settings import UserSettingsService
from app.services.webhooks import WebhookService
from app.services.yt_dlp_service import YtDlpService
from support import FakeJobs, memory_session_factory


def _runs(session, automation_id: str) -> list[AutomationRun]:  # noqa: ANN001
    return session.query(AutomationRun).filter_by(automation_id=automation_id).order_by(AutomationRun.started_at.desc()).all()


def make_session():
    return memory_session_factory()()


def make_user(user_id: str = "user-1", username: str = "tester") -> User:
    return User(id=user_id, username=username, display_name=username.title(), password_hash=hash_password("secret"), role="viewer", is_active=True)


class CapturingEvents:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


def fake_playlist_preview(source_url: str) -> PreviewResponse:
    return PreviewResponse(
        kind="playlist",
        title="Test playlist",
        extractor="youtube",
        extractor_key="Youtube",
        webpage_url=source_url,
        availability="public",
        entries=[
            {
                "id": "vid-1",
                "title": "Keep this tutorial",
                "duration": 240,
                "thumbnail": "https://example.com/1.jpg",
                "webpage_url": "https://www.youtube.com/watch?v=vid-1",
                "uploader": "Creator",
                "availability": "public",
            },
            {
                "id": "vid-2",
                "title": "Skip this short",
                "duration": 30,
                "thumbnail": "https://example.com/2.jpg",
                "webpage_url": "https://www.youtube.com/watch?v=vid-2",
                "uploader": "Creator",
                "availability": "public",
            },
            {
                "id": "vid-3",
                "title": "Keep duplicate",
                "duration": 260,
                "thumbnail": "https://example.com/3.jpg",
                "webpage_url": "https://www.youtube.com/watch?v=vid-3",
                "uploader": "Creator",
                "availability": "public",
            },
        ],
        raw={},
    )


def fake_channel_preview_with_avatar(
    source_url: str,
    avatar_url: str = "https://yt3.googleusercontent.com/avatar=s800",
) -> PreviewResponse:
    preview = fake_playlist_preview(source_url)
    preview.raw = {
        "thumbnails": [
            # A wide banner must never be mistaken for the square avatar.
            {"id": "banner_uncropped", "url": "https://yt3.googleusercontent.com/banner=w1707-h284", "width": 1707, "height": 284},
            {"id": "avatar_uncropped", "url": avatar_url, "width": 800, "height": 800},
        ]
    }
    return preview


def test_user_settings_leave_new_sidebar_preference_unset_and_merge_ui_updates() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()

    service = UserSettingsService(session)
    record = service.ensure_for_user(user)

    assert "sidebar_collapsed" not in record.ui_prefs

    record.ui_prefs = {"theme": "midnight", "sidebar_collapsed": False}
    session.flush()
    updated = service.update_for_user(
        user,
        UserSettingsUpdateRequest(ui_prefs={"sidebar_collapsed": True}),
    )

    assert updated.ui_prefs == {"theme": "midnight", "sidebar_collapsed": True}


def test_remote_playback_cache_settings_default_validate_and_clamp_durable_values() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = UserSettingsService(session)

    record = service.ensure_for_user(user)
    assert service.serialize(record).remote_playback_cache == RemotePlaybackCacheSettings(
        enabled=True,
        recent_video_limit=5,
        storage_limit_mb=2048,
    )

    record.remote_playback_cache = {
        "enabled": False,
        "recent_video_limit": 999,
        "storage_limit_mb": -10,
    }
    session.flush()
    normalized = service.ensure_for_user(user)
    assert normalized.remote_playback_cache == {
        "enabled": False,
        "recent_video_limit": 20,
        "storage_limit_mb": 512,
    }

    with pytest.raises(ValidationError):
        RemotePlaybackCacheSettings(recent_video_limit=0)
    with pytest.raises(ValidationError):
        RemotePlaybackCacheSettings(storage_limit_mb=51_201)
    with pytest.raises(ValidationError):
        RemotePlaybackCacheSettings(recent_video_limit="10")


def test_user_settings_sanitize_legacy_absolute_output_preferences() -> None:
    session = make_session()
    user = make_user()
    record = UserSettings(
        id="settings-1",
        user_id=user.id,
        download_defaults={"output_profile": {"base_path": "/legacy/outside", "organize_by": "downloads"}},
        automation_defaults={"output_profile": {"base_path": "../legacy", "organize_by": "playlist"}},
        ui_prefs={},
        notification_prefs={},
    )
    session.add_all([user, record])
    session.commit()

    normalized = UserSettingsService(session).ensure_for_user(user)

    assert normalized.download_defaults["output_profile"]["base_path"] is None
    assert normalized.automation_defaults["output_profile"]["base_path"] is None


def test_automation_preview_applies_rules_without_queueing(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    jobs = FakeJobs()
    service = SourceAutomationService(session, jobs)
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            auto_download=True,
            label="Tutorials",
            source_url="https://www.youtube.com/playlist?list=PL123",
            source_type="playlist",
            rules=AutomationRuleSet(include_title=["keep"], exclude_title=["short"]),
            max_items_per_run=1,
            backfill_limit=3,
        ),
        user,
    )
    session.commit()

    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: fake_playlist_preview(source_url),
    )

    preview = service.preview_automation(automation.id, user)

    assert preview.run.status == "preview"
    assert preview.run.discovered_count == 3
    assert preview.run.matched_count == 1
    assert preview.run.queued_count == 0
    assert preview.run.manual_count == 0
    assert preview.run.skipped_count == 2
    assert len(jobs.enqueued) == 0
    assert session.query(AutomationRun).count() == 0
    assert session.query(AutomationDecision).count() == 0
    assert {decision.action for decision in preview.run.decisions} == {"would_queue", "skipped_rule", "skipped_limit"}


def test_automation_create_infers_source_type_when_request_omits_it() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())

    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Inferred playlist",
            source_url="https://www.youtube.com/playlist?list=PL123",
        ),
        user,
    )

    assert automation.source_type == "playlist"
    assert service.serialize(automation).source_type == "playlist"


def test_automation_source_type_inference_respects_explicit_values() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    explicit = service.create_automation(
        SourceAutomationCreateRequest(
            label="Explicit generic",
            source_url="https://www.youtube.com/@creator/videos",
            source_type="generic_url",
        ),
        user,
    )
    inferred = service.create_automation(
        SourceAutomationCreateRequest(label="Search", source_url="https://example.com/ytsearch:ambient", source_type=None),
        user,
    )

    assert explicit.source_type == "generic_url"
    assert inferred.source_type == SourceAutomationService.infer_source_type("https://example.com/ytsearch:ambient")


def test_automation_max_age_skips_entries_strictly_older_than_the_run_window(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Recent uploads",
            source_url="https://www.youtube.com/playlist?list=PL123",
            rules=AutomationRuleSet(max_age_days=7),
        ),
        user,
    )
    session.commit()
    inspected = fake_playlist_preview(automation.source_url)
    inspected.entries = inspected.entries[:1]
    inspected.entries[0] = inspected.entries[0].model_copy(update={"published_at": datetime(2026, 7, 9, 11, 59, 59)})
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: inspected)

    run_time = datetime(2026, 7, 16, 12, 0, 0)
    preview = service.preview_automation(automation.id, user, now=run_time)
    run = service.run_automation(automation.id, user, now=run_time)
    session.commit()

    assert [(decision.action, decision.reason) for decision in preview.run.decisions] == [
        ("skipped_rule", "Published before the automation's maximum age window.")
    ]
    persisted = service.serialize_run(run, include_decisions=True)
    assert [(decision.action, decision.reason) for decision in persisted.decisions] == [
        ("skipped_rule", "Published before the automation's maximum age window.")
    ]


@pytest.mark.parametrize("published_at", [datetime(2026, 7, 9, 12, 0, 0), datetime(2026, 7, 15), None])
def test_automation_max_age_accepts_boundary_newer_and_undated_entries(monkeypatch, published_at) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            auto_download=True,
            label="Recent uploads",
            source_url="https://www.youtube.com/playlist?list=PL123",
            rules=AutomationRuleSet(max_age_days=7),
        ),
        user,
    )
    session.commit()
    inspected = fake_playlist_preview(automation.source_url)
    inspected.entries = [inspected.entries[0].model_copy(update={"published_at": published_at})]
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: inspected)

    preview = service.preview_automation(automation.id, user, now=datetime(2026, 7, 16, 12, 0, 0))

    assert [decision.action for decision in preview.run.decisions] == ["would_queue"]


def test_audio_rule_accepts_inspected_audio_only_media_from_any_provider(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            auto_download=True,
            label="Audio",
            source_url="https://www.youtube.com/playlist?list=PL123",
            rules=AutomationRuleSet(media_kind="audio"),
        ),
        user,
    )
    session.commit()
    inspected = fake_playlist_preview(automation.source_url)
    inspected.entries = inspected.entries[:1]
    inspected.entries[0] = inspected.entries[0].model_copy(update={"media_kind": "audio"})
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: inspected)

    preview = service.preview_automation(automation.id, user)

    assert [decision.action for decision in preview.run.decisions] == ["would_queue"]


@pytest.mark.parametrize(
    ("rule_kind", "inspected_kind", "source_url", "expected_action", "expected_reason"),
    [
        ("video", "video", "https://www.youtube.com/watch?v=video-1", "would_queue", "Would queue if this automation runs."),
        ("audio", "audio", "https://soundcloud.com/artist/track", "would_queue", "Would queue if this automation runs."),
        ("video", "audio", "https://soundcloud.com/artist/track", "skipped_rule", "Automation is limited to video media."),
        ("audio", "video", "https://www.youtube.com/watch?v=video-1", "skipped_rule", "Automation is limited to audio media."),
        ("audio", None, "https://example.com/unknown", "skipped_rule", "Media kind is unavailable for this entry."),
        ("any", None, "https://example.com/unknown", "would_queue", "Would queue if this automation runs."),
    ],
)
def test_automation_media_kind_rules_use_inspected_facts(
    monkeypatch,
    rule_kind,
    inspected_kind,
    source_url,
    expected_action,
    expected_reason,
) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            auto_download=True,
            label="Media rule",
            source_url="https://example.com/feed",
            rules=AutomationRuleSet(media_kind=rule_kind),
        ),
        user,
    )
    session.commit()
    inspected = fake_playlist_preview(automation.source_url)
    inspected.entries = [
        inspected.entries[0].model_copy(update={"webpage_url": source_url, "media_kind": inspected_kind})
    ]
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: inspected)

    preview = service.preview_automation(automation.id, user)

    assert [(decision.action, decision.reason) for decision in preview.run.decisions] == [
        (expected_action, expected_reason)
    ]


def test_automation_run_records_decisions_skips_duplicates(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    duplicate = LibraryItem(
        id="item-1",
        user_id=user.id,
        visibility="private",
        remote_id="vid-3",
        title="Existing duplicate",
        metadata_json={},
    )
    session.add_all([user, duplicate])
    session.commit()
    jobs = FakeJobs()
    events = CapturingEvents()
    inspected_format_plans: list[FormatSelection | None] = []
    service = SourceAutomationService(session, jobs, events)  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            auto_download=True,
            label="Tutorials",
            source_url="https://www.youtube.com/playlist?list=PL123",
            source_type="playlist",
            format_selection=FormatSelection(preset="audio_only", output_container="webm"),
            rules=AutomationRuleSet(include_title=["keep"]),
            max_items_per_run=5,
            backfill_limit=3,
        ),
        user,
    )
    session.commit()

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None):  # noqa: ANN001
        inspected_format_plans.append(format_selection)
        preview = fake_playlist_preview(source_url)
        preview.format_resolution = FormatResolutionState(
            requested_selector="bestaudio/best",
            selected_format_id="preview-audio",
        )
        return preview

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)

    run = service.run_automation(automation.id, user)
    session.commit()

    decisions = session.query(AutomationDecision).filter(AutomationDecision.run_id == run.id).all()
    assert run.status == "completed"
    assert run.matched_count == 1
    assert run.queued_count == 1
    assert run.manual_count == 0
    assert run.skipped_count == 2
    assert len(jobs.enqueued) == 1
    assert jobs.enqueued[0].source_url == "https://www.youtube.com/watch?v=vid-1"
    assert jobs.enqueued[0].format_selection.preset == "audio_only"
    assert jobs.enqueued[0].preview_snapshot["format_resolution"] == {
        "requested_selector": "bestaudio/best",
        "selected_format_id": "preview-audio",
        "fallback_reason": None,
    }
    assert [selection.preset if selection else None for selection in inspected_format_plans] == ["audio_only"]
    assert {decision.action for decision in decisions} == {"queued", "skipped_rule", "skipped_duplicate"}
    assert service.serialize(automation).last_run_summary["queued"] == 1
    assert {event_type for event_type, _payload in events.published} == {"automation_checked", "automation_item_decided"}
    assert all(payload["user_id"] == user.id for _event_type, payload in events.published)


def test_manual_automation_run_records_matches_without_queueing(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    jobs = FakeJobs()
    service = SourceAutomationService(session, jobs)
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Manual Tutorials",
            source_url="https://www.youtube.com/playlist?list=PL123",
            source_type="playlist",
            auto_download=False,
            rules=AutomationRuleSet(include_title=["keep"]),
            max_items_per_run=1,
            backfill_limit=1,
        ),
        user,
    )
    session.commit()

    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: fake_playlist_preview(source_url),
    )

    preview = service.preview_automation(automation.id, user)
    run = service.run_automation(automation.id, user)
    session.commit()

    decisions = session.query(AutomationDecision).filter(AutomationDecision.run_id == run.id).all()
    assert preview.run.manual_count == 1
    assert {decision.action for decision in preview.run.decisions} == {"manual"}
    assert run.status == "completed"
    assert run.matched_count == 1
    assert run.queued_count == 0
    assert run.manual_count == 1
    assert run.skipped_count == 0
    assert len(jobs.enqueued) == 0
    assert {decision.action for decision in decisions} == {"manual"}
    assert service.serialize(automation).last_run_summary["manual"] == 1


def test_unrecordable_automation_failure_never_publishes_or_preserves_history(monkeypatch) -> None:
    """A failure whose short write transaction cannot commit must leave no trace."""
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    events = CapturingEvents()
    service = SourceAutomationService(session, FakeJobs(), events)  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Temporary failure", source_url="https://example.com/channel", source_type="channel"),
        user,
    )
    session.commit()
    webhook_failures: list[str] = []
    monkeypatch.setattr(
        WebhookService,
        "notify_automation_error",
        lambda _self, _automation, error: webhook_failures.append(error),
    )
    monkeypatch.setattr(persistence, "WRITER_ADMISSION_TIMEOUT_SECONDS", 0.05)
    holder_ready = Event()
    release_holder = Event()
    preview_reached = Event()

    # The run lease is claimed before the source is inspected, so the writer
    # slot must be starved only after the claim commits (once the preview runs)
    # to exercise the failure-record write rather than the claim transaction.
    def hold_writer_slot() -> None:
        assert preview_reached.wait(timeout=2)
        persistence.writer_admission_lock.acquire()
        holder_ready.set()
        try:
            assert release_holder.wait(timeout=2)
        finally:
            persistence.writer_admission_lock.release()

    def failing_preview(*_args, **_kwargs):
        preview_reached.set()
        assert holder_ready.wait(timeout=2)
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(YtDlpService, "preview", failing_preview)

    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(hold_writer_slot)
        try:
            with pytest.raises(OperationalError):
                service.run_automation(automation.id, user)
        finally:
            release_holder.set()
        holder.result(timeout=1)

    assert _runs(session, automation.id) == []
    assert service.get_automation(automation.id, user).last_error is None  # type: ignore[union-attr]
    assert events.published == []
    assert webhook_failures == []


def test_committed_failure_is_not_reported_as_failed_when_notifications_raise(monkeypatch) -> None:
    class BrokenEvents:
        def publish(self, _event_type, _payload):  # noqa: ANN001
            raise RuntimeError("event delivery unavailable")

    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs(), BrokenEvents())  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Commit first", source_url="https://example.com/channel", source_type="channel"),
        user,
    )
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("source unavailable")))
    monkeypatch.setattr(WebhookService, "notify_automation_error", lambda *_args: (_ for _ in ()).throw(RuntimeError("webhook unavailable")))

    failed = service.run_automation(automation.id, user)
    session.commit()

    assert failed.status == "failed"
    assert [run.id for run in _runs(session, automation.id)] == [failed.id]


def test_channel_lifecycle_is_owner_scoped_and_source_urls_remain_distinct() -> None:
    session = make_session()
    owner = make_user()
    other = make_user("user-2", "other")
    session.add_all([owner, other])
    session.commit()
    service = SourceAutomationService(session, FakeJobs())  # type: ignore[arg-type]
    first = service.create_automation(
        SourceAutomationCreateRequest(label="Same display label", source_url="https://www.youtube.com/@first/videos", source_type="channel"),
        owner,
    )
    second = service.create_automation(
        SourceAutomationCreateRequest(label="Same display label", source_url="https://www.youtube.com/@second/videos", source_type="channel"),
        owner,
    )
    foreign = service.create_automation(
        SourceAutomationCreateRequest(label="Private channel", source_url="https://www.youtube.com/@private/videos", source_type="channel"),
        other,
    )
    session.commit()

    assert {automation.source_url for automation in service.list_automations(owner)} == {
        "https://www.youtube.com/@first/videos",
        "https://www.youtube.com/@second/videos",
    }
    assert [automation.id for automation in service.list_automations(other)] == [foreign.id]
    assert service.get_automation(first.id, other) is None
    for operation in (
        lambda: service.pause_automation(first.id, other),
        lambda: service.resume_automation(first.id, other),
        lambda: service.delete_automation(first.id, other),
        lambda: service.run_automation(first.id, other),
    ):
        with pytest.raises(ValueError, match="not found"):
            operation()
    assert service.get_automation(first.id, owner) is not None
    assert service.get_automation(second.id, owner) is not None


def test_due_check_commits_the_same_failed_run_contract_as_manual_execution(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    events = CapturingEvents()
    service = SourceAutomationService(session, FakeJobs(), events)  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Scheduled channel", source_url="https://example.com/scheduled", source_type="channel"),
        user,
    )
    automation.next_check_at = None
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scheduled source unavailable")))
    monkeypatch.setattr(WebhookService, "notify_automation_error", lambda *_args: None)

    results = service.process_due()
    assert [(entry.id, queued) for entry, queued in results] == [(automation.id, 0)]
    assert [event_type for event_type, _payload in events.published] == ["automation_failed"]

    runs = _runs(session, automation.id)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert automation.last_run_summary["status"] == "failed"
    assert [event_type for event_type, _payload in events.published] == ["automation_failed"]


def test_success_events_and_staged_jobs_are_suppressed_without_a_durable_commit(monkeypatch) -> None:
    """Dispatch and notifications stay tied to the run's own committed write transaction."""
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    jobs = FakeJobs()
    events = CapturingEvents()
    service = SourceAutomationService(session, jobs, events)  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Rollback", source_url="https://example.com/feed", source_type="channel"), user
    )
    session.commit()
    monkeypatch.setattr(persistence, "WRITER_ADMISSION_TIMEOUT_SECONDS", 0.05)
    holder_ready = Event()
    release_holder = Event()
    preview_reached = Event()

    # Starve the writer slot only after the lease claim commits (once the
    # preview runs) so the staging transaction — not the claim — is the one
    # denied a durable commit.
    def hold_writer_slot() -> None:
        assert preview_reached.wait(timeout=2)
        persistence.writer_admission_lock.acquire()
        holder_ready.set()
        try:
            assert release_holder.wait(timeout=2)
        finally:
            persistence.writer_admission_lock.release()

    def gated_preview(*_args, **_kwargs):
        preview_reached.set()
        assert holder_ready.wait(timeout=2)
        return fake_playlist_preview("https://example.com/feed")

    monkeypatch.setattr(YtDlpService, "preview", gated_preview)

    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(hold_writer_slot)
        try:
            with pytest.raises(OperationalError):
                service.run_automation(automation.id, user)
        finally:
            release_holder.set()
        holder.result(timeout=1)

    assert events.published == []
    assert jobs.dispatched == []
    assert _runs(session, automation.id) == []


def test_auto_download_command_preserves_schedule_and_failure_state() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Narrow", source_url="https://example.com/feed", source_type="channel"), user
    )
    original_next = automation.next_check_at
    automation.last_error = "Previous failure"
    session.flush()

    updated = service.set_auto_download(automation.id, True, user)

    assert updated.auto_download is True
    assert updated.next_check_at == original_next
    assert updated.last_error == "Previous failure"


def test_source_validation_failure_is_recorded_instead_of_escaping() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())  # type: ignore[arg-type]
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Invalid later", source_url="https://example.com/feed", source_type="channel"), user
    )
    automation.source_url = "file:///private/source"
    session.commit()

    run = service.run_automation(automation.id, user)
    session.commit()

    assert run.status == "failed"
    assert run.error
    assert _runs(session, automation.id)[0].id == run.id


def test_due_processing_isolates_transaction_failure_and_continues(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())  # type: ignore[arg-type]
    automations = [
        service.create_automation(
            SourceAutomationCreateRequest(
                label=label, source_url=f"https://example.com/{label}", source_type="channel", auto_download=False
            ),
            user,
        )
        for label in ("first", "poison", "last")
    ]
    for automation in automations:
        automation.next_check_at = None
    session.commit()

    def preview(_self, source_url, *_args, **_kwargs):  # noqa: ANN001
        if source_url.endswith("/poison"):
            raise SQLAlchemyError("poisoned transaction")
        return fake_playlist_preview(source_url)

    monkeypatch.setattr(YtDlpService, "preview", preview)

    results = service.process_due()

    assert [automation.id for automation, _queued in results] == [automation.id for automation in automations]
    persisted_automation_ids = {run.automation_id for run in session.query(AutomationRun).all()}
    assert persisted_automation_ids == {automations[0].id, automations[2].id}


def test_serialize_automation_includes_artwork_url_field() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Channel", source_url="https://example.com/channel", source_type="channel"),
        user,
    )
    session.commit()

    assert service.serialize(automation).artwork_url is None

    automation.artwork_url = "https://yt3.googleusercontent.com/avatar=s800"
    session.commit()

    # The raw provider URL is what's persisted (storage assertion reads the
    # model field directly). Without an injected registrar, serialize() must
    # not echo the raw URL back — the frontend Artwork component only renders
    # a src beginning with /api/, so leaking the raw value would silently
    # fall back to initials. See
    # test_serialize_maps_a_stored_artwork_url_through_the_registered_registrar
    # for the registrar-injected mapping contract.
    assert automation.artwork_url == "https://yt3.googleusercontent.com/avatar=s800"
    assert service.serialize(automation).artwork_url is None


def test_serialize_maps_a_stored_artwork_url_through_the_registered_registrar() -> None:
    """Serve-boundary mapping of a stored raw avatar URL.

    The automation sweep persists the RAW upstream avatar URL on
    SourceAutomation.artwork_url. The frontend Artwork component
    (resolveArtworkUrl) hard-rejects any src not starting with /api/, so a
    served automation must map its stored artwork_url through the injected
    artwork registrar (mirroring main.py's _remote_artwork_url /
    _job_response_with_artwork pattern) before it reaches the frontend.
    """
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()

    minted: list[str] = []

    def fake_registrar(raw_url: str) -> str:
        minted.append(raw_url)
        return f"/api/artwork/remote/opaque-{len(minted)}"

    service = SourceAutomationService(session, FakeJobs(), artwork_url_resolver=fake_registrar)
    automation = service.create_automation(
        SourceAutomationCreateRequest(label="Channel", source_url="https://example.com/channel", source_type="channel"),
        user,
    )
    automation.artwork_url = "https://yt3.googleusercontent.com/avatar=s800"
    session.commit()

    served = service.serialize(automation)

    assert served.artwork_url == "/api/artwork/remote/opaque-1"
    assert served.artwork_url.startswith("/api/artwork/")
    assert minted == ["https://yt3.googleusercontent.com/avatar=s800"]
    # Storage is untouched by serving: the model still holds the raw URL.
    assert automation.artwork_url == "https://yt3.googleusercontent.com/avatar=s800"


def test_automation_run_persists_channel_artwork_when_inspection_resolves_an_avatar(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Creator",
            source_url="https://www.youtube.com/@creator/videos",
            source_type="channel",
            auto_download=False,
        ),
        user,
    )
    session.commit()
    assert automation.artwork_url is None

    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: fake_channel_preview_with_avatar(source_url),
    )

    run = service.run_automation(automation.id, user)
    session.commit()

    assert run.status == "completed"
    # Storage assertion reads the model field directly; without an injected
    # registrar, serialize() degrades to null rather than echoing the raw URL.
    assert automation.artwork_url == "https://yt3.googleusercontent.com/avatar=s800"
    assert service.serialize(automation).artwork_url is None


def test_automation_run_failure_never_clears_a_previously_stored_avatar(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Creator",
            source_url="https://www.youtube.com/@creator/videos",
            source_type="channel",
            auto_download=False,
        ),
        user,
    )
    automation.artwork_url = "https://yt3.googleusercontent.com/existing=s800"
    session.commit()

    def fail_preview(*_args, **_kwargs):
        raise RuntimeError("upstream inspection failed")

    monkeypatch.setattr(YtDlpService, "preview", fail_preview)

    run = service.run_automation(automation.id, user)

    assert run.status == "failed"
    assert automation.artwork_url == "https://yt3.googleusercontent.com/existing=s800"


def test_automation_run_without_a_resolvable_avatar_leaves_stored_value_untouched(monkeypatch) -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Creator",
            source_url="https://www.youtube.com/@creator/videos",
            source_type="channel",
            auto_download=False,
        ),
        user,
    )
    automation.artwork_url = "https://yt3.googleusercontent.com/existing=s800"
    session.commit()

    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: fake_playlist_preview(source_url),
    )

    run = service.run_automation(automation.id, user)

    assert run.status == "completed"
    assert automation.artwork_url == "https://yt3.googleusercontent.com/existing=s800"


def test_preview_automation_does_not_persist_channel_artwork(monkeypatch) -> None:
    """Dry-run previews (used before an automation is created) never write durable state."""
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(
        SourceAutomationCreateRequest(
            label="Creator",
            source_url="https://www.youtube.com/@creator/videos",
            source_type="channel",
            auto_download=False,
        ),
        user,
    )
    session.commit()

    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: fake_channel_preview_with_avatar(source_url),
    )

    service.preview_automation(automation.id, user)

    assert automation.artwork_url is None
