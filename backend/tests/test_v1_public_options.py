"""S03 — the closed public-only yt-dlp option surface; the credential surface is absent.

Desired behavior after the cut (the credential surface simply does not
exist; MVP first release, no legacy data): every yt-dlp option construction
site is closed to a public, credential-free allowlist, the removed request
fields are rejected (422, not silently accepted), the saved sign-in /
auth-profile / generic live-chat API surface is absent (routes, models,
tables), and the KEEP surfaces survive: the recording coordinator's
YtDlpLiveChatFetcher-based chat capturer and the captured-chat READ path
(GET /api/chat-replay/{source_identity}) which serves stored chat to its
owner from stored data only.

Observed absence semantics (recorded): the app mounts
the SPA static files at "/" (StaticFiles(html=True)), which answers
UNREGISTERED paths — GET -> 404, POST/DELETE -> 405 — before any auth
dependency runs. Route-table inspection is the primary proof; the static
answers plus a negative control to a sibling path that never existed catch a
handler re-landing on a removed path.
"""

from __future__ import annotations

import socket
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import app.main as main
import app.models as models_module
from app.config import settings
from app.db import Base
from app.main import app
from app.models import ChatReplayAsset, LiveRecording
from app.schemas import FormatSelection, OutputProfile
from app.services import yt_dlp_service as yt_dlp_service_module
from app.services.live_chat_seam import LiveChatBootstrap
from app.services.live_recording_adapters import GuardedLiveChatCapturer, YtDlpLiveSourceProbe
from app.services.live_recording_manager import RecordingContext
from app.services.network_policy import PublicSourcePolicy
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.remote_streaming_adapters import YtDlpLiveChatFetcher
from app.services.twitch_irc_chat import ProviderLiveChatFetcher
from app.services.timed_chat import TimedChatBudget
from app.services.yt_dlp_service import PUBLIC_OPTION_ALLOWLIST, YtDlpService, _PREVIEW_CACHE, _SEARCH_CACHE
from support import make_user, memory_session_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"

TWITCH_IDENTITY = "twitch:12345"
TWITCH_SOURCE_URL = "https://twitch.tv/videos/12345"
TWITCH_ENDPOINT = f"/api/chat-replay/{quote(TWITCH_IDENTITY, safe='')}"

# The option classes the 1.0 cut forbids at every construction site: credential
# transport (cookies, netrc, username/password), client cert/key, arbitrary
# extra headers, and exec/plugin/user-executable options. Nothing on this list
# may appear in a constructed yt-dlp options dict.
CREDENTIAL_AND_EXEC_OPTION_KEYS = frozenset(
    {
        # credential transport options
        "cookiesfrombrowser",
        "cookiefile",
        "netrc",
        "netrc_location",
        "username",
        "password",
        # client certificate / key
        "ssl_cert",
        # arbitrary extra request headers
        "http_headers",
        # code execution / executable / plugin options
        "exec",
        "python_executable",
        "plugin_dirs",
    }
)

# Distinct URLs per entry point so the module-level preview/search caches can
# never short-circuit an entry point out of the options builder.
PREVIEW_URL = "https://www.youtube.com/watch?v=s03-preview"
SEARCH_QUERY = "lumina s03 public"
DOWNLOAD_URL = "https://www.youtube.com/watch?v=s03-download"
RECORD_URL = "https://www.youtube.com/watch?v=s03-record"
FOLLOW_URL = "https://www.youtube.com/@lumina-fixture"

# 93.184.216.34: a global (public, non-private) IPv4 answer so the public
# source policy's resolver passes hermetically without touching DNS.
FAKE_PUBLIC_ANSWER = (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))


