"""S01 — the desktop product and host-launch surface are absent.

Desired behavior after the desktop removal (product = Docker container + web
interface): no desktop/cargo dependency surface in the repo or build scripts,
no OS open/reveal routes or service methods, no desktop diagnostics in the
health response, and the Docker image still builds and serves the web frontend
with the yt-dlp JavaScript runtime present.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from support import make_user

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"


def test_desktop_dependency_absent() -> None:
    """A clean checkout has no desktop workspace, Rust toolchain, or cargo work."""
    assert not (REPO_ROOT / "desktop").exists(), "desktop/ workspace must be removed"
    assert not (REPO_ROOT / "rust-toolchain.toml").exists()
    tool_versions = (REPO_ROOT / ".tool-versions").read_text()
    assert not re.search(r"^rust\b", tool_versions, flags=re.M), ".tool-versions must not pin rust"
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "desktop-" not in makefile, "Makefile must have no desktop-* targets"
    assert "cargo" not in makefile.lower(), "Makefile must never invoke cargo"
    bootstrap = (REPO_ROOT / "scripts" / "bootstrap.py").read_text()
    assert "cargo" not in bootstrap, "bootstrap must not install Rust dependencies"
    assert "desktop" not in bootstrap, "bootstrap must not touch a desktop workspace"
    preflight = (REPO_ROOT / "scripts" / "preflight.py").read_text()
    assert "EXPECTED_RUST" not in preflight
    assert "rustc" not in preflight, "preflight must not require the Rust toolchain"


def test_host_launch_routes_absent() -> None:
    """The open/reveal host-launch routes are gone from the router, and the
    service exposes no OS-launch methods or subprocess usage."""
    # Route-table proof: no registered route targets the removed paths.
    library_open_reveal = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/library/") and route.path.endswith(("/open", "/reveal"))
    ]
    assert library_open_reveal == [], f"removed routes still registered: {library_open_reveal}"

    client = TestClient(app, base_url="http://localhost")
    # The app's SPA static mount answers unregistered POST API paths with 405
    # (GET/HEAD only). A surviving open/reveal handler would answer with API
    # semantics (2xx/401/403/400) instead, so assert the 405 negative control.
    control = client.post("/api/library/any-item-id/no-such-action-ever")
    assert control.status_code == 405
    for path in ("/api/library/any-item-id/open", "/api/library/any-item-id/reveal"):
        response = client.post(path)
        assert response.status_code == 405, (path, response.status_code, response.text)

    from app.services.library import LibraryService

    assert not hasattr(LibraryService, "open_file")
    assert not hasattr(LibraryService, "reveal_file")
    assert "subprocess" not in (BACKEND_APP / "services" / "library.py").read_text()


def test_health_response_has_no_desktop_fields(db_factory, api_client) -> None:
    """Runtime health no longer reports a host Desktop directory."""
    member = make_user("user-desktop", username="desktop", display_name="Desktop Check")
    with db_factory.begin() as session:
        session.add(member)
    response = api_client(user=member, base_url="http://localhost").get("/api/runtime-health")
    assert response.status_code == 200, response.text
    assert "desktop_dir" not in response.json()


def test_docker_frontend_still_builds() -> None:
    """The release image builds and serves the web frontend with the yt-dlp
    JavaScript runtime, and carries no desktop references.

    Substitution for a docker build:
    no docker daemon in the dev sandbox; the frontend-build make lane proves
    buildability, so this asserts the image's static contract instead.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "AS frontend-build" in dockerfile
    assert "COPY --from=frontend-build /build/frontend/dist /app/frontend/dist" in dockerfile
    assert "COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node" in dockerfile, (
        "the in-image node runtime (yt-dlp JavaScript) must be preserved"
    )
    for token in ("desktop", "tauri", "cargo", "rust"):
        assert token not in dockerfile.lower(), f"Dockerfile must not reference {token}"
    entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text()
    for token in ("desktop", "tauri", "cargo"):
        assert token not in entrypoint.lower(), f"entrypoint.sh must not reference {token}"
