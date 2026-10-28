"""Auditable title-anchor evidence shared by the current and legacy policies."""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.services.reco import Candidate


@dataclass(frozen=True)
class AnchorMatch:
    strength: float
    reason: str


MIN_SUBJECT_WORDS = 2
MIN_SUBJECT_COVERAGE = 0.2
CROSS_FORM_SUBJECT_WORDS = 3
CROSS_FORM_COVERAGE = 0.35
SEMANTIC_FLOOR = 0.82  # conservative heuristic pending judged-pair calibration


def _animation_form(tokens: frozenset[str]) -> bool | None:
    if tokens & {"genre:animation", "cat:anime", "cat:animation"}:
        return True
    if "genre:live action" in tokens:
        return False
    return None


def _subject_weights(anchor: Candidate, candidate: Candidate, document_frequency: Mapping[str, int] | None,
                     document_count: int) -> tuple[float, tuple[str, ...]]:
    left = {token for token in anchor.tokens if token.startswith("subject:")}
    right = {token for token in candidate.tokens if token.startswith("subject:")}
    shared = left & right
    if not shared:
        return 0.0, ()
    def weight(token: str) -> float:
        return math.log1p((document_count + 1) / (1 + (document_frequency or {}).get(token, 0))) if document_count else 1.0
    overlap = sum(weight(token) for token in shared)
    coverage = overlap / max(1.0, min(sum(map(weight, left)), sum(map(weight, right))))
    return coverage, tuple(token[8:] for token in sorted(shared, key=lambda token: (-weight(token), token)))


def anchor_match(anchor: Candidate, candidate: Candidate, *, document_frequency: Mapping[str, int] | None = None,
                 document_count: int = 0, cosine_override: float | None = None) -> AnchorMatch | None:
    """Require a concrete title relationship; weak genre/category/decade and relative rank never qualify.

    Thresholds are conservative hand-set heuristics pending judged-pair calibration. Embeddings can bridge sparse
    synopsis metadata within one format; an explicit animation crossing also needs shared subject words.
    """
    if anchor.target_kind != "title" or candidate.target_kind != "title" or anchor.key == candidate.key:
        return None
    if anchor.media_type and candidate.media_type and anchor.media_type != candidate.media_type and not (
        anchor.media_type == "boxset" and candidate.media_type == "movie"):
        return None
    if anchor.media_type == "boxset" and candidate.media_type == "movie" and f"col:{anchor.key}" in candidate.tokens:
        return AnchorMatch(1.0, "Same collection")
    shared = anchor.tokens & candidate.tokens
    coverage, subjects = _subject_weights(anchor, candidate, document_frequency, document_count)
    semantic = cosine_override
    if semantic is None and anchor.vector is not None and candidate.vector is not None and len(anchor.vector) == len(candidate.vector):
        semantic = math.sumprod(anchor.vector, candidate.vector)
    anchor_form, candidate_form = _animation_form(anchor.tokens), _animation_form(candidate.tokens)
    # Unknown form is not evidence of live action. Still require a thematic bridge when only one title
    # explicitly declares animation; a high embedding cosine alone cannot justify that crossing.
    animation_uncertain = (anchor_form is True) != (candidate_form is True)
    thematic = len(subjects) >= (CROSS_FORM_SUBJECT_WORDS if animation_uncertain else MIN_SUBJECT_WORDS) and coverage >= (
        CROSS_FORM_COVERAGE if animation_uncertain else MIN_SUBJECT_COVERAGE)
    if animation_uncertain and not thematic:
        return None
    if any(token.startswith("col:") for token in shared):
        return AnchorMatch(1.0, "Same collection")
    if thematic:
        return AnchorMatch(min(0.95, 0.5 + 0.4 * coverage + 0.1 * min(len(subjects), 4) / 4),
                           f"Shared subject: {subjects[0]} and {subjects[1]}")
    if animation_uncertain:
        return None
    if any(token.startswith("dir:") for token in shared):
        return AnchorMatch(0.8, "Same creator")
    if sum(token.startswith("cast:") for token in shared) >= 2:
        return AnchorMatch(0.65, "Shared cast")
    if semantic is not None and semantic >= SEMANTIC_FLOOR:
        return AnchorMatch(min(0.9, semantic), "Similar story")
    return None
