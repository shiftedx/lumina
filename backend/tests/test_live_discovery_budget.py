"""Provider politeness (26-pp, 2.6.1): what live discovery asks of YouTube and Twitch for open pages and clicks.

The real wiring (create_live_discovery + LiveSearch + youtube_live_directory + TwitchGqlDirectory + FollowedLiveChecker +
YtDlpService's extraction seam) runs against counting fakes at the provider edge (yt-dlp, Twitch GQL) on a simulated
clock. A YouTube search costs one request per ~20 results (yt-dlp pages the search API: a 100-result live search logged
"page 5"/"page 6"); a followed-channel probe is a full extraction (~3 requests); a video click ~2; a Twitch GQL call one.
"""
from __future__ import annotations

import math
import types
from pathlib import Path

import httpx

from app.services import live_discovery, provider_budget, youtube_live_directory as yld, yt_dlp_service
from app.services.followed_live import FollowedLiveChecker
from app.services.live_discovery import LiveSearch, create_live_discovery
from app.services.twitch_gql_directory import TwitchGqlDirectory
from app.services.yt_dlp_service import YtDlpService

YOUTUBE_RESULTS_PER_REQUEST = 20
PROBE_REQUESTS = 3
VIDEO_REQUESTS = 2


class Sync:
    def submit(self, job, *args):  # noqa: ANN001, ANN002
        job(*args)


