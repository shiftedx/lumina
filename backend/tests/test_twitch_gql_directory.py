from __future__ import annotations

import httpx
import pytest

from app.services.twitch_gql_directory import (
    TwitchDirectoryError,
    TwitchGqlDirectory,
    TwitchLiveStream,
)


def _node(login: str, viewers: int | None = 1200, game: str | None = "Fortnite") -> dict:
    return {
        "node": {
            "title": f"{login} plays",
            "viewersCount": viewers,
            "previewImageURL": f"https://static-cdn.jtvnw.net/{login}.jpg",
            "broadcaster": {"login": login, "displayName": login.title()},
            "game": {"displayName": game} if game else None,
        }
    }


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://gql.twitch.tv")


def test_top_streams_parses_nodes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://gql.twitch.tv/gql")
        assert request.headers["Client-ID"]
        return httpx.Response(200, json={"data": {"streams": {"edges": [_node("pixel"), _node("mira", viewers=None, game=None)]}}})

    directory = TwitchGqlDirectory(http_client=_client(handler))
    streams = directory.top_streams(8)
    assert streams == [
        TwitchLiveStream(login="pixel", display_name="Pixel", title="pixel plays", viewers_count=1200, preview_image_url="https://static-cdn.jtvnw.net/pixel.jpg", category_name="Fortnite"),
        TwitchLiveStream(login="mira", display_name="Mira", title="mira plays", viewers_count=None, preview_image_url="https://static-cdn.jtvnw.net/mira.jpg", category_name=None),
    ]


def test_game_streams_targets_named_game() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        assert payload["variables"] == {"name": "Music", "limit": 4, "after": None}
        return httpx.Response(200, json={"data": {"game": {"streams": {"edges": [_node("cello", game="Music")]}}}})

    directory = TwitchGqlDirectory(http_client=_client(handler))
    assert [s.login for s in directory.game_streams("Music", 4)] == ["cello"]


def test_http_error_raises_redacted_domain_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream detail that must not leak")

    directory = TwitchGqlDirectory(http_client=_client(handler))
    with pytest.raises(TwitchDirectoryError) as excinfo:
        directory.top_streams(8)
    assert "upstream detail" not in str(excinfo.value)


def test_schema_drift_raises_and_logs_once(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"streams": {"totally": "different"}}})

    directory = TwitchGqlDirectory(http_client=_client(handler))
    with caplog.at_level("WARNING"):
        with pytest.raises(TwitchDirectoryError):
            directory.top_streams(8)
        with pytest.raises(TwitchDirectoryError):
            directory.top_streams(8)
    drift_records = [r for r in caplog.records if "drift" in r.message.lower()]
    assert len(drift_records) == 1


def test_unknown_game_returns_empty_not_error(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"game": None}})

    directory = TwitchGqlDirectory(http_client=_client(handler))
    with caplog.at_level("WARNING"):
        assert directory.game_streams("Nonexistent", 8) == []
    drift_records = [r for r in caplog.records if "drift" in r.message.lower()]
    assert drift_records == []


def test_unknown_game_does_not_consume_drift_log_slot(caplog) -> None:
    def unknown_game_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"game": None}})

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"streams": {"totally": "different"}}})

    directory = TwitchGqlDirectory(http_client=_client(unknown_game_handler))
    with caplog.at_level("WARNING"):
        assert directory.game_streams("Nonexistent", 8) == []

    directory._http_client = _client(malformed_handler)
    with caplog.at_level("WARNING"):
        with pytest.raises(TwitchDirectoryError):
            directory.top_streams(8)
    drift_records = [r for r in caplog.records if "drift" in r.message.lower()]
    assert len(drift_records) == 1


def test_malformed_edges_are_skipped_not_fatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"streams": {"edges": [
            "not-a-dict",
            {"node": {"broadcaster": {}}},
            _node("valid"),
        ]}}})

    directory = TwitchGqlDirectory(http_client=_client(handler))
    assert [s.login for s in directory.top_streams(8)] == ["valid"]


def test_game_page_returns_cursor_and_broadcaster_count() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["variables"] == {"name": "Music", "limit": 100, "after": "c0"}
        edges = [{"cursor": "c1", **_node("a", game="Music")}, {"cursor": "c2", **_node("b", game="Music")}]
        return httpx.Response(200, json={"data": {"game": {"broadcastersCount": 495, "streams": {"pageInfo": {"hasNextPage": True}, "edges": edges}}}})

    page = TwitchGqlDirectory(http_client=_client(handler)).game_page("Music", 500, "c0")  # capped at 100 per page
    assert [s.login for s in page.streams] == ["a", "b"]
    assert (page.next_cursor, page.live_count) == ("c2", 495)


def test_last_page_has_no_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"streams": {"pageInfo": {"hasNextPage": False}, "edges": [{"cursor": "z", **_node("a")}]}}})

    assert TwitchGqlDirectory(http_client=_client(handler)).top_page(100).next_cursor is None


def test_top_games_lists_broadcaster_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        nodes = [{"node": {"name": "Just Chatting", "broadcastersCount": 5000}}, {"node": {"name": "Bad"}}, {"node": {"name": "Apex", "broadcastersCount": 2400}}]
        return httpx.Response(200, json={"data": {"games": {"edges": nodes}}})

    assert TwitchGqlDirectory(http_client=_client(handler)).top_games() == [("Just Chatting", 5000), ("Apex", 2400)]


def test_rate_limit_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(TwitchDirectoryError) as excinfo:
        TwitchGqlDirectory(http_client=_client(handler)).top_streams(8)
    assert excinfo.value.retry_after == 120
