"""S00 baseline isolation tests (desired behavior).

Run from the repository root after the locked baseline is installed:
    (cd backend && .venv/bin/python -m pytest -q tests/test_v1_baseline.py)
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.request
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from tests.conftest import REPOSITORY_ROOT
import test_safety


def test_baseline_guard_rejects_production_root(tmp_path: Path) -> None:
    # Direct rejection of the live app-data directory and media mounts.
    live_data = REPOSITORY_ROOT / "backend" / ".data"
    with pytest.raises(AssertionError, match="forbidden root"):
        test_safety.check_root_isolation(live_data)
    with pytest.raises(AssertionError, match="forbidden root"):
        test_safety.check_root_isolation(live_data / "cookies" / "attacker.txt")
    with pytest.raises(AssertionError, match="forbidden root"):
        test_safety.check_root_isolation(Path("/Volumes/MountedLibrary/movie.mkv"))

    # The autouse guard must have redirected the running settings object.
    assert settings.data_dir.is_relative_to(tmp_path)
    # database_url is a sqlite URL built from data_dir; it must stay inside it.
    assert str(settings.data_dir) in str(settings.database_url)

    # init_db under the guard writes only inside the temporary root:
    # the live data directory is untouched (name/mtime/size snapshot diff).
    if live_data.is_dir():
        before = {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in live_data.rglob("*")
        }
        db_module.init_db()
        after = {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in live_data.rglob("*")
        }
        assert before == after, "live app-data directory was modified by a test"
    else:
        db_module.init_db()
        assert not live_data.exists(), "test created the live app-data directory"


def test_baseline_provider_network_disabled() -> None:
    # External TCP is refused before any bytes leave the host.
    with pytest.raises(AssertionError, match="blocked in tests"):
        with socket.create_connection(("93.184.215.14", 80), timeout=2):
            pass

    # External DNS is refused.
    with pytest.raises(AssertionError, match="DNS resolution blocked"):
        socket.getaddrinfo("example.com", 80)

    # A loopback fixture server remains permitted (test-only injection).
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()


def test_forbidden_roots_cover_live_app_data() -> None:
    # The guard list must actually contain this checkout's live data dir,
    # so the suite cannot silently run against household data on a new clone.
    assert REPOSITORY_ROOT / "backend" / ".data" in test_safety.FORBIDDEN_ROOTS
