"""Admin-only model routes, the additive AI config contract, admin-only model events."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.events import EventBus
from app.main import app
from app.models import AppSettings, SearchEmbedding
from app.routers import admin_models
from app.security import get_current_user
from app.services import embeddings, model_catalog, model_downloads, model_supervisor
from app.services.yt_dlp_service import YtDlpService
from discovery_support import add_movie
from model_support import FakeModelHost, default_pair, file_entry, fresh_models, install, model_entry, stub_runtime, use_catalog, wait_until  # noqa: F401
from support import make_user

ADMIN, MEMBER = make_user("admin", role="admin"), make_user("member")


@pytest.fixture
def host():
    fake = FakeModelHost()
    yield fake
    fake.close()


@pytest.fixture
def api(monkeypatch, tmp_path, host, fresh_models):  # noqa: ANN001, F811
    catalog = use_catalog(monkeypatch, tmp_path, [*default_pair(host.base), model_entry("other-search", "search", [file_entry(host.base, "other.gguf")])])
    for model in catalog.models:
        for file in model.files:
            host.serve({"name": file.name, "url": file.url})
    db_module.init_db()
    embeddings.reset_caches()
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings()
        db.add_all([make_user("admin", role="admin"), make_user("member")])
    current = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        yield TestClient(app, base_url="http://localhost"), current, catalog
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        wait_until(lambda: not embeddings._draining.locked())
        embeddings.reset_caches()


def _ok(response):  # noqa: ANN001, ANN202
    assert response.status_code == 200, response.text
    return response.json()


def _row(client: TestClient, model_id: str) -> dict:
    return next(row for row in _ok(client.get("/api/admin/models"))["models"] if row["id"] == model_id)


def test_members_cannot_list_or_touch_models(api, host) -> None:  # noqa: ANN001
    client, current, _catalog = api
    current["user"] = MEMBER
    for method, path in (
        ("get", "/api/admin/models"), ("post", "/api/admin/models/test-search/download"),
        ("post", "/api/admin/models/test-search/cancel"), ("post", "/api/admin/models/test-search/activate"),
        ("delete", "/api/admin/models/test-search"),
    ):
        assert getattr(client, method)(path).status_code == 403, path
    assert host.requests == []


@pytest.mark.parametrize("model_id", ["nope", "test-search.part", "TEST-SEARCH", "Bad_ID", "a..b", "x.part", "a" * 65, "local:test-search"])
def test_unknown_or_hostile_ids_are_404(api, host, model_id) -> None:  # noqa: ANN001
    client, _current, _catalog = api
    for method in ("post", "delete"):
        suffix = "/download" if method == "post" else ""
        assert getattr(client, method)(f"/api/admin/models/{model_id}{suffix}").status_code == 404
    for action in ("cancel", "activate"):
        assert client.post(f"/api/admin/models/{model_id}/{action}").status_code == 404
    assert host.requests == [] and not model_catalog.models_root().exists()


def test_the_catalog_with_state_then_a_download_to_ready(api) -> None:  # noqa: ANN001
    client, _current, catalog = api
    assert _row(client, "test-search") == {
        "id": "test-search", "role": "search", "name": "Test-Search", "description": "test-search (test model)",
        "licence": "MIT", "size_bytes": catalog.default_for("search").size_bytes, "ram_bytes": 1 << 20, "default": True,
        "active": True, "state": "absent", "bytes_done": None, "bytes_total": None, "reason": None, "running": False,
        "features": ["semantic_search"],
    }
    assert _row(client, "other-search")["active"] is False
    assert _row(client, "test-speech")["features"] == ["subtitles_from_speech", "sync"]
    assert _ok(client.post("/api/admin/models/test-speech/download"))["state"] in {"downloading", "verifying", "ready"}
    wait_until(lambda: _row(client, "test-speech")["state"] == "ready")
    assert _ok(client.post("/api/admin/models/other-search/cancel"))["state"] == "absent"


def test_a_download_without_space_says_how_much_is_needed(api, host, monkeypatch) -> None:  # noqa: ANN001
    client, _current, _catalog = api
    monkeypatch.setattr(model_downloads, "disk_free", lambda path: 0)
    row = _ok(client.post("/api/admin/models/test-search/download"))
    assert row["state"] == "failed" and row["reason"].startswith("Not enough free space: needs ")
    assert host.requests == []


def test_retry_clears_a_crash_failure(api, monkeypatch) -> None:  # noqa: ANN001
    client, _current, catalog = api
    install(catalog.default_for("search"))
    failed = {"test-search"}
    monkeypatch.setattr(model_supervisor.supervisor, "is_failed", lambda model_id: model_id in failed)
    monkeypatch.setattr(model_supervisor.supervisor, "failure", lambda model_id: "The search model stopped unexpectedly twice; Retry" if model_id in failed else None)
    monkeypatch.setattr(model_supervisor.supervisor, "retry", lambda model_id: failed.discard(model_id))
    assert (_row(client, "test-search")["state"], _row(client, "test-search")["reason"]) == ("failed", "The search model stopped unexpectedly twice; Retry")
    assert _ok(client.post("/api/admin/models/test-search/download"))["state"] == "ready"


def test_activate_chooses_the_model_for_its_role(api) -> None:  # noqa: ANN001
    client, _current, _catalog = api
    assert _ok(client.post("/api/admin/models/other-search/activate"))["active"] is True
    assert _row(client, "test-search")["active"] is False and _row(client, "test-speech")["active"] is True
    with db_module.session_scope() as db:
        assert db.get(AppSettings, 1).local_search_model == "other-search"


def test_removing_the_active_model_turns_off_what_needs_it(api) -> None:  # noqa: ANN001
    client, _current, catalog = api
    install(catalog.default_for("speech"))
    body = _ok(client.delete("/api/admin/models/test-speech"))
    assert body["disabled_features"] == ["subtitles_from_speech"] and body["model"]["state"] == "absent"
    with db_module.session_scope() as db:
        assert db.get(AppSettings, 1).ai_features_disabled == ["subtitles_from_speech"]


def test_removing_keeps_features_that_something_else_serves(api) -> None:  # noqa: ANN001
    client, _current, catalog = api
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.asr_base_url, record.asr_model = "http://127.0.0.1:9/v1", "ext-asr"
    install(catalog.default_for("speech"))
    assert _ok(client.delete("/api/admin/models/test-speech"))["disabled_features"] == []
    install(catalog.get("other-search"))  # installed but not active
    assert _ok(client.delete("/api/admin/models/other-search"))["disabled_features"] == []


def test_ai_config_reports_readiness_and_threads(api, monkeypatch) -> None:  # noqa: ANN001
    client, _current, catalog = api
    monkeypatch.setattr(model_supervisor, "cpu_count", lambda: 4)
    body = _ok(client.get("/api/admin/ai/config"))
    assert body["features"]["semantic_search"] == {"requires": "search_model", "ready": False, "reason": "The search model is not installed"}
    assert body["features"]["subtitles_from_speech"]["requires"] == "speech_model"
    assert body["features"]["recap"] == {"requires": "assistant", "ready": False, "reason": "Needs your assistant server"}
    assert body["features"]["sync"] == {"requires": None, "ready": True, "reason": None}
    assert (body["model_threads"], body["model_threads_auto"]) == (None, 3)
    install(catalog.default_for("search"))
    assert _ok(client.get("/api/admin/ai/config"))["features"]["semantic_search"]["ready"] is True
    assert _ok(client.put("/api/admin/ai/config", json={"model_threads": 2}))["model_threads"] == 2
    assert _ok(client.put("/api/admin/ai/config", json={"model_threads": 0}))["model_threads"] is None
    assert client.put("/api/admin/ai/config", json={"model_threads": 13}).status_code == 422


def test_readiness_ignores_the_off_switch(api) -> None:  # noqa: ANN001
    """Plan 03's enable dialog reads features[key] before Save, while the feature is still switched off."""
    client, _current, catalog = api
    every_key = ["semantic_search", "subtitles_from_speech", "sync", "translate", "recap",
                 "smart_collection_builder", "match_tie_breaker", "mute_strong_language", "episode_summaries", "key_scenes"]
    off = _ok(client.put("/api/admin/ai/config", json={"ai_features_disabled": every_key}))
    assert off["ai_features_disabled"] == every_key and set(off["features"]) == set(every_key)
    assert off["features"]["semantic_search"] == {"requires": "search_model", "ready": False, "reason": "The search model is not installed"}
    assert off["features"]["recap"] == {"requires": "assistant", "ready": False, "reason": "Needs your assistant server"}
    install(catalog.default_for("search"))
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "chat"
    body = _ok(client.get("/api/admin/ai/config"))
    assert body["ai_features_disabled"] == every_key  # still all off
    assert body["features"]["semantic_search"] == {"requires": "search_model", "ready": True, "reason": None}
    assert body["features"]["recap"] == {"requires": "assistant", "ready": True, "reason": None}
    assert body["features"]["subtitles_from_speech"] == {"requires": "speech_model", "ready": False, "reason": "The speech model is not installed"}


