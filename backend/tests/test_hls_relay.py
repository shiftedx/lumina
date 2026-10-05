from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Iterable

import pytest

from app.services.hls_relay import HlsRelayService, UpstreamRelayExpiredError
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError
from app.services.remote_streaming import ByteRange, StreamNotFoundError, UnsupportedPlaybackError, UpstreamMediaResponse


# --- deterministic fixtures ------------------------------------------------

MASTER_URL = "https://cdn.example/vod/master.m3u8"

MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\"\n"
    "360p/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,CODECS=\"avc1.4d401f,mp4a.40.2\"\n"
    "720p/index.m3u8\n"
)

MEDIA_360 = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:7\n"
    "#EXT-X-TARGETDURATION:4\n"
    "#EXT-X-MAP:URI=\"init.mp4\"\n"
    "#EXT-X-KEY:METHOD=AES-128,URI=\"../keys/k.bin\",IV=0x00000000000000000000000000000000\n"
    "#EXTINF:4.0,\n"
    "seg0.m4s\n"
    "#EXT-X-DISCONTINUITY\n"
    "#EXTINF:4.0,\n"
    "seg1.m4s\n"
    "#EXT-X-ENDLIST\n"
)


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del family
        if host not in mapping:
            raise socket.gaierror(f"no fixture address for {host}")
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


def public_policy(extra: dict[str, list[str]] | None = None) -> PublicSourcePolicy:
    mapping = {"cdn.example": ["93.184.216.34"], "internal.example": ["10.0.0.5"]}
    mapping.update(extra or {})
    return PublicSourcePolicy(resolver=resolver_for(mapping))


@dataclass
class FakeRelayFetcher:
    responses: dict[str, tuple[int, dict[str, str], bytes]]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url, *, headers=None, byte_range: ByteRange | None = None, timeout_seconds=None):  # noqa: ANN001
        self.calls.append(url)
        entry = self.responses.get(url)
        if entry is None:
            return UpstreamMediaResponse(404, {}, [b""])
        status, resp_headers, body = entry
        data = body
        code = status
        out_headers = dict(resp_headers)
        if byte_range is not None and status == 200 and byte_range.start is not None:
            start = byte_range.start
            end = byte_range.end if byte_range.end is not None else len(body) - 1
            data = body[start : end + 1]
            code = 206
            out_headers["content-range"] = f"bytes {start}-{end}/{len(body)}"
        out_headers.setdefault("content-length", str(len(data)))
        return UpstreamMediaResponse(code, out_headers, [data])


@dataclass
class FakeResolver:
    masters: list[str]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        self.calls.append((source_url, owner_user_id))
        master = self.masters[min(len(self.calls) - 1, len(self.masters) - 1)]
        variant = master.replace("master.m3u8", "360p/index.m3u8")
        return {
            "extractor_key": "TwitchVod",
            "webpage_url": source_url,
            "formats": [
                {
                    "format_id": "360p30",
                    "protocol": "m3u8_native",
                    "url": variant,
                    "manifest_url": master,
                    "vcodec": "avc1.4d401e",
                    "acodec": "mp4a.40.2",
                }
            ],
        }


def register_info(master_url: str = MASTER_URL) -> dict:
    return {
        "extractor_key": "TwitchVod",
        "webpage_url": "https://www.twitch.tv/videos/1",
        "formats": [
            {
                "format_id": "360p30",
                "protocol": "m3u8_native",
                "url": master_url.replace("master.m3u8", "360p/index.m3u8"),
                "manifest_url": master_url,
                "vcodec": "avc1.4d401e",
                "acodec": "mp4a.40.2",
            }
        ],
    }


def default_responses() -> dict[str, tuple[int, dict[str, str], bytes]]:
    leak = {"set-cookie": "upstream-secret=1", "authorization": "Bearer upstream-token"}
    return {
        MASTER_URL: (200, {"content-type": "application/vnd.apple.mpegurl", **leak}, MASTER.encode()),
        "https://cdn.example/vod/360p/index.m3u8": (200, {**leak}, MEDIA_360.encode()),
        "https://cdn.example/vod/720p/index.m3u8": (200, {}, MEDIA_360.encode()),
        "https://cdn.example/vod/360p/init.mp4": (200, {**leak}, b"init-bytes"),
        "https://cdn.example/vod/360p/seg0.m4s": (200, {**leak}, b"segment-zero-bytes"),
        "https://cdn.example/vod/360p/seg1.m4s": (200, {}, b"segment-one-bytes"),
        "https://cdn.example/vod/keys/k.bin": (200, {**leak}, b"0123456789abcdef"),
    }


