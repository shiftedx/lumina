"""Members import their own Jellyfin history (ADR 0010 amendment): status, preview, import, and what must never happen."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from app.models import AppSettings, MemberFavorite, PlaybackProgress, User
from jellyfin_fake import TOKEN, FakeJellyfin, episode, movie, series
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, FILE, MOVIE, S1E1, SECRET_EPISODE, SERIES, seed_tree

PASSWORD = "jf-secret-pw"
URL = "/api/me/jellyfin-import"
SIGN_IN = {"username": "alice", "password": PASSWORD}
FIRST = {"watched": 1, "in_progress": 1, "favorites": 2, "up_to_date": 0, "unmatched": 1, "unmatched_names": ["Heat (1995)"]}


@pytest.fixture
def jellyfin():  # noqa: ANN201
    fake = FakeJellyfin(password=PASSWORD)
    fake.items = [
        movie("jf-movie", "The Movie", year=2020, played=True, favorite=True, last="2026-09-20T20:00:00.0000000Z", Tmdb="603"),
        episode("jf-s1e1", "jf-show", "Show", 1, 1, path="/data/tv/Show/Season 01/Show S01E01.mkv", seconds=300,
                last="2026-09-21T21:00:00Z"),
        series("jf-show", "Show", favorite=True, Tmdb="100"),
        movie("jf-heat", "Heat", year=1995, played=True, last="2026-09-19T19:00:00Z", Tmdb="949"),
    ]
    fake.series = {"jf-show": series("jf-show", "Show", Tmdb="100")}
    yield fake
    fake.close()


@pytest.fixture
def vault(db_factory, tmp_path, jellyfin):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, tmp_path.resolve() / "media")
        seed_app_settings(session, jellyfin_import_url=jellyfin.url)  # commits
    return db_factory


def member(api_client, factory, user_id: str = ALICE):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def written(factory) -> tuple[int, int]:  # noqa: ANN001
    with factory() as session:
        return session.query(PlaybackProgress).count(), session.query(MemberFavorite).count()


def test_members_see_the_admin_address(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    assert member(api_client, vault).get(URL).json() == {"server": jellyfin.url}


def test_preview_changes_nothing_import_applies_it_and_a_rerun_is_a_no_op(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    client = member(api_client, vault)
    preview = client.post(f"{URL}/preview", json=SIGN_IN)
    assert preview.status_code == 200, preview.text
    assert preview.json() == FIRST
    assert written(vault) == (0, 0)
    assert client.post(URL, json=SIGN_IN).json() == FIRST
    assert written(vault) == (2, 2)
    with vault() as session:
        pilot = session.query(PlaybackProgress).filter_by(item_id=FILE[S1E1]).one()
        assert (pilot.user_id, pilot.position_seconds, pilot.last_watched_at) == (ALICE, 300, datetime(2026, 9, 21, 21, 0))
        assert {(row.user_id, row.target_id) for row in session.query(MemberFavorite)} == {(ALICE, MOVIE), (ALICE, SERIES)}
    assert client.post(URL, json=SIGN_IN).json() == {**FIRST, "watched": 0, "in_progress": 0, "favorites": 0, "up_to_date": 3}
    assert written(vault) == (2, 2)
    assert jellyfin.logouts == 3


def test_newer_lumina_progress_is_never_overwritten(vault, api_client) -> None:  # noqa: ANN001
    with vault() as session:
        session.add(PlaybackProgress(id="p1", user_id=ALICE, item_id=FILE[S1E1], position_seconds=900, duration_seconds=1500,
                                     last_watched_at=datetime(2026, 9, 25)))
        session.commit()
    result = member(api_client, vault).post(URL, json=SIGN_IN).json()
    assert (result["in_progress"], result["up_to_date"]) == (0, 1)
    with vault() as session:
        assert session.get(PlaybackProgress, "p1").position_seconds == 900


def test_what_a_member_cannot_see_is_never_matched_or_named(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    jellyfin.items = [episode("jf-secret", "jf-secret-show", "Their Show", 1, 1, played=True,
                              path="/data/tv/Secret Show/Season 01/Secret S01E01.mkv", last="2026-09-20T20:00:00Z")]
    response = member(api_client, vault).post(URL, json=SIGN_IN)
    assert response.json() == {"watched": 0, "in_progress": 0, "favorites": 0, "up_to_date": 0, "unmatched": 1,
                               "unmatched_names": ["Their Show S01E01"]}
    assert "Hidden" not in response.text and FILE[SECRET_EPISODE] not in response.text
    assert written(vault) == (0, 0)


def test_without_an_address_nothing_is_contacted(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    with vault() as session:
        session.get(AppSettings, 1).jellyfin_import_url = None
        session.commit()
    client = member(api_client, vault)
    assert client.get(URL).json() == {"server": None}
    refused = client.post(f"{URL}/preview", json=SIGN_IN)
    assert (refused.status_code, refused.json()["detail"]) == (409, "Ask a vault owner to set the Jellyfin server address.")
    assert jellyfin.requests == []


def test_members_cannot_choose_the_address(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    response = member(api_client, vault).post(f"{URL}/preview", json={**SIGN_IN, "server": "http://169.254.169.254"})
    assert response.status_code == 422
    assert jellyfin.requests == []


def test_a_refused_sign_in_is_400_and_the_password_is_never_echoed_or_logged(vault, api_client, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    client = member(api_client, vault)
    refused = client.post(f"{URL}/preview", json={"username": "alice", "password": "wrong-horse"})
    assert (refused.status_code, refused.json()["detail"]) == (400, "Jellyfin did not accept that username and password.")
    too_long = client.post(f"{URL}/preview", json={"username": "alice", "password": "p" * 1025})
    assert too_long.status_code == 422 and "p" * 1025 not in too_long.text
    assert client.post(URL, json=SIGN_IN).status_code == 200
    for secret in ("wrong-horse", PASSWORD, TOKEN):
        assert secret not in caplog.text


def test_sign_ins_through_lumina_are_rate_limited(vault, api_client) -> None:  # noqa: ANN001
    client = member(api_client, vault)
    codes = [client.post(f"{URL}/preview", json={"username": "alice", "password": "guess"}).status_code for _ in range(11)]
    assert codes == [400] * 10 + [429]
