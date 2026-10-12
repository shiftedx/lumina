"""Subtitle job routes: subtitle jobs, enrichment job polling and bulk enrichment.

Every item lookup goes through ``visible_item_or_404``: a private item is 404 for everyone else,
admins included. Flat app routes (not include_router), like media_tools, for the route-walking checks.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi import Path as PathParam
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    SUBTITLE_TRACK_ID_PATTERN,
    EnrichmentBulkRequest,
    EnrichmentBulkResponse,
    EnrichmentJob,
    MediaSegmentList,
    MediaSegmentsUpdate,
    MuteRange,
    SubtitleTranslateRequest,
)
from app.models import AsrJob, LibraryItem, User
from app.routers import visible_item_or_404
from app.security import MEMBER_JOB, get_admin_user, get_current_user
from app.services import enrichment, media_segments, profanity
from app.services.library import LibraryService
from app.services.local_asr import AsrBusyError
from app.services.transcripts import TranscriptError, to_iso639_2

TRACK_ID = PathParam(pattern=SUBTITLE_TRACK_ID_PATTERN)
TRACK_NOT_FOUND = "Subtitle track not found"


def _start(db: Session, item_id: str, kind: str, params: dict | None, user: User, response: Response) -> EnrichmentJob:
    try:
        job, created = enrichment.request_job(db, item_id, kind, params, user.id)
    except enrichment.EnrichmentUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AsrBusyError as exc:
        raise HTTPException(status_code=429, detail="Too many enrichment jobs in progress; try again shortly") from exc
    response.status_code = 202 if created else 200
    return enrichment.serialize(job)


def _require_track(db: Session, item: LibraryItem, track_id: str) -> None:
    try:
        found = enrichment.track_exists(db, item, track_id)
    except (FileNotFoundError, TranscriptError):
        found = False
    if not found:
        raise HTTPException(status_code=404, detail=TRACK_NOT_FOUND)


def generate_track(
    item_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentJob:
    item = visible_item_or_404(db, item_id, current_user)
    return _start(db, item.id, "asr", None, current_user, response)


def sync_track(
    item_id: str,
    response: Response,
    track_id: str = TRACK_ID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentJob:
    item = visible_item_or_404(db, item_id, current_user)
    _require_track(db, item, track_id)
    return _start(db, item.id, "sync", {"track_id": track_id}, current_user, response)


def translate_track(
    item_id: str,
    body: SubtitleTranslateRequest,
    response: Response,
    track_id: str = TRACK_ID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentJob:
    item = visible_item_or_404(db, item_id, current_user)
    target = to_iso639_2(body.target_language)
    if target is None:
        raise HTTPException(status_code=422, detail="Unknown target language")
    _require_track(db, item, track_id)
    return _start(db, item.id, "translate", {"track_id": track_id, "target_language": target}, current_user, response)


def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentJob:
    job = db.get(AsrJob, job_id)
    if job is None or LibraryService(db).get_item(job.library_item_id, current_user) is None:
        raise HTTPException(status_code=404, detail="Enrichment job not found")
    return enrichment.serialize(job)


def bulk_enrich(
    body: EnrichmentBulkRequest,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentBulkResponse:
    try:
        item_ids = enrichment.bulk_item_ids(db, title_id=body.title_id, root_id=body.root_id)
        queued, skipped = enrichment.queue_bulk(db, item_ids, list(body.kinds), admin.id)
    except enrichment.EnrichmentUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (enrichment.EnrichmentError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return EnrichmentBulkResponse(queued=queued, skipped=skipped)


def get_segments(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MediaSegmentList:
    item = visible_item_or_404(db, item_id, current_user)
    return MediaSegmentList(item_id=item.id, segments=media_segments.segments_for(db, item.id))


def put_segments(
    item_id: str,
    body: MediaSegmentsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MediaSegmentList:
    item = visible_item_or_404(db, item_id, current_user)
    if not LibraryService.can_manage(item, current_user):
        raise HTTPException(status_code=403, detail="Only the item's owner or an admin can edit segments")
    try:
        segments = media_segments.save_user_segments(db, item.id, body.segments)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Library item media is not available") from exc
    return MediaSegmentList(item_id=item.id, segments=segments)


def detect_segments(
    item_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> EnrichmentJob:
    item = visible_item_or_404(db, item_id, current_user)
    return _start(db, item.id, "segments", {}, current_user, response)


def get_mute_ranges(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[MuteRange]:
    item = visible_item_or_404(db, item_id, current_user)
    return profanity.item_mute_ranges(db, item.id, current_user)


def register(app: FastAPI) -> None:
    item = "/api/library/{item_id}"
    app.post(item + "/subtitle-tracks/generate", response_model=EnrichmentJob, status_code=202, dependencies=MEMBER_JOB)(generate_track)
    app.post(item + "/subtitle-tracks/{track_id}/sync", response_model=EnrichmentJob, status_code=202, dependencies=MEMBER_JOB)(sync_track)
    app.post(item + "/subtitle-tracks/{track_id}/translate", response_model=EnrichmentJob, status_code=202, dependencies=MEMBER_JOB)(translate_track)
    app.get("/api/enrichment/jobs/{job_id}", response_model=EnrichmentJob)(get_job)
    app.post("/api/admin/enrichment/bulk", response_model=EnrichmentBulkResponse, status_code=202)(bulk_enrich)
    app.get(item + "/segments", response_model=MediaSegmentList)(get_segments)
    app.put(item + "/segments", response_model=MediaSegmentList)(put_segments)
    app.post(item + "/segments/detect", response_model=EnrichmentJob, status_code=202, dependencies=MEMBER_JOB)(detect_segments)
    app.get(item + "/mute-ranges", response_model=list[MuteRange])(get_mute_ranges)
