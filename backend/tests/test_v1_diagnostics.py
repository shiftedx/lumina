"""Members get minimal health; admins get redacted diagnostics; logs never carry secrets."""
from __future__ import annotations

from collections import deque
import logging
from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.main import app
from app.models import DownloadJob, ImportRun, StorageRoot, User
from app.routers import admin_diagnostics, admin_library_automation
from app.security import hash_password, utcnow
from app.services import local_playback_sessions as lps
from app.services.hwaccel import HwStatus, hwaccel
from app.services.redaction import redact
from test_admin_library_automation_api import FakeAutomation
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

SECRETS = ("sk-livesecretkey123456", "tok3n-value-abc", "c00kie-value", "hunter2-pass", "inv1te-secret", "res3t-secret")
POISON = (
    f"Authorization: Bearer {SECRETS[1]} failed at /srv/media/Family Videos/birthday.mp4 "
    f"cookie={SECRETS[2]} password={SECRETS[3]} https://x.test/join#invite={SECRETS[4]} "
    f"https://x.test/r#reset={SECRETS[5]} api_key: {SECRETS[0]} under {{data_dir}}/library/x.mp4"
)


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "http://192.0.2.1:8080/v1")
    monkeypatch.setattr(settings, "ai_model", "test-model")
    monkeypatch.setattr(admin_diagnostics, "check_connection", lambda config: {"ok": False, "model_available": False, "error": "Local AI endpoint is unreachable"})


@pytest.fixture(autouse=True)
def fixed_hardware(monkeypatch: pytest.MonkeyPatch):
    hwaccel.__init__()
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus(probe_error=f"qsv: cannot open {settings.data_dir}/dri/renderD128", software_tonemap="software"))
    yield
    hwaccel.__init__()


def test_member_health_no_paths(client: TestClient, factory) -> None:
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    login(client, "member")
    health = client.get("/api/runtime-health")
    assert health.status_code == 200
    assert set(health.json()) == {"status", "version"} and health.json()["status"] in {"ok", "degraded"}
    assert str(settings.data_dir) not in health.text
    assert client.get("/api/admin/diagnostics").status_code == 403


def test_diagnostic_secret_redaction_recursive(client: TestClient, factory) -> None:
    poison = POISON.replace("{data_dir}", str(settings.data_dir))
    with factory.begin() as db:
        db.add(StorageRoot(id="r1", label="Family NAS", path="/srv/media/Family Videos", mode="external", observation={"state": "offline"}))
        db.add(DownloadJob(id=str(uuid.uuid4()), user_id="u1", source_url="https://example.com/v", status="failed", error=poison, finished_at=utcnow()))
    login(client)
    response = client.get("/api/admin/diagnostics")
    assert response.status_code == 200, response.text
    body = response.json()
    for secret in (*SECRETS, str(settings.data_dir), "/srv/media", "birthday.mp4"):
        assert secret not in response.text, secret
    assert body["recent_errors"][0]["source"] == "download" and "[redacted]" in body["recent_errors"][0]["message"]
    assert body["storage_roots"] == [{"label": "Family NAS", "mode": "external", "enabled": True, "state": "offline", "checked_at": None}]
    assert body["versions"]["lumina"] and body["versions"]["python"]
    assert body["ai"]["ok"] is False and body["ai"]["error"] == "Local AI endpoint is unreachable"


def test_playback_block_reports_hardware_and_is_redacted(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lps, "recent_errors", deque([f"Error opening input {settings.data_dir}/library/secret-movie.mkv token={SECRETS[1]}"]))
    login(client)
    body = client.get("/api/admin/diagnostics").json()["playback"]
    assert (body["hwaccel"], body["active"], body["probe_ok"], body["tonemap"]) == ("auto", "none", False, "software")
    assert body["cache_cap_bytes"] == 10 * 1024**3 and body["sessions"] == {} and body["throttled"] == 0
    assert body["probe_error"].startswith("qsv: cannot open [path]")
    text = str(body)
    for secret in (str(settings.data_dir), "secret-movie", SECRETS[1]):
        assert secret not in text, secret


