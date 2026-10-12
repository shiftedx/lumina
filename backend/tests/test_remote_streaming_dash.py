from __future__ import annotations

import struct
from dataclasses import dataclass, field

import pytest

from app.services.remote_streaming import (
    BrowserCapabilities,
    ByteRange,
    RangeNotSatisfiableError,
    RemoteStreamingService,
    StreamNotFoundError,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
    _parse_dash_index,
)


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _segment_base_media(*, first_offset: int = 0, referenced_size: int = 16) -> bytes:
    sidx = (
        b"\x00\x00\x00\x00"
        + struct.pack(">II", 1, 1_000)
        + struct.pack(">II", 0, first_offset)
        + struct.pack(">HH", 0, 1)
        + struct.pack(">III", referenced_size, 6_000, 0x90000000)
    )
    return _box(b"ftyp", b"iso6") + _box(b"moov", b"") + _box(b"sidx", sidx) + _box(b"moof", b"") + _box(b"mdat", b"media-payload")


def _format(
    format_id: str,
    *,
    height: int | None,
    vcodec: str,
    acodec: str,
    ext: str,
    tbr: float,
    size: int,
) -> dict:
    return {
        "format_id": format_id,
        "url": f"https://signed.example/{format_id}?secret=private",
        "protocol": "https",
        "ext": ext,
        "vcodec": vcodec,
        "acodec": acodec,
        "width": 3840 if height == 2160 else 1920 if height == 1080 else 640 if height else None,
        "height": height,
        "fps": 24,
        "tbr": tbr,
        "filesize": size,
        "container": "m4a_dash" if ext == "m4a" else "mp4_dash",
        "http_headers": {"Authorization": "private-header"},
    }


def _provider_shaped_info() -> dict:
    return {
        "formats": [
            _format(
                "muxed-360",
                height=360,
                vcodec="avc1.42001e",
                acodec="mp4a.40.2",
                ext="mp4",
                tbr=350,
                size=250 * 1024 * 1024,
            ),
            _format(
                "video-1080",
                height=1080,
                vcodec="avc1.640028",
                acodec="none",
                ext="mp4",
                tbr=3_000,
                size=2 * 1024 * 1024 * 1024,
            ),
            _format(
                "video-2160",
                height=2160,
                vcodec="av01.0.12M.08",
                acodec="none",
                ext="mp4",
                tbr=5_000,
                size=6 * 1024 * 1024 * 1024,
            ),
            _format(
                "audio-low",
                height=None,
                vcodec="none",
                acodec="mp4a.40.5",
                ext="m4a",
                tbr=49,
                size=90 * 1024 * 1024,
            ),
            _format(
                "audio-high",
                height=None,
                vcodec="none",
                acodec="mp4a.40.2",
                ext="m4a",
                tbr=129,
                size=220 * 1024 * 1024,
            ),
        ]
    }


class UnexpectedResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        raise AssertionError((source_url, owner_user_id))


class FullFilePackagerCannotFit:
    def can_package(self, video, audio):  # noqa: ANN001
        del video, audio
        return False

    def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
        raise AssertionError((stream_id, generation, video, audio))

    def close(self, stream_id):  # noqa: ANN001
        del stream_id

    def close_generation(self, stream_id, generation):  # noqa: ANN001
        del stream_id, generation


@dataclass
class SegmentBaseReader:
    payload: bytes = field(default_factory=_segment_base_media)
    opens: list[tuple[str, ByteRange | None]] = field(default_factory=list)

    def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
        del timeout_seconds
        self.opens.append((track.format_id, byte_range))
        start = byte_range.start if byte_range and byte_range.start is not None else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(self.payload) - 1
        body = self.payload[start : min(end + 1, len(self.payload))]
        return UpstreamMediaResponse(
            206 if byte_range else 200,
            {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(self.payload)}",
            },
            [body],
        )


def test_provider_shaped_4k_split_ladder_bypasses_full_file_package_budget() -> None:
    reader = SegmentBaseReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-stream",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )

    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    assert playback.transport == "dash"
    assert [item.display_label for item in playback.renditions] == ["360p", "1080p", "4K"]
    assert playback.selected_rendition_id == "r2160"
    assert playback.playback_url == "/api/remote-streams/dash-stream/dash/1/manifest.mpd"


