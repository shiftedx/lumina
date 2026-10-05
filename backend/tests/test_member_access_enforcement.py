"""Member access enforcement: streaming gates, viewing hours, the daily limit and screen time.

Seeds title_support's Jellyfin household in the per-test file database (SessionLocal and get_db share it). Alice is the
restricted member, Bob an unrestricted one, Boss the admin. The clock is fake and the household zone pinned to UTC.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import main
from app.db import SessionLocal
from app.main import app
from app.models import ScreenTime, SourceAutomation, User
from app.security import get_current_user
from app.services import member_access, screen_time, streaming_gate
from app.services.rate_limit import rate_limiter
from support import make_user
from title_support import ALICE, ALICE_TOKEN, BOB, FILE, S1E1, jellyfin_household, mediabrowser

BOSS = "boss-admin"
NONE_ALLOWED = {"youtube": False, "twitch": False, "kick": False, "live": False, "open_search": False}
MON_NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # a Monday
TICKS = 10_000_000


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    now = {"now": MON_NOON}
    monkeypatch.setattr(screen_time, "LOCAL_TZ", ZoneInfo("UTC"))
    monkeypatch.setattr(screen_time, "_now", lambda: now["now"])
    screen_time._last_beat.clear(), screen_time._last_pass.clear()
    yield now
    screen_time._last_beat.clear(), screen_time._last_pass.clear()


@pytest.fixture
def house(tmp_path: Path, clock: dict, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    rate_limiter.clear()
    with SessionLocal() as session:
        session.add(make_user(BOSS, role="admin", username="boss"))
        session.commit()
    who = {"id": ALICE}
    def signed_in(request: Request) -> User:  # stands in for a browser session
        request.state.via_session = True
        return user(who["id"])

    app.dependency_overrides[get_current_user] = signed_in
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: user(who["id"]))
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    client.who = who
    yield client
    client.close()
    app.dependency_overrides.pop(get_current_user, None)


def user(user_id: str) -> User:
    with SessionLocal() as session:
        found = session.get(User, user_id)
        session.expunge(found)
        return found


def limit(user_id: str = ALICE, **values) -> None:  # noqa: ANN003
    with SessionLocal() as session:
        member_access.save(session, user_id, values)
        session.commit()


def follow(user_id: str, url: str) -> None:
    with SessionLocal() as session:
        session.add(SourceAutomation(id="follow-1", user_id=user_id, label="Followed", source_url=url, source_type="channel", cron_expression="*/30 * * * *"))
        session.commit()


def seconds_today(user_id: str = ALICE) -> int:
    with SessionLocal() as session:
        return screen_time.seconds_today(session, user_id)


# ---- streaming gates -------------------------------------------------------------------------------------------------

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
GATED = [
    ("GET", "/api/discovery/popular", None, "youtube"),
    ("GET", "/api/discovery/popular/music", None, "youtube"),
    ("GET", "/api/discovery/home", None, "youtube"),
    ("POST", "/api/discovery/up-next", {"source_url": YT}, "youtube"),
    ("GET", "/api/discovery/channels", None, "youtube"),
    ("POST", "/api/discovery/channels/search", {"query": "cats"}, "youtube"),
    ("GET", "/api/channels/youtube/UCuAXFkgsw1L7xaCfnd5JJOw", None, "youtube"),
    ("POST", "/api/channels/resolve", {"url": "https://www.youtube.com/@cats"}, "youtube"),
    ("POST", "/api/youtube-search", {"query": "cats"}, "youtube"),
    ("POST", "/api/source-search", {"query": "cats"}, "open_search"),
    ("GET", "/api/discovery/live", None, "live"),
    ("GET", "/api/discovery/live/gaming", None, "live"),
    ("POST", "/api/preview", {"source_url": YT}, "youtube"),
    ("POST", "/api/preview", {"source_url": "https://www.twitch.tv/somebody"}, "twitch"),
    ("POST", "/api/preview", {"source_url": "https://kick.com/somebody"}, "kick"),
    ("POST", "/api/preview", {"source_url": "https://vimeo.com/1"}, "open_search"),
    ("POST", "/api/jobs", {"source_url": "https://www.twitch.tv/videos/1"}, "twitch"),
    ("POST", "/api/acquisition-batches", {"source_url": "https://kick.com/x", "entries": [{"source_url": "https://kick.com/x/videos/1"}]}, "kick"),
    ("POST", "/api/live-recordings", {"source_url": "https://www.twitch.tv/somebody"}, "twitch"),
    ("POST", "/api/automations", {"label": "Cats", "source_url": "https://www.youtube.com/@cats"}, "youtube"),
    ("GET", f"/api/live-chat?source_url={quote(YT)}", None, "youtube"),
]


@pytest.mark.parametrize(("method", "path", "body", "kind"), GATED)
def test_every_streaming_family_is_refused_for_a_blocked_member_only(house: TestClient, method: str, path: str, body, kind: str) -> None:  # noqa: ANN001
    limit(streaming=NONE_ALLOWED)
    refused = house.request(method, path, json=body)
    assert (refused.status_code, refused.json()["detail"]) == (403, f"streaming_blocked:{kind}")
    for other in (BOSS, BOB):  # the admin and an unrestricted member reach the route (whatever it then answers offline)
        house.who["id"] = other
        reached = house.request(method, path, json=body)
        assert not (reached.status_code == 403 and "streaming_blocked" in str(reached.json().get("detail"))), (other, reached.text)


def test_a_live_gate_alone_refuses_live_surfaces_and_a_live_preview(house: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    limit(streaming={"live": False})
    assert house.get("/api/discovery/live").json()["detail"] == "streaming_blocked:live"
    assert house.post("/api/live-recordings", json={"source_url": YT}).json()["detail"] == "streaming_blocked:live"
    with SessionLocal() as session:
        with pytest.raises(HTTPException) as refused:
            streaming_gate.check_url(session, user(ALICE), YT, channels=(), live=True)
        assert refused.value.detail == "streaming_blocked:live"
        streaming_gate.check_url(session, user(ALICE), YT, channels=())  # a plain YouTube video still plays


def test_followed_only_allows_followed_channels_and_refuses_discovery(house: TestClient) -> None:
    limit(streaming={"followed_only": True})
    follow(ALICE, "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw")
    assert house.post("/api/youtube-search", json={"query": "cats"}).json()["detail"] == "streaming_blocked:youtube"
    assert house.get("/api/discovery/popular").json()["detail"] == "streaming_blocked:youtube"
    assert house.post("/api/automations", json={"label": "New", "source_url": "https://www.youtube.com/@new"}).json()["detail"] == "streaming_blocked:youtube"
    alice = user(ALICE)
    with SessionLocal() as session:
        streaming_gate.check_url(session, alice, YT, channels=("https://youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",))
        with pytest.raises(HTTPException):
            streaming_gate.check_url(session, alice, YT, channels=("https://www.youtube.com/channel/UCsomeoneelse0000000000",))
        streaming_gate.check_url(session, alice, YT)  # channels unknown yet: decided after extraction


def test_admin_manages_follows_for_a_followed_only_member(house: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """#166: followed_only refuses the member's own new follows; an admin adds and removes them on the member's behalf."""
    monkeypatch.setattr(main.YtDlpService, "validate_source_url", lambda self, url: url)  # no DNS in tests
    limit(streaming={"followed_only": True})
    follow(ALICE, "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw")
    base = f"/api/admin/members/{ALICE}/follows"
    new = {"source_url": "https://www.youtube.com/@cats", "display_name": "Cats"}
    other = {"source_url": "https://www.youtube.com/@dogs"}
    # A member, restricted or not, never reaches the admin routes: not for themselves, not for anyone else.
    for who, target in ((ALICE, ALICE), (ALICE, BOB), (BOB, ALICE), (BOB, BOB)):
        house.who["id"] = who
        url = f"/api/admin/members/{target}/follows"
        assert [house.get(url).status_code, house.post(url, json=new).status_code, house.delete(f"{url}/follow-1").status_code] == [403] * 3, who
    house.who["id"] = ALICE  # the restricted member's own new follow is still refused
    assert house.post("/api/automations", json={"label": "Cats", "source_url": new["source_url"]}).json()["detail"] == "streaming_blocked:youtube"

    house.who["id"] = BOSS
    assert [f["label"] for f in house.get(base).json()] == ["Followed"]
    assert [f["label"] for f in house.post(base, json=new).json()] == ["Cats", "Followed"]
    assert len(house.post(base, json=new).json()) == 2  # already followed: no duplicate
    assert [f["label"] for f in house.post(base, json=other).json()] == ["Cats", "Followed", "https://www.youtube.com/@dogs"]
    assert house.post(base, json={"source_url": "not a channel"}).status_code == 422
    assert house.post(f"/api/admin/members/{BOSS}/follows", json=new).status_code == 409  # admins have no limits
    assert house.post("/api/admin/members/nobody/follows", json=new).status_code == 404
    with SessionLocal() as session:
        streaming_gate.check_url(session, user(ALICE), YT, channels=("https://www.youtube.com/@cats",))  # now allowed

    assert house.delete(f"/api/admin/members/{BOB}/follows/follow-1").status_code == 404  # another member's follow id
    assert house.delete(f"{base}/follow-1").status_code == 204
    assert house.delete(f"{base}/follow-1").status_code == 404
    assert [f["label"] for f in house.get(base).json()] == ["Cats", "https://www.youtube.com/@dogs"]
    with SessionLocal() as session:
        with pytest.raises(HTTPException):
            streaming_gate.check_url(session, user(ALICE), YT, channels=("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",))


