"""Per-member watch queue.

An ordered list of MediaRefs a member deliberately wants to play next. It is a
playback intent only: nothing here admits an acquisition. Every mutation bumps
the queue revision in the same transaction, so a stale `expected_revision`
fails as a conflict instead of silently reordering over another client.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import LibraryItem, User, WatchQueue, WatchQueueEntry
from app.services.library import LibraryService

MAX_ENTRIES = 500


class WatchQueueConflict(Exception):
    pass


class WatchQueueFull(Exception):
    pass


class WatchQueueNotFound(Exception):
    pass


@dataclass(frozen=True)
class RemoteRef:
    provider: str | None
    remote_id: str | None
    url: str
    title: str | None
    uploader: str | None
    artwork_url: str | None
    duration: int | None


class WatchQueueService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def read(self, user: User) -> tuple[int, list[tuple[WatchQueueEntry, LibraryItem | None]]]:
        """Entries in order, each paired with its library item only while the member may still see it."""
        revision = self.db.scalar(select(WatchQueue.revision).where(WatchQueue.user_id == user.id)) or 0
        entries = self._entries(user)
        ids = [entry.library_item_id for entry in entries if entry.library_item_id]
        visible = {
            item.id: item
            for item in self.db.query(LibraryItem).filter(LibraryItem.id.in_(ids), LibraryService.visible_predicate(user))
        } if ids else {}
        return revision, [(entry, visible.get(entry.library_item_id or "")) for entry in entries]

    def add(
        self,
        user: User,
        *,
        library_item_id: str | None = None,
        remote: RemoteRef | None = None,
        position: Literal["next", "end"] = "end",
        expected_revision: int | None = None,
    ) -> None:
        if library_item_id and LibraryService(self.db).get_item(library_item_id, user) is None:
            raise WatchQueueNotFound
        entries = self._begin(user, expected_revision)
        existing = next((entry for entry in entries if self._same(entry, library_item_id, remote)), None)
        if existing is not None:
            # Idempotent: re-adding keeps one entry; "next" moves it to the front.
            if position == "end":
                self.db.rollback()
                return
            entries.remove(existing)
            entries.insert(0, existing)
        else:
            if len(entries) >= MAX_ENTRIES:
                self.db.rollback()
                raise WatchQueueFull
            entry = WatchQueueEntry(id=str(uuid.uuid4()), user_id=user.id, position=len(entries), library_item_id=library_item_id)
            if remote is not None:
                entry.provider, entry.remote_id, entry.source_url = remote.provider, remote.remote_id, remote.url
                entry.title, entry.uploader, entry.artwork_url, entry.duration = remote.title, remote.uploader, remote.artwork_url, remote.duration
            self.db.add(entry)
            entries.insert(0 if position == "next" else len(entries), entry)
        self._commit(entries)

    def move(self, user: User, entry_id: str, index: int, expected_revision: int) -> None:
        entries = self._begin(user, expected_revision)
        entry = next((entry for entry in entries if entry.id == entry_id), None)
        if entry is None:
            self.db.rollback()
            raise WatchQueueNotFound
        entries.remove(entry)
        entries.insert(max(0, min(index, len(entries))), entry)
        self._commit(entries)

    def remove(self, user: User, entry_id: str, expected_revision: int | None = None) -> None:
        """Idempotent: removing an already-removed entry succeeds."""
        entries = self._begin(user, expected_revision)
        for entry in entries:
            if entry.id == entry_id:
                self.db.delete(entry)
        self._commit([entry for entry in entries if entry.id != entry_id])

    def clear(self, user: User, expected_revision: int | None = None) -> None:
        for entry in self._begin(user, expected_revision):
            self.db.delete(entry)
        self.db.commit()

    # --- internals ---

    def _entries(self, user: User) -> list[WatchQueueEntry]:
        return list(self.db.scalars(
            select(WatchQueueEntry).where(WatchQueueEntry.user_id == user.id).order_by(WatchQueueEntry.position, WatchQueueEntry.created_at)
        ))

    def _begin(self, user: User, expected_revision: int | None) -> list[WatchQueueEntry]:
        """Claims the next revision atomically (conditional UPDATE) before any order change."""
        if self.db.get(WatchQueue, user.id) is None:
            if expected_revision not in (None, 0):
                raise WatchQueueConflict
            self.db.add(WatchQueue(user_id=user.id, revision=1))
            self.db.flush()
        else:
            statement = update(WatchQueue).where(WatchQueue.user_id == user.id)
            if expected_revision is not None:
                statement = statement.where(WatchQueue.revision == expected_revision)
            claimed = self.db.execute(statement.values(revision=WatchQueue.revision + 1).execution_options(synchronize_session=False))
            if claimed.rowcount == 0:
                self.db.rollback()
                raise WatchQueueConflict
        return self._entries(user)

    def _commit(self, ordered: list[WatchQueueEntry]) -> None:
        for index, entry in enumerate(ordered):
            if entry.position != index:
                entry.position = index
        self.db.commit()

    @staticmethod
    def _same(entry: WatchQueueEntry, library_item_id: str | None, remote: RemoteRef | None) -> bool:
        if library_item_id:
            return entry.library_item_id == library_item_id
        return entry.library_item_id is None and remote is not None and entry.source_url == remote.url
