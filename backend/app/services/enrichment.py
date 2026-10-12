"""Enrichment jobs.

One ``asr_jobs`` table and one bounded worker pool (``local_asr.jobs``, MAX_ACTIVE=4) run every kind:
asr, sync, translate, segments and captions. Interactive requests start at once (429 when the pool
is full). Bulk requests insert ``pending`` rows that ``pump_pending`` admits BULK_ACTIVE at a time,
from the reconcile loop and again whenever a bulk job ends. At startup, orphaned queued/running rows
are re-pended once; a row that was already re-pended fails, so a crashing job cannot crash-loop.
Stored errors are content-free; media, transcript and model text are never logged.
"""

from __future__ import annotations

import dataclasses
import logging
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import session_scope
from app.media_schemas import EnrichmentJob
from app.models import AsrJob, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, Transcript, TranscriptCue, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.services import local_asr, media_segments, model_endpoints, model_supervisor, subtitle_sync, subtitle_tracks, subtitle_translate
from app.services.local_ai import LocalAiError, effective_config
from app.services.media_artifacts import MediaArtifactService
from app.services.media_probe import MediaProbeService, media_tool
from app.services.redaction import redact
from app.services.transcripts import (
    MAX_TRACK_BYTES, TEXT_TRACK_EXTS, Cue, TranscriptError, TranscriptService, normalize_language, parse_caption, to_iso639_2,
)
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

RESTARTED = "restarted"
BULK_ACTIVE = 1  # One bulk job at a time; raise it if an import's backlog drains too slowly
MAX_BULK_ROWS = 5000
BULK_KINDS = frozenset({"asr", "segments", "captions"})
BULK_PARAMS: dict[str, dict | None] = {"asr": None, "segments": {}, "captions": {}}
MODEL_LABELS = {"sync": "sync-v1", "segments": "segments-v1", "captions": "captions-v1"}
# Owner-approved kill switch (ADR 0013): an admin-disabled AI feature blocks its enrichment kind.
FEATURE_KEYS = {"asr": "subtitles_from_speech", "sync": "sync", "translate": "translate"}
IN_FLIGHT = ("pending", "queued", "running")


class EnrichmentError(RuntimeError):
    """Stable, content-free failure reason stored on the job."""


class EnrichmentUnavailable(RuntimeError):
    """The admin endpoint this kind needs is not configured; ``str()`` is the 409 detail code."""


@dataclass(frozen=True)
class JobSpec:
    id: str
    item_id: str
    kind: str
    model_id: str
    params: dict[str, Any]


def _work_asr(spec: JobSpec) -> dict[str, Any]:
    return {"transcript_id": local_asr.transcribe(spec.id, spec.item_id, spec.model_id)}


WORKERS: dict[str, Callable[[JobSpec], dict[str, Any]]] = {"asr": _work_asr}
FAILURES: tuple[type[BaseException], ...] = (
    EnrichmentError, local_asr.AsrError, LocalAiError, TranscriptError, FileNotFoundError,
    subtitle_sync.SyncError, subtitle_translate.TranslationError, media_segments.SegmentError,
)
_bulk: set[str] = set()  # ids admitted by the pump; guarded by local_asr.jobs.lock
_pump_lock = threading.Lock()


def model_for(db: Session, kind: str) -> str:
    """The de-dup key: the configured model for AI kinds, a versioned label otherwise."""
    record = YtDlpService(db).get_app_settings()
    if FEATURE_KEYS.get(kind) in (record.ai_features_disabled or ()):
        raise EnrichmentUnavailable("ai_feature_disabled")
    if kind in MODEL_LABELS:
        return MODEL_LABELS[kind]
    if kind == "asr":
        choice = model_endpoints.speech_choice(record)
        if choice is None:
            raise EnrichmentUnavailable("asr_not_configured")
        return choice.model_id
    config = effective_config(record)
    if not config.enabled:
        raise EnrichmentUnavailable("ai_not_configured")
    return config.ai_model


def _disabled_kinds(db: Session) -> list[str]:
    disabled = YtDlpService(db).get_app_settings().ai_features_disabled or ()
    return [kind for kind, key in FEATURE_KEYS.items() if key in disabled]