def test_global_search_leaves_out_blocked_followed_sources(house: TestClient) -> None:
    limit(streaming={"youtube": False})
    with SessionLocal() as session:
        assert streaming_gate.blocked_kinds(session, user(ALICE)) == ["youtube"]
        assert streaming_gate.blocked_kinds(session, user(BOSS)) == []
    assert house.get("/api/search", params={"q": "pilot"}).status_code == 200


def test_url_kinds() -> None:
    assert [streaming_gate.url_kind(u) for u in (YT, "youtu.be/x", "https://m.twitch.tv/a", "https://kick.com/a", "https://vimeo.com/1")] == [
        "youtube", "youtube", "twitch", "kick", "open_search",
    ]


# ---- viewing hours, daily limit, bonus --------------------------------------------------------------------------------

def watch(user_id: str = ALICE, at: datetime = MON_NOON) -> tuple:
    with SessionLocal() as session:
        return screen_time.can_watch_now(session, user(user_id), at)


def test_schedule_windows_including_one_past_midnight(house: TestClient) -> None:
    limit(schedule={"mon": [["07:00", "12:00"], ["12:00", "20:00"]], "fri": [["20:00", "02:00"]], "sat": [["09:00", "10:00"]]})
    assert watch(at=MON_NOON) == (True, None, datetime(2026, 10, 5, 20, 0, tzinfo=UTC), None)  # touching ranges merge
    assert watch(at=datetime(2026, 10, 5, 6, 59, tzinfo=UTC))[:3] == (False, "outside_hours", datetime(2026, 10, 5, 7, 0, tzinfo=UTC))
    assert watch(at=datetime(2026, 10, 5, 20, 0, tzinfo=UTC))[:3] == (False, "outside_hours", datetime(2026, 10, 9, 20, 0, tzinfo=UTC))
    assert watch(at=datetime(2026, 10, 10, 1, 30, tzinfo=UTC))[:3] == (True, None, datetime(2026, 10, 10, 2, 0, tzinfo=UTC))  # Sat 01:30
    assert watch(at=datetime(2026, 10, 10, 2, 0, tzinfo=UTC))[:3] == (False, "outside_hours", datetime(2026, 10, 10, 9, 0, tzinfo=UTC))
    assert watch(BOSS)[0] and watch(BOB)[0]
    mine = house.get("/api/me/access").json()
    assert (mine["allowed_now"], mine["until"], mine["remaining_minutes"]) == (True, "2026-10-05T20:00:00Z", None)
    house.who["id"] = BOB
    mine = house.get("/api/me/access").json()
    assert (mine["allowed_now"], mine["until"], mine["remaining_minutes"]) == (True, None, None)


