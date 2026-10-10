#!/usr/bin/env python3
"""Measure codec-probe and loudness readiness on an existing media fixture.

The benchmark creates a fresh, isolated Lumina database, registers every media
file below ``ROOT/Movies`` as an artifact, and drains ``ProbeWarming`` directly.
It records codec readiness separately from the eventual full-file loudness pass.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MEDIA_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def timing(samples: list[float]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "min_ms": round(min(samples) * 1000, 4),
        "p50_ms": round(statistics.median(samples) * 1000, 4),
        "p95_ms": round(percentile(samples, 0.95) * 1000, 4),
        "max_ms": round(max(samples) * 1000, 4),
        "raw_ms": [round(value * 1000, 4) for value in samples],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="Shared comparison fixture root")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-interleaved", action="store_true", help="Reproduce the pre-fix probe/loudness ordering")
    args = parser.parse_args()
    root, data_dir = args.root.resolve(), args.data_dir.resolve()
    if data_dir == root or data_dir.is_relative_to(root) or root.is_relative_to(data_dir):
        parser.error("--data-dir and fixture root must not overlap")
    if data_dir.exists() and any(data_dir.iterdir()):
        parser.error("--data-dir must be absent or empty; refusing to delete benchmark data")
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LUMINA_DATA_DIR"] = str(data_dir)

    repository = Path(__file__).resolve().parents[2]
    source_commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "backend/app/services/probe_warming.py"],
        cwd=repository, check=True, capture_output=True, text=True,
    ).stdout.strip()
    sys.path.insert(0, str(repository / "backend"))
    from app.db import SessionLocal, init_db  # noqa: PLC0415
    from app.models import MediaArtifact, StorageRoot  # noqa: PLC0415
    from app.services.media_probe import MediaProbeService  # noqa: PLC0415
    from app.services.probe_warming import ProbeWarming  # noqa: PLC0415
    from sqlalchemy import func  # noqa: PLC0415

    media = sorted(path for path in (root / "Movies").rglob("*") if path.suffix.lower() in MEDIA_SUFFIXES)
    if not media:
        parser.error(f"no media under {root / 'Movies'}")
    init_db()
    with SessionLocal() as db:
        status = root.stat()
        db.add(StorageRoot(
            id="benchmark-root", label="Probe benchmark", path=str(root), mode="external", enabled=True,
            identity=str(status.st_dev), observation={"state": "available"},
        ))
        for index, path in enumerate(media):
            file_status = path.stat()
            db.add(MediaArtifact(
                id=f"benchmark-{index:06d}", root_id="benchmark-root", relative_path=path.relative_to(root).as_posix(),
                ownership="external", lifecycle="available", size=file_status.st_size,
                mtime_ns=file_status.st_mtime_ns, inode=file_status.st_ino,
            ))
        db.commit()

    worker = ProbeWarming()
    started = time.perf_counter()
    probe_samples: list[float] = []
    probe_ready: float | None = None
    loudness_at_probe_ready: int | None = None
    original_cached_facts = MediaProbeService.cached_facts

    def tracked_cached_facts(self, artifact, path):  # noqa: ANN001
        nonlocal probe_ready, loudness_at_probe_ready
        item_started = time.perf_counter()
        facts = original_cached_facts(self, artifact, path)
        if len(probe_samples) < len(media):
            probe_samples.append(time.perf_counter() - item_started)
        if len(probe_samples) == len(media) and probe_ready is None:
            probe_ready = time.perf_counter() - started
            with SessionLocal() as count_db:
                loudness_at_probe_ready = sum(
                    "loudness" in (row.probe or {}) for row in count_db.query(MediaArtifact).all()
                )
        return facts

    MediaProbeService.cached_facts = tracked_cached_facts
    worker_samples: list[float] = []
    if args.legacy_interleaved:
        while True:
            with SessionLocal() as db:
                artifact = (
                    db.query(MediaArtifact)
                    .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
                    .filter(
                        MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True),
                        func.json_extract(MediaArtifact.probe, "$.loudness").is_(None),
                    )
                    .order_by(MediaArtifact.created_at.desc())
                    .first()
                )
                artifact_id = artifact.id if artifact is not None else None
            if artifact_id is None:
                break
            item_started = time.perf_counter()
            worker._warm(artifact_id)
            worker_samples.append(time.perf_counter() - item_started)
    else:
        while True:
            item_started = time.perf_counter()
            if not worker.run_once():
                break
            worker_samples.append(time.perf_counter() - item_started)
    if probe_ready is None or loudness_at_probe_ready is None:
        raise RuntimeError(f"only {len(probe_samples)}/{len(media)} artifacts entered the probe phase")
    MediaProbeService.cached_facts = original_cached_facts
    loudness_ready = time.perf_counter() - started
    with SessionLocal() as db:
        rows = db.query(MediaArtifact).all()
        probe_count = sum(
            bool(row.probe) and ("streams" in row.probe or "error" in row.probe)
            for row in rows
        )
        probe_keys = sorted({key for row in rows for key in (row.probe or {})})
    if probe_count != len(media):
        raise RuntimeError(f"only {probe_count}/{len(media)} artifacts became probe-ready")

    with SessionLocal() as db:
        rows = db.query(MediaArtifact).all()
        loudness_count = sum("loudness" in (row.probe or {}) for row in rows)
        loudness_shapes: dict[str, int] = {}
        for row in rows:
            value = (row.probe or {}).get("loudness")
            shape = ",".join(sorted(value)) if isinstance(value, dict) else type(value).__name__
            loudness_shapes[shape] = loudness_shapes.get(shape, 0) + 1
    if loudness_count != len(media):
        raise RuntimeError(f"only {loudness_count}/{len(media)} artifacts became loudness-ready")

    result = {
        "schema": 1,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "command": " ".join(sys.argv),
        "source_commit": source_commit,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "ffmpeg": shutil.which("ffmpeg"),
            "ffprobe": shutil.which("ffprobe"),
        },
        "fixture": {
            "root": str(root),
            "artifacts": len(media),
            "distinct_inodes": len({path.stat().st_ino for path in media}),
            "duration_seconds": 120,
        },
        "mode": "legacy_interleaved" if args.legacy_interleaved else "probe_first",
        "probe_phase": {
            "ready_seconds": round(probe_ready, 4),
            "ready_count": probe_count,
            "loudness_count": loudness_at_probe_ready,
            "stored_json_keys": probe_keys,
            "per_artifact": timing(probe_samples),
        },
        "loudness_phase": {
            "after_probe_ready_seconds": round(loudness_ready - probe_ready, 4),
            "ready_from_start_seconds": round(loudness_ready, 4),
            "ready_count": loudness_count,
            "stored_json_shapes": loudness_shapes,
            "worker_iterations": timing(worker_samples),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
