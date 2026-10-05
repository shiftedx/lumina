"""Cast photos NFO credit types and thumbs, nfo_people, the people folder,
person portraits, title credits and Infuse person images."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import db as db_module
from app.config import settings
from app.db import Base
from app.main import app
from app.main import artwork as app_artwork
from app.models import MediaTitle, NfoPerson, Person, TitleArtwork
from app.services import art_urls, cast_photos, library_search, renditions
from app.services.local_metadata import nfo_title_fields, parse_nfo, people_folder_guess, person_thumb
from app.services.media_titles import jellyfin_id, person_name_id, synthetic_id
from art_support import add_art_title, art_url, fake_calls, png_header, seed_art_root, use_fake_ffmpeg
from support import make_user
from test_album_detail import member, selects
from test_jellyfin_integration import assert_jellyfin_keys
from test_renditions import fresh_serving  # noqa: F401 - a fixture
from test_v1_scan import client_for, library, register_root, write  # noqa: F401 - library is a fixture
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, MOVIE, SECRET_SERIES, jellyfin_household, mediabrowser, seed_tree

JELLYFIN = "/config/data/metadata/People"


def credits(tmp_path: Path, actors: str) -> list[dict]:
    path = tmp_path / "movie.nfo"
    path.write_text(f"<movie><title>X</title>{actors}</movie>", encoding="utf-8")
    tag, fields = parse_nfo(path)
    return nfo_title_fields(tag, fields).get("people", [])


def test_credits_keep_their_type_role_and_thumb(tmp_path: Path) -> None:
    people = credits(tmp_path, (
        f"<actor><name>Ada Rook</name><role>Keeper</role><type>Actor</type><thumb>{JELLYFIN}/A/Ada Rook/folder.png</thumb></actor>"
        f"<actor><name>Andre Coutu</name><role>Post Producer</role><type>Producer</type>"
        f"<thumb>{JELLYFIN}/A/Andre Coutu/folder.png</thumb></actor>"
        "<actor><name>Kit Halloran</name><type>gueststar</type></actor>"
        "<actor><name>No Type</name></actor>"
        "<actor><name>Sam Lyric</name><type>Lyricist</type></actor>"
    ))
    assert people == [
        {"person_id": None, "name": "Ada Rook", "role": "Keeper", "type": "Actor", "thumb": {"path": "A/Ada Rook/folder.png"}},
        {"person_id": None, "name": "Andre Coutu", "role": "Post Producer", "type": "Producer", "thumb": {"path": "A/Andre Coutu/folder.png"}},
        {"person_id": None, "name": "Kit Halloran", "role": None, "type": "GuestStar", "thumb": None},
        {"person_id": None, "name": "No Type", "role": None, "type": "Actor", "thumb": None},
    ]  # a type Lumina does not show (Lyricist) is skipped


def crew_of(tmp_path: Path, xml: str) -> list[dict]:
    path = tmp_path / "movie.nfo"
    path.write_text(f"<movie><title>X</title>{xml}</movie>", encoding="utf-8")
    tag, fields = parse_nfo(path)
    return nfo_title_fields(tag, fields).get("crew", [])


def test_directors_and_writers_are_read_into_their_own_display_field(tmp_path: Path) -> None:
    crew = crew_of(tmp_path, (
        "<director>Ines Varga</director><credits>Wren Hale</credits><writer>Wren Hale</writer>"
        "<director> ines  varga </director><writer>Omar Pike</writer><director></director>"
        "<actor><name>Ada Rook</name></actor>"
    ))
    assert crew == [
        {"person_id": None, "name": "Ines Varga", "job": "Director"},
        {"person_id": None, "name": "Wren Hale", "job": "Writer"},
        {"person_id": None, "name": "Omar Pike", "job": "Writer"},
    ]  # <credits> and <writer> name the same writer once; case and spacing do not make a second director
    many = "".join(f"<director>D{i}</director>" for i in range(50))
    assert len(crew_of(tmp_path, many)) == 10


@pytest.mark.parametrize(("name", "expected"), [
    ("Ines Varga", "I/Ines Varga/folder.jpg"),
    ("ada rook", "A/ada rook/folder.jpg"),
    ("AC/DC", None),  # a name Jellyfin could not have used as one folder
    ("..", None),
    ("", None),
])
def test_a_crew_name_guesses_its_jellyfin_people_folder(name: str, expected: str | None) -> None:
    assert people_folder_guess(name) == expected


def test_five_hundred_credits_keep_the_first_twenty_shown(tmp_path: Path) -> None:
    actors = "".join(
        f"<actor><name>P{i}</name><type>{'Lyricist' if i % 2 else 'Actor'}</type><thumb>{JELLYFIN}/P/P{i}/folder.jpg</thumb></actor>"
        for i in range(500)
    )
    assert [person["name"] for person in credits(tmp_path, actors)] == [f"P{i}" for i in range(0, 40, 2)]


@pytest.mark.parametrize(("thumb", "expected"), [
    (f"{JELLYFIN}/A/Ada Rook/folder.jpg", {"path": "A/Ada Rook/folder.jpg"}),
    ("/var/lib/jellyfin/metadata/People/B/Bo/folder.png", {"path": "B/Bo/folder.png"}),  # a native Jellyfin install
    ("C:\\ProgramData\\Jellyfin\\Server\\metadata\\People\\C\\Cy\\folder.webp", {"path": "C/Cy/folder.webp"}),
    (f"  {JELLYFIN}/D/Di/folder.JPEG  ", {"path": "D/Di/folder.JPEG"}),
    ("https://image.tmdb.org/t/p/original/kqjL17yufvn9OVLyXYpvtyrFfak.jpg", {"tmdb": "/kqjL17yufvn9OVLyXYpvtyrFfak.jpg"}),
    ("http://image.tmdb.org/t/p/w185/abc.png", {"tmdb": "/abc.png"}),
    (f"{JELLYFIN}/../../../etc/passwd.jpg", None),
    (f"{JELLYFIN}/A/../x.jpg", None),
    (f"{JELLYFIN}/A/./x.jpg", None),
    (f"{JELLYFIN}//x.jpg", None),
    (f"{JELLYFIN}/A/Ada/folder.gif", None),
    (f"{JELLYFIN}/A/Ada/folder", None),
    (f"{JELLYFIN}/" + "a/" * 5 + "x.jpg", None),  # deeper than <Initial>/<Name>/<file>
    (f"{JELLYFIN}/A/{'n' * 600}/folder.jpg", None),
    ("/home/someone/ada.jpg", None),
    ("https://example.com/t/p/w185/abc.jpg", None),
    ("https://image.tmdb.org.evil.example/t/p/w185/abc.jpg", None),
    ("https://image.tmdb.org/t/p/w185/../abc.jpg", None),
    ("", None),
    (None, None),
])
def test_person_thumb_accepts_only_jellyfin_people_paths_and_tmdb_images(thumb: str | None, expected: dict | None) -> None:
    assert person_thumb(thumb) == expected


PHOTO = png_header(400, 600)
CREDITS = (
    f"<actor><name>Ada Rook</name><role>Keeper</role><type>Actor</type><thumb>{JELLYFIN}/A/Ada Rook/folder.png</thumb></actor>"
    f"<actor><name>Andre Coutu</name><role>Post Producer</role><type>Producer</type><thumb>{JELLYFIN}/A/Andre Coutu/folder.png</thumb></actor>"
    "<actor><name>ada  rook</name><role>Twin</role></actor>"
    "<director>Ines Varga</director><credits>Andre Coutu</credits>"
)


@pytest.fixture
def people(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Jellyfin's People folder as the box mounts it, with two photos; the tables on conftest's per-test database."""
    folder = tmp_path.resolve() / "jellyfin-people"
    for relative in ("A/Ada Rook/folder.png", "A/Andre Coutu/folder.png"):
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(PHOTO)
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(folder))
    Base.metadata.create_all(bind=db_module.engine)
    return folder


