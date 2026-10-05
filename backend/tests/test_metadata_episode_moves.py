"""#164 (a): an episode moved to another season of its series is a user edit (ADR 0016): it survives rescans,
reverts to the folder's season, undoes, and the Jellyfin API serves the new numbering."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaTitle, User
from app.services import media_titles, metadata_editor as me
from metadata_support import ADMIN, t1_seam, world  # noqa: F401
from test_jellyfin_api import HEX, get, jf, names  # noqa: F401
from title_support import ALICE, S1E2, SEASON1, SEASON2, SECRET_SEASON, SERIES, SPECIALS


def move(client, season_id, base=SEASON1, episode=S1E2):  # noqa: ANN001, ANN201
    return client.post("/api/metadata/edits", json={"edits": [{"title_id": episode, "changes": {"parent_id": {"value": season_id, "base": base}}}]})


def test_move_lists_seasons_validates_reverts_and_undoes(world) -> None:  # noqa: ANN001, F811
    factory, client_for = world
    admin = client_for(ADMIN)
    doc = admin.get(f"/api/titles/{S1E2}/metadata").json()
    assert [s["name"] for s in doc["seasons"]] == ["Specials", "Season 1", "Season 2"]
    assert doc["fields"]["parent_id"] == {"value": SEASON1, "source": None, "locked": False, "kept": None}
    for bad in (SECRET_SEASON, SERIES, "nope"):
        r = move(admin, bad)
        assert (r.status_code, r.json()["field"], r.json()["reason"]) == (422, "parent_id", "bad_season"), bad
    r = move(admin, SPECIALS)
    assert r.status_code == 200
    batch = r.json()["batch_id"]
    assert r.json()["titles"][0]["fields"]["parent_id"]["source"] == "user"
    with factory() as db:
        assert db.get(MediaTitle, S1E2).parent_id == SPECIALS
    assert admin.post(f"/api/metadata/batches/{batch}/undo").json()["restored"] == 1
    with factory() as db:
        assert db.get(MediaTitle, S1E2).parent_id == SEASON1
    move(admin, SEASON2)
    r = admin.post(f"/api/titles/{S1E2}/metadata/revert", json={"fields": ["parent_id"]})
    assert r.status_code == 200
    with factory() as db:
        title = db.get(MediaTitle, S1E2)
        assert (title.parent_id, (title.field_sources or {}).get("parent_id")) == (SEASON1, None)


def test_a_rescan_keeps_the_move_and_revert_takes_the_newest_folder_season() -> None:
    title = MediaTitle(id="e", type="episode", key="k", name="E", parent_id="s1", field_sources={}, source_values={})
    media_titles.scanned_parent(title, "s1")
    assert title.parent_id == "s1"
    media_titles.keep_source_value(title, "parent_id")
    media_titles.apply_field(title, "parent_id", "s0", "user")
    media_titles.scanned_parent(title, "s2")  # the file moved to Season 2 on disk: the user's season still wins
    assert title.parent_id == "s0"
    assert media_titles.revert_field(title, "parent_id", datetime(2026, 1, 1))
    assert title.parent_id == "s2" and "parent_id" not in title.field_sources


def test_jellyfin_serves_the_moved_episode_in_its_new_season(jf: TestClient) -> None:  # noqa: F811
    with SessionLocal() as db:
        _, applied, _ = me.save_edits(db, db.get(User, ALICE), [me.EditEntry(S1E2, {"parent_id": me.Change(SEASON2)})], owner=False)
        assert applied == [S1E2]
        me.save_edits(db, db.get(User, ALICE), [me.EditEntry(S1E2, {"index_number": me.Change(7)})], owner=False)
    path = f"/Shows/{HEX(SERIES)}/Episodes"
    assert names(get(jf, path, SeasonId=HEX(SEASON1))) == ["Pilot"]
    assert names(get(jf, path, Season=2)) == ["Return", "Second"]
    dto = get(jf, f"/Items/{HEX(S1E2)}").json()
    assert (dto["SeasonId"], dto["ParentId"], dto["ParentIndexNumber"], dto["IndexNumber"]) == (HEX(SEASON2), HEX(SEASON2), 2, 7)
