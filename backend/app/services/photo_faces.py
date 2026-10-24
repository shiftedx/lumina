"""Local, owner-consented face detection, embeddings and conservative grouping."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import resource
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import User, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.photo_face_models import PhotoFace, PhotoFaceEdit, PhotoFaceJob, PhotoFacePreference, PhotoPerson
from app.photo_models import PhotoDerivative, PhotoItem
from app.services import model_catalog, photo_cache, photo_derivatives, photo_face_index, photo_files
from app.services.background_work import BackgroundWorkClosed, background_work
from app.services.raster_images import image_size

logger = logging.getLogger(__name__)

YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
PIPELINE_SIGNATURE = f"yunet-2023mar:{YUNET_SHA256[:12]}+sface-2021dec:{SFACE_SHA256[:12]}+align-v1"
DETECTION_SCORE = 0.70
MAX_FACES = 100
EMBEDDING_DIMENSIONS = 128
EMBEDDING_BYTES = EMBEDDING_DIMENSIONS * 4
AUTO_COSINE = 0.55
AUTO_MARGIN = 0.05
AUTO_SEED_FACES = 3
MAX_ATTEMPTS = 3
QUEUE_BATCH = 500
CHILD_TIMEOUT_SECONDS = 45
CHILD_OUTPUT_BYTES = 256 * 1024
CHILD_IDLE_SECONDS = 300
CHILD_REQUEST_VERSION = 1
CHILD_REQUEST_BYTES = photo_derivatives.MAX_OUTPUT_BYTES + 1
CHILD_CPU_SECONDS = 30
CHILD_IO_JOIN_SECONDS = 1.0
CRASH_WINDOW_SECONDS = 600
CRASH_BACKOFF_SECONDS = 1.0
MAX_RECENT_CRASHES = 2
UNIT_NORM_TOLERANCE = 1e-3
FRAME_HEADER = struct.Struct("!I")


class FaceError(ValueError):
    code = "analysis_failed"


class ModelUnavailable(FaceError):
    code = "model_unavailable"


class InvalidImage(FaceError):
    code = "invalid_image"


class InvalidModelOutput(FaceError):
    code = "invalid_model_output"


class AnalysisFailed(FaceError):
    code = "analysis_failed"


class AnalysisTimeout(FaceError):
    code = "analysis_timeout"


class AnalysisInterrupted(FaceError):
    code = "interrupted"


class AnalysisCircuitOpen(AnalysisFailed):
    """Repeated child faults stopped the runtime until an explicit Retry or artifact replacement."""


@dataclass(frozen=True)
class DetectedFace:
    score: float
    x1: float
    y1: float
    x2: float
    y2: float
    embedding: bytes


def face_model():
    return model_catalog.catalog().default_for("face")


def model_ready() -> bool:
    try:
        return model_catalog.is_installed(face_model())
    except StopIteration:
        return False


def _model_generation() -> tuple[object, ...]:
    """Cheap artifact identity; a replacement clears a process-local failure circuit without rehashing here."""
    model = face_model()
    root = Path(__file__).resolve().parents[1] / "data" / "hero_models"
    paths = (
        root / "yunet.onnx",
        model_catalog.model_dir(model) / "sface.onnx",
        model_catalog.model_dir(model) / model_catalog.READY_MARKER,
    )
    identity: list[object] = [model.id]
    for path in paths:
        try:
            found = path.stat()
            identity.append((found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns))
        except OSError:
            identity.append(None)
    return tuple(identity)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_embedding(value: object) -> bytes:
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise InvalidModelOutput("embedding") from exc
    if vector.shape != (EMBEDDING_DIMENSIONS,) or not np.isfinite(vector).all():
        raise InvalidModelOutput("embedding")
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-6:
        raise InvalidModelOutput("embedding")
    normalized = np.asarray(vector / norm, dtype="<f4")
    encoded = normalized.tobytes()
    if len(encoded) != EMBEDDING_BYTES:
        raise InvalidModelOutput("embedding")
    return encoded


def vector(value: bytes) -> np.ndarray:
    if not isinstance(value, bytes) or len(value) != EMBEDDING_BYTES:
        raise InvalidModelOutput("stored_embedding")
    found = np.frombuffer(value, dtype="<f4")
    if found.shape != (EMBEDDING_DIMENSIONS,) or not np.isfinite(found).all():
        raise InvalidModelOutput("stored_embedding")
    return found


class OpenCvFaceAnalyzer:
    """The production CPU implementation. It accepts only Lumina-produced WebP previews."""

    def __init__(self) -> None:
        import cv2

        root = Path(__file__).resolve().parents[1] / "data" / "hero_models"
        yunet = root / "yunet.onnx"
        model = face_model()
        sface = model_catalog.model_dir(model) / "sface.onnx"
        if not model_catalog.is_installed(model):
            raise ModelUnavailable("face model is not installed")
        if _sha256(yunet) != YUNET_SHA256 or _sha256(sface) != SFACE_SHA256:
            raise ModelUnavailable("face model verification failed")
        cv2.setNumThreads(1)
        cv2.ocl.setUseOpenCL(False)
        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(
            str(yunet), "", (320, 320), DETECTION_SCORE, 0.3, MAX_FACES * 4,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(str(sface), "")

    def analyze(self, data: bytes) -> list[DetectedFace]:
        cv2 = self.cv2
        size = image_size(data)
        if (not data or len(data) > photo_derivatives.MAX_OUTPUT_BYTES or size is None
                or min(size) < 32 or size[0] > 1200 or size[1] > 12_000
                or size[0] * size[1] > 14_400_000):
            raise InvalidImage("preview")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise InvalidImage("preview")
        height, width = image.shape[:2]
        if width < 32 or height < 32 or width > 1200 or height > 12_000 or width * height > 14_400_000:
            raise InvalidImage("dimensions")
        if max(width, height) > 1200:
            scale = 1200 / max(width, height)
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
            height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _result, raw_faces = self.detector.detect(image)
        if raw_faces is None:
            return []
        if not isinstance(raw_faces, np.ndarray) or raw_faces.ndim != 2 or raw_faces.shape[1] < 15:
            raise InvalidModelOutput("detections")
        accepted: list[DetectedFace] = []
        rows = sorted(raw_faces.tolist(), key=lambda row: (-float(row[14]), float(row[1]), float(row[0])))
        for raw in rows[:MAX_FACES]:
            values = np.asarray(raw[:15], dtype=np.float64)
            if not np.isfinite(values).all():
                raise InvalidModelOutput("detections")
            x, y, box_width, box_height, score = float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]), float(raw[14])
            x1, y1 = max(0.0, x), max(0.0, y)
            x2, y2 = min(float(width), x + box_width), min(float(height), y + box_height)
            if score < DETECTION_SCORE or x2 - x1 < 10 or y2 - y1 < 10:
                continue
            try:
                crop = self.recognizer.alignCrop(image, np.asarray(raw, dtype=np.float32))
                embedding = validate_embedding(self.recognizer.feature(crop))
            except InvalidModelOutput:
                raise
            except Exception as exc:  # native API errors are fixed public failures
                raise InvalidModelOutput("recognition") from exc
            accepted.append(DetectedFace(score, x1 / width, y1 / height, x2 / width, y2 / height, embedding))
        return accepted


def preference(db: Session, user_id: str) -> PhotoFacePreference:
    row = db.get(PhotoFacePreference, user_id)
    if row is None:
        row = PhotoFacePreference(user_id=user_id, enabled=False, auto_group=True)
        db.add(row)
        db.flush()
    return row


def set_preference(db: Session, user: User, *, enabled: bool, auto_group: bool) -> PhotoFacePreference:
    with write_transaction(db, name="photo_face_preference"):
        row = preference(db, user.id)
        row.enabled, row.auto_group = enabled, auto_group
        if enabled and row.consented_at is None:
            row.consented_at = utcnow()
    if enabled:
        queue_all(db, user, requested=True)
    worker.wake()
    return row


def _enabled(db: Session, owner_id: str) -> bool:
    row = db.get(PhotoFacePreference, owner_id)
    return bool(row and row.enabled)


def queue_photo(db: Session, photo: PhotoItem, *, requested: bool) -> PhotoFaceJob:
    if not _enabled(db, photo.owner_user_id):
        raise FaceError("face analysis is not enabled")
    if requested:
        _face_process.retry()
    row = db.scalar(select(PhotoFaceJob).where(
        PhotoFaceJob.photo_id == photo.id, PhotoFaceJob.pipeline_signature == PIPELINE_SIGNATURE,
    ))
    now = utcnow()
    with write_transaction(db, name="photo_face_queue"):
        if row is None:
            row = PhotoFaceJob(
                id=str(uuid.uuid4()), photo_id=photo.id, owner_user_id=photo.owner_user_id,
                pipeline_signature=PIPELINE_SIGNATURE, source_sha256=photo.sha256,
                state="queued", requested=requested,
            )
            db.add(row)
        elif row.source_sha256 != photo.sha256:
            db.query(PhotoFace).filter(PhotoFace.photo_id == photo.id).delete(synchronize_session=False)
            row.source_sha256, row.state, row.error_code = photo.sha256, "queued", None
            row.requested, row.attempts = requested, 0
            row.started_at, row.finished_at, row.updated_at = None, None, now
        elif row.state != "succeeded":
            if row.state in {"failed", "canceled"}:
                row.attempts = 0
            row.state, row.error_code = "queued", None
            row.requested = row.requested or requested
            row.updated_at, row.started_at, row.finished_at = now, None, None
    worker.wake()
    return row


def queue_all(db: Session, user: User, *, requested: bool) -> int:
    """Keyset through ids in bounded transactions; never retain a whole library."""
    if requested:
        _face_process.retry()
    queued, cursor = 0, ""
    while True:
        photos = list(db.execute(select(PhotoItem.id, PhotoItem.sha256).where(
            PhotoItem.owner_user_id == user.id, PhotoItem.deleted_at.is_(None), PhotoItem.id > cursor,
        ).order_by(PhotoItem.id).limit(QUEUE_BATCH)))
        if not photos:
            break
        photo_ids = [photo_id for photo_id, _source_sha256 in photos]
        existing = {row.photo_id: row for row in db.scalars(select(PhotoFaceJob).where(
            PhotoFaceJob.photo_id.in_(photo_ids), PhotoFaceJob.pipeline_signature == PIPELINE_SIGNATURE,
        ))}
        with write_transaction(db, name="photo_face_queue_batch"):
            for photo_id, source_sha256 in photos:
                row = existing.get(photo_id)
                if row is None:
                    db.add(PhotoFaceJob(
                        id=str(uuid.uuid4()), photo_id=photo_id, owner_user_id=user.id,
                        pipeline_signature=PIPELINE_SIGNATURE, source_sha256=source_sha256,
                        state="queued", requested=requested,
                    ))
                    queued += 1
                elif row.source_sha256 != source_sha256:
                    db.query(PhotoFace).filter(PhotoFace.photo_id == photo_id).delete(synchronize_session=False)
                    row.source_sha256, row.state, row.error_code = source_sha256, "queued", None
                    row.requested, row.attempts = requested, 0
                    row.started_at, row.finished_at, row.updated_at = None, None, utcnow()
                    queued += 1
                elif row.state in {"failed", "canceled"}:
                    row.state, row.error_code, row.requested = "queued", None, row.requested or requested
                    row.attempts, row.started_at, row.finished_at, row.updated_at = 0, None, None, utcnow()
                    queued += 1
                elif row.state != "succeeded" and requested and not row.requested:
                    row.requested, row.updated_at = True, utcnow()
                    queued += 1
        cursor = photo_ids[-1]
    worker.wake()
    return queued


def recover(db: Session) -> int:
    rows = list(db.scalars(select(PhotoFaceJob).where(PhotoFaceJob.state == "running")))
    with write_transaction(db, name="photo_face_recover"):
        for row in rows:
            row.state = "queued" if row.attempts < MAX_ATTEMPTS else "failed"
            row.error_code = "interrupted"
            row.updated_at, row.finished_at = utcnow(), utcnow() if row.state == "failed" else None
    return len(rows)


def _iou(face: PhotoFace, found: DetectedFace) -> float:
    left, top, right, bottom = max(face.x1, found.x1), max(face.y1, found.y1), min(face.x2, found.x2), min(face.y2, found.y2)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = (face.x2 - face.x1) * (face.y2 - face.y1) + (found.x2 - found.x1) * (found.y2 - found.y1) - intersection
    return intersection / union if union > 0 else 0.0


def _record(db: Session, owner_id: str, operation: str, detail: dict) -> None:
    db.add(PhotoFaceEdit(
        id=str(uuid.uuid4()), owner_user_id=owner_id, operation=operation,
        detail_json=json.dumps(detail, sort_keys=True, separators=(",", ":")),
    ))


def _cluster_new_faces(
    db: Session, owner_id: str, new_faces: list[PhotoFace],
    candidate_ids: tuple[photo_face_index.CandidateIds, ...],
) -> None:
    """Recheck ANN suggestions against authoritative live rows inside the short write."""
    pref = db.get(PhotoFacePreference, owner_id)
    if not pref or not pref.auto_group:
        return
    ids = {key for result in candidate_ids
           for key in (*result.prototype_face_ids, *result.unassigned_face_ids)}
    candidates = []
    ordered_ids = sorted(ids)
    live_candidates = select(PhotoFace).from_statement(text("""
        SELECT f.* FROM photo_faces f INDEXED BY sqlite_autoindex_photo_faces_1
        CROSS JOIN photo_items i ON i.id=f.photo_id
        WHERE f.id IN :ids AND f.owner_user_id=:owner AND f.pipeline_signature=:pipeline
          AND i.owner_user_id=:owner AND i.deleted_at IS NULL AND i.missing_at IS NULL
    """).bindparams(bindparam("ids", expanding=True))).execution_options(populate_existing=True)
    for start in range(0, len(ordered_ids), 400):
        candidates.extend(db.scalars(live_candidates, {
            "ids": ordered_ids[start:start + 400], "owner": owner_id, "pipeline": PIPELINE_SIGNATURE,
        }))
    matrix = np.stack([vector(row.embedding) for row in candidates]) if candidates else np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
    for face in new_faces:
        if face.assignment_source != "automatic" or face.person_id is not None:
            continue
        scores = matrix @ vector(face.embedding) if candidates else np.empty(0, dtype=np.float32)
        scored = [(float(score), other) for score, other in zip(scores, candidates)
                  if other.id != face.id and other.photo_id != face.photo_id]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        people: dict[str, float] = {}
        for score, other in scored:
            if other.person_id is not None:
                people[other.person_id] = max(score, people.get(other.person_id, -1.0))
        ranked = sorted(((score, person_id) for person_id, score in people.items()), reverse=True)
        if ranked and ranked[0][0] >= AUTO_COSINE and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= AUTO_MARGIN):
            face.person_id = ranked[0][1]
            continue
        seed = [other for score, other in scored if score >= AUTO_COSINE and other.person_id is None and other.assignment_source == "automatic"]
        distinct: list[PhotoFace] = []
        seen_photos = {face.photo_id}
        for candidate in seed:
            if candidate.photo_id not in seen_photos:
                distinct.append(candidate)
                seen_photos.add(candidate.photo_id)
            if len(distinct) + 1 >= AUTO_SEED_FACES:
                break
        if len(distinct) + 1 >= AUTO_SEED_FACES:
            person = PhotoPerson(
                id=str(uuid.uuid4()), owner_user_id=owner_id, pipeline_signature=PIPELINE_SIGNATURE,
                feature_face_id=face.id,
            )
            db.add(person)
            db.flush()
            for member in [face, *distinct]:
                member.person_id = person.id


def commit_analysis(db: Session, job_id: str, detections: list[DetectedFace]) -> None:
    job = db.get(PhotoFaceJob, job_id)
    if job is None or job.state != "running" or worker._stop.is_set():  # noqa: SLF001
        return
    for found in detections:
        if not (math.isfinite(found.score) and DETECTION_SCORE <= found.score <= 1.0):
            raise InvalidModelOutput("score")
        if not (0 <= found.x1 < found.x2 <= 1 and 0 <= found.y1 < found.y2 <= 1):
            raise InvalidModelOutput("box")
        vector(found.embedding)
    pref = db.get(PhotoFacePreference, job.owner_user_id)
    candidate_ids = photo_face_index.prepare_candidates(
        db, job.owner_user_id, PIPELINE_SIGNATURE, [found.embedding for found in detections], cancel=worker._stop,  # noqa: SLF001
    ) if detections and pref is not None and pref.enabled and pref.auto_group else ()
    with write_transaction(db, name="photo_face_commit"):
        job = db.get(PhotoFaceJob, job_id, populate_existing=True)
        if job is None or job.state != "running" or worker._stop.is_set():  # noqa: SLF001
            return
        photo = db.scalar(select(PhotoItem).where(PhotoItem.id == job.photo_id).execution_options(populate_existing=True))
        if photo is None or photo.deleted_at is not None or photo.missing_at is not None or photo.owner_user_id != job.owner_user_id:
            job.state, job.error_code, job.finished_at, job.updated_at = "failed", "photo_unavailable", utcnow(), utcnow()
            return
        if photo.sha256 != job.source_sha256:
            db.query(PhotoFace).filter(PhotoFace.photo_id == photo.id).delete(synchronize_session=False)
            job.source_sha256, job.state, job.error_code = photo.sha256, "queued", "source_changed"
            job.attempts, job.started_at, job.finished_at, job.updated_at = 0, None, None, utcnow()
            queue_after_commit(db, worker.wake)
            return
        pref = db.get(PhotoFacePreference, photo.owner_user_id, populate_existing=True)
        if pref is None or not pref.enabled:
            job.state, job.error_code, job.finished_at, job.updated_at = "canceled", "disabled", utcnow(), utcnow()
            return
        old = list(db.scalars(select(PhotoFace).where(PhotoFace.photo_id == photo.id)))
        manual = [face for face in old if face.assignment_source == "manual"]
        new_rows: list[PhotoFace] = []
        for face in old:
            db.delete(face)
        for found in detections:
            kept = max(manual, key=lambda face: _iou(face, found), default=None)
            preserve = kept is not None and _iou(kept, found) >= 0.5
            row = PhotoFace(
                id=kept.id if preserve else str(uuid.uuid4()), photo_id=photo.id, owner_user_id=photo.owner_user_id,
                person_id=kept.person_id if preserve else None, pipeline_signature=PIPELINE_SIGNATURE,
                detector_score=found.score, x1=found.x1, y1=found.y1, x2=found.x2, y2=found.y2,
                embedding=found.embedding, assignment_source="manual" if preserve else "automatic",
            )
            db.add(row)
            new_rows.append(row)
            if preserve:
                manual.remove(kept)
        db.flush()
        _cluster_new_faces(db, photo.owner_user_id, new_rows, candidate_ids)
        job.state, job.error_code, job.finished_at, job.updated_at = "succeeded", None, utcnow(), utcnow()


def _fail(db: Session, job_id: str, code: str, *, terminal: bool = False) -> None:
    job = db.get(PhotoFaceJob, job_id)
    if job is None:
        return
    with write_transaction(db, name="photo_face_fail"):
        job.error_code = code[:64]
        job.updated_at = utcnow()
        job.state = "failed" if terminal or job.attempts >= MAX_ATTEMPTS else "queued"
        job.finished_at = utcnow() if job.state == "failed" else None


def _defer(db: Session, job_id: str, code: str) -> None:
    job = db.get(PhotoFaceJob, job_id)
    if job is None:
        return
    with write_transaction(db, name="photo_face_defer"):
        job.state, job.error_code, job.started_at, job.finished_at, job.updated_at = (
            "queued", code[:64], None, None, utcnow(),
        )
        job.attempts = max(0, job.attempts - 1)


def _preview(db: Session, photo_id: str, source_sha256: str) -> bytes:
    expected_key = photo_derivatives.source_key(source_sha256, 1200)
    row = db.scalar(select(PhotoDerivative).where(
        PhotoDerivative.photo_id == photo_id, PhotoDerivative.width == 1200, PhotoDerivative.state == "ready",
        PhotoDerivative.source_key == expected_key,
    ))
    if row is not None:
        try:
            data = photo_derivatives.read(row.source_key)
            photo_cache.touch(row.source_key)
            return data
        except OSError:
            pass
    photo = db.get(PhotoItem, photo_id)
    if photo is None or photo.deleted_at is not None or photo.missing_at is not None or photo.sha256 != source_sha256:
        raise InvalidImage("preview_unavailable")
    root, relative = photo_files.photo_location(db, photo)
    db.rollback()  # end the read transaction before slow I/O and the later BEGIN IMMEDIATE
    products, fingerprint = photo_files.build_derivatives(root, relative, source_sha256)
    try:
        with write_transaction(db, name="photo_face_preview_repair"):
            photo = db.get(PhotoItem, photo_id)
            if photo is None:
                raise photo_files.PhotoFileChanged("Photo content changed.")
            if photo.deleted_at is not None or photo.missing_at is not None or photo.sha256 != fingerprint.sha256:
                raise photo_files.PhotoFileChanged("Photo content changed.")
            rows = list(db.scalars(select(PhotoDerivative).where(PhotoDerivative.photo_id == photo.id)))
            for derivative in rows:
                product = products.get(derivative.width)
                if product is not None:
                    derivative.source_key, derivative.bytes, derivative.state, derivative.error = product[0], product[1], "ready", None
    finally:
        photo_cache.release_publication({key for key, _size in products.values()})
    data = photo_derivatives.read(expected_key)
    photo_cache.touch(expected_key)
    return data


class _ChildExited(RuntimeError):
    pass


class _ChildProtocolError(RuntimeError):
    pass


class _ChildRecycle(RuntimeError):
    pass


def _read_exact(stream, size: int) -> bytes:  # noqa: ANN001
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream, limit: int) -> bytes | None:  # noqa: ANN001
    header = _read_exact(stream, FRAME_HEADER.size)
    if not header:
        return None
    if len(header) != FRAME_HEADER.size:
        raise _ChildProtocolError("truncated frame header")
    (size,) = FRAME_HEADER.unpack(header)
    if size <= 0 or size > limit:
        raise _ChildProtocolError("invalid frame size")
    payload = _read_exact(stream, size)
    if len(payload) != size:
        raise _ChildProtocolError("truncated frame")
    return payload


def _write_frame(stream, payload: bytes, limit: int) -> None:  # noqa: ANN001
    if not payload or len(payload) > limit:
        raise _ChildProtocolError("invalid frame size")
    stream.write(FRAME_HEADER.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def _child_payload(error: str | None = None, rows: list[DetectedFace] | None = None) -> bytes:
    value: dict[str, object]
    if error is not None:
        value = {"error": error}
    else:
        value = {"ok": [{
            "score": row.score, "x1": row.x1, "y1": row.y1, "x2": row.x2, "y2": row.y2,
            "embedding": row.embedding.hex(),
        } for row in rows or []]}
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise InvalidModelOutput("child_output") from exc
    if len(encoded) > CHILD_OUTPUT_BYTES:
        raise InvalidModelOutput("child_output")
    return encoded


def _decode_child_payload(raw: bytes) -> list[DetectedFace]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ChildProtocolError("invalid child json") from exc
    if not isinstance(payload, dict) or len(payload) != 1:
        raise _ChildProtocolError("invalid child envelope")
    if "error" in payload:
        code = payload["error"]
        if code == "restart_required":
            raise _ChildRecycle("restart_required")
        errors: dict[str, type[FaceError]] = {
            "model_unavailable": ModelUnavailable,
            "invalid_image": InvalidImage,
            "invalid_model_output": InvalidModelOutput,
            "analysis_failed": AnalysisFailed,
        }
        if not isinstance(code, str) or code not in errors:
            raise _ChildProtocolError("invalid child error")
        raise errors[code](code)
    rows = payload.get("ok")
    if not isinstance(rows, list) or len(rows) > MAX_FACES:
        raise _ChildProtocolError("invalid child detections")
    found: list[DetectedFace] = []
    expected = {"score", "x1", "y1", "x2", "y2", "embedding"}
    try:
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected:
                raise _ChildProtocolError("invalid child detection")
            raw_numbers = (row["score"], row["x1"], row["y1"], row["x2"], row["y2"])
            if any(type(value) not in (int, float) for value in raw_numbers):
                raise _ChildProtocolError("invalid child detection")
            score, x1, y1, x2, y2 = (float(value) for value in raw_numbers)
            encoded = row["embedding"]
            if not isinstance(encoded, str):
                raise _ChildProtocolError("invalid child embedding")
            embedding = bytes.fromhex(encoded)
            if (not all(math.isfinite(value) for value in (score, x1, y1, x2, y2))
                    or not DETECTION_SCORE <= score <= 1.0
                    or not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1)):
                raise _ChildProtocolError("invalid child detection")
            values = vector(embedding)
            norm = float(np.linalg.norm(values))
            if not math.isfinite(norm) or norm < 1e-6 or abs(norm - 1.0) > UNIT_NORM_TOLERANCE:
                raise _ChildProtocolError("invalid child embedding")
            embedding = np.asarray(values / norm, dtype="<f4").tobytes()
            found.append(DetectedFace(score, x1, y1, x2, y2, embedding))
    except (KeyError, OverflowError, TypeError, ValueError, InvalidModelOutput) as exc:
        raise _ChildProtocolError("invalid child detection") from exc
    return found


class _FaceProcess:
    """One serial, resident native analyzer process; every request remains bounded and supervised."""

    def __init__(self, *, clock=time.monotonic) -> None:  # noqa: ANN001
        self._clock = clock
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._io_threads: tuple[threading.Thread, ...] = ()
        self._last_used = 0.0
        self._generation: tuple[object, ...] | None = None
        self._crashes: list[float] = []
        self._retry_after = 0.0
        self._failed = False
        self._retry_requested = threading.Event()
        self.starts = 0

    def _sync_generation(self, generation: tuple[object, ...]) -> None:
        if generation == self._generation:
            return
        self._discard()
        self._generation = generation
        self._crashes = []
        self._retry_after = 0.0
        self._failed = False

    def retry(self, generation: tuple[object, ...] | None = None) -> None:
        """Explicit Retry clears crash history; artifact replacement also starts a fresh generation."""
        del generation  # generation identity is synchronized by the next claim without blocking this caller
        self._retry_requested.set()

    def _apply_retry(self) -> None:
        if self._retry_requested.is_set():
            self._retry_requested.clear()
            self._crashes = []
            self._retry_after = 0.0
            self._failed = False

    def available(self, generation: tuple[object, ...]) -> bool:
        with self._lock:
            self._sync_generation(generation)
            self._apply_retry()
            return not self._failed and self._clock() >= self._retry_after

    def _record_crash(self) -> None:
        now = self._clock()
        self._crashes = [found for found in self._crashes if now - found < CRASH_WINDOW_SECONDS]
        self._crashes.append(now)
        self._retry_after = now + CRASH_BACKOFF_SECONDS
        if len(self._crashes) >= MAX_RECENT_CRASHES:
            self._failed = True

    def _start(self) -> subprocess.Popen:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        if process is not None:
            self._discard(process)
        env = os.environ.copy()
        env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "OPENCV_FOR_THREADS_NUM": "1"})
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "app.services.photo_faces", "analyze"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parents[2], env=env, bufsize=0,
                start_new_session=True, close_fds=True,
            )
        except OSError as exc:
            raise AnalysisFailed("analysis_failed") from exc
        self._process = process
        self._last_used = self._clock()
        self.starts += 1
        return process

    def _join_io(self) -> None:
        threads, self._io_threads = self._io_threads, ()
        deadline = time.monotonic() + CHILD_IO_JOIN_SECONDS
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            logger.error("Resident photo face pipe thread did not stop")

    @staticmethod
    def _close_pipes(process: subprocess.Popen) -> None:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except (AttributeError, OSError):
                    pass

    def _discard(self, process: subprocess.Popen | None = None) -> None:
        process = process or self._process
        if process is None:
            self._join_io()
            return
        if self._process is process:
            self._process = None
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGCONT)
            except (AttributeError, OSError, ProcessLookupError):
                pass
            try:
                process.terminate()
            except (AttributeError, OSError, ProcessLookupError):
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except (AttributeError, OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("Resident photo face child could not be reaped")
        self._close_pipes(process)
        self._join_io()

    @staticmethod
    def _resume_and_unregister(process: subprocess.Popen) -> None:
        """Atomically release background_work's pause ownership with the child running."""
        with background_work._lock:  # noqa: SLF001 - paired with BackgroundWork's private pause state
            _automatic, paused = background_work._processes.get(process, (False, False))  # noqa: SLF001
            if paused and process.poll() is None:
                try:
                    process.send_signal(signal.SIGCONT)
                except (OSError, ProcessLookupError):
                    pass
            background_work.unregister(process)

    def _once(
        self, data: bytes, *, backfill: bool, cancel: threading.Event, active_seconds: list[float],
    ) -> list[DetectedFace]:
        process = self._start()
        registered = False
        try:
            background_work.register(process, backfill=backfill)
            registered = True
            if not backfill:
                background_work.promote(process)
            if process.stdin is None or process.stdout is None:
                raise _ChildExited("child pipes unavailable")
            response: list[bytes | None] = []
            failure: list[BaseException] = []
            ready = threading.Event()

            def feed() -> None:
                try:
                    _write_frame(process.stdin, bytes([CHILD_REQUEST_VERSION]) + data, CHILD_REQUEST_BYTES)
                except (BrokenPipeError, OSError, _ChildProtocolError) as exc:
                    failure.append(exc)
                    ready.set()

            def drain() -> None:
                try:
                    response.append(_read_frame(process.stdout, CHILD_OUTPUT_BYTES))
                except BaseException as exc:  # captured and interpreted on the supervising thread
                    failure.append(exc)
                finally:
                    ready.set()

            drain_thread = threading.Thread(target=drain, daemon=True, name="photo-face-output")
            feed_thread = threading.Thread(target=feed, daemon=True, name="photo-face-input")
            self._io_threads = (drain_thread, feed_thread)
            drain_thread.start()
            feed_thread.start()
            last = self._clock()
            while not ready.is_set():
                paused = background_work.sync(process, backfill=backfill)
                now = self._clock()
                if not paused:
                    active_seconds[0] += now - last
                last = now
                if cancel.is_set():
                    raise AnalysisInterrupted("analysis_stopped")
                if active_seconds[0] > CHILD_TIMEOUT_SECONDS:
                    raise AnalysisTimeout("analysis_timeout")
                if process.poll() is not None:
                    ready.wait(0.1)
                    break
                ready.wait(0.05)
            if failure:
                error = failure[0]
                if isinstance(error, _ChildProtocolError):
                    raise error
                raise _ChildExited("child pipe failed") from error
            if not ready.is_set() or not response or response[0] is None:
                raise _ChildExited("child exited")
            if cancel.is_set():
                raise AnalysisInterrupted("analysis_stopped")
            self._join_io()
            self._last_used = self._clock()
            return _decode_child_payload(response[0])
        except BackgroundWorkClosed as exc:
            raise AnalysisInterrupted("analysis_stopped") from exc
        finally:
            if registered:
                self._resume_and_unregister(process)

    def analyze(
        self, data: bytes, *, backfill: bool, cancel: threading.Event,
        generation: tuple[object, ...] | None = None,
    ) -> list[DetectedFace]:
        if not isinstance(data, bytes) or not data or len(data) > photo_derivatives.MAX_OUTPUT_BYTES:
            raise InvalidImage("preview")
        with self._lock:
            self._sync_generation(generation or _model_generation())
            self._apply_retry()
            if self._failed or self._clock() < self._retry_after:
                raise ModelUnavailable("face model stopped unexpectedly; Retry")
            active_seconds = [0.0]
            for attempt in range(2):
                try:
                    return self._once(data, backfill=backfill, cancel=cancel, active_seconds=active_seconds)
                except _ChildRecycle as exc:
                    self._discard()
                    if attempt:
                        self._record_crash()
                        self._record_crash()
                        raise AnalysisCircuitOpen("analysis_failed") from exc
                except (_ChildExited, _ChildProtocolError) as exc:
                    self._record_crash()
                    self._discard()
                    if self._failed:
                        raise AnalysisCircuitOpen("analysis_failed") from exc
                    if attempt:
                        if isinstance(exc, _ChildProtocolError):
                            raise InvalidModelOutput("child_output") from exc
                        raise AnalysisFailed("analysis_failed") from exc
                except AnalysisTimeout:
                    self._record_crash()
                    self._discard()
                    if self._failed:
                        raise AnalysisCircuitOpen("analysis_failed")
                    raise
                except AnalysisInterrupted:
                    self._discard()
                    raise
                except (AnalysisFailed, ModelUnavailable):
                    self._record_crash()
                    self._discard()
                    if self._failed:
                        raise AnalysisCircuitOpen("analysis_failed")
                    raise
        raise AnalysisFailed("analysis_failed")

    def reap_idle(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and (process.poll() is not None or self._clock() - self._last_used >= CHILD_IDLE_SECONDS):
                self._discard(process)

    def close(self) -> None:
        with self._lock:
            self._discard()


_face_process = _FaceProcess()


def analyze_in_child(data: bytes, *, backfill: bool) -> list[DetectedFace]:
    """Analyze through the resident native child while keeping the historic call boundary."""
    return _face_process.analyze(
        data, backfill=backfill, cancel=worker._stop, generation=_model_generation(),  # noqa: SLF001
    )


class FaceWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        with session_scope() as db:
            recover(db)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="photo-face-worker")
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
        stopped = thread is None or not thread.is_alive()
        self._thread = None if stopped else thread
        _face_process.close()
        if stopped:
            # Lifespans and tests may start this process-wide worker again.
            self._stop.clear()

    def _claim(self, db: Session) -> str | None:
        if not model_ready() or not _face_process.available(_model_generation()):
            return None
        # An explicit Enable/Scan is interactive work: ordinary browser traffic
        # must not starve it. Shutdown admission remains authoritative.
        requested_allowed = not background_work.closed
        automatic_allowed = background_work.backfill_allowed()
        if not requested_allowed and not automatic_allowed:
            return None
        query = select(PhotoFaceJob, PhotoItem.sha256).join(
            PhotoFacePreference, PhotoFacePreference.user_id == PhotoFaceJob.owner_user_id,
        ).join(PhotoItem, PhotoItem.id == PhotoFaceJob.photo_id).where(
            PhotoFaceJob.state == "queued", PhotoFacePreference.enabled.is_(True),
            PhotoItem.owner_user_id == PhotoFaceJob.owner_user_id, PhotoItem.deleted_at.is_(None),
        )
        if requested_allowed and not automatic_allowed:
            query = query.where(PhotoFaceJob.requested.is_(True))
        elif automatic_allowed and not requested_allowed:
            query = query.where(PhotoFaceJob.requested.is_(False))
        selected = db.execute(query.order_by(
            PhotoFaceJob.requested.desc(), PhotoFaceJob.updated_at, PhotoFaceJob.id,
        ).limit(1)).first()
        if selected is None:
            return None
        job, source_sha256 = selected
        with write_transaction(db, name="photo_face_claim"):
            if job.source_sha256 != source_sha256:
                db.query(PhotoFace).filter(PhotoFace.photo_id == job.photo_id).delete(synchronize_session=False)
                job.source_sha256, job.attempts, job.error_code = source_sha256, 0, None
            job.state, job.started_at, job.updated_at = "running", utcnow(), utcnow()
            job.attempts += 1
        return job.id

    def run_once(self) -> bool:
        with session_scope() as db:
            job_id = self._claim(db)
            if job_id is None:
                if not background_work.closed and not self._stop.is_set():
                    try:
                        photo_face_index.settle_pending(db, cancel=self._stop)
                    except photo_face_index.IndexInterrupted:
                        pass
                return False
            job = db.get(PhotoFaceJob, job_id)
            requested = job.requested
            try:
                data = _preview(db, job.photo_id, job.source_sha256)
            except (photo_cache.PhotoStorageFull, photo_cache.PhotoStorageBusy) as exc:
                _defer(db, job_id, exc.code)
                return False
            except photo_files.PhotoSnapshotBusy:
                _defer(db, job_id, "snapshot_busy")
                return False
            except photo_files.PhotoFileChanged:
                _defer(db, job_id, "source_changed")
                return False
            except (OSError, FaceError, photo_derivatives.PhotoDecodeError) as exc:
                _fail(db, job_id, getattr(exc, "code", "preview_unavailable"), terminal=True)
                return True
        try:
            found = analyze_in_child(data, backfill=not requested)
            with session_scope() as db:
                commit_analysis(db, job_id, found)
        except photo_face_index.IndexInterrupted:
            with session_scope() as db:
                _defer(db, job_id, "interrupted")
            return False
        except ModelUnavailable:
            with session_scope() as db:
                _fail(db, job_id, "model_unavailable")
        except AnalysisCircuitOpen:
            with session_scope() as db:
                _fail(db, job_id, "analysis_failed", terminal=True)
        except (AnalysisFailed, AnalysisTimeout, AnalysisInterrupted) as exc:
            with session_scope() as db:
                _fail(db, job_id, exc.code)
        except (InvalidImage, InvalidModelOutput) as exc:
            with session_scope() as db:
                _fail(db, job_id, exc.code, terminal=True)
        except Exception:
            logger.exception("Photo face analysis failed without private input details")
            with session_scope() as db:
                _fail(db, job_id, "analysis_failed")
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                _face_process.reap_idle()
                self._wake.wait(5)
                self._wake.clear()


