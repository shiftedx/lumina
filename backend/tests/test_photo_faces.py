"""Public privacy and durable-correction contracts for local photo faces."""
from __future__ import annotations

import math
import signal
import subprocess
import threading
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.models import utcnow
from app.photo_face_models import PhotoFace, PhotoFaceEdit, PhotoFaceJob, PhotoFacePreference, PhotoPerson
from app.photo_models import PhotoDerivative, PhotoItem
from app.services import photo_faces
from support import make_user


OWNER = make_user("face-owner", role="admin")
OTHER = make_user("face-other")


@pytest.fixture(autouse=True)
def reset_resident_face_process():  # noqa: ANN201
    """A lifespan may close the process-wide worker; each test starts from a clean, restartable child."""
    photo_faces._face_process.close()  # noqa: SLF001
    photo_faces._face_process.retry(photo_faces._model_generation())  # noqa: SLF001
    photo_faces.worker._stop.clear()  # noqa: SLF001
    yield
    photo_faces._face_process.close()  # noqa: SLF001
    photo_faces._face_process.retry(photo_faces._model_generation())  # noqa: SLF001
    photo_faces.worker._stop.clear()  # noqa: SLF001


class _ResponsePipe:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._data = bytearray()
        self._closed = False

    def read(self, size: int) -> bytes:
        with self._condition:
            while not self._data and not self._closed:
                self._condition.wait()
            found = bytes(self._data[:size])
            del self._data[:size]
            return found

    def feed(self, data: bytes) -> None:
        with self._condition:
            self._data.extend(data)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class _RequestPipe:
    def __init__(self, process: "_ScriptedProcess") -> None:
        self.process = process
        self.data = bytearray()

    def write(self, data: bytes) -> int:
        if self.process.returncode is not None:
            raise BrokenPipeError
        self.data.extend(data)
        return len(data)

    def flush(self) -> None:
        raw = BytesIO(bytes(self.data))
        self.data.clear()
        request = photo_faces._read_frame(raw, photo_faces.CHILD_REQUEST_BYTES)  # noqa: SLF001
        assert request is not None
        self.process.requests.append(request)
        self.process.behavior(self.process, request)

    def close(self) -> None:
        pass


class _ScriptedProcess:
    def __init__(self, behavior) -> None:  # noqa: ANN001
        self.behavior = behavior
        self.requests: list[bytes] = []
        self.stdout = _ResponsePipe()
        self.stdin = _RequestPipe(self)
        self.returncode: int | None = None
        self.terminated = False
        self.signals: list[int] = []

    def respond(self, payload: bytes) -> None:
        self.stdout.feed(photo_faces.FRAME_HEADER.pack(len(payload)) + payload)

    def exit(self, returncode: int = 1) -> None:
        self.returncode = returncode
        self.stdout.close()

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, found: int) -> None:
        self.signals.append(found)

    def terminate(self) -> None:
        self.terminated = True
        self.exit(-15)

    def kill(self) -> None:
        self.terminated = True
        self.exit(-9)

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self.returncode is None:
            raise subprocess.TimeoutExpired("face-child", timeout)
        return self.returncode


def photo(photo_id: str, owner: str = OWNER.id, *, visibility: str = "private") -> PhotoItem:
    return PhotoItem(
        id=photo_id, owner_user_id=owner, visibility=visibility, storage_kind="managed",
        storage_key=f"originals/aa/{photo_id}.jpg", original_filename="portrait.jpg", content_type="image/jpeg",
        sha256=("a" if owner == OWNER.id else "b") * 64, bytes=100, width=400, height=300,
        orientation=1, metadata_json={},
    )


def embedding(index: int = 0) -> bytes:
    values = np.zeros(128, dtype=np.float32)
    values[index] = 1
    return photo_faces.validate_embedding(values)


def webp(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "#8b6048").save(output, format="WEBP", quality=85)
    return output.getvalue()


def face(face_id: str, photo_id: str, owner: str, person_id: str | None = None, *, source: str = "automatic") -> PhotoFace:
    return PhotoFace(
        id=face_id, photo_id=photo_id, owner_user_id=owner, person_id=person_id,
        pipeline_signature=photo_faces.PIPELINE_SIGNATURE, detector_score=.9,
        x1=.1, y1=.1, x2=.5, y2=.7, embedding=embedding(), assignment_source=source,
    )


