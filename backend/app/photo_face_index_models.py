"""SQLite-authoritative rows used by the disposable face ANN."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class PhotoFaceIndexVector(Base):
    """An immutable embedding version with a never-reused native-index key."""

    __tablename__ = "photo_face_index_vectors"
    __table_args__ = (
        Index("ix_photo_face_index_vectors_scope", "owner_user_id", "pipeline_signature", "active", "id"),
        Index("ix_photo_face_index_vectors_face", "face_id", "active", "id"),
        Index("ix_photo_face_index_vectors_inactive", "active", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    face_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pipeline_signature: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class PhotoFaceIndexChange(Base):
    """Append-only invalidation; current face rows remain the source of truth."""

    __tablename__ = "photo_face_index_changes"
    __table_args__ = (
        Index("ix_photo_face_index_changes_old_scope", "old_owner_user_id", "old_pipeline_signature", "id"),
        Index("ix_photo_face_index_changes_new_scope", "new_owner_user_id", "new_pipeline_signature", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    face_id: Mapped[str] = mapped_column(String(36), nullable=False)
    old_owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    old_pipeline_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_person_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    new_owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    new_pipeline_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_person_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class PhotoFaceIndexSnapshot(Base):
    """Validated native graph checkpoint; safe to discard and rebuild."""

    __tablename__ = "photo_face_index_snapshots"

    owner_user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pipeline_signature: Mapped[str] = mapped_column(String(255), primary_key=True)
    generation: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    hnsw_m: Mapped[int] = mapped_column(Integer, nullable=False)
    ef_construction: Mapped[int] = mapped_column(Integer, nullable=False)
    ef_search: Mapped[int] = mapped_column(Integer, nullable=False)
    prototype_path: Mapped[str] = mapped_column(Text, nullable=False)
    prototype_size: Mapped[int] = mapped_column(Integer, nullable=False)
    prototype_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    unassigned_path: Mapped[str] = mapped_column(Text, nullable=False)
    unassigned_size: Mapped[int] = mapped_column(Integer, nullable=False)
    unassigned_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
