from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.main import update_user
from app.models import AppSession, User
from app.schemas import UserUpdateRequest
from app.security import hash_password
from app.services.users import UserService
from support import make_user, memory_session_factory


def make_session():
    return memory_session_factory()()


def make_member(identifier: str, *, role: str = "viewer", active: bool = True) -> User:
    return make_user(identifier, role=role, is_active=active, display_name=identifier.replace("-", " ").title(), password_hash="disabled")


def test_vault_owner_can_change_another_household_members_role_and_access() -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    member = make_member("member")
    session.add_all([owner, member])
    session.commit()

    updated = UserService(session).manage_user(
        owner,
        member.id,
        UserUpdateRequest(role="admin", is_active=False),
    )

    assert updated.role == "admin"
    assert updated.is_active is False


def test_household_member_cannot_manage_accounts() -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    member = make_member("member")
    session.add_all([owner, member])
    session.commit()

    with pytest.raises(PermissionError, match="vault owner"):
        UserService(session).manage_user(member, owner.id, UserUpdateRequest(is_active=False))

    assert owner.is_active is True


@pytest.mark.parametrize(
    "payload",
    [UserUpdateRequest(is_active=False), UserUpdateRequest(role="viewer")],
    ids=["deactivate", "demote"],
)
def test_vault_owner_cannot_remove_their_own_owner_access(payload: UserUpdateRequest) -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    second_owner = make_member("second-owner", role="admin")
    session.add_all([owner, second_owner])
    session.commit()

    with pytest.raises(ValueError, match="your own vault owner access"):
        UserService(session).manage_user(owner, owner.id, payload)

    assert owner.role == "admin"
    assert owner.is_active is True


@pytest.mark.parametrize(
    "payload",
    [UserUpdateRequest(is_active=False), UserUpdateRequest(role="viewer")],
    ids=["deactivate", "demote"],
)
def test_last_active_vault_owner_cannot_be_removed(payload: UserUpdateRequest) -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    inactive_owner = make_member("inactive-owner", role="admin", active=False)
    session.add_all([owner, inactive_owner])
    session.commit()

    with pytest.raises(ValueError, match="at least one active vault owner"):
        UserService(session).update_user(owner.id, payload)

    assert owner.role == "admin"
    assert owner.is_active is True


def test_admin_user_update_route_uses_household_policy_boundary() -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    second_owner = make_member("second-owner", role="admin")
    member = make_member("member")
    ordinary_member = make_member("ordinary-member")
    session.add_all([owner, second_owner, member, ordinary_member])
    session.commit()

    updated = update_user(member.id, UserUpdateRequest(role="admin"), current_user=owner, db=session)
    assert updated.role == "admin"

    with pytest.raises(HTTPException) as self_lockout:
        update_user(owner.id, UserUpdateRequest(is_active=False), current_user=owner, db=session)
    assert self_lockout.value.status_code == 409
    assert "own vault owner access" in self_lockout.value.detail

    with pytest.raises(HTTPException) as unauthorized:
        update_user(owner.id, UserUpdateRequest(is_active=False), current_user=ordinary_member, db=session)
    assert unauthorized.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        update_user("missing", UserUpdateRequest(is_active=False), current_user=owner, db=session)
    assert missing.value.status_code == 404


def test_password_changes_revoke_every_existing_session() -> None:
    session = make_session()
    owner = make_member("owner", role="admin")
    member = make_member("member")
    member.password_hash = hash_password("Old secure passphrase 41!")
    session.add_all([owner, member])
    session.flush()
    expiry = datetime.utcnow() + timedelta(days=1)
    session.add_all([
        AppSession(id="owner-session", user_id=owner.id, expires_at=expiry),
        AppSession(id="member-session-a", user_id=member.id, expires_at=expiry),
        AppSession(id="member-session-b", user_id=member.id, expires_at=expiry),
    ])
    session.commit()

    UserService(session).change_password(member, "Old secure passphrase 41!", "New secure passphrase 42!")

    assert session.query(AppSession).filter(AppSession.user_id == member.id).count() == 0
    assert session.get(AppSession, "owner-session") is not None
