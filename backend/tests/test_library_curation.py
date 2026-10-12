import pytest
from app.models import LibraryItem, User
from app.services.library_curation import LibraryCurationService
from support import make_user as make_member, memory_session_factory


def make_session():
    return memory_session_factory()()


def make_item(owner: User, *, visibility: str = "shared") -> LibraryItem:
    return LibraryItem(
        id="library-item",
        user_id=owner.id,
        visibility=visibility,
        title="Family film",
        metadata_json={},
        status="available",
    )


def test_only_item_owner_can_change_visibility() -> None:
    session = make_session()
    owner = make_member("owner")
    friend = make_member("friend")
    vault_owner = make_member("vault-owner", role="admin")
    item = make_item(owner)
    session.add_all([owner, friend, vault_owner, item])
    session.commit()
    service = LibraryCurationService(session)

    with pytest.raises(PermissionError, match="item owner"):
        service.set_visibility(item.id, "private", friend)

    with pytest.raises(PermissionError, match="item owner"):
        service.set_visibility(item.id, "private", vault_owner)

    assert service.set_visibility(item.id, "private", owner).visibility == "private"


def test_vault_owner_can_recover_a_legacy_unowned_item_without_accessing_member_private_items() -> None:
    session = make_session()
    vault_owner = make_member("vault-owner", role="admin")
    legacy = LibraryItem(id="legacy", user_id=None, visibility="private", title="Legacy item", metadata_json={}, status="available")
    session.add_all([vault_owner, legacy])
    session.commit()

    updated = LibraryCurationService(session).set_visibility(legacy.id, "shared", vault_owner)

    assert updated.visibility == "shared"
    assert updated.user_id is None


def test_tags_stay_private_to_the_member_who_manages_them() -> None:
    session = make_session()
    owner = make_member("owner")
    friend = make_member("friend")
    item = make_item(owner)
    session.add_all([owner, friend, item])
    session.commit()
    service = LibraryCurationService(session)

    owners_tag = service.add_tag(item.id, " Family Favorite ", owner)
    friends_tag = service.add_tag(item.id, "watch later", friend)

    assert owners_tag.tag == "family favorite"
    assert [tag.id for tag in service.list_tags(item.id, owner)] == [owners_tag.id]
    assert [tag.id for tag in service.list_tags(item.id, friend)] == [friends_tag.id]

    with pytest.raises(PermissionError, match="tag owner"):
        service.delete_tag(owners_tag.id, friend)

    service.delete_tag(owners_tag.id, owner)
    assert service.list_tags(item.id, owner) == []
