"""One follow refresh path for every provider; automatic saving is a separate opt-in."""

from __future__ import annotations

from datetime import datetime

import yt_dlp

from app.models import DownloadJob, SourceAutomation, User
from app.schemas import PreviewResponse, SourceAutomationCreateRequest
from app.services import source_automation
from app.services.channel_discovery import channel_feed_url
from app.services.member_follows import FollowRequest, MemberFollowService
from app.services.source_automation import SourceAutomationService
from app.services.yt_dlp_service import YtDlpService
from support import make_user, memory_session_factory

NOW = datetime(2026, 1, 1, 12, 0)


class FakeJobs:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def stage_enqueue(self, db, payload, user, **kwargs):  # noqa: ANN001, ANN003
        # Admission is another service's job; this fake records attempts and never creates a
        # pending row, so only automation-level uniqueness can prevent a repeat.
        self.enqueued.append(payload.source_url)
        return type("Staged", (), {"id": payload.source_url})()

    def dispatch_staged(self, job):  # noqa: ANN001
        pass


def _preview(url: str, ids: list[str]) -> PreviewResponse:
    return PreviewResponse(
        kind="playlist", title=url, extractor="x", extractor_key="Youtube", webpage_url=url, raw={},
        entries=[{"id": i, "title": f"New {i}", "webpage_url": f"https://www.youtube.com/watch?v={i}"} for i in ids],
    )


