"""Local, owner-consented face detection, embeddings and conservative grouping."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import resource
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import User, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.photo_face_models import PhotoFace, PhotoFaceEdit, PhotoFaceJob, PhotoFacePreference, PhotoPerson
from app.photo_models import PhotoDerivative, PhotoItem
from app.services import model_catalog, photo_cache, photo_derivatives, photo_files
from app.services.background_work import background_work
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
MAX_PERSON_PROTOTYPES = 4096
MAX_UNASSIGNED_CANDIDATES = 4096
CHILD_TIMEOUT_SECONDS = 45
CHILD_OUTPUT_BYTES = 256 * 1024


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
        self.yunet = str(yunet)
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
        detector = cv2.FaceDetectorYN.create(self.yunet, "", (width, height), DETECTION_SCORE, 0.3, MAX_FACES * 4)
        _result, raw_faces = detector.detect(image)
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


def _candidate_faces(db: Session, owner_id: str) -> tuple[list[PhotoFace], list[PhotoFace]]:
    """Return bounded person prototypes and recent outliers, never the whole library."""
    assigned = list(db.scalars(select(PhotoFace).where(
        PhotoFace.owner_user_id == owner_id, PhotoFace.pipeline_signature == PIPELINE_SIGNATURE,
        PhotoFace.person_id.is_not(None),
    ).order_by(PhotoFace.updated_at.desc(), PhotoFace.id).limit(MAX_PERSON_PROTOTYPES * 4)))
    prototypes: list[PhotoFace] = []
    seen: set[str] = set()
    for row in assigned:
        if row.person_id not in seen:
            prototypes.append(row)
            seen.add(row.person_id)
        if len(prototypes) >= MAX_PERSON_PROTOTYPES:
            break
    unassigned = list(db.scalars(select(PhotoFace).where(
        PhotoFace.owner_user_id == owner_id, PhotoFace.pipeline_signature == PIPELINE_SIGNATURE,
        PhotoFace.person_id.is_(None), PhotoFace.assignment_source == "automatic",
    ).order_by(PhotoFace.updated_at.desc(), PhotoFace.id).limit(MAX_UNASSIGNED_CANDIDATES)))
    return prototypes, unassigned


def _cluster_new_faces(db: Session, owner_id: str, new_faces: list[PhotoFace]) -> None:
    pref = db.get(PhotoFacePreference, owner_id)
    if not pref or not pref.auto_group:
        return
    prototypes, unassigned = _candidate_faces(db, owner_id)
    candidates = [*prototypes, *unassigned]
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
    if job is None or job.state != "running":
        return
    for found in detections:
        if not (math.isfinite(found.score) and DETECTION_SCORE <= found.score <= 1.0):
            raise InvalidModelOutput("score")
        if not (0 <= found.x1 < found.x2 <= 1 and 0 <= found.y1 < found.y2 <= 1):
            raise InvalidModelOutput("box")
        vector(found.embedding)
    with write_transaction(db, name="photo_face_commit"):
        photo = db.scalar(select(PhotoItem).where(PhotoItem.id == job.photo_id).execution_options(populate_existing=True))
        if photo is None or photo.deleted_at is not None or photo.owner_user_id != job.owner_user_id:
            job.state, job.error_code, job.finished_at, job.updated_at = "failed", "photo_unavailable", utcnow(), utcnow()
            return
        if photo.sha256 != job.source_sha256:
            db.query(PhotoFace).filter(PhotoFace.photo_id == photo.id).delete(synchronize_session=False)
            job.source_sha256, job.state, job.error_code = photo.sha256, "queued", "source_changed"
            job.attempts, job.started_at, job.finished_at, job.updated_at = 0, None, None, utcnow()
            queue_after_commit(db, worker.wake)
            return
        pref = db.get(PhotoFacePreference, photo.owner_user_id)
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
        _cluster_new_faces(db, photo.owner_user_id, new_rows)
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
    data = photo_derivatives.read(expected_key)
    photo_cache.touch(expected_key)
    return data


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
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None

    def _claim(self, db: Session) -> str | None:
        if not model_ready():
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
                return False
            job = db.get(PhotoFaceJob, job_id)
            requested = job.requested
            try:
                data = _preview(db, job.photo_id, job.source_sha256)
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
        except ModelUnavailable:
            with session_scope() as db:
                _fail(db, job_id, "model_unavailable")
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
                self._wake.wait(5)
                self._wake.clear()


worker = FaceWorker()


def _child_limits() -> None:
    limits = [
        (resource.RLIMIT_CPU, 30, 31),
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


def analyze_in_child(data: bytes, *, backfill: bool) -> list[DetectedFace]:
    """Run native OpenCV in a bounded child; activity can pause the child."""
    process = subprocess.Popen(
        [sys.executable, "-m", "app.services.photo_faces", "analyze"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        background_work.register(process, backfill=backfill)
        if not backfill:
            background_work.promote(process)
        assert process.stdin is not None
        assert process.stdout is not None
        output: list[bytes] = []
        output_ready = threading.Event()

        def feed() -> None:
            try:
                process.stdin.write(data)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def drain() -> None:
            try:
                output.append(process.stdout.read(CHILD_OUTPUT_BYTES + 1))
            finally:
                output_ready.set()

        threading.Thread(target=drain, daemon=True, name="photo-face-output").start()
        threading.Thread(target=feed, daemon=True, name="photo-face-input").start()
        active_seconds, last = 0.0, time.monotonic()
        while process.poll() is None:
            paused = background_work.sync(process, backfill=backfill)
            now = time.monotonic()
            if not paused:
                active_seconds += now - last
            last = now
            if output_ready.is_set() and output and len(output[0]) > CHILD_OUTPUT_BYTES:
                raise InvalidModelOutput("child_output")
            if active_seconds > CHILD_TIMEOUT_SECONDS:
                raise AnalysisTimeout("analysis_timeout")
            if worker._stop.wait(0.1):  # noqa: SLF001 - worker-owned child cancellation
                raise AnalysisInterrupted("analysis_stopped")
        if not output_ready.wait(1) or not output:
            raise InvalidModelOutput("child_output")
        raw = output[0]
        if process.returncode != 0 or len(raw) > CHILD_OUTPUT_BYTES:
            raise AnalysisFailed("analysis_failed")
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > MAX_FACES:
            raise InvalidModelOutput("detections")
        return [DetectedFace(
            score=float(row["score"]), x1=float(row["x1"]), y1=float(row["y1"]),
            x2=float(row["x2"]), y2=float(row["y2"]), embedding=bytes.fromhex(row["embedding"]),
        ) for row in payload]
    except FaceError:
        raise
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidModelOutput("child_output") from exc
    finally:
        background_work.unregister(process)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _child_main() -> int:
    try:
        if os.name == "posix":
            _child_limits()
        data = sys.stdin.buffer.read(photo_derivatives.MAX_OUTPUT_BYTES + 1)
        rows = OpenCvFaceAnalyzer().analyze(data)
        payload = [{
            "score": row.score, "x1": row.x1, "y1": row.y1, "x2": row.x2, "y2": row.y2,
            "embedding": row.embedding.hex(),
        } for row in rows]
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > CHILD_OUTPUT_BYTES:
            return 1
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        return 1


def delete_owner_data(db: Session, user: User) -> None:
    with write_transaction(db, name="photo_face_delete_all"):
        for model in (PhotoFaceEdit, PhotoFace, PhotoPerson, PhotoFaceJob):
            db.query(model).filter(model.owner_user_id == user.id).delete(synchronize_session=False)
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
