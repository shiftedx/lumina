"""Episode stills are a bounded fallback for source-less local episodes."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app import db as db_module
from app.db import Base
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, TitleArtwork
from app.services import episode_stills

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _seed(tmp_path: Path, *, primary: dict | None = None, observation: str = "available") -> tuple[str, str, Path]:
    Base.metadata.create_all(bind=db_module.engine)
    root_path = tmp_path / "media"
    root_path.mkdir()
    video = root_path / "show" / "episode.mkv"
    video.parent.mkdir()
    subprocess.run([
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=12:duration=2", "-an", "-c:v", "libx264", "-preset", "ultrafast", str(video),
    ], check=True, timeout=30)
    root_id, title_id, item_id, artifact_id = (str(uuid.uuid4()) for _ in range(4))
    stat = video.stat()
    with db_module.SessionLocal() as db:
        db.add(StorageRoot(id=root_id, label="Media", path=str(root_path), mode="external", enabled=True,
                           observation={"state": observation}, identity=str(root_path.stat().st_dev)))
        db.add(MediaTitle(id=title_id, type="episode", key=f"episode:{title_id}", name="Episode", root_id=root_id, images={} if primary is None else {"Primary": primary}))
        db.add(LibraryItem(id=item_id, title_id=title_id, title="Episode", kind="episode", status="available", visibility="shared"))
        db.add(MediaArtifact(id=artifact_id, root_id=root_id, relative_path="show/episode.mkv", ownership="external", lifecycle="available",
                             size=stat.st_size, mtime_ns=stat.st_mtime_ns, probe={"duration": 2.0}))
        db.add(LibraryItemArtifact(library_item_id=item_id, artifact_id=artifact_id))
        db.commit()
    return title_id, artifact_id, video


@needs_ffmpeg
def test_source_less_available_episode_generates_and_reuses_a_private_still(tmp_path: Path) -> None:
    title_id, artifact_id, _ = _seed(tmp_path)
    with db_module.SessionLocal() as db:
        candidates = episode_stills.scan(db)
    assert len(candidates) == 1 and candidates[0].missing
    candidate = candidates[0]

    assert episode_stills.ensure(title_id)
    path = episode_stills._cache_path(candidate.source_key)
    assert path.is_file() and path.read_bytes().startswith(b"\xff\xd8")
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, title_id)
        assert title.images["Primary"] == {"frame": artifact_id, "tag": candidate.fingerprint}
        assert episode_stills.scan(db) == []
    before = path.stat().st_mtime_ns
    assert episode_stills.ensure(title_id)  # a cache hit is a successful available still, not another ffmpeg job
    assert path.stat().st_mtime_ns == before
    assert episode_stills.image_bytes({"frame": artifact_id, "tag": candidate.fingerprint}, title_id).startswith(b"\xff\xd8")


@needs_ffmpeg
def test_explicit_or_removed_episode_art_is_never_replaced(tmp_path: Path) -> None:
    title_id, _, _ = _seed(tmp_path, primary={"path": "show/user.jpg", "tag": "user"})
    with db_module.SessionLocal() as db:
        assert episode_stills.scan(db) == []
    assert not episode_stills.ensure(title_id)


@needs_ffmpeg
def test_changed_or_offline_episode_is_not_rendered_from_stale_registration(tmp_path: Path) -> None:
    title_id, _, video = _seed(tmp_path)
    video.touch()  # scanner fingerprint is now stale; the worker must wait for a fresh scan.
    assert not episode_stills.ensure(title_id)
    with db_module.SessionLocal() as db:
        root = db.scalar(__import__("sqlalchemy").select(StorageRoot))
        root.observation = {"state": "offline"}
        db.commit()
        assert episode_stills.scan(db) == []


@needs_ffmpeg
def test_artwork_worker_prepares_an_episode_still_once_then_reuses_it(tmp_path: Path) -> None:
    """The existing worker owns fallback generation and its normal rendition lifecycle."""
    from app.services import art_urls, renditions

    renditions.use_host_format(shutil.which("ffmpeg"))
    title_id, _, _ = _seed(tmp_path)
    with db_module.SessionLocal() as db:
        plan = renditions.scan(db)
    assert plan.missing_episode_stills == plan.pending_episode_stills == 1
    assert plan.order == [(title_id, "Primary")]

    worker = renditions.ArtworkRenditions()
    assert worker.run_once()
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, title_id)
        source = title.images["Primary"]["tag"]
        row = db.get(TitleArtwork, (title_id, "Primary"))
        assert row is not None and row.state == "ready" and row.source_key == art_urls.source_key(title_id, "Primary", source), row.error if row else None
        rendition = art_urls.rendition_file(row.source_key, 800, art_urls.extension())
        assert rendition.is_file() and rendition.stat().st_size > 0
        before = rendition.stat().st_mtime_ns
    assert not worker.run_once()
    assert rendition.stat().st_mtime_ns == before


@needs_ffmpeg
def test_manual_art_arriving_during_extraction_wins_the_publish_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    title_id, _, _ = _seed(tmp_path)

    def manual_edit(_ffmpeg, _source, destination, _artifact, _stop, _on_process):  # noqa: ANN001
        with db_module.SessionLocal() as db:
            title = db.get(MediaTitle, title_id)
            title.images = {"Primary": {"path": "show/manual.jpg", "tag": "manual"}}
            title.field_sources = {"images.Primary": "user"}
            db.commit()
        destination.write_bytes(b"\xff\xd8manual")
        return True

    monkeypatch.setattr(episode_stills, "_render", manual_edit)
    assert not episode_stills.ensure(title_id)
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, title_id)
        assert title.images["Primary"] == {"path": "show/manual.jpg", "tag": "manual"}
        assert title.field_sources["images.Primary"] == "user"


@needs_ffmpeg
def test_failed_frame_extraction_is_deferred_for_one_hour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.models import utcnow

    title_id, _, _ = _seed(tmp_path)
    monkeypatch.setattr(episode_stills, "media_tool", lambda *_args: None)
    assert not episode_stills.ensure(title_id)
    with db_module.SessionLocal() as db:
        row = db.get(TitleArtwork, (title_id, "Primary"))
        assert row is not None and row.state == "failed" and row.error == "frame_failed"
        assert episode_stills.scan(db) == []
        assert len(episode_stills.scan(db, include_deferred=True)) == 1
        row.updated_at = utcnow() - timedelta(hours=2)
        db.commit()
    assert not episode_stills.ensure(title_id)
    with db_module.SessionLocal() as db:
        assert episode_stills.scan(db) == []  # a repeated identical failure resets the retry window


@needs_ffmpeg
def test_worker_retries_an_aged_failed_frame_and_prepares_its_rendition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.models import utcnow
    from app.services import art_urls, renditions

    renditions.use_host_format(shutil.which("ffmpeg"))
    title_id, _, _ = _seed(tmp_path)
    with monkeypatch.context() as failed:
        failed.setattr(episode_stills, "media_tool", lambda *_args: None)
        assert renditions.ArtworkRenditions().run_once()
    with db_module.SessionLocal() as db:
        row = db.get(TitleArtwork, (title_id, "Primary"))
        assert row is not None and row.state == "failed"
        row.updated_at = utcnow() - timedelta(hours=2)
        db.commit()

    assert renditions.ArtworkRenditions().run_once()
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, title_id)
        row = db.get(TitleArtwork, (title_id, "Primary"))
        assert row is not None and row.state == "ready"
        assert row.source_key == art_urls.source_key(title_id, "Primary", title.images["Primary"]["tag"])


@needs_ffmpeg
def test_worker_retries_an_aged_failed_rendition_of_a_cached_still(tmp_path: Path) -> None:
    from datetime import timedelta

    from app.models import utcnow
    from app.services import art_urls, renditions

    renditions.use_host_format(shutil.which("ffmpeg"))
    title_id, _, _ = _seed(tmp_path)
    assert episode_stills.ensure(title_id)
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, title_id)
        source = title.images["Primary"]["tag"]
        db.add(TitleArtwork(title_id=title_id, image_type="Primary", source_key=art_urls.source_key(title_id, "Primary", source),
                            state="failed", error="ffmpeg_failed", updated_at=utcnow() - timedelta(hours=2)))
        db.commit()

    assert renditions.ArtworkRenditions().run_once()
    with db_module.SessionLocal() as db:
        assert db.get(TitleArtwork, (title_id, "Primary")).state == "ready"


@needs_ffmpeg
def test_worker_defers_an_unavailable_fallback_instead_of_spinning(tmp_path: Path) -> None:
    from app.services import renditions

    title_id, _, video = _seed(tmp_path)
    with db_module.SessionLocal() as db:
        key = episode_stills.scan(db)[0].source_key
    video.touch()
    worker = renditions.ArtworkRenditions()
    assert worker.run_once()
    assert key in worker._skipped
