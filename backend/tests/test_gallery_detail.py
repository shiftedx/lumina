"""Gallery artwork on title summaries and the detail additions."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import LibraryItemArtifact, MediaArtifact, MediaTitle, User
from app.services import art_urls
from art_support import artwork_row, give_art
from support import make_user
from title_support import ALICE, BOB, FILE, MOVIE, S1E1, S1E2, SEASON1, SERIES, add_file, seed_tree, uid


@pytest.fixture(autouse=True)
def fresh_queue(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")


@pytest.fixture
def library(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        session.flush()
        movie, show, pilot = (session.get(MediaTitle, key) for key in (MOVIE, SERIES, S1E1))
        for image_type in ("Backdrop", "Logo"):
            give_art(movie, image_type)
        give_art(pilot, "Primary")
        session.flush()
        for image_type in ("Primary", "Backdrop", "Logo"):
            artwork_row(session, movie, image_type)
        artwork_row(session, show, "Primary", stale=True)
        artwork_row(session, pilot, "Primary", width=1920, height=1080)
        session.commit()
    return db_factory, root


def member(api_client, factory, user_id: str):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def test_list_summaries_carry_poster_and_backdrop_art(library, api_client) -> None:  # noqa: ANN001
    items = {t["id"]: t for t in member(api_client, library[0], ALICE).get("/api/titles").json()["items"]}
    movie, show = items[MOVIE], items[SERIES]
    assert movie["poster"]["widths"] == [240, 480] and movie["poster"]["preview"].startswith("data:image/webp;base64,")
    assert (movie["poster"]["dominant"], movie["poster"]["width"], movie["poster"]["url"]) == ("#2a3b4c", 1000, movie["poster_url"])
    assert movie["backdrop"]["widths"] == [960, 1920] and movie["backdrop"]["accent"] == "#c08a4b"
    assert movie["backdrop"]["preview"] is None  # previews of backdrops ride only on the detail
    assert show["poster"]["rendition"].endswith("-{w}.webp")
    assert (show["poster"]["preview"], show["poster"]["dominant"], show["backdrop"]) == (None, None, None)  # stale row
    queued = art_urls.take(100)
    assert (SERIES, "Primary") in queued
    assert (MOVIE, "Primary") not in queued and (MOVIE, "Logo") not in queued


def test_detail_adds_backdrop_preview_logo_and_counts(library, api_client) -> None:  # noqa: ANN001
    factory, root = library
    with factory() as session:
        artifact = session.scalar(
            select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
            .where(LibraryItemArtifact.library_item_id == FILE[S1E1])
        )
        artifact.probe = {"width": 1920, "height": 1080}
        add_file(session, root, "Show/Season 01/Show S01E02 - 4K.mkv", item_id=uid(700), title_id=S1E2, owner=BOB,
                 visibility="private", title="Second · 4K", probe={"width": 3840, "height": 2160})
        session.commit()
    alice = member(api_client, factory, ALICE)
    movie = alice.get(f"/api/titles/{MOVIE}").json()
    assert movie["backdrop"]["preview"].startswith("data:image/webp;base64,")
    assert (movie["logo"]["widths"], movie["logo"]["preview"], movie["logo"]["dominant"], movie["logo"]["width"]) == ([600], None, None, 1000)
    assert (movie["best_height"], movie["episode_count"]) == (2160, None)
    assert (movie["boxset"]["poster"], movie["boxset"]["backdrop"]) == (None, None)
    show = alice.get(f"/api/titles/{SERIES}").json()
    assert (show["episode_count"], show["best_height"], show["backdrop"], show["logo"]) == (4, 1080, None, None)
    assert show["play_next"]["poster"]["widths"] == [400] and show["play_next"]["poster"]["preview"].startswith("data:")
    assert all(child["backdrop"] is None for child in show["children"])
    season = alice.get(f"/api/titles/{SEASON1}").json()
    assert (season["episode_count"], season["best_height"]) == (2, 1080)
    bob = member(api_client, factory, BOB)  # bob's private 4K version counts only for bob
    assert bob.get(f"/api/titles/{SERIES}").json()["best_height"] == 2160


def test_episode_stills_carry_their_preview(library, api_client) -> None:  # noqa: ANN001
    pilot, second = member(api_client, library[0], ALICE).get(f"/api/titles/{SERIES}/episodes", params={"season": 1}).json()
    assert pilot["poster"]["widths"] == [400] and pilot["poster"]["preview"].startswith("data:image/webp;base64,")
    assert (pilot["poster"]["width"], second["poster"]) == (1920, None)
