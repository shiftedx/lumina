import asyncio

import pytest

import app.main as main
from app.models import User
from app.schemas import YouTubeSearchRequest, YouTubeSearchResponse, YouTubeSearchResult
from app.services.yt_dlp_service import YtDlpService


def make_user() -> User:
    return User(id="user-1", username="tester", display_name="Tester", password_hash="disabled", role="viewer", is_active=True)


def test_youtube_search_route_returns_normalized_results(monkeypatch) -> None:
    monkeypatch.setattr(main, "_annotate_in_thread", lambda user, entries: entries)  # annotation has its own tests
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: make_user())
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement

    def fake_search(self, query: str, limit: int = 10) -> YouTubeSearchResponse:  # noqa: ARG001
        return YouTubeSearchResponse(
            query=query,
            items=[
                YouTubeSearchResult(
                    id="abc123",
                    title="Example",
                    uploader="Creator",
                    duration=120,
                    thumbnail="https://example.com/thumb.jpg",
                    webpage_url="https://www.youtube.com/watch?v=abc123",
                    view_count=42,
                    availability="public",
                )
            ]
        )

    monkeypatch.setattr(YtDlpService, "youtube_search", fake_search)

    response = asyncio.run(main.youtube_search(YouTubeSearchRequest(query="example"), request=None, credentials=None))

    assert response.items[0].id == "abc123"
    assert response.items[0].webpage_url == "https://www.youtube.com/watch?v=abc123"


def test_youtube_search_route_rejects_blank_query(monkeypatch) -> None:
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: make_user())
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement

    def fake_search(self, query: str, limit: int = 10):  # noqa: ARG001
        raise ValueError("Enter a YouTube search query.")

    monkeypatch.setattr(YtDlpService, "youtube_search", fake_search)

    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.youtube_search(YouTubeSearchRequest(query="   "), request=None, credentials=None))

    assert exc.value.status_code == 400
    assert "YouTube search query" in str(exc.value.detail)


def test_source_search_route_returns_mixed_results(monkeypatch) -> None:
    monkeypatch.setattr(main, "_annotate_in_thread", lambda user, entries: entries)  # annotation has its own tests
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: make_user())
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement

    def fake_search(self, query: str, limit: int = 10) -> YouTubeSearchResponse:  # noqa: ARG001
        return YouTubeSearchResponse(
            query=query,
            items=[
                YouTubeSearchResult(
                    id="track-1",
                    title="SoundCloud Example",
                    uploader="Artist",
                    duration=180,
                    thumbnail="https://example.com/thumb.jpg",
                    webpage_url="https://soundcloud.com/demo/track-1",
                    source="soundcloud",
                    source_label="SoundCloud",
                )
            ],
        )

    monkeypatch.setattr(YtDlpService, "source_search", fake_search)

    response = asyncio.run(main.source_search(YouTubeSearchRequest(query="example"), request=None, credentials=None))

    assert response.items[0].source == "soundcloud"
    assert response.items[0].source_label == "SoundCloud"


def test_source_search_route_rejects_blank_query(monkeypatch) -> None:
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: make_user())
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement

    def fake_search(self, query: str, limit: int = 10):  # noqa: ARG001
        raise ValueError("Enter a search query.")

    monkeypatch.setattr(YtDlpService, "source_search", fake_search)

    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.source_search(YouTubeSearchRequest(query="   "), request=None, credentials=None))

    assert exc.value.status_code == 400
    assert "search query" in str(exc.value.detail)
