"""Gallery T1: artwork preparation counters for admins: /api/admin/artwork and Diagnostics."""
from __future__ import annotations

import collections
import uuid

import pytest

from app import db as db_module
from app.models import MediaTitle
from app.routers import admin_diagnostics
from app.services import art_urls, renditions
from app.services.hwaccel import HwStatus, hwaccel
from art_support import add_art_title, artwork_row, give_art, seed_art_root, write_rendition
from support import make_user
from test_v1_sessions import client, factory, login  # noqa: F401  (fixtures)

A, B, C = (str(uuid.UUID(int=n)) for n in (0xA1, 0xB1, 0xC1))
ADMIN, MEMBER = make_user("admin", role="admin"), make_user("member")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(renditions, "_serving", collections.Counter())


def test_progress_counts_only_rows_for_the_current_art(tmp_path) -> None:  # noqa: ANN001
    media = tmp_path.resolve() / "media"
    seed_art_root(media)
    add_art_title(media, A, images=("Primary", "Backdrop", "Logo"))
    add_art_title(media, B, "episode")
    with db_module.SessionLocal() as session:
        movie = session.get(MediaTitle, A)
        key = artwork_row(session, movie, "Primary").source_key
        artwork_row(session, movie, "Backdrop", state="failed", error="timeout")
        artwork_row(session, movie, "Logo", stale=True)  # keyed to older art: counts as not prepared
        other = session.get(MediaTitle, B)
        other.type = "movie"
        give_art(other, "Backdrop", path="b/backdrop.avif")  # unsupported format: not in the total
        session.commit()
    write_rendition(key, 240, b"x" * 100)
    write_rendition(key, 480, b"x" * 50)
    progress = renditions.ArtworkRenditions().progress()
    assert (progress.total, progress.prepared, progress.failed, progress.unsupported) == (4, 1, 1, 0)
    assert progress.failure_reasons == {"timeout": 1}
    assert progress.cache_bytes == 150 and progress.running is False and progress.paused_for_playback is False
    assert progress.updated_at is not None


def test_progress_is_cached_for_30_seconds_but_live_flags_and_serving_are_current(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    seed_art_root(tmp_path.resolve() / "media")
    scans = []
    real = renditions.scan
    monkeypatch.setattr(renditions, "scan", lambda db, skipped=frozenset(): scans.append(1) or real(db, skipped))
    worker = renditions.ArtworkRenditions()
    worker.progress()
    renditions.count("hits_memory")
    renditions.count("hits_memory")
    worker._paused = True
    second = worker.progress()
    assert len(scans) == 1
    assert second.serving.hits_memory == 2 and second.paused_for_playback is True
    monkeypatch.setattr(renditions, "PROGRESS_MAX_AGE_SECONDS", 0)
    worker.progress()
    assert len(scans) == 2


def test_housekeeping_never_recounts_the_library(monkeypatch) -> None:  # noqa: ANN001
    # A full scan plus cache_bytes every 30 s for the whole initial pass; admin reads recompute lazily instead.
    monkeypatch.setattr(renditions, "scan", lambda *_args: pytest.fail("housekeeping scanned the library"))
    monkeypatch.setattr(renditions, "cache_bytes", lambda: pytest.fail("housekeeping walked the cache"))
    worker = renditions.ArtworkRenditions()
    worker._swept_at = renditions.time.monotonic()  # the sweep is not due
    worker._housekeeping()


def test_progress_never_fails_without_tables() -> None:
    progress = renditions.ArtworkRenditions().progress()  # conftest's file database has no tables here
    assert (progress.total, progress.prepared) == (0, 0)


def test_admin_artwork_route_is_admin_only(api_client) -> None:  # noqa: ANN001
    assert api_client(base_url="http://localhost").get("/api/admin/artwork").status_code == 401
    assert api_client(user=MEMBER, base_url="http://localhost").get("/api/admin/artwork").status_code == 403
    response = api_client(user=ADMIN, base_url="http://localhost").get("/api/admin/artwork")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"total", "prepared", "failed", "unsupported", "cache_bytes", "running", "paused_for_playback", "failure_reasons", "serving", "updated_at"}


def test_diagnostics_carry_the_artwork_counters(client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(admin_diagnostics, "check_connection", lambda config: {"ok": False, "model_available": False, "error": "offline"})
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus())
    login(client)
    report = client.get("/api/admin/diagnostics")
    assert report.status_code == 200, report.text
    assert report.json()["artwork"]["serving"]["misses"] == 0
