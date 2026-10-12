"""Subtitle auto-sync: pure and stdlib-only.

A subtitle track is aligned to a reference: the words of a same-language ASR
transcript (text anchors), else speech intervals (ASR cues or VAD). The result is
one to three linear pieces ``t' = a + b*t``. Nothing here reads files or the DB.
"""

from __future__ import annotations

import difflib
import re
import statistics

Cue = tuple[int, int, str]
Interval = tuple[int, int]
Piece = tuple[int, float, float]  # (applies from subtitle ms, a, b): t' = a + b*t

COARSE_STEP_MS, COARSE_SPAN_MS = 1000, 120_000
FINE_STEP_MS, FINE_SPAN_MS = 50, 1000
WINDOWS = 8
WINDOW_SPAN_MS = 10_000
FRAME_RATIOS = (23.976 / 25, 25 / 23.976, 24 / 25)
PIECE_STEP_MS = 500
MAX_PIECES = 3
MIN_JACCARD = 0.5
MIN_IMPROVEMENT = 0.10  # relative to the unsynced score
MAX_ANCHOR_RESIDUAL_MS = 300
MIN_ANCHOR_RUN = 3  # matched tokens in a row before they count as an anchor
MIN_FIT_SPAN_MS = 60_000  # a piece shorter than this gets an offset only, not a slope
STEP_WINDOW = 5  # pairs per side when looking for a residual step
_TOKEN = re.compile(r"\w+")


class SyncError(ValueError):
    """The one content-free failure a member sees."""

    def __init__(self) -> None:
        super().__init__("Could not sync confidently")


_SILENCE = re.compile(r"silence_(start|end): (-?\d+(?:\.\d+)?)")


def speech_from_silencedetect(log: str, duration_ms: int) -> list[Interval]:
    """Speech = the complement of ffmpeg ``silencedetect`` silences over [0, duration_ms]."""
    speech: list[Interval] = []
    cursor: int | None = 0
    for kind, value in _SILENCE.findall(log):
        at = max(0, round(float(value) * 1000))
        if kind == "start":
            if cursor is not None and at > cursor:
                speech.append((cursor, at))
            cursor = None
        else:
            cursor = at
    if cursor is not None and cursor < duration_ms:
        speech.append((cursor, duration_ms))
    return speech


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def jaccard(a: list[Interval], b: list[Interval]) -> float:
    """|a ∩ b| / |a ∪ b| of two merged interval lists in one O(n+m) sweep."""
    i = j = inter = 0
    while i < len(a) and j < len(b):
        inter += max(0, min(a[i][1], b[j][1]) - max(a[i][0], b[j][0]))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    union = sum(e - s for s, e in a) + sum(e - s for s, e in b) - inter
    return inter / union if union else 0.0


def _map(t: float, pieces: list[Piece]) -> int:
    a, b = next((a, b) for start, a, b in reversed(pieces) if t >= start)  # pieces[0] starts at 0
    return max(0, round(a + b * t))


def apply(cues: list[Cue], pieces: list[Piece]) -> list[Cue]:
    out = []
    for start, end, text in cues:
        new_start = _map(start, pieces)
        out.append((new_start, max(new_start, _map(end, pieces)), text))
    return out


def _intervals(cues: list[Cue], pieces: list[Piece] | None = None) -> list[Interval]:
    return merge([(s, e) for s, e, _ in (apply(cues, pieces) if pieces else cues)])


def _search(subs: list[Interval], ref: list[Interval], center: int, step: int, span: int) -> tuple[int, float]:
    best = (center, -1.0)
    for offset in range(center - span, center + span + 1, step):
        score = jaccard([(s + offset, e + offset) for s, e in subs], ref)
        if score > best[1]:
            best = (offset, score)
    return best


def best_offset(subs: list[Interval], ref: list[Interval], center: int = 0, span: int = COARSE_SPAN_MS) -> tuple[int, float]:
    """Coarse-to-fine global offset: 1 s steps over ±span, then 50 ms over ±1 s."""
    coarse, _ = _search(subs, ref, center, COARSE_STEP_MS, span)
    return _search(subs, ref, coarse, FINE_STEP_MS, FINE_SPAN_MS)


