"""A member's own recorded playback summary for the account activity sheet."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services import activity


class MemberActivityDay(BaseModel):
    date: date
    watched_seconds: int = Field(ge=0)
    sessions: int = Field(ge=0)


class MemberActivitySummary(BaseModel):
    generated_at: datetime
    period_days: int = Field(ge=1)
    timezone: str
    watched_seconds: int = Field(ge=0)
    sessions: int = Field(ge=0)
    active_days: int = Field(ge=0)
    latest_activity_at: datetime | None
    days: list[MemberActivityDay]


def register(app: FastAPI) -> None:
    @app.get("/api/me/activity-summary", response_model=MemberActivitySummary)
    def get_member_activity_summary(
        current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return activity.member_summary(db, current_user.id)