def test_transcode_diagnostics_reprobes_for_admins_only(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    reprobes: list[int] = []
    monkeypatch.setattr(hwaccel, "reprobe", lambda: reprobes.append(1))
    with factory.begin() as db:
        db.add(User(id="u2", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
    origin = settings.allowed_origins_list[0]
    csrf = login(client, "member")
    assert client.post("/api/admin/media-server/transcode-diagnostics", headers={"Origin": origin, "X-CSRF-Token": csrf}).status_code == 403
    client.cookies.clear()
    csrf = login(client)
    response = client.post("/api/admin/media-server/transcode-diagnostics", headers={"Origin": origin, "X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["probe_ok"] is False and reprobes == [1]


def test_log_records_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("lumina.test")
    with caplog.at_level(logging.INFO, logger="lumina.test"):
        logger.info("request failed: %s", POISON)
        logger.info("wrote /srv/media/show/ep1.mp4")
        try:
            raise RuntimeError(f"Cookie: {SECRETS[2]}")
        except RuntimeError:
            logger.exception("boom token=%s", SECRETS[1])
    text = caplog.text
    for secret in SECRETS:
        assert secret not in text, secret
    assert "/srv/media/show/ep1.mp4" in text  # operator logs keep paths; only the admin report strips them


def test_redact_keeps_useful_text() -> None:
    assert redact("GET /api/library/abc HTTP/1.1 200", paths=True) == "GET /api/library/abc HTTP/1.1 200"
    assert redact("HTTP Error 403: Forbidden") == "HTTP Error 403: Forbidden"
    assert redact("see https://user:pw@host/x", paths=True) == "see https://[redacted]@host/x"
    assert redact("open failed at /home/dana/clips/v.mp4, retrying", paths=True) == "open failed at [path], retrying"
    assert redact('{"password": "p4ss", "new_password":"q"}') == '{"password": "[redacted]", "new_password":"[redacted]"}'


def test_remote_preparation_outcome_is_logged_with_safe_fields_and_shown_to_admins(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from app.services.remote_streaming import RemoteStreamingService, UnsupportedPlaybackError

    class UnavailablePackager:
        def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
            raise UnsupportedPlaybackError("An upstream track was unavailable while preparing playback (HTTP 503).")

        def close(self, stream_id):  # noqa: ANN001
            pass

        def close_generation(self, stream_id, generation):  # noqa: ANN001
            pass

    signed = "https://signed.example/v?expire=1&signature=s1gned-secret"
    service = RemoteStreamingService(resolver=None, reader=None, hls_packager=UnavailablePackager(), token_factory=lambda: "streamid-opaque-tail", clock=lambda: 1_000.0)  # type: ignore[arg-type]
    playback = service.register(owner_user_id="u1", source_url="https://www.youtube.com/watch?v=abc", info={"extractor_key": "Youtube", "formats": [
        {"format_id": "137", "url": signed, "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "http_headers": {"Cookie": SECRETS[2]}},
        {"format_id": "140", "url": signed, "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
    ]})
    with caplog.at_level(logging.INFO, logger="lumina.playback"), pytest.raises(UnsupportedPlaybackError):
        service.serve_hls("u1", playback.stream_id, 1, "manifest.m3u8")

    line = next(record.getMessage() for record in caplog.records if record.name == "lumina.playback")
    for fragment in ("remote_stream.prepare outcome=failed", "stream=streamid", "provider=youtube", "transport=hls", "video_format=137", "audio_format=140", "duration_ms=", "HTTP 503"):
        assert fragment in line, fragment
    for secret in ("signed.example", "s1gned-secret", SECRETS[2], "streamid-opaque-tail"):
        assert secret not in caplog.text, secret

    login(client)
    errors = client.get("/api/admin/diagnostics").json()["recent_errors"]
    assert any(error["source"] == "playback" and "HTTP 503" in error["message"] for error in errors)


def test_access_log_records_failed_requests_by_route_template_only(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    login(client)
    with caplog.at_level(logging.INFO, logger="lumina.access"):
        assert client.get("/api/runtime-health").status_code == 200
        assert client.get("/api/remote-streams/secret-stream-id/content?token=abc").status_code == 404
    lines = [record.getMessage() for record in caplog.records if record.name == "lumina.access"]
    assert len(lines) == 1
    assert lines[0].startswith("GET /api/remote-streams/{stream_id}/content 404 ")
    assert "secret-stream-id" not in caplog.text and "abc" not in lines[0]


def test_library_automation_block_redacted_and_degraded(client: TestClient, factory, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAutomation()
    monkeypatch.setattr(admin_library_automation, "automation_service", lambda: fake)
    now = utcnow()
    with factory.begin() as db:
        db.add(StorageRoot(id="r1", label="NAS", path="/srv/media/Family Videos", mode="external"))
        for i, (trigger, state, age) in enumerate([("manual", "succeeded", 1), (None, "succeeded", 1), ("scheduled", "succeeded", 1), ("scheduled", "succeeded", 1),
                                                   ("watch", "succeeded", 1), ("watch", "failed", 1), ("watch", "needs_confirmation", 1), ("manual", "succeeded", 48)]):
            db.add(ImportRun(id=f"run{i}", root_id="r1", user_id="u1", trigger=trigger, state=state, created_at=now - timedelta(hours=age)))
    login(client)
    monkeypatch.setattr(admin_diagnostics, "health_status", lambda sweeps: "ok")
    healthy = client.get("/api/admin/diagnostics").json()["status"]
    assert healthy == "ok"
    fake.diag["poller"] = {"heartbeat_at": "2026-10-02T14:00:00+00:00", "stalled": True, "last_error": "scandir failed at /srv/media/Family Videos/x"}
    response = client.get("/api/admin/diagnostics")
    body = response.json()
    assert body["library_automation"]["runs_24h"] == {"manual": 2, "scheduled": 2, "watch": 3, "needs_confirmation": 1, "failed": 1}
    assert "/srv/media" not in response.text and "[path]" in body["library_automation"]["poller"]["last_error"]
    assert body["status"] == "degraded"
    fake.diag["poller"]["stalled"] = False
    assert client.get("/api/admin/diagnostics").json()["status"] == healthy


@pytest.mark.parametrize("error", [ImportError, NotImplementedError])
def test_library_automation_absent_is_null(client: TestClient, monkeypatch: pytest.MonkeyPatch, error) -> None:
    def missing():
        raise error("no automation")

    monkeypatch.setattr(admin_library_automation, "automation_service", missing)
    login(client)
    response = client.get("/api/admin/diagnostics")
    assert response.status_code == 200 and response.json()["library_automation"] is None
