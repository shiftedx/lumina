"""Local ASR transcripts through a bounded worker, targeting the admin-configured endpoint."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from app.models import AsrJob, TranscriptCue
from app.services import local_asr
from app.services.yt_dlp_service import YtDlpService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

MODEL = "fake-asr-model"


class FakeAsr:
    """Loopback OpenAI-compatible transcription fake; ``handler`` returns the verbose_json body."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.bodies: list[bytes] = []
        self.auth_headers: list[str | None] = []
        self.status = lambda body: 200  # HTTP status for a request body
        self.handler = lambda: {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hello chunk"}]}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                fake.requests.append(self.path)
                fake.bodies.append(body)
                fake.auth_headers.append(self.headers.get("Authorization"))
                status = fake.status(body)
                raw = json.dumps(fake.handler() if status == 200 else {"error": "rejected"}).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_POST = _serve

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_asr():
    fake = FakeAsr()
    yield fake
    fake.close()


def make_audio(path: Path, seconds: int = 5) -> Path:
    """Tiny synthetic clip from an ffmpeg lavfi sine source; content doesn't matter, the ASR endpoint is faked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)],
        check=True, timeout=30,
    )
    return path


def _configure_asr(url: str, model: str = MODEL) -> None:
    with db_module.SessionLocal() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.asr_base_url, record.asr_model = url, model
        db.commit()


def _settle(client, item_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/library/{item_id}/transcripts/asr").json()
        if body["state"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError("ASR job did not settle")


def test_asr_real_short_fixture(household: None, fake_asr: FakeAsr, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_asr, "CHUNK_SECONDS", 2)  # 5s source -> 3 chunks (2s, 2s, 1s)
    media = make_audio(settings.library_root / "alice" / "speech.wav", seconds=5)
    item_id = _download("alice", media)
    _configure_asr(fake_asr.url)

    with _client("alice") as client:
        started = client.post(f"/api/library/{item_id}/transcripts/asr")
        assert started.status_code == 202
        job = started.json()
        assert job["state"] == "queued" and job["model_id"] == MODEL and job["transcript_id"] is None

        done = _settle(client, item_id)
        assert done["state"] == "succeeded" and done["transcript_id"]
        assert len(fake_asr.requests) == 3  # one bounded upload per chunk

        transcripts = client.get(f"/api/library/{item_id}/transcripts").json()
        assert len(transcripts) == 1 and transcripts[0]["source_kind"] == "asr" and transcripts[0]["model_label"] == MODEL
        cues = client.get(f"/api/transcripts/{done['transcript_id']}/cues").json()["items"]
        assert [cue["text"] for cue in cues] == ["hello chunk"] * 3
        # Each chunk's segment times are offset by its chunk start, not left chunk-relative.
        assert [cue["start_ms"] for cue in cues] == [0, 2000, 4000]
        assert [cue["end_ms"] for cue in cues] == [1000, 3000, 5000]

    # Original media is read-only input to ffmpeg; it is untouched by extraction.
    assert media.read_bytes()


def test_asr_never_sends_the_chat_key_to_the_asr_host(household: None, fake_asr: FakeAsr) -> None:
    """The external ASR endpoint is a separate host from the chat/embedding server; its key never travels here."""
    media = make_audio(settings.library_root / "alice" / "keyed.wav", seconds=1)
    item_id = _download("alice", media)
    with db_module.SessionLocal() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model, record.ai_api_key = "http://127.0.0.1:9", "chat-model", "chat-secret"
        record.asr_base_url, record.asr_model = fake_asr.url, MODEL
        db.commit()

    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/transcripts/asr").status_code == 202
        done = _settle(client, item_id)
    assert done["state"] == "succeeded"
    assert fake_asr.auth_headers == [None]


def test_asr_unconfigured_honest(household: None) -> None:
    media = make_audio(settings.library_root / "alice" / "silent.wav", seconds=1)
    item_id = _download("alice", media)
    # No AppSettings row is created at all: asr_base_url falls back to the ("") env default.

    with _client("alice") as client:
        response = client.post(f"/api/library/{item_id}/transcripts/asr")
    assert response.status_code == 409 and response.json()["detail"] == "asr_not_configured"
    with db_module.SessionLocal() as db:
        assert db.query(AsrJob).count() == 0  # never queued, so the fake endpoint (absent here) is never dialed


def test_asr_permission_and_idempotency(household: None, fake_asr: FakeAsr) -> None:
    media = make_audio(settings.library_root / "alice" / "clip.wav", seconds=1)
    item_id = _download("alice", media)
    _configure_asr(fake_asr.url)

    with _client("bob") as client:
        assert client.post(f"/api/library/{item_id}/transcripts/asr").status_code == 404
        assert client.get(f"/api/library/{item_id}/transcripts/asr").status_code == 404

    with _client("alice") as client:
        first = client.post(f"/api/library/{item_id}/transcripts/asr")
        assert first.status_code == 202
        done = _settle(client, item_id)
        assert done["state"] == "succeeded"
        assert len(fake_asr.requests) == 1

        again = client.post(f"/api/library/{item_id}/transcripts/asr")
        assert again.status_code == 200 and again.json()["id"] == done["id"]
        assert len(fake_asr.requests) == 1  # reused the existing job; no second transcription


def test_asr_cancel_cleans_only_temp(household: None, fake_asr: FakeAsr, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_asr, "CHUNK_SECONDS", 1)  # 3s source -> 3 chunks so cancel can preempt one
    media = make_audio(settings.library_root / "alice" / "long.wav", seconds=3)
    item_id = _download("alice", media)
    _configure_asr(fake_asr.url)

    blocked = threading.Event()
    released = threading.Event()
    real_transcribe = local_asr.transcribe_chunk

    def blocking_transcribe(config, path):
        blocked.set()
        released.wait(timeout=5)
        return real_transcribe(config, path)

    monkeypatch.setattr(local_asr, "transcribe_chunk", blocking_transcribe)

    with _client("alice") as client:
        started = client.post(f"/api/library/{item_id}/transcripts/asr")
        job_id = started.json()["id"]
        assert blocked.wait(timeout=5)  # worker is inside the first chunk's upload

        assert local_asr.cancel(job_id) is True
        released.set()

        done = _settle(client, item_id)
    assert done["state"] == "canceled"
    assert len(fake_asr.requests) == 1  # the loop stopped before a second chunk's upload
    assert not local_asr.work_dir(job_id).exists()  # only this job's temp chunks are gone
    assert media.exists() and media.stat().st_size > 0  # the source original is untouched

    with db_module.SessionLocal() as db:
        assert db.get(AsrJob, job_id).state == "canceled"


def test_asr_extract_kill_on_cancel(tmp_path: Path) -> None:
    """cancel() terminates the ffmpeg (stand-in) child instead of waiting for it to finish."""
    job_id = "kill-me"
    with local_asr.jobs.lock:
        local_asr.jobs.running.add(job_id)
    out_dir = tmp_path / "work"
    outcome: dict[str, bool] = {}

    def run() -> None:
        try:
            local_asr.extract_chunks([sys.executable, "-c", "import time; time.sleep(5)"], out_dir, job_id)
        except local_asr.AsrCanceledError:
            outcome["canceled"] = True

    thread = threading.Thread(target=run)
    started = time.monotonic()
    thread.start()
    time.sleep(0.3)  # let the child process actually start
    assert local_asr.cancel(job_id) is True
    thread.join(timeout=5)

    assert outcome.get("canceled") is True
    assert time.monotonic() - started < 4  # killed well before the 5s sleep would finish on its own
    with local_asr.jobs.lock:
        assert job_id not in local_asr._processes
    local_asr.jobs.running.discard(job_id)
    local_asr.jobs.canceled.discard(job_id)


def test_parse_segments_keeps_word_timings() -> None:
    nested = {"segments": [{"start": 1.0, "end": 2.0, "text": " hi you ", "words": [
        {"word": " hi", "start": 1.0, "end": 1.2}, {"word": "you", "start": 1.3, "end": 1.9}, {"word": "", "start": 1.9, "end": 2.0},
    ]}]}
    assert local_asr.parse_segments(nested, 10_000) == ([(11_000, 12_000, "hi you")], [[[11_000, 11_200, "hi"], [11_300, 11_900, "you"]]])
    loose = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "a"}, {"start": 1.0, "end": 2.0, "text": "b"}],
        "words": [{"word": "a", "start": 0.1, "end": 0.5}, {"word": "b", "start": 1.2, "end": True}, {"word": "c", "start": 1.4, "end": 1.8}],
    }
    assert local_asr.parse_segments(loose, 0)[1] == [[[100, 500, "a"]], [[1400, 1800, "c"]]]
    assert local_asr.parse_segments({"segments": [{"start": 0, "end": 1, "text": "x"}]}, 0)[1] == [None]


def _one_cue(transcript_id: str) -> TranscriptCue:
    with db_module.SessionLocal() as db:
        (cue,) = db.query(TranscriptCue).filter_by(transcript_id=transcript_id).all()
        return cue


def test_asr_stores_word_timestamps(household: None, fake_asr: FakeAsr) -> None:
    fake_asr.handler = lambda: {
        "language": "english",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello there"}],
        "words": [{"word": "hello", "start": 0.0, "end": 0.4}, {"word": "there", "start": 0.5, "end": 0.9}],
    }
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "words.wav", seconds=1))
    _configure_asr(fake_asr.url)
    with _client("alice") as client:
        assert client.post(f"/api/library/{item_id}/transcripts/asr").status_code == 202
        done = _settle(client, item_id)
    assert done["state"] == "succeeded"
    assert b'name="timestamp_granularities[]"' in fake_asr.bodies[0]
    assert _one_cue(done["transcript_id"]).words == [[0, 400, "hello"], [500, 900, "there"]]


def test_asr_retries_once_without_word_timestamps_on_400(household: None, fake_asr: FakeAsr) -> None:
    fake_asr.status = lambda body: 400 if b"timestamp_granularities" in body else 200
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "old-server.wav", seconds=1))
    _configure_asr(fake_asr.url)
    with _client("alice") as client:
        client.post(f"/api/library/{item_id}/transcripts/asr")
        done = _settle(client, item_id)
    assert done["state"] == "succeeded" and len(fake_asr.requests) == 2
    assert b"timestamp_granularities" in fake_asr.bodies[0] and b"timestamp_granularities" not in fake_asr.bodies[1]
    assert _one_cue(done["transcript_id"]).words is None


def test_asr_other_errors_are_not_retried(household: None, fake_asr: FakeAsr) -> None:
    fake_asr.status = lambda body: 500
    item_id = _download("alice", make_audio(settings.library_root / "alice" / "broken.wav", seconds=1))
    _configure_asr(fake_asr.url)
    with _client("alice") as client:
        client.post(f"/api/library/{item_id}/transcripts/asr")
        done = _settle(client, item_id)
    assert (done["state"], done["error"], len(fake_asr.requests)) == ("failed", "ASR endpoint returned HTTP 500", 1)
