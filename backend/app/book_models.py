"""Durable catalog and member state for the standalone Books surface."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class BookItem(Base):
    __tablename__ = "book_items"
    __table_args__ = (
        Index("ix_book_items_visible_created", "visibility", "created_at", "id"),
        Index("ix_book_items_series", "series_id", "volume_number", "title", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # A server-generated filename below <data_dir>/books; never a caller-provided path.
    storage_key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    format: Mapped[str] = mapped_column(String(8), nullable=False)  # epub | pdf | cbz | cbr
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # ebook | manga
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    genres: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    series_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    reading_direction: Mapped[str] = mapped_column(String(3), nullable=False, default="ltr")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private", index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BookState(Base):
    __tablename__ = "book_states"
    __table_args__ = (UniqueConstraint("user_id", "book_id", name="uq_book_state_user_book"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    unit_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offset: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reading_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A favorite/list edit is not a reading checkpoint and must not enter Continue reading.
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BookShelf(Base):
    __tablename__ = "book_shelves"
    __table_args__ = (UniqueConstraint("owner_user_id", "name_key", name="uq_book_shelf_owner_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BookShelfMembership(Base):
    __tablename__ = "book_shelf_memberships"
    __table_args__ = (
        UniqueConstraint("shelf_id", "book_id", name="uq_book_shelf_book"),
        Index("ix_book_shelf_memberships_shelf_position", "shelf_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shelf_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
