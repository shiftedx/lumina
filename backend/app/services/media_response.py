"""File response tuned for video-sized local and cached playback bodies."""
from collections.abc import Callable, Iterable

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE


class MediaFileResponse(FileResponse):
    """FileResponse with fewer worker-thread file reads for media ranges."""

    chunk_size = MEDIA_FILE_CHUNK_SIZE


class ClosingStreamingResponse(StreamingResponse):
    """Always release a playback stream, including when its ASGI send disconnects."""

    def __init__(
        self,
        content: Iterable[bytes],
        *,
        close: Callable[[], None],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers)
        self._close_task = BackgroundTask(close)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._close_task()
