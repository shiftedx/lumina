"""Authenticated queue admission for local hero framing."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import MediaTitle, User
from app.security import get_current_user
from app.services import art_urls, hero_framing, tmdb
from app.services.artwork import ArtworkError, ArtworkService
from app.services.titles import TitleService, image_url, title_image_bytes, title_image_source

_LOCAL = re.compile(r"^/api/titles/([0-9a-f-]{36})/images/Backdrop\?tag=([0-9a-f]{16})$")
_REMOTE_HOST = "image.tmdb.org"


def _result(key: str, loader: hero_framing.Loader) -> dict:
    return hero_framing.hero_framing.request(key, loader).body()


def _local(title_id: str, expected: str, key: str, artwork: ArtworkService) -> hero_framing.Loader:
    def load() -> tuple[bytes, str] | None:
        with SessionLocal() as db:
            title = db.get(MediaTitle, title_id)
            if title is None or image_url(title, "Backdrop") != expected:
                return None
            source = title_image_source(title, "Backdrop")
            if source is None or art_urls.source_key(title.id, "Backdrop", source) != key:
                return None
            try:
                content_type, content = title_image_bytes(db, title, "Backdrop", artwork)
                return content, content_type
            except (ArtworkError, FileNotFoundError):
                return None
    return load


def _remote(path: str, artwork: ArtworkService) -> hero_framing.Loader:
    def load() -> tuple[bytes, str] | None:
        try:
            response = tmdb.load_image(artwork, path, "Backdrop", size="w1280", max_bytes=hero_framing.MAX_INPUT_BYTES)
        except ArtworkError:
            return None
        return response.content, response.content_type
    return load


def framing(
    src: str = Query(min_length=1, max_length=512), current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    """Validate one current artwork address, then queue analysis without reading/fetching in this request."""
    local = _LOCAL.fullmatch(src)
    if local is not None:
        title_id = local.group(1)
        title = TitleService(db).get_visible(title_id, current_user)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        if image_url(title, "Backdrop") != src:
            return {"state": "unavailable"}
        source = title_image_source(title, "Backdrop")
        if source is None:
            return {"state": "unavailable"}
        key = art_urls.source_key(title.id, "Backdrop", source)
        return _result(key, _local(title.id, src, key, _artwork))
    try:
        parsed = urlsplit(src)
    except ValueError:
        return {"state": "unavailable"}
    if parsed.scheme != "https" or parsed.netloc != _REMOTE_HOST or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return {"state": "unavailable"}
    prefix = "/t/p/w1280"
    if not parsed.path.startswith(prefix):
        return {"state": "unavailable"}
    path = tmdb._image_path(parsed.path[len(prefix):])  # private validator keeps exact image-host grammar
    if path is None or f"https://{_REMOTE_HOST}{prefix}{path}" != src or not hero_framing.catalog_sources.contains(src):
        return {"state": "unavailable"}
    key = hashlib.sha256(f"tmdb:{path}".encode()).hexdigest()
    return _result(key, _remote(path, _artwork))


_artwork: ArtworkService


def register(app: FastAPI, artwork: ArtworkService) -> None:
    global _artwork
    _artwork = artwork
    app.get("/api/art/framing")(framing)