def _still_done(db: Session, job: AsrJob) -> bool:
    """Segments are keyed to the file: a succeeded job stops counting once its analysis is gone."""
    if job.kind != "segments" or job.state != "succeeded":
        return True
    current = media_segments.current_analysis(db, job.library_item_id)
    return bool(current and current[2])


def serialize(job: AsrJob) -> EnrichmentJob:
    return EnrichmentJob(
        id=job.id, library_item_id=job.library_item_id, kind=job.kind, state=local_asr.state_of(job),
        transcript_id=job.transcript_id, error=job.error, created_at=job.created_at, completed_at=job.completed_at,
    )


def request_job(db: Session, item_id: str, kind: str, params: dict | None, user_id: str | None) -> tuple[AsrJob, bool]:
    """(job, created). Reuses a succeeded or in-flight job with the same kind, model and params.

    Raises EnrichmentUnavailable (409) or local_asr.AsrBusyError (429).
    """
    model_id = model_for(db, kind)
    existing = (
        db.query(AsrJob)
        .filter(
            AsrJob.library_item_id == item_id, AsrJob.kind == kind, AsrJob.model_id == model_id,
            AsrJob.state.in_(("succeeded", *IN_FLIGHT)),
        )
        .order_by(AsrJob.created_at.desc())
        .all()
    )
    for job in existing:
        same = (job.params or None) == (params or None)
        if same and (job.state in ("succeeded", "pending") or local_asr.jobs.is_active(job.id)) and _still_done(db, job):
            return job, False
    job = local_asr.jobs.start(
        db,
        lambda job_id: AsrJob(
            id=job_id, library_item_id=item_id, kind=kind, params=params, model_id=model_id,
            state="queued", requested_by=user_id,
        ),
        run_job,
        "enrichment_job_request",
    )
    return job, True


