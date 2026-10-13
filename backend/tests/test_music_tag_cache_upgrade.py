"""An upgrade learns newly supported source tags without requiring file changes."""
from pathlib import Path

from mutagen.flac import FLAC
from sqlalchemy import select

from app import db as db_module
from app.models import LibraryItem
from app.services import library_import
from app.services.audio_tags import AUDIO_TAG_VERSION
from test_audio_tags import music_fixture, needs_ffmpeg
from test_v1_titles import admin, media, scan  # noqa: F401


@needs_ffmpeg
def test_legacy_tag_cache_is_refreshed_once_even_when_the_artifact_fingerprint_is_unchanged(admin, media: Path, monkeypatch) -> None:  # noqa: ANN001
    path = music_fixture().write_track(media / "Music" / "Artist A" / "Album A" / "01 Song.flac", {
        "title": "Song", "artist": "Artist A", "album_artist": "Artist A", "album": "Album A",
    })
    audio = FLAC(path)
    audio["lyrics"] = ["Already embedded before the upgrade"]
    audio.save()
    fingerprint = path.stat().st_size, path.stat().st_mtime_ns
    assert scan(admin)["state"] == "succeeded"
    with db_module.SessionLocal() as db:
        item = db.scalar(select(LibraryItem).where(LibraryItem.title == "Song"))
        cached = {key: value for key, value in item.metadata_json["lumina_audio_tags"].items() if key not in {"tag_version", "lyrics"}}
        item.metadata_json = {**item.metadata_json, "lumina_audio_tags": cached, "lyrics": None}
        item_id = item.id
        db.commit()
    reads = []
    original = library_import.read_tags
    monkeypatch.setattr(library_import, "read_tags", lambda source: reads.append(source.name) or original(source))
    assert scan(admin)["state"] == "succeeded"
    assert reads == [path.name]
    assert (path.stat().st_size, path.stat().st_mtime_ns) == fingerprint
    with db_module.SessionLocal() as db:
        fields = db.get(LibraryItem, item_id).metadata_json
        assert fields["lyrics"] == "Already embedded before the upgrade"
        assert fields["lumina_audio_tags"]["tag_version"] == AUDIO_TAG_VERSION
    assert scan(admin)["state"] == "succeeded"
    assert reads == [path.name]
