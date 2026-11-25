"""Verifiable online backups and an offline, verified, reversible restore."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import settings
from app.db import get_db, init_db
from app.main import app
from app.persistence import write_transaction
from app.models import (
    ArrServer,
    DeviceToken,
    IdeaGraph,
    LibraryItem,
    LibraryNote,
    ListeningExportConnection,
    MediaArtifact,
    MediaCastTicket,
    MobilePairing,
    PlaybackHistory,
    MusicCastTicket,
    ServiceConnector,
    StorageRoot,
    User,
)
from app.restore import restore
from app.routers import chromecast, mobile_connect, music
from app.security import CSRF_HEADER, hash_password, session_digest
from app.services import backups, tmdb
from app.services.yt_dlp_service import YtDlpService
from arr_fake import FakeTmdb
from support import make_user
from test_books_api import _cbz
from test_photos import _jpeg
from test_v1_sessions import login
from title_support import ALBUM, ALICE, BOB, FILE, MOVIE, MOVIE_1080, SECRET_EPISODE, TRACKS, seed_gallery, seed_tree

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def admin(tmp_path):  # noqa: ANN001, ANN201
    init_db()
    with db_module.SessionLocal.begin() as db:
        db.add(User(id="u1", username="owner", display_name="Owner", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
        db.add(StorageRoot(id="root-ext", label="NAS", path=str(tmp_path / "nas"), mode="external", observation={}))
        db.add(MediaArtifact(id="art-1", root_id="root-ext", relative_path="show/ep1.mp4", ownership="external", size=5))
        db.add(LibraryItem(id="item-1", user_id="u1", title="Episode", visibility="household", metadata_json={}, status="available"))
        db.add(LibraryNote(id="note-1", item_id="item-1", user_id="u1", body="remember this", visibility="private"))
    client = TestClient(app, base_url="http://localhost")
    response = client.post("/api/session/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 200, response.text
    headers = {"Origin": settings.allowed_origins_list[0], CSRF_HEADER: response.json()["csrf_token"]}
    yield client, headers
    client.close()


def test_backup_restore_isolated_roundtrip(admin, tmp_path) -> None:  # noqa: ANN001
    client, headers = admin
    nas = tmp_path / "nas" / "show"
    nas.mkdir(parents=True)
    (nas / "ep1.mp4").write_bytes(b"media")
    before = sorted(p.stat().st_mtime_ns for p in nas.iterdir())

    with db_module.SessionLocal.begin() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_api_key, record.smtp_password = "sk-test-secret-147", "smtp-secret-258"
        db.add(IdeaGraph(id="graph-1", library_item_id="item-1", summary_id="summary-1",
                         transcript_id="transcript-1", transcript_revision=1, model_id="test-model",
                         state="succeeded", nodes=[{"label": "Archived idea"}]))
        db.add(ArrServer(id="arr", kind="sonarr", name="Sonarr", base_url="http://sonarr.local", api_key="arr-secret-369", path_mappings=[]))
        db.add(ServiceConnector(id="connector", kind="nzbget", name="NZBGet", base_url="http://nzbget.local", api_key="connector-key-741",
                                username="connector-user-852", password="connector-password-963"))
        db.add(ListeningExportConnection(user_id="u1", provider="listenbrainz", generation="test-generation", enabled=True,
                                         credentials={"token": "listen-secret-159"}))
    created = client.post("/api/admin/backups", headers=headers)
    assert created.status_code == 201, created.text
    manifest = created.json()
    # The AI key is redacted from the copy (bytes included); a restore needs it re-entered.
    copy = (backups.backup_root() / f"{manifest['name']}.db").read_bytes()
    assert b"sk-test-secret-147" not in copy and b"smtp-secret-258" not in copy and b"arr-secret-369" not in copy
    assert all(value not in copy for value in (b"connector-key-741", b"connector-user-852", b"connector-password-963"))
    assert b"listen-secret-159" not in copy
    assert manifest["counts"]["users"] == 2 and manifest["counts"]["library_notes"] == 1
    assert manifest["schema_version"] == db_module.SCHEMA_VERSION and manifest["app_version"]
    listed = client.get("/api/admin/backups").json()
    assert [row["name"] for row in listed["backups"]] == [manifest["name"]]
    assert listed["schedule"] == {"daily": True, "keep": 7}
    assert client.post(f"/api/admin/backups/{manifest['name']}/verify", headers=headers).json() == {"ok": True, "problems": []}
    url = f"/api/admin/backups/{manifest['name']}/download"
    assert client.get(url).status_code in (404, 405)  # never a bare GET (SPA fallback may 404): the password is re-proved
    assert client.post(url, headers=headers, json={"password": "wrong"}).status_code == 403
    download = client.post(url, headers=headers, json={"password": PASSWORD})
    assert download.status_code == 200 and download.headers["cache-control"] == "no-store"
    assert download.content[:16] == b"SQLite format 3\x00"

    # Restore into a separate instance directory: the live data dir is never touched.
    target = tmp_path / "restored"
    target.mkdir()
    (target / settings.database_filename).write_bytes(b"previous")
    kept = restore(backups.backup_root() / f"{manifest['name']}.db", target)
    assert kept is not None and kept.read_bytes() == b"previous"
    restored = sqlite3.connect(target / settings.database_filename)
    assert restored.execute("SELECT body FROM library_notes").fetchall() == [("remember this",)]
    assert restored.execute("SELECT count(*) FROM users").fetchone() == (2,)
    assert restored.execute("SELECT ai_api_key FROM app_settings").fetchall() == [(None,)]
    assert restored.execute("SELECT state, json_extract(nodes, '$[0].label') FROM idea_graphs WHERE id = 'graph-1'").fetchone() == (
        "succeeded", "Archived idea",
    )
    assert restored.execute("SELECT root_id, relative_path FROM media_artifacts").fetchall() == [("root-ext", "show/ep1.mp4")]
    restored.close()
    # External media is referenced, never copied or rewritten.
    assert sorted(p.stat().st_mtime_ns for p in nas.iterdir()) == before
    assert not any(p.suffix == ".mp4" for p in backups.backup_root().iterdir())


def test_current_backup_restores_member_activity_and_effective_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    root = tmp_path / "synthetic-media"
    with db_module.SessionLocal.begin() as db:
        password_hash = hash_password(PASSWORD)
        db.add_all([
            make_user("owner-id", username="owner", role="admin", password_hash=password_hash),
            make_user(ALICE, username="alice", password_hash=password_hash),
            make_user(BOB, username="bob", password_hash=password_hash),
        ])
        seed_tree(db, root)
        seed_gallery(db, root)
        record = YtDlpService(db).ensure_app_settings()
        record.requests_enabled = True
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add(PlaybackHistory(
            id="synthetic-history", user_id=ALICE, user_name="Alice", source="library",
            title="Movie", item_id=MOVIE_1080, client={"kind": "web", "name": "Lumina web"}, method="direct",
            started_at=now - timedelta(minutes=2), ended_at=now, watched_seconds=120, stopped_by_admin=False,
        ))
    tmdb_client = FakeTmdb()
    tmdb_client.MOVIES = {987654: {"id": 987654, "title": "A Synthetic Premiere"}}

    def request(client: TestClient, method: str, path: str, status: int = 200, **kwargs: Any) -> Any:
        response = client.request(method, path, **kwargs)
        assert response.status_code == status, response.text
        return response.json() if response.headers.get("content-type") == "application/json" else response.content

    def sign_in(client: TestClient, username: str) -> str:
        client.headers.update({"Origin": settings.allowed_origins_list[0], CSRF_HEADER: login(client, username)})
        return request(client, "GET", "/api/session/me")["user"]["id"]

    with ExitStack() as clients:
        owner, alice, bob, anonymous = (
            clients.enter_context(closing(TestClient(app, base_url="http://localhost"))) for _ in range(4)
        )
        members = [(owner, "owner", "owner-id"), (alice, "alice", ALICE), (bob, "bob", BOB)]
        for client, username, user_id in members:
            assert sign_in(client, username) == user_id

        sections = {"sections": ["music", "books:manga"], "streaming": {"youtube": False}, "can_download": False}
        request(owner, "PUT", f"/api/admin/members/{BOB}/access", json=sections)
        video = f"/api/library/{MOVIE_1080}"
        request(alice, "PUT", f"{video}/playback", json={"position_seconds": 125, "duration_seconds": 7200})
        identity = "youtube:RestoreTst1"
        remote = f"/api/playback/remote/{quote(identity, safe='')}"
        remote_url = "https://www.youtube.com/watch?v=RestoreTst1"
        request(alice, "PUT", remote, json={
            "source_identity": identity, "source_url": remote_url, "title": "Synthetic remote clip",
            "position_seconds": 47.25, "duration_seconds": 600, "checkpoint_client_id": "rehearsal-player",
            "checkpoint_sequence": 1, "expected_revision": 0,
        })
        title = f"/api/titles/{MOVIE}"
        request(alice, "PUT", f"/api/me/favorites/{MOVIE}", 204)
        note = request(alice, "POST", f"{video}/notes", 201, json={"body": "Synthetic private note", "timestamp_ms": 5400})
        watch_queue = "/api/me/watch-queue"
        request(alice, "POST", f"{watch_queue}/entries", json={"ref": {"kind": "library", "library_item_id": MOVIE_1080}})
        queued = request(alice, "POST", f"{watch_queue}/entries", json={
            "ref": {"kind": "remote", "provider": "youtube", "remote_id": "RestoreTst1", "url": remote_url, "title": "Synthetic remote clip"},
            "position": "next",
        })
        music_session = "/api/me/music-session"
        request(bob, "PUT", music_session, json={
            "base_revision": 0, "item_ids": [TRACKS[1], TRACKS[0], TRACKS[1]], "current_index": 2,
            "position_seconds": 38.5, "repeat": "all", "shuffle": True, "active_output": {"kind": "browser"},
        })
        album = request(owner, "POST", "/api/music/albums", json={
            "item_ids": list(TRACKS[:2]), "name": "Curated Synthetic Release", "artist_name": "Synthetic Artist",
            "base": {item_id: {"album_id": ALBUM} for item_id in TRACKS[:2]},
        })
        book = request(owner, "PUT", "/api/books/import?filename=synthetic.cbz", 201, content=_cbz(pages=3))
        book_url = f"/api/books/{book['id']}"
        request(owner, "PATCH", book_url, json={"visibility": "shared"})
        request(alice, "PATCH", f"{book_url}/state", json={"unit_index": 0, "offset": 0.25})
        request(bob, "PATCH", f"{book_url}/state", json={"unit_index": 1, "offset": 0.5, "favorite": True, "reading_list": True})
        with monkeypatch.context() as provider:
            provider.setattr(tmdb, "client_for", lambda _record: tmdb_client)
            pending = request(alice, "POST", "/api/requests", 201, json={"kind": "movie", "tmdb_id": 987654})
        assert pending["status"] == "pending" and pending.get("decided_by") is None

        picks = request(owner, "POST", "/api/collections", 201, json={"name": "Household rehearsal picks", "visibility": "shared"})
        collection = f"/api/collections/{picks['id']}"
        request(owner, "POST", f"{collection}/items/{MOVIE_1080}")
        curated = request(owner, "POST", f"{collection}/items/{TRACKS[0]}")
        photo = request(owner, "PUT", "/api/photos/import?filename=synthetic.jpg", 201, content=_jpeg())
        request(owner, "PATCH", f"/api/photos/{photo['id']}", json={"visibility": "shared"})
        photo_album = request(owner, "POST", "/api/photo-albums", 201, json={"name": "Synthetic photo album", "visibility": "shared"})
        request(owner, "PUT", f"/api/photo-albums/{photo_album['id']}/photos/{photo['id']}")

        agent_token = request(alice, "POST", "/api/connected-apps", 201, json={"name": "Rehearsal client", "scope": "read"})["token"]
        app_password = request(bob, "POST", "/api/connected-apps/app-passwords", 201, json={"name": "Rehearsal player"})["password"]
        bearer = {"Authorization": f"Bearer {agent_token}"}
        request(anonymous, "GET", "/api/session/me", headers=bearer)
        request(anonymous, "GET", "/api/session/me", auth=("bob", app_password))
        manifest = request(owner, "POST", "/api/admin/backups", 201)
        assert manifest["schema_version"] == 44
        name = manifest["name"]
        assert request(owner, "POST", f"/api/admin/backups/{name}/verify") == {"ok": True, "problems": []}

        # Diverge from the snapshot, then close SQLite connections before the offline swap.
        request(alice, "DELETE", watch_queue, params={"confirm": "true"})
        assert request(alice, "GET", watch_queue)["entries"] == []
        data_dir = settings.data_dir
        backup = backups.backup_root() / f"{name}.db"
        db_module.engine.dispose()
        result = subprocess.run(
            [sys.executable, "-m", "app.restore", str(backup), "--data-dir", str(data_dir)],
            cwd=Path(__file__).parents[1],
            env={**os.environ, "LUMINA_DATA_DIR": str(data_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert len(list(data_dir.glob(f"{settings.database_filename}.pre-restore-*"))) == 1

        # Start the regular app against the restored database and try the old credentials first.
        with TestClient(app, base_url="http://localhost"):
            for client, _, _ in members:
                request(client, "GET", "/api/session/me", 401)
            request(anonymous, "GET", "/api/session/me", 401, headers=bearer)
            request(anonymous, "GET", "/api/session/me", 401, auth=("bob", app_password))
            for client, username, user_id in members:
                assert sign_in(client, username) == user_id

            assert request(alice, "GET", f"{video}/playback")["position_seconds"] == 125
            assert request(owner, "GET", f"{video}/playback") is None
            assert request(alice, "GET", remote)["position_seconds"] == 47.25
            assert request(bob, "GET", remote) is None
            assert request(alice, "GET", "/api/me/activity-summary")["watched_seconds"] == 120
            assert request(bob, "GET", "/api/me/activity-summary")["watched_seconds"] == 0
            assert request(alice, "GET", title)["user_data"]["is_favorite"] is True
            assert request(owner, "GET", title)["user_data"]["is_favorite"] is False
            assert request(alice, "GET", f"{video}/notes") == [note]
            assert request(owner, "GET", f"{video}/notes") == []
            assert request(alice, "GET", watch_queue) == queued
            assert request(bob, "GET", watch_queue)["entries"] == []

            resumed = request(bob, "GET", music_session)
            assert [track["id"] for track in resumed["queue"]] == [TRACKS[1], TRACKS[0], TRACKS[1]]
            assert (resumed["current_index"], resumed["position_seconds"], resumed["repeat"], resumed["shuffle"]) == (2, 38.5, "all", True)
            assert request(alice, "GET", music_session)["queue"] == []
            release = request(bob, "GET", f"/api/titles/{album['album_id']}")
            assert release["name"] == "Curated Synthetic Release"
            assert {track["item_id"] for track in release["tracks"]} == set(TRACKS[:2])

            reading = request(bob, "GET", book_url)
            assert (reading["progress"]["unit_index"], reading["progress"]["offset"]) == (1, 0.5)
            assert reading["favorite"] and reading["reading_list"]
            other = request(alice, "GET", book_url)
            assert other["progress"]["offset"] == 0.25 and not other["favorite"] and not other["reading_list"]
            request(bob, "GET", f"{book_url}/pages/1")

            mine = request(alice, "GET", "/api/requests?scope=mine")
            assert mine["counts"]["pending"] == 1 and mine["items"] == [pending]
            assert request(bob, "GET", "/api/requests")["items"] == []
            admin_pending = request(owner, "GET", "/api/requests?scope=all&status=pending")["items"]
            assert [(row["id"], row["status"], row["requested_by"]["id"], row.get("decided_by")) for row in admin_pending] == [
                (pending["id"], "pending", ALICE, None),
            ]
            assert request(alice, "GET", collection)["entries"] == curated["entries"]
            restricted = request(bob, "GET", collection)["entries"]
            assert [(entry["availability"], entry["ref"]["library_item_id"]) for entry in restricted] == [
                ("unavailable", None), ("available", TRACKS[0]),
            ]
            assert request(alice, "GET", f"/api/photos?album_id={photo_album['id']}")["items"][0]["id"] == photo["id"]
            assert request(bob, "GET", "/api/photo-albums")["items"] == []
            request(bob, "GET", video, 404)
            request(alice, "GET", f"/api/library/{FILE[SECRET_EPISODE]}", 404)

            access = request(bob, "GET", "/api/me/access")
            assert access["restricted"] and access["sections"] == sorted(sections["sections"])
            assert access["blocked_streaming"] == ["youtube"] and access["can_download"] is False
            assert request(bob, "POST", "/api/preview", 403, json={"source_url": remote_url})["detail"] == "streaming_blocked:youtube"
            download_url = f"/api/music/tracks/{TRACKS[0]}/audio?format=native&download=true"
            assert request(bob, "GET", download_url, 403)["detail"] == "downloads_not_allowed"
            assert request(alice, "GET", download_url)
            assert request(alice, "GET", "/api/me/access")["restricted"] is False


def test_backups_admin_only_and_names_validated(admin) -> None:  # noqa: ANN001
    client, headers = admin
    name = client.post("/api/admin/backups", headers=headers).json()["name"]
    member = TestClient(app, base_url="http://localhost")
    member.post("/api/session/login", json={"username": "member", "password": PASSWORD})
    assert member.get("/api/admin/backups").status_code == 403
    member_headers = {**headers, CSRF_HEADER: member.get("/api/session/me").json()["csrf_token"]}
    assert member.post(f"/api/admin/backups/{name}/download", headers=member_headers, json={"password": PASSWORD}).status_code == 403
    assert client.post("/api/admin/backups/..app/download", headers=headers, json={"password": PASSWORD}).status_code == 404
    assert client.delete("/api/admin/backups/lumina-20990101T000000Z-manual", headers=headers).status_code == 404
    assert client.delete(f"/api/admin/backups/{name}", headers=headers).status_code == 204
    assert client.get("/api/admin/backups").json()["backups"] == []


def test_backup_incomplete_safe(admin) -> None:  # noqa: ANN001
    client, headers = admin
    manifest = backups.create_backup()
    path = backups.backup_root() / f"{manifest['name']}.db"
    # A copy whose manifest was never written is not a recovery point.
    orphan = backups.backup_root() / "lumina-20000101T000000Z-manual.db"
    orphan.write_bytes(path.read_bytes())
    assert [row["name"] for row in client.get("/api/admin/backups").json()["backups"]] == [manifest["name"]]
    # A tampered copy fails verification and restore refuses it.
    with path.open("r+b") as handle:
        handle.seek(200)
        handle.write(b"\xff\xff")
    result = client.post(f"/api/admin/backups/{manifest['name']}/verify", headers=headers).json()
    assert result["ok"] is False and "checksum" in result["problems"][0]
    with pytest.raises(SystemExit, match="verification"):
        restore(path, settings.data_dir.parent / "elsewhere")


def test_backup_refuses_a_malformed_source_without_publishing_a_copy(admin) -> None:  # noqa: ANN001
    client, headers = admin
    with db_module.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE service_connectors RENAME TO old_connectors")

    response = client.post("/api/admin/backups", headers=headers)

    assert response.status_code == 409, response.text
    assert "service_connectors" in response.json()["detail"]
    assert list(backups.backup_root().iterdir()) == []


def test_restored_sessions_invites_and_cast_capabilities_are_revoked(admin, tmp_path) -> None:  # noqa: ANN001
    client, headers = admin
    invite = client.post("/api/admin/invitations", json={"role": "viewer"}, headers=headers).json()
    token = invite["invitation_url"].split("#invite=", 1)[1]
    music_token, media_token = "captured-sonos-bearer", "captured-chromecast-bearer"
    with db_module.SessionLocal.begin() as db:
        db.add(MusicCastTicket(
            id="music-cast-1", user_id="u1", speaker_id="speaker-1", token_hash=sha256(music_token.encode()).hexdigest(),
            item_ids=["item-1"], expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24),
        ))
        db.add(MediaCastTicket(
            id="media-cast-1", user_id="u1", item_id="item-1", token_hash=sha256(media_token.encode()).hexdigest(),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        ))
    with db_module.SessionLocal.begin() as db:
        YtDlpService(db).ensure_app_settings()
    assert client.post("/api/mobile-connect/enable", headers=headers).status_code == 200
    pairings = []
    for _ in range(3):
        created = client.post("/api/mobile-connect", json={"server_url": "https://lumina.example.com"}, headers=headers)
        assert created.status_code == 201, created.text
        pairings.append(created.json())
    pairing_tokens = [json.loads(pair["qr_payload"])["token"] for pair in pairings]
    name = backups.create_backup()["name"]
    backup = backups.backup_root() / f"{name}.db"
    # After the snapshot, one handoff stays pending, one is consumed and one is revoked.
    assert client.post("/api/mobile-connect/claim", json={"token": pairing_tokens[1], "name": "Synthetic phone"}, headers=headers).status_code == 200
    assert client.delete(f"/api/mobile-connect/{pairings[2]['id']}", headers=headers).status_code == 204
    assert [client.get(f"/api/mobile-connect/{pair['id']}").json()["status"] for pair in pairings] == ["pending", "used", "revoked"]
    captured = sqlite3.connect(backup)
    assert captured.execute("SELECT count(*) FROM music_cast_tickets").fetchone() == (1,)
    assert captured.execute("SELECT count(*) FROM media_cast_tickets").fetchone() == (1,)
    assert captured.execute("SELECT count(*) FROM mobile_pairings WHERE used_at IS NULL AND revoked_at IS NULL").fetchone() == (3,)
    captured.close()
    target = tmp_path / "restored"
    restore(backup, target)
    copy = sqlite3.connect(target / settings.database_filename)
    assert copy.execute("SELECT count(*) FROM app_sessions").fetchone() == (0,)
    assert copy.execute("SELECT count(*) FROM music_cast_tickets").fetchone() == (0,)
    assert copy.execute("SELECT count(*) FROM media_cast_tickets").fetchone() == (0,)
    revoked = copy.execute("SELECT revoked_at FROM account_tokens WHERE token_digest = ?", (session_digest(token),)).fetchone()
    assert revoked is not None and revoked[0] is not None
    copy.close()

    restored_engine = create_engine(f"sqlite:///{target / settings.database_filename}")
    restored_app = FastAPI()
    restored_app.add_api_route("/api/music/cast/{ticket_id}/{item_id}/audio", music.cast_audio, methods=["GET"])
    restored_app.add_api_route("/api/chromecast/{ticket_id}/media", chromecast.media, methods=["GET"])
    restored_app.add_api_route("/api/mobile-connect/claim", mobile_connect.claim, methods=["POST"])

    def restored_db():  # noqa: ANN202
        with Session(restored_engine) as db:
            yield db

    restored_app.dependency_overrides[get_db] = restored_db
    with TestClient(restored_app) as restored_client:
        assert restored_client.get(f"/api/music/cast/music-cast-1/item-1/audio?token={music_token}").status_code == 401
        assert restored_client.get(f"/api/chromecast/media-cast-1/media?token={media_token}").status_code == 401
        claims = [restored_client.post("/api/mobile-connect/claim", json={"token": pairing_token, "name": "Restored phone"}).status_code for pairing_token in pairing_tokens]
        assert claims == [401, 401, 401]
        with Session(restored_engine) as db:
            assert db.query(MobilePairing).count() == 0
            assert db.query(DeviceToken).count() == 0
    restored_engine.dispose()


def test_restore_refuses_an_unsupported_backup_before_replacing_current_data(admin, tmp_path) -> None:  # noqa: ANN001
    name = backups.create_backup()["name"]
    backup = backups.backup_root() / f"{name}.db"
    with sqlite3.connect(backup) as copy:
        copy.execute("PRAGMA user_version = 43")
    manifest_path = backup.with_suffix(".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(schema_version=43, size=backup.stat().st_size, sha256=sha256(backup.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    target = tmp_path / "restored-old"
    target.mkdir()
    live = target / settings.database_filename
    live.write_bytes(b"current-schema-data")
    with pytest.raises(SystemExit, match="verification"):
        restore(backup, target)
    assert live.read_bytes() == b"current-schema-data"


@pytest.mark.parametrize("corrupt_stage", [False, True], ids=["source", "staged-copy"])
def test_restore_refuses_a_corrupt_current_backup_before_replacing_current_data(admin, tmp_path, monkeypatch, corrupt_stage) -> None:  # noqa: ANN001
    name = backups.create_backup()["name"]
    backup = backups.backup_root() / f"{name}.db"

    def drop_trigger(path: Path) -> None:
        with closing(sqlite3.connect(path)) as copy:
            copy.execute("DROP TRIGGER trg_photo_face_index_insert")

    if corrupt_stage:
        copyfile = shutil.copyfile

        def copy_then_corrupt(source: Path, destination: Path) -> Path:
            copyfile(source, destination)
            drop_trigger(destination)
            return destination

        monkeypatch.setattr(shutil, "copyfile", copy_then_corrupt)
    else:
        drop_trigger(backup)
        manifest_path = backup.with_suffix(".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(size=backup.stat().st_size, sha256=sha256(backup.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    target = tmp_path / "restored-corrupt"
    target.mkdir()
    live = target / settings.database_filename
    live.write_bytes(b"current-schema-data")
    with pytest.raises(SystemExit, match="trg_photo_face_index_insert"):
        restore(backup, target)
    assert live.read_bytes() == b"current-schema-data"
    assert not live.with_name(live.name + ".restoring").exists()


def test_standalone_restore_refuses_a_missing_mapped_table_before_replacing_current_data(admin, tmp_path) -> None:  # noqa: ANN001
    name = backups.create_backup()["name"]
    backup = backups.backup_root() / f"{name}.db"
    target = tmp_path / "standalone-target"
    target.mkdir()
    live = target / settings.database_filename
    live.write_bytes(backup.read_bytes())
    before = live.read_bytes()
    with sqlite3.connect(backup) as copy:
        copy.execute("DROP TABLE users")
    manifest_path = backup.with_suffix(".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(size=backup.stat().st_size, sha256=sha256(backup.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "app.restore", str(backup), "--data-dir", str(target)],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "LUMINA_DATA_DIR": str(target)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "users" in result.stderr
    assert live.read_bytes() == before


def test_restore_refuses_while_server_holds_data_dir(admin, tmp_path) -> None:  # noqa: ANN001
    name = backups.create_backup()["name"]
    lock = backups.hold_server_lock()
    try:
        with pytest.raises(SystemExit, match="running"):
            restore(backups.backup_root() / f"{name}.db", settings.data_dir)
    finally:
        lock.close()
    live = settings.data_dir / settings.database_filename
    assert live.exists()  # untouched by the refused restore


def test_scheduled_backup_retention_keeps_manual(admin) -> None:  # noqa: ANN001
    manual = backups.create_backup("manual")
    root = backups.backup_root()
    for days_ago in (2, 3, 4):
        stamp = datetime.now(UTC) - timedelta(days=days_ago)
        name = f"lumina-{stamp:%Y%m%dT%H%M%S}Z-scheduled"
        (root / f"{name}.db").write_bytes((root / f"{manual['name']}.db").read_bytes())
        (root / f"{name}.json").write_text(json.dumps({**manual, "name": name, "kind": "scheduled", "created_at": stamp.isoformat()}))
    created = backups.run_scheduled(keep=2)
    assert created is not None
    assert backups.run_scheduled(keep=2) is None  # the next one is not due for a day
    names = [row["name"] for row in backups.list_backups()]
    scheduled = [name for name in names if name.endswith("-scheduled")]
    assert len(scheduled) == 2 and created["name"] in scheduled and manual["name"] in names
    assert len(list(root.glob("*-scheduled.db"))) == 2  # pruned copies are gone from disk


def test_listing_current_manifests_never_verifies_backup_contents(admin, monkeypatch) -> None:  # noqa: ANN001
    manifest = backups.create_backup()
    monkeypatch.setattr(backups, "verify_file", lambda _path: (_ for _ in ()).throw(AssertionError("listing must stay metadata-only")))
    assert [item["name"] for item in backups.list_backups()] == [manifest["name"]]




def test_server_lock_is_shared_between_instances() -> None:
    first, second = backups.hold_server_lock(), backups.hold_server_lock()
    try:
        with open(settings.data_dir / backups.LOCK_FILENAME, "a") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        first.close()
        second.close()


def test_restored_database_is_private_like_the_backup(admin, tmp_path) -> None:  # noqa: ANN001
    name = backups.create_backup()["name"]
    target = tmp_path / "restored"
    restore(backups.backup_root() / f"{name}.db", target)
    assert (target / settings.database_filename).stat().st_mode & 0o077 == 0  # every password hash lives here


def test_failed_startup_drains_a_writer_before_offline_restore(admin, monkeypatch) -> None:
    from app import main
    from app.services import library_import
    from app.services.probe_warming import ProbeWarming

    name = backups.create_backup()["name"]
    data_dir = settings.data_dir
    backup = backups.backup_root() / f"{name}.db"
    live = data_dir / settings.database_filename
    fault = RuntimeError("injected failure after probe warming started")
    writing = threading.Event()
    allow_commit = threading.Event()
    warming = ProbeWarming()
    stop_warming = warming.stop

    def write_probe_result() -> bool:
        with db_module.SessionLocal() as db, write_transaction(db, name="test-startup-writer"):
            db.get(User, "u1").display_name = "Committed during startup"
            db.flush()
            writing.set()
            if not allow_commit.wait(timeout=20):
                raise TimeoutError("test did not release the probe writer")
        return False

    def fail_start() -> None:
        assert writing.wait(timeout=5), "probe warming never reached its SQLite write"
        raise fault

    def try_restore() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "app.restore", str(backup), "--data-dir", str(data_dir)],
            cwd=Path(__file__).parents[1],
            env={**os.environ, "LUMINA_DATA_DIR": str(data_dir)},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    monkeypatch.setattr(library_import, "after_import_hooks", list(library_import.after_import_hooks))
    monkeypatch.setattr(main, "probe_warming", warming)
    monkeypatch.setattr(warming, "run_once", write_probe_result)
    monkeypatch.setattr(main.m2ts_irap_indexes, "start", fail_start)

    async def exercise():
        cleanup_or_failure = asyncio.Event()
        loop = asyncio.get_running_loop()

        def stop() -> None:
            loop.call_soon_threadsafe(cleanup_or_failure.set)
            stop_warming()

        monkeypatch.setattr(warming, "stop", stop)

        async def start():
            with pytest.raises(RuntimeError) as caught:
                async with main.lifespan(app):
                    pytest.fail("failed startup entered the serve phase")
            return caught.value, warming._thread.is_alive()

        startup = asyncio.create_task(start())
        startup.add_done_callback(lambda _: cleanup_or_failure.set())
        try:
            await asyncio.wait_for(cleanup_or_failure.wait(), timeout=8)
            refused = await asyncio.to_thread(try_restore)
        finally:
            allow_commit.set()
            try:
                failure, writer_alive_at_failure = await asyncio.wait_for(startup, timeout=8)
            finally:
                # Also reap the fixture worker when the implementation leaks it.
                stop_warming()
        return failure, writer_alive_at_failure, refused

    failure, writer_alive_at_failure, refused = asyncio.run(exercise())
    assert failure is fault and failure.__traceback__ is not None
    assert refused.returncode != 0 and "Stop it before restoring." in refused.stderr
    assert not list(data_dir.glob(f"{settings.database_filename}.pre-restore-*"))

    db_module.engine.dispose()
    restored = try_restore()
    assert restored.returncode == 0, restored.stderr
    assert not writer_alive_at_failure, "startup raised before the SQLite writer had exited"

    kept, = data_dir.glob(f"{settings.database_filename}.pre-restore-*")
    with closing(sqlite3.connect(kept)) as previous, closing(sqlite3.connect(live)) as current:
        assert previous.execute("SELECT display_name FROM users WHERE id = 'u1'").fetchone() == ("Committed during startup",)
        assert current.execute("SELECT display_name FROM users WHERE id = 'u1'").fetchone() == ("Owner",)
