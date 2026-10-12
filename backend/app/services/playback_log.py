"""One-line playback outcome logs plus the recent failures Admin -> Diagnostics shows (#140).

Callers pass only safe fields (opaque ids, provider, format ids, fixed error text);
the installed record factory redacts anything else before a handler sees it.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger("lumina.playback")
OK_OUTCOMES = frozenset({"ready", "complete", "started", "stopped", "superseded"})
# In-memory and per-process, lost on restart; persist if admins need history across restarts.
recent_failures: deque[tuple[datetime, str]] = deque(maxlen=20)


def log_playback(event: str, outcome: str, **fields: object) -> None:
    line = " ".join([event, f"outcome={outcome}", *(f"{key}={value}" for key, value in fields.items() if value is not None)])
    if outcome in OK_OUTCOMES:
        logger.info("%s", line)
        return
    logger.warning("%s", line)
    recent_failures.appendleft((datetime.now(timezone.utc).replace(tzinfo=None), line))  # naive UTC, like model timestamps
