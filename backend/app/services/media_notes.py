from __future__ import annotations

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import LibraryItem, LibraryNote, User
from app.schemas import NOTE_BODY_MAX_CHARS, LibraryNoteResponse
from app.services import two_factor
from app.services.library_curation import LibraryCurationService
from app.services.library_search import LibrarySearchService

VISIBILITIES = {"private", "household"}
# Unpaged per-item list; human-authored notes stay small. Add cursor paging if items gather hundreds.
LIST_LIMIT = 500


class MediaNotesService:
    """Private and household notes on a Library item, with author/admin authority."""

    def __init__(self, db: Session):
        self.db = db

    def list_notes(self, item_id: str, actor: User) -> list[LibraryNote]:
        self._require_visible_item(item_id, actor)
        return (
            self.db.query(LibraryNote)
            .filter(LibraryNote.item_id == item_id)
            .filter(or_(LibraryNote.user_id == actor.id, LibraryNote.visibility == "household"))
            # SQLite sorts NULL first: untimed notes lead, then playback order.
            .order_by(LibraryNote.timestamp_ms, LibraryNote.created_at, LibraryNote.id)
            .limit(LIST_LIMIT)
            .all()
        )

    def add_note(self, item_id: str, body: str, visibility: str, actor: User, timestamp_ms: int | None = None) -> LibraryNote:
        item = self._require_visible_item(item_id, actor)
        # A timestamped note is a bookmark; duration is whole seconds, so allow up to the next one.
        if timestamp_ms is not None and (timestamp_ms < 0 or (item.duration and timestamp_ms > (item.duration + 1) * 1000)):
            raise ValueError("Note timestamp is invalid")
        note = LibraryNote(
            id=str(uuid.uuid4()),
            item_id=item_id,
            user_id=actor.id,
            visibility=self._visibility(visibility),
            timestamp_ms=timestamp_ms,
            body=self._body(body),
        )
        self.db.add(note)
        self.db.flush()
        self._resync(item_id, actor.id)
        return note

    def update_note(self, note_id: str, body: str, visibility: str, actor: User) -> LibraryNote:
        note = self._require_note(note_id, actor)
        if note.user_id != actor.id:
            raise PermissionError("Only the note author can change this note")
        note.body = self._body(body)
        note.visibility = self._visibility(visibility)
        self.db.flush()
        self._resync(note.item_id, actor.id)
        return note

    def delete_note(self, note_id: str, actor: User) -> None:
        note = self._require_note(note_id, actor)
        if not self.can_delete(note, actor):
            raise PermissionError("Only the note author can delete this note")
        item_id, author_id = note.item_id, note.user_id
        self.db.delete(note)
        self.db.flush()
        self._resync(item_id, author_id)

    @staticmethod
    def can_delete(note: LibraryNote, actor: User) -> bool:
        return note.user_id == actor.id or (note.visibility == "household" and two_factor.owner_capable(None, actor))

    def serialize(self, note: LibraryNote, viewer: User) -> LibraryNoteResponse:
        author = self.db.get(User, note.user_id) if note.user_id else None
        return LibraryNoteResponse(
            id=note.id,
            item_id=note.item_id,
            user_id=note.user_id,
            visibility=note.visibility if note.visibility in VISIBILITIES else "private",
            timestamp_ms=note.timestamp_ms,
            body=note.body,
            author_username=author.username if author else None,
            author_display_name=author.display_name if author else None,
            is_owner=note.user_id == viewer.id,
            can_delete=self.can_delete(note, viewer),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    def _resync(self, item_id: str, author_id: str | None) -> None:
        """Household text is item-level search text; private text is member-level."""
        search = LibrarySearchService(self.db)
        item = self.db.get(LibraryItem, item_id)
        if item is not None:
            search.sync_item(item)
        if author_id is not None:
            search.sync_member_curation(item_id, author_id)

    def _require_note(self, note_id: str, actor: User) -> LibraryNote:
        note = self.db.get(LibraryNote, note_id)
        if note is None or (note.visibility != "household" and note.user_id != actor.id):
            raise ValueError("Note not found")
        self._require_visible_item(note.item_id, actor)
        return note

    def _require_visible_item(self, item_id: str, actor: User) -> LibraryItem:
        item = self.db.get(LibraryItem, item_id)
        if item is None or not LibraryCurationService.can_view(item, actor):
            raise ValueError("Library item not found")
        return item

    @staticmethod
    def _body(body: str) -> str:
        normalized = body.strip()
        if not normalized:
            raise ValueError("Note body is required")
        if len(normalized) > NOTE_BODY_MAX_CHARS:
            raise ValueError("Note body is too long")
        return normalized

    @staticmethod
    def _visibility(visibility: str) -> str:
        if visibility not in VISIBILITIES:
            raise ValueError("Note visibility is invalid")
        return visibility
