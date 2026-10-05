"""Stand-in for llama-server and the speech server in tests: loads nothing, answers like both.

Reads ``--port`` from argv and its secret from LLAMA_ARG_API_KEY or LUMINA_ASR_SECRET. Serves /health,
/v1/embeddings (a 2-d vector: [1, 0] for undead/zombie texts, else [0, 1]) and /v1/audio/transcriptions
(one "hello" segment). STUB_CRASH=1 exits before listening. STUB_RECORD=<file> appends one JSON line per
start ({"argv", "env_names", "pid"}) and one per embeddings request ({"inputs"}). Secrets are never recorded.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def record(entry: dict) -> None:
    path = os.environ.get("STUB_RECORD")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def main() -> None:
    argv = sys.argv[1:]
    port = int(argv[argv.index("--port") + 1])
    record({"argv": argv, "env_names": sorted(os.environ), "pid": os.getpid()})
    if os.environ.get("STUB_CRASH") == "1":
        sys.exit(3)
    secret = os.environ.get("LLAMA_ARG_API_KEY") or os.environ.get("LUMINA_ASR_SECRET") or ""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            self._send(200 if self.path == "/health" else 404, {"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not secret or self.headers.get("Authorization") != f"Bearer {secret}":
                self._send(401, {"error": "unauthorized"})
                return
            if self.path == "/v1/embeddings":
                inputs = json.loads(body)["input"]
                record({"inputs": inputs})
                vectors = [[1.0, 0.0] if any(word in text.lower() for word in ("zombie", "undead")) else [0.0, 1.0] for text in inputs]
                self._send(200, {"data": [{"index": index, "embedding": vector} for index, vector in enumerate(vectors)]})
            elif self.path == "/v1/audio/transcriptions":
                self._send(200, {
                    "task": "transcribe", "language": "en", "duration": 1.0, "text": "hello",
                    "segments": [{"id": 0, "start": 0.0, "end": 0.9, "text": "hello"}],
                    "words": [{"word": "hello", "start": 0.1, "end": 0.5, "probability": 0.9}],
                })
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
