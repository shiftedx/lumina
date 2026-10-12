"""Gallery AI extras: stored summaries and key scenes, spoiler-safe and switchable."""
from __future__ import annotations

import pytest

from app.models import MediaTitle, TranscriptCue, User
from app.services import ai_extras, model_endpoints
from discovery_support import add_movie, add_progress, add_series, add_summary, add_title, add_version
from support import make_user, memory_session_factory, seed_app_settings
from title_support import ALICE, BOB, MOVIE, S1E1, SECRET_SERIES, SERIES, seed_tree


def _member(session) -> User:  # noqa: ANN001
    return session.get(User, "member")


def _show(session) -> None:  # noqa: ANN001
    """Season 1: e1 finished, e2 in progress, e3 unwatched, e4 finished; e1–e3 have summaries. A special is finished last."""
    session.add_all([make_user("owner"), make_user("member"), make_user("other")])
    add_series(session, "show", "Show", seasons={0: 1, 1: 4})
    for n in (1, 2, 3):
        add_summary(session, f"show-s1e{n}-v", [], overview=f"Summary   of\n episode {n}.")
    add_progress(session, "member", "show-s1e1-v", completed=True, minutes_ago=30)
    add_progress(session, "member", "show-s1e2-v", position=300, minutes_ago=5)
    add_progress(session, "member", "show-s1e4-v", completed=True, minutes_ago=10)
    add_progress(session, "member", "show-s0e1-v", completed=True, minutes_ago=1)
    add_progress(session, "other", "show-s1e3-v", completed=True)  # another member's completion never counts
    session.commit()


def _cues(session, item_id: str, texts: dict[int, str]) -> None:  # noqa: ANN001
    session.add_all([
        TranscriptCue(transcript_id=f"t-{item_id}", ordinal=ordinal, start_ms=ordinal * 61_000, end_ms=ordinal * 61_000 + 900, text=text)
        for ordinal, text in texts.items()
    ])


def test_only_completed_episodes_get_summary_text() -> None:
    session = memory_session_factory()()
    _show(session)
    result = ai_extras.episode_summaries(session, _member(session), session.get(MediaTitle, "show"), 1)
    assert result.model_dump() == {"available": True, "items": [{"episode_id": "show-s1e1", "overview": "Summary of episode 1."}]}
    other = ai_extras.episode_summaries(session, session.get(User, "other"), session.get(MediaTitle, "show"), 1)
    assert [item.episode_id for item in other.items] == ["show-s1e3"]


def test_the_newest_succeeded_summary_wins_and_long_text_is_cut_at_a_word() -> None:
    session = memory_session_factory()()
    _show(session)
    add_summary(session, "show-s1e1-v", [], overview="word " * 200, minutes_ago=-10)  # newer
    failed = add_summary(session, "show-s1e1-v", [], overview="Failed attempt.", minutes_ago=-20)
    failed.state = "failed"
    session.commit()
    (item,) = ai_extras.episode_summaries(session, _member(session), session.get(MediaTitle, "show"), 1).items
    assert len(item.overview) <= 600 and item.overview.endswith("word…") and "  " not in item.overview


def test_another_members_private_version_never_leaks_a_summary() -> None:
    session = memory_session_factory()()
    _show(session)
    add_version(session, "show-s1e4-private", "show-s1e4", owner="owner", visibility="private", kind="episode")
    add_summary(session, "show-s1e4-private", [], overview="The owner's cut.")
    session.commit()
    items = ai_extras.episode_summaries(session, _member(session), session.get(MediaTitle, "show"), 1).items
    assert [item.episode_id for item in items] == ["show-s1e1"]


def test_episode_summaries_switched_off_return_nothing() -> None:
    session = memory_session_factory()()
    _show(session)
    seed_app_settings(session, ai_features_disabled=[ai_extras.EPISODE_SUMMARIES])
    assert ai_extras.episode_summaries(session, _member(session), session.get(MediaTitle, "show"), 1).model_dump() == {"available": False, "items": []}


def _scenes_film(session, *, completed: bool = True) -> None:  # noqa: ANN001
    session.add_all([make_user("owner"), make_user("member")])
    add_movie(session, "film", "Film")
    add_summary(session, "film-v", [
        {"text": "They cross the ice.", "cue_ordinals": [2, 3], "start_ms": 1},
        {"text": "Short", "cue_ordinals": [4]},  # its quote "Hi." is too short
        {"text": "x" * 300, "cue_ordinals": [5]},
        {"text": "The lamp.", "cue_ordinals": [6]},
        {"text": "A fourth scene.", "cue_ordinals": [7]},  # beyond three
        {"text": "Uncited", "cue_ordinals": []},
    ])
    _cues(session, "film-v", {
        2: "<i>Carry it</i>   until the ice sings,\nand do not look back.", 4: "Hi.", 5: "long " * 60,
        6: "Keep the lamp burning all winter.", 7: "Another line that is long enough.",
    })
    if completed:
        add_progress(session, "member", "film-v", completed=True)
    session.commit()