def build_service(responses=None, resolver=None, policy=None, **kwargs) -> tuple[HlsRelayService, FakeRelayFetcher]:
    fetcher = FakeRelayFetcher(responses if responses is not None else default_responses())
    service = HlsRelayService(
        resolver=resolver or FakeResolver([MASTER_URL]),
        fetcher=fetcher,
        policy=policy or public_policy(),
        token_factory=iter_factory(["stream-A", "stream-B", "stream-C"]),
        resource_token_factory=iter_factory([f"res-{i}" for i in range(500)]),
        clock=lambda: 1_000.0,
        **kwargs,
    )
    return service, fetcher


def iter_factory(values):
    iterator = iter(values)
    return lambda: next(iterator)


def drain(spec) -> bytes:
    try:
        return b"".join(spec.body)
    finally:
        spec.close()


def register(service, owner="user-1"):
    return service.register(
        owner_user_id=owner,
        source_url="https://www.twitch.tv/videos/1",
        info=register_info(),
    )


# --- tests -----------------------------------------------------------------


def test_register_reports_ready_hls_playback_with_a_lumina_master_address() -> None:
    service, _ = build_service()
    descriptor = register(service)
    assert descriptor.status == "ready"
    assert descriptor.transport == "hls"
    assert descriptor.media_kind == "video"
    assert descriptor.has_video and descriptor.has_audio and descriptor.seekable
    assert descriptor.playback_url == "/api/remote-streams/stream-A/relay/1/master.m3u8"


def test_register_without_a_master_reports_unsupported() -> None:
    service, _ = build_service()
    descriptor = service.register(
        owner_user_id="user-1",
        source_url="https://example.test/clip",
        info={"extractor_key": "TwitchVod", "formats": [{"protocol": "https", "url": "https://cdn.example/x.mp4"}]},
    )
    assert descriptor.status == "unsupported"
    assert descriptor.transport is None


def test_master_and_media_are_rewritten_to_lumina_addresses_without_upstream_leaks() -> None:
    service, _ = build_service()
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    assert "cdn.example" not in master
    assert "https://" not in master
    assert master.count("/api/remote-streams/stream-A/relay/1/r/") == 2  # two variants
    # Descriptive attributes stay so hls.js can pick a browser-compatible rendition.
    assert "RESOLUTION=1280x720" in master

    variant_id = _first_resource_id(master)
    media = drain(service.serve_resource("user-1", "stream-A", 1, variant_id)).decode()
    assert "cdn.example" not in media
    assert "#EXT-X-DISCONTINUITY" in media  # discontinuities preserved
    assert "IV=0x00000000000000000000000000000000" in media  # key attributes preserved
    assert media.count("/api/remote-streams/stream-A/relay/1/r/") == 4  # map + key + 2 segments


def test_segments_and_keys_relay_bytes_and_strip_upstream_headers() -> None:
    service, _ = build_service()
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    variant_id = _first_resource_id(master)
    media = drain(service.serve_resource("user-1", "stream-A", 1, variant_id)).decode()

    for resource_id in _resource_ids(media):
        spec = service.serve_resource("user-1", "stream-A", 1, resource_id)
        assert "Set-Cookie" not in spec.headers and "set-cookie" not in {k.lower() for k in spec.headers}
        assert "Authorization" not in spec.headers
        assert spec.headers["Cache-Control"] == "private, no-store"
        assert spec.headers["X-Content-Type-Options"] == "nosniff"
        body = drain(spec)
        assert body  # real bytes relayed through Lumina


def test_segment_supports_byte_range_requests() -> None:
    service, _ = build_service()
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    media = drain(service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))).decode()
    segment_id = _resource_ids(media)[-1]  # last registered resource is a segment
    spec = service.serve_resource("user-1", "stream-A", 1, segment_id, range_header="bytes=0-3")
    assert spec.status_code == 206
    assert spec.headers["Content-Range"].startswith("bytes 0-3/")
    assert len(drain(spec)) == 4


def test_relative_and_absolute_addresses_resolve_against_the_playlist() -> None:
    service, fetcher = build_service()
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    media = drain(service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))).decode()
    for resource_id in _resource_ids(media):
        drain(service.serve_resource("user-1", "stream-A", 1, resource_id))
    # ../keys/k.bin resolved above the playlist directory; init.mp4 stayed beside it.
    assert "https://cdn.example/vod/keys/k.bin" in fetcher.calls
    assert "https://cdn.example/vod/360p/init.mp4" in fetcher.calls


