from __future__ import annotations

from datetime import UTC, datetime

import app.main as main
from app.models import SourceAutomation, User
from app.services.channel_discovery import normalize_channel_source_url
from app.services.member_onboarding import MemberOnboardingService
from app.services.popular_discovery import PopularCategorySnapshot, PopularItem, PopularSnapshot
from app.services.yt_dlp_service import YtDlpService
from app.schemas import YouTubeSearchResponse, YouTubeSearchResult
from support import make_user


REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def _item(item_id: str, categories: tuple[str, ...], *, uploader: str, uploader_url: str | None) -> PopularItem:
    return PopularItem(
        id=item_id, title=item_id.title(), uploader=uploader, duration=120, thumbnail=None, artwork_url=None,
        webpage_url=f"https://www.youtube.com/watch?v={item_id}", view_count=100, availability="public",
        published_at=None, source="youtube", source_label="YouTube", capabilities=None, category_keys=categories,
        uploader_url=uploader_url, uploader_id=None,
    )


def _snapshot(*items: PopularItem, categories) -> PopularSnapshot:
    return PopularSnapshot(
        items=items, categories=categories, state="ready", refreshing=False, stale=False,
        last_success_at=REFERENCE, refreshed_at=REFERENCE, next_refresh_at=None, error=None,
    )


def test_complete_onboarding_creates_acquisition_disabled_follows_and_is_idempotent(monkeypatch, db_factory, api_client) -> None:
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    response = client.post(
        "/api/onboarding/complete",
        json={
            "keys": ["music"],
            "follows": [
                {"source_url": "https://www.youtube.com/@veritasium", "display_name": "Veritasium"},
                {"source_url": "not a url", "display_name": "Broken"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["selected_keys"] == ["music"]
    statuses = {outcome["display_name"]: outcome["status"] for outcome in body["followed"]}
    assert statuses == {"Veritasium": "created", "Broken": "invalid"}

    with factory() as session:
        follows = session.query(SourceAutomation).filter(SourceAutomation.user_id == "member").all()
        assert len(follows) == 1
        assert follows[0].source_type == "channel"
        assert follows[0].auto_download is False
        assert follows[0].active is True
        assert session.get(User, "member").onboarding_status == MemberOnboardingService.COMPLETED

    # Reload/retry replays the same follow: still exactly one automation.
    replay = client.post(
        "/api/onboarding/complete",
        json={"keys": ["music"], "follows": [{"source_url": "https://www.youtube.com/@veritasium", "display_name": "Veritasium"}]},
    )
    assert replay.status_code == 200
    assert [outcome["status"] for outcome in replay.json()["followed"]] == ["existing"]
    with factory() as session:
        assert session.query(SourceAutomation).filter(SourceAutomation.user_id == "member").count() == 1


def test_channel_suggestions_endpoint_ranks_curates_and_marks_existing_follows(monkeypatch, db_factory, api_client) -> None:
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add(member)
        session.add(
            SourceAutomation(
                id="follow-1", user_id="member", label="Veritasium",
                source_url="https://www.youtube.com/@veritasium", source_type="channel",
                cron_expression="*/30 * * * *", active=True, auto_download=False,
            )
        )

    snapshot = _snapshot(
        _item("v1", ("science-technology",), uploader="Veritasium", uploader_url="https://www.youtube.com/@veritasium"),
        _item("v2", ("science-technology",), uploader="Other", uploader_url="https://www.youtube.com/@other"),
        categories=(
            PopularCategorySnapshot("science-technology", "Science & Technology", "ready", REFERENCE, None),
            PopularCategorySnapshot("music", "Music", "failed", None, None),
        ),
    )
    monkeypatch.setattr(main.popular_discovery, "get_snapshot", lambda: snapshot)

    client = api_client(user=member, base_url="http://localhost")
    response = client.get("/api/discovery/channels", params={"keys": ["science-technology", "music"]})
    assert response.status_code == 200
    categories = {category["key"]: category for category in response.json()["categories"]}

    tech = categories["science-technology"]
    assert tech["state"] == "ranked"
    by_name = {channel["display_name"]: channel for channel in tech["channels"]}
    assert by_name["Veritasium"]["following"] is True
    assert by_name["Other"]["following"] is False

    music = categories["music"]
    assert music["state"] == "curated"
    assert music["channels"]  # curated fallback is populated


def test_complete_onboarding_reports_a_malformed_channel_address_as_invalid(monkeypatch, db_factory, api_client) -> None:
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    response = client.post(
        "/api/onboarding/complete",
        json={"keys": [], "follows": [{"source_url": "https://youtube.com:abc/@x", "display_name": "Bad Port"}]},
    )
    assert response.status_code == 200
    assert [outcome["status"] for outcome in response.json()["followed"]] == ["invalid"]
    with factory() as session:
        assert session.query(SourceAutomation).count() == 0


def test_channel_search_endpoint_degrades_to_503_when_search_is_busy(monkeypatch, db_factory, api_client) -> None:
    from app.services.yt_dlp_service import SearchBusyError

    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)

    def busy(self, query, limit):  # noqa: ANN001
        raise SearchBusyError("This search is still being prepared. Try again shortly.")

    monkeypatch.setattr(YtDlpService, "youtube_search", busy)
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    response = client.post("/api/discovery/channels/search", json={"query": "veritasium"})
    assert response.status_code == 503


def test_channel_search_endpoint_returns_deduplicated_candidates(monkeypatch, db_factory, api_client) -> None:
    monkeypatch.setattr(main, "enforce_rate_limit", lambda *_args, **_kwargs: None)

    def fake_search(self, query, limit):  # noqa: ANN001
        return YouTubeSearchResponse(
            query=query,
            items=[
                YouTubeSearchResult(id="a", title="One", uploader="Veritasium", uploader_url="https://www.youtube.com/@veritasium"),
                YouTubeSearchResult(id="b", title="Two", uploader="Veritasium", uploader_url="https://m.youtube.com/@veritasium"),
                YouTubeSearchResult(id="c", title="Three", uploader="Other", uploader_url="https://www.youtube.com/@other"),
            ],
        )

    monkeypatch.setattr(YtDlpService, "youtube_search", fake_search)
    factory = db_factory
    member = make_user("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    response = client.post("/api/discovery/channels/search", json={"query": "veritasium"})
    assert response.status_code == 200
    channels = response.json()["channels"]
    keys = [channel["channel_key"] for channel in channels]
    assert keys == [
        normalize_channel_source_url("https://www.youtube.com/@veritasium"),
        normalize_channel_source_url("https://www.youtube.com/@other"),
    ]