def _finish(job_id: str, **fields: Any) -> None:
    with session_scope() as db, write_transaction(db, name="enrichment_job_finish"):
        job = db.get(AsrJob, job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        job.completed_at = utcnow()


def run_job(job_id: str) -> None:
    """Worker thread body for every kind: mark running, dispatch on ``kind``, store the outcome."""
    kind = "enrichment"
    try:
        with session_scope() as db, write_transaction(db, name="enrichment_job_start"):
            job = db.get(AsrJob, job_id)
            if job.kind in _disabled_kinds(db):  # the kill switch also stops jobs admitted before it was flipped
                job.state, job.error, job.completed_at = "canceled", "ai_feature_disabled", utcnow()
                return
            job.state = "running"
            spec = JobSpec(job.id, job.library_item_id, job.kind, job.model_id, dict(job.params or {}))
        kind = spec.kind
        result = WORKERS[spec.kind](spec)
        local_asr.check_canceled(job_id)
        _finish(job_id, state="succeeded", error=None, **result)
    except local_asr.AsrCanceledError:
        _finish(job_id, state="canceled")
    except FAILURES as exc:
        if model_supervisor.supervisor.closed:
            return  # shutdown killed its model server: the row stays running and recover_after_restart re-pends it
        _finish(job_id, state="failed", error=redact(str(exc), paths=True)[:200])  # an OS error names its path
    except Exception as exc:  # noqa: BLE001 - log the type only, never media/transcript/model content
        if model_supervisor.supervisor.closed:
            return
        logger.error("Enrichment job %s (%s) failed: %s", job_id, kind, type(exc).__name__)
        _finish(job_id, state="failed", error="ASR generation failed" if kind == "asr" else "Enrichment job failed")
    finally:
        with local_asr.jobs.lock:
            was_bulk = job_id in _bulk
            _bulk.discard(job_id)
        local_asr.jobs.release(job_id)
        if was_bulk:
            pump_pending()


def pump_pending() -> int:
    """Admit the oldest pending rows while fewer than BULK_ACTIVE bulk jobs run; returns how many started."""
    if not _pump_lock.acquire(blocking=False):
        # Another pump is admitting. A bulk job that ends while this lock is held waits for the next
        # reconcile tick (<= reconcile_interval_seconds); make the pump re-check on release if backlogs stall.
        return 0
    started = 0
    try:
        with session_scope() as db:
            if disabled := _disabled_kinds(db):
                with write_transaction(db, name="enrichment_cancel_disabled"):
                    db.query(AsrJob).filter(AsrJob.state == "pending", AsrJob.kind.in_(disabled)).update(
                        {"state": "canceled", "error": "ai_feature_disabled", "completed_at": utcnow()}, synchronize_session=False,
                    )
        while True:
            with local_asr.jobs.lock:
                if len(_bulk) >= BULK_ACTIVE or model_supervisor.supervisor.closed:  # shutting down: pending rows wait for the next start
                    return started
            with session_scope() as db:
                job = db.query(AsrJob).filter(AsrJob.state == "pending").order_by(AsrJob.created_at, AsrJob.id).first()
                if job is None or not local_asr.jobs.admit(job.id):
                    return started
                job_id = job.id
                with local_asr.jobs.lock:
                    _bulk.add(job_id)
                admitted = False
                try:
                    with write_transaction(db, name="enrichment_job_admit"):
                        # Conditional so a cancel_pending that raced this pass wins.
                        updated = bool(
                            db.query(AsrJob).filter(AsrJob.id == job_id, AsrJob.state == "pending")
                            .update({"state": "queued"}, synchronize_session=False)
                        )
                        if updated:
                            queue_after_commit(db, lambda: threading.Thread(target=run_job, args=(job_id,), daemon=True).start())
                    admitted = updated  # only once the commit succeeded; a failed commit releases the slot below
                finally:
                    if not admitted:
                        with local_asr.jobs.lock:
                            _bulk.discard(job_id)
                        local_asr.jobs.release(job_id)
            if admitted:
                started += 1
    finally:
        _pump_lock.release()


def recover_after_restart() -> tuple[int, int]:
    """Startup only, before any worker runs: re-pend orphaned rows once, fail rows that were re-pended already."""
    repended = failed = 0
    with session_scope() as db, write_transaction(db, name="enrichment_recover"):
        for job in db.query(AsrJob).filter(AsrJob.state.in_(("queued", "running"))).all():
            if job.error == RESTARTED:
                job.state, job.error, job.completed_at = "failed", "Interrupted by two restarts", utcnow()
                failed += 1
            else:
                job.state, job.error = "pending", RESTARTED
                repended += 1
    return repended, failed


def cancel_pending(job_id: str) -> bool:
    with session_scope() as db, write_transaction(db, name="enrichment_cancel_pending"):
        job = db.get(AsrJob, job_id)
        if job is None or job.state != "pending":
            return False
        job.state, job.completed_at = "canceled", utcnow()
    return True


def bulk_item_ids(db: Session, *, title_id: str | None = None, root_id: str | None = None) -> list[str]:
    """Version items (no extras, not missing) under a title (all descendants) or in a storage root."""
    query = db.query(LibraryItem.id)
    if title_id is not None:
        ids, frontier = {title_id}, [title_id]
        while frontier:
            children = [
                child for child, in db.query(MediaTitle.id).filter(
                    or_(MediaTitle.parent_id.in_(frontier), MediaTitle.boxset_id.in_(frontier))
                ).all()
            ]
            frontier = [child for child in children if child not in ids]
            ids.update(frontier)
        query = query.filter(LibraryItem.title_id.in_(ids))
    else:
        query = (
            query.join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .filter(MediaArtifact.root_id == root_id)
        )
    rows = query.filter(LibraryItem.extra_type.is_(None), LibraryItem.status != "missing").limit(MAX_BULK_ROWS + 1).all()
    return [item_id for item_id, in rows]


def queue_bulk(db: Session, item_ids: list[str], kinds: list[str], requested_by: str | None) -> tuple[int, int]:
    """Insert ``pending`` rows for every (item, kind) not already done or in flight; returns (queued, skipped).

    Raises ValueError for kinds that need a track (sync, translate), EnrichmentError("too_many_rows")
    past MAX_BULK_ROWS, and EnrichmentUnavailable when an AI kind is not configured.
    """
    if not set(kinds) <= BULK_KINDS:
        raise ValueError("Bulk enrichment supports asr, segments and captions")
    models = {kind: model_for(db, kind) for kind in kinds}
    wanted = [(item_id, kind) for item_id in item_ids for kind in kinds]
    if len(wanted) > MAX_BULK_ROWS:
        raise EnrichmentError("too_many_rows")
    done = {
        (job.library_item_id, job.kind, job.model_id)
        for job in db.query(AsrJob).filter(
            AsrJob.library_item_id.in_(item_ids), AsrJob.kind.in_(kinds), AsrJob.state.in_(("succeeded", *IN_FLIGHT)),
        ).all()
        if _still_done(db, job)
    }
    rows = [
        AsrJob(
            id=str(uuid.uuid4()), library_item_id=item_id, kind=kind, params=BULK_PARAMS[kind],
            model_id=models[kind], state="pending", requested_by=requested_by,
        )
        for item_id, kind in wanted
        if (item_id, kind, models[kind]) not in done
    ]
    if rows:
        with write_transaction(db, name="enrichment_bulk"):
            db.add_all(rows)
    return len(rows), len(wanted) - len(rows)


@dataclass(frozen=True)
class TrackSource:
    cues: list[Cue]
    language: str
    derived_from: str  # a transcript id, "sidecar:{filename}" or "stream:{index}"


def _item(db: Session, item_id: str) -> LibraryItem:
    item = db.get(LibraryItem, item_id)
    if item is None:
        raise EnrichmentError("Library item no longer exists")
    return item


def text_streams(db: Session, item: LibraryItem) -> list[dict]:
    found = MediaArtifactService(db).artifact_for(item.id)
    streams = ((found[0].probe or {}).get("streams") if found else None) or []
    return [
        stream for stream in streams
        if isinstance(stream, dict) and stream.get("type") == "subtitle"
        and stream.get("codec") in subtitle_tracks.TEXT_CODECS and isinstance(stream.get("index"), int)
    ]


def text_stream(db: Session, item: LibraryItem, index: int) -> dict | None:
    return next((stream for stream in text_streams(db, item) if stream["index"] == index), None)


def _sidecar(db: Session, item: LibraryItem, n: int) -> tuple[dict, Path] | None:
    """The nth listed sidecar (``s:{n}``, F-4) when it is a plain SRT/VTT sibling of the media file."""
    entries = subtitle_tracks.sidecars(item)
    entry = entries[n] if n < len(entries) else None
    if entry is None:
        return None
    filename = entry["filename"]
    if entry.get("format") not in TEXT_TRACK_EXTS:
        return None  # ASS/SSA keep their styling path in the player; they are not synced or translated
    media, _root = MediaArtifactService(db).locate(item)
    path = media.parent / filename
    if path.resolve().parent != media.resolve().parent or not path.resolve().is_file():
        return None  # a symlink out of the media folder is not a sidecar
    return entry, path


def _mirror(db: Session, item_id: str, derived_from: str) -> Transcript | None:
    """The ingested copy of a sidecar or stream; a synced/translated row with the same ``derived_from`` is not one."""
    return (
        db.query(Transcript)
        .filter(
            Transcript.library_item_id == item_id, Transcript.source_kind == "source_caption",
            Transcript.derived_from == derived_from,
        )
        .order_by(Transcript.created_at.desc())
        .first()
    )


def _extract_stream(db: Session, item: LibraryItem, stream_index: int) -> bytes:
    """UTF-8 WebVTT of one embedded text stream through the cached extraction (``vtt_body``)."""
    artifacts = MediaArtifactService(db)
    facts = MediaProbeService(db).facts(item)
    source, root = artifacts.locate(item)
    artifact, _root = artifacts.artifact_for(item.id)
    ffmpeg = media_tool(db, "ffmpeg")
    if not ffmpeg:
        raise EnrichmentError("ffmpeg is not available")
    try:
        body = subtitle_tracks.vtt_body(
            f"e:{stream_index}", facts=facts, sidecar_entries=[], source=source, root=root, artifact_id=artifact.id,
            fingerprint=(artifact.probe or {}).get("fingerprint") or "unknown", ffmpeg=ffmpeg,
        )
    except (LookupError, subtitle_tracks.SubtitleError) as exc:
        raise EnrichmentError("Subtitle stream could not be read") from exc
    return body.encode()


def track_exists(db: Session, item: LibraryItem, track_id: str) -> bool:
    """Cheap check for routes: a text track of this item that sync/translate can read (never ``i:``)."""
    prefix, _, value = track_id.partition(":")
    if prefix == "t":
        transcript = db.get(Transcript, value)
        return transcript is not None and transcript.library_item_id == item.id
    if prefix == "s":
        return _sidecar(db, item, int(value)) is not None
    if prefix == "e":
        return text_stream(db, item, int(value)) is not None
    return False


def resolve_track(db: Session, item: LibraryItem, track_id: str) -> TrackSource | None:
    """The cues behind a track id; ``s:`` and ``e:`` prefer their ingested mirror, else read the source."""
    prefix, _, value = track_id.partition(":")
    service = TranscriptService(db)
    if prefix == "t":
        transcript = db.get(Transcript, value)
        if transcript is None or transcript.library_item_id != item.id:
            return None
        return TrackSource(service.cue_tuples(transcript.id), transcript.language, transcript.id)
    if prefix == "s":
        found = _sidecar(db, item, int(value))
        if found is None:
            return None
        entry, path = found
        mirror = _mirror(db, item.id, f"sidecar:{entry['filename']}"[:80])  # the mirror gives results a t: parent
        if mirror is not None:
            return TrackSource(service.cue_tuples(mirror.id), mirror.language, mirror.id)
        with path.open("rb") as handle:
            data = handle.read(MAX_TRACK_BYTES + 1)
        return TrackSource(parse_caption(data, entry["format"]), normalize_language(entry.get("language") or "und"), f"sidecar:{entry['filename']}")
    if prefix == "e":
        index = int(value)
        mirror = _mirror(db, item.id, f"stream:{index}")
        if mirror is not None:
            return TrackSource(service.cue_tuples(mirror.id), mirror.language, mirror.id)
        stream = text_stream(db, item, index)
        if stream is None:
            return None
        return TrackSource(parse_caption(_extract_stream(db, item, index), "vtt"), normalize_language(stream.get("language") or "und"), f"stream:{index}")
    return None


def _duration_ms(db: Session, item: LibraryItem) -> int:
    seconds = item.duration or MediaProbeService(db).facts(item).get("duration")
    if not seconds:
        raise EnrichmentError("Media duration is unknown")
    return round(seconds * 1000)


def _asr_reference(db: Session, item_id: str, language: str) -> tuple[list[tuple[int, int, str]] | None, list[tuple[int, int]]]:
    """(anchor words of a same-language ASR transcript, speech intervals of any ASR transcript)."""
    rows = (
        db.query(Transcript)
        .filter(Transcript.library_item_id == item_id, Transcript.source_kind == "asr")
        .order_by(Transcript.created_at.desc())
        .all()
    )
    if not rows:
        return None, []
    wanted = to_iso639_2(language)
    same = next((row for row in rows if wanted and to_iso639_2(row.language) == wanted), None)
    cues = db.query(TranscriptCue).filter(TranscriptCue.transcript_id == (same or rows[0]).id).order_by(TranscriptCue.ordinal).all()
    words = [(word[0], word[1], word[2]) for cue in cues for word in (cue.words or [])] if same else []
    return words or None, [(cue.start_ms, cue.end_ms) for cue in cues]


def _vad_speech(ffmpeg: str, media: Path, duration_ms: int) -> list[tuple[int, int]]:
    try:
        result = subprocess.run(
            local_asr.vad_command(ffmpeg, media), capture_output=True, text=True, errors="replace",
            timeout=local_asr.EXTRACT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EnrichmentError("Speech detection timed out") from exc
    if result.returncode != 0:
        raise EnrichmentError("Speech detection failed")
    return subtitle_sync.speech_from_silencedetect(result.stderr, duration_ms)


def _work_sync(spec: JobSpec) -> dict[str, Any]:
    vad: tuple[str, Path, int] | None = None
    with session_scope() as db:
        item = _item(db, spec.item_id)
        source = resolve_track(db, item, spec.params["track_id"])
        if source is None:
            raise EnrichmentError("Subtitle track no longer exists")
        words, speech = _asr_reference(db, item.id, source.language)
        if not speech:
            ffmpeg = media_tool(db, "ffmpeg")
            if not ffmpeg:
                raise EnrichmentError("ffmpeg is not available")
            media, _root = MediaArtifactService(db).locate(item)
            vad = (ffmpeg, media, _duration_ms(db, item))
    if vad is not None:
        speech = _vad_speech(*vad)
    local_asr.check_canceled(spec.id)
    cues = subtitle_sync.sync(source.cues, speech=speech, words=words)
    with session_scope() as db:
        transcript = TranscriptService(db).store(
            spec.item_id, language=source.language, source_kind="synced", cues=cues,
            model_label=spec.model_id, derived_from=source.derived_from,
        )
    return {"transcript_id": transcript.id}


def _work_translate(spec: JobSpec) -> dict[str, Any]:
    with session_scope() as db:
        item = _item(db, spec.item_id)
        source = resolve_track(db, item, spec.params["track_id"])
        if source is None:
            raise EnrichmentError("Subtitle track no longer exists")
        # Running jobs keep the model they were requested with.
        config = dataclasses.replace(effective_config(YtDlpService(db).get_app_settings()), ai_model=spec.model_id)
    target = spec.params["target_language"]
    cues = subtitle_translate.translate(config, source.cues, target, check_canceled=lambda: local_asr.check_canceled(spec.id))
    with session_scope() as db:
        transcript = TranscriptService(db).store(
            spec.item_id, language=target, source_kind="translated", cues=cues,
            model_label=spec.model_id, derived_from=source.derived_from,
        )
    return {"transcript_id": transcript.id}


def _work_captions(spec: JobSpec) -> dict[str, Any]:
    """Ingest every embedded text subtitle stream as a ``source_caption`` transcript (``derived_from=stream:N``)."""
    with session_scope() as db:
        item = _item(db, spec.item_id)
        MediaProbeService(db).facts(item)  # refreshes a probe cached before streams were recorded
        streams = text_streams(db, item)
    for stream in streams:
        local_asr.check_canceled(spec.id)
        try:
            with session_scope() as db:
                data = _extract_stream(db, _item(db, spec.item_id), stream["index"])
        except EnrichmentError:
            continue  # an unreadable stream is skipped, not fatal
        try:
            cues = parse_caption(data, "vtt")
        except TranscriptError:
            continue  # an empty or malformed stream is skipped, not fatal
        with session_scope() as db:
            TranscriptService(db).store(
                spec.item_id, language=normalize_language(stream.get("language") or "und"), source_kind="source_caption",
                cues=cues, model_label=spec.model_id, derived_from=f"stream:{stream['index']}",
            )
    return {}


WORKERS.update(sync=_work_sync, translate=_work_translate, captions=_work_captions)


def _work_segments(spec: JobSpec) -> dict[str, Any]:
    media_segments.detect(spec.id, spec.item_id, lambda: local_asr.check_canceled(spec.id))
    return {}


WORKERS["segments"] = _work_segments


def queue_captions_after_import(run_id: str) -> None:
    """After-import hook: a captions job for every titled version item the run saw. Never raises."""
    try:
        with session_scope() as db:
            item_ids = [
                item_id for item_id, in db.query(LibraryItem.id)
                .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
                .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
                .filter(
                    MediaArtifact.last_seen_run_id == run_id, LibraryItem.title_id.is_not(None),
                    LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
                ).all()
            ]
            for start in range(0, len(item_ids), MAX_BULK_ROWS):  # already-captioned items are skipped as done
                queue_bulk(db, item_ids[start:start + MAX_BULK_ROWS], ["captions"], None)
    except Exception as exc:  # noqa: BLE001 - an import never fails because enrichment could not queue
        logger.warning("Could not queue captions after import run %s: %s", run_id, type(exc).__name__)


from app.services import library_import  # noqa: E402  (hook list; imported last to keep the module order readable)

if queue_captions_after_import not in library_import.after_import_hooks:
    library_import.after_import_hooks.append(queue_captions_after_import)