def seed(factory) -> None:  # noqa: ANN001
    with factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), make_user(OTHER.id), photo("photo-owner"), photo("photo-other", OTHER.id, visibility="shared")])
        db.add_all([
            PhotoFacePreference(user_id=OWNER.id, enabled=True, auto_group=True, consented_at=utcnow()),
            PhotoFacePreference(user_id=OTHER.id, enabled=True, auto_group=True, consented_at=utcnow()),
            PhotoPerson(id="person-owner", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE, name="Owner label"),
            PhotoPerson(id="person-other", owner_user_id=OTHER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE, name="Private other label"),
        ])
        db.add_all([face("face-owner", "photo-owner", OWNER.id, "person-owner"), face("face-other", "photo-other", OTHER.id, "person-other")])
        db.commit()


def test_people_and_face_boxes_are_owner_scoped_even_when_the_photo_is_shared(api_client, db_factory) -> None:  # noqa: ANN001
    seed(db_factory)
    owner = api_client(user=OWNER, base_url="http://localhost")
    people = owner.get("/api/photo-people")
    assert people.status_code == 200
    assert [row["name"] for row in people.json()["items"]] == ["Owner label"]
    payload = owner.get("/api/photos/photo-owner/faces").json()["items"][0]
    assert payload["person_id"] == "person-owner" and "embedding" not in payload
    assert owner.get("/api/photos/photo-other/faces").status_code == 404

    other = api_client(user=OTHER, base_url="http://localhost")
    assert [row["name"] for row in other.get("/api/photo-people").json()["items"]] == ["Private other label"]
    assert other.get("/api/photos/photo-owner/faces").status_code == 404


def test_enabling_requires_explicit_biometric_acknowledgement(api_client, db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), photo("photo-owner")])
        db.commit()
    client = api_client(user=OWNER, base_url="http://localhost")
    refused = client.put("/api/photo-faces/settings", json={"enabled": True, "auto_group": True})
    assert refused.status_code == 422 and refused.json()["detail"] == "biometric_acknowledgement_required"
    enabled = client.put("/api/photo-faces/settings", json={
        "enabled": True, "auto_group": False, "acknowledge_biometric_processing": True,
    })
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True and enabled.json()["auto_group"] is False
    assert client.put("/api/photo-faces/settings", json={"enabled": False, "auto_group": False}).status_code == 200
    # Switching back on reuses recorded consent; deleting biometric data clears it.
    assert client.put("/api/photo-faces/settings", json={"enabled": True, "auto_group": False}).status_code == 200
    assert client.delete("/api/photo-faces/data").status_code == 204
    assert client.put("/api/photo-faces/settings", json={"enabled": True, "auto_group": False}).status_code == 422
    assert client.put("/api/photo-faces/settings", json={"enabled": True, "auto_group": False, "acknowledge_biometric_processing": True}).status_code == 200
    with db_factory() as db:
        job = db.scalar(db.query(PhotoFaceJob).where(PhotoFaceJob.photo_id == "photo-owner").statement)
        assert (job.state, job.requested) == ("queued", True)


def test_explicit_scan_promotes_failed_backfill_and_resets_runtime_circuit(
    api_client, db_factory, monkeypatch,
) -> None:  # noqa: ANN001
    retries: list[bool] = []
    monkeypatch.setattr(photo_faces._face_process, "retry", lambda *_args, **_kwargs: retries.append(True))  # noqa: SLF001
    with db_factory() as db:
        db.add_all([
            make_user(OWNER.id, role="admin"), photo("photo-owner"),
            PhotoFacePreference(user_id=OWNER.id, enabled=True, auto_group=True),
            PhotoFaceJob(
                id="failed-backfill", photo_id="photo-owner", owner_user_id=OWNER.id,
                pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="a" * 64,
                state="failed", requested=False, attempts=3, error_code="analysis_failed",
            ),
        ])
        db.commit()

    response = api_client(user=OWNER, base_url="http://localhost").post("/api/photo-faces/scan")
    assert response.status_code == 200 and response.json() == {"queued": 1}
    assert retries == [True]
    with db_factory() as db:
        job = db.get(PhotoFaceJob, "failed-backfill")
        assert (job.state, job.requested, job.attempts, job.error_code) == ("queued", True, 0, None)


