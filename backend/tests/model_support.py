"""Shared helpers for on-device model tests: a temporary catalog, installed files, fakes (Tasks 2, 4, 5 append)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from app.services import model_catalog

REV = "0123456789abcdef0123456789abcdef01234567"
LOOPBACK = "http://127.0.0.1:9"  # a URL that is never dialed unless a test starts a download


def payload_for(name: str) -> bytes:
    """Deterministic content for a test model file."""
    return (name.encode() + b"-weights-") * 64


def file_entry(base_url: str, name: str, payload: bytes | None = None) -> dict:
    body = payload_for(name) if payload is None else payload
    return {
        "name": name, "url": f"{base_url}/org/repo/resolve/{REV}/{name}",
        "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
    }


def model_entry(
    model_id: str, role: str, files: list[dict], *, default: bool = False, ram_bytes: int = 1 << 20, engine: dict | None = None,
) -> dict:
    if engine is None:
        engine = {"dimensions": 2, "pooling": None, "query_prefix": "", "document_prefix": ""} if role == "search" else {"language": None}
    return {
        "id": model_id, "role": role, "default": default, "name": model_id.title(), "description": f"{model_id} (test model)",
        "licence": "MIT", "ram_bytes": ram_bytes, "engine": engine, "files": files,
    }


def default_pair(base_url: str = LOOPBACK) -> list[dict]:
    """A valid minimal catalog: a default search model (one file) and a default speech model (two files)."""
    return [
        model_entry("test-search", "search", [file_entry(base_url, "search.gguf")], default=True),
        model_entry("test-speech", "speech", [file_entry(base_url, "model.bin"), file_entry(base_url, "config.json")], default=True),
    ]


def use_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, models: list[dict], *, hosts: tuple[str, ...] = ("127.0.0.1",),
) -> model_catalog.Catalog:
    """Point the catalog at a temporary file for this test; monkeypatch restores the shipped one."""
    path = tmp_path / "model-catalog.json"
    path.write_text(json.dumps({"hosts": list(hosts), "cdn_host_suffixes": [], "models": models}))
    monkeypatch.setattr(model_catalog, "CATALOG_PATH", path)
    model_catalog._load.cache_clear()  # a second call in one test rewrites the same file
    return model_catalog.catalog()


def wait_until(predicate, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.01)


def install(model: model_catalog.CatalogModel) -> None:
    """Write the model's files (``payload_for`` content) and its ready marker, as a verified download would."""
    directory = model_catalog.model_dir(model)
    directory.mkdir(parents=True, exist_ok=True)
    for file in model.files:
        (directory / file.name).write_bytes(payload_for(file.name))
    (directory / model_catalog.READY_MARKER).write_text(json.dumps({file.name: file.sha256 for file in model.files}))


import socket  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402


class FakeModelHost:
    """Loopback file host with Range support. Per-path knobs bend one response; ``requests`` logs (path, Range)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.redirects: dict[str, str] = {}  # path -> Location
        self.statuses: dict[str, int] = {}  # path -> forced status with an empty body
        self.drop_after: dict[str, int] = {}  # path -> send this many body bytes, then cut the connection (once)
        self.hide_length: set[str] = set()  # paths answered without Content-Length (body ends at close)
        self.ignore_range = False
        self.slow = False  # 16 KiB pieces with a short pause, so a test can cancel mid-body
        host = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                wanted = self.headers.get("Range")
                host.requests.append((path, wanted))
                if path in host.redirects:
                    self._empty(302, {"Location": host.redirects[path]})
                    return
                if path in host.statuses:
                    self._empty(host.statuses[path])
                    return
                body = host.files.get(path)
                if body is None:
                    self._empty(404)
                    return
                start = int(wanted.removeprefix("bytes=").rstrip("-")) if wanted and not host.ignore_range else 0
                if start >= len(body) and wanted and not host.ignore_range:
                    self._empty(416, {"Content-Range": f"bytes */{len(body)}"})
                    return
                self.send_response(206 if start else 200)
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                chunk = body[start:]
                if path in host.hide_length:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                else:
                    self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                limit = host.drop_after.pop(path, None)
                if limit is not None:
                    self.wfile.write(chunk[:limit])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.close_connection = True
                    return
                step = 16 * 1024 if host.slow else len(chunk) or 1
                for offset in range(0, len(chunk), step):
                    self.wfile.write(chunk[offset:offset + step])
                    if host.slow:
                        time.sleep(0.005)

            def _empty(self, status: int, headers: dict[str, str] | None = None) -> None:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def serve(self, entry: dict, body: bytes | None = None) -> None:
        """Serve a ``file_entry`` at its URL path (``body`` defaults to the entry's own payload)."""
        self.files[urlsplit(entry["url"]).path] = payload_for(entry["name"]) if body is None else body

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


import shlex  # noqa: E402
import sys  # noqa: E402

from app.config import settings  # noqa: E402

STUB = Path(__file__).resolve().parent / "fixtures" / "stub_model_server.py"


def stub_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> Path:
    """Point both model runtimes at the stub server; ``env`` (e.g. STUB_CRASH="1") is baked into a wrapper script.

    Returns the stub's JSON-lines record file. The supervisor passes the child a minimal environment, so
    test knobs travel in the wrapper, never through the test process's environment.
    """
    record = tmp_path / "stub-record.jsonl"
    exports = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in {"STUB_RECORD": str(record), **env}.items())
    wrapper = tmp_path / "stub-runtime"
    wrapper.write_text(f"#!/bin/sh\n{exports}exec {shlex.quote(sys.executable)} {shlex.quote(str(STUB))} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(settings, "llama_server_bin", str(wrapper))
    monkeypatch.setattr(settings, "asr_python", str(wrapper))  # argv: wrapper <asr_server_script> --model ...
    monkeypatch.setattr(settings, "asr_server_script", str(STUB))  # arrives as the stub's first argument; ignored
    return record


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


from app.services import model_downloads, model_supervisor  # noqa: E402


@pytest.fixture
def fresh_models(monkeypatch: pytest.MonkeyPatch):
    """Fresh supervisor + download manager as the module singletons; every child is stopped afterwards."""
    supervisor = model_supervisor.Supervisor()
    manager = model_downloads.DownloadManager()
    monkeypatch.setattr(model_supervisor, "supervisor", supervisor)
    monkeypatch.setattr(model_downloads, "manager", manager)
    monkeypatch.setattr(model_supervisor, "available_memory", lambda: 64 << 30)
    yield supervisor, manager
    manager.shutdown()
    supervisor.stop_all()
