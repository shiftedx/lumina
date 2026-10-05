"""GET /api/library/channels: saved YouTube grouped by channel, as Infuse groups it."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import LibraryItem, PlaybackProgress, SourceAutomation
from app.services import jellyfin
from app.services.library import LibraryService
from support import make_user

CID = "UCabcdefghijklmnopqrstuv"
ALICE, BOB = make_user("alice"), make_user("bob")
BASE = datetime(2026, 9, 1)


def _video(session, item_id: str, uploader: str | None, minutes: int, *, owner=ALICE, visibility="shared", extractor="youtube", kind="video", channel=None, **fields) -> None:  # noqa: ANN001, ANN003
    session.add(LibraryItem(
        id=item_id, user_id=owner.id, visibility=visibility, extractor=extractor, remote_id=item_id, title=item_id, uploader=uploader,
        kind=kind, created_at=BASE + timedelta(minutes=minutes), metadata_json={"channel_id": channel} if channel else {}, **fields,
    ))


@pytest.fixture
def seeded(db_factory):  # noqa: ANN001, ANN201
    with db_factory() as session:
        _video(session, "h1", "Harbor Films", 1, channel=CID)
        _video(session, "h2", "Harbor Films", 30, channel=CID)
        _video(session, "h3", "Harbor Films", 5, kind="recording")
        _video(session, "a1", "alpha", 10, channel="@not-an-id")
        _video(session, "n1", None, 2)
        _video(session, "bob-private", "Harbor Films", 40, owner=BOB, visibility="private")
        _video(session, "gone", "Harbor Films", 50, status="missing")
        _video(session, "tw", "Harbor Films", 3, extractor="twitch:vod")
        _video(session, "movie", "Harbor Films", 4, title_id=None, kind="movie")
        session.add(PlaybackProgress(id="p1", user_id=ALICE.id, item_id="h1", position_seconds=600, duration_seconds=600, completed=True))
        session.add(PlaybackProgress(id="p2", user_id=ALICE.id, item_id="h2", position_seconds=30, duration_seconds=600, completed=False))
        session.add(PlaybackProgress(id="p3", user_id=BOB.id, item_id="h3", position_seconds=600, duration_seconds=600, completed=True))
        session.add(SourceAutomation(
            id="f1", user_id=ALICE.id, label="Harbor", source_url=f"https://www.youtube.com/channel/{CID}", source_type="channel",
            artwork_url="https://yt3.googleusercontent.com/a", cron_expression="0 */6 * * *", active=True, auto_download=False,
            format_selection={}, output_profile={}, rules={}, duplicate_policy="skip_same_source", last_run_summary={}, feed_entries=[],
            created_at=BASE, updated_at=BASE,
        ))
        session.commit()
    return db_factory


def _channels(factory, user=ALICE, sort="recent"):  # noqa: ANN001, ANN202
    with factory() as session:
        return LibraryService(session).list_channels(user, source="youtube", sort=sort, artwork_url=lambda url: f"proxied:{url}" if url else None)


def test_groups_by_extractor_and_uploader_with_the_member_s_unwatched_count(seeded) -> None:  # noqa: ANN001
    harbor, alpha, unnamed = _channels(seeded)
    assert (harbor.name, harbor.uploader, harbor.count, harbor.unwatched_count) == ("Harbor Films", "Harbor Films", 3, 2)
    assert harbor.newest_item_id == "h2" and harbor.newest_at == BASE + timedelta(minutes=30)
    assert harbor.key == jellyfin.channel_id("youtube", "Harbor Films")
    assert harbor.channel_id == CID and harbor.avatar_url == "proxied:https://yt3.googleusercontent.com/a"
    assert (alpha.channel_id, alpha.avatar_url) == (None, None)  # "@not-an-id" is not a UC id
    assert (unnamed.name, unnamed.uploader, unnamed.count) == ("youtube", "", 1)


def test_counts_only_what_the_member_can_see(seeded) -> None:  # noqa: ANN001
    bob = {channel.name: channel for channel in _channels(seeded, BOB)}
    assert bob["Harbor Films"].count == 4 and bob["Harbor Films"].unwatched_count == 3  # sees his private one; h3 watched
    assert bob["Harbor Films"].avatar_url is None  # the follow is Alice's


def test_sorts_by_name_case_insensitively(seeded) -> None:  # noqa: ANN001
    assert [channel.name for channel in _channels(seeded, sort="name")] == ["alpha", "Harbor Films", "youtube"]


def test_matches_jellyfin_s_channel_series_on_the_same_seed(seeded) -> None:  # noqa: ANN001
    with seeded() as session:
        series = {key: channel for key, channel in jellyfin.channel_index(session, ALICE).items() if channel.year is None and channel.extractor == "youtube"}
    mine = {channel.key: channel for channel in _channels(seeded)}
    assert set(mine) == set(series)
    assert {key: channel.count for key, channel in mine.items()} == {key: channel.count for key, channel in series.items()}


def test_caps_at_500(db_factory, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("app.services.library.LIBRARY_GROUPS_MAX", 3)
    with db_factory() as session:
        for index in range(5):
            _video(session, f"v{index}", f"c{index}", index)
        session.commit()
    assert [channel.name for channel in _channels(db_factory)] == ["c4", "c3", "c2"]


def test_the_route(seeded, api_client, monkeypatch) -> None:  # noqa: ANN001
    import app.main as main

    monkeypatch.setattr(main, "_remote_artwork_url", lambda url: "/api/artwork/remote/x" if url else None)
    client = api_client(user=ALICE, base_url="http://localhost")
    body = client.get("/api/library/channels?source=youtube&sort=name").json()
    assert [channel["name"] for channel in body] == ["alpha", "Harbor Films", "youtube"]
    assert body[1]["avatar_url"] == "/api/artwork/remote/x"
    assert client.get("/api/library/channels?source=twitch").status_code == 422
    assert client.get("/api/library/channels?sort=size").status_code == 422
