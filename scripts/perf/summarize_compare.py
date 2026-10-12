"""Build a credential-free summary from raw Lumina/Jellyfin comparison files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api_row(data: dict, scenario: str, concurrency: str) -> dict:
    row = data["api"][scenario][concurrency]
    return {key: row[key] for key in (
        "count", "p50_ms", "p95_ms", "p99_ms", "throughput_requests_per_second",
        "statuses", "response_bytes_mean", "response_cardinality",
    )}


def sustained_row(data: dict, concurrency: str) -> dict:
    row = data["direct_range"]["sustained"][concurrency]
    return {
        "streams": row["streams"],
        "bytes_per_stream": row["bytes_per_stream"],
        "aggregate_mebibytes_per_second": row["aggregate_mebibytes_per_second"],
        "ttfb": row["ttfb"],
        "completion": row["completion"],
        "statuses": row["statuses"],
        "sha256": row["sha256"],
        "byte_integrity": row["byte_integrity"],
        "resources": {
            "cgroup_counter_delta": row["container"]["cgroup_counter_delta"],
            "cgroup_memory_usage_bytes_max": row["container"].get("cgroup_memory_usage_bytes_max"),
            "process_rss_bytes_max": row["container"].get("process_rss_bytes_max"),
        },
    }


def frame_row(data: dict) -> dict:
    return {
        "runs": data["runs"],
        "p50_ms": data["p50_ms"],
        "p95_ms": data["p95_ms"],
        "p99_ms": data["p99_ms"],
        "samples_ms": [sample["milliseconds"] for sample in data["samples"]],
        "cache_state": data["cache_state"],
        "client": data["client"],
    }


def scan_evidence(run_root: Path, environment: dict) -> dict:
    lumina_db = run_root / "lumina-data" / "app.db"
    with sqlite3.connect(lumina_db) as connection:
        first_visible, last_probe, artifacts, probed, loudness = connection.execute(
            "SELECT min(created_at), max(updated_at), count(*), "
            "sum(CASE WHEN probe IS NOT NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN json_type(probe, '$.loudness') IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM media_artifacts"
        ).fetchone()
    first = datetime.fromisoformat(first_visible)
    last = datetime.fromisoformat(last_probe)
    lower = (last - first).total_seconds()
    captured = datetime.fromtimestamp(environment["captured_at_unix"], UTC).replace(tzinfo=None)
    upper = (last - captured).total_seconds()

    jellyfin_db = run_root / "jellyfin-config" / "data" / "jellyfin.db"
    with sqlite3.connect(jellyfin_db) as connection:
        jellyfin_movies, benchmark_names = connection.execute(
            "SELECT count(*), sum(Name LIKE '%Benchmark%') FROM BaseItems "
            "WHERE Path LIKE '/media/Movies/%'"
        ).fetchone()
    options = (run_root / "jellyfin-config" / "root" / "default" / "Comparison Movies" / "options.xml").read_text()
    return {
        "lumina": {
            "metadata_visible_seconds": environment["setup"]["lumina"]["scan_seconds"],
            "codec_probed_artifacts": probed,
            "loudness_analyzed_artifacts": loudness,
            "artifact_count": artifacts,
            "first_artifact_visible_at_utc": first_visible,
            "last_artifact_probe_update_at_utc": last_probe,
            "import_start_to_probe_and_loudness_ready_seconds_bounds": [round(lower, 4), round(upper, 4)],
            "bounds_note": "The original worker ran ffprobe and then full-file loudness analysis for each file before selecting the next file. These timestamps therefore bound combined codec-probe and loudness readiness, not codec readiness alone. The lower bound starts at the first artifact row; the upper bound starts at environment capture before container startup.",
        },
        "jellyfin": {
            "scan_to_idle_seconds": environment["setup"]["jellyfin"]["scan_seconds"],
            "movie_paths_in_database": jellyfin_movies,
            "movie_names_containing_benchmark": benchmark_names,
            "internet_providers_enabled": "<EnableInternetProviders>false</EnableInternetProviders>" not in options,
        },
        "comparison_note": "Lumina metadata visibility and Jellyfin scan-to-idle are different readiness boundaries. The only saved Lumina completion timestamp also includes full-file loudness analysis, which Jellyfin's scan did not perform, so it is not an equivalent readiness boundary either.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    raw = args.raw_dir.resolve()
    names = [
        "environment-initial.json", "lumina-baseline-a.json", "jellyfin-a.json",
        "lumina-baseline-b.json", "jellyfin-b.json", "lumina-first-frame.json",
        "jellyfin-first-frame.json", "initialized-restart.json",
    ]
    data = {name: read(raw / name) for name in names}
    lumina = data["lumina-baseline-b.json"]
    jellyfin = data["jellyfin-b.json"]
    startup = data["initialized-restart.json"]

    result = {
        "schema": 1,
        "protocol_revision": 2,
        "protocol": {
            "corrected_files": ["lumina-baseline-b.json", "jellyfin-b.json"],
            "corrections": [
                "The shared search term is Benchmark and every response contains 60 items; server-reported totals remain 200 for Lumina and 180 for Jellyfin.",
                "The shared HLS profile explicitly requests H.264/AAC, width <= 854, height <= 480, one-second segments, and 2 Mbps maximum streaming bitrate.",
                "Request timeouts are recorded as error statuses instead of aborting and discarding a pass.",
            ],
            "qualification": {
                "lumina-baseline-a.json": "API public/current-user/items-page and direct range only; search query semantics and the paired Jellyfin HLS settings were not equivalent.",
                "jellyfin-a.json": "API public/current-user/items-page and direct range only; search returned zero items and HLS output was 1280x720 with three-second segments.",
                "lumina-baseline-b.json": "Qualifies for all recorded metrics under protocol revision 2.",
                "jellyfin-b.json": "Qualifies for all recorded metrics under protocol revision 2.",
            },
        },
        "environment": data["environment-initial.json"],
        "scan_readiness": scan_evidence(args.run_root.resolve(), data["environment-initial.json"]),
        "library": {
            "lumina_movies": lumina["library_movie_count"],
            "jellyfin_movies": jellyfin["library_movie_count"],
        },
        "api_by_concurrency": {
            scenario: {
                concurrency: {
                    "lumina": api_row(lumina, scenario, concurrency),
                    "jellyfin": api_row(jellyfin, scenario, concurrency),
                }
                for concurrency in ("1", "8", "32")
            }
            for scenario in ("public_system_info", "current_user", "items_page", "items_search")
        },
        "direct_range": {
            "fixture_sha256": data["environment-initial.json"]["fixture"]["direct"]["sha256"],
            "fixture_bytes": data["environment-initial.json"]["fixture"]["direct"]["size_bytes"],
            "lumina": {key: sustained_row(lumina, key) for key in ("1", "8")},
            "jellyfin": {key: sustained_row(jellyfin, key) for key in ("1", "8")},
        },
        "equivalent_hls": {
            "lumina": lumina["transcode"],
            "jellyfin": jellyfin["transcode"],
            "note": "Actual probes differ by two coded pixels in width (854 versus 852) and by AAC encoder implementation. Treat this as closely matched end-to-end negotiation, not encoder-quality equivalence.",
        },
        "first_decoded_frame": {
            "lumina": frame_row(data["lumina-first-frame.json"]),
            "jellyfin": frame_row(data["jellyfin-first-frame.json"]),
        },
        "initialized_restart": {
            label: {
                "runs": len(target["samples"]),
                "median_docker_start_to_ready_seconds": statistics.median(
                    sample["docker_start_to_ready_seconds"] for sample in target["samples"]
                ),
                "samples": target["samples"],
            }
            for label, target in startup["targets"].items()
        },
        "limitations": [
            "The host retained unrelated Tdarr ffmpeg and ComfyUI workloads; raw files capture load before and after each pass.",
            "Only one corrected protocol-revision-2 pass per server is available for search and equivalent HLS.",
            "First decoded-frame results contain five trials, so p95 and p99 equal the maximum and do not estimate a stable tail.",
            "The Lumina image is a trimmed performance runtime and cannot support production image-size or build-time claims.",
            "No shared-client HLS time-to-first-decoded-frame measurement was completed.",
        ],
        "raw_files": {name: {"sha256": digest(raw / name), "bytes": (raw / name).stat().st_size} for name in names},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(args.out), "protocol_revision": 2}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