def photo(session, image_path: str | None = None, tmdb_path: str | None = None):  # noqa: ANN001, ANN201
    """An nfo_people row named after its photo, as cast_photos.subject presents it."""
    name = image_path or tmdb_path or "No Photo"
    session.merge(NfoPerson(id=person_name_id(name), name=name, image_path=image_path, tmdb_path=tmdb_path))
    session.commit()
    return cast_photos.subject(session, person_name_id(name))


def import_root(admin, root_id: str) -> None:  # noqa: ANN001
    started = admin.post("/api/admin/imports", json={"root_id": root_id})
    assert started.status_code == 202, started.text
    run = admin.get(started.json()["status_url"]).json()
    assert run["state"] == "succeeded", run


def test_a_scan_links_credits_to_people_rows_and_a_rescan_backfills_old_refs(library: Path, people: Path) -> None:
    write(library / "Movies" / "Heat (1995)" / "Heat (1995).mkv", b"heat-bytes")
    write(library / "Movies" / "Heat (1995)" / "Heat (1995).nfo", f"<movie><title>Heat</title>{CREDITS}</movie>".encode())
    ada, andre = person_name_id("Ada Rook"), person_name_id("Andre Coutu")
    assert person_name_id("ada  rook") == ada
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        import_root(admin, root_id)
        with db_module.SessionLocal() as session:
            movie = session.scalar(select(MediaTitle).where(MediaTitle.type == "movie"))
            movie_id = movie.id
            assert movie.metadata_json["people"] == [
                {"person_id": ada, "name": "Ada Rook", "role": "Keeper", "type": "Actor"},
                {"person_id": andre, "name": "Andre Coutu", "role": "Post Producer", "type": "Producer"},
                {"person_id": ada, "name": "ada  rook", "role": "Twin", "type": "Actor"},
            ]  # no thumb in any title's refs: it lives once, on the person's row
            ines = person_name_id("Ines Varga")
            assert movie.metadata_json["crew"] == [
                {"person_id": ines, "name": "Ines Varga", "job": "Director"}, {"person_id": andre, "name": "Andre Coutu", "job": "Writer"},
            ]
            assert {person.id: (person.name, person.image_path) for person in session.scalars(select(NfoPerson))} == {
                ada: ("Ada Rook", "A/Ada Rook/folder.png"), andre: ("Andre Coutu", "A/Andre Coutu/folder.png"),
                ines: ("Ines Varga", "I/Ines Varga/folder.jpg"),  # guessed: crew elements carry no thumb
            }  # Ada's thumb-less second credit did not erase her photo; Andre's thumb beat his writer guess
            lookup = lambda title_id: session.get(MediaTitle, title_id) if title_id else None  # noqa: E731
            assert "Ines" not in library_search.title_search_fields(movie, lookup)["people"]  # crew is display only
            # What 1.5.1 stored, before this track:
            movie.metadata_json = {**movie.metadata_json, "people": [
                {"person_id": None, "name": "Ada Rook", "role": "Keeper", "type": "Actor"},
                {"person_id": None, "name": "Andre Coutu", "role": "Post Producer", "type": "Actor"},
                {"person_id": None, "name": "ada  rook", "role": "Twin", "type": "Actor"},
            ]}
            session.execute(delete(NfoPerson))
            session.commit()
        import_root(admin, root_id)  # a plain rescan: every NFO is read again, unchanged files included
        with db_module.SessionLocal() as session:
            refs = session.get(MediaTitle, movie_id).metadata_json["people"]
            assert [(ref["person_id"], ref["type"]) for ref in refs] == [(ada, "Actor"), (andre, "Producer"), (ada, "Actor")]
            assert session.get(NfoPerson, andre).image_path == "A/Andre Coutu/folder.png"


