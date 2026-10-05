"""Admin Activity: now playing, stop, history, server load. Admin only, like /api/admin/overview."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MemberAccess, User
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services import activity, member_access, screen_time


class Person(BaseModel):
    id: str
    name: str


class Client(BaseModel):
    kind: str
    name: str
    device: str | None


class VideoOut(BaseModel):
    from_: str | None = Field(None, alias="from")  # "from" is a Python keyword; responses go out under the alias
    to: str | None = None
    height: int | None = None
    tonemap: bool = False


class AudioOut(BaseModel):
    from_: str | None = Field(None, alias="from")
    to: str | None = None


class ActivitySession(BaseModel):
    id: str
    source: str
    user: Person
    title: str
    subtitle: str | None
    item_id: str | None
    artwork_url: str | None
    client: Client
    method: str
    video: VideoOut | None
    audio: AudioOut | None
    hardware: str | None
    speed: float | None
    throttled: bool
    position_seconds: float | None
    duration_seconds: float | None
    started_at: datetime
    last_seen_at: datetime
    stoppable: bool


class DownloadRow(BaseModel):
    id: str
    title: str | None
    user: Person
    progress: float | None
    speed_bytes: float | None
    started_at: datetime | None


class RecordingRow(BaseModel):
    id: str
    title: str | None
    user: Person
    status: str
    started_at: datetime | None


class Memory(BaseModel):
    used_bytes: int
    total_bytes: int


class Hardware(BaseModel):
    mode: str
    active: str | None
    disabled: bool
    failures: int
    fallbacks: int


class ServerStats(BaseModel):
    cpu_percent: float | None
    memory: Memory | None
    load_average: list[float] | None
    uptime_seconds: float | None
    transcoder_cpu_percent: float | None
    ffmpeg_processes: int
    hardware: Hardware


class ActivityResponse(BaseModel):
    generated_at: datetime
    sessions: list[ActivitySession]
    downloads: list[DownloadRow]
    recordings: list[RecordingRow]
    server: ServerStats


class HistoryRow(BaseModel):
    id: str
    source: str
    user: Person
    title: str
    subtitle: str | None
    item_id: str | None
    client: Client
    method: str
    hardware: str | None
    video: VideoOut | None
    started_at: datetime
    ended_at: datetime
    watched_seconds: float
    stopped_by_admin: bool


class HistoryResponse(BaseModel):
    items: list[HistoryRow]
    next_before: datetime | None


class WatchState(BaseModel):
    """can_watch_now for one member: until = when watching ends (allowed) or may resume (refused)."""
    allowed_now: bool
    code: str | None
    until: datetime | None
    remaining_minutes: int | None


class MemberActivity(WatchState):
    today_seconds: int
    recent: list[HistoryRow]


class BonusIn(BaseModel):
    minutes: int = Field(ge=1, le=24 * 60)


class BonusOut(WatchState):
    bonus_date: date
    bonus_minutes: int


def _member_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return user


def register(app: FastAPI, relays: tuple) -> None:
    activity.install(relays)
    admin = [Depends(get_admin_user)]

    @app.get("/api/admin/activity", response_model=ActivityResponse, dependencies=admin)
    def get_activity(db: Session = Depends(get_db, scope="function")) -> dict:
        return activity.snapshot(db, relays)

    @app.post("/api/admin/activity/{session_id}/stop", status_code=204, dependencies=admin)
    def stop_activity(session_id: str) -> Response:
        if not activity.stop(session_id, relays):
            raise HTTPException(status_code=404, detail="That session is no longer playing.")
        return Response(status_code=204)

    @app.get("/api/admin/activity/history", response_model=HistoryResponse, dependencies=admin)
    def get_history(
        user_id: str | None = Query(None, max_length=36), q: str | None = Query(None, max_length=200), before: datetime | None = None,
        limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return activity.history(db, user_id=user_id, q=q, before=before, limit=limit)

    @app.get("/api/admin/members/{user_id}/activity", response_model=MemberActivity, dependencies=admin)
    def member_activity(user_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
        user = _member_or_404(db, user_id)
        recent = activity.history(db, user_id=user_id, q=None, before=None, limit=20)["items"]
        return {**screen_time.watch_state(db, user), "today_seconds": screen_time.seconds_today(db, user_id), "recent": recent}

    @app.post("/api/admin/members/{user_id}/access/bonus", response_model=BonusOut, dependencies=admin)
    def add_bonus(user_id: str, payload: BonusIn, db: Session = Depends(get_db, scope="function")) -> dict:
        """Extra minutes today on top of the daily limit; a second bonus the same day adds to the first."""
        user = _member_or_404(db, user_id)
        row = db.get(MemberAccess, user_id)
        if row is None:
            raise HTTPException(status_code=409, detail="This member has no limits.")
        today = member_access.today()
        with write_transaction(db, name="member_access_bonus"):
            row.bonus_minutes = (row.bonus_minutes if row.bonus_date == today else 0) + payload.minutes
            row.bonus_date = today
        member_access.invalidate(user_id)
        return {**screen_time.watch_state(db, user), "bonus_date": today, "bonus_minutes": row.bonus_minutes}