def test_dash_manifest_needs_only_bounded_prefixes_and_selects_best_audio() -> None:
    reader = SegmentBaseReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-manifest",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    response = service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    manifest = b"".join(response.body)
    response.close()

    assert response.status_code == 200
    assert b'application/dash+xml' not in manifest
    assert b'height="2160"' in manifest
    assert b'codecs="av01.0.12M.08"' in manifest
    assert b'codecs="av01.0.12m.08"' not in manifest
    assert b'/api/remote-streams/dash-manifest/dash/1/video' in manifest
    assert b'/api/remote-streams/dash-manifest/dash/1/audio' in manifest
    assert b"signed.example" not in manifest
    assert sorted(reader.opens) == [  # the probes run at once, in no fixed order
        ("audio-high", ByteRange(0, 256 * 1024 - 1)),
        ("video-2160", ByteRange(0, 256 * 1024 - 1)),
    ]


def test_dash_index_probe_releases_read_slots_when_open_fails() -> None:
    class FailsOnceReader(SegmentBaseReader):
        failed = False

        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            if not self.failed:
                self.failed = True
                raise OSError("synthetic connection failure")
            return super().open(track, byte_range, timeout_seconds)

    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=FailsOnceReader(),
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-open-failure",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
        max_concurrent_reads_global=1,
        max_concurrent_reads_per_user=1,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    with pytest.raises(OSError, match="synthetic connection failure"):
        service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    recovered = service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    assert recovered.status_code == 200
    recovered.close()


def test_dash_index_requires_a_trusted_media_length() -> None:
    class UnknownLengthReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            response = super().open(track, byte_range, timeout_seconds)
            interval = response.headers["Content-Range"].split("/", 1)[0]
            response.headers["Content-Range"] = f"{interval}/*"
            return response

    info = _provider_shaped_info()
    for item in info["formats"]:
        item.pop("filesize", None)
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=UnknownLengthReader(),
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-unknown-length",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=info,
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    with pytest.raises(UnsupportedPlaybackError, match="media length"):
        service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")


def test_dash_rejects_indirect_or_out_of_bounds_segment_indexes() -> None:
    indirect = _segment_base_media(referenced_size=0x80000010)
    with pytest.raises(UnsupportedPlaybackError, match="Indirect"):
        _parse_dash_index(indirect, content_length=len(indirect))

    outside_file = _segment_base_media(first_offset=10_000)
    with pytest.raises(UnsupportedPlaybackError, match="bounded DASH segment index"):
        _parse_dash_index(outside_file, content_length=len(outside_file))


def test_dash_rejects_oversized_media_ranges_instead_of_truncating_them() -> None:
    reader = SegmentBaseReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-bounded",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
        max_bytes_per_response=2048,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )
    manifest = service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    b"".join(manifest.body)
    manifest.close()
    opens_after_manifest = list(reader.opens)
    assert sorted(opens_after_manifest) == [  # the probes run at once, in no fixed order
        ("audio-high", ByteRange(0, 2047)),
        ("video-2160", ByteRange(0, 2047)),
    ]

    with pytest.raises(RangeNotSatisfiableError):
        service.serve_dash("owner", playback.stream_id, 1, "video-0", "bytes=0-2048")
    assert reader.opens == opens_after_manifest


def test_dash_resources_are_bound_to_owner_and_current_generation() -> None:
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=SegmentBaseReader(),
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-private",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    with pytest.raises(StreamNotFoundError):
        service.serve_dash("other-owner", playback.stream_id, 1, "manifest.mpd")
    with pytest.raises(StreamNotFoundError):
        service.serve_dash("owner", playback.stream_id, 2, "manifest.mpd")


