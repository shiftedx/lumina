""""Previously on" recaps."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.models import AppSettings, SeriesRecap, User
from app.security import get_current_user
from app.services import recaps
from app.services.local_ai import AiConfig
from app.services.summaries import SummaryError
from app.services.yt_dlp_service import YtDlpService
from discovery_support import add_progress, add_series, add_summary, add_version
from support import make_user, memory_session_factory

NOW = datetime(2026, 9, 25)
ENABLED = AiConfig("http://127.0.0.1:9/v1", "fake-model", None, 2, 145_000, "", "")
DISABLED = AiConfig("", "", None, 2, 145_000, "", "")


@pytest.mark.parametrize(
    ("last_played", "first_episode", "expected"),
    [
        (None, False, False),
        (NOW - timedelta(days=1), False, False),
        (NOW - timedelta(days=14), False, False),
        (NOW - timedelta(days=14, seconds=1), False, True),
        (NOW - timedelta(days=90), True, False),
    ],
)
def test_should_preroll_gap_table(last_played, first_episode, expected) -> None:  # noqa: ANN001
    assert recaps.should_preroll(last_played, NOW, first_episode=first_episode) is expected


def _show(session, *, private_episode: int | None = None) -> None:
    """Season 1 has five episodes and there is one special; every version has a summary naming its episode."""
    session.add_all([make_user("owner"), make_user("member")])
    add_series(session, "show", "Show", seasons={0: 1, 1: 5})
    for season, count in ((0, 1), (1, 5)):
        for number in range(1, count + 1):
            add_summary(session, f"show-s{season}e{number}-v", [{"text": f"KP {season}x{number}", "cue_ordinals": [3, 7], "start_ms": 3000}])
    if private_episode is not None:
        episode = f"show-s1e{private_episode}"
        add_version(session, f"{episode}-private", episode, owner="owner", visibility="private", kind="episode")
        add_summary(session, f"{episode}-private", [{"text": "Owner-only cut", "cue_ordinals": [1]}], minutes_ago=-5)
    session.commit()


def test_prompt_never_contains_the_target_later_episodes_or_specials(monkeypatch) -> None:
    session = memory_session_factory()()
    _show(session)
    context = recaps.load_context(session, session.get(User, "member"), "show-s1e3")
    sent: list[list[dict]] = []

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001
        sent.append(messages)
        return json.dumps({"points": [{"text": "They met.", "citations": [{"episode": "E1", "cue_ordinals": [3]}]}]})

    monkeypatch.setattr(recaps, "chat", fake_chat)

    result = recaps.generate(ENABLED, context.inputs)

    system, user = sent[0][0]["content"], sent[0][1]["content"]
    assert "KP 1x1" in user and "KP 1x2" in user
    assert not any(f"KP {episode}" in user for episode in ("1x3", "1x4", "1x5", "0x1"))
    assert "KP" not in system and user.startswith("<evidence>") and user.endswith("</evidence>")
    assert result == {"points": [{"text": "They met.", "citations": [{"episode_id": "show-s1e1", "cue_ordinal": 3, "start_ms": 3000}]}]}


def test_ungrounded_points_are_dropped_and_none_grounded_fails() -> None:
    session = memory_session_factory()()
    _show(session)
    inputs = recaps.load_context(session, session.get(User, "member"), "show-s1e3").inputs
    raw = {"points": [
        {"text": "Grounded", "citations": [{"episode": "E2", "cue_ordinals": [7, 7, 99]}]},
        {"text": "Wrong cue", "citations": [{"episode": "E1", "cue_ordinals": [5]}]},
        {"text": "Unknown episode", "citations": [{"episode": "E9", "cue_ordinals": [3]}]},
        {"text": "Target by id", "citations": [{"episode": "show-s1e3", "cue_ordinals": [3]}]},
        {"text": "Uncited"},
        {"text": " ", "citations": [{"episode": "E1", "cue_ordinals": [3]}]},
    ]}

    assert recaps.validate_recap(raw, inputs) == [
        {"text": "Grounded", "citations": [{"episode_id": "show-s1e2", "cue_ordinal": 7, "start_ms": None}]},
    ]
    for bad in (None, "text", {"points": [{"text": "x", "citations": [{"episode": ["E1"], "cue_ordinals": [3]}]}]}):
        with pytest.raises(SummaryError):
            recaps.validate_recap(bad, inputs)


def test_digest_separates_visibility() -> None:
    session = memory_session_factory()()
    _show(session, private_episode=2)
    owner, member = session.get(User, "owner"), session.get(User, "member")
    owner_context = recaps.load_context(session, owner, "show-s1e3")
    member_context = recaps.load_context(session, member, "show-s1e3")

    assert owner_context.digest != member_context.digest
    assert "show-s1e2-private" in [item.item_id for item in owner_context.inputs]
    assert "show-s1e2-private" not in [item.item_id for item in member_context.inputs]
    session.add(SeriesRecap(
        id="r1", series_id="show", episode_title_id="show-s1e3", model_id="fake-model",
        inputs_digest=owner_context.digest, input_item_ids=[item.item_id for item in owner_context.inputs],
        state="succeeded", points=[{"text": "Owner-only recap", "citations": [{"episode_id": "show-s1e2", "cue_ordinal": 1}]}],
    ))
    session.commit()

    assert recaps.recap_response(session, member, member_context, ENABLED, now=NOW).state == "none"
    assert recaps.recap_response(session, owner, owner_context, ENABLED, now=NOW).points[0].text == "Owner-only recap"


def test_no_ai_fallback_shows_previous_overviews_and_suggests_preroll(monkeypatch) -> None:
    session = memory_session_factory()()
    _show(session)
    member = session.get(User, "member")
    add_progress(session, "member", "show-s1e2-v", completed=True, at=NOW - timedelta(days=30))
    session.commit()
    monkeypatch.setattr(recaps, "chat", lambda *args, **kwargs: pytest.fail("no model call without AI"))
    context = recaps.load_context(session, member, "show-s1e3")

    response = recaps.recap_response(session, member, context, DISABLED, now=NOW)

    assert response.state == "fallback" and response.points == []
    assert [(episode.episode_id, episode.overview) for episode in response.fallback] == [
        ("show-s1e2", "Overview of 1x2"), ("show-s1e1", "Overview of 1x1"),
    ]
    assert response.suggest_preroll is True


def test_special_and_first_episode_fall_back() -> None:
    session = memory_session_factory()()
    _show(session)
    member = session.get(User, "member")
    add_progress(session, "member", "show-s1e1-v", at=NOW - timedelta(days=30))
    session.commit()

    for episode_id in ("show-s0e1", "show-s1e1"):
        context = recaps.load_context(session, member, episode_id)
        assert context.inputs == ()
        response = recaps.recap_response(session, member, context, ENABLED, now=NOW)
        assert (response.state, response.fallback, response.suggest_preroll) == ("fallback", [], False)
    assert recaps.load_context(session, member, "show-s1") is None  # a season is not an episode


def test_post_generates_once_then_serves_the_cache(monkeypatch) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "fake-model"
        _show(db)
    calls: list[str] = []

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001
        calls.append(config.ai_model)
        return json.dumps({"points": [{"text": "Previously…", "citations": [{"episode": "E2", "cue_ordinals": [3]}]}]})

    monkeypatch.setattr(recaps, "chat", fake_chat)
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    try:
        http = TestClient(app, base_url="http://localhost")
        assert http.get("/api/titles/show-s1e3/recap").json()["state"] == "none"
        assert http.post("/api/titles/show-s1e3/recap").status_code == 202
        deadline = time.monotonic() + 10
        while (body := http.get("/api/titles/show-s1e3/recap").json())["state"] in ("queued", "running"):
            assert time.monotonic() < deadline, "recap did not settle"
            time.sleep(0.02)
        assert body["state"] == "succeeded"
        assert body["points"][0]["citations"][0]["episode_id"] == "show-s1e2"
        assert http.post("/api/titles/show-s1e3/recap").status_code == 200
        assert calls == ["fake-model"]
        assert http.get("/api/titles/show-s1e1-v/recap").status_code == 404
        with db_module.session_scope() as db:
            db.get(AppSettings, 1).ai_base_url = ""
        assert http.post("/api/titles/show-s1e3/recap").status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_recap_route_is_404_for_an_episode_the_caller_cannot_see(monkeypatch) -> None:
    """The HTTP route never answers for another member's private episode."""
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "fake-model"
        db.add_all([make_user("owner"), make_user("member")])
        add_series(db, "secret", "Secret", seasons={1: 2}, visibility="private")
    monkeypatch.setattr(recaps, "chat", lambda *args, **kwargs: pytest.fail("no model call for an invisible episode"))
    from app.main import app

    try:
        http = TestClient(app, base_url="http://localhost")
        app.dependency_overrides[get_current_user] = lambda: User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
        assert http.get("/api/titles/secret-s1e2/recap").status_code == 404
        assert http.post("/api/titles/secret-s1e2/recap").status_code == 404
        app.dependency_overrides[get_current_user] = lambda: User(id="owner", username="owner", display_name="Owner", role="viewer", is_active=True)
        assert http.get("/api/titles/secret-s1e2/recap").status_code == 200  # the owner sees it, so the 404 is visibility
    finally:
        app.dependency_overrides.clear()


