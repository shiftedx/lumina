from __future__ import annotations

import socket
from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.main as main
from app.schemas import YouTubeSearchResult
from app.services.popular_discovery import (
    PopularCategorySnapshot,
    PopularItem,
    PopularSnapshot,
)
from support import make_user


def _item(item_id: str, category: str) -> PopularItem:
    return PopularItem(
        id=item_id, title=item_id, uploader="Someone", duration=None,
        thumbnail=f"https://thumb.example/{item_id}.jpg", artwork_url=None,
        webpage_url=f"https://www.youtube.com/watch?v={item_id}", view_count=10,
        availability=None, published_at=None, source="youtube", source_label="YouTube",
        capabilities=None, category_keys=(category,), language="en",
    )


def _snapshot() -> PopularSnapshot:
    now = datetime.now(UTC)
    return PopularSnapshot(
        items=(_item("v1", "gaming"),),
        categories=(PopularCategorySnapshot(key="gaming", label="Gaming", state="ready", last_success_at=now, next_refresh_at=now),),
        state="ready", refreshing=False, stale=False,
        last_success_at=now, refreshed_at=now, next_refresh_at=now, error=None,
    )


class FakeLiveDiscovery:
    def get_snapshot(self):
        return _snapshot()


class FakeChecker:
    def __init__(self, entries):
        self._entries = entries
        self.requested: list[list[str]] = []

    def live_entries(self, urls):
        self.requested.append(list(urls))
        return self._entries

    def unavailable_sources(self, urls):
        return {}


def _public_resolver(host, port, *_args):
    # thumb.example is an RFC 2606 reserved name that never resolves via real DNS,
    # so pin it to a public global address to exercise artwork re-registration.
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


def test_live_endpoint_returns_snapshot_hero_and_twitch_flag(monkeypatch, api_client) -> None:
    member = make_user("dana")
    hero_entry = YouTubeSearchResult(id="h1", title="hero live", webpage_url="https://www.youtube.com/watch?v=h1", thumbnail="https://thumb.example/h1.jpg")
    checker = FakeChecker([hero_entry])
    monkeypatch.setattr(main, "live_discovery", FakeLiveDiscovery())
    monkeypatch.setattr(main, "followed_live_checker", checker)
    monkeypatch.setattr(main.live_search, "health", type("H", (), {"available": False})())
    monkeypatch.setattr(main.artwork._public_source_policy, "_resolver", _public_resolver)
    client = api_client(user=member, base_url="http://localhost")
    response = client.get("/api/discovery/live")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["twitch_available"] is False
    assert [item["id"] for item in payload["items"]] == ["v1"]
    assert payload["items"][0]["category_keys"] == ["gaming"]
    assert [item["id"] for item in payload["hero"]] == ["h1"]
    assert payload["hero"][0]["artwork_url"]  # artwork re-registered per response


def test_live_endpoint_requires_auth() -> None:
    main.app.dependency_overrides.clear()
    client = TestClient(main.app, base_url="http://localhost")
    response = client.get("/api/discovery/live")
    assert response.status_code in (401, 403)


def test_live_recommendations_filter_language_before_ranking_and_return_a_full_provider_pool(monkeypatch, api_client) -> None:
    from dataclasses import replace
    from app.schemas import PopularItemResponse
    from app.services.media_capabilities import derive_media_capabilities

    member = make_user('english-member')
    def item(index, lang, views):
        value = replace(_item(f'live{index}', 'gaming'), title='English gaming title', uploader=f'Creator {index}', view_count=views,
                        capabilities=derive_media_capabilities({'extractor_key': 'youtube', 'is_live': True}))
        return PopularItemResponse.model_validate(value).model_copy(update={'language': lang})
    snapshot = main.LiveSnapshotResponse.model_validate(_snapshot()).model_copy(update={
        'items': [item(0, 'hi', 9_000_000), item(1, None, 8_000_000), *[item(i, 'en-US', 1000-i) for i in range(2, 22)]]})
    monkeypatch.setattr(main, 'live_discovery', type('D', (), {'get_snapshot': lambda self: snapshot})())
    monkeypatch.setattr(main, 'followed_live_checker', FakeChecker([]))
    response = api_client(user=member, base_url='http://localhost').get('/api/discovery/live')
    assert response.status_code == 200, response.text
    entries = response.json()['items']
    assert len(entries) >= 16
    assert not {'live0', 'live1'} & {entry['id'] for entry in entries}
    assert all(entry.get('language') == 'en-US' for entry in entries)
    assert response.json()['language_pending'] is True  # the selected provider may still have fewer choices
