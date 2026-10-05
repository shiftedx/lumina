"""Downloads and recordings publish into the rule-selected root by actual output."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Event

import pytest

from app import db as db_module
from app.config import settings
from app.db import Base
from app.events import EventBus
from app.models import DownloadJob, LibraryItem, MediaArtifact, StorageRoot, User
from app.schemas import FormatResolutionState, JobCreateRequest, OutputProfile
from app.services import job_manager as job_manager_module
from app.services import storage_routing
from app.services.format_resolution import AcquisitionResult
from app.services.job_manager import JobAdmissionError, JobManager
from app.services.library import LibraryService
from app.services.media_artifacts import MediaArtifactService
from app.services.storage_roots import StorageRootService
from app.services.storage_routing import STAGING_DIRNAME, StorageRoutingService, StorageRule, staging_dir
from app.services.yt_dlp_service import YtDlpService


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    parent = tmp_path / "mnt"
    parent.mkdir()
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as db:
        db.add(User(id="member", username="member", display_name="Member", role="viewer", is_active=True))
        db.add(YtDlpService(db)._default_app_settings(None))
        roots = {}
        for name in ("uhd", "media"):
            (parent / name).mkdir()
            roots[name] = StorageRootService(db).create(label=name, container_path=str(parent / name), mode="managed").id
        StorageRoutingService(db).replace(
            expected_revision=0,
            default_root_id=roots["media"],
            rules=[
                StorageRule(id="uhd", priority=10, media_kinds=["video"], min_height=2160, target_root_id=roots["uhd"], relative_template="Video UHD"),
                StorageRule(id="live", priority=30, sources=["twitch"], media_kinds=["recording"], target_root_id=roots["media"], relative_template="Live"),
            ],
        )
        db.commit()
    manager = JobManager(EventBus())
    downloads: dict[str, dict] = {}

    def fake_download(self, url, *, output_root, owner_user_id, **_kwargs):  # noqa: ANN001, ANN202
        spec = downloads[url]
        media = output_root / owner_user_id / f"{spec['title']}.mp4"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(spec["bytes"])
        (media.parent / f"{spec['title']}.en.vtt").write_text("WEBVTT")
        info = {"id": spec["id"], "title": spec["title"], "extractor": "youtube", "extractor_key": "Youtube",
                "ext": "mp4", "height": spec["height"], "vcodec": "avc1", "filepath": str(media)}
        return AcquisitionResult(info=info, format_resolution=FormatResolutionState(requested_selector="bestvideo+bestaudio/best"))

    monkeypatch.setattr(YtDlpService, "download", fake_download)

    def run(url: str, **spec) -> DownloadJob:
        downloads[url] = {"bytes": b"media-" + url.encode(), **spec}
        with db_module.SessionLocal() as db:
            job = manager.enqueue(db, JobCreateRequest(source_url=url, output_profile=OutputProfile(template="%(title)s.%(ext)s")), db.get(User, "member"))
        manager._run_job(job.id)
        with db_module.SessionLocal() as db:
            return db.get(DownloadJob, job.id)

    yield manager, run, roots, parent


def _files(root: Path) -> list[str]:
    """Visible media under a root, ignoring Lumina's own .lumina-* bookkeeping."""
    files = (p.relative_to(root) for p in root.rglob("*") if p.is_file())
    return sorted(str(p) for p in files if not any(part.startswith(".lumina") for part in p.parts))


