"""File response tuned for video-sized local and cached playback bodies."""
from collections.abc import Callable, Iterable
from contextvars import ContextVar

import anyio
from fastapi.responses import FileResponse, StreamingResponse
from starlette._utils import create_collapsing_task_group
from starlette.background import BackgroundTask
from starlette.datastructures import MutableHeaders
from starlette.types import Receive, Scope, Send

from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE

_file_receive: ContextVar[Receive] = ContextVar("media_file_receive")


class MediaFileResponse(FileResponse):
    """Start ranges with a small read, then amortize worker calls with media-sized chunks."""

    chunk_size = MEDIA_FILE_CHUNK_SIZE
    first_chunk_size = 64 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = _file_receive.set(receive)
        try:
            await super().__call__(scope, receive, send)
        finally:
            _file_receive.reset(token)

    async def _listen_for_disconnect(self, group: anyio.abc.TaskGroup) -> None:
        receive = _file_receive.get()
        while True:
            if (await receive())["type"] in {"http.disconnect", "websocket.disconnect"}:
                group.cancel_scope.cancel()
                return
            await anyio.lowlevel.checkpoint()

    async def _handle_single_range(
        self, send: Send, start: int, end: int, file_size: int, send_header_only: bool,
    ) -> None:
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        # Uvicorn can ignore sends after disconnect instead of raising. Watch
        # receive too, so a stopped player never reads the rest of a large file.
        async with create_collapsing_task_group() as group:
            group.start_soon(self._listen_for_disconnect, group)
            file = None
            try:
                file = await anyio.open_file(self.path, mode="rb")
                await file.seek(start)
                size = self.first_chunk_size
                while True:
                    requested = min(size, end - start)
                    chunk = await file.read(requested)
                    start += len(chunk)
                    more_body = len(chunk) == requested and start < end
                    await send({"type": "http.response.body", "body": chunk, "more_body": more_body})
                    if not more_body:
                        break
                    size = self.chunk_size
            finally:
                if file is not None:
                    with anyio.CancelScope(shield=True):
                        await file.aclose()
            group.cancel_scope.cancel()



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
