from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app import main as main_module
from app.models import User
from app.security import get_current_user
from app.schemas import MediaSourceCapabilities, PreviewRequest, PreviewResponse
from app.services.remote_streaming import (
    HlsAsset,
    HlsPresentation,
    RemoteStreamingService,
    StreamResponseSpec,
    StreamNotFoundError,
    UpstreamMediaResponse,
    _FileRangeBody,
)
from app.services.yt_dlp_service import YtDlpService


async def collect_streaming_response(response) -> bytes:  # noqa: ANN001
    return b"".join([chunk async for chunk in response.body_iterator])


def test_streaming_response_closes_file_body_once_when_send_disconnects(tmp_path) -> None:  # noqa: ANN001
    media = tmp_path / "segment.m4s"
    media.write_bytes(b"segment-body")
    body = _FileRangeBody(media, 0, media.stat().st_size - 1)
    closes = []

    def close() -> None:
        closes.append("closed")
        body.close()

    response = main_module._streaming_response(StreamResponseSpec(200, {}, body, close))

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def disconnect(message: dict) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        await response(
            {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}},
            receive,
            disconnect,
        )

    with pytest.raises(ClientDisconnect):
        asyncio.run(request())
    assert closes == ["closed"] and body._file.closed is True


def test_streaming_response_closes_once_after_success() -> None:
    closes = []
    messages = []
    response = main_module._streaming_response(
        StreamResponseSpec(200, {"Content-Type": "video/mp4"}, [b"media"], lambda: closes.append("closed"))
    )

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            messages.append(message)

        await response(
            {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    asyncio.run(request())
    assert closes == ["closed"]
    assert b"".join(message["body"] for message in messages if message["type"] == "http.response.body") == b"media"


class IntegrationReader:
    def open(self, track, byte_range):  # noqa: ANN001
        payload = b"browser-media"
        start = byte_range.start if byte_range else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(payload) - 1
        body = payload[start : end + 1]
        return UpstreamMediaResponse(
            206 if byte_range else 200,
            {
                "content-type": track.content_type,
                "content-length": str(len(body)),
                "content-range": f"bytes {start}-{end}/{len(payload)}" if byte_range else "",
                "accept-ranges": "bytes",
            },
            [body],
        )


class IntegrationPackager:
    def prepare(self, stream_id, generation, video, audio):  # noqa: ANN001
        del stream_id, generation, video, audio
        return HlsPresentation(
            b"#EXTM3U\nsegment.m4s\n",
            {"segment.m4s": HlsAsset(b"muxed-segment", "video/iso.segment")},
        )

    def close(self, stream_id):  # noqa: ANN001
        del stream_id

    def close_generation(self, stream_id, generation):  # noqa: ANN001
        del stream_id, generation


class IntegrationResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        del source_url, owner_user_id
        raise AssertionError("integration fixture should not refresh")


def format_info(kind: str) -> dict:
    if kind == "progressive":
        return {
            "formats": [{
                "format_id": "18",
                "url": "https://secret.example/progressive?signature=private",
                "url_expiry": 5_000,
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.42001e",
                "acodec": "mp4a.40.2",
                "filesize": len(b"browser-media"),
                "http_headers": {"Cookie": "private-cookie"},
            }]
        }
    if kind == "split":
        return {
            "formats": [
                {"format_id": "137", "url": "https://secret.example/video", "url_expiry": 5_000, "protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
                {"format_id": "140", "url": "https://secret.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
            ]
        }
    if kind == "audio":
        return {
            "formats": [{"format_id": "140", "url": "https://secret.example/audio", "url_expiry": 5_000, "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": len(b"browser-media")}]
        }
    raise AssertionError(kind)


@pytest.mark.parametrize(
    ("kind", "transport", "media_kind"),
    [("progressive", "progressive", "video"), ("split", "hls", "video"), ("audio", "progressive", "audio")],
)
def test_preview_registers_stable_redacted_playback_for_browser_source_shapes(
    monkeypatch,
    kind: str,
    transport: str,
    media_kind: str,
) -> None:
    user = User(id="owner-1", username="owner", role="viewer", is_active=True)
    service = RemoteStreamingService(
        resolver=IntegrationResolver(),
        reader=IntegrationReader(),
        hls_packager=IntegrationPackager(),
        token_factory=lambda: f"{kind}-stream",
        clock=lambda: 1_000.0,
    )
    monkeypatch.setattr(main_module, "remote_streams", service)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *args, **kwargs: user)
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        info = {"title": "Playable", "webpage_url": source_url, **format_info(kind)}
        return PreviewResponse(kind="video", title="Playable", webpage_url=source_url, raw=info)

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)

    response = asyncio.run(main_module.preview(PreviewRequest(source_url=f"https://source.example/{kind}"), object()))

    assert response.playback is not None
    assert response.playback.transport == transport
    assert response.playback.media_kind == media_kind
    assert response.playback.playback_url.startswith(f"/api/remote-streams/{kind}-stream/")
    assert "secret.example" not in repr(response)
    assert "private-cookie" not in repr(response)


def test_live_preview_does_not_register_a_seekable_remote_playback_session(monkeypatch) -> None:
    user = User(id="live-owner", username="live-owner", role="viewer", is_active=True)
    service = RemoteStreamingService(
        resolver=IntegrationResolver(), reader=IntegrationReader(), token_factory=lambda: "must-not-register",
    )
    monkeypatch.setattr(main_module, "remote_streams", service)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *args, **kwargs: user)
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        return PreviewResponse(
            kind="video", title="Live now", webpage_url=source_url,
            capabilities=MediaSourceCapabilities(
                provider="youtube", lifecycle="live", can_play=False,
                play_reason="live_playback_not_supported", can_acquire=False,
                acquire_reason="live_acquisition_not_supported",
            ),
            raw={"title": "Live now", "webpage_url": source_url, "is_live": True},
        )

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://www.youtube.com/watch?v=live"), object()))

    assert response.capabilities is not None
    assert response.capabilities.lifecycle == "live"
    assert response.playback is None
    assert "must-not-register" not in repr(service)


def test_manual_rendition_routes_pin_and_serve_only_the_owned_opaque_rendition(monkeypatch) -> None:  # noqa: ANN001
    owner = User(id="quality-owner", username="quality-owner", role="viewer", is_active=True)
    other = User(id="quality-other", username="quality-other", role="viewer", is_active=True)
    rendition_ids = iter(("quality-720", "quality-1080"))
    service = RemoteStreamingService(
        resolver=IntegrationResolver(),
        reader=IntegrationReader(),
        token_factory=lambda: "quality-stream",
        rendition_token_factory=lambda: next(rendition_ids),
        clock=lambda: 1_000.0,
    )
    descriptor = service.register(
        owner_user_id=owner.id,
        source_url="https://source.example/quality",
        info={"formats": [
            {**format_info("progressive")["formats"][0], "format_id": "720", "height": 720, "width": 1280},
            {**format_info("progressive")["formats"][0], "format_id": "1080", "height": 1080, "width": 1920},
        ]},
    )
    monkeypatch.setattr(main_module, "remote_streams", service)
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    selected_id = next(item.rendition_id for item in descriptor.renditions if item.height == 720)

    selected = main_module.select_remote_stream_rendition(
        descriptor.stream_id, selected_id, object(), owner,
    )
    assert selected.selected_rendition_id == selected_id
    response = main_module.remote_stream_rendition_content(
        descriptor.stream_id, selected_id, object(), owner, "bytes=0-3",
    )
    assert response.status_code == 206
    assert asyncio.run(collect_streaming_response(response)) == b"brow"
    with pytest.raises(StreamNotFoundError):
        main_module.select_remote_stream_rendition(descriptor.stream_id, selected_id, object(), other)


def test_http_stream_routes_enforce_owner_auth_and_never_emit_private_transport(monkeypatch) -> None:  # noqa: ANN001
    owner = User(id="owner-http", username="owner-http", display_name="Owner", role="viewer", is_active=True)
    other = User(id="other-http", username="other-http", display_name="Other", role="viewer", is_active=True)
    service = RemoteStreamingService(
        resolver=IntegrationResolver(),
        reader=IntegrationReader(),
        hls_packager=IntegrationPackager(),
        token_factory=lambda: "http-stream",
        clock=lambda: 1_000.0,
    )
    playback = service.register(owner_user_id=owner.id, source_url="https://source.example/private", info=format_info("progressive"))
    monkeypatch.setattr(main_module, "remote_streams", service)
    active_user = {"value": owner}
    main_module.app.dependency_overrides[get_current_user] = lambda: active_user["value"]
    main_module.app.dependency_overrides[main_module.screen_time.require_watch_time] = lambda: None  # covered by test_member_access_enforcement
    client = TestClient(main_module.app, base_url="http://localhost", raise_server_exceptions=False)
    try:
        response = client.get(playback.playback_url, headers={"Range": "bytes=2-6"})
        assert response.status_code == 206
        assert response.content == b"owser"
        assert "secret.example" not in repr(response.headers)
        assert "private-cookie" not in repr(response.headers)

        active_user["value"] = other
        hidden = client.get(playback.playback_url)
        assert hidden.status_code == 404
        assert hidden.json() == {"detail": "Remote stream not found."}
        assert "owner-http" not in hidden.text
    finally:
        client.close()
        main_module.app.dependency_overrides.clear()


def test_preview_event_payload_is_redacted_with_the_http_response(monkeypatch) -> None:  # noqa: ANN001
    user = User(id="event-owner", username="event-owner", display_name="Owner", role="viewer", is_active=True)
    published: list[dict] = []
    service = RemoteStreamingService(
        resolver=IntegrationResolver(), reader=IntegrationReader(), hls_packager=IntegrationPackager(),
        token_factory=lambda: "event-stream", clock=lambda: 1_000.0,
    )
    monkeypatch.setattr(main_module, "remote_streams", service)
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *args, **kwargs: user)
    monkeypatch.setattr(main_module, "_gate_snapshot", lambda *a, **k: None)  # streaming gates: test_member_access_enforcement
    monkeypatch.setattr(main_module, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(main_module, "_playback_ceiling", lambda _user_id: None)
    monkeypatch.setattr(main_module.events, "publish", lambda _kind, payload: published.append(payload))

    def fake_preview(self, source_url, lazy_playlist=True, format_selection=None, entries_limit=None):  # noqa: ANN001
        del self, lazy_playlist, format_selection
        return PreviewResponse(kind="video", title="Private", webpage_url=source_url, raw={"title": "Private", "url": "https://secret.example/direct?token=event-secret", **format_info("progressive")})

    monkeypatch.setattr(YtDlpService, "preview", fake_preview)
    response = asyncio.run(main_module.preview(PreviewRequest(source_url="https://source.example/private"), object()))

    assert response.playback is not None
    assert published
    serialized = repr(published)
    assert "secret.example" not in serialized
    assert "event-secret" not in serialized
    assert "private-cookie" not in serialized


def test_playback_ceiling_reads_only_a_known_saved_member_preference(monkeypatch) -> None:  # noqa: ANN001
    from app.models import UserSettings
    from tests.support import memory_session_factory

    factory = memory_session_factory()
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    with factory() as db:
        db.add_all([
            UserSettings(id="s1", user_id="capped", ui_prefs={"playback_max_height": 720, "theme": "dark"}),
            UserSettings(id="s2", user_id="odd", ui_prefs={"playback_max_height": 999}),
        ])
        db.commit()

    assert main_module._playback_ceiling("capped") == 720
    assert main_module._playback_ceiling("odd") is None
    assert main_module._playback_ceiling("no-settings-row") is None
