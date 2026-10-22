"""Balance authenticated request activity across the full ASGI response lifetime."""
from __future__ import annotations

from app.services.background_work import background_work


class AuthenticatedActivityMiddleware:
    """End activity marked by an authentication dependency when the HTTP request finishes."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_sse_release(message) -> None:  # noqa: ANN001
            await send(message)
            if message["type"] != "http.response.start":
                return
            content_type = next(
                (value for name, value in message.get("headers", ()) if name.lower() == b"content-type"),
                b"",
            )
            # GET subscriptions are passive; finite POST imports remain active through their last result.
            if scope.get("method") == "GET" and content_type.split(b";", 1)[0].strip().lower() == b"text/event-stream":
                background_work.end_request(scope)

        try:
            await self.app(scope, receive, send_with_sse_release)
        finally:
            background_work.end_request(scope)
