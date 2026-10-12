import asyncio
import warnings

from app import main as main_module
from app.models import User
from app.schemas import (
    DescriptionTimestampResponse,
    NormalizedChapterResponse,
    PreviewEntry,
    PreviewRequest,
    PreviewResponse,
)
from app.services.popular_discovery import PopularItem, PopularSnapshot
from app.services.yt_dlp_service import YtDlpService


def test_popular_route_mints_artwork_from_durable_provider_thumbnail(monkeypatch, db_factory) -> None:  # noqa: ANN001
    item = PopularItem(
        id="one", title="One", uploader=None, duration=None,
        thumbnail="https://provider.example/thumb.jpg", artwork_url="/api/artwork/remote/stale-process-id",
        webpage_url="https://provider.example/watch/one", view_count=None, availability=None,
        published_at=None, source="youtube", source_label="YouTube", capabilities=None, category_keys=("music",),
    )
    snapshot = PopularSnapshot(
        items=(item,), categories=(), state="ready", refreshing=False, stale=False,
        last_success_at=None, refreshed_at=None, next_refresh_at=None, error=None,
    )

    class FakeDiscovery:
        def get_snapshot(self):  # noqa: ANN201
            return snapshot

        def candidates(self):  # noqa: ANN201
            return snapshot.items

    monkeypatch.setattr(main_module, "popular_discovery", FakeDiscovery())
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "_remote_artwork_url", lambda url: f"/api/artwork/remote/new-for-{url.rsplit('/', 1)[-1]}")
    with db_factory() as db:
        response = main_module.popular_now(object(), User(id="user", username="user", role="viewer", is_active=True), db)

    assert response.items[0].artwork_url == "/api/artwork/remote/new-for-thumb.jpg"
    assert response.items[0].artwork_url != item.artwork_url


def test_preview_route_uses_square_channel_avatar_and_mints_entry_artwork(monkeypatch) -> None:  # noqa: ANN001
    channel_avatar = "https://yt3.example/channel-avatar.jpg"
    video_thumbnail = "https://i.ytimg.example/video-thumbnail.jpg"
    provider_urls: list[str | None] = []
    published: list[dict] = []

    def fake_preview(self, *_args, **_kwargs):  # noqa: ANN001, ARG001
        return PreviewResponse(
            kind="playlist",
            title="Area52 - Videos",
            entries=[
                PreviewEntry(
                    id="video-1",
                    title="A real channel video",
                    thumbnail=video_thumbnail,
                    webpage_url="https://www.youtube.com/watch?v=video-1",
                )
            ],
            raw={
                "thumbnail": None,
                "thumbnails": [
                    {"url": "https://yt3.example/channel-banner.jpg", "width": 2560, "height": 424, "id": "banner"},
                    {"url": channel_avatar, "width": 900, "height": 900, "id": "avatar"},
                ],
            },
        )

    def mint(provider_url):  # noqa: ANN001, ANN202
        provider_urls.append(provider_url)
        return f"/api/artwork/remote/opaque-{len(provider_urls)}" if provider_url else None

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *_args, **_kwargs: User(id="user", username="user", role="viewer", is_active=True))
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_remote_artwork_url", mint)
    monkeypatch.setattr(main_module.events, "publish", lambda _event, payload: published.append(payload))

    response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.youtube.com/channel/channel-id/videos"), object()))

    assert provider_urls == [channel_avatar, video_thumbnail]
    assert response.artwork_url == "/api/artwork/remote/opaque-1"
    assert response.entries[0].artwork_url == "/api/artwork/remote/opaque-2"
    assert response.entries[0].thumbnail is None
    assert "thumbnail" not in response.raw
    assert "thumbnails" not in response.raw
    assert channel_avatar not in response.model_dump_json()
    assert video_thumbnail not in response.model_dump_json()
    assert channel_avatar not in str(published)
    assert video_thumbnail not in str(published)


def test_video_preview_route_keeps_an_opaque_widescreen_poster(monkeypatch) -> None:  # noqa: ANN001
    provider_thumbnail = "https://i.ytimg.example/video-poster.jpg"

    def fake_preview(self, *_args, **_kwargs):  # noqa: ANN001, ARG001
        return PreviewResponse(
            kind="video",
            title="A standalone video",
            webpage_url="https://www.youtube.com/watch?v=video-1",
            raw={
                "thumbnail": provider_thumbnail,
                "webpage_url": "https://www.youtube.com/watch?v=video-1",
            },
        )

    class FakeRemoteStreams:
        @staticmethod
        def register(**_kwargs):  # noqa: ANN003, ANN205
            return {
                "status": "ready",
                "stream_id": "opaque-stream",
                "transport": "progressive",
                "media_kind": "video",
                "playback_url": "/api/remote-streams/opaque-stream/content",
                "content_type": "video/mp4",
                "has_video": True,
                "has_audio": True,
                "seekable": True,
            }

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    monkeypatch.setattr(main_module, "remote_streams", FakeRemoteStreams())
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *_args, **_kwargs: User(id="user", username="user", role="viewer", is_active=True))
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_remote_artwork_url", lambda url: "/api/artwork/remote/opaque-poster" if url else None)
    monkeypatch.setattr(main_module.events, "publish", lambda *_args, **_kwargs: None)

    response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.youtube.com/watch?v=video-1"), object()))

    assert response.artwork_url == "/api/artwork/remote/opaque-poster"
    assert "thumbnail" not in response.raw
    assert provider_thumbnail not in response.model_dump_json()


def test_preview_route_chapters_and_timestamps_serialize_without_warnings(monkeypatch) -> None:  # noqa: ANN001
    """Regression for #141: model_copy(update=...) skips validation, so the
    preview route must build NormalizedChapterResponse/DescriptionTimestampResponse
    instances itself rather than leaving normalize_chapters()'s plain dicts in
    those typed fields — otherwise pydantic-core emits a UserWarning per item
    on every serialize (model_dump / model_dump_json / the FastAPI response)."""

    def fake_preview(self, *_args, **_kwargs):  # noqa: ANN001, ARG001
        return PreviewResponse(
            kind="video",
            title="A chaptered video",
            webpage_url="https://www.youtube.com/watch?v=video-1",
            raw={
                "chapters": [{"start_time": 0, "end_time": 30, "title": "Intro"}],
                "description": "0:00 Intro\n0:30 Main\n1:00 Outro",
                "duration": 90,
                "webpage_url": "https://www.youtube.com/watch?v=video-1",
            },
        )

    class FakeRemoteStreams:
        @staticmethod
        def register(**_kwargs):  # noqa: ANN003, ANN205
            return {
                "status": "ready",
                "stream_id": "opaque-stream",
                "transport": "progressive",
                "media_kind": "video",
                "playback_url": "/api/remote-streams/opaque-stream/content",
                "content_type": "video/mp4",
                "has_video": True,
                "has_audio": True,
                "seekable": True,
            }

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    monkeypatch.setattr(main_module, "remote_streams", FakeRemoteStreams())
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *_args, **_kwargs: User(id="user", username="user", role="viewer", is_active=True))
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_remote_artwork_url", lambda url: None)
    monkeypatch.setattr(main_module.events, "publish", lambda *_args, **_kwargs: None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.youtube.com/watch?v=video-1"), object()))
        response.model_dump_json()

    assert not [w for w in caught if issubclass(w.category, UserWarning)], [str(w.message) for w in caught]
    assert response.chapters and all(isinstance(c, NormalizedChapterResponse) for c in response.chapters)
    assert response.description_timestamps and all(
        isinstance(t, DescriptionTimestampResponse) for t in response.description_timestamps
    )
