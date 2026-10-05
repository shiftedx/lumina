import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import AsrJob, DownloadJob, LibraryItem, Summary, User
from app.security import get_current_user
from app.services import local_asr, summaries
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService
from test_v1_ai_config import MODEL, FakeAi

ADMIN = User(id="admin", username="admin", display_name="Admin", role="admin", is_active=True)
MEMBER = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
T0 = datetime(2026, 1, 1)


@pytest.fixture
def env():
    db_module.init_db()
    with db_module.session_scope() as db:
        db.add_all([User(**{k: getattr(u, k) for k in ("id", "username", "display_name", "role", "is_active")}) for u in (ADMIN, MEMBER)])
        db.add_all([
            LibraryItem(id="priv", user_id="member", visibility="private", title="Member secret", metadata_json={}, status="available"),
            LibraryItem(id="shared", user_id="member", visibility="shared", title="Shared talk", metadata_json={}, status="available"),
            DownloadJob(id="d-failed", user_id="member", source_url="https://example.com/secret", status="failed", error="HTTP 403",
                        preview_snapshot={"title": "Member download", "extractor": "youtube"}, attempts=[{"status": "failed"}], created_at=T0),
            DownloadJob(id="d-queued", user_id="member", source_url="https://example.com/q", status="queued", created_at=T0 + timedelta(seconds=1)),
            DownloadJob(id="d-mine", user_id="admin", source_url="https://example.com/m", status="completed",
                        preview_snapshot={"title": "Admin download"}, created_at=T0 + timedelta(seconds=2)),
        ])
    current = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        yield TestClient(app, base_url="http://localhost"), current
    finally:
        app.dependency_overrides.clear()


def test_admin_ai_config_no_secret_echo(env) -> None:
    http, current = env
    saved = http.put("/api/admin/ai/config", json={"base_url": "http://10.0.0.5:8080/v1", "model": MODEL, "api_key": "rotate-me-1"})
    assert saved.json()["has_api_key"] is True and "rotate-me-1" not in saved.text
    rotated = http.put("/api/admin/ai/config", json={"api_key": "rotate-me-2"})
    assert rotated.json()["has_api_key"] is True and "rotate-me" not in rotated.text + http.get("/api/admin/ai/config").text
    assert http.put("/api/admin/ai/config", json={"api_key": ""}).json()["has_api_key"] is False

    # Members see only what they may request: no endpoint, model or key.
    current["user"] = MEMBER
    caps = http.get("/api/enrichment")
    assert caps.json() == {"ai_summaries": True, "asr": False, "disabled_features": []}
    assert "10.0.0.5" not in caps.text and MODEL not in caps.text
    assert http.get("/api/admin/tasks").status_code == 403
    assert http.post("/api/admin/tasks/asr/x/cancel").status_code == 403


def test_config_effective_state(env) -> None:
    http, current = env
    assert http.put("/api/admin/ai/config", json={"base_url": "http://10.0.0.5/v1", "model": "m1", "max_concurrency": 2, "context_tokens": 32768}).status_code == 200
    body = http.get("/api/admin/ai/config").json()
    assert (body["max_concurrency"], body["context_tokens"], body["model"]) == (2, 32768, "m1")
    assert http.put("/api/admin/ai/config", json={"base_url": "", "asr_base_url": "http://10.0.0.6/v1", "asr_model": "whisper"}).status_code == 200
    current["user"] = MEMBER
    assert http.get("/api/enrichment").json() == {"ai_summaries": False, "asr": True, "disabled_features": []}


def test_ai_features_disabled_round_trip(env) -> None:
    http, current = env
    saved = http.put("/api/admin/ai/config", json={"ai_features_disabled": ["recap", "translate"]})
    assert saved.status_code == 200 and saved.json()["ai_features_disabled"] == ["recap", "translate"]
    # The member player menu reads the switches to show those actions as turned off.
    current["user"] = MEMBER
    # Only the switches behind member-requestable actions are shown; the rest stay admin-side.
    assert http.get("/api/enrichment").json()["disabled_features"] == ["translate"]
    current["user"] = ADMIN
    saved = http.put("/api/admin/ai/config", json={"ai_features_disabled": ["recap"]})
    assert http.get("/api/admin/ai/config").json()["ai_features_disabled"] == ["recap"]
    assert http.put("/api/admin/ai/config", json={"ai_features_disabled": ["not/a-key"]}).status_code == 422
    assert http.put("/api/admin/ai/config", json={"ai_features_disabled": [f"k{i}" for i in range(33)]}).status_code == 422
    # Untouched by an update that omits it.
    assert http.put("/api/admin/ai/config", json={"model": "m2"}).json()["ai_features_disabled"] == ["recap"]