def test_jobs_are_idempotent_and_restart_recovery_is_bounded(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), photo("photo-owner"), PhotoFacePreference(user_id=OWNER.id, enabled=True)])
        db.commit()
    with db_factory() as db:
        item = db.get(PhotoItem, "photo-owner")
        first = photo_faces.queue_photo(db, item, requested=True)
        second = photo_faces.queue_photo(db, item, requested=True)
        assert first.id == second.id and db.query(PhotoFaceJob).count() == 1
        first.state, first.attempts = "running", 2
        db.commit()
        assert photo_faces.recover(db) == 1 and db.get(PhotoFaceJob, first.id).state == "queued"
        first = db.get(PhotoFaceJob, first.id)
        first.state, first.attempts = "running", 3
        db.commit()
        photo_faces.recover(db)
        assert db.get(PhotoFaceJob, first.id).state == "failed"


def test_open_runtime_circuit_terminalizes_current_job_for_visible_retry(db_factory, monkeypatch) -> None:  # noqa: ANN001
    with db_factory() as db:
        db.add_all([
            make_user(OWNER.id, role="admin"), photo("photo-owner"),
            PhotoFacePreference(user_id=OWNER.id, enabled=True),
            PhotoFaceJob(
                id="circuit-job", photo_id="photo-owner", owner_user_id=OWNER.id,
                pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="a" * 64,
                state="queued", requested=True,
            ),
        ])
        db.commit()
    monkeypatch.setattr(photo_faces, "model_ready", lambda: True)
    monkeypatch.setattr(photo_faces, "session_scope", db_factory)
    monkeypatch.setattr(photo_faces._face_process, "available", lambda _generation: True)  # noqa: SLF001
    monkeypatch.setattr(photo_faces, "_preview", lambda *_args: b"preview")
    monkeypatch.setattr(
        photo_faces, "analyze_in_child",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(photo_faces.AnalysisCircuitOpen("analysis_failed")),
    )
    assert photo_faces.worker.run_once() is True
    with db_factory() as db:
        row = db.get(PhotoFaceJob, "circuit-job")
        assert (row.state, row.error_code, row.attempts) == ("failed", "analysis_failed", 1)


def test_invalid_model_vectors_and_boxes_never_commit(db_factory) -> None:  # noqa: ANN001
    with pytest.raises(photo_faces.InvalidModelOutput):
        photo_faces.validate_embedding([math.nan] * 128)
    with pytest.raises(photo_faces.InvalidModelOutput):
        photo_faces.validate_embedding([1.0] * 127)

    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), photo("photo-owner"), PhotoFacePreference(user_id=OWNER.id, enabled=True)])
        job = PhotoFaceJob(
            id="job", photo_id="photo-owner", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE,
            source_sha256="a" * 64, state="running", requested=True, attempts=1,
        )
        db.add(job)
        db.commit()
        invalid = photo_faces.DetectedFace(.9, -.1, .1, .5, .5, embedding())
        with pytest.raises(photo_faces.InvalidModelOutput):
            photo_faces.commit_analysis(db, job.id, [invalid])
        assert db.query(PhotoFace).count() == 0 and db.get(PhotoFaceJob, job.id).state == "running"


def test_analyzer_reuses_detector_and_sets_each_resized_input() -> None:
    import cv2

    detector_sizes: list[tuple[int, int]] = []

    class Detector:
        def setInputSize(self, input_size):  # noqa: ANN001,ANN202,N802
            detector_sizes.append(input_size)

        def detect(self, image):  # noqa: ANN001,ANN202
            height, width = image.shape[:2]
            row = [width * .1, height * .1, width * .2, height * .2, *([1.0] * 10), .95]
            return None, np.asarray([row], dtype=np.float32)

    class Cv2:
        IMREAD_COLOR = cv2.IMREAD_COLOR
        INTER_AREA = cv2.INTER_AREA
        imdecode = staticmethod(cv2.imdecode)
        resize = staticmethod(cv2.resize)

    class Recognizer:
        @staticmethod
        def alignCrop(image, _face):  # noqa: ANN001,ANN202,N802
            return image

        @staticmethod
        def feature(_crop):  # noqa: ANN001,ANN202
            values = np.zeros((1, 128), dtype=np.float32)
            values[0, 0] = 1
            return values

    analyzer = object.__new__(photo_faces.OpenCvFaceAnalyzer)
    analyzer.cv2, analyzer.detector, analyzer.recognizer = Cv2, Detector(), Recognizer()
    portrait = analyzer.analyze(webp(1200, 1800))
    landscape = analyzer.analyze(webp(1200, 800))

    assert detector_sizes == [(800, 1200), (1200, 800)]
    assert portrait[0].x1 == pytest.approx(.1) and portrait[0].y2 == pytest.approx(.3)
    assert landscape[0].x2 == pytest.approx(.3)


