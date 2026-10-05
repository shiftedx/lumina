"""Bring a household's Jellyfin users and their watch history over to Lumina (ADR 0010 amendment).

One Jellyfin administrator sign-in reads every chosen user's history. Each user lands on the Lumina member with
the same username, or a new household member, and is written in their own write transaction, so one failure
never undoes another. A new member has no password until they redeem their single-use reset link.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import JellyfinHouseholdMember
from app.models import User
from app.persistence import write_transaction
from app.services.jellyfin_history import apply, plan
from app.services.jellyfin_history_client import JellyfinHistory, JellyfinImportError, JellyfinSession, JellyfinUser, read_history
from app.services.users import RESET_LINK_HOURS, UserService, audit_log, reset_link_url

logger = logging.getLogger(__name__)
MAX_USERNAME = 80  # User.username
INVALID_NAME = "This Jellyfin name cannot be a Lumina username."
NOT_SELECTED = "Not selected."
SAVE_FAILED = "Lumina could not save this member's history, so nothing changed for them."
MEMBER_GONE = "This Lumina member no longer exists, so nothing changed for them."


@dataclass(frozen=True)
class Pairing:
    jellyfin: JellyfinUser
    action: str  # "import" | "create" | "skip"
    username: str | None = None  # the Lumina member this user lands on
    user_id: str | None = None  # an existing member; None when one will be created
    reason: str | None = None  # why "skip"

    def row(self, **outcome: Any) -> JellyfinHouseholdMember:
        return JellyfinHouseholdMember(
            jellyfin_id=self.jellyfin.id, jellyfin_name=self.jellyfin.name, disabled=self.jellyfin.disabled,
            action=self.action, lumina_username=self.username, reason=self.reason, **outcome,
        )


def pair(db: Session, admin: User, admin_jellyfin_id: str, users: list[JellyfinUser]) -> list[Pairing]:
    """The signed-in Jellyfin administrator lands on this admin; everyone else on the member with the same
    (lowercased, like every Lumina username) name, or a new member. An unusable name, or a second claim, is skipped."""
    members = dict(db.execute(select(User.username, User.id)).all())
    claimed: dict[str, str] = {}  # Lumina username -> the Jellyfin name paired with it
    pairings: list[Pairing] = []
    for user in sorted(users, key=lambda user: (user.id != admin_jellyfin_id, user.name.casefold())):
        username = admin.username if user.id == admin_jellyfin_id else user.name.strip().lower()
        if not username or len(username) > MAX_USERNAME:
            pairings.append(Pairing(user, "skip", reason=INVALID_NAME))
        elif username in claimed:
            pairings.append(Pairing(user, "skip", reason=f"{claimed[username]} is already paired with the Lumina member {username}."))
        else:
            claimed[username] = user.name
            pairings.append(Pairing(user, "import" if username in members else "create", username, members.get(username)))
    return pairings


def run(
    db: Session, admin: User, session: JellyfinSession, admin_jellyfin_id: str, users: list[JellyfinUser],
    chosen: set[str] | None,
) -> list[JellyfinHouseholdMember]:
    """One row per Jellyfin user. ``chosen`` None previews (reads only); otherwise the chosen users are brought over.

    Histories are read one user at a time, outside any write transaction, and released before the next is read.
    """
    rows: list[JellyfinHouseholdMember] = []
    for pairing in pair(db, admin, admin_jellyfin_id, users):
        if pairing.action != "skip" and chosen is not None and pairing.jellyfin.id not in chosen:
            pairing = Pairing(pairing.jellyfin, "skip", reason=NOT_SELECTED)
        if pairing.action == "skip":
            rows.append(pairing.row())
            continue
        try:
            history = read_history(session, pairing.jellyfin.id)
        except JellyfinImportError as exc:
            rows.append(pairing.row(error=str(exc)))
            continue
        if chosen is None:
            # A member-to-be is planned as the viewer they will become; the transient User never joins the session.
            member = db.get(User, pairing.user_id) if pairing.user_id else User(id=str(uuid.uuid4()), username=pairing.username, role="viewer")
            rows.append(pairing.row(summary=plan(db, member, history).summary()) if member else pairing.row(error=MEMBER_GONE))
            continue
        try:
            rows.append(_bring_over(db, admin, pairing, history))
        except Exception as exc:  # one member's failure never stops the rest, nor loses the links already issued
            logger.warning("jellyfin_household.save_failed jellyfin_user=%s error=%s", pairing.jellyfin.id, type(exc).__name__)
            rows.append(pairing.row(error=SAVE_FAILED))
    audit_log.info("jellyfin_household.%s actor=%s users=%d", "preview" if chosen is None else "import", admin.id, len(rows))
    return rows


def _bring_over(db: Session, admin: User, pairing: Pairing, history: JellyfinHistory) -> JellyfinHouseholdMember:
    """One member in one write: the new account and its link, then the plan recomputed against what is true now."""
    users, link, record = UserService(db), {}, None
    with write_transaction(db, name="jellyfin_household_import"):
        if pairing.user_id:
            member = db.get(User, pairing.user_id)
            if member is None:  # deleted since pairing
                return pairing.row(error=MEMBER_GONE)
        else:
            member = users.create_member_without_password(admin, pairing.username or "", pairing.jellyfin.name)
            record, token = users.issue_account_token(admin, "reset", user_id=member.id, expires_in_hours=RESET_LINK_HOURS, audit=False)
            link = {"reset_url": reset_link_url(token), "reset_expires_at": record.expires_at}
        result = plan(db, member, history)
        apply(db, member, result)
    if record:  # audited only once the member's write has committed
        audit_log.info("user.create actor=%s user=%s source=jellyfin", admin.id, member.id)
        audit_log.info("reset.issue actor=%s token_id=%s role=None user=%s", admin.id, record.id, member.id)
    return pairing.row(summary=result.summary(), **link)
