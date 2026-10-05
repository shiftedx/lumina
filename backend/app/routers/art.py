"""Gallery artwork routes.

``/api/art/{sig}/{title_id}/{image_type}/{key}-{w}.{ext}`` serves content-addressed, immutable renditions with no
session: the URL is a capability, issued only inside visibility-scoped responses and signed over (title, type,
source key). Every path part is validated before anything else, the file path is derived only from validated hex
and integers, and a hit touches no database. Only a miss loads the title (renditions.serve_miss).
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from functools import partial

import anyio
from anyio.lowlevel import RunVar
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.media_schemas import ArtworkProgress
from app.security import get_admin_user
from app.db import SessionLocal
from app.services import art_urls, member_access
from app.services.titles import TitleService
from app.services import renditions as pipeline

SIG = re.compile(r"[A-Za-z0-9_-]{22}")  # always fullmatch: "$" would also accept a trailing newline
# A restricted member's signature: the 22 chars + their art scope (member_access.art_scope / ART_SCOPE).
SCOPED_SIG = re.compile(r"[A-Za-z0-9_-]{22}[0-9a-f]{8}[0-9A-Za-z-]{1,36}")
TITLE_ID = re.compile(r"[0-9a-f-]{36}")
READ_THREADS = 8
MISS_THREADS = 8
BYTE_CACHE_BYTES = 128 * 1024 * 1024
IMMUTABLE = "public, max-age=31536000, immutable"
# A restricted member's art dies with their access, so it must not outlive it in a shared (edge) cache.
SCOPED = "private, max-age=300"
NOT_FOUND = "Image not found"
_limiter: RunVar[anyio.CapacityLimiter] = RunVar("art_read_limiter")
_miss_limiter: RunVar[anyio.CapacityLimiter] = RunVar("art_miss_limiter")


def _per_loop(var: RunVar[anyio.CapacityLimiter], tokens: int) -> anyio.CapacityLimiter:
    """One limiter per event loop, the way anyio keeps its default one."""
    try:
        return var.get()
    except LookupError:
        limiter = anyio.CapacityLimiter(tokens)
        var.set(limiter)
        return limiter


def read_limiter() -> anyio.CapacityLimiter:
    """Rendition reads get their own 8 threads, never the default 40 shared with API routes and video."""
    return _per_loop(_limiter, READ_THREADS)


def miss_limiter() -> anyio.CapacityLimiter:
    """Misses wait up to 0.8 s on generation, so they get their own threads and never delay a hit."""
    return _per_loop(_miss_limiter, MISS_THREADS)


class ByteCache:
    """LRU of rendition bytes by file name, bounded in bytes. Used only on the event loop thread, so no lock."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._items: OrderedDict[str, tuple[bytes, float]] = OrderedDict()

    def get(self, name: str) -> tuple[bytes, float] | None:
        item = self._items.get(name)
        if item is not None:
            self._items.move_to_end(name)
        return item

    def put(self, name: str, data: bytes, mtime: float) -> None:
        if len(data) > self.limit:
            return
        old = self._items.pop(name, None)
        if old is not None:
            self.used -= len(old[0])
        self._items[name] = (data, mtime)
        self.used += len(data)
        while self.used > self.limit:
            _, (evicted, _) = self._items.popitem(last=False)
            self.used -= len(evicted)

    def clear(self) -> None:
        self._items.clear()
        self.used = 0


byte_cache = ByteCache(BYTE_CACHE_BYTES)


def _parse(sig: str, title_id: str, image_type: str, file: str) -> tuple[str, int, str] | None:
    """(key, width, ext) when every part matches its pattern, the width is allowed and the signature verifies."""
    match = pipeline.RENDITION_NAME.fullmatch(file)
    if match is None or not (SIG.fullmatch(sig) or SCOPED_SIG.fullmatch(sig)) or not TITLE_ID.fullmatch(title_id) or image_type not in art_urls.ART_TYPES:
        return None
    key, width, ext = match[1], int(match[2]), match[3]
    if str(width) != match[2] or width not in art_urls.ALLOWED_WIDTHS[image_type]:
        return None
    return (key, width, ext) if art_urls.verify(sig, title_id, image_type, key) else None


def _scope_sees(scope: str, title_id: str) -> bool:
    """A scoped signature's member still has the limits it was signed under and still sees the title (cached)."""
    with SessionLocal() as db:
        return member_access.scope_sees(db, scope, title_id, lambda member: TitleService(db).get_visible(title_id, member) is not None)


def _etag_matches(header: str | None, etag: str) -> bool:
    return header is not None and any(candidate.strip().removeprefix("W/") in (etag, "*") for candidate in header.split(","))


def _image(data: bytes, ext: str, etag: str, *, head: bool, cache: str) -> Response:
    return Response(content=b"" if head else data, media_type=pipeline.CONTENT_TYPES[ext], headers={
        "Cache-Control": cache, "ETag": etag, "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff",
    })


async def art(sig: str, title_id: str, image_type: str, file: str, request: Request) -> Response:
    parsed = _parse(sig, title_id, image_type, file)
    if parsed is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    key, width, ext = parsed
    if len(sig) > 22 and not await anyio.to_thread.run_sync(_scope_sees, sig[22:], title_id, limiter=read_limiter()):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    name = f"{key}-{width}.{ext}"
    cache = SCOPED if len(sig) > 22 else IMMUTABLE
    etag = f'"{name}"'
    head = request.method == "HEAD"
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache})
    path = art_urls.rendition_file(key, width, ext)
    cached = byte_cache.get(name)
    if cached is not None:
        pipeline.count("hits_memory")
        data, mtime = cached
        if time.time() - mtime > pipeline.MTIME_BUMP_SECONDS:  # the utime runs off the loop, once per file per day
            byte_cache.put(name, data, await anyio.to_thread.run_sync(pipeline.touch, path, mtime, limiter=read_limiter()))
        return _image(data, ext, etag, head=head, cache=cache)
    found = await anyio.to_thread.run_sync(pipeline.read_rendition, path, limiter=read_limiter())
    if found is not None:
        pipeline.count("hits_disk")
        byte_cache.put(name, *found)
        return _image(found[0], ext, etag, head=head, cache=cache)
    pipeline.count("misses")
    try:
        served = await anyio.to_thread.run_sync(
            partial(pipeline.serve_miss, title_id, image_type, key, width, ext, request.app.state.artwork), limiter=miss_limiter(),
        )
    except pipeline.Preparing:
        return JSONResponse(status_code=503, content={"detail": "Image is being prepared"}, headers={"Retry-After": "2", "Cache-Control": "no-store"})
    if served is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not served.cacheable:
        return Response(content=b"" if head else served.content, media_type=served.content_type, headers={
            "Cache-Control": "no-store", "Content-Length": str(len(served.content)), "X-Content-Type-Options": "nosniff",
        })
    byte_cache.put(name, served.content, time.time())
    return _image(served.content, ext, etag, head=head, cache=cache)


def artwork_progress() -> ArtworkProgress:
    return pipeline.renditions.progress()


def register(app: FastAPI) -> None:
    app.api_route("/api/art/{sig}/{title_id}/{image_type}/{file}", methods=["GET", "HEAD"], include_in_schema=False)(art)
    app.get("/api/admin/artwork", response_model=ArtworkProgress, dependencies=[Depends(get_admin_user)])(artwork_progress)