def test_invalid_auto_selected_dash_rendition_falls_back_but_manual_selection_does_not() -> None:
    class SelectiveReader(SegmentBaseReader):
        reject: set[str] = {"video-2160"}

        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            response = super().open(track, byte_range, timeout_seconds)
            if track.format_id in self.reject:
                response.body = [b"not-an-iso-bmff-file"]
                response.headers = {
                    "Content-Length": str(len(b"not-an-iso-bmff-file")),
                    "Content-Range": f"bytes 0-{len(b'not-an-iso-bmff-file') - 1}/{len(b'not-an-iso-bmff-file')}",
                }
            return response

    reader = SelectiveReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-fallback",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})
        ),
    )

    redirected = service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    assert redirected.status_code == 307
    assert redirected.headers["Location"].endswith("/dash/2/manifest.mpd")
    redirected.close()
    current = service.describe("owner", playback.stream_id)
    assert current.selected_rendition_id == "r1080"

    reader.reject = {"video-2160"}
    with pytest.raises(UnsupportedPlaybackError):
        service.select_rendition("owner", playback.stream_id, "r2160")
    assert service.describe("owner", playback.stream_id).selected_rendition_id == "r1080"


def _ebml(element_id: int, payload: bytes) -> bytes:
    return element_id.to_bytes((element_id.bit_length() + 7) // 8, "big") + b"\x01" + len(payload).to_bytes(7, "big") + payload


def _webm_dash_media(cluster_sizes: tuple[int, ...] = (40, 90)) -> bytes:
    def build(offsets: list[int]) -> tuple[bytes, int]:
        info = _ebml(0x1549A966, _ebml(0x2AD7B1, (1_000_000).to_bytes(4, "big")) + _ebml(0x4489, struct.pack(">d", 12_000.0)))
        cues = _ebml(0x1C53BB6B, b"".join(
            _ebml(0xBB, _ebml(0xB3, (index * 6_000).to_bytes(4, "big")) + _ebml(0xB7, _ebml(0xF7, b"\x01") + _ebml(0xF1, offset.to_bytes(8, "big"))))
            for index, offset in enumerate(offsets)
        ))
        head = info + _ebml(0x1654AE6B, b"") + cues
        clusters = b"".join(_ebml(0x1F43B675, b"c" * (size - 12)) for size in cluster_sizes)
        return _ebml(0x1A45DFA3, b"") + _ebml(0x18538067, head + clusters), len(head)

    _draft, head_length = build([0] * len(cluster_sizes))
    offsets = [head_length + sum(cluster_sizes[:index]) for index in range(len(cluster_sizes))]
    return build(offsets)[0]


def test_webm_cues_are_a_bounded_segment_index_with_their_largest_cluster() -> None:
    from app.services.remote_streaming import _parse_webm_index

    media = _webm_dash_media((40, 90))
    index = _parse_webm_index(media, content_length=len(media))

    assert index.initialization_end + 1 == index.index_start
    assert media[index.index_start : index.index_start + 4] == bytes.fromhex("1C53BB6B")
    assert index.duration_seconds == 12
    assert index.max_referenced_size == 90
    with pytest.raises(UnsupportedPlaybackError, match="bounded DASH segment index"):
        _parse_webm_index(media[: index.index_end], content_length=len(media))


def _vp9_ladder_info() -> dict:
    info = _provider_shaped_info()
    info["formats"] = [item for item in info["formats"] if item["format_id"] != "video-2160"]
    for format_id, vcodec in (("vp9-2160", "vp9"), ("vp9-hdr-2160", "vp9.2")):
        item = _format(format_id, height=2160, vcodec=vcodec, acodec="none", ext="webm", tbr=24_000, size=900 * 1024 * 1024)
        item["container"] = "webm_dash"
        info["formats"].append(item)
    return info


def test_vp9_webm_4k_joins_the_dash_ladder_only_when_mse_can_play_it() -> None:
    class WebmVideoReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            self.payload = _webm_dash_media() if track.ext == "webm" else _segment_base_media()
            return super().open(track, byte_range, timeout_seconds)

    def register(profiles: set[str]):  # noqa: ANN202
        service = RemoteStreamingService(
            resolver=UnexpectedResolver(),
            reader=WebmVideoReader(),
            hls_packager=FullFilePackagerCannotFit(),
            token_factory=lambda: "vp9",
            rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
            clock=lambda: 1_000,
        )
        playback = service.register(
            owner_user_id="owner",
            source_url="https://source.example/vp9-video",
            info=_vp9_ladder_info(),
            browser_capabilities=BrowserCapabilities(frozenset(profiles)),
        )
        return service, playback

    base = {"dash-segment-base", "mp4-avc-aac", "m4a-aac", "webm-vp9-opus"}
    _service, without_mse = register(base)
    assert [item.display_label for item in without_mse.renditions] == ["360p", "1080p"]

    service, playback = register(base | {"dash-webm-vp9"})
    assert [item.display_label for item in playback.renditions] == ["360p", "1080p", "4K"]
    assert playback.renditions[-1].video_codec == "vp9"
    assert playback.selected_rendition_id == "r2160"
    manifest = service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd")
    body = b"".join(manifest.body)
    manifest.close()
    assert b'mimeType="video/webm"' in body
    assert b'codecs="vp09.00.50.08"' in body  # 3840x2160 at 24 fps, in the form MediaCapabilities accepts


def test_vp9_codec_levels_match_the_providers_own_full_codec_strings() -> None:
    from app.services.remote_streaming import MediaTrack, _manifest_video_codec

    def vp9(width: int, height: int, fps: float) -> str:
        return _manifest_video_codec(MediaTrack("f", "u", "video/webm", "https", "webm", "vp9", "none", height, None, None, width=width, frame_rate=fps))

    # YouTube's HLS variants of the same encodes carry these strings.
    assert [vp9(640, 360, 30), vp9(854, 480, 30), vp9(1280, 720, 60), vp9(1920, 1080, 60), vp9(2560, 1440, 60), vp9(3840, 2160, 60)] == [
        "vp09.00.21.08", "vp09.00.30.08", "vp09.00.40.08", "vp09.00.41.08", "vp09.00.50.08", "vp09.00.51.08",
    ]


def test_dash_segment_budget_scales_to_the_renditions_own_largest_segment() -> None:
    reader = SegmentBaseReader(payload=_segment_base_media(referenced_size=4096) + b"x" * 4096)
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "dash-4k-segments",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
        max_bytes_per_response=2048,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/large-video",
        info=_provider_shaped_info(),
        browser_capabilities=BrowserCapabilities(frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})),
    )
    service.serve_dash("owner", playback.stream_id, 1, "manifest.mpd").close()

    segment = service.serve_dash("owner", playback.stream_id, 1, "video-0", "bytes=0-4095")
    assert len(b"".join(segment.body)) == 4096
    segment.close()
    with pytest.raises(RangeNotSatisfiableError):
        service.serve_dash("owner", playback.stream_id, 1, "video-0", "bytes=0-4096")


