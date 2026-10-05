"""Admin Media server settings: the TMDB key is write-only and never leaves the live database."""
from __future__ import annotations

import sqlite3

import pytest

from app import db as db_module
from app.config import settings
from app.db import init_db
from app.models import AppSettings, DeviceToken
from app.restore import restore
from app.security import session_digest
from app.services import backups
from app.services.yt_dlp_service import YtDlpService
from support import make_user

DEFAULTS = {
    "jellyfin_enabled": False, "jellyfin_url": None, "jellyfin_public_url": None, "has_tmdb_key": False, "metadata_language": "en-US",
    "introdb_enabled": False, "hwaccel": "auto", "max_playback_sessions": 2, "transcode_cache_gb": 10,
    "jellyfin_import_url": None, "anime_folders": ["Anime"], "recategorising": False,
    "members_edit_metadata": False,
}


@pytest.fixture
def admin(db_factory, api_client, monkeypatch):
    monkeypatch.setattr(settings, "app_public_url", None)
    monkeypatch.setattr(settings, "tmdb_api_key", "")
    owner, member = make_user("owner", role="admin"), make_user("member")
    with db_factory.begin() as db:
        db.add_all([owner, member])
    current = {"user": owner}
    return api_client(user=lambda: current["user"], base_url="http://localhost"), current, member, db_factory


def test_fresh_database_serves_defaults(admin) -> None:
    client, *_ = admin
    response = client.get("/api/admin/media-server")
    assert response.status_code == 200, response.text
    assert response.json() == DEFAULTS


