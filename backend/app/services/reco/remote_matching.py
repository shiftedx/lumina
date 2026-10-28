"""Local evidence that a remote video continues the one currently playing."""
from __future__ import annotations

import re

from app.services.reco import Candidate

_GENERIC = frozenset("""
    audio best boss clip clips compilation episode final game gaming guide highlights live music official part podcast reaction
    replay review route short shorts speedrun stream tips today trailer tutorial update upload video walkthrough watch
""".split())
_VECTOR_FLOOR = 0.92
_WORD = re.compile(r"[^\W_]+")


def _raw_words(candidate: Candidate) -> frozenset[str]:
    return frozenset(token for token in candidate.tokens
                     if " " not in token and ":" not in token and not token.isdecimal()
                     and token not in _GENERIC)


def _words(candidate: Candidate) -> frozenset[str]:
    branding = set(_WORD.findall((candidate.channel_name or "").casefold()))
    return _raw_words(candidate) - branding


def remote_anchor_strength(anchor: Candidate, candidate: Candidate) -> float | None:
    """Return subject evidence, or abstain. Category and channel are corroboration, never sole evidence."""
    if anchor.target_kind != "remote" or candidate.target_kind != "remote":
        return None
    if anchor.category_keys and candidate.category_keys and not set(anchor.category_keys).intersection(candidate.category_keys):
        return None
    shared = _raw_words(anchor) & _raw_words(candidate)
    subject_shared = _words(anchor) & _words(candidate)
    bigram = any(" " in token and ":" not in token and all(word in shared for word in token.split())
                 and any(word in subject_shared for word in token.split())
                 for token in anchor.tokens & candidate.tokens)
    same_channel = bool(anchor.channel_key and anchor.channel_key == candidate.channel_key)
    if subject_shared and bigram:
        return 4.0 + min(len(shared), 4) / 10
    if subject_shared and len(shared) >= 2:
        return 3.0 + min(len(shared), 6) / 10
    if same_channel and subject_shared and max(map(len, subject_shared)) >= 4:
        return 2.0
    # Stored remote embeddings include the uploader; for one channel cosine alone can echo creator identity.
    if (
        (not same_channel or subject_shared) and anchor.vector is not None and candidate.vector is not None
        and len(anchor.vector) == len(candidate.vector) and len(anchor.vector)
    ):
        cosine = sum(left * right for left, right in zip(anchor.vector, candidate.vector))
        if cosine >= _VECTOR_FLOOR:
            return 1.0 + min(cosine, 1.0)
    return None
