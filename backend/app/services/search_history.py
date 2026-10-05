from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SearchHistoryEntry, User, utcnow
from app.persistence import write_transaction

# A flat cap trimmed on every write is enough for one member's recent
# searches; a rolling/async trim job would only matter at a write volume this
# feature never sees.
MAX_ENTRIES = 50


@dataclass(frozen=True)
class SearchHistoryRecord:
    id: str
    query: str
    searched_at: datetime


class SearchHistoryService:
    """Record, list, and clear one Household member's search history.

    Bounded at ``MAX_ENTRIES`` per member (oldest trimmed on write, most-recent
    first) and deduped case-insensitively: re-searching an existing query
    touches it to the front instead of storing a duplicate. Recording never
    raises for a blank/oversized query -- the caller (a fire-and-forget client
    write) must never have a failed history write abort the search flow.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def record(self, member: User, query: str) -> SearchHistoryRecord | None:
        text = query.strip()
        if not text or len(text) > 500:
            return None
        key = text.lower()
        with write_transaction(self._db, name="search_history_record"):
            existing = self._existing(member, key)
            if existing is not None:
                existing.query = text
                existing.searched_at = utcnow()
                self._db.flush()
                row = existing
            else:
                row = SearchHistoryEntry(id=str(uuid.uuid4()), user_id=member.id, query=text, query_key=key, searched_at=utcnow())
                try:
                    # A SAVEPOINT contains the insert so a lost race against the
                    # per-member unique constraint rolls back only this insert
                    # (mirrors MemberSuppressionService._upsert).
                    with self._db.begin_nested():
                        self._db.add(row)
                        self._db.flush()
                except IntegrityError:
                    winner = self._existing(member, key)
                    if winner is None:
                        raise
                    winner.searched_at = utcnow()
                    self._db.flush()
                    row = winner
            self._trim(member)
        return self._record(row)

    def list_for(self, member: User) -> list[SearchHistoryRecord]:
        rows = (
            self._db.query(SearchHistoryEntry)
            .filter(SearchHistoryEntry.user_id == member.id)
            .order_by(SearchHistoryEntry.searched_at.desc(), SearchHistoryEntry.id.desc())
            .limit(MAX_ENTRIES)
            .all()
        )
        return [self._record(row) for row in rows]

    def delete(self, member: User, entry_id: str) -> bool:
        """Remove one member-owned entry. Another member's entry is never touched; a missing id is a no-op."""
        with write_transaction(self._db, name="search_history_delete"):
            deleted = (
                self._db.query(SearchHistoryEntry)
                .filter(SearchHistoryEntry.user_id == member.id, SearchHistoryEntry.id == entry_id)
                .delete(synchronize_session=False)
            )
        return bool(deleted)

    def clear(self, member: User) -> int:
        with write_transaction(self._db, name="search_history_clear"):
            deleted = (
                self._db.query(SearchHistoryEntry)
                .filter(SearchHistoryEntry.user_id == member.id)
                .delete(synchronize_session=False)
            )
        return deleted

    def _existing(self, member: User, key: str) -> SearchHistoryEntry | None:
        return self._db.query(SearchHistoryEntry).filter_by(user_id=member.id, query_key=key).one_or_none()

    def _trim(self, member: User) -> None:
        stale_ids = [
            row_id
            for (row_id,) in self._db.query(SearchHistoryEntry.id)
            .filter(SearchHistoryEntry.user_id == member.id)
            .order_by(SearchHistoryEntry.searched_at.desc(), SearchHistoryEntry.id.desc())
            .offset(MAX_ENTRIES)
            .all()
        ]
        if stale_ids:
            self._db.query(SearchHistoryEntry).filter(SearchHistoryEntry.id.in_(stale_ids)).delete(synchronize_session=False)

    @staticmethod
    def _record(row: SearchHistoryEntry) -> SearchHistoryRecord:
        return SearchHistoryRecord(id=row.id, query=row.query, searched_at=row.searched_at)
