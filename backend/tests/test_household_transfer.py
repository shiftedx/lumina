"""First-owner household transfer: cheap discovery and one-pass, request-scoped streaming import."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import contextmanager

import pytest

from app.db import session_scope
from app.media_schemas import HouseholdTransferRequest
from app.models import AccountToken, AppSettings, User
from app.persistence import writer_admission_lock
from app.routers import household_transfer
from app.services import jellyfin_household, plex_history_client
from app.services.jellyfin_history_client import JellyfinEntry, JellyfinHistory, JellyfinImportError, JellyfinUser
from app.services.rate_limit import RATE_LIMIT_RULES
from support import make_user, seed_app_settings

URL = "/api/admin/household-transfer"
OWNER = "owner"
TOKEN = "plex-owner-token"
EMPTY = JellyfinHistory([], {})


@pytest.fixture
def vault(db_factory):  # noqa: ANN001, ANN201
    with db_factory() as db:
        db.add_all([
            make_user(OWNER, role="admin", username="root"),
            make_user("existing-alice", username="alice"),
        ])
        seed_app_settings(
            db, plex_import_url="http://plex.test:32400", jellyfin_import_url="http://jellyfin.test:8096",
        )
    return db_factory


def as_user(api_client, factory, user_id: str = OWNER):  # noqa: ANN001, ANN201
    with factory() as db:
        user = db.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def events(response) -> list[dict]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


def test_discovery_is_history_free_and_import_reads_only_selected_member_once(
    vault, api_client, monkeypatch,
) -> None:  # noqa: ANN001
    users = [JellyfinUser("p-owner", "Plex Root"), JellyfinUser("p-alice", "Alice"), JellyfinUser("p-bob", "Bob")]
    households, reads = [], []

    def household(server: str, token: str):  # noqa: ANN202
        households.append((server, token))
        return "p-owner", users, {user.id: f"token-{user.id}" for user in users}

    def read(server: str, token: str) -> JellyfinHistory:
        lock_available = []

        def probe_writer_slot() -> None:
            acquired = writer_admission_lock.acquire(blocking=False)
            lock_available.append(acquired)
            if acquired:
                writer_admission_lock.release()

        probe = threading.Thread(target=probe_writer_slot)
        probe.start()
        probe.join(1)
        assert lock_available == [True], "a network read ran while Lumina held its writer slot"
        reads.append((server, token))
        return JellyfinHistory([
            JellyfinEntry(f"private-{number}", "Movie", f"Private title {number}") for number in range(200)
        ], {})

    monkeypatch.setattr(plex_history_client, "household", household)
    monkeypatch.setattr(plex_history_client, "read_history", read)
    admin = as_user(api_client, vault)

    discovered = admin.post(f"{URL}/plex/discover", json={"token": TOKEN})
    assert discovered.status_code == 200
    assert [(row["source_name"], row["action"], row["lumina_username"]) for row in discovered.json()["members"]] == [
        ("Plex Root", "import", "root"), ("Alice", "import", "alice"), ("Bob", "create", "bob"),
    ]
    assert reads == []

    streamed = events(admin.post(
        f"{URL}/plex/import", json={"token": TOKEN, "member_ids": ["p-bob"]},
    ))
    assert [event["type"] for event in streamed] == ["connecting", "progress", "member", "complete"]
    assert streamed[1] == {
        "type": "progress", "source_id": "p-bob", "source_name": "Bob", "completed": 0, "total": 1,
    }
    assert streamed[2]["member"]["source_name"] == "Bob"
    assert "/#reset=" in streamed[2]["member"]["reset_url"]
    assert streamed[2]["member"]["summary"]["unmatched"] == 200
    assert streamed[2]["member"]["summary"]["unmatched_names"] == []
    assert "Private title" not in json.dumps(streamed[2]) and len(json.dumps(streamed[2])) < 800
    assert streamed[-1] == {"type": "complete", "completed": 1, "total": 1}
    assert reads == [("http://plex.test:32400", "token-p-bob")]
    assert len(households) == 2  # one proof per request, never one per member


def test_missing_plex_token_is_visible_during_discovery_without_a_history_read(
    vault, api_client, monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(plex_history_client, "household", lambda *_: (
        "p-owner", [JellyfinUser("p-owner", "Root"), JellyfinUser("p-alice", "Alice")], {"p-owner": "owner-token"},
    ))
    monkeypatch.setattr(plex_history_client, "read_history", lambda *_: pytest.fail("discovery read history"))

    response = as_user(api_client, vault).post(f"{URL}/plex/discover", json={"token": TOKEN})
    assert response.status_code == 200
    alice = response.json()["members"][1]
    assert alice["source_id"] == "p-alice"
    assert alice["error"] == "Plex did not provide a server token for this user."


def test_stream_keeps_earlier_reset_link_when_a_later_member_read_fails(
    vault, api_client, monkeypatch,
) -> None:  # noqa: ANN001
    users = [JellyfinUser("j-owner", "Root"), JellyfinUser("j-carol", "Carol"), JellyfinUser("j-dave", "Dave")]
    closed = []

    @contextmanager
    def signed_in(*_):  # noqa: ANN002, ANN202
        try:
            yield object(), "j-owner"
        finally:
            closed.append(True)

    def read(_session, user_id: str) -> JellyfinHistory:  # noqa: ANN001
        if user_id == "j-dave":
            raise JellyfinImportError("Jellyfin history could not be read for this member.")
        return EMPTY

    monkeypatch.setattr(household_transfer, "admin_session", signed_in)
    monkeypatch.setattr(household_transfer, "list_users", lambda _: users)
    monkeypatch.setattr(jellyfin_household, "read_history", read)

    streamed = events(as_user(api_client, vault).post(
        f"{URL}/jellyfin/import",
        json={"username": "source-owner", "password": "secret-password", "member_ids": ["j-carol", "j-dave"]},
    ))
    members = [event["member"] for event in streamed if event["type"] == "member"]
    assert [member["source_name"] for member in members] == ["Carol", "Dave"]
    assert "/#reset=" in members[0]["reset_url"]
    assert members[1]["error"] == "Jellyfin history could not be read for this member."
    assert streamed[-1] == {"type": "complete", "completed": 2, "total": 2}
    assert closed == [True]
    with vault() as db:
        carol = db.query(User).filter_by(username="carol").one()
        assert carol.password_hash is None
        assert db.query(AccountToken).filter_by(user_id=carol.id, kind="reset").count() == 1


def test_progress_precedes_network_read_and_closing_the_stream_releases_source_without_reading(
    vault, monkeypatch,
) -> None:  # noqa: ANN001
    entered, closed, reads = [], [], []

    @contextmanager
    def signed_in(*_):  # noqa: ANN002, ANN202
        entered.append(True)
        try:
            yield object(), "j-owner"
        finally:
            closed.append(True)

    monkeypatch.setattr(household_transfer, "admin_session", signed_in)
    monkeypatch.setattr(household_transfer, "list_users", lambda _: [JellyfinUser("j-member", "Member")])
    monkeypatch.setattr(jellyfin_household, "read_history", lambda *_: reads.append(True) or EMPTY)
    payload = HouseholdTransferRequest(username="owner", password="private", member_ids=["j-member"])
    stream = household_transfer._events(
        "jellyfin", "http://jellyfin.test:8096", payload, OWNER, lambda: session_scope(vault),
    )

    assert json.loads(next(stream).decode().removeprefix("data: ")) == {"type": "connecting"}
    progress = json.loads(next(stream).decode().removeprefix("data: "))
    assert progress == {
        "type": "progress", "source_id": "j-member", "source_name": "Member", "completed": 0, "total": 1,
    }
    assert entered == [True] and reads == []
    stream.close()  # the ASGI response invokes the same close on transport disconnect
    assert closed == [True] and reads == []


def test_transport_disconnect_after_progress_closes_source_before_history_read(vault, monkeypatch) -> None:  # noqa: ANN001
    from app.services.media_response import ClosingStreamingResponse

    closed, reads = [], []

    @contextmanager
    def signed_in(*_):  # noqa: ANN002, ANN202
        try:
            yield object(), "j-owner"
        finally:
            closed.append(True)

    monkeypatch.setattr(household_transfer, "admin_session", signed_in)
    monkeypatch.setattr(household_transfer, "list_users", lambda _: [JellyfinUser("j-member", "Member")])
    monkeypatch.setattr(jellyfin_household, "read_history", lambda *_: reads.append(True) or EMPTY)
    content = household_transfer._events(
        "jellyfin", "http://jellyfin.test:8096",
        HouseholdTransferRequest(username="owner", password="private", member_ids=["j-member"]),
        OWNER, lambda: session_scope(vault),
    )
    response = ClosingStreamingResponse(content, close=content.close, headers={"Content-Type": "text/event-stream"})

    async def disconnect() -> list[dict]:
        disconnected = asyncio.Event()
        bodies = []

        async def receive() -> dict:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] != "http.response.body" or not message.get("body"):
                return
            event = json.loads(message["body"].decode().removeprefix("data: "))
            bodies.append(event)
            if event["type"] == "progress":
                disconnected.set()
                await asyncio.sleep(0)

        await response({"type": "http", "method": "POST", "headers": []}, receive, send)
        return bodies

    assert [event["type"] for event in asyncio.run(disconnect())] == ["connecting", "progress"]
    assert closed == [True] and reads == []


def test_public_post_flushes_progress_and_serves_health_while_member_read_is_blocked(
    vault, api_client, monkeypatch,
) -> None:  # noqa: ANN001
    from app.main import app

    release, reading = threading.Event(), threading.Event()

    @contextmanager
    def source(*_):  # noqa: ANN002, ANN202
        yield household_transfer.SourceHousehold(
            "Plex", "http://plex.test:32400", "p-owner", [JellyfinUser("p-member", "Member")],
            read=lambda *_: (reading.set(), release.wait(2), EMPTY)[2],
        )

    monkeypatch.setattr(household_transfer, "_source", source)
    client = as_user(api_client, vault)

    async def request() -> tuple[list[dict], int]:
        body = json.dumps({"token": TOKEN, "member_ids": ["p-member"]}).encode()
        request_sent = False
        disconnected = asyncio.Event()
        progress_sent = asyncio.Event()
        streamed = []

        async def receive() -> dict:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] != "http.response.body" or not message.get("body"):
                return
            event = json.loads(message["body"].decode().removeprefix("data: "))
            streamed.append(event)
            if event["type"] == "progress":
                progress_sent.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": f"{URL}/plex/import",
            "raw_path": f"{URL}/plex/import".encode(), "query_string": b"",
            "headers": [
                (b"host", b"localhost"), (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 12345), "server": ("localhost", 80), "state": {},
        }
        running = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(progress_sent.wait(), 2)
        assert await asyncio.to_thread(reading.wait, 1)
        assert not running.done()
        health = await asyncio.to_thread(client.get, "/api/health")
        release.set()
        await asyncio.wait_for(running, 2)
        return streamed, health.status_code

    streamed, health_status = asyncio.run(request())
    assert [event["type"] for event in streamed] == ["connecting", "progress", "member", "complete"]
    assert health_status == 200


def test_empty_or_changed_selection_never_authenticates_or_writes(vault, api_client, monkeypatch) -> None:  # noqa: ANN001
    calls = []
    monkeypatch.setattr(plex_history_client, "household", lambda *_: calls.append(True) or (
        "p-owner", [JellyfinUser("p-owner", "Root")], {"p-owner": "member-token"},
    ))
    admin = as_user(api_client, vault)
    empty = admin.post(f"{URL}/plex/import", json={"token": TOKEN, "member_ids": []})
    assert empty.status_code == 400
    assert calls == []

    changed = events(admin.post(f"{URL}/plex/import", json={"token": TOKEN, "member_ids": ["removed-member"]}))
    assert changed == [
        {"type": "connecting"},
        {"type": "error", "message": "The selected source members changed. Discover the household again."},
    ]
    assert calls == [True]
    with vault() as db:
        assert db.query(User).count() == 2 and db.query(AccountToken).count() == 0


def test_admin_server_rate_and_secret_boundaries(vault, api_client, monkeypatch, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.INFO)
    users = [JellyfinUser("p-owner", "Root")]
    monkeypatch.setattr(plex_history_client, "household", lambda *_: ("p-owner", users, {"p-owner": "per-user-token"}))

    with vault() as db:
        viewer = make_user("viewer")
        db.add(viewer)
        db.commit()
    assert api_client(user=viewer, base_url="http://localhost").post(
        f"{URL}/plex/discover", json={"token": TOKEN},
    ).status_code == 403

    admin = as_user(api_client, vault)
    for _ in range(RATE_LIMIT_RULES["plex_import"].max_requests):
        assert admin.post(f"{URL}/plex/discover", json={"token": TOKEN}).status_code == 200
    limited = admin.post(f"{URL}/plex/discover", json={"token": TOKEN})
    assert limited.status_code == 429 and limited.headers["retry-after"]

    # The other provider's independent rate bucket proves its stored-address gate without sending credentials upstream.
    with vault() as db:
        settings = db.get(AppSettings, 1)
        settings.jellyfin_import_url = None
        db.commit()
    secret = "never-log-this-password"
    absent = admin.post(
        f"{URL}/jellyfin/discover", json={"username": "source", "password": secret},
    )
    assert absent.status_code == 409
    assert secret not in absent.text and secret not in caplog.text

    with vault() as db:
        db.get(AppSettings, 1).jellyfin_import_url = "http://jellyfin.test:8096"
        db.commit()

    @contextmanager
    def refused(*_):  # noqa: ANN002, ANN202
        raise JellyfinImportError("Jellyfin did not accept that username and password.")
        yield  # pragma: no cover

    monkeypatch.setattr(household_transfer, "admin_session", refused)
    rejected = admin.post(
        f"{URL}/jellyfin/discover", json={"username": "source", "password": secret},
    )
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "Jellyfin did not accept that username and password."}
    assert secret not in rejected.text and secret not in caplog.text
