"""Scanner v2 — Media titles, versions, extras, sidecars, guards."""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import LibraryItem, MediaTitle, Transcript, User
from app.security import hash_password
from app.services import library_import
from app.services.library import LibraryService, library_kind
from app.services.library_import import mass_missing
from app.services.local_metadata import describe, nfo_title_fields, parse_nfo, strip_provider_tags
from app.services.media_titles import apply_field, set_item_locked
from app.services.rate_limit import rate_limiter

PASSWORD = "Test-only-passphrase-1"


def nfo(tmp_path: Path, xml: str) -> tuple[str, dict]:
    path = tmp_path / "x.nfo"
    path.write_text(xml, encoding="utf-8")
    parsed = parse_nfo(path)
    assert parsed is not None
    return parsed


def test_movie_nfo_ids_set_rating_people(tmp_path: Path) -> None:
    tag, fields = nfo(tmp_path, (
        "<movie><title>Heat</title><sorttitle>Heat 1995</sorttitle><year>1995</year><mpaa>R</mpaa>"
        "<uniqueid type='tmdb' default='true'>949</uniqueid><uniqueid type='imdb'>tt0113277</uniqueid>"
        "<set><name>Crime Saga</name><overview>ignored</overview></set><genre>Crime</genre><genre>Drama</genre>"
        "<actor><name>Al Pacino</name><role>Vincent Hanna</role></actor><actor><name>Robert De Niro</name></actor>"
        "<premiered>1995-12-15</premiered><plot>A heist.</plot></movie>"
    ))
    assert fields["set"] == "Crime Saga"
    assert nfo_title_fields(tag, fields) == {
        "name": "Heat", "sort_name": "Heat 1995", "year": 1995,
        "provider_ids": {"Tmdb": "949", "Imdb": "tt0113277"},
        "overview": "A heist.", "genres": ["Crime", "Drama"], "official_rating": "R", "premiered": "1995-12-15",
        "people": [
            {"person_id": None, "name": "Al Pacino", "role": "Vincent Hanna", "type": "Actor", "thumb": None},
            {"person_id": None, "name": "Robert De Niro", "role": None, "type": "Actor", "thumb": None},
        ],
    }


def test_legacy_set_tvshow_id_and_episode_airs_before(tmp_path: Path) -> None:
    assert nfo(tmp_path, "<movie><title>X</title><set>Old Saga</set></movie>")[1]["set"] == "Old Saga"
    tag, fields = nfo(tmp_path, "<tvshow><title>Omega</title><id>76107</id><tmdbid>1234</tmdbid></tvshow>")
    assert nfo_title_fields(tag, fields)["provider_ids"] == {"Tmdb": "1234", "Tvdb": "76107"}
    tag, fields = nfo(tmp_path, (
        "<episodedetails><title>Christmas</title><season>0</season><episode>1</episode>"
        "<airsbefore_season>2</airsbefore_season><airsbefore_episode>1</airsbefore_episode><aired>2006-12-25</aired>"
        "</episodedetails>"
    ))
    assert nfo_title_fields(tag, fields) == {
        "name": "Christmas", "premiered": "2006-12-25", "airsbefore_season": 2, "airsbefore_episode": 1,
    }


def test_hostile_nfo_is_bounded(tmp_path: Path) -> None:
    actors = "".join(f"<actor><name>A{i}</name></actor>" for i in range(500))
    tag, fields = nfo(tmp_path, (
        f"<movie><title>X</title>{actors}<uniqueid type='tmdb'>1 OR 1=1</uniqueid><uniqueid type='evil'>5</uniqueid>"
        "<tmdbid>../../x</tmdbid><id>tt1/../../</id></movie>"
    ))
    values = nfo_title_fields(tag, fields)
    assert len(values["people"]) == 20
    assert "provider_ids" not in values  # every id was malformed: nothing reaches TMDB URLs


@pytest.mark.parametrize(("text", "clean", "ids"), [
    ("Heat (1995) [tmdbid-949]", "Heat (1995)", {"Tmdb": "949"}),
    ("Heat (1995) {imdbid-tt0113277} - 4K", "Heat (1995) - 4K", {"Imdb": "tt0113277"}),
    ("Omega [tvdbid=76107]", "Omega", {"Tvdb": "76107"}),
    ("Omega [TVDBID-7] [tmdbid-8]", "Omega", {"Tvdb": "7", "Tmdb": "8"}),
    ("Bad [tmdbid-../x]", "Bad", {}),
    ("Plain [2160p]", "Plain [2160p]", {}),
])
def test_strip_provider_tags(text: str, clean: str, ids: dict) -> None:
    assert strip_provider_tags(text) == (clean, ids)


def described(tmp_path: Path, target: str, others: tuple[str, ...] = (), contents: dict[str, str] | None = None) -> dict:
    """describe() one file of a synthetic root; ``others`` are extra files, ``contents`` NFO/sidecar bodies."""
    root = tmp_path / "media"
    for relative in (target, *others, *(contents or {})):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((contents or {}).get(relative, "x"), encoding="utf-8")
    parts = tuple(target.split("/"))
    folder = root.joinpath(*parts[:-1])
    siblings = frozenset(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink())
    return describe(root, parts, siblings, {})


def chain(result: dict) -> list[tuple[str, str]]:
    return [(spec["type"], spec["key"]) for spec in result["titles"]]


