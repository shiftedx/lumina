"""Music video associations retain permissions and follow the owned release."""
from datetime import timedelta

import pytest

from app.models import LibraryItem, MediaTitle, MusicLookup, MusicTrackOverride, User, utcnow
from app.services import music_providers, music_videos, streaming_gate
from test_music_api import ALBUM, ALICE, ARTIST, BOB, TRACKS, music_world  # noqa: F401
from title_support import add_file, uid


def test_structured_saved_video_matches_without_artist_in_display_title_and_hides_private_copies(music_world):  # noqa: ANN001, ANN201
    factory, _client_for, root = music_world
    with factory() as db:
        shared = add_file(db, root, "Videos/one.mp4", item_id=uid(8001), title_id=None, owner=ALICE,
                          kind="video", title="Song One — live", uploader="Example Label", remote_id="01234567890",
                          metadata={"artist": "Artist A", "track": "Song One", "categories": ["Music"]})
        private = add_file(db, root, "Videos/two.mp4", item_id=uid(8002), title_id=None, owner=BOB, visibility="private",
                           kind="video", title="Song One — cover", remote_id="01234567891",
                           metadata={"artist": "Artist A", "track": "Song One"})
        music_videos.associate_import(db, shared)
        db.commit()
        assert shared.metadata_json["music_artist_id"] == ARTIST
        videos = music_videos.saved(db, db.get(User, ALICE), "Artist A", "Song One")
        assert [(video.item_id, video.title, video.performance) for video in videos] == [(shared.id, shared.title, "live")]
        assert private.id not in [video.item_id for video in videos]
        db.get(MediaTitle, ARTIST).name = "New artist name"
        db.commit()
        renamed = music_videos.saved(db, db.get(User, ALICE), "New artist name", "Song One", artist_id=ARTIST)
        assert [video.item_id for video in renamed] == [shared.id]


def test_imported_performance_without_youtube_id_still_opens_the_visible_local_file(music_world):  # noqa: ANN001, ANN201
    factory, _client_for, root = music_world
    with factory() as db:
        item = add_file(db, root, "Videos/local.mp4", item_id=uid(8003), title_id=None, owner=ALICE,
                        kind="video", title="Song One", metadata={"artist": "Artist A", "track": "Song One"})
        db.commit()
        videos = music_videos.saved(db, db.get(User, ALICE), "Artist A", "Song One")
        assert len(videos) == 1 and videos[0].item_id == item.id
        assert videos[0].id == f"local:{item.id}" and videos[0].url == f"/watch/{item.id}"


def test_album_filters_artist_discovery_to_its_visible_effective_recordings(music_world, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    factory, client_for, _root = music_world
    monkeypatch.setattr(music_providers, "enqueue", lambda *_args: False)
    monkeypatch.setattr(music_providers, "pending", lambda *_args: False)
    monkeypatch.setattr(streaming_gate, "check", lambda *_args: None)
    with factory() as db:
        db.add(MusicTrackOverride(item_id=TRACKS[0], fields={"title": "Renamed recording"}))
        payload = {"items": [{"id": str(index).zfill(11), "title": f"Artist A - {name}",
                             "url": f"https://www.youtube.com/watch?v={str(index).zfill(11)}", "performance": "official"}
                            for index, name in enumerate(["Renamed recording", "Other song", "Private song", "Song One"], 1)]}
        db.add(MusicLookup(key=music_providers.key_for("artist_videos", "Artist A"), kind="videos", payload=payload,
                           refreshed_at=utcnow(), expires_at=utcnow() + timedelta(days=7)))
        db.commit()
    reply = client_for(ALICE).get(f"/api/music/titles/{ALBUM}/videos")
    assert reply.status_code == 200
    assert [video["title"] for video in reply.json()["items"]] == ["Artist A - Renamed recording"]
