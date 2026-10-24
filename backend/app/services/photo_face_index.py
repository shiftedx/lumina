"""Bounded, owner-scoped ANN candidate retrieval for photo face grouping.

SQLite face/vector rows are authoritative.  FAISS HNSW graphs are disposable
accelerators: mutations append to a SQL journal, searches replay that journal,
and checksummed checkpoints avoid rebuilding a million-vector graph at every
restart.  Exact float32 scoring and live-row checks happen after ANN search.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid
from weakref import WeakSet

import faiss
import numpy as np
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.persistence import queue_after_commit, write_transaction


DIMENSIONS = 128
BUILD_PAGE = 8192
SQL_ID_PAGE = 400
JOURNAL_PAGE = 4096
MAX_SCOPES = 32
SCOPE_ESTIMATE_BASE_BYTES = 16 * 1024
VECTOR_ESTIMATE_BYTES = 1024
M = 16
EF_CONSTRUCTION = 96
EF_SEARCH = 96
REBUILD_MIN_STALE = 4096
REBUILD_STALE_FRACTION = 0.20
CHECKPOINT_CHANGES = 10_000
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024

def _root() -> Path:
    return settings.data_dir / "face-index"


@dataclass(frozen=True)
class CandidateIds:
    prototype_face_ids: tuple[str, ...]
    unassigned_face_ids: tuple[str, ...]


class IndexInterrupted(RuntimeError):
    """The face worker is stopping; no partial graph may be published."""


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise IndexInterrupted("face index interrupted")


@dataclass
class _Scope:
    owner_id: str
    pipeline: str
    prototypes: faiss.IndexIDMap2
    unassigned: faiss.IndexIDMap2
    prototype_ids: dict[int, int]
    unassigned_ids: dict[int, int]
    prototype_by_person: dict[str, int]
    unassigned_by_face: dict[str, int]
    watermark: int = 0
    checkpoint_watermark: int = 0
    stale: int = 0
    changes_since_checkpoint: int = 0
    last_checkpoint_at: float = 0.0


_lock = threading.RLock()
_scopes: OrderedDict[tuple[object, str, str], _Scope] = OrderedDict()
_cleaned_binds: WeakSet = WeakSet()


def _index() -> faiss.IndexIDMap2:
    graph = faiss.IndexHNSWFlat(DIMENSIONS, M, faiss.METRIC_INNER_PRODUCT)
    graph.hnsw.efConstruction = EF_CONSTRUCTION
    graph.hnsw.efSearch = EF_SEARCH
    return faiss.IndexIDMap2(graph)


def _queries(values: Sequence[bytes | np.ndarray]) -> np.ndarray:
    if len(values) == 0:
        return np.empty((0, DIMENSIONS), dtype=np.float32)
    rows = []
    for value in values:
        row = np.frombuffer(value, dtype=np.float32) if isinstance(value, bytes) else np.asarray(value, dtype=np.float32)
        if row.shape != (DIMENSIONS,) or not np.all(np.isfinite(row)):
            raise ValueError("face embedding must contain 128 finite float32 values")
        norm = float(np.linalg.norm(row))
        if norm <= 0:
            raise ValueError("face embedding must have nonzero norm")
        rows.append(row / norm)
    return np.ascontiguousarray(rows, dtype=np.float32)


def _add(index: faiss.IndexIDMap2, known: dict[int, int], rows: list[tuple[int, bytes]]) -> None:
    pending: dict[int, bytes] = {}
    for vector_id, embedding in rows:
        position = known.get(vector_id)
        if position is None:
            pending.setdefault(vector_id, embedding)
    rows = list(pending.items())
    if not rows:
        return
    start = int(index.ntotal)
    ids = np.ascontiguousarray([row[0] for row in rows], dtype=np.int64)
    vectors = np.vstack([np.frombuffer(row[1], dtype=np.float32) for row in rows]).astype(np.float32, copy=False)
    if vectors.shape != (len(rows), DIMENSIONS) or not np.all(np.isfinite(vectors)):
        raise ValueError("invalid stored face embedding")
    faiss.normalize_L2(vectors)
    index.add_with_ids(np.ascontiguousarray(vectors), ids)
    for offset, value in enumerate(ids):
        known[int(value)] = start + offset


_PROTOTYPE_PEOPLE = text("""
SELECT p.id FROM photo_people p INDEXED BY ix_photo_people_ann_scope
WHERE p.owner_user_id=:owner AND p.pipeline_signature=:pipeline
  AND p.redirected_to_id IS NULL AND p.id>:after