def _av1_ladder_info() -> dict:
    info = _provider_shaped_info()
    info["formats"] = [item for item in info["formats"] if item["format_id"] != "video-1080"]
    for format_id, height, tbr in (("av1-360", 360, 400), ("av1-1080", 1080, 2_500)):
        info["formats"].append(_format(format_id, height=height, vcodec="av01.0.08M.08", acodec="none", ext="mp4", tbr=tbr, size=10**9))
    return info


def _auto_service(reader: SegmentBaseReader, max_height: int | None = None):  # noqa: ANN202
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "auto",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/av1-video",
        info=_av1_ladder_info(),
        browser_capabilities=BrowserCapabilities(
            frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"}), max_height=max_height
        ),
    )
    return service, playback


def _manifest_heights(service: RemoteStreamingService, generation: int) -> list[str]:
    import re

    response = service.serve_dash("owner", "auto", generation, "manifest.mpd")
    manifest = b"".join(response.body).decode()
    response.close()
    return re.findall(r'height="(\d+)"', manifest)


def test_auto_adapts_across_one_codec_ladder_within_the_member_ceiling() -> None:
    service, playback = _auto_service(SegmentBaseReader())
    assert (playback.selected_rendition_id, playback.auto_available) == ("auto", True)
    assert _manifest_heights(service, 1) == ["360", "1080", "2160"]
    service.serve_dash("owner", "auto", 1, "video-2", "bytes=0-15").close()
    with pytest.raises(StreamNotFoundError):
        service.serve_dash("owner", "auto", 1, "video-3", "bytes=0-15")

    capped, capped_playback = _auto_service(SegmentBaseReader(), max_height=1080)
    assert capped_playback.selected_rendition_id == "auto"
    assert _manifest_heights(capped, 1) == ["360", "1080"]


