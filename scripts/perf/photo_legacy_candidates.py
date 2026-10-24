"""Measure archived recency-capped matching against a disposable scale fixture.

Use the SQLite fixture created by photo_scale.py while its service build runs.
This command opens it read-only, imports only the archived matching service, and
reports aggregate timing/coverage without recording face or photo identifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sqlite3
import tempfile
import sys
import time

for variable in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(variable, "1")

import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from photo_scale import SyntheticFaces  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--legacy-indexes", action="store_true", help="Privately back up the fixture and remove the two face indexes added by this change")
    args = parser.parse_args()
    archived = args.baseline_root / "backend/app/services/photo_faces.py"
    spec = importlib.util.spec_from_file_location("photo_faces_archived_scale", archived)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.PIPELINE_SIGNATURE = "synthetic-face-index-v1"
    fixture = SyntheticFaces(count=1_000_000, people=10_000, old_people=8_000, seed=20261024, noise=.08)
    people = np.linspace(0, 7_999, 64, dtype=np.int32)
    queries = fixture.queries(people)
    temporary = tempfile.TemporaryDirectory(prefix="lumina-face-legacy-indexes-")
    measured_database = args.database.resolve()
    if args.legacy_indexes:
        measured_database = Path(temporary.name) / "legacy.sqlite"
        with sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True) as source:
            with sqlite3.connect(measured_database) as target:
                source.backup(target, pages=4096, sleep=0)
                target.execute("DROP INDEX IF EXISTS ix_photo_faces_ann_prototype")
                target.execute("DROP INDEX IF EXISTS ix_photo_faces_ann_unassigned")
    engine = create_engine(URL.create("sqlite", database=f"file:{measured_database}", query={"mode": "ro", "uri": "true"}))
    timings = []
    with Session(engine, autoflush=False) as db:
        total = db.execute(text("SELECT count(*) FROM photo_faces")).scalar_one()
        if total != 1_000_000:
            raise RuntimeError("The immutable million-face fixture must be fully seeded before this measurement")
        for _ in range(args.trials):
            start = time.perf_counter()
            prototypes, unassigned = module._candidate_faces(db, "perf-owner")
            candidates = [*prototypes, *unassigned]
            matrix = np.stack([module.vector(row.embedding) for row in candidates])
            if not np.isfinite(matrix).all() or not np.isfinite(queries[0]).all():
                raise RuntimeError("Non-finite synthetic benchmark input")
            scores = np.einsum("ij,j->i", matrix, queries[0], optimize=False)
            if not np.isfinite(scores).all():
                raise RuntimeError("Non-finite synthetic benchmark score")
            sorted(zip(scores, candidates), key=lambda item: (-float(item[0]), item[1].id))
            timings.append((time.perf_counter() - start) * 1000)
        ids = np.asarray([int(row.id.removeprefix("face-")) for row in candidates], dtype=np.int64)
        candidate_people = fixture.people_for(ids)
        coverage = sum(bool(np.any(candidate_people == person)) for person in people) / len(people)
    engine.dispose()
    temporary.cleanup()
    report = {
        "benchmark": "archived-recency-capped-face-candidates",
        "baseline_commit": "944fdbe12cf3b324aff0afb41b6cd8e6582f6e28",
        "uses_legacy_photo_face_indexes": args.legacy_indexes,
        "baseline_service_sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
        "fixture_embeddings": total, "prototypes": len(prototypes), "unassigned": len(unassigned),
        "retrieval_stack_score_and_sort_ms": {"raw_ms": timings, "median_ms": statistics.median(timings), "p95_ms": float(np.percentile(timings, 95))},
        "old_person_candidate_coverage_64_queries": coverage,
        "limits": "Archived service on the synthetic all-unassigned fixture; when legacy-indexes is enabled, a private consistent SQLite backup removes the two photo-face indexes introduced by this change. Other unused scale tables remain but do not participate in its queries. Times include candidate SQL, ORM materialization, vector stacking, finite float32 einsum scoring and sorting for one query. The recency cap intentionally misses older identities; this is not equivalent matching quality. The main qualification concurrently built its single-threaded graph on the shared development host.",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
