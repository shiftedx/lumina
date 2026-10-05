from datetime import UTC, datetime
from pathlib import Path

import pytest
import yt_dlp
from pydantic import ValidationError

from app.config import settings
from app.schemas import FormatSelection, OutputProfile, PreviewRequest
from app.services.format_resolution import FormatResolutionPolicy
from app.services.yt_dlp_service import (
    PUBLIC_OPTION_ALLOWLIST,
    PublicOnlyOptionsError,
    YtDlpService,
    assert_public_only_options,
)
from support import memory_session_factory, seed_app_settings


def make_session():
    return memory_session_factory()()


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (FormatSelection(preset="best"), "bestvideo[ext=mp4][vcodec!=none]+bestaudio[ext=m4a][acodec!=none]/best[ext=mp4]/bestvideo+bestaudio/best"),
        (FormatSelection(preset="best_1080p"), "bestvideo[height<=1080][ext=mp4][vcodec!=none]+bestaudio[ext=m4a][acodec!=none]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
        (FormatSelection(preset="audio_only"), "bestaudio/best"),
        (FormatSelection(preset="custom", custom_format="best[ext=mp4]"), "best[ext=mp4]"),
        (FormatSelection(preset="best", output_container="webm"), "bestvideo[ext=webm][vcodec!=none]+bestaudio[ext=webm][acodec!=none]/best[ext=webm]/bestvideo+bestaudio/best"),
    ],
)
def test_resolve_format_selector(selection: FormatSelection, expected: str) -> None:
    assert FormatResolutionPolicy.resolve(selection).state.requested_selector == expected


def test_resolve_output_path_uploader() -> None:
    profile = OutputProfile(subdir="curated", organize_by="uploader", template="%(title)s.%(ext)s")
    path = YtDlpService.resolve_output_path(profile, "user-1", settings.temp_root)
    assert "/user-1/" in str(path)
    assert "/by-uploader/" in str(path)
    assert "%(" not in str(path.parent)
    assert str(path).endswith("curated/%(title)s.%(ext)s")


def test_long_output_root_is_not_truncated_out_of_containment(tmp_path) -> None:  # noqa: ANN001
    service = YtDlpService(make_session())
    staging = tmp_path / ("library-" + "x" * 120) / ("nested-" + "y" * 80)

    options = service.build_download_options(
        format_selection=FormatSelection(),
        output_profile=OutputProfile(template="%(title)s [%(id)s].%(ext)s"),
        owner_user_id="user-1",
        output_root=staging,
        progress_hooks=[],
        postprocessor_hooks=[],
    )
    assert "trim_file_name" not in options

    with yt_dlp.YoutubeDL(options) as ydl:
        prepared = Path(ydl.prepare_filename({"title": "Example", "id": "abc123", "ext": "mp4"})).resolve(strict=False)
    assert prepared.is_relative_to(staging.resolve() / "user-1")


def test_preview_stays_read_only_when_the_stored_ffmpeg_path_drifts(monkeypatch) -> None:  # noqa: ANN001
    """Request-path options builds must never flush the ffmpeg correction; startup owns it."""
    import uuid

    from sqlalchemy import event

    from app.models import AppSettings

    session = make_session()
    seed_app_settings(session, ffmpeg_path="/stale/bin/ffmpeg", yt_dlp_defaults={})

    monkeypatch.setattr(
        "app.services.yt_dlp_service.shutil.which",
        lambda name: "/fresh/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def record_statements(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    observed: dict[str, object] = {}

    def fake_extract(self, source_url, options, **_kwargs):  # noqa: ANN001
        driver = self.db.connection().connection.driver_connection
        observed["write_transaction_open"] = bool(driver.in_transaction)
        observed["ffmpeg_location"] = options.get("ffmpeg_location")
        return {
            "id": "vid-1",
            "title": "Drift probe",
            "extractor": "generic",
            "extractor_key": "Generic",
            "webpage_url": source_url,
        }

    monkeypatch.setattr(YtDlpService, "_extract", fake_extract)
    service = YtDlpService(session)
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)

    preview = service.preview(f"https://example.com/watch?v={uuid.uuid4()}")

    assert preview.title == "Drift probe"
    assert observed["write_transaction_open"] is False, "the options build left a write transaction open across extraction"
    assert observed["ffmpeg_location"] == "/fresh/bin/ffmpeg", "an unusable stored path must fall back to the detected ffmpeg without a durable write"
    writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == [], f"the preview path wrote durable state: {writes}"
    assert not session.dirty, "the drift correction was left pending for a later teardown commit"
    assert session.get(AppSettings, 1).ffmpeg_path == "/stale/bin/ffmpeg"


