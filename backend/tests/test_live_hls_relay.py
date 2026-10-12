from __future__ import annotations

import socket
from dataclasses import dataclass, field

import pytest

from app.services.live_hls_relay import LiveHlsRelayService
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError
from app.services.remote_streaming import (
    ByteRange,
    StreamNotFoundError,
    UnsupportedPlaybackError,
    UpstreamMediaResponse,
)


# --- deterministic fixtures ------------------------------------------------

MASTER_URL = "https://cdn.example/live/master.m3u8"
MEDIA_URL = "https://cdn.example/live/360p/index.m3u8"

LIVE_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\"\n"
    "360p/index.m3u8\n"
)


def live_media(media_seq: int, seg_ids: list[int], *, discontinuity_at: int | None = None, endlist: bool = False) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:4", f"#EXT-X-MEDIA-SEQUENCE:{media_seq}"]
    for index, seg in enumerate(seg_ids):
        if discontinuity_at is not None and index == discontinuity_at:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append("#EXTINF:4.0,")
        lines.append(f"seg{seg}.m4s")
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del family
        if host not in mapping:
            raise socket.gaierror(f"no fixture address for {host}")
        return [
            (socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


def public_policy() -> PublicSourcePolicy:
    return PublicSourcePolicy(resolver=resolver_for({
        "cdn.example": ["93.184.216.34"],
        "internal.example": ["10.0.0.5"],
    }))


@dataclass
class SequencedFetcher:
    """Return an evolving body per URL to simulate a live edge advancing."""

    responses: dict[str, list[tuple[int, dict[str, str], bytes]]]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url, *, headers=None, byte_range: ByteRange | None = None, timeout_seconds=None):  # noqa: ANN001
        self.calls.append(url)
        queue = self.responses.get(url)
        if not queue:
            return UpstreamMediaResponse(404, {}, [b""])
        status, resp_headers, body = queue[0] if len(queue) == 1 else queue.pop(0)
        out_headers = dict(resp_headers)
        data = body
        code = status
        if byte_range is not None and status == 200 and byte_range.start is not None:
            end = byte_range.end if byte_range.end is not None else len(body) - 1
            data = body[byte_range.start : end + 1]
            code = 206
            out_headers["content-range"] = f"bytes {byte_range.start}-{end}/{len(body)}"
        out_headers.setdefault("content-length", str(len(data)))
        return UpstreamMediaResponse(code, out_headers, [data])


@dataclass
class FakeResolver:
    masters: list[str]
    calls: list = field(default_factory=list)

    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        self.calls.append((source_url, owner_user_id))
        master = self.masters[min(len(self.calls) - 1, len(self.masters) - 1)]
        return live_register_info(master)


def live_register_info(master_url: str = MASTER_URL) -> dict:
    return {
        "extractor_key": "Youtube",
        "is_live": True,
        "webpage_url": "https://www.youtube.com/watch?v=live1",
        "manifest_url": master_url,
        "formats": [
            {
                "format_id": "360p",
                "protocol": "m3u8_native",
                "url": master_url.replace("master.m3u8", "360p/index.m3u8"),
                "manifest_url": master_url,
                "vcodec": "avc1.4d401e",
                "acodec": "mp4a.40.2",
            }
        ],
    }


def leak_headers() -> dict[str, str]:
    return {"set-cookie": "upstream-secret=1", "authorization": "Bearer upstream-token"}


def build_service(fetcher: SequencedFetcher, resolver: FakeResolver | None = None, **kwargs) -> LiveHlsRelayService:
    kwargs.setdefault("background", lambda job: None)  # the prewarm has its own test; elsewhere it would race the fakes
    return LiveHlsRelayService(
        resolver=resolver or FakeResolver([MASTER_URL]),
        fetcher=fetcher,
        policy=public_policy(),
        token_factory=_incrementing("stream"),
        resource_token_factory=_incrementing("res"),
        **kwargs,
    )


def _incrementing(prefix: str):
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return factory


def gen(descriptor) -> int:  # noqa: ANN001
    return int(descriptor.playback_url.split("/relay/", 1)[1].split("/", 1)[0])


def _text(service, owner, spec_call) -> str:  # noqa: ANN001
    spec = spec_call
    body = b"".join(spec.body)
    spec.close()
    return body.decode()


def media_playlist_id(master_body: str, stream_id: str, generation: int) -> str:
    prefix = f"/api/remote-streams/{stream_id}/relay/{generation}/r/"
    for line in master_body.splitlines():
        if line.startswith(prefix):
            return line.rsplit("/", 1)[1]
    raise AssertionError(f"no media playlist reference in master:\n{master_body}")


def segment_ids(media_body: str, stream_id: str, generation: int) -> list[str]:
    prefix = f"/api/remote-streams/{stream_id}/relay/{generation}/r/"
    return [line.rsplit("/", 1)[1] for line in media_body.splitlines() if line.startswith(prefix)]


# --- tests -----------------------------------------------------------------

def test_live_source_registers_as_a_non_seekable_live_stream() -> None:
    fetcher = SequencedFetcher({MASTER_URL: [(200, leak_headers(), LIVE_MASTER.encode())]})
    service = build_service(fetcher)
    descriptor = service.register(
        owner_user_id="owner", source_url="https://youtube.com/watch?v=live1",
        info=live_register_info(),
    )
    assert descriptor.status == "ready"
    assert descriptor.transport == "hls"
    # Live must never look like a seekable VOD (#91).
    assert descriptor.seekable is False
    assert descriptor.live is True
    assert descriptor.playback_url.endswith("/master.m3u8")


def test_live_media_playlist_advances_across_fetches() -> None:
    fetcher = SequencedFetcher({
        MASTER_URL: [(200, {}, LIVE_MASTER.encode())],
        MEDIA_URL: [
            (200, leak_headers(), live_media(100, [100, 101, 102]).encode()),
            (200, leak_headers(), live_media(101, [101, 102, 103]).encode()),
        ],
    })
    service = build_service(fetcher)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    master = _text(service, "owner", service.serve_master("owner", d.stream_id, gen(d)))
    media_id = media_playlist_id(master, d.stream_id, gen(d))

    first = _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))
    second = _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))

    assert "#EXT-X-MEDIA-SEQUENCE:100" in first
    assert "#EXT-X-MEDIA-SEQUENCE:101" in second
    # A live playlist never carries ENDLIST; the edge simply advances.
    assert "#EXT-X-ENDLIST" not in first and "#EXT-X-ENDLIST" not in second
    # Still-live segments keep a stable opaque id across fetches so an in-flight
    # segment request stays resolvable.
    first_ids = segment_ids(first, d.stream_id, gen(d))
    second_ids = segment_ids(second, d.stream_id, gen(d))
    assert len(first_ids) == 3 and len(second_ids) == 3
    assert first_ids[1:] == second_ids[:2]  # seg101, seg102 keep their ids
    assert second_ids[2] not in first_ids   # seg103 is new


