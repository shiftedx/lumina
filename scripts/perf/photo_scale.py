"""Bounded-RSS synthetic face-index qualification; never opens household media.

Run after the pinned Faiss environment is installed:

    mise exec -- backend/.venv/bin/python scripts/perf/photo_scale.py \
      --count 1000000 --queries 64 \
      --output docs/performance/measurements/photo-face-index-1m-2026-10-24.json

The vectors are synthetic, fixed-seed, normalized 128-d float32 values.  The
benchmark holds only a construction chunk, a 16k recency window, a small exact
representative set, and the native index in memory.  Its temporary serialized
index is deleted on exit.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

# Make the resident exact reference comparable to the single-threaded Faiss
# setting. These must be set before NumPy imports its BLAS provider.
for _thread_environment in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_thread_environment, "1")

import numpy as np


DEFAULT_COUNT = 1_000_000
DIMENSIONS = 128
DEFAULT_PEOPLE = 10_000
DEFAULT_OLD_PEOPLE = 8_000


def _phase(message: str) -> None:
    print(f"photo-scale: {message}", file=sys.stderr, flush=True)


def _percentile(samples: list[float], percentile: float) -> float:
    return round(float(np.percentile(np.asarray(samples), percentile)), 3)


def _peak_rss_bytes() -> int:
    """Return process peak RSS using the platform units exposed by getrusage."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _normalise(values: np.ndarray) -> np.ndarray:
    values = np.ascontiguousarray(values, dtype=np.float32)
    values /= np.linalg.norm(values, axis=1, keepdims=True)
    return values


def _require_unit(values: np.ndarray, label: str) -> None:
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{label} contains non-finite values")
    norms = np.linalg.norm(values, axis=1)
    if not np.all(np.isfinite(norms)) or not np.allclose(norms, 1.0, rtol=1e-5, atol=1e-5):
        raise ValueError(f"{label} is not unit-normalized")


