#!/usr/bin/env python3
"""Opt-in component timings for guarded HLS/live-HLS/DASH relay services.

This does not contact a provider or start an HTTP server.  Every upstream read
comes from deterministic in-memory fakes that expose only a public fixture
address to the services.  It emits raw wall and process-CPU samples; a wrapper
such as ``audit_run.py`` is responsible for host/process RSS observations.

Example (only in a quiet benchmark window)::

    mise exec -- backend/.venv/bin/python scripts/perf/audit_media_services.py \
      --out output/performance-audit/media-services/native.json

The 20 samples are deliberately not summarized as a percentile.  They cover
component work only: descriptor creation, manifest rewriting, bounded rolling
state, and relayed bytes.  They exclude HTTP transport, DNS, providers,
decoders, browser/MSE, and real network latency.
"""

from __future__ import annotations

import argparse
import ipaddress
import itertools
import json
import re
import socket
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import Any, Callable, TypeVar

# This script is intentionally runnable from the repository root without a
# production app import/configuration.  Nothing below starts FastAPI or reads a
# household configuration.
REPOSITORY = Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.hls_relay import HlsRelayService  # noqa: E402
from app.services.live_hls_relay import LiveHlsRelayService  # noqa: E402
from app.services.network_policy import PublicSourcePolicy  # noqa: E402
from app.services.remote_streaming import (  # noqa: E402
    BrowserCapabilities,
    ByteRange,
    RemoteStreamingService,
    UpstreamMediaResponse,
)

SAMPLES = 20
OWNER = "benchmark-member"
FIXTURE_HOST = "media.example"
FIXTURE_IP = "93.184.216.34"  # TEST-NET/public documentation address, never contacted.
VOD_MASTER_URL = f"https://{FIXTURE_HOST}/vod/master.m3u8"
VOD_MEDIA_URL = f"https://{FIXTURE_HOST}/vod/360/index.m3u8"
LIVE_MASTER_URL = f"https://{FIXTURE_HOST}/live/master.m3u8"
LIVE_MEDIA_URL = f"https://{FIXTURE_HOST}/live/360/index.m3u8"
_RESOURCE = re.compile(r"/r/([A-Za-z0-9_-]+)")
T = TypeVar("T")

