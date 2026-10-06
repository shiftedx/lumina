"""Verifiable online backups and an offline, verified, reversible restore."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app import db as db_module
from app.config import settings
from app.db import init_db
from app.main import app
from app.models import ArrServer, LibraryItem, LibraryNote, MediaArtifact, StorageRoot, User
from app.restore import restore
from app.security import CSRF_HEADER, hash_password, session_digest
from app.services import backups
from app.services.yt_dlp_service import YtDlpService

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
        db.add(ArrServer(id="arr", kind="sonarr", name="Sonarr", base_url="http://sonarr.local", api_key="arr-secret-369", path_mappings=[]))
    created = client.post("/api/admin/backups", headers=headers)
    assert created.status_code == 201, created.text
    manifest = created.json()
    # The AI key is redacted from the copy (bytes included); a restore needs it re-entered.
    copy = (backups.backup_root() / f"{manifest['name']}.db").read_bytes()
    assert b"sk-test-secret-147" not in copy and b"smtp-secret-258" not in copy and b"arr-secret-369" not in copy
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
    assert restored.execute("SELECT root_id, relative_path FROM media_artifacts").fetchall() == [("root-ext", "show/ep1.mp4")]
    restored.close()
    # External media is referenced, never copied or rewritten.
    assert sorted(p.stat().st_mtime_ns for p in nas.iterdir()) == before
    assert not any(p.suffix == ".mp4" for p in backups.backup_root().iterdir())


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


def test_restored_sessions_invites_revoked(admin, tmp_path) -> None:  # noqa: ANN001
    client, headers = admin
    invite = client.post("/api/admin/invitations", json={"role": "viewer"}, headers=headers).json()
    token = invite["invitation_url"].split("#invite=", 1)[1]
    name = backups.create_backup()["name"]
    target = tmp_path / "restored"
    restore(backups.backup_root() / f"{name}.db", target)
    copy = sqlite3.connect(target / settings.database_filename)
    assert copy.execute("SELECT count(*) FROM app_sessions").fetchone() == (0,)
    revoked = copy.execute("SELECT revoked_at FROM account_tokens WHERE token_digest = ?", (session_digest(token),)).fetchone()
    assert revoked is not None and revoked[0] is not None
    copy.close()


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


def test_pre_upgrade_backups_keep_the_newest_three(admin) -> None:  # noqa: ANN001
    manual = backups.create_backup("manual")
    root = backups.backup_root()
    stamps = [datetime.now(UTC) - timedelta(days=days_ago) for days_ago in (1, 2, 3, 4, 5)]
    for stamp in stamps:
        name = f"lumina-{stamp:%Y%m%dT%H%M%S}Z-pre-upgrade"
        (root / f"{name}.db").write_bytes((root / f"{manual['name']}.db").read_bytes())
        (root / f"{name}.json").write_text(json.dumps({**manual, "name": name, "kind": "pre-upgrade", "created_at": stamp.isoformat()}))
    backups.run_scheduled(keep=7)
    names = [row["name"] for row in backups.list_backups()]
    kept = [name for name in names if name.endswith("-pre-upgrade")]
    assert kept == [f"lumina-{stamp:%Y%m%dT%H%M%S}Z-pre-upgrade" for stamp in stamps[:3]]  # the newest three
    assert manual["name"] in names and len(list(root.glob("*-pre-upgrade.db"))) == 3


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
