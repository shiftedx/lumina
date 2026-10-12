"""Just-ahead generation: chains start on the completed flip, run one job at a time, stay bounded."""
from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db as db_module
from app.models import MediaTitle, Transcript, User
from app.persistence import write_transaction
from app.schemas import PlaybackProgressUpdateRequest
from app.security import get_current_user
from app.services import just_ahead, recaps, summaries
from app.services.ai_extras import EPISODE_SUMMARIES, KEY_SCENES
from app.services.local_playback_sessions import sessions
from app.services.media_titles import jellyfin_id
from app.services.playback import JUST_AHEAD_QUEUED, PlaybackProgressService
from app.services.titles import TitleService
from app.services.yt_dlp_service import YtDlpService
from discovery_support import add_movie, add_progress, add_series, add_summary, add_version
from support import make_user, memory_session_factory
from title_support import ALICE, ALICE_TOKEN, FILE, S1E1, S1E2, jellyfin_household, mediabrowser


@pytest.fixture(autouse=True)
def isolated_chains(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(just_ahead, "_claims", set())
    monkeypatch.setattr(just_ahead, "POLL_SECONDS", 0.01)


def _recorder(monkeypatch) -> list[tuple[str, str]]:  # noqa: ANN001
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(just_ahead, "on_completed", lambda user_id, item_id: calls.append((user_id, item_id)))
    return calls


def test_only_the_flip_to_completed_of_a_titled_item_queues_the_chain(monkeypatch) -> None:  # noqa: ANN001
    calls = _recorder(monkeypatch)
    session = memory_session_factory()()
    session.add_all([make_user("owner"), make_user("member")])
    add_movie(session, "film", "Film")
    add_version(session, "clip", None, kind="video")  # an untitled YouTube-style item
    session.commit()
    member = session.get(User, "member")

    def update(item_id: str, *, completed: bool, position: int = 60) -> None:
        with write_transaction(session, name="test_progress"):
            PlaybackProgressService(session).update(item_id, PlaybackProgressUpdateRequest(position_seconds=position, completed=completed), member)
        session.info.pop(JUST_AHEAD_QUEUED, None)  # each request has its own session

    update("film-v", completed=False)
    assert calls == []
    update("film-v", completed=True)
    assert calls == [("member", "film-v")]
    update("film-v", completed=True)  # already completed: no flip
    update("clip", completed=True)  # not a movie or episode
    assert calls == [("member", "film-v")]


def test_one_request_queues_one_chain_per_member(monkeypatch) -> None:  # noqa: ANN001
    calls = _recorder(monkeypatch)
    session = memory_session_factory()()
    session.add_all([make_user("owner"), make_user("member")])
    add_series(session, "show", "Show", seasons={1: 5})
    session.commit()
    with write_transaction(session, name="mark_watched"):
        TitleService(session).set_watched(session.get(User, "member"), session.get(MediaTitle, "show"), True)
    assert calls == [("member", "show-s1e1-v")]


def test_a_rolled_back_completion_queues_nothing_and_does_not_block_the_retry(monkeypatch) -> None:  # noqa: ANN001
    calls = _recorder(monkeypatch)
    session = memory_session_factory()()
    session.add_all([make_user("owner"), make_user("member")])
    add_movie(session, "film", "Film")
    session.commit()
    member = session.get(User, "member")
    with pytest.raises(RuntimeError), write_transaction(session, name="test_progress"):
        PlaybackProgressService(session).update("film-v", PlaybackProgressUpdateRequest(position_seconds=60, completed=True), member)
        raise RuntimeError("the request fails after the flip")
    assert calls == []
    with write_transaction(session, name="test_progress"):
        PlaybackProgressService(session).update("film-v", PlaybackProgressUpdateRequest(position_seconds=60, completed=True), member)
    assert calls == [("member", "film-v")]


def test_web_and_jellyfin_completions_both_queue_the_chain(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    calls = _recorder(monkeypatch)
    jellyfin_household(tmp_path.resolve() / "media")
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    try:
        for _ in range(2):  # the second report is not a flip
            assert client.post(f"/UserPlayedItems/{jellyfin_id(S1E2)}", json={}, headers=mediabrowser(ALICE_TOKEN)).status_code == 200
        assert calls == [(ALICE, FILE[S1E2])]
        with db_module.session_scope() as db:
            alice = db.get(User, ALICE)
        app.dependency_overrides[get_current_user] = lambda: alice
        assert client.put(f"/api/library/{FILE[S1E1]}/playback", json={"position_seconds": 0, "completed": True}).status_code == 200
        assert calls == [(ALICE, FILE[S1E2]), (ALICE, FILE[S1E1])]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        client.close()


def _transcript(db, item_id: str) -> None:  # noqa: ANN001
    db.add(Transcript(id=str(uuid.uuid4()), library_item_id=item_id, language="en", source_kind="source_caption", revision=1,
                      source_digest=(item_id * 8)[:64], cue_count=1))


def _household(*, disabled: tuple[str, ...] = (), assistant: bool = True) -> None:
    """Show S1 e1–e12 (e4 has no transcript, e10 already summarized), one special, and a film; the assistant is set."""
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = ("http://127.0.0.1:9/v1", "fake-model") if assistant else ("", "")
        record.ai_features_disabled = list(disabled)
        db.add_all([make_user("owner"), make_user("member")])
        add_series(db, "show", "Show", seasons={0: 1, 1: 12})
        add_movie(db, "film", "Film")
        for n in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12):
            _transcript(db, f"show-s1e{n}-v")
        _transcript(db, "film-v")
        add_summary(db, "show-s1e10-v", [])


def _plan(item_id: str):  # noqa: ANN202
    with db_module.session_scope() as db:
        return just_ahead.plan_chain(db, db.get(User, "member"), item_id)


def test_an_episode_chain_is_the_finished_item_then_eight_earlier_nearest_first() -> None:
    _household()
    chain = _plan("show-s1e12-v")
    assert (chain.user_id, chain.key, chain.recap_series_id, chain.model_id) == ("member", "show", "show", "fake-model")
    # e10 is summarized and e4 has no transcript; e1 is past the cap of eight
    assert chain.items == ("show-s1e12-v", "show-s1e11-v", "show-s1e9-v", "show-s1e8-v", "show-s1e7-v", "show-s1e6-v", "show-s1e5-v", "show-s1e3-v", "show-s1e2-v")
    assert _plan("show-s0e1-v").items == ("show-s0e1-v",)  # a special has no earlier episodes
    assert _plan("film-v").items == ("film-v",) and _plan("film-v").recap_series_id is None


@pytest.mark.parametrize(("disabled", "assistant", "episode", "film"), [
    ((recaps.FEATURE_KEY,), True, "summaries only", "chain"),
    ((EPISODE_SUMMARIES,), True, "chain", "chain"),
    ((recaps.FEATURE_KEY, EPISODE_SUMMARIES), True, None, "chain"),
    ((KEY_SCENES,), True, "chain", None),
    ((), False, None, None),
])
def test_switches_and_the_assistant_decide_what_runs(disabled, assistant, episode, film) -> None:  # noqa: ANN001
    _household(disabled=disabled, assistant=assistant)
    chain = _plan("show-s1e12-v")
    if episode is None:
        assert chain is None
    else:
        assert chain.recap_series_id == (None if episode == "summaries only" else "show")
    assert (_plan("film-v") is not None) == (film is not None)


def test_the_chain_summarizes_in_order_then_asks_for_the_next_recap(monkeypatch) -> None:  # noqa: ANN001
    _household()
    with db_module.session_scope() as db:
        for n in range(1, 12):
            add_progress(db, "member", f"show-s1e{n}-v", completed=True, minutes_ago=100 - n)
        for n in (1, 2):
            add_summary(db, f"show-s1e{n}-v", [{"text": f"KP {n}", "cue_ordinals": [1]}])
        transcripts = db.scalar(select(func.count()).select_from(Transcript))
    requested, recapped = [], []

    def request_summary(db, transcript, model_id, user_id):  # noqa: ANN001, ANN202
        requested.append((transcript.library_item_id, model_id, user_id))
        return SimpleNamespace(id=f"job-{len(requested)}"), True

    monkeypatch.setattr(summaries, "request_summary", request_summary)
    monkeypatch.setattr(recaps, "request_recap", lambda db, user, context, model_id: recapped.append((user.id, context.episode.id, model_id)))
    just_ahead._work(just_ahead.Chain("member", "show", ("show-s1e11-v", "show-s1e4-v", "show-s1e3-v"), "show", "fake-model"))
    assert requested == [("show-s1e11-v", "fake-model", "member"), ("show-s1e3-v", "fake-model", "member")]  # e4: no transcript
    assert recapped == [("member", "show-s1e12", "fake-model")]  # the member's play_next after e11
    with db_module.session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Transcript)) == transcripts  # never creates transcripts