class SyntheticFaces:
    """Generates deterministic chronological face vectors without image files."""

    def __init__(self, *, count: int, people: int, old_people: int, seed: int, noise: float):
        self.count = count
        self.people = people
        self.old_people = old_people
        self.cutover = count * 4 // 5
        self.seed = seed
        self.noise = noise
        rng = np.random.default_rng(seed)
        self.prototypes = _normalise(rng.standard_normal((people, DIMENSIONS), dtype=np.float32))

    def people_for(self, ids: np.ndarray) -> np.ndarray:
        ids = np.asarray(ids, dtype=np.int64)
        result = np.empty(ids.shape, dtype=np.int32)
        older = ids < self.cutover
        result[older] = ids[older] % self.old_people
        result[~older] = self.old_people + ((ids[~older] - self.cutover) % (self.people - self.old_people))
        return result

    def vectors(self, start: int, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ids = np.arange(start, start + count, dtype=np.int64)
        persons = self.people_for(ids)
        # A chunk-local deterministic stream avoids retaining the corpus. Query vectors
        # below are independently generated and are never inserted into the index.
        rng = np.random.default_rng(self.seed + 100_003 + start)
        values = self.prototypes[persons] + self.noise * rng.standard_normal((count, DIMENSIONS), dtype=np.float32)
        return ids, persons, _normalise(values)

    def queries(self, persons: np.ndarray) -> np.ndarray:
        rng = np.random.default_rng(self.seed + 2_000_003)
        values = self.prototypes[persons] + self.noise * rng.standard_normal((len(persons), DIMENSIONS), dtype=np.float32)
        return _normalise(values)


def _create_index(faiss, *, m: int, ef_construction: int, ef_search: int):
    base = faiss.IndexHNSWFlat(DIMENSIONS, m, faiss.METRIC_INNER_PRODUCT)
    base.hnsw.efConstruction = ef_construction
    base.hnsw.efSearch = ef_search
    return faiss.IndexIDMap2(base)


def _capture_representatives(
    representatives: np.ndarray,
    seen: np.ndarray,
    persons: np.ndarray,
    vectors: np.ndarray,
) -> None:
    unseen_rows = np.flatnonzero(~seen[persons])
    if not len(unseen_rows):
        return
    unseen_people = persons[unseen_rows]
    unique_people, positions = np.unique(unseen_people, return_index=True)
    source_rows = unseen_rows[positions]
    representatives[unique_people] = vectors[source_rows]
    seen[unique_people] = True


def _recall_at_k(found: np.ndarray, exact: np.ndarray) -> float:
    return round(float(np.mean([
        len(set(map(int, row_found[row_found >= 0])) & set(map(int, row_exact[row_exact >= 0]))) / len(row_exact)
        for row_found, row_exact in zip(found, exact, strict=True)
    ])), 6)


def _timed_searches(index, queries: np.ndarray, query_people: np.ndarray, candidate_count: int, people_for) -> tuple[dict, np.ndarray]:
    durations: list[float] = []
    hit_count = 0
    all_keys = np.empty((len(queries), candidate_count), dtype=np.int64)
    for query, person in zip(queries, query_people, strict=True):
        started = time.perf_counter_ns()
        _distances, keys = index.search(query.reshape(1, -1), candidate_count)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        valid_keys = keys[0][keys[0] >= 0]
        hit_count += int(np.any(people_for(valid_keys) == person))
        all_keys[len(durations) - 1] = keys[0]
    return {
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
        "candidate_recall_at_k": round(hit_count / len(queries), 6),
        "older_person_coverage": round(hit_count / len(np.unique(query_people)), 6),
    }, all_keys


def _timed_exact(candidates: np.ndarray, candidate_ids: np.ndarray, candidate_people: np.ndarray, queries: np.ndarray, query_people: np.ndarray, candidate_count: int) -> tuple[dict, np.ndarray]:
    _require_unit(candidates, "exact candidates")
    _require_unit(queries, "exact queries")
    durations: list[float] = []
    hit_count = 0
    all_keys = np.empty((len(queries), candidate_count), dtype=np.int64)
    for query, person in zip(queries, query_people, strict=True):
        started = time.perf_counter_ns()
        # NumPy's macOS matmul reports spurious floating-point-status warnings on
        # finite unit inputs here. Einsum computes the same float32 dot products
        # without that platform-specific reporting path.
        scores = np.einsum("ij,j->i", candidates, query, optimize=True)
        k = min(candidate_count, len(scores))
        top = np.argpartition(scores, -k)[-k:]
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        hit_count += int(np.any(candidate_people[top] == person))
        all_keys[len(durations) - 1, :k] = candidate_ids[top]
        if k < candidate_count:
            all_keys[len(durations) - 1, k:] = -1
    return {
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
        "candidate_recall_at_k": round(hit_count / len(queries), 6),
        "older_person_coverage": round(hit_count / len(np.unique(query_people)), 6),
        "vectors_scanned_per_query": int(len(candidates)),
    }, all_keys


def _stream_global_exact(fixture: SyntheticFaces, *, total: int, queries: np.ndarray, candidate_count: int, chunk_size: int) -> tuple[dict, np.ndarray]:
    """Find exact global top-k without retaining the million-vector corpus."""
    best_scores = np.full((len(queries), candidate_count), -np.inf, dtype=np.float32)
    best_ids = np.full((len(queries), candidate_count), -1, dtype=np.int64)
    _require_unit(queries, "global exact queries")
    started = time.perf_counter()
    for start in range(0, total, chunk_size):
        amount = min(chunk_size, total - start)
        ids, _persons, vectors = fixture.vectors(start, amount)
        _require_unit(vectors, "global exact vectors")
        scores = np.einsum("qd,nd->qn", queries, vectors, optimize=True)
        k = min(candidate_count, amount)
        local = np.argpartition(scores, -k, axis=1)[:, -k:]
        local_scores = np.take_along_axis(scores, local, axis=1)
        local_ids = ids[local]
        joined_scores = np.concatenate((best_scores, local_scores), axis=1)
        joined_ids = np.concatenate((best_ids, local_ids), axis=1)
        winners = np.argpartition(joined_scores, -candidate_count, axis=1)[:, -candidate_count:]
        best_scores = np.take_along_axis(joined_scores, winners, axis=1)
        best_ids = np.take_along_axis(joined_ids, winners, axis=1)
    elapsed = time.perf_counter() - started
    return {
        "seconds": round(elapsed, 3),
        "vectors_scanned_per_query": total,
        "query_vectors": len(queries),
    }, best_ids


def _resident_exact_baseline(
    fixture: SyntheticFaces,
    *,
    total: int,
    queries: np.ndarray,
    query_people: np.ndarray,
    candidate_count: int,
    chunk_size: int,
) -> dict:
    """Time exact top-k against a resident float32 corpus without an ANN graph."""
    started = time.perf_counter()
    vectors = np.empty((total, DIMENSIONS), dtype=np.float32)
    people = np.empty(total, dtype=np.int32)
    for start in range(0, total, chunk_size):
        amount = min(chunk_size, total - start)
        _ids, generated_people, generated_vectors = fixture.vectors(start, amount)
        vectors[start:start + amount] = generated_vectors
        people[start:start + amount] = generated_people
    resident_seconds = time.perf_counter() - started
    search, _keys = _timed_exact(
        vectors,
        np.arange(total, dtype=np.int64),
        people,
        queries,
        query_people,
        candidate_count,
    )
    result = {
        "resident_matrix_build_seconds": round(resident_seconds, 3),
        "resident_matrix_bytes": int(vectors.nbytes),
        "search": search,
        "peak_rss_bytes": _peak_rss_bytes(),
    }
    del vectors, people
    gc.collect()
    return result


def _service_recall(candidates, exact: np.ndarray) -> float:
    values = []
    for candidate, expected in zip(candidates, exact, strict=True):
        found = {int(face_id.removeprefix("face-")) for face_id in candidate.unassigned_face_ids}
        values.append(len(found & set(map(int, expected))) / len(expected))
    return round(float(np.mean(values)), 6)


def _prototype_person_recall(candidates, query_people: np.ndarray, fixture: SyntheticFaces) -> float:
    """Whether each query sees its person's one retained prototype in the bounded ANN set."""
    hits = []
    for candidate, person in zip(candidates, query_people, strict=True):
        face_ids = [int(face_id.removeprefix("face-")) for face_id in candidate.prototype_face_ids]
        hits.append(bool(face_ids) and bool(np.any(fixture.people_for(np.asarray(face_ids)) == person)))
    return round(float(np.mean(hits)), 6)


def _service_benchmark(fixture: SyntheticFaces, *, count: int, queries: np.ndarray, exact_keys: np.ndarray, chunk_size: int, candidate_count: int) -> dict:
    """Exercise the shipped SQLite/checkpoint/replay path in disposable storage."""
    old_data_dir = os.environ.get("LUMINA_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="lumina-photo-face-service-") as temporary:
        root = Path(temporary)
        os.environ["LUMINA_DATA_DIR"] = str(root / "data")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
        runtime_source_sha256 = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "backend/app/services/photo_face_index.py").read_bytes()
        ).hexdigest()
        _phase("service fixture seeding")
        from sqlalchemy import create_engine, delete, insert, update
        from sqlalchemy.orm import Session
        from app.db import Base
        from app import photo_face_index_models, photo_face_models, photo_models  # noqa: F401 - register tables/triggers
        from app.photo_face_models import PhotoFace
        from app.photo_models import PhotoItem
        from app.services import photo_face_index

        owner = "perf-owner"
        pipeline = "synthetic-face-index-v1"
        database = root / "faces.sqlite"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        fixture_started = time.perf_counter()
        with Session(engine, expire_on_commit=False) as db:
            for start in range(0, count, chunk_size):
                amount = min(chunk_size, count - start)
                ids, _people, vectors = fixture.vectors(start, amount)
                db.execute(insert(PhotoItem), [{
                    "id": f"photo-{int(value):030d}",
                    "owner_user_id": owner,
                    "visibility": "private",
                    "storage_kind": "managed",
                    "storage_key": f"synthetic/{int(value):012d}",
                    "original_filename": f"synthetic-{int(value):012d}.heic",
                    "content_type": "image/heic",
                    "sha256": f"{int(value):064x}",
                    "bytes": 1,
                    "width": 1,
                    "height": 1,
                    "orientation": 1,
                    "metadata_json": {},
                } for value in ids])
                db.execute(insert(PhotoFace), [{
                    "id": f"face-{int(value):031d}",
                    "photo_id": f"photo-{int(value):030d}",
                    "owner_user_id": owner,
                    "pipeline_signature": pipeline,
                    "person_id": None,
                    "detector_score": 1.0,
                    "x1": 0.0,
                    "y1": 0.0,
                    "x2": 1.0,
                    "y2": 1.0,
                    "embedding": vectors[offset].tobytes(),
                    "assignment_source": "automatic",
                } for offset, value in enumerate(ids)])
                db.commit()
            fixture_write_seconds = time.perf_counter() - fixture_started
            _phase("service initial checkpoint build")
            first_started = time.perf_counter()
            initial = photo_face_index.prepare_candidates(db, owner, pipeline, list(queries), unassigned_limit=candidate_count)
            first_seconds = time.perf_counter() - first_started
            search_durations = []
            for query in queries:
                started = time.perf_counter_ns()
                photo_face_index.prepare_candidates(db, owner, pipeline, [query], unassigned_limit=candidate_count)
                search_durations.append((time.perf_counter_ns() - started) / 1_000_000)
            initial_recall = _service_recall(initial, exact_keys)
            changed = range(count - min(64, count), count)
            db.execute(update(PhotoFace).where(PhotoFace.id.in_([
                f"face-{value:031d}" for value in list(changed)[:32]
            ])).values(assignment_source="manual"))
            db.execute(delete(PhotoFace).where(PhotoFace.id.in_([
                f"face-{value:031d}" for value in list(changed)[32:]
            ])))
            db.commit()
            _phase("service journal replay and settle")
            replay_started = time.perf_counter()
            replay = photo_face_index.prepare_candidates(db, owner, pipeline, [queries[0]], unassigned_limit=candidate_count)
            replay_seconds = time.perf_counter() - replay_started
            if len(replay) != 1:
                raise RuntimeError("service replay did not return one candidate set")
            settle_started = time.perf_counter()
            settled_scopes = photo_face_index.settle_pending(db)
            settle_seconds = time.perf_counter() - settle_started
        photo_face_index.reset_for_tests()
        with Session(engine, expire_on_commit=False) as db:
            _phase("service checkpoint restart")
            restart_started = time.perf_counter()
            restarted = photo_face_index.prepare_candidates(db, owner, pipeline, list(queries), unassigned_limit=candidate_count)
            restart_seconds = time.perf_counter() - restart_started
        photo_face_index.reset_for_tests()
        engine.dispose()
        result = {
            "photo_face_index_source_sha256": runtime_source_sha256,
            "fixture_write_seconds": round(fixture_write_seconds, 3),
            "sqlite_bytes": database.stat().st_size,
            "initial_build_and_checkpoint_seconds": round(first_seconds, 3),
            "candidate_search_p50_ms": _percentile(search_durations, 50),
            "candidate_search_p95_ms": _percentile(search_durations, 95),
            "candidate_exact_recall_at_k": initial_recall,
            "checkpoint_restart_seconds": round(restart_seconds, 3),
            "restart_exact_recall_at_k": _service_recall(restarted, exact_keys),
            "journal_replay_after_32_assignment_updates_and_32_deletes_seconds": round(replay_seconds, 3),
            "settle_pending_after_mutations_seconds": round(settle_seconds, 3),
            "settled_scope_count": settled_scopes,
            "peak_rss_bytes": _peak_rss_bytes(),
        }
    if old_data_dir is None:
        os.environ.pop("LUMINA_DATA_DIR", None)
    else:
        os.environ["LUMINA_DATA_DIR"] = old_data_dir
    return result


