from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

import pytest

from app.services.remote_streaming import (
    ByteRange,
    HlsAsset,
    HlsPresentation,
    RemoteStreamingService,
    RangeNotSatisfiableError,
    StreamNotFoundError,
    UnsupportedPlaybackError,
    UpstreamTrackExpiredError,
    UpstreamMediaResponse,
    parse_range_header,
    redact_preview_info,
)


@dataclass
class FakeReader:
    payload: bytes = b"progressive-media"

    def open(self, track, byte_range: ByteRange | None) -> UpstreamMediaResponse:  # noqa: ANN001
        start = byte_range.start if byte_range else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(self.payload) - 1
        body = self.payload[start : end + 1]
        return UpstreamMediaResponse(
            status_code=206 if byte_range else 200,
            headers={
                "content-type": track.content_type,
                "content-length": str(len(body)),
                "content-range": f"bytes {start}-{end}/{len(self.payload)}" if byte_range else "",
                "accept-ranges": "bytes",
                "set-cookie": "never-public=secret",
                "x-upstream-debug": track.url,
            },
            body=[body],
        )


class NoopResolver:
    def resolve(self, source_url: str, owner_user_id: str):  # noqa: ANN001
        del owner_user_id
        raise AssertionError(f"unexpected refresh for {source_url}")


class FakeHlsPackager:
    def __init__(self) -> None:
        self.closed: list[str] = []
        self.closed_generations: list[tuple[str, int]] = []

    def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
        assert video.acodec == "none"
        assert audio.vcodec == "none"
        return HlsPresentation(
            manifest=b"#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\nsegment-1.m4s\n",
            assets={
                "init.mp4": HlsAsset(b"muxed-init", "video/mp4"),
                "segment-1.m4s": HlsAsset(b"muxed-segment", "video/iso.segment"),
            },
        )

    def close(self, stream_id: str) -> None:
        self.closed.append(stream_id)

    def close_generation(self, stream_id: str, generation: int) -> None:
        self.closed_generations.append((stream_id, generation))


def progressive_info() -> dict:
    return {
        "id": "video-1",
        "title": "Progressive fixture",
        "webpage_url": "https://source.example/watch/1",
        "formats": [
            {
                "format_id": "18",
                "url": "https://signed.example/video.mp4?expire=4102444800&signature=private",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "height": 720,
                "filesize": len(b"progressive-media"),
                "http_headers": {"Cookie": "private-cookie"},
            }
        ],
    }


def collect(chunks: Iterable[bytes]) -> bytes:
    return b"".join(chunks)


def test_progressive_stream_is_opaque_owner_bound_and_range_capable() -> None:
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        token_factory=lambda: "opaque-stream-id",
        clock=lambda: 1_000.0,
    )

    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/watch/1",
        info=progressive_info(),
    )

    assert playback.status == "ready"
    assert playback.transport == "progressive"
    assert playback.media_kind == "video"
    assert playback.has_video is True
    assert playback.has_audio is True
    assert playback.seekable is True
    assert playback.playback_url == "/api/remote-streams/opaque-stream-id/content"
    assert "signed.example" not in repr(playback)
    assert "saved-sign-in-1" not in repr(playback)

    response = service.serve_content("user-1", playback.stream_id, "bytes=2-6")
    assert response.status_code == 206
    assert response.headers == {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Length": "5",
        "Content-Range": "bytes 2-6/17",
        "Content-Type": "video/mp4",
        "X-Content-Type-Options": "nosniff",
    }
    assert collect(response.body) == b"ogres"
    assert "secret" not in repr(response)
    assert "signed.example" not in repr(response)


def test_progressive_stream_forces_an_accepted_media_content_type() -> None:
    class HtmlClaimingReader:
        def open(self, track, byte_range):  # noqa: ANN001
            del track, byte_range
            return UpstreamMediaResponse(200, {"content-type": "text/html", "content-length": "5"}, [b"media"])

    info = progressive_info()
    info["formats"][0]["mime_type"] = "text/html"
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=HtmlClaimingReader(), token_factory=lambda: "typed-stream", clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/typed", info=info)
    response = service.serve_content("owner", playback.stream_id)

    assert response.headers["Content-Type"] == "video/mp4"
    assert collect(response.body) == b"media"


def test_split_audio_video_is_exposed_as_backend_owned_hls() -> None:
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "split-stream",
        clock=lambda: 1_000.0,
    )
    info = {
        "formats": [
            {
                "format_id": "137",
                "url": "https://signed.example/video-only",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
            },
            {
                "format_id": "140",
                "url": "https://signed.example/audio-only",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
            },
        ]
    }

    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/watch/split",
        info=info,
    )

    assert playback.transport == "hls"
    assert playback.has_video is True
    assert playback.has_audio is True
    assert playback.playback_url == "/api/remote-streams/split-stream/hls/1/manifest.m3u8"
    manifest = service.serve_hls("user-1", playback.stream_id, 1, "manifest.m3u8")
    assert manifest.status_code == 200
    assert manifest.headers["Content-Type"] == "application/vnd.apple.mpegurl"
    manifest_body = collect(manifest.body)
    assert manifest_body.startswith(b"#EXTM3U")
    assert b'/api/remote-streams/split-stream/hls/1/init.mp4' in manifest_body
    assert b'/api/remote-streams/split-stream/hls/1/segment-1.m4s' in manifest_body
    segment = service.serve_hls("user-1", playback.stream_id, 1, "segment-1.m4s")
    assert collect(segment.body) == b"muxed-segment"


