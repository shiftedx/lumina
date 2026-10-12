"""The editor routes: permissions, round trips, errors, settings flag."""
from __future__ import annotations

from app.models import MediaTitle, User
from metadata_support import ADMIN, allow_members, t1_seam, world  # noqa: F401
from title_support import ALICE, BOB, MOVIE, SECRET_SERIES, SERIES, uid

ROUTES = [
    ("GET", f"/api/titles/{MOVIE}/metadata", None), ("POST", "/api/metadata/edits", {"edits": [{"title_id": MOVIE}]}),
    ("POST", f"/api/titles/{MOVIE}/metadata/revert", {"fields": ["overview"]}), ("GET", f"/api/titles/{MOVIE}/metadata/history", None),
    ("POST", "/api/metadata/bulk", {"title_ids": [MOVIE], "ops": []}), ("GET", "/api/metadata/vocabulary?field=genres", None),
    ("GET", "/api/metadata/people?q=a", None), ("GET", f"/api/titles/{SERIES}/metadata/episodes", None),
]


def edit(client, title_id=MOVIE, changes=None, **extra):  # noqa: ANN001, ANN201
    return client.post("/api/metadata/edits", json={"edits": [{"title_id": title_id, "changes": changes or {}, **extra}]})


def test_permission_matrix(world) -> None:  # noqa: ANN001
    factory, client_for = world
    for method, url, body in ROUTES:
        assert client_for(ADMIN).request(method, url, json=body).status_code == 200, url
        r = client_for(ALICE).request(method, url, json=body)
        assert (r.status_code, r.json()) == (403, {"detail": "editing_not_allowed"}), url
    allow_members(factory)
    for method, url, body in ROUTES:
        assert client_for(ALICE).request(method, url, json=body).status_code == 200, url
    with factory() as db:
        db.get(User, ALICE).is_active = False
        db.commit()
    assert client_for(ALICE).get(f"/api/titles/{MOVIE}/metadata").status_code in (401, 403)


def test_invisible_title_is_404_for_an_editor(world) -> None:  # noqa: ANN001
    factory, client_for = world
    allow_members(factory)
    assert client_for(ALICE).get(f"/api/titles/{SECRET_SERIES}/metadata").status_code == 404
    assert client_for(BOB).get(f"/api/titles/{SECRET_SERIES}/metadata").status_code == 200
    assert client_for(ALICE).get("/api/titles/not-an-id/metadata").status_code == 404


def test_non_editable_types_are_rejected(world) -> None:  # noqa: ANN001
    _, client_for = world
    assert client_for(ADMIN).get(f"/api/titles/{uid(40)}/metadata").status_code == 404


def test_save_round_trip_and_doc_shape(world) -> None:  # noqa: ANN001
    _, client_for = world
    r = edit(client_for(ADMIN), changes={"overview": {"value": "New", "base": "A heist."}})
    assert r.status_code == 200 and r.json()["batch_id"]
    field = r.json()["titles"][0]["fields"]["overview"]
    assert field == {"value": "New", "source": "user", "locked": True, "kept": {"source": None, "value": "A heist."}}


def test_invalid_field_is_flat_422_and_applies_nothing(world) -> None:  # noqa: ANN001
    factory, client_for = world
    r = edit(client_for(ADMIN), changes={"overview": {"value": "Z"}, "year": {"value": 1}})
    assert r.status_code == 422 and r.json() == {"detail": "invalid_field", "title_id": MOVIE, "field": "year", "reason": "out_of_range"}
    with factory() as db:
        assert (db.get(MediaTitle, MOVIE).field_sources or {}).get("overview") != "user"


def test_conflicts_200_partial_and_409_when_all_conflict(world) -> None:  # noqa: ANN001
    _, client_for = world
    admin = client_for(ADMIN)
    stale = {"overview": {"value": "X", "base": "stale"}}
    r = admin.post("/api/metadata/edits", json={"edits": [
        {"title_id": MOVIE, "changes": stale}, {"title_id": SERIES, "changes": {"tagline": {"value": "T"}}}]})
    assert r.status_code == 200 and [c["title_id"] for c in r.json()["conflicts"]] == [MOVIE] and len(r.json()["titles"]) == 1
    r = edit(admin, changes=stale)
    assert r.status_code == 409 and r.json()["titles"] == [] and r.json()["conflicts"][0]["title_id"] == MOVIE


