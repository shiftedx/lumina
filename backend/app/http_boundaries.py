from __future__ import annotations

import asyncio
import logging
import re
import time

from starlette.datastructures import URL, Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.security import validate_state_changing_request
from app.services import public_address


MAX_REQUEST_BODY_BYTES = 2_097_152
REQUEST_TOO_LARGE_MESSAGE = "Request body is too large. The maximum request size is 2 MiB."
# The one route shape with a bigger cap: an artwork upload. ASGI ``path`` is already percent-decoded
# and carries no query; fullmatch refuses any extra segment or trailing slash. PUT only.
UPLOAD_ROUTE = re.compile(r"/api/titles/[0-9a-f-]{36}/images/(?:Primary|Backdrop|Logo)/[0-4]|/api/metadata/people/[0-9a-f-]{36}/photo")
MAX_UPLOAD_BODY_BYTES = 15 * 1024 * 1024
UPLOAD_TOO_LARGE_MESSAGE = "Request body is too large. The maximum upload size is 15 MiB."
# An upload body that stops arriving (or trickles) is cut so it cannot hold a connection and a worker.
UPLOAD_IDLE_TIMEOUT_SECONDS = 15
UPLOAD_BODY_DEADLINE_SECONDS = 300


def is_upload(scope: Scope) -> bool:
    return scope["method"] == "PUT" and UPLOAD_ROUTE.fullmatch(scope["path"]) is not None


def body_limit(scope: Scope) -> int:
    return MAX_UPLOAD_BODY_BYTES if is_upload(scope) else MAX_REQUEST_BODY_BYTES

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: http: https:; media-src 'self' blob:; "
        "frame-src 'none'; connect-src 'self'; font-src 'self' data:"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Only for a request that reached a trusted proxy over HTTPS: LAN HTTP mode must never be pinned to HTTPS.
# No includeSubDomains/preload: sibling hosts of the public address are not Lumina's to pin.
HSTS = "max-age=31536000"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # An http address (the local one) is never pinned to HTTPS, even if the proxy relays an https request for it.
        https = public_address.arrived_over_https(scope) and not (public_address.origin_for(URL(scope=scope).hostname) or "").startswith("http:")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if https:
                    headers["Strict-Transport-Security"] = HSTS
            await send(message)

        await self.app(scope, receive, send_with_headers)


access_log = logging.getLogger("lumina.access")


class FailedRequestLogMiddleware:
    """Log one line per 4xx/5xx response: method, route template (never the raw path or query), status, duration."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def send_capturing_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capturing_status)
        finally:
            if status >= 400:
                route = getattr(scope.get("route"), "path", "[unmatched]")
                access_log.warning("%s %s %d %dms", scope["method"], route, status, (time.monotonic() - started) * 1000)


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw_length = headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError:
            content_length = None
        limit = body_limit(scope)
        upload = limit == MAX_UPLOAD_BODY_BYTES
        message = UPLOAD_TOO_LARGE_MESSAGE if upload else REQUEST_TOO_LARGE_MESSAGE
        if content_length is not None and (content_length < 0 or content_length > limit):
            await JSONResponse(status_code=413, content={"detail": message})(scope, receive, send)
            return
        if upload:
            # Never buffered here: buffering before the route's auth would let anyone make the server hold 15 MiB.
            # The route authorises first, then reads; past the cap it sees state.body_too_large and a disconnect
            # (Request.body() raises ClientDisconnect), and answers 413.
            await self.app(scope, _counting(scope, receive, limit), send)
            return
        # A request without HTTP body framing has no body to buffer. Passing its
        # receive channel through is important for streaming responses, which use
        # it to observe a later client disconnect.
        if content_length is None and headers.get("transfer-encoding") is None:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                await JSONResponse(status_code=413, content={"detail": REQUEST_TOO_LARGE_MESSAGE})(scope, receive, send)
                return
            body.extend(chunk)
            more_body = bool(message.get("more_body", False))

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_body, send)


def _counting(scope: Scope, receive: Receive, limit: int) -> Receive:
    received, complete, deadline = 0, False, time.monotonic() + UPLOAD_BODY_DEADLINE_SECONDS

    async def counted() -> Message:
        nonlocal received, complete
        state = scope.get("state", {})
        if state.get("body_too_large") or state.get("body_timeout"):
            return {"type": "http.disconnect"}
        if complete:  # after the body, receive() only reports a disconnect: no timeout
            return await receive()
        try:
            message = await asyncio.wait_for(receive(), min(UPLOAD_IDLE_TIMEOUT_SECONDS, max(0.0, deadline - time.monotonic())))
        except TimeoutError:
            scope.setdefault("state", {})["body_timeout"] = True
            return {"type": "http.disconnect"}
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            complete = not message.get("more_body", False)
            if received > limit:
                scope.setdefault("state", {})["body_too_large"] = True
                return {"type": "http.disconnect"}
        return message

    return counted


class OriginCheckMiddleware:
    """Refuse a state-changing request from an untrusted origin (403), as pure ASGI.

    It replaces an ``@app.middleware("http")`` function (Starlette's BaseHTTPMiddleware), which wrapped every
    image and video response in an extra task and stream. The rule is unchanged:
    ``app.security.validate_state_changing_request``.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            failure = validate_state_changing_request(Request(scope))
            if failure:
                await JSONResponse(status_code=403, content={"detail": failure})(scope, receive, send)
                return
        await self.app(scope, receive, send)
