import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import User
from app.security import get_current_user
from app.services import local_ai
from app.services.local_ai import AiConfig, LocalAiError, check_connection, chat
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError

MODEL = "fake-model"


class FakeAi:
    """Loopback OpenAI-compatible fake. ``handler`` returns (status, body, headers) for (method, path, json)."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict, dict | None]] = []
        self.handler = lambda method, path, body: (200, {"data": [{"id": MODEL}]}, {})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.requests.append((self.command, self.path, dict(self.headers), body))
                status, payload, headers = fake.handler(self.command, self.path, body)
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self, **overrides) -> AiConfig:
        values = dict(
            ai_base_url=self.url, ai_model=MODEL, ai_api_key=None, ai_max_concurrency=3,
            ai_context_tokens=145_000, asr_base_url="", asr_model="",
        )
        return AiConfig(**(values | overrides))


@pytest.fixture
def fake_ai():
    fake = FakeAi()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def client():
    db_module.init_db()
    current = {"user": User(id="admin", username="admin", display_name="Admin", role="admin", is_active=True)}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        yield TestClient(app, base_url="http://localhost"), current
    finally:
        app.dependency_overrides.clear()


def test_ai_endpoint_exact_scope(fake_ai) -> None:
    assert check_connection(fake_ai.config())["ok"] is True
    assert [(m, p) for m, p, _, _ in fake_ai.requests] == [("GET", "/v1/models")]
    # The AI exception does not relax public-media SSRF checks for the same private host.
    with pytest.raises(PublicSourcePolicyError):
        PublicSourcePolicy().validate_url(fake_ai.url.replace("/v1", "/media.mp4"))
    # A redirect can never carry the AI client elsewhere.
    fake_ai.handler = lambda *_: (302, b"", {"location": "http://169.254.169.254/latest"})
    result = check_connection(fake_ai.config())
    assert result["ok"] is False and "302" in result["error"]
    assert len(fake_ai.requests) == 2


def test_ai_secret_not_returned(fake_ai, client) -> None:
    http, current = client
    saved = http.put("/api/admin/ai/config", json={"base_url": fake_ai.url, "model": MODEL, "api_key": "sup3r-secret", "max_concurrency": 2})
    assert saved.status_code == 200
    assert saved.json()["has_api_key"] is True and "sup3r-secret" not in saved.text
    fetched = http.get("/api/admin/ai/config")
    assert fetched.json()["base_url"] == fake_ai.url and fetched.json()["max_concurrency"] == 2
    assert "sup3r-secret" not in fetched.text
    tested = http.post("/api/admin/ai/test")
    assert tested.json()["ok"] is True and "sup3r-secret" not in tested.text
    assert fake_ai.requests[-1][2]["Authorization"] == "Bearer sup3r-secret"

    for bad in ("http://user:pw@10.0.0.1/v1", "ftp://10.0.0.1/v1", "http://10.0.0.1/v1?x=1", "not a url"):
        rejected = http.put("/api/admin/ai/config", json={"base_url": bad})
        assert rejected.status_code == 422 and "pw@" not in rejected.text
    assert http.put("/api/admin/ai/config", json={"api_key": ""}).json()["has_api_key"] is False

    current["user"] = User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    assert http.get("/api/admin/ai/config").status_code == 403
    assert http.put("/api/admin/ai/config", json={"base_url": ""}).status_code == 403
    assert http.post("/api/admin/ai/test").status_code == 403


def test_ai_health_timeout_schema(fake_ai, client, monkeypatch) -> None:
    http, _ = client
    assert http.put("/api/admin/ai/config", json={"base_url": fake_ai.url, "model": MODEL}).status_code == 200
    fake_ai.handler = lambda *_: (200, b"<html>not json", {})
    assert http.post("/api/admin/ai/test").json() == {"ok": False, "models": [], "model_available": False, "error": "Local AI endpoint returned malformed JSON"}
    fake_ai.handler = lambda *_: (200, {"data": [{"id": "other"}]}, {})
    assert http.post("/api/admin/ai/test").json()["model_available"] is False

    monkeypatch.setattr(local_ai, "TEST_TIMEOUT_SECONDS", 0.2)
    fake_ai.handler = lambda *_: (time.sleep(1), (200, {}, {}))[1]
    assert http.post("/api/admin/ai/test").json()["error"] == "Local AI endpoint timed out"
    # A failed test never mutates the saved configuration.
    assert http.get("/api/admin/ai/config").json()["base_url"] == fake_ai.url


def test_ai_no_cloud_fallback(fake_ai) -> None:
    disabled = fake_ai.config(ai_base_url="")
    assert check_connection(disabled)["error"] == "Local AI is not configured"
    with pytest.raises(LocalAiError):
        chat(disabled, [{"role": "user", "content": "hi"}], max_tokens=10)
    assert fake_ai.requests == []


def test_inference_slot_caps_concurrency() -> None:
    peak, active, lock = [0], [0], threading.Lock()

    def work():
        with local_ai.inference_slot(2):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 2
