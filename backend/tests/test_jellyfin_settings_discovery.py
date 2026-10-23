"""Connected-app discovery and personal connection settings stay member-scoped."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import AppSettings, User
from app.security import hash_password
from app.services.member_suppressions import MemberSuppressionService
from title_support import ALICE, ALICE_TOKEN, BOB, jellyfin_household, mediabrowser


BASE = "/Lumina/Settings"


def call(client: TestClient, method: str, path: str, **kwargs):  # noqa: ANN201
    return client.request(method, BASE + path, headers=mediabrowser(ALICE_TOKEN), **kwargs)


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def test_interests_and_hidden_recommendations_are_member_scoped(native: TestClient) -> None:
    categories = call(native, "GET", "/interests")
    assert categories.status_code == 200
    assert categories.headers["cache-control"] == "no-store"
    key = categories.json()["categories"][0]["key"]
    saved = call(native, "PUT", "/interests", json={"keys": [key]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["selected_keys"] == [key]

    with db_module.SessionLocal.begin() as db:
        alice = db.get(User, ALICE)
        bob = db.get(User, BOB)
        assert alice is not None and bob is not None
        own = MemberSuppressionService(db).suppress_title(alice, title_id="own-title", name="Own hidden title")
        other = MemberSuppressionService(db).suppress_title(bob, title_id="other-title", name="Other hidden title")

    listed = call(native, "GET", "/suppressions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["titles"]] == [own.id]
    assert call(native, "DELETE", f"/suppressions/{other.id}").status_code == 204
    assert call(native, "DELETE", f"/suppressions/{own.id}").status_code == 204
    assert call(native, "GET", "/suppressions").json()["titles"] == []


def test_connections_require_a_fresh_proof_and_never_use_an_admin_history_route(native: TestClient, monkeypatch) -> None:
    from app.routers import jellyfin_settings_discovery as discovery

    # A valid credential body does not contact a provider until the connected app
    # has completed the one-use proof flow.
    blocked = call(native, "PUT", "/listening-export/listenbrainz", json={"token": "private-token"})
    assert blocked.status_code == 401

    with db_module.SessionLocal.begin() as db:
        user = db.get(User, ALICE)
        assert user is not None
        user.password_hash = hash_password("Correct horse battery staple 7!")
        app_settings = db.get(AppSettings, 1)
        assert app_settings is not None
        app_settings.jellyfin_import_url = "https://configured.invalid"

    calls = []

    def fake_listenbrainz(body, user, db):  # noqa: ANN001, ANN202
        calls.append((body.token, user.id))
        return {"provider": "listenbrainz", "enabled": True, "credentials_set": True}

    monkeypatch.setattr(discovery.listening_export, "listenbrainz", fake_listenbrainz)
    proof = call(native, "POST", "/reauth", json={"password": "Correct horse battery staple 7!"}).json()["proof"]
    connected = native.put(
        BASE + "/listening-export/listenbrainz",
        headers={**mediabrowser(ALICE_TOKEN), "X-Lumina-Reauth": proof},
        json={"token": "private-token"},
    )
    assert connected.status_code == 200, connected.text
    assert calls == [("private-token", ALICE)]
    assert "private-token" not in connected.text

    assert call(native, "GET", "/jellyfin-import").json() == {"server": "https://configured.invalid"}
    assert call(native, "GET", "/jellyfin-import/household").status_code == 404