def _prototype_service_benchmark(fixture: SyntheticFaces, *, count: int, queries: np.ndarray, query_people: np.ndarray, chunk_size: int, candidate_count: int) -> dict:
    """Qualify assigned-face prototype build, journal refresh, and restart."""
    old_data_dir = os.environ.get("LUMINA_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="lumina-photo-face-prototype-") as temporary:
        root = Path(temporary)
        os.environ["LUMINA_DATA_DIR"] = str(root / "data")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
        runtime_source_sha256 = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "backend/app/services/photo_face_index.py").read_bytes()
        ).hexdigest()
        from sqlalchemy import create_engine, insert, update
        from sqlalchemy.orm import Session
        from app.db import Base
        from app import photo_face_index_models, photo_face_models, photo_models  # noqa: F401 - register tables/triggers
        from app.photo_face_models import PhotoFace, PhotoPerson
        from app.photo_models import PhotoItem
        from app.services import photo_face_index

        photo_face_index.reset_for_tests()
        owner = "perf-owner"
        pipeline = "synthetic-face-index-v1"
        database = root / "faces.sqlite"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        fixture_started = time.perf_counter()
        with Session(engine, expire_on_commit=False) as db:
            db.execute(insert(PhotoPerson), [{
                "id": f"person-{value:029d}", "owner_user_id": owner, "pipeline_signature": pipeline,
                "name": None, "hidden": False, "feature_face_id": None, "redirected_to_id": None, "revision": 0,
            } for value in range(fixture.people)])
            db.commit()
            for start in range(0, count, chunk_size):
                amount = min(chunk_size, count - start)
                ids, persons, vectors = fixture.vectors(start, amount)
                db.execute(insert(PhotoItem), [{
                    "id": f"photo-{int(value):030d}", "owner_user_id": owner, "visibility": "private",
                    "storage_kind": "managed", "storage_key": f"synthetic/{int(value):012d}",
                    "original_filename": f"synthetic-{int(value):012d}.heic", "content_type": "image/heic",
                    "sha256": f"{int(value):064x}", "bytes": 1, "width": 1, "height": 1, "orientation": 1,
                    "metadata_json": {},
                } for value in ids])
                db.execute(insert(PhotoFace), [{
                    "id": f"face-{int(value):031d}", "photo_id": f"photo-{int(value):030d}",
                    "owner_user_id": owner, "pipeline_signature": pipeline,
                    "person_id": f"person-{int(persons[offset]):029d}", "detector_score": 1.0,
                    "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0,
                    "embedding": vectors[offset].tobytes(), "assignment_source": "automatic",
                } for offset, value in enumerate(ids)])
                db.commit()
            fixture_write_seconds = time.perf_counter() - fixture_started
            build_started = time.perf_counter()
            initial = photo_face_index.prepare_candidates(
                db, owner, pipeline, list(queries), prototype_limit=candidate_count, unassigned_limit=1,
            )
            build_seconds = time.perf_counter() - build_started
            search_durations = []
            for query in queries:
                started = time.perf_counter_ns()
                photo_face_index.prepare_candidates(
                    db, owner, pipeline, [query], prototype_limit=candidate_count, unassigned_limit=1,
                )
                search_durations.append((time.perf_counter_ns() - started) / 1_000_000)
            protected_people = {int(person) for person in query_people}
            changed: list[tuple[int, int]] = []
            for value in range(count - 1, -1, -1):
                current_person = int(fixture.people_for(np.asarray([value]))[0])
                replacement_person = (current_person + 1) % fixture.people
                if current_person in protected_people or replacement_person in protected_people:
                    continue
                changed.append((value, replacement_person))
                if len(changed) == min(32, count):
                    break
            if len(changed) != min(32, count):
                raise RuntimeError("prototype fixture could not select non-query person refreshes")
            for value, replacement_person in changed:
                db.execute(update(PhotoFace).where(PhotoFace.id == f"face-{value:031d}").values(
                    person_id=f"person-{replacement_person:029d}",
                ))
            db.commit()
            replay_started = time.perf_counter()
            replay = photo_face_index.prepare_candidates(
                db, owner, pipeline, [queries[0]], prototype_limit=candidate_count, unassigned_limit=1,
            )
            replay_seconds = time.perf_counter() - replay_started
            if len(replay) != 1:
                raise RuntimeError("prototype replay did not return one candidate set")
            settle_started = time.perf_counter()
            settled_scopes = photo_face_index.settle_pending(db)
            settle_seconds = time.perf_counter() - settle_started
        photo_face_index.reset_for_tests()
        with Session(engine, expire_on_commit=False) as db:
            restart_started = time.perf_counter()
            restarted = photo_face_index.prepare_candidates(
                db, owner, pipeline, list(queries), prototype_limit=candidate_count, unassigned_limit=1,
            )
            restart_seconds = time.perf_counter() - restart_started
            restarted_again = photo_face_index.prepare_candidates(
                db, owner, pipeline, list(queries), prototype_limit=candidate_count, unassigned_limit=1,
            )
        photo_face_index.reset_for_tests()
        engine.dispose()
        result = {
            "photo_face_index_source_sha256": runtime_source_sha256,
            "fixture_write_seconds": round(fixture_write_seconds, 3),
            "sqlite_bytes": database.stat().st_size,
            "prototype_build_and_checkpoint_seconds": round(build_seconds, 3),
            "prototype_search_p50_ms": _percentile(search_durations, 50),
            "prototype_search_p95_ms": _percentile(search_durations, 95),
            "prototype_person_recall_at_k": _prototype_person_recall(initial, query_people, fixture),
            "journal_replay_after_32_person_reassignments_seconds": round(replay_seconds, 3),
            "person_reassignments_excluded_query_source_and_target_people": len(changed),
            "settle_pending_after_person_reassignments_seconds": round(settle_seconds, 3),
            "settled_scope_count": settled_scopes,
            "checkpoint_restart_seconds": round(restart_seconds, 3),
            "restart_prototype_person_recall_at_k": _prototype_person_recall(restarted, query_people, fixture),
            "second_restart_prototype_person_recall_at_k": _prototype_person_recall(restarted_again, query_people, fixture),
            "peak_rss_bytes": _peak_rss_bytes(),
        }
    if old_data_dir is None:
        os.environ.pop("LUMINA_DATA_DIR", None)
    else:
        os.environ["LUMINA_DATA_DIR"] = old_data_dir
    return result