def test_update_persists_fields_and_keeps_the_tmdb_key_write_only(admin) -> None:
    client, _, _, factory = admin
    saved = client.put("/api/admin/media-server", json={
        "jellyfin_enabled": True, "tmdb_api_key": "  tmdb-secret-123 ", "metadata_language": "de-DE",
        "introdb_enabled": True, "hwaccel": "qsv", "max_playback_sessions": 4, "transcode_cache_gb": 50,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**DEFAULTS, "jellyfin_enabled": True, "has_tmdb_key": True, "metadata_language": "de-DE",
                            "introdb_enabled": True, "hwaccel": "qsv", "max_playback_sessions": 4, "transcode_cache_gb": 50}
    assert "tmdb-secret-123" not in saved.text and "tmdb-secret-123" not in client.get("/api/admin/media-server").text
    with factory() as db:
        assert db.get(AppSettings, 1).tmdb_api_key == "tmdb-secret-123"

    assert client.put("/api/admin/media-server", json={}).json()["has_tmdb_key"] is True  # omitted = unchanged
    assert client.put("/api/admin/media-server", json={"tmdb_api_key": None}).json()["has_tmdb_key"] is True
    assert client.put("/api/admin/media-server", json={"tmdb_api_key": ""}).json()["has_tmdb_key"] is False
    with factory() as db:
        assert db.get(AppSettings, 1).tmdb_api_key is None


def test_env_key_and_public_url_are_reported(admin, monkeypatch) -> None:
    client, *_ = admin
    monkeypatch.setattr(settings, "tmdb_api_key", "env-key")
    monkeypatch.setattr(settings, "app_public_url", "https://vault.example.test/")
    body = client.get("/api/admin/media-server").json()
    assert body["has_tmdb_key"] is True and body["jellyfin_url"] == "https://vault.example.test"
    assert "env-key" not in str(body)


@pytest.mark.parametrize("payload", [
    {"hwaccel": "cuda"}, {"metadata_language": "english"}, {"transcode_cache_gb": 0},
    {"max_playback_sessions": 17}, {"jellyfin_server_key": "ab" * 32}, {"tmdb_api_key": "k" * 513},
    {"jellyfin_import_url": "ftp://jf.lan"}, {"jellyfin_import_url": "http://alice:pw@jf.lan:8096"},
    {"jellyfin_import_url": "http://jf.lan:8096/?api_key=x"}, {"jellyfin_import_url": "jf.lan:8096"},
    {"anime_folders": ["TV/Anime"]}, {"anime_folders": [".."]},
])
def test_invalid_updates_are_422_and_change_nothing(admin, payload) -> None:
    client, *_ = admin
    assert client.put("/api/admin/media-server", json=payload).status_code == 422
    assert client.get("/api/admin/media-server").json() == DEFAULTS


def test_jellyfin_import_address_is_saved_trimmed_and_cleared(admin) -> None:
    client, *_ = admin
    saved = client.put("/api/admin/media-server", json={"jellyfin_import_url": " http://192.168.1.20:8096/ "})
    assert saved.status_code == 200, saved.text
    assert saved.json()["jellyfin_import_url"] == "http://192.168.1.20:8096"
    assert client.put("/api/admin/media-server", json={}).json()["jellyfin_import_url"] == "http://192.168.1.20:8096"
    assert client.get("/api/admin/media-server").json()["jellyfin_import_url"] == "http://192.168.1.20:8096"
    assert client.put("/api/admin/media-server", json={"jellyfin_import_url": ""}).json()["jellyfin_import_url"] is None


def test_members_cannot_read_or_change_media_server_settings(admin) -> None:
    client, current, member, _ = admin
    current["user"] = member
    assert client.get("/api/admin/media-server").status_code == 403
    assert client.put("/api/admin/media-server", json={"jellyfin_enabled": True}).status_code == 403


def test_backups_null_the_tmdb_key_but_keep_the_server_identity() -> None:
    init_db()
    with db_module.SessionLocal.begin() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.tmdb_api_key, record.jellyfin_server_key = "tmdb-secret-147", "ab" * 32

    manifest = backups.create_backup()

    path = backups.backup_root() / f"{manifest['name']}.db"
    assert b"tmdb-secret-147" not in path.read_bytes()
    copy = sqlite3.connect(path)
    try:
        assert copy.execute("SELECT tmdb_api_key, jellyfin_server_key FROM app_settings").fetchall() == [(None, "ab" * 32)]
    finally:
        copy.close()


def test_restore_revokes_connected_apps_but_keeps_the_server_identity(tmp_path) -> None:
    # A device revoked (or simply captured) before a backup must not work again after a restore.
    init_db()
    with db_module.SessionLocal.begin() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.jellyfin_server_key = "cd" * 32
        db.add(DeviceToken(
            id="dt-1", user_id="u1", kind="jellyfin", scope="write",
            token_digest=session_digest("tok"), device_id="phone-1", device_name="Phone",
        ))

    name = backups.create_backup()["name"]
    target = tmp_path / "restored"
    restore(backups.backup_root() / f"{name}.db", target)

    copy = sqlite3.connect(target / settings.database_filename)
    try:
        assert copy.execute("SELECT count(*) FROM device_tokens").fetchone() == (0,)
        assert copy.execute("SELECT jellyfin_server_key FROM app_settings").fetchone() == ("cd" * 32,)
    finally:
        copy.close()


def test_anime_folders_are_saved_trimmed_and_deduped(admin) -> None:  # noqa: ANN001
    client, *_ = admin
    saved = client.put("/api/admin/media-server", json={"anime_folders": [" Anime ", "anime", "Donghua"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["anime_folders"] == ["Anime", "Donghua"]
    assert client.get("/api/admin/media-server").json()["anime_folders"] == ["Anime", "Donghua"]
    assert client.put("/api/admin/media-server", json={"anime_folders": []}).json()["anime_folders"] == []


# ---- Anime folders re-sort the library ------------------------------------------------


def test_only_admins_change_the_anime_folders(admin) -> None:  # noqa: ANN001
    client, current, member, _factory = admin
    owner = current["user"]
    current["user"] = member
    assert client.put("/api/admin/media-server", json={"anime_folders": ["Donghua"]}).status_code == 403
    current["user"] = owner
    assert client.get("/api/admin/media-server").json()["anime_folders"] == ["Anime"]


def test_a_changed_anime_folder_list_queues_one_re_sort(admin, monkeypatch) -> None:  # noqa: ANN001
    from app.services import categories

    client, *_ = admin
    runs: list[int] = []
    monkeypatch.setattr(categories, "recategorise", lambda: runs.append(1))
    for body in ({"anime_folders": ["Anime"]}, {"anime_folders": ["ANIME"]}, {"metadata_language": "de-DE"}):
        assert client.put("/api/admin/media-server", json=body).status_code == 200  # nothing that sorts differently
    assert runs == []
    assert client.put("/api/admin/media-server", json={"anime_folders": ["Anime", "Donghua"]}).status_code == 200
    assert client.put("/api/admin/media-server", json={"anime_folders": ["Donghua", "anime"]}).status_code == 200  # the same set
    assert runs == [1]
    assert client.put("/api/admin/media-server", json={"anime_folders": []}).json()["anime_folders"] == []
    assert runs == [1, 1]


def test_saving_new_anime_folders_reports_the_re_sort_until_it_ends(admin, monkeypatch) -> None:  # noqa: ANN001
    import threading

    from app.services import categories

    client, *_ = admin
    release = threading.Event()
    monkeypatch.setattr(categories, "_run_once", lambda: release.wait(10))
    try:
        saved = client.put("/api/admin/media-server", json={"anime_folders": ["Donghua"]})
        assert (saved.status_code, saved.json()["recategorising"]) == (200, True)
        assert client.get("/api/admin/media-server").json()["recategorising"] is True
    finally:
        release.set()
    categories.wait()
    assert client.get("/api/admin/media-server").json()["recategorising"] is False