def _stored_ffmpeg_path(tmp_path: Path, kind: str) -> str:
    if kind == "missing":
        return str(tmp_path / "gone" / "ffmpeg")
    directory = tmp_path / "ffmpeg-bin"
    directory.mkdir()
    binary = directory / "ffmpeg"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return str(directory if kind == "directory" else binary)


@pytest.mark.parametrize(
    ("stored_kind", "via", "keeps_stored"),
    [
        # An administrator-set, still-executable path wins over which() (issue #102).
        ("binary", "options", True),
        # yt-dlp's ffmpeg_location accepts a directory containing ffmpeg.
        ("directory", "options", True),
        # Startup must not clobber a usable stored path (issue #102)...
        ("binary", "startup", True),
        # ...but repairs one that no longer points at an executable.
        ("missing", "startup", False),
    ],
)
def test_stored_ffmpeg_path_is_kept_only_while_usable(monkeypatch, tmp_path, stored_kind, via, keeps_stored) -> None:  # noqa: ANN001
    stored = _stored_ffmpeg_path(tmp_path, stored_kind)
    session = make_session()
    seed_app_settings(session, ffmpeg_path=stored, yt_dlp_defaults={})
    monkeypatch.setattr(
        "app.services.yt_dlp_service.shutil.which",
        lambda name: "/detected/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    service = YtDlpService(session)
    resolved = service.build_base_options()["ffmpeg_location"] if via == "options" else service.ensure_app_settings().ffmpeg_path

    assert resolved == (stored if keeps_stored else "/detected/bin/ffmpeg")


def test_normalize_source_url_strips_youtube_radio_mix_params() -> None:
    source_url = "https://www.youtube.com/watch?v=h2RdyqepRgw&list=RDh2RdyqepRgw&start_radio=1"
    assert YtDlpService.normalize_source_url(source_url) == "https://www.youtube.com/watch?v=h2RdyqepRgw"


def test_normalize_source_url_preserves_regular_playlist_links() -> None:
    source_url = "https://www.youtube.com/watch?v=h2RdyqepRgw&list=PL1234567890"
    assert YtDlpService.normalize_source_url(source_url) == source_url


def test_preview_normalizes_publication_time_and_media_kind(monkeypatch) -> None:
    service = YtDlpService(make_session())
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    monkeypatch.setattr(
        service,
        "_extract",
        lambda *_args, **_kwargs: {
            "_type": "playlist",
            "id": "playlist-1",
            "title": "Mixed media",
            "entries": [
                {
                    "id": "video-1",
                    "title": "Video",
                    "webpage_url": "https://example.com/video-1",
                    "timestamp": 1_700_000_000,
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                },
                {
                    "id": "audio-1",
                    "title": "Audio",
                    "webpage_url": "https://example.com/audio-1",
                    "upload_date": "20231114",
                    "vcodec": "none",
                    "acodec": "opus",
                },
                {
                    "id": "unknown-1",
                    "title": "Unknown",
                    "webpage_url": "https://example.com/unknown-1",
                    "upload_date": "not-a-date",
                },
            ],
        },
    )

    preview = service.preview("https://example.com/playlist", lazy_playlist=True)

    assert [(entry.published_at, entry.media_kind) for entry in preview.entries] == [
        (datetime(2023, 11, 14, 22, 13, 20), "video"),
        (datetime(2023, 11, 14), "audio"),
        (None, None),
    ]


def test_preview_attaches_capabilities_to_a_source_and_its_playlist_entries(monkeypatch) -> None:
    service = YtDlpService(make_session())
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    monkeypatch.setattr(
        service,
        "_extract",
        lambda *_args, **_kwargs: {
            "_type": "playlist",
            "extractor_key": "Youtube",
            "title": "Live channel",
            "entries": [{
                "id": "upcoming-1",
                "title": "Soon",
                "webpage_url": "https://www.youtube.com/watch?v=upcoming-1",
                "extractor_key": "Youtube",
                "live_status": "is_upcoming",
            }],
        },
    )

    preview = service.preview("https://www.youtube.com/channel/live", lazy_playlist=True)

    assert preview.capabilities is not None
    assert preview.capabilities.provider == "youtube"
    assert preview.entries[0].capabilities is not None
    assert preview.entries[0].capabilities.lifecycle == "upcoming"
    assert preview.entries[0].capabilities.acquire_reason == "upcoming_not_started"


def test_preview_resolves_flat_channel_entry_artwork_and_parent_uploader(monkeypatch) -> None:
    service = YtDlpService(make_session())
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    monkeypatch.setattr(
        service,
        "_extract",
        lambda *_args, **_kwargs: {
            "_type": "playlist",
            "id": "channel-videos",
            "title": "Area52 - Videos",
            "channel": "Area52",
            "entries": [
                {
                    "id": "video-1",
                    "title": "A real channel video",
                    "webpage_url": "https://www.youtube.com/watch?v=video-1",
                    "thumbnail": None,
                    "thumbnails": [
                        {"url": "https://i.ytimg.com/vi/video-1/mqdefault.jpg", "width": 320, "height": 180},
                        {"url": "https://i.ytimg.com/vi/video-1/hqdefault.jpg", "width": 480, "height": 360},
                    ],
                    "uploader": None,
                    "channel": None,
                }
            ],
        },
    )

    preview = service.preview("https://www.youtube.com/channel/channel-id/videos", lazy_playlist=True)

    assert preview.entries[0].thumbnail == "https://i.ytimg.com/vi/video-1/hqdefault.jpg"
    assert preview.entries[0].uploader == "Area52"


def _playlist_ydl_factory(entry_count: int, captured_options: list[dict]):
    """A fake ydl that records the options it was built with and reports ``entry_count`` flat entries.

    Unlike real yt-dlp, this fake does not stop pagination at ``playlistend`` itself —
    the assertions below check the built options directly, and rely on the service's
    own response slicing to bound what a too-generous upstream extraction returns.
    """

    class PlaylistYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            return {
                "_type": "playlist",
                "id": "channel-videos",
                "title": "Channel",
                "extractor_key": "Youtube",
                "entries": [
                    {
                        "id": f"video-{index}",
                        "title": f"Video {index}",
                        "webpage_url": f"https://www.youtube.com/watch?v=video-{index}",
                    }
                    for index in range(entry_count)
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    return PlaylistYDL


def test_preview_bounds_playlist_extraction_to_100_by_default() -> None:
    captured_options: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_playlist_ydl_factory(150, captured_options))

    preview = service.preview("https://www.youtube.com/channel/channel-id-default/videos", lazy_playlist=True)

    assert captured_options[-1]["playlistend"] == 100
    assert len(preview.entries) == 100


def test_preview_entries_limit_bounds_extraction_options_and_response() -> None:
    captured_options: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_playlist_ydl_factory(150, captured_options))

    preview = service.preview(
        "https://www.youtube.com/channel/channel-id-limit-12/videos",
        lazy_playlist=True,
        entries_limit=12,
    )

    assert captured_options[-1]["playlistend"] == 12
    assert len(preview.entries) == 12


@pytest.mark.parametrize(
    ("entries_limit", "expected_bound", "source_suffix"),
    [
        (101, 100, "over-max"),
        (0, 100, "zero"),
        (-5, 1, "negative"),
        (None, 100, "none"),
    ],
)
def test_preview_entries_limit_is_defensively_clamped(entries_limit, expected_bound, source_suffix) -> None:  # noqa: ANN001
    captured_options: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_playlist_ydl_factory(5, captured_options))

    service.preview(
        f"https://www.youtube.com/channel/channel-id-clamp-{source_suffix}/videos",
        lazy_playlist=True,
        entries_limit=entries_limit,
    )

    assert captured_options[-1]["playlistend"] == expected_bound


