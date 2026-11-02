#!/usr/bin/env python3
"""Emit a complete, import-only Lumina FastAPI route inventory.

Usage (from ``backend`` with the pinned virtual environment):

    LUMINA_DATA_DIR=/absolute/path/to/an-isolated-fixture-data \
      mise exec -- .venv/bin/python \
      ../docs/performance/measurements/backend-route-inventory-extract-2026-11-02.py \
      --output ../docs/performance/backend-route-inventory-2026-11-02.json

The extractor imports ``app.main`` but deliberately does *not* construct a
TestClient, run the FastAPI lifespan, call an endpoint, or start runtime
workers. Use an isolated disposable data directory anyway: importing settings
and router modules is intentionally the only permitted application work.

``app.routes`` does not enumerate every route which is included through the
Jellyfin compatibility registration.  Those router lists are deliberately
emitted separately with ``registration_scope=opaque_jellyfin_router``.  They
are not additional public URLs: JellyfinPathMiddleware maps compatible root
paths to these private templates.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import BaseRoute


SOURCE_COMMIT = subprocess.check_output(["git", "rev-parse", "--short=8", "HEAD"], text=True).strip()
SCHEMA_VERSION = 1


def _identity(value: object | None) -> str | None:
    """A stable callable identity without serialising values or closures."""
    if value is None:
        return None
    module = getattr(value, "__module__", type(value).__module__)
    qualname = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    return f"{module}.{qualname or type(value).__qualname__}"


def _source_location(value: object | None) -> dict[str, int | str | None]:
    if value is None:
        return {"file": None, "line": None}
    try:
        filename = inspect.getsourcefile(value)
        _lines, line = inspect.getsourcelines(value)
    except (OSError, TypeError):
        return {"file": None, "line": None}
    if filename is None:
        return {"file": None, "line": None}
    root = Path.cwd().parent if Path.cwd().name == "backend" else Path.cwd()
    try:
        filename = str(Path(filename).resolve().relative_to(root.resolve()))
    except ValueError:
        filename = str(Path(filename).resolve())
    return {"file": filename, "line": line}


def _dependencies(route: APIRoute | APIWebSocketRoute) -> list[str]:
    dependant = getattr(route, "dependant", None)
    values = getattr(dependant, "dependencies", ()) if dependant is not None else ()
    return sorted(identity for dependency in values if (identity := _identity(getattr(dependency, "call", None))))


def _record(
    route: BaseRoute,
    *,
    registration_scope: str,
    router_id: str | None,
    position: int,
    opaque_router_ids: dict[int, str] | None = None,
) -> dict[str, Any]:
    endpoint = getattr(route, "endpoint", None)
    source = _source_location(endpoint)
    record: dict[str, Any] = {
        "position": position,
        "route_class": type(route).__name__,
        "path": getattr(route, "path", None),
        "name": getattr(route, "name", None),
        "registration_scope": registration_scope,
        "router_id": router_id,
        "handler": _identity(endpoint),
        "source": source,
    }
    if original_router := getattr(route, "original_router", None):
        # FastAPI 0.116 stores included routers as _IncludedRouter entries so
        # their routes stay opaque in app.routes. Their children appear in the
        # separately emitted native section below.
        record["registration_scope"] = "opaque_router_mount"
        record["mounted_router"] = {
            "id": (opaque_router_ids or {}).get(id(original_router)),
            "prefix": getattr(original_router, "prefix", None),
            "registered_entries": len(getattr(original_router, "routes", ())),
        }
    if isinstance(route, APIRoute):
        record.update(
            methods=sorted(route.methods or ()),
            include_in_schema=route.include_in_schema,
            dependencies=_dependencies(route),
        )
    elif isinstance(route, APIWebSocketRoute):
        record.update(methods=["WEBSOCKET"], include_in_schema=False, dependencies=_dependencies(route))
    else:
        # Mount/Route entries do not have a FastAPI dependant. Keep them in the
        # complete app inventory rather than pretending they are API handlers.
        record.update(methods=[], include_in_schema=False, dependencies=[])
    return record


def _route_records(
    routes: Iterable[BaseRoute],
    *,
    registration_scope: str,
    router_id: str | None,
    opaque_router_ids: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    return [
        _record(
            route,
            registration_scope=registration_scope,
            router_id=router_id,
            position=position,
            opaque_router_ids=opaque_router_ids,
        )
        for position, route in enumerate(routes)
    ]


def _summary(records: list[dict[str, Any]]) -> dict[str, int]:
    api = [record for record in records if record["route_class"] == "APIRoute"]
    websocket = [record for record in records if record["route_class"] == "APIWebSocketRoute"]
    return {
        "registered_entries": len(records),
        "non_api_entries": len(records) - len(api) - len(websocket),
        "opaque_router_mounts": sum(record["registration_scope"] == "opaque_router_mount" for record in records),
        "api_route_templates": len(api),
        "api_method_bindings": sum(len(record["methods"]) for record in api),
        "openapi_visible_templates": sum(bool(record["include_in_schema"]) for record in api),
        "openapi_hidden_templates": sum(not record["include_in_schema"] for record in api),
        "websocket_templates": len(websocket),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", default=SOURCE_COMMIT)
    args = parser.parse_args()
    if not os.environ.get("LUMINA_DATA_DIR"):
        parser.error("set LUMINA_DATA_DIR to an isolated disposable data directory")
    if not (Path.cwd() / "app").is_dir():
        parser.error("run from the backend directory so the app package is importable")
    # A Python script executed from docs/ has that directory, not the process
    # working directory, at sys.path[0].  Deliberately import only the backend
    # package selected by the documented working directory.
    sys.path.insert(0, str(Path.cwd()))

    # Imports happen after the isolated-data guard.  No lifespan context is
    # entered, so this only builds the router graph.
    from app.main import app
    from app.routers import jellyfin

    nested = [
        ("core", jellyfin.router),
        ("stream_direct", jellyfin.stream_router),
        ("metadata", jellyfin.jellyfin_metadata.router),
        ("music", jellyfin.jellyfin_music.router),
        ("home", jellyfin.jellyfin_home.router),
        ("library", jellyfin.jellyfin_library.router),
        ("requests", jellyfin.jellyfin_requests.router),
        ("settings", jellyfin.jellyfin_settings.router),
        ("settings_discovery", jellyfin.jellyfin_settings_discovery.router),
        ("streaming", jellyfin.jellyfin_streaming.router),
        ("watchlist", jellyfin.jellyfin_watchlist.router),
        ("books", jellyfin.jellyfin_books.router),
        ("probes", jellyfin.jellyfin_probes.router),
    ]
    opaque_router_ids = {id(router): name for name, router in nested}
    app_records = _route_records(
        app.routes,
        registration_scope="direct_app_route",
        router_id=None,
        opaque_router_ids=opaque_router_ids,
    )
    nested_records = {
        name: _route_records(router.routes, registration_scope="opaque_jellyfin_router", router_id=name)
        for name, router in nested
    }
    nested_summary = {name: _summary(records) for name, records in nested_records.items()}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "extraction": {
            "mode": "import_only_no_lifespan_no_http_no_runtime_workers",
            "requires_isolated_data_dir": True,
            "root_alias_behavior": "JellyfinPathMiddleware maps compatible root paths to /jellyfin private templates; literal /jellyfin and /emby are retired.",
            "nested_mount_distinction": "Jellyfin routers are registered by app.routers.jellyfin.register but remain opaque in app.routes; records are separate and must not be added to direct-app totals.",
        },
        "direct_app_routes": {"summary": _summary(app_records), "routes": app_records},
        "opaque_jellyfin_routers": {"summary_by_router": nested_summary, "routers": nested_records},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
