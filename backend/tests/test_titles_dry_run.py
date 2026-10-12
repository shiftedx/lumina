"""The real-library dry run works on a tiny library and prints nothing personal."""
from __future__ import annotations

import importlib.util
import json
import random
import uuid
from pathlib import Path

import pytest

from discovery_support import add_movie, add_title, add_version
from support import file_backed_session_factory, seed_app_settings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "titles_dry_run.py"


def dry_run():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("titles_dry_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p95_is_the_nearest_rank() -> None:
    module = dry_run()
    assert module.p95([float(value) for value in range(1, 21)]) == 19.0
    assert module.p95([5.0]) == 5.0


def test_it_refuses_anything_but_a_copy_under_the_temp_directory(tmp_path: Path) -> None:
    module = dry_run()
    assert module.check_copy(tmp_path) == tmp_path.resolve()
    with pytest.raises(SystemExit):
        module.check_copy(Path("/"))


def test_it_times_every_endpoint_as_each_member_without_naming_anything() -> None:
    factory = file_backed_session_factory("owner", "alice")
    # Routes take uuid ids only (parse_item_id), so the series is built here rather than with add_series.
    movie, series, season, *episodes = (str(uuid.UUID(int=number)) for number in range(1, 7))
    anime, artist, album = (str(uuid.UUID(int=number)) for number in range(7, 10))
    with factory() as session:
        add_movie(session, movie, "Secret Movie Name", genres=["Drama"])
        add_title(session, series, "series", "Hidden Show")
        add_title(session, season, "season", "Season 1", parent_id=series, index=1)
        for number, episode in enumerate(episodes, 1):
            add_title(session, episode, "episode", f"Hidden Show 1x{number}", parent_id=season, index=number)
            add_version(session, f"{episode}-v", episode, kind="episode")
        add_movie(session, anime, "Hidden Anime").category = "anime"
        add_title(session, artist, "artist", "Hidden Artist")
        add_title(session, album, "album", "Hidden Album", parent_id=artist)
        add_version(session, f"{album}-t", album, kind="track")
        seed_app_settings(session)  # commits the titles too
    report = dry_run().measure(repeats=2, sample=0, seed=7)
    text = json.dumps(report)
    for private in ("Secret Movie Name", "Hidden Show", "alice", "owner", movie, series, season, *episodes,
                    "Hidden Anime", "Hidden Artist", "Hidden Album", anime, artist, album):
        assert private not in text
    assert report["members"] == 2
    names = set(report["endpoints"])
    assert {"movies first page (name, total, letters)", "series facets", "movie detail", "series detail (largest)", "season episodes", "episode summaries", "key scenes", "recap"} <= names
    assert {"anime first page (name, total, letters)", "sections", "albums first page (name, total, letters)",
            "album detail (largest)", "artist detail"} <= names
    assert report["categories"]["uncategorised"] == 0 and report["categories"]["by_type"]["movie:anime"] == 1
    assert isinstance(report["recategorise_ms"], float)
    assert {row["status"] for rows in report["endpoints"].values() for row in rows.values()} == {200}


def test_the_network_guard_refuses_anything_but_loopback() -> None:
    import socket

    module = dry_run()
    listener = socket.create_server(("127.0.0.1", 0))
    restore = module.guard_network()
    try:
        with pytest.raises(ConnectionRefusedError, match="loopback"):
            socket.create_connection(("192.0.2.1", 9), timeout=1)  # TEST-NET-1: never reached
        socket.create_connection(listener.getsockname(), timeout=1).close()
        assert module.refused_connections == 1
    finally:
        restore()
        listener.close()


def test_only_locally_stored_art_is_rendered() -> None:
    from app.main import artwork

    module = dry_run()
    factory = file_backed_session_factory("owner")
    film = str(uuid.UUID(int=1))
    with factory() as session:
        add_movie(session, film, "Film").images = {"Primary": {"tmdb": "/poster.jpg"}}  # would fetch from TMDB
        seed_app_settings(session)
    with factory() as session:
        summary, urls = module.render_sample(session, artwork, 5, random.Random(7))
    assert (summary["render_ms"], summary["failures"], urls) == ({}, {}, [])


def test_the_music_scan_reports_durations_and_counters_only(tmp_path: Path) -> None:
    from app import db as db_module

    db_module.init_db()
    file_backed_session_factory("owner")
    music = tmp_path / "Music"
    song = music / "Private Artist" / "Private Album" / "01 - Private Song.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"\x00not-really-audio")
    report = dry_run().scan_music(music)
    text = json.dumps(report)
    assert "Private" not in text and str(music) not in text
    assert (report["first_scan_state"], report["rescan_state"]) == ("succeeded", "succeeded")
    assert (report["first_scan_counters"]["indexed"], report["rescan_counters"]["unchanged"]) == (1, 1)
    assert all(isinstance(report[key], int) for key in ("albums", "artists", "tracks"))
    assert all(isinstance(report[key], float) for key in ("first_scan_ms", "rescan_ms"))