def test_split_hls_quality_selection_advances_the_generation_without_a_downgrade() -> None:
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=FakeHlsPackager(),
        token_factory=lambda: "split-quality-stream",
        rendition_token_factory=iter(("r720", "r1080")).__next__,
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/watch/split-quality",
        info={
            "formats": [
                {
                    "format_id": "video-720",
                    "url": "https://signed.example/video-720",
                    "protocol": "https",
                    "ext": "mp4",
                    "vcodec": "avc1.4d401f",
                    "acodec": "none",
                    "width": 1280,
                    "height": 720,
                },
                {
                    "format_id": "video-1080",
                    "url": "https://signed.example/video-1080",
                    "protocol": "https",
                    "ext": "mp4",
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "format_id": "audio-aac",
                    "url": "https://signed.example/audio-aac",
                    "protocol": "https",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                },
            ]
        },
    )

    assert playback.playback_url == "/api/remote-streams/split-quality-stream/hls/1/manifest.m3u8"
    assert [rendition.display_label for rendition in playback.renditions] == ["720p", "1080p"]
    selected_720 = next(rendition.rendition_id for rendition in playback.renditions if rendition.height == 720)

    selected = service.select_rendition("user-1", playback.stream_id, selected_720)

    assert selected.transport == "hls"
    assert selected.selected_rendition_id == selected_720
    assert selected.playback_url == "/api/remote-streams/split-quality-stream/hls/2/manifest.m3u8"
    assert collect(service.serve_hls("user-1", selected.stream_id, 2, "manifest.m3u8").body).startswith(b"#EXTM3U")


def test_file_backed_hls_asset_supports_byte_ranges(tmp_path) -> None:  # noqa: ANN001
    segment_path = tmp_path / "segment-1.m4s"
    segment_path.write_bytes(b"0123456789")

    class FilePackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            del stream_id, generation, video, audio
            return HlsPresentation(
                b"#EXTM3U\nsegment-1.m4s\n",
                {"segment-1.m4s": HlsAsset(segment_path, "video/iso.segment")},
            )

    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=FilePackager(),
        token_factory=lambda: "file-hls-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/split",
        info={
            "formats": [
                {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
                {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
            ]
        },
    )

    response = service.serve_hls("user-1", playback.stream_id, 1, "segment-1.m4s", "bytes=2-5")

    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert collect(response.body) == b"2345"


def test_hls_assets_share_the_active_media_read_budget() -> None:
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=FakeHlsPackager(),
        token_factory=lambda: "bounded-hls", clock=lambda: 1_000.0,
        max_concurrent_reads_global=1, max_concurrent_reads_per_user=1,
    )
    playback = service.register(
        owner_user_id="owner", source_url="https://source.example/split",
        info={"formats": [
            {"format_id": "137", "url": "https://signed.example/video", "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
            {"format_id": "140", "url": "https://signed.example/audio", "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ]},
    )
    manifest = service.serve_hls("owner", playback.stream_id, 1, "manifest.m3u8")
    collect(manifest.body)
    first = service.serve_hls("owner", playback.stream_id, 1, "segment-1.m4s")
    with pytest.raises(UnsupportedPlaybackError, match="active media responses"):
        service.serve_hls("owner", playback.stream_id, 1, "init.mp4")
    first.close()
    assert collect(service.serve_hls("owner", playback.stream_id, 1, "init.mp4").body) == b"muxed-init"


def test_audio_only_is_progressive_and_video_without_audio_is_explicitly_unsupported() -> None:
    tokens = iter(["audio-stream", "silent-video-stream"])
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(payload=b"audio-media"),
        token_factory=lambda: next(tokens),
        clock=lambda: 1_000.0,
    )
    audio = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/audio",
        info={
            "formats": [{
                "format_id": "140",
                "url": "https://signed.example/audio",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": len(b"audio-media"),
            }]
        },
    )
    assert audio.status == "ready"
    assert audio.media_kind == "audio"
    assert audio.content_type == "audio/mp4"
    assert audio.has_video is False
    assert audio.has_audio is True

    silent_video = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/silent",
        info={
            "formats": [{
                "format_id": "137",
                "url": "https://signed.example/silent",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
            }]
        },
    )
    assert silent_video.status == "unsupported"
    assert silent_video.playback_url is None
    assert silent_video.has_video is False
    assert silent_video.has_audio is False
    assert silent_video.fallback_code == "no_audio"
    assert silent_video.fallback_message == "This source cannot be streamed here yet. You can still download it."


def test_registry_hides_foreign_streams_and_cleans_expired_resources() -> None:
    now = [1_000.0]
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "owned-stream",
        clock=lambda: now[0],
        idle_ttl_seconds=30,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/watch/1",
        info=progressive_info(),
    )

    with pytest.raises(StreamNotFoundError, match="not found"):
        service.describe("other-user", playback.stream_id)
    with pytest.raises(StreamNotFoundError, match="not found"):
        service.serve_content("other-user", playback.stream_id)

    now[0] += 31
    assert service.expire_idle() == 1
    assert packager.closed == ["owned-stream"]
    with pytest.raises(StreamNotFoundError, match="not found"):
        service.describe("owner", playback.stream_id)


