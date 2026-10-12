"""One subtitle track list per item; text tracks become cached WebVTT; nothing outside the item's folder."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings
from app.models import LibraryItem
from app.services import local_playback_sessions as lps
from app.services import subtitle_tracks
from app.services.subtitle_tracks import list_tracks
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401
from tests.test_v1_probe import make_media, pytestmark  # noqa: F401  (skips without ffmpeg)

SRT = "1\n00:00:00,500 --> 00:00:01,500\nHello there\n\n"
# ffmpeg's webvtt muxer omits zero hours (00:00.500), unlike the source SRT.
CUE_TIME = re.compile(r"(?:\d{2}:)?\d{2}:\d{2}\.\d{3} --> (?:\d{2}:)?\d{2}:\d{2}\.\d{3}")


def _with_subtitles(folder: Path) -> Path:
    clip = make_media(folder / "plain.mkv")
    (folder / "embedded.srt").write_text(SRT)
    out = folder / "movie.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-i", str(folder / "embedded.srt"), "-map", "0", "-map", "1",
                    "-c", "copy", "-c:s", "srt", "-metadata:s:s:0", "language=eng", str(out)], check=True, timeout=60)
    return out


def _set_sidecars(item_id: str, entries: list) -> None:
    with db_module.SessionLocal() as db:
        item = db.get(LibraryItem, item_id)
        item.metadata_json = {**(item.metadata_json or {}), "lumina_subtitles": entries}
        db.commit()


def test_embedded_and_sidecar_tracks_list_and_convert(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings.library_root / "alice"
    item_id = _download("alice", _with_subtitles(folder))
    (folder / "movie.en.srt").write_text(SRT.replace("Hello", "Sidecar"))
    _set_sidecars(item_id, [{"filename": "movie.en.srt", "language": "eng", "forced": False, "hearing_impaired": True, "default": False, "format": "srt"}])
    with _client("alice") as client:
        tracks = client.get(f"/api/library/{item_id}/subtitle-tracks").json()
        assert [(t["id"], t["origin"], t["format"], t["language"], t["label"]) for t in tracks] == [
            ("e:2", "embedded", "text", "eng", "ENG"), ("s:0", "sidecar", "text", "eng", "ENG · SDH")]
        embedded = client.get(tracks[0]["url"])
        assert embedded.status_code == 200 and embedded.headers["content-type"].startswith("text/vtt")
        assert embedded.text.startswith("WEBVTT") and CUE_TIME.search(embedded.text) and "Hello there" in embedded.text
        assert "Sidecar there" in client.get(tracks[1]["url"]).text
    assert len(list(subtitle_tracks.subtitle_cache_root().glob("*.vtt"))) == 2

    def no_extraction(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("must be served from the cache")

    monkeypatch.setattr(subtitle_tracks, "_extract", no_extraction)
    with _client("alice") as client:
        assert client.get(tracks[0]["url"]).status_code == 200


def test_hostile_track_ids_and_sidecars_never_escape(household: None) -> None:
    folder = settings.library_root / "alice"
    item_id = _download("alice", _with_subtitles(folder))
    secret = folder.parent / "secret.srt"
    secret.write_text(SRT)
    (folder / "linked.srt").symlink_to(secret)
    (folder / ".hidden.srt").write_text(SRT)
    _set_sidecars(item_id, [{"filename": "../secret.srt"}, {"filename": "linked.srt", "format": "srt"}, {"filename": ".hidden.srt"}, "junk", {"filename": 7}])
    with _client("alice") as client:
        listed = [t["id"] for t in client.get(f"/api/library/{item_id}/subtitle-tracks").json()]
        assert listed == ["e:2", "s:1"]  # unsafe names are never offered; indexes stay positional
        for track in ("s:0", "s:1", "s:2", "s:3", "s:4", "s:99", "t:0f8fad5b-d9cb-469f-a165-70867728950e", "i:2", "e:0", "e:1", "e:99", "x:1", "e:-1", "e:1a"):
            assert client.get(f"/api/library/{item_id}/subtitle-tracks/{track}.vtt").status_code == 404, track  # s:1 is a symlink
        assert client.get(f"/api/library/{item_id}/subtitle-tracks/s:..%2F..%2Fsecret.vtt").status_code == 404
    with _client("bob") as bob:
        assert bob.get(f"/api/library/{item_id}/subtitle-tracks").status_code == 404
        assert bob.get(f"/api/library/{item_id}/subtitle-tracks/e:2.vtt").status_code == 404


def test_image_tracks_are_listed_as_burn_in_only() -> None:
    facts = {"streams": [
        {"index": 3, "type": "subtitle", "codec": "hdmv_pgs_subtitle", "language": "eng", "title": None, "forced": True, "default": False, "hearing_impaired": False},
        {"index": 4, "type": "subtitle", "codec": "eia_608", "language": None, "title": None, "forced": False, "default": False, "hearing_impaired": False},
    ]}
    (track,) = list_tracks(facts, LibraryItem(id="i1", metadata_json={}))
    assert (track.id, track.format, track.url, track.forced, track.label) == ("i:3", "image", None, True, "ENG · Forced · Burned in")


def test_reap_prunes_the_subtitle_cache_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    root = subtitle_tracks.subtitle_cache_root()
    root.mkdir(parents=True)
    for n, age in enumerate((300, 200, 100)):
        path = root / f"a-{n}.vtt"
        path.write_bytes(b"x" * 100)
        os.utime(path, (time.time() - age, time.time() - age))
    monkeypatch.setattr(subtitle_tracks, "CACHE_MAX_BYTES", 150)
    lps.LocalPlaybackSessions().reap()
    assert sorted(p.name for p in root.iterdir()) == ["a-2.vtt"]
