#!/usr/bin/env python3
"""Real-stack smoke for on-device models (Testing → Real stack).

Runs INSIDE the built image (``make models-smoke``). Downloads two tiny pinned test models through Lumina's
own downloader (sha256-verified, from huggingface.co), starts the real llama-server and the isolated speech
server through the supervisor, embeds two texts and transcribes a 3-second synthetic clip. Uses a throwaway
data directory; never point it at real app-data.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="lumina-models-smoke-"))
os.environ["LUMINA_DATA_DIR"] = str(DATA)  # before any app import: settings are read at import time
sys.path.insert(0, "/app/backend")

from app.services import local_asr, model_catalog, model_downloads, model_endpoints, model_supervisor  # noqa: E402
from app.services.local_ai import AiConfig, embed  # noqa: E402

CATALOG = Path(__file__).with_name("models_smoke_catalog.json")
DOWNLOAD_TIMEOUT_SECONDS = 900


def image_contract() -> None:
    """Development and installer payloads do not ship in the production image."""
    assert not Path("/app/backend/tests").exists(), "backend tests reached the runtime image"
    assert not list(Path("/opt/lumina-asr/bin").glob("pip*")), "ASR venv still has a pip executable"
    result = subprocess.run(
        [
            "/opt/lumina-asr/bin/python", "-c",
            "import importlib.util; raise SystemExit(importlib.util.find_spec('pip') is not None)",
        ],
        check=False,
    )
    assert result.returncode == 0, "ASR venv still contains the pip installer"


def main() -> None:
    image_contract()
    model_catalog.CATALOG_PATH = CATALOG
    catalog = model_catalog.catalog()
    manager = model_downloads.manager
    for model in catalog.models:
        manager.start(model)
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    while any(manager.status(model).state in ("downloading", "verifying") for model in catalog.models):
        if time.monotonic() > deadline:
            sys.exit("models smoke: downloads did not finish")
        time.sleep(1)
    for model in catalog.models:
        status = manager.status(model)
        if status.state != "ready":
            sys.exit(f"models smoke: {model.id} is {status.state}: {status.reason}")
    print(f"models smoke: downloaded and verified {', '.join(model.id for model in catalog.models)}")

    threads = model_supervisor.effective_threads(None)
    base = AiConfig(ai_base_url="", ai_model="", ai_api_key=None, ai_max_concurrency=2, ai_context_tokens=4096, asr_base_url="", asr_model="")
    search, speech = catalog.default_for("search"), catalog.default_for("speech")
    try:
        started = time.monotonic()
        with model_endpoints.connect(model_endpoints.Choice("local", search.choice_id, base, search, threads), wait=True, heavy=True) as config:
            first = time.monotonic()
            vectors = embed(config, ["a film about the undead", "a sunny picnic"])
            timed = time.monotonic()
            embed(config, ["one short query"])
            query_ms = (time.monotonic() - timed) * 1000
        assert len(vectors) == 2 and all(len(vector) == search.engine["dimensions"] for vector in vectors), "bad embeddings"
        print(f"models smoke: llama-server up in {first - started:.1f}s, {len(vectors[0])}-d vectors, one query {query_ms:.0f} ms")

        clip = DATA / "clip.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "1", "-ar", "16000", str(clip)],
            check=True, timeout=60,
        )
        started = time.monotonic()
        with model_endpoints.connect(model_endpoints.Choice("local", speech.choice_id, base, speech, threads), wait=True, heavy=True) as config:
            body = local_asr.transcribe_chunk(config, clip)
        assert isinstance(body.get("segments"), list) and isinstance(body.get("words"), list), "not verbose_json"
        assert abs(float(body["duration"]) - 3.0) < 0.25, "wrong duration"
        print(f"models smoke: speech server answered verbose_json in {time.monotonic() - started:.1f}s (incl. start)")
    finally:
        model_supervisor.supervisor.stop_all()
    print("models smoke: OK")


if __name__ == "__main__":
    main()
