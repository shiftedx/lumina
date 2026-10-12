"""S72 pre-gate security regressions (network policy, NFO, webhook privacy, deactivation)."""

from __future__ import annotations

import io

import pytest
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.downloader.http import HttpFD

from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicy, PublicSourcePolicyError
from tests.test_v1_artifacts import household  # noqa: F401  (fixture)
from tests.test_v1_sessions import factory  # noqa: F401  (fixture)

HOSTILE_MANIFEST = (
    b"#EXTM3U\n"
    b'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="http://127.0.0.1:2375/containers/json"\n'
    b"#EXTINF:5,\nhttp://169.254.169.254/latest/meta-data/iam/\n#EXT-X-ENDLIST\n"
)


@pytest.fixture
def ffmpeg_calls(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(FFmpegFD, "real_download", lambda self, fn, info: calls.append(info["url"]) or True)
    monkeypatch.setattr(FFmpegFD, "available", classmethod(lambda cls, path=None: True))
    return calls


def _policy_ydl(tmp_path, **extra) -> PolicyYoutubeDL:
    options = {
        "ignoreconfig": True, "quiet": True, "proxy": "", "external_downloader": {"default": "native"},
        "hls_prefer_native": True, "skip_unavailable_fragments": False, "restrictfilenames": True,
        "outtmpl": {"default": str(tmp_path / "%(id)s.%(ext)s")}, **extra,
    }
    ydl = PolicyYoutubeDL(options, policy=PublicSourcePolicy(resolver=lambda h, p, *a: [(2, 1, 6, "", ("93.184.216.34", p))]))

    class Response(io.BytesIO):
        url = "https://pub.example/sub.m3u8"

    ydl.urlopen = lambda request: Response(HOSTILE_MANIFEST)
    return ydl


def test_hls_subtitle_track_never_reaches_ffmpeg(tmp_path, monkeypatch, ffmpeg_calls) -> None:
    http_calls: list[str] = []

    def fake_http(self, filename, info):  # noqa: ANN001
        open(filename, "wb").write(b"x")
        http_calls.append(info["url"])
        return True

    monkeypatch.setattr(HttpFD, "real_download", fake_http)
    ydl = _policy_ydl(tmp_path, writesubtitles=True, format="best")
    info = {
        "id": "page", "title": "t", "extractor": "generic", "extractor_key": "Generic",
        "webpage_url": "https://pub.example/page",
        "formats": [{"url": "https://pub.example/v.mp4", "ext": "mp4", "format_id": "0"}],
        "subtitles": {"en": [{"url": "https://pub.example/sub.m3u8"}]},
    }
    # The unguardable (m3u8) subtitle track is dropped; the media itself still downloads.
    ydl.process_ie_result(info, download=True)
    assert ffmpeg_calls == []
    assert http_calls == ["https://pub.example/v.mp4"]

    # The replay-chat path (skip_download: no process_info gate) still routes the
    # subtitle fetch through the guarded chokepoint, which refuses it.
    with pytest.raises(PublicSourcePolicyError):
        ydl.dl(str(tmp_path / "x.vtt"), {"url": "https://pub.example/sub.m3u8", "ext": "vtt", "http_headers": {}}, subtitle=True)
    assert ffmpeg_calls == []


def test_gate_validates_requested_subtitles(tmp_path) -> None:
    ydl = _policy_ydl(tmp_path)
    base = {"extractor": "generic", "extractor_key": "Generic", "url": "https://pub.example/v.mp4", "protocol": "https"}
    info = {**base, "requested_subtitles": {
        "hls": {"url": "https://pub.example/s.m3u8", "ext": "vtt"},
        "private": {"url": "http://127.0.0.1/s.vtt", "ext": "vtt"},
        "en": {"url": "https://pub.example/s.vtt", "ext": "vtt"},
    }}
    ydl.validate_download_transport(info)
    assert list(info["requested_subtitles"]) == ["en"]


def test_live_hls_is_never_handed_to_ffmpeg(tmp_path, ffmpeg_calls) -> None:
    ydl = _policy_ydl(tmp_path)
    live = {
        "id": "x", "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/x.m3u8", "protocol": "m3u8_native",
        "is_live": True, "live_status": "is_live", "extractor": "youtube", "extractor_key": "Youtube", "ext": "mp4",
    }
    with pytest.raises(PublicSourcePolicyError):
        ydl.dl(str(tmp_path / "x.mp4"), dict(live, http_headers={}))
    # Non-HLS downloaders that would fetch outside the guard are refused too.
    with pytest.raises(PublicSourcePolicyError):
        ydl.dl(str(tmp_path / "y.mp4"), {"url": "rtmp://pub.example/live", "protocol": "rtmp", "http_headers": {}})
    assert ffmpeg_calls == []


def test_household_webhook_never_carries_member_private_details(monkeypatch) -> None:
    from app.models import LibraryItem, SourceAutomation
    from app.services.webhooks import WebhookService

    sent: list[dict] = []
    monkeypatch.setattr(WebhookService, "_deliver_quietly", lambda self, event, **kw: sent.append({"event": event, **kw}))
    service = WebhookService(None)  # type: ignore[arg-type]
    private = LibraryItem(title="member-private-title", visibility="private", webpage_url="https://example.com/private-watch")
    service.notify_new_video(private)
    assert sent == []
    service.notify_new_video(LibraryItem(title="shared-title", visibility="shared", webpage_url="https://example.com/w"))
    assert sent[-1]["message"] == "shared-title"

    service.notify_job_failed({"title": "secret-job", "source_url": "https://example.com/s?token=abc", "error": "/data/x.part"})
    service.notify_automation_error(SourceAutomation(label="secret-label", source_url="https://example.com/c"), "boom /data")
    for payload in sent[1:]:
        text = repr(payload)
        assert payload["details"] == {}
        assert not any(leak in text for leak in ("secret", "example.com", "/data", "boom"))


def test_inactive_owner_automations_never_run(monkeypatch) -> None:
    from app.schemas import SourceAutomationCreateRequest
    from app.services.source_automation import SourceAutomationService
    from app.services.yt_dlp_service import YtDlpService
    from tests.test_user_settings_and_source_automation import FakeJobs, fake_playlist_preview, make_session, make_user

    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    jobs = FakeJobs()
    service = SourceAutomationService(session, jobs)  # type: ignore[arg-type]
    request = SourceAutomationCreateRequest(label="ch", source_url="https://example.com/ch", source_type="channel", auto_download=True)
    automation = service.create_automation(request, user)
    automation.next_check_at = None
    user.is_active = False
    session.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda _s, url, *a, **k: fake_playlist_preview(url))
    assert service.due_ids(automation.created_at) == []
    assert service.process_due() == [] and jobs.enqueued == []
    assert service.process_one(automation.id, automation.created_at) is None and jobs.enqueued == []


