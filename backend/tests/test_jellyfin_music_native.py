"""Native Audio retains the music services' member and credential boundaries."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.main import app
from app.models import AppSettings, DeviceToken, LibraryItem, MediaTitle, MemberAccess, StorageRoot
from app.security import session_digest
from app.services.media_titles import jellyfin_id
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, ROOT, add_file, jellyfin_household, mediabrowser, uid

BASE = "/Lumina/Music"
ARTIST, ALBUM, ONE, TWO, HIDDEN = [uid(n) for n in range(850, 855)]


@pytest.fixture
def native(tmp_path):  # noqa: ANN001, ANN201
    jellyfin_household(tmp_path / "media")
    with db_module.SessionLocal.begin() as db:
        root = Path(db.get(StorageRoot, ROOT).path)
        db.add_all([MediaTitle(id=ARTIST, type="artist", key="native:artist", root_id=ROOT, name="Artist"),
                    MediaTitle(id=ALBUM, type="album", key="native:album", root_id=ROOT, parent_id=ARTIST, name="Album", year=2020,
                               images={"Primary": {"path": "native/cover.png"}})])
        db.flush()
        for item_id, owner, visibility, number in [(ONE, ALICE, "shared", 1), (TWO, ALICE, "shared", 2), (HIDDEN, BOB, "private", 3)]:
            add_file(db, root, f"native/{number}.flac", item_id=item_id, title_id=ALBUM, owner=owner, visibility=visibility,
                     kind="track", title=f"Track {number}", metadata={"artist": "Artist", "track_number": number,
                     "genres": ["Soul"], "lyrics": "[00:01.00]Synthetic lyric", "replaygain_track_peak": 0.8,
                     "replaygain_album_gain_db": -4}, probe={"audio_codec": "flac", "streams": []})
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def call(client, method, path, *, token=ALICE_TOKEN, **kwargs):  # noqa: ANN001, ANN201
    return client.request(method, BASE + path, headers=mediabrowser(token) if token else {}, **kwargs)


def session_body(revision, ids, position=0):  # noqa: ANN001, ANN201
    return {"base_revision": revision, "item_ids": ids, "current_index": len(ids) - 1 if ids else None,
            "position_seconds": position, "repeat": "all", "shuffle": False, "active_output": {"kind": "browser"}}


def assert_native_track(track):  # noqa: ANN001, ANN201
    assert track["id"] in (ONE, TWO)
    assert track["audio_url"] == f"/Audio/{jellyfin_id(track['id'])}/stream"
    assert track["artwork_url"] == f"/Items/{jellyfin_id(ALBUM)}/Images/Primary"
    assert "/api/" not in str(track) and "ApiKey" not in str(track)


def test_playlist_crud_sharing_revisions_and_smart_tracks(native):  # noqa: ANN001
    body = {"name": "Saved queue", "shared": True, "item_ids": [jellyfin_id(TWO), ONE]}
    created = call(native, "POST", "/playlists", json=body)
    assert created.status_code == 200
    playlist = created.json()
    playlist_id = playlist["id"]
    assert playlist["can_edit"] and playlist["revision"] == 1 and playlist["count"] == 2
    listed = call(native, "GET", "/playlists", token=BOB_TOKEN).json()
    assert listed[0]["id"] == playlist_id and not listed[0]["can_edit"]
    tracks = call(native, "GET", f"/playlists/{jellyfin_id(playlist_id)}/tracks").json()
    assert [t["id"] for t in tracks] == [TWO, ONE]
    for track in tracks:
        assert_native_track(track)
    assert call(native, "PUT", f"/playlists/{playlist_id}", token=BOB_TOKEN, json={**body, "revision": 1}).status_code == 403
    assert call(native, "DELETE", f"/playlists/{playlist_id}", token=BOB_TOKEN).status_code == 403
    assert call(native, "PUT", f"/playlists/{playlist_id}", json={**body, "revision": 0}).status_code == 409
    assert call(native, "PUT", f"/playlists/{playlist_id}", json={**body, "name": "Renamed", "revision": 1}).json()["revision"] == 2
    smart = call(native, "POST", "/playlists", json={"name": "Smart", "rules": {"artist_id": jellyfin_id(ARTIST), "genres": ["Soul"], "year_from": 2019, "year_to": 2021}}).json()
    assert smart["rules"]["artist_id"] == ARTIST and smart["count"] is None
    assert {t["id"] for t in call(native, "GET", f"/playlists/{smart['id']}/tracks").json()} == {ONE, TWO}
    assert call(native, "POST", "/playlists", json={"name": "Forbidden", "item_ids": [HIDDEN]}).status_code == 404
    assert call(native, "DELETE", f"/playlists/{playlist_id}").json() == {"deleted": True}
    assert call(native, "GET", f"/playlists/{playlist_id}/tracks").status_code == 404


def test_member_queue_duplicates_rewind_conflict_and_visibility_pruning(native):  # noqa: ANN001
    ids = [jellyfin_id(ONE), TWO, ONE]
    saved = call(native, "PUT", "/session", json={**session_body(0, ids, 50),
                 "entries": [{"kind": "track", "item_id": item_id} for item_id in ids], "entry_index": 2})
    assert saved.status_code == 200
    assert [t["id"] for t in saved.json()["queue"]] == [ONE, TWO, ONE]
    assert_native_track(saved.json()["queue"][0])
    rewound = call(native, "PUT", "/session", json=session_body(1, ids, 7)).json()
    assert (rewound["revision"], rewound["position_seconds"]) == (2, 7)
    conflict = call(native, "PUT", "/session", json=session_body(1, [TWO], 1))
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "music_session_conflict"
    assert conflict.json()["detail"]["session"]["revision"] == 2
    assert_native_track(conflict.json()["detail"]["session"]["queue"][0])
    assert call(native, "GET", "/session", token=BOB_TOKEN).json()["queue"] == []
    with db_module.SessionLocal.begin() as db:
        item = db.get(LibraryItem, TWO)
        item.visibility, item.user_id = "private", BOB
    pruned = call(native, "GET", "/session").json()
    assert [t["id"] for t in pruned["queue"]] == [ONE, ONE]
    assert (pruned["removed_count"], pruned["current_index"], pruned["position_seconds"]) == (1, 1, 7)
    assert pruned["revision"] == 3


def test_native_metadata_lyrics_mixes_and_queue_mapping(native):  # noqa: ANN001
    track = call(native, "GET", f"/tracks/{jellyfin_id(ONE)}").json()
    assert_native_track(track)
    assert track["album_gain_db"] == -4 and track["peak"] == 0.8
    assert call(native, "GET", f"/tracks/{ONE}/lyrics").json()["cues"] == [{"start": 1.0, "text": "Synthetic lyric"}]
    queue = call(native, "POST", "/queue", json={"title_id": jellyfin_id(ALBUM)}).json()
    assert [t["id"] for t in queue] == [ONE, TWO]
    for kind in ("unplayed", "rotation", "radio"):
        mixed = call(native, "GET", f"/mixes/{kind}", params={"seed_id": jellyfin_id(ONE)})
        assert mixed.status_code == 200 and {t["id"] for t in mixed.json()} == {ONE, TWO}
    assert call(native, "GET", f"/tracks/{HIDDEN}/lyrics").status_code == 404


def test_download_permission_conversion_ranges_and_native_auth(native, monkeypatch, tmp_path):  # noqa: ANN001
    from app.services import music_audio
    converted = tmp_path / "converted.flac"
    converted.write_bytes(b"fLaCconverted-audio")
    original = music_audio.playback_file
    formats = []

    def playback(source, format_):  # noqa: ANN001, ANN202
        formats.append(format_)
        return original(source, format_) if format_ == "native" else music_audio.PlaybackFile(converted, "audio/flac")

    monkeypatch.setattr(music_audio, "playback_file", playback)
    path = f"/tracks/{jellyfin_id(ONE)}/download"
    response = native.get(BASE + path, params={"format": "flac"}, headers={**mediabrowser(ALICE_TOKEN), "Range": "bytes=0-3"})
    assert (response.status_code, response.content) == (206, b"fLaC")
    assert response.headers["content-type"] == "audio/flac"
    assert response.headers["content-range"] == f"bytes 0-3/{converted.stat().st_size}"
    assert call(native, "HEAD", path).headers["content-length"] == "4096"
    assert call(native, "GET", path, token=None).status_code == 401
    assert call(native, "GET", f"/tracks/{HIDDEN}/download").status_code == 404
    assert call(native, "GET", path, params={"format": "bogus"}).status_code == 400
    with db_module.SessionLocal.begin() as db:
        db.add(MemberAccess(user_id=ALICE, can_download=False))
    before = len(formats)
    assert call(native, "GET", path, params={"format": "flac"}).status_code == 403
    assert call(native, "HEAD", path).status_code == 403
    assert len(formats) == before
    assert native.get("/api/music/playlists", headers=mediabrowser(ALICE_TOKEN)).status_code == 401


def test_opt_in_revocation_and_browser_credentials_do_not_cross_boundary(native):  # noqa: ANN001
    assert call(native, "GET", "/playlists", token=None).status_code == 401
    with db_module.SessionLocal.begin() as db:
        token = db.scalar(select(DeviceToken).where(DeviceToken.token_digest == session_digest(BOB_TOKEN)))
        token.kind = "agent"
    assert native.get(BASE + "/playlists", headers={"Authorization": f"Bearer {BOB_TOKEN}"}).status_code == 401
    assert native.get("/api/music/playlists", headers={"Authorization": f"Bearer {BOB_TOKEN}"}).status_code == 200
    with db_module.SessionLocal.begin() as db:
        db.delete(db.scalar(select(DeviceToken).where(DeviceToken.token_digest == session_digest(ALICE_TOKEN))))
    assert call(native, "GET", "/session").status_code == 401
    with db_module.SessionLocal.begin() as db:
        db.scalar(select(AppSettings)).jellyfin_enabled = False
    assert call(native, "GET", "/playlists", token=BOB_TOKEN).status_code == 404


def test_sonos_uses_acknowledged_handoff_owner_control_and_revoke(native, monkeypatch):  # noqa: ANN001
    from app.services import music_sonos, public_address
    queued, controls = [], []
    monkeypatch.setattr(public_address, "local_origin", lambda: "http://speaker-reachable.test")
    monkeypatch.setattr(music_sonos.service, "discover", lambda: [{"id": "speaker", "name": "Room", "model": "Synthetic", "coordinator_id": "speaker"}])
    monkeypatch.setattr(music_sonos.service, "play_queue", lambda speaker, tracks: queued.extend(tracks) or {"ok": True})
    monkeypatch.setattr(music_sonos.service, "status", lambda speaker: {"playing": True, "track": {"track_number": 1}})
    monkeypatch.setattr(music_sonos.service, "control", lambda speaker, action, value: controls.append((action, value)) or {"ok": True})
    assert call(native, "GET", "/sonos").json()[0]["id"] == "speaker"
    call(native, "PUT", "/session", json=session_body(0, [ONE, TWO], 3))
    cast_body = {"speaker_id": "speaker", "item_ids": [jellyfin_id(ONE), TWO], "base_revision": 1, "current_index": 0, "position_seconds": 3}
    result = call(native, "POST", "/cast", json=cast_body)
    assert result.status_code == 200
    output = result.json()
    assert output["queue_indices"] == [0, 1] and output["session"]["revision"] == 2
    assert output["session"]["active_output"]["kind"] == "sonos"
    assert_native_track(output["session"]["queue"][0])
    assert len(queued) == 2 and controls == [("seek", 3)]
    ticket = output["ticket_id"]
    assert call(native, "GET", f"/cast/{ticket}/status").status_code == 200
    assert call(native, "POST", f"/cast/{ticket}/control", token=BOB_TOKEN, json={"action": "pause"}).status_code == 404
    conflict = call(native, "POST", "/cast", json={**cast_body, "item_ids": [TWO]})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "music_session_conflict"
    assert_native_track(conflict.json()["detail"]["session"]["queue"][0])
    assert call(native, "POST", f"/cast/{ticket}/control", json={"action": "stop"}).status_code == 200
    assert call(native, "GET", f"/cast/{ticket}/status").status_code == 404