worker = FaceWorker()


def _child_limits() -> None:
    limits = [
        (resource.RLIMIT_FSIZE, CHILD_OUTPUT_BYTES, CHILD_OUTPUT_BYTES),
    ]
    if sys.platform == "linux":
        limits.append((resource.RLIMIT_AS, 1536 * 1024 * 1024, 1536 * 1024 * 1024))
    for kind, soft, hard in limits:
        try:
            _, current_hard = resource.getrlimit(kind)
            if current_hard != resource.RLIM_INFINITY:
                hard = min(hard, current_hard)
                soft = min(soft, hard)
            resource.setrlimit(kind, (soft, hard))
        except (OSError, ValueError):
            pass
    try:
        os.nice(19)
    except OSError:
        pass


def _rearm_child_cpu_limit() -> bool:
    """Give this request a cumulative CPU deadline without exhausting a resident process after N photos."""
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        used = usage.ru_utime + usage.ru_stime
        _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        target = max(1, math.ceil(used + CHILD_CPU_SECONDS))
        if hard != resource.RLIM_INFINITY and target > hard:
            # A process may lower but cannot raise an inherited finite hard cap.
            # One fresh request gets the available cap; a used process recycles.
            if used >= 1.0:
                return False
            target = hard
        resource.setrlimit(resource.RLIMIT_CPU, (target, hard))
        return True
    except (AttributeError, OSError, ValueError):
        return True  # request wall-time supervision remains authoritative


