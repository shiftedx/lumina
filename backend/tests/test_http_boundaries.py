import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.http_boundaries import (
    MAX_REQUEST_BODY_BYTES,
    REQUEST_TOO_LARGE_MESSAGE,
    SECURITY_HEADERS,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.main import app
from app.models import SourceAutomation
from app.security import get_current_user
from app import main as main_module
from support import make_user


@pytest.fixture
def authenticated_client(tmp_path, monkeypatch, db_factory, api_client):  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    user = make_user("user-1", username="viewer", display_name="Viewer")
    with db_factory.begin() as session:
        session.add(user)
    client = api_client(user=user, base_url="http://localhost", raise_server_exceptions=False)
    return client, db_factory, tmp_path / "data"


def test_unauthenticated_health_exposes_only_liveness() -> None:
    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert all(marker not in response.text for marker in ("data_dir", "ffmpeg", "yt_dlp"))
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
    finally:
        client.close()


def test_auto_download_route_is_owner_scoped_and_preserves_schedule_and_error(authenticated_client) -> None:  # noqa: ANN001
    client, session_factory, _data_dir = authenticated_client
    with session_factory.begin() as session:
        automation = SourceAutomation(
            id="automation-1", user_id="user-1", label="Channel", source_url="https://example.com/channel",
            source_type="channel", cron_expression="17 */6 * * *", active=True, auto_download=False,
            next_check_at=None, last_error="Previous failure", format_selection={}, output_profile={}, rules={},
            duplicate_policy="skip_same_source",
        )
        session.add(automation)

    response = client.patch("/api/automations/automation-1/auto-download", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["auto_download"] is True
    assert response.json()["cron_expression"] == "17 */6 * * *"
    assert response.json()["last_error"] == "Previous failure"
    paused = client.post("/api/automations/automation-1/pause")
    assert paused.status_code == 200
    assert paused.json()["active"] is False
    assert paused.json()["last_error"] == "Previous failure"
    resumed = client.post("/api/automations/automation-1/resume")
    assert resumed.status_code == 200
    assert resumed.json()["active"] is True

    other = make_user("other")
    with session_factory.begin() as session:
        session.add(other)
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.patch("/api/automations/automation-1/auto-download", json={"enabled": False}).status_code == 404
    assert client.post("/api/automations/automation-1/pause").status_code == 404
    assert client.delete("/api/automations/automation-1").status_code == 404

    owner = make_user("user-1", username="viewer", display_name="Viewer")
    app.dependency_overrides[get_current_user] = lambda: owner
    assert client.delete("/api/automations/automation-1").status_code == 204


@pytest.mark.parametrize(
    "retention",
    [
        {"mode": "keep_forever", "archive_metadata": True},
        {"mode": "delete_after_age", "delete_after_days": 1, "archive_metadata": True},
    ],
)
def test_source_automation_retention_is_not_an_input(authenticated_client, retention) -> None:  # noqa: ANN001
    client, _, _ = authenticated_client

    response = client.post(
        "/api/automations",
        json={
            "label": "Disposable channel",
            "source_url": "https://example.com/channel",
            "retention": retention,
        },
    )

    assert response.status_code == 422
    assert client.get("/api/automations").json() == []


def test_source_automation_retention_is_not_a_setting(authenticated_client) -> None:  # noqa: ANN001
    client, _, _ = authenticated_client

    response = client.put(
        "/api/settings/me",
        json={"automation_defaults": {"retention": {"mode": "keep_forever", "archive_metadata": True}}},
    )

    assert response.status_code == 422


def test_runtime_health_requires_authentication() -> None:
    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.get("/api/runtime-health")

        assert response.status_code == 401
        assert "data_dir" not in response.text
    finally:
        client.close()


def test_security_headers_cover_api_and_static_files(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "asset.js").write_text("window.boundaryTest = true;", encoding="utf-8")
    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)
    test_app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    with TestClient(test_app) as client:
        responses = [client.get("/missing"), client.get("/assets/asset.js")]

    assert responses[0].status_code == 404
    assert responses[1].status_code == 200
    for response in responses:
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
    assert "'unsafe-eval'" not in responses[1].headers["content-security-policy"]
    assert "frame-ancestors 'none'" in responses[1].headers["content-security-policy"]
    assert "img-src 'self' data: blob: http: https:" in responses[1].headers["content-security-policy"]
    assert "media-src 'self' blob:;" in responses[1].headers["content-security-policy"]


def test_security_headers_cover_early_request_rejections() -> None:
    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.post(
            "/api/auth-profiles/profile-1/cookie-file",
            headers={"origin": "https://attacker.invalid", "content-length": "0"},
        )

        assert response.status_code == 403
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
    finally:
        client.close()


def test_security_headers_cover_unhandled_server_errors(authenticated_client, monkeypatch) -> None:  # noqa: ANN001
    client, _, _ = authenticated_client
    failure_marker = "runtime-diagnostic-secret"

    def fail_diagnostics():
        raise RuntimeError(failure_marker)

    monkeypatch.setattr(main_module.maintenance_sweep_health, "snapshot", fail_diagnostics)
    response = client.get("/api/runtime-health")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert failure_marker not in response.text
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_global_request_limit_rejects_declared_and_chunked_oversized_bodies() -> None:
    downstream_called = False

    async def downstream(scope, receive, send):  # noqa: ANN001
        nonlocal downstream_called
        downstream_called = True
        await PlainTextResponse("accepted")(scope, receive, send)

    async def invoke(headers, incoming):  # noqa: ANN001
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/jobs", "raw_path": b"/api/jobs", "query_string": b"",
            "root_path": "", "headers": headers, "client": ("127.0.0.1", 1234), "server": ("localhost", 80),
        }
        sent = []

        async def receive():
            return incoming.pop(0)

        async def send(message):  # noqa: ANN001
            sent.append(message)

        await RequestBodyLimitMiddleware(downstream)(scope, receive, send)
        return sent

    declared = asyncio.run(invoke(
        [(b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode("ascii"))],
        [{"type": "http.request", "body": b"", "more_body": False}],
    ))
    chunked = asyncio.run(invoke(
        [(b"transfer-encoding", b"chunked")],
        [
            {"type": "http.request", "body": b"x" * MAX_REQUEST_BODY_BYTES, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ],
    ))

    assert declared[0]["status"] == 413
    assert chunked[0]["status"] == 413
    assert REQUEST_TOO_LARGE_MESSAGE.encode() in declared[1]["body"]
    assert downstream_called is False


# ---- 2.1.0: the upload route shape gets 15 MiB, read through a counter (never buffered before auth) ----

from app.http_boundaries import MAX_UPLOAD_BODY_BYTES, body_limit  # noqa: E402

TITLE_ID = "0b6f3c1e-7a1d-4c2b-9f00-1234567890ab"
MIB = 1024 * 1024


def _scope(method: str, path: str, headers: list) -> dict:  # noqa: ANN001
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("localhost", 80),
    }


