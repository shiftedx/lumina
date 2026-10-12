"""Kick as a first-class followable source, honest about what anonymous access allows."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.main as main
from app.models import SourceAutomation, User
from app.schemas import YouTubeSearchResult
from app.services.channel_discovery import normalize_channel_source_url
from app.services.followed_live import FollowedLiveChecker
from app.services.member_follows import FollowRequest, MemberFollowService
from app.services.source_automation import SourceAutomationService
from app.services.yt_dlp_service import YtDlpService, _PREVIEW_CACHE
from support import make_user, memory_session_factory
from tests.test_v1_kick_resolve import VOD_ID, _fake_extract, _service


class ImmediateExecutor:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def _follow(db, member: User, *urls: str) -> None:
    MemberFollowService(db, validate_url=lambda url: url).follow_channels(
        member, [FollowRequest(source_url=url, display_name="Channel") for url in urls]
    )
    db.commit()


def test_kick_filter_and_follow_roundtrip(monkeypatch) -> None:
    _PREVIEW_CACHE.clear()
    _fake_extract(monkeypatch, [])
    service = _service()

    # Browsing a Kick VOD exposes its canonical, followable channel address.
    vod = service.preview(f"https://www.kick.com/XQC/videos/{VOD_ID}")
    assert vod.raw["channel_url"] == "https://kick.com/xqc"
    # Every public link form and host variant names one channel; odd forms are unusable.
    for variant in ("https://WWW.kick.com/XQC/", f"kick.com/xqc/videos/{VOD_ID}", "https://kick.com/xqc?clip=clip_1"):
        assert normalize_channel_source_url(variant) == "https://kick.com/xqc"
    assert normalize_channel_source_url("https://kick.com:8443/xqc") is None
    assert normalize_channel_source_url("https://kick.com/categories/games") is None

    db = memory_session_factory()()
    member = make_user("dana")
    db.add(member)
    _follow(db, member, "https://kick.com/Buddha", "https://www.kick.com/buddha/")
    [follow] = db.query(SourceAutomation).all()
    assert follow.source_url == "https://kick.com/buddha" and follow.auto_download is False

    # The follow's scheduled refresh reads the live channel into its feed.
    class NoJobs:
        def stage_enqueue(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("following never acquires")

    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    real_preview = YtDlpService.preview
    monkeypatch.setattr(YtDlpService, "preview", lambda _self, url, **kw: real_preview(service, url, **kw))
    automations = SourceAutomationService(db, NoJobs())  # type: ignore[arg-type]
    run = automations.run_automation(follow.id, member)
    db.commit()
    assert run.status == "completed", run.error
    assert run.discovered_count == 1
    # Kick cannot be saved yet, so automatic saving is refused rather than silently queued.
    with pytest.raises(ValueError, match="Kick downloads are not supported"):
        automations.set_auto_download(follow.id, True, member)

    # An offline Kick channel is a successful check with nothing new, not an outage.
    offline = SourceAutomation(**{
        column.name: getattr(follow, column.name) for column in SourceAutomation.__table__.columns
    } | {"id": "offline", "source_url": "https://kick.com/offline"})
    db.add(offline)
    db.commit()
    run = automations.run_automation("offline", member)
    assert run.status == "completed" and run.discovered_count == 0

    # The same follow is live-checked through the public channel endpoint.
    checker = FollowedLiveChecker(
        lambda url: YtDlpService.probe_live_source(service, url), executor=ImmediateExecutor()
    )
    followed = sorted(MemberFollowService(db).existing_follow_identities(member))
    checker.live_entries(followed)
    [live] = checker.live_entries(followed)
    assert (live.source, live.webpage_url) == ("kick", "https://kick.com/buddha")


def test_kick_outage_is_partial(monkeypatch, db_factory, api_client) -> None:
    def probe(url: str) -> YouTubeSearchResult | None:
        if "kick.com" in url:
            raise RuntimeError("403: Kick refused the anonymous request")
        return YouTubeSearchResult(id=url, title="live", webpage_url=url, source="twitch" if "twitch" in url else "youtube")

    checker = FollowedLiveChecker(probe, clock=lambda: 1_700_000_000.0, executor=ImmediateExecutor())
    factory = db_factory
    member = make_user("dana")
    with factory() as db:
        db.add(member)
        _follow(db, member, "https://www.youtube.com/@creator", "https://www.twitch.tv/streamer", "https://kick.com/xqc")
    checker.live_entries(sorted(MemberFollowService(factory()).existing_follow_identities(member)))

    class Live:
        def get_snapshot(self):
            from tests.test_live_discovery_api import _snapshot

            return _snapshot()

    monkeypatch.setattr(main, "live_discovery", Live())
    monkeypatch.setattr(main, "followed_live_checker", checker)
    monkeypatch.setattr(main, "_remote_artwork_url", lambda _url: None)

    payload = api_client(user=member, base_url="http://localhost").get("/api/discovery/live").json()

    # YouTube and Twitch rows stay usable; Kick's failure is labelled with its check time.
    assert sorted(item["source"] for item in payload["hero"]) == ["twitch", "youtube"]
    assert payload["followed_unavailable"] == [{"source": "kick", "checked_at": "2023-11-14T22:13:20Z"}]


def test_kick_refresh_dedup() -> None:
    release = threading.Event()
    calls: list[str] = []

    def probe(url: str) -> None:
        calls.append(url)
        release.wait(5)
        return None

    pool = ThreadPoolExecutor(max_workers=4)
    checker = FollowedLiveChecker(probe, executor=pool)
    db = memory_session_factory()()
    viewers = [make_user("a"), make_user("b"), make_user("c")]
    db.add_all(viewers)
    for viewer, url in zip(viewers, ("https://kick.com/XQC", "https://www.kick.com/xqc/", "kick.com/xqc")):
        _follow(db, viewer, url)
    identities = [sorted(MemberFollowService(db).existing_follow_identities(viewer)) for viewer in viewers]
    # Stable channel identity across viewers and link forms.
    assert identities == [["https://kick.com/xqc"]] * 3

    with ThreadPoolExecutor(max_workers=6) as viewers_pool:
        list(viewers_pool.map(checker.live_entries, identities * 2))
    release.set()
    pool.shutdown(wait=True)
    # Six concurrent viewer requests coalesce into one bounded provider probe.
    assert calls == ["https://kick.com/xqc"]
