"""Chromecast direct-video tickets are revocable receiver capabilities, not sessions."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import LibraryItem, MediaCastTicket, User
from app.services import music_cast_capabilities, public_address
from app.services.media_probe import MediaProbeService
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, MOVIE_1080, seed_gallery, seed_tree


@pytest.fixture
def cast_world(db_factory, api_client, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    root = tmp_path / "media"
    root.mkdir()
    with db_factory() as db:
        db.add_all([make_user(ALICE), make_user(BOB)])
        seed_tree(db, root)
        seed_gallery(db, root)
        seed_app_settings(db)
    current = {"id": ALICE}

    def who() -> User:
        with db_factory() as db:
            return db.get(User, current["id"])

    facts = {"container": "mov,mp4", "duration": 120.0, "streams": [
        {"index": 0, "type": "video", "codec": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
    ]}
    monkeypatch.setattr(MediaProbeService, "facts", lambda _self, _item: facts)
    monkeypatch.setattr(public_address, "origin", lambda: None)
    monkeypatch.setattr(public_address, "local_origin", lambda: "https://lumina.home.arpa")
    return api_client(user=who, base_url="http://localhost"), current, db_factory, facts


def _mint(client, **extra):  # noqa: ANN001, ANN202
    response = client.post("/api/chromecast/tickets", json={"item_id": MOVIE_1080, "position_seconds": 12.5, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_ticket_is_hashed_owner_scoped_renewable_and_rechecks_visibility(cast_world) -> None:  # noqa: ANN001
    client, current, factory, _facts = cast_world
    ticket = _mint(client)
    assert ticket["content_type"] == "video/mp4" and ticket["qualification"] == "unverified_hardware"
    assert ticket["media_url"].startswith("https://lumina.home.arpa/api/chromecast/")
    parsed = urlparse(ticket["media_url"])
    token = parse_qs(parsed.query)["token"][0]
    with factory() as db:
        row = db.get(MediaCastTicket, ticket["ticket_id"])
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest() and row.token_hash != token
    bad = client.get(f"{parsed.path}?token=wrong")
    assert bad.status_code == 401
    media = client.get(f"{parsed.path}?token={token}")
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4" and media.headers["access-control-allow-origin"] == "*"
    renewed = client.post(f"/api/chromecast/tickets/{ticket['ticket_id']}/renew")
    assert renewed.status_code == 200 and set(renewed.json()) == {"ticket_id", "expires_at"}
    with factory() as db:
        item = db.get(LibraryItem, MOVIE_1080)
        item.visibility, item.user_id = "private", BOB
        db.commit()
    assert client.get(f"{parsed.path}?token={token}").status_code == 404
    current["id"] = BOB
    assert client.delete(f"/api/chromecast/tickets/{ticket['ticket_id']}").status_code == 404
    current["id"] = ALICE
    assert client.delete(f"/api/chromecast/tickets/{ticket['ticket_id']}").status_code == 204
    assert client.get(f"{parsed.path}?token={token}").status_code == 401


def test_mint_refuses_unqualified_media_unknown_origin_and_audio_override(cast_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    client, _current, _factory, facts = cast_world
    facts["streams"][0].update(codec="hevc", height=2160)
    reply = client.post("/api/chromecast/tickets", json={"item_id": MOVIE_1080, "position_seconds": 0})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "conversion_needed"
    facts["streams"][0].update(codec="h264", height=1080)
    facts["streams"].append({"index": 2, "type": "audio", "codec": "aac", "channels": 2, "default": False})
    reply = client.post("/api/chromecast/tickets", json={"item_id": MOVIE_1080, "position_seconds": 0, "audio_index": 2})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "default_audio_override_unsupported"
    monkeypatch.setattr(public_address, "local_origin", lambda: None)
    reply = client.post("/api/chromecast/tickets", json={"item_id": MOVIE_1080, "position_seconds": 0})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "cast_address_required"


def test_logout_revoke_helper_removes_video_capabilities(cast_world) -> None:  # noqa: ANN001
    client, _current, factory, _facts = cast_world
    ticket = _mint(client)
    with factory() as db:
        assert music_cast_capabilities.revoke_for_user(db, ALICE) == 1
        db.commit()
        assert db.get(MediaCastTicket, ticket["ticket_id"]) is None