def test_preview_cache_key_includes_entries_limit() -> None:
    captured_options: list[dict] = []
    extraction_count = 0

    class CountingPlaylistYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            nonlocal extraction_count
            extraction_count += 1
            return {
                "_type": "playlist",
                "id": "channel-videos",
                "title": "Channel",
                "extractor_key": "Youtube",
                "entries": [
                    {
                        "id": f"video-{index}",
                        "title": f"Video {index}",
                        "webpage_url": f"https://www.youtube.com/watch?v=video-{index}",
                    }
                    for index in range(150)
                ],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=CountingPlaylistYDL)
    url = "https://www.youtube.com/channel/channel-id-cache-key/videos"

    first = service.preview(url, lazy_playlist=True, entries_limit=12)
    second = service.preview(url, lazy_playlist=True, entries_limit=100)

    assert extraction_count == 2, "different entries_limit values must not share a cached preview response"
    assert len(first.entries) == 12
    assert len(second.entries) == 100


def test_preview_request_entries_limit_is_validated_to_the_response_bound() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest(source_url="https://example.com/playlist", entries_limit=0)
    with pytest.raises(ValidationError):
        PreviewRequest(source_url="https://example.com/playlist", entries_limit=101)
    assert PreviewRequest(source_url="https://example.com/playlist", entries_limit=12).entries_limit == 12