def test_photo_files_are_read_only_inside_the_people_folder(people: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.png"
    outside.write_bytes(PHOTO)
    (people / "L").mkdir()
    (people / "L" / "link.png").symlink_to(outside)
    (people / "D").symlink_to(tmp_path, target_is_directory=True)
    (people / "B" / "Big").mkdir(parents=True)
    (people / "B" / "Big" / "folder.png").write_bytes(PHOTO + b"\0" * cast_photos.PHOTO_MAX_BYTES)
    (people / "F" / "Dir.png").mkdir(parents=True)  # a folder named like a photo
    (people / "G").mkdir()
    (people / "G" / "x.gif").write_bytes(b"GIF89a")
    with db_module.SessionLocal() as session:
        assert cast_photos.image_bytes(photo(session, "A/Ada Rook/folder.png"), app_artwork) == ("image/png", PHOTO)
        for unsafe in ("../secret.png", "A/../../secret.png", str(outside), "L/link.png", "D/secret.png",
                       "B/Big/folder.png", "F/Dir.png", "G/x.gif", "A/Nobody/folder.png"):
            with pytest.raises(FileNotFoundError):
                cast_photos.image_bytes(photo(session, unsafe), app_artwork)


def test_no_folder_means_no_photo_and_an_empty_folder_is_offline(people: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with db_module.SessionLocal() as session:
        ada = photo(session, "A/Ada Rook/folder.png")
        assert ada.images == {"Primary": {"path": "A/Ada Rook/folder.png"}} and cast_photos.people_dir_online()
        monkeypatch.setattr(settings, "jellyfin_people_dir", "")
        assert cast_photos.subject(session, ada.id).images == {} and not cast_photos.people_dir_online()
        with pytest.raises(FileNotFoundError):
            cast_photos.image_bytes(cast_photos.subject(session, ada.id), app_artwork)
    unmounted = tmp_path / "unmounted"
    unmounted.mkdir()  # a bind mount of a missing host folder shows up empty
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(unmounted))
    assert not cast_photos.people_dir_online()


def test_a_tmdb_photo_is_offered_only_while_tmdb_is_on_and_never_over_a_local_one(people: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with db_module.SessionLocal() as session:
        both = photo(session, "A/Ada Rook/folder.png", "/ada.jpg")
        only = photo(session, None, "/bo.jpg")
        assert both.images == {"Primary": {"path": "A/Ada Rook/folder.png"}} and only.images == {}
        monkeypatch.setattr(settings, "tmdb_api_key", "test-key")
        assert cast_photos.subject(session, only.id).images == {"Primary": {"tmdb": "/bo.jpg"}}
        monkeypatch.setattr(settings, "jellyfin_people_dir", "")
        assert cast_photos.subject(session, both.id).images == {"Primary": {"tmdb": "/ada.jpg"}}


def test_a_tmdb_thumb_beats_a_crew_folder_guess_in_the_same_batch_and_a_later_one(people: Path, tmp_path: Path) -> None:
    nfo = tmp_path / "movie.nfo"
    nfo.write_text("<movie><title>X</title><actor><name>Tove Lind</name><thumb>https://image.tmdb.org/t/p/w185/tove.jpg"
                   "</thumb></actor><director>Tove Lind</director></movie>", encoding="utf-8")
    fields = nfo_title_fields(*parse_nfo(nfo))
    photos: dict[str, dict] = {}
    cast_photos.split_people(fields["people"], photos)
    cast_photos.split_crew(fields["crew"], photos)
    tove = person_name_id("Tove Lind")
    with db_module.SessionLocal() as session:
        cast_photos.save_people(session, photos)
        session.commit()
        assert (session.get(NfoPerson, tove).image_path, session.get(NfoPerson, tove).tmdb_path) == (None, "/tove.jpg")
        later: dict[str, dict] = {}
        cast_photos.split_crew(fields["crew"], later)  # a later title credits her only as crew
        cast_photos.save_people(session, later)
        session.commit()
        assert session.get(NfoPerson, tove).image_path is None


TITLE = str(uuid.UUID(int=0xA1))
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def fresh_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)


def _row(subject_id: str) -> TitleArtwork | None:
    with db_module.SessionLocal() as session:
        return session.get(TitleArtwork, (subject_id, "Primary"))


def test_a_person_photo_renders_two_exact_portraits_without_preview_or_colours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    rendered = renditions.render(png_header(400, 500), "image/png", title_type="person", image_type="Primary", timeout=10,
                                 ffmpeg=renditions.media_tool(None, "ffmpeg"))
    argv = fake_calls(log)[0]["argv"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "crop='min(iw,ih*2/3)':'min(ih,iw*3/2)'" in graph and "scale=120:180" in graph and "scale=240:360" in graph
    assert sorted(rendered.files) == [120, 240] and len([token for token in argv if "/.staging/" in token]) == 2
    assert (rendered.width, rendered.height, rendered.preview, rendered.dominant) == (333, 499, None, None)
    renditions.discard(rendered)


@needs_ffmpeg
def test_real_ffmpeg_makes_exact_two_by_three_portraits(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    source = tmp_path / "face.png"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=500x501",
                    "-frames:v", "1", str(source)], check=True)
    ext = "webp" if renditions.has_libwebp(ffmpeg) else "jpg"
    rendered = renditions.render(source.read_bytes(), "image/png", title_type="person", image_type="Primary", timeout=20, ext=ext, ffmpeg=ffmpeg)
    try:
        assert {width: renditions.image_size(path.read_bytes()) for width, path in rendered.files.items()} == {120: (120, 180), 240: (240, 360)}
    finally:
        renditions.discard(rendered)


def test_the_pass_prepares_people_after_every_title_and_never_counts_them(people: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_ffmpeg(tmp_path, monkeypatch)
    media = tmp_path.resolve() / "media"
    seed_art_root(media)
    add_art_title(media, TITLE)
    with db_module.SessionLocal() as session:
        ada = photo(session, "A/Ada Rook/folder.png").id
        photo(session, None, "/bo.jpg")  # a TMDB-only photo: the pass never fetches, on-demand does
    with db_module.SessionLocal() as session:
        found = renditions.scan(session)
    assert found.order == [(TITLE, "Primary"), (ada, "Primary")] and found.total == 1
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() and worker.run_once() and not worker.run_once()
    row = _row(ada)
    assert (row.state, row.width, row.height, row.preview, row.dominant) == ("ready", 400, 600, None, None)
    assert art_urls.rendition_file(row.source_key, 120, "webp").read_bytes() == b"r" * 64


def test_an_unmounted_folder_skips_people_and_a_missing_file_fails_once(people: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_ffmpeg(tmp_path, monkeypatch)
    with db_module.SessionLocal() as session:
        ada = photo(session, "A/Ada Rook/folder.png").id
        gone = photo(session, "Z/Gone/folder.png").id
    unmounted = tmp_path / "unmounted"
    unmounted.mkdir()
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(unmounted))
    with db_module.SessionLocal() as session:
        assert renditions.scan(session).order == []
    renditions.ArtworkRenditions()._prepare(ada, "Primary")
    assert _row(ada) is None  # skipped, not failed: prepared once the folder is back
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(people))
    worker = renditions.ArtworkRenditions()
    while worker.run_once():
        pass
    assert (_row(ada).state, _row(gone).state, _row(gone).error) == ("ready", "failed", "unreadable")


def test_the_sweep_keeps_person_rows_and_their_files(people: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_ffmpeg(tmp_path, monkeypatch)
    with db_module.SessionLocal() as session:
        ada = photo(session, "A/Ada Rook/folder.png").id
    assert renditions.ArtworkRenditions().run_once()
    key = _row(ada).source_key
    renditions.ArtworkRenditions().sweep()
    assert _row(ada) is not None and art_urls.rendition_file(key, 240, "webp").is_file()


def test_signed_art_serves_a_person_photo_at_both_widths_and_nothing_else(
    people: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_serving: None,  # noqa: ARG001
) -> None:
    use_fake_ffmpeg(tmp_path, monkeypatch)
    with db_module.SessionLocal() as session:
        ada = photo(session, "A/Ada Rook/folder.png").id
    template = art_urls.rendition_template(ada, "Primary", art_urls.source_key(ada, "Primary", "A/Ada Rook/folder.png"))
    client = TestClient(app, base_url="http://localhost")
    for width in (120, 240):
        response = client.get(template.replace("{w}", str(width)))
        assert response.status_code == 200 and response.content == b"r" * 64
    assert client.get(template.replace("{w}", "480")).status_code == 404  # a poster width on a person
    assert client.get(art_urls.rendition_template(ada, "Primary", "0" * 64).replace("{w}", "120")).status_code == 404
    client.close()


AMY = synthetic_id("tmdb-person:9273")


@pytest.fixture
def titled(db_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """title_support's tree on the in-memory database; MOVIE is what these tests credit people on."""
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(tmp_path))  # configured: image_urls reads no file
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, tmp_path.resolve() / "media")
        session.commit()
    return db_factory


def credit(factory, refs: list[dict], rows: list = ()) -> None:  # noqa: ANN001
    with factory() as session:
        title = session.get(MediaTitle, MOVIE)
        title.metadata_json = {**(title.metadata_json or {}), "people": refs}
        title.field_sources = {**(title.field_sources or {}), "people": "nfo"}
        session.add_all(rows)
        session.commit()


def ref(name: str, kind: str = "Actor", role: str | None = None) -> dict:
    return {"person_id": person_name_id(name), "name": name, "role": role, "type": kind}


def test_title_detail_gives_nfo_people_a_signed_portrait_and_hides_failed_ones(titled, api_client) -> None:  # noqa: ANN001
    ada, andre, kit = (person_name_id(name) for name in ("Ada Rook", "Andre Coutu", "Kit Halloran"))
    credit(titled, [ref("Ada Rook", role="Keeper"), ref("Andre Coutu", "Producer", "Post Producer"), ref("Kit Halloran", "GuestStar")], [
        NfoPerson(id=ada, name="Ada Rook", image_path="A/Ada Rook/folder.png"),
        NfoPerson(id=andre, name="Andre Coutu", image_path="A/Andre Coutu/folder.png"),
        NfoPerson(id=kit, name="Kit Halloran"),
        TitleArtwork(title_id=andre, image_type="Primary", source_key=art_urls.source_key(andre, "Primary", "A/Andre Coutu/folder.png"),
                     state="failed", error="unreadable"),
    ])
    people = member(api_client, titled, ALICE).get(f"/api/titles/{MOVIE}").json()["people"]
    assert people == [
        {"id": ada, "name": "Ada Rook", "role": "Keeper", "type": "Actor", "image_url": art_url(ada, "Primary", "A/Ada Rook/folder.png", 240)},
        {"id": andre, "name": "Andre Coutu", "role": "Post Producer", "type": "Producer", "image_url": None},
        {"id": kit, "name": "Kit Halloran", "role": None, "type": "GuestStar", "image_url": None},
    ]


def test_nfo_directors_and_writers_join_the_credits_after_the_cast_with_their_photos(titled, api_client) -> None:  # noqa: ANN001
    ines, wren = person_name_id("Ines Varga"), person_name_id("Wren Hale")
    credit(titled, [ref("Ada Rook"), ref("Wren Hale", "Writer")], [
        NfoPerson(id=ines, name="Ines Varga", image_path="I/Ines Varga/folder.jpg"), NfoPerson(id=wren, name="Wren Hale"),
    ])
    with titled() as session:
        title = session.get(MediaTitle, MOVIE)
        title.metadata_json = {**title.metadata_json, "crew": [
            {"person_id": ines, "name": "Ines Varga", "job": "Director"},
            {"person_id": wren, "name": "wren hale", "job": "Writer"},  # already credited as a Writer: listed once
            {"person_id": None, "name": "Old Ref", "job": "Director"},
        ]}
        session.commit()
    people = member(api_client, titled, ALICE).get(f"/api/titles/{MOVIE}").json()["people"]
    assert [(person["name"], person["type"], person["image_url"]) for person in people] == [
        ("Ada Rook", "Actor", None), ("Wren Hale", "Writer", None),
        ("Ines Varga", "Director", art_url(ines, "Primary", "I/Ines Varga/folder.jpg", 240)), ("Old Ref", "Director", None),
    ]


def test_without_the_folder_only_tmdb_photos_show_and_only_while_tmdb_is_on(titled, api_client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    bo = person_name_id("Bo Tran")
    credit(titled, [ref("Ada Rook"), ref("Bo Tran")], [
        NfoPerson(id=person_name_id("Ada Rook"), name="Ada Rook", image_path="A/Ada Rook/folder.png"),
        NfoPerson(id=bo, name="Bo Tran", tmdb_path="/bo.jpg"),
    ])
    monkeypatch.setattr(settings, "jellyfin_people_dir", "")

    def urls() -> list[str | None]:
        return [person["image_url"] for person in member(api_client, titled, ALICE).get(f"/api/titles/{MOVIE}").json()["people"]]

    assert urls() == [None, None]
    monkeypatch.setattr(settings, "tmdb_api_key", "test-key")
    assert urls() == [None, art_url(bo, "Primary", "/bo.jpg", 240)]


def test_a_title_page_costs_the_same_queries_for_tmdb_or_nfo_credits_of_any_size(titled, api_client) -> None:  # noqa: ANN001
    names = [f"Person {n}" for n in range(20)]
    credit(titled, [], [Person(id=AMY, tmdb_id=9273, name="Amy Adams", profile_path="/amy.jpg"),
                        *(NfoPerson(id=person_name_id(name), name=name, image_path=f"P/{name}/folder.png") for name in names)])

    def count(refs: list[dict]) -> int:
        credit(titled, refs)
        member(api_client, titled, ALICE).get(f"/api/titles/{MOVIE}").raise_for_status()  # warm
        client = member(api_client, titled, ALICE)
        return selects(titled, lambda: client.get(f"/api/titles/{MOVIE}").raise_for_status())

    tmdb_one = count([{"person_id": AMY, "name": "Amy Adams", "role": None, "type": "Actor"}])
    assert count([ref(names[0])]) == count([ref(name) for name in names]) == tmdb_one


def test_tmdb_tops_up_a_credited_person_by_name_on_the_same_title_only(titled) -> None:  # noqa: ANN001
    ada, bo = person_name_id("Ada Rook"), person_name_id("Bo Tran")
    credit(titled, [ref("Ada  Rook")], [NfoPerson(id=ada, name="Ada Rook"), NfoPerson(id=bo, name="Bo Tran")])
    with titled() as session:
        cast_photos.top_up(session, session.get(MediaTitle, MOVIE), [
            {"id": "x", "tmdb_id": 1, "name": "ada rook", "profile_path": "/ada.jpg"},
            {"id": "y", "tmdb_id": 2, "name": "Bo Tran", "profile_path": "/bo.jpg"},  # a TMDB credit this title's NFO lacks
            {"id": "z", "tmdb_id": 3, "name": "Ada Rook", "profile_path": None},
        ])
        session.commit()
        assert (session.get(NfoPerson, ada).tmdb_path, session.get(NfoPerson, bo).tmdb_path) == ("/ada.jpg", None)


def test_a_five_hundred_credit_nfo_keeps_twenty_people_with_photos(library: Path, people: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renditions, "media_tool", lambda db, name: None)  # the app's pass would fail these unwritten files mid-test
    actors = "".join(f"<actor><name>P{i}</name><type>Actor</type><thumb>{JELLYFIN}/P/P{i}/folder.png</thumb></actor>" for i in range(500))
    write(library / "Movies" / "Heat (1995)" / "Heat (1995).mkv", b"heat-bytes")
    write(library / "Movies" / "Heat (1995)" / "Heat (1995).nfo", f"<movie><title>Heat</title>{actors}</movie>".encode())
    with client_for("admin") as admin:
        import_root(admin, register_root(admin, library))
        with db_module.SessionLocal() as session:
            movie_id = session.scalar(select(MediaTitle.id).where(MediaTitle.type == "movie"))
            assert session.scalar(select(func.count()).select_from(NfoPerson)) == 20
        credited = admin.get(f"/api/titles/{movie_id}").json()["people"]
    assert len(credited) == 20 and all(person["image_url"].startswith("/api/art/") for person in credited)


def test_image_urls_binds_a_page_of_ids_once_so_17000_people_fit_sqlites_variable_limit(titled) -> None:  # noqa: ANN001
    ada = person_name_id("Ada Rook")
    ids = {ada, AMY} | {f"p{n}" for n in range(17_000)}  # 2 × 17,002 would pass SQLite's 32,766 variables
    with titled() as session:
        session.add_all([NfoPerson(id=ada, name="Ada Rook", image_path="A/Ada Rook/folder.png"),
                         Person(id=AMY, tmdb_id=9273, name="Amy Adams", profile_path="/amy.jpg")])
        session.commit()
        assert cast_photos.image_urls(session, ids) == {
            AMY: f"/api/people/{AMY}/image", ada: art_url(ada, "Primary", "A/Ada Rook/folder.png", 240),
        }


@pytest.fixture
def infuse(tmp_path: Path, people: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201, ARG001
    """jellyfin_household (alice and bob with device tokens) with MOVIE crediting Ada (a photo) and a producer (none)."""
    use_fake_ffmpeg(tmp_path, monkeypatch)
    jellyfin_household(tmp_path.resolve() / "media")
    ada = person_name_id("Ada Rook")
    with db_module.SessionLocal() as session:
        session.merge(NfoPerson(id=ada, name="Ada Rook", image_path="A/Ada Rook/folder.png"))
        session.merge(NfoPerson(id=person_name_id("Ines Varga"), name="Ines Varga", image_path="A/Andre Coutu/folder.png"))
        movie = session.get(MediaTitle, MOVIE)
        movie.metadata_json = {**(movie.metadata_json or {}), "people": [ref("Ada Rook", role="Keeper"), ref("No Photo", "Producer")],
                               "crew": [{"person_id": person_name_id("Ines Varga"), "name": "Ines Varga", "job": "Director"}]}
        session.commit()
    client = TestClient(app, base_url="http://localhost")
    yield client, ada
    client.close()


def test_infuse_gets_a_photo_tag_for_nfo_people_and_fetches_it_without_a_token(infuse, fresh_serving: None) -> None:  # noqa: ANN001, ARG001
    client, ada = infuse
    people = client.get(f"/Items/{jellyfin_id(MOVIE)}", headers=mediabrowser(ALICE_TOKEN)).json()["People"]
    for person in people:
        assert_jellyfin_keys("BaseItemPerson", person)
    assert [(person["Name"], person["Id"], person["Type"], "PrimaryImageTag" in person) for person in people] == [
        ("Ada Rook", jellyfin_id(ada), "Actor", True), ("No Photo", jellyfin_id(person_name_id("No Photo")), "Producer", False),
        ("Ines Varga", jellyfin_id(person_name_id("Ines Varga")), "Director", True),  # NFO <director>: in People, with a photo
    ]
    tag = people[0]["PrimaryImageTag"]
    path = f"/Items/{jellyfin_id(ada)}/Images/Primary"
    small = client.get(path, params={"tag": tag, "maxWidth": "100"}, headers={"Accept": "image/webp,*/*"})
    assert (small.status_code, small.content, small.headers["content-type"]) == (200, b"r" * 64, "image/webp")
    assert client.get(path, params={"tag": tag}).status_code == 200  # no size: the 240 px portrait
    director = f"/Items/{people[2]['Id']}/Images/Primary"
    assert client.get(director, params={"tag": people[2]["PrimaryImageTag"]}).status_code == 200
    assert client.get(path, params={"tag": "0" * 32}).status_code == 404
    assert client.get(path).status_code in (401, 404)  # no token, no tag
    assert client.get(f"/Items/{jellyfin_id(ada)}/Images/Backdrop", params={"tag": tag}).status_code == 404


def test_a_device_token_sees_a_photo_only_through_a_title_it_can_see(infuse, fresh_serving: None) -> None:  # noqa: ANN001, ARG001
    client, _ = infuse
    sly = person_name_id("Sly Secret")
    with db_module.SessionLocal() as session:
        session.merge(NfoPerson(id=sly, name="Sly Secret", image_path="A/Andre Coutu/folder.png"))
        secret = session.get(MediaTitle, SECRET_SERIES)  # bob's private show
        secret.metadata_json = {**(secret.metadata_json or {}), "people": [ref("Sly Secret")]}
        session.commit()
    path = f"/Items/{jellyfin_id(sly)}/Images/Primary"
    assert client.get(path, headers=mediabrowser(BOB_TOKEN)).status_code == 200
    assert client.get(path, headers=mediabrowser(ALICE_TOKEN)).status_code == 404


def test_a_device_token_sees_a_crew_only_photo(infuse, fresh_serving: None) -> None:  # noqa: ANN001, ARG001
    client, _ = infuse
    director = f"/Items/{jellyfin_id(person_name_id('Ines Varga'))}/Images/Primary"
    assert client.get(director, headers=mediabrowser(ALICE_TOKEN)).status_code == 200


def test_a_height_only_request_for_a_missing_photo_is_not_found(infuse, people: Path, fresh_serving: None) -> None:  # noqa: ANN001, ARG001
    client, ada = infuse
    (people / "A/Ada Rook/folder.png").unlink()
    path = f"/Items/{jellyfin_id(ada)}/Images/Primary"
    assert client.get(path, params={"maxHeight": "100"}, headers=mediabrowser(ALICE_TOKEN)).status_code == 404


def test_an_unmounted_people_folder_gives_no_portrait_urls(titled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    unmounted = tmp_path / "unmounted"
    unmounted.mkdir()  # an unmounted bind mount is an empty folder
    monkeypatch.setattr(settings, "jellyfin_people_dir", str(unmounted))
    ada = person_name_id("Ada Rook")
    with titled() as session:
        session.add_all([NfoPerson(id=ada, name="Ada Rook", image_path="A/Ada Rook/folder.png"),
                         Person(id=AMY, tmdb_id=9273, name="Amy Adams", profile_path="/amy.jpg")])
        session.commit()
        assert cast_photos.image_urls(session, {ada, AMY}) == {AMY: f"/api/people/{AMY}/image"}
