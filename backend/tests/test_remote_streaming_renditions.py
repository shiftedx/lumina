from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.remote_streaming import (
    BrowserCapabilities,
    ByteRange,
    RemoteStreamingService,
    StreamNotFoundError,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
)


def rendition_info(*, expires_at: float = 5_000) -> dict:
    def video(
        format_id: str,
        *,
        height: int,
        width: int,
        ext: str,
        vcodec: str,
        acodec: str,
        fps: float,
        tbr: float,
    ) -> dict:
        return {
            "format_id": format_id,
            "url": f"https://signed.example/{format_id}?secret=private",
            "url_expiry": expires_at,
            "protocol": "https",
            "ext": ext,
            "vcodec": vcodec,
            "acodec": acodec,
            "width": width,
            "height": height,
            "fps": fps,
            "tbr": tbr,
            "filesize": 20,
            "http_headers": {"Authorization": "private-header"},
        }

    return {
        "formats": [
            video("muxed-720", height=720, width=1280, ext="mp4", vcodec="avc1.4d401f", acodec="mp4a.40.2", fps=30, tbr=1_800),
            video("muxed-1080", height=1080, width=1920, ext="mp4", vcodec="avc1.640028", acodec="mp4a.40.2", fps=60, tbr=4_500),
            video("muxed-1440", height=1440, width=2560, ext="webm", vcodec="vp09.00.40.08", acodec="opus", fps=60, tbr=9_000),
            video("muxed-2160", height=2160, width=3840, ext="webm", vcodec="av01.0.12M.08", acodec="opus", fps=60, tbr=18_000),
            video("hevc-2160", height=2160, width=3840, ext="mp4", vcodec="hvc1.2.4.L153", acodec="mp4a.40.2", fps=60, tbr=16_000),
        ]
    }


ALL_TEST_PROFILES = BrowserCapabilities(
    frozenset({"mp4-avc-aac", "webm-vp9-opus", "webm-av1-opus", "webm-opus"})
)


@dataclass
class RecordingReader:
    payload: bytes = b"0123456789abcdefghij"

    def __post_init__(self) -> None:
        self.opens: list[tuple[str, ByteRange | None]] = []

    def open(self, track, byte_range: ByteRange | None, timeout_seconds=None) -> UpstreamMediaResponse:  # noqa: ANN001
        del timeout_seconds
        self.opens.append((track.format_id, byte_range))
        start = byte_range.start if byte_range and byte_range.start is not None else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(self.payload) - 1
        body = self.payload[start : end + 1]
        return UpstreamMediaResponse(
            206 if byte_range else 200,
            {
                "Content-Type": track.content_type,
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}" if byte_range else "",
                "Set-Cookie": "private-cookie",
            },
            [body],
        )


class UnexpectedResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        raise AssertionError((source_url, owner_user_id))


def register(service: RemoteStreamingService, info: dict | None = None):  # noqa: ANN201
    return service.register(
        owner_user_id="owner",
        source_url="https://source.example/video",
        info=info or rendition_info(),
        browser_capabilities=ALL_TEST_PROFILES,
    )


def test_descriptor_exposes_only_opaque_browser_compatible_renditions_through_2160p() -> None:
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=RecordingReader(),
        token_factory=lambda: "opaque-stream",
        rendition_token_factory=iter(("r720", "r1080", "r1440", "r2160")).__next__,
        clock=lambda: 1_000,
    )

    playback = register(service)

    assert playback.status == "ready"
    assert playback.selected_rendition_id == "r2160"
    assert playback.playback_url == "/api/remote-streams/opaque-stream/renditions/r2160/content"
    assert [(item.height, item.video_codec, item.audio_codec) for item in playback.renditions] == [
        (720, "avc", "aac"),
        (1080, "avc", "aac"),
        (1440, "vp9", "opus"),
        (2160, "av1", "opus"),
    ]
    assert playback.renditions[-1].display_label == "4K · 60 fps"
    assert playback.renditions[-1].width == 3840
    assert playback.renditions[-1].frame_rate == 60
    assert playback.renditions[-1].bitrate_kbps == 18_000
    assert "hevc-2160" not in repr(playback)
    assert "signed.example" not in repr(playback)
    assert "private-header" not in repr(playback)


def test_browser_capabilities_filter_codec_container_combinations() -> None:
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=RecordingReader(),
        token_factory=lambda: "filtered-stream",
        rendition_token_factory=iter(("r720", "r1080")).__next__,
        clock=lambda: 1_000,
    )

    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/video",
        info=rendition_info(),
        browser_capabilities=BrowserCapabilities(frozenset({"mp4-avc-aac"})),
    )

    assert [item.height for item in playback.renditions] == [720, 1080]
    assert playback.selected_rendition_id == "r1080"


