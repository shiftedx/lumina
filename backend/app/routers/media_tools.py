"""Transcript read and ASR API. Access mirrors the library item's visibility; 404 hides private items."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import visible_item_or_404
from app.models import AsrJob, Transcript, TranscriptCue, User
from app.security import MEMBER_JOB, get_current_user
from app.services import enrichment
from app.services.local_asr import AsrBusyError, state_of
from app.services.transcripts import TranscriptService


class TranscriptResponse(BaseModel):
    id: str
    library_item_id: str
    language: str
    source_kind: str
    revision: int
    source_digest: str
    cue_count: int
    model_label: str | None
    created_at: datetime


class CueResponse(BaseModel):
    ordinal: int
    start_ms: int
    end_ms: int
    text: str


class CuePageResponse(BaseModel):
    items: list[CueResponse]
    next_cursor: str | None


def _transcript(transcript: Transcript) -> TranscriptResponse:
    return TranscriptResponse.model_validate(transcript, from_attributes=True)


def _cue(cue: TranscriptCue) -> CueResponse:
    return CueResponse.model_validate(cue, from_attributes=True)


def _visible(service: TranscriptService, transcript_id: str, user: User) -> Transcript:
    transcript = service.visible_transcript(transcript_id, user)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


def list_item_transcripts(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[TranscriptResponse]:
    service = TranscriptService(db)
    visible_item_or_404(db, item_id, current_user)
    return [_transcript(transcript) for transcript in service.list_for_item(item_id)]


def list_cues(
    transcript_id: str,
    cursor: str | None = Query(default=None, pattern=r"^\d{1,6}$"),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> CuePageResponse:
    service = TranscriptService(db)
    transcript = _visible(service, transcript_id, current_user)
    cues = service.cues(transcript.id, after=int(cursor) if cursor else -1, limit=limit)
    more = bool(cues) and cues[-1].ordinal < transcript.cue_count - 1
    return CuePageResponse(items=[_cue(cue) for cue in cues], next_cursor=str(cues[-1].ordinal) if more else None)


def search_cues(
    transcript_id: str,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[CueResponse]:
    service = TranscriptService(db)
    transcript = _visible(service, transcript_id, current_user)
    return [_cue(cue) for cue in service.search(transcript.id, q.strip() or q, limit=limit)]


class AsrJobResponse(BaseModel):
    id: str
    library_item_id: str
    model_id: str
    state: str  # pending | queued | running | succeeded | failed | canceled | interrupted
    transcript_id: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


def _serialize_job(job: AsrJob) -> AsrJobResponse:
    state = state_of(job)  # refreshes a row whose worker just finished: read its fields after, not before
    return AsrJobResponse.model_validate(job, from_attributes=True).model_copy(update={"state": state})


def create_asr_job(
    item_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AsrJobResponse:
    item = visible_item_or_404(db, item_id, current_user)
    try:
        job, created = enrichment.request_job(db, item.id, "asr", None, current_user.id)
    except enrichment.EnrichmentUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AsrBusyError as exc:
        raise HTTPException(status_code=429, detail="Too many transcriptions in progress; try again shortly") from exc
    response.status_code = 202 if created else 200
    return _serialize_job(job)


def get_latest_asr_job(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AsrJobResponse:
    visible_item_or_404(db, item_id, current_user)
    job = (
        db.query(AsrJob)
        .filter(AsrJob.library_item_id == item_id, AsrJob.kind == "asr")
        .order_by(AsrJob.created_at.desc())
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="No transcription job yet")
    return _serialize_job(job)


def register(app: FastAPI) -> None:
    """Flat app routes (not include_router) so route-walking contract checks see them."""
    app.get("/api/library/{item_id}/transcripts", response_model=list[TranscriptResponse])(list_item_transcripts)
    app.get("/api/transcripts/{transcript_id}/cues", response_model=CuePageResponse)(list_cues)
    app.get("/api/transcripts/{transcript_id}/search", response_model=list[CueResponse])(search_cues)
    app.post("/api/library/{item_id}/transcripts/asr", response_model=AsrJobResponse, status_code=202, dependencies=MEMBER_JOB)(create_asr_job)
    app.get("/api/library/{item_id}/transcripts/asr", response_model=AsrJobResponse)(get_latest_asr_job)
