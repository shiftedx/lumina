import warnings

from app.db import Base
from app.models import LibraryItem, User
from app.schemas import DescriptionTimestampResponse, NormalizedChapterResponse
from app.security import hash_password
from app.services.library import LibraryService
from support import make_user as _make_user, memory_session_factory


def make_session():
    return memory_session_factory()()


def make_user() -> User:
    return _make_user("user-1", username="tester", display_name="Tester", password_hash=hash_password("secret"))


def make_user_two() -> User:
    return _make_user("user-2", username="friend", display_name="Friend", password_hash=hash_password("secret"))


def test_upsert_from_info_creates_and_updates_item() -> None:
    session = make_session()
    service = LibraryService(session)
    created = service.upsert_from_info(
        {
            "id": "abc123",
            "extractor_key": "YouTube",
            "title": "Initial title",
            "uploader": "Channel",
            "filepath": "/tmp/video.mp4",
            "thumbnail": "https://example.com/thumb.jpg",
        }
    )
    session.commit()
    updated = service.upsert_from_info(
        {
            "id": "abc123",
            "extractor_key": "YouTube",
            "title": "Updated title",
            "uploader": "Channel",
            "filepath": "/tmp/video.mp4",
            "thumbnail": "https://example.com/thumb.jpg",
        }
    )
    session.commit()
    assert created.id == updated.id
    assert updated.title == "Updated title"


def test_upsert_from_info_creates_distinct_audio_and_video_variants() -> None:
    session = make_session()
    service = LibraryService(session)

    video = service.upsert_from_info(
        {
            "id": "track123",
            "extractor_key": "YouTube",
            "title": "Track title",
            "uploader": "Artist",
            "filepath": "/tmp/track.mp4",
            "height": 1080,
            "fps": 24,
            "ext": "mp4",
            "vcodec": "av01.0.08M.08",
        }
    )
    session.commit()

    audio = service.upsert_from_info(
        {
            "id": "track123",
            "extractor_key": "YouTube",
            "title": "Track title",
            "uploader": "Artist",
            "requested_downloads": [
                {
                    "filepath": "/tmp/track.mp3",
                    "ext": "mp3",
                    "vcodec": "none",
                    "acodec": "opus",
                }
            ],
            "ext": "webm",
            "vcodec": "none",
            "acodec": "opus",
        }
    )
    session.commit()

    assert video.id != audio.id
    assert video.file_path == "/tmp/track.mp4"
    assert audio.file_path == "/tmp/track.mp3"
    assert video.metadata_json["lumina_media_kind"] == "video"
    assert audio.metadata_json["lumina_media_kind"] == "audio"
    assert video.metadata_json["lumina_variant_key"].startswith("video:")
    assert audio.metadata_json["lumina_variant_key"].startswith("audio:")


def test_upsert_from_info_prefers_postprocessed_audio_filepath() -> None:
    session = make_session()
    service = LibraryService(session)

    audio = service.upsert_from_info(
        {
            "id": "track456",
            "extractor_key": "YouTube",
            "title": "Audio title",
            "requested_downloads": [
                {
                    "filepath": "/tmp/audio-title.mp3",
                    "filename": "/tmp/audio-title.webm",
                    "ext": "mp3",
                    "vcodec": "none",
                    "acodec": "opus",
                }
            ],
            "ext": "webm",
            "vcodec": "none",
            "acodec": "opus",
        }
    )
    session.commit()

    assert audio.file_path == "/tmp/audio-title.mp3"
    assert audio.metadata_json["filepath"] == "/tmp/audio-title.mp3"
    assert audio.metadata_json["ext"] == "mp3"


