"""An admin brings the household over from Jellyfin (ADR 0010 amendment): pairing, preview, import, links, isolation."""
from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AccountToken, AppSettings, MemberFavorite, PlaybackProgress, User
from app.security import has_local_password
from app.services import jellyfin_history_client as client
from app.services import jellyfin_household as household
from app.services.users import UserService
from jellyfin_fake import TOKEN, USER_ID, FakeJellyfin, episode, movie, series
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, seed_tree

PASSWORD = "jf-admin-pw"
URL = "/api/admin/jellyfin-household"
SIGN_IN = {"username": "root", "password": PASSWORD}
OWNER = "owner-id"
LONG = "x" * 81
ALL = [USER_ID, "jf-alice", "jf-carol"]
PAIRED = "root is already paired with the Lumina member dana."


@pytest.fixture
def jellyfin():  # noqa: ANN201
    fake = FakeJellyfin(username="root", password=PASSWORD, admin=True)
    fake.items = [movie("jf-movie", "The Movie", played=True, last="2026-09-20T20:00:00Z", Tmdb="603")]
    fake.others = {
        "jf-alice": {"Name": "Alice", "Items": [
            episode("jf-s1e1", "jf-show", "Show", 1, 1, path="/data/tv/Show/Season 01/Show S01E01.mkv", seconds=300,
                    last="2026-09-21T21:00:00Z"),
        ]},
        "jf-carol": {"Name": "Carol", "Items": [
            movie("jf-movie", "The Movie", played=True, last="2026-09-22T20:00:00Z", Tmdb="603"),
            series("jf-show", "Show", favorite=True, Tmdb="100"),
        ]},
        "jf-dave": {"Name": "Dave", "Items": [], "Disabled": True},
        "jf-dana": {"Name": "Dana", "Items": []},
        "jf-long": {"Name": LONG, "Items": []},
    }
    fake.series = {"jf-show": series("jf-show", "Show", Tmdb="100")}
    yield fake
    fake.close()


