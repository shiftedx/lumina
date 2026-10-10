"""File response tuned for video-sized local and cached playback bodies."""
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from typing import BinaryIO

import anyio
from fastapi.responses import FileResponse, StreamingResponse
from starlette._utils import create_collapsing_task_group
from starlette.background import BackgroundTask
from starlette.datastructures import MutableHeaders
from starlette.types import Receive, Scope, Send

from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE

_file_receive: ContextVar[Receive] = ContextVar("media_file_receive")


def _open_seek_read(path: str, start: int, size: int) -> tuple[BinaryIO, bytes]:
    """Open a range and return its first bytes in one worker-thread handoff."""
    file = open(path, "rb")  # noqa: SIM115 - ownership transfers to the response.
    try:
        file.seek(start)
        return file, file.read(size)
    except BaseException:
        file.close()
        raise


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
            file: BinaryIO | None = None
            try:
                # End-index MP4s ask for another range while parsing this prefix.
                # Give that request a chance before buffering a bulk chunk.
                startup_end = min(end, 4 * self.first_chunk_size) if start == 0 else start
                size = self.first_chunk_size
                requested = min(size, end - start)
                file, chunk = await anyio.to_thread.run_sync(_open_seek_read, str(self.path), start, requested)
                while True:
                    start += len(chunk)
                    more_body = len(chunk) == requested and start < end
                    await send({"type": "http.response.body", "body": chunk, "more_body": more_body})
                    if not more_body:
                        break
                    size = self.first_chunk_size if start < startup_end else self.chunk_size
                    requested = min(size, end - start)
                    chunk = await anyio.to_thread.run_sync(file.read, requested)
            finally:
                if file is not None:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(file.close)
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
