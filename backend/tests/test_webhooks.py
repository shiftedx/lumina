from __future__ import annotations

import pytest

from app.models import SourceAutomation
from app.services.webhooks import WebhookService
from support import memory_session_factory, seed_app_settings


def make_session():
    return memory_session_factory()()


def test_webhook_service_reads_config_from_app_settings() -> None:
    session = make_session()
    seed_app_settings(
        session,
        ui_prefs={
            "webhook_url": "https://discord.com/api/webhooks/test/token",
            "webhook_enabled": True,
            "webhook_notify_new_videos": False,
            "webhook_notify_failures": True,
        },
    )

    config = WebhookService(session)._config()  # noqa: SLF001

    assert config == {
        "url": "https://discord.com/api/webhooks/test/token",
        "enabled": True,
        "notify_new_videos": False,
        "notify_failures": True,
    }


def test_discord_payload_uses_embed_shape() -> None:
    payload = WebhookService._discord_payload(  # noqa: SLF001
        "new_video_added",
        "New video added",
        "Example upload",
        {"Uploader": "Creator", "Visibility": "shared"},
        color=0xFF5A36,
    )

    assert payload["content"] == "Lumina • New video added"
    assert payload["embeds"][0]["title"] == "New video added"
    assert payload["embeds"][0]["description"] == "Example upload"
    assert payload["embeds"][0]["fields"][0]["name"] == "Uploader"


def test_send_test_posts_when_webhook_is_enabled(monkeypatch) -> None:
    session = make_session()
    seed_app_settings(
        session,
        ui_prefs={
            "webhook_url": "https://discord.com/api/webhooks/test/token",
            "webhook_enabled": True,
            "webhook_notify_new_videos": True,
            "webhook_notify_failures": True,
        },
    )

    captured: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *, timeout: float, trust_env: bool, follow_redirects: bool):
            captured["timeout"] = timeout
            captured["trust_env"] = trust_env
            captured["follow_redirects"] = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def post(self, url: str, json: dict) -> DummyResponse:
            captured["url"] = url
            captured["payload"] = json
            return DummyResponse()

    monkeypatch.setattr("app.services.webhooks.httpx.Client", DummyClient)

    ok, message = WebhookService(session).send_test()

    assert ok is True
    assert message == "Webhook delivered."
    assert captured["url"] == "https://discord.com/api/webhooks/test/token"
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["payload"]["embeds"][0]["title"] == "Webhook test"


def test_webhook_delivery_rejects_non_discord_and_credentialed_destinations(monkeypatch) -> None:
    for url in (
        "http://127.0.0.1/internal",
        "https://169.254.169.254/latest/meta-data",
        "https://attacker@discord.com/api/webhooks/test/token",
        "https://discord.com.evil.test/api/webhooks/test/token",
    ):
        session = make_session()
        seed_app_settings(session, ui_prefs={"webhook_url": url, "webhook_enabled": True})
        monkeypatch.setattr(
            "app.services.webhooks.httpx.Client",
            lambda **_kwargs: pytest.fail("network client must not be created for a rejected webhook"),
        )

        assert WebhookService(session).send_test() == (
            False,
            "Webhooks require an HTTPS Discord webhook URL.",
        )


def test_automation_failure_uses_source_automation_contract(monkeypatch) -> None:
    session = make_session()
    automation = SourceAutomation(
        id="automation-1",
        user_id="user-1",
        label="New uploads",
        source_url="https://www.youtube.com/@creator/videos",
        source_type="channel",
        cron_expression="*/30 * * * *",
    )
    captured: dict[str, object] = {}

    def fake_deliver(self, event_type, **kwargs):  # noqa: ANN001
        captured["event_type"] = event_type
        captured.update(kwargs)

    monkeypatch.setattr(WebhookService, "_deliver_quietly", fake_deliver)

    WebhookService(session).notify_automation_error(automation, "Sign-in expired")

    assert captured["event_type"] == "source_automation_error"
    assert captured["title"] == "Source automation error"
    # Member-private: the shared household channel gets no label, address or error.
    assert "New uploads" not in captured["message"] and "Sign-in" not in captured["message"]
    assert captured["details"] == {}
