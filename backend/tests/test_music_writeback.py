"""Atomic music tag writeback: synthetic media only, never a real library file."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading

import mutagen
from mutagen.id3 import ID3, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
import pytest

from app.services import music_writeback as writeback_service
from app.services.audio_tags import embedded_picture, read_tags


REPO = Path(__file__).resolve().parents[2]
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
RECORDING_ID = "12345678-1234-4678-9234-123456789012"
RELEASE_ID = "22345678-1234-4678-9234-123456789012"
ARTIST_ID = "32345678-1234-4678-9234-123456789012"
ALBUM_ARTIST_ID = "42345678-1234-4678-9234-123456789012"


def music_fixture():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("music_fixture", REPO / "scripts" / "e2e" / "music_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pcm_sha256(path: Path) -> str:
    pcm = subprocess.check_output(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "s16le", "-"], stderr=subprocess.PIPE)
    return hashlib.sha256(pcm).hexdigest()


def _set_unrelated_tag(path: Path, codec: str) -> None:
    audio = mutagen.File(path)
    assert audio is not None
    if audio.tags is None:
        audio.add_tags()
    if codec == "mp3":
        audio.tags.add(TXXX(encoding=3, desc="Lumina Keep", text=["unrelated-value"]))
    elif codec == "flac":
        audio["x-lumina-keep"] = ["unrelated-value"]
    elif codec == "opus":
        audio["x-lumina-keep"] = ["unrelated-value"]
    else:
        audio["----:com.apple.iTunes:Lumina Keep"] = [MP4FreeForm(b"unrelated-value")]
    audio.save()


def _assert_unrelated_tag(path: Path, codec: str) -> None:
    audio = mutagen.File(path)
    assert audio is not None and audio.tags is not None
    if codec == "mp3":
        assert audio.tags["TXXX:Lumina Keep"].text == ["unrelated-value"]
    elif codec in {"flac", "opus"}:
        assert audio.tags["x-lumina-keep"] == ["unrelated-value"]
    else:
        assert bytes(audio.tags["----:com.apple.iTunes:Lumina Keep"][0]) == b"unrelated-value"


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["flac", "mp3", "m4a", "opus"])
def test_round_trip_common_fields_artwork_and_audio_essence(tmp_path: Path, codec: str) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / f"song.{codec}", {"title": "Before", "artist": "Old Artist", "album": "Old Album"}, codec=codec)
    artwork = fixture.cover_image(tmp_path / "cover.jpg", "32x32").read_bytes()
    _set_unrelated_tag(path, codec)
    before_pcm = _pcm_sha256(path)
    original_mode = stat.S_IMODE(path.stat().st_mode)

    result = writeback_service.writeback(
        path,
        {
            "title": "After",
            "artist": "New Artist",
            "album_artist": "New Artist",
            "album": "New Album",
            "track_number": 7,
            "disc_number": 3,
            "year": 2024,
            "genres": ["Jazz", "Rock"],
            "lyrics": "A synthetic lyric line",
            "musicbrainz_recording_id": RECORDING_ID,
            "musicbrainz_release_id": RELEASE_ID,
            "musicbrainz_artist_id": ARTIST_ID,
            "musicbrainz_album_artist_id": ALBUM_ARTIST_ID,
        },
        backup_root=tmp_path / "backups",
        artwork=artwork,
        artwork_type="image/jpeg",
    )

    assert result["backup_id"]
    assert result["format"] == ("m4a" if codec == "m4a" else codec)
    assert result["bytes"] == path.stat().st_size
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    assert read_tags(path).title == "After"
    tags = read_tags(path)
    assert tags is not None
    assert (tags.artist, tags.album_artist, tags.album, tags.track, tags.disc, tags.year, tags.genres) == (
        "New Artist", "New Artist", "New Album", 7, 3, 2024, ("Jazz", "Rock")
    )
    assert tags.musicbrainz_album_artist_id == ALBUM_ARTIST_ID
    assert embedded_picture(path, 8 * 1024 * 1024) == ("image/jpeg", artwork)
    assert _pcm_sha256(path) == before_pcm
    _assert_unrelated_tag(path, codec)

    audio = mutagen.File(path)
    assert audio is not None and audio.tags is not None
    if codec == "mp3":
        assert audio.tags.getall("USLT")[0].text == "A synthetic lyric line"
        assert audio.tags["TXXX:MusicBrainz Recording Id"].text == [RECORDING_ID]
        assert audio.tags["TXXX:MusicBrainz Album Artist Id"].text == [ALBUM_ARTIST_ID]
    elif codec in {"flac", "opus"}:
        assert audio.tags["lyrics"] == ["A synthetic lyric line"]
        assert audio.tags["musicbrainz_trackid"] == [RECORDING_ID]
        assert audio.tags["musicbrainz_albumartistid"] == [ALBUM_ARTIST_ID]
    else:
        assert audio.tags["©lyr"] == ["A synthetic lyric line"]
        assert bytes(audio.tags["----:com.apple.iTunes:MusicBrainz Track Id"][0]) == RECORDING_ID.encode()
        assert bytes(audio.tags["----:com.apple.iTunes:MusicBrainz Album Artist Id"][0]) == ALBUM_ARTIST_ID.encode()


@needs_ffmpeg
def test_stale_preview_fingerprint_is_refused_before_backup_or_mutation(tmp_path: Path) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.flac", {"title": "Before"}, codec="flac")
    backup_root = tmp_path / "backups"
    preview_stat = path.stat()
    before = path.read_bytes()
    os.utime(path, ns=(preview_stat.st_atime_ns, preview_stat.st_mtime_ns + 1_000_000_000))

    with pytest.raises(writeback_service.WritebackError, match="changed since writeback preview"):
        writeback_service.writeback(
            path,
            {"title": "Stale update"},
            backup_root=backup_root,
            expected_fingerprint=(preview_stat.st_size, preview_stat.st_mtime_ns),
        )

    assert path.read_bytes() == before
    assert not backup_root.exists()


@needs_ffmpeg
def test_competing_restore_waits_for_writeback_and_refuses_the_newer_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.flac", {"title": "Before"}, codec="flac")
    backup_root = tmp_path / "backups"
    first = writeback_service.writeback(path, {"title": "First"}, backup_root=backup_root)
    source_stat = path.stat()

    writer_at_replace = threading.Event()
    allow_writer_replace = threading.Event()
    restore_called = threading.Event()
    restore_entered = threading.Event()
    real_replace = writeback_service.os.replace
    real_restore_locked = writeback_service._restore_locked

    def controlled_replace(source, target):  # noqa: ANN001, ANN202
        if threading.current_thread().name == "competing-writeback" and Path(target) == path:
            writer_at_replace.set()
            if not allow_writer_replace.wait(5):
                raise AssertionError("test did not release the competing writeback")
        return real_replace(source, target)

    def observed_restore_locked(artifact_path, backup_id, root):  # noqa: ANN001, ANN202
        restore_entered.set()
        return real_restore_locked(artifact_path, backup_id, root)

    monkeypatch.setattr(writeback_service.os, "replace", controlled_replace)
    monkeypatch.setattr(writeback_service, "_restore_locked", observed_restore_locked)

    def run_writeback():  # noqa: ANN202
        threading.current_thread().name = "competing-writeback"
        return writeback_service.writeback(
            path,
            {"title": "Newer"},
            backup_root=backup_root,
            expected_fingerprint=(source_stat.st_size, source_stat.st_mtime_ns),
        )

    def run_restore():  # noqa: ANN202
        threading.current_thread().name = "competing-restore"
        restore_called.set()
        return writeback_service.restore(path, first["backup_id"], backup_root=backup_root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writeback_future = executor.submit(run_writeback)
        assert writer_at_replace.wait(5)
        restore_future = executor.submit(run_restore)
        assert restore_called.wait(5)
        assert not restore_entered.wait(0.2)
        allow_writer_replace.set()
        writeback_future.result(timeout=10)
        with pytest.raises(writeback_service.WritebackError, match="changed since this writeback"):
            restore_future.result(timeout=10)

    assert read_tags(path).title == "Newer"


@needs_ffmpeg
def test_unrelated_artifacts_can_write_while_another_path_is_at_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = music_fixture()
    first = fixture.write_track(tmp_path / "first.flac", {"title": "First"}, codec="flac")
    second = next(
        candidate
        for index in range(1, 256)
        if writeback_service._path_lock(candidate := tmp_path / f"second-{index}.flac")
        is not writeback_service._path_lock(first)
    )
    fixture.write_track(second, {"title": "Second"}, codec="flac")
    backup_root = tmp_path / "backups"
    first_at_replace = threading.Event()
    allow_first_replace = threading.Event()
    real_replace = writeback_service.os.replace

    def controlled_replace(source, target):  # noqa: ANN001, ANN202
        if threading.current_thread().name == "first-writeback" and Path(target) == first:
            first_at_replace.set()
            if not allow_first_replace.wait(5):
                raise AssertionError("test did not release the first writeback")
        return real_replace(source, target)

    monkeypatch.setattr(writeback_service.os, "replace", controlled_replace)

    def write_first():  # noqa: ANN202
        threading.current_thread().name = "first-writeback"
        return writeback_service.writeback(first, {"title": "First updated"}, backup_root=backup_root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(write_first)
        assert first_at_replace.wait(5)
        second_future = executor.submit(
            writeback_service.writeback,
            second,
            {"title": "Second updated"},
            backup_root=backup_root,
        )
        second_future.result(timeout=10)
        allow_first_replace.set()
        first_future.result(timeout=10)

    assert read_tags(first).title == "First updated"
    assert read_tags(second).title == "Second updated"


@needs_ffmpeg
def test_backup_restore_and_changed_since_restore_refusal(tmp_path: Path) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.flac", {"title": "Before", "artist": "Artist"}, codec="flac")
    original = path.read_bytes()
    root = tmp_path / "backups"
    result = writeback_service.writeback(path, {"title": "After"}, backup_root=root)
    changed = path.read_bytes()
    assert changed != original

    restored = writeback_service.restore(path, result["backup_id"], backup_root=root)
    assert restored == {"backup_id": result["backup_id"], "format": "flac", "bytes": len(original)}
    assert path.read_bytes() == original

    result = writeback_service.writeback(path, {"title": "Second"}, backup_root=root)
    path.write_bytes(path.read_bytes() + b"later-edit")
    current = path.read_bytes()
    with pytest.raises(writeback_service.WritebackError, match="changed"):
        writeback_service.restore(path, result["backup_id"], backup_root=root)
    assert path.read_bytes() == current


@needs_ffmpeg
def test_valid_png_artwork_is_embedded(tmp_path: Path) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.opus", {"title": "Before"}, codec="opus")
    artwork = fixture.cover_image(tmp_path / "cover.png", "24x18").read_bytes()
    writeback_service.writeback(path, {}, backup_root=tmp_path / "backups", artwork=artwork, artwork_type="image/png")
    assert embedded_picture(path, 8 * 1024 * 1024) == ("image/png", artwork)


@needs_ffmpeg
def test_raw_aac_is_rejected_because_mutagen_cannot_tag_adts(tmp_path: Path) -> None:
    path = tmp_path / "song.aac"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(path)],
        check=True,
    )
    original = path.read_bytes()
    with pytest.raises(writeback_service.WritebackError, match="AAC"):
        writeback_service.writeback(path, {"title": "Nope"}, backup_root=tmp_path / "backups")
    assert path.read_bytes() == original


@needs_ffmpeg
def test_unrelated_metadata_survives_a_second_writeback(tmp_path: Path) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.mp3", {"title": "Before"}, codec="mp3")
    audio = mutagen.File(path)
    assert audio is not None and isinstance(audio.tags, ID3)
    audio.tags.add(TXXX(encoding=3, desc="Keep Me", text=["yes"]))
    audio.save()
    writeback_service.writeback(path, {"title": "After"}, backup_root=tmp_path / "backups")
    audio = mutagen.File(path)
    assert audio is not None and isinstance(audio.tags, ID3)
    assert audio.tags["TXXX:Keep Me"].text == ["yes"]


@needs_ffmpeg
def test_rejects_symlink_read_only_unsupported_traversal_and_malformed_artwork(tmp_path: Path) -> None:
    fixture = music_fixture()
    real = fixture.write_track(tmp_path / "real.flac", {"title": "Before"}, codec="flac")
    original = real.read_bytes()
    (tmp_path / "link.flac").symlink_to(real)
    with pytest.raises(writeback_service.WritebackError):
        writeback_service.writeback(tmp_path / "link.flac", {"title": "Nope"}, backup_root=tmp_path / "backups")
    assert real.read_bytes() == original

    mode = stat.S_IMODE(real.stat().st_mode)
    os.chmod(real, 0o444)
    try:
        with pytest.raises(writeback_service.WritebackError, match="read-only"):
            writeback_service.writeback(real, {"title": "Nope"}, backup_root=tmp_path / "backups")
        assert real.read_bytes() == original
    finally:
        os.chmod(real, mode)

    unsupported = tmp_path / "unsupported.wav"
    unsupported.write_bytes(b"not an audio file")
    before_unsupported = unsupported.read_bytes()
    with pytest.raises(writeback_service.WritebackError):
        writeback_service.writeback(unsupported, {"title": "Nope"}, backup_root=tmp_path / "backups")
    assert unsupported.read_bytes() == before_unsupported

    with pytest.raises(writeback_service.WritebackError):
        writeback_service.restore(real, "../escape", backup_root=tmp_path / "backups")
    with pytest.raises(writeback_service.WritebackError, match="valid JPEG"):
        writeback_service.writeback(real, {"title": "Nope"}, backup_root=tmp_path / "backups", artwork=b"\xff\xd8")
    assert real.read_bytes() == original


@needs_ffmpeg
def test_save_and_replace_failures_leave_the_original_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = music_fixture()
    path = fixture.write_track(tmp_path / "song.flac", {"title": "Before"}, codec="flac")
    original = path.read_bytes()

    def fail_save(audio, format_name):  # noqa: ANN001
        raise writeback_service.WritebackError("simulated save failure")

    monkeypatch.setattr(writeback_service, "_save_audio", fail_save)
    with pytest.raises(writeback_service.WritebackError, match="simulated"):
        writeback_service.writeback(path, {"title": "After"}, backup_root=tmp_path / "save-backups")
    assert path.read_bytes() == original

    monkeypatch.undo()
    original = path.read_bytes()

    def fail_replace(source, target):  # noqa: ANN001
        raise OSError("simulated replace failure")

    monkeypatch.setattr(writeback_service.os, "replace", fail_replace)
    with pytest.raises(writeback_service.WritebackError, match="replaced"):
        writeback_service.writeback(path, {"title": "After"}, backup_root=tmp_path / "replace-backups")
    assert path.read_bytes() == original