def test_live_playlist_preserves_discontinuities_without_leaking_addresses() -> None:
    fetcher = SequencedFetcher({
        MASTER_URL: [(200, {}, LIVE_MASTER.encode())],
        MEDIA_URL: [(200, leak_headers(), live_media(200, [200, 201], discontinuity_at=1).encode())],
        "https://cdn.example/live/360p/seg200.m4s": [(200, leak_headers(), b"AAAA")],
    })
    service = build_service(fetcher)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    master = _text(service, "owner", service.serve_master("owner", d.stream_id, gen(d)))
    assert "cdn.example" not in master and "upstream-secret" not in master
    media_id = media_playlist_id(master, d.stream_id, gen(d))
    media = _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))
    assert "#EXT-X-DISCONTINUITY" in media
    assert "cdn.example" not in media
    assert "upstream-secret" not in media and "Bearer" not in media
    # A rewritten segment still serves its bytes through the guarded transport.
    seg_id = segment_ids(media, d.stream_id, gen(d))[0]
    spec = service.serve_resource("owner", d.stream_id, gen(d), seg_id)
    assert b"".join(spec.body) == b"AAAA"
    spec.close()


def test_live_resource_map_is_bounded_across_long_advancement() -> None:
    # A 3-segment rolling window advanced far past the resource cap must keep the
    # map bounded while the current edge stays resolvable.
    windows = [(200, {}, live_media(seq, [seq, seq + 1, seq + 2]).encode()) for seq in range(300, 340)]
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())], MEDIA_URL: windows})
    service = build_service(fetcher, max_resources_per_generation=6)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    master = _text(service, "owner", service.serve_master("owner", d.stream_id, gen(d)))
    media_id = media_playlist_id(master, d.stream_id, gen(d))

    last_media = ""
    for _ in range(len(windows)):
        last_media = _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))

    generation_state = service._records[d.stream_id].generations[gen(d)]  # noqa: SLF001
    assert len(generation_state.resources_by_id) <= 6
    # The current edge segments remain resolvable despite eviction of old ones,
    # and the long-lived media-playlist pointer the browser keeps re-fetching is
    # never evicted.
    assert media_id in generation_state.resources_by_id
    for seg_id in segment_ids(last_media, d.stream_id, gen(d)):
        assert seg_id in generation_state.resources_by_id


