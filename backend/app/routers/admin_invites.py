"""Email invites and the public and local addresses. Admin only."""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AccountToken, User
from app.persistence import write_transaction
from app.security import get_admin_user, get_owner_session_user
from app.services import invites, public_address
from app.services.users import audit_log
from app.services.yt_dlp_service import YtDlpService


class PublicAddressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_address: str | None = None


class LocalAddressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_address: str | None = None


def _address(db: Session) -> dict:
    return {"public_address": YtDlpService(db).get_app_settings().public_address}


def _local_address(db: Session) -> dict:
    return {"local_address": YtDlpService(db).get_app_settings().local_address, "lan_http": settings.lan_http}


def register(app: FastAPI) -> None:
    @app.post("/api/admin/invites", status_code=201)
    def create_invite(body: invites.InviteCreate, admin: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> dict:
        result = invites.create(db, admin, body)
        audit_log.info("invite.create actor=%s invite=%s sent=%s", admin.id, result["id"], result["email_sent"])
        return result

    @app.get("/api/admin/invites")
    def list_invites(admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[dict]:
        rows = db.scalars(select(AccountToken).where(AccountToken.kind == "invite", AccountToken.email.is_not(None))
                          .order_by(AccountToken.created_at.desc()).limit(100)).all()
        return [invites.serialize(db, row) for row in rows]

    @app.post("/api/admin/invites/{invite_id}/resend")
    def resend_invite(invite_id: str, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
        try:
            result = invites.resend(db, admin, invite_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Invitation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_log.info("invite.resend actor=%s invite=%s sent=%s", admin.id, invite_id, result["email_sent"])
        return result

    @app.delete("/api/admin/invites/{invite_id}", status_code=204)
    def revoke_invite(invite_id: str, admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> None:
        from app.services.users import UserService

        try:
            UserService(db).revoke_invitation(admin, invite_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Invitation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/admin/public-address")
    def get_public_address(admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _address(db)

    @app.put("/api/admin/public-address")
    def put_public_address(body: PublicAddressBody, admin: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> dict:
        try:
            value = public_address.normalize(body.public_address)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if value and urlparse(value).hostname == public_address.local_host():
            raise HTTPException(status_code=422, detail="The public address must differ from the local address.")
        with write_transaction(db, name="public_address"):
            YtDlpService(db).ensure_app_settings().public_address = value
        public_address.set_origin(value)
        return _address(db)

    @app.get("/api/admin/local-address")
    def get_local_address(admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _local_address(db)

    @app.put("/api/admin/local-address")
    def put_local_address(body: LocalAddressBody, admin: User = Depends(get_owner_session_user), db: Session = Depends(get_db, scope="function")) -> dict:
        try:
            value = public_address.normalize_local(body.local_address)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with write_transaction(db, name="local_address"):
            YtDlpService(db).ensure_app_settings().local_address = value
        public_address.set_local_origin(value)
        audit_log.info("local_address.set actor=%s set=%s", admin.id, value is not None)
        return _local_address(db)
