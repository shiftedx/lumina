"""Recommendations R1: watch depth on progress rows and the events a checkpoint writes."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import LibraryItem, PlaybackProgress, RecoEvent, RemotePlaybackProgress
from app.persistence import write_transaction
from app.schemas import PlaybackProgressUpdateRequest, RemotePlaybackProgressUpdateRequest
from app.services.playback import PlaybackProgressService
from app.services.remote_playback import RemotePlaybackProgressService
from discovery_support import add_movie, add_series
from support import make_user, memory_session_factory

START = datetime(2026, 9, 30, 12, 0, 0)
UC = "UC" + "a" * 22


@pytest.fixture
def env(monkeypatch):  # noqa: ANN001, ANN201
    factory = memory_session_factory()
    clock = [START]
    monkeypatch.setattr("app.services.playback.utcnow", lambda: clock[0])
    with factory.begin() as db:
        db.add_all([make_user("owner"), make_user("alice")])
        add_movie(db, "m", "Movie")  # its version m-v runs 1200 s
        add_series(db, "show", "Show", seasons={1: 3})
        db.add(LibraryItem(
            id="yt", user_id="owner", visibility="shared", title="A saved video", duration=600, status="available",
            extractor="youtube", uploader="Some Creator", metadata_json={"channel_id": UC},
        ))
        db.add(LibraryItem(
            id="yt-name", user_id="owner", visibility="shared", title="Another saved video", duration=600, status="available",
            extractor="youtube", uploader="Some Creator", metadata_json={},
        ))
    return factory, clock


def checkpoint(factory, clock, minutes: float, item_id: str, position: int, *, completed: bool = False, duration: int | None = 1200) -> None:  # noqa: ANN001
    clock[0] = START + timedelta(minutes=minutes)
    with factory() as db, write_transaction(db, name="test"):
        PlaybackProgressService(db).update(
            item_id, PlaybackProgressUpdateRequest(position_seconds=position, duration_seconds=duration, completed=completed), make_user("alice"),
        )


def progress(factory, item_id: str) -> PlaybackProgress:  # noqa: ANN001
    with factory() as db:
        return db.scalars(select(PlaybackProgress).where(PlaybackProgress.user_id == "alice", PlaybackProgress.item_id == item_id)).one()


def events(factory) -> list[tuple[str, str]]:  # noqa: ANN001
    with factory() as db:
        return [(row.kind, row.item_key) for row in db.scalars(select(RecoEvent).order_by(RecoEvent.id))]


def test_depth_and_events_follow_a_viewing_session(env) -> None:  # noqa: ANN001
    factory, clock = env
    checkpoint(factory, clock, 0, "m-v", 60)
    row = progress(factory, "m-v")
    assert (row.plays, row.completions, row.max_fraction) == (1, 0, pytest.approx(0.05))
    checkpoint(factory, clock, 5, "m-v", 600)  # the same session
    row = progress(factory, "m-v")
    assert (row.plays, row.max_fraction) == (1, pytest.approx(0.5))
    checkpoint(factory, clock, 36, "m-v", 700)  # 31 minutes of silence: a new session
    assert progress(factory, "m-v").plays == 2
    checkpoint(factory, clock, 37, "m-v", 1160)  # past 95%
    row = progress(factory, "m-v")
    assert (row.completions, row.max_fraction, row.completed) == (1, 1.0, True)
    checkpoint(factory, clock, 38, "m-v", 1190)  # a repeated completed checkpoint completes once
    assert progress(factory, "m-v").completions == 1
    checkpoint(factory, clock, 39, "m-v", 30)  # restarting a completed title is a new play, and the depth stays
    row = progress(factory, "m-v")
    assert (row.plays, row.completions, row.max_fraction) == (3, 1, 1.0)
    # Events are sampled once per session (30 minutes): the third play is inside the second's window.
    assert events(factory) == [("play", "m"), ("play", "m"), ("complete", "m")]


def test_an_episode_counts_for_its_series(env) -> None:  # noqa: ANN001
    factory, clock = env
    checkpoint(factory, clock, 0, "show-s1e2-v", 100)
    assert events(factory) == [("play", "show")]


def test_marking_a_series_watched_writes_one_play_and_one_complete_event(env) -> None:  # noqa: ANN001
    factory, clock = env
    for number in (1, 2, 3):
        checkpoint(factory, clock, 0, f"show-s1e{number}-v", 0, completed=True)
        row = progress(factory, f"show-s1e{number}-v")
        assert (row.plays, row.completions, row.max_fraction) == (1, 1, 1.0)
    assert events(factory) == [("play", "show"), ("complete", "show")]


def test_a_zero_duration_records_no_depth_and_does_not_crash(env) -> None:  # noqa: ANN001
    factory, clock = env
    checkpoint(factory, clock, 0, "m-v", 50, duration=0)
    row = progress(factory, "m-v")
    assert (row.plays, row.completions, row.max_fraction, row.completed) == (1, 0, 0.0, False)


def test_a_saved_video_counts_depth_but_writes_no_event_and_learns_its_channel(env) -> None:  # noqa: ANN001
    factory, clock = env
    checkpoint(factory, clock, 0, "yt", 300, duration=600)
    row = progress(factory, "yt")
    assert (row.plays, row.max_fraction) == (1, pytest.approx(0.5))
    assert events(factory) == []  # no title, so no item key
    with factory() as db:
        assert db.get(LibraryItem, "yt").channel_key == "https://www.youtube.com/channel/" + UC
    checkpoint(factory, clock, 1, "yt-name", 10, duration=600)
    with factory() as db:
        assert db.get(LibraryItem, "yt-name").channel_key is None  # a name key is never stored on the item


def test_a_local_clear_still_deletes_the_row_and_keeps_the_history(env) -> None:  # noqa: ANN001
    factory, clock = env
    checkpoint(factory, clock, 0, "m-v", 60)
    with factory() as db, write_transaction(db, name="test"):
        PlaybackProgressService(db).clear("m-v", make_user("alice"))
    with factory() as db:
        assert db.scalars(select(PlaybackProgress).where(PlaybackProgress.user_id == "alice")).first() is None
    assert events(factory) == [("play", "m")]


IDENTITY = "youtube:dQw4w9WgXcQ"
ITEM_KEY = RemotePlaybackProgressService.source_identity_key(IDENTITY)


@pytest.fixture
def remote(monkeypatch):  # noqa: ANN001, ANN201
    factory = memory_session_factory()
    clock = [START]
    monkeypatch.setattr("app.services.remote_playback.utcnow", lambda: clock[0])
    with factory.begin() as db:
        db.add(make_user("alice"))
    return factory, clock


def send(factory, clock, minutes: float, position: float, *, seq: int, completed: bool = False, duration: float | None = 600.0,  # noqa: ANN001
         channel_id: str | None = None, channel_url: str | None = None, uploader: str | None = "Some Creator") -> RemotePlaybackProgress:
    clock[0] = START + timedelta(minutes=minutes)
    payload = RemotePlaybackProgressUpdateRequest(
        source_identity=IDENTITY, source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", uploader=uploader, position_seconds=position,
        duration_seconds=duration, completed=completed, checkpoint_client_id="player-a", checkpoint_sequence=seq, expected_revision=0,
        channel_id=channel_id, channel_url=channel_url,
    )
    with factory() as db, write_transaction(db, name="test"):
        return RemotePlaybackProgressService(db).update(IDENTITY, payload, make_user("alice"))


def remote_row(factory) -> RemotePlaybackProgress:  # noqa: ANN001
    with factory() as db:
        return db.scalars(select(RemotePlaybackProgress).where(RemotePlaybackProgress.user_id == "alice")).one()


def test_remote_depth_follows_a_session_and_writes_events_keyed_by_the_identity(remote) -> None:  # noqa: ANN001
    factory, clock = remote
    send(factory, clock, 0, 60, seq=1)
    row = remote_row(factory)
    assert (row.plays, row.completions, row.max_fraction) == (1, 0, pytest.approx(0.1))
    send(factory, clock, 5, 300, seq=2)
    send(factory, clock, 10, 600, seq=3, completed=True)
    row = remote_row(factory)
    assert (row.plays, row.completions, row.max_fraction) == (1, 1, 1.0)
    send(factory, clock, 11, 600, seq=4, completed=True)  # a repeated completed checkpoint completes once
    assert remote_row(factory).completions == 1
    send(factory, clock, 42, 10, seq=5)  # 31 minutes later, restarting below 10%: a new play
    row = remote_row(factory)
    assert (row.plays, row.completions, row.max_fraction) == (2, 1, 1.0)
    assert events(factory) == [("play", ITEM_KEY), ("complete", ITEM_KEY), ("play", ITEM_KEY)]
    with factory() as db:
        assert {row.target_kind for row in db.scalars(select(RecoEvent))} == {"remote"}


def test_a_stale_or_repeated_checkpoint_changes_nothing(remote) -> None:  # noqa: ANN001
    factory, clock = remote
    send(factory, clock, 0, 60, seq=5)
    send(factory, clock, 40, 500, seq=6)  # below the 95% completion rule
    before = remote_row(factory)
    assert (before.plays, before.max_fraction, before.checkpoint_revision) == (2, pytest.approx(500 / 600), 2)
    recorded = events(factory)
    send(factory, clock, 80, 599, seq=6, completed=True)  # the same sequence again: refused by the ordered upsert
    send(factory, clock, 81, 0, seq=4)                     # an older sequence: refused too
    after = remote_row(factory)
    assert (after.plays, after.completions, after.max_fraction, after.checkpoint_revision, after.position_seconds) == (
        2, 0, before.max_fraction, 2, 500,
    )
    assert events(factory) == recorded


def test_a_clear_keeps_depth_and_the_channel_and_the_next_resume_is_a_new_play(remote) -> None:  # noqa: ANN001
    factory, clock = remote
    send(factory, clock, 0, 300, seq=1, channel_id=UC)
    clock[0] = START + timedelta(minutes=1)
    with factory() as db, write_transaction(db, name="test"):
        RemotePlaybackProgressService(db).clear(IDENTITY, make_user("alice"), "player-a", 2, 0)
    cleared = remote_row(factory)
    assert (cleared.cleared, cleared.position_seconds) == (True, 0)
    assert (cleared.plays, cleared.completions, cleared.max_fraction, cleared.channel_key) == (1, 0, pytest.approx(0.5), "https://www.youtube.com/channel/" + UC)
    send(factory, clock, 2, 5, seq=3)  # resuming from zero after a clear, inside the 30-minute window
    resumed = remote_row(factory)
    assert (resumed.cleared, resumed.plays, resumed.max_fraction) == (False, 2, pytest.approx(0.5))


def test_the_channel_key_prefers_a_stable_key_and_never_downgrades(remote) -> None:  # noqa: ANN001
    factory, clock = remote
    send(factory, clock, 0, 10, seq=1)  # only an uploader name
    assert remote_row(factory).channel_key == "name:youtube:some creator"
    send(factory, clock, 1, 20, seq=2, channel_id=UC)
    stable = "https://www.youtube.com/channel/" + UC
    assert remote_row(factory).channel_key == stable
    send(factory, clock, 2, 30, seq=3)  # a later checkpoint without the channel
    assert remote_row(factory).channel_key == stable
    send(factory, clock, 3, 40, seq=4, uploader="Renamed")
    assert remote_row(factory).channel_key == stable


def test_a_remote_checkpoint_without_a_duration_does_not_crash(remote) -> None:  # noqa: ANN001
    factory, clock = remote
    send(factory, clock, 0, 25, seq=1, duration=None)
    row = remote_row(factory)
    assert (row.plays, row.max_fraction) == (1, 0.0)


def test_a_saved_youtube_download_gets_its_stable_channel_key(tmp_path) -> None:  # noqa: ANN001
    from app.services.library import LibraryService

    factory = memory_session_factory()
    file = tmp_path / "video.mp4"
    file.write_bytes(b"x" * 2048)

    def info(**changes):  # noqa: ANN003, ANN202
        return {"id": "abc", "extractor_key": "youtube", "title": "A saved video", "uploader": "Some Creator", "duration": 60,
                "filepath": str(file), **changes}

    with factory.begin() as db:
        db.add(make_user("alice"))
        service = LibraryService(db)
        keyed = service.upsert_from_info(info(channel_id=UC), owner_user_id="alice")
        named = service.upsert_from_info(info(id="def", uploader_id="@handle"), owner_user_id="alice")
        other = service.upsert_from_info(info(id="ghi", extractor_key="vimeo", channel_id=UC), owner_user_id="alice")
        key = keyed.id
    assert keyed.channel_key == "https://www.youtube.com/channel/" + UC
    assert named.channel_key is None  # no UC id: the name key is applied at read time, never stored on the item
    assert other.channel_key is None
    with factory.begin() as db:
        # A re-download keeps the key it has, even if the new info names another channel.
        again = LibraryService(db).upsert_from_info(info(channel_id="UC" + "b" * 22), owner_user_id="alice")
        assert (again.id, again.channel_key) == (key, "https://www.youtube.com/channel/" + UC)
