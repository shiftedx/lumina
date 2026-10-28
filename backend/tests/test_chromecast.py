"""Chromecast direct-video tickets are revocable receiver capabilities, not sessions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models import DeviceToken, LibraryItem, MediaCastTicket, User, utcnow
from app.routers import chromecast
from app.security import get_current_user
from app.services import music_cast_capabilities, public_address
from app.services.connected_apps import revoke_connected_app
from app.services.jellyfin_hls import Plan
from app.services.media_probe import MediaProbeService
from app.services.media_titles import jellyfin_id
from support import make_user, seed_app_settings
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, MOVIE, MOVIE_1080, MOVIE_4K, device_token, mediabrowser, seed_gallery, seed_tree


@pytest.fixture
def cast_world(db_factory, api_client, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    root = tmp_path / "media"
    root.mkdir()
    with db_factory() as db:
        db.add_all([make_user(ALICE), make_user(BOB)])
        seed_tree(db, root)
        seed_gallery(db, root)
        device_token(db, ALICE, ALICE_TOKEN)
        device_token(db, BOB, BOB_TOKEN, device_id="device-2")
        seed_app_settings(db, jellyfin_enabled=True)
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
        assert row.issuer_device_id is None
        assert timedelta(minutes=14) <= row.expires_at - row.created_at <= timedelta(minutes=15, seconds=1)
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


def test_paired_jellyfin_device_can_manage_cast_ticket_without_widening_browser_auth(cast_world) -> None:  # noqa: ANN001
    client, _current, factory, _facts = cast_world
    body = {"item_id": MOVIE_1080, "position_seconds": 12.5}
    paired = mediabrowser(ALICE_TOKEN)
    browser_ticket = _mint(client)
    assert client.post(f"/Lumina/Cast/tickets/{browser_ticket['ticket_id']}/renew", headers=paired).status_code == 404

    # Native clients use the root-form Jellyfin endpoint; the private mount is
    # only an internal routing detail.
    minted = client.post("/Lumina/Cast/tickets", json=body, headers=paired)
    assert minted.status_code == 200, minted.text
    ticket = minted.json()
    assert ALICE_TOKEN not in minted.text
    parsed = urlparse(ticket["media_url"])
    receiver_token = parse_qs(parsed.query)["token"][0]
    assert ALICE_TOKEN not in ticket["media_url"]
    assert client.get(f"{parsed.path}?token={receiver_token}").status_code == 200
    with factory() as db:
        native_ticket = db.get(MediaCastTicket, ticket["ticket_id"])
        assert native_ticket.issuer_device_id is not None
        assert timedelta(minutes=16, seconds=59) <= native_ticket.expires_at - native_ticket.created_at <= timedelta(minutes=17, seconds=1)

    renewed = client.post(f"/Lumina/Cast/tickets/{ticket['ticket_id']}/renew", headers=paired)
    assert renewed.status_code == 200 and renewed.json()["ticket_id"] == ticket["ticket_id"]
    assert client.post(f"/Lumina/Cast/tickets/{ticket['ticket_id']}/renew", headers=mediabrowser(BOB_TOKEN)).status_code == 404
    assert client.delete(f"/Lumina/Cast/tickets/{ticket['ticket_id']}", headers=mediabrowser(BOB_TOKEN)).status_code == 404

    # A native MediaBrowser token never authenticates the browser API surface.
    browser_override = app.dependency_overrides.pop(get_current_user)
    try:
        with TestClient(app, base_url="http://localhost") as unauthed:
            denied = unauthed.post("/api/chromecast/tickets", json=body, headers=paired)
        assert denied.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = browser_override

    revoked_token = "alice-revoked-device-token-for-tests"
    with factory() as db:
        device_token(db, ALICE, revoked_token, device_id="device-3")
        db.commit()
    revoked = mediabrowser(revoked_token)
    assert client.post(f"/Lumina/Cast/tickets/{ticket['ticket_id']}/renew", headers=revoked).status_code == 404
    assert client.delete(f"/Lumina/Cast/tickets/{ticket['ticket_id']}", headers=revoked).status_code == 404
    revocable = client.post("/Lumina/Cast/tickets", json=body, headers=revoked)
    assert revocable.status_code == 200
    revocable_media = urlparse(revocable.json()["media_url"])
    assert client.post(f"/Lumina/Cast/tickets/{revocable.json()['ticket_id']}/renew", headers=paired).status_code == 404
    assert client.delete(f"/Lumina/Cast/tickets/{revocable.json()['ticket_id']}", headers=paired).status_code == 404
    with factory() as db:
        record = db.query(DeviceToken).filter(DeviceToken.device_id == "device-3").one()
        db.delete(record)
        db.commit()
    assert client.get(f"{revocable_media.path}?{revocable_media.query}").status_code == 401
    assert client.post(f"/Lumina/Cast/tickets/{revocable.json()['ticket_id']}/renew", headers=revoked).status_code == 401
    assert client.delete(f"/Lumina/Cast/tickets/{revocable.json()['ticket_id']}", headers=revoked).status_code == 401

    with factory() as db:
        db.get(User, ALICE).is_active = False
        db.commit()
    assert client.post("/Lumina/Cast/tickets", json=body, headers=paired).status_code == 401


def test_native_cast_converts_the_selected_jellyfin_source_with_scoped_hls(cast_world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ANN001
    """A native handoff preserves its chosen source, audio and bitmap subtitles without leaking its device credential."""
    client, _current, factory, facts = cast_world
    foreign_token = parse_qs(urlparse(_mint(client)["media_url"]).query)["token"][0]
    facts.update({"container": "matroska,webm", "duration": 12.0, "bit_rate": 20_000_000, "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
        {"index": 2, "type": "audio", "codec": "truehd", "channels": 8, "default": False},
        {"index": 3, "type": "subtitle", "codec": "hdmv_pgs_subtitle", "default": False},
    ]})
    # This is the renderer contract: all incompatible source combinations become
    # 1080p H.264 + stereo AAC and bitmap subtitles are burned into that rendition.
    decision = chromecast.decide(chromecast.CHROMECAST_CAPS, facts)
    selected = chromecast.decide(chromecast.CHROMECAST_CAPS, facts, audio_index=2, subtitle_index=3)
    assert decision.mode == "transcode" and decision.height == 1080
    assert selected.video == "encode" and selected.audio == "encode" and selected.audio_channels == 2 and selected.burn_subtitle == 3

    init, segment = tmp_path / "init.mp4", tmp_path / "seg0.m4s"
    init.write_bytes(b"init")
    segment.write_bytes(b"segment")
    session = SimpleNamespace(
        id="cast-conversion", user_id=ALICE, item_id=MOVIE_4K,
        plan=Plan((0.0, 4.0, 8.0), (4.0, 4.0, 4.0)), bandwidth=4_000_000,
        resource_admission=None,
    )
    opened, stopped = [], []

    def open_conversion(*args, **kwargs):  # noqa: ANN002, ANN003
        opened.append(kwargs)
        return session

    monkeypatch.setattr(chromecast, "open_session", open_conversion)
    monkeypatch.setattr(chromecast.sessions, "get", lambda session_id, user_id: session if (session_id, user_id) == (session.id, ALICE) else None)
    monkeypatch.setattr(chromecast.sessions, "segment", lambda current, name: {"init.mp4": init, "seg0.m4s": segment}.get(name))
    monkeypatch.setattr(chromecast.sessions, "stop", lambda session_id, user_id=None: stopped.append((session_id, user_id)))

    # PlaybackInfo exposes a title/entity id to the player but a concrete version as
    # MediaSourceId. The Cast endpoint must use that actual source, not the title.
    paired = mediabrowser(ALICE_TOKEN)
    playback = client.post(f"/Items/{jellyfin_id(MOVIE)}/PlaybackInfo", headers=paired, json={"MediaSourceId": jellyfin_id(MOVIE_4K)})
    assert playback.status_code == 200, playback.text
    source = next(entry for entry in playback.json()["MediaSources"] if entry["Id"] == jellyfin_id(MOVIE_4K))
    minted = client.post("/Lumina/Cast/tickets", headers=paired, json={
        "item_id": source["Id"], "position_seconds": 7.5, "audio_index": 2, "subtitle": "j:3",
    })
    assert minted.status_code == 200, minted.text
    ticket = minted.json()
    assert ticket["content_type"] == chromecast.MPEGURL and ticket["position_seconds"] == 7.5
    assert "subtitle" not in ticket  # bitmap subtitles are already burned into H.264 output
    assert ALICE_TOKEN not in minted.text and "ApiKey" not in minted.text
    assert opened and opened[0]["start"] == 7.5
    payload = opened[0]["request"]
    assert payload.audio_index == 2 and payload.subtitle == "i:3" and payload.max_height == 1080
    with factory() as db:
        record = db.get(MediaCastTicket, ticket["ticket_id"])
        assert record.item_id == MOVIE_4K and record.playback_session_id == session.id

    media_url = urlparse(ticket["media_url"])
    token = parse_qs(media_url.query)["token"][0]
    master = client.get(f"{media_url.path}?token={token}")
    assert master.status_code == 200 and master.headers["content-type"].startswith(chromecast.MPEGURL)
    assert f"hls/index.m3u8?token={token}" in master.text and "ApiKey" not in master.text
    playlist = client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/index.m3u8?token={token}")
    assert playlist.status_code == 200 and "#EXT-X-PLAYLIST-TYPE:VOD" in playlist.text
    assert f'URI="init.mp4?token={token}"' in playlist.text
    assert all(f"seg{index}.m4s?token={token}" in playlist.text for index in range(3))
    assert client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/seg0.m4s?token={token}").status_code == 200
    assert client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/seg0.m4s?token=wrong").status_code == 401
    assert client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/seg0.m4s?token={foreign_token}").status_code == 401
    with factory() as db:
        record = db.get(MediaCastTicket, ticket["ticket_id"])
        db.delete(db.get(DeviceToken, record.issuer_device_id))
        db.commit()
    assert client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/seg0.m4s?token={token}").status_code == 401
    assert stopped == [(session.id, ALICE)]
    with factory() as db:
        assert db.get(MediaCastTicket, ticket["ticket_id"]) is None


def test_native_hls_ticket_recreates_an_idle_conversion_with_its_saved_track_choices(cast_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    client, _current, factory, facts = cast_world
    facts.update({"container": "matroska", "duration": 120.0, "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
        {"index": 2, "type": "audio", "codec": "truehd", "channels": 8, "default": False},
        {"index": 3, "type": "subtitle", "codec": "hdmv_pgs_subtitle", "default": False},
    ]})
    plan = Plan((0.0, 4.0), (4.0, 4.0))
    initial = SimpleNamespace(id="idle-cast-session", user_id=ALICE, item_id=MOVIE_4K, plan=plan, bandwidth=4_000_000, resource_admission=None)
    reopened = SimpleNamespace(id="reopened-cast-session", user_id=ALICE, item_id=MOVIE_4K, plan=plan, bandwidth=4_000_000, resource_admission=None)
    opened, live = [], {"session": initial}

    def open_conversion(*_args, **kwargs):  # noqa: ANN002, ANN003
        opened.append(kwargs)
        if len(opened) == 1:
            return initial
        if len(opened) == 2:
            return reopened
        raise HTTPException(429, "Conversion is busy")

    monkeypatch.setattr(chromecast, "open_session", open_conversion)
    monkeypatch.setattr(chromecast.sessions, "get", lambda session_id, user_id: live["session"] if live["session"] and (session_id, user_id) == (live["session"].id, ALICE) else None)
    paired = mediabrowser(ALICE_TOKEN)
    minted = client.post("/Lumina/Cast/tickets", headers=paired, json={
        "item_id": jellyfin_id(MOVIE_4K), "position_seconds": 48, "audio_index": 2, "subtitle": "j:3",
    })
    assert minted.status_code == 200, minted.text
    ticket = minted.json()
    media_url = urlparse(ticket["media_url"])
    token = parse_qs(media_url.query)["token"][0]
    assert ticket["content_type"] == chromecast.MPEGURL and ALICE_TOKEN not in ticket["media_url"]
    assert opened[0]["request"].audio_index == 2 and opened[0]["request"].subtitle == "i:3"

    live["session"] = None  # The local 10-minute idle reaper already disposed this ffmpeg session.
    renewed = client.post(f"/Lumina/Cast/tickets/{ticket['ticket_id']}/renew", headers=paired)
    assert renewed.status_code == 200, renewed.text
    assert len(opened) == 1, "Renewing a valid ticket must not treat an idle-reaped conversion as a revoked ticket."

    recreated = client.get(f"{media_url.path}?token={token}")
    assert recreated.status_code == 200 and f"hls/index.m3u8?token={token}" in recreated.text
    assert opened[1]["start"] == 0
    assert opened[1]["request"].audio_index == 2 and opened[1]["request"].subtitle == "i:3"
    with factory() as db:
        assert db.get(MediaCastTicket, ticket["ticket_id"]).playback_session_id == reopened.id

    unavailable = client.get(f"/api/chromecast/{ticket['ticket_id']}/hls/index.m3u8?token={token}")
    assert unavailable.status_code == 429 and ALICE_TOKEN not in unavailable.text
    with factory() as db:
        assert db.get(MediaCastTicket, ticket["ticket_id"]).playback_session_id == reopened.id


def test_revoking_issuer_device_ends_only_its_cast_conversion(cast_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Device revocation cannot leave an unreachable ffmpeg child running until idle reap."""
    client, _current, factory, facts = cast_world
    browser_ticket = _mint(client)
    facts.update({"container": "matroska", "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
    ]})
    opened, stopped = [], []

    def open_conversion(*_args, **_kwargs):  # noqa: ANN002, ANN003
        session = SimpleNamespace(id=f"conversion-{len(opened)}", user_id=ALICE, item_id=MOVIE_1080)
        opened.append(session)
        return session

    monkeypatch.setattr(chromecast, "open_session", open_conversion)
    monkeypatch.setattr(chromecast.sessions, "stop", lambda session_id, user_id=None: stopped.append((session_id, user_id)))

    first = client.post("/Lumina/Cast/tickets", headers=mediabrowser(ALICE_TOKEN), json={
        "item_id": jellyfin_id(MOVIE_1080), "position_seconds": 0,
    })
    assert first.status_code == 200, first.text
    other_token = "alice-other-cast-device-token"
    with factory() as db:
        device_token(db, ALICE, other_token, device_id="other-cast-device")
        db.commit()
    second = client.post("/Lumina/Cast/tickets", headers=mediabrowser(other_token), json={
        "item_id": jellyfin_id(MOVIE_1080), "position_seconds": 0,
    })
    assert second.status_code == 200, second.text

    with factory() as db:
        first_ticket = db.get(MediaCastTicket, first.json()["ticket_id"])
        assert first_ticket is not None
        issuer_id = first_ticket.issuer_device_id
        assert issuer_id is not None
        revoke_connected_app(db, db.get(User, ALICE), issuer_id)
        assert db.get(MediaCastTicket, first_ticket.id) is None
        assert db.get(MediaCastTicket, browser_ticket["ticket_id"]) is not None
        assert db.get(MediaCastTicket, second.json()["ticket_id"]) is not None

    assert stopped == [(opened[0].id, ALICE)]