ORDER BY p.id LIMIT :limit
""")

_PROTOTYPES = text("""
WITH targets(person_id) AS MATERIALIZED (
 SELECT p.id FROM photo_people p
 WHERE p.id IN :people AND p.owner_user_id=:owner AND p.pipeline_signature=:pipeline
   AND p.redirected_to_id IS NULL
), latest(person_id, face_id) AS MATERIALIZED (
 SELECT t.person_id, (
   SELECT f2.id FROM photo_faces f2 INDEXED BY ix_photo_faces_ann_prototype
   JOIN photo_items i2 ON i2.id=f2.photo_id
   WHERE f2.owner_user_id=:owner AND f2.pipeline_signature=:pipeline
     AND f2.person_id=t.person_id AND i2.owner_user_id=:owner
     AND i2.deleted_at IS NULL AND i2.missing_at IS NULL
   ORDER BY f2.updated_at DESC, f2.id ASC LIMIT 1
 ) FROM targets t
)
SELECT v.id, v.embedding, f.id face_id, l.person_id
FROM latest l
CROSS JOIN photo_faces f INDEXED BY sqlite_autoindex_photo_faces_1 ON f.id=l.face_id
CROSS JOIN photo_face_index_vectors v INDEXED BY ix_photo_face_index_vectors_face
  ON v.face_id=f.id AND v.active=1
WHERE l.face_id IS NOT NULL AND v.owner_user_id=:owner AND v.pipeline_signature=:pipeline
""").bindparams(bindparam("people", expanding=True))

_UNASSIGNED = text("""
SELECT v.id vector_id, v.embedding, f.id face_id
FROM photo_face_index_vectors v
JOIN photo_faces f ON f.id=v.face_id JOIN photo_items i ON i.id=f.photo_id
WHERE v.owner_user_id=:owner AND v.pipeline_signature=:pipeline AND v.active=1
  AND f.owner_user_id=:owner AND f.pipeline_signature=:pipeline
  AND i.owner_user_id=:owner AND i.deleted_at IS NULL AND i.missing_at IS NULL
  AND f.person_id IS NULL AND f.assignment_source='automatic' AND v.id>:after