def test_live_signed_master_refreshes_to_the_next_generation() -> None:
    next_master = "https://cdn.example/live/master.m3u8?token=g2"
    fetcher = SequencedFetcher({
        MASTER_URL: [(200, {}, LIVE_MASTER.encode())],
        next_master: [(200, {}, LIVE_MASTER.encode())],
    })
    resolver = FakeResolver([MASTER_URL, next_master])
    service = build_service(fetcher, resolver=resolver)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    assert gen(d) == 1
    refreshed = service.refresh("owner", d.stream_id)
    assert refreshed.status == "ready"
    assert gen(refreshed) == 2
    assert refreshed.live is True and refreshed.seekable is False


def test_live_stream_end_is_surfaced_when_the_source_stops_resolving() -> None:
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())]})
    resolver = FakeResolver([MASTER_URL])

    def resolve_gone(source_url, owner_user_id):  # noqa: ANN001
        return {"extractor_key": "Youtube", "is_live": False, "formats": []}

    resolver.resolve = resolve_gone  # type: ignore[assignment]
    service = build_service(fetcher, resolver=resolver)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    ended = service.refresh("owner", d.stream_id)
    assert ended.status == "unsupported"
    assert ended.fallback_code == "live_stream_ended"


def test_live_stream_is_owner_isolated() -> None:
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())]})
    service = build_service(fetcher)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    with pytest.raises(StreamNotFoundError):
        service.serve_master("intruder", d.stream_id, gen(d))
    with pytest.raises(StreamNotFoundError):
        service.refresh("intruder", d.stream_id)
    with pytest.raises(StreamNotFoundError):
        service.release("intruder", d.stream_id)


def test_live_per_member_and_global_session_limits_are_bounded() -> None:
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())]})
    service = build_service(fetcher, max_streams_per_user=1, max_streams_global=2)
    service.register(owner_user_id="a", source_url="s", info=live_register_info())
    from app.services.remote_streaming import UnsupportedPlaybackError
    with pytest.raises(UnsupportedPlaybackError):
        service.register(owner_user_id="a", source_url="s", info=live_register_info())
    service.register(owner_user_id="b", source_url="s", info=live_register_info())
    with pytest.raises(UnsupportedPlaybackError):
        service.register(owner_user_id="c", source_url="s", info=live_register_info())


# --- fail-closed negatives on the live media-playlist path -----------------
# These lock the guarded-transport invariant on the live override: the thin
# LiveHlsRelayService._serve_media_playlist delegates to the shared fail-closed
# rewrite_media / _fetch_document, so a future edit that dropped the residual
# scan, the byte budget, or the policy revalidation would break these.

def _serve_hostile_media(hostile_media: str, *, extra=None, **kwargs):
    responses = {
        MASTER_URL: [(200, {}, LIVE_MASTER.encode())],
        MEDIA_URL: [(200, leak_headers(), hostile_media.encode())],
    }
    responses.update(extra or {})
    service = build_service(SequencedFetcher(responses), **kwargs)
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    master = _text(service, "owner", service.serve_master("owner", d.stream_id, gen(d)))
    media_id = media_playlist_id(master, d.stream_id, gen(d))
    return service, d, media_id