def test_best_resolves_1080_routes_1080(env) -> None:  # noqa: ANN001
    _manager, run, roots, parent = env
    job = run("https://example.com/a", id="a", title="Clip", height=1080)  # requested best/4K
    assert job.status == "completed", job.error
    [applied] = job.routing["published"]
    assert applied.pop("library_item_id")
    assert applied == {"rule_id": None, "revision": 1, "root_id": roots["media"], "folder": "", "media_kind": "video", "actual_height": 1080, "root_label": "media"}
    assert _files(parent / "media") == ["member/Clip.en.vtt", "member/Clip.mp4"]
    assert _files(parent / "uhd") == []
    uhd = run("https://example.com/b", id="b", title="Big", height=2160)
    assert uhd.status == "completed"
    assert _files(parent / "uhd") == ["Video UHD/member/Big.en.vtt", "Video UHD/member/Big.mp4"]
    with db_module.SessionLocal() as db:
        item = db.query(LibraryItem).filter_by(remote_id="b").one()
        # test_download_destination_truth: the job DTO names the persisted routing decision, never a path.
        [output] = JobManager.serialize(uhd).outputs
        assert (output.library_item_id, output.root_label, output.folder) == (item.id, "uhd", "Video UHD")
        assert str(parent) not in JobManager.serialize(uhd).model_dump_json()
        assert LibraryService(db).resolve_media_path(item) == parent / "uhd" / "Video UHD/member/Big.mp4"
    # Staging never outlives the attempt.
    assert not [p for name in ("uhd", "media") for p in (parent / name / STAGING_DIRNAME).glob("*/*")]


def test_collision_preserves_both(env) -> None:  # noqa: ANN001
    _manager, run, _roots, parent = env
    assert run("https://example.com/1", id="one", title="Same", height=720).status == "completed"
    assert run("https://example.com/2", id="two", title="Same", height=720).status == "completed"
    assert (parent / "media/member/Same.mp4").read_bytes() == b"media-https://example.com/1"
    assert (parent / "media/member/Same (2).mp4").read_bytes() == b"media-https://example.com/2"
    with db_module.SessionLocal() as db:
        assert {a.relative_path for a in db.query(MediaArtifact)} == {"member/Same.mp4", "member/Same (2).mp4"}


def test_no_hardlink_filesystem_falls_back_to_copy(env, monkeypatch) -> None:  # noqa: ANN001
    """os.link raising EXDEV (no hard-link support) still publishes, via a fsynced copy + os.replace."""
    _manager, run, _roots, parent = env

    def no_hardlinks(*_a, **_k):  # noqa: ANN001, ANN202
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr(storage_routing.os, "link", no_hardlinks)
    first = run("https://example.com/nolink1", id="nolink1", title="NoLink", height=720)
    second = run("https://example.com/nolink2", id="nolink2", title="NoLink", height=720)
    assert (first.status, second.status) == ("completed", "completed")
    assert (parent / "media/member/NoLink.mp4").read_bytes() == b"media-https://example.com/nolink1"
    assert (parent / "media/member/NoLink (2).mp4").read_bytes() == b"media-https://example.com/nolink2"
    # Staging never outlives the attempt, even on the fallback path.
    assert not [p for p in (parent / "media" / STAGING_DIRNAME).glob("*/*")]


def test_hardlink_error_other_than_unsupported_propagates(env, monkeypatch) -> None:  # noqa: ANN001
    def denied(*_a, **_k):  # noqa: ANN001, ANN202
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(storage_routing.os, "link", denied)
    _manager, run, _roots, _parent = env
    job = run("https://example.com/denied", id="denied", title="Denied", height=720)
    assert job.status == "failed"


def test_offline_target_refuses_admission(env) -> None:  # noqa: ANN001
    manager, _run, roots, _parent = env
    with db_module.SessionLocal() as db:
        db.get(StorageRoot, roots["media"]).enabled = False
        db.commit()
        with pytest.raises(JobAdmissionError) as refused:
            manager.enqueue(db, JobCreateRequest(source_url="https://example.com/x"), db.get(User, "member"))
    assert (refused.value.reason, refused.value.status_code) == ("storage_unavailable", 503)


