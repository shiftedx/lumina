"""Admin Activity: now playing, stop, history, server stats."""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.main import app
from app.models import DeviceToken, LibraryItem, MediaTitle, PlaybackHistory
from app.security import CSRF_HEADER, hash_password, utcnow
from app.services import activity
from app.services.local_playback_sessions import PlaybackSession, sessions
from support import make_user
from test_hls_relay import build_service, register
from test_v1_sessions import PASSWORD, client, factory, login  # noqa: F401  (fixtures)

APP = "token-1"


def admin(client: TestClient, username: str = "local") -> None:
    client.headers.update({"Origin": settings.allowed_origins_list[0], CSRF_HEADER: login(client, username)})


class FakeProcess:
    pid = 0

    def __init__(self) -> None:
        self.code = None

    def poll(self):  # noqa: ANN201
        return self.code

    def send_signal(self, _sig) -> None:  # noqa: ANN001
        pass

    def terminate(self) -> None:
        self.code = 0

    def wait(self, timeout=None) -> int:  # noqa: ANN001
        return 0


@pytest.fixture(autouse=True)
def clean(factory, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    @contextmanager
    def scope():  # noqa: ANN202
        with factory.begin() as db:
            yield db

    monkeypatch.setattr(activity, "session_scope", scope)
    monkeypatch.setattr(activity, "_clock", lambda: clock["now"])
    clock = {"now": time.time()}
    activity._direct.clear(), activity._blocked.clear(), activity._recent_end.clear()
    sessions._sessions.clear()
    activity.install(())
    yield clock
    sessions._sessions.clear()
    activity._direct.clear(), activity._blocked.clear(), activity._recent_end.clear()
    activity.install(())


@pytest.fixture
def clock(clean):  # noqa: ANN001, ANN201
    return clean


@pytest.fixture
def library(factory):  # noqa: ANN001, ANN201
    with factory.begin() as db:
        db.add(make_user("u2", username="sam", display_name="Sam"))
        db.add(MediaTitle(id="series", type="series", key="s", name="Severance"))
        db.add(MediaTitle(id="season", type="season", key="s2", name="Season 2", parent_id="series", index_number=2))
        db.add(MediaTitle(id="ep", type="episode", key="s2e4", name="Woe's Hollow", parent_id="season", index_number=4))
        db.add(MediaTitle(id="movie", type="movie", key="m", name="Dune", year=2021))
        db.add(LibraryItem(id="item-ep", user_id="u1", title="x", visibility="shared", status="available", title_id="ep", duration=3000))
        db.add(LibraryItem(id="item-movie", user_id="u1", title="Dune file", visibility="shared", status="available", title_id="movie", duration=9000))
        db.add(DeviceToken(id=APP, user_id="u2", kind="jellyfin", scope="read", token_digest="d", device_id="dev", device_name="Living room", client="Infuse"))


def fake_session(tmp_path: Path, *, user="u2", item="item-ep", device="web", kind="video_hw", hw="qsv", mode="transcode", age=100) -> PlaybackSession:
    session = PlaybackSession("sess1", user, item, mode, tmp_path, FakeProcess(), device=device, kind=kind, hw=hw)  # type: ignore[arg-type]
    session.started_wall = activity._clock() - age
    session.info = {"video": {"from": "hevc", "to": "h264", "height": 1080, "tonemap": True}, "audio": {"from": "eac3", "to": "aac"}}
    sessions._sessions[session.id] = session
    return session


def history(factory):  # noqa: ANN001, ANN201
    with factory() as db:
        return db.query(PlaybackHistory).order_by(PlaybackHistory.ended_at).all()


def test_activity_joins_user_device_title_and_hardware(client: TestClient, factory, library, tmp_path) -> None:  # noqa: ANN001
    admin(client)
    fake_session(tmp_path)
    activity.touch("u2", "item-movie", APP, position=120, duration=9000)
    body = client.get("/api/admin/activity").json()
    by_method = {s["method"]: s for s in body["sessions"]}
    encode, direct = by_method["transcode"], by_method["direct"]
    assert (encode["id"], encode["user"], encode["title"], encode["subtitle"]) == ("t:sess1", {"id": "u2", "name": "Sam"}, "Severance", "S2 · E4 · Woe's Hollow")
    assert encode["client"] == {"kind": "web", "name": "Lumina web", "device": None}
    assert (encode["hardware"], encode["video"], encode["audio"]) == ("qsv", {"from": "hevc", "to": "h264", "height": 1080, "tonemap": True}, {"from": "eac3", "to": "aac"})
    assert encode["duration_seconds"] == 3000 and encode["stoppable"] is True
    assert (direct["title"], direct["subtitle"], direct["hardware"], direct["position_seconds"], direct["duration_seconds"]) == ("Dune", "2021", None, 120, 9000)
    assert direct["client"] == {"kind": "app", "name": "Infuse", "device": "Living room"} and direct["video"] is None
    assert body["server"]["ffmpeg_processes"] == 1
    assert body["server"]["hardware"] == {"mode": "auto", "active": None, "disabled": False, "failures": 0, "fallbacks": 0}
    assert body["downloads"] == [] and body["recordings"] == []


def test_software_encode_and_remux_report_their_hardware(client: TestClient, library, tmp_path) -> None:  # noqa: ANN001
    admin(client)
    fake_session(tmp_path, kind="video_sw", hw="none")
    assert client.get("/api/admin/activity").json()["sessions"][0]["hardware"] == "software"
    sessions._sessions["sess1"].kind = "remux"
    assert client.get("/api/admin/activity").json()["sessions"][0]["hardware"] is None


def test_direct_play_expires_after_sixty_seconds_into_history(client: TestClient, factory, library, clock) -> None:  # noqa: ANN001
    admin(client)
    activity.touch("u2", "item-ep", APP, position=100)
    clock["now"] += 30
    activity.touch("u2", "item-ep", APP, position=190)
    clock["now"] += 59
    assert len(client.get("/api/admin/activity").json()["sessions"]) == 1
    clock["now"] += 2
    assert client.get("/api/admin/activity").json()["sessions"] == []
    (row,) = history(factory)
    assert (row.method, row.title, row.subtitle, row.user_name, row.stopped_by_admin, row.watched_seconds) == ("direct", "Severance", "S2 · E4 · Woe's Hollow", "Sam", False, 90)
    assert row.client == {"kind": "app", "name": "Infuse", "device": "Living room"}


def test_watched_falls_back_to_last_seen_minus_started_and_short_sessions_are_dropped(factory, library, clock) -> None:  # noqa: ANN001
    activity.touch("u2", "item-ep", "web")
    clock["now"] += 40
    activity.touch("u2", "item-ep", "web")
    activity.touch("u2", "item-movie", "web")
    clock["now"] += 120
    activity.sweep()
    (row,) = history(factory)  # the second session lasted 0 s
    assert row.watched_seconds == 40


def test_jellyfin_stopped_ends_a_direct_session(factory, library) -> None:  # noqa: ANN001
    activity.touch("u2", "item-ep", APP, position=0)
    activity.finish("u2", "item-ep", APP)
    assert history(factory) == []  # under the 10 s floor; the session itself is gone
    assert activity.snapshot is not None and not activity._direct


def test_stop_transcode_ends_job_records_history_and_blocks_restart(client: TestClient, factory, library, tmp_path, clock) -> None:  # noqa: ANN001
    admin(client)
    fake_session(tmp_path)
    activity.touch("u2", "item-ep", "web", position=50)
    clock["now"] += 20
    activity.touch("u2", "item-ep", "web", position=70)
    assert client.post("/api/admin/activity/t:sess1/stop").status_code == 204
    assert sessions.live() == []
    (row,) = history(factory)
    assert (row.method, row.hardware, row.stopped_by_admin, row.watched_seconds) == ("transcode", "qsv", True, 20)
    assert row.video["height"] == 1080
    assert client.post("/api/admin/activity/t:sess1/stop").status_code == 404
    with pytest.raises(HTTPException) as blocked:
        activity.guard("u2", "item-ep", "web")
    assert blocked.value.detail["code"] == "stopped_by_admin"


def test_a_transcode_that_ends_by_itself_records_one_row_and_no_ghost_direct_session(factory, library, tmp_path) -> None:  # noqa: ANN001
    session = fake_session(tmp_path)
    activity.touch("u2", "item-ep", "web", position=0)
    assert sessions.stop("sess1")
    assert [(r.method, r.stopped_by_admin) for r in history(factory)] == [("transcode", False)]
    activity.touch("u2", "item-ep", "web", position=10)  # a late progress report
    assert not activity._direct and session.stopped_by_admin is False


def test_stop_direct_play_refuses_media_for_120_seconds(client: TestClient, factory, library, clock) -> None:  # noqa: ANN001
    admin(client)
    activity.touch("u2", "item-ep", APP, position=5)
    (session,) = client.get("/api/admin/activity").json()["sessions"]
    assert client.post(f"/api/admin/activity/{session['id']}/stop").status_code == 204
    assert [(r.method, r.stopped_by_admin) for r in history(factory)] == [("direct", True)]
    clock["now"] += 119
    with pytest.raises(HTTPException) as blocked:
        activity.guard("u2", "item-ep", APP)
    assert blocked.value.status_code == 403 and blocked.value.detail["code"] == "stopped_by_admin"
    activity.touch("u2", "item-ep", APP, position=9)  # the app keeps reporting: no new session while blocked
    assert client.get("/api/admin/activity").json()["sessions"] == []
    activity.guard("u2", "item-movie", APP)  # another item is unaffected
    clock["now"] += 2
    activity.guard("u2", "item-ep", APP)  # allowed again
    assert client.post(f"/api/admin/activity/{session['id']}/stop").status_code == 404


def test_library_media_route_answers_403_stopped_by_admin(client: TestClient, factory, library, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from app.services.library import LibraryService

    admin(client)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    monkeypatch.setattr("app.main.SessionLocal", factory)
    monkeypatch.setattr(LibraryService, "resolve_media_path", lambda self, item: video)
    assert client.get("/api/library/item-movie/media").status_code == 200
    assert [s["title"] for s in client.get("/api/admin/activity").json()["sessions"]] == ["Dune"]  # the stream request is the report
    (session,) = client.get("/api/admin/activity").json()["sessions"]
    client.post(f"/api/admin/activity/{session['id']}/stop")
    blocked = client.get("/api/library/item-movie/media")
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "stopped_by_admin"


def test_relays_list_and_stop(client: TestClient, factory, library) -> None:  # noqa: ANN001
    admin(client)
    service, _ = build_service()
    register(service, owner="u2")
    activity.install((service,))
    (relay,) = [s for s in activity.list_sessions(factory(), (service,)) if s["method"] == "relay"]
    assert (relay["id"], relay["source"], relay["title"], relay["user"]["name"]) == ("r:stream-A", "remote", "www.twitch.tv", "Sam")
    assert activity.stop(relay["id"], (service,)) is True and activity.stop(relay["id"], (service,)) is False
    (row,) = history(factory)
    assert (row.method, row.source, row.stopped_by_admin) == ("relay", "remote", True)


def test_history_filter_search_and_cursor(client: TestClient, factory) -> None:  # noqa: ANN001
    admin(client)
    base = utcnow() - timedelta(hours=1)
    with factory.begin() as db:
        for index, (user, title) in enumerate([("u1", "Dune"), ("u2", "Severance"), ("u2", "100% Wolf"), ("u2", "Dune: Part Two")]):
            db.add(PlaybackHistory(
                id=f"h{index}", user_id=user, user_name=user, source="library", title=title, subtitle="S1 · E1" if index == 1 else None, item_id=None,
                client={"kind": "web", "name": "Lumina web", "device": None}, method="direct", started_at=base + timedelta(minutes=index),
                ended_at=base + timedelta(minutes=index, seconds=30), watched_seconds=30, stopped_by_admin=False,
            ))
    ids = lambda **params: [r["id"] for r in client.get("/api/admin/activity/history", params=params).json()["items"]]  # noqa: E731
    assert ids() == ["h3", "h2", "h1", "h0"]
    assert ids(user_id="u1") == ["h0"]
    assert ids(q="dune") == ["h3", "h0"] and ids(q="e1") == ["h1"] and ids(q="100%") == ["h2"]
    first = client.get("/api/admin/activity/history", params={"limit": 3}).json()
    assert [r["id"] for r in first["items"]] == ["h3", "h2", "h1"] and first["next_before"] == first["items"][-1]["ended_at"]
    second = client.get("/api/admin/activity/history", params={"limit": 3, "before": first["next_before"]}).json()
    assert [r["id"] for r in second["items"]] == ["h0"] and second["next_before"] is None


def test_history_rows_older_than_90_days_are_pruned_on_write(factory, library, clock) -> None:  # noqa: ANN001
    with factory.begin() as db:
        old = utcnow() - timedelta(days=91)
        db.add(PlaybackHistory(id="old", user_id="u2", user_name="Sam", source="library", title="Old", client={}, method="direct", started_at=old, ended_at=old, watched_seconds=0, stopped_by_admin=False))
    activity.touch("u2", "item-ep", "web")
    clock["now"] += 50
    activity.touch("u2", "item-ep", "web")
    clock["now"] += 100
    activity.sweep()
    assert [r.id == "old" for r in history(factory)] == [False]


def test_proc_parsers_read_fixture_text() -> None:
    assert activity.parse_cpu("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4 5\n") == (150, 1000)
    assert activity.parse_meminfo("MemTotal:  8000 kB\nMemFree: 1 kB\nMemAvailable:  2000 kB\n") == {"used_bytes": 6000 * 1024, "total_bytes": 8000 * 1024}
    assert activity.parse_loadavg("0.50 0.75 1.00 2/300 1234\n") == [0.5, 0.75, 1.0]
    assert activity.parse_uptime("12345.67 9999.1\n") == 12345.67
    stat = "42 (ffmpeg (v2) x) S 1 42 42 0 -1 4194560 100 0 0 0 7 3 0 0 20 0 1 0 100 1000 100 18446744073709551615"
    assert activity.parse_process_jiffies(stat) == 10
    assert activity.parse_cpu(None) is None and activity.parse_meminfo("") is None and activity.parse_loadavg("") is None
    assert activity.parse_uptime(None) is None and activity.parse_process_jiffies("garbage") is None


def test_server_stats_are_null_without_proc_and_cpu_is_a_delta(factory, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(activity, "_read", lambda path: None)
    monkeypatch.setattr(activity, "_cpu_prev", None)
    with factory() as db:
        stats = activity.server_stats(db)
    assert (stats["cpu_percent"], stats["memory"], stats["load_average"], stats["uptime_seconds"], stats["transcoder_cpu_percent"]) == (None, None, None, None, None)
    reads = iter(["cpu 0 0 0 100 0 0 0 0", "cpu 50 0 0 150 0 0 0 0"])
    monkeypatch.setattr(activity, "_read", lambda path: next(reads) if path == "/proc/stat" else None)
    with factory() as db:
        activity.server_stats(db)
        assert activity.server_stats(db)["cpu_percent"] == 50.0


def test_activity_is_admin_only(client: TestClient, factory) -> None:  # noqa: ANN001
    with factory.begin() as db:
        db.add(make_user("member", username="member", password_hash=hash_password(PASSWORD)))
    anonymous = TestClient(app, base_url="http://localhost")
    assert [anonymous.get("/api/admin/activity").status_code, anonymous.get("/api/admin/activity/history").status_code] == [401, 401]
    assert anonymous.post("/api/admin/activity/d:x/stop").status_code in (401, 403)
    member = TestClient(app, base_url="http://localhost")
    admin(member, "member")
    assert [member.get("/api/admin/activity").status_code, member.get("/api/admin/activity/history").status_code] == [403, 403]
    assert member.post("/api/admin/activity/d:x/stop").status_code == 403


def test_schema_step_11_adds_playback_history(factory) -> None:  # noqa: ANN001
    from app import db as db_module

    assert db_module.SCHEMA_VERSION >= 11 and db_module.MIGRATIONS[11] == ()
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM playback_history")).scalar() == 0