def test_upsert_from_info_normalizes_youtube_thumbnail_choice() -> None:
    session = make_session()
    service = LibraryService(session)

    created = service.upsert_from_info(
        {
            "id": "thumb123",
            "extractor_key": "YouTube",
            "title": "Thumbnail title",
            "filepath": "/tmp/video.mp4",
            "thumbnail": "https://i.ytimg.com/vi/thumb123/maxresdefault.jpg",
            "thumbnails": [
                {"url": "https://i.ytimg.com/vi/thumb123/hqdefault.jpg", "width": 480, "height": 360},
                {"url": "https://i.ytimg.com/vi/thumb123/sddefault.jpg", "width": 640, "height": 480},
                {"url": "https://i.ytimg.com/vi/thumb123/maxresdefault.jpg", "width": 1920, "height": 1080},
            ],
        }
    )
    session.commit()

    assert created.thumbnail_url == "https://i.ytimg.com/vi/thumb123/sddefault.jpg"
    assert created.metadata_json["thumbnail"] == "https://i.ytimg.com/vi/thumb123/sddefault.jpg"


def test_serialize_summary_trims_large_metadata_payloads() -> None:
    session = make_session()
    item = LibraryItem(
        id="summary-1",
        extractor="YouTube",
        remote_id="abc123",
        title="Summary candidate",
        uploader="Creator",
        metadata_json={
            "id": "abc123",
            "categories": ["Music"],
            "thumbnail": "https://example.com/thumb.jpg",
            "formats": [{}, {}, {}],
            "subtitles": {"en": [{}], "es": [{}]},
            "description": "A very large description that should not be sent in summary payloads.",
        },
        status="available",
    )
    session.add(item)
    session.commit()

    serialized = LibraryService(session).serialize(item, summary=True)

    assert serialized.metadata_json["categories"] == ["Music"]
    assert serialized.metadata_json["formats_count"] == 3
    assert serialized.metadata_json["subtitles_count"] == 2
    assert "description" not in serialized.metadata_json
    assert "formats" not in serialized.metadata_json
    assert "subtitles" not in serialized.metadata_json
    assert serialized.chapters == []
    assert serialized.description_timestamps == []


def test_serialize_chapters_and_timestamps_are_typed_and_warning_free() -> None:
    """Regression for #141: library item detail must not carry the plain dicts
    normalize_chapters() returns into fields typed as NormalizedChapterResponse /
    DescriptionTimestampResponse (a pydantic-core UserWarning on every serialize)."""
    session = make_session()
    item = LibraryItem(
        id="chaptered-1",
        extractor="YouTube",
        remote_id="abc123",
        title="Chaptered video",
        uploader="Creator",
        duration=90,
        metadata_json={
            "id": "abc123",
            "chapters": [{"start_time": 0, "end_time": 30, "title": "Intro"}],
            "description": "0:00 Intro\n0:30 Main\n1:00 Outro",
        },
        status="available",
    )
    session.add(item)
    session.commit()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        serialized = LibraryService(session).serialize(item)
        serialized.model_dump_json()

    assert not [w for w in caught if issubclass(w.category, UserWarning)], [str(w.message) for w in caught]
    assert serialized.chapters and all(isinstance(c, NormalizedChapterResponse) for c in serialized.chapters)
    assert serialized.description_timestamps and all(
        isinstance(t, DescriptionTimestampResponse) for t in serialized.description_timestamps
    )


def test_upsert_from_info_rounds_fractional_duration() -> None:
    session = make_session()
    service = LibraryService(session)

    item = service.upsert_from_info(
        {
            "id": "soundcloud-1",
            "extractor_key": "Soundcloud",
            "title": "Fractional duration track",
            "duration": 240.372,
            "filepath": "/tmp/track.mp3",
        }
    )
    session.commit()

    assert item.duration == 240


def test_serialize_summary_accepts_fractional_duration_from_existing_row() -> None:
    session = make_session()
    item = LibraryItem(
        id="legacy-float-duration",
        extractor="Soundcloud",
        remote_id="legacy1",
        title="Legacy item",
        duration=240.372,
        metadata_json={"duration": 240.372},
        status="available",
    )
    session.add(item)
    session.commit()

    serialized = LibraryService(session).serialize(item, summary=True)

    assert serialized.duration == 240


def test_search_items_matches_private_tags() -> None:
    session = make_session()
    user = make_user()
    session.add(user)
    session.commit()
    service = LibraryService(session)
    item = service.upsert_from_info(
        {
            "id": "tag123",
            "extractor_key": "YouTube",
            "title": "Unrelated title",
            "uploader": "Channel",
            "filepath": "/tmp/video.mp4",
        },
        owner_user_id=user.id,
    )
    service.add_tag(item.id, "favorite", user)
    session.commit()

    results, _ = service.search_page("favorite", user, offset=0, limit=10)

    assert len(results) == 1
    assert results[0].id == item.id


