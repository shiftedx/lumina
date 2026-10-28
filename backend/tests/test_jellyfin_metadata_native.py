"""Native metadata authorization, history/conflict, and identify workflows."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.main import app
from app.models import AppSettings, DeviceToken, MediaTitle, MemberAccess, TitleEdit, User
from app.services import tmdb
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, MOVIE, jellyfin_household, mediabrowser

BASE = "/Lumina/Metadata"


@pytest.fixture
def native(tmp_path):
    from app.services.rate_limit import rate_limiter
    rate_limiter.clear()
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()
    rate_limiter.clear()


def call(client, method, path, *, token=ALICE_TOKEN, **kwargs):
    return client.request(method, BASE + path, headers=mediabrowser(token), **kwargs)


def test_delegated_edit_uses_history_optimistic_conflicts_and_visibility(native):
    assert call(native, "GET", "/capabilities").json() == {"can_edit_details": False, "can_identify": False}
    assert call(native, "GET", f"/titles/{MOVIE}").status_code == 403
    with db_module.SessionLocal.begin() as db:
        db.get(AppSettings, 1).members_edit_metadata = True
    doc = call(native, "GET", f"/titles/{MOVIE}")
    assert doc.status_code == 200 and doc.headers["cache-control"] == "no-store"
    assert doc.json()["can_identify"] is False
    base = doc.json()["fields"]["name"]["value"]
    body = {"edits": [{"title_id": MOVIE, "changes": {"name": {"value": "Corrected title", "base": base}}}]}
    result = call(native, "POST", "/edits", json=body)
    assert result.status_code == 200 and not result.json()["conflicts"]
    assert result.headers["cache-control"] == "no-store"
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, MOVIE)
        assert title.name == "Corrected title" and title.field_sources["name"] == "user"
        assert db.scalar(select(TitleEdit).where(TitleEdit.title_id == MOVIE)) is not None
    conflict = call(native, "POST", "/edits", json=body)
    assert conflict.status_code == 409 and conflict.json()["conflicts"][0]["fields"] == ["name"]
    with db_module.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=BOB, sections=["shows"]))
    assert call(native, "GET", f"/titles/{MOVIE}", token=BOB_TOKEN).status_code == 404
    assert call(native, "POST", "/edits", token=BOB_TOKEN, json=body).status_code == 404
    assert call(native, "GET", f"/titles/{MOVIE}/matches").status_code == 403


def test_owner_match_search_and_apply_preserve_edits_and_lock(native, monkeypatch):
    class Catalog:
        def search(self, kind, name, year):
            assert kind == "movie"
            return [{"tmdb_id": 603, "name": "Correct match", "original_name": "Correct match", "year": 1999,
                     "overview": "Synthetic candidate.", "poster_path": None}]
    monkeypatch.setattr(tmdb, "client_for", lambda settings: Catalog())
    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"
        title = db.get(MediaTitle, MOVIE)
        title.metadata_json = {**(title.metadata_json or {}), "overview": "My own description"}
        title.field_sources = {**(title.field_sources or {}), "overview": "user"}
    assert call(native, "GET", "/capabilities").json() == {"can_edit_details": True, "can_identify": True}
    candidates = call(native, "GET", f"/titles/{MOVIE}/matches", params={"q": "Correct match", "year": 1999})
    assert candidates.status_code == 200 and candidates.json()[0]["tmdb_id"] == 603
    result = call(native, "POST", f"/titles/{MOVIE}/match", json={"tmdb_id": 603})
    assert result.status_code == 202
    with db_module.SessionLocal.begin() as db:
        title = db.get(MediaTitle, MOVIE)
        assert title.provider_ids["Tmdb"] == "603"
        assert title.metadata_json["overview"] == "My own description"
        title.locked = True
    assert call(native, "POST", f"/titles/{MOVIE}/match", json={"tmdb_id": 604}).status_code == 409


def test_live_role_scope_two_factor_and_revocation_never_become_browser_authority(native):
    with db_module.SessionLocal.begin() as db:
        db.get(User, ALICE).role = "admin"
    assert call(native, "GET", "/capabilities").json()["can_identify"] is True
    assert native.get(f"/api/titles/{MOVIE}/metadata", headers=mediabrowser(ALICE_TOKEN)).status_code in (401, 403)
    with db_module.SessionLocal.begin() as db:
        parent = DeviceToken(id="native-parent", user_id=ALICE, kind="app_password", scope="write",
                             token_digest="native-parent-digest", device_id="native-parent-device", device_name="Media app")
        db.add(parent)
        db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE, DeviceToken.kind == "jellyfin")).app_password_id = parent.id
    assert call(native, "GET", "/capabilities").json() == {"can_edit_details": False, "can_identify": False}
    assert call(native, "POST", f"/titles/{MOVIE}/match", json={"tmdb_id": 603}).status_code == 403
    with db_module.SessionLocal.begin() as db:
        db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE, DeviceToken.kind == "jellyfin")).app_password_id = None
        db.delete(db.get(DeviceToken, "native-parent"))
    with db_module.SessionLocal.begin() as db:
        db.get(AppSettings, 1).require_owner_two_factor = True
    assert call(native, "GET", "/capabilities").json() == {"can_edit_details": False, "can_identify": False}
    with db_module.SessionLocal.begin() as db:
        db.get(AppSettings, 1).require_owner_two_factor = False
        db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE)).scope = "read"
    assert call(native, "GET", "/capabilities").json() == {"can_edit_details": False, "can_identify": False}
    assert call(native, "POST", f"/titles/{MOVIE}/match", json={"tmdb_id": 603}).status_code == 403
    with db_module.SessionLocal.begin() as db:
        db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE)).scope = "write"
        db.get(User, ALICE).role = "member"
    assert call(native, "POST", "/edits", json={"edits": [{"title_id": MOVIE, "changes": {}}]}).status_code == 403
    with db_module.SessionLocal.begin() as db:
        db.delete(db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE)))
    assert call(native, "GET", "/capabilities").status_code == 401
