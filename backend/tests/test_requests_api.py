"""Requests API: member scope, admin-only endpoints, write-only secrets, servers, settings, policies, notifications."""
from __future__ import annotations

import pytest

from arr_fake import API_KEY, FakeArr, FakeTmdb, seed_anime
from support import make_user, seed_app_settings

from app.models import ArrServer, User
from app.services import tmdb

ADMIN_CALLS = [
    ("get", "/api/requests?scope=all", None),
    ("post", "/api/requests/x/approve", None),
    ("post", "/api/requests/x/decline", {}),
    ("post", "/api/requests/x/retry", None),
    ("get", "/api/admin/requests/servers", None),
    ("post", "/api/admin/requests/servers", {"kind": "radarr", "name": "R", "base_url": "http://r.local", "api_key": "k"}),
    ("put", "/api/admin/requests/servers/x", {"name": "R"}),
    ("delete", "/api/admin/requests/servers/x", None),
    ("post", "/api/admin/requests/servers/test", {"kind": "radarr", "base_url": "http://r.local"}),
    ("post", "/api/admin/requests/servers/x/anime-language-profiles", None),
    ("get", "/api/admin/requests/settings", None),
    ("put", "/api/admin/requests/settings", {"requests_enabled": True}),
    ("post", "/api/admin/requests/smtp/test", {"to": "a@example.com"}),
    ("get", "/api/admin/requests/policies", None),
    ("put", "/api/admin/requests/policies", {"user_id": None, "policies": []}),
    ("delete", "/api/admin/requests/policies/alice", None),
]


@pytest.fixture
def clients(db_factory, api_client, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(tmdb, "client_for", lambda record: FakeTmdb())
    from app.services.requests import catalog

    catalog.clear_cache()
    seed_anime(21, {"tmdb_id": 37854, "tvdb_id": 81797})
    seed_anime(1, {"tmdb_id": 37854, "tvdb_id": 81797})
    with db_factory() as session:
        session.add_all([make_user("admin", role="admin"), make_user("alice"), make_user("bob")])
        seed_app_settings(session, requests_enabled=True)
    current = {"id": "alice"}

    def who() -> User:
        with db_factory() as session:
            return session.get(User, current["id"])

    client = api_client(user=who, base_url="http://localhost")

    def as_(user_id: str):  # noqa: ANN202
        current["id"] = user_id
        return client

    return as_


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_CALLS)
def test_members_cannot_call_admin_endpoints(clients, method, path, body) -> None:  # noqa: ANN001
    kwargs = {"json": body} if body is not None else {}
    assert getattr(clients("alice"), method)(path, **kwargs).status_code == 403


def test_members_see_only_their_own_requests(clients) -> None:  # noqa: ANN001
    created = clients("alice").post("/api/requests", json={"kind": "movie", "tmdb_id": 603})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["key"], body["status"], body["requested_by"], body["seasons"], body["language"]) == (
        "movie:603", "pending", {"id": "alice", "name": "Alice"}, None, None)
    assert clients("bob").get("/api/requests").json()["items"] == []
    mine = clients("alice").get("/api/requests?scope=mine").json()
    assert [i["id"] for i in mine["items"]] == [body["id"]] and mine["counts"]["pending"] == 1 and mine["total_pages"] == 1
    assert clients("admin").get("/api/requests?scope=all&status=pending").json()["items"][0]["id"] == body["id"]
    # Another member asking follows instead (200), and cannot delete or even see someone else's request.
    followed = clients("bob").post("/api/requests", json={"kind": "movie", "tmdb_id": 603})
    assert followed.status_code == 200 and followed.json()["followers"] == [{"id": "bob", "name": "Bob"}]
    assert clients("bob").delete(f"/api/requests/{body['id']}").status_code == 404
    assert clients("alice").delete(f"/api/requests/{body['id']}").status_code == 204


