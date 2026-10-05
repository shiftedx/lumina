"""Remote entry annotation: the member's saved copy and progress on web results."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import event

from app.models import LibraryItem, PlaybackProgress, RemotePlaybackProgress
from app.schemas import PreviewEntry, RemoteProgressAnnotation, YouTubeSearchResult
from app.services.remote_annotation import annotate_remote_entries, remote_source_identity
from app.services.remote_playback import RemotePlaybackProgressService
from support import make_user

CASES = json.loads((Path(__file__).parent / "remote_identity_cases.json").read_text())
ALICE, BOB = make_user("alice"), make_user("bob")


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["webpage_url"]))
def test_matches_the_client_for_every_parity_case(case) -> None:  # noqa: ANN001
    assert remote_source_identity(case["provider"], case["id"], case["webpage_url"]) == case["identity"]


def _item(session, item_id: str, remote_id: str, *, owner=ALICE, visibility: str = "shared", extractor: str = "youtube", status: str = "available") -> None:  # noqa: ANN001
    session.add(LibraryItem(
        id=item_id, user_id=owner.id, visibility=visibility, extractor=extractor, remote_id=remote_id, title=item_id,
        created_at=datetime(2026, 9, 1), status=status,
    ))


def _remote_progress(session, user, identity: str, position: float, *, completed: bool = False, cleared: bool = False) -> None:  # noqa: ANN001
    canonical = RemotePlaybackProgressService.canonical_source_identity(identity)
    session.add(RemotePlaybackProgress(
        id=f"rp-{user.id}-{position}", user_id=user.id, source_identity=canonical,
        source_identity_key=RemotePlaybackProgressService.source_identity_key(canonical), source_url="https://example.test",
        position_seconds=position, duration_seconds=600, completed=completed, cleared=cleared,
    ))


def _video(video_id: str, **fields) -> YouTubeSearchResult:  # noqa: ANN003
    return YouTubeSearchResult(id=video_id, webpage_url=f"https://www.youtube.com/watch?v={video_id}", source="youtube", **fields)


def test_marks_a_saved_video_and_takes_its_progress_from_the_library(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _item(session, "item-1", "abcdefghijk")
        session.add(PlaybackProgress(id="p1", user_id=ALICE.id, item_id="item-1", position_seconds=312, duration_seconds=1450, completed=False))
        _remote_progress(session, ALICE, "youtube:abcdefghijk", 50)  # ignored: the saved copy's progress wins
        session.commit()
        [entry] = annotate_remote_entries(session, ALICE, [_video("abcdefghijk")])
    assert entry.saved_item_id == "item-1"
    assert entry.progress == RemoteProgressAnnotation(position_seconds=312, duration_seconds=1450, completed=False)


def test_takes_an_unsaved_video_s_progress_from_remote_history_under_any_spelling(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _remote_progress(session, ALICE, "youtube:abcdefghijk", 600, completed=True)
        _remote_progress(session, ALICE, "url:https://www.twitch.tv/somecreator", 30)
        session.commit()
        short, twitch, fresh = annotate_remote_entries(session, ALICE, [
            YouTubeSearchResult(id=None, webpage_url="https://youtu.be/abcdefghijk?si=x", source="youtube"),
            YouTubeSearchResult(id="1", webpage_url="https://WWW.twitch.tv/somecreator#chat", source="twitch"),
            _video("zzzzzzzzzzz"),
        ])
    assert (short.saved_item_id, short.progress) == (None, RemoteProgressAnnotation(position_seconds=600, duration_seconds=600, completed=True))
    assert twitch.progress == RemoteProgressAnnotation(position_seconds=30, duration_seconds=600, completed=False)
    assert (fresh.saved_item_id, fresh.progress) == (None, None)


def test_a_private_item_of_another_member_never_annotates(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _item(session, "bob-private", "abcdefghijk", owner=BOB, visibility="private")
        _item(session, "missing", "bbbbbbbbbbb", status="missing")
        _item(session, "twitch-vod", "ccccccccccc", extractor="twitch:vod")
        _remote_progress(session, BOB, "youtube:abcdefghijk", 90)
        session.commit()
        entries = annotate_remote_entries(session, ALICE, [_video("abcdefghijk"), _video("bbbbbbbbbbb"), _video("ccccccccccc")])
        mine = annotate_remote_entries(session, BOB, [_video("abcdefghijk")])
    assert [(entry.saved_item_id, entry.progress) for entry in entries] == [(None, None)] * 3
    assert mine[0].saved_item_id == "bob-private" and mine[0].progress is None


def test_another_member_s_progress_on_a_shared_item_never_annotates(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _item(session, "shared-1", "abcdefghijk", owner=BOB)
        session.add(PlaybackProgress(id="bp", user_id=BOB.id, item_id="shared-1", position_seconds=700, duration_seconds=900, completed=True))
        session.commit()
        [entry] = annotate_remote_entries(session, ALICE, [_video("abcdefghijk")])
    assert entry.saved_item_id == "shared-1" and entry.progress is None


def test_cleared_or_unstarted_progress_is_no_marker_and_entries_are_copies(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _remote_progress(session, ALICE, "youtube:abcdefghijk", 400, cleared=True)
        _item(session, "item-1", "bbbbbbbbbbb")
        session.add(PlaybackProgress(id="p1", user_id=ALICE.id, item_id="item-1", position_seconds=0, duration_seconds=100, completed=False))
        session.commit()
        original = _video("abcdefghijk")
        cleared, unstarted = annotate_remote_entries(session, ALICE, [original, _video("bbbbbbbbbbb")])
    assert cleared.progress is None and unstarted.saved_item_id == "item-1" and unstarted.progress is None
    assert original.saved_item_id is None and cleared is not original


def test_annotates_follow_feed_entries_by_their_capabilities_provider(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        _item(session, "item-1", "abcdefghijk")
        session.commit()
        [entry] = annotate_remote_entries(session, ALICE, [PreviewEntry(id="abcdefghijk", webpage_url="https://www.youtube.com/watch?v=abcdefghijk")])
    assert isinstance(entry, PreviewEntry) and entry.saved_item_id == "item-1"


def test_answers_120_entries_in_two_queries_and_nothing_for_none(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        for index in range(0, 120, 3):
            _item(session, f"item-{index}", f"v{index:010d}")
        session.commit()
        statements: list[str] = []
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(session.get_bind(), "before_cursor_execute", listener)
        try:
            entries = annotate_remote_entries(session, ALICE, [_video(f"v{index:010d}") for index in range(120)])
            assert annotate_remote_entries(session, ALICE, []) == []
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", listener)
    assert len(statements) == 2
    assert sum(entry.saved_item_id is not None for entry in entries) == 40


# ---- the listed routes ----
import app.main as main  # noqa: E402
from app.schemas import SourceAutomationResponse, YouTubeSearchResponse  # noqa: E402
from app.services.popular_discovery import PopularCategorySnapshot, PopularItem, PopularSnapshot  # noqa: E402
from app.services.yt_dlp_service import YtDlpService  # noqa: E402


def _spy(monkeypatch) -> list[int]:  # noqa: ANN001
    calls: list[int] = []

    def annotate(_db, _user, entries):  # noqa: ANN001, ANN202
        calls.append(len(entries))
        return [entry.model_copy(update={"saved_item_id": "spy"}) for entry in entries]

    monkeypatch.setattr(main, "annotate_remote_entries", annotate)
    return calls


def _popular() -> PopularSnapshot:
    now = datetime(2026, 9, 29, 20, 42)
    item = PopularItem(
        id="v1", title="v1", uploader="Someone", duration=None, thumbnail=None, artwork_url=None,
        webpage_url="https://www.youtube.com/watch?v=v1", view_count=10, availability=None, published_at=None,
        source="youtube", source_label="YouTube", capabilities=None, category_keys=("gaming",),
    )
    return PopularSnapshot(
        items=(item,), categories=(PopularCategorySnapshot(key="gaming", label="Gaming", state="ready", last_success_at=now, next_refresh_at=now),),
        state="ready", refreshing=False, stale=False, last_success_at=now, refreshed_at=now, next_refresh_at=now, error=None,
    )


class _Snapshots:
    def get_snapshot(self):  # noqa: ANN201
        return _popular()

    def candidates(self):  # noqa: ANN201
        return _popular().items


class _Checker:
    def live_entries(self, urls):  # noqa: ANN001, ANN201
        return [YouTubeSearchResult(id="h1", webpage_url="https://www.youtube.com/watch?v=h1")]

    def unavailable_sources(self, urls):  # noqa: ANN001, ANN201
        return {}


def test_popular_and_live_are_annotated_in_one_call_each(monkeypatch, api_client) -> None:  # noqa: ANN001
    calls = _spy(monkeypatch)
    monkeypatch.setattr(main, "popular_discovery", _Snapshots())
    monkeypatch.setattr(main, "live_discovery", _Snapshots())
    monkeypatch.setattr(main, "followed_live_checker", _Checker())
    client = api_client(user=ALICE, base_url="http://localhost")
    popular = client.get("/api/discovery/popular").json()
    live = client.get("/api/discovery/live").json()
    assert popular["items"][0]["saved_item_id"] == "spy"
    assert live["hero"][0]["saved_item_id"] == "spy" and live["items"][0]["saved_item_id"] == "spy"
    # One call per route: Popular's rail and For you share one, Live's hero and items share one (still two queries each).
    assert calls == [2, 2]


@pytest.mark.parametrize("path", ["/api/youtube-search", "/api/source-search"])
def test_searches_are_annotated(monkeypatch, api_client, path: str) -> None:  # noqa: ANN001
    calls = _spy(monkeypatch)
    canned = YouTubeSearchResponse(query="q", items=[_video("abcdefghijk")])
    monkeypatch.setattr(YtDlpService, "youtube_search", lambda self, query, limit=10: canned)
    monkeypatch.setattr(YtDlpService, "source_search", lambda self, query, limit=10: canned)
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda request, credentials=None: ALICE)
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    response = api_client(user=ALICE, base_url="http://localhost").post(path, json={"query": "q", "limit": 10})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["saved_item_id"] == "spy" and calls == [1]


def test_follow_feeds_are_annotated_in_one_call_across_follows(monkeypatch, api_client) -> None:  # noqa: ANN001
    calls = _spy(monkeypatch)
    at = datetime(2026, 9, 29)

    def follow(follow_id: str, count: int) -> SourceAutomationResponse:
        return SourceAutomationResponse(
            id=follow_id, user_id=ALICE.id, label=follow_id, source_url=f"https://www.youtube.com/@{follow_id}", source_type="channel",
            cron_expression="0 */6 * * *", active=True, auto_download=False, format_selection={}, output_profile={},
            rules={}, duplicate_policy="skip_same_source", last_run_summary={}, created_at=at, updated_at=at,
            feed_entries=[PreviewEntry(id=f"{follow_id}{n}", webpage_url=f"https://www.youtube.com/watch?v={follow_id}{n}") for n in range(count)],
        )

    class Service:
        def list_automations(self, user):  # noqa: ANN001, ANN201
            return ["a", "b"]

        def serialize(self, automation):  # noqa: ANN001, ANN201
            return follow(automation, 2 if automation == "a" else 3)

    monkeypatch.setattr(main, "_source_automation_service", lambda db: Service())
    follows = api_client(user=ALICE, base_url="http://localhost").get("/api/automations").json()
    assert [len(entry["feed_entries"]) for entry in follows] == [2, 3]
    assert {entry["saved_item_id"] for follow_ in follows for entry in follow_["feed_entries"]} == {"spy"}
    assert calls == [5]


def test_a_follow_feed_entry_knows_its_channel_and_views() -> None:
    class Ydl:
        def __init__(self, options):  # noqa: ANN001
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

        def extract_info(self, url, download):  # noqa: ANN001, ANN201
            return {"_type": "playlist", "extractor": "youtube:tab", "entries": [{
                "id": "abcdefghijk", "title": "t", "url": "https://www.youtube.com/watch?v=abcdefghijk", "view_count": 42,
                "channel_id": "UCabcdefghijklmnopqrstuv", "channel_url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            }]}

        def sanitize_info(self, info):  # noqa: ANN001, ANN201
            return info

    from app.services.yt_dlp_service import YtDlpService as Service

    class Policy:
        def validate_url(self, url: str) -> str:
            return url

    service = Service(None, ydl_factory=Ydl, network_policy=Policy())
    service.build_base_options = lambda: {"ignoreconfig": True}  # no settings row in this unit test
    service._build_acquisition_options = lambda selection, format_plan: {"ignoreconfig": True}
    preview = service.preview("https://www.youtube.com/@harborfilms/videos")
    [entry] = preview.entries
    assert (entry.channel_id, entry.channel_url, entry.view_count) == (
        "UCabcdefghijklmnopqrstuv", "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", 42,
    )
