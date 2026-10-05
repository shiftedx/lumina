"""Categories: the folder rule, the scoped refresh after a scan batch, and the re-sort."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import AppSettings, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, User
from app.security import hash_password
from app.services.categories import refresh_category, same_folders
from app.services.rate_limit import rate_limiter
from support import make_user, seed_app_settings
from title_support import (
    ALBUM, ALICE, ANIME_EPISODE, ANIME_FILE, ANIME_MOVIE, ANIME_MOVIE_FILE, ANIME_SEASON, ANIME_SERIES, ARTIST, BOB, BOXSET,
    MOVIE, ROOT, S1E1, SECRET_SERIES, SERIES, add_file, seed_gallery, seed_tree, uid,
)

PASSWORD = "Test-only-passphrase-1"
# Every title back to its type's insert default (models.CATEGORY_OF_TYPE); boxsets, albums and artists stay NULL.
TYPE_DEFAULTS = (
    "UPDATE media_titles SET category = CASE type WHEN 'movie' THEN 'movies' WHEN 'series' THEN 'shows'"
    " WHEN 'season' THEN 'shows' WHEN 'episode' THEN 'shows' END"
)


@pytest.fixture
def library(db_factory, tmp_path):  # noqa: ANN001, ANN201
    """seed_tree + seed_gallery under tmp/media with the settings row (anime folders ["Anime"])."""
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        seed_app_settings(session)  # commits
    return db_factory, root


def _categories(session) -> dict[str, str | None]:  # noqa: ANN001
    return dict(session.execute(select(MediaTitle.id, MediaTitle.category)).all())


def _filed_movie(session, root: Path, title_id: str, relative: str) -> None:  # noqa: ANN001
    """A movie with one file at ``relative`` under seed_tree's root."""
    session.add(MediaTitle(id=title_id, type="movie", key=f"k:{title_id}", root_id=ROOT, name=relative))
    add_file(session, root, relative, item_id=str(uuid.uuid4()), title_id=title_id, owner=ALICE, title=relative, kind="movie")


def _derive(session, folders: list[str]) -> dict[str, str | None]:  # noqa: ANN001
    """Reset every title to its type's default, save ``folders``, and refresh every title as a scan batch would."""
    session.flush()  # sessions never autoflush; the scan flushes before refresh_category too
    session.execute(text(TYPE_DEFAULTS))
    session.execute(update(AppSettings).values(anime_folders=folders))
    refresh_category(session, list(session.scalars(select(MediaTitle.id))))
    session.commit()
    return _categories(session)


def test_a_file_under_an_anime_folder_makes_its_title_anime(library) -> None:  # noqa: ANN001
    """A movie under Anime/, a series through its episode, seasons and episodes by inheritance, and
    a root registered at …/anime by its own path. Boxsets, albums and artists have no category."""
    factory, root = library
    with factory() as session:
        session.add_all([
            StorageRoot(id=uid(901), label="Anime", path=str(root.parent / "anime"), mode="external", enabled=True),
            MediaTitle(id=uid(640), type="movie", key="k:rooted", root_id=uid(901), name="Rooted"),
            LibraryItem(id=uid(641), user_id=ALICE, visibility="shared", title="Rooted", title_id=uid(640), status="available",
                        kind="movie", metadata_json={}),
            MediaArtifact(id=uid(642), root_id=uid(901), relative_path="Film/Film.mkv", ownership="external", lifecycle="available"),
            LibraryItemArtifact(library_item_id=uid(641), artifact_id=uid(642)),
        ])
        category = _derive(session, ["Anime"])
    assert {category[i] for i in (ANIME_MOVIE, ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, uid(640))} == {"anime"}
    assert (category[MOVIE], category[SERIES], category[S1E1], category[SECRET_SERIES]) == ("movies", "shows", "shows", "shows")
    assert (category[BOXSET], category[ALBUM], category[ARTIST]) == (None, None, None)


def test_folder_names_match_whole_segments_in_any_ascii_case(library) -> None:  # noqa: ANN001
    factory, root = library
    with factory() as session:
        _filed_movie(session, root, uid(610), "Movies/Anime.mkv")  # a file name, not a folder
        _filed_movie(session, root, uid(611), "Movies/Anime Classics/Heat/Heat.mkv")  # a longer folder name
        _filed_movie(session, root, uid(612), "Movies/anime/Heat/Heat.mkv")  # lower case
        category = _derive(session, ["ANIME"])
    assert (category[uid(610)], category[uid(611)], category[uid(612)], category[ANIME_MOVIE]) == ("movies", "movies", "anime", "anime")