def test_manual_pin_disables_auto_until_the_viewer_returns_to_it() -> None:
    service, playback = _auto_service(SegmentBaseReader())

    pinned = service.select_rendition("owner", playback.stream_id, "r1080")
    assert (pinned.selected_rendition_id, pinned.auto_available) == ("r1080", True)
    assert _manifest_heights(service, 2) == ["1080"]

    auto = service.select_rendition("owner", playback.stream_id, "auto")
    assert auto.selected_rendition_id == "auto"
    assert auto.playback_url.endswith("/dash/3/manifest.mpd")
    assert _manifest_heights(service, 3) == ["360", "1080", "2160"]
    # Under 720p only the muxed 360p stream remains: nothing to adapt across, and nothing changes.
    with pytest.raises(StreamNotFoundError):
        service.select_rendition("owner", playback.stream_id, "auto", max_height=720)
    assert service.describe("owner", playback.stream_id).selected_rendition_id == "auto"


def test_auto_skips_a_rung_it_cannot_index_instead_of_failing() -> None:
    class RejectingReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            response = super().open(track, byte_range, timeout_seconds)
            if track.format_id == "av1-1080":
                response.body = [b"not-an-index"]
                response.headers = {"Content-Length": "12", "Content-Range": "bytes 0-11/12"}
            return response

    service, _playback = _auto_service(RejectingReader())
    assert _manifest_heights(service, 1) == ["360", "2160"]


