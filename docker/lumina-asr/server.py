#!/usr/bin/env python3
"""Lumina's on-device speech server.

It runs under /opt/lumina-asr, its own virtual environment, so faster-whisper's dependencies never
touch Lumina's. It serves ONE model on 127.0.0.1 behind a per-start bearer secret, passed in the
environment as LUMINA_ASR_SECRET:

- ``GET /health``: 200 once listening (the model loads before the socket opens).
- ``POST /v1/audio/transcriptions``: multipart ``file`` (+ optional ``language``), answered with
  OpenAI ``verbose_json`` including top-level ``words[]``.

Decoding is sequential: one request at a time (a single-threaded HTTPServer), one segment at a time
(never the batched pipeline), beam size 1, voice-activity filtering on. The 2026-09-26 benchmark
found that batching cost 3.1 GiB and ran out of memory with the larger model. Requests, audio and
text are never logged. Only the stdlib is imported at module level; faster-whisper loads in main().
"""
from __future__ import annotations

import argparse
import email.parser
import email.policy
import hmac
import json
import os
import sys
import tempfile
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # a 10-minute 16 kHz mono WAV chunk is about 19 MB
TRANSCRIPTION_PATH = "/v1/audio/transcriptions"

Transcriber = Callable[[str, str | None], dict[str, Any]]


def verbose_json(segments: Any, info: Any) -> dict[str, Any]:
    """OpenAI verbose_json from faster-whisper's lazy segments (iterating them runs the decode)."""
    out_segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        out_segments.append({"id": index, "start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text.strip()})
        for word in segment.words or ():
            words.append({
                "word": word.word.strip(), "start": round(word.start, 3), "end": round(word.end, 3),
                "probability": round(word.probability, 4),
            })
    return {
        "task": "transcribe", "language": info.language, "duration": round(info.duration, 3),
        "text": " ".join(segment["text"] for segment in out_segments if segment["text"]),
        "segments": out_segments, "words": words,
    }


def parse_form(content_type: str, body: bytes) -> dict[str, bytes]:
    """multipart/form-data fields by name, first occurrence wins (stdlib email parser; no cgi module)."""
    head = b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n"
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    fields: dict[str, bytes] = {}
    if not message.is_multipart():
        return fields
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if isinstance(name, str) and name not in fields:
            fields[name] = part.get_payload(decode=True) or b""
    return fields


def make_handler(secret: str, transcribe: Transcriber) -> type[BaseHTTPRequestHandler]:
    expected = f"Bearer {secret}".encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._reply(200, {"status": "ok"})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != TRANSCRIPTION_PATH:
                self.close_connection = True
                self._reply(404, {"error": "not found"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected):
                self.close_connection = True
                self._reply(401, {"error": "unauthorized"})
                return
            length = self.headers.get("Content-Length", "")
            if not length.isdigit():
                self.close_connection = True
                self._reply(411, {"error": "length required"})
                return
            if int(length) > MAX_UPLOAD_BYTES:
                self.close_connection = True
                self._reply(413, {"error": "upload too large"})
                return
            fields = parse_form(self.headers.get("Content-Type", ""), self.rfile.read(int(length)))
            audio = fields.get("file")
            if not audio:
                self._reply(400, {"error": "missing file"})
                return
            language = (fields.get("language") or b"").decode("ascii", "ignore").strip() or None
            with tempfile.NamedTemporaryFile(suffix=".audio") as handle:
                handle.write(audio)
                handle.flush()
                try:
                    result = transcribe(handle.name, language)
                except Exception as exc:  # noqa: BLE001 - the type only; never audio or model text
                    self._reply(500, {"error": type(exc).__name__})
                    return
            self._reply(200, result)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lumina on-device speech server")
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--language", default=None)
    args = parser.parse_args(argv)
    secret = os.environ.get("LUMINA_ASR_SECRET", "")
    if len(secret) < 32:
        sys.exit("LUMINA_ASR_SECRET must be set by Lumina")

    from faster_whisper import WhisperModel  # the isolated venv's dependency

    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads, num_workers=1, local_files_only=True)

    def transcribe(path: str, language: str | None) -> dict[str, Any]:
        segments, info = model.transcribe(
            path, language=language or args.language, beam_size=1, vad_filter=True, word_timestamps=True,
        )
        return verbose_json(segments, info)

    HTTPServer(("127.0.0.1", args.port), make_handler(secret, transcribe)).serve_forever()


if __name__ == "__main__":
    main()