def test_normalize_youtube_search_result_handles_expected_fields() -> None:
    result = YtDlpService.normalize_search_result(
        {
            "id": "abc123",
            "title": "Example video",
            "uploader": "Creator",
            "duration": 215.0,
            "url": "https://www.youtube.com/watch?v=abc123",
            "view_count": 4829102,
            "availability": "public",
            "timestamp": 1_700_000_000,
            "thumbnails": [{"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"}],
        }, "youtube"
    )

    assert result.id == "abc123"
    assert result.title == "Example video"
    assert result.uploader == "Creator"
    assert result.duration == 215
    assert result.thumbnail == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    assert result.webpage_url == "https://www.youtube.com/watch?v=abc123"
    assert result.view_count == 4_829_102
    assert result.availability == "public"
    assert result.published_at == datetime.fromtimestamp(1_700_000_000, UTC).replace(tzinfo=None)
    assert result.source == "youtube"
    assert result.source_label == "YouTube"


def test_normalize_soundcloud_search_result_handles_expected_fields() -> None:
    result = YtDlpService.normalize_search_result(
        {
            "id": "track-1",
            "title": "SoundCloud example",
            "uploader": "Artist",
            "duration": 215.0,
            "url": "https://soundcloud.com/demo/track-1",
            "view_count": 4829102,
            "timestamp": 1_700_000_000,
            "thumbnails": [
                {"url": "https://i1.sndcdn.com/artworks-small.jpg", "width": 32, "height": 32},
                {"url": "https://i1.sndcdn.com/artworks-t500x500.jpg", "width": 500, "height": 500},
            ],
        }, "soundcloud"
    )

    assert result.id == "track-1"
    assert result.title == "SoundCloud example"
    assert result.uploader == "Artist"
    assert result.duration == 215
    assert result.thumbnail == "https://i1.sndcdn.com/artworks-t500x500.jpg"
    assert result.webpage_url == "https://soundcloud.com/demo/track-1"
    assert result.view_count == 4_829_102
    assert result.published_at == datetime.fromtimestamp(1_700_000_000, UTC).replace(tzinfo=None)
    assert result.source == "soundcloud"
    assert result.source_label == "SoundCloud"


def test_normalize_search_limit_bounds() -> None:
    assert YtDlpService.normalize_search_limit(None) == 10
    assert YtDlpService.normalize_search_limit(0) == 1
    assert YtDlpService.normalize_search_limit(4) == 4
    assert YtDlpService.normalize_search_limit(20) == 20
    assert YtDlpService.normalize_search_limit(30) == 24


def test_build_download_options_does_not_use_download_archive() -> None:
    session = make_session()
    service = YtDlpService(session)

    options = service.build_download_options(
        format_selection=FormatSelection(),
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
    )

    assert "download_archive" not in options
    assert options["proxy"] == ""
    assert options["external_downloader"] == {"default": "native"}
    assert options["hls_prefer_native"] is True
    # Fragmented acquisitions fail closed instead of silently dropping a fragment
    # the guarded transport refused (issue #95).
    assert options["skip_unavailable_fragments"] is False


CREDENTIAL_OPTION_KEYS = {
    "cookiefile",
    "cookiesfrombrowser",
    "username",
    "password",
    "twofactor",
    "twofacer",
    "videopassword",
    "usenetrc",
    "netrc_location",
    "client_certificate",
    "ap_mso",
    "exec",
}