def test_a_busy_registry_or_no_room_drops_the_chain(monkeypatch) -> None:  # noqa: ANN001
    _household()
    recapped = []
    monkeypatch.setattr(recaps, "request_recap", lambda *args: recapped.append(args))
    chain = just_ahead.Chain("member", "show", ("show-s1e11-v", "show-s1e9-v"), "show", "fake-model")

    def busy(*args):  # noqa: ANN002, ANN202
        raise summaries.SummaryBusyError()

    monkeypatch.setattr(summaries, "request_summary", busy)
    just_ahead._work(chain)
    requested = []
    monkeypatch.setattr(summaries, "request_summary", lambda *args: requested.append(args) or (SimpleNamespace(id="x"), True))
    monkeypatch.setattr(just_ahead, "MAX_ACTIVE", 0)  # member-requested jobs fill the room
    just_ahead._work(chain)
    assert (requested, recapped) == ([], [])


def test_the_chain_waits_for_each_summary_before_the_next(monkeypatch) -> None:  # noqa: ANN001
    summaries.jobs.running.add("job-slow")
    threading.Timer(0.05, lambda: summaries.jobs.release("job-slow")).start()
    started = time.monotonic()
    just_ahead._wait("job-slow")
    assert time.monotonic() - started >= 0.04
    monkeypatch.setattr(just_ahead, "JOB_WAIT_SECONDS", 0.02)
    summaries.jobs.running.add("job-stuck")
    try:
        started = time.monotonic()
        just_ahead._wait("job-stuck")  # gives up after JOB_WAIT_SECONDS
        assert time.monotonic() - started < 1
    finally:
        summaries.jobs.release("job-stuck")


