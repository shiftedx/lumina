from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models import LibraryItem, SourceAutomation
from app.services.yt_dlp_service import YtDlpService


class WebhookService:
    def __init__(self, db: Session):
        self.db = db

    def send_test(self) -> tuple[bool, str]:
        return self._deliver(
            "manual_test",
            title="Webhook test",
            message="Lumina webhook integration is configured and reachable.",
            details={},
        )

    # The household webhook is a shared channel: it only ever carries details of
    # household-shared items. Downloads, jobs and automations belong to one member,
    # so failures are announced without any title, address or error text.
    def notify_new_video(self, item: LibraryItem) -> None:
        if item.visibility != "shared":
            return
        self._deliver_quietly(
            "new_video_added",
            title="New video added",
            message=item.title,
            details={
                "Uploader": item.uploader,
                "Playlist": item.playlist_name,
                "Duration": self._format_duration(item.duration),
                "Source": item.extractor,
                "Link": item.webpage_url,
            },
            gate="new_videos",
        )

    def notify_job_failed(self, job: dict[str, Any]) -> None:
        del job  # member-private: see the note above
        self._deliver_quietly(
            "job_failed",
            title="Download failed",
            message="A household member's download failed. Details are in Lumina.",
            details={},
            gate="failures",
            color=0xED4245,
        )

    def notify_automation_error(self, automation: SourceAutomation, error: str) -> None:
        del automation, error  # member-private: see the note above
        self._deliver_quietly(
            "source_automation_error",
            title="Source automation error",
            message="A household member's source automation failed. Details are in Lumina.",
            details={},
            gate="failures",
            color=0xED4245,
        )

    def _deliver_quietly(
        self,
        event_type: str,
        *,
        title: str,
        message: str,
        details: dict[str, Any],
        gate: str,
        color: int | None = None,
    ) -> None:
        try:
            self._deliver(event_type, title=title, message=message, details=details, gate=gate, color=color)
        except Exception:
            return

    def _deliver(
        self,
        event_type: str,
        *,
        title: str,
        message: str,
        details: dict[str, Any],
        gate: str | None = None,
        color: int | None = None,
    ) -> tuple[bool, str]:
        config = self._config()
        url = config.get("url")
        if not url:
            return False, "Webhook URL is not configured."
        if not config.get("enabled", False):
            return False, "Webhook delivery is disabled."
        if gate == "new_videos" and not config.get("notify_new_videos", True):
            return False, "New-video notifications are disabled."
        if gate == "failures" and not config.get("notify_failures", True):
            return False, "Failure notifications are disabled."

        if not self._is_discord_webhook(url):
            return False, "Webhooks require an HTTPS Discord webhook URL."
        payload = self._discord_payload(event_type, title, message, details, color)
        with httpx.Client(timeout=10.0, trust_env=False, follow_redirects=False) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        return True, "Webhook delivered."

    def _config(self) -> dict[str, Any]:
        record = YtDlpService(self.db).get_app_settings()
        prefs = record.ui_prefs or {}
        return {
            "url": (prefs.get("webhook_url") or "").strip() or None,
            "enabled": bool(prefs.get("webhook_enabled", False)),
            "notify_new_videos": bool(prefs.get("webhook_notify_new_videos", True)),
            "notify_failures": bool(prefs.get("webhook_notify_failures", True)),
        }

    @staticmethod
    def _is_discord_webhook(url: str) -> bool:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "https"
            and parsed.hostname == "discord.com"
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and parsed.path.startswith("/api/webhooks/")
            and not parsed.fragment
        )

    @staticmethod
    def _discord_payload(event_type: str, title: str, message: str, details: dict[str, Any], color: int | None = None) -> dict[str, Any]:
        fields = [
            {"name": key, "value": str(value), "inline": False}
            for key, value in details.items()
            if value not in (None, "", [], {})
        ][:10]
        return {
            "content": f"Lumina • {title}",
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": color or 0xFF5A36,
                    "fields": fields,
                    "footer": {"text": f"event: {event_type}"},
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ],
        }

    @staticmethod
    def _format_duration(duration: int | None) -> str | None:
        if not duration:
            return None
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