@pytest.mark.parametrize(("target", "others", "kind", "keys", "title", "extra_type"), [
    # Rule 3: season folders, Specials, a file directly under the show folder, tags on the show folder.
    ("TV/Omega [tvdbid-7]/Season 01/Omega S01E02 - The Return.mkv", (), "episode",
     [("series", "TV/Omega [tvdbid-7]"), ("season", "TV/Omega [tvdbid-7]#s1"), ("episode", "TV/Omega [tvdbid-7]#s1e2")], "The Return", None),
    ("TV/Omega/Specials/Omega S00E01.mkv", (), "episode",
     [("series", "TV/Omega"), ("season", "TV/Omega#s0"), ("episode", "TV/Omega#s0e1")], "Omega S00E01", None),
    ("TV/Omega/Omega S02E03.mkv", (), "episode",
     [("series", "TV/Omega"), ("season", "TV/Omega#s2"), ("episode", "TV/Omega#s2e3")], "Omega S02E03", None),
    # A loose episode in a real show folder (season dirs) belongs to that show,
    # even when its filename prefix abbreviates the folder name.
    ("TV/The Office (US)/Office S02E01.mkv", ("TV/The Office (US)/Season 01/Office S01E01.mkv",), "episode",
     [("series", "TV/The Office (US)"), ("season", "TV/The Office (US)#s2"), ("episode", "TV/The Office (US)#s2e1")],
     "Office S02E01", None),
    # A flat folder of several shows: one series per show, not one named after the folder.
    ("Downloads/Delta S01E01.mkv", ("Downloads/Omega S01E01.mkv",), "episode",
     [("series", "Downloads/Delta"), ("season", "Downloads/Delta#s1"), ("episode", "Downloads/Delta#s1e1")], "Delta S01E01", None),
    # Rule 5: versions converge on one key; loose files split after "(Year)".
    ("Movies/Heat (1995)/Heat (1995) - 4K.mkv", ("Movies/Heat (1995)/Heat (1995).mkv",), "movie",
     [("movie", "Movies/Heat (1995)")], "Heat · 4K", None),
    ("Movies/Heat (1995) [tmdbid-949] - 1080p.mkv", (), "movie", [("movie", "Movies/Heat (1995)")], "Heat · 1080p", None),
    # Review 24 (Jellyfin's movie-folder rule): a movie folder owns every non-extra video in it,
    # whether scene-named or Radarr's "{Title} ({Year}) {Quality}".
    ("Movies/Up (2009)/Up.2009.1080p.BluRay.x264.mkv", ("Movies/Up (2009)/poster.jpg",), "movie", [("movie", "Movies/Up (2009)")], "Up", None),
    ("Movies/Heat (1995)/Heat (1995) Bluray-1080p.mkv", ("Movies/Heat (1995)/Heat (1995) Remux-2160p.mkv",), "movie",
     [("movie", "Movies/Heat (1995)")], "Heat · Bluray-1080p", None),
    ("Movies/Heat (1995)/heat (1995) - 4K.mkv", (), "movie", [("movie", "Movies/Heat (1995)")], "heat · 4K", None),
    # A series folder named "Show (Year)" never owns loose files as a movie...
    ("TV/Show (2005)/Show - Unaired Pilot.mkv", ("TV/Show (2005)/Season 1/Show S01E01.mkv",), "unclassified", [], "Show - Unaired Pilot", None),
    ("TV/Show (2005)/Pilot.mkv", ("TV/Show (2005)/tvshow.nfo",), "unclassified", [], "Pilot", None),
    ("TV/Anime (2019)/Anime - 01.mkv", ("TV/Anime (2019)/Anime - 02.mkv",), "unclassified", [], "Anime - 01", None),
    ("TV/Show (2005)/Show (2005) - Unaired Pilot.mkv", ("TV/Show (2005)/Season 1/Show S01E01.mkv",), "movie",
     [("movie", "TV/Show (2005)/Show (2005)")], "Show · Unaired Pilot", None),
    # ...a different movie beside the folder's own is its own title...
    ("Movies/Heat (1995)/Collateral (2004).mkv", ("Movies/Heat (1995)/Heat (1995).mkv",), "movie",
     [("movie", "Movies/Heat (1995)/Collateral (2004)")], "Collateral", None),
    ("Movies/Kill Bill (2003)/Kill Bill Vol 2 (2004).mkv", ("Movies/Kill Bill (2003)/Kill Bill Vol 1 (2003).mkv",), "movie",
     [("movie", "Movies/Kill Bill (2003)/Kill Bill Vol 2 (2004)")], "Kill Bill Vol 2", None),
    # ...and extras in a movie folder belong to it whatever their base name.
    ("Movies/Up (2009)/Up.2009.1080p-trailer.mkv", ("Movies/Up (2009)/Up.2009.1080p.mkv",), "extra",
     [("movie", "Movies/Up (2009)")], "Up 2009 1080p-trailer", "trailer"),
    ("Movies/Up (2009)/trailer.mkv", ("Movies/Up (2009)/Up.2009.1080p.mkv",), "extra", [("movie", "Movies/Up (2009)")], "trailer", "trailer"),
    # A category folder named like an extras folder is not an extra without a movie/series owner.
    ("Movies/Shorts/Paperman (2012).mkv", (), "movie", [("movie", "Movies/Shorts/Paperman (2012)")], "Paperman", None),
    ("Home/Beach/Clips/sunset.mp4", (), "unclassified", [], "sunset", None),
    # Rule 6: extras by folder and by suffix link to the owning title.
    ("Movies/Heat (1995)/Trailers/Teaser.mkv", ("Movies/Heat (1995)/Heat (1995).mkv",), "extra",
     [("movie", "Movies/Heat (1995)")], "Teaser", "trailer"),
    ("Movies/Heat (1995)/Heat (1995)-featurette.mkv", (), "extra", [("movie", "Movies/Heat (1995)")], "Heat (1995)-featurette", "featurette"),
    ("Movies/Heat (1995)-deleted.mkv", (), "extra", [("movie", "Movies/Heat (1995)")], "Heat (1995)-deleted", "deletedscene"),
    ("TV/Omega/Extras/Making Of.mkv", ("TV/Omega/tvshow.nfo",), "extra", [("series", "TV/Omega")], "Making Of", "other"),
    ("clip.mp4", (), "unclassified", [], "clip", None),
])
def test_describe_title_chains(tmp_path: Path, target: str, others: tuple, kind: str, keys: list, title: str, extra_type: str | None) -> None:
    result = described(tmp_path, target, others)
    assert (result["metadata"]["lumina_import_kind"], chain(result), result["title"], result["extra_type"]) == (kind, keys, title, extra_type)


