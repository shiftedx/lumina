"""Pair and import a household from Jellyfin or Plex (ADR 0010 amendments).

A provider-specific session reads each chosen member's history. Each user lands on the Lumina member with
the same username, or a new household member, and is written in their own write transaction, so one failure
never undoes another. A new member has no password until they redeem their single-use reset link.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import HouseholdTransferMember
from app.models import User
from app.persistence import write_transaction
from app.services.jellyfin_history import apply, plan
from app.services.jellyfin_history_client import JellyfinHistory, JellyfinImportError, JellyfinUser
from app.services.users import RESET_LINK_HOURS, UserService, audit_log, reset_link_url

logger = logging.getLogger(__name__)
MAX_USERNAME = 80  # User.username
INVALID_NAME = "This {source} name cannot be a Lumina username."
NOT_SELECTED = "Not selected."
SAVE_FAILED = "Lumina could not save this member's history, so nothing changed for them."
READ_FAILED = "{source} history could not be read for this member."
MEMBER_GONE = "This Lumina member no longer exists, so nothing changed for them."


@dataclass(frozen=True)
class Pairing:
    member: JellyfinUser
    action: Literal["import", "create", "skip"]
    username: str | None = None  # the Lumina member this user lands on
    user_id: str | None = None  # an existing member; None when one will be created
    reason: str | None = None  # why "skip"

    def row(self, **outcome: Any) -> HouseholdTransferMember:
        return HouseholdTransferMember(
            source_id=self.member.id, source_name=self.member.name, disabled=self.member.disabled,
            action=self.action, lumina_username=self.username, reason=self.reason, **outcome,
        )


@dataclass(frozen=True)
class ReadProgress:
    """A selected source member whose network history read is about to begin."""

    pairing: Pairing
    completed: int
    total: int


def pair(db: Session, admin: User, source_owner_id: str, users: list[JellyfinUser], *, source: str) -> list[Pairing]:
    """The signed-in source owner lands on this admin; everyone else on the member with the same
    (lowercased, like every Lumina username) name, or a new member. An unusable name, or a second claim, is skipped."""
    members = dict(db.execute(select(User.username, User.id)).all())
    claimed: dict[str, str] = {}  # Lumina username -> the source name paired with it
    pairings: list[Pairing] = []
    for user in sorted(users, key=lambda user: (user.id != source_owner_id, user.name.casefold())):
        username = admin.username if user.id == source_owner_id else user.name.strip().lower()
        if not username or len(username) > MAX_USERNAME:
            pairings.append(Pairing(user, "skip", reason=INVALID_NAME.format(source=source)))
        elif username in claimed:
            pairings.append(Pairing(user, "skip", reason=f"{claimed[username]} is already paired with the Lumina member {username}."))
        else:
            claimed[username] = user.name
            pairings.append(Pairing(user, "import" if username in members else "create", username, members.get(username)))
    return pairings


def discover(
    db: Session, admin: User, source_owner_id: str, users: list[JellyfinUser], *,
    source: str, errors: dict[str, str] | None = None,
) -> list[HouseholdTransferMember]:
    """Pair source accounts without reading history or changing Lumina."""
    failures = errors or {}
    return [pairing.row(error=failures.get(pairing.member.id)) for pairing in pair(
        db, admin, source_owner_id, users, source=source,
    )]


def iter_run(
    db: Session, admin: User, source_owner_id: str, users: list[JellyfinUser],
    chosen: set[str] | None, *, read: Callable[[str], JellyfinHistory], source: str,
    progress: bool = False, include_unselected: bool = True,
) -> Iterator[HouseholdTransferMember | ReadProgress]:
    """One row per source member. ``chosen`` None previews (reads only); otherwise the chosen users are brought over.

    Histories are read one user at a time, outside any write transaction, and released before the next is read.
    ``progress`` yields a marker before each selected network read for finite streaming callers.
    """
    pairings = pair(db, admin, source_owner_id, users, source=source)
    selected = [
        pairing for pairing in pairings
        if pairing.action != "skip" and (chosen is None or pairing.member.id in chosen)
    ]
    completed = 0
    emitted = 0
    try:
        for pairing in pairings:
            if pairing.action != "skip" and chosen is not None and pairing.member.id not in chosen:
                if include_unselected:
                    emitted += 1
                    yield Pairing(pairing.member, "skip", reason=NOT_SELECTED).row()
                continue
            if pairing.action == "skip":
                if include_unselected:
                    emitted += 1
                    yield pairing.row()
                continue
            if progress:
                yield ReadProgress(pairing, completed, len(selected))
            try:
                history = read(pairing.member.id)
            except JellyfinImportError as exc:
                row = pairing.row(error=str(exc))
            except Exception as exc:
                logger.warning("%s_household.read_failed error=%s", source.lower(), type(exc).__name__)
                row = pairing.row(error=READ_FAILED.format(source=source))
            else:
                if chosen is None:
                    # A member-to-be is planned as the viewer they will become; the transient User never joins the session.
                    member = db.get(User, pairing.user_id) if pairing.user_id else User(
                        id=str(uuid.uuid4()), username=pairing.username, role="viewer",
                    )
                    row = pairing.row(summary=plan(db, member, history).summary()) if member else pairing.row(error=MEMBER_GONE)
                else:
                    try:
                        row = _bring_over(db, admin, pairing, history, source=source)
                    except Exception as exc:  # one member's failure never stops the rest, nor loses the links already issued
                        logger.warning("%s_household.save_failed error=%s", source.lower(), type(exc).__name__)
                        row = pairing.row(error=SAVE_FAILED)
            completed += 1
            emitted += 1
            yield row
    finally:
        audit_log.info(
            "%s_household.%s actor=%s users=%d", source.lower(), "preview" if chosen is None else "import", admin.id, emitted,
        )


def run(
    db: Session, admin: User, source_owner_id: str, users: list[JellyfinUser],
    chosen: set[str] | None, *, read: Callable[[str], JellyfinHistory], source: str,
) -> list[HouseholdTransferMember]:
    """Compatibility wrapper for the existing JSON preview/import endpoints."""
    return cast(list[HouseholdTransferMember], list(iter_run(
        db, admin, source_owner_id, users, chosen, read=read, source=source,
    )))


def _bring_over(db: Session, admin: User, pairing: Pairing, history: JellyfinHistory, *, source: str) -> HouseholdTransferMember:
    """One member in one write: the new account and its link, then the plan recomputed against what is true now."""
    users, link, record = UserService(db), {}, None
    with write_transaction(db, name=f"{source.lower()}_household_import"):
        if pairing.user_id:
            member = db.get(User, pairing.user_id)
            if member is None:  # deleted since pairing
                return pairing.row(error=MEMBER_GONE)
        else:
            member = users.create_member_without_password(admin, pairing.username or "", pairing.member.name)
            record, token = users.issue_account_token(admin, "reset", user_id=member.id, expires_in_hours=RESET_LINK_HOURS, audit=False)
            link = {"reset_url": reset_link_url(token), "reset_expires_at": record.expires_at}
        result = plan(db, member, history)
        apply(db, member, result)
    if record:  # audited only once the member's write has committed
        audit_log.info("user.create actor=%s user=%s source=%s", admin.id, member.id, source.lower())
        audit_log.info("reset.issue actor=%s token_id=%s role=None user=%s", admin.id, record.id, member.id)
    return pairing.row(summary=result.summary(), **link)
