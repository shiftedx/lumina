from __future__ import annotations

import uuid
from urllib.parse import urlparse

from sqlalchemy import and_, false, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.media_schemas import SmartCollectionRule
from app.models import HouseholdCollection, HouseholdCollectionMembership, LibraryItem, User
from app.persistence import write_transaction
from app.services import member_access, smart_collections
from app.services.library import LibraryService
from app.services.watch_queue import MAX_ENTRIES, RemoteRef


class CollectionNotFoundError(LookupError):
    pass


class CollectionPermissionError(PermissionError):
    pass


class InvisibleLibraryItemError(LookupError):
    pass


class DuplicateCollectionItemError(ValueError):
    pass


class DuplicateCollectionNameError(ValueError):
    pass


class CollectionConflictError(Exception):
    """A stale expected_revision lost a race with another add/remove/reorder."""


class CollectionFullError(Exception):
    pass


class CollectionEntryNotFoundError(LookupError):
    pass


class SmartCollectionEditError(CollectionConflictError):
    """Smart collections change through their rules, never by manual add, remove or reorder (409)."""

    def __init__(self, message: str = "Smart collections change through their rules.") -> None:
        super().__init__(message)


class HouseholdCollectionService:
    """Own collection policy; collection membership never grants library access."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, owner_user_id: str, name: str, visibility: str = "private", description: str | None = None, rules: dict | None = None) -> HouseholdCollection:
        clean_name, name_key = self._name(name)
        self._visibility(visibility)
        collection = HouseholdCollection(id=str(uuid.uuid4()), owner_user_id=owner_user_id, name=clean_name, name_key=name_key, visibility=visibility, description=description, rules=rules)
        try:
            with write_transaction(self.db, name="collection_create"):
                self.db.add(collection)
        except IntegrityError as error:
            raise DuplicateCollectionNameError("A collection with this name already exists.") from error
        return collection

    def list_visible(self, member_user_id: str) -> list[HouseholdCollection]:
        return list(self.db.scalars(select(HouseholdCollection).where(or_(HouseholdCollection.owner_user_id == member_user_id, HouseholdCollection.visibility == "shared")).order_by(HouseholdCollection.created_at, HouseholdCollection.id)))

    def shown_to(self, user: User, collections: list[HouseholdCollection], *, remote: bool = True) -> list[HouseholdCollection]:
        """Member access: a restricted member does not see another member's shared collection when none of its entries
        is visible to them (its name alone would leak hidden titles). ``remote``: links count as visible entries."""
        others = [c for c in collections if c.owner_user_id != user.id]
        if not others or member_access.item_clause(user, db=self.db) is None:
            return collections
        manual = [c.id for c in others if c.rules is None]
        entry_visible = and_(LibraryItem.status != "missing", LibraryService.visible_predicate(user))
        seen = set(self.db.scalars(
            select(HouseholdCollectionMembership.collection_id).distinct()
            .outerjoin(LibraryItem, LibraryItem.id == HouseholdCollectionMembership.library_item_id)
            .where(
                HouseholdCollectionMembership.collection_id.in_(manual),
                or_(HouseholdCollectionMembership.library_item_id.is_(None) if remote else false(), entry_visible),
            )
        )) if manual else set()
        seen |= {
            c.id for c in others
            if c.rules is not None and smart_collections.count_matches(self.db, user, SmartCollectionRule.model_validate(c.rules))
        }
        return [c for c in collections if c.owner_user_id == user.id or c.id in seen]

    def get_visible(self, *, member_user_id: str, collection_id: str) -> HouseholdCollection:
        collection = self.db.get(HouseholdCollection, collection_id)
        if collection is None or (collection.owner_user_id != member_user_id and collection.visibility != "shared"):
            raise CollectionNotFoundError(collection_id)
        return collection

    def rename(self, *, member_user_id: str, collection_id: str, name: str) -> HouseholdCollection:
        collection = self._owned(member_user_id, collection_id)
        try:
            with write_transaction(self.db, name="collection_rename"):
                collection.name, collection.name_key = self._name(name)
        except IntegrityError as error:
            raise DuplicateCollectionNameError("A collection with this name already exists.") from error
        return collection

    def set_visibility(self, *, member_user_id: str, collection_id: str, visibility: str) -> HouseholdCollection:
        collection = self._owned(member_user_id, collection_id)
        self._visibility(visibility)
        with write_transaction(self.db, name="collection_visibility"):
            collection.visibility = visibility
        return collection

    def set_rules(self, *, member_user_id: str, collection_id: str, rules: dict) -> HouseholdCollection:
        """Make an empty collection smart, or change a smart collection's (already validated) rule."""
        collection = self._owned(member_user_id, collection_id)
        if collection.rules is None and self._ordered(collection_id):
            raise SmartCollectionEditError("Empty the collection before turning it into a smart collection.")
        with write_transaction(self.db, name="collection_rules"):
            collection.rules = rules
            collection.revision += 1
        return collection

    def delete(self, *, member_user_id: str, collection_id: str) -> None:
        collection = self._owned(member_user_id, collection_id)
        memberships = list(
            self.db.scalars(
                select(HouseholdCollectionMembership).where(
                    HouseholdCollectionMembership.collection_id == collection.id
                )
            )
        )
        with write_transaction(self.db, name="collection_delete"):
            for membership in memberships:
                self.db.delete(membership)
            self.db.delete(collection)

    def add_item(self, *, member_user_id: str, collection_id: str, library_item_id: str, expected_revision: int | None = None) -> HouseholdCollectionMembership:
        item, member = self.db.get(LibraryItem, library_item_id), self.db.get(User, member_user_id)
        # The member's full visibility (Sections and ratings too): a hidden id answers exactly like a missing one.
        if item is None or member is None or not LibraryService.can_view(item, member):
            raise InvisibleLibraryItemError("Library item is not visible to this member.")
        entries = self._begin(member_user_id, collection_id, expected_revision)
        if len(entries) >= MAX_ENTRIES:
            self.db.rollback()
            raise CollectionFullError
        membership = HouseholdCollectionMembership(id=str(uuid.uuid4()), collection_id=collection_id, library_item_id=library_item_id, added_by_user_id=member_user_id, position=len(entries))
        self.db.add(membership)
        try:
            self.db.flush()
        except IntegrityError as error:
            self.db.rollback()
            raise DuplicateCollectionItemError("Item is already in this collection.") from error
        entries.append(membership)
        self._commit(entries)
        return membership

    def add_remote(self, *, member_user_id: str, collection_id: str, remote: RemoteRef, expected_revision: int | None = None) -> HouseholdCollectionMembership:
        """Add a public source ref outside the vault. Never admits a download (S19 handles acquisition)."""
        if not self._is_http_url(remote.url):
            raise ValueError("Remote items need an http(s) url.")
        entries = self._begin(member_user_id, collection_id, expected_revision)
        if any(entry.library_item_id is None and entry.source_url == remote.url for entry in entries):
            self.db.rollback()
            raise DuplicateCollectionItemError("This link is already in the collection.")
        if len(entries) >= MAX_ENTRIES:
            self.db.rollback()
            raise CollectionFullError
        membership = HouseholdCollectionMembership(
            id=str(uuid.uuid4()), collection_id=collection_id, added_by_user_id=member_user_id, position=len(entries),
            provider=remote.provider, remote_id=remote.remote_id, source_url=remote.url,
            title=remote.title, uploader=remote.uploader, artwork_url=remote.artwork_url, duration=remote.duration,
        )
        self.db.add(membership)
        entries.append(membership)
        self._commit(entries)
        return membership

    def remove_entry(self, *, member_user_id: str, collection_id: str, entry_id: str, expected_revision: int | None = None) -> None:
        """Removes any entry (library or remote) by membership id. Idempotent, and never touches the underlying artifact."""
        entries = self._begin(member_user_id, collection_id, expected_revision)
        for entry in entries:
            if entry.id == entry_id:
                self.db.delete(entry)
        self._commit([entry for entry in entries if entry.id != entry_id])

    def move(self, *, member_user_id: str, collection_id: str, entry_id: str, index: int, expected_revision: int) -> HouseholdCollection:
        entries = self._begin(member_user_id, collection_id, expected_revision)
        entry = next((candidate for candidate in entries if candidate.id == entry_id), None)
        if entry is None:
            self.db.rollback()
            raise CollectionEntryNotFoundError(entry_id)
        entries.remove(entry)
        entries.insert(max(0, min(index, len(entries))), entry)
        self._commit(entries)
        return self.db.get(HouseholdCollection, collection_id)

    def list_entries(self, *, member_user_id: str, collection_id: str) -> tuple[HouseholdCollection, list[HouseholdCollectionMembership]]:
        collection = self.get_visible(member_user_id=member_user_id, collection_id=collection_id)
        return collection, self._ordered(collection_id)

    def _owned(self, member_user_id: str, collection_id: str) -> HouseholdCollection:
        collection = self.db.get(HouseholdCollection, collection_id)
        if collection is None or (collection.owner_user_id != member_user_id and collection.visibility != "shared"):
            raise CollectionNotFoundError(collection_id)
        if collection.owner_user_id != member_user_id:
            raise CollectionPermissionError("Only the collection owner may change it.")
        return collection

    def _ordered(self, collection_id: str) -> list[HouseholdCollectionMembership]:
        return list(self.db.scalars(select(HouseholdCollectionMembership).where(HouseholdCollectionMembership.collection_id == collection_id).order_by(HouseholdCollectionMembership.position, HouseholdCollectionMembership.created_at)))

    def _begin(self, member_user_id: str, collection_id: str, expected_revision: int | None) -> list[HouseholdCollectionMembership]:
        """Claims the next revision atomically (conditional UPDATE) before any membership change."""
        if self._owned(member_user_id, collection_id).rules is not None:
            raise SmartCollectionEditError
        statement = update(HouseholdCollection).where(HouseholdCollection.id == collection_id)
        if expected_revision is not None:
            statement = statement.where(HouseholdCollection.revision == expected_revision)
        claimed = self.db.execute(statement.values(revision=HouseholdCollection.revision + 1).execution_options(synchronize_session=False))
        if claimed.rowcount == 0:
            self.db.rollback()
            raise CollectionConflictError
        return self._ordered(collection_id)

    def _commit(self, ordered: list[HouseholdCollectionMembership]) -> None:
        for index, entry in enumerate(ordered):
            if entry.position != index:
                entry.position = index
        self.db.commit()

    @staticmethod
    def _name(name: str) -> tuple[str, str]:
        clean = " ".join(name.split())
        if not clean:
            raise ValueError("Collection name is required.")
        return clean, clean.casefold()

    @staticmethod
    def _visibility(visibility: str) -> None:
        if visibility not in {"private", "shared"}:
            raise ValueError("Collection visibility must be private or shared.")

    @staticmethod
    def _is_http_url(value: str | None) -> bool:
        parsed = urlparse(value or "")
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
