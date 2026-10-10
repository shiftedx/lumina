"""Text response compression with explicit client opt-outs respected."""
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


# Starlette 1.3.1 only excludes event streams and does not accept the newer
# ``exclude_content_types`` constructor argument. Keep the newer exclusions
# here so the locked runtime and development runtime have the same behavior.
_EXCLUDED_CONTENT_TYPE_PREFIXES = (
    "application/gzip",
    "application/x-gzip",
    "application/zip",
    "application/octet-stream",
    "audio/",
    "font/woff",
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/event-stream",
    "video/",
)
_BYPASS_HEADER = "x-lumina-compression-bypass"


class _ExcludeCompressedContent:
    """Mark already-compressed or byte-exact bodies so Starlette passes them through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def mark_excluded(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                content_type = headers.get("content-type", "").casefold()
                if "content-encoding" not in headers and content_type.startswith(_EXCLUDED_CONTENT_TYPE_PREFIXES):
                    # GZipMiddleware already treats any Content-Encoding as an
                    # instruction to pass through. The outer send removes both
                    # private headers before the response leaves the process.
                    headers["content-encoding"] = "identity"
                    headers[_BYPASS_HEADER] = "1"
            await send(message)

        await self.app(scope, receive, mark_excluded)


def _declines_gzip(value: str) -> bool:
    if ";" not in value:
        return False
    for entry in value.lower().split(","):
        coding, *parameters = entry.split(";")
        if coding.strip() != "gzip":
            continue
        for parameter in parameters:
            name, _, quality = parameter.partition("=")
            if name.strip() == "q":
                try:
                    return float(quality.strip()) <= 0
                except ValueError:
                    return True
    return False


class ResponseCompressionMiddleware(GZipMiddleware):
    """Use Starlette's compression and streaming exclusions, honoring gzip;q=0."""

    def __init__(self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 9) -> None:
        super().__init__(_ExcludeCompressedContent(app), minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        request_headers = Headers(scope=scope)
        if "range" in request_headers or _declines_gzip(request_headers.get("accept-encoding", "")):
            # Starlette checks for the substring "gzip", including gzip;q=0.
            # Copy the scope so outer middleware keeps the client's original headers.
            headers = [(name, value) for name, value in scope["headers"] if name.lower() != b"accept-encoding"]
            scope = {**scope, "headers": [*headers, (b"accept-encoding", b"identity")]}

        async def strip_bypass(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                if headers.get(_BYPASS_HEADER) == "1":
                    del headers[_BYPASS_HEADER]
                    if headers.get("content-encoding") == "identity":
                        del headers["content-encoding"]
            await send(message)

        await super().__call__(scope, receive, strip_bypass)
