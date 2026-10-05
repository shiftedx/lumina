"""Shared test builders: databases, household members, app settings, and Popular snapshots.

Fixtures wrapping these (``db_factory``, ``api_client``) live in conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AppSettings, User
from app.services.popular_discovery import PopularCategorySnapshot, PopularItem, PopularSnapshot


def memory_session_factory() -> sessionmaker:
    """A fresh in-memory database with the full schema, configured like the app's SessionLocal."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def file_backed_session_factory(*user_ids: str) -> sessionmaker:
    """A factory over conftest's per-test file-backed engine, seeded with active members.

    Use this (not ``memory_session_factory``) when worker threads share the database:
    a StaticPool in-memory engine shares ONE sqlite3 connection across threads, which
    flaked (closed database / lost writes / SIGSEGV) under concurrent use.
    """
    from app import db as db_module

    Base.metadata.create_all(bind=db_module.engine)
    factory = sessionmaker(bind=db_module.engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    with factory.begin() as session:
        session.add_all([make_user(user_id) for user_id in user_ids])
    return factory


def make_user(user_id: str = "member", *, role: str = "viewer", **fields: Any) -> User:
    """An active household member; ``username``/``display_name`` default from the id."""
    fields.setdefault("username", user_id)
    fields.setdefault("display_name", user_id.title())
    fields.setdefault("is_active", True)
    return User(id=user_id, role=role, **fields)


def seed_app_settings(session: Session, **fields: Any) -> AppSettings:
    """Add and commit the singleton AppSettings row; ``fields`` override the defaults."""
    record = AppSettings(
        **{
            "id": 1,
            "temp_root": "/tmp/temp",
            "archive_path": "/tmp/archive.txt",
            "concurrency": 1,
            "ffmpeg_path": None,
            "yt_dlp_defaults": {"ignoreconfig": True},
            "ui_prefs": {},
            **fields,
        }
    )
    session.add(record)
    session.commit()
    return record


def popular_item(item_id: str, categories: tuple[str, ...] = (), **fields: Any) -> PopularItem:
    """A public YouTube Popular item; ``fields`` override the defaults."""
    return PopularItem(
        **{
            "id": item_id, "title": item_id.title(), "uploader": "Creator", "duration": 120, "thumbnail": None,
            "artwork_url": None, "webpage_url": f"https://www.youtube.com/watch?v={item_id}", "view_count": 10,
            "availability": "public", "published_at": None, "source": "youtube", "source_label": "YouTube",
            "capabilities": None, "category_keys": categories,
            **fields,
        }
    )


def popular_snapshot(*items: PopularItem) -> PopularSnapshot:
    """A ready Popular snapshot whose music, cooking, and gaming categories are all healthy."""
    at = datetime(2026, 7, 19, tzinfo=UTC)
    return PopularSnapshot(
        items=items,
        categories=tuple(
            PopularCategorySnapshot(key, key.title(), "ready", None, None) for key in ("music", "cooking", "gaming")
        ),
        state="ready", refreshing=False, stale=False,
        last_success_at=at, refreshed_at=at, next_refresh_at=None, error=None,
    )


class FakeJobs:
    """A job manager stand-in that records staged payloads and dispatched job ids."""

    def __init__(self) -> None:
        self.enqueued: list = []
        self.dispatched: list = []

    def stage_enqueue(self, db, payload, user, **kwargs):  # noqa: ANN001
        self.enqueued.append(payload)
        return type("StagedJob", (), {"id": f"job-{len(self.enqueued)}"})()

    def dispatch_staged(self, job):  # noqa: ANN001
        self.dispatched.append(job.id)