def test_daily_limit_and_bonus(house: TestClient, clock: dict) -> None:
    limit(daily_limit_minutes=60)
    with SessionLocal() as session:
        screen_time.add_screen_time(session, ALICE, 59 * 60, MON_NOON)
        screen_time.add_screen_time(session, ALICE, 30, MON_NOON)
    assert watch() == (True, None, None, 0)
    with SessionLocal() as session:
        screen_time.add_screen_time(session, ALICE, 30, MON_NOON)
    assert watch() == (False, "screen_time_up", datetime(2026, 10, 6, tzinfo=UTC), 0)
    assert watch(at=MON_NOON + timedelta(hours=12))[0]  # a new household day
    house.who["id"] = ALICE
    assert house.post(f"/api/admin/members/{ALICE}/access/bonus", json={"minutes": 30}).status_code == 403  # admins only
    house.who["id"] = BOSS
    granted = house.post(f"/api/admin/members/{ALICE}/access/bonus", json={"minutes": 30}).json()
    assert (granted["bonus_minutes"], granted["allowed_now"], granted["remaining_minutes"]) == (30, True, 30)
    assert house.post(f"/api/admin/members/{ALICE}/access/bonus", json={"minutes": 15}).json()["bonus_minutes"] == 45
    assert house.post(f"/api/admin/members/{BOB}/access/bonus", json={"minutes": 15}).status_code == 409  # no limits to lift
    clock["now"] = MON_NOON + timedelta(days=1)
    limit(daily_limit_minutes=60, bonus_date=date(2026, 10, 5), bonus_minutes=45)
    assert watch(at=clock["now"])[3] == 60  # yesterday's bonus is gone


