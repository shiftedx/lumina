"""Comparable Jellyfin API, range-stream, and HLS measurements.

Both Lumina and Jellyfin expose the Jellyfin API used here.  Each target is a
JSON file with ``name``, ``base_url``, ``username``, ``password`` and optionally
``container``.  The output is structured JSON and never includes credentials or
access tokens.

    backend/.venv/bin/python scripts/perf/compare_servers.py target.json --out result.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

AUTH = 'MediaBrowser Client="LuminaCompare", Device="benchmark", DeviceId="lumina-compare", Version="1"'
DIRECT_TITLE = "Benchmark Direct"
TRANSCODE_TITLE = "Benchmark Transcode"
TRANSCODE_PROFILE = {
    "MaxStreamingBitrate": 2_000_000,
    "DirectPlayProfiles": [],
    "TranscodingProfiles": [{
        "Type": "Video", "Container": "mp4", "Protocol": "hls", "VideoCodec": "h264", "AudioCodec": "aac",
        "Context": "Streaming", "MaxAudioChannels": "2", "MinSegments": 1, "SegmentLength": 1,
    }],
    "CodecProfiles": [{
        "Type": "Video", "Codec": "h264",
        "Conditions": [
            {"Condition": "LessThanEqual", "Property": "Width", "Value": "854"},
            {"Condition": "LessThanEqual", "Property": "Height", "Value": "480"},
        ],
    }],
}


@dataclass(frozen=True)
class Target:
    name: str
    base_url: str
    username: str
    password: str
    container: str | None = None

    @classmethod
    def read(cls, path: Path) -> "Target":
        raw = json.loads(path.read_text())
        return cls(**{key: raw.get(key) for key in ("name", "base_url", "username", "password", "container")})


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    return ordered[max(0, math.ceil(value * len(ordered)) - 1)]


def distribution(values: list[float]) -> dict:
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(statistics.fmean(values), 3),
    }


def json_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def response_shape(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {"kind": "non_json", "decoded_sha256": hashlib.sha256(response.content).hexdigest()}
    output = {
        "kind": type(body).__name__,
        "decoded_sha256": hashlib.sha256(response.content).hexdigest(),
        "canonical_json_sha256": json_digest(body),
        "decoded_bytes": len(response.content),
    }
    if isinstance(body, dict):
        output["top_level_keys"] = sorted(body)
        items = body.get("Items")
        if isinstance(items, list):
            output.update({
                "returned_item_count": len(items),
                "item_key_sets_sha256": json_digest([sorted(item) for item in items if isinstance(item, dict)]),
                "item_ids_sha256": json_digest([item.get("Id") for item in items if isinstance(item, dict)]),
                "item_names_sha256": json_digest([item.get("Name") for item in items if isinstance(item, dict)]),
            })
    return output


def parse_bytes(value: str) -> int:
    match = re.fullmatch(r"([\d.]+)([KMGTP]?i?B)", value.strip())
    if not match:
        return 0
    units = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
             "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return round(float(match.group(1)) * units[match.group(2)])


class DockerSampler:
    def __init__(self, container: str | None) -> None:
        self.container = container
        self.rows: list[dict] = []
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.before: dict | None = None
        self.after: dict | None = None

    def __enter__(self) -> "DockerSampler":
        if self.container:
            self.before = self._snapshot()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def _run(self) -> None:
        while not self.stop.is_set():
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", self.container],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                cpu, memory = result.stdout.strip().split("|", 1)
                top = subprocess.run(
                    ["docker", "top", self.container, "-eo", "pid,rss"],
                    capture_output=True, text=True,
                )
                process_rss = sum(
                    int(line.split()[1]) * 1024 for line in top.stdout.splitlines()[1:]
                    if len(line.split()) == 2 and line.split()[1].isdigit()
                ) if top.returncode == 0 else None
                self.rows.append({
                    "at": time.time(),
                    "cpu_percent": float(cpu.rstrip("%")),
                    "cgroup_memory_usage_bytes": parse_bytes(memory.split("/", 1)[0]),
                    "process_rss_bytes": process_rss,
                })
            self.stop.wait(0.25)

    def _snapshot(self) -> dict:
        result = subprocess.run(
            [
                "docker", "exec", self.container, "sh", "-c",
                "cat /sys/fs/cgroup/cpu.stat; echo __MEMORY__; cat /sys/fs/cgroup/memory.current",
            ],
            capture_output=True, text=True,
        )
        cpu: dict[str, int] = {}
        memory = None
        section = "cpu"
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line == "__MEMORY__":
                    section = "memory"
                elif section == "cpu" and len(parts := line.split()) == 2 and parts[1].isdigit():
                    cpu[parts[0]] = int(parts[1])
                elif section == "memory" and line.isdigit():
                    memory = int(line)
        return {"at_monotonic": time.monotonic(), "cpu": cpu, "cgroup_memory_usage_bytes": memory}

    def __exit__(self, *_exc) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.container:
            self.after = self._snapshot()

    def summary(self) -> dict:
        cumulative = None
        if self.before and self.after and "usage_usec" in self.before["cpu"] and "usage_usec" in self.after["cpu"]:
            elapsed = self.after["at_monotonic"] - self.before["at_monotonic"]
            cpu_seconds = (self.after["cpu"]["usage_usec"] - self.before["cpu"]["usage_usec"]) / 1_000_000
            cumulative = {
                "measurement_seconds": round(elapsed, 4),
                "cpu_seconds": round(cpu_seconds, 6),
                "cpu_percent_over_measurement": round(cpu_seconds / elapsed * 100, 2),
                "cgroup_memory_before_bytes": self.before["cgroup_memory_usage_bytes"],
                "cgroup_memory_after_bytes": self.after["cgroup_memory_usage_bytes"],
            }
        if not self.rows:
            return {"samples": 0, "cgroup_counter_delta": cumulative, "note": "no docker-stats interval sample returned"}
        cpu = [row["cpu_percent"] for row in self.rows]
        cgroup = [row["cgroup_memory_usage_bytes"] for row in self.rows]
        rss = [row["process_rss_bytes"] for row in self.rows if row["process_rss_bytes"] is not None]
        return {
            "samples": len(self.rows),
            "cpu_percent_mean": round(statistics.fmean(cpu), 2),
            "cpu_percent_max": round(max(cpu), 2),
            "cgroup_memory_usage_bytes_mean": round(statistics.fmean(cgroup)),
            "cgroup_memory_usage_bytes_max": max(cgroup),
            "process_rss_bytes_mean": round(statistics.fmean(rss)) if rss else None,
            "process_rss_bytes_max": max(rss) if rss else None,
            "cgroup_counter_delta": cumulative,
            "raw": self.rows,
        }


def client_and_identity(target: Target) -> tuple[httpx.Client, str, str]:
    client = httpx.Client(base_url=target.base_url.rstrip("/"), timeout=120, trust_env=False)
    response = client.post(
        "/Users/AuthenticateByName",
        headers={"Authorization": AUTH},
        json={"Username": target.username, "Pw": target.password},
    )
    response.raise_for_status()
    body = response.json()
    token = body["AccessToken"]
    client.headers["Authorization"] = f'{AUTH}, Token="{token}"'
    return client, body["User"]["Id"], token


def timed_call(
    client: httpx.Client, method: str, path: str, headers: dict[str, str] | None = None,
    *, hash_body: bool = False,
) -> dict:
    started = time.perf_counter()
    try:
        response = client.request(method, path, headers=headers)
        content_length = response.headers.get("content-length")
        return {
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "status": response.status_code,
            "decoded_bytes": len(response.content),
            "wire_content_length_bytes": int(content_length) if content_length and content_length.isdigit() else None,
            "content_encoding": response.headers.get("content-encoding", "identity"),
            "vary": response.headers.get("vary"),
            "decoded_sha256": hashlib.sha256(response.content).hexdigest() if hash_body else None,
        }
    except httpx.RequestError as error:
        return {
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "status": f"error:{type(error).__name__}",
            "decoded_bytes": 0,
            "wire_content_length_bytes": None,
            "content_encoding": None,
            "vary": None,
            "decoded_sha256": None,
        }


def representation_benchmark(client: httpx.Client, target: Target, path: str, block_count: int = 25) -> dict:
    """Order-balanced identity/gzip samples for one representative 60-card response."""
    blocks = []
    for encoding in ("identity", "gzip", "gzip", "identity"):
        for _ in range(5):
            response = client.get(path, headers={"Accept-Encoding": encoding})
            response.raise_for_status()
        with DockerSampler(target.container) as sampler:
            rows = [timed_call(
                client, "GET", path, {"Accept-Encoding": encoding}, hash_body=True,
            ) for _ in range(block_count)]
        blocks.append({"requested_encoding": encoding, "rows": rows, "container": sampler.summary()})
    output = {}
    for encoding in ("identity", "gzip"):
        selected = [block for block in blocks if block["requested_encoding"] == encoding]
        rows = [row for block in selected for row in block["rows"]]
        counters = [
            block["container"].get("cgroup_counter_delta") for block in selected
            if block["container"].get("cgroup_counter_delta")
        ]
        wire = [row["wire_content_length_bytes"] for row in rows if row["wire_content_length_bytes"] is not None]
        output[encoding] = {
            **distribution([row["elapsed_ms"] for row in rows]),
            "statuses": {str(status): sum(row["status"] == status for row in rows) for status in sorted({row["status"] for row in rows}, key=str)},
            "decoded_bytes": sorted({row["decoded_bytes"] for row in rows}),
            "wire_content_length_bytes": sorted(set(wire)),
            "wire_content_length_samples": len(wire),
            "content_encodings": sorted({str(row["content_encoding"]) for row in rows}),
            "vary": sorted({str(row["vary"]) for row in rows}),
            "decoded_sha256": sorted({row["decoded_sha256"] for row in rows if row["decoded_sha256"]}),
            "cgroup_cpu_seconds": round(sum(counter["cpu_seconds"] for counter in counters), 6),
            "cgroup_measurement_seconds": round(sum(counter["measurement_seconds"] for counter in counters), 4),
        }
    output["decoded_payloads_equal"] = output["identity"]["decoded_sha256"] == output["gzip"]["decoded_sha256"]
    output["blocks"] = blocks
    output["note"] = "Two 25-request blocks per encoding in identity,gzip,gzip,identity order; Content-Length is the encoded HTTP representation while httpx reports decoded body bytes."
    return output


def api_benchmark(client: httpx.Client, target: Target, user_id: str, count: int) -> dict:
    scenarios = {
        "public_system_info": "/System/Info/Public",
        "current_user": f"/Users/{user_id}",
        "items_page": f"/Users/{user_id}/Items?Recursive=true&IncludeItemTypes=Movie&Limit=60&Fields=MediaSources",
        "items_search": f"/Users/{user_id}/Items?Recursive=true&IncludeItemTypes=Movie&SearchTerm=Benchmark&Limit=60",
    }
    output: dict[str, dict] = {}
    for name, path in scenarios.items():
        for _ in range(10):
            response = client.get(path)
            response.raise_for_status()
        warm_body = response.json()
        warm_shape = response_shape(response)
        response_cardinality = {
            "total_record_count": warm_body.get("TotalRecordCount"),
            "returned_item_count": len(warm_body["Items"]),
        } if isinstance(warm_body, dict) and isinstance(warm_body.get("Items"), list) else None
        output[name] = {}
        for concurrency in (1, 8, 32):
            with DockerSampler(target.container) as sampler:
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    started = time.perf_counter()
                    rows = list(pool.map(lambda _index: timed_call(client, "GET", path), range(count)))
                    wall = time.perf_counter() - started
            latency = [row["elapsed_ms"] for row in rows]
            observed_statuses = {row["status"] for row in rows}
            statuses = {
                str(status): sum(1 for row in rows if row["status"] == status)
                for status in sorted(observed_statuses, key=str)
            }
            wire = [row["wire_content_length_bytes"] for row in rows if row["wire_content_length_bytes"] is not None]
            output[name][str(concurrency)] = {
                **distribution(latency),
                "wall_seconds": round(wall, 4),
                "throughput_requests_per_second": round(count / wall, 2),
                "response_bytes_mean": round(statistics.fmean(row["decoded_bytes"] for row in rows)),
                "wire_content_length_bytes_mean": round(statistics.fmean(wire)) if wire else None,
                "wire_content_length_samples": len(wire),
                "content_encodings": sorted({str(row["content_encoding"]) for row in rows}),
                "vary": sorted({str(row["vary"]) for row in rows}),
                "statuses": statuses,
                "response_cardinality": response_cardinality,
                "warm_response_shape": warm_shape,
                "container": sampler.summary(),
            }
    return output


def item(client: httpx.Client, user_id: str, title: str) -> dict:
    response = client.get(
        f"/Users/{user_id}/Items",
        params={"Recursive": "true", "IncludeItemTypes": "Movie", "SearchTerm": title, "Fields": "MediaSources,MediaStreams"},
    )
    response.raise_for_status()
    exact = [entry for entry in response.json()["Items"] if entry.get("Name") == title]
    if not exact:
        raise RuntimeError(f"target has no exact fixture title {title!r}")
    return exact[0]


def stream_once(client: httpx.Client, path: str, headers: dict[str, str], *, capture: bool = False) -> dict:
    started = time.perf_counter()
    first = None
    total = 0
    status = 0
    content = bytearray()
    response_headers: dict[str, str] = {}
    digest = hashlib.sha256()
    with client.stream("GET", path, headers=headers) as response:
        status = response.status_code
        response_headers = dict(response.headers)
        for chunk in response.iter_bytes():
            if first is None:
                first = (time.perf_counter() - started) * 1000
            total += len(chunk)
            digest.update(chunk)
            if capture:
                content.extend(chunk)
    return {
        "ttfb_ms": first if first is not None else (time.perf_counter() - started) * 1000,
        "total_ms": (time.perf_counter() - started) * 1000,
        "bytes": total,
        "status": status,
        "content_range": response_headers.get("content-range"),
        "content_type": response_headers.get("content-type"),
        "content_encoding": response_headers.get("content-encoding", "identity"),
        "wire_content_length": response_headers.get("content-length"),
        "sha256": digest.hexdigest(),
        "content": bytes(content) if capture else None,
    }


def direct_benchmark(client: httpx.Client, target: Target, user_id: str, runs: int) -> dict:
    found = item(client, user_id, DIRECT_TITLE)
    source = found["MediaSources"][0]
    size = int(source.get("Size") or 0)
    source_id = source.get("Id") or found["Id"]
    path = f"/Videos/{found['Id']}/stream?Static=true&mediaSourceId={source_id}"
    ranges = {
        "first_megabyte": "bytes=0-1048575",
        "middle_megabyte": f"bytes={max(0, size // 2)}-{max(0, size // 2) + 1048575}",
    }
    output = {"item_id": found["Id"], "source_id": source_id, "source_size_bytes": size, "runs": runs, "ranges": {}}
    for name, requested_range in ranges.items():
        rows = [stream_once(client, path, {"Range": requested_range}) for _ in range(runs)]
        output["ranges"][name] = {
            "requested_range": requested_range,
            "ttfb": distribution([row["ttfb_ms"] for row in rows]),
            "completion": distribution([row["total_ms"] for row in rows]),
            "statuses": sorted({row["status"] for row in rows}),
            "bytes": sorted({row["bytes"] for row in rows}),
            "content_ranges": sorted({str(row["content_range"]) for row in rows}),
            "content_types": sorted({str(row["content_type"]) for row in rows}),
            "content_encodings": sorted({str(row["content_encoding"]) for row in rows}),
            "wire_content_lengths": sorted({str(row["wire_content_length"]) for row in rows}),
        }
    requested_bytes = min(size, 64 * 1024 * 1024)
    sustained_range = f"bytes=0-{requested_bytes - 1}"
    output["sustained"] = {}
    for concurrency in (1, 8):
        stream_count = 8
        with DockerSampler(target.container) as sampler:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                started = time.perf_counter()
                rows = list(pool.map(
                    lambda _index: stream_once(client, path, {"Range": sustained_range}),
                    range(stream_count),
                ))
                wall = time.perf_counter() - started
        hashes = sorted({row["sha256"] for row in rows})
        total_bytes = sum(row["bytes"] for row in rows)
        output["sustained"][str(concurrency)] = {
            "concurrency": concurrency,
            "streams": stream_count,
            "requested_range": sustained_range,
            "bytes_per_stream": sorted({row["bytes"] for row in rows}),
            "aggregate_bytes": total_bytes,
            "wall_seconds": round(wall, 4),
            "aggregate_mebibytes_per_second": round(total_bytes / wall / (1024 * 1024), 2),
            "ttfb": distribution([row["ttfb_ms"] for row in rows]),
            "completion": distribution([row["total_ms"] for row in rows]),
            "statuses": sorted({row["status"] for row in rows}),
            "content_ranges": sorted({str(row["content_range"]) for row in rows}),
            "content_encodings": sorted({str(row["content_encoding"]) for row in rows}),
            "wire_content_lengths": sorted({str(row["wire_content_length"]) for row in rows}),
            "sha256": hashes,
            "byte_integrity": len(hashes) == 1 and all(row["bytes"] == requested_bytes for row in rows),
            "container": sampler.summary(),
        }
    return output


def clean_url(value: str) -> str:
    parts = urlsplit(value)
    useful = {
        "audiocodec", "container", "maxheight", "maxstreamingbitrate", "maxwidth",
        "transcodingmaxaudiochannels", "videobitrate", "videocodec",
    }
    query = urlencode([(key, item) for key, item in parse_qsl(parts.query) if key.lower() in useful])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def manifest_uri(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise RuntimeError("manifest contains no media URI")


def init_uri(text: str) -> str | None:
    match = re.search(r'#EXT-X-MAP:.*URI="([^"]+)"', text)
    return match.group(1) if match else None


def first_segment_duration(text: str) -> float | None:
    match = re.search(r"#EXTINF:([\d.]+)", text)
    return float(match.group(1)) if match else None


def probe_segment(initialization: bytes, segment: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as media:
        media.write(initialization)
        media.write(segment)
        media.flush()
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", media.name],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        return {"error": result.stderr.strip()[:500]}
    streams = []
    for stream in json.loads(result.stdout).get("streams", []):
        streams.append({key: stream.get(key) for key in (
            "codec_type", "codec_name", "profile", "pix_fmt", "width", "height", "channels", "sample_rate",
        ) if stream.get(key) is not None})
    return {"streams": streams}


def transcode_once(client: httpx.Client, user_id: str, found: dict) -> dict:
    started = time.perf_counter()
    response = client.post(
        f"/Items/{found['Id']}/PlaybackInfo",
        params={"UserId": user_id},
        json={
            "UserId": user_id,
            "DeviceProfile": TRANSCODE_PROFILE,
            "StartTimeTicks": 0,
            "EnableDirectPlay": False,
            "EnableDirectStream": False,
            "EnableTranscoding": True,
        },
    )
    response.raise_for_status()
    playback_ms = (time.perf_counter() - started) * 1000
    body = response.json()
    source = body["MediaSources"][0]
    transcode_url = source.get("TranscodingUrl")
    if not transcode_url:
        raise RuntimeError(f"server did not provide a transcoding URL: {source.get('SupportsTranscoding')=}")
    master_url = urljoin(str(client.base_url), transcode_url)
    master_started = time.perf_counter()
    master = client.get(master_url)
    master.raise_for_status()
    master_ms = (time.perf_counter() - master_started) * 1000
    variant_url = urljoin(master_url, manifest_uri(master.text))
    variant_started = time.perf_counter()
    variant = client.get(variant_url)
    variant.raise_for_status()
    variant_ms = (time.perf_counter() - variant_started) * 1000
    segment_url = urljoin(variant_url, manifest_uri(variant.text))
    initialization = b""
    if uri := init_uri(variant.text):
        init_response = client.get(urljoin(variant_url, uri))
        init_response.raise_for_status()
        initialization = init_response.content
    before_segment = time.perf_counter()
    segment = stream_once(client, segment_url, {}, capture=True)
    intent_to_first_byte = (before_segment - started) * 1000 + segment["ttfb_ms"]
    probed = probe_segment(initialization, segment["content"])
    input_streams = found.get("MediaStreams") or source.get("MediaStreams") or []
    input_video = next((stream.get("Codec") for stream in input_streams if stream.get("Type") == "Video"), None)
    output_video = next((stream.get("codec_name") for stream in probed.get("streams", []) if stream.get("codec_type") == "video"), None)
    session = body.get("PlaySessionId")
    if session:
        client.delete("/Videos/ActiveEncodings", params={"PlaySessionId": session})
    return {
        "playback_info_ms": playback_ms,
        "master_ms": master_ms,
        "variant_ms": variant_ms,
        "first_segment_ttfb_ms": segment["ttfb_ms"],
        "intent_to_first_segment_byte_ms": intent_to_first_byte,
        "first_segment_total_ms": segment["total_ms"],
        "first_segment_bytes": segment["bytes"],
        "first_segment_duration_seconds": first_segment_duration(variant.text),
        "first_segment_status": segment["status"],
        "transcoding_url_path": clean_url(transcode_url),
        "variant_url_path": clean_url(variant_url),
        "input_video_codec": input_video,
        "output_probe": probed,
        "video_mode": "copy" if input_video and output_video == input_video.lower() else "encode",
        "transcode_reasons": source.get("TranscodingReasons"),
    }


def transcode_benchmark(client: httpx.Client, target: Target, user_id: str, runs: int) -> dict:
    found = item(client, user_id, TRANSCODE_TITLE)
    with DockerSampler(target.container) as sampler:
        rows = [transcode_once(client, user_id, found) for _ in range(runs)]
    return {
        "item_id": found["Id"],
        "runs": runs,
        "requested_profile": TRANSCODE_PROFILE,
        "playback_info": distribution([row["playback_info_ms"] for row in rows]),
        "master": distribution([row["master_ms"] for row in rows]),
        "variant": distribution([row["variant_ms"] for row in rows]),
        "first_segment_ttfb": distribution([row["first_segment_ttfb_ms"] for row in rows]),
        "intent_to_first_segment_byte": distribution([row["intent_to_first_segment_byte_ms"] for row in rows]),
        "first_segment_completion": distribution([row["first_segment_total_ms"] for row in rows]),
        "first_segment_bytes": [row["first_segment_bytes"] for row in rows],
        "container": sampler.summary(),
        "percentile_note": "With three runs, p95 and p99 are the maximum; inspect raw values.",
        "raw": rows,
    }


def versions() -> dict:
    commands = {
        "python": ["python3", "--version"],
        "docker": ["docker", "--version"],
        "ffmpeg": ["ffmpeg", "-version"],
    }
    output = {}
    for name, command in commands.items():
        result = subprocess.run(command, capture_output=True, text=True)
        output[name] = (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else None
    return output


def host_load() -> dict:
    processes = subprocess.run(
        ["ps", "-Ao", "pid=,pcpu=,rss=,comm="], capture_output=True, text=True,
    ).stdout.splitlines()
    busy = []
    for line in processes:
        parts = line.strip().split(maxsplit=3)
        if len(parts) == 4 and float(parts[1]) >= 5:
            busy.append({"pid": int(parts[0]), "cpu_percent": float(parts[1]), "rss_kib": int(parts[2]), "command": parts[3]})
    return {
        "captured_at_unix": time.time(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "load_average": list(os.getloadavg()),
        "processes_at_or_above_5_percent_cpu": busy,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark one Jellyfin-compatible server with a common workload.")
    parser.add_argument("target", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--api-count", type=int, default=300)
    parser.add_argument("--stream-runs", type=int, default=30)
    parser.add_argument("--transcode-runs", type=int, default=3)
    args = parser.parse_args()
    target = Target.read(args.target)
    client, user_id, _token = client_and_identity(target)
    try:
        system = client.get("/System/Info/Public").json()
        library = client.get(f"/Users/{user_id}/Items", params={"Recursive": "true"}).json()
        movies = client.get(
            f"/Users/{user_id}/Items",
            params={"Recursive": "true", "IncludeItemTypes": "Movie", "Limit": 1},
        ).json()
        result = {
            "schema": 1,
            "captured_at_unix": time.time(),
            "target": {"name": target.name, "base_url": target.base_url, "container": target.container},
            "server": system,
            "host_load_before": host_load(),
            "library_total_record_count": library.get("TotalRecordCount"),
            "library_movie_count": movies.get("TotalRecordCount"),
            "tool_versions": versions(),
            "api": api_benchmark(client, target, user_id, args.api_count),
            "api_representations": representation_benchmark(
                client, target,
                f"/Users/{user_id}/Items?Recursive=true&IncludeItemTypes=Movie&Limit=60&Fields=MediaSources",
            ),
            "direct_range": direct_benchmark(client, target, user_id, args.stream_runs),
            "transcode": transcode_benchmark(client, target, user_id, args.transcode_runs),
            "host_load_after": host_load(),
        }
    finally:
        client.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(args.out), "target": target.name, "library": result["library_total_record_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