def test_build_base_options_stays_within_the_public_allowlist() -> None:
    """The 1.0 cut closed base option construction to the public-only allowlist."""
    service = YtDlpService(make_session())

    options = service.build_base_options()

    assert set(options) <= PUBLIC_OPTION_ALLOWLIST
    assert not (set(options) & CREDENTIAL_OPTION_KEYS)


def test_build_download_options_stays_within_the_public_allowlist() -> None:
    service = YtDlpService(make_session())

    options = service.build_download_options(
        format_selection=FormatSelection(),
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
    )

    assert set(options) <= PUBLIC_OPTION_ALLOWLIST
    assert not (set(options) & CREDENTIAL_OPTION_KEYS)


def test_assert_public_only_options_rejects_and_names_blocked_keys() -> None:
    with pytest.raises(PublicOnlyOptionsError, match="cookiefile"):
        assert_public_only_options({"cookiefile": "/tmp/x"})

    with pytest.raises(PublicOnlyOptionsError) as excinfo:
        assert_public_only_options({"username": "u", "password": "p", "quiet": True})
    assert "username" in str(excinfo.value)
    assert "password" in str(excinfo.value)

    # Allowlisted keys pass silently.
    assert assert_public_only_options({"quiet": True, "format": "best"}) is None


def _recording_ydl_factory(recorded: list[dict]):
    """A ydl_factory stub that records the options it is invoked with."""

    class RecordingYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options
            recorded.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            return {"id": "video-1", "title": "Guarded", "webpage_url": source_url}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    return RecordingYDL


def test_extraction_seam_blocks_non_allowlisted_options_before_the_factory(monkeypatch) -> None:  # noqa: ANN001
    """The seam re-checks options and refuses to invoke the factory at all."""
    recorded: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_recording_ydl_factory(recorded))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    monkeypatch.setattr(service, "build_base_options", lambda: {"cookiefile": "/tmp/x"})

    with pytest.raises(PublicOnlyOptionsError, match="cookiefile"):
        service.preview("https://example.com/seam-guard-preview")

    assert recorded == [], "the factory must never be invoked with options outside the allowlist"


def test_download_seam_blocks_non_allowlisted_options_before_the_factory(monkeypatch) -> None:  # noqa: ANN001
    recorded: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_recording_ydl_factory(recorded))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    monkeypatch.setattr(
        service,
        "build_base_options",
        lambda: {"cookiesfrombrowser": ("firefox", "default", None, None)},
    )

    with pytest.raises(PublicOnlyOptionsError, match="cookiesfrombrowser"):
        service.download(
            "https://example.com/seam-guard-download",
            format_selection=FormatSelection(),
            output_profile=OutputProfile(),
            owner_user_id="user-1",
            output_root=settings.temp_root,
            progress_hooks=[],
            postprocessor_hooks=[],
        )

    assert recorded == []