def fit_pieces(pairs: list[tuple[float, float]]) -> list[Piece]:
    """(subtitle ms, reference ms) pairs → up to MAX_PIECES lines, split where the residual steps > PIECE_STEP_MS."""
    pairs = sorted(pairs)
    if len(pairs) < 2:
        raise SyncError()
    offsets = [y - x for x, y in pairs]
    k = max(1, min(STEP_WINDOW, len(pairs) // 4))
    steps = []
    for i in range(k, len(pairs) - k + 1):
        jump = abs(statistics.median(offsets[i:i + k]) - statistics.median(offsets[i - k:i]))
        if jump > PIECE_STEP_MS:
            steps.append((jump, i))
    cuts: list[int] = []
    for _jump, i in sorted(steps, reverse=True):
        if len(cuts) == MAX_PIECES - 1:
            break
        if all(abs(i - c) >= k for c in cuts):
            cuts.append(i)
    pieces: list[Piece] = []
    bounds = [0, *sorted(cuts), len(pairs)]
    for lo, hi in zip(bounds, bounds[1:]):
        xs, ys = [x for x, _ in pairs[lo:hi]], [y for _, y in pairs[lo:hi]]
        if len(set(xs)) >= 2 and xs[-1] - xs[0] >= MIN_FIT_SPAN_MS:
            b, a = statistics.linear_regression(xs, ys)
        else:
            b, a = 1.0, statistics.median(y - x for x, y in zip(xs, ys))
        pieces.append((0 if lo == 0 else round((pairs[lo - 1][0] + pairs[lo][0]) / 2), a, b))
    return pieces


def _window_pairs(subs: list[Interval], ref: list[Interval], center: int) -> list[tuple[float, float]]:
    if not subs:
        return []
    first, last = subs[0][0], subs[-1][1]
    width = max(1, (last - first) // WINDOWS + 1)
    pairs = []
    for w in range(WINDOWS):
        lo, hi = first + w * width, first + (w + 1) * width
        window = [i for i in subs if lo <= i[0] < hi]
        if len(window) < 3:
            continue
        offset, score = best_offset(window, ref, center, WINDOW_SPAN_MS)
        if score > 0:
            mid = (window[0][0] + window[-1][1]) / 2
            pairs.append((mid, mid + offset))
    return pairs


def text_anchors(cues: list[Cue], words: list[tuple[int, int, str]]) -> list[tuple[float, float]]:
    """(subtitle ms, reference ms) for runs of ≥ MIN_ANCHOR_RUN equal normalized tokens."""
    sub_tokens: list[tuple[str, float]] = []
    for start, end, text in cues:
        tokens = _TOKEN.findall(text.casefold())
        for n, token in enumerate(tokens):  # tokens spread evenly over the cue
            sub_tokens.append((token, start + (end - start) * n / len(tokens)))
    ref_tokens = [(token, start) for start, _end, word in words for token in _TOKEN.findall(word.casefold())[:1]]
    # SequenceMatcher is quadratic in the worst case; chunk by time window if a feature-length match is slow.
    matcher = difflib.SequenceMatcher(None, [t for t, _ in sub_tokens], [t for t, _ in ref_tokens], autojunk=False)
    return [
        (sub_tokens[block.a + n][1], ref_tokens[block.b + n][1])
        for block in matcher.get_matching_blocks() if block.size >= MIN_ANCHOR_RUN
        for n in range(block.size)
    ]


def sync(cues: list[Cue], *, speech: list[Interval], words: list[tuple[int, int, str]] | None = None) -> list[Cue]:
    """Aligned cues, or SyncError. ``speech`` is the reference speech (ASR cues or VAD); ``words`` enables anchors."""
    ref = merge(speech)
    subs = _intervals(cues)
    if not ref or not subs:
        raise SyncError()
    baseline = jaccard(subs, ref)
    candidates: list[list[Piece]] = []
    if words:
        pairs = text_anchors(cues, words)
        if len(pairs) >= 2:
            pieces = fit_pieces(pairs)
            residuals = [abs(y - _map(x, pieces)) for x, y in pairs]
            if statistics.median(residuals) <= MAX_ANCHOR_RESIDUAL_MS:
                candidates.append(pieces)
    if not candidates:
        offset, _ = best_offset(subs, ref)
        candidates.append([(0, float(offset), 1.0)])
        for ratio in FRAME_RATIOS:
            scaled = [(round(s * ratio), round(e * ratio)) for s, e in subs]
            r_offset, _ = best_offset(scaled, ref, round(offset))
            candidates.append([(0, float(r_offset), ratio)])
        pairs = _window_pairs(subs, ref, offset)
        if len(pairs) >= 2:
            candidates.append(fit_pieces(pairs))
    best = max(candidates, key=lambda pieces: jaccard(_intervals(cues, pieces), ref))
    score = jaccard(_intervals(cues, best), ref)
    if score < MIN_JACCARD or score < baseline * (1 + MIN_IMPROVEMENT):
        raise SyncError()
    return apply(cues, best)
