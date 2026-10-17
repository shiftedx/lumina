"""Owner-controlled household resource defaults and member overrides."""
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.persistence import queue_after_commit, write_transaction
from app.security import get_admin_user, get_current_user
from app.services.resource_policy import HouseholdResources, ResourceAllowances, ResourceOverrides, configure_runtime, effective, household
from app.services.yt_dlp_service import YtDlpService


def _member(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Member not found")
    return user


def _out(db: Session, user: User) -> dict:
    return {"overrides": user.resource_overrides or {}, "effective": effective(db, user.id).model_dump()}


def register(app: FastAPI, on_change: Callable[[], object] | None = None) -> None:
    admin = [Depends(get_admin_user)]

    @app.get("/api/admin/resources", response_model=HouseholdResources, dependencies=admin)
    def get_resources(db: Session = Depends(get_db, scope="function")) -> HouseholdResources:
        return household(db)

    @app.put("/api/admin/resources", response_model=HouseholdResources, dependencies=admin)
    def put_resources(payload: HouseholdResources, db: Session = Depends(get_db, scope="function")) -> HouseholdResources:
        with write_transaction(db, name="household_resources"):
            # A default change must leave every saved override resolvable.
            for (overrides,) in db.query(User.resource_overrides).all():
                try:
                    ResourceAllowances.model_validate({**payload.defaults.model_dump(), **(overrides or {})})
                except ValidationError as exc:
                    raise HTTPException(422, "Defaults conflict with a member override. Adjust that override first.") from exc
            record = YtDlpService(db).ensure_app_settings()
            record.resource_policy = payload.model_dump()
            queue_after_commit(db, lambda: configure_runtime(payload))
            if on_change:
                queue_after_commit(db, on_change)
            db.flush()
        return payload

    @app.get("/api/admin/members/{user_id}/resources", dependencies=admin)
    def get_member_resources(user_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
        return _out(db, _member(db, user_id))

    @app.put("/api/admin/members/{user_id}/resources", dependencies=admin)
    def put_member_resources(user_id: str, payload: ResourceOverrides, db: Session = Depends(get_db, scope="function")) -> dict:
        with write_transaction(db, name="member_resources"):
            user = _member(db, user_id)
            overrides = payload.model_dump(exclude_none=True)
            try:
                ResourceAllowances.model_validate({**household(db).defaults.model_dump(), **overrides})
            except ValidationError as exc:
                raise HTTPException(422, "Member resource allowances conflict. Check bandwidth and playback slots.") from exc
            user.resource_overrides = overrides
            policy = household(db)
            queue_after_commit(db, lambda: configure_runtime(policy))
            if on_change:
                queue_after_commit(db, on_change)
            db.flush()
        return _out(db, user)

    @app.get("/api/me/resources")
    def get_my_resources(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _out(db, user)

    @app.get("/api/me/resource-jobs")
    def get_my_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[dict]:
        from app.services.ai_resources import MODELS
        kinds = ("enrichment", "summary", "graph", "recap")
        result = []
        for kind, model in zip(kinds, MODELS):
            for job in db.query(model).filter(model.requested_by == user.id, model.state.in_(("pending", "queued", "running"))).order_by(model.created_at).all():
                result.append({"id": job.id, "kind": kind, "state": job.state})
        return result

    @app.post("/api/me/resource-jobs/{kind}/{job_id}/cancel")
    def cancel_my_job(kind: str, job_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db, scope="function")) -> dict:
        from app.services.ai_resources import MODELS
        from app.services import local_asr, summaries
        model = dict(zip(("enrichment", "summary", "graph", "recap"), MODELS)).get(kind)
        job = db.get(model, job_id) if model else None
        if job is None or job.requested_by != user.id:
            raise HTTPException(404, "Job not found")
        if job.state not in {"pending", "queued", "running"}:
            return {"state": job.state}
        registry = local_asr.jobs if kind == "enrichment" else summaries.jobs
        if not registry.cancel(job_id):
            with write_transaction(db, name="member_cancel_ai_job"):
                job.state = "canceled"
                db.flush()
        return {"state": "canceling" if registry.is_active(job_id) else "canceled"}