def test_like_wildcards_in_a_folder_name_are_literal(library) -> None:  # noqa: ANN001
    factory, root = library
    with factory() as session:
        _filed_movie(session, root, uid(620), "Movies/100%_Anime/Film/Film.mkv")
        _filed_movie(session, root, uid(621), "Movies/100 xAnime/Film/Film.mkv")  # what % and _ would match as wildcards
        category = _derive(session, ["100%_Anime"])
    assert (category[uid(620)], category[uid(621)], category[ANIME_MOVIE]) == ("anime", "movies", "movies")


def test_no_anime_folders_means_nothing_is_anime(library) -> None:  # noqa: ANN001
    factory, _root = library
    with factory() as session:
        category = _derive(session, [])
    assert "anime" not in category.values()
    assert (category[ANIME_MOVIE], category[ANIME_SERIES], category[ANIME_EPISODE]) == ("movies", "shows", "shows")


def test_refresh_touches_only_the_given_titles_and_their_series(library) -> None:  # noqa: ANN001
    """A batch may hold only an episode: its series (found through the season), with that series' seasons and episodes,
    re-derives. Titles outside the batch keep whatever they hold, even when wrong."""
    factory, _root = library
    with factory() as session:
        session.execute(text(TYPE_DEFAULTS))  # the four anime titles are now wrong
        session.execute(update(MediaTitle).where(MediaTitle.id.in_([MOVIE, SERIES])).values(category="anime"))
        refresh_category(session, [ANIME_EPISODE, ANIME_MOVIE])
        session.commit()
        category = _categories(session)
    assert {category[i] for i in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, ANIME_MOVIE)} == {"anime"}
    assert (category[MOVIE], category[SERIES]) == ("anime", "anime")  # outside the batch: untouched


def test_a_title_without_linked_files_keeps_its_category(library) -> None:  # noqa: ANN001
    """Files gone (an unplugged NAS) never flip a title back to movies/shows; titles with files re-derive."""
    factory, _root = library
    with factory() as session:
        session.execute(text("DELETE FROM library_item_artifacts WHERE library_item_id = :item"), {"item": ANIME_MOVIE_FILE})
        session.execute(update(AppSettings).values(anime_folders=[]))
        refresh_category(session, list(session.scalars(select(MediaTitle.id))))
        session.commit()
        category = _categories(session)
    assert category[ANIME_MOVIE] == "anime"  # no linked file: kept
    assert (category[ANIME_SERIES], category[ANIME_EPISODE]) == ("shows", "shows")  # still filed: re-derived


def test_equivalent_folder_lists_sort_the_same() -> None:
    """Pinned interpretation 2: sets under SQLite LIKE's ASCII-only case folding."""
    assert same_folders(["Anime", "Donghua"], ["donghua", "ANIME"])
    assert not same_folders(["Anime"], ["Anime", "Donghua"])
    assert not same_folders(["Animé"], ["ANIMÉ"])  # LIKE compares non-ASCII letters exactly