ORDER BY v.id LIMIT :limit
""")


def _fill_unassigned(
    db: Session, scope: _Scope, *, existing_only: bool = False, cancel: threading.Event | None = None,
) -> None:
    after = 0
    while True:
        _check(cancel)
        rows = list(db.execute(_UNASSIGNED, {"owner": scope.owner_id, "pipeline": scope.pipeline, "after": after, "limit": BUILD_PAGE}))
        if not rows:
            return
        if not existing_only:
            _add(scope.unassigned, scope.unassigned_ids,
                 [(int(row.vector_id), bytes(row.embedding)) for row in rows])
            _check(cancel)
        scope.unassigned_by_face.update((str(row.face_id), int(row.vector_id)) for row in rows
                                        if int(row.vector_id) in scope.unassigned_ids)
        after = int(rows[-1].vector_id)


def _fill_prototypes(
    db: Session, scope: _Scope, *, existing_only: bool = False, cancel: threading.Event | None = None,
) -> None:
    after = ""
    while True:
        _check(cancel)
        people = list(db.scalars(_PROTOTYPE_PEOPLE, {
            "owner": scope.owner_id, "pipeline": scope.pipeline, "after": after, "limit": SQL_ID_PAGE,
        }))
        if not people:
            return
        rows = list(db.execute(_PROTOTYPES, {"owner": scope.owner_id, "pipeline": scope.pipeline, "people": people}))
        if not existing_only:
            _add(scope.prototypes, scope.prototype_ids,
                 [(int(row.id), bytes(row.embedding)) for row in rows])
            _check(cancel)
        scope.prototype_by_person.update((str(row.person_id), int(row.id)) for row in rows
                                         if int(row.id) in scope.prototype_ids)
        after = str(people[-1])


def _watermark(db: Session) -> int:
    return int(db.execute(text("SELECT coalesce(max(id),0) FROM photo_face_index_changes")).scalar_one())


def _scope_key(bind, owner: str, pipeline: str) -> str:  # noqa: ANN001
    database = bind.url.render_as_string(hide_password=True)
    return hashlib.sha256(f"{database}\0{owner}\0{pipeline}".encode()).hexdigest()


def _estimated_scope_bytes(scope: _Scope) -> int:
    return SCOPE_ESTIMATE_BASE_BYTES + VECTOR_ESTIMATE_BYTES * int(
        scope.prototypes.ntotal + scope.unassigned.ntotal
    )


def _trim_scopes(current_key: tuple[object, str, str]) -> None:
    """Apply count and soft-byte LRU bounds while retaining the active scope."""
    budget = settings.photo_face_index_cache_max_bytes
    while (len(_scopes) > MAX_SCOPES
           or sum(_estimated_scope_bytes(scope) for scope in _scopes.values()) > budget):
        victim = next((key for key in _scopes if key != current_key), None)
        if victim is None:
            return
        del _scopes[victim]


def _snapshot_bytes(index: faiss.IndexIDMap2) -> bytes:
    return faiss.serialize_index(index).tobytes()


def _atomic_write(path: Path, payload: bytes) -> tuple[int, str]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return len(payload), hashlib.sha256(payload).hexdigest()


def _read_checked(path_text: str, size: int, digest: str) -> bytes:
    path = Path(path_text)
    root = _root().resolve()
    resolved = path.resolve(strict=True)
    if resolved.parent != root or size < 0 or size > MAX_SNAPSHOT_BYTES:
        raise ValueError("unsafe face-index snapshot path")
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size != size:
        raise ValueError("invalid face-index snapshot file")
    payload = path.read_bytes()
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("face-index snapshot checksum mismatch")
    return payload


def _load_snapshot(
    db: Session, owner: str, pipeline: str, cancel: threading.Event | None = None,
) -> _Scope | None:
    row = db.execute(text("""
      SELECT watermark,runtime_version,dimensions,metric,hnsw_m,ef_construction,ef_search,
             prototype_path,prototype_size,prototype_sha256,
             unassigned_path,unassigned_size,unassigned_sha256
      FROM photo_face_index_snapshots WHERE owner_user_id=:owner AND pipeline_signature=:pipeline
    """), {"owner": owner, "pipeline": pipeline}).mappings().first()
    if row is None:
        return None
    try:
        _check(cancel)
        if (row["runtime_version"] != faiss.__version__ or row["dimensions"] != DIMENSIONS
                or row["metric"] != "inner_product" or row["hnsw_m"] != M
                or row["ef_construction"] != EF_CONSTRUCTION or row["ef_search"] != EF_SEARCH):
            raise ValueError("face-index snapshot configuration mismatch")
        prototype = faiss.deserialize_index(np.frombuffer(_read_checked(
            row["prototype_path"], row["prototype_size"], row["prototype_sha256"]), dtype=np.uint8
        ))
        _check(cancel)
        unassigned = faiss.deserialize_index(np.frombuffer(_read_checked(
            row["unassigned_path"], row["unassigned_size"], row["unassigned_sha256"]), dtype=np.uint8
        ))
        _check(cancel)
        if not isinstance(prototype, faiss.IndexIDMap2) or not isinstance(unassigned, faiss.IndexIDMap2):
            raise ValueError("unexpected face-index snapshot type")
        if (prototype.d != DIMENSIONS or unassigned.d != DIMENSIONS
                or prototype.metric_type != faiss.METRIC_INNER_PRODUCT
                or unassigned.metric_type != faiss.METRIC_INNER_PRODUCT):
            raise ValueError("unexpected face-index snapshot geometry")
        prototype_ids = {int(value): position for position, value in enumerate(faiss.vector_to_array(prototype.id_map))}
        unassigned_ids = {int(value): position for position, value in enumerate(faiss.vector_to_array(unassigned.id_map))}
        scope = _Scope(
            owner, pipeline, prototype, unassigned, prototype_ids, unassigned_ids,
            {}, {}, int(row["watermark"]), checkpoint_watermark=int(row["watermark"]),
        )
        # Membership maps are rebuilt from authoritative rows; the costly HNSW
        # graph itself comes from the validated checkpoint.
        _load_membership(db, scope, cancel)
        return scope
    except (OSError, RuntimeError, ValueError):
        return None


def _build(db: Session, owner: str, pipeline: str, cancel: threading.Event | None = None) -> _Scope:
    _check(cancel)
    boundary = _watermark(db)
    scope = _Scope(owner, pipeline, _index(), _index(), {}, {}, {}, {}, boundary)
    _fill_prototypes(db, scope, cancel=cancel)
    _fill_unassigned(db, scope, cancel=cancel)
    _sync(db, scope, cancel)
    return scope


def _load_membership(db: Session, scope: _Scope, cancel: threading.Event | None = None) -> None:
    # Map only keys already present in the checkpoint. Journal replay adds newer
    # keys; treating them as checkpoint members would inflate tombstone counts.
    _fill_prototypes(db, scope, existing_only=True, cancel=cancel)
    _fill_unassigned(db, scope, existing_only=True, cancel=cancel)
    scope.stale = ((int(scope.prototypes.ntotal) - len(scope.prototype_by_person))
                   + (int(scope.unassigned.ntotal) - len(scope.unassigned_by_face)))


def _current_rows(
    db: Session, owner: str, pipeline: str, face_ids: set[str], person_ids: set[str],
    cancel: threading.Event | None = None,
) -> tuple[list, list]:
    unassigned: list = []
    prototypes: list = []
    if face_ids:
        stmt = text("""
          SELECT v.id, v.embedding, f.id face_id
          FROM photo_faces f INDEXED BY sqlite_autoindex_photo_faces_1
          CROSS JOIN photo_face_index_vectors v INDEXED BY ix_photo_face_index_vectors_face
            ON v.face_id=f.id AND v.active=1
          CROSS JOIN photo_items i ON i.id=f.photo_id
          WHERE f.id IN :ids AND v.active=1 AND v.owner_user_id=:owner AND v.pipeline_signature=:pipeline
            AND f.owner_user_id=:owner AND f.pipeline_signature=:pipeline AND f.person_id IS NULL
            AND f.assignment_source='automatic' AND i.owner_user_id=:owner
            AND i.deleted_at IS NULL AND i.missing_at IS NULL
        """).bindparams(bindparam("ids", expanding=True))
        ordered = sorted(face_ids)
        for start in range(0, len(ordered), SQL_ID_PAGE):
            _check(cancel)
            unassigned.extend(db.execute(stmt, {
                "ids": ordered[start:start + SQL_ID_PAGE], "owner": owner, "pipeline": pipeline,
            }))
    if person_ids:
        ordered = sorted(person_ids)
        for start in range(0, len(ordered), SQL_ID_PAGE):
            _check(cancel)
            prototypes.extend(db.execute(_PROTOTYPES, {
                "people": ordered[start:start + SQL_ID_PAGE], "owner": owner, "pipeline": pipeline,
            }))
    return prototypes, unassigned


def _sync(db: Session, scope: _Scope, cancel: threading.Event | None = None) -> None:
    while True:
        _check(cancel)
        changes = list(db.execute(text("""
          SELECT id,face_id,old_person_id,new_person_id,operation FROM photo_face_index_changes
          WHERE id>:after AND ((old_owner_user_id=:owner AND old_pipeline_signature=:pipeline)
            OR (new_owner_user_id=:owner AND new_pipeline_signature=:pipeline)) ORDER BY id LIMIT :limit
        """), {"after": scope.watermark, "owner": scope.owner_id, "pipeline": scope.pipeline, "limit": JOURNAL_PAGE}))
        if not changes:
            break
        face_ids = {row.face_id for row in changes}
        person_ids = {value for row in changes for value in (row.old_person_id, row.new_person_id) if value is not None}
        for face_id in face_ids:
            scope.unassigned_by_face.pop(str(face_id), None)
        for person_id in person_ids:
            scope.prototype_by_person.pop(str(person_id), None)
        prototypes, unassigned = _current_rows(
            db, scope.owner_id, scope.pipeline, face_ids, person_ids, cancel,
        )
        _add(scope.prototypes, scope.prototype_ids, [(int(row.id), bytes(row.embedding)) for row in prototypes])
        _add(scope.unassigned, scope.unassigned_ids, [(int(row.id), bytes(row.embedding)) for row in unassigned])
        _check(cancel)
        scope.prototype_by_person.update((str(row.person_id), int(row.id)) for row in prototypes)
        scope.unassigned_by_face.update((str(row.face_id), int(row.id)) for row in unassigned)
        scope.stale = ((int(scope.prototypes.ntotal) - len(scope.prototype_by_person))
                       + (int(scope.unassigned.ntotal) - len(scope.unassigned_by_face)))
        scope.changes_since_checkpoint += len(changes)
        scope.watermark = int(changes[-1].id)


def _should_rebuild(scope: _Scope) -> bool:
    total = int(scope.prototypes.ntotal + scope.unassigned.ntotal)
    if total and not scope.prototype_by_person and not scope.unassigned_by_face:
        return True
    # Inserts can replace a person's prototype on every photo. A fixed 4k
    # rebuild interval would therefore become quadratic during a large import;
    # compact at a measured fraction while search overfetches every tombstone.
    return scope.stale >= max(REBUILD_MIN_STALE, int(total * REBUILD_STALE_FRACTION))


def _exact(
    db: Session, owner: str, pipeline: str, vector_ids: list[int], query: np.ndarray,
    *, prototype: bool, limit: int, cancel: threading.Event | None = None,
) -> tuple[str, ...]:
    _check(cancel)
    if not vector_ids or limit <= 0:
        return ()
    eligibility = """AND f.person_id IS NOT NULL AND p.owner_user_id=:owner
      AND p.pipeline_signature=:pipeline AND p.redirected_to_id IS NULL""" \
        if prototype else "AND f.person_id IS NULL AND f.assignment_source='automatic'"
    people = "CROSS JOIN photo_people p ON p.id=f.person_id" if prototype else ""
    vector_stmt = text("""
      SELECT id,face_id,embedding FROM photo_face_index_vectors NOT INDEXED
      WHERE rowid IN :ids AND active=1 AND owner_user_id=:owner AND pipeline_signature=:pipeline
    """).bindparams(bindparam("ids", expanding=True))
    vector_rows = []
    for start in range(0, len(vector_ids), SQL_ID_PAGE):
        _check(cancel)
        vector_rows.extend(db.execute(vector_stmt, {
            "ids": vector_ids[start:start + SQL_ID_PAGE], "owner": owner, "pipeline": pipeline,
        }))
    face_stmt = text(f"""
      SELECT f.id face_id,f.person_id FROM photo_faces f INDEXED BY sqlite_autoindex_photo_faces_1
      CROSS JOIN photo_items i ON i.id=f.photo_id {people}
      WHERE f.id IN :ids AND f.owner_user_id=:owner AND f.pipeline_signature=:pipeline
        AND i.owner_user_id=:owner AND i.deleted_at IS NULL AND i.missing_at IS NULL {eligibility}
    """).bindparams(bindparam("ids", expanding=True))
    face_rows = []
    face_ids = sorted({str(row.face_id) for row in vector_rows})
    for start in range(0, len(face_ids), SQL_ID_PAGE):
        _check(cancel)
        face_rows.extend(db.execute(face_stmt, {
            "ids": face_ids[start:start + SQL_ID_PAGE], "owner": owner, "pipeline": pipeline,
        }))
    eligible_faces = {str(row.face_id) for row in face_rows}
    if prototype and face_rows:
        # Keep the latest-live lookup separate from the candidate point reads.
        # SQLite can turn the correlated form inside the outer query into a
        # prolific-person scan despite the explicit composite index.
        people_ids = sorted({str(row.person_id) for row in face_rows})
        latest: set[str] = set()
        for start in range(0, len(people_ids), SQL_ID_PAGE):
            _check(cancel)
            latest.update(str(row.face_id) for row in db.execute(_PROTOTYPES, {
                "people": people_ids[start:start + SQL_ID_PAGE], "owner": owner, "pipeline": pipeline,
            }))
        eligible_faces.intersection_update(latest)
    scored = []
    for row in vector_rows:
        if str(row.face_id) not in eligible_faces:
            continue
        value = np.frombuffer(row.embedding, dtype=np.float32)
        if value.shape != (DIMENSIONS,) or not np.all(np.isfinite(value)):
            continue
        norm = float(np.linalg.norm(value))
        if norm > 0:
            scored.append((float((value / norm) @ query), str(row.face_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(face_id for _score, face_id in scored[:limit])


def _search(
    db: Session, scope: _Scope, query: np.ndarray, index: faiss.IndexIDMap2, limit: int,
    *, prototype: bool, cancel: threading.Event | None = None,
) -> tuple[str, ...]:
    _check(cancel)
    if limit <= 0 or index.ntotal == 0:
        return ()
    live = len(scope.prototype_by_person) if prototype else len(scope.unassigned_by_face)
    stale = int(index.ntotal) - live
    count = min(int(index.ntotal), max(limit * 4, limit + stale))
    graph = faiss.downcast_index(index.index)
    previous_ef = graph.hnsw.efSearch
    graph.hnsw.efSearch = max(EF_SEARCH, count)
    try:
        _distances, ids = index.search(query.reshape(1, -1), count)
    finally:
        graph.hnsw.efSearch = previous_ef
    _check(cancel)
    candidates = [int(value) for value in ids[0] if value >= 0]
    return _exact(
        db, scope.owner_id, scope.pipeline, candidates, query,
        prototype=prototype, limit=limit, cancel=cancel,
    )


def _checkpoint(scope: _Scope, bind) -> bool:  # noqa: ANN001
    if scope.prototypes.ntotal + scope.unassigned.ntotal == 0:
        factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False, future=True)
        old_paths: list[str] = []
        with factory() as db, write_transaction(db, name="photo_face_index_checkpoint"):
            old = db.execute(text("""
              SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots
              WHERE owner_user_id=:o AND pipeline_signature=:p
            """), {"o": scope.owner_id, "p": scope.pipeline}).first()
            if old is None:
                return False
            old_paths.extend([old.prototype_path, old.unassigned_path])
            db.execute(text("""
              DELETE FROM photo_face_index_snapshots
              WHERE owner_user_id=:o AND pipeline_signature=:p
            """), {"o": scope.owner_id, "p": scope.pipeline})
        root = _root()
        for value in old_paths:
            with suppress(OSError):
                path = Path(value)
                if path.resolve().parent == root.resolve():
                    path.unlink(missing_ok=True)
        scope.changes_since_checkpoint = 0
        scope.checkpoint_watermark = scope.watermark
        scope.last_checkpoint_at = time.monotonic()
        _prune(bind, scope)
        return True
    generation = uuid.uuid4().hex
    prefix = f"{_scope_key(bind, scope.owner_id, scope.pipeline)}-{generation}"
    root = _root()
    proto_path = root / f"{prefix}-prototype.faiss"
    unassigned_path = root / f"{prefix}-unassigned.faiss"
    proto_payload = _snapshot_bytes(scope.prototypes)
    unassigned_payload = _snapshot_bytes(scope.unassigned)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        required = len(proto_payload) + len(unassigned_payload) + settings.photo_preview_min_free_bytes
        if shutil.disk_usage(root).free < required:
            return False
        proto_size, proto_hash = _atomic_write(proto_path, proto_payload)
        unassigned_size, unassigned_hash = _atomic_write(unassigned_path, unassigned_payload)
    except OSError:
        with suppress(OSError):
            proto_path.unlink(missing_ok=True)
        with suppress(OSError):
            unassigned_path.unlink(missing_ok=True)
        return False
    factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False, future=True)
    old_paths: list[str] = []
    with factory() as db, write_transaction(db, name="photo_face_index_checkpoint"):
        old = db.execute(text("""
          SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots
          WHERE owner_user_id=:o AND pipeline_signature=:p
        """), {"o": scope.owner_id, "p": scope.pipeline}).first()
        if old:
            old_paths.extend([old.prototype_path, old.unassigned_path])
        db.execute(text("""
          INSERT INTO photo_face_index_snapshots
          (owner_user_id,pipeline_signature,generation,watermark,runtime_version,dimensions,metric,hnsw_m,
           ef_construction,ef_search,prototype_path,prototype_size,prototype_sha256,
           unassigned_path,unassigned_size,unassigned_sha256,created_at)
          VALUES (:o,:p,:g,:w,:rv,:d,:metric,:m,:efc,:efs,:pp,:ps,:ph,:up,:us,:uh,:at)
          ON CONFLICT(owner_user_id,pipeline_signature) DO UPDATE SET generation=excluded.generation,
           watermark=excluded.watermark,runtime_version=excluded.runtime_version,dimensions=excluded.dimensions,
           metric=excluded.metric,hnsw_m=excluded.hnsw_m,ef_construction=excluded.ef_construction,
           ef_search=excluded.ef_search,prototype_path=excluded.prototype_path,prototype_size=excluded.prototype_size,
           prototype_sha256=excluded.prototype_sha256,unassigned_path=excluded.unassigned_path,
           unassigned_size=excluded.unassigned_size,unassigned_sha256=excluded.unassigned_sha256,created_at=excluded.created_at
        """), {"o": scope.owner_id, "p": scope.pipeline, "g": generation, "w": scope.watermark,
                "rv": faiss.__version__, "d": DIMENSIONS, "metric": "inner_product", "m": M,
                "efc": EF_CONSTRUCTION, "efs": EF_SEARCH,
                "pp": str(proto_path), "ps": proto_size, "ph": proto_hash, "up": str(unassigned_path),
                "us": unassigned_size, "uh": unassigned_hash, "at": datetime.now(UTC).replace(tzinfo=None)})
    for value in old_paths:
        try:
            path = Path(value)
            if path.resolve().parent == root.resolve():
                path.unlink(missing_ok=True)
        except OSError:
            pass
    scope.changes_since_checkpoint = 0
    scope.checkpoint_watermark = scope.watermark
    scope.last_checkpoint_at = time.monotonic()
    _prune(bind, scope)
    return True


def _prune(bind, scope: _Scope) -> None:  # noqa: ANN001
    """Prune one bounded page covered by this and any cross-scope snapshot."""
    factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False, future=True)
    values = {"owner": scope.owner_id, "pipeline": scope.pipeline,
              "watermark": scope.checkpoint_watermark, "limit": JOURNAL_PAGE}
    with factory() as db, write_transaction(db, name="photo_face_index_prune"):
        db.execute(text("""
          DELETE FROM photo_face_index_changes WHERE id IN (
            SELECT c.id FROM photo_face_index_changes c
            WHERE c.id<=:watermark AND (
              (c.old_owner_user_id=:owner AND c.old_pipeline_signature=:pipeline)
              OR (c.new_owner_user_id=:owner AND c.new_pipeline_signature=:pipeline)
            )
            AND (c.old_owner_user_id IS NULL
              OR (c.old_owner_user_id=:owner AND c.old_pipeline_signature=:pipeline)
              OR EXISTS (SELECT 1 FROM photo_face_index_snapshots s
                WHERE s.owner_user_id=c.old_owner_user_id AND s.pipeline_signature=c.old_pipeline_signature
                  AND s.watermark>=c.id))
            AND (c.new_owner_user_id IS NULL
              OR (c.new_owner_user_id=:owner AND c.new_pipeline_signature=:pipeline)
              OR EXISTS (SELECT 1 FROM photo_face_index_snapshots s
                WHERE s.owner_user_id=c.new_owner_user_id AND s.pipeline_signature=c.new_pipeline_signature
                  AND s.watermark>=c.id))
            ORDER BY c.id LIMIT :limit
          )
        """), values)
    with factory() as db, write_transaction(db, name="photo_face_index_prune"):
        db.execute(text("""
          DELETE FROM photo_face_index_vectors WHERE id IN (
            SELECT id FROM photo_face_index_vectors
            WHERE active=0 AND owner_user_id=:owner AND pipeline_signature=:pipeline
            ORDER BY id LIMIT :limit
          )
        """), values)


def _cleanup_orphans(db: Session, bind) -> None:  # noqa: ANN001
    if bind in _cleaned_binds:
        return
    root = _root()
    if not root.exists():
        _cleaned_binds.add(bind)
        return
    retained = {Path(value).resolve() for row in db.execute(text(
        "SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots"
    )) for value in row}
    for path in root.iterdir():
        try:
            if path.is_file() and (path.name.endswith(".tmp") or path.resolve() not in retained):
                path.unlink()
        except OSError:
            continue
    _cleaned_binds.add(bind)


def prepare_candidates(
    db: Session, owner_id: str, pipeline_signature: str, embeddings: Sequence[bytes | np.ndarray],
    *, prototype_limit: int = 64, unassigned_limit: int = 64, cancel: threading.Event | None = None,
) -> tuple[CandidateIds, ...]:
    """Return bounded ANN candidates; caller performs its clustering checks."""
    values = _queries(embeddings)
    _check(cancel)
    if not len(values):
        return ()
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("prepare_candidates requires a clean session")
    bind = db.get_bind()
    key = (bind, owner_id, pipeline_signature)
    with _lock:
        try:
            # OpenMP controls are thread-local in FAISS; set the service budget
            # on the actual worker thread rather than at module import.
            faiss.omp_set_num_threads(1)
            persistent = bind.url.database not in (None, "", ":memory:")
            if persistent:
                _cleanup_orphans(db, bind)
            scope = _scopes.get(key)
            checkpoint_needed = scope is None
            if scope is None:
                scope = (_load_snapshot(db, owner_id, pipeline_signature, cancel)
                         or _build(db, owner_id, pipeline_signature, cancel))
                _scopes[key] = scope
            _scopes.move_to_end(key)
            _sync(db, scope, cancel)
            if _should_rebuild(scope):
                scope = _build(db, owner_id, pipeline_signature, cancel)
                _scopes[key] = scope
                checkpoint_needed = True
            answer = tuple(CandidateIds(
                _search(db, scope, query, scope.prototypes, prototype_limit, prototype=True, cancel=cancel),
                _search(db, scope, query, scope.unassigned, unassigned_limit, prototype=False, cancel=cancel),
            ) for query in values)
            _check(cancel)
            db.rollback()  # close the read transaction; there were no caller writes
            checkpoint_due = (scope.changes_since_checkpoint >= CHECKPOINT_CHANGES
                              and time.monotonic() - scope.last_checkpoint_at >= 60)
            if persistent and (checkpoint_needed or checkpoint_due):
                _checkpoint(scope, bind)
            _trim_scopes(key)
            return answer
        except IndexInterrupted:
            # A replay may have changed membership/native graph before the
            # cancellation check. Drop it; the next call reloads a validated
            # checkpoint and replays the still-durable journal.
            _scopes.pop(key, None)
            db.rollback()
            raise


def settle_pending(db: Session, cancel: threading.Event | None = None) -> int:
    """Replay and checkpoint dirty resident scopes when the face queue is idle."""
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("settle_pending requires a clean session")
    bind = db.get_bind()
    persistent = bind.url.database not in (None, "", ":memory:")
    settled: list[_Scope] = []
    with _lock:
        try:
            _check(cancel)
            faiss.omp_set_num_threads(1)
            for key, scope in list(_scopes.items()):
                if key[0] is not bind:
                    continue
                _sync(db, scope, cancel)
                needs_checkpoint = bool(scope.changes_since_checkpoint)
                if _should_rebuild(scope):
                    scope = _build(db, scope.owner_id, scope.pipeline, cancel)
                    _scopes[key] = scope
                    needs_checkpoint = True
                if needs_checkpoint:
                    settled.append(scope)
            _check(cancel)
            db.rollback()
            if persistent:
                checkpointed = sum(_checkpoint(scope, bind) for scope in settled)
                settled_ids = {id(scope) for scope in settled}
                for scope in _scopes.values():
                    if id(scope) not in settled_ids and scope.checkpoint_watermark:
                        _prune(bind, scope)
                return checkpointed
        except IndexInterrupted:
            for key in [key for key in _scopes if key[0] is bind]:
                del _scopes[key]
            db.rollback()
            raise
    return len(settled)


def clear_owner(owner_id: str) -> None:
    """Drop one owner's biometric accelerators from this process."""
    with _lock:
        for key in [key for key in _scopes if key[1] == owner_id]:
            del _scopes[key]


