"""Reading a member's Jellyfin history (ADR 0010 amendment): sign-in, paging, caps, sign-out, and no leaks."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from app.services import jellyfin_history_client as client
from app.services.jellyfin_history_client import (
    NOT_ADMIN, JellyfinImportError, JellyfinUser, admin_session, fetch_history, list_users, read_history,
)
from jellyfin_fake import TOKEN, USER_ID, FakeJellyfin, episode, movie, series

PASSWORD = "jf-secret-pw"


@pytest.fixture
def jellyfin():  # noqa: ANN201
    fake = FakeJellyfin(password=PASSWORD)
    yield fake
    fake.close()


def test_reads_played_resumable_and_favorite_entries_then_signs_out(jellyfin) -> None:  # noqa: ANN001
    jellyfin.items = [
        movie("m1", "The Movie", path="/data/movies/Movie (2020)/Movie (2020) - 4K.mkv", year=2020, played=True,
              last="2026-09-20T20:00:00.1234567Z", Tmdb="603"),
        movie("m2", "Half Watched", seconds=600, last="2026-09-21T10:00:00+02:00", imdb="tt1"),
        movie("m3", "Never Opened"),
        episode("e1", "s1", "Show", 1, 2, favorite=True),
        series("s1", "Show", favorite=True, Tmdb="100"),
    ]
    jellyfin.series = {"s1": series("s1", "Show", Tmdb="100", Tvdb="7")}
    history = fetch_history(jellyfin.url, "alice", PASSWORD)
    entries = {entry.id: entry for entry in history.entries}
    assert set(entries) == {"m1", "m2", "e1", "s1"}
    assert (entries["m1"].played, entries["m1"].last_played, entries["m1"].provider_ids) == (
        True, datetime(2026, 9, 20, 20, 0, 0, 123456), {"Tmdb": "603"})
    assert (entries["m2"].position_seconds, entries["m2"].last_played, entries["m2"].provider_ids) == (
        600, datetime(2026, 9, 21, 8, 0), {"Imdb": "tt1"})
    assert (entries["e1"].series_id, entries["e1"].season, entries["e1"].episode, entries["e1"].favorite) == ("s1", 1, 2, True)
    assert (entries["e1"].label, entries["m1"].label, entries["m2"].label) == ("Show S01E02", "The Movie (2020)", "Half Watched")
    assert history.series_provider_ids == {"s1": {"Tmdb": "100", "Tvdb": "7"}}
    assert jellyfin.logouts == 1


def test_pages_through_a_large_history(jellyfin) -> None:  # noqa: ANN001
    jellyfin.items = [movie(f"m{n}", f"Movie {n}", played=True) for n in range(1200)]
    assert len(fetch_history(jellyfin.url, "alice", PASSWORD).entries) == 1200
    starts = [query["StartIndex"] for _, path, query in jellyfin.requests if path == "/Items" and query.get("Filters") == "IsPlayed"]
    assert starts == ["0", "500", "1000"]


def test_a_history_beyond_the_cap_is_refused_and_still_signs_out(jellyfin, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(client, "MAX_ENTRIES", 3)
    jellyfin.items = [movie(f"m{n}", f"Movie {n}", played=True) for n in range(5)]
    with pytest.raises(JellyfinImportError, match="more than 3 items"):
        fetch_history(jellyfin.url, "alice", PASSWORD)
    assert jellyfin.logouts == 1


def test_a_refused_sign_in_is_a_plain_message_and_requests_nothing_else(jellyfin) -> None:  # noqa: ANN001
    with pytest.raises(JellyfinImportError, match="did not accept that username and password") as caught:
        fetch_history(jellyfin.url, "alice", "wrong-password")
    assert "wrong-password" not in str(caught.value) and caught.value.__cause__ is None
    assert [(method, path) for method, path, _ in jellyfin.requests] == [("POST", "/Users/AuthenticateByName")]


def test_a_failure_mid_read_still_signs_out(jellyfin) -> None:  # noqa: ANN001
    jellyfin.items_status = 500
    with pytest.raises(JellyfinImportError, match="HTTP 500"):
        fetch_history(jellyfin.url, "alice", PASSWORD)
    assert jellyfin.logouts == 1


def test_redirects_are_never_followed(jellyfin) -> None:  # noqa: ANN001
    jellyfin.redirect = "http://127.0.0.1:9/elsewhere"
    with pytest.raises(JellyfinImportError, match="redirects"):
        fetch_history(jellyfin.url, "alice", PASSWORD)
    assert len(jellyfin.requests) == 1


def test_an_unreachable_server_is_a_plain_message() -> None:
    with pytest.raises(JellyfinImportError, match="unreachable"):
        fetch_history("http://127.0.0.1:9", "alice", PASSWORD)


def test_credentials_and_token_never_reach_the_logs(jellyfin, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    jellyfin.items = [movie("m1", "The Movie", played=True)]
    fetch_history(jellyfin.url, "alice", PASSWORD)
    with pytest.raises(JellyfinImportError):
        fetch_history(jellyfin.url, "alice", "wrong-password")
    for secret in (PASSWORD, "wrong-password", TOKEN):
        assert secret not in caplog.text


def test_malformed_entries_are_skipped(jellyfin) -> None:  # noqa: ANN001
    data = {"Played": True, "PlaybackPositionTicks": 0, "IsFavorite": False}
    jellyfin.items = [
        {"Id": 7, "Type": "Movie", "Name": "Bad id", "UserData": data},
        {"Id": "m9", "Type": "Movie", "Name": "Odd date", "UserData": {**data, "LastPlayedDate": "yesterday"}},
    ]
    [entry] = fetch_history(jellyfin.url, "alice", PASSWORD).entries
    assert (entry.id, entry.last_played, entry.path, entry.provider_ids) == ("m9", None, None, {})


def test_an_administrator_lists_every_user_and_reads_each_history_with_one_sign_in(jellyfin) -> None:  # noqa: ANN001
    jellyfin.admin = True
    jellyfin.items = [movie("m1", "Mine", played=True)]
    jellyfin.others = {
        "u2": {"Name": "Bob", "Items": [movie("m2", "Theirs", seconds=60, last="2026-09-20T20:00:00Z")]},
        "u3": {"Name": "Old", "Items": [], "Disabled": True},
    }
    with admin_session(jellyfin.url, "alice", PASSWORD) as (session, admin_id):
        users = list_users(session)
        theirs = read_history(session, "u2")
        mine = read_history(session, admin_id)
    assert admin_id == USER_ID
    assert users == [JellyfinUser(USER_ID, "alice"), JellyfinUser("u2", "Bob"), JellyfinUser("u3", "Old", disabled=True)]
    assert ([entry.id for entry in theirs.entries], [entry.id for entry in mine.entries]) == (["m2"], ["m1"])
    sign_ins = sum(1 for _, path, _ in jellyfin.requests if path == "/Users/AuthenticateByName")
    assert (sign_ins, jellyfin.logouts) == (1, 1)


def test_a_jellyfin_account_that_is_not_an_administrator_is_refused_and_signed_out(jellyfin, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    with pytest.raises(JellyfinImportError) as refused, admin_session(jellyfin.url, "alice", PASSWORD):
        pass
    assert str(refused.value) == NOT_ADMIN and refused.value.__cause__ is None
    assert [path for _, path, _ in jellyfin.requests] == ["/Users/AuthenticateByName", "/Sessions/Logout"]
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text


def test_more_users_than_the_cap_are_refused_and_still_signs_out(jellyfin, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(client, "MAX_USERS", 1)
    jellyfin.admin = True
    jellyfin.others = {"u2": {"Name": "Bob", "Items": []}}
    with pytest.raises(JellyfinImportError, match="more than 1 users"), admin_session(jellyfin.url, "alice", PASSWORD) as (session, _):
        list_users(session)
    assert jellyfin.logouts == 1