def test_playback_starts_are_refused_with_the_code_and_when(house: TestClient) -> None:
    limit(schedule={"tue": [["07:00", "20:00"]]})
    refused = house.get(f"/api/library/{FILE[S1E1]}/playback-options")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "outside_hours"
    assert house.get("/api/me/access").json()["until"] == "2026-10-06T07:00:00Z"
    assert house.post(f"/api/library/{FILE[S1E1]}/playback-sessions").json()["detail"] == "outside_hours"
    assert house.get(f"/api/library/{FILE[S1E1]}/media").json()["detail"] == "outside_hours"
    assert house.post("/api/preview", json={"source_url": "https://vimeo.com/1"}).status_code != 403  # browsing is not watching
    jellyfin = house.post(f"/Items/{FILE[S1E1].replace('-', '')}/PlaybackInfo", headers=mediabrowser(ALICE_TOKEN))
    assert (jellyfin.status_code, jellyfin.json()["detail"]) == (403, "outside_hours")
    house.who["id"] = BOB
    assert house.get(f"/api/library/{FILE[S1E1]}/playback-options").status_code != 403


# ---- screen-time accounting -------------------------------------------------------------------------------------------

def test_heartbeats_count_once_per_member_whatever_the_source(house: TestClient, clock: dict) -> None:
    def tick(seconds: int) -> None:
        clock["now"] += timedelta(seconds=seconds)

    report = {"ItemId": FILE[S1E1].replace("-", ""), "MediaSourceId": FILE[S1E1].replace("-", "")}
    jellyfin = lambda path, ticks: house.post(path, json={**report, "PositionTicks": ticks * TICKS}, headers=mediabrowser(ALICE_TOKEN))  # noqa: E731
    assert jellyfin("/Sessions/Playing", 0).status_code == 204  # the first report starts the beat
    tick(10)
    assert jellyfin("/Sessions/Playing/Progress", 10).status_code == 204
    assert seconds_today() == 10
    tick(10)
    assert house.put(f"/api/library/{FILE[S1E1]}/playback", json={"position_seconds": 20}).status_code == 200  # web, same member
    assert seconds_today() == 20
    tick(5)
    identity = "youtube:dQw4w9WgXcQ"
    remote = house.put(f"/api/playback/remote/{quote(identity, safe='')}", json={
        "source_identity": identity, "source_url": YT, "position_seconds": 5, "checkpoint_client_id": "p",
        "checkpoint_sequence": 1, "expected_revision": 0,
    })
    assert remote.status_code == 200, remote.text
    assert seconds_today() == 25  # three sources, one clock: nothing counted twice
    tick(5)
    assert jellyfin("/Sessions/Playing/Stopped", 30).status_code == 204
    assert seconds_today() == 30
    tick(600)
    assert jellyfin("/Sessions/Playing", 30).status_code == 204  # the first gap after Stopped counts at most RESUME_SECONDS
    assert seconds_today() == 30 + screen_time.RESUME_SECONDS
    tick(1000)
    assert jellyfin("/Sessions/Playing/Progress", 40).status_code == 204  # a long silence counts GAP_SECONDS, never 0
    assert seconds_today() == 30 + screen_time.RESUME_SECONDS + screen_time.GAP_SECONDS