def test_list_items_hides_private_videos_from_other_users() -> None:
    session = make_session()
    owner = make_user()
    friend = make_user_two()
    session.add_all([owner, friend])
    session.commit()
    service = LibraryService(session)

    private_item = service.upsert_from_info(
        {
            "id": "private-1",
            "extractor_key": "YouTube",
            "title": "Private title",
            "filepath": "/tmp/private.mp4",
        },
        owner_user_id=owner.id,
        visibility="private",
    )
    shared_item = service.upsert_from_info(
        {
            "id": "shared-1",
            "extractor_key": "YouTube",
            "title": "Shared title",
            "filepath": "/tmp/shared.mp4",
        },
        owner_user_id=owner.id,
        visibility="shared",
    )
    session.commit()

    owner_items = service.list_items_page(owner)[0]
    friend_items = service.list_items_page(friend)[0]

    assert {item.id for item in owner_items} == {private_item.id, shared_item.id}
    assert {item.id for item in friend_items} == {shared_item.id}


def test_vault_owner_has_no_bypass_into_another_members_private_library() -> None:
    session = make_session()
    owner = make_user()
    vault_owner = User(
        id="vault-owner",
        username="vault-owner",
        display_name="Vault Owner",
        password_hash=hash_password("secret"),
        role="admin",
        is_active=True,
    )
    session.add_all([owner, vault_owner])
    session.commit()
    service = LibraryService(session)
    private_item = service.upsert_from_info(
        {"id": "private-admin-check", "extractor_key": "YouTube", "title": "Private", "filepath": "/tmp/private.mp4"},
        owner_user_id=owner.id,
        visibility="private",
    )
    shared_item = service.upsert_from_info(
        {"id": "shared-admin-check", "extractor_key": "YouTube", "title": "Shared", "filepath": "/tmp/shared.mp4"},
        owner_user_id=owner.id,
        visibility="shared",
    )
    session.commit()

    assert [item.id for item in service.list_items_page(vault_owner)[0]] == [shared_item.id]
    assert service.get_item(private_item.id, vault_owner) is None
    assert service.can_manage(private_item, vault_owner) is False
    assert service.can_manage(shared_item, vault_owner) is False
    assert "file_path" not in service.serialize(shared_item, vault_owner).model_dump()


def test_set_visibility_allows_owner_to_publish_video() -> None:
    session = make_session()
    owner = make_user()
    friend = make_user_two()
    session.add_all([owner, friend])
    session.commit()
    service = LibraryService(session)

    item = service.upsert_from_info(
        {
            "id": "toggle-1",
            "extractor_key": "YouTube",
            "title": "Toggle title",
            "filepath": "/tmp/toggle.mp4",
        },
        owner_user_id=owner.id,
        visibility="private",
    )
    session.commit()

    assert service.get_item(item.id, friend) is None
    updated = service.set_visibility(item, "shared", owner)
    session.commit()

    assert updated.visibility == "shared"
    assert service.get_item(item.id, friend) is not None


def test_library_event_payloads_distinguish_shared_broadcasts_from_private_audiences() -> None:
    session = make_session()
    owner = make_user()
    session.add(owner)
    session.commit()
    service = LibraryService(session)
    item = service.upsert_from_info(
        {
            "id": "event-audience",
            "extractor_key": "YouTube",
            "title": "Audience",
            "filepath": "/tmp/audience.mp4",
        },
        owner_user_id=owner.id,
        visibility="private",
    )

    private_payload = service._event_payload(item)  # noqa: SLF001
    item.visibility = "shared"
    shared_payload = service._event_payload(item)  # noqa: SLF001

    assert private_payload["user_id"] == owner.id
    assert "broadcast" not in private_payload
    assert shared_payload["broadcast"] is True
    assert "user_id" not in shared_payload


