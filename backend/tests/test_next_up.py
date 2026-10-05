"""Next Up is a pure function of one series' ordered episodes and the member's progress."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.titles import UNNUMBERED, EpisodeProgress, next_up, resume_anchor

T0 = datetime(2026, 9, 1)


def ep(season: int, episode: int, *, at: int | None = None, done: bool = False, pos: int = 0, name: str | None = None) -> EpisodeProgress:
    return EpisodeProgress(
        name or f"s{season}e{episode}", season, episode,
        None if at is None else T0 + timedelta(hours=at), done, pos,
    )


CASES = {
    "nothing watched": ([ep(1, 1), ep(1, 2)], None),
    "normal progression": ([ep(1, 1, at=1, done=True), ep(1, 2)], "s1e2"),
    "crosses a season boundary": ([ep(1, 1, at=1, done=True), ep(2, 1)], "s2e1"),
    "in-progress anchor belongs to resume": ([ep(1, 1, at=1, done=True), ep(1, 2, at=2, pos=300)], None),
    "partial play below 95% is in progress": ([ep(1, 1, at=1, pos=1200), ep(1, 2)], None),
    "rewatch from E1 of a completed series moves forward": (
        [ep(1, 1, at=10, done=True), ep(1, 2, at=2, done=True), ep(1, 3, at=3, done=True)], "s1e2"),
    "finished series": ([ep(1, 1, at=1, done=True), ep(1, 2, at=2, done=True)], None),
    "a watched special never becomes the anchor": ([ep(0, 1, at=5, done=True), ep(1, 1, at=1, done=True), ep(1, 2)], "s1e2"),
    "a special is never next": ([ep(0, 1), ep(1, 1, at=1, done=True)], None),
    "multi-episode file counts once": ([ep(1, 1, at=1, done=True, name="s1e1-2"), ep(1, 3)], "s1e3"),
    "opened but never started is ignored": ([ep(1, 1, at=1, done=True), ep(1, 2, at=2)], "s1e2"),
    "unnumbered episode sorts last": ([ep(1, 1, at=1, done=True), ep(1, UNNUMBERED, name="bonus")], "bonus"),
}


def test_a_dismissed_anchor_hides_the_series() -> None:
    episodes = [ep(1, 1, at=1, done=True), ep(1, 2)]
    assert next_up(episodes, dismissed=True) is None
    assert next_up(episodes, dismissed=False).title_id == "s1e2"


@pytest.mark.parametrize("case", sorted(CASES))
def test_next_up_table(case: str) -> None:
    episodes, expected = CASES[case]
    pick = next_up(episodes)
    assert (pick.title_id if pick else None) == expected


def test_resume_anchor_is_the_latest_started_regular_episode() -> None:
    episodes = [ep(0, 1, at=9, pos=10), ep(1, 1, at=1, done=True), ep(1, 2, at=2, pos=300)]
    assert resume_anchor(episodes).title_id == "s1e2"
    assert resume_anchor([ep(1, 1)]) is None