def test_model_events_reach_admins_only_and_carry_no_secret(api, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    _client, _current, catalog = api
    stub_runtime(monkeypatch, tmp_path)
    search = catalog.default_for("search")
    install(search)
    bus = EventBus()
    admin_stream, member_stream = bus.reserve("admin"), bus.reserve("member")
    endpoint = model_supervisor.supervisor.acquire(search, threads=1, wait=True)
    try:
        admin_models.publish(bus, "test-search")
    finally:
        model_supervisor.supervisor.release(search.id, endpoint)
    message = admin_stream.queue.get_nowait()
    assert message["type"] == "model_state" and message["payload"]["model"]["running"] is True
    assert member_stream.queue.empty()
    text = json.dumps(message)
    port = endpoint.base_url.split(":")[2].split("/")[0]
    assert endpoint.secret not in text and f":{port}" not in text


def test_model_views_never_expose_the_server_endpoint(api, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    client, _current, catalog = api
    stub_runtime(monkeypatch, tmp_path)
    search = catalog.default_for("search")
    install(search)
    endpoint = model_supervisor.supervisor.acquire(search, threads=1, wait=True)
    try:
        response = client.get("/api/admin/models")
    finally:
        model_supervisor.supervisor.release(search.id, endpoint)
    port = endpoint.base_url.split(":")[2].split("/")[0]
    assert _row(client, "test-search")["running"] is True
    assert endpoint.secret not in response.text and f":{port}" not in response.text and "127.0.0.1" not in response.text


def test_a_downloaded_search_model_indexes_without_further_action(api, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    client, _current, _catalog = api
    stub_runtime(monkeypatch, tmp_path)
    with db_module.session_scope() as db:
        add_movie(db, "night", "Night of the Living", overview="The undead rise at dusk")
    admin_models.wire(EventBus())
    _ok(client.post("/api/admin/models/test-search/download"))

    def indexed() -> bool:
        with db_module.session_scope() as db:
            return db.query(SearchEmbedding).filter_by(model_id="local:test-search").count() == 1

    wait_until(indexed)


def test_retrying_a_stepped_aside_search_model_tops_up_its_index(api, monkeypatch) -> None:  # noqa: ANN001
    """Its kept index lacks items imported while it was crash-failed: search reads another index until the backfill tops it up."""
    client, _current, catalog = api
    install(catalog.default_for("search"))
    monkeypatch.setitem(model_supervisor.supervisor._failed, "test-search", "The search model stopped unexpectedly twice; Retry")
    embeddings._complete.add("local:test-search")
    backfills: list[str] = []
    monkeypatch.setattr(embeddings, "start_backfill", lambda: backfills.append("start"))
    admin_models.wire(EventBus())
    assert _ok(client.post("/api/admin/models/test-search/download"))["state"] == "ready"
    assert "local:test-search" not in embeddings._complete and backfills