@pytest.mark.parametrize(("stem", "end", "name"), [
    ("Omega S01E01-E02 - Pilot", 2, "Pilot"),
    ("Omega S01E01E02", 2, "Episode 1"),
    ("Omega S01E01-02", 2, "Episode 1"),
    ("Omega S01E01 - 1080p", None, "1080p"),  # a quality suffix is not a second episode
    ("Omega S01E01-720p", None, "720p"),
    ("Omega S01E03-E02", None, "E02"),  # a range must go forward
])
def test_multi_episode(tmp_path: Path, stem: str, end: int | None, name: str) -> None:
    result = described(tmp_path, f"TV/Omega/Season 1/{stem}.mkv")
    episode = result["titles"][-1]
    assert episode["key"] == ("TV/Omega#s1e3" if stem.startswith("Omega S01E03") else "TV/Omega#s1e1")
    assert result["metadata"].get("episode_number_end") == end
    assert (episode["fields"]["path"].get("index_number_end"), episode["fields"]["path"]["name"]) == (end, name)


def images(spec: dict) -> dict[str, str]:
    return {field: value["path"] for field, value in spec["fields"]["nfo"].items() if field.startswith("images.")}


def test_series_season_and_episode_artwork(tmp_path: Path) -> None:
    show = "TV/Omega"
    result = described(tmp_path, f"{show}/Season 01/Omega S01E01.mkv", (
        f"{show}/poster.jpg", f"{show}/fanart.jpg", f"{show}/clearlogo.png", f"{show}/banner.jpg", f"{show}/landscape.jpg",
        f"{show}/season01-poster.jpg", f"{show}/Season 01/Omega S01E01-thumb.jpg",
    ), {f"{show}/tvshow.nfo": "<tvshow><title>Doctor Ωmega</title><uniqueid type='tvdb'>76107</uniqueid><genre>SF</genre></tvshow>"})
    series, season, episode = result["titles"]
    assert images(series) == {
        "images.Primary": "TV/Omega/poster.jpg", "images.Backdrop": "TV/Omega/fanart.jpg", "images.Logo": "TV/Omega/clearlogo.png",
        "images.Thumb": "TV/Omega/landscape.jpg", "images.Banner": "TV/Omega/banner.jpg",
    }
    assert (series["fields"]["nfo"]["name"], series["fields"]["nfo"]["provider_ids"], series["fields"]["nfo"]["genres"]) == (
        "Doctor Ωmega", {"Tvdb": "76107"}, ["SF"])
    assert series["fields"]["path"] == {"name": "Omega"}
    assert images(season) == {"images.Primary": "TV/Omega/season01-poster.jpg"}
    assert images(episode) == {"images.Primary": "TV/Omega/Season 01/Omega S01E01-thumb.jpg"}
    assert all(value["tag"] for spec in result["titles"] for field, value in spec["fields"]["nfo"].items() if field.startswith("images."))


def test_specials_and_season_folder_art(tmp_path: Path) -> None:
    specials = described(tmp_path, "TV/Omega/Specials/Omega S00E01.mkv", ("TV/Omega/season-specials-poster.png",))
    assert specials["titles"][1]["fields"]["path"] == {"name": "Specials", "index_number": 0}
    assert images(specials["titles"][1]) == {"images.Primary": "TV/Omega/season-specials-poster.png"}
    two = described(tmp_path, "TV/Omega/Season 2/Omega S02E01.mkv", ("TV/Omega/Season 2/folder.jpg",))
    assert images(two["titles"][1]) == {"images.Primary": "TV/Omega/Season 2/folder.jpg"}


def test_movie_folder_art_covers_scene_named_files(tmp_path: Path) -> None:
    folder = "Movies/Up (2009)"
    result = described(tmp_path, f"{folder}/Up.2009.1080p.BluRay.x264.mkv", (f"{folder}/poster.jpg", f"{folder}/fanart.jpg"))
    movie = result["titles"][-1]
    assert images(movie) == {"images.Primary": f"{folder}/poster.jpg", "images.Backdrop": f"{folder}/fanart.jpg"}
    assert movie["fields"]["path"] == {"name": "Up", "year": 2009} and result["metadata"]["release_year"] == 2009


