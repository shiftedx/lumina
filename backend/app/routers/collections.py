"""Household collection serialization and HTTP routes."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import SmartCollectionRule
from app.models import HouseholdCollection, HouseholdCollectionMembership, LibraryItem, User
from app.routers.watch_queue import RemoteRef
from app.schemas import CollectionEntryMoveRequest, CollectionEntryRefResponse, CollectionEntryResponse, CollectionRemoteRefRequest, HouseholdCollectionCreateRequest, HouseholdCollectionRenameRequest, HouseholdCollectionResponse, HouseholdCollectionVisibilityRequest
from app.security import get_current_user
from app.services import smart_collections
from app.services.household_collections import CollectionConflictError, CollectionEntryNotFoundError, CollectionFullError, CollectionNotFoundError, CollectionPermissionError, DuplicateCollectionItemError, DuplicateCollectionNameError, HouseholdCollectionService, InvisibleLibraryItemError, SmartCollectionEditError
from app.services.library import LibraryService
from app.services.title_summaries import title_summaries


def register(app: FastAPI) -> None:
    COLLECTION_ITEMS_PREVIEW_LIMIT = 100
    
    
    def _collection_entry_response(membership: HouseholdCollectionMembership, item: LibraryItem | None) -> CollectionEntryResponse:
        if membership.library_item_id is not None:
            if item is None:
                # Revoked or deleted: a redacted tombstone the owner can only remove.
                return CollectionEntryResponse(
                    id=membership.id, position=membership.position, availability="unavailable",
                    ref=CollectionEntryRefResponse(kind="library"), title=None, uploader=None, artwork_url=None, duration=None,
                )
            return CollectionEntryResponse(
                id=membership.id, position=membership.position, availability="available",
                ref=CollectionEntryRefResponse(kind="library", library_item_id=item.id, url=item.webpage_url),
                title=item.title, uploader=item.uploader, artwork_url=f"/api/library/{item.id}/artwork", duration=item.duration,
            )
        return CollectionEntryResponse(
            id=membership.id, position=membership.position, availability="available",
            ref=CollectionEntryRefResponse(kind="remote", provider=membership.provider, remote_id=membership.remote_id, url=membership.source_url),
            title=membership.title, uploader=membership.uploader, artwork_url=membership.artwork_url, duration=membership.duration,
        )
    
    
    def serialize_household_collection(
        db: Session,
        collection: HouseholdCollection,
        current_user: User,
        *,
        include_items: bool,
        item_count: int | None = None,
    ) -> HouseholdCollectionResponse:
        """``item_count`` lets list callers pass a pre-aggregated count (see list_household_collections)."""
        HouseholdCollectionService(db).get_visible(member_user_id=current_user.id, collection_id=collection.id)
        if collection.rules is not None:
            # A smart collection: its rule evaluated as this viewer (ADR 0003); the passed item_count is ignored.
            rule = SmartCollectionRule.model_validate(collection.rules)
            rows = smart_collections.evaluate(db, current_user, rule) if include_items else []
            titled = rule.type != "channel_video"
            library = LibraryService(db)
            if not titled:
                library.prime_owners(rows)
            return HouseholdCollectionResponse(
                id=collection.id, owner_user_id=collection.owner_user_id, name=collection.name,
                description=collection.description, visibility=collection.visibility, revision=collection.revision,
                item_count=smart_collections.count_matches(db, current_user, rule),
                items=[] if titled else [library.serialize(item, current_user, summary=True) for item in rows],
                entries=[], rules=rule,
                titles=title_summaries(db, current_user, [row.id for row in rows]) if titled else [],
                created_at=collection.created_at, updated_at=collection.updated_at,
            )
        # Collection membership never grants access to an invisible item.
        visible_membership = db.query(HouseholdCollectionMembership).join(
            LibraryItem, LibraryItem.id == HouseholdCollectionMembership.library_item_id
        ).filter(
            HouseholdCollectionMembership.collection_id == collection.id,
            LibraryService.visible_predicate(current_user),
        )
        if item_count is None:
            item_count = visible_membership.with_entities(func.count()).scalar() or 0
        items: list[LibraryItem] = []
        entries: list[CollectionEntryResponse] = []
        library = LibraryService(db)
        if include_items:
            items = (
                visible_membership.with_entities(LibraryItem)
                .order_by(HouseholdCollectionMembership.created_at, HouseholdCollectionMembership.id)
                .limit(COLLECTION_ITEMS_PREVIEW_LIMIT)
                .all()
            )
            library.prime_owners(items)
            # Mixed-source view: every entry in position order, library refs
            # tombstoned rather than dropped when visibility was revoked.
            _, memberships = HouseholdCollectionService(db).list_entries(member_user_id=current_user.id, collection_id=collection.id)
            library_ids = [membership.library_item_id for membership in memberships if membership.library_item_id]
            visible_items = {
                row.id: row
                for row in db.query(LibraryItem).filter(LibraryItem.id.in_(library_ids), LibraryService.visible_predicate(current_user))
            } if library_ids else {}
            entries = [_collection_entry_response(membership, visible_items.get(membership.library_item_id or "")) for membership in memberships]
        return HouseholdCollectionResponse(
            id=collection.id,
            owner_user_id=collection.owner_user_id,
            name=collection.name,
            description=collection.description,
            visibility=collection.visibility,
            revision=collection.revision,
            item_count=item_count,
            items=[library.serialize(item, current_user, summary=True) for item in items],
            entries=entries,
            rules=None,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )
    
    def _collection_http_error(error: Exception) -> HTTPException:
        if isinstance(error, SmartCollectionEditError):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, smart_collections.RuleError):
            return HTTPException(status_code=422, detail=str(error))
        if isinstance(error, (CollectionNotFoundError, InvisibleLibraryItemError, CollectionEntryNotFoundError)):
            return HTTPException(status_code=404, detail="Collection or entry not found")
        if isinstance(error, CollectionPermissionError):
            return HTTPException(status_code=403, detail=str(error))
        if isinstance(error, CollectionConflictError):
            return HTTPException(status_code=409, detail="This collection changed elsewhere. Refresh and try again.")
        if isinstance(error, (DuplicateCollectionItemError, DuplicateCollectionNameError)):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, CollectionFullError):
            return HTTPException(status_code=422, detail="This collection is full.")
        return HTTPException(status_code=400, detail=str(error))
    
    
    @app.post("/api/collections", response_model=HouseholdCollectionResponse, status_code=201)
    async def create_household_collection(
        payload: HouseholdCollectionCreateRequest,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        try:
            # Re-parse the raw body's rules through parse_rule; the lax HouseholdCollectionCreateRequest
            # (a frozen body model) would coerce a stray bool value (e.g. `rating gte true`) to an int.
            raw_rules = (await request.json()).get("rules")
            rules = smart_collections.parse_rule(raw_rules).model_dump() if raw_rules is not None else None
            collection = HouseholdCollectionService(db).create(
                owner_user_id=current_user.id,
                name=payload.name,
                description=payload.description,
                visibility=payload.visibility,
                rules=rules,
            )
        except (ValueError, DuplicateCollectionNameError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.get("/api/collections", response_model=list[HouseholdCollectionResponse])
    def list_household_collections(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[HouseholdCollectionResponse]:
        service = HouseholdCollectionService(db)
        collections = service.shown_to(current_user, service.list_visible(current_user.id))
        # One grouped count instead of a count query per collection; same visibility rule as the serializer.
        counts = dict(
            db.query(HouseholdCollectionMembership.collection_id, func.count())
            .join(LibraryItem, LibraryItem.id == HouseholdCollectionMembership.library_item_id)
            .filter(
                HouseholdCollectionMembership.collection_id.in_([collection.id for collection in collections]),
                LibraryService.visible_predicate(current_user),
            )
            .group_by(HouseholdCollectionMembership.collection_id)
            .all()
        ) if collections else {}
        return [
            serialize_household_collection(db, collection, current_user, include_items=False, item_count=counts.get(collection.id, 0))
            for collection in collections
        ]
    
    
    @app.get("/api/collections/{collection_id}", response_model=HouseholdCollectionResponse)
    def get_household_collection(
        collection_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        try:
            collection = HouseholdCollectionService(db).get_visible(
                member_user_id=current_user.id, collection_id=collection_id,
            )
        except CollectionNotFoundError as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.put("/api/collections/{collection_id}/name", response_model=HouseholdCollectionResponse)
    def rename_household_collection(
        collection_id: str,
        payload: HouseholdCollectionRenameRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        try:
            collection = HouseholdCollectionService(db).rename(
                member_user_id=current_user.id, collection_id=collection_id, name=payload.name,
            )
        except (CollectionNotFoundError, CollectionPermissionError, DuplicateCollectionNameError, ValueError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.put("/api/collections/{collection_id}/visibility", response_model=HouseholdCollectionResponse)
    def update_household_collection_visibility(
        collection_id: str,
        payload: HouseholdCollectionVisibilityRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        try:
            collection = HouseholdCollectionService(db).set_visibility(
                member_user_id=current_user.id,
                collection_id=collection_id,
                visibility=payload.visibility,
            )
        except (CollectionNotFoundError, CollectionPermissionError, ValueError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.put("/api/collections/{collection_id}/rules", response_model=HouseholdCollectionResponse)
    async def set_household_collection_rules(
        collection_id: str,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        """Make an empty collection smart or change its rule; evaluated as each viewer (ADR 0003).
    
        The body is re-parsed strictly through ``parse_rule`` rather than declared as the frozen,
        lax ``SmartCollectionRule`` body model, which would silently coerce a stray bool value to an int.
        """
        try:
            rule = smart_collections.parse_rule(await request.json())
            collection = HouseholdCollectionService(db).set_rules(
                member_user_id=current_user.id, collection_id=collection_id,
                rules=rule.model_dump(),
            )
        except (CollectionNotFoundError, CollectionPermissionError, SmartCollectionEditError, ValueError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.delete("/api/collections/{collection_id}", status_code=204)
    def delete_household_collection(
        collection_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> Response:
        try:
            HouseholdCollectionService(db).delete(member_user_id=current_user.id, collection_id=collection_id)
        except (CollectionNotFoundError, CollectionPermissionError) as exc:
            raise _collection_http_error(exc) from exc
        return Response(status_code=204)
    
    
    @app.post("/api/collections/{collection_id}/items/{item_id}", response_model=HouseholdCollectionResponse)
    def add_household_collection_item(
        collection_id: str,
        item_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        service = HouseholdCollectionService(db)
        try:
            service.add_item(
                member_user_id=current_user.id,
                collection_id=collection_id,
                library_item_id=item_id,
            )
            collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
        except (CollectionNotFoundError, CollectionPermissionError, InvisibleLibraryItemError, DuplicateCollectionItemError, CollectionFullError, SmartCollectionEditError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.post("/api/collections/{collection_id}/remote-items", response_model=HouseholdCollectionResponse, status_code=201)
    def add_household_collection_remote_item(
        collection_id: str,
        payload: CollectionRemoteRefRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        """Adds a public source ref (S45 mixed collections). Never admits a download; Save to vault is a separate action."""
        service = HouseholdCollectionService(db)
        remote = RemoteRef(
            provider=payload.provider, remote_id=payload.remote_id, url=payload.url, title=payload.title,
            uploader=payload.uploader, artwork_url=payload.artwork_url, duration=payload.duration,
        )
        try:
            service.add_remote(member_user_id=current_user.id, collection_id=collection_id, remote=remote, expected_revision=payload.expected_revision)
            collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
        except (CollectionNotFoundError, CollectionPermissionError, DuplicateCollectionItemError, CollectionFullError, CollectionConflictError, ValueError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.delete("/api/collections/{collection_id}/entries/{entry_id}", response_model=HouseholdCollectionResponse)
    def remove_household_collection_entry(
        collection_id: str,
        entry_id: str,
        expected_revision: int | None = Query(default=None, ge=0),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        """Removes any entry (library or remote) by membership id. Idempotent; never deletes the underlying artifact."""
        service = HouseholdCollectionService(db)
        try:
            service.remove_entry(member_user_id=current_user.id, collection_id=collection_id, entry_id=entry_id, expected_revision=expected_revision)
            collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
        except (CollectionNotFoundError, CollectionPermissionError, CollectionConflictError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    
    @app.patch("/api/collections/{collection_id}/entries/{entry_id}", response_model=HouseholdCollectionResponse)
    def move_household_collection_entry(
        collection_id: str,
        entry_id: str,
        payload: CollectionEntryMoveRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> HouseholdCollectionResponse:
        """Reorders one entry to a new position; a stale expected_revision conflicts rather than losing the other client's order."""
        service = HouseholdCollectionService(db)
        try:
            service.move(member_user_id=current_user.id, collection_id=collection_id, entry_id=entry_id, index=payload.position, expected_revision=payload.expected_revision)
            collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
        except (CollectionNotFoundError, CollectionPermissionError, CollectionEntryNotFoundError, CollectionConflictError) as exc:
            raise _collection_http_error(exc) from exc
        return serialize_household_collection(db, collection, current_user, include_items=True)
    
    

