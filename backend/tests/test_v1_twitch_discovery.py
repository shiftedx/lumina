"""Twitch discovery stays public (no auth) and degrades to honest partial results."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
import yt_dlp

from app.schemas import YouTubeSearchResponse, YouTubeSearchResult
from app.services.followed_live import FollowedLiveChecker
from app.services.live_discovery import LiveSearch
from app.services.twitch_gql_directory import TwitchGqlDirectory
from app.services.yt_dlp_service import YtDlpService


def _node(login, title="Stream", preview="https://static-cdn.jtvnw.net/previews-ttv/live_user_x-640x360.jpg", game="Art"):
    return {"node": {
        "title": title, "viewersCount": 10,
        "previewImageURL": preview,
        "broadcaster": {"login": login, "displayName": login.title() if isinstance(login, str) else None},
        "game": {"displayName": game},
    }}


def _directory(responder, requests):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responder(json.loads(request.content))

    return TwitchGqlDirectory(http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_twitch_public_directory_no_auth() -> None:
    requests: list[httpx.Request] = []
    directory = _directory(lambda body: httpx.Response(200, json={"data": {"streams": {"edges": [_node("artist")]}}}), requests)

    streams = directory.top_streams(4)

    assert [stream.login for stream in streams] == ["artist"]
    assert requests and all(request.url.host == "gql.twitch.tv" for request in requests)
    for request in requests:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "helix" not in str(request.url) and "id.twitch.tv" not in str(request.url)


def test_twitch_schema_drift_partial() -> None:
    def responder(body):
        name = body["variables"].get("name")
        if name == "Just Chatting":
            return httpx.Response(200, json={"data": {"game": {"streams": {"edges": [
                _node("chatter"),
                _node("chatter2", title={"drift": True}),  # changed field type -> coerced, not a crash
                _node("../evil"),  # unsafe login -> dropped
                _node("imgspoof", preview="https://attacker.example/x.jpg"),  # unsafe artwork -> dropped
                {"node": "not-a-dict"},
            ]}}}})
        return httpx.Response(200, json={"data": {"game": {"streamz": []}}})  # "Art" drifted shape

    live = LiveSearch(
        lambda query, limit: YouTubeSearchResponse(query=query, items=[]),
        _directory(responder, []),
    )

    response = live("irl creative stream", 8)

    by_login = {item.uploader_id: item for item in response.items}
    assert set(by_login) == {"chatter", "chatter2", "imgspoof"}
    assert by_login["chatter2"].title is None
    assert by_login["imgspoof"].thumbnail is None
    assert by_login["chatter"].webpage_url == "https://www.twitch.tv/chatter"
    assert live.health.available is False  # the drifted directory is reported, not hidden


def test_twitch_follow_survives_outage() -> None:
    live_entry = YouTubeSearchResult(id="twitch:artist", title="On air", webpage_url="https://www.twitch.tv/artist", source="twitch")
    answers: list = [live_entry, RuntimeError("gql.twitch.tv unreachable")]

    def probe(url):  # noqa: ANN001, ARG001
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    class Inline:
        def submit(self, fn, *args):  # noqa: ANN001
            fn(*args)

    now = [0.0]
    checker = FollowedLiveChecker(probe, ttl_seconds=10, clock=lambda: now[0], executor=Inline())
    follows = ["https://www.twitch.tv/artist"]
    checker.live_entries(follows)  # first probe: live
    now[0] = 60
    checker.live_entries(follows)  # second probe: outage
    assert checker.live_entries(follows) == [live_entry]  # last-known status retained

    # The production probe separates "provider says not live" from an outage.
    def service_raising(exc):
        service = YtDlpService.__new__(YtDlpService)
        service.build_base_options = lambda: {}

        def extract(*_args, **_kwargs):
            raise exc

        service._extract_with_options = extract
        return service

    try:
        raise yt_dlp.utils.UserNotLive(video_id="artist")
    except yt_dlp.utils.UserNotLive:
        not_live = yt_dlp.utils.DownloadError("not live", sys.exc_info())
    assert service_raising(not_live).probe_live_source("https://www.twitch.tv/artist") is None
    with pytest.raises(yt_dlp.utils.DownloadError):
        service_raising(yt_dlp.utils.DownloadError("Unable to download JSON metadata")).probe_live_source(
            "https://www.twitch.tv/artist"
        )