def test_a_stem_nfo_naming_another_movie_is_not_the_folders(tmp_path: Path) -> None:
    folder = "Movies/Heat (1995)"
    result = described(tmp_path, f"{folder}/Heat (1995) Collateral cut.mkv", (f"{folder}/Heat (1995).mkv",), {
        f"{folder}/Heat (1995) Collateral cut.nfo": "<movie><title>Collateral</title><uniqueid type='tmdb'>1538</uniqueid></movie>",
    })
    assert chain(result) == [("movie", f"{folder}/Heat (1995) Collateral cut")]
    same = described(tmp_path / "b", f"{folder}/Heat (1995) - 4K.mkv", (), {f"{folder}/Heat (1995) - 4K.nfo": "<movie><title>Heat</title></movie>"})
    assert chain(same) == [("movie", folder)]


def test_series_folder_with_a_year_keeps_its_type_on_a_real_scan(admin: TestClient, media: Path) -> None:
    show = media / "TV" / "Show (2005)"
    write(show / "Season 1" / "Show S01E01.mkv", b"e1")
    write(show / "Show - Unaired Pilot.mkv", b"p")
    write(show / "Show (2005) - Unaired Pilot.mkv", b"q")
    assert scan(admin)["state"] == "succeeded"
    titles = by_key()
    assert titles["TV/Show (2005)"].type == "series"
    assert library_items()["Show - Unaired Pilot"].title_id is None


def test_a_leaf_key_change_carries_the_title_over(admin: TestClient, media: Path) -> None:
    """A file whose key changes under the new rule keeps its title (edits, favorites, matches)."""
    write(media / "Movies" / "Up (2009)" / "Up.2009.1080p.mkv", b"up")
    scan(admin)
    with db_module.SessionLocal() as db:
        movie = db.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        movie_id, movie.key = movie.id, movie.key.replace("Movies/Up (2009)", "Movies/Up (2009)/Up.2009.1080p")  # an old-rule key
        apply_field(movie, "name", "Up (family night)", "user")
        db.commit()
    scan(admin)
    moved = by_key()["Movies/Up (2009)"]
    assert (moved.id, moved.name) == (movie_id, "Up (family night)")
    assert library_items()["Up"].title_id == movie_id


def test_movie_nfo_set_makes_a_boxset(tmp_path: Path) -> None:
    folder = "Movies/Heat (1995)"
    result = described(tmp_path, f"{folder}/Heat (1995) - 4K.mkv", (f"{folder}/Heat (1995).mkv", f"{folder}/poster.jpg"), {
        f"{folder}/movie.nfo": "<movie><title>Heat</title><set><name>Crime Saga</name></set><uniqueid type='tmdb'>949</uniqueid></movie>",
    })
    assert chain(result) == [("boxset", "set:crime saga"), ("movie", "Movies/Heat (1995)")]
    assert (result["title"], result["metadata"]["version"]) == ("Heat · 4K", "4K")
    movie = result["titles"][1]["fields"]
    assert movie["path"] == {"name": "Heat", "year": 1995}
    assert movie["nfo"]["provider_ids"] == {"Tmdb": "949"} and images(result["titles"][1])["images.Primary"] == f"{folder}/poster.jpg"


def test_subtitle_sidecars_sorted_with_flags(tmp_path: Path) -> None:
    stem = "TV/Omega/Season 1/Omega S01E01"
    result = described(tmp_path, f"{stem}.mkv", tuple(f"{stem}{suffix}" for suffix in (
        ".en.srt", ".en.forced.srt", ".de.sdh.vtt", ".default.ass", ".nfo.txt", "-thumb.jpg")))
    assert result["metadata"]["lumina_subtitles"] == [
        {"filename": "Omega S01E01.de.sdh.vtt", "language": "de", "forced": False, "hearing_impaired": True, "default": False, "format": "vtt"},
        {"filename": "Omega S01E01.default.ass", "language": None, "forced": False, "hearing_impaired": False, "default": True, "format": "ass"},
        {"filename": "Omega S01E01.en.forced.srt", "language": "en", "forced": True, "hearing_impaired": False, "default": False, "format": "srt"},
        {"filename": "Omega S01E01.en.srt", "language": "en", "forced": False, "hearing_impaired": False, "default": False, "format": "srt"},
    ]


def test_extras_have_their_own_library_kind() -> None:
    assert library_kind({"lumina_import_kind": "extra"}) == "extra"


@pytest.fixture
def media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "mnt"
    (parent / "media").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add_all([
            User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(id="member", username="member", display_name="Member", password_hash=hash_password(PASSWORD), role="viewer", is_active=True),
        ])
        db.commit()
    rate_limiter.clear()
    yield parent / "media"
    rate_limiter.clear()


@pytest.fixture
def admin(media: Path):
    with TestClient(app, base_url="http://localhost") as client:  # lifespan: import drivers enabled
        client.auth = ("admin", PASSWORD)
        client.root_id = client.post(
            "/api/admin/storage/roots", json={"label": "Media", "container_path": str(media), "mode": "external"}
        ).json()["id"]
        yield client


def write(path: Path, content: bytes | str = b"\x00fake") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def scan(admin: TestClient) -> dict:
    started = admin.post("/api/admin/imports", json={"root_id": admin.root_id, "visibility": "private"})
    assert started.status_code == 202, started.text
    return admin.get(started.json()["status_url"]).json()


