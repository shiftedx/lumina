"""Recommendations R1: POST /api/reco/events, DELETE /api/reco/history and the maintenance sweep."""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from app import main as main_module
from app.config import settings
from app.models import MemberRecommendationSuppression, RecoEvent, RecoPool, utcnow
from app.security import CSRF_HEADER, hash_password
from app.services.reco import Candidate, Ranked, ServedList
from app.services.reco.events import RecoEventService, served_lists
from support import make_user

PASSWORD = "Test-only-passphrase-1"
LIST_A, LIST_B = "0123456789abcdef", "fedcba9876543210"
KEY_A, KEY_B = "a" * 64, "b" * 64


def origin() -> dict[str, str]:
    return {"Origin": settings.allowed_origins_list[0]}


def served(list_id: str, user: str, *keys: str) -> ServedList:
    return ServedList(
        list_id=list_id, user_id=user, surface="home_picked", context_key="-", created_at=utcnow(),
        items=tuple(
            Ranked(
                candidate=Candidate(key=key, target_kind="remote", title="A video", channel_key="name:youtube:chan", channel_name="Chan",
                                    tokens=frozenset(), published_at=None, sources=0),
                position=index, slot="exploit", score=1.0, p_shown=None, reason_code="channel", reason="From a channel you watch",
            )
            for index, key in enumerate(keys)
        ),
    )


def event(kind: str = "impression", list_id: str = LIST_A, key: str = KEY_A, age_ms: int = 1500) -> dict:
    return {"kind": kind, "list_id": list_id, "key": key, "age_ms": age_ms}