@contextmanager
def serialized_index_mutation():  # noqa: ANN201
    """Hold the ANN lock before a writer mutates/erases authoritative faces.

    Callers must acquire this outer context before ``write_transaction``. The
    reverse order could deadlock a checkpoint's short manifest writer.
    """
    with _lock:
        yield


def delete_owner_data(db: Session, owner_id: str) -> None:
    """Stage physical derived-index deletion; RAM/files clear after commit."""
    paths = [value for row in db.execute(text("""
      SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots WHERE owner_user_id=:owner
    """), {"owner": owner_id}) for value in row]
    db.execute(text("DELETE FROM photo_face_index_snapshots WHERE owner_user_id=:owner"), {"owner": owner_id})
    db.execute(text("DELETE FROM photo_face_index_vectors WHERE owner_user_id=:owner"), {"owner": owner_id})
    db.execute(text("DELETE FROM photo_face_index_changes WHERE old_owner_user_id=:owner OR new_owner_user_id=:owner"), {"owner": owner_id})

    def finish() -> None:
        clear_owner(owner_id)
        for value in paths:
            try:
                path = Path(value)
                if path.resolve().parent == _root().resolve():
                    path.unlink(missing_ok=True)
            except OSError:
                pass
    queue_after_commit(db, finish)


def reset_for_tests() -> None:
    with _lock:
        _scopes.clear()
        _cleaned_binds.clear()
