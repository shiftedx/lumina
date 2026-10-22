#!/usr/bin/env python3
"""Give each native UI-test class a private disposable proof server.

The broker is intentionally loopback-only.  Its random URL capability is the
only way to obtain a fixture, and it is written with mode 0600 because fixture
responses contain disposable credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]


class FixtureBroker:
    def __init__(self, state: Path, python: str, command_factory: Callable[[str, Path], list[str]] | None = None) -> None:
        self.state = state
        self.python = python
        self.command_factory = command_factory or self._proof_command
        self._fixtures: dict[str, tuple[subprocess.Popen[bytes], Path, Path]] = {}
        self._diagnostics: set[Path] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _valid_key(key: str) -> bool:
        return bool(key) and all(character.isalnum() or character in "_.-" for character in key)

    def _proof_command(self, _: str, fixture: Path) -> list[str]:
        return [self.python, "-u", str(ROOT / "apple" / "scripts" / "proof_server.py")]

    def fixture(self, key: str) -> bytes:
        if not self._valid_key(key):
            raise ValueError("invalid fixture key")
        with self._lock:
            existing = self._fixtures.get(key)
            if existing and existing[0].poll() is None and existing[1].is_file():
                return existing[1].read_bytes()
            self.stop(key)
            fixture = self.state / f"{key}.json"
            environment = os.environ.copy()
            environment.update(LUMINA_NATIVE_PORT="0", LUMINA_NATIVE_FIXTURE=str(fixture))
            # Do not retain the server's output: its errors may include the fixture path.
            log = self.state / f"{key}.log"
            with log.open("w", encoding="utf-8") as stream:
                os.chmod(log, 0o600)
                process = subprocess.Popen(self.command_factory(key, fixture), cwd=ROOT, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            self._fixtures[key] = (process, fixture, log)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if fixture.is_file() and fixture.stat().st_size:
                return fixture.read_bytes()
            if process.poll() is not None:
                break
            time.sleep(0.1)
        self.stop(key, preserve_log=True)
        raise RuntimeError(f"proof fixture did not become ready; private diagnostic: {log}")

    def stop(self, key: str, preserve_log: bool = False) -> None:
        entry = self._fixtures.pop(key, None)
        if not entry:
            return
        process, fixture, log = entry
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        fixture.unlink(missing_ok=True)
        if preserve_log:
            self._diagnostics.add(log)
        else:
            log.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            for key in list(self._fixtures):
                self.stop(key)
            for log in self._diagnostics:
                log.unlink(missing_ok=True)
            self._diagnostics.clear()


def serve(output: Path, python: str, command_factory: Callable[[str, Path], list[str]] | None = None,
          stop_event: threading.Event | None = None) -> None:
    state = Path(tempfile.mkdtemp(prefix="lumina-native-ui-suite-"))
    os.chmod(state, 0o700)
    broker = FixtureBroker(state, python, command_factory)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def _key(self) -> str | None:
            prefix = f"/{token}/"
            return self.path.removeprefix(prefix) if self.path.startswith(prefix) else None

        def _reply(self, status: HTTPStatus, payload: bytes = b"") -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            key = self._key()
            if key is None:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            try:
                self._reply(HTTPStatus.OK, broker.fixture(key))
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST)
            except RuntimeError:
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)

        def do_DELETE(self) -> None:  # noqa: N802
            key = self._key()
            if key is None or not broker._valid_key(key):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            broker.stop(key)
            self._reply(HTTPStatus.OK, b"{}")

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    payload = json.dumps({"brokerURL": f"http://127.0.0.1:{server.server_port}/{token}"}) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    os.chmod(output, 0o600)
    stopping = threading.Event()

    def shutdown(*_: object) -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
    if stop_event is not None:
        def wait_for_stop() -> None:
            stop_event.wait()
            shutdown()
        threading.Thread(target=wait_for_stop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        broker.close()
        output.unlink(missing_ok=True)
        state.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="private broker JSON path")
    parser.add_argument("--python", default=str(ROOT / "backend" / ".venv" / "bin" / "python"))
    args = parser.parse_args()
    serve(args.output, args.python)


if __name__ == "__main__":
    main()
