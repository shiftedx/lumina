"""Library + acquisition-API integration for the Twitch VOD tracer.

The durable acquisition machinery (Download job, progress, cancellation, restart
recovery, idempotency, member-scoped dedup, atomic publication) is reused
unchanged from the existing acquisition slice; these tests cover what is specific
to acquiring a Twitch VOD as ordinary media: the acquisition API now admits the
tracer and still rejects Twitch live, and the resulting Library item preserves
safe provider identity, title, uploader, duration, artwork, provenance, and
chapters — with no replay-chat surface — and stays member-scoped.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main as main_module
from app.models import LibraryItem
from app.schemas import (
    AcquisitionBatchCreateRequest,
    JobCreateRequest,
    MediaSourceCapabilities,
    MediaSourceChatCapabilities,
    PreviewResponse,
)
from app.services.acquisition_batch import AcquisitionBatchService
from app.services.library import LibraryService
from app.services.yt_dlp_service import YtDlpService
from support import make_user, memory_session_factory

SEGMENTS = b"local-twitch-vod-media-bytes"


def _twitch_vod_sanitized_info(file_path: str) -> dict:
    """A sanitized Twitch VOD info dict as the worker hands it to the Library."""

    return {
        "id": "2170248437",
        "title": "Full stream — building the vault",
        "extractor": "twitch:vod",
        "extractor_key": "Twitch",
        "webpage_url": "https://www.twitch.tv/videos/2170248437",
        "was_live": True,
        "uploader": "fixturestreamer",
        "channel": "fixturestreamer",
        "duration": 7325,
        "thumbnail": "https://static-cdn.jtvnw.example/vod-thumb.jpg",
        "chapters": [
            {"start_time": 0, "end_time": 1800, "title": "Intro and setup"},
            {"start_time": 1800, "end_time": 7325, "title": "Pair programming"},
        ],
        "filepath": file_path,
        "ext": "mp4",
        "vcodec": "avc1.4d401f",
        "acodec": "mp4a.40.2",
        "height": 720,
    }


def test_twitch_vod_library_item_preserves_safe_metadata_and_chapters_and_offers_no_chat(tmp_path) -> None:
    media_file = tmp_path / "vod.mp4"
    media_file.write_bytes(SEGMENTS)
    session_factory = memory_session_factory()
    with session_factory() as db:
        owner = make_user("owner-1", username="owner")
        db.add(owner)
        db.commit()
        library = LibraryService(db)
        info = _twitch_vod_sanitized_info(str(media_file))

        item = library.upsert_from_info(info, owner_user_id=owner.id, visibility="private")
        db.commit()

        # Exactly one durable Library item, member-owned and private.
        assert db.query(LibraryItem).count() == 1
        assert item.user_id == owner.id
        assert item.visibility == "private"

        # Safe provider identity, title, uploader, duration, artwork, provenance.
        assert item.extractor == "Twitch"
        assert item.remote_id == "2170248437"
        assert item.title == "Full stream — building the vault"
        assert item.uploader == "fixturestreamer"
        assert item.duration == 7325
        assert item.thumbnail_url == "https://static-cdn.jtvnw.example/vod-thumb.jpg"
        assert item.webpage_url == "https://www.twitch.tv/videos/2170248437"

        serialized = library.serialize(item, owner, summary=False)
        # Chapters are preserved and surfaced on the item detail.
        assert [chapter.title for chapter in serialized.chapters] == ["Intro and setup", "Pair programming"]

        # There is no replay-chat surface anywhere on the acquired item.
        payload = serialized.model_dump()
        assert "chat" not in payload
        assert "replay" not in str(payload).lower()

        # A second acquisition of the same VOD by the same member does not fork a
        # second Library item (member-scoped dedup on the reused machinery).
        library.upsert_from_info(_twitch_vod_sanitized_info(str(media_file)), owner_user_id=owner.id, visibility="private")
        db.commit()
        assert db.query(LibraryItem).filter(LibraryItem.user_id == owner.id).count() == 1


def test_stored_metadata_redacts_request_headers_and_cookies(tmp_path) -> None:
    # Saved-sign-in request material (an Authorization header, a Cookie) must
    # never be persisted into Library metadata, where the owner could read it back
    # through the item detail. Redact it at the storage seam.
    media_file = tmp_path / "vod.mp4"
    media_file.write_bytes(SEGMENTS)
    secret = "OAuth saved-sign-in-token-must-not-persist"
    info = _twitch_vod_sanitized_info(str(media_file))
    info["http_headers"] = {"Authorization": secret, "User-Agent": "yt-dlp"}
    info["cookies"] = f"auth_token={secret}"
    info["formats"] = [{
        "format_id": "720p60",
        "protocol": "m3u8_native",
        "url": "https://vod.twitch.example/master.m3u8",
        "http_headers": {"Authorization": secret, "User-Agent": "yt-dlp"},
        "cookies": f"auth_token={secret}",
    }]

    session_factory = memory_session_factory()
    with session_factory() as db:
        owner = make_user("owner-1", username="owner")
        db.add(owner)
        db.commit()
        library = LibraryService(db)
        item = library.upsert_from_info(info, owner_user_id=owner.id, visibility="private")
        db.commit()

        assert "http_headers" not in item.metadata_json
        assert "cookies" not in item.metadata_json
        for fmt in item.metadata_json.get("formats", []):
            assert "http_headers" not in fmt
            assert "cookies" not in fmt
        assert secret not in str(item.metadata_json)
        assert secret not in str(library.serialize(item, owner, summary=False).model_dump())


def test_stored_metadata_redaction_recurses_into_nested_format_lists(tmp_path) -> None:
    # A merged video+audio HLS acquisition retains nested
    # requested_downloads[].requested_formats[] with per-sub-format headers, so
    # redaction must strip http_headers/cookies from every dict at any depth.
    media_file = tmp_path / "vod.mp4"
    media_file.write_bytes(SEGMENTS)
    secret = "OAuth nested-saved-sign-in-token"
    info = _twitch_vod_sanitized_info(str(media_file))
    info["requested_downloads"] = [{
        "format_id": "720p60+audio",
        "http_headers": {"Authorization": secret},
        "requested_formats": [
            {"format_id": "720p60", "url": "https://vod.twitch.example/v.m3u8",
             "http_headers": {"Authorization": secret}, "cookies": f"auth={secret}"},
            {"format_id": "audio", "url": "https://vod.twitch.example/a.m3u8",
             "http_headers": {"Authorization": secret}},
        ],
    }]

    session_factory = memory_session_factory()
    with session_factory() as db:
        owner = make_user("owner-1", username="owner")
        db.add(owner)
        db.commit()
        library = LibraryService(db)
        item = library.upsert_from_info(info, owner_user_id=owner.id, visibility="private")
        db.commit()

        for download in item.metadata_json.get("requested_downloads", []):
            assert "http_headers" not in download
            for sub in download.get("requested_formats", []):
                assert "http_headers" not in sub
                assert "cookies" not in sub
        assert secret not in str(item.metadata_json)
        assert secret not in str(library.serialize(item, owner, summary=False).model_dump())


def test_twitch_vod_acquisition_is_member_scoped_across_households(tmp_path) -> None:
    media_file = tmp_path / "vod.mp4"
    media_file.write_bytes(SEGMENTS)
    session_factory = memory_session_factory()
    with session_factory() as db:
        one = make_user("member-a", username="a", display_name="A")
        two = make_user("member-b", username="b", display_name="B")
        db.add_all([one, two])
        db.commit()
        library = LibraryService(db)

        library.upsert_from_info(_twitch_vod_sanitized_info(str(media_file)), owner_user_id=one.id, visibility="private")
        library.upsert_from_info(_twitch_vod_sanitized_info(str(media_file)), owner_user_id=two.id, visibility="private")
        db.commit()

        # Each member gets their own private Library item for the same Twitch VOD.
        assert db.query(LibraryItem).filter(LibraryItem.user_id == one.id).count() == 1
        assert db.query(LibraryItem).filter(LibraryItem.user_id == two.id).count() == 1
        b_item = db.query(LibraryItem).filter(LibraryItem.user_id == two.id).one()
        assert LibraryService(db).get_item(b_item.id, one) is None


def _make_user_session():
    session_factory = memory_session_factory()
    session = session_factory()
    user = make_user("user-1", username="viewer", display_name="Viewer")
    session.add(user)
    session.commit()
    return session, user


def _twitch_preview(lifecycle: str, *, can_acquire: bool, acquire_reason: str | None) -> PreviewResponse:
    return PreviewResponse(
        kind="video",
        title="Twitch VOD",
        webpage_url="https://www.twitch.tv/videos/2170248437",
        extractor_key="Twitch",
        capabilities=MediaSourceCapabilities(
            provider="twitch",
            lifecycle=lifecycle,
            can_play=True,
            can_acquire=can_acquire,
            acquire_reason=acquire_reason,
            chat=MediaSourceChatCapabilities(),
        ),
        raw={"extractor_key": "Twitch", "id": "2170248437"},
    )


def test_acquisition_api_admits_the_twitch_vod_tracer(monkeypatch) -> None:
    session, user = _make_user_session()
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "jobs", SimpleNamespace(acquisition=object()))
    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: _twitch_preview(
            "completed_live", can_acquire=True, acquire_reason=None
        ),
    )

    captured: dict = {}

    def capture(self, **options):  # noqa: ANN001, ANN003
        captured.update(options)
        raise _Stop

    class _Stop(Exception):
        pass

    monkeypatch.setattr(AcquisitionBatchService, "queue_selected", capture)

    batch = AcquisitionBatchCreateRequest.model_validate({
        "source_url": "https://www.twitch.tv/videos/2170248437",
        "format_selection": {"preset": "best"},
        "output_profile": {},
        "entries": [{"source_url": "https://www.twitch.tv/videos/2170248437", "remote_id": "2170248437", "title": "VOD"}],
    })
    with pytest.raises(_Stop):
        main_module.create_acquisition_batch(batch, object(), user, session)
    # The tracer reached dispatch: its entry was accepted, not gated out.
    assert [entry.source_url for entry in captured["entries"]] == ["https://www.twitch.tv/videos/2170248437"]


def test_acquisition_api_still_rejects_twitch_live(monkeypatch) -> None:
    session, user = _make_user_session()
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        YtDlpService,
        "preview",
        lambda self, source_url, lazy_playlist=True, format_selection=None: _twitch_preview(
            "live", can_acquire=False, acquire_reason="live_acquisition_not_supported"
        ),
    )

    with pytest.raises(HTTPException) as job_error:
        main_module.create_job(JobCreateRequest(source_url="https://www.twitch.tv/videos/live"), object(), user, session)
    assert job_error.value.status_code == 409
    assert job_error.value.detail["reason"] == "live_acquisition_not_supported"