def test_auto_indexes_its_rungs_at_once_under_one_member_read_slot() -> None:
    """Time to first frame: YouTube took 0.1-2.6 s per index probe, 7-10 s for a 4K ladder one at a time."""
    import time

    class SlowReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            time.sleep(0.3)
            return super().open(track, byte_range, timeout_seconds)

    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=SlowReader(),
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "auto",
        rendition_token_factory=iter(("r360", "r1080", "r2160")).__next__,
        clock=lambda: 1_000,
        max_concurrent_reads_per_user=1,
    )
    service.register(
        owner_user_id="owner",
        source_url="https://source.example/av1-video",
        info=_av1_ladder_info(),
        browser_capabilities=BrowserCapabilities(frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"})),
    )
    started = time.monotonic()
    heights = _manifest_heights(service, 1)
    assert heights == ["360", "1080", "2160"]  # no rung lost to the member's one read slot
    assert time.monotonic() - started < 0.9  # four 0.3 s probes (three video, one audio) at once, not 1.2 s in a row
    # The probes gave their slot back: the member can read a segment.
    service.serve_dash("owner", "auto", 1, "video-2", "bytes=0-15").close()


def test_init_and_index_ranges_come_from_the_prepared_probe_without_an_upstream_read() -> None:
    """dash.js asks every rung for its init and index at once: 9 upstream reads against a 4-slot member, so five 409'd
    and retried ~1 s later. Preparation already read those bytes; serving them from memory needs no slot."""
    import re

    reader = SegmentBaseReader()
    service, playback = _auto_service(reader)
    manifest = service.serve_dash("owner", "auto", 1, "manifest.mpd")
    text = b"".join(manifest.body).decode()
    manifest.close()
    probes = len(reader.opens)
    ranges = re.findall(r'(?:indexRange|range)="(\d+)-(\d+)"', text)
    assert ranges
    expected = _segment_base_media()
    for resource in ("video-0", "video-1", "video-2", "audio"):
        for start, end in ranges[:2]:
            response = service.serve_dash("owner", "auto", 1, resource, f"bytes={start}-{end}")
            assert response.status_code == 206
            assert b"".join(response.body) == expected[int(start) : int(end) + 1]
            assert response.headers["Content-Range"] == f"bytes {start}-{end}/{len(expected)}"
            response.close()
    assert len(reader.opens) == probes  # nothing more went upstream


def test_an_intent_prefetch_reads_the_dash_indexes_so_the_click_prepares_without_upstream() -> None:
    """Hovering a card resolves it; for DASH the slow part after extraction is the index probes (1.6 s)."""
    reader = SegmentBaseReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "auto",
        rendition_token_factory=map("r{}".format, range(100)).__next__,  # the warm-up plans too
        clock=lambda: 1_000,
    )
    capabilities = BrowserCapabilities(frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"}))
    service.warm_dash(owner_user_id="owner", info=_av1_ladder_info(), browser_capabilities=capabilities)
    warmed = len(reader.opens)
    assert warmed == 4  # three video rungs and the audio, nothing registered
    service.register(owner_user_id="owner", source_url="https://source.example/av1-video", info=_av1_ladder_info(), browser_capabilities=capabilities)
    assert _manifest_heights(service, 1) == ["360", "1080", "2160"]
    assert len(reader.opens) == warmed


def test_a_click_joins_index_probes_its_prefetch_still_has_in_flight() -> None:
    import threading
    import time

    class SlowReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            time.sleep(0.3)
            return super().open(track, byte_range, timeout_seconds)

    reader = SlowReader()
    service = RemoteStreamingService(
        resolver=UnexpectedResolver(),
        reader=reader,
        hls_packager=FullFilePackagerCannotFit(),
        token_factory=lambda: "auto",
        rendition_token_factory=map("r{}".format, range(100)).__next__,
        clock=lambda: 1_000,
    )
    capabilities = BrowserCapabilities(frozenset({"dash-segment-base", "mp4-avc-aac", "mp4-av1-aac", "m4a-aac"}))
    warm = threading.Thread(target=service.warm_dash, kwargs={"owner_user_id": "owner", "info": _av1_ladder_info(), "browser_capabilities": capabilities})
    warm.start()
    time.sleep(0.1)  # the click lands while the prefetch's probes are still reading
    service.register(owner_user_id="owner", source_url="https://source.example/av1-video", info=_av1_ladder_info(), browser_capabilities=capabilities)
    assert _manifest_heights(service, 1) == ["360", "1080", "2160"]
    warm.join()
    assert len(reader.opens) == 4  # each index read once, by whoever asked first


def test_an_index_probe_stops_reading_once_the_index_is_complete() -> None:
    """26-pp: a probe read the whole 256 KB range per rung; at a slow googlevideo edge that took up to 2 s for bytes past
    the index (TTFB was 0.07-0.27 s), and preparation waits for the slowest of nine rungs."""
    pulled: list[int] = []

    class TrickleReader(SegmentBaseReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            response = super().open(track, byte_range, timeout_seconds)
            head = b"".join(response.body)

            def body():  # noqa: ANN202
                yield head
                for _ in range(60):  # the rest of the 256 KB range, which the probe must leave unread
                    pulled.append(1)
                    yield b"\0" * 4096

            response.headers = {"Content-Length": str(256 * 1024), "Content-Range": f"bytes 0-{256 * 1024 - 1}/{10 * 1024 * 1024}"}
            response.body = body()
            return response

    service, _playback = _auto_service(TrickleReader())
    assert _manifest_heights(service, 1) == ["360", "1080", "2160"]
    assert pulled == []



def test_the_original_audio_track_wins_over_auto_dubs() -> None:
    """YouTube lists auto-dubbed copies of each audio format (140-0 German ... 140-7 the original); yt-dlp marks the
    original with language_preference 10 and the dubs -1. Picking by codec and bitrate alone played German first."""
    from app.services.remote_streaming import _audio_score, _track

    def audio(format_id: str, language: str, preference: int) -> dict:
        return {"format_id": format_id, "url": f"https://rr.example/{format_id}", "protocol": "https", "ext": "m4a", "vcodec": "none",
                "acodec": "mp4a.40.2", "tbr": 129.5, "language": language, "language_preference": preference}

    tracks = [_track(audio(f"140-{n}", lang, -1)) for n, lang in enumerate(["de-DE", "es-US", "fr-FR"])] + [_track(audio("140-7", "en-US", 10))]
    assert max(tracks, key=_audio_score).format_id == "140-7"
    # A better-bitrate dub still loses to the original.
    louder = _track({**audio("251-0", "pt-BR", -1), "ext": "webm", "acodec": "opus", "tbr": 160})
    assert max([*tracks, louder], key=_audio_score).format_id == "140-7"