def test_member_tmdb_change_is_403_and_owner_change_is_free(world) -> None:  # noqa: ANN001
    factory, client_for = world
    allow_members(factory)
    change = {"provider_ids": {"value": {"Tmdb": "604"}}}
    r = edit(client_for(ALICE), changes=change)
    assert (r.status_code, r.json()) == (403, {"detail": "identify_requires_owner"})
    assert edit(client_for(ADMIN), changes=change).status_code == 200


def test_member_cannot_revert_an_owner_set_tmdb_id(world) -> None:  # noqa: ANN001
    factory, client_for = world
    allow_members(factory)
    assert edit(client_for(ADMIN), changes={"provider_ids": {"value": {"Tmdb": "604"}}}).status_code == 200
    r = client_for(ALICE).post(f"/api/titles/{MOVIE}/metadata/revert", json={"fields": ["provider_ids"]})
    assert (r.status_code, r.json()) == (403, {"detail": "identify_requires_owner"})


def test_locked_title_identify_is_409_title_locked(world) -> None:  # noqa: ANN001
    _, client_for = world
    admin = client_for(ADMIN)
    assert edit(admin, locked=True).status_code == 200
    r = edit(admin, changes={"provider_ids": {"value": {"Tmdb": "604"}}})
    assert (r.status_code, r.json()) == (409, {"detail": "title_locked"})


def test_revert_history_and_undo_routes(world) -> None:  # noqa: ANN001
    _, client_for = world
    admin = client_for(ADMIN)
    edit(admin, changes={"overview": {"value": "New"}})
    r = admin.post(f"/api/titles/{MOVIE}/metadata/revert", json={"fields": ["overview"]})
    assert r.status_code == 200 and r.json()["batch_id"] and r.json()["title"]["fields"]["overview"]["source"] != "user"
    assert admin.post(f"/api/titles/{MOVIE}/metadata/revert", json={"fields": ["nope"]}).status_code == 422
    batches = admin.get(f"/api/titles/{MOVIE}/metadata/history").json()["batches"]
    assert len(batches) == 2
    undone = admin.post(f"/api/metadata/batches/{batches[0]['batch_id']}/undo")
    assert undone.status_code == 200 and undone.json()["restored"] == 1
    assert admin.post(f"/api/metadata/batches/{batches[0]['batch_id']}/undo").json() == {"detail": "already_undone"}
    assert admin.post("/api/metadata/batches/nope/undo").json() == {"detail": "batch_not_found"}


def test_bulk_route_and_vocabulary(world) -> None:  # noqa: ANN001
    _, client_for = world
    admin = client_for(ADMIN)
    r = admin.post("/api/metadata/bulk", json={"title_ids": [MOVIE, SERIES], "ops": [{"op": "add", "field": "tags", "values": ["kids"]}]})
    assert r.status_code == 200 and r.json()["applied"] == 2
    assert {"value": "kids", "count": 2} in admin.get("/api/metadata/vocabulary?field=tags").json()
    assert admin.get("/api/metadata/vocabulary?field=year").status_code == 422
    assert admin.post("/api/metadata/bulk", json={"title_ids": [MOVIE], "ops": [{"op": "add", "field": "year"}]}).status_code == 422


def test_refresh_preview_is_owner_only_and_rate_limited(world) -> None:  # noqa: ANN001
    factory, client_for = world
    allow_members(factory)
    url = f"/api/titles/{MOVIE}/metadata/refresh-preview"
    assert client_for(ALICE).post(url).status_code == 403
    admin = client_for(ADMIN)
    first = admin.post(url)
    assert first.status_code == 409 and first.json()["detail"] == "tmdb_not_configured"
    codes = [admin.post(url).status_code for _ in range(6)]
    assert codes[-1] == 429


def test_size_limits(world) -> None:  # noqa: ANN001
    _, client_for = world
    admin = client_for(ADMIN)
    assert admin.post("/api/metadata/edits", json={"edits": [{"title_id": uid(i)} for i in range(501)]}).status_code == 422
    assert edit(admin, changes={f"f{i}": {"value": 1} for i in range(41)}).status_code == 422


def test_session_user_carries_can_edit_details_and_setting_is_served(world) -> None:  # noqa: ANN001
    factory, client_for = world
    assert client_for(ADMIN).get("/api/session/me").json()["user"]["can_edit_details"] is True
    assert client_for(ALICE).get("/api/session/me").json()["user"]["can_edit_details"] is False
    assert client_for(ADMIN).put("/api/admin/media-server", json={"members_edit_metadata": True}).json()["members_edit_metadata"] is True
    assert client_for(ALICE).get("/api/session/me").json()["user"]["can_edit_details"] is True
    assert client_for(ALICE).put("/api/admin/media-server", json={"members_edit_metadata": False}).status_code == 403