VOD_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\"
360/index.m3u8
"""
VOD_MEDIA = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:4
#EXT-X-MAP:URI=\"init.mp4\"
#EXTINF:4.0,
seg0.m4s
#EXTINF:4.0,
seg1.m4s
#EXT-X-ENDLIST
"""
LIVE_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\"
360/index.m3u8
"""


def _fixture_resolver(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
    """A strict resolver proves fakes cannot reach loopback/private services."""
    del family
    if host != FIXTURE_HOST:
        raise socket.gaierror(f"unexpected benchmark host: {host}")
    address = ipaddress.ip_address(FIXTURE_IP)
    if address.is_loopback or address.is_private or address.is_link_local:
        raise AssertionError("benchmark fixture address must be public and non-loopback")
    return [(socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (str(address), port))]


def _policy() -> PublicSourcePolicy:
    return PublicSourcePolicy(resolver=_fixture_resolver)


def _drain(response) -> bytes:  # noqa: ANN001
    try:
        return b"".join(response.body)
    finally:
        response.close()


def _resource_ids(text: str) -> list[str]:
    return _RESOURCE.findall(text)


def _timed(action: Callable[[], T]) -> tuple[T, dict[str, float]]:
    cpu_before = process_time_ns()
    wall_before = perf_counter_ns()
    result = action()
    return result, {
        "wall_ms": round((perf_counter_ns() - wall_before) / 1_000_000, 6),
        "process_cpu_ms": round((process_time_ns() - cpu_before) / 1_000_000, 6),
    }


def _counter(prefix: str) -> Callable[[], str]:
    values = itertools.count(1)
    return lambda: f"{prefix}-{next(values)}"


@dataclass
class HlsFetcher:
    responses: dict[str, bytes]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, *, headers=None, byte_range: ByteRange | None = None, timeout_seconds=None):  # noqa: ANN001
        del headers, timeout_seconds
        self.calls.append(url)
        body = self.responses.get(url)
        if body is None:
            return UpstreamMediaResponse(404, {}, [b""])
        start = byte_range.start if byte_range and byte_range.start is not None else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(body) - 1
        payload = body[start : end + 1]
        status = 206 if byte_range else 200
        headers_out = {"content-length": str(len(payload)), "content-type": "application/octet-stream"}
        if byte_range:
            headers_out["content-range"] = f"bytes {start}-{end}/{len(body)}"
        return UpstreamMediaResponse(status, headers_out, [payload])


class StaticResolver:
    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info

    def resolve(self, source_url: str, owner_user_id: str) -> dict[str, Any]:
        del source_url, owner_user_id
        return self.info


def _hls_info(master_url: str, *, live: bool) -> dict[str, Any]:
    return {
        "extractor_key": "Youtube" if live else "TwitchVod",
        "webpage_url": "https://source.invalid/live" if live else "https://source.invalid/vod",
        "is_live": live,
        "manifest_url": master_url,
        "formats": [{
            "format_id": "360p", "protocol": "m3u8_native",
            "url": master_url.replace("master.m3u8", "360/index.m3u8"), "manifest_url": master_url,
            "vcodec": "avc1.4d401e", "acodec": "mp4a.40.2",
        }],
    }


def _vod_service() -> HlsRelayService:
    fetcher = HlsFetcher({
        VOD_MASTER_URL: VOD_MASTER.encode(), VOD_MEDIA_URL: VOD_MEDIA.encode(),
        f"https://{FIXTURE_HOST}/vod/360/init.mp4": b"benchmark-init",
        f"https://{FIXTURE_HOST}/vod/360/seg0.m4s": b"benchmark-segment-0",
        f"https://{FIXTURE_HOST}/vod/360/seg1.m4s": b"benchmark-segment-1",
    })
    return HlsRelayService(
        resolver=StaticResolver(_hls_info(VOD_MASTER_URL, live=False)), fetcher=fetcher, policy=_policy(),
        token_factory=_counter("vod-stream"), resource_token_factory=_counter("vod-resource"), clock=lambda: 1_000.0,
    )


def _live_playlist(sequence: int) -> bytes:
    segment_lines: list[str] = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:4", f"#EXT-X-MEDIA-SEQUENCE:{sequence}"]
    for segment in range(sequence, sequence + 3):
        segment_lines.extend(["#EXTINF:4.0,", f"seg{segment}.m4s"])
    return ("\n".join(segment_lines) + "\n").encode()


@dataclass
class RollingHlsFetcher:
    media_reads: int = 0
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, *, headers=None, byte_range: ByteRange | None = None, timeout_seconds=None):  # noqa: ANN001
        del headers, byte_range, timeout_seconds
        self.calls.append(url)
        if url == LIVE_MASTER_URL:
            body = LIVE_MASTER.encode()
        elif url == LIVE_MEDIA_URL:
            body = _live_playlist(100 + self.media_reads)
            self.media_reads += 1
        elif url.startswith(f"https://{FIXTURE_HOST}/live/360/seg"):
            body = f"benchmark-live-{url.rsplit('/', 1)[1]}".encode()
        else:
            return UpstreamMediaResponse(404, {}, [b""])
        return UpstreamMediaResponse(200, {"content-length": str(len(body))}, [body])


def _live_service(fetcher: RollingHlsFetcher) -> LiveHlsRelayService:
    return LiveHlsRelayService(
        resolver=StaticResolver(_hls_info(LIVE_MASTER_URL, live=True)), fetcher=fetcher, policy=_policy(),
        token_factory=_counter("live-stream"), resource_token_factory=_counter("live-resource"),
        clock=lambda: 1_000.0, background=lambda job: None, max_resources_per_generation=8,
    )


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _dash_media() -> bytes:
    sidx = b"\x00\x00\x00\x00" + struct.pack(">II", 1, 1_000) + struct.pack(">II", 0, 0) + struct.pack(">HH", 0, 1) + struct.pack(">III", 16, 6_000, 0x90000000)
    return _box(b"ftyp", b"iso6") + _box(b"moov", b"") + _box(b"sidx", sidx) + _box(b"moof", b"") + _box(b"mdat", b"benchmark-dash-payload")


class NoResolver:
    def resolve(self, source_url: str, owner_user_id: str):  # noqa: ANN001
        raise AssertionError((source_url, owner_user_id))


@dataclass
class DashReader:
    payload: bytes
    opens: list[tuple[str, ByteRange | None]] = field(default_factory=list)

    def open(self, track, byte_range: ByteRange | None, timeout_seconds=None):  # noqa: ANN001
        del timeout_seconds
        if url_host(track.url) != FIXTURE_HOST:
            raise AssertionError("DASH benchmark reader accepted a non-fixture host")
        self.opens.append((track.format_id, byte_range))
        start = byte_range.start if byte_range and byte_range.start is not None else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(self.payload) - 1
        body = self.payload[start : end + 1]
        return UpstreamMediaResponse(206 if byte_range else 200, {
            "Content-Length": str(len(body)), "Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(self.payload)}",
        }, [body])


class NoFullFilePackager:
    def can_package(self, video, audio) -> bool:  # noqa: ANN001
        del video, audio
        return False

    def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
        raise AssertionError((stream_id, generation, video, audio))

    def close(self, stream_id: str) -> None:
        del stream_id

    def close_generation(self, stream_id: str, generation: int) -> None:
        del stream_id, generation


def url_host(url: str) -> str:
    # Avoid a real resolver/network stack in the component harness.
    return url.split("//", 1)[1].split("/", 1)[0].split("@")[-1].split(":", 1)[0]


def _dash_info(payload: bytes) -> dict[str, Any]:
    def track(format_id: str, *, video: bool) -> dict[str, Any]:
        return {
            "format_id": format_id, "url": f"https://{FIXTURE_HOST}/{format_id}.{'mp4' if video else 'm4a'}", "protocol": "https",
            "ext": "mp4" if video else "m4a", "vcodec": "avc1.640028" if video else "none", "acodec": "none" if video else "mp4a.40.2",
            "width": 640 if video else None, "height": 360 if video else None, "fps": 24 if video else None, "tbr": 400 if video else 96,
            "filesize": len(payload), "container": "mp4_dash" if video else "m4a_dash",
        }
    return {"extractor_key": "Fixture", "formats": [track("video", video=True), track("audio", video=False)]}


def _dash_service(payload: bytes) -> RemoteStreamingService:
    return RemoteStreamingService(
        resolver=NoResolver(), reader=DashReader(payload), hls_packager=NoFullFilePackager(),
        token_factory=_counter("dash-stream"), rendition_token_factory=_counter("dash-rendition"), clock=lambda: 1_000.0,
    )


def _hls_sample(trial: int) -> dict[str, Any]:
    service = _vod_service()
    descriptor, register = _timed(lambda: service.register(owner_user_id=OWNER, source_url="https://source.invalid/vod", info=_hls_info(VOD_MASTER_URL, live=False)))
    if descriptor.status != "ready" or descriptor.transport != "hls":
        raise AssertionError("VOD descriptor did not register as ready HLS")
    master_spec, master_timing = _timed(lambda: service.serve_master(OWNER, descriptor.stream_id, 1))
    master = _drain(master_spec).decode()
    ids = _resource_ids(master)
    if FIXTURE_HOST in master or len(ids) != 1:
        raise AssertionError("VOD master did not rewrite one opaque resource")
    media_spec, media_timing = _timed(lambda: service.serve_resource(OWNER, descriptor.stream_id, 1, ids[0]))
    media = _drain(media_spec).decode()
    media_ids = _resource_ids(media)
    if FIXTURE_HOST in media or len(media_ids) != 3:
        raise AssertionError("VOD media did not rewrite map and segments")
    segment_spec, segment_timing = _timed(lambda: service.serve_resource(OWNER, descriptor.stream_id, 1, media_ids[-1]))
    segment = _drain(segment_spec)
    if segment != b"benchmark-segment-1":
        raise AssertionError("VOD segment bytes changed during relay")
    return {"trial": trial, "register": register, "master_rewrite": master_timing, "media_rewrite": media_timing, "segment_relay": segment_timing, "correctness": {"descriptor": "ready-hls", "opaque_master": True, "opaque_media": True, "segment_bytes": len(segment)}}


def _live_sample(trial: int) -> dict[str, Any]:
    fetcher = RollingHlsFetcher()
    service = _live_service(fetcher)
    descriptor, register = _timed(lambda: service.register(owner_user_id=OWNER, source_url="https://source.invalid/live", info=_hls_info(LIVE_MASTER_URL, live=True)))
    if descriptor.status != "ready" or not descriptor.live or descriptor.seekable:
        raise AssertionError("live descriptor did not retain non-seekable live semantics")
    master_spec, master_timing = _timed(lambda: service.serve_master(OWNER, descriptor.stream_id, 1))
    master = _drain(master_spec).decode()
    media_id = _resource_ids(master)[0]
    first_spec, first_timing = _timed(lambda: service.serve_resource(OWNER, descriptor.stream_id, 1, media_id))
    first = _drain(first_spec).decode()
    first_ids = _resource_ids(first)
    if "#EXT-X-ENDLIST" in first or len(first_ids) != 3:
        raise AssertionError("live media was not a three-segment current-edge playlist")
    rolling_start = perf_counter_ns()
    rolling_cpu_start = process_time_ns()
    latest = first
    for _ in range(31):
        latest = _drain(service.serve_resource(OWNER, descriptor.stream_id, 1, media_id)).decode()
    rolling_timing = {"wall_ms": round((perf_counter_ns() - rolling_start) / 1_000_000, 6), "process_cpu_ms": round((process_time_ns() - rolling_cpu_start) / 1_000_000, 6), "iterations": 31}
    latest_ids = _resource_ids(latest)
    state = service._records[descriptor.stream_id].generations[1]  # bounded-state contract; no I/O.
    if len(state.resources_by_id) > 8 or not latest_ids:
        raise AssertionError("live rolling resource map exceeded its configured budget")
    segment_spec, segment_timing = _timed(lambda: service.serve_resource(OWNER, descriptor.stream_id, 1, latest_ids[-1]))
    segment = _drain(segment_spec)
    if not segment.startswith(b"benchmark-live-seg"):
        raise AssertionError("live segment bytes changed during relay")
    return {"trial": trial, "register": register, "master_rewrite": master_timing, "first_media_rewrite": first_timing, "rolling_media_rewrites": rolling_timing, "segment_relay": segment_timing, "correctness": {"non_seekable_live": True, "media_reads": fetcher.media_reads, "resource_map_entries": len(state.resources_by_id), "resource_map_limit": 8, "segment_bytes": len(segment)}}


def _dash_sample(trial: int) -> dict[str, Any]:
    payload = _dash_media()
    service = _dash_service(payload)
    descriptor, register = _timed(lambda: service.register(
        owner_user_id=OWNER, source_url="https://source.invalid/dash", info=_dash_info(payload),
        browser_capabilities=BrowserCapabilities(frozenset({"dash-segment-base", "mp4-avc-aac", "m4a-aac"})),
    ))
    if descriptor.status != "ready" or descriptor.transport != "dash":
        raise AssertionError("DASH descriptor did not register as ready DASH")
    manifest_spec, manifest_timing = _timed(lambda: service.serve_dash(OWNER, descriptor.stream_id, 1, "manifest.mpd"))
    manifest = _drain(manifest_spec)
    if FIXTURE_HOST.encode() in manifest or b"/dash/1/video-0" not in manifest or b"/dash/1/audio" not in manifest:
        raise AssertionError("DASH manifest leaked the fixture address or missed bound resources")
    segment_spec, segment_timing = _timed(lambda: service.serve_dash(OWNER, descriptor.stream_id, 1, "video-0", "bytes=0-15"))
    segment = _drain(segment_spec)
    if segment != payload[:16]:
        raise AssertionError("DASH cached init/index range bytes changed during relay")
    return {"trial": trial, "register": register, "manifest_rewrite_and_probe": manifest_timing, "segment_relay": segment_timing, "correctness": {"descriptor": "ready-dash", "opaque_manifest": True, "segment_bytes": len(segment)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="JSON raw-sample destination")
    args = parser.parse_args()

    # Deliberately reserve a private temp directory as an isolation witness.
    # Components use in-memory fakes; no app database, config, host socket, or
    # provider credential is read from it or from the runtime environment.
    with tempfile.TemporaryDirectory(prefix="lumina-media-services-audit-") as temporary:
        results = {
            "schema": 1,
            "sample_count": SAMPLES,
            "statistics": "Raw samples only. N=20 is not summarized as a p95/p99 by this harness.",
            "isolation": {"temporary_workspace": True, "loopback_denied": True, "fixture_host": "public deterministic fixture", "temporary_workspace_name": Path(temporary).name},
            "qualification": "In-memory/file-free component fakes. Excludes HTTP server/client transport, DNS/network timing, provider behavior, authentication, external storage, FFmpeg, browser/MSE, and decode/GPU work.",
            "cells": [
                {"name": "hls_vod_register_master_media_segment", "samples": [_hls_sample(trial) for trial in range(1, SAMPLES + 1)]},
                {"name": "live_hls_register_master_rolling_media_segment", "samples": [_live_sample(trial) for trial in range(1, SAMPLES + 1)]},
                {"name": "dash_register_manifest_segment", "samples": [_dash_sample(trial) for trial in range(1, SAMPLES + 1)]},
            ],
            "unsupported": [
                "HTTP transport, TLS, real DNS and provider/CDN latency",
                "real credentials, household hosts, and provider resolver behavior",
                "browser hls.js/dash.js/MSE, decoder/GPU/audio allocations and rendered frames",
                "FFmpeg/remux/transcode process CPU and RSS",
                "concurrency/load saturation and persistent cache/database behavior",
            ],
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
