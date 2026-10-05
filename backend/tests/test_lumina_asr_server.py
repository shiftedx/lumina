"""The Lumina-owned speech server (loaded from docker/lumina-asr, without faster-whisper)."""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
import types
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SECRET = "s" * 43


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("lumina_asr_server", ROOT / "docker" / "lumina-asr" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


def _segment(start: float, end: float, text: str, words: list[tuple[str, float, float]]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        start=start, end=end, text=text,
        words=[types.SimpleNamespace(word=w, start=s, end=e, probability=0.91234) for w, s, e in words],
    )


def test_verbose_json_has_top_level_words() -> None:
    info = types.SimpleNamespace(language="en", duration=3.0004)
    body = server.verbose_json(iter([
        _segment(0.0, 1.2, " Hello there.", [(" Hello", 0.1, 0.5), (" there.", 0.6, 1.1)]),
        _segment(1.5, 2.0, " ", []),
    ]), info)
    assert body == {
        "task": "transcribe", "language": "en", "duration": 3.0, "text": "Hello there.",
        "segments": [{"id": 0, "start": 0.0, "end": 1.2, "text": "Hello there."}, {"id": 1, "start": 1.5, "end": 2.0, "text": ""}],
        "words": [
            {"word": "Hello", "start": 0.1, "end": 0.5, "probability": 0.9123},
            {"word": "there.", "start": 0.6, "end": 1.1, "probability": 0.9123},
        ],
    }


@pytest.fixture
def running():
    calls: list[tuple[bytes, str | None]] = []
    active = {"now": 0, "max": 0}

    def transcribe(path: str, language: str | None) -> dict:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        calls.append((Path(path).read_bytes(), language))
        active["now"] -= 1
        return {"task": "transcribe", "language": language or "en", "duration": 1.0, "text": "hi", "segments": [], "words": []}

    httpd = HTTPServer(("127.0.0.1", 0), server.make_handler(SECRET, transcribe))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", calls, active
    httpd.shutdown()
    httpd.server_close()


def _post(url: str, secret: str | None = SECRET, **form) -> httpx.Response:  # noqa: ANN003
    headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
    data = {"model": "ignored", "response_format": "verbose_json", **form}
    return httpx.post(url + server.TRANSCRIPTION_PATH, headers=headers, data=data, files={"file": ("chunk.wav", b"RIFFfake", "audio/wav")}, timeout=5)


def test_transcribes_with_the_secret_only(running) -> None:  # noqa: ANN001
    url, calls, _active = running
    assert httpx.get(url + "/health", timeout=5).json() == {"status": "ok"}
    assert _post(url, secret=None).status_code == 401
    assert _post(url, secret="wrong").status_code == 401
    assert calls == []
    reply = _post(url, language="de")
    assert reply.status_code == 200 and reply.json()["language"] == "de"
    assert calls == [(b"RIFFfake", "de")]
    assert _post(url).json()["language"] == "en"  # no language: the model default


def test_uploads_are_bounded(running, monkeypatch) -> None:  # noqa: ANN001
    url, calls, _active = running
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 16)
    assert _post(url).status_code == 413
    assert calls == []
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 64 << 20)
    no_file = httpx.post(url + server.TRANSCRIPTION_PATH, headers={"Authorization": f"Bearer {SECRET}"}, data={"x": "1"}, timeout=5)
    assert no_file.status_code == 400
    assert httpx.post(url + "/v1/other", headers={"Authorization": f"Bearer {SECRET}"}, timeout=5).status_code == 404


def test_requests_are_decoded_one_at_a_time(running) -> None:  # noqa: ANN001
    url, calls, active = running
    threads = [threading.Thread(target=_post, args=(url,)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 3 and active["max"] == 1


def test_main_binds_loopback_loads_offline_and_requires_a_secret(monkeypatch) -> None:  # noqa: ANN001
    seen: dict = {}

    class FakeModel:
        def __init__(self, path, **kwargs) -> None:  # noqa: ANN001, ANN003
            seen["model"] = (path, kwargs)

    class FakeServer:
        def __init__(self, address, handler) -> None:  # noqa: ANN001
            seen["address"] = address

        def serve_forever(self) -> None:
            seen["served"] = True

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(server, "HTTPServer", FakeServer)
    monkeypatch.delenv("LUMINA_ASR_SECRET", raising=False)
    with pytest.raises(SystemExit):
        server.main(["--model", "/m", "--port", "5000"])
    monkeypatch.setenv("LUMINA_ASR_SECRET", SECRET)
    server.main(["--model", "/m", "--port", "5000", "--threads", "3"])
    assert seen["address"] == ("127.0.0.1", 5000) and seen["served"]
    path, kwargs = seen["model"]
    assert path == "/m" and kwargs == {"device": "cpu", "compute_type": "int8", "cpu_threads": 3, "num_workers": 1, "local_files_only": True}


def test_the_speech_lock_is_hash_pinned() -> None:
    lock = (ROOT / "docker" / "lumina-asr" / "requirements.lock").read_text()
    assert "faster-whisper==1.2.1" in lock and re.search(r"^ctranslate2==4\.", lock, re.M)
    assert "--python-version 3.12 " in lock.splitlines()[1]
    requirements = [line for line in lock.splitlines() if re.match(r"^[a-z0-9]", line)]
    assert requirements and all(line.endswith("\\") for line in requirements)  # every pin carries --hash lines
    assert "BatchedInferencePipeline" not in (ROOT / "docker" / "lumina-asr" / "server.py").read_text()