def simulate(
    tmp_path: Path, monkeypatch, *, minutes: float, poll_seconds: float, follows: int = 0, members: int = 1, clicks: int = 0,  # noqa: ANN001
) -> dict:
    """YouTube requests by priority while ``members`` each poll the live snapshot (and their ``follows`` followed
    channels) every ``poll_seconds`` for ``minutes``, and each clicks ``clicks`` videos an hour."""
    now = [1_000_000.0]
    clock = lambda: now[0]  # noqa: E731
    fake_time = types.SimpleNamespace(monotonic=clock, time=clock)
    counts = {"youtube": 0, "twitch": 0, "interactive": 0, "background": 0}

    class FakeYDL:  # yt-dlp at the provider edge
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

        def extract_info(self, url: str, download: bool = False) -> dict:
            if "/results?" in url:
                cost, limit = math.ceil(self.options["playlistend"] / YOUTUBE_RESULTS_PER_REQUEST), self.options["playlistend"]
                info = {"_type": "playlist", "entries": [
                    {"id": f"{url[-24:-18]}{i:05d}", "title": "t", "live_status": "is_live", "concurrent_view_count": 10}
                    for i in range(limit)]}
            elif url.endswith("/live"):
                cost, info = PROBE_REQUESTS, {"id": url, "title": "offline", "live_status": "not_live"}
            else:
                cost, info = VIDEO_REQUESTS, {"id": url[-11:], "title": "v", "webpage_url": url, "formats": [
                    {"format_id": "137", "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "url": "https://rr1.googlevideo.com/v"}]}
            counts["youtube"] += cost
            counts["interactive" if provider_budget.current_priority() == "interactive" else "background"] += cost
            return info

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    def twitch(request: httpx.Request) -> httpx.Response:
        counts["twitch"] += 1
        body = request.content.decode()
        if "games(" in body or "TopGames" in body or "games {" in body:
            edges = [{"node": {"name": f"Game {i}", "broadcastersCount": 100}} for i in range(100)]
            return httpx.Response(200, json={"data": {"games": {"edges": edges}}})
        edges = [{"cursor": "c", "node": {"broadcaster": {"login": f"s{i}"}, "viewersCount": 5, "title": "t"}} for i in range(20)]
        return httpx.Response(200, json={"data": {"game": {"broadcastersCount": 20, "streams": {"edges": edges}}}})

    monkeypatch.setattr(YtDlpService, "build_base_options", lambda self: {"ignoreconfig": True})
    service = YtDlpService(None, ydl_factory=FakeYDL)
    monkeypatch.setattr(yld, "time", fake_time)
    monkeypatch.setattr(yld, "_default_search", lambda q, n: service.youtube_live_search(q, n, max_limit=yld.QUERY_RESULTS))
    monkeypatch.setattr(yld, "_cache", {})
    monkeypatch.setattr(yt_dlp_service, "_PREVIEW_CACHE", {})
    for name in ("youtube", "twitch"):  # a fresh, clock-driven budget per run
        monkeypatch.setattr(provider_budget, name, provider_budget.fresh(name, clock))
    directory = TwitchGqlDirectory(http_client=httpx.Client(transport=httpx.MockTransport(twitch)))
    search = LiveSearch(service.youtube_live_search, directory, gaming=live_discovery.GamingDirectories(directory, clock=clock))
    discovery = create_live_discovery(tmp_path / "live.json", search, clock=clock, executor=Sync(), random=lambda: 0.5)
    checker = FollowedLiveChecker(service.probe_live_source, clock=clock, executor=Sync())
    followed = [[f"https://www.youtube.com/@member{m}channel{i}" for i in range(follows)] for m in range(members)]
    click_every = 3600 / clicks if clicks else math.inf
    elapsed, next_click, clicked = 0.0, 0.0 if clicks else math.inf, 0
    while elapsed < minutes * 60:
        for member in range(members):
            discovery.get_snapshot()
            checker.live_entries(followed[member])
        while next_click <= elapsed:
            for member in range(members):  # every member clicks; half the time the same video as the others
                video = f"v{clicked:09d}" + ("sh" if clicked % 2 else f"m{member}")
                service.preview(f"https://www.youtube.com/watch?v={video}")
            clicked += 1
            next_click += click_every
        now[0] += poll_seconds
        elapsed += poll_seconds
    snapshot = discovery.get_snapshot()
    rows = {category.key: sum(category.key in (item.category_keys or ()) for item in snapshot.items) for category in snapshot.categories}
    return {**counts, "clicks": clicked * members, "rows": rows, "pools": {key: len(search.youtube_pool(key)) for key in rows}}


def _bound(name: str, minutes: float) -> float:
    from app.services import provider_budget

    limits = provider_budget._LIMITS[name]
    return limits["burst"] + limits["per_hour"] * minutes / 60


def test_one_live_tab_open_stays_inside_the_provider_budgets(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Before the budget, opening the Live tab for 5 minutes cost 221 YouTube requests (2,314 an hour)."""
    five = simulate(tmp_path / "five", monkeypatch, minutes=5, poll_seconds=30)
    assert five["youtube"] <= _bound("youtube", 5)
    assert five["twitch"] <= _bound("twitch", 5)
    # Every rail still opens with YouTube's live page (24 results; Twitch fills the rest where it has the category).
    assert all(count >= 24 for count in five["rows"].values())


def test_an_hour_of_open_pages_stays_inside_the_budgets_and_still_fills_the_walls(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    live = simulate(tmp_path / "live", monkeypatch, minutes=60, poll_seconds=30)
    assert live["youtube"] <= _bound("youtube", 60) and live["twitch"] <= _bound("twitch", 60)
    assert sum(size > 0 for size in live["pools"].values()) >= 5  # the deep YouTube lists still refill within the hour
    # Home left open with ten followed YouTube channels (each check a full extraction): before, 3,244 requests an hour.
    home = simulate(tmp_path / "home", monkeypatch, minutes=60, poll_seconds=60, follows=10)
    assert home["youtube"] <= _bound("youtube", 60) and home["twitch"] <= _bound("twitch", 60)


def test_four_members_cost_youtube_what_one_does_and_every_click_goes_through(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """2.6.1: one home IP, several members. Live pages, ten follows each and 15 clicks an hour per member: YouTube sees the
    same total as for one member, background work yields to the clicks, and no click is ever refused."""
    one = simulate(tmp_path / "one", monkeypatch, minutes=60, poll_seconds=30, follows=10, members=1, clicks=15)
    four = simulate(tmp_path / "four", monkeypatch, minutes=60, poll_seconds=30, follows=10, members=4, clicks=15)
    for run in (one, four):
        assert run["youtube"] <= _bound("youtube", 60)
    assert four["background"] <= 300 and four["background"] < one["background"]
    assert four["youtube"] <= one["youtube"] * 1.1
    assert four["interactive"] >= four["clicks"] * VIDEO_REQUESTS // 2  # half the clicks share one extraction


def test_a_rate_limit_answer_pauses_the_provider_for_every_caller_and_honours_retry_after() -> None:
    from app.services.provider_budget import ProviderBudget

    now = [0.0]
    budget = ProviderBudget(per_hour=3600, burst=10, cooldown_seconds=60, clock=lambda: now[0])
    budget.failed(RuntimeError("Unable to download JSON metadata"))  # an ordinary failure pauses nothing
    assert budget.take()
    budget.failed(RuntimeError('ERROR: query "gaming" page 3: HTTP Error 403: Forbidden'))
    assert not budget.take()
    now[0] += 61
    assert budget.take()
    budget.failed(RuntimeError("HTTP Error 429"))  # a second strike doubles the pause
    now[0] += 61
    assert not budget.take()
    now[0] += 60
    assert budget.take()
    budget.failed(RuntimeError("busy"), retry_after=600)  # Retry-After wins when longer
    now[0] += 300
    assert not budget.take()
    now[0] += 301
    assert budget.take() and budget.take(5, keep=4) and not budget.take(1, keep=5)
