"""Summarize full-production baseline, optimized, and Jellyfin controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resource(row: dict) -> dict:
    container = row["container"]
    return {
        "cgroup_cpu_seconds": container["cgroup_counter_delta"]["cpu_seconds"],
        "cgroup_cpu_percent_over_measurement": container["cgroup_counter_delta"]["cpu_percent_over_measurement"],
        "cgroup_memory_usage_bytes_max": container.get("cgroup_memory_usage_bytes_max"),
        "summed_process_rss_bytes_max": container.get("process_rss_bytes_max"),
        "docker_stats_samples": container["samples"],
    }


def api(row: dict) -> dict:
    return {
        key: row[key] for key in (
            "count", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "wall_seconds",
            "throughput_requests_per_second", "statuses", "response_bytes_mean", "response_cardinality",
        )
    } | {"resources": resource(row)}


def direct(data: dict) -> dict:
    return {
        "source_size_bytes": data["direct_range"]["source_size_bytes"],
        "ranges": data["direct_range"]["ranges"],
        "sustained": {
            concurrency: {
                key: row[key] for key in (
                    "streams", "bytes_per_stream", "aggregate_bytes", "wall_seconds",
                    "aggregate_mebibytes_per_second", "ttfb", "completion", "statuses",
                    "content_ranges", "sha256", "byte_integrity",
                )
            } | {"resources": resource(row)}
            for concurrency, row in data["direct_range"]["sustained"].items()
        },
    }


def hls(data: dict) -> dict:
    row = data["transcode"]
    return {
        key: row[key] for key in (
            "runs", "requested_profile", "playback_info", "master", "variant",
            "first_segment_ttfb", "intent_to_first_segment_byte", "first_segment_completion",
            "first_segment_bytes", "raw", "percentile_note",
        )
    } | {"resources": resource(row)}


def startup(data: dict) -> dict:
    output = {}
    for label, target in data["targets"].items():
        values = [sample["docker_start_to_ready_seconds"] for sample in target["samples"]]
        output[label] = {
            "runs": len(values),
            "median_docker_start_to_ready_seconds": statistics.median(values),
            "samples": target["samples"],
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    root = args.raw_dir.resolve()
    names = {
        "baseline": "production-baseline.json",
        "optimized": "production-optimized-1cada1d.json",
        "jellyfin_before": "production-jellyfin-baseline-control.json",
        "jellyfin_after": "production-jellyfin-optimized-control.json",
        "direct_frames_before": "production-baseline-vs-jellyfin-direct-frame.json",
        "direct_frames_after": "production-optimized-vs-jellyfin-direct-frame.json",
        "hls_frames_before": "production-baseline-vs-jellyfin-hls-frame.json",
        "hls_frames_after": "production-optimized-vs-jellyfin-hls-frame.json",
        "startup_before": "production-baseline-startup.json",
        "startup_after": "production-optimized-startup-1cada1d.json",
        "environment_before": "production-baseline-environment.json",
        "environment_after": "production-optimized-environment-1cada1d.json",
        "failed_jellyfin_attempt": "production-jellyfin-control-failed-attempt.json",
    }
    data = {key: read(root / name) for key, name in names.items()}
    servers = {key: data[key] for key in ("baseline", "optimized", "jellyfin_before", "jellyfin_after")}
    scenarios = ("public_system_info", "current_user", "items_page", "items_search")
    result = {
        "schema": 1,
        "protocol": "full-production-comparison-v1",
        "source_revisions": {
            "baseline": data["environment_before"]["images"]["lumina"]["labels"]["org.opencontainers.image.revision"],
            "optimized": data["environment_after"]["images"]["lumina"]["labels"]["org.opencontainers.image.revision"],
        },
        "images": read(args.images),
        "state": {
            "description": "Two copies of the same initialized, fully warmed 302-movie synthetic database and media tree.",
            "lumina_movie_count": servers["baseline"]["library_movie_count"],
            "optimized_movie_count": servers["optimized"]["library_movie_count"],
            "jellyfin_movie_count_before": servers["jellyfin_before"]["library_movie_count"],
            "jellyfin_movie_count_after": servers["jellyfin_after"]["library_movie_count"],
        },
        "api": {
            scenario: {
                concurrency: {label: api(server["api"][scenario][concurrency]) for label, server in servers.items()}
                for concurrency in ("1", "8", "32")
            }
            for scenario in scenarios
        },
        "direct": {label: direct(server) for label, server in servers.items()},
        "hls_first_segment": {label: hls(server) for label, server in servers.items()},
        "direct_first_decoded_frame": {
            "baseline_pair": data["direct_frames_before"],
            "optimized_pair": data["direct_frames_after"],
        },
        "hls_first_decoded_frame": {
            "baseline_pair": data["hls_frames_before"],
            "optimized_pair": data["hls_frames_after"],
        },
        "initialized_restart": {
            "baseline_pair": startup(data["startup_before"]),
            "optimized_pair": startup(data["startup_after"]),
        },
        "observed_failures": {
            "failed_jellyfin_attempt": data["failed_jellyfin_attempt"],
            "successful_jellyfin_before_statuses": {
                scenario: {concurrency: servers["jellyfin_before"]["api"][scenario][concurrency]["statuses"] for concurrency in ("1", "8", "32")}
                for scenario in scenarios
            },
            "successful_jellyfin_after_statuses": {
                scenario: {concurrency: servers["jellyfin_after"]["api"][scenario][concurrency]["statuses"] for concurrency in ("1", "8", "32")}
                for scenario in scenarios
            },
        },
        "limitations": [
            "The host retained unrelated Tdarr FFmpeg and ComfyUI workloads; each server result records host load before and after.",
            "Jellyfin control runs observed isolated ReadError/ReadTimeout failures at concurrency 32; they are included in statuses and wall throughput.",
            "The search endpoints returned 60 items but reported different candidate totals (Lumina 200 and Jellyfin 180).",
            "HLS output differed by one displayed pixel in width (854 versus 853) and by AAC encoder implementation.",
            "The optimized source revision predates separate probe-scheduling and disconnect-cleanup commits; API/playback behavior is expected to remain applicable but the combined image requires qualification.",
        ],
        "raw_files": {
            key: {"name": name, "sha256": sha256(root / name), "bytes": (root / name).stat().st_size}
            for key, name in names.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(args.out), "protocol": result["protocol"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
