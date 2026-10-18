"""Logical plays, provider uncertainty and account changes never create duplicate exports."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.models import LibraryItem, ListeningExportConnection, ListeningPlayEvent, MediaTitle
from app.routers import listening_export as router
from app.security import get_current_user
from app.services import listening_export as exports
from app.services.local_ai import LocalAiError
from support import make_user


@pytest.fixture
def export_api(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    app = FastAPI()
    router.register(app)
    current = {"id": "alice"}
    with db_factory.begin() as db:
        db.add_all([make_user("alice"), make_user("bob"), MediaTitle(id="album", type="album", key="album:one", name="Album"),
                    LibraryItem(id="track", user_id="alice", visibility="private", title="Track", kind="track", status="available", title_id="album", duration=120,
                                metadata_json={"artist": "Artist", "lumina_audio_tags": {"title": "Track", "artist": "Artist"}})])
    def database():  # noqa: ANN202
        with db_factory() as db:
            yield db
            db.commit()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: make_user(current["id"])
    monkeypatch.setattr(exports.time, "time", lambda: 1000)
    with TestClient(app, base_url="http://localhost") as client:
        yield client, db_factory, current


def event(**changes):  # noqa: ANN003, ANN201
    return {"id": str(uuid.uuid4()), "owner_id": "alice", "account_generations": {"listenbrainz": "generation", "lastfm": "generation"}, "item_id": "track", "started_at": 900, "listened_seconds": 60, **changes}


def connected(factory, provider="listenbrainz"):  # noqa: ANN001, ANN201
    with factory.begin() as db:
        db.add(ListeningExportConnection(user_id="alice", provider=provider, generation="generation", enabled=True, username="alice", credentials={"token": "private-token"}))


def test_disabled_export_does_not_record_and_short_listens_cannot_export(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    assert client.post("/api/me/listening-events", json=event()).json() == {"recorded": False}
    with factory() as db:
        assert db.query(ListeningPlayEvent).count() == 0
    connected(factory)
    monkeypatch.setattr(exports, "listenbrainz", lambda *a, **kw: pytest.fail("ineligible listen exported"))
    result = client.post("/api/me/listening-events", json=event(listened_seconds=59)).json()
    assert result["deliveries"]["listenbrainz"]["status"] == "ineligible"
    assert client.post("/api/me/listening-events", json=event(listened_seconds=200)).json() == {"recorded": False}
    with factory.begin() as db:
        db.get(LibraryItem, "track").metadata_json = {"artist": "Filename guess"}
    assert client.post("/api/me/listening-events", json=event()).json() == {"recorded": False}


def test_replay_and_seek_reports_do_not_duplicate_or_accumulate_listening(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    connected(factory)
    calls = []
    monkeypatch.setattr(exports, "listenbrainz", lambda *a, **kw: calls.append(kw["body"]) or {"status": "ok"})
    payload = event(listened_seconds=40)
    assert client.post("/api/me/listening-events", json=payload).json()["listened_seconds"] == 40
    assert client.post("/api/me/listening-events", json=payload).json()["listened_seconds"] == 40
    assert calls == []
    payload["listened_seconds"] = 60
    first = client.post("/api/me/listening-events", json=payload).json()
    assert first["deliveries"]["listenbrainz"]["status"] == "sent"
    assert client.post("/api/me/listening-events", json=payload).json() == first
    assert client.post("/api/me/listening-events", json={**payload, "id": str(uuid.uuid4())}).json()["id"] == payload["id"]
    assert len(calls) == 1 and calls[0]["payload"][0]["track_metadata"]["track_name"] == "Track"


def test_accepted_timeout_survives_reload_replay_and_read_only_reconciliation(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    connected(factory)
    sent = []
    def provider(token, path, **kw):  # noqa: ANN001, ANN201
        if kw.get("body"):
            sent.append(kw["body"])
            raise LocalAiError("response lost")
        return {"payload": {"listens": [{"listened_at": 900, "track_metadata": {"track_name": "Track", "artist_name": "Artist"}}]}}
    monkeypatch.setattr(exports, "listenbrainz", provider)
    payload = event()
    response = client.post("/api/me/listening-events", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["deliveries"]["listenbrainz"]["status"] == "uncertain"
    assert client.post("/api/me/listening-events", json=payload).json()["deliveries"]["listenbrainz"]["status"] == "uncertain"
    assert client.post(f"/api/me/listening-events/{payload['id']}/reconcile").json()["deliveries"]["listenbrainz"]["status"] == "sent"
    assert len(sent) == 1


def test_account_revocation_owner_isolation_and_library_visibility_are_rechecked(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, current = export_api
    connected(factory)
    payload = event(listened_seconds=20)
    assert client.post("/api/me/listening-events", json=payload).status_code == 200
    assert client.delete("/api/me/listening-export/listenbrainz").json()["credentials_set"] is False
    monkeypatch.setattr(exports, "listenbrainz", lambda *a, **kw: pytest.fail("revoked export dispatched"))
    payload["listened_seconds"] = 60
    assert client.post("/api/me/listening-events", json=payload).json()["deliveries"]["listenbrainz"]["status"] == "revoked"
    current["id"] = "bob"
    assert client.get("/api/me/listening-events").json()["items"] == []
    assert client.post(f"/api/me/listening-events/{payload['id']}/reconcile").status_code == 404
    assert client.post("/api/me/listening-events", json=payload).status_code == 409
    current["id"] = "alice"
    with factory.begin() as db:
        db.get(LibraryItem, "track").status = "missing"
    assert client.post("/api/me/listening-events", json=payload).status_code == 404


def test_provider_authorization_and_signatures_keep_credentials_write_only(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    calls = []
    def request(base, method, path, **kw):  # noqa: ANN001, ANN201
        calls.append((base, method, path, kw))
        if "validate-token" in path:
            return {"valid": True, "user_name": "listener"}
        data = kw["data"]
        assert len(data["api_sig"]) == 32 and data["format"] == "json"
        return {"token": "requesttoken"} if data["method"] == "auth.getToken" else {"session": {"key": "private-session", "name": "listener"}}
    monkeypatch.setattr(exports, "bounded_json_request", request)
    assert client.put("/api/me/listening-export/listenbrainz", json={"token": "private-token"}).json()["enabled"] is True
    auth = client.post("/api/me/listening-export/lastfm/authorize", json={"api_key": "a"*32, "api_secret": "b"*32})
    assert auth.status_code == 200 and auth.json()["authorization_url"].startswith("https://www.last.fm/api/auth/")
    assert "b"*32 not in auth.text
    assert client.post("/api/me/listening-export/lastfm/finish").json()["enabled"] is True
    public = client.get("/api/me/listening-export")
    assert "private-token" not in public.text and "private-session" not in public.text and "api_secret" not in public.text


def test_failed_replacement_keeps_working_connection_and_its_queued_generation(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    connected(factory)
    monkeypatch.setattr(exports, "listenbrainz", lambda *args, **kwargs: {"valid": False})
    assert client.put("/api/me/listening-export/listenbrainz", json={"token": "typo"}).status_code == 400
    with factory() as db:
        row = db.get(ListeningExportConnection, ("alice", "listenbrainz"))
        assert row.enabled and row.generation == "generation" and row.credentials == {"token": "private-token"}
    connected(factory, "lastfm")
    def unavailable(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise LocalAiError("provider unavailable")
    monkeypatch.setattr(exports, "lastfm", unavailable)
    assert client.post("/api/me/listening-export/lastfm/authorize", json={"api_key": "a"*32, "api_secret": "b"*32}).status_code == 502
    with factory() as db:
        row = db.get(ListeningExportConnection, ("alice", "lastfm"))
        assert row.enabled and row.generation == "generation" and row.credentials == {"token": "private-token"}


def test_offline_play_bound_to_previous_account_cannot_export_to_a_replacement(export_api, monkeypatch):  # noqa: ANN001, ANN201
    client, factory, _ = export_api
    connected(factory)
    with factory.begin() as db:
        db.get(ListeningExportConnection, ("alice", "listenbrainz")).generation = "new-account"
    monkeypatch.setattr(exports, "listenbrainz", lambda *args, **kwargs: pytest.fail("old offline play exported to a new account"))
    assert client.post("/api/me/listening-events", json=event()).json() == {"recorded": False}
