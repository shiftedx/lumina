"""Assemble the protocol-3 production comparison without discarding raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


FILES = {
    "baseline": "final-baseline.json",
    "optimized": "final-optimized.json",
    "jellyfin_before": "final-jellyfin-baseline-control.json",
    "jellyfin_after": "final-jellyfin-optimized-control.json",
    "direct_frames_before": "final-baseline-vs-jellyfin-direct-frame.json",
    "direct_frames_after": "final-optimized-vs-jellyfin-direct-frame.json",
    "hls_frames_before": "final-baseline-vs-jellyfin-hls-frame.json",
    "hls_frames_after": "final-optimized-vs-jellyfin-hls-frame.json",
    "startup_before": "final-baseline-startup.json",
    "startup_after": "final-optimized-startup.json",
    "environment_before": "final-baseline-environment.json",
    "environment_after": "final-optimized-environment.json",
    "fresh_scan": "final-fresh-scan-environment.json",
    "cloned_state": "final-cloned-state-manifest.json",
    "probe_facts": "final-probe-facts-validation.json",
    "model_runtime_state": "final-model-runtime-state.json",
    "phase_manifest_before": "final-baseline-phase-manifest.json",
    "phase_manifest_after": "final-optimized-phase-manifest.json",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def identity(path: Path) -> dict:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


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
    keys = (
        "count", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "wall_seconds",
        "throughput_requests_per_second", "statuses", "response_bytes_mean",
        "wire_content_length_bytes_mean", "wire_content_length_samples", "content_encodings",
        "vary", "response_cardinality", "warm_response_shape",
    )
    return {key: row.get(key) for key in keys} | {"resources": resource(row)}


def direct(data: dict) -> dict:
    row = data["direct_range"]
    return {
        "source_size_bytes": row["source_size_bytes"],
        "ranges": row["ranges"],
        "sustained": {
            concurrency: {
                key: value.get(key) for key in (
                    "streams", "bytes_per_stream", "aggregate_bytes", "wall_seconds",
                    "aggregate_mebibytes_per_second", "ttfb", "completion", "statuses",
                    "content_ranges", "content_encodings", "wire_content_lengths", "sha256", "byte_integrity",
                )
            } | {"resources": resource(value)}
            for concurrency, value in row["sustained"].items()
        },
    }


def hls(data: dict) -> dict:
    row = data["transcode"]
    keys = (
        "runs", "requested_profile", "playback_info", "master", "variant",
        "first_segment_ttfb", "intent_to_first_segment_byte", "first_segment_completion",
        "first_segment_bytes", "raw", "percentile_note",
    )
    return {key: row[key] for key in keys} | {"resources": resource(row)}


def startup(data: dict) -> dict:
    output = {"schema": data["schema"], "readiness": data["readiness"], "targets": {}}
    for label, target in data["targets"].items():
        values = [sample["docker_start_to_ready_seconds"] for sample in target["samples"]]
        output["targets"][label] = {
            "runs": len(values),
            "median_docker_start_to_ready_seconds": statistics.median(values),
            "samples": target["samples"],
        }
    return output


def shape(server: dict, scenario: str) -> dict:
    return server["api"][scenario]["1"]["warm_response_shape"]


def same_shape(left: dict, right: dict, scenario: str) -> dict:
    first, second = shape(left, scenario), shape(right, scenario)
    keys = (
        "canonical_json_sha256", "returned_item_count", "item_key_sets_sha256",
        "item_ids_sha256", "item_names_sha256",
    )
    complete = all(key in value and value[key] is not None for value in (first, second) for key in keys)
    return {
        "complete": complete,
        "equal": complete and all(first[key] == second[key] for key in keys),
        "baseline": {key: first.get(key) for key in keys},
        "optimized": {key: second.get(key) for key in keys},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--expected-baseline-revision", required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--expected-baseline-image-id", required=True)
    parser.add_argument("--expected-optimized-image-id", required=True)
    parser.add_argument("--expected-jellyfin-image-id", required=True)
    args = parser.parse_args()
    root = args.raw_dir.resolve()
    paths = {key: root / value for key, value in FILES.items()}
    data = {key: read(path) for key, path in paths.items()}
    servers = {key: data[key] for key in ("baseline", "optimized", "jellyfin_before", "jellyfin_after")}
    scenarios = ("public_system_info", "current_user", "items_page", "items_search")
    result = {
        "schema": 3,
        "protocol": "full-production-comparison-v3",
        "source_revisions": {
            "baseline": data["environment_before"]["images"]["lumina"]["labels"]["org.opencontainers.image.revision"],
            "optimized": data["environment_after"]["images"]["lumina"]["labels"]["org.opencontainers.image.revision"],
        },
        "images": {
            "baseline": data["environment_before"]["images"]["lumina"],
            "optimized": data["environment_after"]["images"]["lumina"],
            "jellyfin": data["environment_after"]["images"]["jellyfin"],
        },
        "state": {
            "description": "Baseline and optimized runs use independent clones of one stopped, fully warmed 302-movie Lumina database; the Jellyfin controls likewise clone one stopped initialized state. Both pairs mount the same read-only synthetic media tree.",
            "movie_counts": {label: server["library_movie_count"] for label, server in servers.items()},
            "payload_equivalence": {
                scenario: same_shape(servers["baseline"], servers["optimized"], scenario)
                for scenario in ("items_page", "items_search")
            },
            "jellyfin_control_equivalence": {
                scenario: same_shape(servers["jellyfin_before"], servers["jellyfin_after"], scenario)
                for scenario in ("items_page", "items_search")
            },
        },
        "fresh_scan": data["fresh_scan"]["setup"],
        "fresh_scan_successful_facts": data["probe_facts"],
        "model_runtime_state": data["model_runtime_state"],
        "cloned_state": data["cloned_state"],
        "phase_manifests": {
            "baseline": data["phase_manifest_before"],
            "optimized": data["phase_manifest_after"],
        },
        "api": {
            scenario: {
                concurrency: {label: api(server["api"][scenario][concurrency]) for label, server in servers.items()}
                for concurrency in ("1", "8", "32")
            }
            for scenario in scenarios
        },
        "api_representations": {label: server["api_representations"] for label, server in servers.items()},
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
        "limitations": [
            "The host retained unrelated user workloads; every server result records host load before and after, and controls were repeated around the Lumina revisions.",
            "Search returns the same requested 60 cards but Lumina and Jellyfin report different full candidate totals, so the cross-server search comparison is a practical client workload rather than identical query semantics.",
            "HLS output dimensions and AAC implementations can differ slightly. Startup measurements compare negotiated defaults and do not establish equal visual quality or encoder throughput.",
            "Three first-segment HLS runs make p95 and p99 equal to the maximum; decoded-frame tests use 30 paired order-balanced trials for each server.",
            "Direct decoded-frame timing begins after authentication and playback-source preparation, at media-source assignment. HLS decoded-frame timing begins before PlaybackInfo; their absolute values have different scopes.",
            "Ready/serving resource measurements use the benchmark configuration with model inference inactive; the full production image still includes its model runtimes.",
        ],
        "raw_files": {key: identity(path) for key, path in paths.items()},
    }
    if result["source_revisions"]["baseline"] != args.expected_baseline_revision:
        raise RuntimeError(f"baseline revision mismatch: {result['source_revisions']['baseline']}")
    if result["source_revisions"]["optimized"] != args.expected_source_revision:
        raise RuntimeError(f"optimized revision mismatch: {result['source_revisions']['optimized']}")
    expected_images = {
        "baseline": args.expected_baseline_image_id,
        "optimized": args.expected_optimized_image_id,
        "jellyfin": args.expected_jellyfin_image_id,
    }
    actual_images = {name: image["id"] for name, image in result["images"].items()}
    if actual_images != expected_images:
        raise RuntimeError(f"image ID mismatch: expected {expected_images}, got {actual_images}")
    before_jellyfin = data["environment_before"]["images"]["jellyfin"]["id"]
    fresh_images = data["fresh_scan"]["images"]
    if before_jellyfin != args.expected_jellyfin_image_id:
        raise RuntimeError(f"baseline control Jellyfin image mismatch: {before_jellyfin}")
    if fresh_images["lumina"]["id"] != args.expected_optimized_image_id:
        raise RuntimeError(f"fresh-scan Lumina image mismatch: {fresh_images['lumina']['id']}")
    if fresh_images["jellyfin"]["id"] != args.expected_jellyfin_image_id:
        raise RuntimeError(f"fresh-scan Jellyfin image mismatch: {fresh_images['jellyfin']['id']}")
    for phase, manifest_key, environment_key in (
        ("baseline", "phase_manifest_before", "environment_before"),
        ("optimized", "phase_manifest_after", "environment_after"),
    ):
        manifest = data[manifest_key]
        if manifest["lumina_image_id"] != data[environment_key]["images"]["lumina"]["id"]:
            raise RuntimeError(f"{phase} phase manifest Lumina image mismatch")
        if manifest["jellyfin_image_id"] != args.expected_jellyfin_image_id:
            raise RuntimeError(f"{phase} phase manifest Jellyfin image mismatch")
        for artifact in manifest["artifacts"]:
            artifact_path = root / artifact["name"]
            if identity(artifact_path) != {key: artifact[key] for key in ("name", "bytes", "sha256")}:
                raise RuntimeError(f"{phase} phase artifact changed after manifest: {artifact['name']}")
    counts = result["state"]["movie_counts"]
    if any(count != 302 for count in counts.values()):
        raise RuntimeError(f"expected 302 movies in all four runs: {counts}")
    comparisons = result["state"]["payload_equivalence"] | {
        f"jellyfin_{name}": check
        for name, check in result["state"]["jellyfin_control_equivalence"].items()
    }
    failed = [name for name, check in comparisons.items() if not check["equal"]]
    if failed:
        raise RuntimeError(f"baseline/optimized payloads differ for: {', '.join(failed)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(args.out), "protocol": result["protocol"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
