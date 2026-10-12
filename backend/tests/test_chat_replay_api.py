"""Public API contract for the timed chat asset: member isolation, auth, redaction."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

import app.main as main
from app.main import app
from app.models import User
from app.security import get_current_user
from app.services.yt_dlp_service import ReplayChatFetch, YtDlpService
from support import make_user


IDENTITY = "youtube:dQw4w9WgXcQ"
ENDPOINT = f"/api/chat-replay/{quote(IDENTITY, safe='')}"
SOURCE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _line(offset, item_id, author, text, **renderer):
    base = {
        "id": item_id,
        "authorName": {"simpleText": author},
        "authorPhoto": {"thumbnails": [{"url": "https://yt3.example/secret-photo.jpg"}]},
        "message": {"runs": [{"text": text}]},
    }
    base.update(renderer)
    action = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset),
            "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": base}}}],
            "continuations": [{"liveChatReplayContinuationData": {"continuation": "SECRET_CONTINUATION_TOKEN"}}],
        }
    }
    return json.dumps(action, ensure_ascii=False).encode("utf-8")


@pytest.fixture()
def harness(monkeypatch, db_factory, api_client):
    user_a = make_user("user-a")
    with db_factory.begin() as session:
        session.add_all([user_a, make_user("user-b")])

    active = {"value": user_a}
    monkeypatch.setattr(main, "SessionLocal", db_factory)
    client = api_client(user=lambda: active["value"], base_url="http://localhost")
    yield client, active, monkeypatch


def _stub_fetch(monkeypatch, fetch: ReplayChatFetch) -> None:
    monkeypatch.setattr(YtDlpService, "download_replay_chat", lambda self, url, **kw: fetch)


def test_load_builds_member_scoped_asset_and_is_readable(harness) -> None:
    client, _active, monkeypatch = harness
    _stub_fetch(
        monkeypatch,
        ReplayChatFetch(
            status="available",
            lines=[_line(1000, "m1", "Ada", "hello"), _line(2000, "m2", "Grace", "hi there")],
        ),
    )
    loaded = client.post(ENDPOINT, json={"source_url": SOURCE_URL})
    assert loaded.status_code == 200, loaded.text
    body = loaded.json()
    assert body["status"] == "ready"
    assert body["event_count"] == 2
    assert [event["offset_ms"] for event in body["events"]] == [1000, 2000]

    fetched = client.get(ENDPOINT)
    assert fetched.status_code == 200
    assert fetched.json()["event_count"] == 2


def test_asset_never_crosses_to_another_member(harness) -> None:
    client, active, monkeypatch = harness
    _stub_fetch(monkeypatch, ReplayChatFetch(status="available", lines=[_line(1000, "m1", "Ada", "alice only")]))
    assert client.post(ENDPOINT, json={"source_url": SOURCE_URL}).status_code == 200

    active["value"] = User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True)
    # Bob has no asset for Alice's source identity.
    assert client.get(ENDPOINT).json() is None
    # Bob building his own asset for the same identity never sees Alice's events.
    _stub_fetch(monkeypatch, ReplayChatFetch(status="available", lines=[_line(9000, "b1", "Bob", "bob only")]))
    bob = client.post(ENDPOINT, json={"source_url": SOURCE_URL}).json()
    assert bob["event_count"] == 1
    assert bob["events"][0]["text"] == "bob only"


def test_anonymous_requests_are_rejected(harness) -> None:
    client, _active, monkeypatch = harness
    # Drop the auth override so the real get_current_user dependency (still
    # against the in-memory DB) rejects an unauthenticated call before any
    # build work.
    app.dependency_overrides.pop(get_current_user, None)
    _stub_fetch(monkeypatch, ReplayChatFetch(status="unavailable"))
    response = client.post(ENDPOINT, json={"source_url": SOURCE_URL})
    assert response.status_code == 401


def test_response_never_leaks_continuations_headers_or_photo_urls(harness) -> None:
    client, _active, monkeypatch = harness
    _stub_fetch(
        monkeypatch,
        ReplayChatFetch(
            status="available",
            lines=[
                _line(
                    1000,
                    "m1",
                    "Mod",
                    "keep it civil",
                    authorBadges=[{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "MODERATOR"}}}],
                    authorExternalChannelId="UC_mod",
                )
            ],
        ),
    )
    body = client.post(ENDPOINT, json={"source_url": SOURCE_URL}).text
    assert "SECRET_CONTINUATION_TOKEN" not in body
    assert "yt3.example" not in body
    assert "secret-photo" not in body
    event = client.post(ENDPOINT, json={"source_url": SOURCE_URL, "refresh": True}).json()["events"][0]
    assert event["author"]["badges"] == ["moderator"]


@pytest.mark.parametrize(
    "fetch, expected_status",
    [
        (ReplayChatFetch(status="unavailable"), "unavailable"),
        (ReplayChatFetch(status="failed"), "failed"),
        (ReplayChatFetch(status="available", lines=[]), "empty"),
        (ReplayChatFetch(status="available", lines=[b"garbage", b"{oops"]), "malformed"),
    ],
)
def test_degraded_states_return_terminal_status_without_error(harness, fetch, expected_status) -> None:
    client, _active, monkeypatch = harness
    _stub_fetch(monkeypatch, fetch)
    response = client.post(ENDPOINT, json={"source_url": SOURCE_URL})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected_status
    assert response.json()["events"] == []


def test_missing_asset_reads_as_null(harness) -> None:
    client, _active, _monkeypatch = harness
    assert client.get(ENDPOINT).json() is None


def test_load_rejects_a_source_url_that_does_not_match_the_identity(harness) -> None:
    client, _active, monkeypatch = harness
    _stub_fetch(monkeypatch, ReplayChatFetch(status="available", lines=[]))
    # The path identity is youtube:dQw4w9WgXcQ; the body URL is a different video.
    response = client.post(ENDPOINT, json={"source_url": "https://www.youtube.com/watch?v=OTHERVIDEO0"})
    assert response.status_code == 400
    assert "does not match" in response.text


TWITCH_IDENTITY = "twitch:12345"
TWITCH_ENDPOINT = f"/api/chat-replay/{quote(TWITCH_IDENTITY, safe='')}"
TWITCH_SOURCE_URL = "https://twitch.tv/coolstreamer"


def test_twitch_load_never_invokes_a_provider_replay_download(harness) -> None:
    # FORWARD-ONLY hard invariant (issue #100): loading chat for a Twitch source
    # must NEVER download a historical provider replay track (Twitch VOD rechat).
    # download_replay_chat is booby-trapped to fail the test if it is ever called.
    client, _active, monkeypatch = harness

    def _forbidden(self, url, **kw):  # noqa: ANN001
        raise AssertionError("Twitch chat replay must never trigger a provider backfill download")

    monkeypatch.setattr(YtDlpService, "download_replay_chat", _forbidden)
    response = client.post(TWITCH_ENDPOINT, json={"source_url": TWITCH_SOURCE_URL})
    assert response.status_code == 200, response.text
    body = response.json()
    # No captured asset exists, so the honest state is "unavailable" — never a
    # build, never an import.
    assert body["status"] == "unavailable"
    assert body["events"] == []


def test_captured_twitch_asset_is_served_for_its_owner_without_any_provider_download(harness) -> None:
    # Affirmative half of #100 AC6 (issue #110): completed playback of a Twitch
    # live recording surfaces the chat captured DURING that recording. The client
    # keys the recording's Library item by the same twitch:<stream_id> identity the
    # capture persisted under, so the member-scoped GET must return the captured
    # asset — and only to its owner, and never via a provider download.
    client, active, monkeypatch = harness

    def _forbidden(self, url, **kw):  # noqa: ANN001
        raise AssertionError("Serving a captured Twitch asset must never trigger a provider download")

    monkeypatch.setattr(YtDlpService, "download_replay_chat", _forbidden)

    import uuid
    from datetime import UTC, datetime

    from app.models import ChatReplayAsset
    from app.services.remote_playback import RemotePlaybackProgressService

    canonical = RemotePlaybackProgressService.canonical_source_identity(TWITCH_IDENTITY)
    now = datetime.now(UTC).replace(tzinfo=None)
    with main.SessionLocal.begin() as session:  # type: ignore[attr-defined]
        session.add(
            ChatReplayAsset(
                id=str(uuid.uuid4()),
                user_id="user-a",
                source_identity=canonical,
                source_identity_key=RemotePlaybackProgressService.source_identity_key(canonical),
                source_url=TWITCH_SOURCE_URL,
                status="ready",
                event_count=1,
                truncated=False,
                dropped_malformed=0,
                events_json=[{"id": "c1", "offset_ms": 1500, "kind": "message", "text": "captured forward-only", "moderation": "visible", "author": {"name": "Ada", "badges": []}}],
                created_at=now,
                updated_at=now,
            )
        )

    fetched = client.get(TWITCH_ENDPOINT)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["status"] == "ready"
    assert [event["text"] for event in body["events"]] == ["captured forward-only"]

    # A deliberate Load on the same identity returns the captured asset as-is
    # (no build, no import) — the booby-trapped download proves it. The shared
    # limiter is cleared first so this extra POST never starves later tests.
    from app.services.rate_limit import rate_limiter

    rate_limiter.clear()
    loaded = client.post(TWITCH_ENDPOINT, json={"source_url": TWITCH_SOURCE_URL})
    assert loaded.status_code == 200, loaded.text
    assert [event["text"] for event in loaded.json()["events"]] == ["captured forward-only"]

    # Another member never sees the owner's captured chat.
    active["value"] = User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True)
    assert client.get(TWITCH_ENDPOINT).json() is None


FORGED_YOUTUBE_IDENTITY = "youtube:FAKEVIDEO01"
FORGED_ENDPOINT = f"/api/chat-replay/{quote(FORGED_YOUTUBE_IDENTITY, safe='')}"
FORGED_TWITCH_VOD_URL = "https://www.twitch.tv/videos/123456789"


def test_youtube_identity_with_twitch_url_never_invokes_a_provider_replay_download(harness) -> None:
    # FORWARD-ONLY hard invariant (issue #100), crafted CROSS-HOST variant. The path
    # identity is a build-eligible youtube:<id>, but source_url points at a Twitch
    # VOD. The gate keys the no-backfill closure on the URL HOST, not the caller-
    # controlled identity prefix, so download_replay_chat is booby-trapped to fail
    # the test if the forged request ever reaches a provider replay download.
    client, _active, monkeypatch = harness

    def _forbidden(self, url, **kw):  # noqa: ANN001
        raise AssertionError("A Twitch-hosted source_url must never trigger a provider backfill download")

    monkeypatch.setattr(YtDlpService, "download_replay_chat", _forbidden)
    response = client.post(FORGED_ENDPOINT, json={"source_url": FORGED_TWITCH_VOD_URL})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["events"] == []
