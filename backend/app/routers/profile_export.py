"""GET /api/me/export: a bounded JSON download of the signed-in member's own portable data.

Aggregates existing per-user tables/services (no new source of truth): settings,
followed channels, watch queue, notes, search history, and playback progress.
Owner-only by construction (every query is scoped to current_user; there is no
user-id path parameter for another account or an admin variant to request
someone else's export). No import path: first release, no legacy data to
round-trip.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LibraryNote, MemberRecommendationSuppression, PlaybackProgress, RemotePlaybackProgress, SourceAutomation, User, utcnow
from app.schemas import SuppressionResponse, UserResponse, UserSettingsResponse
from app.security import get_current_user
from app.services.member_follows import CHANNEL_SOURCE_TYPE
from app.services.reco import CONSTANTS
from app.services.reco.events import RecoEventService
from app.services.search_history import SearchHistoryService
from app.services.user_settings import UserSettingsService
from app.services.users import UserService
from app.services.watch_queue import WatchQueueService

URL = "/api/me/export"

# A flat per-section cap instead of cursor pagination. A household
# member's own data stays well under these in practice; add real paging if a
# single member's history genuinely grows past them.
NOTES_LIMIT = 2000
PLAYBACK_PROGRESS_LIMIT = 5000
FOLLOWS_LIMIT = 2000
RECO_EVENTS_LIMIT = 5000
# Show fewer is x0.2 for 35 days, then 0.4, 0.6, 0.8, and back to normal at 140 days.
# The Settings serializer computes the same date for Settings; share one helper if either ever changes.
FEWER_RECOVERY = timedelta(days=CONSTANTS.fewer_step_days * 4)


class ExportFollow(BaseModel):
    id: str
    label: str
    source_url: str
    active: bool
    auto_download: bool
    created_at: datetime


class ExportQueueEntry(BaseModel):
    id: str
    position: int
    kind: Literal["library", "remote"]
    library_item_id: str | None
    url: str | None
    title: str | None
    uploader: str | None
    duration: int | None


class ExportNote(BaseModel):
    id: str
    item_id: str
    visibility: str
    timestamp_ms: int | None
    body: str
    created_at: datetime
    updated_at: datetime


class ExportSearchHistoryEntry(BaseModel):
    id: str
    query: str
    searched_at: datetime


class ExportPlaybackProgress(BaseModel):
    item_id: str
    position_seconds: int
    duration_seconds: int | None
    completed: bool
    last_watched_at: datetime


class ExportRemotePlaybackProgress(BaseModel):
    source_identity: str
    source_url: str
    title: str | None
    position_seconds: float
    duration_seconds: float | None
    completed: bool
    updated_at: datetime


class ExportRecoEvent(BaseModel):
    at: datetime
    kind: str
    surface: str | None = None
    target_kind: str
    item_key: str
    position: int | None = None
    slot: str | None = None
    reason_code: str | None = None
    fraction: float | None = None


class ProfileExportResponse(BaseModel):
    exported_at: datetime
    user: UserResponse
    settings: UserSettingsResponse
    follows: list[ExportFollow]
    queue: list[ExportQueueEntry]
    notes: list[ExportNote]
    search_history: list[ExportSearchHistoryEntry]
    playback_progress: list[ExportPlaybackProgress]
    remote_playback_progress: list[ExportRemotePlaybackProgress]
    recommendation_events: list[ExportRecoEvent]
    recommendation_feedback: list[SuppressionResponse]


def _queue_entries(db: Session, user: User) -> list[ExportQueueEntry]:
    _, rows = WatchQueueService(db).read(user)
    entries: list[ExportQueueEntry] = []
    for entry, item in rows:
        if entry.library_item_id is not None:
            entries.append(ExportQueueEntry(
                id=entry.id, position=entry.position, kind="library",
                library_item_id=entry.library_item_id, url=item.webpage_url if item else None,
                title=item.title if item else entry.title, uploader=item.uploader if item else entry.uploader,
                duration=item.duration if item else entry.duration,
            ))
        else:
            entries.append(ExportQueueEntry(
                id=entry.id, position=entry.position, kind="remote", library_item_id=None,
                url=entry.source_url, title=entry.title, uploader=entry.uploader, duration=entry.duration,
            ))
    return entries


def _feedback(db: Session, user: User) -> list[SuppressionResponse]:
    """Every suppression of the member, all four scopes; this also fixes the gap that suppressions were never exported."""
    records = (
        db.query(MemberRecommendationSuppression)
        .filter(MemberRecommendationSuppression.user_id == user.id)
        .order_by(MemberRecommendationSuppression.created_at, MemberRecommendationSuppression.id)
        .all()
    )
    feedback = []
    for record in records:
        item = SuppressionResponse.model_validate(record)
        feedback.append(item.model_copy(update={"recovers_at": record.created_at + FEWER_RECOVERY}) if record.scope == "fewer" else item)
    return feedback


def export_my_data(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ProfileExportResponse:
    response.headers["Content-Disposition"] = f'attachment; filename="lumina-export-{re.sub(r"[^A-Za-z0-9._-]", "_", current_user.username)}.json"'
    settings_service = UserSettingsService(db)
    follows = (
        db.query(SourceAutomation)
        .filter(SourceAutomation.user_id == current_user.id, SourceAutomation.source_type == CHANNEL_SOURCE_TYPE)
        .order_by(SourceAutomation.created_at)
        .limit(FOLLOWS_LIMIT)
        .all()
    )
    notes = (
        db.query(LibraryNote)
        .filter(LibraryNote.user_id == current_user.id)
        .order_by(LibraryNote.created_at)
        .limit(NOTES_LIMIT)
        .all()
    )
    playback_progress = (
        db.query(PlaybackProgress)
        .filter(PlaybackProgress.user_id == current_user.id)
        .order_by(PlaybackProgress.last_watched_at.desc())
        .limit(PLAYBACK_PROGRESS_LIMIT)
        .all()
    )
    remote_playback_progress = (
        db.query(RemotePlaybackProgress)
        .filter(RemotePlaybackProgress.user_id == current_user.id, RemotePlaybackProgress.cleared.is_(False))
        .order_by(RemotePlaybackProgress.updated_at.desc())
        .limit(PLAYBACK_PROGRESS_LIMIT)
        .all()
    )
    return ProfileExportResponse(
        exported_at=utcnow(),
        user=UserService(db).serialize_user(current_user),
        settings=settings_service.serialize(settings_service.ensure_for_user(current_user)),
        follows=[
            ExportFollow(id=f.id, label=f.label, source_url=f.source_url, active=f.active, auto_download=f.auto_download, created_at=f.created_at)
            for f in follows
        ],
        queue=_queue_entries(db, current_user),
        notes=[
            ExportNote(id=n.id, item_id=n.item_id, visibility=n.visibility, timestamp_ms=n.timestamp_ms, body=n.body, created_at=n.created_at, updated_at=n.updated_at)
            for n in notes
        ],
        search_history=[
            ExportSearchHistoryEntry(id=r.id, query=r.query, searched_at=r.searched_at)
            for r in SearchHistoryService(db).list_for(current_user)
        ],
        playback_progress=[
            ExportPlaybackProgress(item_id=p.item_id, position_seconds=p.position_seconds, duration_seconds=p.duration_seconds, completed=p.completed, last_watched_at=p.last_watched_at)
            for p in playback_progress
        ],
        remote_playback_progress=[
            ExportRemotePlaybackProgress(source_identity=r.source_identity, source_url=r.source_url, title=r.title, position_seconds=r.position_seconds, duration_seconds=r.duration_seconds, completed=r.completed, updated_at=r.updated_at)
            for r in remote_playback_progress
        ],
        recommendation_events=RecoEventService(db).export(current_user.id, limit=RECO_EVENTS_LIMIT),
        recommendation_feedback=_feedback(db, current_user),
    )


def register(app: FastAPI) -> None:
    app.get(URL, response_model=ProfileExportResponse)(export_my_data)
