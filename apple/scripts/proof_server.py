#!/usr/bin/env python3
"""Run a disposable, loopback-only Lumina server for native simulator tests.

The server uses the production SQLite schema and import service. It creates two
synthetic, 90-second H.264/AAC files, enables the opt-in Jellyfin-compatible
protocol, verifies the client journey, then remains available until Ctrl-C.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PORT = 18_743
ORIGIN = f"http://127.0.0.1:{PORT}"
FIXTURE_PATH = REPOSITORY_ROOT / "apple" / "Tests" / "UITests" / "Fixture.json"
DEVICE_ID = f"apple-proof-{secrets.token_hex(8)}"
AUTH_HEADER = f'MediaBrowser Client="Lumina Apple Proof", Device="Simulator", DeviceId="{DEVICE_ID}", Version="1.0"'
DIRECT_TITLE = "The Lantern Keepers’ Extraordinary Midnight Voyage"
CONVERTED_TITLE = "A Cartographer’s Long Road Beyond the Winter Mountains"
MATRIX_TITLE = "The Patient Observatory at the Edge of Morning"


def ffmpeg(*args: str | Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to create the Apple proof fixture media.")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def make_movie(media_root: Path, title: str, year: int, extension: str, poster_size: str, duration: int = 90) -> None:
    folder = media_root / "Movies" / f"{title} ({year})"
    folder.mkdir(parents=True)
    output = folder / f"{title} ({year}){extension}"
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        *( ("-movflags", "+faststart") if extension == ".mp4" else () ),
        output,
    )
    (folder / "movie.nfo").write_text(
        f"<movie><title>{title}</title><year>{year}</year><plot>Synthetic Apple proof fixture.</plot></movie>",
        encoding="utf-8",
    )
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=size={poster_size}:rate=1:duration=1",
        "-frames:v", "1", folder / "poster.jpg",
    )


def install_loopback_guard() -> None:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    import test_safety

    def set_attribute(owner: object, name: str, guarded: Any) -> None:
        def hook(*args: Any, **kwargs: Any) -> Any:
            try:
                return guarded(*args, **kwargs)
            except AssertionError as error:
                raise socket.gaierror(str(error)) from None
        setattr(owner, name, hook)

    test_safety.LoopbackOnlyGuard().apply(set_attribute)


def configure_environment(state_root: Path) -> None:
    os.environ.update(
        LUMINA_DATA_DIR=str(state_root / "data"),
        LUMINA_PORT=str(PORT),
        LUMINA_APP_PUBLIC_URL=ORIGIN,
        LUMINA_FRONTEND_PUBLIC_URL=ORIGIN,
        LUMINA_STORAGE_MOUNT_PARENTS=str(state_root / "media"),
        LUMINA_ARTWORK_PASS="off",
        LUMINA_MUSIC_YOUTUBE_DISCOVERY="false",
        LUMINA_TMDB_API_KEY="",
        LUMINA_AI_BASE_URL="",
        LUMINA_ASR_BASE_URL="",
    )


def provision(state_root: Path, username: str, password: str) -> dict[str, str]:
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.db import init_db, session_scope
    from app.models import LibraryItem
    from app.services.library_import import LibraryImportService, drive
    from app.services.storage_roots import StorageRootService
    from app.services.users import UserService
    from app.services.yt_dlp_service import YtDlpService

    init_db()
    with session_scope() as db:
        user = UserService(db).create_initial_admin(username, password, "Apple Proof")
        settings = YtDlpService(db).ensure_app_settings()
        settings.jellyfin_enabled = True
        root = StorageRootService(db).create(
            label="Apple proof media",
            container_path=str(state_root / "media"),
            mode="external",
        )
        run = LibraryImportService(db).start(root.id, user.id, visibility="shared")
        run_id = run.id

    drive(run_id)
    with session_scope() as db:
        items = {item.title: item.id for item in db.query(LibraryItem).all()}
    try:
        return {
            "directItemID": items[DIRECT_TITLE],
            "directItemTitle": DIRECT_TITLE,
            "convertedItemID": items[CONVERTED_TITLE],
            "convertedItemTitle": CONVERTED_TITLE,
            "matrixItemID": items[MATRIX_TITLE],
            "matrixItemTitle": MATRIX_TITLE,
        }
    except KeyError as error:
        raise RuntimeError("The production import did not index both proof videos.") from error


def request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Authorization": AUTH_HEADER}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f'{AUTH_HEADER}, Token="{token}"'
    request = Request(ORIGIN + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
    except HTTPError as error:
        raise RuntimeError(f"{method} {path} returned {error.code}: {error.read().decode(errors='replace')}") from error
    return {} if not raw else json.loads(raw)


def verify_api(expected: dict[str, str], username: str, password: str) -> dict[str, str]:
    info = request_json("/System/Info/Public")
    if info.get("ServerName") != "Lumina":
        raise RuntimeError("The Jellyfin-compatible public-info endpoint did not identify Lumina.")

    signed_in = request_json("/Users/AuthenticateByName", method="POST", payload={"Username": username, "Pw": password})
    token = signed_in.get("AccessToken")
    if not isinstance(token, str) or not token:
        raise RuntimeError("The Jellyfin-compatible sign-in did not return an access token.")

    items = request_json("/Items?IncludeItemTypes=Movie,Episode,Video&Recursive=true&Fields=Overview,UserData,ImageTags&SortBy=SortName&StartIndex=0&Limit=50", token=token)
    by_name = {item.get("Name"): item for item in items.get("Items", [])}
    direct = by_name.get(DIRECT_TITLE)
    converted = by_name.get(CONVERTED_TITLE)
    matrix = by_name.get(MATRIX_TITLE)
    if not isinstance(direct, dict) or not isinstance(converted, dict) or not isinstance(matrix, dict):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all imported videos.")
    if any(not item.get("ImageTags", {}).get("Primary") for item in (direct, converted, matrix)):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all synthetic posters.")

    for item in (direct, converted, matrix):
        artwork_url = ORIGIN + f"/Items/{item['Id']}/Images/Primary?" + urlencode({"maxWidth": "400", "ApiKey": token})
        with urlopen(Request(artwork_url), timeout=10) as response:
            if not response.headers.get_content_type().startswith("image/") or not response.read(1):
                raise RuntimeError("A synthetic poster did not load through the authenticated artwork route.")

    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    }
    playback = request_json(
        f"/Items/{direct['Id']}/PlaybackInfo",
        method="POST",
        token=token,
        payload={"DeviceProfile": profile, "EnableDirectPlay": True},
    )
    source = next((candidate for candidate in playback.get("MediaSources", []) if candidate.get("SupportsDirectPlay")), None)
    if not isinstance(source, dict):
        raise RuntimeError("The direct H.264/AAC fixture did not negotiate direct playback.")
    direct_url = ORIGIN + f"/Videos/{direct['Id']}/stream?" + urlencode({"MediaSourceId": source["Id"], "Static": "true", "ApiKey": token})
    with urlopen(Request(direct_url, headers={"Range": "bytes=0-1023"}), timeout=10) as response:
        if not response.read(1):
            raise RuntimeError("The negotiated direct stream returned no media bytes.")

    converted_playback = request_json(
        f"/Items/{converted['Id']}/PlaybackInfo",
        method="POST",
        token=token,
        payload={"DeviceProfile": profile, "EnableDirectPlay": False},
    )
    if not any(candidate.get("SupportsTranscoding") for candidate in converted_playback.get("MediaSources", [])):
        raise RuntimeError("The compatibility fixture did not negotiate a transcode source.")

    request_json(
        "/Sessions/Playing/Progress",
        method="POST",
        token=token,
        payload={
            "ItemId": direct["Id"],
            "MediaSourceId": source["Id"],
            "PlaySessionId": playback["PlaySessionId"],
            "PositionTicks": 120_000_000,
            "IsPaused": True,
        },
    )
    resume = request_json("/UserItems/Resume?IncludeItemTypes=Movie,Episode,Video&Recursive=true&Fields=Overview,UserData,ImageTags&Limit=50", token=token)
    if direct["Id"] not in {item.get("Id") for item in resume.get("Items", [])}:
        raise RuntimeError("Playback progress did not make the direct fixture resumable.")
    return {
        "directItemID": str(direct["Id"]),
        "directItemTitle": DIRECT_TITLE,
        "convertedItemID": str(converted["Id"]),
        "convertedItemTitle": CONVERTED_TITLE,
        "matrixItemID": str(matrix["Id"]),
        "matrixItemTitle": MATRIX_TITLE,
    }


def write_fixture(state_root: Path, username: str, password: str, items: dict[str, str]) -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps({"serverURL": ORIGIN, "username": username, "password": password, **items, "stateRoot": str(state_root)}, indent=2) + "\n", encoding="utf-8")
    FIXTURE_PATH.chmod(0o600)


def main() -> None:
    state_root = Path(tempfile.mkdtemp(prefix="lumina-apple-proof-")).resolve()
    if Path(tempfile.gettempdir()).resolve() not in state_root.parents:
        raise RuntimeError("Refusing to create proof state outside the system temporary directory.")
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    import test_safety

    test_safety.check_root_isolation(state_root)
    username = f"proof-{secrets.token_hex(4)}"
    password = f"Proof-{secrets.token_urlsafe(18)}"
    thread: threading.Thread | None = None
    server: Any = None
    try:
        # Exercise both ordinary poster art and a landscape source that the client
        # must crop into the same library-card bounds. Both images are generated
        # locally and require no downloaded fixture media.
        make_movie(state_root / "media", DIRECT_TITLE, 2026, ".mp4", "360x540")
        make_movie(state_root / "media", CONVERTED_TITLE, 2025, ".mkv", "960x540")
        make_movie(state_root / "media", MATRIX_TITLE, 2024, ".mp4", "360x540", duration=40)
        configure_environment(state_root)
        install_loopback_guard()
        provisioned = provision(state_root, username, password)

        import uvicorn
        from app.main import app

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", timeout_keep_alive=30))
        thread = threading.Thread(target=server.run, name="lumina-apple-proof", daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError("The proof server stopped before becoming ready.")
            time.sleep(0.1)
        else:
            raise RuntimeError("The proof server did not become ready within 10 seconds.")

        verified = verify_api(provisioned, username, password)
        write_fixture(state_root, username, password, verified)
        print(f"Lumina Apple proof server ready at {ORIGIN}")
        print(f"Fixture: {FIXTURE_PATH}")
        print("Press Ctrl-C to stop and remove the disposable state.")
        while thread.is_alive():
            thread.join(timeout=1)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        FIXTURE_PATH.unlink(missing_ok=True)
        shutil.rmtree(state_root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