def by_key() -> dict[str, MediaTitle]:
    """Titles by root-relative key (boxsets by their global key)."""
    with db_module.SessionLocal() as db:
        rows = db.scalars(select(MediaTitle)).all()
        db.expunge_all()
    return {row.key if row.type == "boxset" else row.key.split(":", 1)[1]: row for row in rows}


def library_items() -> dict[str, LibraryItem]:
    with db_module.SessionLocal() as db:
        rows = db.scalars(select(LibraryItem)).all()
        db.expunge_all()
    return {row.title: row for row in rows}


def test_series_with_specials_and_provider_tag(admin: TestClient, media: Path) -> None:
    show = media / "TV" / "Omega [tvdbid-76107]"
    write(show / "tvshow.nfo", "<tvshow><title>Doctor Ωmega</title><genre>SF</genre></tvshow>")
    write(show / "Season 01" / "Omega S01E01 - Pilot.mkv", b"e1")
    write(show / "Specials" / "Omega S00E01 - Christmas.mkv", b"sp")
    assert scan(admin)["state"] == "succeeded"

    base = "TV/Omega [tvdbid-76107]"
    titles = by_key()
    series, s1, s0 = titles[base], titles[f"{base}#s1"], titles[f"{base}#s0"]
    assert (series.type, series.name, series.provider_ids, series.root_id) == ("series", "Doctor Ωmega", {"Tvdb": "76107"}, admin.root_id)
    assert (series.field_sources["name"], series.field_sources["provider_ids"], series.metadata_json["genres"]) == ("nfo", "path", ["SF"])
    assert (s0.name, s0.index_number, s0.parent_id, s1.parent_id) == ("Specials", 0, series.id, series.id)
    pilot, special = titles[f"{base}#s1e1"], titles[f"{base}#s0e1"]
    assert (pilot.name, pilot.parent_id, special.parent_id) == ("Pilot", s1.id, s0.id)
    items = library_items()
    assert (items["Pilot"].title_id, items["Christmas"].title_id, items["Pilot"].extra_type) == (pilot.id, special.id, None)
    # Queue: only new series/movie titles are due.
    assert series.metadata_due_at is not None and s1.metadata_due_at is None and pilot.metadata_due_at is None


