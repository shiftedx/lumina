"""Local summary API. Access mirrors the library item's visibility; 404 hides private items."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import visible_item_or_404
from app.models import Summary, Transcript, User
from app.security import MEMBER_JOB, get_current_user
from app.services import model_endpoints
from app.services.enrichment import FEATURE_KEYS
from app.services.local_ai import effective_config
from app.services.summaries import SummaryBusyError, request_summary, state_of
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService


class SummaryRequest(BaseModel):
    transcript_id: str | None = Field(default=None, max_length=36)


class SummaryResponse(BaseModel):
    id: str
    library_item_id: str
    transcript_id: str
    transcript_revision: int
    model_id: str
    state: str  # queued | running | succeeded | failed | canceled | interrupted
    generated_locally: bool = True
    overview: str | None
    key_points: list[dict]
    chapters: list[dict]
    dropped_points: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


def _serialize(summary: Summary) -> SummaryResponse:
    state = state_of(summary)  # refreshes a row whose worker just finished: read its fields after, not before
    return SummaryResponse.model_validate(summary, from_attributes=True).model_copy(update={"state": state})


def create_summary(
    item_id: str,
    payload: SummaryRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SummaryResponse:
    visible_item_or_404(db, item_id, current_user)
    config = effective_config(YtDlpService(db).get_app_settings())
    if not config.enabled:
        raise HTTPException(status_code=409, detail="Local AI is not configured")
    query = db.query(Transcript).filter(Transcript.library_item_id == item_id)
    if payload.transcript_id:
        query = query.filter(Transcript.id == payload.transcript_id)
    transcript = query.order_by(Transcript.created_at.desc(), Transcript.revision.desc()).first()
    if transcript is None:
        raise HTTPException(status_code=409, detail="No transcript is available to summarize")
    try:
        summary, created = request_summary(db, transcript, config.ai_model, current_user.id)
    except SummaryBusyError as exc:
        raise HTTPException(status_code=429, detail="Too many summaries in progress; try again shortly") from exc
    response.status_code = 202 if created else 200
    return _serialize(summary)


def get_latest_summary(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SummaryResponse:
    visible_item_or_404(db, item_id, current_user)
    summary = (
        db.query(Summary)
        .filter(Summary.library_item_id == item_id, Summary.state == "succeeded")
        .order_by(Summary.completed_at.desc())
        .first()
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="No summary yet")
    return _serialize(summary)


def get_summary(
    summary_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SummaryResponse:
    summary = db.get(Summary, summary_id)
    if summary is None or TranscriptService(db).visible_item(summary.library_item_id, current_user) is None:
        raise HTTPException(status_code=404, detail="Summary not found")
    return _serialize(summary)


class EnrichmentCapabilities(BaseModel):
    """What a member may request; endpoints and models stay admin-only."""

    ai_summaries: bool
    asr: bool
    disabled_features: list[str] = []  # the admin's Off switches for member actions (enrichment.FEATURE_KEYS values)


def get_enrichment_capabilities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentCapabilities:
    record = YtDlpService(db).get_app_settings()
    config = effective_config(record)
    return EnrichmentCapabilities(
        ai_summaries=config.enabled, asr=model_endpoints.speech_choice(record) is not None,
        disabled_features=[key for key in record.ai_features_disabled or [] if key in FEATURE_KEYS.values()],
    )


def register(app: FastAPI) -> None:
    app.get("/api/enrichment", response_model=EnrichmentCapabilities)(get_enrichment_capabilities)
    app.post("/api/library/{item_id}/summaries", response_model=SummaryResponse, status_code=202, dependencies=MEMBER_JOB)(create_summary)
    app.get("/api/library/{item_id}/summary", response_model=SummaryResponse)(get_latest_summary)
    app.get("/api/summaries/{summary_id}", response_model=SummaryResponse)(get_summary)
