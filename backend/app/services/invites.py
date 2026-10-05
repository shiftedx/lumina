"""Email invites: a 7-day single-use link that carries the libraries and limits the invitee gets.

The existing account-token machinery owns the secret (digest only); this adds the email, the access preset, sending
and resend. The redeem link keeps the existing fragment form, which keeps the secret out of server logs.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models import AccountToken, AppSettings, MemberAccess, RequestPolicy, StorageRoot, User
from app.persistence import write_transaction
from app.schemas import MemberAccessIn
from app.security import session_digest, utcnow
from app.services import invite_email, public_address
from app.services.requests import notify
from app.services.requests.engine import KINDS
from app.services.users import UserService

logger = logging.getLogger(__name__)
INVITE_DAYS = 7
SERVER_NAME = "Lumina"
SECTION_NAMES = {"movies": "Movies", "shows": "TV shows", "anime": "Anime", "music": "Music", "vault": "Videos and downloads"}


class InvitePreset(MemberAccessIn):
    """What the invitee gets, validated like an admin's member access save (422 on a bad schedule or Section).
    Defaults are the safe guest: only the listed sections, no downloads, no requests."""

    sections: list[str] | None = Field(default_factory=list, max_length=64)  # None = every section
    can_download: bool = False
    can_request: bool = False


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
    display_name: str | None = Field(default=None, max_length=120)
    access: InvitePreset = Field(default_factory=InvitePreset)
    send: bool = True


def library_names(db: Session, sections: list[str] | None) -> list[str]:
    names = []
    for section in sections or []:
        if section.startswith("root:"):
            root = db.get(StorageRoot, section[5:])
            names.append(root.label if root else "A shared folder")
        else:
            names.append(SECTION_NAMES.get(section, section.capitalize()))
    return names


def _status(record: AccountToken) -> str:
    return "used" if record.used_at else "revoked" if record.revoked_at else "expired" if record.expires_at <= utcnow() else "pending"


def serialize(db: Session, record: AccountToken) -> dict:
    return {
        "id": record.id, "email": record.email, "status": _status(record), "created_at": record.created_at,
        "expires_at": record.expires_at, "sent_at": record.sent_at, "access": record.access or {},
        "libraries": library_names(db, (record.access or {}).get("sections")),
    }


def _link(token: str) -> str:
    return f"{public_address.link_base()}/#invite={token}"


def _deliver(db: Session, inviter: User, record: AccountToken, link: str) -> bool:
    """Send now; False when SMTP is missing or the server refuses (type only is logged, never the address or token)."""
    smtp = notify.Smtp.of(db.get(AppSettings, 1))
    if smtp is None or not record.email:
        return False
    subject, text, html = invite_email.render(
        inviter=inviter.display_name, server=SERVER_NAME, link=link, expires=record.expires_at,
        libraries=library_names(db, (record.access or {}).get("sections")),
    )
    try:
        notify.send(smtp, record.email, subject, text, html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invite email failed: %s", type(exc).__name__)
        return False
    with write_transaction(db, name="invite_sent"):
        record.sent_at = utcnow()
    return True


def create(db: Session, inviter: User, body: InviteCreate) -> dict:
    service = UserService(db)
    record, token = service.issue_account_token(inviter, "invite", role="viewer", expires_in_hours=INVITE_DAYS * 24, audit=False)
    with write_transaction(db, name="invite_details"):
        record.email = body.email.strip().lower()
        record.access = {**body.access.model_dump(), "display_name": (body.display_name or "").strip() or None}
    link = _link(token)
    sent = _deliver(db, inviter, record, link) if body.send else False
    return {**serialize(db, record), "invitation_url": link, "email_sent": sent}


def resend(db: Session, inviter: User, invite_id: str) -> dict:
    """Same invitation, fresh secret and a fresh 7 days (the old link stops working; only digests are stored)."""
    with write_transaction(db, name="invite_resend"):
        record = db.get(AccountToken, invite_id, populate_existing=True)
        if record is None or record.kind != "invite":
            raise LookupError(invite_id)
        if record.used_at or record.revoked_at:
            raise ValueError("This invitation was already used or revoked; send a new one.")
        token = secrets.token_urlsafe(32)
        record.token_digest, record.expires_at = session_digest(token), utcnow() + timedelta(days=INVITE_DAYS)
    link = _link(token)
    return {**serialize(db, record), "invitation_url": link, "email_sent": _deliver(db, inviter, record, link)}


def apply_access(db: Session, user: User, record: AccountToken) -> None:
    """Called inside the redeem transaction: the member's access row and request-policy overrides from the preset."""
    preset = dict(record.access or {})
    preset.pop("display_name", None)
    can_request = bool(preset.pop("can_request", False))
    for kind in KINDS:
        db.add(RequestPolicy(id=str(uuid.uuid4()), user_id=user.id, kind=kind, can_request=can_request, auto_approve=False))
    preset["streaming"] = preset.get("streaming") or {}
    db.add(MemberAccess(user_id=user.id, **preset))
