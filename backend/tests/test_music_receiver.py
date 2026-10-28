"""Paired native music receiver capabilities never expose an app credential."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import MusicReceiverTicket, User
from app.services import music_audio, public_address
from app.services.connected_apps import revoke_connected_app
from app.services.media_titles import jellyfin_id
from support import make_user, seed_app_settings
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, TRACKS, device_token, mediabrowser, seed_gallery, seed_tree

ALICE_SECOND_TOKEN = "alice-second-music-receiver-token"


@pytest.fixture
def receiver_world(db_factory, api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    root = tmp_path / "media"
    root.mkdir()
    with db_factory() as db:
        db.add_all([make_user(ALICE), make_user(BOB)])
        seed_tree(db, root)
        seed_gallery(db, root)
        device_token(db, ALICE, ALICE_TOKEN)
        device_token(db, ALICE, ALICE_SECOND_TOKEN, device_id="device-2")
        device_token(db, BOB, BOB_TOKEN, device_id="device-3")
        seed_app_settings(db, jellyfin_enabled=True)
    current = {"id": ALICE}

    def who() -> User:
        with db_factory() as db:
            return db.get(User, current["id"])

    converted = tmp_path / "receiver.m4a"
    converted.write_bytes(b"m4a")
    formats = []

    def rendition(_source, format_):  # noqa: ANN001
        formats.append(format_)
        return music_audio.PlaybackFile(converted, "audio/mp4")

    monkeypatch.setattr(music_audio, "playback_file", rendition)
    monkeypatch.setattr(public_address, "origin", lambda: None)
    monkeypatch.setattr(public_address, "local_origin", lambda: "https://lumina.home.arpa")
    return api_client(user=who, base_url="http://localhost"), db_factory, formats


def _header(token: str, device_id: str = "device-1") -> dict[str, str]:
    if device_id == "device-1":
        return mediabrowser(token)
    return {"Authorization": f'MediaBrowser Client="Lumina", Device="iPhone", DeviceId="{device_id}", Version="1", Token="{token}"'}


def _mint(client):  # noqa: ANN001, ANN202
    response = client.post("/Lumina/Music/Receiver/tickets", headers=_header(ALICE_TOKEN), json={
        "item_ids": [jellyfin_id(TRACKS[0]), jellyfin_id(TRACKS[1])], "current_index": 0, "position_seconds": 12.5,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_native_music_receiver_ticket_is_device_bound_and_serves_aac_without_credentials(receiver_world) -> None:  # noqa: ANN001
    client, factory, formats = receiver_world
    ticket = _mint(client)
    parsed = urlparse(ticket["media_url"])
    token = parse_qs(parsed.query)["token"][0]
    assert ticket["content_type"] == "audio/mp4" and ticket["qualification"] == "unverified_hardware"
    assert parsed.path == f"/api/music/receivers/{ticket['ticket_id']}/{TRACKS[0]}/audio"
    assert ALICE_TOKEN not in ticket["media_url"] and "ApiKey" not in ticket["media_url"]
    assert ticket["queue_limit"] == 100
    assert [entry["item_id"] for entry in ticket["queue"]] == list(TRACKS[:2])
    assert formats == []  # receiver URLs lazily encode only the requested track
    with factory() as db:
        row = db.get(MusicReceiverTicket, ticket["ticket_id"])
        assert row.user_id == ALICE and row.item_ids == list(TRACKS[:2]) and row.issuer_device_id
        assert timedelta(minutes=20) <= row.expires_at - row.created_at <= timedelta(minutes=21, seconds=1)

    response = client.get(f"{parsed.path}?token={token}", headers={"Range": "bytes=0-1"})
    assert response.status_code == 206 and response.headers["content-type"] == "audio/mp4"
    assert response.headers["content-range"] == "bytes 0-1/3"
    assert formats == ["chromecast"]
    next_url = urlparse(ticket["queue"][1]["media_url"])
    assert client.get(f"{next_url.path}?{next_url.query}").status_code == 200
    assert formats == ["chromecast", "chromecast"]
    denied_path = f"/api/music/receivers/{ticket['ticket_id']}/{TRACKS[2]}/audio"
    assert client.get(f"{denied_path}?token={token}").status_code == 401
    assert client.get(f"{parsed.path}?token=wrong").status_code == 401
    assert client.post(f"/Lumina/Music/Receiver/tickets/{ticket['ticket_id']}/renew", headers=_header(ALICE_SECOND_TOKEN, "device-2")).status_code == 404
    assert client.post(f"/Lumina/Music/Receiver/tickets/{ticket['ticket_id']}/renew", headers=_header(BOB_TOKEN, "device-3")).status_code == 404
    assert client.delete(f"/Lumina/Music/Receiver/tickets/{ticket['ticket_id']}", headers=_header(ALICE_SECOND_TOKEN, "device-2")).status_code == 404
    assert client.post(f"/Lumina/Music/Receiver/tickets/{ticket['ticket_id']}/renew", headers=_header(ALICE_TOKEN)).status_code == 200
    assert client.delete(f"/Lumina/Music/Receiver/tickets/{ticket['ticket_id']}", headers=_header(ALICE_TOKEN)).status_code == 204
    assert client.get(f"{parsed.path}?token={token}").status_code == 401


def test_device_revocation_proactively_removes_its_music_receiver_ticket(receiver_world) -> None:  # noqa: ANN001
    client, factory, _formats = receiver_world
    ticket = _mint(client)
    parsed = urlparse(ticket["media_url"])
    with factory() as db:
        actor = db.get(User, ALICE)
        record_id = db.get(MusicReceiverTicket, ticket["ticket_id"]).issuer_device_id
        revoke_connected_app(db, actor, record_id)
    assert client.get(f"{parsed.path}?{parsed.query}").status_code == 401
    with factory() as db:
        assert db.get(MusicReceiverTicket, ticket["ticket_id"]) is None


def test_native_music_receiver_rejects_an_invalid_track_id(receiver_world) -> None:  # noqa: ANN001
    client, _factory, _formats = receiver_world
    invalid = client.post("/Lumina/Music/Receiver/tickets", headers=_header(ALICE_TOKEN), json={"item_ids": ["bad"], "position_seconds": 0})
    assert invalid.status_code == 404


def test_native_music_receiver_rejects_more_than_its_explicit_queue_limit(receiver_world) -> None:  # noqa: ANN001
    client, _factory, _formats = receiver_world
    response = client.post("/Lumina/Music/Receiver/tickets", headers=_header(ALICE_TOKEN), json={
        "item_ids": [jellyfin_id(TRACKS[0])] * 101, "position_seconds": 0,
    })
    assert response.status_code == 400
