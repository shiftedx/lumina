#!/usr/bin/env python3
"""Run a disposable, loopback-only Lumina server for native simulator tests.

The server uses the production SQLite schema and import service. It creates
synthetic H.264/AAC videos and an AAC music album, enables the opt-in Jellyfin-compatible
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
import uuid
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
AUDIO_ARTIST = "The Synthetic Lantern Ensemble"
AUDIO_ALBUM = "Signals from a Quiet Observatory"
AUDIO_TRACK = "First Light in the Listening Room"
AUDIO_SECOND_TRACK = "A Signal Answered at Dawn"
SHARED_MUSIC_PLAYLIST = "Observatory household listening"
SYNTHETIC_LYRIC = "A lantern answers through the quiet night"
SHOW_TITLE = "The Observatory Signal Logs"
EPISODE_TITLE = "The First Signal"
SECOND_EPISODE_TITLE = "Morning Coordinates"


def fixture_artwork() -> dict[str, Path]:
    directory = os.environ.get("LUMINA_NATIVE_ARTWORK_DIR")
    if not directory:
        return {}
    files = {}
    for name in ("cartographer.png", "lanterns.png", "observatory.png"):
        source = Path(directory) / name
        try:
            size = source.stat().st_size
            with source.open("rb") as image:
                header = image.read(33)
            valid = (source.is_file() and 33 <= size <= 12 * 1024 * 1024
                     and header[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                     and int.from_bytes(header[16:20], "big") > 0 and int.from_bytes(header[20:24], "big") > 0)
        except OSError:
            valid = False
        if not valid:
            raise RuntimeError(f"Original fixture artwork requires {name} to be a PNG no larger than 12 MiB.")
        files[name] = source
    return files


def copy_artwork(source: Path, target: Path) -> None:
    # Importers can prefer a JPEG sidecar over a PNG with the same image role.
    for extension in (".jpg", ".jpeg"):
        target.with_suffix(extension).unlink(missing_ok=True)
    shutil.copyfile(source, target)


def ffmpeg(*args: str | Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to create the Apple proof fixture media.")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def make_movie(media_root: Path, title: str, year: int, extension: str, poster_size: str, duration: int = 90,
               artwork: Path | None = None) -> None:
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
    if artwork:
        copy_artwork(artwork, folder / "poster.png")
    else:
        ffmpeg(
            "-f", "lavfi", "-i", f"testsrc2=size={poster_size}:rate=1:duration=1",
            "-frames:v", "1", folder / "poster.jpg",
        )


def make_music(media_root: Path, artwork: Path | None = None) -> None:
    """Original generated tone and artwork; no downloaded music or recordings."""
    folder = media_root / "Music" / AUDIO_ARTIST / f"{AUDIO_ALBUM} (2026)"
    folder.mkdir(parents=True)
    from mutagen.mp4 import MP4
    for number, title, frequency in [(1, AUDIO_TRACK, 440), (2, AUDIO_SECOND_TRACK, 550)]:
        ffmpeg(
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=90",
            "-c:a", "aac", "-metadata", f"title={title}",
            "-metadata", f"artist={AUDIO_ARTIST}", "-metadata", f"album_artist={AUDIO_ARTIST}",
            "-metadata", f"album={AUDIO_ALBUM}", "-metadata", f"track={number}", "-metadata", "date=2026",
            "-movflags", "+faststart", folder / f"{number:02d} {title}.m4a",
        )
        tagged = MP4(folder / f"{number:02d} {title}.m4a")
        tagged["©lyr"] = [f"[00:00.00]{SYNTHETIC_LYRIC}\n[00:08.00]Morning coordinates find the light"]
        tagged["©gen"] = ["Ambient"]
        for key, value in {"REPLAYGAIN_TRACK_GAIN": "-6 dB", "REPLAYGAIN_ALBUM_GAIN": "-4 dB",
                           "REPLAYGAIN_TRACK_PEAK": "0.8", "REPLAYGAIN_ALBUM_PEAK": "0.9"}.items():
            tagged[f"----:com.apple.iTunes:{key}"] = [value.encode()]
        tagged.save()
    if artwork:
        copy_artwork(artwork, folder / "cover.png")
        copy_artwork(artwork, folder.parent / "artist.png")
    else:
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=540x540:rate=1:duration=1",
            "-frames:v", "1", folder / "cover.jpg",
        )
        shutil.copyfile(folder / "cover.jpg", folder.parent / "artist.jpg")


def make_show(media_root: Path) -> None:
    """Reuse only this harness's original generated video and artwork."""
    show = media_root / "Shows" / SHOW_TITLE
    season = show / "Season 01"
    season.mkdir(parents=True)
    (show / "tvshow.nfo").write_text(f"<tvshow><title>{SHOW_TITLE}</title><plot>Synthetic observatory episode fixture.</plot></tvshow>", encoding="utf-8")
    movie = media_root / "Movies" / f"{DIRECT_TITLE} (2026)"
    poster = movie / "poster.png" if (movie / "poster.png").is_file() else movie / "poster.jpg"
    shutil.copyfile(poster, show / poster.name)
    for number, title in [(1, EPISODE_TITLE), (2, SECOND_EPISODE_TITLE)]:
        stem = f"{SHOW_TITLE} S01E{number:02d}"
        shutil.copyfile(movie / f"{DIRECT_TITLE} (2026).mp4", season / f"{stem}.mp4")
        (season / f"{stem}.nfo").write_text(
            f"<episodedetails><title>{title}</title><showtitle>{SHOW_TITLE}</showtitle><season>1</season><episode>{number}</episode><plot>Synthetic episode.</plot></episodedetails>",
            encoding="utf-8",
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
    from app.models import LibraryItem, MediaTitle, User
    from app.music_schemas import MusicPlaylistSave
    from app.services import music_playlists
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
        titles = {(title.type, title.name): title.id for title in db.query(MediaTitle).all()}
        show_id = titles[("series", SHOW_TITLE)]
        season_id = db.query(MediaTitle).filter_by(type="season", parent_id=show_id).one().id
        owner = db.query(User).filter_by(username=username).one()
        curator = UserService(db).create_member_without_password(owner, "observatory-curator", "Observatory curator")
        shared_playlist = music_playlists.save(db, curator, MusicPlaylistSave(
            name=SHARED_MUSIC_PLAYLIST, shared=True, item_ids=[items[AUDIO_TRACK], items[AUDIO_SECOND_TRACK]]))
    try:
        return {
            "directItemID": items[DIRECT_TITLE],
            "directItemTitle": DIRECT_TITLE,
            "convertedItemID": items[CONVERTED_TITLE],
            "convertedItemTitle": CONVERTED_TITLE,
            "matrixItemID": items[MATRIX_TITLE],
            "matrixItemTitle": MATRIX_TITLE,
            "showID": uuid.UUID(show_id).hex,
            "showTitle": SHOW_TITLE,
            "seasonID": uuid.UUID(season_id).hex,
            "episodeID": uuid.UUID(titles[("episode", EPISODE_TITLE)]).hex,
            "episodeTitle": EPISODE_TITLE,
            "secondEpisodeID": uuid.UUID(titles[("episode", SECOND_EPISODE_TITLE)]).hex,
            "audioAlbumID": uuid.UUID(titles[("album", AUDIO_ALBUM)]).hex,
            "audioAlbumTitle": AUDIO_ALBUM,
            "audioArtistID": uuid.UUID(titles[("artist", AUDIO_ARTIST)]).hex,
            "audioTrackID": uuid.UUID(items[AUDIO_TRACK]).hex,
            "audioTrackTitle": AUDIO_TRACK,
            "audioSecondTrackID": uuid.UUID(items[AUDIO_SECOND_TRACK]).hex,
            "audioSecondTrackTitle": AUDIO_SECOND_TRACK,
            "sharedMusicPlaylistID": shared_playlist["id"],
            "sharedMusicPlaylistTitle": SHARED_MUSIC_PLAYLIST,
            "audioLyric": SYNTHETIC_LYRIC,
        }
    except KeyError as error:
        raise RuntimeError("The production import did not index all proof videos and music.") from error


def request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Authorization": AUTH_HEADER}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f'{AUTH_HEADER}, Token="{token}"'
    request = Request(ORIGIN + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
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
        with urlopen(Request(artwork_url), timeout=60) as response:
            if not response.headers.get_content_type().startswith("image/") or not response.read(1):
                raise RuntimeError("A synthetic poster did not load through the authenticated artwork route.")

    shows = request_json("/Items?IncludeItemTypes=Series&Recursive=true&Limit=50", token=token)
    if expected["showID"] not in {item.get("Id") for item in shows.get("Items", [])}:
        raise RuntimeError("The imported synthetic show was absent from the public catalog.")
    seasons = request_json(f"/Shows/{expected['showID']}/Seasons", token=token)
    if expected["seasonID"] not in {item.get("Id") for item in seasons.get("Items", [])}:
        raise RuntimeError("The synthetic show did not expose its imported season.")
    episodes = request_json(f"/Items?ParentId={expected['seasonID']}&IncludeItemTypes=Episode&Limit=50", token=token)
    if not {expected["episodeID"], expected["secondEpisodeID"]}.issubset({item.get("Id") for item in episodes.get("Items", [])}):
        raise RuntimeError("The synthetic season did not expose both imported episodes.")

    albums = request_json("/Items?IncludeItemTypes=MusicAlbum&Recursive=true&Fields=Overview,UserData,ImageTags&Limit=50", token=token)
    album = next((item for item in albums.get("Items", []) if item.get("Id") == expected["audioAlbumID"]), None)
    if not isinstance(album, dict) or not album.get("ImageTags", {}).get("Primary"):
        raise RuntimeError("The authenticated music catalog did not expose the imported album and its cover.")
    artists = request_json("/Items?IncludeItemTypes=MusicArtist&Recursive=true&Limit=50", token=token)
    if expected["audioArtistID"] not in {item.get("Id") for item in artists.get("Items", [])}:
        raise RuntimeError("The authenticated music catalog did not expose the imported artist.")
    tracks = request_json(f"/Items?ParentId={expected['audioAlbumID']}&IncludeItemTypes=Audio&Limit=50", token=token)
    if not {expected["audioTrackID"], expected["audioSecondTrackID"]}.issubset({item.get("Id") for item in tracks.get("Items", [])}):
        raise RuntimeError("The authenticated album did not expose its imported audio track.")
    album_art = ORIGIN + f"/Items/{expected['audioAlbumID']}/Images/Primary?" + urlencode({"maxWidth": "400", "ApiKey": token})
    with urlopen(Request(album_art), timeout=60) as response:
        if not response.headers.get_content_type().startswith("image/") or not response.read(1):
            raise RuntimeError("The synthetic album cover did not load through authenticated artwork.")
    track = request_json(f"/Users/{signed_in['User']['Id']}/Items/{expected['audioTrackID']}", token=token)
    if track.get("Type") != "Audio" or track.get("Id") != expected["audioTrackID"]:
        raise RuntimeError("The authenticated fresh-item route did not expose the synthetic audio track.")
    native_track = request_json(f"/Lumina/Music/tracks/{expected['audioTrackID']}", token=token)
    if native_track.get("gain_db") != -6 or native_track.get("album_gain_db") != -4:
        raise RuntimeError("The production music import did not preserve synthetic ReplayGain metadata.")
    lyrics = request_json(f"/Lumina/Music/tracks/{expected['audioTrackID']}/lyrics", token=token)
    if not any(cue.get("text") == SYNTHETIC_LYRIC for cue in lyrics.get("cues", [])):
        raise RuntimeError("The production music import did not preserve synthetic timed lyrics.")
    playlists = request_json("/Lumina/Music/playlists", token=token)
    shared = next((entry for entry in playlists if entry.get("id") == expected["sharedMusicPlaylistID"]), None)
    if shared is None or shared.get("can_edit") is not False:
        raise RuntimeError("The native music boundary did not preserve a shared read-only playlist.")
    audio_url = ORIGIN + f"/Audio/{expected['audioTrackID']}/stream?" + urlencode({"Static": "true", "ApiKey": token})
    with urlopen(Request(audio_url, headers={"Range": "bytes=0-1023"}), timeout=60) as response:
        if response.headers.get_content_type() != "audio/mp4":
            raise RuntimeError("The generated AAC M4A stream did not return the audio/mp4 media type required by native playback.")
        if not response.read(1):
            raise RuntimeError("The authenticated audio stream returned no media bytes.")

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
    with urlopen(Request(direct_url, headers={"Range": "bytes=0-1023"}), timeout=60) as response:
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
        **expected,
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
        artwork = fixture_artwork()
        # Default sidecars are generated locally; optional original PNGs are copied unchanged.
        print("Generating synthetic proof video and music media...", flush=True)
        make_movie(state_root / "media", DIRECT_TITLE, 2026, ".mp4", "360x540", artwork=artwork.get("lanterns.png"))
        make_movie(state_root / "media", CONVERTED_TITLE, 2025, ".mkv", "960x540", artwork=artwork.get("cartographer.png"))
        make_movie(state_root / "media", MATRIX_TITLE, 2024, ".mp4", "360x540", duration=40, artwork=artwork.get("observatory.png"))
        make_music(state_root / "media", artwork=artwork.get("observatory.png"))
        make_show(state_root / "media")
        configure_environment(state_root)
        install_loopback_guard()
        print("Importing the disposable video and music catalog...", flush=True)
        provisioned = provision(state_root, username, password)

        import uvicorn
        from app.main import app

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", timeout_keep_alive=30))
        thread = threading.Thread(target=server.run, name="lumina-apple-proof", daemon=True)
        thread.start()
        for _ in range(600):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError("The proof server stopped before becoming ready.")
            time.sleep(0.1)
        else:
            raise RuntimeError("The proof server did not become ready within 60 seconds.")

        print("Verifying authenticated catalog, artwork, and playback routes...", flush=True)
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
            thread.join(timeout=60)
        FIXTURE_PATH.unlink(missing_ok=True)
        shutil.rmtree(state_root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
