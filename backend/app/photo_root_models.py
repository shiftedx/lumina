"""Registered external photo sources and their resumable scan ledger."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class PhotoImportSource(Base):
    __tablename__ = "photo_import_sources"
    __table_args__ = (
        UniqueConstraint("root_id", "relative_folder", name="uq_photo_import_source_root_folder"),
        Index("ix_photo_import_sources_scan", "enabled", "scan_requested", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relative_folder: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scan_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    indexed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoImportRun(Base):
    __tablename__ = "photo_import_runs"
    __table_args__ = (Index("ix_photo_import_runs_source_created", "source_id", text("created_at DESC")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    cursor: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
