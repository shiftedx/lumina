"""Connected-app watchlist detail actions share the web member queue."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.main import app
from app.models import LibraryItem, WatchQueueEntry
from title_support import ALICE_TOKEN, BOB_TOKEN, MOVIE, S1E1, S1E2, SEASON1, SERIES, jellyfin_household, mediabrowser


BASE = "/Lumina/Watchlist"


def call(client: TestClient, method: str, item_id: str, *, token: str = ALICE_TOKEN, **kwargs):  # noqa: ANN201
    return client.request(method, f"{BASE}/{item_id}", headers=mediabrowser(token), **kwargs)


@pytest.fixture
def native(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def queued_title_ids() -> set[str]:
    with db_module.SessionLocal() as db:
        return set(db.scalars(
            select(LibraryItem.title_id).join(WatchQueueEntry, WatchQueueEntry.library_item_id == LibraryItem.id)
        ))


def test_movie_episode_series_and_season_resolve_to_real_visible_watchlist_targets(native: TestClient) -> None:
    assert call(native, "GET", MOVIE).json() == {"included": False}
    assert call(native, "PUT", MOVIE, json={"included": True}).json() == {"included": True}
    assert MOVIE in queued_title_ids()
    assert call(native, "GET", MOVIE).json() == {"included": True}

    # A series uses its web detail CTA's next visible episode; a Season resolves its first visible episode.
    assert call(native, "PUT", SERIES, json={"included": True}).json() == {"included": True}
    assert S1E1 in queued_title_ids()
    assert call(native, "PUT", SEASON1, json={"included": True}).json() == {"included": True}
    assert S1E1 in queued_title_ids(), "Adding the season must be idempotent with the same concrete queue entry"

    assert call(native, "PUT", S1E2, json={"included": True}).json() == {"included": True}
    assert S1E2 in queued_title_ids()


def test_remove_is_idempotent_and_connected_apps_cannot_read_or_mutate_another_members_watchlist(native: TestClient) -> None:
    assert call(native, "PUT", MOVIE, json={"included": True}).status_code == 200
    assert call(native, "PUT", MOVIE, json={"included": False}).json() == {"included": False}
    assert call(native, "PUT", MOVIE, json={"included": False}).json() == {"included": False}

    assert call(native, "PUT", MOVIE, token=BOB_TOKEN, json={"included": True}).json() == {"included": True}
    assert call(native, "GET", MOVIE).json() == {"included": False}
    assert call(native, "GET", MOVIE, token=BOB_TOKEN).json() == {"included": True}


def test_watchlist_adapter_stays_in_connected_app_namespace_and_hides_invalid_targets(native: TestClient) -> None:
    assert native.get("/api/me/watch-queue", headers=mediabrowser(ALICE_TOKEN)).status_code == 401
    assert call(native, "GET", "not-an-id").status_code == 404
    assert call(native, "PUT", SERIES, json={"included": "yes"}).status_code == 400
