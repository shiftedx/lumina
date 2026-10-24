"""Durable catalog, personal state and albums for the standalone Photos surface."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class PhotoItem(Base):
    __tablename__ = "photo_items"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "sha256", name="uq_photo_items_owner_sha256"),
        Index("ix_photo_items_owner_added", "owner_user_id", "deleted_at", text("created_at DESC"), text("id DESC")),
        Index("ix_photo_items_shared_added", "visibility", "deleted_at", text("created_at DESC"), text("id DESC")),
        Index("ix_photo_items_catalog_added", "deleted_at", text("created_at DESC"), text("id DESC")),
        Index(
            "ix_photo_items_catalog_captured", "deleted_at",
            text("coalesce(captured_at, created_at) DESC"), text("created_at DESC"), text("id DESC"),
        ),
        Index(
            "ix_photo_items_owner_captured", "owner_user_id", "deleted_at",
            text("coalesce(captured_at, created_at) DESC"), text("created_at DESC"), text("id DESC"),
        ),
        Index(
            "ix_photo_items_shared_captured", "visibility", "deleted_at",
            text("coalesce(captured_at, created_at) DESC"), text("created_at DESC"), text("id DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private", index=True)
    storage_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="managed")  # managed | external
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    root_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_seen_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    missing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    orientation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    captured_at_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    camera_make: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Allowlisted technical facts only. GPS and arbitrary source metadata are never persisted here.
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoState(Base):
    __tablename__ = "photo_states"
    __table_args__ = (UniqueConstraint("user_id", "photo_id", name="uq_photo_states_user_photo"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoDerivative(Base):
    __tablename__ = "photo_derivatives"
    __table_args__ = (UniqueConstraint("photo_id", "width", name="uq_photo_derivatives_photo_width"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="image/webp")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoAlbum(Base):
    __tablename__ = "photo_albums"
    __table_args__ = (UniqueConstraint("owner_user_id", "name_key", name="uq_photo_albums_owner_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PhotoAlbumMembership(Base):
    __tablename__ = "photo_album_memberships"
    __table_args__ = (
        UniqueConstraint("album_id", "photo_id", name="uq_photo_album_memberships_album_photo"),
        Index("ix_photo_album_memberships_album_position", "album_id", "position", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    album_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