def test_one_chain_per_member_and_series(monkeypatch) -> None:  # noqa: ANN001
    _household()
    worked = []
    monkeypatch.setattr(just_ahead, "_work", lambda chain: worked.append(chain.items[0]))
    blocking = just_ahead.Chain("member", "show", (), None, "fake-model")
    assert just_ahead._claim(blocking) and not just_ahead._claim(blocking)
    assert just_ahead._claim(just_ahead.Chain("owner", "show", (), None, "fake-model"))
    just_ahead.run("member", "show-s1e12-v")  # the member's show chain is running: dropped
    assert worked == []
    just_ahead._release(blocking)
    just_ahead.run("member", "show-s1e12-v")
    assert worked == ["show-s1e12-v"] and ("member", "show") not in just_ahead._claims


def test_background_work_leaves_an_assistant_slot_for_members(monkeypatch) -> None:  # noqa: ANN001
    _household()
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings().ai_max_concurrency = 3
    requested = []
    monkeypatch.setattr(summaries, "request_summary", lambda *args: requested.append(args) or (SimpleNamespace(id="x"), True))
    chain = just_ahead.Chain("member", "film", ("film-v",), None, "fake-model")
    summaries.jobs.running.update({"member-1", "member-2"})  # two of the assistant's three slots are busy
    try:
        just_ahead._work(chain)
        assert requested == []
        summaries.jobs.release("member-2")
        just_ahead._work(chain)
        assert len(requested) == 1
    finally:
        summaries.jobs.release("member-1")
        summaries.jobs.release("member-2")


def test_no_background_summary_starts_while_a_video_transcode_runs(monkeypatch) -> None:  # noqa: ANN001
    _household()
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings().ai_max_concurrency = 3
    requested = []
    monkeypatch.setattr(summaries, "request_summary", lambda *args: requested.append(args) or (SimpleNamespace(id="x"), True))
    chain = just_ahead.Chain("member", "film", ("film-v",), None, "fake-model")
    monkeypatch.setattr(sessions, "video_encodes", lambda: 1)
    just_ahead._work(chain)
    assert requested == []
    monkeypatch.setattr(sessions, "video_encodes", lambda: 0)
    just_ahead._work(chain)
    assert len(requested) == 1
