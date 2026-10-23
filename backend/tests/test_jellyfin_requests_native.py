"""Connected-app Requests stay member-scoped and never borrow browser /api credentials."""
from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import AppSettings, DeviceToken
from app.security import session_digest
from app.services import tmdb
from app.services.artwork import RemoteArtworkResponse
from app.services.network_policy import PublicSourcePolicy
from app.services.requests import anilist, catalog
from title_support import ALICE, ALICE_TOKEN, BOB_TOKEN, jellyfin_household, mediabrowser
from test_artwork import resolver_for
from test_requests_catalog import Fake, KEY, PUBLIC

BASE = "/Lumina/Requests"


def call(client, method, path, *, token=ALICE_TOKEN, **kwargs):  # noqa: ANN001, ANN201
    return client.request(method, BASE + path, headers=mediabrowser(token) if token else {}, **kwargs)


@pytest.fixture
def native(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    jellyfin_household(Path(tmp_path) / "media")
    upstream = Fake()
    policy = PublicSourcePolicy(resolver=resolver_for({"api.themoviedb.org": PUBLIC, "graphql.anilist.co": PUBLIC}))
    for module in (tmdb, anilist):
        monkeypatch.setattr(module, "policy", policy)
        monkeypatch.setattr(module, "transport", upstream)
        monkeypatch.setattr(module, "LIMITER", tmdb.RateLimiter(1_000_000))
    monkeypatch.setattr(settings, "tmdb_api_key", KEY)
    catalog.clear_cache()
    with db_module.SessionLocal.begin() as db:
        db.get(AppSettings, 1).requests_enabled = True
    client = TestClient(app, base_url="http://localhost")
    yield client, upstream
    client.close()
    catalog.clear_cache()


def test_catalog_quota_and_member_request_use_connected_app_auth(native):  # noqa: ANN001
    client, _upstream = native
    home = call(client, "GET", "/catalog/home")
    assert home.status_code == 200
    assert home.json()["hero"][0]["key"] == "movie:603"
    assert call(client, "GET", "/quota").json()["quotas"][0]["kind"] == "movie"
    created = call(client, "POST", "/", json={"kind": "show", "tmdb_id": 1399})
    assert created.status_code == 201
    assert created.json()["requested_by"]["id"] == ALICE
    mine = call(client, "GET", "/").json()
    assert [row["id"] for row in mine["items"]] == [created.json()["id"]]
    assert call(client, "GET", "/", token=BOB_TOKEN).json()["items"] == []
    # A connected-app credential never opens the browser API boundary.
    assert client.get("/api/requests/catalog/home", headers=mediabrowser(ALICE_TOKEN)).status_code == 401


def test_catalog_errors_and_revocation_keep_the_native_json_contract(native):  # noqa: ANN001
    client, _upstream = native
    unavailable = call(client, "GET", "/catalog/list/movie", params={"section": "not-a-section"})
    assert (unavailable.status_code, unavailable.json()) == (422, {"detail": "invalid_section"})
    # Trailer entries are catalog metadata for an external link; this namespace does not relay provider media.
    assert call(client, "POST", "/catalog/title/movie/603/trailers/official111/playback").status_code == 404
    assert call(client, "GET", "/catalog/home", token=None).status_code == 401
    with db_module.SessionLocal.begin() as db:
        db.delete(db.scalar(select(DeviceToken).where(DeviceToken.token_digest == session_digest(ALICE_TOKEN))))
    assert call(client, "GET", "/catalog/home").status_code == 401




def test_native_catalog_hero_framing_shares_analysis_and_rejects_unissued_sources(native, monkeypatch):  # noqa: ANN001
    from app.routers import hero_framing

    client, _ = native
    item = call(client, "GET", "/catalog/home").json()["hero"][0]
    source = item["backdrop_url"]
    keys = []
    monkeypatch.setattr(hero_framing, "_result", lambda key, loader: keys.append(key) or {
        "state": "ready", "width": 1920, "height": 1080,
        "regions": [{"kind": "face", "x": .8, "y": .1, "width": .1, "height": .2, "weight": .9}],
    })
    framed = call(client, "GET", "/catalog/framing", params={"src": source})
    assert framed.status_code == 200 and framed.json()["state"] == "ready"
    assert len(keys) == 1
    for rejected in [item["poster_url"], "https://127.0.0.1/private.jpg", "https://image.tmdb.org/t/p/w1280/unissued.jpg"]:
        assert call(client, "GET", "/catalog/framing", params={"src": rejected}).json() == {"state": "unavailable"}
    assert len(keys) == 1
    assert call(client, "GET", "/catalog/framing", token=None, params={"src": source}).status_code == 401
