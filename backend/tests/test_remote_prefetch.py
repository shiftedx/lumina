"""Time to the right picture: one extraction per source, warmed on intent, never a storm."""
from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

from support import make_user, memory_session_factory

import app.services.yt_dlp_service as yt_dlp_service_module
from app.services.network_policy import PublicSourcePolicy
from app.services.remote_prefetch import IntentPrefetcher
from app.services.yt_dlp_service import YtDlpService, _PREVIEW_CACHE

SOURCE = "https://www.youtube.com/watch?v=prefetch-fixture"
PUBLIC_ANSWER = (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))


class _SlowYDL:
    extractions = 0

    def __init__(self, options: dict) -> None:
        del options

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> bool:
        return False

    def extract_info(self, source_url: str, download: bool = False) -> dict:
        del download
        type(self).extractions += 1
        time.sleep(0.2)
        return {"id": "prefetch-fixture", "title": "Fixture", "webpage_url": source_url, "extractor": "Youtube", "extractor_key": "youtube"}

    def sanitize_info(self, info: dict) -> dict:
        return info


def test_a_click_during_a_prefetch_joins_its_extraction_instead_of_starting_another(monkeypatch) -> None:
    monkeypatch.setattr(yt_dlp_service_module, "PolicyYoutubeDL", lambda options, **_kwargs: _SlowYDL(options))
    monkeypatch.setattr(yt_dlp_service_module, "PublicSourcePolicy", lambda: PublicSourcePolicy(resolver=lambda *a, **k: [PUBLIC_ANSWER]))
    _PREVIEW_CACHE.clear()
    _SlowYDL.extractions = 0
    factory = memory_session_factory()
    results: list[str | None] = []

    def preview() -> None:
        with factory() as db:
            results.append(YtDlpService(db).preview(SOURCE).title)

    threads = [threading.Thread(target=preview) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert results == ["Fixture", "Fixture"]
    assert _SlowYDL.extractions == 1
    _PREVIEW_CACHE.clear()


def test_prefetch_dedupes_a_source_and_caps_each_member_at_two_extractions() -> None:
    prefetcher = IntentPrefetcher(max_per_member=2)
    gate = threading.Event()
    started: list[str] = []

    def job(name: str):
        def run() -> None:
            started.append(name)
            gate.wait(5)
        return run

    assert prefetcher.submit("ann", "https://a", job("a")) is True
    assert prefetcher.submit("bob", "https://a", job("a-again")) is False  # one extraction per source, whoever asks
    assert prefetcher.submit("ann", "https://b", job("b")) is True
    assert prefetcher.submit("ann", "https://c", job("c")) is False  # ann already has two running
    assert prefetcher.submit("bob", "https://d", job("d")) is True  # the cap is per member
    gate.set()
    prefetcher.drain()
    assert sorted(started) == ["a", "b", "d"]
    assert prefetcher.submit("ann", "https://c", job("c")) is True  # finished work frees the slot and the source
    prefetcher.drain()


def test_prefetch_swallows_extraction_failures() -> None:
    prefetcher = IntentPrefetcher()

    def boom() -> None:
        raise RuntimeError("provider down")

    assert prefetcher.submit("ann", "https://a", boom) is True
    prefetcher.drain()
    assert prefetcher.submit("ann", "https://a", boom) is True


def test_prefetch_endpoint_needs_a_member_and_warms_the_members_own_preview(api_client, monkeypatch) -> None:
    from app import main

    submitted: list[tuple[str, str]] = []
    warmed: list[tuple[str, str | None]] = []

    def submit(member_id: str, source_url: str, job, **_kwargs) -> bool:  # noqa: ANN001
        submitted.append((member_id, source_url))
        job()
        return True

    def preview(self, source_url, lazy_playlist=True, *, format_selection=None, entries_limit=None):  # noqa: ANN001
        warmed.append((source_url, format_selection.preset if format_selection else None))
        return SimpleNamespace(kind="video", capabilities=None, raw={"id": "prefetch-fixture"})

    indexed: list[tuple[str, frozenset[str]]] = []
    monkeypatch.setattr(main.remote_prefetch, "submit", submit)
    monkeypatch.setattr(YtDlpService, "preview", preview)
    monkeypatch.setattr(main.remote_streams, "warm_dash", lambda *, owner_user_id, info, browser_capabilities: indexed.append((owner_user_id, browser_capabilities.supported_profiles)))

    assert api_client(base_url="http://localhost").post("/api/remote/prefetch", json={"source_url": SOURCE}).status_code in {400, 401, 403}
    assert submitted == []  # nobody signed in: nothing resolves
    response = api_client(user=make_user("ann"), base_url="http://localhost").post("/api/remote/prefetch", json={"source_url": SOURCE, "supported_profiles": ["dash-segment-base", "mp4-avc-aac"]})
    assert (response.status_code, response.json()) == (202, {"accepted": True})
    assert submitted == [("ann", SOURCE)]
    assert warmed == [(SOURCE, "best")]  # the member's own format default, so the click finds the same cache entry
    assert indexed == []  # DASH indexes are read when the click plays, never on a hover


def test_a_dash_preview_reads_its_indexes_while_the_browser_loads_the_player(monkeypatch) -> None:
    """26-pp: the manifest request came ~0.4 s after the preview answered (render, dash.js import), and only then did the
    index probes start. The preview starts them, so the manifest finds them read or in flight."""
    import asyncio
    from contextlib import nullcontext

    from app import main
    from app.models import User
    from app.schemas import PreviewRequest, PreviewResponse

    user = User(id="owner-1", username="owner", role="viewer", is_active=True)
    monkeypatch.setattr(main, "resolve_request_user_snapshot", lambda *a, **k: user)
    monkeypatch.setattr(main, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(main, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main, "_playback_ceiling", lambda _user_id: None)
    monkeypatch.setattr(YtDlpService, "preview", lambda self, source_url, *a, **k: PreviewResponse(kind="video", title="Clip", webpage_url=source_url, raw={"id": "clip", "extractor_key": "Youtube"}))
    descriptor = {"status": "ready", "stream_id": "s1", "transport": "dash", "media_kind": "video", "playback_url": "/api/remote-streams/s1/dash/1/manifest.mpd", "has_video": True, "has_audio": True, "seekable": True}
    monkeypatch.setattr(main.remote_streams, "register", lambda **kwargs: descriptor)
    submitted: list[tuple[str, str]] = []
    warmed: list[str] = []

    def submit(member_id: str, key: str, job, **_kwargs) -> bool:  # noqa: ANN001
        submitted.append((member_id, key))
        job()
        return True

    monkeypatch.setattr(main.remote_prefetch, "submit", submit)
    monkeypatch.setattr(main.remote_streams, "warm_dash", lambda *, owner_user_id, info, browser_capabilities: warmed.append(info["id"]))

    asyncio.run(main.preview(PreviewRequest(source_url=SOURCE, supported_profiles=["dash-segment-base"]), object()))
    assert submitted == [("owner-1", "dash:s1")] and warmed == ["clip"]

    descriptor = {**descriptor, "transport": "progressive", "playback_url": "/api/remote-streams/s1/content"}
    monkeypatch.setattr(main.remote_streams, "register", lambda **kwargs: descriptor)
    asyncio.run(main.preview(PreviewRequest(source_url=SOURCE), object()))
    assert len(submitted) == 1  # nothing to index


def test_household_cap_stops_a_fourth_concurrent_prefetch_but_not_the_click_path() -> None:
    prefetcher = IntentPrefetcher()
    gate = threading.Event()
    wait = lambda: gate.wait(5)  # noqa: E731
    assert [prefetcher.submit(member, f"https://{member}", wait) for member in ("ann", "bob", "cy", "di")] == [True, True, True, False]
    assert prefetcher.submit("di", "dash:s1", wait, household_cap=False) is True  # a click's own warm-up is not a hover
    gate.set()
    prefetcher.drain()


def test_prefetch_runs_at_prefetch_priority_and_a_spent_budget_is_dropped_quietly(api_client, monkeypatch) -> None:
    from app import main
    from app.services import provider_budget

    seen: list[str] = []

    def preview(self, source_url, lazy_playlist=True, *, format_selection=None, entries_limit=None):  # noqa: ANN001
        seen.append(provider_budget.current_priority())
        raise provider_budget.BudgetExhausted("prefetch budget spent")

    monkeypatch.setattr(main.remote_prefetch, "submit", lambda member_id, source_url, job, **_k: (job(), True)[1])
    monkeypatch.setattr(YtDlpService, "preview", preview)
    client = api_client(user=make_user("ann"), base_url="http://localhost")
    assert client.post("/api/remote/prefetch", json={"source_url": SOURCE}).json() == {"accepted": True}
    assert seen == ["prefetch"]  # BudgetExhausted never reached the response

    provider_budget.youtube.failed(RuntimeError("HTTP Error 429"))  # YouTube paused the household
    assert client.post("/api/remote/prefetch", json={"source_url": SOURCE}).json() == {"accepted": False}
    assert seen == ["prefetch"]  # skipped before any work
