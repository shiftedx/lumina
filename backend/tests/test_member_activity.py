from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import PlaybackHistory
from app.security import hash_password, utcnow
from app.services import activity
from support import make_user
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401


def _history(id: str, user_id: str, ended_at, watched_seconds: float) -> PlaybackHistory:  # noqa: ANN001
    return PlaybackHistory(
        id=id, user_id=user_id, user_name=user_id, source="library", title="Title", subtitle=None, item_id=None,
        client={"kind": "web", "name": "Lumina web", "device": None}, method="direct", started_at=ended_at - timedelta(minutes=1),
        ended_at=ended_at, watched_seconds=watched_seconds, stopped_by_admin=False,
    )


@pytest.mark.parametrize("hour", [0, 12, 23])
def test_member_activity_is_own_utc_14_day_recorded_session_summary(client, factory, monkeypatch, hour) -> None:  # noqa: ANN001
    now = datetime(2026, 1, 15, hour, 30)
    monkeypatch.setattr(activity, "member_summary", partial(activity.member_summary, now=now))
    recent = now - timedelta(days=1)
    today = now.replace(hour=0, minute=0)
    with factory.begin() as db:
        db.add(make_user("member", username="member", password_hash=hash_password(PASSWORD)))
        db.add_all((
            _history("mine-recent", "u1", recent, 61.6),
            _history("mine-today", "u1", today, 120),
            _history("member", "member", now - timedelta(hours=2), 999),
            _history("old", "u1", now - timedelta(days=15), 300),
        ))
    assert client.get("/api/me/activity-summary").status_code == 401
    assert login(client)
    body = client.get("/api/me/activity-summary").json()
    assert body["period_days"] == 14 and body["timezone"] == "UTC"
    assert (body["watched_seconds"], body["sessions"], body["active_days"]) == (182, 2, 2)
    days = {day["date"]: day for day in body["days"]}
    assert len(days) == 14
    assert days[today.date().isoformat()]["watched_seconds"] == 120
    assert days[recent.date().isoformat()] == {"date": recent.date().isoformat(), "watched_seconds": 62, "sessions": 1}
    assert body["latest_activity_at"] == today.isoformat()


def test_member_activity_never_exposes_another_member(client: TestClient, factory) -> None:  # noqa: ANN001
    now = utcnow().replace(microsecond=0)
    with factory.begin() as db:
        db.add(make_user("member", username="member", password_hash=hash_password(PASSWORD)))
        db.add(_history("member", "member", now - timedelta(hours=2), 999))
    assert login(client)
    body = client.get("/api/me/activity-summary").json()
    assert (body["watched_seconds"], body["sessions"], body["active_days"], body["latest_activity_at"]) == (0, 0, 0, None)

