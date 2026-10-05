"""Library sections: what each member can see per Library tab, cached 30 s."""
from __future__ import annotations

import pytest

from app.models import LibraryItem
from test_category_walls import walls  # noqa: F401 - the shared gallery fixture
from title_support import ALICE, BOB, CHANNEL_OLD, add_file, uid


@pytest.fixture(autouse=True)
def fresh_sections(monkeypatch):  # noqa: ANN001, ANN201
    from app.routers import library_sections

    monkeypatch.setattr(library_sections, "_sections", {})


def test_sections_count_what_each_member_can_see(walls) -> None:  # noqa: ANN001, F811
    """Private items count only for their owner; a missing item leaves its tab and counts as deleted."""
    with walls.db() as session:
        add_file(session, walls.root, "audio/song.m4a", item_id=uid(500), title_id=None, owner=ALICE, title="Saved song", kind="audio")
        add_file(session, walls.root, "recordings/live.mkv", item_id=uid(501), title_id=None, owner=BOB, visibility="private",
                 title="Live", kind="recording")
        session.get(LibraryItem, CHANNEL_OLD).status = "missing"
        session.commit()
    alice = walls.get("/api/library/sections").json()
    bob = walls.get("/api/library/sections", member="bob").json()
    # youtube: the shared upload and seed_tree's trailer (kind video); bob also sees his private upload.
    assert alice == {"movies": 1, "shows": 1, "anime": 2, "albums": 1, "artists": 1, "saved_audio": 1, "youtube": 2, "recordings": 0, "deleted": 1}
    assert bob == {"movies": 1, "shows": 2, "anime": 2, "albums": 1, "artists": 1, "saved_audio": 1, "youtube": 3, "recordings": 1, "deleted": 1}


def test_sections_are_cached_per_member_until_cleared(walls) -> None:  # noqa: ANN001, F811
    from app.routers import library_sections

    assert walls.get("/api/library/sections").json()["saved_audio"] == 0
    with walls.db() as session:
        add_file(session, walls.root, "audio/song.m4a", item_id=uid(500), title_id=None, owner=ALICE, title="Saved song", kind="audio")
        session.commit()
    assert walls.get("/api/library/sections").json()["saved_audio"] == 0  # within the 30 s TTL
    assert walls.get("/api/library/sections", member="bob").json()["saved_audio"] == 1  # bob's own entry
    library_sections.clear()
    assert walls.get("/api/library/sections").json()["saved_audio"] == 1


def test_sections_need_a_member(api_client) -> None:  # noqa: ANN001
    assert api_client(base_url="http://localhost").get("/api/library/sections").status_code == 401
