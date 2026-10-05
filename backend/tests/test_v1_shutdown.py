"""S18/F10: shutdown joins real blocking work; restart settles, never re-runs, stranded attempts."""

import asyncio
import sys
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.events import EventBus
from app.models import DownloadJob, LibraryItem, User
from app.services import job_manager as job_manager_module
from app.services.job_manager import JobManager
from app.services.media_artifacts import MediaArtifactService
from app.services.yt_dlp_service import YtDlpService
from yt_dlp.utils import Popen as YtDlpPopen


@pytest.fixture
def factory(tmp_path, monkeypatch):  # noqa: ANN001
    engine = create_engine(
        f"sqlite:///{tmp_path / 'shutdown.sqlite3'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(job_manager_module, "SessionLocal", session_factory)
    with session_factory.begin() as db:
        db.add(User(id="member-1", username="member", display_name="Member", role="viewer", is_active=True))
    yield session_factory
    engine.dispose()


def _add_job(factory, job_id: str, status: str, remote_id: str | None = None) -> None:  # noqa: ANN001
    with factory.begin() as db:
        db.add(
            DownloadJob(
                id=job_id, user_id="member-1", source_url=f"https://example.com/{job_id}", status=status,
                format_selection={}, output_profile={}, preview_snapshot={"id": remote_id} if remote_id else None,
            )
        )


def _status(factory, job_id: str) -> str:  # noqa: ANN001
    with factory() as db:
        return db.get(DownloadJob, job_id).status


async def _wait(event: Event) -> None:
    assert await asyncio.to_thread(event.wait, 5)


def test_stop_joins_real_blocking_fixture(factory, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    entered, exited = Event(), Event()
    target = tmp_path / "partial.bin"

    def fake_download(self, url, *, progress_hooks, **kwargs):  # noqa: ANN001
        try:
            with target.open("wb") as handle:
                entered.set()
                for written in range(1, 10_000):
                    handle.write(b"x")
                    handle.flush()
                    progress_hooks[0]({"status": "downloading", "downloaded_bytes": written, "total_bytes": 10_000})
                    Event().wait(0.01)
        finally:
            exited.set()

    monkeypatch.setattr(YtDlpService, "download", fake_download)
    _add_job(factory, "job-1", "queued")

    async def runner() -> bool:
        manager = JobManager(EventBus())
        await manager.start()
        await _wait(entered)
        clean = await manager.stop(grace_seconds=4)
        # stop() must not report completion while the blocking writer still runs.
        assert exited.is_set()
        return clean

    assert asyncio.run(runner()) is True
    assert _status(factory, "job-1") == "interrupted"


def test_stop_terminates_ytdlp_children_that_ignore_cancel_flags(factory, monkeypatch) -> None:  # noqa: ANN001
    entered = Event()
    children: list = []

    def fake_download(self, url, **kwargs):  # noqa: ANN001
        # Like an FFmpeg merge: blocks in a child with no progress hooks firing.
        child = YtDlpPopen([sys.executable, "-c", "import time; time.sleep(60)"])
        children.append(child)
        entered.set()
        child.communicate()
        raise RuntimeError(f"child exited {child.returncode}")

    monkeypatch.setattr(YtDlpService, "download", fake_download)
    _add_job(factory, "job-1", "queued")

    async def runner() -> bool:
        manager = JobManager(EventBus())
        await manager.start()
        await _wait(entered)
        return await manager.stop(grace_seconds=4)

    assert asyncio.run(runner()) is True
    assert children[0].poll() is not None
    assert _status(factory, "job-1") == "interrupted"


def test_restart_published_not_redownloaded(factory) -> None:  # noqa: ANN001
    library = Path(settings.library_root)
    library.mkdir(parents=True, exist_ok=True)
    media = library / "published.mp4"
    media.write_bytes(b"media")
    _add_job(factory, "published", "postprocessing", remote_id="abc")
    _add_job(factory, "partial", "running", remote_id="missing")
    _add_job(factory, "waiting", "queued")
    with factory.begin() as db:
        db.add(LibraryItem(id="item-1", user_id="member-1", remote_id="abc", title="Published", file_path=str(media)))
        db.flush()
        MediaArtifactService(db).register_file(db.get(LibraryItem, "item-1"), str(media))
    runs: list[str] = []

    async def start_twice() -> None:
        for _ in range(2):  # restart outcomes are stable across repeated starts
            manager = JobManager(EventBus())
            manager._run_job = runs.append  # type: ignore[method-assign]
            await manager.start()
            await asyncio.wait_for(manager._queue.join(), timeout=2)
            await manager.stop(grace_seconds=1)

    asyncio.run(start_twice())
    assert _status(factory, "published") == "completed"
    assert _status(factory, "partial") == "interrupted"
    # Only genuinely queued work is dispatched; stranded attempts never re-run.
    assert runs == ["waiting", "waiting"]


# test_cancel_recording_finalizes_partial: recording finalization is owned by
# live_recording_manager and covered by test_live_recording_edge_follow
# (deliberate stop yields a usable partial; abrupt cancel discards).
