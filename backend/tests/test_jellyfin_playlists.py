"""Watchlist and household collections as Jellyfin Playlists: the service layer."""
from __future__ import annotations

import pytest

from app import db as db_module
from app.models import HouseholdCollection, User
from app.services.household_collections import HouseholdCollectionService
from app.services.media_titles import jellyfin_id, synthetic_id
from app.services.playlists import (
    PlaylistBadRequest,
    PlaylistConflict,
    PlaylistForbidden,
    PlaylistNotFound,
    add_to_playlist,
    get_playlist,
    has_playlists,
    list_playlists,
    move_playlist_entry,
    remove_from_playlist,
    watchlist_id,
)
from app.services.watch_queue import RemoteRef, WatchQueueService
from discovery_support import add_movie, add_progress, add_series, add_version
from support import make_user, memory_session_factory
from tests.test_jellyfin_integration import ADMIN_ID, Vault, episodes, fake_show, login, movie_tree, names, title_ids, vault  # noqa: F401
from tests.test_v1_sidecars import scan

PLAYLISTS_VIEW = jellyfin_id(synthetic_id("view:playlists"))


def view_types(vault: Vault, headers: dict) -> list[str | None]:
    return [view.get("CollectionType") for view in vault.jf.get("/UserViews", headers=headers).json()["Items"]]


def _library():
    session = memory_session_factory()()
    session.add_all([make_user("owner"), make_user("member")])
    add_movie(session, "arr", "Arrival")
    add_version(session, "arr-4k", "arr", file_size=9_000)
    add_movie(session, "pad", "Paddington")
    add_movie(session, "priv", "Private", owner="owner", visibility="private")
    add_series(session, "show", "Show", seasons={1: 2})
    session.commit()
    return session, session.get(User, "owner"), session.get(User, "member")


def _items(playlist) -> list[str]:  # noqa: ANN001
    return [entry.item_id for entry in playlist.entries]


def test_a_member_with_nothing_queued_or_collected_has_no_playlists() -> None:
    session, _owner, member = _library()

    assert list_playlists(session, member) == [] and has_playlists(session, member) is False


def test_watch_queue_is_a_synthetic_playlist_without_remote_entries() -> None:
    session, _owner, member = _library()
    queue = WatchQueueService(session)
    queue.add(member, library_item_id="pad-v")
    queue.add(member, remote=RemoteRef(provider="youtube", remote_id="x", url="https://www.youtube.com/watch?v=x",
                                       title="Remote", uploader=None, artwork_url=None, duration=None))

    (playlist,) = list_playlists(session, member)

    assert (playlist.id, playlist.name, playlist.kind) == (synthetic_id(f"watchqueue:{member.id}"), "Watchlist", "watchlist")
    assert playlist.id == watchlist_id(member) and _items(playlist) == ["pad-v"]
    assert has_playlists(session, member) is True


def test_add_resolves_titles_to_the_preferred_version_and_rejects_series() -> None:
    session, _owner, member = _library()
    add_progress(session, "member", "arr-v")  # the member last watched this version
    session.commit()
    playlist = watchlist_id(member)

    add_to_playlist(session, member, playlist, ["arr", "show-s1e2", "pad-v"])

    assert _items(get_playlist(session, member, playlist)) == ["arr-v", "show-s1e2-v", "pad-v"]
    with pytest.raises(PlaylistBadRequest):
        add_to_playlist(session, member, playlist, ["show"])
    with pytest.raises(PlaylistNotFound):
        add_to_playlist(session, member, playlist, ["priv-v"])
    with pytest.raises(PlaylistNotFound):
        add_to_playlist(session, member, playlist, ["nope"])


def test_largest_file_is_the_preferred_version_without_history() -> None:
    session, _owner, member = _library()

    add_to_playlist(session, member, watchlist_id(member), ["arr"])

    assert _items(get_playlist(session, member, watchlist_id(member))) == ["arr-4k"]