@pytest.fixture
def scanner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A real admin, a registered external root and the app's scan drivers (like test_v1_titles' fixtures)."""
    parent = tmp_path / "mnt"
    media = parent / "media"
    media.mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add(User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    rate_limiter.clear()
    with TestClient(app, base_url="http://localhost") as client:
        client.auth = ("admin", PASSWORD)
        client.root_id = client.post(
            "/api/admin/storage/roots", json={"label": "Media", "container_path": str(media), "mode": "external"}
        ).json()["id"]
        yield client, media
    rate_limiter.clear()


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(path.name.encode())  # distinct bytes per file, so a move is recognised by inode, not by content


def _scan(client: TestClient) -> dict:
    started = client.post("/api/admin/imports", json={"root_id": client.root_id, "visibility": "shared"})
    assert started.status_code == 202, started.text
    return client.get(started.json()["status_url"]).json()


def _tree_categories() -> dict[str, set[str | None]]:
    """Each top-level movie or series, by name -> the categories of it and every title under it."""
    with db_module.SessionLocal() as db:
        titles = {title.id: title for title in db.scalars(select(MediaTitle).where(MediaTitle.type.in_(("movie", "series", "season", "episode"))))}
    found: dict[str, set[str | None]] = {}
    for title in titles.values():
        top = title
        while top.parent_id in titles:
            top = titles[top.parent_id]
        found.setdefault(top.name, set()).add(title.category)
    return found


def test_a_show_moved_into_anime_flips_on_rescan(scanner) -> None:  # noqa: ANN001
    """The scan hook: every batch re-derives its titles with the saved folders, so a show whose folder moves
    from TV/Shows to TV/Anime (relinked by inode, same ids) is anime after the rescan, seasons and episodes included."""
    client, media = scanner
    for relative in ("TV/Shows/Show B/Season 01/Show B S01E01.mkv", "TV/Anime/Show A/Season 01/Show A S01E01.mkv",
                     "Movies/Anime/Film (2020)/Film (2020).mkv", "Movies/Heat (1995)/Heat (1995).mkv"):
        _write(media / relative)
    assert _scan(client)["state"] == "succeeded"
    assert _tree_categories() == {"Show B": {"shows"}, "Show A": {"anime"}, "Film": {"anime"}, "Heat": {"movies"}}
    (media / "TV" / "Shows" / "Show B").rename(media / "TV" / "Anime" / "Show B")
    run = _scan(client)
    assert (run["state"], run["counters"]["relinked"]) == ("succeeded", 1)
    assert _tree_categories() == {"Show B": {"anime"}, "Show A": {"anime"}, "Film": {"anime"}, "Heat": {"movies"}}


# ---- Re-sort when the anime folders change ----------------------------------------------------------------


@pytest.fixture
def shared_library(tmp_path):  # noqa: ANN001, ANN201
    """The same library in conftest's file-backed database, which recategorise()'s thread reads through SessionLocal."""
    from support import file_backed_session_factory

    factory = file_backed_session_factory()
    root = tmp_path.resolve() / "media"
    with factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        seed_app_settings(session)  # commits
    return factory


def test_recategorise_re_sorts_the_whole_library_in_the_background(shared_library, monkeypatch) -> None:  # noqa: ANN001
    from app.routers import library_sections
    from app.routers import titles as titles_router
    from app.services import categories

    monkeypatch.setattr(titles_router, "_facets", {("member", "viewer", "", "anime"): (0.0, None)})
    monkeypatch.setattr(library_sections, "_sections", {("member", "viewer"): (0.0, None)})
    with shared_library() as session:
        session.execute(update(AppSettings).values(anime_folders=[]))
        session.commit()
    categories.recategorise()
    categories.wait()
    assert categories.recategorising() is False
    with shared_library() as session:
        category = _categories(session)
    assert (category[ANIME_MOVIE], category[ANIME_SERIES], category[ANIME_EPISODE]) == ("movies", "shows", "shows")
    assert (titles_router._facets, library_sections._sections) == ({}, {})  # noqa: SLF001


def test_recategorise_keeps_an_anime_series_whose_files_are_gone(shared_library) -> None:  # noqa: ANN001
    """An unplugged NAS unlinks nothing forever, but a series with no linked file must stay anime through a
    re-sort; its season and episode keep inheriting it."""
    from app.services import categories

    with shared_library() as session:
        session.execute(text("DELETE FROM library_item_artifacts WHERE library_item_id = :item"), {"item": ANIME_FILE})
        session.execute(update(AppSettings).values(anime_folders=[]))
        session.commit()
    categories.recategorise()
    categories.wait()
    with shared_library() as session:
        category = _categories(session)
    assert {category[i] for i in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE)} == {"anime"}
    assert category[ANIME_MOVIE] == "movies"  # still filed: re-sorted


def test_saves_during_a_run_coalesce_into_one_more_run(monkeypatch) -> None:  # noqa: ANN001
    import threading

    from app.services import categories

    started, release, runs = threading.Event(), threading.Event(), []

    def blocking_run() -> None:
        runs.append(1)
        started.set()
        release.wait(10)

    monkeypatch.setattr(categories, "_run_once", blocking_run)
    try:
        categories.recategorise()
        assert started.wait(10)
        categories.recategorise()
        categories.recategorise()
        assert categories.recategorising() is True
    finally:
        release.set()
    categories.wait()
    assert (len(runs), categories.recategorising()) == (2, False)


def test_a_failed_run_still_ends_and_clears_the_flag(monkeypatch, caplog) -> None:  # noqa: ANN001
    from app.services import categories

    def broken() -> None:
        raise RuntimeError("disk full at /media/tv/Private Show")

    monkeypatch.setattr(categories, "_run_once", broken)
    categories.recategorise()
    categories.wait()
    assert categories.recategorising() is False
    assert "RuntimeError" in caplog.text and "Private Show" not in caplog.text