def test_portrait_face_crop_is_served_as_a_small_thumbnail(api_client, db_factory) -> None:  # noqa: ANN001
    seed(db_factory)
    key = photo_faces.photo_derivatives.source_key("a" * 64, 400)
    target = photo_faces.photo_derivatives.derivative_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(webp(400, 600))
    with db_factory() as db:
        db.add(PhotoDerivative(
            id="portrait-400", photo_id="photo-owner", width=400, source_key=key,
            state="ready", bytes=target.stat().st_size,
        ))
        db.commit()

    response = api_client(user=OWNER, base_url="http://localhost").get("/api/photo-faces/face-owner/image")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    with Image.open(BytesIO(response.content)) as thumbnail:
        assert max(thumbnail.size) <= 400


def test_manual_assignment_survives_reanalysis_and_merge_split_are_durable(db_factory) -> None:  # noqa: ANN001
    seed(db_factory)
    with db_factory() as db:
        owner = db.get(type(OWNER), OWNER.id)
        second = PhotoPerson(id="person-second", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE, name="Second")
        db.add(second)
        db.commit()
        photo_faces.assign(db, owner, "face-owner", second.id)
        job = PhotoFaceJob(
            id="job", photo_id="photo-owner", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE,
            source_sha256="a" * 64, state="running", requested=True, attempts=1,
        )
        db.add(job)
        db.commit()
        photo_faces.commit_analysis(db, job.id, [photo_faces.DetectedFace(.95, .1, .1, .5, .7, embedding())])
        kept = db.scalar(db.query(PhotoFace).where(PhotoFace.photo_id == "photo-owner").statement)
        assert (kept.id, kept.person_id, kept.assignment_source) == ("face-owner", second.id, "manual")

        photo_faces.merge(db, owner, "person-owner", second.id)
        assert db.get(PhotoPerson, "person-owner").redirected_to_id == second.id
        split = photo_faces.split(db, owner, second.id, [kept.id])
        assert db.get(PhotoFace, kept.id).person_id == split.id
        assert db.get(PhotoFace, kept.id).assignment_source == "manual"


def test_single_ungrouped_face_can_create_private_manual_person_and_survive_reanalysis(
    api_client, db_factory,
) -> None:  # noqa: ANN001
    seed(db_factory)
    with db_factory() as db:
        owner_face = db.get(PhotoFace, "face-owner")
        owner_face.person_id = None
        db.commit()

    client = api_client(user=OWNER, base_url="http://localhost")
    refused = client.post("/api/photo-people", json={"name": "Not mine", "face_ids": ["face-other"]})
    assert refused.status_code == 404 and refused.json()["detail"] == "face_not_found"
    created = client.post("/api/photo-people", json={"name": "Alice", "face_ids": ["face-owner"]})
    assert created.status_code == 201
    person = created.json()["person"]
    assert person["name"] == "Alice" and person["revision"] == 1
    assert person["face_count"] == person["photo_count"] == 1
    filtered = client.get(f"/api/photos?person_id={person['id']}").json()
    assert [item["id"] for item in filtered["items"]] == ["photo-owner"]

    with db_factory() as db:
        grouped = db.get(PhotoFace, "face-owner")
        assert (grouped.person_id, grouped.assignment_source) == (person["id"], "manual")
        assert db.query(PhotoFaceEdit).filter(PhotoFaceEdit.operation == "create_person").one_or_none() is not None
        job = PhotoFaceJob(
            id="manual-person-reanalysis", photo_id="photo-owner", owner_user_id=OWNER.id,
            pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="a" * 64,
            state="running", requested=True, attempts=1,
        )
        db.add(job)
        db.commit()
        photo_faces.commit_analysis(db, job.id, [photo_faces.DetectedFace(.95, .1, .1, .5, .7, embedding())])
        preserved = db.get(PhotoFace, "face-owner")
        assert (preserved.person_id, preserved.assignment_source) == (person["id"], "manual")