def test_registry_enforces_global_and_per_user_resource_limits() -> None:
    tokens = iter(["owner-one", "owner-two", "other-one"])
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        token_factory=lambda: next(tokens),
        clock=lambda: 1_000.0,
        max_streams_global=2,
        max_streams_per_user=1,
    )
    service.register(owner_user_id="owner", source_url="https://source.example/one", info=progressive_info())

    with pytest.raises(UnsupportedPlaybackError, match="active playback sessions"):
        service.register(owner_user_id="owner", source_url="https://source.example/two", info=progressive_info())

    service.register(owner_user_id="other", source_url="https://source.example/other", info=progressive_info())
    with pytest.raises(UnsupportedPlaybackError, match="server is at capacity"):
        service.register(owner_user_id="third", source_url="https://source.example/third", info=progressive_info())


def test_packaging_concurrency_is_bounded_globally() -> None:
    started = threading.Event()
    finish = threading.Event()

    class BlockingPackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            started.set()
            assert finish.wait(timeout=2)
            return super().prepare(stream_id, generation, video, audio)

    tokens = iter(["first-split", "second-split"])
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=BlockingPackager(),
        token_factory=lambda: next(tokens),
        clock=lambda: 1_000.0,
        max_concurrent_packaging_global=1,
        max_concurrent_packaging_per_user=1,
    )
    split = {"formats": [
        {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
        {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
    ]}
    first = service.register(owner_user_id="owner-1", source_url="https://source.example/one", info=split)
    second = service.register(owner_user_id="owner-2", source_url="https://source.example/two", info=split)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.serve_hls, "owner-1", first.stream_id, 1, "manifest.m3u8")
        assert started.wait(timeout=2)
        with pytest.raises(UnsupportedPlaybackError, match="already preparing"):
            service.serve_hls("owner-2", second.stream_id, 1, "manifest.m3u8")
        finish.set()
        response = future.result(timeout=2)
        response.close()


def test_split_track_expiry_refreshes_once_and_redirects_to_new_generation() -> None:
    prepared: list[int] = []

    class ExpiringPackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            prepared.append(generation)
            if generation == 1:
                raise UpstreamTrackExpiredError("expired")
            return super().prepare(stream_id, generation, video, audio)

    class FreshSplitResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            del source_url, owner_user_id
            self.calls += 1
            return {"formats": [
                {"format_id": "137-new", "url": "https://signed.example/new-video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
                {"format_id": "140-new", "url": "https://signed.example/new-audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
            ]}

    resolver = FreshSplitResolver()
    service = RemoteStreamingService(
        resolver=resolver,
        reader=FakeReader(),
        hls_packager=ExpiringPackager(),
        token_factory=lambda: "refresh-split",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/split",
        info={"formats": [
            {"format_id": "137", "url": "https://signed.example/old-video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
            {"format_id": "140", "url": "https://signed.example/old-audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ]},
    )

    response = service.serve_hls("owner", playback.stream_id, 1, "manifest.m3u8")

    assert response.status_code == 307
    assert response.headers["Location"] == "/api/remote-streams/refresh-split/hls/2/manifest.m3u8"
    response.close()
    assert resolver.calls == 1
    assert prepared == [1, 2]


def test_generation_retention_keeps_only_current_and_one_grace_without_deleting_active_response() -> None:
    split = {"formats": [
        {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
        {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
    ]}

    class SplitResolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            del source_url, owner_user_id
            return split

    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=SplitResolver(), reader=FakeReader(), hls_packager=packager,
        token_factory=lambda: "bounded-generations", clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/split", info=split)
    active_generation_one = service.serve_hls("owner", playback.stream_id, 1, "manifest.m3u8")

    for generation in (2, 3):
        refreshed = service.refresh("owner", playback.stream_id)
        assert f"/hls/{generation}/" in (refreshed.playback_url or "")
        response = service.serve_hls("owner", playback.stream_id, generation, "manifest.m3u8")
        response.close()

    assert packager.closed_generations == []
    active_generation_one.close()
    assert packager.closed_generations == [("bounded-generations", 1)]

    service.refresh("owner", playback.stream_id)
    generation_four = service.serve_hls("owner", playback.stream_id, 4, "manifest.m3u8")
    generation_four.close()
    assert packager.closed_generations == [("bounded-generations", 1), ("bounded-generations", 2)]


def test_owner_release_removes_session_and_closes_packager_resources() -> None:
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "released-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/watch/1",
        info=progressive_info(),
    )

    with pytest.raises(StreamNotFoundError, match="not found"):
        service.release("other-user", playback.stream_id)
    assert packager.closed == []

    service.release("owner", playback.stream_id)

    assert packager.closed == ["released-stream"]
    with pytest.raises(StreamNotFoundError, match="not found"):
        service.describe("owner", playback.stream_id)


def test_release_defers_cleanup_until_active_progressive_response_closes() -> None:
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "leased-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/watch/1", info=progressive_info())
    response = service.serve_content("owner", playback.stream_id)

    service.release("owner", playback.stream_id)

    assert packager.closed == []
    with pytest.raises(StreamNotFoundError):
        service.describe("owner", playback.stream_id)
    response.close()
    assert packager.closed == ["leased-stream"]


def test_released_progressive_lease_holds_capacity_until_response_cleanup() -> None:
    tokens = iter(["leased-capacity", "replacement"])
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=FakeHlsPackager(),
        token_factory=lambda: next(tokens), clock=lambda: 1_000.0,
        max_streams_global=1, max_streams_per_user=1,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/one", info=progressive_info())
    response = service.serve_content("owner", playback.stream_id)
    service.release("owner", playback.stream_id)

    with pytest.raises(UnsupportedPlaybackError, match="capacity"):
        service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())

    response.close()
    replacement = service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())
    assert replacement.stream_id == "replacement"


def test_expiry_never_cleans_an_active_hls_preparation_or_response() -> None:
    now = [1_000.0]
    preparing = threading.Event()
    continue_preparing = threading.Event()

    class BlockingPackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            preparing.set()
            assert continue_preparing.wait(timeout=2)
            return super().prepare(stream_id, generation, video, audio)

    packager = BlockingPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "active-hls-stream",
        clock=lambda: now[0],
        idle_ttl_seconds=30,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/split",
        info={"formats": [
            {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
            {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ]},
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.serve_hls, "owner", playback.stream_id, 1, "manifest.m3u8")
        assert preparing.wait(timeout=2)
        now[0] += 31
        assert service.expire_idle() == 1
        assert packager.closed == []
        continue_preparing.set()
        response = future.result(timeout=2)
        assert packager.closed == []
        response.close()

    assert packager.closed == ["active-hls-stream"]


def test_expired_hls_lease_holds_capacity_and_close_all_tracks_retired_resources() -> None:
    now = [1_000.0]
    tokens = iter(["leased-hls-capacity", "hls-replacement"])
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=packager,
        token_factory=lambda: next(tokens), clock=lambda: now[0], idle_ttl_seconds=30,
        max_streams_global=1, max_streams_per_user=1,
    )
    split = {"formats": [
        {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
        {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
    ]}
    playback = service.register(owner_user_id="owner", source_url="https://source.example/split", info=split)
    response = service.serve_hls("owner", playback.stream_id, 1, "manifest.m3u8")
    now[0] += 31
    assert service.expire_idle() == 1

    with pytest.raises(UnsupportedPlaybackError, match="capacity"):
        service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())
    assert service.close_all() == 0
    assert packager.closed == []

    response.close()
    assert packager.closed == ["leased-hls-capacity"]
    replacement = service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())
    assert replacement.stream_id == "hls-replacement"


def test_close_all_is_idempotent_but_active_lease_keeps_capacity_until_cleanup() -> None:
    tokens = iter(["shutdown-lease", "after-shutdown"])
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=FakeHlsPackager(),
        token_factory=lambda: next(tokens), clock=lambda: 1_000.0, max_streams_global=1,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/one", info=progressive_info())
    response = service.serve_content("owner", playback.stream_id)

    assert service.close_all() == 1
    assert service.close_all() == 0
    with pytest.raises(UnsupportedPlaybackError, match="capacity"):
        service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())

    response.close()
    replacement = service.register(owner_user_id="other", source_url="https://source.example/two", info=progressive_info())
    assert replacement.stream_id == "after-shutdown"


def test_shutdown_closes_every_registered_session() -> None:
    tokens = iter(["stream-one", "stream-two"])
    packager = FakeHlsPackager()
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: next(tokens),
        clock=lambda: 1_000.0,
    )
    for source in ("one", "two"):
        service.register(
            owner_user_id="owner",
            source_url=f"https://source.example/{source}",
            info=progressive_info(),
        )

    assert service.close_all() == 2
    assert sorted(packager.closed) == ["stream-one", "stream-two"]
    assert service.close_all() == 0


def test_expired_upstream_retries_once_with_singleflight_refresh_and_preserved_ranges() -> None:
    old_url = "https://signed.example/old?signature=expired"
    new_url = "https://signed.example/new?signature=fresh"
    old_open_barrier = threading.Barrier(2)

    class RefreshResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, source_url: str, owner_user_id: str) -> dict:
            assert owner_user_id == "user-1"
            assert source_url == "https://source.example/expiring"
            self.calls += 1
            return {
                "formats": [{
                    "format_id": "18-fresh",
                    "url": new_url,
                    "url_expiry": 5_000,
                    "protocol": "https",
                    "ext": "mp4",
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                    "filesize": 20,
                }]
            }

    class ExpiringReader:
        def __init__(self) -> None:
            self.opens: list[tuple[str, ByteRange | None]] = []
            self.lock = threading.Lock()

        def open(self, track, byte_range):  # noqa: ANN001
            with self.lock:
                self.opens.append((track.url, byte_range))
            if track.url == old_url:
                old_open_barrier.wait(timeout=2)
                return UpstreamMediaResponse(403, {"set-cookie": "expired-secret"}, [b"never-public"])
            return UpstreamMediaResponse(
                206,
                {
                    "content-type": "video/mp4",
                    "content-length": "5",
                    "content-range": f"bytes {byte_range.start}-{byte_range.end}/20",
                },
                [f"{byte_range.start}-{byte_range.end}".encode()],
            )

    resolver = RefreshResolver()
    reader = ExpiringReader()
    service = RemoteStreamingService(
        resolver=resolver,
        reader=reader,
        token_factory=lambda: "expiring-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/expiring",
        info={
            "formats": [{
                "format_id": "18-old",
                "url": old_url,
                "url_expiry": 5_000,
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "filesize": 20,
            }]
        },
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(service.serve_content, "user-1", playback.stream_id, "bytes=0-4"),
            pool.submit(service.serve_content, "user-1", playback.stream_id, "bytes=5-9"),
        ]
        responses = [future.result(timeout=3) for future in futures]

    assert resolver.calls == 1
    assert [collect(response.body) for response in responses] == [b"0-4", b"5-9"]
    assert reader.opens.count((old_url, ByteRange(0, 4))) == 1
    assert reader.opens.count((old_url, ByteRange(5, 9))) == 1
    assert reader.opens.count((new_url, ByteRange(0, 4))) == 1
    assert reader.opens.count((new_url, ByteRange(5, 9))) == 1
    assert all("secret" not in repr(response) and "signed.example" not in repr(response) for response in responses)


def test_upstream_error_body_is_never_forwarded_after_refresh_retry() -> None:
    closed: list[str] = []

    class StillExpiredResolver:
        def resolve(self, source_url: str, owner_user_id: str) -> dict:
            del source_url, owner_user_id
            return progressive_info()

    class RejectingReader:
        def open(self, track, byte_range):  # noqa: ANN001
            del track, byte_range
            return UpstreamMediaResponse(
                403,
                {"content-type": "text/html", "set-cookie": "private-cookie"},
                [b"private upstream error body"],
                lambda: closed.append("closed"),
            )

    service = RemoteStreamingService(
        resolver=StillExpiredResolver(),
        reader=RejectingReader(),
        token_factory=lambda: "rejected-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/rejected",
        info=progressive_info(),
    )

    with pytest.raises(UnsupportedPlaybackError, match="did not provide playable media"):
        service.serve_content("user-1", playback.stream_id)

    assert closed == ["closed", "closed"]


def test_progressive_reader_is_closed_when_body_finishes_or_caller_disconnects() -> None:
    closed: list[str] = []

    class ClosingReader:
        def open(self, track, byte_range):  # noqa: ANN001
            del track, byte_range
            return UpstreamMediaResponse(200, {"content-type": "video/mp4"}, [b"one", b"two"], lambda: closed.append("closed"))

    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=ClosingReader(),
        token_factory=lambda: "closing-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/watch/1",
        info=progressive_info(),
    )

    completed = service.serve_content("user-1", playback.stream_id)
    assert collect(completed.body) == b"onetwo"
    assert closed == ["closed"]
    completed.close()
    assert closed == ["closed"]

    disconnected = service.serve_content("user-1", playback.stream_id)
    iterator = iter(disconnected.body)
    assert next(iterator) == b"one"
    disconnected.close()
    assert closed == ["closed", "closed"]


def test_public_preview_redaction_keeps_format_facts_without_transport_secrets() -> None:
    public = redact_preview_info({
        "id": "video-1",
        "title": "Safe title",
        "thumbnail": "https://source.example/provider-thumbnail.jpg",
        "webpage_url": "https://source.example/watch/1",
        "channel_url": "https://source.example/channel/safe",
        "uploader_url": "https://source.example/@safe",
        "http_headers": {"Cookie": "TOP-LEVEL-SECRET"},
        "url": "https://signed.example/direct?signature=TOP-LEVEL-SECRET",
        "requested_formats": [{"url": "https://signed.example/requested"}],
        "formats": [{
            "format_id": "18",
            "url": "https://signed.example/format?signature=FORMAT-SECRET",
            "fragment_base_url": "https://signed.example/fragments",
            "fragments": [{"url": "https://signed.example/segment"}],
            "http_headers": {"Authorization": "HEADER-SECRET"},
            "protocol": "https",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": 720,
        }],
        "entries": [{
            "id": "child",
            "title": "Child",
            "url": "https://signed.example/child",
            "http_headers": {"Cookie": "CHILD-SECRET"},
        }],
    })

    assert public["id"] == "video-1"
    assert public["channel_url"] == "https://source.example/channel/safe"
    assert public["uploader_url"] == "https://source.example/@safe"
    assert "thumbnail" not in public
    assert public["formats"] == [{
        "format_id": "18",
        "protocol": "https",
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "height": 720,
    }]
    assert public["entries"] == [{"id": "child", "title": "Child"}]
    serialized = repr(public)
    assert "signed.example" not in serialized
    assert "SECRET" not in serialized
    assert "http_headers" not in serialized
    assert "fragments" not in serialized


def test_range_parser_supports_bounded_open_and_suffix_ranges_but_rejects_multiple_ranges() -> None:
    assert parse_range_header(None, 20) is None
    assert parse_range_header("bytes=2-6", 20) == ByteRange(2, 6)
    assert parse_range_header("bytes=7-", 20) == ByteRange(7, None)
    assert parse_range_header("bytes=-5", 20) == ByteRange(15, 19)
    assert parse_range_header("bytes=18-30", 20) == ByteRange(18, 19)
    with pytest.raises(RangeNotSatisfiableError) as multiple:
        parse_range_header("bytes=0-1,4-5", 20)
    assert multiple.value.length == 20
    assert parse_range_header("bytes=-5", None) == ByteRange(None, 5)


def test_approximate_filesize_keeps_range_length_unknown_and_forwards_suffix_to_upstream() -> None:
    opened: list[ByteRange | None] = []

    class RecordingReader(FakeReader):
        def open(self, track, byte_range):  # noqa: ANN001
            assert track.content_length is None
            opened.append(byte_range)
            return UpstreamMediaResponse(206, {"content-range": "bytes 12-16/17", "content-length": "5"}, [b"media"])

    info = progressive_info()
    info["formats"][0].pop("filesize")
    info["formats"][0]["filesize_approx"] = 17
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=RecordingReader(),
        token_factory=lambda: "unknown-size-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="user-1", source_url="https://source.example/watch/1", info=info)

    response = service.serve_content("user-1", playback.stream_id, "bytes=-5")

    assert opened == [ByteRange(None, 5)]
    assert collect(response.body) == b"media"


def test_upstream_416_is_propagated_without_forwarding_its_body() -> None:
    closed: list[str] = []

    class RejectingRangeReader:
        def open(self, track, byte_range):  # noqa: ANN001
            del track, byte_range
            return UpstreamMediaResponse(416, {"Content-Range": "bytes */17", "Set-Cookie": "secret"}, [b"private error"], lambda: closed.append("closed"))

    info = progressive_info()
    info["formats"][0].pop("filesize")
    service = RemoteStreamingService(resolver=NoopResolver(), reader=RejectingRangeReader(), token_factory=lambda: "range-416", clock=lambda: 1_000.0)
    playback = service.register(owner_user_id="user-1", source_url="https://source.example/watch/1", info=info)

    with pytest.raises(RangeNotSatisfiableError) as rejected:
        service.serve_content("user-1", playback.stream_id, "bytes=99-")

    assert rejected.value.length == 17
    assert closed == ["closed"]


def test_unknown_codecs_and_live_sources_are_explicitly_unsupported() -> None:
    tokens = iter(["unknown-codec", "unsupported-video", "live-source"])
    service = RemoteStreamingService(
        resolver=NoopResolver(),
        reader=FakeReader(),
        hls_packager=FakeHlsPackager(),
        token_factory=lambda: next(tokens),
        clock=lambda: 1_000.0,
    )
    unknown = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/unknown",
        info={"formats": [{
            "format_id": "mystery",
            "url": "https://signed.example/mystery",
            "protocol": "https",
            "ext": "bin",
            "vcodec": "proprietary-video",
            "acodec": "proprietary-audio",
        }]},
    )
    unsupported_video = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/unsupported-video",
        info={"formats": [
            {
                "format_id": "video",
                "url": "https://signed.example/video",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "proprietary-video",
                "acodec": "none",
            },
            {
                "format_id": "audio",
                "url": "https://signed.example/audio",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
            },
        ]},
    )
    live = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/live",
        info={"is_live": True, "formats": progressive_info()["formats"]},
    )

    assert unknown.status == "unsupported"
    assert unknown.fallback_code == "no_browser_compatible_format"
    assert unsupported_video.status == "unsupported"
    assert unsupported_video.media_kind is None
    assert unsupported_video.fallback_code == "no_browser_compatible_format"
    assert live.status == "unsupported"
    assert live.fallback_code == "live_not_supported"