def test_master_with_uri_bearing_session_data_fails_closed_without_leaking() -> None:
    poisoned = (
        "#EXTM3U\n"
        "#EXT-X-SESSION-DATA:DATA-ID=\"com.x\",URI=\"https://cdn.example/vod/move.json?sig=SECRETTOKEN\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000\n360p/index.m3u8\n"
    )
    responses = default_responses()
    responses[MASTER_URL] = (200, {}, poisoned.encode())
    service, _ = build_service(responses=responses)
    register(service)
    with pytest.raises(UnsupportedPlaybackError) as caught:
        service.serve_master("user-1", "stream-A", 1)
    assert "SECRETTOKEN" not in str(caught.value)


def test_media_playlist_with_rendition_report_fails_closed_without_leaking() -> None:
    hostile_media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-RENDITION-REPORT:URI=\"https://cdn.example/vod/1080/report.m3u8?sig=SECRETTOKEN\",LAST-MSN=9\n"
        "#EXTINF:4.0,\nseg0.m4s\n#EXT-X-ENDLIST\n"
    )
    responses = default_responses()
    responses["https://cdn.example/vod/360p/index.m3u8"] = (200, {}, hostile_media.encode())
    service, _ = build_service(responses=responses)
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    with pytest.raises(UnsupportedPlaybackError) as caught:
        service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))
    assert "SECRETTOKEN" not in str(caught.value)


def test_owner_isolation_blocks_cross_member_access() -> None:
    service, _ = build_service()
    register(service, owner="user-1")
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    resource_id = _first_resource_id(master)
    with pytest.raises(StreamNotFoundError):
        service.serve_master("user-2", "stream-A", 1)
    with pytest.raises(StreamNotFoundError):
        service.serve_resource("user-2", "stream-A", 1, resource_id)
    with pytest.raises(StreamNotFoundError):
        service.refresh("user-2", "stream-A")


def test_private_and_mixed_playlists_fail_closed() -> None:
    poisoned_master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000\n"
        "360p/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000\n"
        "https://internal.example/720p/index.m3u8\n"
    )
    responses = default_responses()
    responses[MASTER_URL] = (200, {}, poisoned_master.encode())
    service, _ = build_service(responses=responses)
    register(service)
    with pytest.raises(PublicSourcePolicyError):
        service.serve_master("user-1", "stream-A", 1)


def test_media_playlist_referencing_loopback_fails_closed() -> None:
    hostile_media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nhttp://127.0.0.1/evil.ts\n#EXT-X-ENDLIST\n"
    )
    responses = default_responses()
    responses["https://cdn.example/vod/360p/index.m3u8"] = (200, {}, hostile_media.encode())
    service, _ = build_service(responses=responses)
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    with pytest.raises(PublicSourcePolicyError):
        service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))


def test_segment_fetch_redirected_into_private_space_fails_closed() -> None:
    # The segment URL passes policy at rewrite time (public host) but the upstream
    # redirects into private space; the guarded transport revalidates and raises a
    # policy denial wrapped by the request layer. The relay must fail closed.
    class RedirectingFetcher(FakeRelayFetcher):
        def fetch(self, url, *, headers=None, byte_range=None, timeout_seconds=None):  # noqa: ANN001
            if url.endswith("seg0.m4s"):
                raise RuntimeError("upstream redirect") from PublicSourcePolicyError()
            return super().fetch(url, headers=headers, byte_range=byte_range, timeout_seconds=timeout_seconds)

    service = HlsRelayService(
        resolver=FakeResolver([MASTER_URL]),
        fetcher=RedirectingFetcher(default_responses()),
        policy=public_policy(),
        token_factory=iter_factory(["stream-A"]),
        resource_token_factory=iter_factory([f"res-{i}" for i in range(50)]),
        clock=lambda: 1_000.0,
    )
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    media = drain(service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))).decode()
    seg0 = _resource_ids(media)[2]
    with pytest.raises(PublicSourcePolicyError):
        service.serve_resource("user-1", "stream-A", 1, seg0)


def test_unsupported_scheme_in_playlist_fails_closed() -> None:
    hostile_media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nrtmp://cdn.example/live\n#EXT-X-ENDLIST\n"
    )
    responses = default_responses()
    responses["https://cdn.example/vod/360p/index.m3u8"] = (200, {}, hostile_media.encode())
    service, _ = build_service(responses=responses)
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    with pytest.raises(PublicSourcePolicyError):
        service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))


def test_malformed_master_fails_closed() -> None:
    responses = default_responses()
    responses[MASTER_URL] = (200, {}, b"this is not a playlist\n")
    service, _ = build_service(responses=responses)
    register(service)
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_master("user-1", "stream-A", 1)


