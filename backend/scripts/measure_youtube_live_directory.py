"""Real-network measurement (not a test): PYTHONPATH=. .venv/bin/python scripts/measure_youtube_live_directory.py"""
import os
import tempfile
import time

os.environ["LUMINA_DATA_DIR"] = tempfile.mkdtemp()

from app.db import init_db  # noqa: E402
from app.services.youtube_live_directory import CATEGORY_QUERIES, youtube_live_directory

init_db()
for key in CATEGORY_QUERIES:
    t = time.monotonic()
    items, cursor, _ = youtube_live_directory(key, 60)
    print(key, len(items), "first page; more:", bool(cursor), f"{time.monotonic() - t:.1f}s", items[0].title[:50] if items else "")