def _run_limit(method: str, path: str, chunks: list[bytes], *, declared: int | None = None, downstream=None):  # noqa: ANN001, ANN202
    """Drive RequestBodyLimitMiddleware with ``chunks``; returns (sent messages, receive calls, what the app saw)."""
    total = sum(map(len, chunks))
    headers = [(b"content-length", str(total if declared is None else declared).encode())] if declared != -1 else [(b"transfer-encoding", b"chunked")]
    incoming = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1} for index, chunk in enumerate(chunks)]
    calls = {"receive": 0}
    seen: dict = {}
    sent: list = []
    scope = _scope(method, path, headers)

    async def receive():
        calls["receive"] += 1
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message):  # noqa: ANN001
        sent.append(message)

    async def reading_app(scope, receive, send):  # noqa: ANN001
        seen["receive_calls_before_app"] = calls["receive"]
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                seen["disconnected"] = True
                break
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        seen["body"] = len(body)
        seen["too_large"] = scope.get("state", {}).get("body_too_large", False)
        await PlainTextResponse("ok")(scope, receive, send)

    asyncio.run(RequestBodyLimitMiddleware(downstream or reading_app)(scope, receive, send))
    return sent, calls["receive"], seen


@pytest.mark.parametrize("kind, index", [("Primary", 0), ("Backdrop", 4), ("Logo", 0)])
def test_the_upload_route_shape_gets_15_mib(kind, index) -> None:  # noqa: ANN001
    path = f"/api/titles/{TITLE_ID}/images/{kind}/{index}"
    assert body_limit(_scope("PUT", path, [])) == MAX_UPLOAD_BODY_BYTES == 15 * MIB
    sent, _calls, seen = _run_limit("PUT", path, [b"x" * MIB] * 3)
    assert sent[0]["status"] == 200 and seen["body"] == 3 * MIB and seen["too_large"] is False


