"""Member access API (member access spec, ADR 0019): an admin sets a member's libraries and limits; a member reads their own."""
from __future__ import annotations

from typing import Any

import logging

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MemberAccess, SourceAutomation, User, utcnow
from app.persistence import write_transaction
from app.schemas import MemberAccessIn, MemberAccessOut, MemberFollowIn, MemberFollowOut, MyAccessResponse, SectionInfo
from app.security import get_admin_user, get_current_user
from app.services import member_access
from app.services.member_follows import CHANNEL_SOURCE_TYPE, FollowRequest, MemberFollowService

logger = logging.getLogger(__name__)


def _member(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return user


def _viewer(db: Session, user_id: str) -> User:
    user = _member(db, user_id)
    if user.role == "admin":
        raise HTTPException(status_code=409, detail="Admins always see every library with no limits.")
    return user


def _follows(db: Session, user_id: str) -> list[SourceAutomation]:
    return (
        db.query(SourceAutomation)
        .filter(SourceAutomation.user_id == user_id, SourceAutomation.source_type == CHANNEL_SOURCE_TYPE)
        .order_by(SourceAutomation.label, SourceAutomation.id)
        .all()
    )


def _out(db: Session, user_id: str) -> dict[str, Any]:
    row = db.get(MemberAccess, user_id)
    access = member_access.as_dict(member_access.effective(row) if row else None)
    return {
        **access, "restricted": row is not None,
        "bonus_minutes_today": access["bonus_minutes"] if access["bonus_date"] == member_access.today() else 0,
        "screen_time_today_seconds": member_access.screen_time_seconds(db, [user_id]).get(user_id, 0),
    }


def _fill() -> None:
    try:
        logger.info("ratings filled in: %s", member_access.fill_ratings(utcnow()))
    except RuntimeError:
        pass  # one is already running


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]

    @app.get("/api/admin/members/{user_id}/access", response_model=MemberAccessOut, dependencies=admin)
    def get_access(user_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
        _member(db, user_id)
        return _out(db, user_id)

    @app.put("/api/admin/members/{user_id}/access", response_model=MemberAccessOut, dependencies=admin)
    def put_access(user_id: str, payload: MemberAccessIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
        if _member(db, user_id).role == "admin":
            raise HTTPException(status_code=409, detail="Admins always see every library with no limits.")
        with write_transaction(db, name="member_access"):
            member_access.save(db, user_id, payload.model_dump(mode="json"))
        return _out(db, user_id)

    # Follows on a member's behalf (#166): followed_only refuses the member's own new follows, so the admin keeps the list.
    @app.get("/api/admin/members/{user_id}/follows", response_model=list[MemberFollowOut], dependencies=admin)
    def get_follows(user_id: str, db: Session = Depends(get_db, scope="function")) -> list[SourceAutomation]:
        _member(db, user_id)
        return _follows(db, user_id)

    @app.post("/api/admin/members/{user_id}/follows", response_model=list[MemberFollowOut], dependencies=admin)
    def add_follow(user_id: str, payload: MemberFollowIn, db: Session = Depends(get_db, scope="function")) -> list[SourceAutomation]:
        member = _viewer(db, user_id)
        follows = MemberFollowService(db)
        name = payload.display_name.strip() or payload.source_url.strip()
        planned, rejected = follows.validate_and_plan(member, [FollowRequest(source_url=payload.source_url, display_name=name)])
        if rejected and rejected[0].status == "invalid":
            raise HTTPException(status_code=422, detail=rejected[0].reason or "Unusable channel address.")
        with write_transaction(db, name="admin_member_follow"):  # validated above: no DNS inside the write
            follows.create_planned(member, planned)
        return _follows(db, user_id)

    @app.delete("/api/admin/members/{user_id}/follows/{follow_id}", status_code=204, dependencies=admin)
    def remove_follow(user_id: str, follow_id: str, db: Session = Depends(get_db, scope="function")) -> None:
        _viewer(db, user_id)
        follow = db.get(SourceAutomation, follow_id)
        if follow is None or follow.user_id != user_id or follow.source_type != CHANNEL_SOURCE_TYPE:
            raise HTTPException(status_code=404, detail="Follow not found")
        with write_transaction(db, name="admin_member_unfollow"):
            db.delete(follow)

    @app.get("/api/admin/sections", response_model=list[SectionInfo], dependencies=admin)
    def get_sections(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
        return member_access.section_counts(db)

    @app.post("/api/admin/ratings/fill", status_code=202, dependencies=admin)
    def fill_ratings(background: BackgroundTasks) -> dict[str, bool]:
        """"Fill in ratings": runs after the response; a second press while one runs is a no-op."""
        background.add_task(_fill)
        return {"started": True}

    @app.get("/api/me/access", response_model=MyAccessResponse)
    def get_my_access(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
        access = member_access.for_user(db, current_user)
        effective = access or member_access.EffectiveAccess()
        return {
            "restricted": access is not None,
            "sections": None if effective.sections is None else sorted(effective.sections),
            "blocked_streaming": effective.blocked_streaming,
            "followed_only": bool(effective.streaming.get("followed_only")),
            "can_download": effective.can_download,
            "schedule_state": (state := member_access.schedule_state(db, current_user, access)),
            **({"allowed_now": state["allowed_now"], "until": state["until"], "remaining_minutes": state["remaining_minutes"]} if state else {}),
        }