def test_deleting_face_data_is_complete_for_one_owner_only(api_client, db_factory) -> None:  # noqa: ANN001
    seed(db_factory)
    response = api_client(user=OWNER, base_url="http://localhost").delete("/api/photo-faces/data")
    assert response.status_code == 204
    with db_factory() as db:
        assert db.query(PhotoFace).filter(PhotoFace.owner_user_id == OWNER.id).count() == 0
        assert db.query(PhotoPerson).filter(PhotoPerson.owner_user_id == OWNER.id).count() == 0
        assert db.query(PhotoFace).filter(PhotoFace.owner_user_id == OTHER.id).count() == 1
        assert db.get(PhotoFacePreference, OWNER.id).enabled is False


def test_disabling_during_inference_prevents_late_biometric_publication(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), photo("photo-owner"), PhotoFacePreference(user_id=OWNER.id, enabled=True)])
        db.add(PhotoFaceJob(
            id="job", photo_id="photo-owner", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE,
            source_sha256="a" * 64, state="running", requested=True, attempts=1,
        ))
        db.commit()
        db.get(PhotoFacePreference, OWNER.id).enabled = False
        db.commit()
        photo_faces.commit_analysis(db, "job", [photo_faces.DetectedFace(.95, .1, .1, .5, .7, embedding())])
        assert db.query(PhotoFace).count() == 0
        job = db.get(PhotoFaceJob, "job")
        assert (job.state, job.error_code) == ("canceled", "disabled")


def test_changed_external_source_requeues_and_discards_in_flight_output(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), photo("photo-owner"), PhotoFacePreference(user_id=OWNER.id, enabled=True)])
        job = PhotoFaceJob(
            id="job", photo_id="photo-owner", owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE,
            source_sha256="a" * 64, state="running", requested=True, attempts=2,
        )
        db.add_all([job, face("old-face", "photo-owner", OWNER.id)])
        db.commit()
        db.get(PhotoItem, "photo-owner").sha256 = "c" * 64
        db.commit()

        photo_faces.commit_analysis(db, job.id, [photo_faces.DetectedFace(.95, .1, .1, .5, .7, embedding())])

        assert db.query(PhotoFace).count() == 0
        job = db.get(PhotoFaceJob, job.id)
        assert (job.state, job.source_sha256, job.attempts, job.error_code) == (
            "queued", "c" * 64, 0, "source_changed",
        )