def _canned_info(source_url: str, download: bool) -> dict:
    """Provider-free canned extraction results for the fake yt-dlp seam."""
    if source_url.startswith(("ytsearch", "scsearch")):
        return {
            "_type": "playlist",
            "entries": [
                {
                    "id": "s03-result-1",
                    "title": "S03 public result",
                    "uploader": "Lumina fixture",
                    "webpage_url": "https://www.youtube.com/watch?v=s03-result-1",
                    "view_count": 42,
                },
            ],
        }
    if download:
        return {
            "id": "s03-download",
            "title": "S03 download",
            "webpage_url": source_url,
            "extractor": "Youtube",
            "extractor_key": "youtube",
            "formats": [
                {"format_id": "video-720", "vcodec": "avc1", "acodec": "none", "ext": "mp4", "height": 720},
                {"format_id": "audio", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
            ],
        }
    # A currently-live single video (preview + record probe + follow).
    return {
        "id": "s03-live",
        "title": "S03 live source",
        "webpage_url": source_url,
        "uploader": "Lumina fixture",
        "extractor": "Youtube",
        "extractor_key": "youtube",
        "live_status": "is_live",
        "timestamp": 1_770_000_000,
    }


class _SpyYDL:
    """Fake yt-dlp context manager: records the options dict the factory
    received (what the app forwarded to yt-dlp) and returns canned info."""

    def __init__(self, options: dict, records: list) -> None:
        self._options = options
        records.append(dict(options))

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> bool:
        return False

    def extract_info(self, source_url: str, download: bool = False) -> dict:  # noqa: ANN001
        return _canned_info(source_url, download)

    def sanitize_info(self, info: dict) -> dict:  # noqa: ANN001
        return info


def test_public_options_never_forward_credentials(monkeypatch) -> None:
    """Fresh fixture (no credential rows exist in the product); every
    entry point — preview, search, download, record, follow — is exercised
    through the real construction path while a spy on the yt-dlp factory
    proves ZERO credential/exec options are ever constructed or forwarded.
    """
    # Hermetic seams: the factory is a recording spy, and the public source
    # policy resolves every host through a canned public answer (no DNS).
    records: list[dict] = []
    monkeypatch.setattr(
        yt_dlp_service_module,
        "PolicyYoutubeDL",
        lambda options, **_kwargs: _SpyYDL(options, records),
    )
    monkeypatch.setattr(
        yt_dlp_service_module,
        "PublicSourcePolicy",
        lambda: PublicSourcePolicy(resolver=lambda *args, **kwargs: [FAKE_PUBLIC_ANSWER]),
    )
    _PREVIEW_CACHE.clear()
    _SEARCH_CACHE.clear()

    factory = memory_session_factory()
    service = YtDlpService(factory())  # constructed through the patched seams

    def assert_only_public(options_list: list[dict], label: str) -> None:
        assert options_list, f"{label}: the options builder was never exercised"
        for options in options_list:
            blocked = sorted(set(options) & CREDENTIAL_AND_EXEC_OPTION_KEYS)
            assert blocked == [], f"{label}: credential/exec options constructed: {blocked}"
            outside = sorted(set(options) - PUBLIC_OPTION_ALLOWLIST)
            assert outside == [], f"{label}: options outside the public-only allowlist: {outside}"

    # preview: POST /api/preview delegates to YtDlpService.preview.
    before = len(records)
    preview = service.preview(PREVIEW_URL, lazy_playlist=False)
    assert preview.kind == "video"
    assert_only_public(records[before:], "preview")

    # search: source_search runs both provider extractions.
    before = len(records)
    searched = service.source_search(SEARCH_QUERY, limit=4)
    assert len(searched.items) >= 1
    assert_only_public(records[before:], "search")

    # download: the acquisition builder + extraction seam.
    before = len(records)
    result = service.download(
        DOWNLOAD_URL,
        format_selection=FormatSelection(preset="best_1080p", output_container="mp4"),
        output_profile=OutputProfile(organize_by="downloads"),
        owner_user_id="user-s03",
        output_root=settings.temp_root,
        progress_hooks=[],
        postprocessor_hooks=[],
    )
    assert result.info.get("id") == "s03-download"
    assert_only_public(records[before:], "download")

    # record: the recording waiter probes a scheduled source through the same
    # guarded preview seam the coordinator wires (YtDlpLiveSourceProbe).
    before = len(records)
    session_factory = memory_session_factory()
    events = threading.Event()
    ctx = RecordingContext(
        recording_id="rec-s03",
        user_id="user-s03",
        source_url=RECORD_URL,
        source_identity="youtube:rec-s03",
        format_selection={},
        output_profile={},
        offset_base=None,
        resume=False,
        session_factory=session_factory,
        _stop=events,
        _cancel=events,
        _either=events,
    )
    probe = YtDlpLiveSourceProbe(session_factory=session_factory)
    live = probe.probe(ctx)
    assert live.state == "live", f"the record probe must see the live source, got: {live.state}"
    assert_only_public(records[before:], "record")

    # follow: the followed-channel poll probes through probe_live_source.
    before = len(records)
    followed = service.probe_live_source(FOLLOW_URL)
    assert followed is not None, "the follow probe must classify the live channel"
    assert followed.capabilities is not None and followed.capabilities.lifecycle == "live"
    assert_only_public(records[before:], "follow")

    # Every options dict the spy saw across all five entry points is clean.
    assert len(records) >= 5, f"expected at least one options build per entry point, saw {len(records)}"
    assert_only_public(records, "all entry points")


def test_removed_credential_fields_rejected_422(db_factory, api_client) -> None:
    """The removed request fields are rejected with 422 (reject, don't
    silently accept) at every endpoint that carried one:
    POST /api/preview, POST /api/live-recordings, POST /api/jobs,
    POST /api/acquisition-batches, PUT /api/settings/me (top-level
    active_auth_profile_id + the download/automation defaults),
    POST /api/automations (create/update base), and the surviving
    POST /api/chat-replay/{source_identity} Load route. The credential
    inputs for cookie files and username/password had their only request
    surface (the auth-profile CRUD) removed entirely — covered by
    test_auth_profile_surface_absent.
    """
    member = make_user("user-s03", username="s03", display_name="S03 Check", role="admin")
    with db_factory.begin() as session:
        session.add(member)
    client = api_client(user=member, base_url="http://localhost")
    cases = (
        ("POST", "/api/preview", {"source_url": PREVIEW_URL, "auth_profile_id": "removed"}, "auth_profile_id"),
        ("POST", "/api/live-recordings", {"source_url": RECORD_URL, "auth_profile_id": "removed"}, "auth_profile_id"),
        ("POST", "/api/jobs", {"source_url": DOWNLOAD_URL, "auth_profile_id": "removed"}, "auth_profile_id"),
        (
            "POST",
            "/api/acquisition-batches",
            {"source_url": DOWNLOAD_URL, "entries": [{"source_url": DOWNLOAD_URL}], "auth_profile_id": "removed"},
            "auth_profile_id",
        ),
        ("PUT", "/api/settings/me", {"download_defaults": {"auth_profile_id": "removed"}}, "auth_profile_id"),
        ("PUT", "/api/settings/me", {"automation_defaults": {"auth_profile_id": "removed"}}, "auth_profile_id"),
        ("PUT", "/api/settings/me", {"active_auth_profile_id": "removed"}, "active_auth_profile_id"),
        ("POST", "/api/automations", {"label": "s03", "source_url": RECORD_URL, "auth_profile_id": "removed"}, "auth_profile_id"),
        ("POST", f"/api/chat-replay/{quote(TWITCH_IDENTITY, safe='')}", {"source_url": TWITCH_SOURCE_URL, "auth_profile_id": "removed"}, "auth_profile_id"),
    )
    for method, path, body, removed_field in cases:
        response = client.request(method, path, json=body)
        assert response.status_code == 422, (path, response.status_code, response.text)
        detail = response.json().get("detail")
        locs = [error.get("loc") for error in detail] if isinstance(detail, list) else []
        assert any(isinstance(loc, list) and removed_field in loc for loc in locs), (path, detail)


def test_auth_profile_surface_absent() -> None:
    """The saved sign-in surface is simply not part of the product: no
    /api/auth-profiles* routes, no AuthProfile model, and a fresh schema
    creates no auth-profile / secret / live-chat tables. (Live chat viewing came
    back in 2.1.2 as GET /api/live-chat, in memory, with no tables.)
    """
    # Route table: the removed route families are gone from the router.
    removed_paths = sorted(
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/auth-profiles")
    )
    assert removed_paths == [], f"removed route families re-landed: {removed_paths}"

    # The app module must not wire the removed service surface.
    main_source = (BACKEND_APP / "main.py").read_text(encoding="utf-8")
    for name in ("AuthProfileService", "LiveChatService", "secret_storage"):
        assert name not in main_source, f"main.py still references the removed surface: {name}"

    # SPA static-mount semantics: the mount answers the removed paths with
    # 404 (GET) / 405 (POST, DELETE) before any handler could run; a
    # surviving handler would answer with API semantics instead. The negative
    # control proves the 405 is the static mount, not app routing.
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/definitely-not-a-route").status_code == 405
    for path in ("/api/auth-profiles", "/api/auth-profiles/profile-1", "/api/live-chat/session-1/events"):
        assert client.get(path).status_code == 404, path
    for path in ("/api/auth-profiles", "/api/live-chat", "/api/live-chat/session-1"):
        assert client.post(path).status_code == 405, path
    for path in ("/api/auth-profiles/profile-1", "/api/live-chat/session-1"):
        assert client.delete(path).status_code == 405, path

    # The model is not part of the product schema.
    assert not hasattr(models_module, "AuthProfile")

    # A fresh create_all creates no auth-profile / secret / live-chat tables.
    engine = create_engine("sqlite://", future=True)
    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as connection:
            tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))}
        assert "auth_profiles" not in tables, tables
        assert not any("secret" in table for table in tables), tables
        assert not any("live_chat" in table for table in tables), tables
    finally:
        engine.dispose()


