"""Member access hardening (2.8.0 review): every acquisition entry, mirror hosts, resolved sources, chat replay,
automations, prefetch, collection names, add-item parity, cached counts and invite presets.

Builds on test_member_access_enforcement's household: shared episodes and the movie belong to Alice; Bob is the member
restricted here, Boss the admin.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AutomationRun, HouseholdCollection, SourceAutomation
from app.schemas import MediaSourceCapabilities, PreviewEntry, PreviewResponse
from app.security import utcnow
from app.services import playlists, streaming_gate
from app.services.household_collections import HouseholdCollectionService
from app.services.source_automation import SourceAutomationService
from app.services.yt_dlp_service import YtDlpService
from support import FakeJobs
from test_member_access_enforcement import BOSS, YT, clock, follow, house, limit, user  # noqa: F401  (fixtures)
from title_support import ALICE, BOB, BOB_TOKEN, FILE, S1E1, mediabrowser

VOD = MediaSourceCapabilities(provider="generic", lifecycle="vod", can_play=True, can_acquire=True)
LIVE = MediaSourceCapabilities(provider="youtube", lifecycle="live", can_play=True, can_acquire=False, can_record=True)


def fake_extraction(monkeypatch: pytest.MonkeyPatch, *, webpage_url: str, extractor_key: str = "Generic",
                    entries: list[PreviewEntry] | None = None, capabilities: MediaSourceCapabilities = VOD) -> None:
    preview = PreviewResponse(kind="playlist" if entries is not None else "video", webpage_url=webpage_url, extractor_key=extractor_key,
                              extractor=extractor_key.lower(), capabilities=capabilities, entries=entries or [], raw={"id": "x1"})
    monkeypatch.setattr(YtDlpService, "preview", lambda self, *args, **kwargs: preview)
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)


def blocked(response) -> str | None:  # noqa: ANN001
    detail = response.json().get("detail")
    return detail if isinstance(detail, str) and detail.startswith("streaming_blocked") else None


# ---- 1. every batch entry is gated -------------------------------------------------------------------------------------

def test_a_batch_entry_the_container_does_not_list_takes_its_own_gate(house: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_extraction(monkeypatch, webpage_url="https://vimeo.com/1", entries=[PreviewEntry(id="v2", webpage_url="https://vimeo.com/2")])
    limit(streaming={"youtube": False})
    smuggled = house.post("/api/acquisition-batches", json={"source_url": "https://vimeo.com/1", "entries": [{"source_url": YT}]})
    assert (smuggled.status_code, blocked(smuggled)) == (403, "streaming_blocked:youtube")
    listed = house.post("/api/acquisition-batches", json={"source_url": "https://vimeo.com/1", "entries": [{"source_url": "https://vimeo.com/2"}]})
    assert blocked(listed) is None, listed.text


def test_followed_only_batch_entries_outside_the_followed_container_are_refused(house: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_extraction(monkeypatch, webpage_url="https://vimeo.com/1", entries=[PreviewEntry(id="v2", webpage_url="https://vimeo.com/2")])
    follow(ALICE, "https://vimeo.com/1")
    limit(streaming={"followed_only": True})
    outside = house.post("/api/acquisition-batches", json={"source_url": "https://vimeo.com/1", "entries": [{"source_url": "https://vimeo.com/9"}]})
    assert blocked(outside) == "streaming_blocked:open_search"
    inside = house.post("/api/acquisition-batches", json={"source_url": "https://vimeo.com/1", "entries": [{"source_url": "https://vimeo.com/2"}]})
    assert blocked(inside) is None, inside.text


# ---- 2. mirrors and resolved sources -----------------------------------------------------------------------------------

MIRRORS = [
    "https://yewtu.be/watch?v=dQw4w9WgXcQ", "https://invidious.snopyta.org/watch?v=dQw4w9WgXcQ", "https://piped.video/watch?v=dQw4w9WgXcQ",
    "https://hooktube.com/watch?v=dQw4w9WgXcQ", "https://www.youtubekids.com/watch?v=dQw4w9WgXcQ", "https://youtube.googleapis.com/v/dQw4w9WgXcQ",
]


@pytest.mark.parametrize("url", MIRRORS)
def test_youtube_mirrors_are_youtube(house: TestClient, url: str) -> None:
    assert streaming_gate.url_kind(url) == "youtube"
    limit(streaming={"youtube": False})
    assert blocked(house.post("/api/preview", json={"source_url": url})) == "streaming_blocked:youtube"


def test_lookalike_extractors_and_other_sites_stay_open_search() -> None:
    assert [streaming_gate.url_kind(u) for u in ("https://www.kicker.de/a", "https://www.kickstarter.com/projects/a/b", "https://vimeo.com/1")] == [
        "open_search", "open_search", "open_search",
    ]
    assert streaming_gate.url_kind("https://bit.ly/x", "Youtube") == "youtube"
    assert streaming_gate.url_kind("https://bit.ly/x", "twitch:stream") == "twitch"


def test_a_shortener_that_resolves_to_a_blocked_provider_is_refused(house: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    limit(streaming={"youtube": False})
    fake_extraction(monkeypatch, webpage_url=YT, extractor_key="Youtube")
    job = house.post("/api/jobs", json={"source_url": "https://short.example/x"})
    assert (job.status_code, blocked(job)) == (403, "streaming_blocked:youtube")
    batch = house.post("/api/acquisition-batches", json={"source_url": "https://short.example/x", "entries": [{"source_url": "https://short.example/x"}]})
    assert blocked(batch) == "streaming_blocked:youtube"
    fake_extraction(monkeypatch, webpage_url=YT, extractor_key="Youtube", capabilities=LIVE)
    recording = house.post("/api/live-recordings", json={"source_url": "https://short.example/x"})
    assert blocked(recording) == "streaming_blocked:youtube"


# ---- 3 & 4. chat replay, automations, prefetch -------------------------------------------------------------------------

def test_chat_replay_takes_the_streaming_gate(house: TestClient) -> None:
    limit(streaming={"youtube": False})
    refused = house.post("/api/chat-replay/youtube:dQw4w9WgXcQ", json={"source_url": YT})
    assert (refused.status_code, blocked(refused)) == (403, "streaming_blocked:youtube")
    limit(streaming={"followed_only": True})
    assert blocked(house.post("/api/chat-replay/youtube:dQw4w9WgXcQ", json={"source_url": YT})) == "streaming_blocked:youtube"


def test_a_blocked_automation_neither_runs_nor_resumes_but_can_pause(house: TestClient) -> None:
    follow(ALICE, "https://www.youtube.com/@cats")
    limit(streaming={"youtube": False})
    assert blocked(house.post("/api/automations/follow-1/run")) == "streaming_blocked:youtube"
    assert blocked(house.post("/api/automations/follow-1/resume")) == "streaming_blocked:youtube"
    assert house.post("/api/automations/follow-1/pause").status_code == 200


def test_a_scheduled_run_skips_a_blocked_kind_and_records_nothing(house: TestClient) -> None:
    follow(ALICE, "https://www.youtube.com/@cats")
    limit(streaming={"youtube": False})
    now = utcnow()
    with SessionLocal() as session:
        assert SourceAutomationService(session, FakeJobs()).process_one("follow-1", now) is None
        session.commit()
    with SessionLocal() as session:
        assert session.query(AutomationRun).count() == 0
        assert session.get(SourceAutomation, "follow-1").next_check_at > now.replace(tzinfo=None) - timedelta(seconds=1)


def test_prefetch_under_followed_only_warms_nothing_unfollowed(house: TestClient) -> None:
    limit(streaming={"followed_only": True})
    assert house.post("/api/remote/prefetch", json={"source_url": YT}).json() == {"accepted": False}


# ---- 5 & 6. collections ------------------------------------------------------------------------------------------------

def shared_collection_of_episodes() -> str:
    with SessionLocal() as session:
        service = HouseholdCollectionService(session)
        collection = service.create(owner_user_id=ALICE, name="Show night", visibility="shared")
        service.add_item(member_user_id=ALICE, collection_id=collection.id, library_item_id=FILE[S1E1])
        session.commit()
        return collection.id


def test_a_shared_collection_of_hidden_titles_is_not_shown_to_a_restricted_member(house: TestClient) -> None:
    collection_id = shared_collection_of_episodes()
    house.who["id"] = BOB
    assert [c["id"] for c in house.get("/api/collections").json()] == [collection_id]
    playlists_view = lambda: [v.get("CollectionType") for v in house.get("/UserViews", headers=mediabrowser(BOB_TOKEN)).json()["Items"]]  # noqa: E731
    assert "playlists" in playlists_view()
    limit(BOB, sections=["movies"])
    assert house.get("/api/collections").json() == []
    assert "playlists" not in playlists_view()
    with SessionLocal() as session:
        assert playlists.list_playlists(session, session.get(type(user(BOB)), BOB)) == []
    house.who["id"] = ALICE  # the owner always sees their own
    assert [c["id"] for c in house.get("/api/collections").json()] == [collection_id]


def test_adding_a_hidden_item_answers_like_a_missing_one(house: TestClient) -> None:
    house.who["id"] = BOB
    mine = house.post("/api/collections", json={"name": "Mine"}).json()["id"]
    limit(BOB, sections=["movies"])
    hidden = house.post(f"/api/collections/{mine}/items/{FILE[S1E1]}")
    missing = house.post(f"/api/collections/{mine}/items/no-such-item")
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json()) == (404, {"detail": "Collection or entry not found"})
    with SessionLocal() as session:
        assert session.get(HouseholdCollection, mine).revision == 0


# ---- 7. cached counts --------------------------------------------------------------------------------------------------

def test_tightened_access_shows_in_cached_counts_at_once(house: TestClient) -> None:
    house.who["id"] = BOB
    assert house.get("/api/library/sections").json()["shows"] > 0
    before = house.get("/api/titles/facets", params={"type": "series"}).json()
    house.who["id"] = BOSS  # through the admin save, which bumps the member's access generation
    assert house.put(f"/api/admin/members/{BOB}/access", json={"sections": ["movies"]}).status_code == 200
    house.who["id"] = BOB
    assert house.get("/api/library/sections").json()["shows"] == 0
    assert house.get("/api/titles/facets", params={"type": "series"}).json() != before


# ---- 8. invite presets -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("access", [
    {"schedule": {"mon": [["20:00", "07:00"]]}}, {"schedule": {"mon": [["7am", "8pm"]]}}, {"sections": ["../etc"]},
    {"streaming": {"youtube": False, "bogus": True}},
])
def test_invite_presets_are_validated_like_member_access(house: TestClient, access: dict) -> None:
    house.who["id"] = BOSS
    response = house.post("/api/admin/invites", json={"email": "pal@example.com", "access": access, "send": False})
    assert response.status_code == 422, response.text
