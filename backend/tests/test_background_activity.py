"""Authenticated HTTP activity pauses optional work for the complete response lifetime."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import events as events_module, http_activity, main as main_module
from app.http_activity import AuthenticatedActivityMiddleware
from app.models import DeviceToken
from app.security import hash_password
from app.services import connected_apps
from app.services.background_work import BackgroundWork, REQUEST_MARKER, background_work
from support import make_user, seed_app_settings

PASSWORD = "Test-only-passphrase-1"
MEMBER_ID = "7d1e2f30-2222-4b5c-8d9e-0f1a2b3c4d02"


def _spied_activity(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    events: list[tuple[str, int]] = []
    real_begin, real_end = background_work.begin_request, background_work.end_request

    def begin(scope) -> None:  # noqa: ANN001
        marked = scope.get("state", {}).get(REQUEST_MARKER, False)
        real_begin(scope)
        if not marked and scope.get("state", {}).get(REQUEST_MARKER, False):
            events.append(("begin", id(scope)))

    def end(scope) -> None:  # noqa: ANN001
        marked = scope.get("state", {}).get(REQUEST_MARKER, False)
        real_end(scope)
        if marked:
            events.append(("end", id(scope)))

    monkeypatch.setattr(background_work, "begin_request", begin)
    monkeypatch.setattr(background_work, "end_request", end)
    return events


def _jellyfin_header(token: str | None = None) -> dict[str, str]:
    values = ['Client="ActivityTest"', 'Device="Test"', 'DeviceId="activity-test"', 'Version="1"']
    if token:
        values.append(f'Token="{token}"')
    return {"Authorization": "MediaBrowser " + ", ".join(values)}


def test_only_successfully_authenticated_app_and_jellyfin_requests_mark_activity(
    db_factory, api_client, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    with db_factory.begin() as db:
        db.add(make_user(MEMBER_ID, username="member", password_hash=hash_password(PASSWORD)))
    with db_factory() as db:
        seed_app_settings(db, jellyfin_enabled=True)
    events = _spied_activity(monkeypatch)
    client = api_client(base_url="http://localhost")

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/session/me").status_code == 401
    assert client.options(
        "/api/session/me", headers={"Origin": "http://localhost", "Access-Control-Request-Method": "GET"},
    ).status_code < 500
    assert events == []

    login = client.post("/api/session/login", json={"username": "member", "password": PASSWORD})
    assert login.status_code == 200
    assert events == []  # proving credentials is not yet an authenticated API request
    assert client.get("/api/session/me").status_code == 200
    assert [name for name, _ in events] == ["begin", "end"]
    assert events[0][1] == events[1][1]

    events.clear()
    jellyfin_client = api_client(base_url="http://localhost")
    jellyfin_login = jellyfin_client.post(
        "/users/authenticatebyname", json={"Username": "member", "Pw": PASSWORD}, headers=_jellyfin_header(),
    )
    assert jellyfin_login.status_code == 200
    assert events == []
    token = jellyfin_login.json()["AccessToken"]
    assert jellyfin_client.get("/users/me", headers=_jellyfin_header(token)).status_code == 200
    assert [name for name, _ in events] == ["begin", "end"]
    assert events[0][1] == events[1][1]


def test_optimized_jellyfin_stream_resolution_marks_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _spied_activity(monkeypatch)
    user = make_user("member")
    token = DeviceToken(id="app", user_id=user.id, kind="jellyfin", scope="write", token_digest="digest")

    class Query:
        def select_from(self, *_args):
            return self

        def outerjoin(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def one_or_none(self):
            return True, token, user

    class DB:
        def query(self, *_args):
            return Query()

    request = Request({
        "type": "http", "method": "GET", "path": "/videos/item/stream", "raw_path": b"/videos/item/stream",
        "query_string": b"", "headers": [(b"x-emby-token", b"presented")], "path_params": {},
        "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http",
    })
    monkeypatch.setattr(connected_apps, "validate_device_token_record", lambda *_args: (token, user))
    monkeypatch.setattr(connected_apps, "_jellyfin_snapshot", lambda *_args, **_kwargs: user)

    assert connected_apps.jellyfin_stream_user(request, "item", DB()).user is user
    assert [name for name, _ in events] == ["begin"]
    background_work.end_request(request.scope)
    assert [name for name, _ in events] == ["begin", "end"]


def test_event_stream_marks_initial_auth_but_not_periodic_revalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    user = make_user("event-member")
    auth_calls: list[bool] = []
    marked: list[int] = []

    def authenticate(request: Request, _credentials=None, *, mark_activity: bool = True) -> object:  # noqa: ANN001
        auth_calls.append(mark_activity)
        if mark_activity:
            main_module.background_work.begin_request(request.scope)
        return user

    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", authenticate)
    monkeypatch.setattr(main_module.background_work, "begin_request", lambda scope: marked.append(id(scope)))
    monkeypatch.setattr(events_module, "REVALIDATE_SECONDS", 0)
    request = Request({
        "type": "http", "method": "GET", "path": "/api/events", "raw_path": b"/api/events",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
        "server": ("testserver", 80), "scheme": "http",
    })

    async def scenario() -> None:
        response = await main_module.events_endpoint(request, None)
        assert await anext(response.body_iterator) == "event: connected\ndata: {}\n\n"
        assert await anext(response.body_iterator) == ": keepalive\n\n"
        await response.body_iterator.aclose()

    asyncio.run(scenario())
    assert auth_calls == [True, False]
    assert marked == [id(request.scope)]


def test_direct_web_media_marks_after_auth_and_holds_through_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _spied_activity(monkeypatch)
    user = make_user("media-member")
    media = tmp_path / "sample.bin"
    media.write_bytes(b"direct-media")
    item = SimpleNamespace(id="item")
    monkeypatch.setattr(main_module, "resolve_user_from_request", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(main_module.member_access, "carry_access", lambda _db, snapshot, _user: snapshot)
    monkeypatch.setattr(main_module, "visible_item_or_404", lambda *_args: item)
    monkeypatch.setattr(main_module.LibraryService, "resolve_media_path", lambda _service, _item: media)
    monkeypatch.setattr(main_module.screen_time, "enforce", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.activity, "guard", lambda *_args: None)
    monkeypatch.setattr(main_module.activity, "touch", lambda *_args: None)
    request = Request({
        "type": "http", "method": "GET", "path": "/api/library/item/media",
        "raw_path": b"/api/library/item/media", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http",
    })

    response = main_module.stream_library_item("item", request, None)
    assert [name for name, _ in events] == ["begin"]

    async def send(message) -> None:  # noqa: ANN001
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            assert [name for name, _ in events] == ["begin"]

    asyncio.run(AuthenticatedActivityMiddleware(response)(request.scope, None, send))
    assert [name for name, _ in events] == ["begin", "end"]

    events.clear()
    denied = Request({
        "type": "http", "method": "GET", "path": "/api/library/item/media",
        "raw_path": b"/api/library/item/media", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http",
    })

    def reject(*_args, **_kwargs) -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(main_module, "resolve_user_from_request", reject)
    with pytest.raises(HTTPException) as exc_info:
        main_module.stream_library_item("item", denied, None)
    assert exc_info.value.status_code == 401
    assert events == []


@pytest.mark.parametrize(("method", "content_type", "releases_at_start"), [
    ("GET", b"text/event-stream; charset=utf-8", True),
    ("POST", b"text/event-stream; charset=utf-8", False),
    ("GET", b"video/mp4", False),
])
def test_activity_middleware_releases_sse_but_holds_ordinary_streams(
    monkeypatch: pytest.MonkeyPatch, method: str, content_type: bytes, releases_at_start: bool,
) -> None:
    clock = [100.0]
    monkeypatch.setattr("app.services.background_work.time.monotonic", lambda: clock[0])
    gate = BackgroundWork()
    monkeypatch.setattr(http_activity, "background_work", gate)

    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        messages: list[dict] = []
        # A client request header cannot exempt an ordinary stream; only the
        # response generated by the server determines the SSE release.
        scope = {"type": "http", "method": method, "headers": [(b"accept", b"text/event-stream")]}
        gate.begin_request(scope)

        async def downstream(_scope, _receive, send) -> None:  # noqa: ANN001
            await send({
                "type": "http.response.start", "status": 200,
                "headers": [(b"content-type", content_type)],
            })
            started.set()
            await release.wait()
            await send({"type": "http.response.body", "body": b"done", "more_body": False})

        task = asyncio.create_task(
            AuthenticatedActivityMiddleware(downstream)(scope, None, lambda message: _capture(messages, message)),
        )
        await started.wait()
        assert gate.busy()
        clock[0] += 30
        assert gate.busy() is (not releases_at_start)
        assert not task.done()
        release.set()
        await task
        assert [message["type"] for message in messages] == ["http.response.start", "http.response.body"]

    asyncio.run(scenario())


async def _capture(messages: list[dict], message: dict) -> None:
    messages.append(message)


def test_activity_middleware_balances_cancelled_requests_and_ignores_non_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ended: list[str] = []

    async def cancelled(_scope, _receive, _send) -> None:  # noqa: ANN001
        raise asyncio.CancelledError

    monkeypatch.setattr(background_work, "end_request", lambda _scope: ended.append("end"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(AuthenticatedActivityMiddleware(cancelled)({"type": "http", "state": {}}, None, None))
    assert ended == ["end"]

    seen: list[str] = []

    async def lifespan(scope, _receive, _send) -> None:  # noqa: ANN001
        seen.append(scope["type"])

    asyncio.run(AuthenticatedActivityMiddleware(lifespan)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
    assert ended == ["end"]
