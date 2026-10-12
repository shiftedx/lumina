from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import yt_dlp
from fastapi import HTTPException
from sqlalchemy import select

from app import main as main_module
from app.models import AcquisitionBatchEntry, User
from app.schemas import AcquisitionBatchCreateRequest, FormatSelection, JobCreateRequest, MediaSourceCapabilities, PreviewEntry, PreviewRequest, PreviewResponse
from app.services.acquisition_batch import AcquisitionBatchService, SelectedSourceEntry
from app.services.yt_dlp_service import YtDlpService
from support import memory_session_factory


class _NullDispatcher:
    def ensure_dispatched(self, **_options: object):  # noqa: ANN202
        raise AssertionError("dispatch must not run when only staging entries")

    def activate_dispatched(self, _receipt: object) -> None:
        raise AssertionError("activation must not run when only staging entries")


def make_session():
    session = memory_session_factory()()
    user = User(
        id="user-1",
        username="tester",
        display_name="Tester",
        password_hash="hash",
        role="admin",
        is_active=True,
    )
    session.add(user)
    session.commit()
    return session, user


def test_preview_request_preserves_the_selected_format_plan() -> None:
    request = PreviewRequest(
        source_url="https://example.com/video",
        format_selection=FormatSelection(preset="audio_only", output_container="webm"),
    )

    assert request.format_selection.preset == "audio_only"
    assert request.format_selection.output_container == "webm"


def test_preview_route_passes_the_selected_format_plan_to_acquisition(monkeypatch) -> None:
    _session, user = make_session()
    inspected: list[FormatSelection | None] = []
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *args, **kwargs: user)
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        inspected.append(format_selection)
        return PreviewResponse(kind="video", title="Preview", webpage_url=source_url, raw={"formats": []})

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)

    response = asyncio.run(
        main_module.preview(
            PreviewRequest(
                source_url="https://example.com/video",
                format_selection=FormatSelection(preset="audio_only", output_container="webm"),
            ),
            object(),  # type: ignore[arg-type]
        )
    )

    assert response.title == "Preview"
    assert [selection.preset if selection else None for selection in inspected] == ["audio_only"]


def test_direct_jobs_and_batches_reinspect_and_reject_unacquirable_live_sources(monkeypatch) -> None:
    session, user = make_session()
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    live_capabilities = MediaSourceCapabilities(
        provider="youtube", lifecycle="live", can_play=False,
        play_reason="live_playback_not_supported", can_acquire=False,
        acquire_reason="live_acquisition_not_supported",
    )

    def live_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        # A batch reinspects the source once; the container expands into per-entry
        # capabilities the gate reads. A direct job reinspects the video itself.
        if "playlist" in source_url:
            return PreviewResponse(
                kind="playlist", webpage_url=source_url,
                capabilities=MediaSourceCapabilities(provider="youtube", lifecycle="vod", can_play=False, can_acquire=True),
                entries=[PreviewEntry(id="live", webpage_url="https://www.youtube.com/watch?v=live", capabilities=live_capabilities)],
                raw={"_type": "playlist"},
            )
        return PreviewResponse(kind="video", webpage_url=source_url, capabilities=live_capabilities, raw={"is_live": True})

    monkeypatch.setattr(YtDlpService, "preview", live_preview)
    with pytest.raises(HTTPException) as job_error:
        main_module.create_job(JobCreateRequest(source_url="https://www.youtube.com/watch?v=live"), object(), user, session)
    assert (job_error.value.status_code, job_error.value.detail["reason"]) == (409, "live_acquisition_not_supported")

    batch = AcquisitionBatchCreateRequest.model_validate({
        "source_url": "https://www.youtube.com/playlist?list=live",
        "format_selection": {"preset": "best"}, "output_profile": {},
        "entries": [{"source_url": "https://www.youtube.com/watch?v=live", "remote_id": "live", "title": "Live"}],
    })
    with pytest.raises(HTTPException) as batch_error:
        main_module.create_acquisition_batch(batch, object(), user, session)
    assert (batch_error.value.status_code, batch_error.value.detail["reason"]) == (409, "live_acquisition_not_supported")


