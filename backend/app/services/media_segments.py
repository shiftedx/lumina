"""Media segments: typed time ranges of one media file.

Stored in ``media_artifacts.analysis`` under the file's size:mtime fingerprint (the probe cache's key),
in a column of their own so re-probing never wipes them; a changed file drops them. Sources rank
user > fingerprint > introdb > heuristic and the best source present wins per type; a member's edit
replaces the whole list. Detection follows the storage helpers.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import statistics
import struct
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from app.media_schemas import MediaSegment, MediaSegmentInput
from app.models import AppSettings, LibraryItem, MediaArtifact, MediaTitle, utcnow
from app.persistence import write_transaction
from app.services.artwork import PublicArtworkFetcher
from app.services.media_artifacts import MediaArtifactService, artifact_file
from app.services.media_probe import LOCAL_INPUT_ARGS, MediaProbeService, media_tool
from app.services.media_probe import _fingerprint as file_fingerprint  # the probe cache's size:mtime key
from app.services.media_titles import jellyfin_id, synthetic_id, to_ticks
from app.services.network_policy import PublicSourcePolicy
from app.services.transcripts import Cue, TranscriptService

logger = logging.getLogger(__name__)

SOURCE_RANK = {"heuristic": 0, "introdb": 1, "fingerprint": 2, "user": 3}
JELLYFIN_TYPES = {"intro": "Intro", "credits": "Outro", "recap": "Recap", "preview": "Preview", "commercial": "Commercial"}
WATCHED_CHECK_FRACTION = 0.7  # credits are looked up only once playback passes 70%
CREDITS_COMPLETE_MIN_FRACTION = 0.5  # credits that start before half-way are not trusted as "watched"


def current_analysis(db: Session, item_id: str) -> tuple[MediaArtifact, str, dict[str, Any]] | None:
    """(artifact, file fingerprint, analysis valid for that fingerprint or ``{}``); None when the file is unavailable."""
    found = MediaArtifactService(db).artifact_for(item_id)
    if found is None:
        return None
    artifact, root = found
    try:
        fingerprint = file_fingerprint(artifact_file(root.path, artifact.relative_path))
    except OSError:
        return None
    analysis = artifact.analysis or {}
    return artifact, fingerprint, analysis if analysis.get("fingerprint") == fingerprint else {}


def effective(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Per type, the segments of the best-ranked source present; only user segments after a member's edit."""
    segments = [
        segment for segment in analysis.get("segments") or []
        if isinstance(segment, dict) and segment.get("source") in SOURCE_RANK and segment.get("type") in JELLYFIN_TYPES
    ]
    if analysis.get("user_edited"):
        segments = [segment for segment in segments if segment["source"] == "user"]
    best: dict[str, int] = {}
    for segment in segments:
        best[segment["type"]] = max(best.get(segment["type"], -1), SOURCE_RANK[segment["source"]])
    chosen = [segment for segment in segments if SOURCE_RANK[segment["source"]] == best[segment["type"]]]
    return sorted(chosen, key=lambda segment: (segment["start_ms"], segment["type"]))


def _model(segment: dict[str, Any]) -> MediaSegment:
    return MediaSegment(
        type=segment["type"], start_seconds=segment["start_ms"] / 1000, end_seconds=segment["end_ms"] / 1000,
        source=segment["source"], confidence=segment["confidence"],
    )


def segments_for(db: Session, item_id: str) -> list[MediaSegment]:
    current = current_analysis(db, item_id)
    return [_model(segment) for segment in effective(current[2])] if current else []


def credits_start_seconds(db: Session, item_id: str) -> float | None:
    starts = [segment.start_seconds for segment in segments_for(db, item_id) if segment.type == "credits"]
    return min(starts) if starts else None