def test_a_query_string_leaves_the_upload_cap_in_place() -> None:
    scope = _scope("PUT", f"/api/titles/{TITLE_ID}/images/Primary/0", [])
    scope["query_string"] = b"x=1"
    assert body_limit(scope) == MAX_UPLOAD_BODY_BYTES


@pytest.mark.parametrize("path, method", [
    (f"/api/titles/{TITLE_ID}/images/Thumb/0", "PUT"), (f"/api/titles/{TITLE_ID}/images/Banner/0", "PUT"),
    (f"/api/titles/{TITLE_ID}/images/Backdrop/5", "PUT"), (f"/api/titles/{TITLE_ID}/images/Backdrop/04", "PUT"),
    (f"/api/titles/{TITLE_ID}/images/Backdrop/-1", "PUT"), (f"/api/titles/{TITLE_ID}/images/primary/0", "PUT"),
    (f"/api/titles/{TITLE_ID}/images/Primary/0", "POST"), (f"/api/titles/{TITLE_ID}/images/Primary/0", "PATCH"),
    (f"/api/titles/{TITLE_ID}/images/Primary/0/", "PUT"), ("/api/titles/x/images/Primary/0", "PUT"),
    (f"/api/titles/{TITLE_ID.upper()}/images/Primary/0", "PUT"), (f"/api/titles/{TITLE_ID}/images/Primary/0/..", "PUT"),
    (f"/api/titles/{TITLE_ID}/images/Primary/0/../../../downloads", "PUT"), (f"/x/api/titles/{TITLE_ID}/images/Primary/0", "PUT"),
    (f"/api/titles/{TITLE_ID}/images/Backdrop/order", "POST"), ("/api/jobs", "POST"),
])
def test_everything_else_keeps_2_mib(path, method) -> None:  # noqa: ANN001
    assert body_limit(_scope(method, path, [])) == MAX_REQUEST_BODY_BYTES
    declared, _calls, seen = _run_limit(method, path, [b"x" * (MAX_REQUEST_BODY_BYTES + 1)])
    streamed, _calls, _seen = _run_limit(method, path, [b"x" * MAX_REQUEST_BODY_BYTES, b"x"], declared=-1)
    assert declared[0]["status"] == 413 and streamed[0]["status"] == 413 and seen == {}
    assert b"2 MiB" in declared[1]["body"]


def test_over_15_mib_declared_is_413_before_any_read() -> None:
    sent, calls, seen = _run_limit("PUT", f"/api/titles/{TITLE_ID}/images/Primary/0", [b""], declared=15 * MIB + 1)
    assert sent[0]["status"] == 413 and calls == 0 and seen == {}
    assert b"15 MiB" in sent[1]["body"]


def test_over_15_mib_streamed_without_content_length_is_413() -> None:
    chunks = [b"x" * MIB] * 15 + [b"x", b"y" * MIB, b"z" * MIB]
    _sent, calls, seen = _run_limit("PUT", f"/api/titles/{TITLE_ID}/images/Backdrop/2", chunks, declared=-1)
    # the app saw the cap, then a disconnect: the chunk past the cap is never handed over and nothing more is read
    assert seen["too_large"] is True and seen["disconnected"] is True and seen["body"] == 15 * MIB and calls == 16


def test_an_unauthenticated_upload_is_refused_without_buffering_the_body() -> None:
    async def refusing_app(scope, receive, send):  # noqa: ANN001 - what the route does when its auth dependency fails
        await JSONResponse(status_code=401, content={"detail": "Not authenticated"})(scope, receive, send)

    sent, calls, _seen = _run_limit("PUT", f"/api/titles/{TITLE_ID}/images/Primary/0", [b"x" * MIB] * 10, downstream=refusing_app)
    assert sent[0]["status"] == 401 and calls == 0