def _write_output(path: Path | None, payload: dict) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(encoded, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    print(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--queries", type=int, default=64)
    parser.add_argument("--people", type=int, default=DEFAULT_PEOPLE)
    parser.add_argument("--old-people", type=int, default=DEFAULT_OLD_PEOPLE)
    parser.add_argument("--recency-cap", type=int, default=16_000)
    parser.add_argument("--candidate-count", type=int, default=32)
    parser.add_argument("--chunk-size", type=int, default=8_192)
    parser.add_argument("--updates", type=int, default=10_000)
    parser.add_argument("--m", type=int, default=16)
    parser.add_argument("--ef-construction", type=int, default=96)
    parser.add_argument("--ef-search", type=int, default=96)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--noise", type=float, default=0.08)
    parser.add_argument("--skip-rebuild", action="store_true")
    parser.add_argument("--service", action="store_true", help="also exercise the shipped SQLite/checkpoint candidate service")
    parser.add_argument("--resident-exact-only", action="store_true", help="measure only exact top-k against a resident full corpus")
    parser.add_argument("--prototype-service-only", action="store_true", help="qualify assigned-face prototype service path only")
    parser.add_argument("--service-only", action="store_true", help="update an existing direct-core JSON with service measurements")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not 10_000 <= args.count <= 1_000_000:
        parser.error("count must be between 10000 and 1000000")
    if not 1 <= args.queries <= args.old_people or not args.old_people < args.people:
        parser.error("queries must be within old people, and 0 < old-people < people")
    if min(args.recency_cap, args.candidate_count, args.chunk_size, args.m, args.ef_construction, args.ef_search, args.threads) < 1:
        parser.error("caps, HNSW parameters, and threads must be positive")
    if args.recency_cap > args.count or args.candidate_count > args.count:
        parser.error("recency-cap and candidate-count must not exceed count")
    if not 0.0 < args.noise < 1.0:
        parser.error("noise must be between zero and one")
    if sum((args.resident_exact_only, args.prototype_service_only, args.service_only, args.service)) > 1:
        parser.error("--resident-exact-only, --prototype-service-only, --service-only, and --service are mutually exclusive")

    fixture = SyntheticFaces(count=args.count, people=args.people, old_people=args.old_people, seed=args.seed, noise=args.noise)
    query_people = np.linspace(0, args.old_people - 1, args.queries, dtype=np.int32)
    queries = fixture.queries(query_people)
    if args.resident_exact_only:
        _phase("building resident exact baseline")
        payload = {
            "benchmark": "synthetic-photo-face-index-resident-exact",
            "generated_at": datetime.now(UTC).isoformat(),
            "environment": {
                "numpy_version": np.__version__,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "machine": platform.machine(),
                "blas_thread_environment": {
                    name: os.environ.get(name) for name in (
                        "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    )
                },
            },
            "config": {
                "embeddings": args.count,
                "dimensions": DIMENSIONS,
                "query_people": args.queries,
                "candidate_count": args.candidate_count,
                "chunk_size": args.chunk_size,
                "seed": args.seed,
                "noise": args.noise,
            },
            "results": _resident_exact_baseline(
                fixture,
                total=args.count,
                queries=queries,
                query_people=query_people,
                candidate_count=args.candidate_count,
                chunk_size=args.chunk_size,
            ),
            "limits": [
                "This is a pure exact float32 cosine/inner-product baseline with a resident corpus, not a SQLite, photo-ingestion, model, or grouping-accuracy measurement.",
                "It is deliberately single-thread conditioned through BLAS environment variables recorded above; compare it only to the similarly single-threaded direct Faiss timing.",
            ],
        }
        _write_output(args.output, payload)
        return
    if args.prototype_service_only:
        _phase("building assigned prototype service qualification")
        payload = {
            "benchmark": "synthetic-photo-face-index-prototype-service",
            "generated_at": datetime.now(UTC).isoformat(),
            "environment": {
                "numpy_version": np.__version__,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "machine": platform.machine(),
            },
            "config": {
                "assigned_embeddings": args.count,
                "synthetic_people": args.people,
                "query_people": args.queries,
                "candidate_count": args.candidate_count,
                "chunk_size": args.chunk_size,
                "seed": args.seed,
                "noise": args.noise,
            },
            "results": _prototype_service_benchmark(
                fixture,
                count=args.count,
                queries=queries,
                query_people=query_people,
                chunk_size=args.chunk_size,
                candidate_count=args.candidate_count,
            ),
            "limits": [
                "Every synthetic face is assigned to a synthetic person. This measures one retained ANN prototype per person, not face-model or person-grouping accuracy.",
                "The journal exercise reassigns 32 faces between existing people; it is a bounded person-prototype refresh, not a bulk correction benchmark.",
            ],
        }
        _write_output(args.output, payload)
        return
    if args.service_only:
        if args.output is None or not args.output.is_file():
            parser.error("--service-only requires an existing --output direct-core JSON")
        _phase("streaming service-only exact ground truth")
        _global_exact, exact_keys = _stream_global_exact(
            fixture, total=args.count, queries=queries, candidate_count=args.candidate_count, chunk_size=args.chunk_size,
        )
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        _phase("building and qualifying SQLite service path")
        payload["results"]["service_path"] = _service_benchmark(
            fixture,
            count=args.count,
            queries=queries,
            exact_keys=exact_keys,
            chunk_size=args.chunk_size,
            candidate_count=args.candidate_count,
        )
        payload.setdefault("limits", []).append(
            "The service path was rerun separately from the direct-core phases against the source hash recorded in service_path."
        )
        _write_output(args.output, payload)
        return

    try:
        import faiss  # Installed by the parent-owned dependency change.
    except ImportError as error:
        raise SystemExit("faiss-cpu is required; install the pinned project environment before running this benchmark") from error

    faiss.omp_set_num_threads(args.threads)
    representatives = np.empty((args.people, DIMENSIONS), dtype=np.float32)
    seen = np.zeros(args.people, dtype=bool)
    recency_vectors = np.empty((args.recency_cap, DIMENSIONS), dtype=np.float32)
    recency_people = np.empty(args.recency_cap, dtype=np.int32)

    _phase("building direct Faiss HNSW index")
    started = time.perf_counter()
    index = _create_index(faiss, m=args.m, ef_construction=args.ef_construction, ef_search=args.ef_search)
    for start in range(0, args.count, args.chunk_size):
        amount = min(args.chunk_size, args.count - start)
        ids, persons, vectors = fixture.vectors(start, amount)
        index.add_with_ids(vectors, ids)
        _capture_representatives(representatives, seen, persons, vectors)
        if start + amount > args.count - args.recency_cap:
            local_start = max(0, args.count - args.recency_cap - start)
            target_start = max(0, start - (args.count - args.recency_cap))
            selected = amount - local_start
            recency_vectors[target_start:target_start + selected] = vectors[local_start:]
            recency_people[target_start:target_start + selected] = persons[local_start:]
    build_seconds = time.perf_counter() - started
    if not seen.all():
        raise RuntimeError("synthetic stream did not yield one representative for every person")

    _phase("streaming global exact ground truth")
    global_exact, exact_keys = _stream_global_exact(
        fixture, total=args.count, queries=queries, candidate_count=args.candidate_count, chunk_size=args.chunk_size,
    )
    hnsw_search, hnsw_keys = _timed_searches(index, queries, query_people, args.candidate_count, fixture.people_for)
    hnsw_search["exact_recall_at_k"] = _recall_at_k(hnsw_keys, exact_keys)
    recency_search, recency_keys = _timed_exact(
        recency_vectors,
        np.arange(args.count - args.recency_cap, args.count, dtype=np.int64),
        recency_people,
        queries,
        query_people,
        args.candidate_count,
    )
    recency_search["exact_recall_at_k"] = _recall_at_k(recency_keys, exact_keys)
    exact_search, _representative_keys = _timed_exact(
        representatives,
        np.arange(args.people, dtype=np.int64),
        np.arange(args.people, dtype=np.int32),
        queries,
        query_people,
        args.candidate_count,
    )

    _phase("adding incremental direct Faiss vectors")
    update_start = args.count
    update_started = time.perf_counter()
    for start in range(update_start, update_start + args.updates, args.chunk_size):
        amount = min(args.chunk_size, update_start + args.updates - start)
        ids, _persons, vectors = fixture.vectors(start, amount)
        index.add_with_ids(vectors, ids)
    update_seconds = time.perf_counter() - update_started

    with tempfile.TemporaryDirectory(prefix="lumina-photo-face-index-") as temporary:
        artifact = Path(temporary) / "synthetic.faiss"
        _phase("serializing and reloading direct Faiss index")
        save_started = time.perf_counter()
        faiss.write_index(index, str(artifact))
        save_seconds = time.perf_counter() - save_started
        artifact_bytes = artifact.stat().st_size
        del index
        gc.collect()
        load_started = time.perf_counter()
        reloaded = faiss.read_index(str(artifact))
        load_seconds = time.perf_counter() - load_started
        reloaded_search, reloaded_keys = _timed_searches(reloaded, queries, query_people, args.candidate_count, fixture.people_for)
        reloaded_search["exact_recall_at_k_against_base"] = _recall_at_k(reloaded_keys, exact_keys)
        del reloaded
        gc.collect()

        rebuild_seconds: float | None = None
        if not args.skip_rebuild:
            _phase("rebuilding direct Faiss index")
            rebuild_started = time.perf_counter()
            rebuilt = _create_index(faiss, m=args.m, ef_construction=args.ef_construction, ef_search=args.ef_search)
            for start in range(0, args.count + args.updates, args.chunk_size):
                amount = min(args.chunk_size, args.count + args.updates - start)
                ids, _persons, vectors = fixture.vectors(start, amount)
                rebuilt.add_with_ids(vectors, ids)
            rebuild_seconds = time.perf_counter() - rebuild_started
            del rebuilt
            gc.collect()

    payload = {
        "benchmark": "synthetic-photo-face-index",
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "faiss_version": getattr(faiss, "__version__", "unknown"),
            "numpy_version": np.__version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "config": {
            "base_embeddings": args.count,
            "incremental_embeddings": args.updates,
            "dimensions": DIMENSIONS,
            "people": args.people,
            "old_people": args.old_people,
            "query_people": args.queries,
            "recency_cap": args.recency_cap,
            "candidate_count": args.candidate_count,
            "chunk_size": args.chunk_size,
            "hnsw_m": args.m,
            "ef_construction": args.ef_construction,
            "ef_search": args.ef_search,
            "faiss_threads": args.threads,
            "seed": args.seed,
            "noise": args.noise,
        },
        "results": {
            "initial_build": {
                "seconds": round(build_seconds, 3),
                "embeddings_per_second": round(args.count / build_seconds, 2),
                "peak_rss_bytes": _peak_rss_bytes(),
            },
            "incremental_add": {
                "seconds": round(update_seconds, 3),
                "embeddings_per_second": round(args.updates / update_seconds, 2) if args.updates else None,
                "peak_rss_bytes": _peak_rss_bytes(),
            },
            "serialized_restart": {
                "save_seconds": round(save_seconds, 3),
                "artifact_bytes": artifact_bytes,
                "load_seconds": round(load_seconds, 3),
                "search": reloaded_search,
                "peak_rss_bytes": _peak_rss_bytes(),
            },
            "rebuild": {
                "seconds": round(rebuild_seconds, 3) if rebuild_seconds is not None else None,
                "embeddings_per_second": round((args.count + args.updates) / rebuild_seconds, 2) if rebuild_seconds else None,
                "skipped": args.skip_rebuild,
                "peak_rss_bytes": _peak_rss_bytes(),
            },
            "hnsw_search": hnsw_search,
            "recency_cap_exact_search": recency_search,
            "representative_exact_search": exact_search,
            "streamed_global_exact_ground_truth": global_exact,
            "service_path": None,
        },
        "limits": [
            "Synthetic vectors exercise only the native Faiss core; they are not face-model, grouping-accuracy, SQLite, NAS, or photo-ingestion measurements.",
            "Representative exact-search timing scans one representative per synthetic person. Separately, streamed global exact ground truth scans every synthetic embedding without retaining the corpus.",
            "The recency-cap comparison intentionally queries old people absent from the final chronological window; zero coverage demonstrates the cap's selection bias, not Faiss accuracy.",
            "Peak RSS is process-wide getrusage high-water mark and includes Python/NumPy; it is not an index-only memory measurement.",
            "The optional service path uses a disposable SQLite database and data directory; it intentionally creates one unassigned face per synthetic photo so prototype-person behavior is not measured.",
        ],
    }
    if args.output is not None:
        _phase("writing durable direct-core results before service path")
        _write_output(args.output, payload)

    if args.service:
        _phase("building and qualifying SQLite service path")
        payload["results"]["service_path"] = _service_benchmark(
            fixture,
            count=args.count,
            queries=queries,
            exact_keys=exact_keys,
            chunk_size=args.chunk_size,
            candidate_count=args.candidate_count,
        )
        _phase("SQLite service path complete")
    _write_output(args.output, payload)


if __name__ == "__main__":
    main()