def test_explicit_refresh_reuses_opaque_identity_and_can_change_transport_plan() -> None:
    class SplitResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, source_url: str, owner_user_id: str) -> dict:
            assert owner_user_id == "user-1"
            assert source_url == "https://source.example/changing"
            self.calls += 1
            return {
                "formats": [
                    {
                        "format_id": "137",
                        "url": "https://signed.example/video",
                        "url_expiry": 5_000,
                        "protocol": "https",
                        "ext": "mp4",
                        "vcodec": "avc1.640028",
                        "acodec": "none",
                        "height": 1080,
                    },
                    {
                        "format_id": "140",
                        "url": "https://signed.example/audio",
                        "url_expiry": 5_000,
                        "protocol": "https",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                    },
                ]
            }

    resolver = SplitResolver()
    service = RemoteStreamingService(
        resolver=resolver,
        reader=FakeReader(),
        hls_packager=FakeHlsPackager(),
        token_factory=lambda: "changing-stream",
        clock=lambda: 1_000.0,
    )
    original = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/changing",
        info=progressive_info(),
    )

    refreshed = service.refresh("user-1", original.stream_id)

    assert resolver.calls == 1
    assert refreshed.stream_id == original.stream_id
    assert refreshed.transport == "hls"
    assert refreshed.playback_url == "/api/remote-streams/changing-stream/hls/2/manifest.m3u8"


