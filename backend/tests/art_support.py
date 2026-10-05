"""Gallery artwork test helpers (contract commit). T1 appends its fake-ffmpeg helpers below the marker."""
from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models import MediaTitle, TitleArtwork
from app.services import art_urls
from app.services.titles import title_image_source

READY_FACTS = {
    "width": 1000, "height": 1500, "preview": b"RIFF\x00\x00\x00\x00WEBP", "preview_type": "image/webp",
    "dominant": "#2a3b4c", "accent": "#c08a4b",
}


def give_art(title: MediaTitle, image_type: str = "Primary", *, path: str | None = None, tag: str | None = None) -> str:
    """Store a local image entry on ``title`` like the scanner does; returns its source (titles.title_image_source)."""
    path = path or f"art/{title.id}-{image_type.lower()}.jpg"
    tag = tag or hashlib.sha256(path.encode()).hexdigest()[:12]
    title.images = {**(title.images or {}), image_type: {"path": path, "tag": tag}}
    return tag


def artwork_row(session: Session, title: MediaTitle, image_type: str = "Primary", *, state: str = "ready", stale: bool = False, **facts) -> TitleArtwork:
    """A title_artwork row for the title's current source (``stale=True``: keyed to an older source)."""
    source = title_image_source(title, image_type)
    assert source is not None, "give_art first"
    key = art_urls.source_key(title.id, image_type, "older-source" if stale else source)
    row = TitleArtwork(title_id=title.id, image_type=image_type, source_key=key, state=state, **({**READY_FACTS, **facts} if state == "ready" else facts))
    session.add(row)
    session.flush()
    return row


# ---- helpers below (append only) ----

FAKE_DOMINANT, FAKE_ACCENT = "#817f7f", "#c82828"  # the fake ffmpeg's 8 x 8 frame: its mean, and its one vivid pixel
ART_ROOT_ID = "gallery-art-root"


def use_fake_ffmpeg(tmp_path, monkeypatch, mode: str = "ok"):  # noqa: ANN001, ANN201
    """Point renditions at fixtures/fake_ffmpeg.py (through an sh wrapper); returns the JSON-lines log of its runs."""
    import sys
    from pathlib import Path

    from app.services import renditions

    fake = Path(__file__).resolve().parent / "fixtures" / "fake_ffmpeg.py"
    wrapper = tmp_path / "fake-ffmpeg"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
    wrapper.chmod(0o755)
    log = tmp_path / "fake-ffmpeg.jsonl"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(log))
    monkeypatch.setenv("FAKE_FFMPEG_MODE", mode)
    monkeypatch.setattr(renditions, "media_tool", lambda db, name: str(wrapper))
    return log


def fake_calls(log) -> list[dict]:  # noqa: ANN001
    import json

    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def png_header(width: int, height: int) -> bytes:
    """Just enough PNG for renditions.image_size; the fake ffmpeg never decodes it."""
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"


def sample_images(dest):  # noqa: ANN001, ANN201
    """scripts/perf/sample_art.generate, loaded by path (backend/tests/perf would shadow a ``perf`` package)."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("sample_art", Path(__file__).resolve().parents[2] / "scripts" / "perf" / "sample_art.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(dest)


def seed_art_root(root) -> None:  # noqa: ANN001
    """Tables on conftest's file-backed engine (what SessionLocal uses) plus one online external root at ``root``."""
    from app import db as db_module
    from app.db import Base
    from app.models import StorageRoot

    root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=db_module.engine)
    with db_module.SessionLocal() as session:
        session.add(StorageRoot(id=ART_ROOT_ID, label="Media", path=str(root), mode="external", enabled=True, observation={"state": "available"},
                                identity=str(root.stat().st_dev)))
        session.commit()


def add_art_title(root, title_id: str, type_: str = "movie", *, images=("Primary",), days: int = 0, parent_id: str | None = None,  # noqa: ANN001
                  size: tuple[int, int] = (1000, 1500)) -> None:
    """A title under seed_art_root whose art files are PNG headers of ``size``."""
    from app import db as db_module
    from discovery_support import add_title

    with db_module.SessionLocal() as session:
        title = add_title(session, title_id, type_, f"Title {title_id[-4:]}", parent_id=parent_id, days=days)
        title.root_id = ART_ROOT_ID
        for image_type in images:
            relative = f"{title_id}/{image_type.lower()}.png"
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(png_header(*size))
            give_art(title, image_type, path=relative)
        session.commit()


def art_url(title_id: str, image_type: str, source: str, width: int) -> str:
    """The /api/art URL T2's summaries issue for this image at ``width``."""
    return art_urls.rendition_template(title_id, image_type, art_urls.source_key(title_id, image_type, source)).replace("{w}", str(width))


def write_rendition(key: str, width: int, data: bytes, ext: str = "webp"):  # noqa: ANN201
    path = art_urls.rendition_file(key, width, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