def _stub_provider(monkeypatch, pages: dict[str, list[str] | Exception]) -> list[str]:
    inspected: list[str] = []

    def preview(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        inspected.append(url)
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return _preview(url, page)

    monkeypatch.setattr(YtDlpService, "preview", preview)
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    return inspected


def _follow(db, member: User, url: str) -> SourceAutomation:
    MemberFollowService(db, validate_url=lambda value: value).follow_channels(member, [FollowRequest(url, "Channel")])
    db.commit()
    return db.query(SourceAutomation).filter_by(user_id=member.id, source_url=url).one()


def test_unified_feed_owner_scoped(monkeypatch, db_factory, api_client) -> None:
    db = db_factory()
    dana, sam = make_user("dana"), make_user("sam")
    db.add_all([dana, sam])
    _follow(db, dana, "https://www.youtube.com/@creator")
    _follow(db, dana, "https://kick.com/xqc")
    _follow(db, sam, "https://www.youtube.com/@other")
    offline = yt_dlp.utils.DownloadError("offline", exc_info=(None, yt_dlp.utils.UserNotLive(video_id="xqc"), None))
    inspected = _stub_provider(monkeypatch, {
        "https://www.youtube.com/@creator/videos": ["k1", "k2"],
        "https://kick.com/xqc": offline,
        "https://www.youtube.com/@other/videos": ["s1"],
    })

    # New follows are due at once; one sweep reads every provider through one path.
    service = SourceAutomationService(db, FakeJobs())  # type: ignore[arg-type]
    service.process_due(NOW)
    db.commit()
    assert sorted(inspected) == sorted([
        "https://www.youtube.com/@creator/videos", "https://kick.com/xqc", "https://www.youtube.com/@other/videos",
    ])

    def feed_for(member: User, path: str = "/api/automations", method: str = "get") -> dict[str, list[str]]:
        response = getattr(api_client(user=member, base_url="http://localhost"), method)(path)
        assert response.status_code == 200, response.text
        return {row["source_url"]: [entry["id"] for entry in row["feed_entries"]] for row in response.json()}

    assert feed_for(dana) == {"https://www.youtube.com/@creator": ["k1", "k2"], "https://kick.com/xqc": []}
    assert feed_for(sam) == {"https://www.youtube.com/@other": ["s1"]}

    # A refresh request marks only the caller's follows due; the scheduler does the work.
    assert set(feed_for(sam, "/api/follows/refresh", "post")) == {"https://www.youtube.com/@other"}
    db.expire_all()
    assert [a.user_id for a in db.query(SourceAutomation).filter(SourceAutomation.next_check_at.is_(None))] == ["sam"]


def test_follow_does_not_download(monkeypatch) -> None:
    db = memory_session_factory()()
    dana = make_user("dana")
    db.add(dana)
    follow = _follow(db, dana, "https://www.youtube.com/@creator")
    _stub_provider(monkeypatch, {"https://www.youtube.com/@creator/videos": ["v1", "v2"]})
    jobs = FakeJobs()

    SourceAutomationService(db, jobs).process_due(NOW)  # type: ignore[arg-type]
    db.commit()

    assert follow.auto_download is False
    assert [entry["id"] for entry in follow.feed_entries] == ["v1", "v2"]
    assert jobs.enqueued == [] and db.query(DownloadJob).count() == 0
    # A plain automation request is also opt-in: omitting the flag never saves media.
    assert SourceAutomationCreateRequest(label="x", source_url="https://example.com/c").auto_download is False


def test_automation_repeat_no_duplicates(monkeypatch) -> None:
    db = memory_session_factory()()
    dana = make_user("dana")
    db.add(dana)
    follow = _follow(db, dana, "https://www.youtube.com/@creator")
    jobs = FakeJobs()
    service = SourceAutomationService(db, jobs)  # type: ignore[arg-type]
    service.set_auto_download(follow.id, True, dana)
    db.commit()
    _stub_provider(monkeypatch, {"https://www.youtube.com/@creator/videos": ["v1", "v2"]})

    for _ in range(3):  # repeated manual refreshes and scheduled sweeps
        service.request_follow_refresh(dana)
        db.commit()
        service.process_due(NOW)
        db.commit()

    assert jobs.enqueued == ["https://www.youtube.com/watch?v=v1", "https://www.youtube.com/watch?v=v2"]


def test_provider_failure_keeps_cursor(monkeypatch) -> None:
    db = memory_session_factory()()
    dana = make_user("dana")
    db.add(dana)
    follow = _follow(db, dana, "https://www.youtube.com/@creator")
    jobs = FakeJobs()
    service = SourceAutomationService(db, jobs)  # type: ignore[arg-type]
    service.set_auto_download(follow.id, True, dana)
    follow.max_items_per_run = 1
    db.commit()
    url = "https://www.youtube.com/@creator/videos"
    pages: dict[str, list[str] | Exception] = {url: ["v1", "v2"]}
    _stub_provider(monkeypatch, pages)

    service.run_automation(follow.id, dana, now=NOW)
    db.commit()
    pages[url] = yt_dlp.utils.DownloadError("HTTP Error 503")
    source_automation._FEED_CACHE.clear()  # the outage is only seen once the shared feed cache has expired
    failed = service.run_automation(follow.id, dana, now=NOW)
    db.commit()
    # The outage is visible on this source while its last-known feed stays.
    assert failed.status == "failed" and "503" in (follow.last_error or "")
    assert [entry["id"] for entry in follow.feed_entries] == ["v1", "v2"]

    pages[url] = ["v1", "v2"]
    source_automation._FEED_CACHE.clear()
    service.run_automation(follow.id, dana, now=NOW)
    db.commit()
    # v2 was not marked handled by the run limit or the failed page: it is admitted now, once.
    assert jobs.enqueued == ["https://www.youtube.com/watch?v=v1", "https://www.youtube.com/watch?v=v2"]
    assert follow.last_error is None


def test_channel_feed_url_reads_uploads() -> None:
    assert channel_feed_url("https://www.youtube.com/@creator") == "https://www.youtube.com/@creator/videos"
    assert channel_feed_url("https://www.youtube.com/channel/UCabc/") == "https://www.youtube.com/channel/UCabc/videos"
    for unchanged in ("https://www.youtube.com/@creator/streams", "https://kick.com/xqc", "https://www.twitch.tv/x"):
        assert channel_feed_url(unchanged) == unchanged


def test_two_members_following_one_channel_share_one_extraction(monkeypatch, db_factory) -> None:
    db = db_factory()
    dana, sam = make_user("dana"), make_user("sam")
    db.add_all([dana, sam])
    dana_follow = _follow(db, dana, "https://www.youtube.com/@creator")
    sam_follow = _follow(db, sam, "https://www.youtube.com/@creator")
    inspected = _stub_provider(monkeypatch, {"https://www.youtube.com/@creator/videos": ["v1"]})
    service = SourceAutomationService(db, FakeJobs())  # type: ignore[arg-type]

    service.run_automation(dana_follow.id, dana, now=NOW)
    service.run_automation(sam_follow.id, sam, now=NOW)
    assert len(inspected) == 1 and [e["id"] for e in sam_follow.feed_entries] == ["v1"]

    source_automation._FEED_CACHE.clear()  # the shared entry expired
    service.run_automation(sam_follow.id, sam, now=NOW)
    assert len(inspected) == 2


def test_a_visit_refreshes_only_follows_not_checked_in_ten_minutes(db_factory) -> None:
    db = db_factory()
    dana = make_user("dana")
    db.add(dana)
    fresh, stale = _follow(db, dana, "https://www.youtube.com/@fresh"), _follow(db, dana, "https://www.youtube.com/@stale")
    now = source_automation.utcnow()
    later = now + source_automation.timedelta(days=1)
    fresh.last_checked_at, fresh.next_check_at = now - source_automation.timedelta(minutes=5), later
    stale.last_checked_at, stale.next_check_at = now - source_automation.timedelta(minutes=11), later
    SourceAutomationService(db, FakeJobs()).request_follow_refresh(dana)  # type: ignore[arg-type]
    assert (fresh.next_check_at, stale.next_check_at) == (later, None)


def test_an_exhausted_youtube_budget_reschedules_the_follow_without_a_failure(monkeypatch, db_factory) -> None:
    from app.services import provider_budget

    db = db_factory()
    dana = make_user("dana")
    db.add(dana)
    follow = _follow(db, dana, "https://www.youtube.com/@creator")
    seen: list[str] = []

    def spent(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(provider_budget.current_priority())
        raise provider_budget.BudgetExhausted("background budget spent")

    monkeypatch.setattr(YtDlpService, "preview", spent)
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    service = SourceAutomationService(db, FakeJobs())  # type: ignore[arg-type]
    notified: list[str] = []
    monkeypatch.setattr(service, "_notify_failure_after_commit", lambda *a: notified.append("x"))

    run = service.run_automation(follow.id, dana, now=NOW)
    db.commit()
    assert seen == ["background"] and run.status == "deferred" and notified == []
    assert follow.next_check_at == NOW + source_automation.BUDGET_RETRY
    assert follow.last_error is None and follow.last_checked_at is None and follow.run_lease_id is None