def test_signed_url_is_refreshed_proactively_before_opening_upstream() -> None:
    now = [1_000.0]
    opens: list[str] = []

    class FreshResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, source_url: str, owner_user_id: str) -> dict:
            assert owner_user_id == "user-1"
            assert source_url == "https://source.example/proactive"
            self.calls += 1
            fresh = progressive_info()
            fresh["formats"][0]["url"] = "https://signed.example/fresh"
            fresh["formats"][0]["url_expiry"] = 5_000
            return fresh

    class RecordingReader(FakeReader):
        def open(self, track, byte_range):  # noqa: ANN001
            opens.append(track.url)
            return super().open(track, byte_range)

    expiring = progressive_info()
    expiring["formats"][0]["url"] = "https://signed.example/stale"
    expiring["formats"][0]["url_expiry"] = 1_100
    resolver = FreshResolver()
    service = RemoteStreamingService(
        resolver=resolver,
        reader=RecordingReader(),
        token_factory=lambda: "proactive-stream",
        clock=lambda: now[0],
        refresh_margin_seconds=60,
    )
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/proactive",
        info=expiring,
    )

    now[0] = 1_050
    response = service.serve_content("user-1", playback.stream_id)

    assert collect(response.body) == b"progressive-media"
    assert resolver.calls == 1
    assert opens == ["https://signed.example/fresh"]