def test_cross_fs_interrupt_recovery(env, monkeypatch) -> None:  # noqa: ANN001
    manager, run, roots, parent = env
    monkeypatch.setattr(storage_routing, "_same_filesystem", lambda a, b: False)

    # 1. The Library commit fails after the copy + link: nothing is left published.
    original = LibraryService.upsert_from_info
    monkeypatch.setattr(LibraryService, "upsert_from_info", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    failed = run("https://example.com/c", id="c", title="Cross", height=1080)
    assert failed.status == "failed"
    assert _files(parent / "media") == []
    assert not list((parent / "media" / STAGING_DIRNAME).glob("*/*"))
    monkeypatch.setattr(LibraryService, "upsert_from_info", original)
    assert run("https://example.com/c", id="c", title="Cross", height=1080).status == "completed"
    assert (parent / "media/member/Cross.mp4").read_bytes() == b"media-https://example.com/c"

    # 2. A crash between the link and the commit: restart recovery removes only
    #    the unconfirmed hard link, never unrelated media at a journaled path.
    with db_module.SessionLocal() as db:
        media = db.get(StorageRoot, roots["media"])
        partial = staging_dir(media, "crashed") / "x.partial"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"bytes")
        os.link(partial, parent / "media/member/Orphan.mp4")
        (parent / "media/member/Keep.mp4").write_bytes(b"unrelated")
        db.add_all([
            DownloadJob(id="crashed", user_id="member", source_url="u", status="running", format_selection={}, output_profile={},
                        routing={"staging_root_id": roots["media"], "publishing": {"root_id": roots["media"], "path": "member/Orphan.mp4"}}),
            DownloadJob(id="stray", user_id="member", source_url="u", status="running", format_selection={}, output_profile={},
                        routing={"staging_root_id": roots["media"], "publishing": {"root_id": roots["media"], "path": "member/Keep.mp4"}}),
        ])
        db.commit()
    manager._reconcile_interrupted_jobs()
    assert _files(parent / "media") == ["member/Cross.en.vtt", "member/Cross.mp4", "member/Keep.mp4"]
    assert not partial.parent.exists()
    with db_module.SessionLocal() as db:
        assert db.get(DownloadJob, "crashed").status == "interrupted"
        assert db.query(MediaArtifact).count() == 1


def test_crash_after_commit_is_adopted(env, monkeypatch) -> None:  # noqa: ANN001
    manager, run, _roots, parent = env
    monkeypatch.setattr(job_manager_module, "_discard_staging", lambda db, job: None)
    job = run("https://example.com/d", id="d", title="Done", height=1080)
    with db_module.SessionLocal() as db:
        db.get(DownloadJob, job.id).status = "running"  # the process died before the final status write
        db.commit()
    monkeypatch.undo()
    manager._reconcile_interrupted_jobs()
    with db_module.SessionLocal() as db:
        assert db.get(DownloadJob, job.id).status == "completed"
    assert (parent / "media/member/Done.mp4").read_bytes() == b"media-https://example.com/d"
    assert not list((parent / "media" / STAGING_DIRNAME).glob("*/*"))


def test_recording_final_destination(env, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _manager, _run, roots, parent = env
    from app.services.live_recording_adapters import FfmpegLiveMediaSink
    from app.services.live_recording_manager import RecordingContext

    ctx = RecordingContext(recording_id="rec-1", user_id="member", source_url="https://twitch.tv/streamer",
                           source_identity="twitch:streamer", format_selection={}, output_profile={}, offset_base=None,
                           resume=False, session_factory=db_module.SessionLocal, _stop=Event(), _cancel=Event(), _either=Event())
    monkeypatch.setattr("app.services.live_recording.LiveRecordingService.note_media_published", lambda *a, **k: None)
    sink = FfmpegLiveMediaSink(ctx, session_factory=db_module.SessionLocal, events=None, temp_root=tmp_path / "tmp", ffmpeg_path=None)
    monkeypatch.setattr(sink, "_remux", lambda out: out.write_bytes(b"recorded") or True)
    sink.append_segment(b"seg", 0)
    item_id = sink.finalize()
    assert _files(parent / "media") == ["Live/member/live/live-rec-1.mp4"]
    assert not list((tmp_path / "tmp").rglob("*.mp4"))
    with db_module.SessionLocal() as db:
        artifact, root = MediaArtifactService(db).artifact_for(item_id)
    assert (root.id, artifact.relative_path, artifact.owner_user_id) == (roots["media"], "Live/member/live/live-rec-1.mp4", "member")
