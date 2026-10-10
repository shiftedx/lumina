"""Text response compression with explicit client opt-outs respected."""
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import Receive, Scope, Send


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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _declines_gzip(Headers(scope=scope).get("accept-encoding", "")):
            # Starlette checks for the substring "gzip", including gzip;q=0.
            # Copy the scope so outer middleware keeps the client's original headers.
            headers = [(name, value) for name, value in scope["headers"] if name.lower() != b"accept-encoding"]
            scope = {**scope, "headers": [*headers, (b"accept-encoding", b"identity")]}
        await super().__call__(scope, receive, send)