def _serve_child(source, sink) -> int:  # noqa: ANN001
    """Serve fixed analyze requests until EOF; malformed framing terminates this child."""
    try:
        analyzer: OpenCvFaceAnalyzer | None = None
        while True:
            request = _read_frame(source, CHILD_REQUEST_BYTES)
            if request is None:
                return 0
            if request[0] != CHILD_REQUEST_VERSION:
                return 1
            if not _rearm_child_cpu_limit():
                _write_frame(sink, _child_payload("restart_required"), CHILD_OUTPUT_BYTES)
                return 0
            if analyzer is None:
                try:
                    analyzer = OpenCvFaceAnalyzer()
                except ModelUnavailable:
                    _write_frame(sink, _child_payload("model_unavailable"), CHILD_OUTPUT_BYTES)
                    return 1
            try:
                rows = analyzer.analyze(request[1:])
                response = _child_payload(rows=rows)
            except FaceError as exc:
                response = _child_payload(exc.code)
            except Exception:
                response = _child_payload("analysis_failed")
            _write_frame(sink, response, CHILD_OUTPUT_BYTES)
    except Exception:
        return 1


def _child_main() -> int:
    if os.name == "posix":
        _child_limits()
    return _serve_child(sys.stdin.buffer, sys.stdout.buffer)


