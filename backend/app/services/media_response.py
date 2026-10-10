"""File response tuned for video-sized local and cached playback bodies."""
from fastapi.responses import FileResponse

from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE


class MediaFileResponse(FileResponse):
    """FileResponse with fewer worker-thread file reads for media ranges."""

    chunk_size = MEDIA_FILE_CHUNK_SIZE