def test_provider_shaped_split_ladder_beats_the_lone_low_quality_muxed_stream() -> None:
    """YouTube commonly exposes one 360p mux plus higher split AVC/AAC tracks."""

    def track(format_id: str, *, width: int | None, height: int | None, vcodec: str, acodec: str, ext: str = "mp4") -> dict:
        return {
            "format_id": format_id,
            "url": f"https://signed.example/{format_id}?secret=private",
            "protocol": "https",
            "ext": ext,
            "vcodec": vcodec,
            "acodec": acodec,
            "width": width,
            "height": height,
            "fps": 30,
            "tbr": 1_000,
        }

    info = {
        "formats": [
            track("muxed-360", width=640, height=360, vcodec="avc1.42001E", acodec="mp4a.40.2"),
            track("muxed-360-cropped", width=640, height=338, vcodec="avc1.4d401e", acodec="mp4a.40.2"),
            track("video-360", width=640, height=338, vcodec="avc1.4d401e", acodec="none"),
            track("video-480", width=854, height=450, vcodec="avc1.4d401e", acodec="none"),
            track("video-480-nominal", width=854, height=480, vcodec="avc1.64001f", acodec="none"),
            track("video-720", width=1280, height=676, vcodec="avc1.64001f", acodec="none"),
            track("video-1080", width=1920, height=1012, vcodec="avc1.640028", acodec="none"),
            track("audio", width=None, height=None, vcodec="none", acodec="mp4a.40.2", ext="m4a"),
        ]
    }

    class UnexpectedPackager:
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            raise AssertionError((stream_id, generation, video, audio))

        def close(self, stream_id):  # noqa: ANN001
            del stream_id

        def close_generation(self, stream_id, generation):  # noqa: ANN001
            del stream_id, generation

    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=RecordingReader(),
        hls_packager=UnexpectedPackager(),
        token_factory=lambda: "provider-shaped-stream",
        rendition_token_factory=iter(("r360", "r480", "r720", "r1080")).__next__,
        clock=lambda: 1_000,
    )

    playback = service.register(
        owner_user_id="owner",
        source_url="https://www.youtube.com/watch?v=provider-shaped",
        info=info,
        browser_capabilities=BrowserCapabilities(frozenset({"mp4-avc-aac", "m4a-aac"})),
    )

    assert playback.transport == "hls"
    assert playback.selected_rendition_id == "r1080"
    assert [item.display_label for item in playback.renditions] == ["360p", "480p", "720p", "1080p"]
    assert len(playback.renditions) > 1


def test_default_excludes_split_renditions_that_cannot_fit_the_packager_budget() -> None:
    class BudgetPackager:
        def can_package(self, video, audio):  # noqa: ANN001
            video_size = video.content_length or video.estimated_content_length or 0
            audio_size = audio.content_length or audio.estimated_content_length or 0
            return video_size + audio_size <= 120

        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            raise AssertionError((stream_id, generation, video, audio))

        def close(self, stream_id):  # noqa: ANN001
            del stream_id

        def close_generation(self, stream_id, generation):  # noqa: ANN001
            del stream_id, generation

    def candidate(format_id: str, *, height: int | None, vcodec: str, acodec: str, size: int) -> dict:
        return {
            "format_id": format_id,
            "url": f"https://signed.example/{format_id}",
            "protocol": "https",
            "ext": "m4a" if vcodec == "none" else "mp4",
            "vcodec": vcodec,
            "acodec": acodec,
            "height": height,
            "filesize_approx": size,
        }

    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=RecordingReader(),
        hls_packager=BudgetPackager(),
        token_factory=lambda: "bounded-default",
        rendition_token_factory=iter(("r360", "r480")).__next__,
        clock=lambda: 1_000,
    )

    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/long-video",
        info={"formats": [
            candidate("muxed-360", height=360, vcodec="avc1.42001e", acodec="mp4a.40.2", size=40),
            candidate("video-480", height=480, vcodec="avc1.4d401e", acodec="none", size=80),
            candidate("video-1080", height=1080, vcodec="avc1.640028", acodec="none", size=200),
            candidate("audio", height=None, vcodec="none", acodec="mp4a.40.2", size=20),
        ]},
        browser_capabilities=BrowserCapabilities(frozenset({"mp4-avc-aac", "m4a-aac"})),
    )

    assert [rendition.display_label for rendition in playback.renditions] == ["360p", "480p"]
    assert playback.selected_rendition_id == "r480"
    assert playback.transport == "hls"


