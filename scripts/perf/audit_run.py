"""Record a benchmark's environment and sampled process-tree resources.

Use inside the existing performance lock. This does not flush the OS page cache.
The resource scope includes the benchmark driver and all of its descendants.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]


def command_text(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def fingerprint(patterns: list[str]) -> str:
    paths = subprocess.check_output([
        "git", "ls-files", "-co", "--exclude-standard", "-z", "--", *patterns,
    ], cwd=REPO).split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(set(paths) - {b""}):
        path = REPO / os.fsdecode(raw)
        if path.is_file():
            digest.update(raw + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def source_identity() -> dict:
    return {"commit": command_text(["git", "rev-parse", "HEAD"]),
            "production_source_sha256": fingerprint(["backend/app", "frontend/src", ".tool-versions", "frontend/package-lock.json", "backend/requirements*"]),
            "benchmark_source_sha256": fingerprint(["scripts/perf", "frontend/e2e/playperf", "frontend/playwright.*.config.ts"]),
            "dirty": bool(command_text(["git", "status", "--porcelain"]))}


def processes() -> list[dict]:
    process = subprocess.Popen(["ps", "-axo", "pid=,ppid=,rss=,pcpu=,comm="], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass  # A blocked OS process must not make the benchmark guard block indefinitely.
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, "ps")
    rows = []
    for line in stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            try:
                rows.append({"pid": int(parts[0]), "parent": int(parts[1]), "rss_bytes": int(parts[2]) * 1024,
                             "cpu_percent": float(parts[3]), "name": Path(parts[4]).name})
            except ValueError:
                continue
    return rows


def descendants(rows: list[dict], root: int) -> list[dict]:
    ids = {root}
    while True:
        more = {row["pid"] for row in rows if row["parent"] in ids} - ids
        if not more:
            return [row for row in rows if row["pid"] in ids]
        ids.update(more)


def role(name: str) -> str:
    value = name.lower()
    if "ffmpeg" in value:
        return "ffmpeg"
    if any(word in value for word in ("chrome", "webkit", "safari", "playwright")):
        return "browser"
    if "python" in value:
        return "python_driver_and_servers"
    if "node" in value:
        return "node_driver_and_servers"
    return "other_descendants"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--require-quiet", action="store_true")
    parser.add_argument("--docker-workload", action="store_true", help="annotate Docker VM CPU separately; retain other-container inventory")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.interval < 0.25:
        parser.error("supply a command after -- and an interval of at least 0.25 seconds")
    metadata = {"platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count(),
                "cpu": command_text(["sysctl", "-n", "machdep.cpu.brand_string"]),
                "memory_bytes": command_text(["sysctl", "-n", "hw.memsize"]), "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version, "node": command_text(["node", "--version"]),
                "ffmpeg": (command_text(["ffmpeg", "-version"]) or "unavailable").splitlines()[0],
                "source": source_identity(), "filesystem_cache": "not flushed; warm/unknown",
                "load_average_before": os.getloadavg()}
    if args.docker_workload:
        metadata["docker_containers_before"] = command_text(["docker", "ps", "--format", "{{.ID}} {{.Image}}"])
    baseline = []
    for index in range(3):
        try:
            rows = processes()
            own = {row["pid"] for row in descendants(rows, os.getpid())}
            baseline.append({"at": time.time(), "busy_processes": [row for row in rows
                             if row["pid"] not in own and row["cpu_percent"] >= 50]})
        except (OSError, subprocess.SubprocessError) as error:
            baseline.append({"at": time.time(), "busy_processes": [], "sampling_error": type(error).__name__})
            break
        if index < 2:
            time.sleep(1)
    quiet = not any(sample["busy_processes"] or sample.get("sampling_error") for sample in baseline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = {"name": args.name, "environment": metadata, "quiet_baseline": quiet, "baseline": baseline,
              "sampling_interval_seconds": args.interval, "samples": [], "sampling_errors": [],
              "notes": ["RSS is an observed process-tree sum, not a GPU allocation metric or an exact peak.",
                        "ps CPU percentages are operating-system observations, not integrated CPU seconds.",
                        "Process names omit arguments; no environment values or credentials are recorded."]}
    if args.require_quiet and not quiet:
        result.update({"executed": False, "reason": "quiet baseline unavailable: external CPU activity or process sampling failure"})
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"{args.name}: quiet baseline unavailable; command was not started", flush=True)
        return 75
    started = time.monotonic()
    child = subprocess.Popen(command, cwd=REPO)
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            sample_started = time.monotonic()
            try:
                all_rows = processes()
                tree = descendants(all_rows, child.pid)
                ids = {row["pid"] for row in tree} | {os.getpid()}
                grouped = defaultdict(lambda: {"rss_bytes": 0, "cpu_percent": 0.0})
                for row in tree:
                    group = grouped[role(row["name"])]
                    group["rss_bytes"] += row["rss_bytes"]
                    group["cpu_percent"] += row["cpu_percent"]
                result["samples"].append({"elapsed_seconds": round(time.monotonic() - started, 3),
                    "rss_bytes": sum(row["rss_bytes"] for row in tree), "roles": dict(grouped),
                    "external_busy": [row for row in all_rows if row["pid"] not in ids and row["cpu_percent"] >= 50
                                      and not (args.docker_workload and row["name"] == "com.docker.backend")],
                    "docker_vm": [row for row in all_rows if args.docker_workload and row["name"] == "com.docker.backend"],
                    "sampler_ms": round((time.monotonic() - sample_started) * 1000, 3)})
            except (OSError, subprocess.SubprocessError) as error:
                result["sampling_errors"].append(type(error).__name__)
            stop.wait(max(0, args.interval - (time.monotonic() - sample_started)))

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        status = child.wait()
    except KeyboardInterrupt:
        child.terminate()
        status = child.wait()
    finally:
        stop.set()
        thread.join(timeout=6)
    result.update({"executed": True, "exit_code": status, "wall_seconds": round(time.monotonic() - started, 3),
                   "load_average_after": os.getloadavg(), "observed_tree_peak_rss_bytes": max((row["rss_bytes"] for row in result["samples"]), default=None),
                   "process_sampling_complete": bool(result["samples"]) and not result["sampling_errors"],
                   "external_busy_during_run": (True if any(row["external_busy"] for row in result["samples"])
                                                else None if result["sampling_errors"] or not result["samples"] else False)})
    if args.docker_workload:
        result["environment"]["docker_containers_after"] = command_text(["docker", "ps", "--format", "{{.ID}} {{.Image}}"])
        result["notes"].append("Docker VM CPU during this workload is annotated separately; cgroup resources are in the Docker result. Other containers can still contend and are inventoried.")
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{args.name}: exit {status}; resource evidence {args.out}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