def test_clustering_candidates_are_bounded_and_claim_skips_disabled_owners(db_factory, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(photo_faces, "MAX_PERSON_PROTOTYPES", 2)
    monkeypatch.setattr(photo_faces, "MAX_UNASSIGNED_CANDIDATES", 3)
    with db_factory() as db:
        db.add_all([make_user(OWNER.id, role="admin"), make_user(OTHER.id), photo("photo-owner"), photo("photo-other", OTHER.id)])
        db.add_all([
            PhotoFacePreference(user_id=OWNER.id, enabled=True), PhotoFacePreference(user_id=OTHER.id, enabled=False),
            PhotoFaceJob(id="enabled-job", photo_id="photo-owner", owner_user_id=OWNER.id,
                         pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="a" * 64,
                         state="queued", requested=True),
            PhotoFaceJob(id="disabled-job", photo_id="photo-other", owner_user_id=OTHER.id,
                         pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="b" * 64,
                         state="queued", requested=True),
        ])
        for index in range(6):
            person_id = f"person-{index}" if index < 3 else None
            if person_id:
                db.add(PhotoPerson(id=person_id, owner_user_id=OWNER.id, pipeline_signature=photo_faces.PIPELINE_SIGNATURE))
            db.add(face(f"face-{index}", f"candidate-photo-{index}", OWNER.id, person_id))
        db.commit()
        prototypes, unassigned = photo_faces._candidate_faces(db, OWNER.id)  # noqa: SLF001
        assert len(prototypes) == 2 and len(unassigned) == 3
        monkeypatch.setattr(photo_faces, "model_ready", lambda: True)
        monkeypatch.setattr(photo_faces.background_work, "closed", False)
        monkeypatch.setattr(photo_faces.background_work, "busy", lambda: False)
        monkeypatch.setattr(photo_faces.background_work, "backfill_allowed", lambda: False)
        db.get(PhotoItem, "photo-owner").sha256 = "c" * 64
        db.commit()
        assert photo_faces.worker._claim(db) == "enabled-job"  # noqa: SLF001
        assert db.get(PhotoFaceJob, "enabled-job").source_sha256 == "c" * 64
        assert db.get(PhotoFaceJob, "disabled-job").state == "queued"


def test_requested_job_claims_while_busy_but_automatic_job_stays_queued(db_factory, monkeypatch) -> None:  # noqa: ANN001
    requested_photo = photo("requested-photo")
    automatic_photo = photo("automatic-photo")
    automatic_photo.sha256 = "c" * 64
    with db_factory() as db:
        db.add_all([
            make_user(OWNER.id, role="admin"), requested_photo, automatic_photo,
            PhotoFacePreference(user_id=OWNER.id, enabled=True),
            PhotoFaceJob(
                id="automatic-job", photo_id="automatic-photo", owner_user_id=OWNER.id,
                pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="c" * 64,
                state="queued", requested=False,
            ),
            PhotoFaceJob(
                id="requested-job", photo_id="requested-photo", owner_user_id=OWNER.id,
                pipeline_signature=photo_faces.PIPELINE_SIGNATURE, source_sha256="a" * 64,
                state="queued", requested=True,
            ),
        ])
        db.commit()
        monkeypatch.setattr(photo_faces, "model_ready", lambda: True)
        monkeypatch.setattr(photo_faces.background_work, "closed", False)
        monkeypatch.setattr(photo_faces.background_work, "busy", lambda: True)
        monkeypatch.setattr(photo_faces.background_work, "backfill_allowed", lambda: False)

        assert photo_faces.worker._claim(db) == "requested-job"  # noqa: SLF001
        assert db.get(PhotoFaceJob, "automatic-job").state == "queued"


def test_face_child_is_reused_and_uses_backend_package_directory_from_any_server_cwd(tmp_path: Path, monkeypatch) -> None:
    created: dict[str, object] = {}
    promoted: list[object] = []
    processes: list[_ScriptedProcess] = []

    def popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        created.update(kwargs)
        process = _ScriptedProcess(lambda child, _request: child.respond(photo_faces._child_payload(rows=[])))  # noqa: SLF001
        processes.append(process)
        return process

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    monkeypatch.setattr(photo_faces.background_work, "promote", promoted.append)
    assert photo_faces.analyze_in_child(b"preview", backfill=False) == []
    assert photo_faces.analyze_in_child(b"another preview", backfill=False) == []
    assert created["cwd"] == Path(photo_faces.__file__).resolve().parents[2]
    assert len(processes) == 1 and len(processes[0].requests) == 2
    assert all(request[0] == photo_faces.CHILD_REQUEST_VERSION for request in processes[0].requests)
    assert len(promoted) == 2
    photo_faces.worker.close()
    assert photo_faces.analyze_in_child(b"after restart", backfill=False) == []
    assert len(processes) == 2


def test_child_loop_loads_models_once_and_rejects_unknown_request_versions(monkeypatch) -> None:
    loads: list[object] = []

    class Analyzer:
        def __init__(self) -> None:
            loads.append(self)

        @staticmethod
        def analyze(_data: bytes) -> list[photo_faces.DetectedFace]:
            return []

    def framed(payload: bytes) -> bytes:
        return photo_faces.FRAME_HEADER.pack(len(payload)) + payload

    monkeypatch.setattr(photo_faces, "OpenCvFaceAnalyzer", Analyzer)
    monkeypatch.setattr(photo_faces, "_rearm_child_cpu_limit", lambda: True)
    source = BytesIO(framed(b"\x01first") + framed(b"\x01second"))
    sink = BytesIO()
    assert photo_faces._serve_child(source, sink) == 0  # noqa: SLF001
    assert len(loads) == 1
    output = BytesIO(sink.getvalue())
    assert photo_faces._decode_child_payload(photo_faces._read_frame(output, photo_faces.CHILD_OUTPUT_BYTES)) == []  # noqa: SLF001
    assert photo_faces._decode_child_payload(photo_faces._read_frame(output, photo_faces.CHILD_OUTPUT_BYTES)) == []  # noqa: SLF001

    loads.clear()
    assert photo_faces._serve_child(BytesIO(framed(b"\x02corrupt")), BytesIO()) == 1  # noqa: SLF001
    assert loads == []


def test_face_child_restarts_and_retries_after_native_exit_or_corrupt_output(monkeypatch) -> None:
    behaviors = [
        lambda child, _request: child.exit(),
        lambda child, _request: child.respond(b"{}"),
        lambda child, _request: child.respond(photo_faces._child_payload(rows=[])),  # noqa: SLF001
    ]
    processes: list[_ScriptedProcess] = []

    def popen(*_args, **_kwargs):  # noqa: ANN202
        process = _ScriptedProcess(behaviors[len(processes)])
        processes.append(process)
        return process

    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    client = photo_faces._FaceProcess()  # noqa: SLF001
    cancel = threading.Event()
    # A native exit is retried once and recovers in process 2, whose corrupt
    # response is the second failure for this call.
    with pytest.raises(photo_faces.AnalysisCircuitOpen):
        client.analyze(b"preview", backfill=False, cancel=cancel)
    # The next request starts cleanly after both failed generations.
    client.retry()
    assert client.analyze(b"preview", backfill=False, cancel=cancel) == []
    assert client.starts == len(processes) == 3
    assert processes[0].terminated is False
    assert processes[1].terminated is True
    client.close()


def test_face_child_output_requires_json_numbers_and_unit_embeddings() -> None:
    row = {
        "score": .9, "x1": .1, "y1": .2, "x2": .4, "y2": .6,
        "embedding": embedding().hex(),
    }
    for key, invalid in (("score", True), ("x1", "0.1")):
        malformed = {**row, key: invalid}
        with pytest.raises(photo_faces._ChildProtocolError):  # noqa: SLF001
            photo_faces._decode_child_payload(  # noqa: SLF001
                photo_faces.json.dumps({"ok": [malformed]}).encode(),
            )

    nonunit = np.zeros(128, dtype="<f4")
    nonunit[0] = 2
    with pytest.raises(photo_faces._ChildProtocolError):  # noqa: SLF001
        photo_faces._decode_child_payload(  # noqa: SLF001
            photo_faces.json.dumps({"ok": [{**row, "embedding": nonunit.tobytes().hex()}]}).encode(),
        )

    almost_unit = np.zeros(128, dtype="<f4")
    almost_unit[0] = 1.0005
    decoded = photo_faces._decode_child_payload(  # noqa: SLF001
        photo_faces.json.dumps({"ok": [{**row, "embedding": almost_unit.tobytes().hex()}]}).encode(),
    )
    assert np.linalg.norm(photo_faces.vector(decoded[0].embedding)) == pytest.approx(1.0)


def test_face_child_crash_circuit_requires_retry_or_model_generation_change(monkeypatch) -> None:
    processes: list[_ScriptedProcess] = []

    def popen(*_args, **_kwargs):  # noqa: ANN202
        behavior = ((lambda child, _request: child.exit()) if len(processes) < 2 else
                    (lambda child, _request: child.respond(photo_faces._child_payload(rows=[]))))  # noqa: SLF001
        process = _ScriptedProcess(behavior)
        processes.append(process)
        return process

    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    client = photo_faces._FaceProcess()  # noqa: SLF001
    with pytest.raises(photo_faces.AnalysisFailed):
        client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("old",))
    with pytest.raises(photo_faces.ModelUnavailable):
        client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("old",))
    assert len(processes) == 2, "an open circuit must not keep starting a corrupt model"
    assert client.analyze(
        b"preview", backfill=False, cancel=threading.Event(), generation=("replacement",),
    ) == []
    assert len(processes) == 3
    client.close()