def delete_owner_data(db: Session, user: User) -> None:
    with photo_face_index.serialized_index_mutation(), write_transaction(db, name="photo_face_delete_all"):
        for model in (PhotoFaceEdit, PhotoFace, PhotoPerson, PhotoFaceJob):
            db.query(model).filter(model.owner_user_id == user.id).delete(synchronize_session=False)
        photo_face_index.delete_owner_data(db, user.id)
        row = preference(db, user.id)
        row.enabled, row.consented_at = False, None


def person_for_owner(db: Session, user: User, person_id: str) -> PhotoPerson:
    person = db.scalar(select(PhotoPerson).where(
        PhotoPerson.id == person_id, PhotoPerson.owner_user_id == user.id, PhotoPerson.redirected_to_id.is_(None),
    ))
    if person is None:
        raise FaceError("person_not_found")
    return person


def face_for_owner(db: Session, user: User, face_id: str) -> PhotoFace:
    face = db.scalar(select(PhotoFace).where(PhotoFace.id == face_id, PhotoFace.owner_user_id == user.id))
    if face is None:
        raise FaceError("face_not_found")
    return face


def assign(db: Session, user: User, face_id: str, person_id: str | None) -> PhotoFace:
    face = face_for_owner(db, user, face_id)
    prior = db.get(PhotoPerson, face.person_id) if face.person_id else None
    target = None
    if person_id is not None:
        target = person_for_owner(db, user, person_id)
    with write_transaction(db, name="photo_face_assign"):
        before = face.person_id
        face.person_id, face.assignment_source, face.updated_at = person_id, "manual", utcnow()
        if target is not None and target.feature_face_id is None:
            target.feature_face_id = face.id
        if prior is not None and prior.feature_face_id == face.id and prior.id != person_id:
            prior.feature_face_id = db.scalar(select(PhotoFace.id).where(
                PhotoFace.owner_user_id == user.id, PhotoFace.person_id == prior.id, PhotoFace.id != face.id,
            ).order_by(PhotoFace.id).limit(1))
        _record(db, user.id, "assign", {"face_id": face.id, "before": before, "after": person_id})
    return face


