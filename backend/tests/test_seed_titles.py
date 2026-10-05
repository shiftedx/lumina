"""The gallery title seed at a small size: its shape, and that it refuses a used directory."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "seed_titles.py"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="the seed renders its images with ffmpeg")


def test_a_small_seed_has_the_pinned_shape(tmp_path: Path) -> None:
    target = tmp_path / "seed"
    command = [
        sys.executable, str(SCRIPT), str(target), "--movies", "40", "--series", "8", "--episodes", "1200", "--progress", "90",
        "--anime-series", "3", "--anime-movies", "2", "--albums", "10", "--tracks", "40",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=True)
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert (summary["movies"], summary["series"], summary["episodes"]) == (40, 8, 1200)
    assert (summary["anime_movies"], summary["anime_series"], summary["anime_episodes"]) == (2, 3, 36)
    assert (summary["artists"], summary["albums"], summary["tracks"]) == (9, 10, 40)
    assert summary["data_dir"] == str((target / "data").resolve()) and summary["images"] > 1200
    engine = create_engine(f"sqlite:///{target / 'data' / 'app.db'}")
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM users")) == 3
        big = db.scalar(text(
            "SELECT count(*) FROM (SELECT s.parent_id FROM media_titles e JOIN media_titles s ON s.id = e.parent_id "
            "WHERE e.type = 'episode' GROUP BY s.parent_id HAVING count(DISTINCT s.id) >= 10 AND count(*) >= 200)"
        ))
        assert big == 5  # the detail budget's 10-season, 200-episode shows
        assert db.scalar(text("SELECT count(*) FROM playback_progress")) == 90
        assert db.scalar(text("SELECT count(*) FROM media_titles WHERE json_extract(images, '$.Primary.path') LIKE '%.avif'")) > 0
        assert db.scalar(text("SELECT count(*) FROM media_artifacts WHERE json_extract(probe, '$.height') >= 2000")) > 0
        tags = db.scalar(text("SELECT count(DISTINCT json_extract(images, '$.Primary.tag')) FROM media_titles WHERE json_extract(images, '$.Primary') IS NOT NULL"))
        assert tags == db.scalar(text("SELECT count(*) FROM media_titles WHERE json_extract(images, '$.Primary') IS NOT NULL"))
    engine.dispose()
    assert (target / "media" / "art" / "poster-0.jpg").is_file()
    again = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert again.returncode != 0 and "not empty" in again.stderr


def test_the_seed_s_categories_and_music_have_the_contract_shape(tmp_path: Path) -> None:
    """Categories agree with the category SQL, keys have the music shapes, covers split 6/3/1 per 10."""
    from app.db import CATEGORY_BACKFILL_SQL

    target = tmp_path / "seed"
    subprocess.run(
        [sys.executable, str(SCRIPT), str(target), "--movies", "4", "--series", "2", "--episodes", "20", "--progress", "0",
         "--anime-series", "2", "--anime-movies", "1", "--albums", "10", "--tracks", "30"],
        capture_output=True, text=True, timeout=600, check=True,
    )
    database = target / "data" / "app.db"
    with closing(sqlite3.connect(database)) as db:
        changes = db.total_changes
        for statement in CATEGORY_BACKFILL_SQL:
            db.execute(statement)
        assert db.total_changes == changes  # every seeded category is what the scan would derive
        assert db.execute("SELECT count(*) FROM media_titles WHERE category = 'anime' AND type IN ('movie', 'series')").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM media_titles WHERE type = 'album' AND key NOT LIKE 'album:%/%'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM media_titles WHERE type = 'artist' AND key NOT LIKE 'artist:%'").fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM library_items i JOIN media_titles a ON a.id = i.title_id"
            " WHERE i.kind = 'track' AND a.type = 'album' AND json_extract(i.metadata_json, '$.track_number') IS NOT NULL"
        ).fetchone()[0] == 30
        folder = db.execute("SELECT json_extract(images, '$.Primary.path') FROM media_titles WHERE type = 'album' AND json_extract(images, '$.Primary.path') IS NOT NULL").fetchall()
        embedded = db.execute("SELECT json_extract(images, '$.Primary.embedded'), json_extract(images, '$.Primary.tag') FROM media_titles WHERE type = 'album' AND json_extract(images, '$.Primary.embedded') IS NOT NULL").fetchall()
    assert (len(folder), len(embedded)) == (6, 3)
    for (path,) in folder:
        assert path.endswith("/cover.jpg") and (target / "media" / path).is_file()
    for relative, tag in embedded:
        track = target / "media" / relative
        assert track.read_bytes()[:4] == b"fLaC" and re.fullmatch(r"\d+:\d+", tag)
        assert tag == f"{track.stat().st_size}:{track.stat().st_mtime_ns}"