def test_key_scenes_quote_the_first_cited_cue_of_a_completed_movie() -> None:
    session = memory_session_factory()()
    _scenes_film(session)
    scenes = ai_extras.key_scenes(session, _member(session), session.get(MediaTitle, "film"))
    assert (scenes.available, scenes.title_id, scenes.item_id) == (True, "film", "film-v")
    first, long, lamp = scenes.scenes
    assert first.model_dump() == {"start_ms": 122_000, "quote": "Carry it until the ice sings, and do not look back.", "caption": "They cross the ice."}
    assert len(long.quote) <= 160 and long.quote.endswith("…") and long.start_ms == 305_000
    assert len(long.caption) == 200 and long.caption.endswith("…")
    assert (lamp.quote, lamp.caption) == ("Keep the lamp burning all winter.", "The lamp.")


def test_key_scenes_need_a_completed_source_a_summary_and_the_switch() -> None:
    session = memory_session_factory()()
    _scenes_film(session, completed=False)
    film = session.get(MediaTitle, "film")
    assert ai_extras.key_scenes(session, _member(session), film).model_dump() == {"available": False, "title_id": None, "item_id": None, "scenes": []}
    add_progress(session, "member", "film-v", completed=True)
    add_title(session, "set", "boxset", "Saga")
    session.commit()
    assert ai_extras.key_scenes(session, _member(session), film).available is True
    assert ai_extras.key_scenes(session, _member(session), session.get(MediaTitle, "set")).available is False
    seed_app_settings(session, ai_features_disabled=[ai_extras.KEY_SCENES])
    assert ai_extras.key_scenes(session, _member(session), film).available is False


def test_key_scenes_come_from_the_last_completed_regular_episode() -> None:
    session = memory_session_factory()()
    _show(session)  # e4 is the newest completed regular episode; e2 (newer) is in progress, the special is newer still
    add_summary(session, "show-s1e4-v", [{"text": "The storm arrives.", "cue_ordinals": [9]}])
    _cues(session, "show-s1e4-v", {9: "Batten down every hatch before dark."})
    session.commit()
    scenes = ai_extras.key_scenes(session, _member(session), session.get(MediaTitle, "show"))
    assert (scenes.title_id, scenes.item_id, [scene.quote for scene in scenes.scenes]) == ("show-s1e4", "show-s1e4-v", ["Batten down every hatch before dark."])
    assert ai_extras.key_scenes(session, session.get(User, "owner"), session.get(MediaTitle, "show")).available is False  # watched nothing


def test_clip_collapses_whitespace_and_cuts_at_a_word() -> None:
    assert ai_extras.clip("  a \n b  ", 10) == "a b"
    assert ai_extras.clip("alpha beta gamma", 12) == "alpha beta…"
    assert ai_extras.clip("x" * 20, 10) == "x" * 9 + "…"


def test_new_feature_keys_need_the_assistant() -> None:
    assert model_endpoints.REQUIRES["episode_summaries"] == model_endpoints.REQUIRES["key_scenes"] == "assistant"


@pytest.fixture
def library(db_factory, tmp_path):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, tmp_path.resolve() / "media")
        session.commit()
    return db_factory


def test_routes_check_visibility_and_type_first(library, api_client) -> None:  # noqa: ANN001
    with library() as session:
        alice = session.get(User, ALICE)
    client = api_client(user=alice, base_url="http://localhost")
    assert client.get(f"/api/titles/{SERIES}/episode-summaries", params={"season": 1}).json() == {"available": True, "items": []}
    assert client.get(f"/api/titles/{SERIES}/episode-summaries").status_code == 422
    assert client.get(f"/api/titles/{SERIES}/episode-summaries", params={"season": -1}).status_code == 422
    for path in (f"/api/titles/{MOVIE}/episode-summaries?season=1", f"/api/titles/{SECRET_SERIES}/episode-summaries?season=1",
                 f"/api/titles/{SECRET_SERIES}/key-scenes", "/api/titles/not-a-uuid/key-scenes"):
        assert client.get(path).status_code == 404, path
    assert client.get(f"/api/titles/{MOVIE}/key-scenes").json() == {"available": False, "title_id": None, "item_id": None, "scenes": []}
    assert client.get(f"/api/titles/{S1E1}/key-scenes").json()["available"] is False