def create_person(db: Session, user: User, face_ids: list[str], *, name: str | None) -> PhotoPerson:
    unique = list(dict.fromkeys(face_ids))
    if not unique or len(unique) > 100 or len(unique) != len(face_ids):
        raise FaceError("invalid_faces")
    cleaned = " ".join(name.split()) if isinstance(name, str) else None
    if cleaned is not None and (not cleaned or len(cleaned) > 120):
        raise FaceError("invalid_name")
    with write_transaction(db, name="photo_person_create"):
        faces = list(db.scalars(select(PhotoFace).join(
            PhotoItem, PhotoItem.id == PhotoFace.photo_id,
        ).where(
            PhotoFace.id.in_(unique), PhotoFace.owner_user_id == user.id,
            PhotoItem.owner_user_id == user.id, PhotoItem.deleted_at.is_(None), PhotoItem.missing_at.is_(None),
        )))
        if len(faces) != len(unique):
            raise FaceError("face_not_found")
        prior_ids = {face.person_id for face in faces if face.person_id is not None}
        prior_people = list(db.scalars(select(PhotoPerson).where(
            PhotoPerson.id.in_(prior_ids), PhotoPerson.owner_user_id == user.id,
        ))) if prior_ids else []
        target = PhotoPerson(
            id=str(uuid.uuid4()), owner_user_id=user.id, pipeline_signature=PIPELINE_SIGNATURE,
            name=cleaned, feature_face_id=faces[0].id, revision=1,
        )
        db.add(target)
        db.flush()
        now = utcnow()
        for face in faces:
            face.person_id, face.assignment_source, face.updated_at = target.id, "manual", now
        for prior in prior_people:
            prior.revision += 1
            if prior.feature_face_id in unique:
                prior.feature_face_id = db.scalar(select(PhotoFace.id).where(
                    PhotoFace.owner_user_id == user.id, PhotoFace.person_id == prior.id,
                    PhotoFace.id.not_in(unique),
                ).order_by(PhotoFace.id).limit(1))
        _record(db, user.id, "create_person", {
            "person_id": target.id, "face_ids": sorted(unique), "name": cleaned,
        })
    return target