def test_refresh_keeps_prepared_hls_generation_until_session_release() -> None:
    packager = FakeHlsPackager()

    class ProgressiveResolver:
        def resolve(self, source_url: str, owner_user_id: str) -> dict:
            assert owner_user_id == "user-1"
            assert source_url == "https://source.example/repackage"
            return progressive_info()

    service = RemoteStreamingService(
        resolver=ProgressiveResolver(),
        reader=FakeReader(),
        hls_packager=packager,
        token_factory=lambda: "repackage-stream",
        clock=lambda: 1_000.0,
    )
    split = {
        "formats": [
            {
                "format_id": "137",
                "url": "https://signed.example/video",
                "url_expiry": 5_000,
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
            },
            {
                "format_id": "140",
                "url": "https://signed.example/audio",
                "url_expiry": 5_000,
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
            },
        ]
    }
    playback = service.register(
        owner_user_id="user-1",
        source_url="https://source.example/repackage",
        info=split,
    )
    prepared_manifest = service.serve_hls("user-1", playback.stream_id, 1, "manifest.m3u8")
    assert collect(prepared_manifest.body).startswith(b"#EXTM3U")

    refreshed = service.refresh("user-1", playback.stream_id)

    assert refreshed.transport == "progressive"
    old_manifest = service.serve_hls("user-1", playback.stream_id, 1, "manifest.m3u8")
    assert collect(old_manifest.body).startswith(b"#EXTM3U")
    assert packager.closed == []
    service.release("user-1", playback.stream_id)
    assert packager.closed == ["repackage-stream"]


