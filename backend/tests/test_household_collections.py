import pytest

from app.models import LibraryItem, User
from app.services.household_collections import (
    CollectionPermissionError,
    DuplicateCollectionNameError,
    HouseholdCollectionService,
    InvisibleLibraryItemError,
)
from support import memory_session_factory


def _session():
    return memory_session_factory()()


def test_collection_visibility_and_owner_only_mutation() -> None:
    db = _session()
    db.add_all(
        [
            User(id="owner", username="owner", display_name="Owner"),
            User(id="member", username="member", display_name="Member"),
        ]
    )
    service = HouseholdCollectionService(db)
    private = service.create(owner_user_id="owner", name="Private", visibility="private")
    shared = service.create(owner_user_id="owner", name="Shared", visibility="shared")

    assert {item.id for item in service.list_visible("member")} == {shared.id}
    assert {item.id for item in service.list_visible("owner")} == {private.id, shared.id}

    try:
        service.rename(member_user_id="member", collection_id=shared.id, name="Taken")
    except CollectionPermissionError:
        pass
    else:
        raise AssertionError("non-owners must not rename a shared collection")


def test_membership_never_grants_access_to_an_invisible_library_item() -> None:
    db = _session()
    db.add_all(
        [
            User(id="owner", username="owner", display_name="Owner"),
            LibraryItem(id="private-item", user_id="owner", visibility="private", title="Private"),
            LibraryItem(id="shared-item", user_id="owner", visibility="shared", title="Shared"),
        ]
    )
    service = HouseholdCollectionService(db)
    collection = service.create(owner_user_id="owner", name="Household", visibility="shared")
    service.add_item(member_user_id="owner", collection_id=collection.id, library_item_id="private-item")
    service.add_item(member_user_id="owner", collection_id=collection.id, library_item_id="shared-item")

    try:
        service.add_item(member_user_id="owner", collection_id=collection.id, library_item_id="missing")
    except InvisibleLibraryItemError:
        pass
    else:
        raise AssertionError("missing or invisible items must be rejected")


def test_rename_collision_rolls_back_and_leaves_session_usable() -> None:
    db = _session()
    service = HouseholdCollectionService(db)
    first = service.create(owner_user_id="owner", name="First")
    second = service.create(owner_user_id="owner", name="Second")

    with pytest.raises(DuplicateCollectionNameError, match="already exists"):
        service.rename(member_user_id="owner", collection_id=second.id, name=" first ")

    assert db.get(type(first), first.id).name == "First"
    assert db.get(type(second), second.id).name == "Second"
    assert service.rename(member_user_id="owner", collection_id=second.id, name="Third").name == "Third"
