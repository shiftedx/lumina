"""Viewing hours, the daily limit and screen time.

``can_watch_now`` answers at every playback start; ``enforce`` turns a refusal into 403 ``outside_hours`` /
``screen_time_up`` and stops the member's encodes. Times are household-local: the server's TZ (as library automation).

Counting is server-side: one clock per member across devices, advanced by progress reports (``heartbeat``, everyone)
and by every media, stream and segment serve of a restricted member (``enforce``), so a player that never reports still
counts. Each gap counts at most ``GAP_SECONDS`` (a pause then resuming costs at most that; silence never hides time),
or ``RESUME_SECONDS`` after a Stopped report. A client that fetches a whole file or many segments in one burst
and plays them offline counts only the burst; that is a download, not streaming.
A changed limit or bonus drops the member's cached allow (``forget``), so a running stream's next fetch re-checks;
one that fetches nothing (a single long direct-play response) stops at its next report, so it may run up to a minute on.
"""
from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta, tzinfo

from fastapi import Depends, HTTPException
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ScreenTime, User
from app.persistence import write_transaction
from app.security import get_current_user
from app.services import member_access
from app.services.local_playback_sessions import sessions

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GAP_SECONDS = 90  # the most one gap between a member's reports or fetches counts: a longer one is a pause
RESUME_SECONDS = 10  # the most the first gap after a Stopped report counts
CREDIT_STEP = 5  # seconds the clock runs before a fetch writes them: a burst of Range requests is one write
RECHECK_SECONDS = 60
LOCAL_TZ: tzinfo | None = None  # None = the server's TZ; tests pin one

_lock = threading.Lock()
_last_beat: dict[str, tuple[datetime, int]] = {}  # user id -> (last count, most the next gap counts), any device
_last_pass: dict[str, datetime] = {}  # user id -> last check that allowed watching


def _now() -> datetime:
    return datetime.now(UTC)


def _local(now: datetime) -> datetime:
    return now.astimezone(LOCAL_TZ)


def local_day(now: datetime | None = None) -> date:
    return _local(now or _now()).date()


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _windows(schedule: dict, local: datetime) -> list[tuple[datetime, datetime]]:
    """The schedule's ranges from yesterday to a week ahead, merged; an end at or before its start runs past midnight."""
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    spans = []
    for offset in range(-1, 8):
        day = midnight + timedelta(days=offset)
        for start, end in schedule.get(DAYS[day.weekday()]) or ():
            begin, finish = day + timedelta(minutes=_minutes(start)), day + timedelta(minutes=_minutes(end))
            spans.append((begin, finish if finish > begin else finish + timedelta(days=1)))
    merged: list[tuple[datetime, datetime]] = []
    for begin, finish in sorted(spans):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged


def seconds_today(db: Session, user_id: str, now: datetime | None = None) -> int:
    row = db.get(ScreenTime, (user_id, local_day(now)))
    return row.seconds if row is not None else 0


def can_watch_now(db: Session, user: User, now: datetime | None = None) -> tuple[bool, str | None, datetime | None, int | None]:
    """(allowed, code, until, remaining_minutes). ``until`` is when watching ends (allowed) or may resume (refused)."""
    access = member_access.for_user(db, user)
    if access is None:
        return True, None, None, None
    now = now or _now()
    local = _local(now)
    until = None
    if access.schedule is not None:
        windows = _windows(access.schedule, local)
        current = next((w for w in windows if w[0] <= local < w[1]), None)
        if current is None:
            return False, "outside_hours", next((w[0] for w in windows if w[0] > local), None), 0
        until = current[1]
    if access.daily_limit_minutes is None:
        return True, None, until, None
    allowance = access.daily_limit_minutes * 60 + (access.bonus_minutes * 60 if access.bonus_date == local.date() else 0)
    remaining = allowance - seconds_today(db, user.id, now)
    if remaining <= 0:
        return False, "screen_time_up", local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1), 0
    return True, None, until, remaining // 60


def watch_state(db: Session, user: User, now: datetime | None = None) -> dict:
    """``can_watch_now`` as the API returns it (``/api/me/access``, the admin member page)."""
    allowed, code, until, remaining = can_watch_now(db, user, now)
    return {"allowed_now": allowed, "code": code, "until": until, "remaining_minutes": remaining}


def add_screen_time(db: Session, user_id: str, seconds: float, now: datetime | None = None) -> None:
    seconds = round(seconds)
    if seconds <= 0:
        return
    statement = insert(ScreenTime).values(user_id=user_id, day=local_day(now), seconds=seconds)
    with write_transaction(db, name="screen_time"):
        db.execute(statement.on_conflict_do_update(
            index_elements=["user_id", "day"], set_={"seconds": ScreenTime.seconds + statement.excluded.seconds},
        ))


def enforce(db: Session, user: User, now: datetime | None = None, *, fresh: bool = False) -> None:
    """403 when the member may not watch now. ``fresh`` (a playback start) always checks; a running session re-checks
    at most once a minute. A refusal also stops the member's encodes, so a player that ignores the 403 still stops."""
    now = now or _now()
    if not fresh and member_access.for_user(db, user) is not None:
        _count(db, user.id, now)  # a serve of a running stream is watching time, reported or not
    with _lock:
        passed = _last_pass.get(user.id)
    if not fresh and passed is not None and timedelta(0) <= now - passed < timedelta(seconds=RECHECK_SECONDS):
        return
    allowed, code, _, _ = can_watch_now(db, user, now)
    with _lock:
        if allowed:
            _last_pass[user.id] = now
            return
        _last_pass.pop(user.id, None)
    sessions.stop_where(user_id=user.id)
    raise HTTPException(status_code=403, detail=code)  # the UI reads until/remaining from GET /api/me/access


def _count(db: Session, user_id: str, now: datetime, *, stopped: bool = False) -> None:
    """Advance the member's one clock and count the gap since its last count, capped (see the module docstring)."""
    with _lock:
        last, cap = _last_beat.get(user_id, (None, 0))
        gap = (now - last).total_seconds() if last is not None else 0.0
        if 0 <= gap < CREDIT_STEP and last is not None and not stopped:
            return  # too soon to write: the clock keeps running from ``last``
        _last_beat[user_id] = (now, RESUME_SECONDS if stopped else GAP_SECONDS)
    add_screen_time(db, user_id, min(max(gap, 0.0), cap), now)


def heartbeat(db: Session, user: User, now: datetime | None = None, *, stopped: bool = False) -> None:
    """A playback progress report from any client: count the time since the member's previous count, then re-check.
    A player that reports Stopped counts its tail and is not re-checked."""
    now = now or _now()
    _count(db, user.id, now, stopped=stopped)
    if not stopped:
        enforce(db, user, now)


def forget(user_id: str) -> None:
    """Drop the cached allow (a changed limit or a bonus applies on the next report, not a minute later)."""
    with _lock:
        _last_pass.pop(user_id, None)



def require_watch_time(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> None:
    """Route dependency for a running stream's manifests and segments: the once-a-minute re-check."""
    enforce(db, current_user)