def test_batch_creation_reinspects_the_source_once_for_all_entries(monkeypatch) -> None:
    session, user = make_session()
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "jobs", SimpleNamespace(acquisition=object()))

    extractions: list[str] = []

    def counting_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        extractions.append(source_url)
        return PreviewResponse(
            kind="playlist", webpage_url=source_url,
            capabilities=MediaSourceCapabilities(provider="youtube", lifecycle="vod", can_play=False, can_acquire=True),
            entries=[
                PreviewEntry(
                    id=f"v{index}", webpage_url=f"https://www.youtube.com/watch?v=v{index}",
                    capabilities=MediaSourceCapabilities(provider="youtube", lifecycle="vod", can_play=True, can_acquire=True),
                )
                for index in range(4)
            ],
            raw={"_type": "playlist"},
        )

    monkeypatch.setattr(YtDlpService, "preview", counting_preview)

    class _StopBeforeDispatch(Exception):
        pass

    captured: dict[str, object] = {}

    def capture_and_stop(self, **options):  # noqa: ANN001, ANN003
        del self
        captured["entries"] = options["entries"]
        raise _StopBeforeDispatch

    monkeypatch.setattr(AcquisitionBatchService, "queue_selected", capture_and_stop)

    batch = AcquisitionBatchCreateRequest.model_validate({
        "source_url": "https://www.youtube.com/playlist?list=multi",
        "format_selection": {"preset": "best"}, "output_profile": {},
        "entries": [
            {"source_url": f"https://www.youtube.com/watch?v=v{index}", "remote_id": f"v{index}", "title": f"V{index}"}
            for index in range(4)
        ],
    })
    with pytest.raises(_StopBeforeDispatch):
        main_module.create_acquisition_batch(batch, object(), user, session)

    # Exactly one fresh extraction — the container — settles all four entries.
    assert extractions == ["https://www.youtube.com/playlist?list=multi"]
    assert [entry.source_url for entry in captured["entries"]] == [
        f"https://www.youtube.com/watch?v=v{index}" for index in range(4)
    ]


def test_enqueue_gate_download_errors_surface_as_structured_400_not_500(monkeypatch) -> None:
    session, user = make_session()
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)

    # Pre-stage a batch entry so the retry endpoint reaches the reinspection gate.
    staged = AcquisitionBatchService(session, _NullDispatcher()).stage_selected(
        user_id=user.id,
        source_url="https://example.com/playlist",
        entries=[SelectedSourceEntry("https://example.com/watch", "example", "one", "One")],
    )
    staged_entry = session.scalar(select(AcquisitionBatchEntry).where(AcquisitionBatchEntry.batch_id == staged.id))

    def failing_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, source_url, lazy_playlist, format_selection
        raise yt_dlp.utils.DownloadError("ERROR: Requested format is not available. secret-diagnostic-token")

    monkeypatch.setattr(YtDlpService, "preview", failing_preview)

    with pytest.raises(HTTPException) as job_error:
        main_module.create_job(JobCreateRequest(source_url="https://example.com/watch"), object(), user, session)
    assert job_error.value.status_code == 400
    assert job_error.value.detail["category"] == "format_unavailable"
    assert "secret-diagnostic-token" not in str(job_error.value.detail)

    batch_request = AcquisitionBatchCreateRequest.model_validate({
        "source_url": "https://example.com/playlist",
        "format_selection": {"preset": "best"}, "output_profile": {},
        "entries": [{"source_url": "https://example.com/watch", "title": "One"}],
    })
    with pytest.raises(HTTPException) as batch_error:
        main_module.create_acquisition_batch(batch_request, object(), user, session)
    assert batch_error.value.status_code == 400
    assert batch_error.value.detail["category"] == "format_unavailable"

    with pytest.raises(HTTPException) as retry_error:
        main_module.retry_acquisition_batch_entry(staged.id, staged_entry.id, user, session)
    assert retry_error.value.status_code == 400
    assert retry_error.value.detail["category"] == "format_unavailable"