@pytest.fixture
def members(db_factory, api_client):  # noqa: ANN001, ANN201
    with db_factory.begin() as session:
        session.add_all([make_user("alice", password_hash=hash_password(PASSWORD)), make_user("bob", password_hash=hash_password(PASSWORD))])
    served_lists.clear()

    def login(name: str):  # noqa: ANN202
        client = api_client(base_url="http://localhost")
        response = client.post("/api/session/login", json={"username": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client, response.json()["csrf_token"]

    yield db_factory, login
    served_lists.clear()


def stored(db_factory, user: str | None = None) -> list[RecoEvent]:  # noqa: ANN001
    with db_factory() as db:
        query = select(RecoEvent).order_by(RecoEvent.id)
        return list(db.scalars(query.where(RecoEvent.user_id == user) if user else query))


def test_a_member_posts_with_the_header_or_the_beacon_body_token(members) -> None:  # noqa: ANN001
    db_factory, login = members
    client, token = login("alice")
    served_lists.put(served(LIST_A, "alice", KEY_A, KEY_B))
    assert client.post("/api/reco/events", json={"events": [event()]}, headers={**origin(), CSRF_HEADER: token}).status_code == 204
    assert client.post("/api/reco/events", json={"events": [event("open", key=KEY_B)], "csrf": token}, headers=origin()).status_code == 204
    first, second = stored(db_factory)
    assert (first.kind, first.surface, first.position, first.slot, first.item_key, first.user_id) == ("impression", "home_picked", 0, "exploit", KEY_A, "alice")
    assert (second.kind, second.position, second.item_key) == ("open", 1, KEY_B)


def test_a_stale_or_missing_token_and_a_missing_session_are_refused(members, api_client) -> None:  # noqa: ANN001
    db_factory, login = members
    client, _token = login("alice")
    served_lists.put(served(LIST_A, "alice", KEY_A))
    assert api_client(base_url="http://localhost").post("/api/reco/events", json={"events": [event()]}, headers=origin()).status_code == 401
    stale = client.post("/api/reco/events", json={"events": [event()], "csrf": "0" * 64}, headers=origin())
    assert stale.status_code == 403 and stale.json() == {"detail": "CSRF token missing or invalid."}
    assert client.post("/api/reco/events", json={"events": [event()]}, headers=origin()).status_code == 403
    assert client.post("/api/reco/events", json={"events": [event()]}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert stored(db_factory) == []


def test_a_member_cannot_post_into_another_members_list(members) -> None:  # noqa: ANN001
    db_factory, login = members
    bob, token = login("bob")
    served_lists.put(served(LIST_A, "alice", KEY_A))
    served_lists.put(served(LIST_B, "bob", KEY_B))
    body = {"events": [event(list_id=LIST_A), event(list_id=LIST_B, key=KEY_A), event(list_id="1" * 16), event(list_id=LIST_B, key=KEY_B)]}
    assert bob.post("/api/reco/events", json=body, headers={**origin(), CSRF_HEADER: token}).status_code == 204
    assert [(row.user_id, row.item_key, row.list_id) for row in stored(db_factory)] == [("bob", KEY_B, LIST_B)]
    with db_factory() as db:
        assert RecoEventService(db).dropped_24h(utcnow()) >= 3


def test_malformed_and_oversized_batches_are_refused(members) -> None:  # noqa: ANN001
    _db, login = members
    client, token = login("alice")
    headers = {**origin(), CSRF_HEADER: token}
    assert client.post("/api/reco/events", json={"events": [], "csrf": "x" * 40_000}, headers=headers).status_code == 413
    for body in (
        {"events": [event("play")]},
        {"events": [event("skip")]},
        {"events": [event(list_id="ABCDEF0123456789")]},
        {"events": [event(list_id="abc")]},
        {"events": [event(key="not-a-key")]},
        {"events": [event(age_ms=-1)]},
        {"events": [event(age_ms=900_001)]},
        {"events": [event()] * 201},
        {"nothing": []},
    ):
        assert client.post("/api/reco/events", json=body, headers=headers).status_code == 422, body


def test_thirteen_posts_in_a_minute_are_refused(members) -> None:  # noqa: ANN001
    _db, login = members
    client, token = login("alice")
    headers = {**origin(), CSRF_HEADER: token}
    codes = [client.post("/api/reco/events", json={"events": []}, headers=headers).status_code for _ in range(13)]
    assert codes == [204] * 12 + [429]


def test_clearing_history_erases_the_members_rows_and_lists_only(members) -> None:  # noqa: ANN001
    db_factory, login = members
    with db_factory.begin() as db:
        db.add_all([
            RecoEvent(user_id="alice", at=utcnow(), kind="open", target_kind="remote", item_key=KEY_A),
            RecoEvent(user_id="bob", at=utcnow(), kind="open", target_kind="remote", item_key=KEY_A),
            RecoPool(user_id="alice", item_key=KEY_A, sources=1), RecoPool(user_id="bob", item_key=KEY_A, sources=1),
        ])
    served_lists.put(served(LIST_A, "alice", KEY_A))
    served_lists.put(served(LIST_B, "bob", KEY_A))
    client, token = login("alice")
    assert client.delete("/api/reco/history", headers=origin()).status_code == 403  # no CSRF header
    assert client.delete("/api/reco/history", headers={**origin(), CSRF_HEADER: token}).status_code == 204
    assert [row.user_id for row in stored(db_factory)] == ["bob"]
    with db_factory() as db:
        assert [pool.user_id for pool in db.scalars(select(RecoPool))] == ["bob"]
    now = utcnow()
    assert served_lists.get("alice", LIST_A, now) is None and served_lists.get("bob", LIST_B, now) is not None


def test_anonymous_callers_cannot_clear_history(members, api_client) -> None:  # noqa: ANN001
    assert api_client(base_url="http://localhost").delete("/api/reco/history", headers=origin()).status_code == 401


def test_the_maintenance_cycle_sweeps_reco_events_once() -> None:
    tree = ast.parse(Path(main_module.__file__).read_text())
    cycle = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_library_maintenance_cycle")
    sweeps = [
        node for node in ast.walk(cycle)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sweep"
        and isinstance(node.func.value, ast.Call) and getattr(node.func.value.func, "id", None) == "RecoEventService"
    ]
    assert len(sweeps) == 1


def test_the_export_carries_the_members_events_and_every_suppression(members) -> None:  # noqa: ANN001
    db_factory, login = members
    created = datetime(2026, 8, 1, 9, 0, 0)
    with db_factory.begin() as db:
        db.execute(insert(RecoEvent), [
            {"user_id": "alice", "at": datetime(2026, 9, 1) + timedelta(minutes=n), "kind": "open", "surface": "home_picked", "list_id": LIST_A,
             "position": 2, "slot": "exploit", "reason_code": "channel", "target_kind": "remote", "item_key": KEY_A, "channel_key": "name:youtube:chan"}
            for n in range(5010)
        ])
        db.add(RecoEvent(user_id="bob", at=datetime(2026, 9, 1), kind="open", target_kind="remote", item_key=KEY_B))
        for scope in ("item", "channel", "fewer", "title"):
            db.add(MemberRecommendationSuppression(id=str(uuid.uuid4()), user_id="alice", scope=scope, target_key=f"{scope}-target", created_at=created))
        db.add(MemberRecommendationSuppression(id=str(uuid.uuid4()), user_id="bob", scope="item", target_key="bobs", created_at=created))
    body = login("alice")[0].get("/api/me/export").json()
    events = body["recommendation_events"]
    assert len(events) == 5000
    assert events[0]["at"] > events[-1]["at"]  # newest first, and the oldest ten fell off the end
    assert set(events[0]) == {"at", "kind", "surface", "target_kind", "item_key", "position", "slot", "reason_code", "fraction"}
    assert {row["item_key"] for row in events} == {KEY_A}
    feedback = {row["scope"]: row for row in body["recommendation_feedback"]}
    assert sorted(feedback) == ["channel", "fewer", "item", "title"]
    assert feedback["fewer"]["recovers_at"] == "2026-12-19T09:00:00"  # 2026-08-01 + 140 days
    assert all(feedback[scope]["recovers_at"] is None for scope in ("item", "channel", "title"))
    assert "bobs" not in str(body)
    other = login("bob")[0].get("/api/me/export").json()
    assert [row["item_key"] for row in other["recommendation_events"]] == [KEY_B]
    assert [row["target_key"] for row in other["recommendation_feedback"]] == ["bobs"]