def test_mint_reaps_an_expired_receiver_conversion(cast_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    client, _current, factory, _facts = cast_world
    stopped = []
    monkeypatch.setattr(chromecast.sessions, "stop", lambda session_id, user_id=None: stopped.append((session_id, user_id)))
    with factory() as db:
        db.add(MediaCastTicket(
            id="expired-cast-ticket", user_id=ALICE, item_id=MOVIE_1080, token_hash="expired",
            expires_at=utcnow() - timedelta(seconds=1), playback_session_id="expired-conversion",
        ))
        db.commit()
    _mint(client)
    assert stopped == [("expired-conversion", ALICE)]
    with factory() as db:
        assert db.get(MediaCastTicket, "expired-cast-ticket") is None


def test_native_mint_stops_a_conversion_when_ticket_persistence_fails(cast_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    client, _current, _factory, facts = cast_world
    facts.update({"container": "matroska", "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
    ]})
    session = SimpleNamespace(id="uncommitted-conversion", user_id=ALICE, item_id=MOVIE_1080)
    stopped = []
    monkeypatch.setattr(chromecast, "open_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(chromecast.sessions, "stop", lambda session_id, user_id=None: stopped.append((session_id, user_id)))

    @contextmanager
    def failed_transaction(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(chromecast, "write_transaction", failed_transaction)
    with pytest.raises(RuntimeError, match="database unavailable"):
        client.post("/Lumina/Cast/tickets", headers=mediabrowser(ALICE_TOKEN), json={
            "item_id": jellyfin_id(MOVIE_1080), "position_seconds": 0,
        })
    assert stopped == [(session.id, ALICE)]