_SPLIT = {"formats": [
    {"format_id": "137", "url": "https://signed.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
    {"format_id": "140", "url": "https://signed.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
]}


class CancellablePackager(FakeHlsPackager):
    """Blocks preparations of ``block`` streams until they are cancelled via close_generation."""

    def __init__(self, block: set[str]) -> None:
        super().__init__()
        self.block = block
        self.started: dict[str, threading.Event] = {}
        self.cancelled: dict[tuple[str, int], threading.Event] = {}

    def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
        cancel = self.cancelled.setdefault((stream_id, generation), threading.Event())
        self.started.setdefault(stream_id, threading.Event()).set()
        if stream_id in self.block and cancel.wait(timeout=5):
            raise UnsupportedPlaybackError("A newer request replaced this split playback preparation.")
        return super().prepare(stream_id, generation, video, audio)

    def close_generation(self, stream_id: str, generation: int) -> None:
        super().close_generation(stream_id, generation)
        self.cancelled.setdefault((stream_id, generation), threading.Event()).set()

    def wait_started(self, stream_id: str) -> bool:
        return self.started.setdefault(stream_id, threading.Event()).wait(timeout=2)


def _slots_free(service: RemoteStreamingService, owner: str) -> bool:
    user_slot = service._packaging_by_user[owner]  # noqa: SLF001
    if not user_slot.acquire(blocking=False):
        return False
    user_slot.release()
    return service._preparing == {}  # noqa: SLF001


def test_new_stream_request_supersedes_the_members_in_flight_preparation() -> None:
    tokens = iter(["stream-a", "stream-b"])
    packager = CancellablePackager(block={"stream-a"})
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=packager,
        token_factory=lambda: next(tokens), clock=lambda: 1_000.0,
    )
    first = service.register(owner_user_id="owner", source_url="https://source.example/a", info=_SPLIT)
    second = service.register(owner_user_id="owner", source_url="https://source.example/b", info=_SPLIT)

    with ThreadPoolExecutor(max_workers=1) as pool:
        superseded = pool.submit(service.serve_hls, "owner", first.stream_id, 1, "manifest.m3u8")
        assert packager.wait_started("stream-a")
        response = service.serve_hls("owner", second.stream_id, 1, "manifest.m3u8")
        assert response.status_code == 200
        response.close()
        with pytest.raises(UnsupportedPlaybackError, match="newer request"):
            superseded.result(timeout=2)

    assert ("stream-a", 1) in packager.closed_generations
    assert _slots_free(service, "owner")


def test_older_queued_request_cannot_supersede_a_newer_preparation() -> None:
    tokens = iter(["stream-a", "stream-b"])
    packager = CancellablePackager(block={"stream-b"})
    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=packager,
        token_factory=lambda: next(tokens), clock=lambda: 1_000.0,
    )
    first = service.register(owner_user_id="owner", source_url="https://source.example/a", info=_SPLIT)
    second = service.register(owner_user_id="owner", source_url="https://source.example/b", info=_SPLIT)
    stale_ticket = next(service._tickets)  # noqa: SLF001 - a request that arrived before the next

    with ThreadPoolExecutor(max_workers=1) as pool:
        newer = pool.submit(service.serve_hls, "owner", second.stream_id, 1, "manifest.m3u8")
        assert packager.wait_started("stream-b")
        record = service._records[first.stream_id]  # noqa: SLF001
        with pytest.raises(UnsupportedPlaybackError, match="newer remote stream request"):
            service._prepare_generation(record, 1, stale_ticket)  # noqa: SLF001
        assert ("stream-b", 1) not in packager.closed_generations
        # Releasing the stream cancels its preparation immediately.
        service.release("owner", second.stream_id)
        with pytest.raises(UnsupportedPlaybackError):
            newer.result(timeout=2)
    assert _slots_free(service, "owner")