def test_model_verification_failure_opens_cross_job_circuit_until_explicit_retry(monkeypatch) -> None:
    now = [10.0]
    processes: list[_ScriptedProcess] = []

    def popen(*_args, **_kwargs):  # noqa: ANN202
        payload = (photo_faces._child_payload("model_unavailable") if len(processes) < 2  # noqa: SLF001
                   else photo_faces._child_payload(rows=[]))  # noqa: SLF001
        process = _ScriptedProcess(lambda child, _request: child.respond(payload))
        processes.append(process)
        return process

    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    client = photo_faces._FaceProcess(clock=lambda: now[0])  # noqa: SLF001
    with pytest.raises(photo_faces.ModelUnavailable):
        client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("same",))
    now[0] += photo_faces.CRASH_BACKOFF_SECONDS + 1
    with pytest.raises(photo_faces.AnalysisCircuitOpen):
        client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("same",))
    with pytest.raises(photo_faces.ModelUnavailable):
        client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("same",))
    assert len(processes) == 2, "a corrupt ready-marker generation must not start once per queued photo"
    client.retry()
    assert client.analyze(b"preview", backfill=False, cancel=threading.Event(), generation=("same",)) == []
    assert len(processes) == 3
    client.close()


def test_resident_child_cpu_limit_rearms_from_cumulative_usage(monkeypatch) -> None:
    limits: list[tuple[int, int]] = []
    usage = type("Usage", (), {"ru_utime": 4.25, "ru_stime": 0.5})()
    monkeypatch.setattr(photo_faces.resource, "getrusage", lambda _who: usage)
    monkeypatch.setattr(
        photo_faces.resource, "getrlimit",
        lambda _kind: (10, photo_faces.resource.RLIM_INFINITY),
    )
    monkeypatch.setattr(photo_faces.resource, "setrlimit", lambda _kind, value: limits.append(value))
    assert photo_faces._rearm_child_cpu_limit() is True  # noqa: SLF001
    assert limits == [(35, photo_faces.resource.RLIM_INFINITY)]

    monkeypatch.setattr(photo_faces.resource, "getrlimit", lambda _kind: (10, 20))
    assert photo_faces._rearm_child_cpu_limit() is False  # noqa: SLF001