def test_remove_and_move_by_entry_id_and_a_stale_revision_conflicts() -> None:
    session, _owner, member = _library()
    playlist = watchlist_id(member)
    add_to_playlist(session, member, playlist, ["arr-v", "pad-v", "show-s1e1-v"])
    entries = get_playlist(session, member, playlist).entries

    move_playlist_entry(session, member, playlist, entries[2].entry_id, 0)
    assert _items(get_playlist(session, member, playlist)) == ["show-s1e1-v", "arr-v", "pad-v"]
    remove_from_playlist(session, member, playlist, [entries[0].entry_id])
    assert _items(get_playlist(session, member, playlist)) == ["show-s1e1-v", "pad-v"]
    with pytest.raises(PlaylistConflict):
        move_playlist_entry(session, member, playlist, entries[1].entry_id, 0, expected_revision=1)
    with pytest.raises(PlaylistNotFound):
        move_playlist_entry(session, member, playlist, "nope", 0)


def test_collections_are_playlists_with_owner_and_smart_rules_enforced() -> None:
    session, owner, member = _library()
    collections = HouseholdCollectionService(session)
    shared = collections.create(owner_user_id="owner", name="Household", visibility="shared")
    collections.add_item(member_user_id="owner", collection_id=shared.id, library_item_id="priv-v")
    collections.add_item(member_user_id="owner", collection_id=shared.id, library_item_id="pad-v")
    smart = collections.create(owner_user_id="member", name="Smart", rules={"type": "movie", "match": "all", "conditions": [], "sort": None, "limit": 100})
    hidden = collections.create(owner_user_id="owner", name="Mine", visibility="private")

    by_id = {playlist.id: playlist for playlist in list_playlists(session, member)}

    assert set(by_id) == {shared.id, smart.id}
    assert _items(by_id[shared.id]) == ["pad-v"]  # the owner's private item stays invisible
    assert by_id[smart.id].kind == "smart" and set(_items(by_id[smart.id])) == {"arr-4k", "pad-v"}
    with pytest.raises(PlaylistForbidden):
        add_to_playlist(session, member, shared.id, ["arr-v"])  # not the owner
    with pytest.raises(PlaylistForbidden):
        add_to_playlist(session, member, smart.id, ["arr-v"])  # smart collections change through their rules
    with pytest.raises(PlaylistNotFound):
        get_playlist(session, member, hidden.id)
    add_to_playlist(session, owner, shared.id, ["arr"])
    assert _items(get_playlist(session, owner, shared.id)) == ["priv-v", "pad-v", "arr-4k"]


def test_watch_queue_is_a_playlist_over_http(vault: Vault) -> None:
    movie_tree(vault.media)
    fake_show(vault.media, 2)
    scan(vault.admin, vault.media)
    arrival, series, eps = title_ids(vault, "movie")["Arrival"], title_ids(vault, "series")["Vault Show"], episodes(vault)
    tv = login(vault)
    queue = jellyfin_id(synthetic_id(f"watchqueue:{ADMIN_ID}"))
    items_url = f"/Playlists/{queue}/Items"
    assert "playlists" not in view_types(vault, tv.headers)  # empty queue, no collections

    assert vault.jf.post(items_url, headers=tv.headers, params={"ids": f"{jellyfin_id(eps[1]['id'])},{jellyfin_id(arrival)}"}).status_code == 204
    assert vault.jf.post(items_url, headers=tv.headers, params={"ids": jellyfin_id(series)}).status_code == 400  # series are not playable entries
    assert vault.jf.post(items_url, headers=tv.headers, params={"ids": "nope"}).status_code == 400
    assert "playlists" in view_types(vault, tv.headers)
    assert "Watchlist" in names(vault.jf.get("/Items", headers=tv.headers, params={"ParentId": PLAYLISTS_VIEW}))

    rows = vault.jf.get(items_url, headers=tv.headers).json()["Items"]
    assert [row["Id"] for row in rows] == [jellyfin_id(eps[1]["id"]), jellyfin_id(arrival)]  # entries show as their title
    assert all(len(row["PlaylistItemId"]) == 32 for row in rows)
    assert vault.jf.get(f"/Playlists/{queue}", headers=tv.headers).json() == {"OpenAccess": False, "Shares": [], "ItemIds": [row["Id"] for row in rows]}
    assert [row["Id"] for row in vault.jf.get("/Items", headers=tv.headers, params={"ParentId": queue}).json()["Items"]] == [row["Id"] for row in rows]
    assert len(vault.admin.get("/api/me/watch-queue").json()["entries"]) == 2  # the same queue Lumina's UI shows

    assert vault.jf.post(f"{items_url}/{rows[1]['PlaylistItemId']}/Move/0", headers=tv.headers).status_code == 204
    assert [row["Id"] for row in vault.jf.get(items_url, headers=tv.headers).json()["Items"]] == [jellyfin_id(arrival), jellyfin_id(eps[1]["id"])]
    assert vault.jf.delete(items_url, headers=tv.headers, params={"entryIds": rows[1]["PlaylistItemId"]}).status_code == 204
    assert [row["Id"] for row in vault.jf.get(items_url, headers=tv.headers).json()["Items"]] == [jellyfin_id(eps[1]["id"])]
    assert vault.jf.post(f"{items_url}/{'0' * 32}/Move/0", headers=tv.headers).status_code == 404

    bob = login(vault, "bob", "bob-tv")
    assert vault.jf.get(items_url, headers=bob.headers).status_code == 404  # someone else's queue id

    # Final review #22: clients fetch the folder itself before listing it (jellyfin-web's item page).
    for url in (f"/Items/{queue}", f"/Users/{tv.user_id}/Items/{queue}"):
        opened = vault.jf.get(url, headers=tv.headers).json()
        assert (opened["Id"], opened["Type"], opened["Name"]) == (queue, "Playlist", "Watchlist")
    assert names(vault.jf.get("/Items", headers=tv.headers, params={"Ids": queue})) == ["Watchlist"]
    assert vault.jf.get(f"/Items/{queue}", headers=bob.headers).status_code == 404
    assert vault.jf.get("/Items", headers=bob.headers, params={"Ids": queue}).json()["Items"] == []


