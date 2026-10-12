"""ASR transcripts, on the on-device speech model when it is ready, else the admin's OpenAI-compatible
endpoint.

``model_endpoints.speech_choice_for_job`` resolves the source once per job (running jobs keep the model
they were requested with); ``model_endpoints.connect`` is then entered fresh for each chunk, so a local
server's lease and the bulk-work lock are held per chunk, never for the whole job. An unset external
endpoint and no local model fails honestly instead of silently skipping or falling back to a cloud service.
Audio is extracted from the library item's own artifact with ffmpeg (16 kHz mono, chunked to bound each
upload), one chunk at a time, and the chunk files are deleted as soon as they are sent. Cancellation kills
the ffmpeg child and stops the chunk loop before its next upload.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.config import settings
from app.db import session_scope
from app.models import LibraryItem
from app.services import model_endpoints
from app.services.local_ai import AiConfig, JobRegistry, LocalAiError, bounded_json_request
from app.services.media_artifacts import MediaArtifactService
from app.services.media_probe import LOCAL_INPUT_ARGS, media_tool
from app.services.transcripts import Cue, TranscriptService, normalize_language
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 600  # 10 minutes; keeps each transcription upload bounded
SAMPLE_RATE = 16_000
EXTRACT_TIMEOUT_SECONDS = 3600  # one bounded ffmpeg pass over the whole source
TRANSCRIBE_TIMEOUT_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ACTIVE = 4  # queued + running ASR jobs in this process; extraction+upload is heavier than chat
MAX_WORD_CHARS = 100



class AsrError(RuntimeError):
    """Stable, content-free failure reason stored on the job."""


class AsrBusyError(RuntimeError):
    pass


class AsrCanceledError(RuntimeError):
    pass


jobs = JobRegistry(MAX_ACTIVE, AsrBusyError)
state_of = jobs.state_of
_processes: dict[str, subprocess.Popen] = {}  # guarded by jobs.lock


def work_dir(job_id: str) -> Path:
    return settings.temp_root / "asr-jobs" / job_id


def ffmpeg_command(ffmpeg: str, source: Path, out_dir: Path) -> list[str]:
    """One bounded pass: drop video, downmix to 16kHz mono, segment into <=CHUNK_SECONDS WAV files."""
    return [
        ffmpeg, "-nostdin", "-v", "error", *LOCAL_INPUT_ARGS, "-i", str(source), "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1",
        str(out_dir / "chunk%04d.wav"),
    ]


def vad_command(ffmpeg: str, source: Path) -> list[str]:
    """The same 16 kHz mono decode as ``ffmpeg_command``, measured by silencedetect (it logs at info level)."""
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", *LOCAL_INPUT_ARGS, "-i", str(source),
        "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-af", "silencedetect=n=-35dB:d=0.3", "-f", "null", "-",
    ]


def cancel(job_id: str) -> bool:
    """Mark a job canceled and kill its ffmpeg child if one is running; True if a job was signaled."""
    if not jobs.cancel(job_id):
        return False
    with jobs.lock:
        process = _processes.get(job_id)
    if process is not None:
        process.terminate()
    return True


def check_canceled(job_id: str) -> None:
    if jobs.is_canceled(job_id):
        raise AsrCanceledError()


def extract_chunks(cmd: list[str], out_dir: Path, job_id: str) -> list[Path]:
    """Run one subprocess to completion or cancellation; never leaves it running behind."""
    out_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with jobs.lock:
        already_canceled = job_id in jobs.canceled
        _processes[job_id] = process
    if already_canceled:
        process.terminate()
    try:
        process.wait(timeout=EXTRACT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AsrError("Audio extraction timed out")
    finally:
        with jobs.lock:
            _processes.pop(job_id, None)
    check_canceled(job_id)
    if process.returncode != 0:
        raise AsrError("Audio extraction failed")
    return sorted(out_dir.glob("chunk*.wav"))


def _post_chunk(config: AiConfig, path: Path, data: dict) -> dict:
    headers = {"Authorization": f"Bearer {config.asr_api_key}"} if config.asr_api_key else {}
    with path.open("rb") as handle:
        return bounded_json_request(
            config.asr_base_url, "POST", "audio/transcriptions", timeout=config.asr_timeout_seconds, label="ASR",
            max_bytes=MAX_RESPONSE_BYTES, data=data, files={"file": (path.name, handle, "audio/wav")}, headers=headers,
        )


def transcribe_chunk(config: AiConfig, path: Path) -> dict:
    """POST one bounded audio chunk for verbose_json with word and segment timestamps.

    An endpoint that rejects ``timestamp_granularities[]`` (HTTP 400) is asked once more without it.
    """
    if not config.asr_available:
        raise AsrError("Local ASR is not configured")
    data = {"model": config.asr_model, "response_format": "verbose_json"}
    try:
        return _post_chunk(config, path, {**data, "timestamp_granularities[]": ["word", "segment"]})
    except LocalAiError as exc:
        if exc.status != 400:
            raise
    return _post_chunk(config, path, data)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _words(raw: object, offset_ms: int) -> list[list]:
    """``[[start_ms, end_ms, word], ...]`` from a verbose_json ``words`` list; malformed entries are dropped."""
    words: list[list] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        start, end, word = entry.get("start"), entry.get("end"), entry.get("word")
        if not (_is_number(start) and _is_number(end) and isinstance(word, str) and word.strip()) or end < start:
            continue
        words.append([offset_ms + round(start * 1000), offset_ms + round(end * 1000), word.strip()[:MAX_WORD_CHARS]])
    return words


def parse_segments(body: dict, offset_ms: int) -> tuple[list[Cue], list[list[list] | None]]:
    """Verbose_json ``segments`` into millisecond cues offset by chunk start, plus each cue's word timings.

    Servers nest ``words`` in each segment or return one top-level list; top-level words go to the
    segment their start falls in.
    """
    segments = body.get("segments")
    if not isinstance(segments, list):
        raise AsrError("ASR endpoint returned no segments")
    loose = _words(body.get("words"), offset_ms)
    cues: list[Cue] = []
    words: list[list[list] | None] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
        if not (_is_number(start) and _is_number(end)):
            continue
        if not isinstance(text, str) or not text.strip() or end < start:
            continue
        cue = (offset_ms + round(start * 1000), offset_ms + round(end * 1000), text.strip())
        own = _words(segment["words"], offset_ms) if "words" in segment else [w for w in loose if cue[0] <= w[0] < cue[1]]
        cues.append(cue)
        words.append(own or None)
    return cues, words


def transcribe(job_id: str, item_id: str, model_id: str) -> str:
    """Extract, transcribe chunk by chunk and store an ``asr`` transcript; returns its id.

    The source is resolved once (running jobs keep the model they were requested with); each chunk then
    runs inside its own ``connect``, so a local server's lease and the bulk-work lock are held per chunk and
    an embedding backfill batch can take its turn between chunks. Raises AsrError/LocalAiError (stored on
    the job) or AsrCanceledError. Temp chunks are always removed.
    """
    directory = work_dir(job_id)
    try:
        with session_scope() as db:
            item = db.get(LibraryItem, item_id)
            if item is None:
                raise AsrError("Library item no longer exists")
            choice = model_endpoints.speech_choice_for_job(YtDlpService(db).get_app_settings(), model_id)
            ffmpeg = media_tool(db, "ffmpeg")
            source, _root = MediaArtifactService(db).locate(item)
        if not ffmpeg:
            raise AsrError("ffmpeg is not available")

        chunks = extract_chunks(ffmpeg_command(ffmpeg, source, directory), directory, job_id)
        cues: list[Cue] = []
        words: list[list[list] | None] = []
        language: str | None = None
        for index, chunk in enumerate(chunks):
            check_canceled(job_id)
            with model_endpoints.connect(choice, wait=True, heavy=True) as config:
                body = transcribe_chunk(config, chunk)
            if language is None and isinstance(body.get("language"), str):
                language = body["language"]
            chunk_cues, chunk_words = parse_segments(body, index * CHUNK_SECONDS * 1000)
            cues.extend(chunk_cues)
            words.extend(chunk_words)
            chunk.unlink(missing_ok=True)  # free space as we go; a long source is many chunks of WAV
        if not cues:
            raise AsrError("ASR produced no speech segments")
        with session_scope() as db:
            transcript = TranscriptService(db).store(
                item_id, language=normalize_language(language), source_kind="asr", cues=cues, model_label=model_id,
                words=words if any(words) else None,
            )
        return transcript.id
    finally:
        with jobs.lock:
            _processes.pop(job_id, None)
        shutil.rmtree(directory, ignore_errors=True)
