"""S02 — provider OAuth and Twitch connection identity are absent; local auth survives.

Desired behavior after the removal (provider-native chat that needs
third-party authentication is unavailable in 1.0; MVP first release, no legacy
provider accounts): no /api/oauth/* routes, no /api/twitch/connection* routes,
no member-scoped /api/twitch/live-chat* routes, no OAuth identity models or
tables, and the session/user DTOs carry no provider-identity fields. Local
account auth (login/me/logout) and the captured-chat READ path
(GET /api/chat-replay/{source_identity}) survive unchanged.

Observed absence semantics (recorded per lead steer #1): the app mounts the SPA
static files at "/" (StaticFiles(html=True)), which answers UNREGISTERED paths:
GET -> 404 (the HTML 404 page), POST/DELETE -> 405 (the mount allows GET/HEAD
only). The static mount intercepts the removed paths BEFORE any auth
dependency runs, so the observed answers are 404/405 — not 401 — and no
fixture authentication is needed to observe them. A surviving provider-auth
handler would answer with API semantics (2xx/401/403/400) instead, so the HTTP
checks below — including a negative-control POST to a sibling path that never
existed — catch a handler re-landing on a removed path. Route-table
inspection is the primary proof.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import app.models as models_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import ChatReplayAsset, LiveRecording
from app.schemas import UserResponse
from app.security import hash_password
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.yt_dlp_service import YtDlpService
from support import make_user

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"

TWITCH_IDENTITY = "twitch:12345"
TWITCH_SOURCE_URL = "https://twitch.tv/videos/12345"
TWITCH_ENDPOINT = f"/api/chat-replay/{quote(TWITCH_IDENTITY, safe='')}"


def test_provider_auth_routes_absent() -> None:
    """The OAuth + Twitch-connection + Twitch-live-chat routes are gone from
    the router, the service modules are deleted, and the SPA mount answers the
    removed paths with 404 (GET) / 405 (POST/DELETE) — never API semantics."""
    # Route-table proof (primary): no registered route targets the removed
    # prefixes.
    removed = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith(
            ("/api/oauth", "/api/twitch/connection", "/api/twitch/live-chat")
        )
    ]
    assert removed == [], f"removed routes still registered: {removed}"

    main_source = (REPO_ROOT / "backend" / "app" / "main.py").read_text()
    assert "OAuthService" not in main_source, "main.py must not import the OAuth service"
    assert "TwitchConnectionService" not in main_source
    assert "TwitchLiveChatService" not in main_source
    # The provider-credential service modules are gone from the repo.
    for name in (
        "oauth.py",
        "twitch_connection.py",
        "twitch_live_chat.py",
        "live_recording_twitch.py",
        "twitch_chat.py",
        "twitch_eventsub.py",
        "twitch_api.py",
    ):
        assert not (BACKEND_APP / "services" / name).exists(), f"services/{name} must be removed"

    client = TestClient(app, base_url="http://localhost")
    # Negative control: a sibling path that never existed also 405s on POST —
    # the SPA static mount is answering, not a provider handler.
    control = client.post("/api/twitch/definitely-not-a-route")
    assert control.status_code == 405

    for path in (
        "/api/oauth/providers",
        "/api/oauth/google/start",
        "/api/oauth/github/start",
        "/api/oauth/google/callback",
        "/api/oauth/github/callback",
        "/api/twitch/connection",
        "/api/twitch/connection/callback",
        "/api/twitch/live-chat/session-id/events",
    ):
        response = client.get(path)
        assert response.status_code == 404, (path, response.status_code)

    for path in (
        "/api/oauth/providers",
        "/api/oauth/google/link/start",
        "/api/oauth/google/unlink",
        "/api/twitch/connection",
        "/api/twitch/connection/begin",
        "/api/twitch/connection/cancel",
        "/api/twitch/connection/validate",
        "/api/twitch/connection/callback",
        "/api/twitch/live-chat",
        "/api/twitch/live-chat/session-id",
    ):
        response = client.post(path)
        assert response.status_code == 405, (path, response.status_code)

    for path in ("/api/twitch/connection", "/api/twitch/live-chat/session-id"):
        response = client.delete(path)
        assert response.status_code == 405, (path, response.status_code)


def test_local_login_survives_removal(db_factory, api_client) -> None:
    """Local account auth is untouched by the provider cut: login 200, me 200,
    logout 204, then me 401 — and a protected surface stays 401 without a
    session (still protected, never accidentally opened). The state-changing
    session request (logout) carries an allowedlist Origin, as the real browser
    does: the kept CSRF-style origin enforcement (security.py) 403s a
    cookie-bearing state-changing request that omits Origin/Referer."""
    with db_factory.begin() as session:
        session.add(make_user("user-local", role="admin", username="local", display_name="Local", password_hash=hash_password("test-only-passphrase")))

    client = api_client(base_url="http://localhost")
    # Unauthenticated: me and a protected member surface both refuse.
    assert client.get("/api/session/me").status_code == 401
    assert client.get("/api/library").status_code == 401

    logged_in = client.post("/api/session/login", json={"username": "local", "password": "test-only-passphrase"})
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["user"]["username"] == "local"

    me = client.get("/api/session/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "local"
    # The session DTO carries no provider-identity fields.
    assert "linked_identities" not in me.json()["user"]

    assert client.post("/api/session/logout", headers={"Origin": settings.allowed_origins_list[0]}).status_code == 204
    assert client.get("/api/session/me").status_code == 401


def test_oauth_identity_surface_absent() -> None:
    """No OAuth identity models in the module, no removed provider tables in a
    fresh schema (sqlite_master), and the user DTO exposes no provider fields —
    nothing to migrate, diagnose, or quarantine (no-legacy directive)."""
    for name in ("OAuthIdentity", "TwitchConnection", "OAuthFlowState"):
        assert not hasattr(models_module, name), f"app.models must not define {name}"

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        for name in ("oauth_identities", "twitch_connections", "oauth_flow_states"):
            assert name not in tables, f"{name} must not be created by a fresh schema"
    finally:
        engine.dispose()

    # The user DTO exposed by the API carries no provider-identity fields.
    assert "linked_identities" not in UserResponse.model_fields


def test_stored_chat_read_path_survives(db_factory, api_client) -> None:
    """The captured-chat READ path survives the removal: a completed Twitch
    live recording's captured chat is served to its owner from stored data only
    — no provider download, no connection, no OAuth — and the Load route is
    still registered (it is yt-dlp-based, not connection-based)."""
    # The Load route stays registered: POST /api/chat-replay/{source_identity}.
    load_routes = [
        route.path
        for route in app.routes
        if getattr(route, "path", "") == "/api/chat-replay/{source_identity:path}"
        and getattr(route, "methods", set()) and "POST" in getattr(route, "methods", set())
    ]
    assert load_routes == ["/api/chat-replay/{source_identity:path}"], (
        f"POST Load route must stay registered, saw: {load_routes}"
    )

    factory = db_factory
    owner = make_user("user-owner", username="owner", display_name="Owner")
    member_b = make_user("user-b", username="bob", display_name="Bob")
    with factory.begin() as session:
        session.add_all([owner, member_b])

    canonical = RemotePlaybackProgressService.canonical_source_identity(TWITCH_IDENTITY)
    asset_id = str(uuid.uuid4())
    recording_id = str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory.begin() as session:
        session.add(
            ChatReplayAsset(
                id=asset_id,
                user_id=owner.id,
                source_identity=canonical,
                source_identity_key=RemotePlaybackProgressService.source_identity_key(canonical),
                source_url=TWITCH_SOURCE_URL,
                status="ready",
                event_count=1,
                total_seen=1,
                bytes_processed=128,
                dropped_malformed=0,
                truncated=False,
                events_json=[
                    {
                        "id": "c1",
                        "offset_ms": 1500,
                        "kind": "message",
                        "text": "captured during the recording",
                        "moderation": "visible",
                        "author": {"name": "Ada", "badges": []},
                    }
                ],
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        session.add(
            LiveRecording(
                id=recording_id,
                user_id=owner.id,
                source_url=TWITCH_SOURCE_URL,
                source_identity=canonical,
                source_identity_key=RemotePlaybackProgressService.source_identity_key(canonical),
                extractor="twitch",
                title="A captured Twitch broadcast",
                status="completed",
                stop_requested=True,
                media_status="completed",
                chat_status="completed",
                chat_asset_id=asset_id,
                created_at=now,
                updated_at=now,
            )
        )

    def _forbidden(self, url, **kw):  # noqa: ANN001
        raise AssertionError("Serving a stored chat asset must never trigger a provider download")

    original = YtDlpService.download_replay_chat
    YtDlpService.download_replay_chat = _forbidden

    try:
        client = api_client(user=owner, base_url="http://localhost")
        fetched = client.get(TWITCH_ENDPOINT)
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["status"] == "ready"
        assert body["event_count"] == 1
        assert body["events"][0]["text"] == "captured during the recording"
    finally:
        YtDlpService.download_replay_chat = original

    # Ownership intact: the same identity read by another member gets nothing.
    client = api_client(user=member_b, base_url="http://localhost")
    assert client.get(TWITCH_ENDPOINT).json() is None