def test_other_routes_are_unchanged_spot_check() -> None:
    for method, path in (("POST", "/api/downloads"), ("PUT", f"/api/titles/{TITLE_ID}/watched"), ("POST", "/api/metadata/edits")):
        sent, _calls, seen = _run_limit(method, path, [b"x" * (MAX_REQUEST_BODY_BYTES + 1)], declared=-1)
        assert sent[0]["status"] == 413 and seen == {}
        small, _calls, seen = _run_limit(method, path, [b"x" * 10])
        assert small[0]["status"] == 200 and seen["receive_calls_before_app"] == 1  # still buffered and replayed


def test_the_image_upload_rate_limit_rule_exists() -> None:
    from app.services.rate_limit import RATE_LIMIT_RULES

    rule = RATE_LIMIT_RULES["image_upload"]
    assert (rule.max_requests, rule.window_seconds) == (30, 3600)


def _stalling_receive(chunks: list[bytes], delay: float):  # noqa: ANN202
    """Hands over ``chunks`` (more to come after each), then each later chunk only after ``delay`` seconds."""
    async def receive():  # noqa: ANN202
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": True}
        await asyncio.sleep(delay)
        return {"type": "http.request", "body": b"late", "more_body": True}
    return receive


def _drive_upload(receive, *, idle: float, deadline: float, monkeypatch) -> tuple[dict, float]:  # noqa: ANN001
    from app import http_boundaries

    monkeypatch.setattr(http_boundaries, "UPLOAD_IDLE_TIMEOUT_SECONDS", idle)
    monkeypatch.setattr(http_boundaries, "UPLOAD_BODY_DEADLINE_SECONDS", deadline)
    seen: dict = {}
    scope = _scope("PUT", f"/api/titles/{TITLE_ID}/images/Primary/0", [(b"transfer-encoding", b"chunked")])

    async def app(scope, receive, send):  # noqa: ANN001
        while (message := await receive())["type"] != "http.disconnect":
            seen["chunks"] = seen.get("chunks", 0) + 1
        seen["timeout"] = scope.get("state", {}).get("body_timeout", False)
        await PlainTextResponse("ok")(scope, receive, lambda m: asyncio.sleep(0))

    started = time.monotonic()
    asyncio.run(RequestBodyLimitMiddleware(app)(scope, receive, lambda m: asyncio.sleep(0)))
    return seen, time.monotonic() - started


def test_a_stalled_upload_body_is_cut_after_the_idle_timeout(monkeypatch) -> None:  # noqa: ANN001
    """A body that stops arriving ends in a disconnect with state.body_timeout, so the route answers 408."""
    from app.http_boundaries import UPLOAD_BODY_DEADLINE_SECONDS, UPLOAD_IDLE_TIMEOUT_SECONDS

    assert (UPLOAD_IDLE_TIMEOUT_SECONDS, UPLOAD_BODY_DEADLINE_SECONDS) == (15, 300)
    seen, took = _drive_upload(_stalling_receive([b"x" * 10], 30), idle=0.2, deadline=60, monkeypatch=monkeypatch)
    assert seen == {"chunks": 1, "timeout": True} and took < 2


def test_a_trickled_upload_body_is_cut_at_the_total_deadline(monkeypatch) -> None:  # noqa: ANN001
    seen, took = _drive_upload(_stalling_receive([], 0.1), idle=1, deadline=0.5, monkeypatch=monkeypatch)
    assert seen["timeout"] is True and 2 <= seen["chunks"] <= 6 and took < 2


def test_hsts_only_over_https_from_a_trusted_proxy(monkeypatch) -> None:
    """HSTS pins the public HTTPS host; LAN HTTP mode and a forged X-Forwarded-Proto never get it."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", "")  # nobody is trusted
    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)
    test_app.get("/x")(lambda: {"ok": True})
    with TestClient(test_app) as client:
        assert "strict-transport-security" not in client.get("/x").headers
        assert "strict-transport-security" not in client.get("/x", headers={"x-forwarded-proto": "https"}).headers

    from app.services import public_address
    monkeypatch.setattr(public_address, "arrived_over_https", lambda scope: True)
    with TestClient(test_app) as client:
        assert client.get("/x").headers["strict-transport-security"] == "max-age=31536000"


def test_browser_isolation_headers_and_tight_media_policy() -> None:
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert SECURITY_HEADERS["Cross-Origin-Opener-Policy"] == "same-origin"
    # The browser never receives an upstream media address (ADR 0008) and never embeds a provider frame.
    assert "media-src 'self' blob:;" in csp
    assert "frame-src 'none'" in csp