def test_manual_selection_serves_only_the_exact_active_rendition_and_preserves_it_on_refresh() -> None:
    class ReorderedResolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            assert (source_url, owner_user_id) == (
                "https://source.example/video",
                "owner",
            )
            refreshed = rendition_info(expires_at=9_000)
            refreshed["formats"].reverse()
            return refreshed

    reader = RecordingReader()
    tokens = iter(("r720", "r1080", "r1440", "r2160"))
    service = RemoteStreamingService(
        resolver=ReorderedResolver(),
        reader=reader,
        token_factory=lambda: "select-stream",
        rendition_token_factory=tokens.__next__,
        clock=lambda: 1_000,
    )
    initial = register(service)
    selected_1440 = next(item for item in initial.renditions if item.height == 1440)
    old_2160 = initial.selected_rendition_id

    selected = service.select_rendition("owner", initial.stream_id, selected_1440.rendition_id)
    refreshed = service.refresh("owner", initial.stream_id)
    response = service.serve_content(
        "owner",
        initial.stream_id,
        "bytes=4-7",
        rendition_id=selected_1440.rendition_id,
    )

    assert selected.selected_rendition_id == selected_1440.rendition_id
    assert refreshed.selected_rendition_id == selected_1440.rendition_id
    assert refreshed.playback_url.endswith(f"/{selected_1440.rendition_id}/content")
    assert b"".join(response.body) == b"4567"
    assert reader.opens == [("muxed-1440", ByteRange(4, 7))]
    with pytest.raises(StreamNotFoundError):
        service.serve_content("owner", initial.stream_id, rendition_id=old_2160)
    with pytest.raises(StreamNotFoundError):
        service.select_rendition("other", initial.stream_id, selected_1440.rendition_id)


def test_refresh_never_silently_downgrades_a_missing_manual_rendition() -> None:
    class LowResolutionResolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            del source_url, owner_user_id
            info = rendition_info(expires_at=9_000)
            info["formats"] = [item for item in info["formats"] if item["height"] <= 1080]
            return info

    reader = RecordingReader()
    service = RemoteStreamingService(
        resolver=LowResolutionResolver(),
        reader=reader,
        token_factory=lambda: "no-downgrade-stream",
        rendition_token_factory=iter(("r720", "r1080", "r1440", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    initial = register(service)
    assert initial.selected_rendition_id is not None
    service.select_rendition("owner", initial.stream_id, initial.selected_rendition_id)

    refreshed = service.refresh("owner", initial.stream_id)

    assert refreshed.status == "unsupported"
    assert refreshed.selected_rendition_id is None
    assert refreshed.fallback_code == "selected_rendition_unavailable"
    assert [item.height for item in refreshed.renditions] == [720, 1080]
    assert reader.opens == []


def test_incremental_transport_bounds_each_response_and_concurrent_active_reads() -> None:
    reader = RecordingReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        token_factory=lambda: "budget-stream",
        rendition_token_factory=iter(("r720", "r1080", "r1440", "r2160")).__next__,
        clock=lambda: 1_000,
        max_bytes_per_response=4,
        max_concurrent_reads_global=1,
        max_concurrent_reads_per_user=1,
    )
    playback = register(service)
    active = playback.selected_rendition_id
    assert active is not None

    first = service.serve_content("owner", playback.stream_id, rendition_id=active)
    with pytest.raises(UnsupportedPlaybackError, match="active media response"):
        service.serve_content("owner", playback.stream_id, rendition_id=active)
    assert b"".join(first.body) == b"0123"

    second = service.serve_content("owner", playback.stream_id, "bytes=8-19", rendition_id=active)
    assert b"".join(second.body) == b"89ab"
    assert reader.opens == [
        ("muxed-2160", ByteRange(0, 3)),
        ("muxed-2160", ByteRange(8, 11)),
    ]


def test_split_2160p_is_not_sent_through_full_download_hls_packaging() -> None:
    class UnexpectedPackager:
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            raise AssertionError((stream_id, generation, video, audio))

        def close(self, stream_id):  # noqa: ANN001
            del stream_id

        def close_generation(self, stream_id, generation):  # noqa: ANN001
            del stream_id, generation

    info = {
        "formats": [
            {
                "format_id": "video-2160",
                "url": "https://signed.example/video",
                "protocol": "https",
                "ext": "webm",
                "vcodec": "vp09.00.50.08",
                "acodec": "none",
                "height": 2160,
            },
            {
                "format_id": "audio",
                "url": "https://signed.example/audio",
                "protocol": "https",
                "ext": "webm",
                "vcodec": "none",
                "acodec": "opus",
            },
        ]
    }
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=RecordingReader(),
        hls_packager=UnexpectedPackager(),
        token_factory=lambda: "split-2160",
        clock=lambda: 1_000,
    )

    playback = register(service, info)

    assert playback.status == "unsupported"
    assert playback.fallback_code == "incremental_mux_unavailable"
    assert playback.renditions == ()