def save_user_segments(db: Session, item_id: str, segments: list[MediaSegmentInput]) -> list[MediaSegment]:
    """A member's edit: the given list becomes the item's segments (detected ones are kept but no longer shown)."""
    with write_transaction(db, name="media_segments_edit"):
        current = current_analysis(db, item_id)
        if current is None:
            raise FileNotFoundError("Library item media is not available.")
        artifact, fingerprint, analysis = current
        detected = [segment for segment in analysis.get("segments") or [] if segment.get("source") != "user"]
        edited = [
            {"type": s.type, "start_ms": round(s.start_seconds * 1000), "end_ms": round(s.end_seconds * 1000), "source": "user", "confidence": 1.0}
            for s in segments
        ]
        artifact.analysis = {**analysis, "fingerprint": fingerprint, "segments": detected + edited, "user_edited": True}
    return segments_for(db, item_id)


def jellyfin_segments(
    jellyfin_item_id: str, library_item_id: str, segments: list[MediaSegment], include: Iterable[str] | None = None,
) -> dict[str, Any]:
    """``MediaSegmentDtoQueryResult`` for Jellyfin's ``GET /MediaSegments/{itemId}``; mute ranges are never segments."""
    wanted = {name.casefold() for name in include} if include else None
    items = []
    for segment in segments:
        kind = JELLYFIN_TYPES[segment.type]
        if wanted and kind.casefold() not in wanted:
            continue
        start_ms = round(segment.start_seconds * 1000)
        items.append({
            "Id": jellyfin_id(synthetic_id(f"segment:{library_item_id}:{segment.type}:{start_ms}")),
            "ItemId": jellyfin_id(jellyfin_item_id),
            "Type": kind,
            "StartTicks": to_ticks(segment.start_seconds),
            "EndTicks": to_ticks(segment.end_seconds),
        })
    return {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}


# ---- detection ---------------------------------------------------------

INTRO_MIN_S, INTRO_MAX_S = 15, 150
INTRO_WINDOW_FRACTION, INTRO_WINDOW_MAX_S = 0.25, 12 * 60
CREDITS_WINDOW_S = 6 * 60
CREDITS_TAIL_FRACTION = 0.15
MOVIE_CREDITS_MIN_S, MOVIE_CREDITS_MAX_S = 2 * 60, 15 * 60
NEIGHBOURS = 4  # season neighbours fingerprinted per job; fingerprints are not persisted
VOTES = 2
RECAP_WINDOW_MS = 3 * 60 * 1000
RECAP = re.compile(r"^(previously on|last time on)\b", re.IGNORECASE)
PREVIEW = re.compile(r"^next time on\b", re.IGNORECASE)
HEURISTIC_CONFIDENCE, INTRODB_CONFIDENCE = 0.5, 0.8
INTRODB_URL = "https://api.theintrodb.org/v3/media"
INTRODB_RECHECK = timedelta(days=30)
INTRODB_MAX_BYTES = 64 * 1024
INTRODB_TOLERANCE = 0.02  # reject results ending more than 2% past this file's duration (another cut)
INTRODB_KINDS = ("intro", "recap", "credits", "preview")
FFMPEG_TIMEOUT_SECONDS = 600
# Band-energy fallback (clean-room, from the published Haitsma–Kalker idea): 8 bands give 7 energy-difference
# bits per 100 ms frame; 4 consecutive frames pack into one 28-bit word.
BANDS = ((150, 300), (300, 500), (500, 800), (800, 1250), (1250, 2000), (2000, 3000), (3000, 4500), (4500, 7000))
BAND_HOP_S = 0.1
CHROMAPRINT_HOP_S = 4096 / 3 / 11025  # chromaprint's default: 4096-sample frames at 11025 Hz, 2/3 overlap
MATCH_BITS = {28: 8, 32: 10}  # words match at or under this Hamming distance; unrelated words differ by about half
SMOOTH_S = 1.0  # a frame is inside a run when half the frames within ±0.5 s match
AGREE_S = 2.0  # neighbours agree when their run starts and ends are this close


class SegmentError(RuntimeError):
    """Stable, content-free failure reason stored on the job."""


@dataclass(frozen=True)
class Fingerprint:
    words: list[int]
    hop_s: float
    bits: int
    start_s: float  # media time of words[0]


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SegmentError("Media analysis timed out") from exc
    if result.returncode != 0:
        raise SegmentError("Media analysis failed")
    return result


