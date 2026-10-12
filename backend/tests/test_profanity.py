"""Mute ranges (word timings, cue interpolation, YouTube's [ __ ]) and caption masking driven by the pref."""
from __future__ import annotations

import uuid

import pytest

from app import db as db_module
from app.config import settings
from app.models import User, UserSettings
from app.services import profanity
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401

FILTER = profanity.build_filter(["Heck", "darn*"])


def test_word_timings_are_padded() -> None:
    cue = (1000, 2000, "oh fuck", [[1000, 1300, "oh"], [1400, 1800, "Fuck!"]])
    assert profanity.mute_ranges([cue], FILTER) == [(1300, 1900)]


def test_without_words_the_window_is_interpolated_and_clamped_to_the_cue() -> None:
    assert profanity.mute_ranges([(10_000, 12_000, "what the [ __ ] is that", None)], FILTER) == [(10_643, 11_443)]
    assert profanity.mute_ranges([(0, 1000, "heck darnit shitty", None)], FILTER) == [(0, 1000)]  # merged, clamped
    assert profanity.mute_ranges([(0, 1000, "hello darling", None)], profanity.build_filter(["dar"])) == []  # no wildcard, no prefix match


def test_masking_keeps_cue_structure() -> None:
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nWell [ __ ] it, Heck no\n\n00:00:03.000 --> 00:00:04.000\nshitty day\n"
    assert profanity.mask_vtt(vtt, FILTER) == (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nWell **** it, **** no\n\n00:00:03.000 --> 00:00:04.000\n**** day\n"
    )


def _pref(user_id: str, value: object) -> None:
    with db_module.SessionLocal() as db:
        record = db.query(UserSettings).filter_by(user_id=user_id).one_or_none()
        if record is None:
            record = UserSettings(id=str(uuid.uuid4()), user_id=user_id, ui_prefs={})
            db.add(record)
        record.ui_prefs = {**(record.ui_prefs or {}), "profanity": value}
        db.commit()


def test_mute_route_follows_the_pref_and_prefers_word_timings(household: None) -> None:
    item_id = _download("alice", settings.library_root / "alice" / "show.mkv")
    with db_module.SessionLocal() as db:
        service = TranscriptService(db)
        service.store(item_id, language="en", source_kind="source_caption", cues=[(0, 2000, "damn it all")])
        service.store(item_id, language="en", source_kind="asr", cues=[(5000, 6000, "oh shit")], words=[[[5000, 5300, "oh"], [5400, 5800, "shit"]]])
    url = f"/api/library/{item_id}/mute-ranges"
    with _client("alice") as client:
        assert client.get(url).json() == []  # off by default
        _pref("alice", {"enabled": True, "words": []})
        assert client.get(url).json() == [{"start_seconds": 5.3, "end_seconds": 5.9}]
        _pref("alice", {"enabled": True, "words": ["x" * 41]})  # malformed pref: treated as off, never a 500
        assert client.get(url).json() == []
    with _client("bob") as client:
        assert client.get(url).status_code == 404


def test_masking_follows_the_members_pref(household: None) -> None:
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nshit happens\n"
    with db_module.SessionLocal() as db:
        alice = db.get(User, "alice")
        assert profanity.member_filter(db, alice) is None  # off by default: nothing to mask with
    _pref("alice", {"enabled": True, "words": []})
    with db_module.SessionLocal() as db:
        word_filter = profanity.member_filter(db, db.get(User, "alice"))
        assert "**** happens" in profanity.mask_vtt(vtt, word_filter)


def test_disabled_ai_feature_kills_the_mute_pref(household: None) -> None:
    """Owner-approved kill switch (ADR 0013): ``mute_strong_language`` gates member_filter, same entry point as the job kinds."""
    item_id = _download("alice", settings.library_root / "alice" / "show.mkv")
    with db_module.SessionLocal() as db:
        TranscriptService(db).store(item_id, language="en", source_kind="source_caption", cues=[(0, 2000, "damn it all")])
    _pref("alice", {"enabled": True, "words": []})
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["mute_strong_language"]
        db.commit()
    with _client("alice") as client:
        assert client.get(f"/api/library/{item_id}/mute-ranges").json() == []
    with db_module.SessionLocal() as db:
        assert profanity.member_filter(db, db.get(User, "alice")) is None


@pytest.mark.parametrize("word", ["fuck", "Motherfucker", "SHITTY", "[ __ ]"])
def test_default_words_match(word: str) -> None:
    assert profanity.build_filter([]).matches(word)