def test_oversized_master_fails_closed() -> None:
    responses = default_responses()
    responses[MASTER_URL] = (200, {}, MASTER.encode())
    service, _ = build_service(responses=responses, max_manifest_bytes=8)
    register(service)
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_master("user-1", "stream-A", 1)


def test_resource_budget_fails_closed_on_recursive_or_huge_playlists() -> None:
    service, _ = build_service(max_resources_per_generation=1)
    register(service)
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_master("user-1", "stream-A", 1)


def test_segment_exceeding_the_response_budget_fails_closed() -> None:
    service, _ = build_service(max_bytes_per_response=4)
    register(service)
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    media = drain(service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))).decode()
    segment_id = _resource_ids(media)[2]  # seg0 (18 bytes) exceeds the 4-byte budget
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_resource("user-1", "stream-A", 1, segment_id)


def test_expired_segment_reresolves_without_changing_stream_identity() -> None:
    responses = default_responses()
    responses["https://cdn.example/vod/360p/seg0.m4s"] = (403, {}, b"")
    resolver = FakeResolver([MASTER_URL, "https://cdn.example/vod/master.m3u8?expire=9999999999"])
    service, _ = build_service(responses=responses, resolver=resolver)
    descriptor = register(service)
    assert descriptor.playback_url.endswith("/relay/1/master.m3u8")
    master = drain(service.serve_master("user-1", "stream-A", 1)).decode()
    media = drain(service.serve_resource("user-1", "stream-A", 1, _first_resource_id(master))).decode()
    seg0 = _resource_ids(media)[2]  # order: map, key, seg0, seg1
    with pytest.raises(UpstreamRelayExpiredError):
        service.serve_resource("user-1", "stream-A", 1, seg0)
    # The session re-resolved server-side: same opaque stream id, new generation.
    refreshed = service.refresh("user-1", "stream-A")
    assert refreshed.stream_id == "stream-A"
    assert refreshed.playback_url.endswith("/relay/2/master.m3u8")
    assert resolver.calls  # the resolver was consulted server-side


def test_stale_master_generation_redirects_to_current() -> None:
    service, _ = build_service()
    register(service)
    service.refresh("user-1", "stream-A")  # bumps to generation 2
    spec = service.serve_master("user-1", "stream-A", 1)
    assert spec.status_code == 307
    assert spec.headers["Location"] == "/api/remote-streams/stream-A/relay/2/master.m3u8"
    spec.close()


def test_release_and_expiry_clean_up_sessions() -> None:
    service, _ = build_service()
    register(service)
    service.release("user-1", "stream-A")
    with pytest.raises(StreamNotFoundError):
        service.serve_master("user-1", "stream-A", 1)
    with pytest.raises(StreamNotFoundError):
        service.release("user-1", "stream-A")


def test_idle_and_lifetime_expiry_retire_sessions() -> None:
    clock = {"now": 1_000.0}
    service = HlsRelayService(
        resolver=FakeResolver([MASTER_URL]),
        fetcher=FakeRelayFetcher(default_responses()),
        policy=public_policy(),
        token_factory=iter_factory(["stream-A"]),
        resource_token_factory=iter_factory([f"res-{i}" for i in range(50)]),
        clock=lambda: clock["now"],
        idle_ttl_seconds=100,
    )
    register(service)
    clock["now"] = 1_000.0 + 101
    assert service.expire_idle() == 1
    with pytest.raises(StreamNotFoundError):
        service.serve_master("user-1", "stream-A", 1)


def test_close_all_reports_retired_sessions() -> None:
    service, _ = build_service()
    register(service)
    assert service.close_all() == 1
    assert service.close_all() == 0


def test_session_caps_fail_closed() -> None:
    service = HlsRelayService(
        resolver=FakeResolver([MASTER_URL]),
        fetcher=FakeRelayFetcher(default_responses()),
        policy=public_policy(),
        token_factory=iter_factory(["s1", "s2", "s3"]),
        resource_token_factory=iter_factory([f"res-{i}" for i in range(50)]),
        clock=lambda: 1_000.0,
        max_streams_per_user=1,
    )
    register(service, owner="user-1")
    with pytest.raises(UnsupportedPlaybackError):
        register(service, owner="user-1")


# --- helpers ---------------------------------------------------------------


def _resource_ids(manifest: str) -> list[str]:
    prefix = "/relay/1/r/"
    ids: list[str] = []
    for token in manifest.replace("\"", " ").split():
        marker = token.find(prefix)
        if marker != -1:
            ids.append(token[marker + len(prefix):])
    return ids


def _first_resource_id(manifest: str) -> str:
    ids = _resource_ids(manifest)
    assert ids, "expected at least one rewritten resource"
    return ids[0]
