"""Perf server: the medium title seed with every rendition ready, then the real backend.

    backend/.venv/bin/python scripts/perf/serve_titles.py ROOT PORT

ROOT is wiped and recreated (like run_realstack_fixtures.py). Art is prepared fast: each sample
image is rendered once per image type and its outputs are hard-linked to every title that uses it, with a ready
title_artwork row per image, so the walls are served exactly as after a finished background pass.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MEDIUM = ("--movies", "1735", "--series", "60", "--episodes", "2000", "--anime-series", "10", "--albums", "40", "--tracks", "400")
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def prepare_art(media: Path) -> int:
    """Render each (sample, image type, still?) once, hard-link it per title, and add ready rows; returns the row count.

    The seed's deliberate 1 % unsupported (AVIF) entries are dropped first, so every image a wall asks for
    has a rendition and the zero-image-failure budget measures the app, not the seed. This differs from production, which
    keeps an AVIF entry on the title with no title_artwork row (the wall shows its card); here the entry is removed.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import MediaTitle
    from app.services import art_urls, renditions
    from app.services.titles import title_image_source

    renditions.use_host_format()  # JPEG when this host's ffmpeg has no WebP encoder
    rendered: dict[tuple[str, str, bool], renditions.Rendered | str] = {}  # a str is the RenditionError reason
    rows = []
    with SessionLocal() as db:
        for title in db.scalars(select(MediaTitle)):
            if any(not art_urls.supported(entry) for entry in (title.images or {}).values()):
                title.images = {image_type: entry for image_type, entry in title.images.items() if art_urls.supported(entry)}
            for image_type, entry in (title.images or {}).items():
                if image_type not in art_urls.ART_TYPES or not art_urls.supported(entry) or not entry.get("path"):
                    continue
                still = title.type == "episode" and image_type == "Primary"
                kind = "episode" if still else title.type  # albums/artists render square (art_urls.square)
                cache_key = (entry["path"], image_type, still, art_urls.square(kind, image_type))
                if cache_key not in rendered:
                    source = media / entry["path"]
                    try:
                        rendered[cache_key] = renditions.render(
                            source.read_bytes(), CONTENT_TYPES[source.suffix.lower()],
                            title_type=kind, image_type=image_type, timeout=120,
                        )
                    except renditions.RenditionError as exc:  # e.g. a logo on a JPEG-only host: the original serves
                        rendered[cache_key] = exc.reason
                result = rendered[cache_key]
                key = art_urls.source_key(title.id, image_type, title_image_source(title, image_type))
                if isinstance(result, str):
                    rows.append(renditions.failed_row(title.id, image_type, key, result))
                    continue
                for width in art_urls.widths(title.type, image_type):
                    target = art_urls.rendition_file(key, width, result.ext)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(result.files[width], target)
                rows.append(renditions.ready_row(title.id, image_type, key, result))
        db.add_all(rows)
        db.commit()
    for result in rendered.values():
        if not isinstance(result, str):
            renditions.discard(result)
    return len(rows)


def main() -> None:
    root, port = Path(sys.argv[1]).resolve(), sys.argv[2]
    sys.path[:0] = [str(REPOSITORY_ROOT / "scripts"), str(REPOSITORY_ROOT / "backend")]
    import test_safety
    from run_realstack_fixtures import offline_setattr

    test_safety.check_root_isolation(root)
    shutil.rmtree(root, ignore_errors=True)
    subprocess.run([sys.executable, str(REPOSITORY_ROOT / "scripts" / "perf" / "seed_titles.py"), str(root), *MEDIUM], check=True, stdout=subprocess.DEVNULL)
    os.environ.update(
        LUMINA_DATA_DIR=str(root / "data"),
        LUMINA_PORT=port,
        LUMINA_STORAGE_MOUNT_PARENTS=str(root / "media"),
        LUMINA_ALLOWED_ORIGINS=f"http://127.0.0.1:{port}",
    )
    test_safety.LoopbackOnlyGuard().apply(offline_setattr)
    print(f"serve_titles: {prepare_art(root / 'media')} renditions ready", flush=True)
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(port), log_level="warning", timeout_keep_alive=30)


if __name__ == "__main__":
    main()