def test_admin_decides_and_errors_use_the_envelope(clients) -> None:  # noqa: ANN001
    req = clients("alice").post("/api/requests", json={"kind": "movie", "tmdb_id": 603}).json()
    declined = clients("admin").post(f"/api/requests/{req['id']}/decline", json={"reason": "No"})
    assert declined.json()["status"] == "declined" and declined.json()["decided_by"]["id"] == "admin"
    assert clients("admin").post(f"/api/requests/{req['id']}/approve").json() == {"detail": "not_pending"}
    assert clients("alice").post("/api/requests", json={"kind": "anime", "anilist_id": 1, "tmdb_id": 37854}).json() == {"detail": "language_required"}
    assert clients("alice").post("/api/requests", json={"kind": "movie", "tmdb_id": 603, "extra": 1}).status_code == 422


def test_requests_disabled_refuses_new_requests(clients) -> None:  # noqa: ANN001
    clients("admin").put("/api/admin/requests/settings", json={"requests_enabled": False})
    response = clients("alice").post("/api/requests", json={"kind": "movie", "tmdb_id": 603})
    assert (response.status_code, response.json()) == (403, {"detail": "requests_disabled"})


def test_quota_endpoint(clients) -> None:  # noqa: ANN001
    clients("alice").post("/api/requests", json={"kind": "movie", "tmdb_id": 603})
    quotas = {q["kind"]: q for q in clients("alice").get("/api/requests/quota").json()["quotas"]}
    assert quotas["movie"] == {"kind": "movie", "can_request": True, "auto_approve": False, "limit": 10, "days": 7, "used": 1, "remaining": 9}
    assert set(quotas) == {"movie", "show", "anime"}


def test_server_api_key_is_write_only_and_test_uses_the_stored_key(clients) -> None:  # noqa: ANN001
    fake = FakeArr("sonarr")
    try:
        admin = clients("admin")
        assert admin.post("/api/admin/requests/servers", json={"kind": "sonarr", "name": "S", "base_url": "ftp://x", "api_key": "k"}).status_code == 422
        created = admin.post("/api/admin/requests/servers", json={
            "kind": "sonarr", "name": "Sonarr", "base_url": fake.url, "api_key": API_KEY,
            "path_mappings": [{"remote": "/tv", "local": "/media/tv"}]})
        assert created.status_code == 201
        server = created.json()
        assert server["api_key_set"] is True and "api_key" not in server and API_KEY not in created.text
        partial = admin.put(f"/api/admin/requests/servers/{server['id']}", json={"dub_profile_id": 5, "sub_profile_id": 6}).json()
        assert (partial["dub_profile_id"], partial["api_key_set"], partial["name"]) == (5, True, "Sonarr")
        listed = admin.get("/api/admin/requests/servers")
        assert [s["id"] for s in listed.json()["servers"]] == [server["id"]] and API_KEY not in listed.text
        tested = admin.post("/api/admin/requests/servers/test", json={"kind": "sonarr", "base_url": fake.url, "id": server["id"]}).json()
        assert tested["ok"] is True and tested["version"].startswith("4.")
        assert {"path": "/tv/Anime/", "free_space": 10**12} in tested["root_folders"]
        assert {"id": 4, "name": "HD-1080p"} in tested["quality_profiles"]
        bad = admin.post("/api/admin/requests/servers/test", json={"kind": "sonarr", "base_url": fake.url, "api_key": "wrong-key"}).json()
        assert bad == {"ok": False, "version": None, "root_folders": [], "quality_profiles": [], "error": "Sonarr endpoint returned HTTP 401"}
        made = admin.post(f"/api/admin/requests/servers/{server['id']}/anime-language-profiles").json()
        assert made["dub_profile_id"] and made["sub_profile_id"] and made["dub_profile_id"] != 5
        assert admin.delete(f"/api/admin/requests/servers/{server['id']}").status_code == 204
    finally:
        fake.close()


def test_smtp_password_is_write_only(clients, db_factory) -> None:  # noqa: ANN001
    admin = clients("admin")
    saved = admin.put("/api/admin/requests/settings", json={"requests_enabled": True, "smtp": {
        "host": "mail.local", "port": 587, "security": "starttls", "username": "lumina", "from": "lumina@example.com", "password": "hunter2"}})
    assert saved.json() == {"requests_enabled": True, "smtp": {
        "host": "mail.local", "port": 587, "security": "starttls", "username": "lumina", "from": "lumina@example.com", "password_set": True}}
    assert "hunter2" not in saved.text
    admin.put("/api/admin/requests/settings", json={"smtp": {"host": "mail2.local"}})  # omitted password is kept
    assert admin.get("/api/admin/requests/settings").json()["smtp"]["password_set"] is True
    admin.put("/api/admin/requests/settings", json={"smtp": {"password": ""}})
    assert admin.get("/api/admin/requests/settings").json()["smtp"]["password_set"] is False