def test_automation_count_and_cadence_are_bounded(monkeypatch) -> None:
    from app.schemas import SourceAutomationCreateRequest
    from app.services import source_automation
    from app.services.source_automation import SourceAutomationService
    from tests.test_user_settings_and_source_automation import FakeJobs, make_session, make_user

    for too_often in ("* * * * *", "*/5 * * * *", "0,10 * * * *", "0-59/14 * * * *", "0,10 3 * * *"):
        with pytest.raises(ValueError, match="at most every"):
            SourceAutomationService._validate_cron(too_often)
    # "0,50 3 * * *" only fires twice a day (3:00 and 3:50, 50 minutes apart);
    # the old check judged the minute field alone as if every hour fired,
    # which looked like a 10-minute gap and wrongly refused it.
    for fine in ("*/15 * * * *", "0 * * * *", "17 */6 * * *", "0,30 * * * *", "0,50 3 * * *"):
        SourceAutomationService._validate_cron(fine)

    monkeypatch.setattr(source_automation, "MAX_AUTOMATIONS_PER_USER", 1)
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = SourceAutomationService(session, FakeJobs())  # type: ignore[arg-type]
    request = SourceAutomationCreateRequest(label="ch", source_url="https://example.com/ch", source_type="channel")
    service.create_automation(request, user)
    with pytest.raises(ValueError, match="at most 1"):
        service.create_automation(request, user)