def test_streams_count_without_progress_reports(house: TestClient, clock: dict) -> None:
    """A restricted member's media, Jellyfin stream and HLS fetches advance the one clock; nothing counts twice."""
    limit(daily_limit_minutes=60)
    jf_id = FILE[S1E1].replace("-", "")
    fetches = [
        lambda: house.get(f"/api/library/{FILE[S1E1]}/media", headers={"Range": "bytes=0-1"}),
        lambda: house.get(f"/Videos/{jf_id}/stream", params={"static": "true", "MediaSourceId": jf_id}, headers={**mediabrowser(ALICE_TOKEN), "Range": "bytes=0-1"}),
    ]
    for fetch in fetches * 3:
        assert fetch().status_code in (200, 206)
        clock["now"] += timedelta(seconds=30)
    assert seconds_today() == 150  # five 30 s gaps; the first fetch only starts the clock
    for _ in range(4):  # a burst of Range requests is one count
        house.get(f"/api/library/{FILE[S1E1]}/media", headers={"Range": "bytes=0-1"})
    assert seconds_today() == 180
    house.who["id"] = BOB  # an unrestricted member's fetches write nothing
    clock["now"] += timedelta(seconds=30)
    house.get(f"/api/library/{FILE[S1E1]}/media", headers={"Range": "bytes=0-1"})
    assert seconds_today(BOB) == 0


def test_a_stricter_limit_applies_to_the_next_fetch(house: TestClient, clock: dict) -> None:
    limit(daily_limit_minutes=60)
    with SessionLocal() as session:
        screen_time.add_screen_time(session, ALICE, 30 * 60, MON_NOON)
    media = f"/api/library/{FILE[S1E1]}/media"
    assert house.get(media, headers={"Range": "bytes=0-1"}).status_code in (200, 206)  # allowed and cached for a minute
    house.who["id"] = BOSS
    assert house.put(f"/api/admin/members/{ALICE}/access", json={"daily_limit_minutes": 20}).status_code == 200
    house.who["id"] = ALICE
    clock["now"] += timedelta(seconds=10)
    assert house.get(media, headers={"Range": "bytes=0-1"}).json()["detail"] == "screen_time_up"


def test_a_running_session_stops_within_a_minute_of_the_limit(house: TestClient, clock: dict) -> None:
    limit(daily_limit_minutes=1)
    path = f"/api/library/{FILE[S1E1]}/playback"
    statuses = []
    for _ in range(5):  # 20 s apart: counted 0, 20, 40, 60 s; re-checked at the first beat and a minute later
        clock["now"] += timedelta(seconds=20)
        reply = house.put(path, json={"position_seconds": 1})
        statuses.append(reply.status_code)
    assert statuses == [200, 200, 200, 403, 403]
    assert reply.json()["detail"] == "screen_time_up"
    assert seconds_today() == 80  # a player that keeps reporting is still watching: counted, and refused each time
    jellyfin = house.post("/Sessions/Playing/Progress", json={"ItemId": FILE[S1E1].replace("-", ""), "PositionTicks": TICKS}, headers=mediabrowser(ALICE_TOKEN))
    assert (jellyfin.status_code, jellyfin.json()["detail"]) == (403, "screen_time_up")
    with SessionLocal() as session:
        assert session.get(ScreenTime, (ALICE, date(2026, 10, 5))).seconds >= 60


# ---- admin activity ---------------------------------------------------------------------------------------------------

