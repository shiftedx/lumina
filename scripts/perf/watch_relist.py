"""Measure watcher bookkeeping for changed directories in a synthetic tree.

No disk/network cost: this isolates the cost added by the watcher itself.
Run: backend/.venv/bin/python scripts/perf/watch_relist.py --output result.json
Set LUMINA_BENCH_BACKEND to a detached baseline's backend directory to run
the identical fixture against that revision.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("LUMINA_BENCH_BACKEND", str(Path(__file__).resolve().parents[2] / "backend")))
from app.services.library_watch import Listing, RootWatch


class Tree:
    def __init__(self, count: int, changed: int):
        self.known = {"": 1, **{f"show-{i:05d}": 1 for i in range(count)}}
        self.changed = {f"show-{i:05d}" for i in range(changed)}

    def stat_dir(self, path):
        return 2 if path in self.changed else 1

    def list_dir(self, path):
        return Listing()

    def read_file(self, path):
        raise AssertionError("No files in this fixture")


def measure(count=20000, changed=500, repeats=5):
    samples = []
    for _ in range(repeats):
        tree = Tree(count, changed)
        watch = RootWatch("synthetic", tree, known=tree.known, full_run_cutoff_ns=None,
                          interval_s=60, monotonic=time.monotonic, workers=1)
        started = time.perf_counter()
        watch.run_batch()
        elapsed = (time.perf_counter() - started) * 1000
        assert len(watch.dirty) == changed
        assert watch.pass_complete()
        samples.append(elapsed)
    return {"directories": count + 1, "changed_directories": changed, "workers": 1,
            "samples_ms": samples, "median_ms": statistics.median(samples),
            "scope": "synthetic filesystem; watcher bookkeeping, not disk scan throughput"}


def measure_deletes(count=10000, removed=500, repeats=5):
    samples = []
    for _ in range(repeats):
        known = {"": 1, **{f"show-{i:05d}": 1 for i in range(count)},
                 **{f"show-{i:05d}/season": 1 for i in range(count)}}
        watch = RootWatch("synthetic", None, known=known, full_run_cutoff_ns=None,
                          interval_s=60, monotonic=time.monotonic, workers=1)
        started = time.perf_counter()
        for index in range(removed):
            assert watch._purge(f"show-{index:05d}") == {f"show-{index:05d}", f"show-{index:05d}/season"}
        samples.append((time.perf_counter() - started) * 1000)
        assert len(watch.dirs) == len(known) - 2 * removed
    return {"directories": len(known), "removed_subtrees": removed,
            "samples_ms": samples, "median_ms": statistics.median(samples),
            "scope": "synthetic watcher subtree deletion bookkeeping, no disk I/O"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenario", choices=("relist", "remove"), default="relist")
    args = parser.parse_args()
    result = measure() if args.scenario == "relist" else measure_deletes()
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
