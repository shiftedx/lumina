"""Authenticated, owner-scoped API for local photo face suggestions."""
from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.photo_face_models import PhotoFace, PhotoFaceJob, PhotoFacePreference, PhotoPerson
from app.photo_models import PhotoDerivative, PhotoItem
from app.security import get_current_user
from app.services import model_downloads, photo_derivatives, photo_faces, photos
from app.services.raster_images import image_size


class FaceSettingsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    auto_group: bool = True
    acknowledge_biometric_processing: bool = False


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    hidden: bool | None = None


class PersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    face_ids: list[str] = Field(min_length=1, max_length=100)


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str | None = Field(default=None, max_length=36)


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_person_id: str = Field(max_length=36)


class SplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    face_ids: list[str] = Field(min_length=1, max_length=500)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    photo_ids: list[str] = Field(min_length=1, max_length=100)


def _refuse(error: photo_faces.FaceError) -> HTTPException:
    code = str(error) if str(error) in {
        "person_not_found", "face_not_found", "same_person", "invalid_faces", "invalid_name",
        "face analysis is not enabled",
    } else error.code
    status = 404 if code in {"person_not_found", "face_not_found"} else 409
    return HTTPException(status_code=status, detail=code)


def _settings(db: Session, user: User) -> dict[str, Any]:
    row = db.get(PhotoFacePreference, user.id)
    model = photo_faces.face_model()
    state = model_downloads.manager.status(model)
    return {
        "enabled": bool(row and row.enabled),
        "auto_group": True if row is None else row.auto_group,
        "consented_at": row.consented_at if row else None,
        "model": {
            "id": model.id, "state": state.state, "reason": state.reason,
            "bytes_done": state.bytes_done, "bytes_total": state.bytes_total,
        },
        "jobs": photo_faces.counts(db, user.id),
        "pipeline_signature": photo_faces.PIPELINE_SIGNATURE,
    }


def _person(db: Session, row: PhotoPerson) -> dict[str, Any]:
    return _people_items(db, [row])[0]


