from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.events import EventBus
from app.models import LibraryItem, LibraryTag, User
from app.services import member_access, two_factor
from app.services.library_search import LibrarySearchService


class LibraryCurationService:
    """Own library visibility and member-authored curation policy."""

    def __init__(self, db: Session, events: EventBus | None = None):
        self.db = db
        self.events = events
        self._search = LibrarySearchService(db)

    @staticmethod
    def can_view(item: LibraryItem, actor: User) -> bool:
        if item.user_id == actor.id:
            return True
        if item.visibility == "shared":
            return member_access.allows_item(item, actor)
        return item.user_id is None and actor.role == "admin"  # reading, not an owner power

    @staticmethod
    def can_manage_item(item: LibraryItem, actor: User) -> bool:
        if item.user_id is not None:
            return item.user_id == actor.id
        return two_factor.owner_capable(None, actor)

    def set_visibility(self, item_id: str, visibility: str, actor: User) -> LibraryItem:
        item = self._require_visible_item(item_id, actor)
        if not self.can_manage_item(item, actor):
            raise PermissionError("Only the item owner can change visibility")
        if visibility not in {"private", "shared"}:
            raise ValueError("Visibility is invalid")
        if item.user_id is None and not two_factor.owner_capable(self.db, actor):
            item.user_id = actor.id
        item.visibility = visibility
        self.db.flush()
        return item

    def list_tags(self, item_id: str, actor: User) -> list[LibraryTag]:
        self._require_visible_item(item_id, actor)
        return (
            self.db.query(LibraryTag)
            .filter(LibraryTag.item_id == item_id, LibraryTag.user_id == actor.id)
            .order_by(LibraryTag.tag.asc(), LibraryTag.created_at.desc())
            .all()
        )

    def add_tag(self, item_id: str, value: str, actor: User) -> LibraryTag:
        self._require_visible_item(item_id, actor)
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Tag is required")
        existing = (
            self.db.query(LibraryTag)
            .filter(LibraryTag.item_id == item_id, LibraryTag.user_id == actor.id, LibraryTag.tag == normalized)
            .first()
        )
        if existing is not None:
            return existing
        tag = LibraryTag(id=str(uuid.uuid4()), item_id=item_id, user_id=actor.id, tag=normalized)
        self.db.add(tag)
        self.db.flush()
        self._search.sync_member_curation(item_id, actor.id)
        return tag

    def delete_tag(self, tag_id: str, actor: User) -> None:
        tag = self.db.get(LibraryTag, tag_id)
        if tag is None:
            raise ValueError("Tag not found")
        self._require_visible_item(tag.item_id, actor)
        if tag.user_id != actor.id:
            raise PermissionError("Only the tag owner can delete this tag")
        item_id = tag.item_id
        owner_id = tag.user_id
        self.db.delete(tag)
        self.db.flush()
        self._search.sync_member_curation(item_id, owner_id)

    def _require_visible_item(self, item_id: str, actor: User) -> LibraryItem:
        item = self.db.get(LibraryItem, item_id)
        if item is None or not self.can_view(item, actor):
            raise ValueError("Library item not found")
        return item