def test_collections_as_playlists_respect_ownership_and_smart_rules(vault: Vault) -> None:
    movie_tree(vault.media)
    scan(vault.admin, vault.media)
    arrival = title_ids(vault, "movie")["Arrival"]
    shared = vault.admin.post("/api/collections", json={"name": "Family night", "visibility": "shared"}).json()["id"]
    private = vault.admin.post("/api/collections", json={"name": "Mine", "visibility": "private"}).json()["id"]
    smart = vault.admin.post("/api/collections", json={"name": "Smart", "visibility": "private"}).json()["id"]
    with db_module.SessionLocal() as db:
        db.get(HouseholdCollection, smart).rules = {"type": "movie", "match": "all", "conditions": [], "sort": {"field": "name", "order": "asc"}, "limit": 50}
        db.commit()
    tv, bob = login(vault), login(vault, "bob", "bob-tv")

    url = f"/Playlists/{jellyfin_id(shared)}/Items"
    assert vault.jf.post(url, headers=tv.headers, params={"ids": jellyfin_id(arrival)}).status_code == 204
    assert vault.jf.post(url, headers=tv.headers, params={"ids": jellyfin_id(arrival)}).status_code == 204  # idempotent
    assert names(vault.jf.get(url, headers=tv.headers)) == ["Arrival"]
    assert vault.jf.get(f"/Playlists/{jellyfin_id(shared)}", headers=tv.headers).json()["OpenAccess"] is True

    assert vault.jf.get(url, headers=bob.headers).json()["Items"] == []  # bob sees the list, not admin's private film
    assert vault.jf.post(url, headers=bob.headers, params={"ids": jellyfin_id(arrival)}).status_code == 403  # visible, not his
    first_entry = vault.jf.get(url, headers=tv.headers).json()["Items"][0]["PlaylistItemId"]
    assert vault.jf.delete(url, headers=bob.headers, params={"entryIds": first_entry}).status_code == 403
    assert vault.jf.get(f"/Playlists/{jellyfin_id(private)}/Items", headers=bob.headers).status_code == 404
    assert vault.jf.get(f"/Items/{jellyfin_id(private)}", headers=bob.headers).status_code == 404  # final review #22
    assert vault.jf.get(f"/Items/{jellyfin_id(private)}", headers=tv.headers).json()["Type"] == "Playlist"
    assert vault.jf.get(f"/Items/{jellyfin_id(shared)}", headers=bob.headers).json()["Name"] == "Family night"
    assert vault.jf.post(f"/Playlists/{jellyfin_id(smart)}/Items", headers=tv.headers, params={"ids": jellyfin_id(arrival)}).status_code == 403
    listed = vault.jf.get("/Items", headers=tv.headers, params={"ParentId": PLAYLISTS_VIEW}).json()["Items"]
    assert jellyfin_id(smart) in [row["Id"] for row in listed]  # smart collections are read-only playlists (G)
