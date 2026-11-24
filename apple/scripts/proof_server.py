#!/usr/bin/env python3
"""Run a disposable, loopback-only Lumina server for native simulator tests.

The server uses the production SQLite schema and import service. It creates
synthetic H.264/AAC videos and an AAC music album, enables the opt-in Jellyfin-compatible
protocol, verifies the client journey, then remains available until Ctrl-C.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import shutil
import socket
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

from proof_server_fixtures import (
    AUDIO_ALBUM,
    AUDIO_ARTIST,
    AUDIO_SECOND_TRACK,
    AUDIO_TRACK,
    AUDIO_TRACK_DURATION_SECONDS,
    AUTOPLAY_CREDITS_START_SECONDS,
    AUTOPLAY_DISABLED_ENV,
    AUTOPLAY_SERIES_ENV,
    CLIENT_DEMUX_VIDEO_FORMAT_RECOVERY_ENV,
    COLLECTIONS_FIXTURE_ENV,
    CONVERTED_TITLE,
    DIRECT_TITLE,
    EPISODE_CREDITS_START_SECONDS,
    EPISODE_INTRO_END_SECONDS,
    EPISODE_TITLE,
    FIRST_EPISODE_NOTE,
    FIRST_EPISODE_OVERVIEW,
    FIRST_EPISODE_SOURCE,
    FOURTH_EPISODE_TITLE,
    FRANCHISE_COLLECTION_TITLE,
    MANUAL_COLLECTION_TITLE,
    MATRIX_TITLE,
    METADATA_REFRESH_METRICS_ENV,
    MUSIC_HOME_FIXTURE_ENV,
    MUSIC_HOME_MEMBER_USERNAME,
    PERFORMANCE_SERIES_COUNT_ENV,
    REPOSITORY_ROOT,
    REQUESTS_METRICS_ENV,
    REQUEST_ANIME_KEY,
    REQUEST_ANIME_TITLE,
    REQUEST_FRESH_ANIME_ID,
    REQUEST_FRESH_ANIME_KEY,
    REQUEST_FRESH_ANIME_TITLE,
    REQUEST_MOVIE_KEY,
    REQUEST_MOVIE_TITLE,
    REQUEST_SHOW_KEY,
    REQUEST_SHOW_TITLE,
    REQUEST_UNAVAILABLE_SHOW_KEY,
    REQUEST_UNAVAILABLE_SHOW_TITLE,
    SECOND_EPISODE_NOTE,
    SECOND_EPISODE_OVERVIEW,
    SECOND_EPISODE_SOURCE,
    SECOND_EPISODE_TITLE,
    SECOND_SEASON_EPISODE_TITLE,
    SERIES_ABOUT_FINAL_PARAGRAPH,
    SERIES_ABOUT_FIRST_NOTE,
    SERIES_ABOUT_FIRST_PARAGRAPH,
    SERIES_ABOUT_SECOND_NOTE,
    SERIES_ABOUT_SOURCE,
    SHARED_MUSIC_PLAYLIST,
    SHOW_TITLE,
    SMART_COLLECTION_TITLE,
    SYNTHETIC_LYRIC,
    THIRD_EPISODE_TITLE,
    VIDEO_FORMAT_FAILURE_ENV,
    fixture_artwork,
    install_requests_catalog_fixture,
    install_showcase_art_fixture,
    make_books,
    make_movie,
    make_music,
    make_music_catalogue,
    make_music_home_media,
    make_performance_catalogue,
    make_performance_posters,
    make_performance_series,
    make_show,
)

from proof_server_metrics import (
    _ArtworkRequestMetrics,
    _PerformanceRequestLog,
    artwork_metrics_path,
    install_artwork_request_metrics,
    install_audio_format_failure,
    install_audio_restore_failure,
    install_initial_video_format_failure,
    install_metadata_refresh_metrics,
    install_navigation_fixture_controls,
    install_performance_request_log,
    install_playback_info_delay,
    install_request_quota_failure,
    install_requests_catalog_metrics,
    install_streaming_fixture,
    performance_catalogue_count,
    performance_poster_variant_count,
    performance_request_log_path,
    performance_video_size,
    playback_info_delay_seconds,
)

PORT = int(os.environ.get("LUMINA_NATIVE_PORT", os.environ.get("LUMINA_NATIVE_PROOF_PORT", os.environ.get("LUMINA_APPLE_PROOF_PORT", "18743"))))
ORIGIN = f"http://127.0.0.1:{PORT}"
FIXTURE_PATH = Path(os.environ.get("LUMINA_NATIVE_FIXTURE", os.environ.get("LUMINA_APPLE_PROOF_FIXTURE", REPOSITORY_ROOT / "apple" / "Tests" / "UITests" / "Fixture.json"))).resolve()
DEVICE_ID = f"apple-proof-{secrets.token_hex(8)}"
AUTH_HEADER = f'MediaBrowser Client="Lumina Apple Proof", Device="Simulator", DeviceId="{DEVICE_ID}", Version="1.0"'


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


def provision(state_root: Path, username: str, password: str) -> dict[str, Any]:
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.db import init_db, session_scope
    from app.models import LibraryItem, MediaRequest, MediaTitle, MusicAcquisition, MusicListeningSession, User, UserSettings
    from app.media_schemas import MediaSegmentInput, SmartCollectionRule
    from app.music_schemas import MusicPlaylistSave
    from app.account_schemas import UserCreateRequest
    from app.services import media_segments
    from app.services import music_playlists
    from app.services import smart_collections
    from app.services.household_collections import HouseholdCollectionService
    from app.services.library_import import LibraryImportService, drive
    from app.services.media_titles import jellyfin_id
    from app.services.storage_roots import StorageRootService
    from app.services.users import UserService
    from app.services.yt_dlp_service import YtDlpService

    init_db()
    with session_scope() as db:
        user = UserService(db).create_initial_admin(username, password, "Apple Proof")
        settings = YtDlpService(db).ensure_app_settings()
        settings.jellyfin_enabled = True
        settings.requests_enabled = True
        root = StorageRootService(db).create(
            label="Apple proof media",
            container_path=str(state_root / "media"),
            mode="external",
        )
        run = LibraryImportService(db).start(root.id, user.id, visibility="shared")
        run_id = run.id

    drive(run_id)
    if os.environ.get("LUMINA_NATIVE_LONG_SERIES") == "1" or os.environ.get("LUMINA_NATIVE_MUSIC_CATALOGUE") == "1":
        # This fixture exceeds the startup probe batch; finish its generated files
        # before API verification instead of racing the background warmer.
        from app.services.media_probe import MediaProbeService
        with session_scope() as db:
            for item in db.query(LibraryItem).all():
                MediaProbeService(db).facts(item)
    if performance_catalogue_count(PERFORMANCE_SERIES_COUNT_ENV):
        # The background probe queue may still be working through thousands of
        # synthetic videos when the fixture verifies its known audio tracks.
        from app.services.media_probe import MediaProbeService
        with session_scope() as db:
            for item in db.query(LibraryItem).filter(LibraryItem.kind.in_(("track", "audio"))):
                MediaProbeService(db).facts(item)
    with session_scope() as db:
        items = {item.title: item.id for item in db.query(LibraryItem).all()}
        titles = {(title.type, title.name): title.id for title in db.query(MediaTitle).all()}
        direct_title = db.query(MediaTitle).filter_by(type="movie", name=DIRECT_TITLE).one()
        show_id = titles[("series", SHOW_TITLE)]
        show_title = db.query(MediaTitle).filter_by(id=show_id).one()
        # Reuse the generated vault titles as exact, authenticated catalog matches.
        # This keeps the proof server offline while Requests follows the same
        # provider-ID status path as a household library.
        direct_title.metadata_json = {**(direct_title.metadata_json or {}), "critic_rating": 88, "imdb_rating": 8.2, "rotten_tomatoes_rating": 91.5, "metacritic_rating": 73}
        direct_title.field_sources = {**(direct_title.field_sources or {}), "critic_rating": "user"}
        direct_title.provider_ids = {**direct_title.provider_ids, "Tmdb": "603"}
        show_title.metadata_json = {**(show_title.metadata_json or {}), "imdb_rating": 6.4,
                                    "rotten_tomatoes_rating": 0, "metacritic_rating": 39.9}
        show_title.field_sources = {**(show_title.field_sources or {}),
                                    **dict.fromkeys(("imdb_rating", "rotten_tomatoes_rating", "metacritic_rating"), "user")}
        show_title.provider_ids = {**show_title.provider_ids, "Tmdb": "1399", "Tvdb": "424536"}
        first_episode_title = db.query(MediaTitle).filter_by(type="episode", name=EPISODE_TITLE).one()
        second_season_episode_title = db.query(MediaTitle).filter_by(
            type="episode", name=SECOND_SEASON_EPISODE_TITLE
        ).one()
        season_id = first_episode_title.parent_id
        second_season_id = second_season_episode_title.parent_id
        if not season_id or not second_season_id or season_id == second_season_id:
            raise RuntimeError("The synthetic show did not import two distinct seasons.")
        first_episode_item = db.query(LibraryItem).filter_by(title_id=first_episode_title.id).one()
        if "NativeServerMediaLanguageTests" in str(FIXTURE_PATH):
            from app.services.media_probe import MediaProbeService
            MediaProbeService(db).facts(first_episode_item)
        media_segments.save_user_segments(db, first_episode_item.id, [
            MediaSegmentInput(type="intro", start_seconds=0, end_seconds=EPISODE_INTRO_END_SECONDS),
            MediaSegmentInput(type="credits", start_seconds=EPISODE_CREDITS_START_SECONDS, end_seconds=90),
        ])
        third_episode_title = None
        fourth_episode_title = None
        if os.environ.get(AUTOPLAY_SERIES_ENV) == "1":
            second_episode_title = db.query(MediaTitle).filter_by(type="episode", name=SECOND_EPISODE_TITLE).one()
            third_episode_title = db.query(MediaTitle).filter_by(type="episode", name=THIRD_EPISODE_TITLE).one()
            fourth_episode_title = db.query(MediaTitle).filter_by(type="episode", name=FOURTH_EPISODE_TITLE).one()
            for title in (second_episode_title, third_episode_title):
                item = db.query(LibraryItem).filter_by(title_id=title.id).one()
                media_segments.save_user_segments(db, item.id, [
                    MediaSegmentInput(type="credits", start_seconds=AUTOPLAY_CREDITS_START_SECONDS, end_seconds=90),
                ])
        owner = db.query(User).filter_by(username=username).one()
        streaming_fixture: dict[str, str] = {}
        if os.environ.get("LUMINA_NATIVE_STREAMING_PROOF") == "1":
            owner.onboarding_status = "completed"
            from app.models import LibraryItemArtifact
            source = db.get(LibraryItem, items[DIRECT_TITLE])
            artifact = db.get(LibraryItemArtifact, source.id)
            for provider, title, remote_id in (
                ("Youtube", "Saved Observatory on YouTube", "proof-saved-youtube"),
                ("TwitchVod", "Saved Observatory on Twitch", "proof-saved-twitch"),
            ):
                saved = LibraryItem(
                    id=str(uuid.uuid4()), user_id=owner.id, visibility="shared", kind="video",
                    extractor=provider, remote_id=remote_id, title=title, uploader="Observatory Studio",
                    duration=source.duration, file_path=source.file_path, file_size=source.file_size,
                    status="available", metadata_json=dict(source.metadata_json or {}),
                    webpage_url=("https://www.youtube.com/watch?v=proofsaved1" if provider == "Youtube"
                                 else "https://www.twitch.tv/videos/900100"),
                )
                db.add(saved)
                if artifact:
                    db.add(LibraryItemArtifact(library_item_id=saved.id, artifact_id=artifact.artifact_id))
                streaming_fixture["savedYouTubeID" if provider == "Youtube" else "savedTwitchID"] = uuid.UUID(saved.id).hex
        if os.environ.get(AUTOPLAY_DISABLED_ENV) == "1":
            preferences = db.query(UserSettings).filter_by(user_id=owner.id).one()
            preferences.ui_prefs = {**(preferences.ui_prefs or {}), "autoplay_up_next": False}
        db.add(MediaRequest(
            id=str(uuid.uuid4()), kind="anime", media_type="tv", tmdb_id=209867,
            tvdb_id=424536, anilist_id=154587, title=REQUEST_ANIME_TITLE,
            year=2023, seasons=[1], language="sub", status="partially_available",
            requested_by=owner.id, library_title_id=show_id,
        ))
        curator = UserService(db).create_member_without_password(owner, "observatory-curator", "Observatory curator")
        shared_playlist = music_playlists.save(db, curator, MusicPlaylistSave(
            name=SHARED_MUSIC_PLAYLIST, shared=True, item_ids=[items[AUDIO_TRACK], items[AUDIO_SECOND_TRACK]]))
        music_home_fixture: dict[str, str] = {}
        if os.environ.get(MUSIC_HOME_FIXTURE_ENV) == "1":
            from e2e.music_fixture import (
                MUSIC_HOME_MULTIDISC_ALBUM,
                MUSIC_HOME_PRIVATE_ALBUM,
                MUSIC_HOME_PRIVATE_TRACK,
                MUSIC_HOME_UNTAGGED_TITLE,
                MUSIC_HOME_WAIT_ALBUM,
            )

            member_password = f"Fixture-{secrets.token_urlsafe(18)}"
            member = UserService(db).create_user(UserCreateRequest(
                username=MUSIC_HOME_MEMBER_USERNAME, password=member_password,
                display_name="Music Home member",
            ))
            private_item = db.query(LibraryItem).filter_by(title=MUSIC_HOME_PRIVATE_TRACK).one()
            private_item.user_id = member.id
            private_item.visibility = "private"
            wait_request_id = str(uuid.uuid4())
            db.add(MusicAcquisition(
                id=wait_request_id, user_id=owner.id, connector_id=str(uuid.uuid4()),
                foreign_artist_id=str(uuid.uuid4()), foreign_album_id=str(uuid.uuid4()),
                artist_name="Fixture Artist", album_name=MUSIC_HOME_WAIT_ALBUM, status="awaiting_import",
            ))
            db.add(MusicListeningSession(
                user_id=owner.id, revision=1,
                item_ids=[items[AUDIO_TRACK], items[AUDIO_TRACK]],
                queue_entries=[
                    {"kind": "track", "item_id": items[AUDIO_TRACK]},
                    {"kind": "track", "item_id": items[AUDIO_TRACK]},
                    {"kind": "acquisition", "request_id": wait_request_id},
                    {"kind": "track", "item_id": items[AUDIO_SECOND_TRACK]},
                ],
                entry_index=0, unavailable_policy="wait", current_index=0, position_seconds=23,
                repeat="off", shuffle=False, active_output={"kind": "browser"},
            ))
            music_home_fixture = {
                "musicHomeMemberUsername": MUSIC_HOME_MEMBER_USERNAME,
                "musicHomeMemberPassword": member_password,
                "musicHomePrivateAlbumID": uuid.UUID(titles[("album", MUSIC_HOME_PRIVATE_ALBUM)]).hex,
                "musicHomePrivateTrackID": uuid.UUID(private_item.id).hex,
                "musicHomeMultidiscAlbumID": uuid.UUID(titles[("album", MUSIC_HOME_MULTIDISC_ALBUM)]).hex,
                "musicHomeWaitRequestID": wait_request_id,
                "musicHomeUntaggedTrackID": uuid.UUID(items[MUSIC_HOME_UNTAGGED_TITLE]).hex,
            }
        book_items = make_books(state_root, db, owner)
        collection_fixture: dict[str, str] = {}
        if os.environ.get(COLLECTIONS_FIXTURE_ENV) == "1":
            boxset = MediaTitle(
                id=str(uuid.uuid4()), type="boxset", key="proof:boxset:lantern-keepers",
                name=FRANCHISE_COLLECTION_TITLE, provider_ids={"Tmdb": "9001"},
                field_sources={"name": "user", "provider_ids": "user"},
                root_id=direct_title.root_id, images=dict(direct_title.images or {}),
                metadata_json={"overview": "The Lantern Keepers film collection."},
            )
            db.add(boxset)
            direct_title.boxset_id = boxset.id

            collections = HouseholdCollectionService(db)
            manual = collections.create(owner_user_id=owner.id, name=MANUAL_COLLECTION_TITLE, visibility="shared")
            collections.add_item(member_user_id=owner.id, collection_id=manual.id, library_item_id=items[DIRECT_TITLE])
            smart_rule = SmartCollectionRule(type="movie", conditions=[{"field": "provider", "op": "is", "value": "Tmdb"}])
            smart_collections.validate_rule(smart_rule)
            smart = collections.create(
                owner_user_id=owner.id, name=SMART_COLLECTION_TITLE, visibility="shared",
                rules=smart_rule.model_dump(mode="json"),
            )
            collection_fixture = {
                "franchiseCollectionID": jellyfin_id(boxset.id),
                "franchiseCollectionTitle": FRANCHISE_COLLECTION_TITLE,
                "manualCollectionID": jellyfin_id(manual.id),
                "manualCollectionTitle": MANUAL_COLLECTION_TITLE,
                "smartCollectionID": jellyfin_id(smart.id),
                "smartCollectionTitle": SMART_COLLECTION_TITLE,
            }
    try:
        return {
            **book_items,
            **music_home_fixture,
            **collection_fixture,
            **streaming_fixture,
            "streamingRecommendationID": hashlib.sha256(b"https://www.youtube.com/watch?v=proofremote").hexdigest()[:24],
            "streamingLiveID": hashlib.sha256(b"https://www.twitch.tv/observatory").hexdigest()[:24],
            "directItemID": items[DIRECT_TITLE],
            "directItemTitle": DIRECT_TITLE,
            "convertedItemID": items[CONVERTED_TITLE],
            "convertedItemTitle": CONVERTED_TITLE,
            "matrixItemID": items[MATRIX_TITLE],
            "matrixItemTitle": MATRIX_TITLE,
            "showID": uuid.UUID(show_id).hex,
            "showTitle": SHOW_TITLE,
            "seriesAboutFirstParagraph": SERIES_ABOUT_FIRST_PARAGRAPH,
            "seriesAboutFinalParagraph": SERIES_ABOUT_FINAL_PARAGRAPH,
            "seriesAboutSource": SERIES_ABOUT_SOURCE,
            "seriesAboutFirstNote": SERIES_ABOUT_FIRST_NOTE,
            "seriesAboutSecondNote": SERIES_ABOUT_SECOND_NOTE,
            "seasonID": uuid.UUID(season_id).hex,
            "secondSeasonID": uuid.UUID(second_season_id).hex,
            "episodeID": uuid.UUID(titles[("episode", EPISODE_TITLE)]).hex,
            "episodeTitle": EPISODE_TITLE,
            "episodeAbout": FIRST_EPISODE_OVERVIEW,
            "episodeAboutSource": FIRST_EPISODE_SOURCE,
            "episodeAboutNote": FIRST_EPISODE_NOTE,
            "secondEpisodeID": uuid.UUID(titles[("episode", SECOND_EPISODE_TITLE)]).hex,
            "secondEpisodeTitle": SECOND_EPISODE_TITLE,
            "secondEpisodeAbout": SECOND_EPISODE_OVERVIEW,
            "secondEpisodeAboutSource": SECOND_EPISODE_SOURCE,
            "secondEpisodeAboutNote": SECOND_EPISODE_NOTE,
            "thirdEpisodeID": uuid.UUID(third_episode_title.id).hex if third_episode_title else None,
            "thirdEpisodeTitle": THIRD_EPISODE_TITLE if third_episode_title else None,
            "fourthEpisodeID": uuid.UUID(fourth_episode_title.id).hex if fourth_episode_title else None,
            "fourthEpisodeTitle": FOURTH_EPISODE_TITLE if fourth_episode_title else None,
            "secondSeasonEpisodeID": uuid.UUID(second_season_episode_title.id).hex,
            "secondSeasonEpisodeTitle": SECOND_SEASON_EPISODE_TITLE,
            "episodeIntroEndSeconds": EPISODE_INTRO_END_SECONDS,
            "episodeCreditsStartSeconds": EPISODE_CREDITS_START_SECONDS,
            "audioAlbumID": uuid.UUID(titles[("album", AUDIO_ALBUM)]).hex,
            "audioAlbumTitle": AUDIO_ALBUM,
            "audioPaginationAlbumID": uuid.UUID(titles[("album", "ZZ Music Album 055")]).hex
                if ("album", "ZZ Music Album 055") in titles else None,
            "audioArtistID": uuid.UUID(titles[("artist", AUDIO_ARTIST)]).hex,
            "audioTrackID": uuid.UUID(items[AUDIO_TRACK]).hex,
            "audioTrackTitle": AUDIO_TRACK,
            "audioTrackDurationSeconds": AUDIO_TRACK_DURATION_SECONDS,
            "audioSecondTrackID": uuid.UUID(items[AUDIO_SECOND_TRACK]).hex,
            "audioSecondTrackTitle": AUDIO_SECOND_TRACK,
            "sharedMusicPlaylistID": shared_playlist["id"],
            "sharedMusicPlaylistTitle": SHARED_MUSIC_PLAYLIST,
            "audioLyric": SYNTHETIC_LYRIC,
            "requestMovieKey": REQUEST_MOVIE_KEY,
            "requestMovieTitle": REQUEST_MOVIE_TITLE,
            "requestShowKey": REQUEST_SHOW_KEY,
            "requestShowTitle": REQUEST_SHOW_TITLE,
            "requestUnavailableShowKey": REQUEST_UNAVAILABLE_SHOW_KEY,
            "requestUnavailableShowTitle": REQUEST_UNAVAILABLE_SHOW_TITLE,
            "requestAnimeKey": REQUEST_ANIME_KEY,
            "requestAnimeTitle": REQUEST_ANIME_TITLE,
            "requestFreshAnimeKey": REQUEST_FRESH_ANIME_KEY,
            "requestFreshAnimeTitle": REQUEST_FRESH_ANIME_TITLE,
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


def verify_api(expected: dict[str, Any], username: str, password: str) -> dict[str, Any]:
    info = request_json("/System/Info/Public")
    if info.get("ServerName") != "Lumina":
        raise RuntimeError("The Jellyfin-compatible public-info endpoint did not identify Lumina.")

    signed_in = request_json("/Users/AuthenticateByName", method="POST", payload={"Username": username, "Pw": password})
    token = signed_in.get("AccessToken")
    if not isinstance(token, str) or not token:
        raise RuntimeError("The Jellyfin-compatible sign-in did not return an access token.")

    request_home = request_json("/Lumina/Requests/catalog/home", token=token)
    request_keys = {
        item.get("key")
        for rail in request_home.get("rails", []) if isinstance(rail, dict)
        for item in rail.get("items", []) if isinstance(item, dict)
    }
    if not {REQUEST_MOVIE_KEY, REQUEST_SHOW_KEY, REQUEST_ANIME_KEY}.issubset(request_keys):
        raise RuntimeError("The native Requests catalog did not expose the proof titles.")
    request_movie = next(
        (
            item
            for rail in request_home.get("rails", []) if isinstance(rail, dict)
            for item in rail.get("items", []) if isinstance(item, dict) and item.get("key") == REQUEST_MOVIE_KEY
        ),
        None,
    )
    request_art_url = request_movie.get("backdrop_url") if isinstance(request_movie, dict) else None
    if not isinstance(request_art_url, str) or not request_art_url.startswith(f"{ORIGIN}/proof/requests-art/"):
        raise RuntimeError("The native Requests fixture did not rewrite catalog artwork to its loopback image route.")
    with urlopen(request_art_url, timeout=60) as response:
        if response.status != 200 or not response.headers.get_content_type().startswith("image/") or not response.read(1):
            raise RuntimeError("The native Requests fixture artwork route did not return image bytes.")
    request_statuses = {
        item.get("key"): item.get("status")
        for rail in request_home.get("rails", []) if isinstance(rail, dict)
        for item in rail.get("items", []) if isinstance(item, dict)
    }
    for key in (REQUEST_MOVIE_KEY, REQUEST_SHOW_KEY, REQUEST_ANIME_KEY):
        status = request_statuses.get(key)
        if not isinstance(status, dict) or not status.get("library_title_id"):
            raise RuntimeError(f"The native Requests catalog did not link the proof vault title for {key}.")
    unavailable_status = request_statuses.get(REQUEST_UNAVAILABLE_SHOW_KEY)
    if unavailable_status != {"state": "none"}:
        raise RuntimeError("The native Requests catalog lost its unlinked series request fixture.")
    request_search = request_json("/Lumina/Requests/catalog/search?q=Frieren&page=1", token=token)
    if not any(item.get("key") == REQUEST_ANIME_KEY for item in request_search.get("items", [])):
        raise RuntimeError("The native Requests search did not expose the anime proof title.")
    fresh_anime_search = request_json(
        f"/Lumina/Requests/catalog/search?{urlencode({'q': REQUEST_FRESH_ANIME_TITLE, 'page': 1})}", token=token,
    )
    fresh_anime = next((item for item in fresh_anime_search.get("items", []) if item.get("key") == REQUEST_FRESH_ANIME_KEY), None)
    if not isinstance(fresh_anime, dict) or fresh_anime.get("status", {}).get("state") != "none":
        raise RuntimeError("The fresh anime request fixture was not search-reachable without a seeded request.")
    request_history = request_json("/Lumina/Requests", token=token)
    history_items = [item for item in request_history.get("items", []) if isinstance(item, dict)]
    if any(item.get("key") == REQUEST_FRESH_ANIME_KEY for item in history_items):
        raise RuntimeError("The fresh anime request fixture unexpectedly has a seeded request.")
    seeded_anime = next((item for item in history_items if item.get("key") == REQUEST_ANIME_KEY), None)
    if not isinstance(seeded_anime, dict) or seeded_anime.get("seasons") != [1] or seeded_anime.get("language") != "sub":
        raise RuntimeError("The seeded partial anime request fixture lost its Season 1 subtitle request.")
    request_quota = request_json("/Lumina/Requests/quota", token=token)
    if not any(row.get("kind") == "anime" for row in request_quota.get("quotas", []) if isinstance(row, dict)):
        raise RuntimeError("The native Requests quota route did not expose the anime policy.")
    request_show = request_json("/Lumina/Requests/catalog/title/show/1399", token=token)
    if not any(row.get("number") == 1 for row in request_show.get("seasons", []) if isinstance(row, dict)):
        raise RuntimeError("The native Requests title route did not expose the show season selector.")
    request_unlinked_show = request_json("/Lumina/Requests/catalog/title/show/95396", token=token)
    if request_unlinked_show.get("key") != REQUEST_UNAVAILABLE_SHOW_KEY or request_unlinked_show.get("status", {}).get("state") != "none":
        raise RuntimeError("The unlinked Requests show fixture did not retain its requestable identity.")
    if request_unlinked_show.get("tvdb_id") != 371980 or not any(
        row.get("number") == 1 for row in request_unlinked_show.get("seasons", []) if isinstance(row, dict)
    ):
        raise RuntimeError("The unlinked Requests show fixture did not expose provider metadata and seasons.")
    request_anime = request_json("/Lumina/Requests/catalog/title/anime/154587", token=token)
    if request_anime.get("key") != REQUEST_ANIME_KEY or request_anime.get("tvdb_id") != 424536:
        raise RuntimeError("The native Requests title route did not preserve anime request metadata.")
    if request_anime.get("status", {}).get("state") != "partially_available" or request_anime.get("status", {}).get("library_title_id") is None:
        raise RuntimeError("The seeded partial anime request fixture lost its vault and partial-coverage state.")
    if "NativeServerMediaLanguageTests" in str(FIXTURE_PATH):
        files = request_anime["status"].get("language_availability", {}).get("files", [])
        if not any(
            file.get("season") == 1 and file.get("episode") == 1
            and file.get("audio_languages") == ["ja"] and file.get("subtitle_languages") == ["en"]
            and file.get("audio_known") is True and file.get("subtitles_known") is True
            for file in files
        ):
            raise RuntimeError("Language proof requires Japanese audio and English subtitles on the same Season 1 Episode 1 file.")
    fresh_anime_detail = request_json(f"/Lumina/Requests/catalog/title/anime/{REQUEST_FRESH_ANIME_ID}", token=token)
    if fresh_anime_detail.get("key") != REQUEST_FRESH_ANIME_KEY or fresh_anime_detail.get("status", {}).get("state") != "none":
        raise RuntimeError("The fresh anime request fixture did not retain a distinct unrequested identity.")
    if not any(row.get("number") == 1 for row in fresh_anime_detail.get("seasons", []) if isinstance(row, dict)):
        raise RuntimeError("The fresh anime request fixture did not expose its first-season selector.")

    showcase = request_json("/api/public/showcase")
    slides = showcase.get("slides", [])
    if not slides or not slides[0].get("backdrop_url", "").startswith("/api/public/showcase/art/"):
        raise RuntimeError("The public welcome fixture did not expose local artwork.")
    with urlopen(ORIGIN + slides[0]["backdrop_url"], timeout=60) as response:
        if not response.headers.get("Content-Type", "").startswith("image/") or not response.read():
            raise RuntimeError("The public welcome fixture artwork did not load.")

    items = request_json("/Items?IncludeItemTypes=Movie,Video&Recursive=true&Fields=Overview,UserData,ImageTags&SortBy=SortName&StartIndex=0&Limit=50", token=token)
    by_name = {item.get("Name"): item for item in items.get("Items", [])}
    direct = by_name.get(DIRECT_TITLE)
    converted = by_name.get(CONVERTED_TITLE)
    matrix = by_name.get(MATRIX_TITLE)
    if not isinstance(direct, dict) or not isinstance(converted, dict) or not isinstance(matrix, dict):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all imported videos.")
    if any(not item.get("ImageTags", {}).get("Primary") for item in (direct, converted, matrix)):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all synthetic posters.")
    if not direct.get("ImageTags", {}).get("Logo") or converted.get("ImageTags", {}).get("Logo"):
        raise RuntimeError("The direct proof title must expose a logo while the converted title remains a text fallback.")

    franchise_id = expected.get("franchiseCollectionID")
    if franchise_id:
        boxsets = request_json("/Items?IncludeItemTypes=BoxSet&Recursive=true&Limit=50", token=token)
        franchises = {item.get("Id"): item for item in boxsets.get("Items", []) if isinstance(item, dict)}
        if franchises.get(franchise_id, {}).get("Name") != expected.get("franchiseCollectionTitle"):
            raise RuntimeError("The native Collections fixture did not expose its synthetic movie franchise.")
        franchise_movies = request_json(
            f"/Items?ParentId={franchise_id}&IncludeItemTypes=Movie&Limit=50", token=token,
        )
        if direct.get("Id") not in {item.get("Id") for item in franchise_movies.get("Items", [])}:
            raise RuntimeError("The native Collections fixture did not expose the franchise movie.")
        views = request_json("/UserViews", token=token)
        playlists_view = next(
            (item for item in views.get("Items", []) if isinstance(item, dict) and item.get("CollectionType") == "playlists"),
            None,
        )
        if not isinstance(playlists_view, dict) or not isinstance(playlists_view.get("Id"), str):
            raise RuntimeError("The native Collections fixture did not expose the household playlists view.")
        household = request_json(
            f"/Items?ParentId={playlists_view['Id']}&IncludeItemTypes=Playlist&Limit=50", token=token,
        )
        listed = {item.get("Id"): item.get("Name") for item in household.get("Items", []) if isinstance(item, dict)}
        for identifier, title in (
            (expected.get("manualCollectionID"), expected.get("manualCollectionTitle")),
            (expected.get("smartCollectionID"), expected.get("smartCollectionTitle")),
        ):
            if not isinstance(identifier, str) or listed.get(identifier) != title:
                raise RuntimeError("The native Collections fixture did not expose its household manual and smart lists.")
            entries = request_json(
                f"/Items?ParentId={identifier}&IncludeItemTypes=Movie,Episode,Video&Limit=50", token=token,
            )
            if direct.get("Id") not in {item.get("Id") for item in entries.get("Items", [])}:
                raise RuntimeError("The native Collections fixture did not expose the household list movie.")

    direct_detail = request_json(f"/Users/{signed_in['User']['Id']}/Items/{direct['Id']}", token=token)
    if direct_detail.get("Id") != direct["Id"] or not direct_detail.get("ImageTags", {}).get("Logo"):
        raise RuntimeError("The authenticated fresh-item route did not preserve the direct title logo tag.")

    for item in (direct, converted, matrix):
        artwork_url = ORIGIN + f"/Items/{item['Id']}/Images/Primary?" + urlencode({"maxWidth": "400", "ApiKey": token})
        with urlopen(Request(artwork_url), timeout=60) as response:
            if not response.headers.get_content_type().startswith("image/") or not response.read(1):
                raise RuntimeError("A synthetic poster did not load through the authenticated artwork route.")
    logo_url = ORIGIN + f"/Items/{direct['Id']}/Images/Logo?" + urlencode({"maxWidth": "600", "ApiKey": token})
    with urlopen(Request(logo_url), timeout=60) as response:
        if response.status != 200 or not response.headers.get_content_type().startswith("image/") or not response.read(1):
            raise RuntimeError("The synthetic title logo did not load through the authenticated artwork route.")

    shows = request_json("/Items?IncludeItemTypes=Series&Recursive=true&Limit=50", token=token)
    if expected["showID"] not in {item.get("Id") for item in shows.get("Items", [])}:
        raise RuntimeError("The imported synthetic show was absent from the public catalog.")
    seasons = request_json(f"/Shows/{expected['showID']}/Seasons", token=token)
    if not {expected["seasonID"], expected["secondSeasonID"]}.issubset({item.get("Id") for item in seasons.get("Items", [])}):
        raise RuntimeError("The synthetic show did not expose both imported seasons.")
    episodes = request_json(f"/Items?ParentId={expected['seasonID']}&IncludeItemTypes=Episode&Limit=50", token=token)
    expected_season_episode_ids = {expected["episodeID"], expected["secondEpisodeID"]}
    expected_season_episode_ids.update(item for item in (expected.get("thirdEpisodeID"), expected.get("fourthEpisodeID")) if item)
    if not expected_season_episode_ids.issubset({item.get("Id") for item in episodes.get("Items", [])}):
        raise RuntimeError("The synthetic season did not expose both imported episodes.")
    second_season_episodes = request_json(f"/Items?ParentId={expected['secondSeasonID']}&IncludeItemTypes=Episode&Limit=50", token=token)
    if expected["secondSeasonEpisodeID"] not in {item.get("Id") for item in second_season_episodes.get("Items", [])}:
        raise RuntimeError("The synthetic second season did not expose its episode.")
    segments = request_json(f"/MediaSegments/{expected['episodeID']}", token=token)
    expected_segments = [("Intro", 0, EPISODE_INTRO_END_SECONDS * 10_000_000),
                         ("Outro", EPISODE_CREDITS_START_SECONDS * 10_000_000, 900_000_000)]
    actual_segments = [(item.get("Type"), item.get("StartTicks"), item.get("EndTicks"))
                       for item in segments.get("Items", []) if isinstance(item, dict)]
    if actual_segments != expected_segments:
        raise RuntimeError("The synthetic first episode did not expose its authoritative intro and credits segments.")

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
    if track.get("RunTimeTicks") != expected["audioTrackDurationSeconds"] * 10_000_000:
        raise RuntimeError("The synthetic audio track did not retain its advertised duration.")
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
    if "musicHomePrivateTrackID" in expected:
        session = request_json("/Lumina/Music/session", token=token)
        entries = session.get("entries")
        if session.get("entry_index") != 0 or session.get("unavailable_policy") != "wait" or not isinstance(entries, list):
            raise RuntimeError("The Music Home fixture did not retain its selected wait-policy queue.")
        expected_states = [("track", "playable"), ("track", "playable"), ("acquisition", "awaiting_import"), ("track", "playable")]
        if [(entry.get("kind"), entry.get("state")) for entry in entries] != expected_states:
            raise RuntimeError("The Music Home fixture did not retain duplicate tracks and its Wait boundary.")
        if entries[2].get("request_id") != expected["musicHomeWaitRequestID"]:
            raise RuntimeError("The Music Home fixture did not retain its pending acquisition identity.")
        if [str(track.get("id", "")).replace("-", "") for track in session.get("queue", [])] != [expected["audioTrackID"], expected["audioTrackID"]]:
            raise RuntimeError("The Music Home fixture did not stop its playable queue at the Wait boundary.")

        def require_not_found(path: str) -> None:
            try:
                request_json(path, token=token)
            except RuntimeError as error:
                if f"GET {path} returned 404:" not in str(error):
                    raise
            else:
                raise RuntimeError(f"The Music Home owner unexpectedly resolved private media at {path}.")

        require_not_found(f"/Lumina/Music/tracks/{expected['musicHomePrivateTrackID']}")
        require_not_found(f"/Items/{expected['musicHomePrivateAlbumID']}")
        member = request_json("/Users/AuthenticateByName", method="POST", payload={
            "Username": expected["musicHomeMemberUsername"], "Pw": expected["musicHomeMemberPassword"],
        })
        member_token = member.get("AccessToken")
        if not isinstance(member_token, str):
            raise RuntimeError("The Music Home member could not authenticate.")
        private_track = request_json(f"/Lumina/Music/tracks/{expected['musicHomePrivateTrackID']}", token=member_token)
        if private_track.get("title") != "Private Lantern Draft":
            raise RuntimeError("The Music Home member could not read its private track.")
        discs = request_json(
            f"/Items?ParentId={expected['musicHomeMultidiscAlbumID']}&IncludeItemTypes=Audio&Limit=50", token=token,
        ).get("Items", [])
        if [(track.get("ParentIndexNumber"), track.get("IndexNumber")) for track in discs] != [(1, 1), (2, 1)]:
            raise RuntimeError("The Music Home fixture did not retain ordered multidisc tracks.")
        untagged = request_json(f"/Lumina/Music/tracks/{expected['musicHomeUntaggedTrackID']}", token=token)
        if untagged.get("title") != "Untagged Demo":
            raise RuntimeError("The Music Home fixture did not retain the untagged file fallback title.")
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
    converted_source = next((candidate for candidate in converted_playback.get("MediaSources", [])
                             if isinstance(candidate.get("Id"), str)), None)
    if not isinstance(converted_source, dict):
        raise RuntimeError("The compatibility fixture did not expose its original media source.")

    capped_profile = {
        **profile,
        "DirectPlayProfiles": [
            {"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264", "AudioCodec": "aac"},
            {"Type": "Video", "Container": "mkv", "VideoCodec": "h264", "AudioCodec": "aac"},
        ],
        "CodecProfiles": [{
            "Type": "Video", "Codec": "h264",
            "Conditions": [{"Property": "Height", "Condition": "LessThanEqual", "Value": "480"}],
        }],
        "MaxStreamingBitrate": 1_500_000,
    }
    capped_playback = request_json(
        f"/Items/{converted['Id']}/PlaybackInfo",
        method="POST",
        token=token,
        payload={"DeviceProfile": capped_profile, "EnableDirectPlay": True, "MaxStreamingBitrate": 1_500_000},
    )
    if not any(candidate.get("SupportsTranscoding") and ".m3u8" in str(candidate.get("TranscodingUrl") or "")
               for candidate in capped_playback.get("MediaSources", [])):
        raise RuntimeError("The 480p proof profile did not negotiate HLS for the high-resolution subtitle fixture.")

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
        "directMediaSourceID": str(source["Id"]),
        "convertedItemID": str(converted["Id"]),
        "convertedItemTitle": CONVERTED_TITLE,
        "convertedMediaSourceID": str(converted_source["Id"]),
        "matrixItemID": str(matrix["Id"]),
        "matrixItemTitle": MATRIX_TITLE,
    }


def write_fixture(state_root: Path, username: str, password: str, items: dict[str, Any]) -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete JSON, with private permissions before credentials are written.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=FIXTURE_PATH.parent, delete=False) as pending:
        temporary = Path(pending.name)
        try:
            json.dump({"serverURL": ORIGIN, "username": username, "password": password, **items, "stateRoot": str(state_root)}, pending, indent=2)
            pending.write("\n")
            pending.close()
            temporary.replace(FIXTURE_PATH)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    global PORT, ORIGIN
    # Reserve the port until Uvicorn takes ownership; parallel fixtures cannot race.
    listener = socket.socket()
    listener.bind(("127.0.0.1", PORT))
    listener.listen(128)
    PORT = listener.getsockname()[1]
    ORIGIN = f"http://127.0.0.1:{PORT}"
    def terminate(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
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
    performance_request_log: _PerformanceRequestLog | None = None
    artwork_metrics: _ArtworkRequestMetrics | None = None
    try:
        playback_info_delay = playback_info_delay_seconds()
        artwork = fixture_artwork()
        performance_catalogue = performance_catalogue_count()
        performance_series = performance_catalogue_count(PERFORMANCE_SERIES_COUNT_ENV)
        performance_poster_variants = performance_poster_variant_count()
        perf_video_size = performance_video_size() if performance_catalogue else "320x180"
        # Default sidecars are generated locally; optional original PNGs are copied unchanged.
        print("Generating synthetic proof video and music media...", flush=True)
        make_movie(state_root / "media", DIRECT_TITLE, 2026, ".mp4", "360x540", artwork=artwork.get("lanterns.png"),
                   video_size=perf_video_size, title_logo=True)
        # This fixture must physically exceed the 480p quality rung: its poster
        # dimensions are unrelated to the imported video stream's dimensions.
        make_movie(state_root / "media", CONVERTED_TITLE, 2025, ".mkv", "960x540", artwork=artwork.get("cartographer.png"),
                   embedded_subtitle=True, embedded_chapters=True, video_size="960x540")
        make_movie(state_root / "media", MATRIX_TITLE, 2024, ".mp4", "360x540", duration=40, artwork=artwork.get("observatory.png"))
        performance_posters = make_performance_posters(state_root / "media", performance_poster_variants)
        if performance_posters:
            print(f"Adding {len(performance_posters)} synthetic performance poster variants...", flush=True)
        if performance_catalogue:
            print(f"Adding {performance_catalogue} synthetic performance-catalog movies...", flush=True)
            make_performance_catalogue(state_root / "media", performance_catalogue, performance_posters)
        make_music(state_root / "media", artwork=artwork.get("observatory.png"))
        if os.environ.get(MUSIC_HOME_FIXTURE_ENV) == "1":
            make_music_home_media(state_root / "media")
        if os.environ.get("LUMINA_NATIVE_MUSIC_CATALOGUE") == "1":
            make_music_catalogue(state_root / "media")
        make_show(state_root / "media", media_language_proof="NativeServerMediaLanguageTests" in str(FIXTURE_PATH))
        if performance_series:
            print(f"Adding {performance_series} synthetic performance-catalog series...", flush=True)
            make_performance_series(state_root / "media", performance_series, performance_posters)
        configure_environment(state_root)
        install_loopback_guard()
        install_requests_catalog_fixture()
        print("Importing the disposable video and music catalog...", flush=True)
        provisioned = provision(state_root, username, password)

        import uvicorn
        from app.main import app
        install_showcase_art_fixture(state_root, ORIGIN)
        quota_failure_proof = FIXTURE_PATH.stem.split("-", 1)[0] == "NativeTVRequestQuotaRetryTests"
        if quota_failure_proof:
            install_request_quota_failure(app)
        if os.environ.get("LUMINA_NATIVE_STREAMING_PROOF") == "1":
            install_streaming_fixture(app, provisioned)
        if "NativeServerNavigationTests" in str(FIXTURE_PATH):
            install_navigation_fixture_controls(app)
        install_playback_info_delay(app, playback_info_delay)
        if os.environ.get("LUMINA_NATIVE_AUDIO_FORMAT_FAILURE") == "1":
            install_audio_format_failure(app)
        activate_video_format_failure = None
        if os.environ.get(VIDEO_FORMAT_FAILURE_ENV) == "1":
            activate_video_format_failure = install_initial_video_format_failure(app)
        activate_restore_failure = None
        if os.environ.get("LUMINA_NATIVE_AUDIO_RESTORE_FAILURE") == "1":
            activate_restore_failure = install_audio_restore_failure(app, provisioned["audioTrackID"])
        if (performance_catalogue or performance_series) and (request_log_path := performance_request_log_path()) is not None:
            performance_request_log = install_performance_request_log(app, request_log_path)
        if (metrics_path := artwork_metrics_path()) is not None:
            artwork_metrics = install_artwork_request_metrics(app, metrics_path)
        if os.environ.get(REQUESTS_METRICS_ENV) == "1":
            install_requests_catalog_metrics(app)
        if os.environ.get(METADATA_REFRESH_METRICS_ENV) == "1":
            install_metadata_refresh_metrics(app)

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", timeout_keep_alive=30))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), name="lumina-apple-proof", daemon=True)
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
        if quota_failure_proof:
            verified["requestQuotaOutageAvailable"] = True
        if activate_video_format_failure is not None:
            client_demux_target = os.environ.get(CLIENT_DEMUX_VIDEO_FORMAT_RECOVERY_ENV) == "1"
            target = verified["convertedItemID"] if client_demux_target else verified["directItemID"]
            source = verified["convertedMediaSourceID"] if client_demux_target else verified["directMediaSourceID"]
            activate_video_format_failure(target, source)
        if activate_restore_failure is not None:
            activate_restore_failure()
        if performance_request_log is not None:
            performance_request_log.activate()
        if artwork_metrics is not None:
            artwork_metrics.activate()
        if performance_catalogue == 997 and performance_series == 1999:
            verified["largeVideoCatalog"] = True
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
        if performance_request_log is not None:
            performance_request_log.close()
        if artwork_metrics is not None:
            artwork_metrics.close()
        listener.close()
        FIXTURE_PATH.unlink(missing_ok=True)
        shutil.rmtree(state_root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
