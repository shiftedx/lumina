"""Owner-scoped biometric suggestions for Photos.

Embeddings never cross the service boundary.  A manual assignment is a durable
constraint: automatic regrouping may only change rows whose assignment source
is ``automatic``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class PhotoFacePreference(Base):
    __tablename__ = "photo_face_preferences"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoFaceJob(Base):
    __tablename__ = "photo_face_jobs"
    __table_args__ = (
        UniqueConstraint("photo_id", "pipeline_signature", name="uq_photo_face_jobs_photo_pipeline"),
        Index("ix_photo_face_jobs_dispatch", "state", "requested", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    pipeline_signature: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PhotoPerson(Base):
    __tablename__ = "photo_people"
    __table_args__ = (Index("ix_photo_people_owner_updated", "owner_user_id", "hidden", "updated_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    pipeline_signature: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feature_face_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    redirected_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoFace(Base):
    __tablename__ = "photo_faces"
    __table_args__ = (
        Index("ix_photo_faces_photo", "photo_id", "id"),
        Index("ix_photo_faces_person", "person_id", "photo_id", "id"),
        Index("ix_photo_faces_owner_unassigned", "owner_user_id", "person_id", "assignment_source", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    person_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    pipeline_signature: Mapped[str] = mapped_column(String(255), nullable=False)
    detector_score: Mapped[float] = mapped_column(Float, nullable=False)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    x2: Mapped[float] = mapped_column(Float, nullable=False)
    y2: Mapped[float] = mapped_column(Float, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(16), nullable=False, default="automatic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoFaceEdit(Base):
    """Append-only correction receipt; payloads contain ids and labels, never vectors."""

    __tablename__ = "photo_face_edits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