def test_member_activity_is_admin_only(house: TestClient) -> None:
    limit(daily_limit_minutes=30)
    with SessionLocal() as session:
        screen_time.add_screen_time(session, ALICE, 600, MON_NOON)
    house.who["id"] = BOB
    assert house.get(f"/api/admin/members/{ALICE}/activity").status_code == 403
    house.who["id"] = ALICE
    assert house.get(f"/api/admin/members/{ALICE}/activity").status_code == 403  # not even their own: /api/me/access is theirs
    house.who["id"] = BOSS
    body = house.get(f"/api/admin/members/{ALICE}/activity").json()
    assert (body["today_seconds"], body["allowed_now"], body["remaining_minutes"], body["recent"]) == (600, True, 20, [])
    assert house.get("/api/admin/members/nobody/activity").status_code == 404


# ---- can_download -----------------------------------------------------------------------------------------------------

TWITCH_VOD = "https://www.twitch.tv/videos/1"
ACQUIRE = [
    ("POST", "/api/jobs", {"source_url": TWITCH_VOD}),
    ("POST", "/api/jobs/job-1/retry", None),
    ("POST", "/api/acquisition-batches", {"source_url": "https://kick.com/x", "entries": [{"source_url": "https://kick.com/x/videos/1"}]}),
    ("POST", "/api/acquisition-batches/batch-1/entries/entry-1/retry", None),
    ("POST", "/api/live-recordings", {"source_url": "https://www.twitch.tv/somebody"}),
    ("POST", "/api/automations", {"label": "Cats", "source_url": "https://www.youtube.com/@cats", "auto_download": True}),
    ("PATCH", "/api/automations/follow-1/auto-download", {"enabled": True}),
]


@pytest.fixture
def owned(house: TestClient) -> TestClient:
    from app.models import AcquisitionBatch, AcquisitionBatchEntry, DownloadJob

    follow(ALICE, "https://www.youtube.com/@cats")
    with SessionLocal() as session:
        session.add(DownloadJob(id="job-1", user_id=ALICE, source_url=TWITCH_VOD, status="failed", format_selection={}, output_profile={}))
        session.add(AcquisitionBatch(id="batch-1", user_id=ALICE, source_url=TWITCH_VOD))
        session.add(AcquisitionBatchEntry(id="entry-1", batch_id="batch-1", user_id=ALICE, selection_index=0, source_url=TWITCH_VOD, source_identity="twitch:1"))
        session.commit()
    return house


@pytest.mark.parametrize(("method", "path", "body"), ACQUIRE)
def test_every_acquisition_path_needs_can_download(owned: TestClient, method: str, path: str, body) -> None:  # noqa: ANN001
    limit(can_download=False)
    refused = owned.request(method, path, json=body)
    assert (refused.status_code, refused.json()["detail"]) == (403, "downloads_not_allowed")
    limit(can_download=True)
    reached = owned.request(method, path, json=body)
    assert reached.json().get("detail") != "downloads_not_allowed", reached.text


@pytest.mark.parametrize("path", ["/api/jobs/job-1/retry", "/api/acquisition-batches/batch-1/entries/entry-1/retry"])
def test_retries_take_the_streaming_gate_of_their_source(owned: TestClient, path: str) -> None:
    limit(streaming={"twitch": False})
    assert owned.post(path).json()["detail"] == "streaming_blocked:twitch"


def test_an_auto_download_follow_only_matches_when_its_owner_may_not_download(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas import SourceAutomationCreateRequest
    from app.services.source_automation import SourceAutomationService
    from app.services.yt_dlp_service import YtDlpService
    from support import FakeJobs
    from test_user_settings_and_source_automation import fake_playlist_preview, make_session
    from test_user_settings_and_source_automation import make_user as automation_user

    session = make_session()
    owner = automation_user()
    session.add(owner)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())
    automation = service.create_automation(SourceAutomationCreateRequest(
        auto_download=True, label="Tutorials", source_url="https://www.youtube.com/playlist?list=PL123", source_type="playlist",
    ), owner)
    member_access.save(session, owner.id, {"can_download": False})
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda self, source_url, lazy_playlist=True, format_selection=None: fake_playlist_preview(source_url))
    actions = {decision.action for decision in service.preview_automation(automation.id, owner).run.decisions}
    assert "would_queue" not in actions and "manual" in actions
