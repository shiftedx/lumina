"""Server account lifecycle revokes member-scoped Sonos capabilities."""
from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from app.config import settings
from app.models import MusicCastTicket, User, utcnow
from app.security import CSRF_HEADER, hash_password
from support import make_user, seed_app_settings
from title_support import ALBUM, ALICE, BOB, TRACKS, seed_gallery, seed_tree, uid

ADMIN = uid(0xAD11)
PASSWORD = "Test-only-passphrase-1"
ORIGIN = settings.allowed_origins_list[0]


@pytest.fixture
def cast_lifecycle_world(db_factory, tmp_path, api_client):  # noqa: ANN001, ANN201
    root = (tmp_path / "media").resolve()
    password_hash = hash_password(PASSWORD)
    with db_factory() as db:
        db.add_all([
            make_user(ADMIN, role="admin", username="admin", password_hash=password_hash),
            make_user(ALICE, username="alice", password_hash=password_hash),
            make_user(BOB, username="bob", password_hash=password_hash),
        ])
        seed_tree(db, root)
        seed_gallery(db, root)
        seed_app_settings(db)
        db.commit()
    return db_factory, api_client


def _login(client, username: str, *, remember: bool = False) -> str:  # noqa: ANN001
    response = client.post(
        "/api/session/login",
        json={"username": username, "password": PASSWORD, "remember_on_device": remember},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _capability(db_factory, owner: str, label: str) -> tuple[str, tuple[str, str]]:  # noqa: ANN001
    token = f"cast-capability-{label}"
    ticket_id = f"ticket-{label}"
    with db_factory() as db:
        db.add(MusicCastTicket(
            id=ticket_id,
            user_id=owner,
            speaker_id=f"speaker-{label}",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            item_ids=[TRACKS[0]],
            expires_at=utcnow() + timedelta(hours=24),
            output_format="native",
        ))
        db.commit()
    base = f"/api/music/cast/{ticket_id}/{TRACKS[0]}"
    return ticket_id, (f"{base}/audio?token={token}", f"{base}/artwork?token={token}")


def _assert_status(client, urls: tuple[str, str], expected: int) -> None:  # noqa: ANN001
    assert [client.get(url).status_code for url in urls] == [expected, expected]


def test_logout_revokes_unrestored_audio_and_artwork_capabilities_but_not_another_owner(
    cast_lifecycle_world,
) -> None:  # noqa: ANN001
    factory, api_client = cast_lifecycle_world
    _alice_ticket, alice_urls = _capability(factory, ALICE, "alice-logout")
    _bob_ticket, bob_urls = _capability(factory, BOB, "bob-logout")
    anonymous = api_client(base_url="http://localhost")
    _assert_status(anonymous, alice_urls, 200)
    _assert_status(anonymous, bob_urls, 200)

    alice = api_client(base_url="http://localhost")
    _login(alice, "alice")
    assert alice.post("/api/session/logout", headers={"Origin": ORIGIN}).status_code == 204

    _assert_status(anonymous, alice_urls, 401)
    _assert_status(anonymous, bob_urls, 200)


def test_member_switch_revokes_only_the_leaving_members_capabilities(cast_lifecycle_world) -> None:  # noqa: ANN001
    factory, api_client = cast_lifecycle_world
    _alice_ticket, alice_urls = _capability(factory, ALICE, "alice-switch")
    _bob_ticket, bob_urls = _capability(factory, BOB, "bob-switch")
    client = api_client(base_url="http://localhost")
    _login(client, "bob", remember=True)
    _login(client, "alice", remember=True)

    switched = client.post("/api/session/switch", json={"user_id": BOB}, headers={"Origin": ORIGIN})
    assert switched.status_code == 200, switched.text

    anonymous = api_client(base_url="http://localhost")
    _assert_status(anonymous, alice_urls, 401)
    _assert_status(anonymous, bob_urls, 200)


def test_active_and_role_changes_cannot_revive_old_capabilities_and_preserve_other_owners(
    cast_lifecycle_world,
) -> None:  # noqa: ANN001
    factory, api_client = cast_lifecycle_world
    _alice_ticket, alice_urls = _capability(factory, ALICE, "alice-active")
    _bob_ticket, bob_urls = _capability(factory, BOB, "bob-active")
    admin = api_client(base_url="http://localhost")
    csrf = _login(admin, "admin")
    headers = {"Origin": ORIGIN, CSRF_HEADER: csrf}

    disabled = admin.put(f"/api/admin/users/{ALICE}", json={"is_active": False}, headers=headers)
    assert disabled.status_code == 200, disabled.text
    anonymous = api_client(base_url="http://localhost")
    _assert_status(anonymous, alice_urls, 401)
    _assert_status(anonymous, bob_urls, 200)

    # A stale row introduced while inactive must not spring back to life when
    # the member is reactivated.
    _stale_ticket, stale_urls = _capability(factory, ALICE, "alice-stale")
    _assert_status(anonymous, stale_urls, 401)
    enabled = admin.put(f"/api/admin/users/{ALICE}", json={"is_active": True}, headers=headers)
    assert enabled.status_code == 200, enabled.text
    _assert_status(anonymous, stale_urls, 401)

    _role_ticket, role_urls = _capability(factory, ALICE, "alice-role")
    changed = admin.put(f"/api/admin/users/{ALICE}", json={"role": "admin"}, headers=headers)
    assert changed.status_code == 200, changed.text
    _assert_status(anonymous, role_urls, 401)
    _assert_status(anonymous, bob_urls, 200)
