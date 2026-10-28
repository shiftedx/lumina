"""The Connected-app Home layout shares the member's web preference, not its API session."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.main import app
from app.models import MediaTitle, PlaybackProgress, RecoEvent, User, UserSettings, utcnow
from app.media_schemas import RecoAnnotation, TitleRow, TitleRowsResponse
from app.services.reco import Candidate, Ranked, ServedList
from app.services.reco.events import served_lists
from app.services.title_summaries import title_summaries
from app.services.watch_queue import WatchQueueService
from app.services.user_settings import UserSettingsService
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, FILE, MOVIE_1080, S1E1, SECRET_SERIES, SERIES, TRACKS, T0, jellyfin_household, mediabrowser, seed_gallery, uid


BASE = "/Lumina/Home/layout"
ROWS = "/Lumina/Home/rows"
HOME_IDS = (
    "continue", "next_up", "live", "watchlist", "new_in_library", "because_you_watched", "recommended",
    "picked_for_you", "collections", "from_follows", "recently_saved", "new_anime", "recent_music", "continue_listening",
)


def call(client: TestClient, method: str, *, token: str | None = ALICE_TOKEN, **kwargs):  # noqa: ANN201
    return client.request(method, BASE, headers=mediabrowser(token) if token else {}, **kwargs)


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def test_layout_saves_order_hide_and_preserves_shared_ui_preferences(native: TestClient) -> None:
    with db_module.SessionLocal.begin() as db:
        settings = UserSettingsService(db).ensure_for_user(db.get(User, ALICE))
        settings.ui_prefs = {"theme": "dark", "autoplay_up_next": False}

    layout = [{"id": "watchlist", "visible": False}, {"id": "continue", "visible": True}]
    saved = call(native, "PUT", json={"shelves": layout})
    assert saved.status_code == 200, saved.text
    expected = [*layout, *({"id": shelf_id, "visible": True} for shelf_id in HOME_IDS if shelf_id not in {"watchlist", "continue"})]
    assert saved.json() == {"shelves": expected}
    assert call(native, "GET").json() == {"shelves": expected}

    with db_module.SessionLocal() as db:
        settings = db.scalar(select(UserSettings).where(UserSettings.user_id == ALICE))
        assert settings is not None
        assert settings.ui_prefs == {"theme": "dark", "autoplay_up_next": False, "home_shelves": expected}

    # A Connected-app credential is restricted to its root Jellyfin namespace.
    assert native.get("/api/settings/me", headers=mediabrowser(ALICE_TOKEN)).status_code == 401


def test_layout_normalizes_malformed_stored_values_like_the_website(native: TestClient) -> None:
    malformed = [
        {"id": "watchlist", "visible": False},
        {"id": "watchlist", "visible": True},
        {"id": "unknown", "visible": False},
        {"id": "live", "visible": "yes"},
        None,
        *({"id": "unknown"} for _ in range(59)),
        {"id": "continue", "visible": False},  # entry 65: the stored cap drops it
    ]
    with db_module.SessionLocal.begin() as db:
        settings = UserSettingsService(db).ensure_for_user(db.get(User, ALICE))
        settings.ui_prefs = {"home_shelves": malformed}

    response = call(native, "GET")
    assert response.status_code == 200
    shelves = response.json()["shelves"]
    assert shelves[:3] == [
        {"id": "watchlist", "visible": False},
        {"id": "live", "visible": True},
        {"id": "continue", "visible": True},
    ]
    assert [shelf["id"] for shelf in shelves] == ["watchlist", "live", *[shelf_id for shelf_id in HOME_IDS if shelf_id not in {"watchlist", "live"}]]


def test_layout_validates_member_scope_and_null_reset(native: TestClient) -> None:
    assert call(native, "GET").json() == {"shelves": None}
    assert call(native, "GET", token=BOB_TOKEN).json() == {"shelves": None}
    assert call(native, "GET", token=None).status_code == 401

    assert call(native, "PUT", json={"shelves": [{"id": "continue", "visible": False}]}).status_code == 200
    assert call(native, "GET", token=BOB_TOKEN).json() == {"shelves": None}
    assert call(native, "PUT", json={"shelves": None}).json() == {"shelves": None}
    assert call(native, "GET").json() == {"shelves": None}

    duplicate = [{"id": "continue", "visible": True}, {"id": "continue", "visible": False}]
    assert call(native, "PUT", json={"shelves": duplicate}).status_code == 422
    assert call(native, "PUT", json={"shelves": [{"id": "not-a-shelf", "visible": True}]}).status_code == 422
    assert call(native, "PUT", json={"shelves": [{"id": "continue", "visible": True}] * 65}).status_code == 422


def test_rows_follow_the_shared_layout_and_only_serve_visible_member_media(native: TestClient, tmp_path: Path) -> None:
    with db_module.SessionLocal.begin() as db:
        seed_gallery(db, tmp_path / "media")
        series = db.get(MediaTitle, SERIES)
        series.images = {**series.images,
                         "Backdrop": {"path": "Show/poster.png", "tag": "series-backdrop"},
                         "Logo": {"path": "Show/poster.png", "tag": "series-logo"}}
        db.add_all([
            PlaybackProgress(id=uid(900), user_id=ALICE, item_id=FILE[S1E1], position_seconds=90, duration_seconds=1500, last_watched_at=T0),
            PlaybackProgress(id=uid(901), user_id=ALICE, item_id=TRACKS[0], position_seconds=30, duration_seconds=180, last_watched_at=T0),
        ])

    # WatchQueueService owns its write transaction, so do not call it inside
    # Session.begin(); the Home route itself only reads its member-scoped view.
    with db_module.SessionLocal() as db:
        alice = db.get(User, ALICE)
        assert alice is not None
        WatchQueueService(db).add(alice, library_item_id=MOVIE_1080)
        WatchQueueService(db).add(alice, library_item_id=FILE[S1E1])
        settings = UserSettingsService(db).ensure_for_user(alice)
        settings.ui_prefs = {
            "home_shelves": [
                {"id": "watchlist", "visible": True},
                {"id": "new_anime", "visible": True},
                {"id": "recent_music", "visible": True},
                {"id": "continue_listening", "visible": True},
            ]
        }
        db.commit()

    response = native.get(ROWS, headers=mediabrowser(ALICE_TOKEN))
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert [row["shelf_id"] for row in rows[:4]] == ["watchlist", "new_anime", "recent_music", "continue_listening"]
    assert rows[0]["shape"] == "still"
    assert rows[0]["items"][0]["Name"].startswith("Movie")
    assert {item["Name"] for item in rows[1]["items"]} >= {"Show A", "Film"}
    assert rows[2]["shape"] == "square"
    assert rows[2]["items"][0]["Type"] == "MusicAlbum"
    assert rows[3]["items"][0]["Type"] == "Audio"
    assert all(item["Name"] != "Hidden" for row in rows for item in row["items"])

    # Home carries the local title facts needed for in-memory native refinement.
    # Generic Jellyfin item pages remain schema-compatible and do not grow this field.
    watchlist_item = next(item for item in rows[0]["items"] if item["LuminaCategory"] == "movies")
    assert isinstance(watchlist_item.get("Genres"), list)
    queued_episode = next(item for item in rows[0]["items"] if item.get("SeriesId"))
    assert queued_episode["LuminaCategory"] == "shows"
    assert queued_episode["SeriesId"]
    assert queued_episode["Genres"] == ["Drama"]  # inherited from its series; the episode itself has none
    assert queued_episode["Id"] == FILE[S1E1].replace("-", "")  # retain the saved playable version
    assert queued_episode["Type"] == "Episode"
    assert queued_episode["Name"] == "Pilot"
    assert queued_episode["SeriesName"] == "Show"
    assert queued_episode["IndexNumber"] == 1 and queued_episode["ParentIndexNumber"] == 1
    assert queued_episode["ParentBackdropItemId"] == SERIES.replace("-", "")
    assert queued_episode["ParentBackdropImageTags"]
    assert queued_episode["ParentLogoItemId"] == SERIES.replace("-", "")
    assert queued_episode["ParentLogoImageTag"]
    assert queued_episode["ParentPrimaryImageItemId"] == SERIES.replace("-", "")
    assert queued_episode["ParentPrimaryImageTag"]
    anime = rows[1]["items"]
    assert anime and all(item["LuminaCategory"] == "anime" for item in anime)
    generic = native.get("/Items", params={"IncludeItemTypes": "Movie", "Recursive": "true"}, headers=mediabrowser(ALICE_TOKEN))
    assert generic.status_code == 200
    assert all("LuminaCategory" not in item for item in generic.json()["Items"])

    # Connected-app credentials stay in their own namespace, and Bob cannot
    # read Alice's watchlist or playback rows.
    assert native.get(ROWS).status_code == 401
    bob = native.get(ROWS, headers=mediabrowser(BOB_TOKEN))
    assert bob.status_code == 200
    assert {row["shelf_id"] for row in bob.json()["rows"]}.isdisjoint({"watchlist", "continue_listening"})


def test_native_recommendation_retains_annotation_and_feedback_is_member_scoped(native: TestClient, tmp_path: Path, monkeypatch) -> None:
    from app.routers import jellyfin_home

    with db_module.SessionLocal.begin() as db:
        seed_gallery(db, tmp_path / "media")

    annotation = RecoAnnotation(list_id="1234567890abcdef", key=SERIES, position=3,
                                slot="exploit", reason_code="like_anchor", reason="Shared subject")

    def rows_for(user, db):
        title = title_summaries(db, user, [SERIES])[0].model_copy(update={"reco": annotation})
        return TitleRowsResponse(rows=[TitleRow(id="recommended", kind="recommended", title="Recommended for you", items=[title])])

    monkeypatch.setattr(jellyfin_home.discovery, "get_home_title_rows", rows_for)
    response = native.get(ROWS, headers=mediabrowser(ALICE_TOKEN))
    assert response.status_code == 200, response.text
    card = next(row for row in response.json()["rows"] if row["shelf_id"] == "recommended")["items"][0]
    assert card["LuminaReco"] == annotation.model_dump(mode="json")
    served_lists.put(ServedList(list_id=annotation.list_id, user_id=ALICE, surface="home_recommended",
        context_key="-", created_at=utcnow(), items=(Ranked(candidate=Candidate(
            key=SERIES, target_kind="title", title="Show", channel_key=None, channel_name=None,
            tokens=frozenset(), published_at=None, sources=0), position=3, slot="exploit",
            score=1.0, p_shown=None, reason_code="like_anchor", reason="Shared subject"),)))
    event = {"kind": "impression", "list_id": annotation.list_id, "key": SERIES, "age_ms": 0}
    assert native.post("/Lumina/Home/reco/events", json={"events": [event]}, headers=mediabrowser(BOB_TOKEN)).status_code == 204
    with db_module.SessionLocal() as db:
        assert db.scalars(select(RecoEvent).where(RecoEvent.user_id == BOB, RecoEvent.list_id == annotation.list_id)).all() == []
    assert native.post("/Lumina/Home/reco/events", json={"events": [event]}, headers=mediabrowser(ALICE_TOKEN)).status_code == 204
    with db_module.SessionLocal() as db:
        assert db.scalars(select(RecoEvent).where(RecoEvent.user_id == ALICE, RecoEvent.list_id == annotation.list_id)).first() is not None
    denied = native.post("/Lumina/Home/reco/suppressions", json={"scope": "title", "title_id": SECRET_SERIES},
                         headers=mediabrowser(ALICE_TOKEN))
    assert denied.status_code == 404
    hidden = native.post("/Lumina/Home/reco/suppressions", json={"scope": "title", "title_id": SERIES,
        "key": SERIES, "list_id": annotation.list_id}, headers=mediabrowser(ALICE_TOKEN))
    assert hidden.status_code == 200, hidden.text
    assert native.get("/Lumina/Settings/suppressions", headers=mediabrowser(BOB_TOKEN)).json()["titles"] == []
    assert native.delete(f"/Lumina/Settings/suppressions/{hidden.json()['id']}", headers=mediabrowser(ALICE_TOKEN)).status_code == 204



def test_external_home_sources_are_opt_in_member_scoped_and_bounded(native: TestClient, monkeypatch) -> None:
    from types import SimpleNamespace
    from app import main
    from app.models import AppSettings, SourceAutomation, MemberAccess

    public = {"title": "Example video", "webpage_url": "https://www.youtube.com/watch?v=example", "thumbnail": "https://i.ytimg.com/vi/example/hqdefault.jpg", "uploader": "Example channel"}
    monkeypatch.setattr(main, "live_now", lambda *_: SimpleNamespace(hero=[public], items=[public]))
    monkeypatch.setattr(main, "home_recommendations", lambda *_: SimpleNamespace(items=[public]))
    with db_module.SessionLocal.begin() as db:
        db.add(SourceAutomation(id=uid(3000), user_id=ALICE, label="Example follow", source_url="https://www.youtube.com/@example", source_type="channel", cron_expression="0 * * * *", feed_entries=[
            {**public, "webpage_url": "https://www.youtube.com/watch?v=follow", "thumbnail": "https://127.0.0.1/private"},
            {**public, "webpage_url": "javascript:alert(1)"},
        ]))
    assert all("external_items" not in row for row in native.get(ROWS, headers=mediabrowser(ALICE_TOKEN)).json()["rows"])
    disabled = native.get(ROWS, params={"include_external": True}, headers=mediabrowser(ALICE_TOKEN))
    assert not any(row.get("external_items") for row in disabled.json()["rows"])
    with db_module.SessionLocal.begin() as db:
        settings = db.get(AppSettings, 1)
        assert settings is not None
        settings.native_streaming_enabled = True
    rows = native.get(ROWS, params={"include_external": True}, headers=mediabrowser(ALICE_TOKEN)).json()["rows"]
    external = {row["shelf_id"]: row for row in rows if row.get("external_items")}
    assert set(external) == {"live", "picked_for_you", "from_follows"}
    assert len(external["live"]["external_items"]) == 1  # followed/popular duplicate
    assert external["from_follows"]["external_items"][0]["url"].endswith("v=follow")
    assert external["from_follows"]["external_items"][0]["artwork_url"] is None
    assert len(external["from_follows"]["external_items"]) == 1
    bob = native.get(ROWS, params={"include_external": True}, headers=mediabrowser(BOB_TOKEN))
    assert not any(row["shelf_id"] == "from_follows" for row in bob.json()["rows"])
    # Browser and native source discovery share the same member restrictions.
    with db_module.SessionLocal.begin() as db:
        access = db.get(MemberAccess, ALICE)
        if access is None:
            access = MemberAccess(user_id=ALICE)
            db.add(access)
        access.streaming = {"youtube": False, "live": False}
    blocked = native.get(ROWS, params={"include_external": True}, headers=mediabrowser(ALICE_TOKEN))
    assert blocked.status_code == 200
    assert not any(row.get("external_items") for row in blocked.json()["rows"])