def test_recording_chat_capture_and_chat_replay_survives(db_factory, api_client) -> None:
    """The KEEP surfaces survived the cut: the recording coordinator wires
    the YtDlpLiveChatFetcher-based capturer, the fetcher bootstrap fails
    closed without a provider call, and the captured-chat READ path serves
    the live recording's captured chat to its owner from stored data only
    (mirrors test_v1_public_auth.py::test_stored_chat_read_path_survives).
    """
    # 1. The recording coordinator wires the YtDlp-based chat capturer.
    capturer = main.live_recording_manager.chat_capturer
    assert isinstance(capturer, GuardedLiveChatCapturer)
    assert isinstance(capturer._fetcher, ProviderLiveChatFetcher)
    assert isinstance(capturer._fetcher._youtube, YtDlpLiveChatFetcher)
    assert isinstance(capturer._budget, TimedChatBudget)

    # 2. The fetcher bootstrap fails closed (no provider call) when the
    #    public preview seam fails: an honest "unavailable" state.
    def _denied(self, source_url: str) -> dict:  # noqa: ANN001
        raise AssertionError("bootstrap must not reach the provider network in tests")

    original = YtDlpService.resolve_remote_playback
    YtDlpService.resolve_remote_playback = _denied
    try:
        boot = capturer._fetcher.bootstrap("https://www.youtube.com/watch?v=x", "user-s03")
        assert isinstance(boot, LiveChatBootstrap)
        assert boot.status == "unavailable"
    finally:
        YtDlpService.resolve_remote_playback = original

    # 3. The captured-chat READ path: the Load route stays registered and the
    #    stored asset is served to its owner without any provider download.
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

    original_download = YtDlpService.download_replay_chat
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
        YtDlpService.download_replay_chat = original_download

    # Ownership intact: the same identity read by another member gets nothing.
    assert api_client(user=member_b, base_url="http://localhost").get(TWITCH_ENDPOINT).json() is None