def test_duplicate_manifest_requests_join_one_preparation() -> None:
    started = threading.Event()
    finish = threading.Event()
    calls: list[int] = []

    class SlowPackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            calls.append(generation)
            started.set()
            assert finish.wait(timeout=2)
            return super().prepare(stream_id, generation, video, audio)

    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=SlowPackager(),
        token_factory=lambda: "joined", clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/a", info=_SPLIT)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.serve_hls, "owner", playback.stream_id, 1, "manifest.m3u8")
        assert started.wait(timeout=2)
        duplicate = pool.submit(service.serve_hls, "owner", playback.stream_id, 1, "manifest.m3u8")
        finish.set()
        responses = [first.result(timeout=2), duplicate.result(timeout=2)]
    assert [response.status_code for response in responses] == [200, 200]
    for response in responses:
        response.close()
    assert calls == [1]
    assert _slots_free(service, "owner")


def test_refresh_during_preparation_cancels_it_instead_of_waiting() -> None:
    class SplitResolver:
        def resolve(self, source_url, owner_user_id):  # noqa: ANN001
            del source_url, owner_user_id
            return _SPLIT

    packager = CancellablePackager(block={"refreshing"})
    service = RemoteStreamingService(
        resolver=SplitResolver(), reader=FakeReader(), hls_packager=packager,
        token_factory=lambda: "refreshing", clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/a", info=_SPLIT)
    with ThreadPoolExecutor(max_workers=1) as pool:
        preparing = pool.submit(service.serve_hls, "owner", playback.stream_id, 1, "manifest.m3u8")
        assert packager.wait_started("refreshing")
        refreshed = service.refresh("owner", playback.stream_id)
        assert refreshed.playback_url == "/api/remote-streams/refreshing/hls/2/manifest.m3u8"
        with pytest.raises(UnsupportedPlaybackError, match="newer request"):
            preparing.result(timeout=2)
    assert _slots_free(service, "owner")


def test_growing_package_is_reread_per_request_and_keeps_its_slot_until_upstream_work_stops(tmp_path) -> None:  # noqa: ANN001
    from concurrent.futures import Future

    manifest = tmp_path / "manifest.m3u8"
    manifest.write_bytes(b'#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6.0,\nsegment-00000.m4s\n')
    done: Future[None] = Future()

    class GrowingPackager(FakeHlsPackager):
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            return HlsPresentation(manifest, {"init.mp4": HlsAsset(b"init", "video/mp4")}, done)

    service = RemoteStreamingService(
        resolver=NoopResolver(), reader=FakeReader(), hls_packager=GrowingPackager(),
        token_factory=lambda: "growing", clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id="owner", source_url="https://source.example/a", info=_SPLIT)
    assert not service.has_presentation("owner", "growing", 1)

    first = service.serve_hls("owner", "growing", 1, "manifest.m3u8")
    body = collect(first.body)
    assert body.startswith(b"#EXTM3U\n#EXT-X-START:TIME-OFFSET=0\n")  # a growing playlist plays from its start
    assert b'URI="/api/remote-streams/growing/hls/1/init.mp4"' in body
    assert b"\n/api/remote-streams/growing/hls/1/segment-00000.m4s\n" in body
    assert service.has_presentation("owner", "growing", 1) and not service.has_presentation("other", "growing", 1)
    assert not _slots_free(service, "owner")  # background packaging still owns the member's slot

    with manifest.open("ab") as output:
        output.write(b"#EXTINF:6.0,\nsegment-00001.m4s\n#EXT-X-ENDLIST\n")
    reloaded = collect(service.serve_hls("owner", playback.stream_id, 1, "manifest.m3u8").body)
    assert b"/api/remote-streams/growing/hls/1/segment-00001.m4s" in reloaded and b"EXT-X-START" not in reloaded

    done.set_result(None)
    assert _slots_free(service, "owner")
