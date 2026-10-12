"""Jellyfin history import (ADR 0010 amendment): matching only what a member can see, and a merge that never loses Lumina data."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import LibraryItem, MediaTitle, MemberFavorite, PlaybackProgress, User
from app.services.jellyfin_history import Matcher, Target, apply, common_tail, merge, path_parts, plan
from app.services.jellyfin_history_client import JellyfinEntry, JellyfinHistory
from support import make_user
from title_support import ALICE, BOB, FILE, MOVIE, MOVIE_1080, MOVIE_4K, S1E1, S1E2, S2E1, SECRET_EPISODE, SERIES, SPECIALS, add_file, seed_tree, uid


@pytest.fixture
def household(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        session.commit()
    return db_factory, root


def match(factory, user_id: str, *entries: JellyfinEntry, series: dict | None = None):  # noqa: ANN001, ANN201
    with factory() as db:
        matched, unmatched = Matcher(db, db.get(User, user_id)).match(JellyfinHistory(list(entries), series or {}))
    return {entry.id: target for entry, target in matched}, [entry.label for entry in unmatched]


def test_path_tails_ignore_different_mount_prefixes() -> None:
    assert path_parts("/data/movies/X (2020)/X.mkv") == ("data", "movies", "X (2020)", "X.mkv")
    assert path_parts("D:\\Movies\\X (2020)\\X.mkv") == ("D:", "Movies", "X (2020)", "X.mkv")
    assert common_tail(path_parts("/data/movies/X (2020)/X.mkv"), path_parts("/media/movies/X (2020)/X.mkv")) == 3


def test_files_match_by_their_longest_shared_trailing_path(household) -> None:  # noqa: ANN001
    matched, unmatched = match(
        household[0], ALICE,
        JellyfinEntry(id="e", type="Episode", name="Pilot", path="/data/tv/Show/Season 01/Show S01E01.mkv"),
        JellyfinEntry(id="m", type="Movie", name="Movie", path="/mnt/nas/Movie (2020)/Movie (2020) - 4K.mkv"),
        JellyfinEntry(id="x", type="Movie", name="Elsewhere", path="/data/Other/Movie (2020) - 4K.mkv"),
    )
    assert matched == {"e": Target(S1E1, FILE[S1E1]), "m": Target(MOVIE, MOVIE_4K)}
    assert unmatched == ["Elsewhere"]  # only the file name in common: not enough


def test_an_ambiguous_path_matches_nothing_until_a_longer_tail_decides(household) -> None:  # noqa: ANN001
    factory, root = household
    with factory() as session:
        for n, folder in enumerate(("a", "b")):
            add_file(session, root, f"{folder}/Clips/clip.mp4", item_id=uid(800 + n), title_id=None, owner=ALICE,
                     title=f"Clip {folder}", kind="video")
        session.commit()
    matched, unmatched = match(
        factory, ALICE,
        JellyfinEntry(id="both", type="Movie", name="Clip", path="/j/Clips/clip.mp4"),
        JellyfinEntry(id="b", type="Movie", name="Clip B", path="/j/b/Clips/clip.mp4"),
    )
    assert matched == {"b": Target(None, uid(801))} and unmatched == ["Clip"]


def test_provider_ids_match_movies_series_and_numbered_episodes(household) -> None:  # noqa: ANN001
    matched, unmatched = match(
        household[0], ALICE,
        JellyfinEntry(id="m", type="Movie", name="Movie", provider_ids={"Imdb": "tt0133093"}),
        JellyfinEntry(id="s", type="Series", name="Show", provider_ids={"Tmdb": "100"}),
        JellyfinEntry(id="e", type="Episode", name="Return", series_id="jf-show", series_name="Show", season=2, episode=1),
        JellyfinEntry(id="later", type="Episode", name="Later", series_id="jf-show", series_name="Show", season=3, episode=1),
        JellyfinEntry(id="heat", type="Movie", name="Heat", year=1995, provider_ids={"Tmdb": "949"}),
        series={"jf-show": {"Tmdb": "100"}},
    )
    assert matched == {"m": Target(MOVIE, None), "s": Target(SERIES, None), "e": Target(S2E1, None)}
    assert sorted(unmatched) == ["Heat (1995)", "Show S03E01"]


def test_an_episode_without_both_numbers_never_matches_an_unnumbered_lumina_episode(household) -> None:  # noqa: ANN001
    factory, _root = household
    with factory() as session:  # Lumina has an unnumbered episode and an unnumbered season too
        session.get(MediaTitle, S1E2).index_number = None
        session.get(MediaTitle, SPECIALS).index_number = None
        session.commit()
    matched, unmatched = match(
        factory, ALICE,
        JellyfinEntry(id="no-episode", type="Episode", name="Some", series_id="jf-show", series_name="Show", season=1),
        JellyfinEntry(id="no-season", type="Episode", name="Other", series_id="jf-show", series_name="Show", episode=1),
        series={"jf-show": {"Tmdb": "100"}},
    )
    assert matched == {} and len(unmatched) == 2


def test_what_a_member_cannot_see_is_never_matched(household) -> None:  # noqa: ANN001
    secret = JellyfinEntry(id="h", type="Episode", name="Hidden", path="/data/tv/Secret Show/Season 01/Secret S01E01.mkv")
    trailer = JellyfinEntry(id="t", type="Movie", name="Trailer", path="/data/movies/Movie (2020)/trailers/Trailer.mp4")
    matched, unmatched = match(household[0], ALICE, secret, trailer)  # bob's private file; an extra
    assert matched == {} and sorted(unmatched) == ["Hidden", "Trailer"]
    assert match(household[0], BOB, secret)[0] == {"h": Target(SECRET_EPISODE, FILE[SECRET_EPISODE])}


def test_missing_files_are_not_matched(household) -> None:  # noqa: ANN001
    factory, _root = household
    with factory() as session:
        session.get(LibraryItem, FILE[S1E1]).status = "missing"
        session.commit()
    entry = JellyfinEntry(id="e", type="Episode", name="Pilot", path="/data/tv/Show/Season 01/Show S01E01.mkv")
    assert match(factory, ALICE, entry) == ({}, ["Pilot"])


T1 = datetime(2026, 9, 20, 20, 0)


def lumina(completed: bool, when: datetime) -> PlaybackProgress:
    return PlaybackProgress(completed=completed, last_watched_at=when, position_seconds=900)


def jf(**fields) -> JellyfinEntry:  # noqa: ANN003
    return JellyfinEntry(id="x", type="Movie", name="X", **fields)


@pytest.mark.parametrize(("current", "entry", "expected"), [
    (None, jf(played=True, last_played=T1), (0, True)),
    (None, jf(position_seconds=600, last_played=T1), (600, False)),
    (None, jf(played=True), (0, True)),  # undated, but there is nothing in Lumina to protect
    (None, jf(position_seconds=600), None),  # undated resume: no honest place in Continue watching
    (lumina(False, T1 - timedelta(days=1)), jf(played=True, last_played=T1), (0, True)),
    (lumina(False, T1 + timedelta(days=1)), jf(played=True, last_played=T1), None),  # Lumina is newer
    (lumina(False, T1), jf(position_seconds=600, last_played=T1), None),  # the same moment: a re-run changes nothing
    (lumina(True, T1 - timedelta(days=1)), jf(position_seconds=600, last_played=T1), None),  # completed stays completed
    (lumina(False, T1 - timedelta(days=1)), jf(played=True), None),  # an undated play cannot prove it is newer
    (None, jf(favorite=True), None),  # a favorite is not progress
])
def test_merge_never_loses_lumina_progress(current, entry, expected) -> None:  # noqa: ANN001
    assert merge(entry, current) == expected


def run(factory, user_id: str, *entries: JellyfinEntry, write: bool = True):  # noqa: ANN001, ANN201
    with factory() as db:
        user = db.get(User, user_id)
        result = plan(db, user, JellyfinHistory(list(entries), {}))
        if write:
            apply(db, user, result)
            db.commit()
    return result.summary()


HISTORY = (
    JellyfinEntry(id="m", type="Movie", name="Movie", provider_ids={"Tmdb": "603"}, played=True, favorite=True, last_played=T1),
    JellyfinEntry(id="e1", type="Episode", name="Pilot", path="/data/tv/Show/Season 01/Show S01E01.mkv", position_seconds=300, last_played=T1),
    JellyfinEntry(id="e2", type="Episode", name="Second", path="/data/tv/Show/Season 01/Show S01E02.mkv", played=True, last_played=T1),
    JellyfinEntry(id="s", type="Series", name="Show", provider_ids={"Tmdb": "100"}, favorite=True),
    JellyfinEntry(id="h", type="Movie", name="Heat", year=1995, provider_ids={"Tmdb": "949"}, played=True, last_played=T1),
)


def test_plan_counts_apply_writes_and_a_rerun_changes_nothing(household) -> None:  # noqa: ANN001
    factory, _root = household
    with factory() as session:  # alice watched Second in Lumina after her last Jellyfin play
        session.add(PlaybackProgress(id=uid(700), user_id=ALICE, item_id=FILE[S1E2], position_seconds=900,
                                     duration_seconds=1500, last_watched_at=T1 + timedelta(days=2)))
        session.commit()
    first = run(factory, ALICE, *HISTORY)
    assert first.model_dump() == {"watched": 1, "in_progress": 1, "favorites": 2, "up_to_date": 1, "unmatched": 1,
                                  "unmatched_names": ["Heat (1995)"]}
    with factory() as session:
        rows = {row.item_id: row for row in session.query(PlaybackProgress).filter_by(user_id=ALICE)}
        pilot = rows[FILE[S1E1]]
        assert (pilot.position_seconds, pilot.completed, pilot.duration_seconds, pilot.last_watched_at) == (300, False, 1500, T1)
        assert (rows[FILE[S1E2]].position_seconds, rows[FILE[S1E2]].completed) == (900, False)  # newer Lumina progress kept
        [movie] = [row for item_id, row in rows.items() if item_id in (MOVIE_1080, MOVIE_4K)]
        assert (movie.completed, movie.position_seconds, movie.last_watched_at) == (True, 0, T1)
        assert {row.target_id for row in session.query(MemberFavorite).filter_by(user_id=ALICE)} == {MOVIE, SERIES}
    again = run(factory, ALICE, *HISTORY)
    assert again.model_dump() == {"watched": 0, "in_progress": 0, "favorites": 0, "up_to_date": 4, "unmatched": 1,
                                  "unmatched_names": ["Heat (1995)"]}


def test_two_jellyfin_copies_of_one_title_collapse_to_the_newest(household) -> None:  # noqa: ANN001
    summary = run(
        household[0], ALICE,
        JellyfinEntry(id="a", type="Movie", name="Movie", provider_ids={"Tmdb": "603"}, played=True, last_played=T1),
        JellyfinEntry(id="b", type="Movie", name="Movie", provider_ids={"Imdb": "tt0133093"}, position_seconds=60,
                      last_played=T1 + timedelta(hours=1)),
        write=False,
    )
    assert (summary.watched, summary.in_progress, summary.up_to_date) == (0, 1, 0)


def test_a_dismiss_after_the_jellyfin_play_is_kept_and_an_older_one_is_undone(household) -> None:  # noqa: ANN001
    factory, _root = household
    with factory() as session:
        for n, (episode, dismissed) in enumerate(((S1E1, T1 + timedelta(days=1)), (S1E2, T1 - timedelta(hours=12)))):
            session.add(PlaybackProgress(id=uid(710 + n), user_id=ALICE, item_id=FILE[episode], position_seconds=60,
                                         last_watched_at=T1 - timedelta(days=1), dismissed_at=dismissed))
        session.commit()
    run(factory, ALICE,
        JellyfinEntry(id="e1", type="Episode", name="Pilot", path="/data/tv/Show/Season 01/Show S01E01.mkv", position_seconds=300, last_played=T1),
        JellyfinEntry(id="e2", type="Episode", name="Second", path="/data/tv/Show/Season 01/Show S01E02.mkv", position_seconds=300, last_played=T1))
    with factory() as session:
        rows = {row.item_id: row for row in session.query(PlaybackProgress).filter_by(user_id=ALICE)}
        assert (rows[FILE[S1E1]].position_seconds, rows[FILE[S1E1]].dismissed_at) == (300, T1 + timedelta(days=1))
        assert (rows[FILE[S1E2]].position_seconds, rows[FILE[S1E2]].dismissed_at) == (300, None)


def test_an_undated_resume_point_counts_as_up_to_date_and_writes_nothing(household) -> None:  # noqa: ANN001
    factory, _root = household
    summary = run(factory, ALICE, JellyfinEntry(id="e1", type="Episode", name="Pilot",
                                                path="/data/tv/Show/Season 01/Show S01E01.mkv", position_seconds=300))
    assert (summary.in_progress, summary.up_to_date) == (0, 1)
    with factory() as session:
        assert session.query(PlaybackProgress).filter_by(user_id=ALICE).count() == 0