def test_face_child_hang_times_out_then_the_next_request_restarts(monkeypatch) -> None:
    processes: list[_ScriptedProcess] = []

    def popen(*_args, **_kwargs):  # noqa: ANN202
        behavior = ((lambda _child, _request: None) if not processes else
                    (lambda child, _request: child.respond(photo_faces._child_payload(rows=[]))))  # noqa: SLF001
        process = _ScriptedProcess(behavior)
        processes.append(process)
        return process

    monkeypatch.setattr(photo_faces, "CHILD_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    client = photo_faces._FaceProcess()  # noqa: SLF001
    with pytest.raises(photo_faces.AnalysisTimeout):
        client.analyze(b"preview", backfill=False, cancel=threading.Event())
    assert processes[0].terminated is True
    assert client._io_threads == ()  # noqa: SLF001
    client.retry()
    assert client.analyze(b"preview", backfill=False, cancel=threading.Event()) == []
    assert client.starts == 2
    client.close()


def test_face_child_reaps_after_idle_and_rejects_corrupt_request_frames(monkeypatch) -> None:
    now = [10.0]
    processes: list[_ScriptedProcess] = []

    def popen(*_args, **_kwargs):  # noqa: ANN202
        process = _ScriptedProcess(lambda child, _request: child.respond(photo_faces._child_payload(rows=[])))  # noqa: SLF001
        processes.append(process)
        return process

    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    client = photo_faces._FaceProcess(clock=lambda: now[0])  # noqa: SLF001
    assert client.analyze(b"preview", backfill=True, cancel=threading.Event()) == []
    assert signal.SIGSTOP in processes[0].signals and signal.SIGCONT in processes[0].signals
    now[0] += photo_faces.CHILD_IDLE_SECONDS + 1
    client.reap_idle()
    assert processes[0].terminated is True
    oversized = BytesIO(photo_faces.FRAME_HEADER.pack(photo_faces.CHILD_REQUEST_BYTES + 1))
    with pytest.raises(photo_faces._ChildProtocolError):  # noqa: SLF001
        photo_faces._read_frame(oversized, photo_faces.CHILD_REQUEST_BYTES)  # noqa: SLF001


def test_evicted_face_preview_is_rebuilt_from_hash_bound_source(db_factory, monkeypatch) -> None:  # noqa: ANN001
    source_sha256 = "a" * 64
    with db_factory() as db:
        item = photo("photo-owner")
        db.add_all([make_user(OWNER.id, role="admin"), item])
        for width in photo_faces.photo_derivatives.WIDTHS:
            db.add(PhotoDerivative(
                id=f"derivative-{width}", photo_id=item.id, width=width,
                source_key=photo_faces.photo_derivatives.source_key(source_sha256, width), state="ready", bytes=10,
            ))
        db.commit()

        def rebuild(_root, _relative, expected):  # noqa: ANN001,ANN202
            assert expected == source_sha256
            products = {}
            for width in photo_faces.photo_derivatives.WIDTHS:
                key = photo_faces.photo_derivatives.source_key(expected, width)
                path = photo_faces.photo_derivatives.derivative_path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                data = b"preview" if width == 1200 else b"cache"
                path.write_bytes(data)
                products[width] = (key, len(data))
            return products, photo_faces.photo_files.PhotoFingerprint(expected, 100, 1)

        monkeypatch.setattr(photo_faces.photo_files, "build_derivatives", rebuild)
        assert photo_faces._preview(db, item.id, source_sha256) == b"preview"  # noqa: SLF001
        assert db.get(PhotoDerivative, "derivative-1200").state == "ready"
