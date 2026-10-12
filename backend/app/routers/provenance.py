"""Source provenance and related context of one library item.

Only stored facts are returned: the canonical public page (never a signed
transport URL or a filesystem path), where the bytes live by storage-root
label, cached probe facts (no probe is run here) and bounded related context
computed after the viewer's visibility filter.
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import visible_item_or_404
from app.models import LibraryItem, LibraryNote, User
from app.security import get_current_user
from app.services.hls_relay_support import derive_provider, public_provider
from app.services.library import EXTERNAL_LIBRARY_ORIGIN, LibraryService
from app.services.media_artifacts import MediaArtifactService

RELATED_LIMIT = 6
FORMAT_KEYS = ("container", "video_codec", "audio_codec", "width", "height")


class RelatedItem(BaseModel):
    id: str
    title: str


class RelatedGroup(BaseModel):
    reason: str  # same_channel | same_series
    name: str
    count: int
    items: list[RelatedItem]


class ProvenanceResponse(BaseModel):
    origin: str  # saved | imported | recording
    provider: str | None
    original_url: str | None
    channel: str | None
    channel_url: str | None
    uploaded_on: date | None
    saved_at: datetime | None
    storage_label: str | None
    storage_mode: str | None  # managed | external
    media_state: str | None  # artifact lifecycle
    file_size: int | None
    format: dict | None
    notes_count: int
    related: list[RelatedGroup]


def public_url(value: object) -> str | None:
    """A shareable page address: http(s) without credentials; anything else is withheld."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    return value.strip()


def _uploaded_on(value: object) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date() if isinstance(value, str) else None
    except ValueError:
        return None


def _related(db: Session, item: LibraryItem, user: User, column, value: str | None, reason: str) -> RelatedGroup | None:
    if not value:
        return None
    query = db.query(LibraryItem).filter(LibraryService.visible_predicate(user), column == value, LibraryItem.id != item.id)
    count = query.with_entities(func.count(LibraryItem.id)).scalar() or 0
    if not count:
        return None
    rows = query.with_entities(LibraryItem.id, LibraryItem.title).order_by(LibraryItem.downloaded_at.desc().nullslast(), LibraryItem.id).limit(RELATED_LIMIT).all()
    return RelatedGroup(reason=reason, name=value, count=count, items=[RelatedItem(id=row.id, title=row.title) for row in rows])


def get_provenance(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> ProvenanceResponse:
    item = visible_item_or_404(db, item_id, current_user)
    meta = item.metadata_json or {}
    imported = item.extractor == EXTERNAL_LIBRARY_ORIGIN
    found = MediaArtifactService(db).artifact_for(item.id)
    artifact, root = found if found else (None, None)
    probe = (artifact.probe or {}) if artifact else {}
    facts = {} if probe.get("error") else {key: probe[key] for key in FORMAT_KEYS if probe.get(key)}
    notes_count = (
        db.query(func.count(LibraryNote.id))
        .filter(LibraryNote.item_id == item.id, or_(LibraryNote.user_id == current_user.id, LibraryNote.visibility == "household"))
        .scalar()
    )
    related = [
        _related(db, item, current_user, LibraryItem.uploader, item.uploader, "same_channel"),
        _related(db, item, current_user, LibraryItem.playlist_name, item.playlist_name, "same_series"),
    ]
    return ProvenanceResponse(
        origin="imported" if imported else "recording" if item.kind == "recording" else "saved",
        provider=None if imported else public_provider(derive_provider({"extractor": item.extractor, "extractor_key": meta.get("extractor_key")})).label,
        original_url=None if imported else public_url(item.webpage_url or meta.get("original_url")),
        channel=item.uploader,
        channel_url=None if imported else public_url(meta.get("channel_url") or meta.get("uploader_url")),
        uploaded_on=_uploaded_on(meta.get("upload_date")),
        saved_at=item.downloaded_at,
        storage_label=root.label if root else None,
        storage_mode=root.mode if root else None,
        media_state=artifact.lifecycle if artifact else None,
        file_size=artifact.size if artifact and artifact.size else item.file_size,
        format=facts or None,
        notes_count=notes_count or 0,
        related=[group for group in related if group],
    )


def register(app: FastAPI) -> None:
    app.get("/api/library/{item_id}/provenance", response_model=ProvenanceResponse)(get_provenance)