def test_recap_kill_switch_falls_back_and_refuses_generation(monkeypatch) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "fake-model"
        record.ai_features_disabled = [recaps.FEATURE_KEY]
        _show(db)
    monkeypatch.setattr(recaps, "chat", lambda *args, **kwargs: pytest.fail("no model call when recaps are switched off"))
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id="member", username="member", display_name="Member", role="viewer", is_active=True)
    try:
        http = TestClient(app, base_url="http://localhost")
        assert http.get("/api/titles/show-s1e3/recap").json()["state"] == "fallback"
        assert http.post("/api/titles/show-s1e3/recap").status_code == 409
    finally:
        app.dependency_overrides.clear()


def _recap_db() -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "fake-model"
        _show(db)


def test_concurrent_first_request_returns_the_winners_row(monkeypatch) -> None:
    """Two first POSTs both miss the cache; the loser's insert hits the unique key and reuses the winner's row."""
    _recap_db()
    real_cached, misses = recaps.cached, []

    def racing_cached(db, context, model_id):  # noqa: ANN001
        if not misses:  # the other request commits between this request's cache miss and its insert
            misses.append(1)
            with db_module.session_scope() as other:
                other.add(SeriesRecap(
                    id="winner", series_id="show", episode_title_id=context.episode.id, model_id=model_id,
                    inputs_digest=context.digest, input_item_ids=[], state="queued", points=[],
                ))
            return None
        return real_cached(db, context, model_id)

    monkeypatch.setattr(recaps, "cached", racing_cached)
    monkeypatch.setattr(recaps, "run_recap", lambda *args, **kwargs: pytest.fail("the loser starts no worker"))
    with db_module.session_scope() as db:
        member = db.get(User, "member")
        context = recaps.load_context(db, member, "show-s1e3")
        recap, created = recaps.request_recap(db, member, context, "fake-model")

        assert (recap.id, created) == ("winner", False)
    assert not recaps.jobs.running


