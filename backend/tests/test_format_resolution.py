from datetime import datetime

import pytest

from app.config import settings
from app.models import DownloadJob, User
from app.schemas import FormatResolutionState, FormatSelection, OutputProfile
from app.services import job_manager as job_manager_module
from app.services.format_resolution import AcquisitionResult, FormatResolutionPolicy
from app.services.job_manager import JobManager
from app.services.yt_dlp_service import YtDlpService
from support import memory_session_factory


def make_session():
    return memory_session_factory()()


def test_previewed_format_falls_back_with_an_explanation_when_source_formats_change() -> None:
    captured_options: list[dict] = []

    class ChangingFormatsYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            if download and not self.options["format"].startswith("preview-video+preview-audio/"):
                raise RuntimeError("Requested format is not available: preview-video+preview-audio")
            selected = "new-video+new-audio" if download else "preview-video+preview-audio"
            return {
                "id": "changing-video",
                "title": "Changing formats",
                "webpage_url": source_url,
                "format_id": selected,
                "formats": [
                    {"format_id": "new-video", "vcodec": "avc1", "acodec": "none", "ext": "mp4"},
                    {"format_id": "new-audio", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    selection = FormatSelection(preset="best_1080p", output_container="mp4", subtitles=True)
    service = YtDlpService(make_session(), ydl_factory=ChangingFormatsYDL)

    preview = service.preview("https://example.com/changing", lazy_playlist=False, format_selection=selection)
    result = service.download(
        "https://example.com/changing",
        format_selection=selection,
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
        previous_resolution=preview.format_resolution,
    )

    assert preview.format_resolution.selected_format_id == "preview-video+preview-audio"
    assert captured_options[1]["format"].startswith("preview-video+preview-audio/")
    assert captured_options[1]["writesubtitles"] is True
    assert result.info["id"] == "changing-video"
    assert result.format_resolution.selected_format_id == "new-video+new-audio"
    assert result.format_resolution.fallback_reason == (
        "Source formats changed after preview; Lumina used new-video+new-audio instead of "
        "preview-video+preview-audio while preserving the best_1080p selection policy."
    )


def test_playlist_download_reports_actual_nested_requested_format_ids() -> None:
    class PlaylistYDL:
        def __init__(self, _options):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            assert download is True
            return {
                "_type": "playlist",
                "id": "playlist",
                "webpage_url": source_url,
                "entries": [
                    {
                        "id": "video",
                        "requested_downloads": [
                            {
                                "format_id": "actual-video+actual-audio",
                                "requested_formats": [
                                    {"format_id": "actual-video"},
                                    {"format_id": "actual-audio"},
                                ]
                            }
                        ],
                    }
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    selection = FormatSelection(preset="best", output_container="mp4")
    previous = FormatResolutionState(
        requested_selector=FormatResolutionPolicy.resolve(selection).state.requested_selector,
        selected_format_id="preview-video+preview-audio",
    )

    result = YtDlpService(make_session(), ydl_factory=PlaylistYDL).download(
        "https://example.com/playlist",
        format_selection=selection,
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
        previous_resolution=previous,
    )

    assert result.format_resolution.selected_format_id == "actual-video+actual-audio"
    assert "preview-video+preview-audio" in (result.format_resolution.fallback_reason or "")


def test_custom_format_remains_the_exact_download_selector() -> None:
    captured_options: list[dict] = []

    class CustomFormatYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            return {"id": "custom-video", "webpage_url": source_url, "format_id": "22"}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    selection = FormatSelection(preset="custom", custom_format="22/18")
    previous = FormatResolutionState(requested_selector="22/18", selected_format_id="18")

    result = YtDlpService(make_session(), ydl_factory=CustomFormatYDL).download(
        "https://example.com/custom",
        format_selection=selection,
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
        previous_resolution=previous,
    )

    assert captured_options[0]["format"] == "22/18"
    assert result.format_resolution.selected_format_id == "22"
    assert result.format_resolution.fallback_reason is None


@pytest.mark.parametrize("custom_format", [None, "", "   "])
def test_custom_preset_requires_an_explicit_nonblank_selector(custom_format: str | None) -> None:
    with pytest.raises(ValueError, match="custom_format"):
        FormatSelection(preset="custom", custom_format=custom_format)


def test_changed_user_choice_does_not_reuse_the_previewed_format_id() -> None:
    captured_options: list[dict] = []

    class ChangedChoiceYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            return {"id": "video", "webpage_url": source_url, "format_id": "18"}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    previous_audio = FormatResolutionState(requested_selector="bestaudio/best", selected_format_id="251")

    result = YtDlpService(make_session(), ydl_factory=ChangedChoiceYDL).download(
        "https://example.com/changed-choice",
        format_selection=FormatSelection(preset="best", output_container="mp4"),
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
        previous_resolution=previous_audio,
    )

    assert not captured_options[0]["format"].startswith("251/")
    assert result.format_resolution.selected_format_id == "18"
    assert result.format_resolution.fallback_reason is None


def test_matching_current_format_clears_a_previous_fallback_explanation() -> None:
    class StableFormatYDL:
        def __init__(self, _options):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            assert download is True
            return {"id": "video", "webpage_url": source_url, "format_id": "stable-format"}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    selection = FormatSelection(preset="best", output_container="mp4")
    previous = FormatResolutionState(
        requested_selector=FormatResolutionPolicy.resolve(selection).state.requested_selector,
        selected_format_id="stable-format",
        fallback_reason="An earlier acquisition used a different format.",
    )

    result = YtDlpService(make_session(), ydl_factory=StableFormatYDL).download(
        "https://example.com/stable",
        format_selection=selection,
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
        previous_resolution=previous,
    )

    assert result.format_resolution.selected_format_id == "stable-format"
    assert result.format_resolution.fallback_reason is None


def test_completed_job_persists_and_exposes_the_effective_resolution_without_a_new_column(monkeypatch) -> None:  # noqa: ANN001
    Session = memory_session_factory()
    previous = FormatResolutionState(
        requested_selector="bestvideo+bestaudio/best",
        selected_format_id="137+140",
    )
    effective = FormatResolutionState(
        requested_selector="bestvideo+bestaudio/best",
        selected_format_id="18",
        fallback_reason="Source formats changed after preview; Lumina used 18 instead of 137+140.",
    )
    with Session.begin() as session:
        session.add(User(id="user-1", username="member", display_name="Member", role="viewer", is_active=True))
        session.add(
            DownloadJob(
                id="job-1",
                user_id="user-1",
                source_url="https://example.com/video",
                status="queued",
                format_selection=FormatSelection().model_dump(),
                output_profile=OutputProfile().model_dump(),
                preview_snapshot={"title": "Video", "format_resolution": previous.model_dump()},
                created_at=datetime.utcnow(),
            )
        )

    def fake_download(*_args, output_root, **_kwargs):  # noqa: ANN002, ANN003
        media = output_root / "user-1" / "video.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"media")
        return AcquisitionResult(info={"id": "video", "title": "Video", "filepath": str(media)}, format_resolution=effective)

    monkeypatch.setattr(job_manager_module, "SessionLocal", Session)
    monkeypatch.setattr(YtDlpService, "download", fake_download)

    JobManager(type("Events", (), {"publish": lambda *_args, **_kwargs: None})())._run_job("job-1")  # type: ignore[arg-type]

    with Session() as session:
        persisted = session.get(DownloadJob, "job-1")
        serialized = JobManager.serialize(persisted)

    assert serialized.status == "completed"
    assert serialized.format_resolution == effective
    assert "cookie" not in str(serialized.format_resolution).lower()
