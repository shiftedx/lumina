"""Exercise the Home and contextual-mix contracts against the disposable #343 server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

WEB_HOME = "/api/me/music-home"
NATIVE_HOME = "/Lumina/Music/home"
WEB_MIX = "/api/music/contextual-mix"
NATIVE_MIX = "/Lumina/Music/contextual-mix"
DEFAULT_SHELVES = ["resume", "recent", "playlists", "downloads", "mixes"]


def web_login(client: httpx.Client, username: str, password: str) -> None:
    session = client.post("/api/session/login", json={"username": username, "password": password})
    session.raise_for_status()
    client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
    client.headers["Origin"] = str(client.base_url).rstrip("/")


def native_login(client: httpx.Client, username: str, password: str, device: str) -> None:
    auth = f'MediaBrowser Client="Lumina Music Contract", Device="Proof", DeviceId="{device}", Version="1.0"'
    session = client.post("/Users/AuthenticateByName", json={"Username": username, "Pw": password},
                          headers={"Authorization": auth})
    session.raise_for_status()
    client.headers["Authorization"] = f'{auth}, Token="{session.json()["AccessToken"]}"'


def read(client: httpx.Client, path: str, **kwargs):
    return client.get(path, **kwargs).raise_for_status().json()


def normalized_id(value: str) -> str:
    return UUID(value).hex


def verify_home(owner: httpx.Client, native_owner: httpx.Client, member: httpx.Client, fixture: dict) -> None:
    original = read(owner, WEB_HOME)
    assert original["layout"] == {"order": DEFAULT_SHELVES, "hidden": []}
    assert original["resume"] and original["resume"]["position_seconds"] == 23
    assert read(member, WEB_HOME)["resume"] is None
    assert any(row["id"] == fixture["sharedMusicPlaylistID"] for row in original["playlists"])
    assert [row["kind"] for row in original["mixes"]] == ["rotation", "favorites", "unplayed"]

    # Browser writes with its session cookie and CSRF token. Native reads the
    # canonical order and resets it for that same member.
    requested = {"order": ["mixes", "resume"], "hidden": ["downloads"]}
    canonical = {"order": ["mixes", "resume", "recent", "playlists", "downloads"], "hidden": ["downloads"]}
    owner.put(WEB_HOME + "/layout", json=requested).raise_for_status()
    assert read(native_owner, NATIVE_HOME)["layout"] == canonical
    assert read(member, WEB_HOME)["layout"] == original["layout"]
    assert owner.put(WEB_HOME + "/layout", json={"order": ["resume", "resume"], "hidden": []}).status_code == 422
    assert read(owner, WEB_HOME)["layout"] == canonical
    native_owner.put(NATIVE_HOME + "/layout", json={"order": DEFAULT_SHELVES, "hidden": []}).raise_for_status()
    assert read(owner, WEB_HOME)["layout"] == original["layout"]
    assert read(owner, WEB_HOME)["resume"] == original["resume"]

    track = str(UUID(fixture["audioTrackID"]))
    owner.put(f"/api/library/{track}/playback",
              json={"position_seconds": 45, "duration_seconds": 90}).raise_for_status()
    assert any(normalized_id(row["id"]) == fixture["audioAlbumID"] for row in read(owner, WEB_HOME)["recent"])
    assert read(member, WEB_HOME)["recent"] == []
    private_track = str(UUID(fixture["musicHomePrivateTrackID"]))
    private_album = fixture["musicHomePrivateAlbumID"]
    member.put(f"/api/library/{private_track}/playback",
               json={"position_seconds": 1, "duration_seconds": 1}).raise_for_status()
    assert private_album in {normalized_id(row["id"]) for row in read(member, WEB_HOME)["recent"]}
    assert private_album not in {normalized_id(row["id"]) for row in read(owner, WEB_HOME)["recent"]}
    print("#344 PASS web/native layout round-trip, member isolation, Resume, recent albums, playlists, mix descriptors")


def verify_mixes(
    owner: httpx.Client, native_owner: httpx.Client, member: httpx.Client, native_member: httpx.Client, fixture: dict,
) -> None:
    first, second = fixture["audioTrackID"], fixture["audioSecondTrackID"]
    params = [("seed_kind", "track"), ("seed_id", str(UUID(first)))]
    track_mix = read(owner, WEB_MIX, params=params)
    assert track_mix == read(owner, WEB_MIX, params=params)
    ids = [normalized_id(row["id"]) for row in track_mix["items"]]
    assert first not in ids and second in ids and len(ids) == len(set(ids)) <= 100
    assert next(row["reason"]["code"] for row in track_mix["items"] if normalized_id(row["id"]) == second) == "same_artist"
    native_track_mix = read(native_owner, NATIVE_MIX, params=[("seed_kind", "track"), ("seed_id", first)])
    assert [normalized_id(row["id"]) for row in native_track_mix["items"]] == ids

    album = fixture["musicHomeMultidiscAlbumID"]
    album_mix = read(owner, WEB_MIX, params={"seed_kind": "album", "seed_id": str(UUID(album))})
    assert [(row["disc"], row["number"], row["reason"]["code"]) for row in album_mix["items"][:2]] == [
        (1, 1, "seed_track"), (2, 1, "seed_track"),
    ]
    artist_mix = read(native_owner, NATIVE_MIX, params={"seed_kind": "artist", "seed_id": fixture["audioArtistID"]})
    assert {first, second} <= {normalized_id(row["id"]) for row in artist_mix["items"]}

    omitted = params + [("exclude_item_ids", str(UUID(second)))]
    assert second not in [normalized_id(row["id"]) for row in read(owner, WEB_MIX, params=omitted)["items"]]
    exhausted = params + [("exclude_item_ids", str(UUID(item))) for item in ids]
    empty = read(owner, WEB_MIX, params=exhausted)
    assert empty["items"] == [] and empty["exhausted"] is True
    for seed_kind, key in (("album", "musicHomePrivateAlbumID"), ("track", "musicHomePrivateTrackID")):
        private_id = fixture[key]
        web_params = {"seed_kind": seed_kind, "seed_id": str(UUID(private_id))}
        native_params = {"seed_kind": seed_kind, "seed_id": private_id}
        assert owner.get(WEB_MIX, params=web_params).status_code == 404
        assert native_owner.get(NATIVE_MIX, params=native_params).status_code == 404
        assert member.get(WEB_MIX, params=web_params).status_code == 200
        assert native_member.get(NATIVE_MIX, params=native_params).status_code == 200

    assert isinstance(read(owner, "/api/music/mixes/rotation"), list)
    assert isinstance(read(native_owner, "/Lumina/Music/mixes/rotation"), list)
    print("#345 PASS track/artist/album seeds, reasons/order, exclusions, exhaustion, private seeds, legacy mixes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="private JSON from LUMINA_MUSIC_HOME_FIXTURE=1")
    fixture = json.loads(parser.parse_args().fixture.read_text())
    origin = urlsplit(fixture["serverURL"])
    if origin.scheme != "http" or origin.hostname != "127.0.0.1" or not fixture.get("musicHomeWaitRequestID"):
        parser.error("Use a disposable #343 loopback proof server.")
    owner_credentials = (fixture["username"], fixture["password"])
    member_credentials = (fixture["musicHomeMemberUsername"], fixture["musicHomeMemberPassword"])
    options = {"base_url": fixture["serverURL"], "timeout": 30, "trust_env": False}
    with (httpx.Client(**options) as owner, httpx.Client(**options) as native_owner,
          httpx.Client(**options) as member, httpx.Client(**options) as native_member):
        web_login(owner, *owner_credentials)
        web_login(member, *member_credentials)
        native_login(native_owner, *owner_credentials, "music-contract-owner")
        native_login(native_member, *member_credentials, "music-contract-member")
        verify_home(owner, native_owner, member, fixture)
        verify_mixes(owner, native_owner, member, native_member, fixture)


if __name__ == "__main__":
    main()