def test_recently_added_follows_when_files_arrived_not_scan_order(admin: TestClient, media: Path) -> None:
    """A first scan creates every title within minutes, walking alphabetically. Recently added uses file times instead:
    a movie's earliest version (a later 4K upgrade does not re-date it), a show's newest episode, which a rescan lifts."""
    now = time.time_ns()

    def arrive(relative: str, days_ago: int) -> None:
        write(media / relative)
        os.utime(media / relative, ns=(now, now - days_ago * 86_400 * 10**9))

    arrive("Movies/Alpha (2001)/Alpha (2001).mkv", 1)
    arrive("Movies/Mike (2001)/Mike (2001).mkv", 10)
    arrive("Movies/Zulu (2001)/Zulu (2001) - 1080p.mkv", 30)
    arrive("Movies/Zulu (2001)/Zulu (2001) - 4K.mkv", 2)
    arrive("TV/Able/Season 1/Able S01E01.mkv", 5)
    arrive("TV/Baker/Season 1/Baker S01E01.mkv", 100)
    arrive("TV/Baker/Season 2/Baker S02E01.mkv", 3)
    assert scan(admin)["state"] == "succeeded"

    def recently_added(kind: str) -> list[str]:
        return [t["name"] for t in admin.get("/api/titles", params={"type": kind, "sort": "created"}).json()["items"]]

    assert recently_added("movie") == ["Alpha", "Mike", "Zulu"]
    assert recently_added("series") == ["Baker", "Able"]
    arrive("TV/Able/Season 1/Able S01E02.mkv", 0)
    assert scan(admin)["state"] == "succeeded"
    assert recently_added("series") == ["Able", "Baker"]
    zulu = by_key()["Movies/Zulu (2001)"]
    assert zulu.added_at == datetime.fromtimestamp((now - 30 * 86_400 * 10**9) // 1000 / 10**6, UTC).replace(tzinfo=None)


def test_multi_episode_file(admin: TestClient, media: Path) -> None:
    write(media / "TV" / "Omega" / "Season 1" / "Omega S01E01-E02 - Two Parter.mkv")
    scan(admin)
    episode = by_key()["TV/Omega#s1e1"]
    assert (episode.index_number, episode.index_number_end, episode.name) == (1, 2, "Two Parter")
    item = library_items()["Two Parter"]
    assert admin.get(f"/api/library/{item.id}").json()["metadata_json"]["episode_number_end"] == 2


def test_movie_with_two_versions_is_one_title(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995) - 1080p.mkv", b"hd")
    write(folder / "Heat (1995) - 4K.mkv", b"uhd")
    scan(admin)
    titles, items = by_key(), library_items()
    movie = titles["Movies/Heat (1995)"]
    assert (movie.name, movie.year, len(titles)) == ("Heat", 1995, 1)
    assert {items["Heat · 1080p"].title_id, items["Heat · 4K"].title_id} == {movie.id}


def test_extras_by_folder_and_suffix_are_hidden_from_grids(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    write(folder / "Trailers" / "Teaser.mkv", b"teaser")
    write(folder / "Heat (1995)-featurette.mkv", b"feat")
    write(media / "TV" / "Omega" / "tvshow.nfo", "<tvshow><title>Omega</title></tvshow>")
    write(media / "TV" / "Omega" / "Extras" / "Making Of.mkv", b"making")
    scan(admin)
    titles, items = by_key(), library_items()
    movie = titles["Movies/Heat (1995)"].id
    assert (items["Teaser"].extra_type, items["Teaser"].title_id) == ("trailer", movie)
    assert (items["Heat (1995)-featurette"].extra_type, items["Heat (1995)-featurette"].title_id) == ("featurette", movie)
    assert (items["Making Of"].extra_type, items["Making Of"].title_id) == ("other", titles["TV/Omega"].id)
    extras = {"Teaser", "Heat (1995)-featurette", "Making Of"}
    for view in ("video", "movie"):
        assert not {entry["title"] for entry in admin.get("/api/library", params={"kind": view}).json()["items"]} & extras
    assert [entry["title"] for entry in admin.get("/api/library", params={"kind": "movie"}).json()["items"]] == ["Heat"]


def test_ignore_marker_and_skipped_names(admin: TestClient, media: Path) -> None:
    write(media / "Movies" / "Heat (1995).mkv", b"heat")
    write(media / "Movies" / "Private" / ".ignore", b"")
    write(media / "Movies" / "Private" / "Secret (2001).mkv", b"secret")
    write(media / "Movies" / "backdrops" / "loop.mp4", b"loop")
    write(media / "TV" / "Omega" / "theme-music" / "song.mp3", b"song")
    write(media / "TV" / "Omega" / "theme.mp3", b"theme")
    write(media / "Movies" / "Heat (1995)-sample.mkv", b"sample")
    assert scan(admin)["counters"]["inspected"] == 1
    assert list(library_items()) == ["Heat"]


def test_renamed_show_folder_keeps_all_ids(admin: TestClient, media: Path) -> None:
    show = media / "TV" / "Omega"
    write(show / "Season 1" / "Omega S01E01.mkv", b"e1")
    write(show / "Season 1" / "Omega S01E02.mkv", b"e2")
    scan(admin)
    before = {key: title.id for key, title in by_key().items()}
    item_id = library_items()["Omega S01E01"].id

    show.rename(media / "TV" / "Omega (2005)")
    assert scan(admin)["counters"]["relinked"] == 2

    after = {key: title.id for key, title in by_key().items()}
    assert sorted(after.values()) == sorted(before.values())  # re-keyed, nothing new
    assert after["TV/Omega (2005)"] == before["TV/Omega"] and after["TV/Omega (2005)#s1e2"] == before["TV/Omega#s1e2"]
    assert library_items()["Omega S01E01"].id == item_id


def test_noop_rescan_creates_nothing(admin: TestClient, media: Path) -> None:
    write(media / "TV" / "Omega" / "Season 1" / "Omega S01E01.mkv", b"e1")
    write(media / "TV" / "Omega" / "poster.jpg", b"\xff\xd8p")
    write(media / "Movies" / "Heat (1995)" / "Heat (1995).mkv", b"heat")
    write(media / "Movies" / "Heat (1995)" / "movie.nfo", "<movie><title>Heat</title><set>Saga</set></movie>")
    scan(admin)

    def snapshot() -> dict:
        return {t.id: (t.key, t.parent_id, t.boxset_id, t.name, t.field_sources, t.images, t.provider_ids) for t in by_key().values()}

    first = snapshot()
    assert len(first) == 5  # series, season, episode, boxset, movie
    assert scan(admin)["counters"]["unchanged"] == 2
    assert snapshot() == first


def test_private_import_invisible_until_shared(admin: TestClient, media: Path) -> None:
    write(media / "TV" / "Secret Show" / "Season 1" / "Secret Show S01E01.mkv")
    scan(admin)  # scan() imports as private
    with db_module.SessionLocal() as db:
        def visible(user_id: str) -> set[str]:
            user = db.get(User, user_id)
            return set(db.scalars(select(MediaTitle.id).where(LibraryService.visible_title_predicate(user))))

        everything = set(db.scalars(select(MediaTitle.id)))
        assert len(everything) == 3 and visible("admin") == everything and visible("member") == set()
        db.query(LibraryItem).update({LibraryItem.visibility: "shared"})
        db.commit()
        assert visible("member") == everything


def test_user_name_survives_an_nfo_change(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    write(folder / "movie.nfo", "<movie><title>Heat</title></movie>")
    scan(admin)
    with db_module.SessionLocal() as db:
        movie = db.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        apply_field(movie, "name", "Heat (director's pick)", "user")
        db.commit()
    write(folder / "movie.nfo", "<movie><title>Heat!</title><sorttitle>Heat 2</sorttitle></movie>")
    scan(admin)
    movie = by_key()["Movies/Heat (1995)"]
    assert (movie.name, movie.field_sources["name"], movie.sort_name) == ("Heat (director's pick)", "user", "Heat 2")


def test_reclassified_file_unlinks_title(admin: TestClient, media: Path) -> None:
    write(media / "Heat (1995).mkv", b"film")
    scan(admin)
    movie_id = by_key()["Heat (1995)"].id
    (media / "Heat (1995).mkv").rename(media / "holiday.mkv")
    scan(admin)
    assert library_items()["holiday"].title_id is None
    assert by_key()["Heat (1995)"].id == movie_id  # titles are never deleted...
    with db_module.SessionLocal() as db:  # ...but one with no linked item is invisible, even to its importer
        assert db.scalars(select(MediaTitle.id).where(LibraryService.visible_title_predicate(db.get(User, "admin")))).all() == []


SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n2\n00:00:03,000 --> 00:00:04,000\nGeneral Kenobi.\n"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHallo.\n"


def test_sidecar_subtitles_become_transcripts(admin: TestClient, media: Path, tmp_path: Path) -> None:
    folder = media / "TV" / "Omega" / "Season 1"
    stem = "Omega S01E01"
    write(folder / f"{stem}.mkv", b"e1")
    write(folder / f"{stem}.en.srt", SRT)
    write(folder / f"{stem}.de.vtt", VTT)
    write(folder / f"{stem}.fr.ass", "[Script Info]")  # ASS is a track, not a transcript
    write(folder / f"{stem}.es.srt", b"\xff\xfe not utf-8")  # skipped, never fails the scan
    outside = tmp_path / "outside.srt"
    outside.write_text(SRT)
    (folder / f"{stem}.it.srt").symlink_to(outside)  # never followed
    long_stem = "Omega S01E02 - " + "a" * 90
    write(folder / f"{long_stem}.mkv", b"e2")
    write(folder / f"{long_stem}.en.srt", SRT)
    assert scan(admin)["state"] == "succeeded"

    def rows() -> list[tuple]:
        with db_module.SessionLocal() as db:
            return sorted((t.language, t.source_kind, t.derived_from) for t in db.query(Transcript))

    first = rows()
    assert first == sorted([
        ("de", "source_caption", f"sidecar:{stem}.de.vtt"),
        ("en", "source_caption", f"sidecar:{stem}.en.srt"),
        ("en", "source_caption", f"sidecar:{long_stem}.en.srt"[:80]),
    ])
    item = library_items()[stem]
    assert [entry["filename"] for entry in item.metadata_json["lumina_subtitles"]] == [
        f"{stem}.de.vtt", f"{stem}.en.srt", f"{stem}.es.srt", f"{stem}.fr.ass"]
    assert "lumina_subtitles" not in admin.get(f"/api/library/{item.id}").json()["metadata_json"]  # filenames never reach members
    assert scan(admin)["state"] == "succeeded" and rows() == first  # a no-op rescan stores nothing new


def test_skipped_sidecar_log_names_the_error_type_only(
    admin: TestClient, media: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Review 25: an OSError's text names the private path; a DB error's names cue text. Log the type only."""
    write(media / "TV" / "Omega" / "Season 1" / "Omega S01E01.mkv", b"e1")
    write(media / "TV" / "Omega" / "Season 1" / "Omega S01E01.en.srt", SRT)

    def unreadable(data: bytes, fmt: str) -> list:
        raise PermissionError(13, "Permission denied", "/private/Omega S01E01.en.srt General Kenobi")

    monkeypatch.setattr(library_import, "parse_caption", unreadable)
    assert scan(admin)["state"] == "succeeded"
    skipped = [record.getMessage() for record in caplog.records if "subtitle sidecar" in record.getMessage()]
    assert skipped and all("PermissionError" in message for message in skipped)
    assert "Omega S01E01.en.srt" not in caplog.text and "Kenobi" not in caplog.text


def test_after_import_hooks_run_once_per_finished_run(admin: TestClient, media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def broken(run_id: str) -> None:
        raise RuntimeError("a hook failure never fails a scan")

    monkeypatch.setattr(library_import, "after_import_hooks", [broken, calls.append])
    write(media / "Heat (1995).mkv")
    run = scan(admin)
    assert run["state"] == "succeeded" and calls == [run["id"]]


@pytest.mark.parametrize(("candidates", "available", "held"), [
    (26, 30, True), (25, 30, False), (26, 130, False), (27, 130, True), (1, 1, False),
])
def test_mass_missing_threshold(candidates: int, available: int, held: bool) -> None:
    assert mass_missing(candidates, available) is held


def half_synced(admin: TestClient, media: Path) -> None:
    for index in range(30):
        write(media / "clips" / f"clip{index:02d}.mp4", f"clip{index}".encode())
    assert scan(admin)["counters"]["indexed"] == 30
    for index in range(26):
        (media / "clips" / f"clip{index:02d}.mp4").unlink()


def statuses() -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in library_items().values():
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def test_mass_missing_waits_for_confirmation(admin: TestClient, media: Path) -> None:
    half_synced(admin, media)
    held = scan(admin)
    assert held["state"] == "needs_confirmation"
    assert (held["counters"]["missing_candidates"], held["counters"]["available"], held["counters"].get("missing")) == (26, 30, None)
    assert statuses() == {"available": 30}  # an unmounted or half-synced share is not a deletion

    confirmed = admin.post(f"/api/admin/imports/{held['id']}/confirm")
    assert confirmed.status_code == 202, confirmed.text
    assert (confirmed.json()["state"], confirmed.json()["counters"]["missing"]) == ("succeeded", 26)
    assert statuses() == {"available": 4, "missing": 26}
    assert admin.post(f"/api/admin/imports/{held['id']}/confirm").status_code == 409
    assert admin.post("/api/admin/imports/nope/confirm").status_code == 404


def test_stale_confirmation_is_refused(admin: TestClient, media: Path) -> None:
    half_synced(admin, media)
    older = scan(admin)
    newer = scan(admin)
    assert (older["state"], newer["state"]) == ("needs_confirmation", "needs_confirmation")
    # Marks are relative to the run's own last_seen stamps: confirming the older run after a newer
    # one would tombstone every file the newer run saw.
    assert admin.post(f"/api/admin/imports/{older['id']}/confirm").status_code == 409
    assert statuses() == {"available": 30}
    assert admin.post(f"/api/admin/imports/{newer['id']}/confirm").status_code == 202
    member = TestClient(app, base_url="http://localhost")
    member.auth = ("member", PASSWORD)
    assert member.post(f"/api/admin/imports/{newer['id']}/confirm").status_code == 403


def test_a_re_keyed_file_never_takes_a_shared_title(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"heat")
    write(folder / "Collateral (2004).mkv", b"collateral")
    scan(admin)
    heat_id = by_key()["Movies/Heat (1995)"].id
    with db_module.SessionLocal() as db:  # as an older rule would have left it: Collateral merged into Heat
        collateral = db.scalars(select(MediaTitle).where(MediaTitle.name == "Collateral")).one()
        db.query(LibraryItem).filter(LibraryItem.title_id == collateral.id).update({LibraryItem.title_id: heat_id})
        db.delete(collateral)
        db.commit()
    scan(admin)
    titles = by_key()
    assert titles["Movies/Heat (1995)"].id == heat_id and titles["Movies/Heat (1995)/Collateral (2004)"].id != heat_id
    assert library_items()["Heat"].title_id == heat_id


def test_a_re_keyed_episode_never_drags_its_season_along(admin: TestClient, media: Path) -> None:
    """Round-2 re-review of 24: a non-move key change re-keys only the leaf; shared ancestors stay put."""
    show = media / "TV" / "Show"
    write(show / "tvshow.nfo", "<tvshow><title>Show</title></tvshow>")
    write(show / "Show S01E01.mkv", b"e1")
    write(show / "Show S01E02.mkv", b"e2")
    scan(admin)
    before = by_key()
    season1, episode2 = before["TV/Show#s1"].id, before["TV/Show#s1e2"].id
    write(show / "Show S01E02.nfo", "<episodedetails><title>Moved</title><season>2</season><episode>1</episode></episodedetails>")
    scan(admin)
    after = by_key()
    assert after["TV/Show#s1"].id == season1 and after["TV/Show#s2"].id != season1
    assert after["TV/Show#s2e1"].id == episode2 and after["TV/Show#s2e1"].parent_id == after["TV/Show#s2"].id
    assert library_items()["Show S01E01"].title_id == after["TV/Show#s1e1"].id and after["TV/Show#s1e1"].parent_id == season1


def lock_the_movie() -> None:
    with db_module.SessionLocal() as db:
        movie = db.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        movie.locked = True
        db.commit()


def test_a_locked_title_stays_linked_but_keeps_its_fields(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    write(folder / "movie.nfo", "<movie><title>Heat</title><plot>One.</plot></movie>")
    scan(admin)
    lock_the_movie()
    write(folder / "movie.nfo", "<movie><title>Heat!</title><plot>Two.</plot><year>1996</year></movie>")
    write(folder / "poster.jpg", b"jpg")
    write(folder / "Heat (1995) 2.mkv", b"film2")
    scan(admin)
    movie = by_key()["Movies/Heat (1995)"]
    assert (movie.name, movie.metadata_json["overview"], movie.year) == ("Heat", "One.", 1995)  # year from the folder name; the NFO 1996 is blocked
    assert movie.images == {} and movie.locked
    with db_module.SessionLocal() as db:
        movie = db.get(MediaTitle, movie.id)
        assert movie.source_values["name"] == {"source": "nfo", "value": "Heat!"}
        assert {item.title_id for item in db.scalars(select(LibraryItem))} == {movie.id}
    with db_module.SessionLocal() as db:
        row = db.get(MediaTitle, movie.id)
        set_item_locked(row, False, datetime(2026, 10, 2))
        db.commit()
    assert by_key()["Movies/Heat (1995)"].name == "Heat!"


def test_an_unchanged_rescan_of_a_locked_item_writes_nothing(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    write(folder / "movie.nfo", "<movie><title>Heat</title></movie>")
    scan(admin)
    lock_the_movie()
    write(folder / "movie.nfo", "<movie><title>Heat!</title></movie>")
    scan(admin)
    statements: list[str] = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(db_module.engine, "before_cursor_execute", listener)
    try:
        scan(admin)
    finally:
        event.remove(db_module.engine, "before_cursor_execute", listener)
    assert not [s for s in statements if s.lstrip().upper().startswith("UPDATE MEDIA_TITLES") and "source_values" in s]


def test_a_removed_image_is_not_readded_by_a_sidecar_scan(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    scan(admin)
    with db_module.SessionLocal() as db:
        movie = db.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        apply_field(movie, "images.Primary", {"removed": True, "tag": "removed:1"}, "user")
        db.commit()
    write(folder / "poster.jpg", b"jpg")
    scan(admin)
    assert by_key()["Movies/Heat (1995)"].images["Primary"] == {"removed": True, "tag": "removed:1"}


def test_a_scan_never_redates_a_locked_title_but_still_categorises_it(admin: TestClient, media: Path) -> None:
    folder = media / "Movies" / "Heat (1995)"
    write(folder / "Heat (1995).mkv", b"film")
    scan(admin)
    with db_module.SessionLocal() as db:
        movie = db.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        movie.locked, movie.added_at = True, datetime(2035, 1, 1)
        db.commit()
    os.utime(folder / "Heat (1995).mkv", (1_700_000_000, 1_700_000_000))
    scan(admin)
    movie = by_key()["Movies/Heat (1995)"]
    assert movie.added_at == datetime(2035, 1, 1) and movie.category == "movies"