def merge(db: Session, user: User, source_id: str, target_id: str) -> PhotoPerson:
    if source_id == target_id:
        raise FaceError("same_person")
    source, target = person_for_owner(db, user, source_id), person_for_owner(db, user, target_id)
    with write_transaction(db, name="photo_person_merge"):
        db.query(PhotoFace).filter(PhotoFace.owner_user_id == user.id, PhotoFace.person_id == source.id).update(
            {"person_id": target.id, "assignment_source": "manual", "updated_at": utcnow()}, synchronize_session=False,
        )
        source.redirected_to_id, source.revision = target.id, source.revision + 1
        target.revision += 1
        if target.feature_face_id is None:
            target.feature_face_id = source.feature_face_id
        if target.name is None and source.name is not None:
            target.name = source.name
        _record(db, user.id, "merge", {"source_id": source.id, "target_id": target.id})
    return target


def split(db: Session, user: User, source_id: str, face_ids: list[str]) -> PhotoPerson:
    source = person_for_owner(db, user, source_id)
    unique = list(dict.fromkeys(face_ids))
    if not unique or len(unique) > 500:
        raise FaceError("invalid_faces")
    faces = list(db.scalars(select(PhotoFace).where(
        PhotoFace.owner_user_id == user.id, PhotoFace.person_id == source.id, PhotoFace.id.in_(unique),
    )))
    if len(faces) != len(unique):
        raise FaceError("face_not_found")
    target = PhotoPerson(
        id=str(uuid.uuid4()), owner_user_id=user.id, pipeline_signature=PIPELINE_SIGNATURE,
        feature_face_id=faces[0].id,
    )
    with write_transaction(db, name="photo_person_split"):
        db.add(target)
        db.flush()
        for face in faces:
            face.person_id, face.assignment_source, face.updated_at = target.id, "manual", utcnow()
        if source.feature_face_id in unique:
            source.feature_face_id = db.scalar(select(PhotoFace.id).where(
                PhotoFace.owner_user_id == user.id, PhotoFace.person_id == source.id,
                PhotoFace.id.not_in(unique),
            ).order_by(PhotoFace.id).limit(1))
        source.revision += 1
        _record(db, user.id, "split", {"source_id": source.id, "target_id": target.id, "face_ids": sorted(unique)})
    return target


def rename(
    db: Session, user: User, person_id: str, *, name: str | None, name_present: bool = True, hidden: bool | None,
) -> PhotoPerson:
    person = person_for_owner(db, user, person_id)
    cleaned = " ".join(name.split()) if isinstance(name, str) else None
    if cleaned is not None and (not cleaned or len(cleaned) > 120):
        raise FaceError("invalid_name")
    with write_transaction(db, name="photo_person_edit"):
        before = {"name": person.name, "hidden": person.hidden}
        if name_present:
            person.name = cleaned
        if hidden is not None:
            person.hidden = hidden
        person.revision += 1
        _record(db, user.id, "edit_person", {"person_id": person.id, "before": before, "after": {"name": person.name, "hidden": person.hidden}})
    return person


def counts(db: Session, user_id: str) -> dict[str, int]:
    rows = db.execute(select(PhotoFaceJob.state, func.count()).where(PhotoFaceJob.owner_user_id == user_id).group_by(PhotoFaceJob.state))
    return {state: int(count) for state, count in rows}


def pack_floats(values: list[float]) -> bytes:
    """Small test/support helper whose format is the public persistence contract."""
    return validate_embedding(values)


if __name__ == "__main__":
    raise SystemExit(_child_main())
