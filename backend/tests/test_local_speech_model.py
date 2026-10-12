"""Subtitles from speech on the on-device model, never beside a backfill batch."""
from __future__ import annotations

import shutil
import time

import pytest

from app import db as db_module
from app.config import settings
from app.services import model_supervisor
from app.services.yt_dlp_service import YtDlpService
from model_support import default_pair, fresh_models, install, records, stub_runtime, use_catalog  # noqa: F401
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401
from tests.test_v1_asr import FakeAsr, _configure_asr, _one_cue, _settle, make_audio

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def local_speech(monkeypatch, tmp_path, fresh_models):  # noqa: ANN001, F811
    record = stub_runtime(monkeypatch, tmp_path)
    catalog = use_catalog(monkeypatch, tmp_path, default_pair())
    return catalog.default_for("speech"), record


def test_a_generated_subtitle_track_without_any_asr_server(household, local_speech) -> None:  # noqa: ANN001, F811
    speech, _record = local_speech
    install(speech)
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "speech.wav", seconds=1))
    with _client("alice") as client:
        assert client.get("/api/enrichment").json()["asr"] is True
        started = client.post(f"/api/library/{item_id}/transcripts/asr")
        assert started.status_code == 202 and started.json()["model_id"] == "local:test-speech"
        done = _settle(client, item_id)
        assert done["state"] == "succeeded"
        cues = client.get(f"/api/transcripts/{done['transcript_id']}/cues").json()["items"]
    assert [cue["text"] for cue in cues] == ["hello"]
    assert _one_cue(done["transcript_id"]).words == [[100, 500, "hello"]]  # the server's top-level words[]


def test_no_model_and_no_server_stays_unavailable(household, local_speech) -> None:  # noqa: ANN001, F811
    _speech, record = local_speech
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "quiet.wav", seconds=1))
    with _client("alice") as client:
        assert client.get("/api/enrichment").json()["asr"] is False
        response = client.post(f"/api/library/{item_id}/transcripts/asr")
    assert response.status_code == 409 and response.json()["detail"] == "asr_not_configured"
    assert records(record) == []


def test_the_local_model_wins_over_the_external_server(household, local_speech) -> None:  # noqa: ANN001, F811
    speech, _record = local_speech
    install(speech)
    fake = FakeAsr()
    try:
        _configure_asr(fake.url)
        item_id = _download("alice", make_audio(settings.library_root / "alice" / "both.wav", seconds=1))
        with _client("alice") as client:
            assert client.post(f"/api/library/{item_id}/transcripts/asr").json()["model_id"] == "local:test-speech"
            assert _settle(client, item_id)["state"] == "succeeded"
        assert fake.requests == []
    finally:
        fake.close()


def test_the_kill_switch_never_starts_the_speech_server(household, local_speech) -> None:  # noqa: ANN001, F811
    speech, record = local_speech
    install(speech)
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["subtitles_from_speech"]
        db.commit()
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "off.wav", seconds=1))
    with _client("alice") as client:
        response = client.post(f"/api/library/{item_id}/transcripts/asr")
    assert response.status_code == 409 and response.json()["detail"] == "ai_feature_disabled"
    assert records(record) == []


def test_transcription_waits_while_a_backfill_batch_runs(household, local_speech) -> None:  # noqa: ANN001, F811
    speech, record = local_speech
    install(speech)
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "wait.wav", seconds=1))
    heavy = model_supervisor.supervisor.heavy
    heavy.acquire()  # a local backfill batch holds the bulk-work lock
    try:
        with _client("alice") as client:
            client.post(f"/api/library/{item_id}/transcripts/asr")
            time.sleep(0.3)
            assert client.get(f"/api/library/{item_id}/transcripts/asr").json()["state"] in {"queued", "running"}
            assert records(record) == []  # the speech server is not even started while the batch runs
    finally:
        heavy.release()
    with _client("alice") as client:
        assert _settle(client, item_id)["state"] == "succeeded"


def test_a_job_whose_local_model_was_removed_fails_honestly(household, local_speech, monkeypatch) -> None:  # noqa: ANN001, F811
    from app.services import local_asr

    speech, _record = local_speech
    install(speech)
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "gone.wav", seconds=1))
    real = local_asr.transcribe

    def remove_then_run(job_id: str, item: str, model_id: str) -> str:
        shutil.rmtree(settings.data_dir / "models" / speech.id)  # removed after the job was queued
        return real(job_id, item, model_id)

    monkeypatch.setattr(local_asr, "transcribe", remove_then_run)
    with _client("alice") as client:
        client.post(f"/api/library/{item_id}/transcripts/asr")
        done = _settle(client, item_id)
    assert (done["state"], done["error"]) == ("failed", "The speech model is not installed")