def test_inactive_owner_queued_job_never_starts(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.events import EventBus
    from app.models import DownloadJob, User
    from app.services import job_manager as job_manager_module
    from app.services.job_manager import JobManager
    from app.services.yt_dlp_service import YtDlpService

    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite3'}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(job_manager_module, "SessionLocal", factory)
    with factory.begin() as db:
        db.add(User(id="m", username="m", display_name="M", role="viewer", is_active=False))
        db.add(DownloadJob(id="j", user_id="m", source_url="https://example.com/v", status="queued", format_selection={}, output_profile={}))
    downloads: list[str] = []
    monkeypatch.setattr(YtDlpService, "download", lambda self, url, **kw: downloads.append(url))
    JobManager(EventBus())._run_job("j")
    with factory() as db:
        assert db.get(DownloadJob, "j").status == "cancelled"
    assert downloads == []
    engine.dispose()


def test_cancel_terminates_only_that_jobs_ytdlp_children(tmp_path, monkeypatch) -> None:
    import sys
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from yt_dlp.utils import Popen as YtDlpPopen

    from app.db import Base
    from app.events import EventBus
    from app.models import DownloadJob, User
    from app.services import job_manager as job_manager_module
    from app.services.job_manager import JobManager
    from app.services.yt_dlp_service import YtDlpService

    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite3'}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(job_manager_module, "SessionLocal", factory)
    with factory.begin() as db:
        db.add(User(id="m", username="m", display_name="M", role="viewer", is_active=True))
        db.add(DownloadJob(id="j", user_id="m", source_url="https://example.com/v", status="queued", format_selection={}, output_profile={}))
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    bystander = YtDlpPopen(sleeper)  # spawned outside any job: never touched
    entered, children = threading.Event(), []

    def fake_download(self, url, **kwargs):  # noqa: ANN001
        child = YtDlpPopen(sleeper)  # like an FFmpeg merge: no hook fires while it runs
        children.append(child)
        entered.set()
        child.communicate()
        raise RuntimeError("merge died")

    monkeypatch.setattr(YtDlpService, "download", fake_download)
    manager = JobManager(EventBus())
    worker = threading.Thread(target=manager._run_job, args=("j",))
    worker.start()
    try:
        assert entered.wait(5)
        with factory() as db:
            manager.cancel(db, "j", db.get(User, "m"))
        worker.join(10)
        assert not worker.is_alive() and children[0].poll() is not None
        assert bystander.poll() is None
        with factory() as db:
            assert db.get(DownloadJob, "j").status == "cancelled"
    finally:
        bystander.kill()
        for child in children:
            child.kill()
        engine.dispose()


def test_deactivation_stops_live_recordings(factory, monkeypatch) -> None:  # noqa: ANN001, F811
    import uuid

    from fastapi.testclient import TestClient

    from app import main
    from app.config import settings
    from app.models import LiveRecording, User
    from app.security import CSRF_HEADER, hash_password
    from tests.test_v1_sessions import PASSWORD, login

    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(main.live_recording_manager, "stop", lambda user_id, recording_id, **_: stopped.append((user_id, recording_id)))
    member = str(uuid.uuid4())
    with factory.begin() as db:
        db.add(User(id=member, username="member", display_name="M", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
        for rid, status in (("live-1", "live"), ("done-1", "completed")):
            db.add(LiveRecording(id=rid, user_id=member, source_url="https://www.youtube.com/watch?v=x",
                                 source_identity=f"youtube:{rid}", source_identity_key=rid, status=status))
    client = TestClient(main.app, base_url="http://localhost")
    csrf = login(client)
    response = client.put(f"/api/admin/users/{member}", json={"is_active": False},
                          headers={"Origin": settings.allowed_origins_list[0], CSRF_HEADER: csrf})
    assert response.status_code == 200, response.text
    assert stopped == [(member, "live-1")]


def test_playback_session_files_recheck_access(household, tmp_path) -> None:  # noqa: ANN001, F811
    import shutil

    from app import db as db_module
    from app.config import settings
    from app.models import LibraryItem
    from app.services import local_playback_sessions as lps
    from tests.test_v1_artifacts import _client, _download
    from tests.test_v1_probe import make_media

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    item_id = _download("alice", make_media(settings.library_root / "alice" / "clip.mkv"))
    with db_module.SessionLocal() as db:
        db.get(LibraryItem, item_id).visibility = "shared"
        db.commit()
    try:
        with _client("bob") as bob:
            body = bob.post(f"/api/library/{item_id}/playback-sessions").json()
            assert lps.sessions._sessions[body["session_id"]].process.wait(timeout=60) == 0
            init = f"/api/playback-sessions/{body['session_id']}/init.mp4"
            assert bob.get(init).status_code == 200
            with db_module.SessionLocal() as db:
                db.get(LibraryItem, item_id).visibility = "private"
                db.commit()
            assert bob.get(init).status_code == 404  # not only the manifest re-checks
    finally:
        lps.sessions.close_all()


def test_local_media_reads_refuse_playlists(tmp_path) -> None:
    import shutil

    from app.services.local_asr import ffmpeg_command as asr_command
    from app.services.local_playback_sessions import ffmpeg_command as remux_command
    from app.services.media_probe import LOCAL_INPUT_ARGS, ProbeError, run_ffprobe
    from app.services.playback_decision import Decision
    from app.services.storage_routing import publish_file

    secret = tmp_path / "secret.ts"
    secret.write_bytes(b"\x47" * 188)
    playlist = tmp_path / "evil.m3u8"
    playlist.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\n{secret}\n#EXT-X-ENDLIST\n")
    for command in (remux_command("ffmpeg", playlist, {}, tmp_path, Decision("remux", video="copy", audio="copy", video_index=0, audio_index=1)), asr_command("ffmpeg", playlist, tmp_path)):
        assert command[command.index("-i") - len(LOCAL_INPUT_ARGS):command.index("-i")] == LOCAL_INPUT_ARGS
    with pytest.raises(ValueError, match="playlist"):
        publish_file(None, playlist, source="generic", media_kind="video", height=None, relative="x", key="k", record=lambda *a: None)  # type: ignore[arg-type]
    if shutil.which("ffprobe"):
        with pytest.raises(ProbeError):
            run_ffprobe("ffprobe", playlist)


def test_nfo_dtd_rejected_in_any_encoding(tmp_path) -> None:
    from app.services.local_metadata import parse_nfo

    dtd = '<!DOCTYPE m [<!ENTITY a "AAAAAAAAAA"><!ENTITY b "&a;&a;&a;&a;&a;">]><movie><title>&b;</title></movie>'
    nfo = tmp_path / "movie.nfo"
    for encoding in ("utf-16", "utf-16-le", "utf-8"):
        nfo.write_bytes(f'<?xml version="1.0" encoding="{encoding.upper()}"?>{dtd}'.encode(encoding))
        assert parse_nfo(nfo) is None, encoding
    nfo.write_bytes('<?xml version="1.0" encoding="UTF-16"?><movie><title>Fine</title></movie>'.encode("utf-16"))
    assert parse_nfo(nfo) == ("movie", {"title": "Fine"})
