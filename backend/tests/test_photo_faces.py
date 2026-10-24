"""Public privacy and durable-correction contracts for local photo faces."""
from __future__ import annotations

import math
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


def test_explicit_scan_promotes_failed_backfill_to_requested_work(api_client, db_factory) -> None:  # noqa: ANN001
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


def test_analyzer_downscales_portrait_long_edge_but_leaves_landscape_size() -> None:
    import cv2

    detector_sizes: list[tuple[int, int]] = []

    class Detector:
        def detect(self, image):  # noqa: ANN001,ANN202
            height, width = image.shape[:2]
            row = [width * .1, height * .1, width * .2, height * .2, *([1.0] * 10), .95]
            return None, np.asarray([row], dtype=np.float32)

    class DetectorFactory:
        @staticmethod
        def create(_model, _config, input_size, *_args):  # noqa: ANN001,ANN202
            detector_sizes.append(input_size)
            return Detector()

    class Cv2:
        FaceDetectorYN = DetectorFactory
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
    analyzer.cv2, analyzer.yunet, analyzer.recognizer = Cv2, "yunet", Recognizer()
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


def test_face_child_uses_backend_package_directory_from_any_server_cwd(tmp_path: Path, monkeypatch) -> None:
    created: dict[str, object] = {}
    promoted: list[object] = []

    class Process:
        def __init__(self) -> None:
            self.stdin = BytesIO()
            self.stdout = BytesIO(b"[]")
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: int) -> int:  # noqa: ARG002
            return 0

    def popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        created.update(kwargs)
        return Process()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(photo_faces.subprocess, "Popen", popen)
    monkeypatch.setattr(photo_faces.background_work, "promote", promoted.append)
    assert photo_faces.analyze_in_child(b"preview", backfill=False) == []
    assert created["cwd"] == Path(photo_faces.__file__).resolve().parents[2]
    assert len(promoted) == 1


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