@lru_cache(maxsize=4)
def has_chromaprint(ffmpeg: str) -> bool:
    """jellyfin-ffmpeg ships the chromaprint muxer; a stock build may not."""
    try:
        muxers = subprocess.run([ffmpeg, "-hide_banner", "-muxers"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == ["chromaprint"] for line in muxers.splitlines())


def bandpass_command(ffmpeg: str, source: Path, start_s: float, duration_s: float) -> list[str]:
    """One pass: 8 band-pass chains, per-100 ms RMS level of each written to band{i}.txt in the working directory."""
    chains = "".join(
        f";[s{i}]bandpass=f={round((lo * hi) ** 0.5)}:width_type=h:w={hi - lo},asetnsamples=n=1600:p=0,"
        "astats=metadata=1:reset=1:measure_overall=RMS_level:measure_perchannel=none,"
        f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=band{i}.txt[o{i}]"
        for i, (lo, hi) in enumerate(BANDS)
    )
    graph = (
        "[0:a:0]aresample=16000,pan=mono|c0=c0,asplit=8" + "".join(f"[s{i}]" for i in range(len(BANDS)))
        + chains + ";" + "".join(f"[o{i}]" for i in range(len(BANDS))) + f"amix=inputs={len(BANDS)}[out]"
    )
    return [
        ffmpeg, "-nostdin", "-v", "error", *LOCAL_INPUT_ARGS, "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}",
        "-i", str(source), "-filter_complex", graph, "-map", "[out]", "-f", "null", "-",
    ]


def pack_band_words(energies: list[list[float]]) -> list[int]:
    """Per frame, the sign of each adjacent-band energy difference's change since the previous frame; 4 frames per word."""
    bits = []
    for previous, current in zip(energies, energies[1:]):
        value = 0
        for band in range(len(BANDS) - 1):
            change = (current[band] - current[band + 1]) - (previous[band] - previous[band + 1])
            value = (value << 1) | (change > 0)
        bits.append(value)
    return [(bits[t] << 21) | (bits[t + 1] << 14) | (bits[t + 2] << 7) | bits[t + 3] for t in range(len(bits) - 3)]


def _levels(text: str) -> list[float]:
    return [float(v) if v not in {"-inf", "inf", "nan"} else -120.0 for v in re.findall(r"RMS_level=(\S+)", text)]


def fingerprint(ffmpeg: str, source: Path, start_s: float, duration_s: float, work: Path) -> Fingerprint:
    if has_chromaprint(ffmpeg):
        out = _run([
            ffmpeg, "-nostdin", "-v", "error", *LOCAL_INPUT_ARGS, "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}",
            "-i", str(source), "-map", "0:a:0", "-ac", "1", "-f", "chromaprint", "-fp_format", "raw", "-",
        ]).stdout
        return Fingerprint([word for (word,) in struct.iter_unpack("<I", out[: len(out) // 4 * 4])], CHROMAPRINT_HOP_S, 32, start_s)
    work.mkdir(parents=True, exist_ok=True)
    _run(bandpass_command(ffmpeg, source, start_s, duration_s), cwd=work)  # relative band files: no path in the filter graph
    rows = [_levels((work / f"band{i}.txt").read_text()) for i in range(len(BANDS))]
    frames = min(map(len, rows))
    energies = [[rows[band][t] for band in range(len(BANDS))] for t in range(frames)]
    return Fingerprint(pack_band_words(energies), BAND_HOP_S, 28, start_s + BAND_HOP_S)


def _best_offset(a: list[int], b: list[int]) -> int | None:
    """Hash index of b → the word offset most exact matches vote for."""
    index: dict[int, list[int]] = collections.defaultdict(list)
    for j, word in enumerate(b):
        index[word].append(j)
    votes = collections.Counter(j - i for i, word in enumerate(a) for j in index.get(word, ()))
    return votes.most_common(1)[0][0] if votes else None


def _matching_run(a: Fingerprint, b: Fingerprint, offset: int) -> tuple[int, int] | None:
    """Longest [start, end) word range of ``a`` matching ``b`` at ``offset`` under the Hamming threshold (smoothed)."""
    limit = MATCH_BITS[a.bits]
    ok = [0 <= i + offset < len(b.words) and (a.words[i] ^ b.words[i + offset]).bit_count() <= limit for i in range(len(a.words))]
    half = max(1, round(SMOOTH_S / a.hop_s / 2))
    inside = [sum(ok[max(0, i - half):i + half]) >= half for i in range(len(ok))]
    best: tuple[int, int] | None = None
    start: int | None = None
    for i, flag in enumerate([*inside, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def shared_run(target: Fingerprint, neighbours: list[Fingerprint], min_s: float, max_s: float) -> tuple[float, float, float] | None:
    """(start_s, end_s, confidence) of a run the target shares with ≥ VOTES neighbours that agree within AGREE_S."""
    candidates = []
    for other in neighbours:
        offset = _best_offset(target.words, other.words)
        run = _matching_run(target, other, offset) if offset is not None else None
        if run is None:
            continue
        start, end = target.start_s + run[0] * target.hop_s, target.start_s + run[1] * target.hop_s
        if min_s <= end - start <= max_s:
            candidates.append((start, end))
    for start, end in candidates:
        agree = [(s, e) for s, e in candidates if abs(s - start) <= AGREE_S and abs(e - end) <= AGREE_S]
        if len(agree) >= VOTES:
            return statistics.median(s for s, _ in agree), statistics.median(e for _, e in agree), len(agree) / len(neighbours)
    return None


def _segment(kind: str, start_s: float, end_s: float, source: str, confidence: float) -> dict[str, Any]:
    return {"type": kind, "start_ms": round(start_s * 1000), "end_ms": round(end_s * 1000), "source": source, "confidence": round(confidence, 2)}


def fingerprint_segments(
    ffmpeg: str, media: Path, duration_s: float, neighbours: list[tuple[Path, float]], work: Path, check_canceled: Callable[[], None],
) -> list[dict[str, Any]]:
    """Intro in the first min(25%, 12 min) and credits in the last 6 min, each voted by ≥ VOTES season neighbours."""
    if len(neighbours) < VOTES:
        return []
    found = []
    windows = (
        ("intro", lambda d: 0.0, lambda d: min(d * INTRO_WINDOW_FRACTION, INTRO_WINDOW_MAX_S), INTRO_MAX_S),
        # Credits: the last 6 min, never the first half (a short file's tail must not overlap its intro).
        ("credits", lambda d: max(d - CREDITS_WINDOW_S, d / 2), lambda d: d - max(d - CREDITS_WINDOW_S, d / 2), CREDITS_WINDOW_S),
    )
    for kind, start_of, length_of, longest in windows:
        target = fingerprint(ffmpeg, media, start_of(duration_s), length_of(duration_s), work)
        others = []
        for path, other_duration in neighbours:
            check_canceled()
            others.append(fingerprint(ffmpeg, path, start_of(other_duration), length_of(other_duration), work))
        run = shared_run(target, others, INTRO_MIN_S, longest)
        if run is not None:
            start, end, confidence = run
            found.append(_segment(kind, start, duration_s if kind == "credits" else end, "fingerprint", confidence))
    return found


def blackdetect_credits(ffmpeg: str, media: Path, duration_s: float, cues: list[Cue], is_movie: bool) -> dict[str, Any] | None:
    """The last fade to black in the final 15% (keyframes only, 160 px wide) with no speech after it."""
    tail_s = duration_s * (1 - CREDITS_TAIL_FRACTION)
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", *LOCAL_INPUT_ARGS, "-ss", f"{tail_s:.3f}",
        "-skip_frame", "nokey", "-i", str(media), "-an", "-sn", "-dn",
        "-vf", "scale=160:-2,blackdetect=d=0.5:pic_th=0.98", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=FFMPEG_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return None
    starts = [tail_s + float(value) for value in re.findall(r"black_start:(\d+(?:\.\d+)?)", result.stderr)]
    if result.returncode != 0 or not starts:
        return None  # no video stream, or no fade
    black_s = starts[-1]
    if is_movie and not MOVIE_CREDITS_MIN_S <= duration_s - black_s <= MOVIE_CREDITS_MAX_S:
        return None
    if any(start >= black_s * 1000 for start, _end, _text in cues):
        return None  # dialogue after the fade: a scene change, not the credits
    return _segment("credits", black_s, duration_s, "heuristic", HEURISTIC_CONFIDENCE)


def text_segments(cues: list[Cue], found: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    """Recap: a "previously on" cue in the first 3 min, up to the intro. Preview: "next time on" after credits start."""
    def best(kind: str) -> dict[str, Any] | None:
        return max((s for s in found if s["type"] == kind), key=lambda s: SOURCE_RANK[s["source"]], default=None)

    out = []
    intro, credits = best("intro"), best("credits")
    if intro is not None:
        recap = next((c for c in cues if c[0] < min(RECAP_WINDOW_MS, intro["start_ms"]) and RECAP.match(c[2].strip())), None)
        if recap is not None:
            out.append({**_segment("recap", 0, 0, "heuristic", HEURISTIC_CONFIDENCE), "start_ms": recap[0], "end_ms": intro["start_ms"]})
    if credits is not None:
        preview = next((c for c in cues if c[0] >= credits["start_ms"] and PREVIEW.match(c[2].strip())), None)
        if preview is not None:
            out.append({**_segment("preview", 0, duration_s, "heuristic", HEURISTIC_CONFIDENCE), "start_ms": preview[0]})
    return out


def introdb_segments(data: dict[str, Any], duration_s: float) -> list[dict[str, Any]]:
    """TheIntroDB v3 ``{intro|recap|credits|preview: [{start_ms, end_ms}]}``; a null start is 0, a null end is the file's end."""
    duration_ms = round(duration_s * 1000)
    out = []
    for kind in INTRODB_KINDS:
        entries = data.get(kind)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            start = 0 if entry.get("start_ms") is None else entry["start_ms"]
            end = duration_ms if entry.get("end_ms") is None else entry["end_ms"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                continue
            if end > duration_ms * (1 + INTRODB_TOLERANCE):
                continue
            out.append({"type": kind, "start_ms": start, "end_ms": min(end, duration_ms), "source": "introdb", "confidence": INTRODB_CONFIDENCE})
    return out


def introdb_query(db: Session, title: MediaTitle | None, analysis: dict[str, Any]) -> dict[str, Any] | None:
    """TheIntroDB parameters, or None: disabled (the default), no TMDB id, or looked up in the last 30 days."""
    record = db.get(AppSettings, 1)
    if record is None or not record.introdb_enabled or title is None:
        return None
    checked = analysis.get("introdb_checked_at")
    if checked and datetime.fromisoformat(checked) > utcnow() - INTRODB_RECHECK:
        return None
    if title.type == "movie":
        tmdb = (title.provider_ids or {}).get("Tmdb")
        return {"tmdb_id": tmdb} if tmdb else None
    if title.type != "episode" or title.parent_id is None or title.index_number is None:
        return None
    season = db.get(MediaTitle, title.parent_id)
    series = db.get(MediaTitle, season.parent_id) if season is not None and season.parent_id else None
    tmdb = (series.provider_ids or {}).get("Tmdb") if series is not None else None
    if not tmdb or season.index_number is None:
        return None
    return {"tmdb_id": tmdb, "season": season.index_number, "episode": title.index_number}


def _introdb_get(query: dict[str, Any]) -> dict[str, Any]:
    """One bounded GET through the public-only transport (PublicSourcePolicy); 404 means TheIntroDB has nothing."""
    policy = PublicSourcePolicy()
    response = PublicArtworkFetcher(policy, request_timeout_seconds=10).fetch(
        f"{INTRODB_URL}?{urlencode(query)}", validate_redirect=policy.validate_url,
    )
    try:
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            raise SegmentError(f"TheIntroDB returned HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.body:
            body += chunk
            if len(body) > INTRODB_MAX_BYTES:
                raise SegmentError("TheIntroDB response exceeded the size limit")
    finally:
        response.close()
    data = json.loads(body)
    return data if isinstance(data, dict) else {}


def _duration_s(db: Session, item: LibraryItem) -> float | None:
    if item.duration:
        return float(item.duration)
    try:
        facts = MediaProbeService(db).facts(item)
    except FileNotFoundError:
        return None
    return facts.get("duration")


def _neighbours(db: Session, title: MediaTitle | None) -> list[tuple[Path, float]]:
    """Up to NEIGHBOURS available episodes of the same season, nearest episode numbers first."""
    if title is None or title.type != "episode" or title.parent_id is None:
        return []
    siblings = (
        db.query(MediaTitle)
        .filter(MediaTitle.parent_id == title.parent_id, MediaTitle.type == "episode", MediaTitle.id != title.id)
        .all()
    )
    siblings.sort(key=lambda sibling: (abs((sibling.index_number or 0) - (title.index_number or 0)), sibling.index_number or 0))
    artifacts, found = MediaArtifactService(db), []
    for sibling in siblings:
        other = (
            db.query(LibraryItem)
            .filter(LibraryItem.title_id == sibling.id, LibraryItem.extra_type.is_(None), LibraryItem.status != "missing")
            .order_by(LibraryItem.id)
            .first()
        )
        if other is None:
            continue
        try:
            path, _root = artifacts.locate(other)
        except FileNotFoundError:
            continue
        duration = _duration_s(db, other)
        if duration:
            found.append((path, duration))
        if len(found) == NEIGHBOURS:
            break
    return found


def detect(job_id: str, item_id: str, check_canceled: Callable[[], None]) -> None:
    """The ``segments`` job: run every detector and store the result under the fingerprint read at the start.

    User segments are kept. A file that changes during detection discards the run (SegmentError).
    """
    with session_scope() as db:
        item = db.get(LibraryItem, item_id)
        current = current_analysis(db, item_id) if item is not None else None
        if current is None:
            raise FileNotFoundError("Library item media is not available.")
        _artifact, fingerprint_key, analysis = current
        media, _root = MediaArtifactService(db).locate(item)
        ffmpeg = media_tool(db, "ffmpeg")
        duration_s = _duration_s(db, item)
        title = db.get(MediaTitle, item.title_id) if item.title_id else None
        neighbours = _neighbours(db, title)
        query = introdb_query(db, title, analysis)
        timing = TranscriptService(db).timing_transcript(item_id)
        cues = TranscriptService(db).cue_tuples(timing.id) if timing is not None else []
    if not ffmpeg:
        raise SegmentError("ffmpeg is not available")
    if not duration_s:
        raise SegmentError("Media duration is unknown")

    checked_at = analysis.get("introdb_checked_at")
    found: list[dict[str, Any]] = []
    if query is None:  # not asked this time: keep what an earlier lookup found for this same file
        found += [segment for segment in analysis.get("segments") or [] if segment.get("source") == "introdb"]
    else:
        try:
            found += introdb_segments(_introdb_get(query), duration_s)
            checked_at = utcnow().isoformat()
        except Exception as exc:  # noqa: BLE001 - an optional lookup; the next detection retries it
            logger.warning("TheIntroDB lookup failed: %s", type(exc).__name__)
    work = settings.temp_root / "segment-jobs" / job_id
    try:
        found += fingerprint_segments(ffmpeg, media, duration_s, neighbours, work, check_canceled)
    except SegmentError:
        pass  # e.g. no audio stream: the other detectors still run
    finally:
        shutil.rmtree(work, ignore_errors=True)
    check_canceled()
    if not any(segment["type"] == "credits" for segment in found):
        black = blackdetect_credits(ffmpeg, media, duration_s, cues, title is not None and title.type == "movie")
        if black is not None:
            found.append(black)
    found += text_segments(cues, found, duration_s)

    with session_scope() as db, write_transaction(db, name="media_segments_detect"):
        latest = current_analysis(db, item_id)
        if latest is None or latest[1] != fingerprint_key:
            raise SegmentError("The media file changed during detection")
        artifact, _key, stored = latest
        kept = [segment for segment in stored.get("segments") or [] if segment.get("source") == "user"]
        artifact.analysis = {**stored, "fingerprint": fingerprint_key, "segments": kept + found, "introdb_checked_at": checked_at}