def test_recap_job_reads_the_summaries_its_digest_keyed(monkeypatch) -> None:
    """A summary that finishes between request and run never changes the recap input."""
    _recap_db()
    targets: list = []

    def deferred_start(db, make_row, target, name):  # noqa: ANN001
        row = make_row("job-1")
        db.add(row)
        db.commit()
        targets.append(target)
        return row

    monkeypatch.setattr(recaps, "start_job", deferred_start)
    sent: list[str] = []

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001
        sent.append(messages[1]["content"])
        return json.dumps({"points": [{"text": "Previously…", "citations": [{"episode": "E2", "cue_ordinals": [3]}]}]})

    monkeypatch.setattr(recaps, "chat", fake_chat)
    with db_module.session_scope() as db:
        member = db.get(User, "member")
        context = recaps.load_context(db, member, "show-s1e3")
        recaps.request_recap(db, member, context, "fake-model")
    with db_module.session_scope() as db:
        add_summary(db, "show-s1e2-v", [{"text": "Newer KP", "cue_ordinals": [3]}], minutes_ago=-60)

    targets[0]("job-1")

    with db_module.session_scope() as db:
        assert db.get(SeriesRecap, "job-1").state == "succeeded"
    assert "KP 1x2" in sent[0] and "Newer KP" not in sent[0]
