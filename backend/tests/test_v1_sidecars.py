"""Local NFO, artwork and grouping for imported files."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import PlaybackProgress, User
from app.security import hash_password
from app.services.local_metadata import parse_nfo
from app.services.rate_limit import rate_limiter

PASSWORD = "Test-only-passphrase-1"


@pytest.fixture
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    parent = tmp_path / "mnt"
    (parent / "media").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add(User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    rate_limiter.clear()
    with TestClient(app, base_url="http://localhost") as client:  # lifespan: import drivers enabled
        client.auth = ("admin", PASSWORD)
        yield client
    rate_limiter.clear()


def write(path: Path, content: bytes | str = b"\x00fake") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def scan(admin: TestClient, root: Path) -> dict[str, dict]:
    root_id = admin.post("/api/admin/storage/roots", json={"label": "Media", "container_path": str(root), "mode": "external"}).json()["id"]
    run = admin.post("/api/admin/imports", json={"root_id": root_id, "visibility": "private"}).json()
    assert admin.get(run["status_url"]).json()["state"] == "succeeded"
    items = admin.get("/api/library").json()["items"]
    return {item["title"]: admin.get(f"/api/library/{item['id']}").json() for item in items}


def test_nfo_movie_episode_album(admin: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "mnt" / "media"
    movie = root / "Movies" / "Amelie (2001)"
    write(movie / "Amelie (2001).mkv")
    # Hybrid NFO: a scraper URL after the XML must not discard the parsed fields.
    write(movie / "Amelie (2001).nfo", "<?xml version='1.0' encoding='utf-8'?><movie><title>Le Fabuleux Destin d’Amélie Poulain</title>"
          "<year>2001</year><plot>Une serveuse.</plot><genre>Comedy</genre><genre>Romance</genre></movie>\nhttps://www.themoviedb.org/movie/194")
    write(movie / "poster.jpg", b"\xff\xd8poster")
    write(movie / "fanart.jpg", b"\xff\xd8fanart")
    show = root / "TV" / "Omega"
    write(show / "tvshow.nfo", "<tvshow><title>Doctor Ωmega</title></tvshow>")
    write(show / "poster.jpg", b"\xff\xd8show")
    write(show / "Season 01" / "Omega - S01E02 - The Return.mkv")
    album = root / "Music" / "Björk" / "Homogenic"
    write(album / "03 - Jóga.flac")
    write(album / "album.nfo", "<album><title>Homogenic</title><artist>Björk</artist><year>1997</year></album>")
    before = sorted((str(p), p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file())

    items = scan(admin, root)

    film = items["Le Fabuleux Destin d’Amélie Poulain"]
    assert film["metadata_json"]["lumina_import_kind"] == "movie"
    assert film["metadata_json"]["release_year"] == 2001
    assert film["metadata_json"]["genre"] == "Comedy, Romance"
    assert film["metadata_json"]["lumina_title_source"] == "sidecar"
    assert "lumina_local_artwork" not in film["metadata_json"]  # paths never reach members
    artwork = admin.get(f"/api/library/{film['id']}/artwork")
    assert artwork.status_code == 200 and artwork.content == b"\xff\xd8poster"

    episode = items["The Return"]
    assert episode["uploader"] == "Doctor Ωmega"
    assert episode["playlist_name"] == "Doctor Ωmega · Season 1"
    assert (episode["metadata_json"]["season_number"], episode["metadata_json"]["episode_number"]) == (1, 2)
    assert episode["metadata_json"]["lumina_import_kind"] == "episode"
    # Kodi layout: the show poster sits beside tvshow.nfo, above the season folder.
    assert admin.get(f"/api/library/{episode['id']}/artwork").content == b"\xff\xd8show"

    track = items["Jóga"]
    assert (track["uploader"], track["playlist_name"]) == ("Björk", "Homogenic")
    assert track["metadata_json"]["track_number"] == 3
    assert track["metadata_json"]["release_year"] == 1997

    assert [item["title"] for item in admin.get("/api/library", params={"search": "Ωmega"}).json()["items"]] == ["The Return"]
    assert sorted((str(p), p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()) == before


def test_nfo_xxe_and_remote_url_blocked(admin: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "mnt" / "media"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    write(root / "Leak.mkv")
    write(root / "Leak.nfo", f'<?xml version="1.0"?><!DOCTYPE movie [<!ENTITY x SYSTEM "file://{secret}">]><movie><title>&x;</title></movie>')
    write(root / "Laughs.mkv")
    write(root / "Laughs.nfo", '<!DOCTYPE m [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><movie><title>&b;</title></movie>')
    write(root / "Remote (2020).mkv")
    write(root / "Remote (2020).nfo", "<movie><title>Remote</title><thumb>https://images.example.com/p.jpg</thumb></movie>")
    deep = "<movie>" + "<a>" * 20 + "</a>" * 20 + "<title>Deep</title></movie>"
    write(root / "deep.nfo", deep)
    assert parse_nfo(root / "deep.nfo") is None

    items = scan(admin, root)

    assert sorted(items) == ["Laughs", "Leak", "Remote"]
    assert all("TOP-SECRET" not in str(item) for item in items.values())
    remote = items["Remote"]
    assert remote["thumbnail_url"] is None
    # No local poster and no fetch of the NFO's URL (the network guard would fail the test).
    assert admin.get(f"/api/library/{remote['id']}/artwork").status_code == 404


def test_ambiguous_filename_unclassified(admin: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "mnt" / "media"
    write(root / "holiday 2019 clip 1920x1080.mp4", b"clip-bytes")
    write(root / "Broken Thing.mkv", b"broken-bytes")
    write(root / "Broken Thing.nfo", "<movie><title>Never closed")
    write(root / "Watched (2010).mkv")
    write(root / "Watched (2010).nfo", "<movie><title>Watched</title><playcount>5</playcount><watched>true</watched></movie>")

    items = scan(admin, root)

    clip = items["holiday 2019 clip 1920x1080"]
    assert clip["metadata_json"]["lumina_import_kind"] == "unclassified"
    assert not {"season_number", "episode_number", "series", "release_year"} & clip["metadata_json"].keys()
    broken = items["Broken Thing"]
    assert broken["metadata_json"]["lumina_title_source"] == "filename"
    assert admin.get(f"/api/library/{broken['id']}/media").content == b"broken-bytes"
    assert items["Watched"]["metadata_json"]["lumina_import_kind"] == "movie"
    with db_module.SessionLocal() as db:
        assert db.query(PlaybackProgress).count() == 0  # sidecar watch state is never assigned