def test_admin_task_list_permission_safe_and_paged(env) -> None:
    http, _ = env
    page = http.get("/api/admin/tasks", params={"limit": 2}).json()
    assert [t["id"] for t in page["items"]] == ["d-mine", "d-queued"] and page["next_cursor"]
    assert page["counts"]["download"] == {"failed": 1, "queued": 1, "completed": 1}
    rest = http.get("/api/admin/tasks", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    (failed,) = rest["items"]
    # Another member's download: owner, site and real error, but never their title or source.
    assert (failed["title"], failed["owner"], failed["detail"], failed["error"], failed["attempts"]) == (None, "Member", "youtube", "HTTP 403", 1)
    assert "secret" not in str(rest) and failed["can_retry"] and not failed["can_cancel"]
    assert page["items"][0]["title"] == "Admin download"
    assert [t["id"] for t in http.get("/api/admin/tasks", params={"status": "failed"}).json()["items"]] == ["d-failed"]
    assert http.get("/api/admin/tasks", params={"cursor": "nope"}).status_code == 400


def test_admin_task_cancel_owner_semantics(env) -> None:
    http, _ = env
    cancelled = http.post("/api/admin/tasks/download/d-queued/cancel")
    assert cancelled.json() == {"status": "cancelled"}
    with db_module.session_scope() as db:
        assert db.get(DownloadJob, "d-queued").status == "cancelled"
        assert db.get(DownloadJob, "d-failed").status == "failed"  # untouched
    assert http.post("/api/admin/tasks/download/missing/cancel").status_code == 404

    with db_module.session_scope() as db:
        db.add_all([
            AsrJob(id="a-live", library_item_id="priv", model_id="w", state="running", requested_by="member", created_at=T0),
            AsrJob(id="a-dead", library_item_id="shared", model_id="w", state="queued", requested_by="member", created_at=T0 + timedelta(seconds=1)),
        ])
    with local_asr.jobs.lock:
        local_asr.jobs.running.add("a-live")
    try:
        listed = http.get("/api/admin/tasks", params={"kind": "asr"}).json()
        assert listed["counts"]["asr"] == {"active": 1, "interrupted": 1}
        by_id = {t["id"]: t for t in listed["items"]}
        assert by_id["a-dead"]["status"] == "interrupted" and by_id["a-dead"]["title"] == "Shared talk"
        assert by_id["a-live"]["title"] is None and by_id["a-live"]["can_cancel"]  # private to the member
        assert [t["id"] for t in http.get("/api/admin/tasks", params={"kind": "asr", "status": "active"}).json()["items"]] == ["a-live"]
        assert [t["id"] for t in http.get("/api/admin/tasks", params={"kind": "asr", "status": "failed"}).json()["items"]] == ["a-dead"]
        assert http.post("/api/admin/tasks/asr/a-live/cancel").json() == {"status": "canceling"}
        assert "a-live" in local_asr.jobs.canceled
        assert http.post("/api/admin/tasks/asr/a-dead/cancel").status_code == 409
    finally:
        with local_asr.jobs.lock:
            local_asr.jobs.running.discard("a-live")
            local_asr.jobs.canceled.discard("a-live")


def test_summary_cancel_keeps_previous(env, monkeypatch) -> None:
    fake = FakeAi()
    try:
        with db_module.session_scope() as db:
            record = YtDlpService(db).ensure_app_settings()
            record.ai_base_url, record.ai_model = fake.url, MODEL
        with db_module.session_scope() as db:
            transcript = TranscriptService(db).store("shared", language="en", source_kind="source_caption", cues=[(0, 900, "Hello")])
            db.add(Summary(id="old", library_item_id="shared", transcript_id=transcript.id, transcript_revision=transcript.revision,
                           model_id="older", state="succeeded", overview="Kept", completed_at=T0))
        gate, release = threading.Event(), threading.Event()

        def slow_generate(config, cues):
            gate.set()
            release.wait(5)
            return {"overview": "new", "key_points": [], "chapters": [], "dropped_points": 0}

        monkeypatch.setattr(summaries, "generate", slow_generate)
        with db_module.session_scope() as db:
            summary, _ = summaries.request_summary(db, db.get(type(transcript), transcript.id), MODEL, "member")
            summary_id = summary.id
        assert gate.wait(5)
        http, _ = env
        assert http.post(f"/api/admin/tasks/summary/{summary_id}/cancel").json() == {"status": "canceling"}
        release.set()
        for _ in range(200):
            with db_module.session_scope() as db:
                if db.get(Summary, summary_id).state == "canceled":
                    break
            threading.Event().wait(0.02)
        with db_module.session_scope() as db:
            assert db.get(Summary, summary_id).state == "canceled" and db.get(Summary, summary_id).overview is None
            assert db.get(Summary, "old").overview == "Kept"
        assert http.get("/api/library/shared/summary").json()["id"] == "old"
    finally:
        fake.server.shutdown()
        fake.server.server_close()