def test_serialize_hides_server_file_path_from_non_manager() -> None:
    session = make_session()
    owner = make_user()
    friend = make_user_two()
    session.add_all([owner, friend])
    session.commit()
    service = LibraryService(session)

    item = service.upsert_from_info(
        {
            "id": "shared-visible-path",
            "extractor_key": "YouTube",
            "title": "Shared title",
            "filepath": "/tmp/shared.mp4",
        },
        owner_user_id=owner.id,
        visibility="shared",
    )
    session.commit()

    owner_view = service.serialize(item, owner)
    friend_view = service.serialize(item, friend)

    assert "file_path" not in owner_view.model_dump()
    assert "file_path" not in friend_view.model_dump()


def test_upsert_from_info_maintains_stored_metadata_summary() -> None:
    session = make_session()
    service = LibraryService(session)

    item = service.upsert_from_info(
        {
            "id": "summary-write",
            "extractor_key": "YouTube",
            "title": "Summary maintained",
            "filepath": "/tmp/summary.mp4",
            "formats": [{}, {}, {}],
            "description": "should never reach the stored summary",
        }
    )
    session.commit()

    assert item.metadata_summary == LibraryService.summarize_metadata(item.metadata_json)
    assert item.metadata_summary["formats_count"] == 3
    assert "formats" not in item.metadata_summary
    assert "description" not in item.metadata_summary

    updated = service.upsert_from_info(
        {
            "id": "summary-write",
            "extractor_key": "YouTube",
            "title": "Summary refreshed",
            "filepath": "/tmp/summary.mp4",
            "formats": [{}],
        }
    )
    session.commit()

    assert updated.id == item.id
    assert updated.metadata_summary["formats_count"] == 1


def test_serialize_summary_prefers_the_stored_summary_column() -> None:
    session = make_session()
    item = LibraryItem(
        id="stored-summary",
        extractor="YouTube",
        remote_id="stored1",
        title="Stored summary wins",
        metadata_json={"id": "stored1", "formats": [{}, {}], "description": "full detail"},
        metadata_summary={"id": "stored1", "formats_count": 2, "title": "stored marker"},
        status="available",
    )
    session.add(item)
    session.commit()

    summarized = LibraryService(session).serialize(item, summary=True)
    detailed = LibraryService(session).serialize(item)

    assert summarized.metadata_json == {"id": "stored1", "formats_count": 2, "title": "stored marker"}
    assert detailed.metadata_json["description"] == "full detail"
    assert "formats" not in detailed.metadata_json  # raw formats stay server-side


class RecordingEvents:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, name: str, payload: dict) -> None:
        self.published.append((name, payload))


def _reconcile_item(item_id: str, file_path: str) -> LibraryItem:
    from app.services.library import LibraryService as _Service, normalize_thumbnail_metadata

    metadata = normalize_thumbnail_metadata({"id": item_id, "filepath": file_path}, None, file_path)
    return LibraryItem(
        id=item_id,
        title=item_id,
        visibility="shared",
        file_path=file_path,
        file_size=5,
        metadata_json=metadata,
        metadata_summary=_Service.summarize_metadata(metadata),
        status="available",
    )


def _library_dir():
    from app.config import settings

    settings.library_root.mkdir(parents=True, exist_ok=True)
    return settings.library_root


def _register(session, item_id: str, path) -> None:  # noqa: ANN001
    from app.services.media_artifacts import MediaArtifactService

    with session.begin():
        MediaArtifactService(session).register_file(session.get(LibraryItem, item_id), str(path))


