"""First-owner media setup is durable, resumable, and never leaks to later admins."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from app.models import User
from app.schemas import InvitationRedeemRequest, UserCreateRequest
from app.services.users import UserService
from support import make_user


PASSWORD = "Test-only-passphrase-1"


def test_only_initial_admin_starts_with_vault_setup_pending_and_it_survives_reload(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        first_owner = UserService(db).create_initial_admin("owner", PASSWORD, "First Owner")
        first_owner_id = first_owner.id
        assert first_owner.vault_setup_pending is True
        assert first_owner.onboarding_status == "pending"

    with db_factory() as db:
        reloaded = db.get(User, first_owner_id)
        assert reloaded is not None
        assert reloaded.vault_setup_pending is True
        assert UserService(db).serialize_user(reloaded).vault_setup_pending is True


def test_later_and_invited_admins_keep_the_false_default(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = UserService(db)
        owner = service.create_initial_admin("owner", PASSWORD)

        later_admin = service.create_user(
            UserCreateRequest(username="later-admin", password=PASSWORD, role="admin")
        )
        db.commit()

        _, token = service.issue_account_token(owner, "invite", expires_in_hours=24, role="admin")
        invited_admin = service.redeem_invitation(
            InvitationRedeemRequest(token=token, username="invited-admin", password=PASSWORD)
        )

        assert later_admin.vault_setup_pending is False
        assert invited_admin.role == "admin"
        assert invited_admin.vault_setup_pending is False


def test_media_onboarding_context_and_completion_are_admin_only_and_idempotent(
    db_factory, api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    first_mount = tmp_path / "media"
    second_mount = tmp_path / "archive"
    first_mount.mkdir()
    second_mount.mkdir()
    monkeypatch.setattr(settings, "storage_mount_parents", f"{first_mount},{second_mount}")

    with db_factory() as db:
        owner = UserService(db).create_initial_admin("owner", PASSWORD)
        owner_id = owner.id

    client = api_client(user=owner, base_url="http://localhost")
    context = client.get("/api/admin/onboarding/media")
    assert context.status_code == 200
    assert context.json() == {"mount_parents": [str(first_mount), str(second_mount)]}

    for _ in range(2):
        completed = client.post("/api/admin/onboarding/media/complete")
        assert completed.status_code == 200
        assert completed.json()["vault_setup_pending"] is False
        assert completed.json()["onboarding_status"] == "pending"

    with db_factory() as db:
        reloaded = db.get(User, owner_id)
        assert reloaded is not None
        assert reloaded.vault_setup_pending is False
        assert reloaded.onboarding_status == "pending"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/onboarding/media"),
        ("post", "/api/admin/onboarding/media/complete"),
    ],
)
def test_viewer_cannot_access_admin_media_onboarding(api_client, method: str, path: str) -> None:  # noqa: ANN001
    client = api_client(user=make_user("viewer"), base_url="http://localhost")
    response = getattr(client, method)(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}


def test_schema_19_defaults_migrated_users_to_completed_setup(tmp_path: Path) -> None:
    database = tmp_path / "schema-18.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO users (id) VALUES ('existing-owner')")
        for statement in db_module.MIGRATIONS[19]:
            connection.execute(statement)
        value = connection.execute(
            "SELECT vault_setup_pending FROM users WHERE id = 'existing-owner'"
        ).fetchone()

    assert value == (0,)