def test_policies_round_trip_and_reset(clients) -> None:  # noqa: ANN001
    admin = clients("admin")
    policies = admin.get("/api/admin/requests/policies").json()
    assert [p["kind"] for p in policies["defaults"]] == ["movie", "show", "anime"]
    assert policies["defaults"][0] == {"kind": "movie", "can_request": True, "auto_approve": False, "quota_count": 10, "quota_days": 7}
    override = {"kind": "anime", "can_request": False, "auto_approve": False, "quota_count": None, "quota_days": None}
    updated = admin.put("/api/admin/requests/policies", json={"user_id": "alice", "policies": [override]}).json()
    [alice] = [m for m in updated["members"] if m["user"]["id"] == "alice"]
    assert alice == {"user": {"id": "alice", "name": "Alice", "role": "viewer"}, "overrides": [override]}
    refused = clients("alice").post("/api/requests", json={"kind": "anime", "anilist_id": 21, "tmdb_id": 37854, "language": "sub"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "not_allowed"})
    assert clients("admin").delete("/api/admin/requests/policies/alice").status_code == 204
    assert [m["overrides"] for m in clients("admin").get("/api/admin/requests/policies").json()["members"] if m["user"]["id"] == "alice"] == [[]]


def test_member_notifications(clients) -> None:  # noqa: ANN001
    alice = clients("alice")
    assert alice.get("/api/requests/notifications").json() == {"email": None, "enabled": True}
    assert alice.put("/api/requests/notifications", json={"email": "not-an-email", "enabled": True}).status_code == 422
    assert alice.put("/api/requests/notifications", json={"email": "alice@example.com", "enabled": False}).json() == {
        "email": "alice@example.com", "enabled": False}
    assert clients("bob").get("/api/requests/notifications").json() == {"email": None, "enabled": True}


def test_admin_requests_are_approved_and_dispatched(clients, db_factory) -> None:  # noqa: ANN001
    fake = FakeArr("radarr")
    fake.add_lookup("tmdb:603", "The Matrix", tmdbId=603)
    try:
        with db_factory() as session:
            session.add(ArrServer(id="r", kind="radarr", name="Radarr", base_url=fake.url, api_key=API_KEY, root_folder="/movies/",
                                  quality_profile_id=4, path_mappings=[], enabled=True))
            session.commit()
        body = clients("admin").post("/api/requests", json={"kind": "movie", "tmdb_id": 603}).json()
        assert body["status"] == "approved" and len(fake.library) == 1
    finally:
        fake.close()


def test_the_stored_key_never_follows_a_changed_address(clients, db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add(ArrServer(id="s", kind="sonarr", name="Sonarr", base_url="http://127.0.0.1:1", api_key=API_KEY, path_mappings=[], enabled=True))
        session.commit()
    admin = clients("admin")
    moved = admin.post("/api/admin/requests/servers/test", json={"kind": "sonarr", "base_url": "http://evil.example", "id": "s"})
    assert (moved.status_code, moved.json()) == (400, {"detail": "api_key_required"})
    kind = admin.post("/api/admin/requests/servers/test", json={"kind": "radarr", "base_url": "http://127.0.0.1:1", "id": "s"})
    assert kind.status_code == 400
    assert admin.post("/api/admin/requests/servers/test", json={"kind": "sonarr", "base_url": "http://127.0.0.1:1"}).status_code == 400
    updated = admin.put("/api/admin/requests/servers/s", json={"base_url": "http://evil.example"})
    assert (updated.status_code, updated.json()) == (400, {"detail": "api_key_required"})
    assert admin.put("/api/admin/requests/servers/s", json={"base_url": "http://new.local", "api_key": "new"}).json()["base_url"] == "http://new.local"
