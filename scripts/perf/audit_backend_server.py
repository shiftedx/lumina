"""Loopback-only audit server with the ordinary perf instrumentation plus telemetry.

It is intentionally a script-local endpoint, never registered by production
``app.main``.  ``audit_backend.py`` uses it only after every measured HTTP call
to collect server-process RSS/CPU, SQL counts, and the existing durable-write
admission metrics.
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERF = Path(__file__).resolve().parent
sys.path[:0] = [str(PERF), str(ROOT / "backend"), str(ROOT / "scripts"), str(ROOT / "backend" / "tests")]

# ``serve`` is purpose-built for a loopback perf process. It reads DATA_DIR and
# PORT from argv, applies the provider socket guard and canned extractor, then
# exposes main.app without starting uvicorn when imported.
import serve  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Response  # noqa: E402

from app.persistence import persistence_metrics_snapshot  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ArrServer, LibraryItem, ListeningExportConnection, ServiceConnector, User  # noqa: E402
from app.services import arr_observer, infrastructure_connectors, listening_export  # noqa: E402
from arr_fake import API_KEY, FakeArr  # noqa: E402
from lidarr_fake import API_KEY as LIDARR_KEY, FakeLidarr  # noqa: E402


ARR = FakeArr("radarr")
ARR_OPERATION = FakeArr("sonarr")
LIDARR = FakeLidarr()
EXPORT_DISPATCHES = 0


def _seed_fakes() -> None:
    """Install only loopback fakes and durable rows needed by audit routes."""
    global EXPORT_DISPATCHES
    # A controlled 10k upstream inventory checks client pagination/replace
    # bounds. The observer's own 1000-item pages remain authoritative.
    for number in range(10_000):
        ARR.library[number + 1] = {"id": number + 1, "title": f"Audit movie {number}", "tmdbId": number + 1,
                                   "hasFile": False, "monitored": bool(number % 2), "year": 2000 + number % 20}
    ARR.queue = [{"id": f"queue-{number}", "movieId": number % 10_000 + 1, "status": "downloading"} for number in range(10_000)]
    ARR_OPERATION.library[101] = {"id": 101, "title": "Audit operation", "monitored": False, "qualityProfileId": 1,
                                  "rootFolderPath": "/tv/Shows", "seasons": [{"seasonNumber": 1, "monitored": False}]}
    LIDARR.artists[1] = {"id": 1, "foreignArtistId": "11111111-1111-4111-8111-111111111111", "artistName": "Audit artist", "monitored": True}
    LIDARR.albums[1] = {"id": 1, "foreignAlbumId": "22222222-2222-4222-8222-222222222222", "artist": {"id": 1}, "title": "Audit album", "monitored": True}
    LIDARR.queue = [{"id": number + 1, "albumId": 1, "artistId": 1, "status": "downloading", "trackedDownloadState": "ok"} for number in range(10_000)]
    with SessionLocal() as db:
        if db.get(ArrServer, "audit-arr") is None:
            db.add(ArrServer(id="audit-arr", kind="radarr", name="Audit Radarr", base_url=ARR.url, api_key=API_KEY, enabled=True))
        if db.get(ArrServer, "audit-operation") is None:
            db.add(ArrServer(id="audit-operation", kind="sonarr", name="Audit Sonarr", base_url=ARR_OPERATION.url, api_key=API_KEY, enabled=True))
        if db.get(ServiceConnector, "audit-lidarr") is None:
            db.add(ServiceConnector(id="audit-lidarr", kind="lidarr", name="Audit Lidarr", base_url=LIDARR.url, api_key=LIDARR_KEY,
                                    enabled=True, settings={"root_folder": "/music", "quality_profile_id": 4, "metadata_profile_id": 8, "path_mappings": []}))
        users = list(db.query(User).all())
        track = db.query(LibraryItem).filter_by(kind="track", visibility="shared", status="available").first()
        if track is not None:
            track.metadata_json = {**(track.metadata_json or {}), "lumina_audio_tags": {"title": track.title, "artist": "Audit artist", "album": "Audit album"}}
        for user in users:
            if db.get(ListeningExportConnection, (user.id, "lastfm")) is None:
                db.add(ListeningExportConnection(user_id=user.id, provider="lastfm", generation=str(uuid4()),
                                                 credentials={"fixture": "script-local"}, enabled=True, username="audit"))
        db.commit()

    def lastfm(_credentials, method: str, **_params):  # noqa: ANN001, ANN202
        nonlocal_dispatch[0] += 1
        if method == "track.scrobble":
            return {"scrobbles": {"@attr": {"accepted": "1", "ignored": "0"}}}
        return {}

    nonlocal_dispatch = [0]
    def counted_lastfm(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        result = lastfm(*args, **kwargs)
        globals()["EXPORT_DISPATCHES"] = nonlocal_dispatch[0]
        return result
    listening_export.lastfm = counted_lastfm


def _rss_bytes() -> tuple[int | None, str]:
    """Peak RSS without assuming ``ps`` is installed in the production image."""
    try:
        # macOS ru_maxrss is bytes; Linux reports KiB.
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if value > 0:
            return int(value if sys.platform == "darwin" else value * 1024), "getrusage"
    except (AttributeError, OSError):
        pass
    # Minimal Linux images may still expose procfs when procps/ps is absent.
    try:
        values = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        raw = values.get("VmHWM") or values.get("VmRSS")
        if raw:
            return int(raw.split()[0]) * 1024, "proc_status"
    except (OSError, ValueError, IndexError):
        pass
    return None, "unavailable"


@serve.main.app.get("/_perf/audit/metrics", include_in_schema=False)
def metrics() -> Response:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss, rss_source = _rss_bytes()
    payload = {
        "rss_peak_bytes": rss, "rss_source": rss_source,
        "cpu_user_seconds": usage.ru_utime,
        "cpu_system_seconds": usage.ru_stime,
        "writer": persistence_metrics_snapshot(),
    }
    return Response(json.dumps(payload), media_type="application/json", headers={"Cache-Control": "no-store"})


@serve.main.app.post("/_perf/audit/arr/refresh", include_in_schema=False)
def refresh_arr() -> Response:
    started = time.perf_counter()
    ok = arr_observer.sync_once("audit-arr")
    if not ok:
        return Response(json.dumps({"ok": False}), status_code=409, media_type="application/json")
    return Response(json.dumps({"ok": True, "elapsed_server_ms": round((time.perf_counter() - started) * 1000, 3)}), media_type="application/json")


@serve.main.app.post("/_perf/audit/connector/refresh", include_in_schema=False)
def refresh_connector() -> Response:
    with SessionLocal() as db:
        row = infrastructure_connectors.refresh(db, "audit-lidarr")
        db.refresh(row)
        return Response(json.dumps({"ok": row.last_error is None, "enabled": row.enabled}), media_type="application/json")


@serve.main.app.get("/_perf/audit/export-dispatches", include_in_schema=False)
def export_dispatches() -> Response:
    return Response(json.dumps({"dispatches": EXPORT_DISPATCHES}), media_type="application/json")


def _prioritize_audit_routes() -> None:
    """Put script-local controls ahead of main.py's root SPA mount.

    FastAPI routes added after importing ``app.main`` otherwise land after the
    ``/`` StaticFiles mount, which serves index.html for these controls.  Keep
    this reordering entirely inside the benchmark server process.
    """
    routes = serve.main.app.router.routes
    controls = [route for route in routes if getattr(route, "path", "").startswith("/_perf/audit/")]
    routes[:] = [*controls, *(route for route in routes if route not in controls)]


_prioritize_audit_routes()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: audit_backend_server.py DATA_DIR PORT")
    _seed_fakes()
    try:
        # Docker's port publishing requires 0.0.0.0; native runs stay loopback-only.
        uvicorn.run(serve.main.app, host=os.environ.get("AUDIT_BIND_HOST", "127.0.0.1"), port=int(sys.argv[2]), log_level="warning", timeout_keep_alive=30)
    finally:
        ARR.close()
        ARR_OPERATION.close()
        LIDARR.close()