def test_reconcile_step_processes_exactly_one_batch_and_advances_a_durable_cursor(tmp_path) -> None:  # noqa: ANN001
    from app.models import AppMaintenanceState

    session = make_session()
    events = RecordingEvents()
    service = LibraryService(session, events)
    present = _library_dir() / "present.mp4"
    present.write_bytes(b"media")
    with session.begin():
        session.add_all([
            _reconcile_item("batch-1", str(tmp_path / "gone-1.mp4")),
            _reconcile_item("batch-2", str(present)),
            _reconcile_item("batch-3", str(tmp_path / "gone-3.mp4")),
            _reconcile_item("batch-4", str(tmp_path / "gone-4.mp4")),
            _reconcile_item("batch-5", str(tmp_path / "gone-5.mp4")),
        ])
    _register(session, "batch-2", present)

    updated, completed = service.reconcile_files_step(batch_size=2)

    assert completed is False
    assert {item.id for item in updated} == {"batch-1"}
    cursor = session.get(AppMaintenanceState, "library_reconcile_files_cursor")
    assert cursor is not None and cursor.value == "batch-2"
    assert [name for name, _ in events.published] == ["library_item_missing"]
    assert events.published[0][1]["item"]["id"] == "batch-1"
    assert session.get(LibraryItem, "batch-1").status == "missing"
    assert session.get(LibraryItem, "batch-2").status == "available"

    updated, completed = service.reconcile_files_step(batch_size=2)
    assert completed is False
    assert {item.id for item in updated} == {"batch-3", "batch-4"}

    updated, completed = service.reconcile_files_step(batch_size=2)
    assert completed is True
    assert {item.id for item in updated} == {"batch-5"}
    assert session.get(AppMaintenanceState, "library_reconcile_files_cursor").value == ""

    events.published.clear()
    updated, completed = service.reconcile_files_step(batch_size=2)
    assert completed is False
    assert updated == []  # no transitions on the second sweep
    assert events.published == []


def test_reconcile_step_fires_recovery_transition_after_commit(tmp_path) -> None:  # noqa: ANN001
    session = make_session()
    events = RecordingEvents()
    service = LibraryService(session, events)
    media = _library_dir() / "restored.mp4"
    with session.begin():
        item = _reconcile_item("restored-item", str(media))
        item.status = "missing"
        session.add(item)
    _register(session, "restored-item", media)

    media.write_bytes(b"media returned")
    updated, completed = service.reconcile_files_step(batch_size=10)

    assert completed is True
    assert [item.id for item in updated] == ["restored-item"]
    assert session.get(LibraryItem, "restored-item").status == "available"
    assert [name for name, _ in events.published] == ["library_item_upserted"]


def test_member_refresh_cannot_interleave_with_a_stalled_background_step(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A background step that stalls mid-stat must not store a stale cursor over a refresh."""
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'sweep.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    media_paths = {}
    with factory.begin() as session:
        for index in range(4):
            path = tmp_path / f"sweep-{index}.mp4"
            path.write_bytes(b"media")
            media_paths[f"sweep-{index}"] = path
            session.add(_reconcile_item(f"sweep-{index}", str(path)))

    stat_entered = threading.Event()
    resume = threading.Event()
    original_raw_path = LibraryService._raw_file_path

    def stalling_raw_path(item):  # noqa: ANN001
        if threading.current_thread().name == "background-sweep" and not resume.is_set():
            stat_entered.set()
            assert resume.wait(timeout=10)
        return original_raw_path(item)

    monkeypatch.setattr(LibraryService, "_raw_file_path", staticmethod(stalling_raw_path))

    def background_step() -> None:
        with factory() as session:
            LibraryService(session).reconcile_files_step(batch_size=2)

    background = threading.Thread(target=background_step, name="background-sweep")
    background.start()
    assert stat_entered.wait(timeout=10)

    refresh_done = threading.Event()

    def member_refresh() -> None:
        with factory() as session:
            service = LibraryService(session)
            while not service.reconcile_files_step(batch_size=2)[1]:
                pass
        refresh_done.set()

    refresh = threading.Thread(target=member_refresh, name="member-refresh")
    refresh.start()
    # With proper sweep mutual exclusion the refresh must wait for the stalled
    # background step instead of interleaving with it.
    refresh_done.wait(timeout=0.5)
    resume.set()
    background.join(timeout=10)
    refresh.join(timeout=10)
    assert not background.is_alive() and not refresh.is_alive()

    # Every file disappears; the very next COMPLETED sweep must have observed
    # every row. A stale cursor left by the interleave would skip rows.
    for path in media_paths.values():
        path.unlink()
    with factory() as session:
        service = LibraryService(session)
        completed = False
        for _ in range(10):
            _updated, completed = service.reconcile_files_step(batch_size=2)
            if completed:
                break
        assert completed
        statuses = {item_id: session.get(LibraryItem, item_id).status for item_id in media_paths}
    assert statuses == {item_id: "missing" for item_id in media_paths}