def test_live_media_playlist_with_rendition_report_fails_closed_without_leaking() -> None:
    hostile = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-RENDITION-REPORT:URI=\"https://cdn.example/live/1080/report.m3u8?sig=SECRETTOKEN\",LAST-MSN=9\n"
        "#EXTINF:4.0,\nseg0.m4s\n"
    )
    service, d, media_id = _serve_hostile_media(hostile)
    with pytest.raises(UnsupportedPlaybackError) as caught:
        service.serve_resource("owner", d.stream_id, gen(d), media_id)
    assert "SECRETTOKEN" not in str(caught.value)


def test_live_media_playlist_that_is_malformed_fails_closed() -> None:
    service, d, media_id = _serve_hostile_media("this is not a playlist\n")
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_resource("owner", d.stream_id, gen(d), media_id)


def test_live_media_playlist_exceeding_the_byte_budget_fails_closed() -> None:
    oversized = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n" + "".join(f"#EXTINF:4.0,\nseg{i}.m4s\n" for i in range(200))
    service, d, media_id = _serve_hostile_media(oversized, max_manifest_bytes=256)
    with pytest.raises(UnsupportedPlaybackError):
        service.serve_resource("owner", d.stream_id, gen(d), media_id)


def test_live_media_playlist_referencing_private_space_fails_closed() -> None:
    hostile = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\nhttps://internal.example/seg0.m4s\n"
    )
    service, d, media_id = _serve_hostile_media(hostile)
    with pytest.raises(PublicSourcePolicyError):
        service.serve_resource("owner", d.stream_id, gen(d), media_id)


def test_an_abandoned_live_session_frees_the_members_slot_once_idle() -> None:
    """A hard navigation or reload never releases its relay. A playing live stream polls its playlist every few
    seconds, so at the member's cap one silent for 15 s is abandoned and gives way; a polled one never does."""
    now = {"t": 1_000.0}
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())]})
    service = build_service(fetcher, max_streams_per_user=1, idle_ttl_seconds=60, clock=lambda: now["t"])
    first = service.register(owner_user_id="a", source_url="s", info=live_register_info())
    from app.services.remote_streaming import UnsupportedPlaybackError
    now["t"] += 14
    with pytest.raises(UnsupportedPlaybackError):
        service.register(owner_user_id="a", source_url="s", info=live_register_info())
    now["t"] += 1
    service.register(owner_user_id="a", source_url="s", info=live_register_info())
    assert not service.has_owned_stream("a", first.stream_id)


def test_live_target_duration_is_tightened_to_the_listed_segments() -> None:
    """Twitch and Kick (IVS) advertise TARGETDURATION:6 over 2 s segments; hls.js holds 3 target durations behind the
    edge, so the relay's live playlists sat 18 s behind it instead of 6. The tightened value never undercuts a segment."""
    media = (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:7\n"
        "#EXTINF:2.000,live\nseg7.ts\n#EXTINF:2.002,live\nseg8.ts\n"
    )
    service, d, media_id = _serve_hostile_media(media)
    body = _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))
    assert "#EXT-X-TARGETDURATION:2\n" in body  # RFC 8216: each EXTINF rounded to the nearest integer fits
    assert body.count("#EXT-X-TARGETDURATION") == 1
    # A playlist that already advertises its real segment length passes through unchanged.
    service, d, media_id = _serve_hostile_media(live_media(1, [1, 2]))
    assert "#EXT-X-TARGETDURATION:4\n" in _text(service, "owner", service.serve_resource("owner", d.stream_id, gen(d), media_id))


def test_registering_a_live_stream_prepares_its_master_and_variant_connection_before_the_player_asks() -> None:
    """While the browser loads the player (~300 ms), the relay renders the master and opens the variant host's
    connection, so the player's first two requests skip an upstream round trip and a TLS handshake each."""
    fetcher = SequencedFetcher({MASTER_URL: [(200, {}, LIVE_MASTER.encode())]})
    preconnected: list[str] = []
    fetcher.preconnect = preconnected.append  # type: ignore[attr-defined]
    service = build_service(fetcher, background=lambda job: job())
    d = service.register(owner_user_id="owner", source_url="s", info=live_register_info())
    assert fetcher.calls == [MASTER_URL]
    assert preconnected == [MEDIA_URL]
    master = _text(service, "owner", service.serve_master("owner", d.stream_id, gen(d)))
    assert fetcher.calls == [MASTER_URL]  # served from the prepared render
    assert media_playlist_id(master, d.stream_id, gen(d))