def _people_items(db: Session, rows: list[PhotoPerson]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [row.id for row in rows]
    aggregates = {person_id: (int(face_count), int(photo_count)) for person_id, face_count, photo_count in db.execute(
        select(PhotoFace.person_id, func.count(PhotoFace.id), func.count(func.distinct(PhotoFace.photo_id))).join(
            PhotoItem, PhotoItem.id == PhotoFace.photo_id,
        ).where(
            PhotoFace.person_id.in_(ids), PhotoItem.owner_user_id == rows[0].owner_user_id, PhotoItem.deleted_at.is_(None),
        ).group_by(PhotoFace.person_id)
    )}
    feature_ids = {row.feature_face_id for row in rows if row.feature_face_id}
    live_features = {face_id: person_id for face_id, person_id in db.execute(select(
        PhotoFace.id, PhotoFace.person_id,
    ).join(PhotoItem, PhotoItem.id == PhotoFace.photo_id).where(
        PhotoFace.id.in_(feature_ids), PhotoItem.owner_user_id == rows[0].owner_user_id, PhotoItem.deleted_at.is_(None),
    ))} if feature_ids else {}
    fallbacks = {person_id: face_id for person_id, face_id in db.execute(select(
        PhotoFace.person_id, func.min(PhotoFace.id),
    ).join(PhotoItem, PhotoItem.id == PhotoFace.photo_id).where(
        PhotoFace.person_id.in_(ids), PhotoItem.owner_user_id == rows[0].owner_user_id,
        PhotoItem.deleted_at.is_(None),
    ).group_by(PhotoFace.person_id))}
    items = []
    for row in rows:
        feature_id = row.feature_face_id if live_features.get(row.feature_face_id) == row.id else fallbacks.get(row.id)
        items.append({
            "id": row.id, "name": row.name, "photo_count": aggregates.get(row.id, (0, 0))[1],
            "face_count": aggregates.get(row.id, (0, 0))[0],
            "thumbnail_url": f"/api/photo-faces/{feature_id}/image" if feature_id else None,
            "hidden": row.hidden, "revision": row.revision,
        })
    return items


def register(app: FastAPI) -> None:
    @app.get("/api/photo-faces/settings")
    def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _settings(db, user)

    @app.put("/api/photo-faces/settings")
    def put_settings(
        body: FaceSettingsPut, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        current = db.get(PhotoFacePreference, user.id)
        if body.enabled and not (current and (current.enabled or current.consented_at is not None)) and not body.acknowledge_biometric_processing:
            raise HTTPException(status_code=422, detail="biometric_acknowledgement_required")
        photo_faces.set_preference(db, user, enabled=body.enabled, auto_group=body.auto_group)
        return _settings(db, user)

    @app.post("/api/photo-faces/scan")
    def scan_all(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        if not (pref := db.get(PhotoFacePreference, user.id)) or not pref.enabled:
            raise HTTPException(status_code=409, detail="face_analysis_disabled")
        return {"queued": photo_faces.queue_all(db, user, requested=True)}

    @app.post("/api/photo-faces/retry")
    def retry(
        body: RetryRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        queued = 0
        for photo_id in dict.fromkeys(body.photo_ids):
            try:
                photo = photos.owned_photo(db, user, photo_id)
                row = photo_faces.queue_photo(db, photo, requested=True)
            except photos.PhotoNotFound:
                raise HTTPException(status_code=404, detail="photo_not_found") from None
            except photo_faces.FaceError as exc:
                raise _refuse(exc) from None
            queued += row.state == "queued"
        return {"queued": queued}

    @app.delete("/api/photo-faces/data", status_code=204)
    def delete_data(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
        photo_faces.delete_owner_data(db, user)
        return Response(status_code=204)

    @app.get("/api/photo-people")
    def list_people(
        include_hidden: bool = Query(False), limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        query = select(PhotoPerson).where(
            PhotoPerson.owner_user_id == user.id, PhotoPerson.redirected_to_id.is_(None),
        )
        if not include_hidden:
            query = query.where(PhotoPerson.hidden.is_(False))
        rows = list(db.scalars(query.order_by(
            PhotoPerson.name.is_(None), PhotoPerson.name, PhotoPerson.updated_at.desc(), PhotoPerson.id,
        ).offset(offset).limit(limit + 1)))
        items = [item for item in _people_items(db, rows[:limit]) if item["face_count"]]
        return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}

    @app.post("/api/photo-people", status_code=201)
    def create_person(
        body: PersonCreate, user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            row = photo_faces.create_person(db, user, body.face_ids, name=body.name)
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None
        return {"person": _person(db, row)}

    @app.patch("/api/photo-people/{person_id}")
    def patch_person(
        person_id: str, body: PersonPatch, user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            row = photo_faces.rename(
                db, user, person_id,
                name=body.name if "name" in body.model_fields_set else None,
                name_present="name" in body.model_fields_set,
                hidden=body.hidden if "hidden" in body.model_fields_set else None,
            )
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None
        return _person(db, row)

    @app.post("/api/photo-people/{source_id}/merge")
    def merge_person(
        source_id: str, body: MergeRequest, user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            return _person(db, photo_faces.merge(db, user, source_id, body.target_person_id))
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None

    @app.post("/api/photo-people/{person_id}/split")
    def split_person(
        person_id: str, body: SplitRequest, user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            return {"person": _person(db, photo_faces.split(db, user, person_id, body.face_ids))}
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None

    @app.get("/api/photos/{photo_id}/faces")
    def photo_face_list(
        photo_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            photos.owned_photo(db, user, photo_id)
        except photos.PhotoNotFound:
            raise HTTPException(status_code=404, detail="photo_not_found") from None
        rows = list(db.scalars(select(PhotoFace).where(PhotoFace.photo_id == photo_id).order_by(PhotoFace.detector_score.desc(), PhotoFace.id)))
        return {"items": [{
            "id": row.id, "person_id": row.person_id,
            "box": {"x": row.x1, "y": row.y1, "width": row.x2 - row.x1, "height": row.y2 - row.y1},
            "confidence": row.detector_score, "thumbnail_url": f"/api/photo-faces/{row.id}/image",
        } for row in rows]}

    @app.post("/api/photo-faces/{face_id}/assign")
    def assign_face(
        face_id: str, body: AssignRequest, user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        try:
            row = photo_faces.assign(db, user, face_id, body.person_id)
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None
        return {"id": row.id, "person_id": row.person_id, "assignment_source": row.assignment_source}

    @app.get("/api/photo-faces/{face_id}/image")
    def face_image(
        face_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> Response:
        try:
            face = photo_faces.face_for_owner(db, user, face_id)
        except photo_faces.FaceError as exc:
            raise _refuse(exc) from None
        derivative = db.scalar(select(PhotoDerivative).where(
            PhotoDerivative.photo_id == face.photo_id, PhotoDerivative.width == 400, PhotoDerivative.state == "ready",
        ))
        if derivative is None:
            raise HTTPException(status_code=404, detail="face_image_unavailable")
        try:
            import cv2
            data = photo_derivatives.read(derivative.source_key)
            size = image_size(data)
            if (size is None or min(size) < 1 or size[0] > 400 or size[1] > 4000
                    or size[0] * size[1] > 1_600_000):
                raise ValueError
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError
            height, width = image.shape[:2]
            if width > 400 or height > 4000 or width * height > 1_600_000:
                raise ValueError
            x1, y1, x2, y2 = int(face.x1 * width), int(face.y1 * height), int(face.x2 * width), int(face.y2 * height)
            pad = max(x2 - x1, y2 - y1) // 4
            crop = image[max(0, y1 - pad):min(height, y2 + pad), max(0, x1 - pad):min(width, x2 + pad)]
            if not crop.size:
                raise ValueError
            crop_height, crop_width = crop.shape[:2]
            if max(crop_width, crop_height) > 400:
                scale = 400 / max(crop_width, crop_height)
                crop = cv2.resize(
                    crop, (max(1, round(crop_width * scale)), max(1, round(crop_height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 82])
            if not ok or len(encoded) > 512 * 1024:
                raise ValueError
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="face_image_unavailable") from None
        return Response(content=encoded.tobytes(), media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})
