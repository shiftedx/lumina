"""Normalize provider chapters and description timestamp links at one seam."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import TypedDict


NEAR_START_SECONDS = 5.0
MAX_DESCRIPTION_SCAN_CHARS = 50_000
MAX_TIMELINE_ITEMS = 200
MAX_CHAPTER_TITLE_CHARS = 240
_TIMESTAMP_CANDIDATE = re.compile(r"(?<![\w:/])(?P<label>(?:\d+:){1,2}\d+)(?![\w:])")


class NormalizedChapter(TypedDict):
    start_time: float
    end_time: float | None
    title: str


class TimestampToken(TypedDict):
    start: int
    end: int
    seconds: float
    label: str


class TimelineMetadata(TypedDict):
    chapters: list[NormalizedChapter]
    timestamps: list[TimestampToken]


def normalize_chapters(
    *,
    provider_chapters: object,
    description: object,
    duration: object,
) -> TimelineMetadata:
    """Return safe, duration-bounded timeline metadata for a player response.

    Provider chapters win whenever they yield at least one usable division. Description
    timestamps remain independently useful as seek links, but form divisions only as
    a complete, ascending timestamp list beginning at the start of the media.
    """

    normalized_duration = _duration(duration)
    timestamp_candidates, timestamps_truncated = _timestamp_candidates(description, normalized_duration)
    timestamps = [token for token, valid_for_chapters in timestamp_candidates if valid_for_chapters]
    chapters = _provider_chapters(provider_chapters, normalized_duration)
    if not chapters:
        chapters = _inferred_chapters(
            description,
            timestamps,
            candidates_are_valid=(
                bool(timestamp_candidates)
                and not timestamps_truncated
                and all(valid for _, valid in timestamp_candidates)
            ),
            duration=normalized_duration,
        )
    return {"chapters": chapters, "timestamps": timestamps}


def _provider_chapters(provider_chapters: object, duration: float | None) -> list[NormalizedChapter]:
    if not isinstance(provider_chapters, Sequence) or isinstance(provider_chapters, (str, bytes, bytearray)):
        return []

    candidates: list[tuple[float, float | None, str]] = []
    for index, raw in enumerate(provider_chapters):
        if index >= MAX_TIMELINE_ITEMS:
            break
        if not isinstance(raw, Mapping):
            continue
        start = _seconds(raw.get("start_time"))
        if start is None or start < 0 or (duration is not None and start >= duration):
            continue
        raw_end = _seconds(raw.get("end_time"))
        end = min(raw_end, duration) if raw_end is not None and duration is not None else raw_end
        title = raw.get("title")
        candidates.append((
            start,
            end,
            title.strip()[:MAX_CHAPTER_TITLE_CHARS] if isinstance(title, str) and title.strip() else "",
        ))

    candidates.sort(key=lambda chapter: chapter[0])
    unique: list[tuple[float, float | None, str]] = []
    for candidate in candidates:
        if not unique or candidate[0] > unique[-1][0]:
            unique.append(candidate)

    normalized: list[NormalizedChapter] = []
    for index, (start, end, title) in enumerate(unique):
        next_start = unique[index + 1][0] if index + 1 < len(unique) else duration
        if next_start is not None:
            end = min(end, next_start) if end is not None else next_start
        if end is not None and end <= start:
            continue
        normalized.append(
            {
                "start_time": start,
                "end_time": end,
                "title": title or f"Chapter {len(normalized) + 1}",
            },
        )
    return normalized


def _inferred_chapters(
    description: object,
    timestamps: list[TimestampToken],
    *,
    candidates_are_valid: bool,
    duration: float | None,
) -> list[NormalizedChapter]:
    if not isinstance(description, str) or duration is None or len(timestamps) < 3 or not candidates_are_valid:
        return []
    if timestamps[0]["seconds"] > NEAR_START_SECONDS:
        return []
    if any(left["seconds"] >= right["seconds"] for left, right in zip(timestamps, timestamps[1:])):
        return []

    chapters: list[NormalizedChapter] = []
    for index, timestamp in enumerate(timestamps):
        next_start = timestamps[index + 1]["seconds"] if index + 1 < len(timestamps) else duration
        chapters.append(
            {
                "start_time": timestamp["seconds"],
                "end_time": next_start,
                "title": _description_title(description, timestamp) or f"Chapter {index + 1}",
            },
        )
    return chapters


def _timestamp_candidates(description: object, duration: float | None) -> tuple[list[tuple[TimestampToken, bool]], bool]:
    if not isinstance(description, str):
        return [], False
    bounded_description = description[:MAX_DESCRIPTION_SCAN_CHARS]
    candidates: list[tuple[TimestampToken, bool]] = []
    for match in _TIMESTAMP_CANDIDATE.finditer(bounded_description):
        if len(candidates) >= MAX_TIMELINE_ITEMS:
            return candidates, True
        label = match.group("label")
        seconds = _parse_timestamp(label)
        if seconds is None:
            candidates.append(({"start": match.start(), "end": match.end(), "seconds": -1.0, "label": label}, False))
            continue
        in_range = duration is None or seconds < duration
        token = {"start": match.start(), "end": match.end(), "seconds": seconds, "label": label}
        candidates.append((token, in_range))
    return candidates, False


def _description_title(description: str, timestamp: TimestampToken) -> str:
    bounded_description = description[:MAX_DESCRIPTION_SCAN_CHARS]
    line_end = bounded_description.find("\n", timestamp["end"])
    suffix = bounded_description[timestamp["end"]:line_end if line_end >= 0 else len(bounded_description)]
    return suffix.strip(" \t-–—:|•")[:MAX_CHAPTER_TITLE_CHARS]


def _parse_timestamp(value: str) -> float | None:
    try:
        parts = [int(part) for part in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        minutes, seconds = parts
        if seconds > 59:
            return None
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if minutes > 59 or seconds > 59:
            return None
        return float(hours * 3600 + minutes * 60 + seconds)
    return None


def _duration(value: object) -> float | None:
    seconds = _seconds(value)
    return seconds if seconds is not None and seconds > 0 else None


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    return seconds if math.isfinite(seconds) else None