@pytest.fixture
def vault(db_factory, tmp_path, jellyfin):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add_all([make_user(OWNER, role="admin", username="dana"), make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, tmp_path.resolve() / "media")
        seed_app_settings(session, jellyfin_import_url=jellyfin.url)  # commits
    return db_factory


def as_user(api_client, factory, user_id: str = OWNER):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def brief(response) -> list[tuple]:  # noqa: ANN001
    """(Jellyfin name, action, Lumina username, reason, error, (watched, in progress, favorites) or None) per row."""
    assert response.status_code == 200, response.text
    return [
        (row["jellyfin_name"], row["action"], row["lumina_username"], row["reason"], row["error"],
         row["summary"] and (row["summary"]["watched"], row["summary"]["in_progress"], row["summary"]["favorites"]))
        for row in response.json()["members"]
    ]


def counts(factory) -> tuple[int, int, int, int]:  # noqa: ANN001
    with factory() as session:
        return (session.query(User).count(), session.query(PlaybackProgress).count(),
                session.query(MemberFavorite).count(), session.query(AccountToken).count())


def test_preview_pairs_every_jellyfin_user_and_changes_nothing(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    before = counts(vault)
    response = as_user(api_client, vault).post(f"{URL}/preview", json=SIGN_IN)
    assert brief(response) == [
        ("root", "import", "dana", None, None, (1, 0, 0)),
        ("Alice", "import", "alice", None, None, (0, 1, 0)),
        ("Carol", "create", "carol", None, None, (1, 0, 1)),
        ("Dana", "skip", None, PAIRED, None, None),
        ("Dave", "create", "dave", None, None, (0, 0, 0)),
        (LONG, "skip", None, household.INVALID_NAME, None, None),
    ]
    assert [row["disabled"] for row in response.json()["members"]] == [False, False, False, False, True, False]
    assert counts(vault) == before
    assert jellyfin.logouts == 1


def test_bringing_members_over_imports_creates_links_and_a_rerun_changes_nothing(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    admin = as_user(api_client, vault)
    response = admin.post(URL, json={**SIGN_IN, "jellyfin_ids": ALL})
    assert brief(response) == [
        ("root", "import", "dana", None, None, (1, 0, 0)),
        ("Alice", "import", "alice", None, None, (0, 1, 0)),
        ("Carol", "create", "carol", None, None, (1, 0, 1)),
        ("Dana", "skip", None, PAIRED, None, None),
        ("Dave", "skip", None, household.NOT_SELECTED, None, None),
        (LONG, "skip", None, household.INVALID_NAME, None, None),
    ]
    links = {row["jellyfin_name"]: row["reset_url"] for row in response.json()["members"] if row["reset_url"]}
    assert list(links) == ["Carol"] and "/#reset=" in links["Carol"]
    assert counts(vault) == (4, 3, 1, 1)
    with vault() as session:
        carol = session.query(User).filter_by(username="carol").one()
        assert (carol.role, carol.is_active, carol.display_name, carol.password_hash) == ("viewer", True, "Carol", None)
        assert {(row.user_id, row.completed) for row in session.query(PlaybackProgress)} == {(OWNER, True), (ALICE, False), (carol.id, True)}
        UserService(session).redeem_password_reset(links["Carol"].split("#reset=")[1], "Blue-Harbor-2046")
        assert has_local_password(session.get(User, carol.id).password_hash)
    again = admin.post(URL, json={**SIGN_IN, "jellyfin_ids": ALL})
    assert brief(again)[:3] == [
        ("root", "import", "dana", None, None, (0, 0, 0)),
        ("Alice", "import", "alice", None, None, (0, 0, 0)),
        ("Carol", "import", "carol", None, None, (0, 0, 0)),
    ]
    assert not any(row["reset_url"] for row in again.json()["members"])
    assert counts(vault) == (4, 3, 1, 1)
    assert jellyfin.logouts == 2


def test_one_member_failing_never_undoes_the_others(vault, api_client, jellyfin, monkeypatch, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.INFO, logger="lumina.audit")
    monkeypatch.setattr(client, "MAX_ENTRIES", 2)  # Alice's three entries are too many; everyone else has at most two
    jellyfin.others["jf-alice"]["Items"] += [movie(f"jf-a{n}", f"A{n}", played=True) for n in range(2)]
    real_apply = household.apply

    def refuse_carol(db, user, result):  # noqa: ANN001, ANN202
        if user.username == "carol":
            raise IntegrityError("INSERT", {}, Exception("refused"))
        real_apply(db, user, result)

    monkeypatch.setattr(household, "apply", refuse_carol)
    response = as_user(api_client, vault).post(URL, json={**SIGN_IN, "jellyfin_ids": ALL})
    assert response.status_code == 200, response.text
    rows = {row["jellyfin_name"]: row for row in response.json()["members"]}
    assert rows["Alice"]["error"] == "This Jellyfin history has more than 2 items, more than Lumina imports at once."
    assert (rows["Carol"]["error"], rows["Carol"]["reset_url"], rows["Carol"]["summary"]) == (household.SAVE_FAILED, None, None)
    assert rows["root"]["summary"]["watched"] == 1
    with vault() as session:
        assert session.query(User).filter_by(username="carol").count() == 0  # her new account rolled back with her history
        assert session.query(AccountToken).count() == 0
        assert [row.user_id for row in session.query(PlaybackProgress)] == [OWNER]
    assert "user.create" not in caplog.text and "reset.issue" not in caplog.text  # nothing audited for a rolled-back member


@pytest.mark.parametrize("path", ["/preview", ""])
def test_a_member_deleted_after_pairing_errors_and_the_others_still_come_over(vault, api_client, jellyfin, monkeypatch, caplog, path) -> None:  # noqa: ANN001, E501
    caplog.set_level(logging.INFO, logger="lumina.audit")
    real_pair = household.pair

    def pair_then_delete_alice(*args):  # noqa: ANN002, ANN202
        pairings = real_pair(*args)
        with vault() as session:
            session.delete(session.get(User, ALICE))
            session.commit()
        return pairings

    monkeypatch.setattr(household, "pair", pair_then_delete_alice)
    response = as_user(api_client, vault).post(f"{URL}{path}", json={**SIGN_IN, **({"jellyfin_ids": ALL} if path == "" else {})})
    assert brief(response)[:3] == [
        ("root", "import", "dana", None, None, (1, 0, 0)),
        ("Alice", "import", "alice", None, household.MEMBER_GONE, None),
        ("Carol", "create", "carol", None, None, (1, 0, 1)),
    ]
    if path == "":
        assert "/#reset=" in response.json()["members"][2]["reset_url"]
        with vault() as session:
            carol = session.query(User).filter_by(username="carol").one().id
        assert f"user={carol}" in caplog.text and "reset.issue" in caplog.text


def test_only_an_admin_signed_in_as_a_jellyfin_administrator_can_bring_members_over(vault, api_client, jellyfin, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    assert as_user(api_client, vault, ALICE).post(f"{URL}/preview", json=SIGN_IN).status_code == 403
    admin = as_user(api_client, vault)
    assert admin.post(f"{URL}/preview", json={**SIGN_IN, "server": "http://169.254.169.254"}).status_code == 422
    assert jellyfin.requests == []
    jellyfin.admin = False
    refused = admin.post(URL, json={**SIGN_IN, "jellyfin_ids": ALL})
    assert (refused.status_code, refused.json()["detail"]) == (400, client.NOT_ADMIN)
    assert [path for _, path, _ in jellyfin.requests] == ["/Users/AuthenticateByName", "/Sessions/Logout"]
    wrong = admin.post(f"{URL}/preview", json={**SIGN_IN, "password": "wrong-horse"})
    assert (wrong.status_code, wrong.json()["detail"]) == (400, "Jellyfin did not accept that username and password.")
    jellyfin.admin = True
    link = admin.post(URL, json={**SIGN_IN, "jellyfin_ids": ALL}).json()["members"][2]["reset_url"]
    for secret in (PASSWORD, "wrong-horse", TOKEN, link.split("#reset=")[1]):
        assert secret not in caplog.text
    with vault() as session:
        session.get(AppSettings, 1).jellyfin_import_url = None
        session.commit()
    unset = admin.post(f"{URL}/preview", json=SIGN_IN)
    assert (unset.status_code, unset.json()["detail"]) == (409, "Set the Jellyfin server address under Media server first.")


def test_a_jellyfin_id_longer_than_64_characters_is_refused(vault, api_client, jellyfin) -> None:  # noqa: ANN001
    response = as_user(api_client, vault).post(URL, json={**SIGN_IN, "jellyfin_ids": ["x" * 65]})
    assert response.status_code == 422
    assert jellyfin.requests == []
