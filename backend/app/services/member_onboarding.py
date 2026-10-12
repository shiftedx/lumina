from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.models import User
from app.services.member_recommendations import MemberInterestService


class MemberOnboardingService:
    """Own the durable, member-scoped onboarding decision for one Household member.

    A member is ``pending`` until they either complete first-run setup (choosing
    Interest categories, or continuing with none) or explicitly skip it. Both
    transitions are idempotent: replaying them from a reload, a second tab, or a
    second device leaves the same durable state. Skip never regresses a real
    completion, so a stale session cannot erase interests another session saved.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"

    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def status_of(member: User) -> str:
        return member.onboarding_status or MemberOnboardingService.COMPLETED

    def complete(self, member: User, keys: Iterable[str]) -> tuple[str, tuple[str, ...]]:
        record = self._require(member)
        selected = MemberInterestService(self._db).replace(record, keys)
        record.onboarding_status = self.COMPLETED
        self._db.flush()
        return self.COMPLETED, selected

    def skip(self, member: User) -> str:
        record = self._require(member)
        if self.status_of(record) == self.PENDING:
            record.onboarding_status = self.SKIPPED
            self._db.flush()
        return self.status_of(record)

    def _require(self, member: User) -> User:
        # get_current_user may hand us a member attached to a different session
        # than this request's writer; resolve the row in the writer's identity
        # map so the status mutation is the one that durably commits.
        record = self._db.get(User, member.id)
        if record is None:
            raise ValueError("Unknown household member")
        return record
