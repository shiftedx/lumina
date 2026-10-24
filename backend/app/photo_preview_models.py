"""Durable registry and capacity accounting for photo preview files."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class PhotoPreviewAsset(Base):
    __tablename__ = "photo_preview_assets"
    __table_args__ = (
        Index("ix_photo_preview_assets_reclaim_page", "state", "reservation_id", "source_key"),
    )

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # reserved | ready
    bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoPreviewStorage(Base):
    __tablename__ = "photo_preview_storage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sweep_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