def test_preview_and_download_use_the_same_format_plan() -> None:
    session = make_session()
    captured_options: list[dict] = []

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            return {
                "id": "video-1",
                "title": "Authenticated video",
                "webpage_url": source_url,
                "formats": [{"format_id": "251", "vcodec": "none", "acodec": "opus", "ext": "webm"}],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(session, ydl_factory=FakeYDL)
    selection = FormatSelection(preset="audio_only", output_container="webm")

    preview = service.preview("https://example.com/video-1", lazy_playlist=False, format_selection=selection)
    downloaded = service.download(
        "https://example.com/video-1",
        format_selection=selection,
        output_profile=OutputProfile(),
        owner_user_id="user-1",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
    )

    assert preview.title == "Authenticated video"
    assert downloaded.info["id"] == "video-1"
    assert [options["format"] for options in captured_options] == ["bestaudio/best", "bestaudio/best"]


def test_remote_playback_resolution_is_shared_for_a_short_while() -> None:
    captured_options: list[dict] = []
    extraction_count = 0

    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            captured_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            nonlocal extraction_count
            extraction_count += 1
            return {
                "id": "protected-video",
                "title": f"Resolution {extraction_count}",
                "webpage_url": source_url,
                "formats": [{"format_id": "18", "url": f"https://media.example/{extraction_count}"}],
            }

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    service = YtDlpService(make_session(), ydl_factory=FakeYDL)

    first = service.resolve_remote_playback("https://example.com/protected")
    second = service.resolve_remote_playback("https://example.com/protected")

    # Members watching one stream share one extraction (2.6.1); the signed-URL expiry bounds it (test_youtube_request_budget).
    assert first["title"] == second["title"] == "Resolution 1"
    assert extraction_count == 1
    assert all("download_archive" not in options for options in captured_options)


def test_preview_and_download_explain_the_same_format_error() -> None:
    session = make_session()

    class RejectingYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            raise yt_dlp.utils.DownloadError(
                "[youtube] video-2: Requested format is not available. Use --list-formats for a list of available formats"
            )

    service = YtDlpService(session, ydl_factory=RejectingYDL)
    selection = FormatSelection(preset="best")

    with pytest.raises(yt_dlp.utils.DownloadError) as preview_error:
        service.preview("https://example.com/video-2", lazy_playlist=False, format_selection=selection)
    with pytest.raises(yt_dlp.utils.DownloadError) as download_error:
        service.download(
            "https://example.com/video-2",
            format_selection=selection,
            output_profile=OutputProfile(),
            owner_user_id="user-1",
            output_root=settings.temp_root,
            progress_hooks=[],
            postprocessor_hooks=[],
        )

    assert "storyboard images" in str(preview_error.value)
    assert str(download_error.value) == str(preview_error.value)


def test_explain_download_error_for_storyboard_only_case() -> None:
    message = "ERROR: [youtube] 47fSGw0pNJc: Requested format is not available. Use --list-formats for a list of available formats"

    explained = YtDlpService.explain_download_error(message)

    assert "storyboard images" in explained
    assert "not a simple format-preset issue" in explained


def test_explain_download_error_for_n_challenge_case() -> None:
    message = "WARNING: [youtube] 47fSGw0pNJc: n challenge solving failed"

    explained = YtDlpService.explain_download_error(message)

    assert "client challenge" in explained
    assert "extracts anonymously" in explained




def test_downloads_prefer_guarded_https_formats_at_equal_resolution() -> None:
    """YouTube's best 1080p is sometimes HLS-only (refused by the download gate); an equal-resolution
    HTTPS format must win so the download goes through the guarded transport instead of failing."""
    options = YtDlpService(make_session())._build_acquisition_options(FormatSelection())
    formats = [
        {"format_id": "616", "protocol": "m3u8_native", "url": "https://example.com/616.m3u8", "ext": "mp4",
         "height": 1080, "width": 1920, "vcodec": "vp09.00.40.08", "acodec": "none", "tbr": 2760, "quality": 9, "source_preference": 99},
        {"format_id": "399", "protocol": "https", "url": "https://example.com/399", "ext": "mp4",
         "height": 1080, "width": 1920, "vcodec": "av01.0.08M.08", "acodec": "none", "tbr": 688, "quality": 9, "source_preference": -1},
        {"format_id": "251", "protocol": "https", "url": "https://example.com/251", "ext": "webm",
         "vcodec": "none", "acodec": "opus", "abr": 135, "quality": 3, "source_preference": -1},
    ]
    info = {"id": "vid", "title": "t", "extractor": "youtube", "extractor_key": "Youtube",
            "webpage_url": "https://www.youtube.com/watch?v=vid", "formats": formats,
            "_format_sort_fields": ("quality", "res", "fps", "hdr:12", "source", "vcodec", "channels", "acodec", "lang", "proto")}
    with yt_dlp.YoutubeDL({"quiet": True, "format": options["format"], "format_sort": options.get("format_sort", [])}) as ydl:
        chosen = ydl.process_ie_result(info, download=False)
    assert [f["format_id"] for f in chosen["requested_formats"]][0] == "399"


_YOUTUBE_SORT_FIELDS = ("quality", "res", "fps", "hdr:12", "source", "vcodec", "channels", "acodec", "lang", "proto")


def _resolve_editable(formats: list[dict]) -> tuple[dict, str | None]:
    """Run the editable preset through yt-dlp's real format ranking, then through Lumina's resolution state."""
    selection = FormatSelection(preset="best_editable", output_container="webm")
    options = YtDlpService(make_session())._build_acquisition_options(selection)
    info = {"id": "vid", "title": "t", "extractor": "youtube", "extractor_key": "Youtube",
            "webpage_url": "https://www.youtube.com/watch?v=vid", "formats": formats, "_format_sort_fields": _YOUTUBE_SORT_FIELDS}
    with yt_dlp.YoutubeDL({"quiet": True, "format": options["format"], "format_sort": options["format_sort"]}) as ydl:
        chosen = ydl.sanitize_info(ydl.process_ie_result(info, download=False))
    return chosen, FormatResolutionPolicy.resolve(selection, info=chosen).state.fallback_reason


def test_editable_preset_picks_https_h264_and_aac_over_higher_ranked_codecs() -> None:
    def video(format_id, protocol, vcodec, height, tbr, source_preference=-1):
        return {"format_id": format_id, "protocol": protocol, "url": f"https://example.com/{format_id}", "ext": "mp4",
                "height": height, "width": height * 16 // 9, "fps": 30, "vcodec": vcodec, "acodec": "none", "tbr": tbr,
                "quality": {1080: 9, 720: 8}[height], "source_preference": source_preference}

    formats = [
        video("270", "m3u8_native", "avc1.640028", 1080, 4500, source_preference=99),  # HLS "Premium": refused by the gate
        video("616", "m3u8_native", "vp09.00.40.08", 1080, 2760, source_preference=99),
        video("399", "https", "av01.0.08M.08", 1080, 688),
        video("248", "https", "vp09.00.40.08", 1080, 1500),
        video("137", "https", "avc1.640028", 1080, 2300),
        video("136", "https", "avc1.4d401f", 720, 1100),
        {"format_id": "18", "protocol": "https", "url": "https://example.com/18", "ext": "mp4", "height": 360, "width": 640,
         "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "tbr": 500, "quality": 6, "source_preference": -1},
        {"format_id": "251", "protocol": "https", "url": "https://example.com/251", "ext": "webm", "vcodec": "none",
         "acodec": "opus", "abr": 135, "asr": 48000, "audio_channels": 2, "quality": 3, "source_preference": -1},
        {"format_id": "140", "protocol": "https", "url": "https://example.com/140", "ext": "m4a", "vcodec": "none",
         "acodec": "mp4a.40.2", "abr": 129, "asr": 44100, "audio_channels": 2, "quality": 3, "source_preference": -1},
    ]
    chosen, fallback_reason = _resolve_editable(formats)
    assert [f["format_id"] for f in chosen["requested_formats"]] == ["137", "140"]
    assert fallback_reason is None
    # The container is forced to mp4 even when the member's default is webm.
    assert YtDlpService.resolve_output_container(FormatSelection(preset="best_editable", output_container="webm")) == "mp4"


def test_editable_preset_without_h264_still_downloads_and_records_the_fallback() -> None:
    formats = [
        {"format_id": "248", "protocol": "https", "url": "https://example.com/248", "ext": "webm", "height": 1080,
         "width": 1920, "vcodec": "vp09.00.40.08", "acodec": "none", "tbr": 1500, "quality": 9, "source_preference": -1},
        {"format_id": "251", "protocol": "https", "url": "https://example.com/251", "ext": "webm", "vcodec": "none",
         "acodec": "opus", "abr": 135, "quality": 3, "source_preference": -1},
    ]
    chosen, fallback_reason = _resolve_editable(formats)
    assert [f["format_id"] for f in chosen["requested_formats"]] == ["248", "251"]
    assert fallback_reason and fallback_reason.startswith("Not editable")


def _scripted_ydl_factory(recorded: list[dict], failures: int, fast_height: int = 1080):
    """Records options; the first ``failures`` extractions raise like a client YouTube refused, and a fast (JS-skipping)
    one carries formats up to ``fast_height`` (360 = refused quietly: only the web client's muxed format)."""

    class ScriptedYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options
            recorded.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            if len(recorded) <= failures:
                raise yt_dlp.utils.DownloadError("ERROR: [youtube] abc: Requested format is not available")
            height = fast_height if self.options.get("extractor_args") else 1080
            return {"id": "abc", "title": "Fast", "webpage_url": source_url, "extractor_key": "Youtube", "formats": [{"format_id": "f", "height": height}]}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    return ScriptedYDL


def test_youtube_preview_skips_the_player_js_challenge_and_falls_back_to_it(monkeypatch) -> None:  # noqa: ANN001
    """26-pp: solving the web client's JS challenge in node cost 1-2 s of every YouTube click, for one 360p format the
    player never picks (the visionos formats carry the whole ladder). A refused fast extraction retries in full."""
    from app.services.yt_dlp_service import _PREVIEW_CACHE

    _PREVIEW_CACHE.clear()
    recorded: list[dict] = []
    service = YtDlpService(make_session(), ydl_factory=_scripted_ydl_factory(recorded, failures=0))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    service.preview("https://www.youtube.com/watch?v=abcdefghijk")
    assert [options.get("extractor_args") for options in recorded] == [{"youtube": {"player_skip": ["js"]}}]

    _PREVIEW_CACHE.clear()
    recorded.clear()
    service = YtDlpService(make_session(), ydl_factory=_scripted_ydl_factory(recorded, failures=1))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    assert service.preview("https://youtu.be/abcdefghijk").title == "Fast"
    fast = {"youtube": {"player_skip": ["js"]}}
    assert [options.get("extractor_args") for options in recorded] == [fast, None]  # one fast try, then in full

    _PREVIEW_CACHE.clear()
    recorded.clear()
    service = YtDlpService(make_session(), ydl_factory=_scripted_ydl_factory(recorded, failures=0))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    service.preview("https://vimeo.com/123")  # other providers extract once, as before
    assert [options.get("extractor_args") for options in recorded] == [None]

    _PREVIEW_CACHE.clear()
    recorded.clear()
    service = YtDlpService(make_session(), ydl_factory=_scripted_ydl_factory(recorded, failures=0, fast_height=360))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    service.preview("https://www.youtube.com/watch?v=abcdefghijk")  # only 360p: the full extraction decides
    assert [options.get("extractor_args") for options in recorded] == [fast, None]

    _PREVIEW_CACHE.clear()
    recorded.clear()
    service = YtDlpService(make_session(), ydl_factory=_scripted_ydl_factory(recorded, failures=0))
    monkeypatch.setattr(service, "validate_source_url", lambda source_url: source_url)
    service.preview("https://vimeo.com/123")
    assert [options.get("extractor_args") for options in recorded] == [None]


def test_only_the_fixed_player_skip_extractor_argument_is_public() -> None:
    assert assert_public_only_options({"extractor_args": {"youtube": {"player_skip": ["js"]}}}) is None
    for smuggled in ({"youtube": {"po_token": ["web+x"]}}, {"youtube": {"player_skip": ["js"], "visitor_data": ["v"]}}, {"generic": {"impersonate": ["chrome"]}}):
        with pytest.raises(PublicOnlyOptionsError, match="extractor_args"):
            assert_public_only_options({"extractor_args": smuggled})


def test_ffmpeg_postprocessors_only_read_local_media(tmp_path) -> None:  # noqa: ANN001
    """A downloaded playlist must not make yt-dlp's ffmpeg read another member's file (or the LAN)."""
    import shutil
    import subprocess

    from yt_dlp.postprocessor import FFmpegExtractAudioPP
    from yt_dlp.utils import PostProcessingError

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg is not installed")
    options = YtDlpService(make_session()).build_download_options(
        format_selection=FormatSelection(preset="audio_only", extract_audio=True), output_profile=OutputProfile(),
        owner_user_id="user-1", output_root=settings.temp_root, progress_hooks=[], postprocessor_hooks=[],
    )
    assert set(options) <= PUBLIC_OPTION_ALLOWLIST
    assert_public_only_options(options)
    secret = tmp_path / "other" / "secret.mp4"
    secret.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "aac", str(secret)], check=True)
    mine = tmp_path / "mine"
    mine.mkdir()
    evil = mine / "evil.m3u"
    evil.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:1,\nfile://{secret}\n#EXT-X-ENDLIST\n")
    honest = mine / "honest.m4a"
    shutil.copy(secret, honest)
    params = {"quiet": True, "postprocessor_args": options["postprocessor_args"]}
    with yt_dlp.YoutubeDL(params) as ydl:
        pp = FFmpegExtractAudioPP(ydl, preferredcodec="mp3")
        with pytest.raises(PostProcessingError):
            pp.run({"filepath": str(evil), "ext": "m3u"})
        assert not (mine / "evil.mp3").exists()
        pp.run({"filepath": str(honest), "ext": "m4a"})  # real media still converts
        assert (mine / "honest.mp3").stat().st_size > 0
